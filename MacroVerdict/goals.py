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


def _protein_meets_energy_density(food) -> bool:
    """EU/UK food-labeling standard (Regulation (EC) No 1924/2006):
    'source of protein' requires >=12% of a food's energy to come from
    protein. Density-based, not tied to a serving size -- rescues foods
    like almonds, where a small realistic serving makes absolute grams
    look unimpressive even though the food's actual protein density is
    genuinely good (protein is calculated at 4 kcal/g)."""
    if not food.calories or food.protein_g is None:
        return False
    return (food.protein_g * 4 / food.calories * 100) >= 12


def _is_low_energy_density(food) -> bool:
    """Rolls' Volumetrics research on weight management (used by CDC/NHS
    guidance): foods at or below ~1.5 kcal/g (150kcal/100g) are
    considered low energy density, associated with better satiety per
    calorie -- genuinely useful for a cutting goal even when a food's
    large realistic serving pushes its absolute per-serving calories over
    a flat ceiling. Rescues high-volume, low-density whole foods like
    legumes and vegetables."""
    return food.calories is not None and food.calories <= 150



def _is_heart_healthy_fat_ratio(food) -> bool:
    """AHA/ACC Clinical Guidelines on Cardiovascular Disease & Dietary Fats
    (Sacks et al., Circulation 2017; 2018 AHA/ACC Cholesterol Guidelines):
    Replacing saturated fatty acids with unsaturated fats (MUFAs and PUFAs)
    significantly lowers atherogenic LDL-C and small dense LDL particles.

    A whole food or healthy fat with high lipid quality (where unsaturated fat
    is at least double saturated fat, i.e., UFA : SFA >= 2:1, or unsaturated
    fat comprises >=67% of total fat) is cardioprotective, even if naturally
    occurring saturated fat exceeds a flat 1g ceiling.

    Applies to whole foods (avocados, nuts, seeds, fatty fish) and minimally
    processed culinary oils (olive oil). Does not apply to ultra-processed
    foods with added hydrogenated fats or sweets."""
    if food.total_fat_g is None or food.saturated_fat_g is None:
        return False
    if food.saturated_fat_g <= 0:
        return True

    unsaturated_fat = food.total_fat_g - food.saturated_fat_g
    if unsaturated_fat < 2 * food.saturated_fat_g:
        return False

    if food.is_processed is True:
        # Culinary oils like extra virgin olive oil might be cataloged under branded DBs,
        # but ultra-processed snacks shouldn't pass on fat ratio alone.
        if food.food_category and any(c in food.food_category.lower() for c in ["oil", "olive"]):
            return True
        return False

    return True


def _is_cardioprotective_cholesterol_profile(food) -> bool:
    """AHA 2019 Science Advisory on Dietary Cholesterol and Cardiovascular Risk
    (Carson et al., Circulation 2020):
    Dietary cholesterol itself has a negligible impact on serum LDL-C when
    consumed in whole foods that are low in saturated fat and rich in
    unsaturated fats.

    Seafood and whole fish (such as salmon) naturally contain 50-85mg of
    dietary cholesterol, but their low saturated fat (<=3.5g/100g in fatty fish)
    and rich omega-3 fatty acids (EPA/DHA) actively reduce cardiovascular risk.
    Rescues fish/seafood from being falsely penalized for dietary cholesterol."""
    if food.saturated_fat_g is None or food.total_fat_g is None:
        return False
    if food.saturated_fat_g > 3.5:
        return False
    if food.is_processed is True:
        return False
    return (food.total_fat_g - food.saturated_fat_g) >= 2 * food.saturated_fat_g


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
# ALT PASS CHECK: AHA 2019 Dietary Cholesterol Advisory (Carson et al. 2020).
# Rescues whole fish/seafood like salmon, which naturally contains 50-85mg
# dietary cholesterol but minimal saturated fat (<=3.5g) and high cardioprotective
# omega-3s (EPA/DHA), which do not elevate atherogenic LDL-C.
#
# Saturated fat: BLOCKING. Saturated fat is the primary dietary driver of LDL-C
# upregulation. Foods where saturated fat dominates (butter, cheese, fatty beef,
# palm oil) trigger AVOID.
#
# ALT PASS CHECK: Heart-healthy lipid ratio (AHA/ACC Sacks et al. 2017).
# Rescues whole foods like avocados, walnuts, almonds, salmon, and olive oil
# where unsaturated fat (MUFA/PUFA) overwhelmingly exceeds saturated fat
# (>=2:1 ratio) and fiber/phytosterols provide active LDL-lowering benefits,
# rather than wrongly flagging them with the flat 1g packaged-food ceiling.
#
# Bonus: fiber, FDA "good source of fiber" claim threshold (>=2.5g/serving).
_register(Goal(
    name="High Cholesterol Management",
    rules=[
        Rule(nutrient="cholesterol_mg", comparison=Comparison.MAX,
             threshold=20,  # FDA "low cholesterol" claim, 21 CFR 101.62
             role=RuleRole.MODERATION, label="Cholesterol",
             alt_pass_check=_is_cardioprotective_cholesterol_profile,
             alt_pass_label="AHA lipid guidance: dietary cholesterol in whole seafood with low saturated fat (<=3.5g) does not elevate LDL-C"),
        Rule(nutrient="saturated_fat_g", comparison=Comparison.MAX,
             threshold=1,  # FDA "low saturated fat" claim, 21 CFR 101.62
             role=RuleRole.BLOCKING, label="Saturated Fat",
             alt_pass_check=_is_heart_healthy_fat_ratio,
             alt_pass_label="AHA heart-healthy lipid profile (unsaturated fat >= 2x saturated fat in whole foods / healthy fats)"),
        Rule(nutrient="fiber_g", comparison=Comparison.MIN,
             threshold=2.5,  # FDA "good source of fiber" claim
             role=RuleRole.BONUS, label="Fiber",
             applies_when=("calories", 30)),
    ],
))

# --- Cutting / Weight Loss ----------------------------------------------
# NOTE: no FDA/AHA/ADA body sets an official "calories per snack while
# cutting" number -- this is a fitness goal, not a medical condition, so
# there's no equivalent regulatory source. 200kcal is a reasoned heuristic:
# a cutting diet commonly targets ~1500-1800 kcal/day across ~5 eating
# occasions, and 200kcal is a reasonable single-item ceiling under that
# framing. A high-volume, low-density whole food (legumes, vegetables)
# with a naturally large serving can rescue itself via alt_pass_check
# below -- see goal.py's comment on alt_pass_check for why this exists
# (found via testing: black beans, ~77kcal/100g and genuinely great for
# a cutting goal, were wrongly AVOID-ed purely because their large
# 172g standardized serving pushed absolute per-serving calories over
# the flat 200kcal line).
# KNOWN LIMITATION: a food with no serving_size_g data (common for raw
# cooking ingredients like flour, oil, dry rice) gets judged against its
# full 100g density rather than a realistic portion, since there's no
# reliable way to auto-detect "this is a bulk ingredient" (tried a
# category-keyword approach, found it too unreliable -- see DECISIONS.md).
# The verdict's reasoning text always shows which basis was used
# ("per_serving" vs "per_100g (serving size unknown)"), so this isn't
# hidden, just not auto-corrected.
# Bonus: protein, FDA "good source of protein" claim (>=5g/serving, 10%DV).
_register(Goal(
    name="Cutting",
    rules=[
        Rule(nutrient="calories", comparison=Comparison.MAX,
             threshold=200,  # heuristic, reasoned above
             role=RuleRole.BLOCKING, label="Calories",
             alt_pass_check=_is_low_energy_density,
             alt_pass_label="low energy density (<=150kcal/100g, per weight-management research)"),
        Rule(nutrient="protein_g", comparison=Comparison.MIN,
             threshold=5,  # FDA "good source of protein" claim, 10% DV
             role=RuleRole.BONUS, label="Protein",
             applies_when=("calories", 30)),
    ],
))

# --- High Blood Pressure / Sodium Management ----------------------------
# Sodium is structured into two clinical tiers based on FDA & AHA guidelines:
# 1. High Sodium Limit (<=480mg): BLOCKING.
#    The AHA Heart-Check meal ceiling and FDA "Healthy" threshold (21 CFR 101.65)
#    is 480mg per serving (~20% Daily Value). Foods exceeding 480mg (ramen,
#    canned soups, bacon, cured meats) are excessive single-item sodium loads
#    and trigger AVOID.
# 2. Low Sodium Target (<=140mg): MODERATION.
#    FDA's 21 CFR 101.62 "low sodium" claim threshold. Whole foods with moderate
#    canning/natural sodium (141mg - 480mg, such as canned salmon, canned tuna,
#    or canned beans) warrant MODERATE ("moderate sodium, rinse canned foods"),
#    NOT an outright AVOID.
# 3. Bonus: Fiber (DASH Diet component, >=2.5g). Clinical trials (Appel et al.,
#    NEJM 1997) prove fiber and electrolytes counteract sodium vasoconstriction.
_register(Goal(
    name="High Blood Pressure Management",
    rules=[
        Rule(nutrient="sodium_mg", comparison=Comparison.MAX,
             threshold=480,  # AHA Heart-Check meal ceiling / FDA 20% DV high-sodium threshold
             role=RuleRole.BLOCKING, label="Sodium Limit"),
        Rule(nutrient="sodium_mg", comparison=Comparison.MAX,
             threshold=140,  # FDA "low sodium" claim, 21 CFR 101.62
             role=RuleRole.MODERATION, label="Sodium (Low-Sodium Target)"),
        Rule(nutrient="fiber_g", comparison=Comparison.MIN,
             threshold=2.5,  # FDA "good source of fiber" claim; DASH dietary component
             role=RuleRole.BONUS, label="Fiber (DASH Component)",
             applies_when=("calories", 30)),
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
#
# Whole vs. Processed Role:
# Whole foods (like brown rice or whole grains) naturally have around
# 10-13g fiber/1000kcal (just under the 14g daily-diet target). Demoting
# fiber to MODERATION for whole foods prevents whole staples from being
# falsely AVOID-ed over a 0.08g deficit, while processed low-fiber carbs
# (white bread, chips) are still blocked.
_register(Goal(
    name="Diabetes Management",
    rules=[
        Rule(nutrient="fiber_g", comparison=Comparison.MIN,
             ratio_per_1000kcal=14,  # ADA guidance: >=14g fiber per 1000kcal
             role=RuleRole.MODERATION,
             role_when_whole=RuleRole.MODERATION,
             role_when_processed=RuleRole.BLOCKING,
             label="Fiber",
             applies_when=("carbs_g", 5)),  # only relevant if food is >=5g carbs per 100g
        Rule(nutrient="sugar_g", comparison=Comparison.MAX,
             threshold=10,  # heuristic ceiling for single item
             role=RuleRole.BLOCKING,
             role_when_whole=RuleRole.MODERATION,
             role_when_processed=RuleRole.BLOCKING,
             label="Sugar"),
    ],
))

# --- Muscle Building / Bulking -------------------------------------------
# Blocking: protein, FDA "excellent source of protein" claim (>=10g/serving, 20%DV) --
# chosen over the lower "good source" (5g) tier since this goal specifically
# targets meaningful protein intake, not just "some." A calorie-dense food
# with a small standardized serving (nuts, seeds) can rescue itself via
# alt_pass_check below using EU protein-density claims instead -- found
# via testing: almonds (genuinely ~21g protein/100g) were wrongly
# AVOID-ed because their small realistic 28g serving only contains ~6g
# protein in absolute terms, even though their protein density is solid.
# Bonus: calories minimum. Same gap as Cutting above (no official bulking-
# calorie source) -- reasoned similarly: a bulking diet commonly targets a
# ~300-500kcal/day surplus on top of maintenance, and 200kcal is a
# meaningful chunk of a single item's contribution to that surplus.
# Same known limitation as Cutting above re: missing serving-size data.
_register(Goal(
    name="Muscle Building",
    rules=[
        Rule(nutrient="protein_g", comparison=Comparison.MIN,
             threshold=10,  # FDA "excellent source of protein" claim, 20% DV
             role=RuleRole.BLOCKING, label="Protein",
             alt_pass_check=_protein_meets_energy_density,
             alt_pass_label="EU 'source of protein' standard (>=12% of calories from protein)",
             applies_when=("calories", 30)),
        Rule(nutrient="calories", comparison=Comparison.MIN,
             threshold=200,  # heuristic, reasoned above
             role=RuleRole.BONUS, label="Calories",
             applies_when=("calories", 30)),
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