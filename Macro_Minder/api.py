"""
API layer -- wraps the existing pipeline (fetcher, normalize, goal engine,
db) in HTTP endpoints for a frontend to consume. No new logic lives here;
this is a thin translation layer between HTTP and the functions/classes
that already exist and are already tested.

Run with: uvicorn api:app --reload
Docs auto-generated at: http://localhost:8000/docs
"""

import dataclasses
from typing import Optional
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
import requests

import db
from fetcher import fetch_usda_foods, fetch_off_product
from goals import GOALS
from goal import combine_results
from normalize import Food
from ai_tip import generate_contextual_tip, TipResponse

app = FastAPI(title="MacroMinder API")

# Allow a local React dev server to call this API. Tighten this before
# any real deployment -- wide open CORS is fine for local dev only.
app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://localhost:3000",
        "http://localhost:5173",
        # Vercel production + preview deployments
        "https://macro-minder.vercel.app",
        "https://macro-minder-mohammadr33.vercel.app",
    ],
    allow_origin_regex=r"https://macro-minder.*\.vercel\.app",
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.on_event("startup")
def startup():
    db.init_db()


# ---- Request/response models ----
# Pydantic models here validate the HTTP boundary. Food itself stays a
# plain dataclass (normalize.py) -- no reason to duplicate that logic,
# this model just describes the JSON shape going over the wire.
class FoodIn(BaseModel):
    name: str
    source: str
    serving_size_g: Optional[float] = None
    calories: Optional[float] = None
    protein_g: Optional[float] = None
    total_fat_g: Optional[float] = None
    saturated_fat_g: Optional[float] = None
    carbs_g: Optional[float] = None
    sugar_g: Optional[float] = None
    fiber_g: Optional[float] = None
    sodium_mg: Optional[float] = None
    cholesterol_mg: Optional[float] = None
    is_processed: Optional[bool] = None
    food_category: Optional[str] = None


class EvaluateRequest(BaseModel):
    food: FoodIn
    goal_names: list[str]
    # No portion_type field -- auto-inferred server-side from the food's
    # category data (infer_portion_type()), not asked of the client. See
    # goal.py/DECISIONS.md for why: this tool checks single items, not
    # composed meals, so it's detectable rather than needing user input.


def _serialize_rules(rule_results):
    return [
        {
            "label": r.rule.label,
            "reason": r.reason,
            "confidence": r.confidence.value,
            "applicable": r.applicable,
            "role": r.resolved_role.value if r.resolved_role else None,
            "passed": r.passed,
            "value": r.value,
            "threshold": r.threshold,
            "unit": r.unit,
            "comparison": r.comparison,
        }
        for r in rule_results
    ]


# ---- Endpoints ----

@app.get("/api/goals")
def list_goals():
    return list(GOALS.keys())


@app.get("/api/foods/search")
def search_foods(q: str, page: int = 1, page_size: int = 10):
    if not q.strip():
        raise HTTPException(400, "Query cannot be empty")
    try:
        results = fetch_usda_foods(q, page_size=page_size, page_number=page)
    except requests.exceptions.RequestException as e:
        raise HTTPException(502, f"Error reaching USDA: {e}")
    return [dataclasses.asdict(f) for f in results]


@app.get("/api/foods/barcode/{barcode}")
def lookup_barcode(barcode: str):
    try:
        food = fetch_off_product(barcode)
    except requests.exceptions.RequestException as e:
        raise HTTPException(502, f"Error reaching Open Food Facts: {e}")
    if food is None:
        raise HTTPException(404, f"No product found for barcode '{barcode}'")
    return dataclasses.asdict(food)


@app.post("/api/evaluate")
def evaluate(req: EvaluateRequest):
    if not req.goal_names:
        raise HTTPException(400, "At least one goal is required")
    unknown_goals = [g for g in req.goal_names if g not in GOALS]
    if unknown_goals:
        raise HTTPException(400, f"Unknown goal(s): {unknown_goals}. Options: {list(GOALS.keys())}")

    food = Food(**req.food.model_dump())

    results_by_goal = {gname: GOALS[gname].evaluate(food) for gname in req.goal_names}

    # Log each goal's evaluation separately -- keeps the existing
    # single-goal history schema unchanged rather than needing a
    # migration just to support stacked goals.
    for gname, result in results_by_goal.items():
        reasons = [r.reason for r in result.rule_results]
        db.log_evaluation(food, gname, result.tier.value, reasons)

    response = {
        "food_name": food.name,
        "is_processed": food.is_processed,
        "serving_basis": food.per_serving().get("basis", "per_serving"),
        "goal_names": req.goal_names,
        "results": [
            {
                "goal_name": gname,
                "tier": result.tier.value,
                "rules": _serialize_rules(result.rule_results),
            }
            for gname, result in results_by_goal.items()
        ],
    }

    # Combined verdict only makes sense (and is only computed) when
    # multiple goals are stacked -- for a single goal, "combined" and
    # "that goal's own result" are the same thing, so skip the extra
    # noise in the response.
    if len(req.goal_names) > 1:
        combined_tier, driving_goal = combine_results(results_by_goal)
        response["combined_tier"] = combined_tier.value
        response["driving_goal"] = driving_goal

    return response


@app.post("/api/tip", response_model=TipResponse)
def get_tip(req: EvaluateRequest):
    if not req.goal_names:
        raise HTTPException(400, "At least one goal is required")
    unknown_goals = [g for g in req.goal_names if g not in GOALS]
    if unknown_goals:
        raise HTTPException(400, f"Unknown goal(s): {unknown_goals}. Options: {list(GOALS.keys())}")

    food = Food(**req.food.model_dump())
    results_by_goal = {gname: GOALS[gname].evaluate(food) for gname in req.goal_names}

    if len(req.goal_names) > 1:
        combined_tier, driving_goal = combine_results(results_by_goal)
        verdict_tier_str = combined_tier.value
        driver_str = driving_goal
    else:
        gname = req.goal_names[0]
        verdict_tier_str = results_by_goal[gname].tier.value
        driver_str = gname

    # Extract all evaluated rules across active goals
    failed_rules = []
    passed_rules = []
    confidence_notes = []

    for gname, result in results_by_goal.items():
        for r in result.rule_results:
            if not r.applicable:
                continue
            serialized = {
                "label": r.rule.label,
                "value": r.value,
                "threshold": r.threshold,
                "unit": r.unit,
                "comparison": r.comparison,
                "goal": gname,
            }
            if r.passed is False:
                failed_rules.append(serialized)
            elif r.passed is True:
                passed_rules.append(serialized)

            if r.confidence.value == "estimated":
                confidence_notes.append(f"{r.rule.label} estimated from related nutrient ({r.rule.fallback_nutrient})")
            elif r.confidence.value == "unknown":
                confidence_notes.append(f"{r.rule.label} data missing")

    serving_basis = food.per_serving().get("basis", "per_serving")

    return generate_contextual_tip(
        food_name=food.name,
        is_processed=food.is_processed,
        serving_basis=serving_basis,
        goal_names=req.goal_names,
        verdict_tier=verdict_tier_str,
        driving_goal=driver_str,
        failed_rules=failed_rules,
        passed_rules=passed_rules,
        confidence_notes=confidence_notes,
    )


@app.get("/api/history")
def history(limit: int = 10):
    return db.get_recent_history(limit=limit)