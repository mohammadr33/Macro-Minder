"""
Step 1 of the food-goal-checker tool: normalize raw API responses from
USDA FoodData Central and Open Food Facts into one common `Food` schema.

This is the messy-data-cleanup layer. Two different sources, two different
shapes, two different unit conventions -- this file is where that gets
resolved into one consistent structure the rest of the app can rely on.
"""

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
    "208": "calories",        # Energy (KCAL)
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
    for nutrient in raw.get("foodNutrients", []):
        field = USDA_NUTRIENT_MAP.get(nutrient.get("nutrientNumber"))
        if field:
            values[field] = nutrient.get("value")

    data_type = raw.get("dataType")
    # "Branded" = packaged/manufactured product. "SR Legacy", "Foundation",
    # "Survey (FNDDS)" = generic whole/minimally processed foods. Any other
    # or missing value -> unknown, not guessed.
    is_processed = (data_type == "Branded") if data_type else None

    return Food(
        name=raw.get("description", "Unknown"),
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

    return Food(
        name=raw.get("product_name", "Unknown"),
        source="off",
        serving_size_g=raw.get("serving_quantity"),
        calories=n.get("energy-kcal_100g"),
        protein_g=n.get("proteins_100g"),
        total_fat_g=n.get("fat_100g"),
        saturated_fat_g=n.get("saturated-fat_100g"),
        carbs_g=n.get("carbohydrates_100g"),
        sugar_g=n.get("sugars_100g"),
        fiber_g=n.get("fiber_100g"),
        sodium_mg=sodium_mg,
        cholesterol_mg=n.get("cholesterol_100g"),  # present sometimes, worth trying
        is_processed=is_processed,
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
