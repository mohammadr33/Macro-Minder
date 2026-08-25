"""
Concrete Goal definitions live here, separate from the Goal/Rule model
(goal.py). Splitting model from data means adding a new goal later is just
adding an entry here -- no engine code changes needed.

Thresholds below are sourced from real guidance where a clean per-serving
number exists. Where it doesn't (a couple of spots -- flagged explicitly),
a reasoned heuristic is used instead of a fabricated "official" number.
Sources:
  - FDA 21 CFR 101.62 nutrient content claim thresholds (per serving/RACC):
    low sodium <=140mg, low saturated fat <=1g, low cholesterol <=20mg,
    low calorie <=40kcal, low fat <=3g. "Good source" fiber >=2.5g,
    "high" fiber >=5g. "Good source" protein >=10%DV (5g), "excellent
    source" >=20%DV (10g), since FDA's protein DV is 50g.
  - AHA: sodium <=2300mg/day general, <=1500mg/day ideal for those with
    hypertension specifically.
  - ADA: no single sugar-gram ceiling is endorsed (their guidance focuses
    on fiber and overall carb quality instead) -- recommend >=14g fiber
    per 1000kcal.
"""

from goal import Goal, Rule, Comparison, RuleRole


GOALS: dict[str, Goal] = {}


def _register(goal: Goal):
    GOALS[goal.name] = goal
    return goal


# --- High Cholesterol Management ---------------------------------------
# Moderation: cholesterol, FDA "low cholesterol" claim threshold (<=20mg/serving).
# Demoted from BLOCKING to MODERATION -- current AHA/ACC guidance no
# longer sets a specific numeric daily cholesterol limit, shifting
# emphasis to saturated fat instead.
#
# Saturated fat: role now depends on whether the food is processed.
# BLOCKING for processed/packaged foods (default `role`) -- FDA "low
# saturated fat" claim (<=1g/serving) is a fair bar for manufactured
# products. MODERATION for whole foods (role_when_whole) -- found via
# testing that salmon (naturally ~3g sat fat/100g, from a food broadly
# considered heart-healthy due to omega-3s) was being wrongly AVOID-ed by
# the same strict threshold that correctly flags potato chips. Whole
# foods' naturally occurring saturated fat isn't the same signal as
# added/processed fat content.
# Bonus: fiber, FDA "good source of fiber" claim threshold (>=2.5g/serving).
_register(Goal(
    name="High Cholesterol Management",
    rules=[
        Rule(nutrient="cholesterol_mg", comparison=Comparison.MAX,
             threshold=20,  # FDA "low cholesterol" claim, 21 CFR 101.62
             role=RuleRole.MODERATION, label="Cholesterol"),
        Rule(nutrient="saturated_fat_g", comparison=Comparison.MAX,
             threshold=1,  # FDA "low saturated fat" claim, 21 CFR 101.62
             role=RuleRole.BLOCKING,  # default when processing status unknown
             role_when_whole=RuleRole.MODERATION,
             role_when_processed=RuleRole.BLOCKING,
             label="Saturated Fat"),
        Rule(nutrient="fiber_g", comparison=Comparison.MIN,
             threshold=2.5,  # FDA "good source of fiber" claim
             role=RuleRole.BONUS, label="Fiber"),
    ],
))

# --- Cutting / Weight Loss ----------------------------------------------
# NOTE: no FDA/AHA/ADA body sets an official "calories per snack while
# cutting" number -- this is a fitness goal, not a medical condition, so
# there's no equivalent regulatory source. Threshold below is a reasoned
# heuristic, not a sourced medical guideline:
#   A cutting diet commonly targets ~1500-1800 kcal/day. Split across
#   ~5 eating occasions (3 meals + 2 snacks), snack-sized items should
#   sit meaningfully below the per-meal average (~300-450kcal) -- 200kcal
#   is a reasonable snack-tier ceiling under that framing.
# KNOWN LIMITATION: this threshold assumes the food being checked is a
# snack-sized portion. It does not distinguish snack vs. meal vs.
# ingredient (e.g. a chicken sandwich vs. a tablespoon of olive oil) --
# a full meal will be flagged more harshly than is actually appropriate.
# Decided to punt on portion-type detection for the MVP (see DECISIONS.md)
# rather than add a manual selector or category-inference system now.
# Bonus: protein, FDA "good source of protein" claim (>=5g/serving, 10%DV).
_register(Goal(
    name="Cutting",
    rules=[
        Rule(nutrient="calories", comparison=Comparison.MAX,
             threshold=200,  # heuristic, reasoned above -- not an official source
             role=RuleRole.BLOCKING, label="Calories"),
        Rule(nutrient="protein_g", comparison=Comparison.MIN,
             threshold=5,  # FDA "good source of protein" claim, 10% DV
             role=RuleRole.BONUS, label="Protein"),
    ],
))

# --- High Blood Pressure / Sodium Management ----------------------------
# Blocking: sodium. Using FDA's "low sodium" per-serving claim (<=140mg)
# rather than deriving a number from AHA's daily total (1500mg/day for
# hypertension specifically) -- AHA only publishes a DAILY budget, not a
# per-food number, so dividing it ourselves (e.g. /4 meals) would be our
# own invented math, not a sourced figure. FDA's claim threshold is the
# most defensible single-food cutoff available.
_register(Goal(
    name="High Blood Pressure Management",
    rules=[
        Rule(nutrient="sodium_mg", comparison=Comparison.MAX,
             threshold=140,  # FDA "low sodium" claim, 21 CFR 101.62
             role=RuleRole.BLOCKING, label="Sodium"),
    ],
))

# --- Diabetes Management --------------------------------------------------
# NOTE: ADA does NOT endorse a hard sugar-gram ceiling -- their guidance
# centers on fiber intake (>=14g fiber per 1000kcal) and overall carb
# quality, not a strict sugar limit. This goal's structure reflects that:
# fiber is promoted to BLOCKING (the thing ADA actually emphasizes), sugar
# is demoted to BONUS (still useful signal, just not the medically-endorsed
# primary lever).
#
# Fiber uses a RATIO-based threshold (14g/1000kcal), not a flat number --
# see comment history/DECISIONS.md for why a flat number broke for foods
# far from the calorie count it was derived from.
#
# Fiber's role in diabetes management is slowing carbohydrate/sugar
# absorption -- it's only relevant when a food actually HAS meaningful
# carbs to slow down. A zero-carb food (plain chicken, eggs, etc.) was
# being wrongly flagged AVOID purely for lacking fiber it never needed in
# the first place. applies_when exempts the rule when carbs are minimal.
_register(Goal(
    name="Diabetes Management",
    rules=[
        Rule(nutrient="fiber_g", comparison=Comparison.MIN,
             ratio_per_1000kcal=14,  # ADA guidance: >=14g fiber per 1000kcal
             role=RuleRole.BLOCKING, label="Fiber",
             applies_when=("carbs_g", 5)),  # only relevant if food has >=5g carbs/serving
        Rule(nutrient="sugar_g", comparison=Comparison.MAX,
             threshold=10,  # heuristic, no ADA hard ceiling exists -- open to adjustment
             role=RuleRole.BONUS, label="Sugar"),
    ],
))

# --- Muscle Building / Bulking -------------------------------------------
# Blocking: protein, FDA "excellent source of protein" claim (>=10g/serving, 20%DV) --
# chosen over the lower "good source" (5g) tier since this goal specifically
# targets meaningful protein intake, not just "some."
# Bonus: calories minimum. Same gap as Cutting above (no official bulking-
# calorie source) -- reasoned similarly: a bulking diet commonly targets a
# ~300-500kcal/day surplus on top of maintenance; a snack contributing
# ~200kcal is a meaningful chunk of that surplus without assuming it's a
# full meal. Same portion-type limitation applies here as Cutting (see
# DECISIONS.md) -- this threshold assumes a snack-sized portion.
_register(Goal(
    name="Muscle Building",
    rules=[
        Rule(nutrient="protein_g", comparison=Comparison.MIN,
             threshold=10,  # FDA "excellent source of protein" claim, 20% DV
             role=RuleRole.BLOCKING, label="Protein"),
        Rule(nutrient="calories", comparison=Comparison.MIN,
             threshold=200,  # heuristic, reasoned above -- not an official source
             role=RuleRole.BONUS, label="Calories"),
    ],
))


if __name__ == "__main__":
    from normalize import Food

    nutella = Food(
        name="Nutella", source="off", serving_size_g=None,
        calories=539, protein_g=6.3, total_fat_g=30.9,
        saturated_fat_g=10.6, carbs_g=57.5, sugar_g=56.3,
        fiber_g=None, sodium_mg=41, cholesterol_mg=None,
    )

    print(f"Evaluating: {nutella.name}\n")
    for goal_name, goal in GOALS.items():
        result = goal.evaluate(nutella)
        print(f"[{goal_name}] -> {result.tier.value}")
        for r in result.rule_results:
            print(f"    [{r.confidence.value}] {r.reason}")
        print()
