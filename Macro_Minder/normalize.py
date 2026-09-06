"""
Step 1 of the food-goal-checker tool: normalize raw API responses from
USDA FoodData Central and Open Food Facts into one common `Food` schema.

This is the messy-data-cleanup layer. Two different sources, two different
shapes, two different unit conventions -- this file is where that gets
resolved into one consistent structure the rest of the app can rely on.
"""

import re
from dataclasses import dataclass
from typing import Optional


# Fields that are actual nutrient amounts (used by missing_fields() and
# per_serving() so we don't accidentally try to "convert" name/source/etc).
_NUTRIENT_FIELDS = [
    "calories", "protein_g", "total_fat_g", "saturated_fat_g",
    "carbs_g", "sugar_g", "fiber_g", "sodium_mg", "cholesterol_mg",
]


@dataclass
class Food:
    """All nutrient fields here are stored PER 100g. This is the one basis
    both USDA and Open Food Facts can reliably provide -- per-serving data
    is NOT guaranteed from either source (see: OFF's barcode endpoint
    dropping _serving fields entirely). Per-serving amounts are computed
    on demand via per_serving(), using serving_size_g when available."""
    name: str
    source: str          # "usda" or "off"
    serving_size_g: Optional[float]
    calories: Optional[float]
    protein_g: Optional[float]
    total_fat_g: Optional[float]
    saturated_fat_g: Optional[float]
    carbs_g: Optional[float]
    sugar_g: Optional[float]
    fiber_g: Optional[float]
    sodium_mg: Optional[float]
    cholesterol_mg: Optional[float]
    # Whether this food is processed/packaged vs. whole/minimally
    # processed. NOT a guess -- derived from real classification signals:
    # OFF's NOVA group (established food-processing classification,
    # Monteiro et al.) where group >=4 = ultra-processed, or USDA's
    # dataType field ("Branded" = packaged product, "SR Legacy" /
    # "Foundation" / "Survey (FNDDS)" = generic whole/minimally processed
    # food). None when neither source gives us a usable signal --
    # deliberately NOT guessed, same missing-data philosophy as nutrients.
    is_processed: Optional[bool] = None
    # Raw category text from the source (USDA's foodCategory, or OFF's
    # categories_tags). Used to auto-detect whether a food is a raw
    # cooking ingredient (oil, flour, spices) vs. a regular snack/product --
    # this drives portion-type inference so the user doesn't have to
    # manually specify it (see goal.py's infer_portion_type()).
    food_category: Optional[str] = None

    def missing_fields(self):
        """Which nutrients we don't actually have data for.
        Important for the rule engine later -- we don't want to silently
        treat 'missing' as 'zero', since that could wrongly mark an
        unhealthy food as safe."""
        return [f for f in _NUTRIENT_FIELDS if getattr(self, f) is None]

    def per_serving(self) -> dict:
        """Compute per-serving nutrient amounts from the per-100g base.
        Returns a dict rather than a new Food, since 'per serving' is a
        view/presentation concern, not a new source of truth.

        If serving_size_g is unknown, returns per-100g values instead and
        flags that with 'basis': the caller (UI/rule engine) decides how
        to communicate that to the user rather than silently mislabeling
        100g amounts as a serving."""
        if self.serving_size_g is None:
            result = {f: getattr(self, f) for f in _NUTRIENT_FIELDS}
            result["basis"] = "per_100g (serving size unknown)"
            return result

        factor = self.serving_size_g / 100
        result = {}
        for f in _NUTRIENT_FIELDS:
            val = getattr(self, f)
            result[f] = round(val * factor, 2) if val is not None else None
        result["basis"] = f"per_serving ({self.serving_size_g}g)"
        return result


# USDA identifies nutrients by a stable numeric ID, not by name (names are
# inconsistent -- e.g. "Fatty acids, total saturated" vs "saturated-fat").
# These IDs come from the `nutrientNumber` field in each foodNutrients entry.
USDA_NUTRIENT_MAP = {
    "208": "calories",        # Energy (KCAL) - Standard / Legacy
    "1008": "calories",       # Energy (KCAL) - Foundation Foods
    "958": "calories",        # Energy (Atwater Specific Factors)
    "957": "calories",        # Energy (Atwater General Factors)
    "203": "protein_g",       # Protein
    "204": "total_fat_g",     # Total lipid (fat)
    "606": "saturated_fat_g", # Fatty acids, total saturated
    "205": "carbs_g",         # Carbohydrate, by difference
    "269": "sugar_g",         # Total Sugars
    "291": "fiber_g",         # Fiber, total dietary
    "307": "sodium_mg",       # Sodium, Na (already in mg from USDA)
    "601": "cholesterol_mg",  # Cholesterol (already in mg from USDA)
}


def from_usda(raw: dict) -> Food:
    """Convert one raw USDA foodSearch result into a normalized Food.
    Verified (via USDA docs + sanity-checking sample data) that the
    foodNutrients array reports values on a per-100g basis, even though
    USDA separately tracks a per-labeled-serving basis too. This lines up
    with our schema's per-100g base, so no rescaling needed here --
    servingSize is stored separately purely for computing per_serving()."""
    values = {}
    calories_val = None
    for nutrient in raw.get("foodNutrients", []):
        num = str(nutrient.get("nutrientNumber"))
        val = nutrient.get("value")
        if val is not None:
            if num == "208":
                calories_val = val
            elif num in ("1008", "958", "957") and calories_val is None:
                calories_val = val
            field = USDA_NUTRIENT_MAP.get(num)
            if field and field != "calories":
                values[field] = val
    if calories_val is not None:
        values["calories"] = calories_val
    elif values.get("protein_g") is not None and values.get("total_fat_g") is not None:
        # Standard 4-9-4 Atwater formula if energy number wasn't explicitly labeled
        carbs = values.get("carbs_g") or 0.0
        values["calories"] = round(values["protein_g"] * 4 + values["total_fat_g"] * 9 + carbs * 4, 1)

    # Physical / Atwater macro consistency for zero-energy or zero-macro items
    cal_check = values.get("calories")
    if cal_check == 0 or (cal_check is not None and cal_check <= 0):
        for f in ["total_fat_g", "saturated_fat_g", "carbs_g", "sugar_g", "protein_g", "fiber_g", "cholesterol_mg"]:
            if values.get(f) is None:
                values[f] = 0.0

    if values.get("total_fat_g") == 0 and values.get("saturated_fat_g") is None:
        values["saturated_fat_g"] = 0.0
    if values.get("carbs_g") == 0:
        if values.get("sugar_g") is None:
            values["sugar_g"] = 0.0
        if values.get("fiber_g") is None:
            values["fiber_g"] = 0.0

    data_type = raw.get("dataType")
    food_category = raw.get("foodCategory") or ""
    description = raw.get("description") or ""
    ingredients = raw.get("ingredients") or ""

    desc_lower = description.lower()
    cat_lower = food_category.lower()
    ing_lower = ingredients.lower()

    # USDA Whole Produce & Staple Categories
    # In USDA, "Branded" merely means an item was submitted by a commercial distributor/label.
    # Many branded entries are simply bagged whole produce (e.g. bagged apples) or dry single-ingredient grains.
    # We inspect categories and ingredients so whole foods are not falsely labeled ultra-processed.
    WHOLE_FOOD_CATEGORIES = {
        "pre-packaged fruit & vegetables",
        "produce",
        "fresh vegetables",
        "fresh fruit",
        "fresh produce",
        "apples",
        "bananas",
        "berries",
        "citrus fruits",
        "stone fruits",
        "melons",
        "dark green leafy vegetables",
        "vegetables and vegetable products",
        "dry beans, peas, other legumes",
        "dry rice",
        "rice",
    }

    PROCESSED_CATEGORIES = {
        "fast foods", "cookies and brownies", "baked products", "sweets", "candy",
        "snacks", "snack foods", "ice cream", "carbonated beverages", "cakes and pies",
        "doughnuts, sweet rolls, pastries", "crackers",
    }

    has_added_sugar_or_oil = bool(
        re.search(r"\boils?\b", ing_lower)
        or any(w in ing_lower for w in ["cane sugar", "corn syrup", "dextrose", "preservative", "emulsifier"])
    )

    RAW_NUT_SEED_WORDS = {
        "walnut", "walnuts", "almond", "almonds", "pecan", "pecans", "pistachio", "pistachios",
        "cashew", "cashews", "chia seed", "chia seeds", "flax seed", "flaxseed", "flax seeds",
        "pumpkin seed", "pumpkin seeds", "sunflower seed", "sunflower seeds", "sesame seed", "sesame seeds"
    }

    if any(w in desc_lower for w in ["candied", "in heavy syrup", "glazed with sugar"]):
        is_processed = True
    elif any(w in ing_lower.strip().rstrip(".").lower() for w in RAW_NUT_SEED_WORDS) and not has_added_sugar_or_oil:
        is_processed = False
    elif any(w in desc_lower for w in RAW_NUT_SEED_WORDS) and not has_added_sugar_or_oil and "candy" not in cat_lower:
        is_processed = False
    elif any(cat in cat_lower for cat in PROCESSED_CATEGORIES):
        is_processed = True
    elif cat_lower in WHOLE_FOOD_CATEGORIES:
        if ing_lower and has_added_sugar_or_oil:
            is_processed = True
        else:
            is_processed = False
    elif any(w in desc_lower for w in [", raw", "raw,", "fresh "]):
        is_processed = False
    elif data_type == "Branded":
        # Plain bottled water is not ultra-processed
        is_plain_water = any(w in desc_lower for w in ["water", "spring water", "mineral water", "drinking water"]) or "water" in cat_lower
        if is_plain_water and not has_added_sugar_or_oil:
            is_processed = False
        else:
            # Check single-ingredient whole grains/legumes/produce in description
            is_whole_grain_desc = any(w in desc_lower for w in [
                "brown rice", "rolled oats", "steel cut oats", "quinoa", "dry black beans", "dry lentils",
                "walnuts", "almonds", "pecans", "pistachios", "chia seeds"
            ])
            if is_whole_grain_desc and not has_added_sugar_or_oil:
                is_processed = False
            else:
                is_processed = True
    elif data_type in ("SR Legacy", "Foundation", "Survey (FNDDS)"):
        is_processed = False
    else:
        is_processed = None

    # Append brand name when available so users can distinguish identical product names
    brand = raw.get("brandName") or raw.get("brandOwner")
    raw_desc = raw.get("description", "Unknown")
    if brand and brand.lower() not in raw_desc.lower():
        display_name = f"{raw_desc} ({brand})"
    else:
        display_name = raw_desc

    return Food(
        name=display_name,
        source="usda",
        serving_size_g=raw.get("servingSize"),
        calories=values.get("calories"),
        protein_g=values.get("protein_g"),
        total_fat_g=values.get("total_fat_g"),
        saturated_fat_g=values.get("saturated_fat_g"),
        carbs_g=values.get("carbs_g"),
        sugar_g=values.get("sugar_g"),
        fiber_g=values.get("fiber_g"),
        sodium_mg=values.get("sodium_mg"),
        cholesterol_mg=values.get("cholesterol_mg"),
        is_processed=is_processed,
        food_category=raw.get("foodCategory"),
    )


def from_off(raw: dict) -> Food:
    """Convert one raw Open Food Facts product into a normalized Food.

    Key gotchas handled here:
    - We pull the *_100g fields, not *_serving. Confirmed via live testing
      that _serving fields are present on the search endpoint's response
      shape but MISSING entirely on the barcode/product endpoint's
      response shape -- same API, inconsistent shape depending on which
      endpoint you hit. Standardizing on *_100g avoids depending on a
      field that isn't reliably there.
    - OFF reports salt/sodium in GRAMS, but our schema (matching USDA)
      uses MILLIGRAMS for sodium -- so we convert (x1000).
    - serving_size_g comes from a separate top-level field ("serving_quantity")
      when OFF provides it, used later to compute per-serving views on demand.
    - Cholesterol often isn't present in OFF's nutriments at all -- left as
      None rather than guessed at.
    """
    n = raw.get("nutriments", {})

    sodium_g = n.get("sodium_100g")
    sodium_mg = sodium_g * 1000 if sodium_g is not None else None

    # NOVA group is typically a top-level field on the product object for
    # real barcode lookups (1-4, established food-processing classification).
    # >=4 = ultra-processed. Not guessed when absent.
    nova_group = raw.get("nova_group")
    is_processed = (nova_group >= 4) if isinstance(nova_group, (int, float)) else None

    # OFF's categories_tags is a list like ["en:fats", "en:vegetable-fats-and-oils"].
    # Join and clean into readable text ("fats, vegetable fats and oils") --
    # same purpose as USDA's foodCategory, used for portion-type inference.
    categories_tags = raw.get("categories_tags")
    food_category = None
    if categories_tags:
        cleaned = [tag.split(":", 1)[-1].replace("-", " ") for tag in categories_tags]
        food_category = ", ".join(cleaned)

    calories = n.get("energy-kcal_100g")
    total_fat = n.get("fat_100g")
    sat_fat = n.get("saturated-fat_100g")
    carbs = n.get("carbohydrates_100g")
    sugar = n.get("sugars_100g")
    fiber = n.get("fiber_100g")
    protein = n.get("proteins_100g")
    cholesterol = n.get("cholesterol_100g")

    # Physical / Atwater macro consistency for zero-energy or zero-macro items
    if calories == 0 or (calories is not None and calories <= 0):
        if total_fat is None: total_fat = 0.0
        if sat_fat is None: sat_fat = 0.0
        if carbs is None: carbs = 0.0
        if sugar is None: sugar = 0.0
        if fiber is None: fiber = 0.0
        if protein is None: protein = 0.0
        if cholesterol is None: cholesterol = 0.0

    if total_fat == 0 and sat_fat is None:
        sat_fat = 0.0
    if carbs == 0:
        if sugar is None: sugar = 0.0
        if fiber is None: fiber = 0.0

    # Plain bottled / mineral / spring water is whole / minimally processed
    is_water = False
    if food_category:
        cat_lower = food_category.lower()
        if any(w in cat_lower for w in ["waters", "spring waters", "mineral waters"]):
            is_water = True
    prod_name = raw.get("product_name", "").lower()
    if any(w in prod_name for w in ["spring water", "mineral water", "natural water", "eau minérale", "eau minerale", "bottled water"]):
        is_water = True

    if is_water and (sugar is None or sugar == 0) and (calories is None or calories == 0):
        is_processed = False

    return Food(
        name=raw.get("product_name", "Unknown"),
        source="off",
        serving_size_g=raw.get("serving_quantity"),
        calories=calories,
        protein_g=protein,
        total_fat_g=total_fat,
        saturated_fat_g=sat_fat,
        carbs_g=carbs,
        sugar_g=sugar,
        fiber_g=fiber,
        sodium_mg=sodium_mg,
        cholesterol_mg=cholesterol,  # present sometimes, worth trying
        is_processed=is_processed,
        food_category=food_category,
    )


if __name__ == "__main__":
    # Real sample data from the USDA response you pasted (2nd granola bar --
    # the Bakehouse one, since it has more complete data than the first).
    usda_sample = {
        "description": "GRANOLA BAR",
        "servingSize": 43,
        "foodNutrients": [
            {"nutrientNumber": "203", "value": 9.3},
            {"nutrientNumber": "204", "value": 23.3},
            {"nutrientNumber": "205", "value": 55.8},
            {"nutrientNumber": "208", "value": 465},
            {"nutrientNumber": "269", "value": 27.9},
            {"nutrientNumber": "291", "value": 7},
            {"nutrientNumber": "307", "value": 35},
            {"nutrientNumber": "601", "value": 12},
            {"nutrientNumber": "606", "value": 5.81},
        ],
    }

    # Real sample data from the Open Food Facts BARCODE lookup you ran
    # (Nutella, 3017620422003) -- this endpoint only returns _100g fields,
    # not _serving fields, which is exactly why we standardized on 100g.
    off_sample = {
        "product_name": "Nutella",
        "nutriments": {
            "energy-kcal_100g": 539,
            "proteins_100g": 6.3,
            "fat_100g": 30.9,
            "carbohydrates_100g": 57.5,
            # saturated-fat, sugars, fiber, sodium truncated in the sample
            # you pasted -- left out here since we don't actually have
            # them, which is realistic and worth seeing missing_fields()
            # catch correctly below.
        },
    }

    usda_food = from_usda(usda_sample)
    off_food = from_off(off_sample)

    print("--- USDA normalized ---")
    print(usda_food)
    print("Missing fields:", usda_food.missing_fields())

    print("\n--- Open Food Facts normalized ---")
    print(off_food)
    print("Missing fields:", off_food.missing_fields())