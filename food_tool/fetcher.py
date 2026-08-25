"""
Fetcher layer -- the only place in this project that talks to the network.
Everything here calls a live API, then hands the raw JSON to normalize.py
to turn into a clean Food object. Keeping network calls isolated here (and
out of normalize.py) means the normalization logic can be tested with
fixtures, with no internet required -- which is exactly what we were doing
in normalize.py's __main__ block.
"""

import os
import requests
from dotenv import load_dotenv
from normalize import from_usda, from_off, Food
import db

load_dotenv()  # reads a local .env file (if present) into the environment

USDA_API_KEY = os.environ.get("USDA_API_KEY")
if not USDA_API_KEY:
    raise RuntimeError(
        "USDA_API_KEY not set. Get a free key at "
        "https://fdc.nal.usda.gov/api-key-signup, then either:\n"
        "  - create a .env file with: USDA_API_KEY=your_key_here\n"
        "  - or run: export USDA_API_KEY=your_key_here   (Mac/Linux)\n"
        "            set USDA_API_KEY=your_key_here       (Windows cmd)"
    )

# Open Food Facts requires a descriptive User-Agent or it returns 403.
OFF_HEADERS = {
    "User-Agent": "FoodGoalChecker/0.1 (student project; contact: your_email@example.com)"
}


def fetch_usda_foods(query: str, page_size: int = 5) -> list[Food]:
    """Search USDA by food name, return a list of normalized Food objects.
    Best for generic/whole foods ("chicken breast", "granola bar").

    NOTE: deliberately not cached. Caching would only help repeat
    IDENTICAL query strings -- a name search always needs a live call to
    resolve "granola bar" to specific food entries in the first place,
    since there's no local name index to check against. Barcode lookups
    (below) are the case where caching has a clear, obvious payoff:
    the same barcode always means the same product."""
    url = "https://api.nal.usda.gov/fdc/v1/foods/search"
    params = {"query": query, "api_key": USDA_API_KEY, "pageSize": page_size}

    resp = requests.get(url, params=params, timeout=10)
    resp.raise_for_status()
    data = resp.json()

    foods = []
    for raw_food in data.get("foods", []):
        try:
            foods.append(from_usda(raw_food))
        except Exception as e:
            # One malformed entry shouldn't kill the whole search --
            # skip it and keep going, but don't fail silently either.
            print(f"Skipped a USDA result due to parse error: {e}")
    return foods


def fetch_off_product(barcode: str) -> Food | None:
    """Look up a single Open Food Facts product by barcode, return a
    normalized Food, or None if not found. Best for exact branded
    products, since OFF's search endpoint is currently broken (see
    DECISIONS.md) -- barcode lookup is the reliable path here.

    Cached: the same barcode always resolves to the same product, so a
    repeat lookup (e.g. checking the same snack again next week) hits the
    local cache instead of the network."""
    cache_key = db.cache_key_for("off", barcode)
    cached = db.get_cached_food(cache_key)
    if cached is not None:
        return cached

    url = f"https://world.openfoodfacts.org/api/v2/product/{barcode}.json"
    params = {"fields": "product_name,nutriments,serving_quantity,nova_group"}

    resp = requests.get(url, params=params, headers=OFF_HEADERS, timeout=10)
    resp.raise_for_status()
    data = resp.json()

    if data.get("status_verbose") != "product found":
        return None

    food = from_off(data["product"])
    db.cache_food(cache_key, food)
    return food


if __name__ == "__main__":
    db.init_db()

    print("=== USDA search: 'granola bar' ===")
    results = fetch_usda_foods("granola bar", page_size=3)
    for f in results:
        print(f.name, "-", f.calories, "kcal/100g -- missing:", f.missing_fields())

    print("\n=== OFF barcode lookup: Nutella ===")
    nutella = fetch_off_product("3017620422003")
    if nutella:
        print(nutella.name, "-", nutella.calories, "kcal/100g -- missing:", nutella.missing_fields())
    else:
        print("Not found")
