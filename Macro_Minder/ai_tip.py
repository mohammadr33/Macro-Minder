"""
Grounded AI Contextual Tip Engine for MacroMinder.

ANTI-THIN-WRAPPER DESIGN:
This engine takes the deterministic outputs of the rule engine (exact nutrients,
limits, passed/failed rules, confidence, and serving basis) as ground truth.
It does not prompt an LLM to guess or estimate nutrition data. Instead, it:
1. Synthesizes a grounded biological takeaway explaining *why* the failing/passing
   nutrient matters for the user's specific goal.
2. Suggests a concrete, actionable whole-food swap or pairing strategy.
3. Provides a reliable, deterministic fallback synthesizer when no API key is present
   or when offline, ensuring the app is always fully functional.
"""

import json
import os
import requests
from typing import Optional
from pydantic import BaseModel
from dotenv import load_dotenv

load_dotenv()

GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY") or os.environ.get("GOOGLE_API_KEY")
OPENAI_API_KEY = os.environ.get("OPENAI_API_KEY")


class TipResponse(BaseModel):
    takeaway: str
    suggested_swap: str
    is_ai_generated: bool
    source_model: Optional[str] = None


def _build_deterministic_tip(
    food_name: str,
    is_processed: Optional[bool],
    goal_names: list[str],
    verdict_tier: str,
    failed_rules: list[dict],
    passed_rules: list[dict],
) -> TipResponse:
    """
    Deterministic, rule-grounded fallback synthesizer.
    Generates a high-quality, scientifically grounded takeaway and swap based
    on the exact nutrient failures/passes without requiring an external API.
    """
    takeaways = []
    swaps = []

    # Map specific nutrient failures to biochemical mechanisms & actionable swaps
    for r in failed_rules:
        label = r.get("label", "").lower()
        val = r.get("value")
        thresh = r.get("threshold")
        unit = r.get("unit", "")

        if "saturated fat" in label:
            takeaways.append(
                f"Saturated fat ({val}{unit}) exceeds the {thresh}{unit} threshold; "
                "excess saturated fat downregulates hepatic LDL receptors, raising circulating LDL cholesterol."
            )
            swaps.append("swap with foods rich in monounsaturated or polyunsaturated fats like avocado, walnuts, or chia seeds")
        elif "sodium" in label:
            takeaways.append(
                f"Sodium ({val}{unit}) exceeds the {thresh}{unit} limit; elevated sodium increases "
                "intravascular fluid retention, elevating arterial blood pressure."
            )
            swaps.append("choose fresh, unsalted whole foods like edamame, sliced cucumber with lemon, or air-popped popcorn seasoned with smoked paprika")
        elif "sugar" in label:
            takeaways.append(
                f"Sugar content ({val}{unit}) exceeds the {thresh}{unit} target, causing rapid glycemic excursions "
                "without satiety-inducing fiber."
            )
            swaps.append("substitute with whole fresh berries, an apple with almond butter, or plain Greek yogurt with cinnamon")
        elif "calorie" in label:
            takeaways.append(
                f"Caloric density ({val}{unit}) exceeds the {thresh}{unit} target, limiting meal volume and satiety."
            )
            swaps.append("opt for high-volume, low-energy-density whole foods such as crisp greens, roasted zucchini, or watermelon")
        elif "protein" in label and r.get("comparison") == "min":
            takeaways.append(
                f"Protein ({val}{unit}) is below the {thresh}{unit} target needed to optimize muscle protein synthesis."
            )
            swaps.append("pair or replace with high-protein staples such as canned tuna, boiled eggs, firm tofu, or edamame")
        elif "fiber" in label and r.get("comparison") == "min":
            takeaways.append(
                f"Fiber ({val}{unit}) is below the {thresh}{unit} target; dietary fiber is essential for blunting glucose absorption and feeding the gut microbiome."
            )
            swaps.append("incorporate legumes (lentils, black beans), flaxseed meal, or rolled oats")

    if is_processed is True and not swaps:
        swaps.append("choose a single-ingredient whole-food equivalent to eliminate added emulsifiers, refined oils, and sodium enhancers")

    if not takeaways:
        if verdict_tier in ("suitable", "recommended"):
            takeaways.append(
                f"{food_name} aligns well with your goal{'s' if len(goal_names) > 1 else ''} ({', '.join(goal_names)}), "
                "providing clean nutritional density without adverse nutrient thresholds."
            )
            swaps.append("pair with leafy greens or a lean protein source to build a balanced, nutrient-dense meal")
        else:
            takeaways.append(
                f"Nutritional profile for {food_name} warrants moderation under your current goal requirements."
            )
            swaps.append("consider pairing with nutrient-dense raw vegetables or unsalted nuts to buffer nutritional balance")

    takeaway_str = " ".join(takeaways[:2])
    swap_str = "To optimize your goal: " + "; ".join(swaps[:2]) + "."

    return TipResponse(
        takeaway=takeaway_str,
        suggested_swap=swap_str,
        is_ai_generated=False,
        source_model="MacroMinder Rule Synthesizer (Offline Fallback)",
    )


def generate_contextual_tip(
    food_name: str,
    is_processed: Optional[bool],
    serving_basis: str,
    goal_names: list[str],
    verdict_tier: str,
    driving_goal: Optional[str],
    failed_rules: list[dict],
    passed_rules: list[dict],
    confidence_notes: list[str],
) -> TipResponse:
    """
    Assembles a grounded payload and calls Gemini (or OpenAI) if configured.
    Falls back gracefully to the deterministic rule synthesizer if no key is present or on error.
    """
    # If no LLM keys are configured, return the high-quality deterministic response immediately
    if not GEMINI_API_KEY and not OPENAI_API_KEY:
        return _build_deterministic_tip(
            food_name=food_name,
            is_processed=is_processed,
            goal_names=goal_names,
            verdict_tier=verdict_tier,
            failed_rules=failed_rules,
            passed_rules=passed_rules,
        )

    # Prepare grounded prompt context
    failed_summary = [
        f"- {r.get('label')}: {r.get('value')}{r.get('unit')} (Threshold: {r.get('threshold')}{r.get('unit')}, comparison: {r.get('comparison')})"
        for r in failed_rules
    ] or ["None (all evaluated rules passed)"]

    passed_summary = [
        f"- {r.get('label')}: {r.get('value')}{r.get('unit')} (Threshold: {r.get('threshold')}{r.get('unit')})"
        for r in passed_rules
    ] or ["None"]

    newline = "\n"
    prompt = f"""You are MacroMinder's Grounded Nutrition Synthesizer.
Analyze this single evaluated food item against the user's specific health goals.
STRICT ANTI-HALLUCINATION RULES:
1. Do NOT invent, recalculate, or guess any nutrient numbers. Use ONLY the data provided below.
2. Ground your takeaway strictly in the biochemical relevance of the failed or passed rules to the user's goal(s).
3. Suggest a tangible, whole-food or minimally processed swap or pairing that directly addresses the specific failing or passing nutrients.
4. Keep the tone concise, scientific, and actionable.

DATA PAYLOAD:
- Food Name: {food_name}
- Classification: {"Processed / Packaged (NOVA 4 or Branded)" if is_processed else "Whole / Minimally Processed" if is_processed is False else "Processing level unknown"}
- Serving Basis: {serving_basis}
- Evaluated Goals: {', '.join(goal_names)}
- Driving Goal: {driving_goal or goal_names[0]}
- Overall Verdict: {verdict_tier.upper()}
- Failing Rules:
{newline.join(failed_summary)}
- Passing Rules:
{newline.join(passed_summary)}
- Data Confidence Notes: {', '.join(confidence_notes) if confidence_notes else 'Direct laboratory/label data'}

Respond ONLY with a valid JSON object matching this exact structure:
{{
  "takeaway": "1-2 sentences explaining the biochemical mechanism/impact of the specific nutrients on the active goal(s).",
  "suggested_swap": "1 actionable sentence suggesting a specific whole-food alternative or pairing addressing the exact metric."
}}
"""

    # Dynamically read keys in case .env was modified after module import
    gemini_key = os.environ.get("GEMINI_API_KEY") or os.environ.get("GOOGLE_API_KEY") or GEMINI_API_KEY
    openai_key = os.environ.get("OPENAI_API_KEY") or OPENAI_API_KEY

    # 1. Try Gemini API if available
    if gemini_key:
        for model_name in ["gemini-flash-lite-latest", "gemini-flash-latest", "gemini-2.5-flash-lite"]:
            try:
                url = f"https://generativelanguage.googleapis.com/v1beta/models/{model_name}:generateContent?key={gemini_key}"
                payload = {
                    "contents": [{"parts": [{"text": prompt}]}],
                    "generationConfig": {
                        "temperature": 0.2,
                        "responseMimeType": "application/json",
                    },
                }
                resp = requests.post(url, json=payload, timeout=5.0)
                if resp.status_code == 200:
                    data = resp.json()
                    text_content = data["candidates"][0]["content"]["parts"][0]["text"]
                    parsed = json.loads(text_content)
                    return TipResponse(
                        takeaway=parsed.get("takeaway", ""),
                        suggested_swap=parsed.get("suggested_swap", ""),
                        is_ai_generated=True,
                        source_model="Gemini Flash",
                    )
            except Exception:
                continue

    # 2. Try OpenAI API if available
    if OPENAI_API_KEY:
        try:
            url = "https://api.openai.com/v1/chat/completions"
            headers = {
                "Authorization": f"Bearer {OPENAI_API_KEY}",
                "Content-Type": "application/json",
            }
            payload = {
                "model": "gpt-4o-mini",
                "messages": [
                    {"role": "system", "content": "You are MacroMinder's Grounded Nutrition Synthesizer. Return only valid JSON."},
                    {"role": "user", "content": prompt},
                ],
                "response_format": {"type": "json_object"},
                "temperature": 0.2,
            }
            resp = requests.post(url, headers=headers, json=payload, timeout=4.0)
            if resp.status_code == 200:
                data = resp.json()
                text_content = data["choices"][0]["message"]["content"]
                parsed = json.loads(text_content)
                return TipResponse(
                    takeaway=parsed.get("takeaway", ""),
                    suggested_swap=parsed.get("suggested_swap", ""),
                    is_ai_generated=True,
                    source_model="GPT-4o Mini",
                )
        except Exception:
            pass

    # 3. Fallback if LLM fails or keys fail
    return _build_deterministic_tip(
        food_name=food_name,
        is_processed=is_processed,
        goal_names=goal_names,
        verdict_tier=verdict_tier,
        failed_rules=failed_rules,
        passed_rules=passed_rules,
    )
