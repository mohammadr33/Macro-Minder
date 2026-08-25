# Food Goal Checker

A tool that tells you whether a food fits a specific health/fitness goal
(high cholesterol management, diabetes management, cutting, muscle
building, blood pressure management) — and explains *why*, with a
confidence level, instead of just a yes/no.

## Why this exists

Nutrition labels give you numbers. They don't tell you whether those
numbers matter for *your* specific goal. This tool pulls real nutrition
data from public APIs, runs it through a rule engine calibrated against
real FDA/AHA/ADA guidance, and gives a plain verdict with reasoning.

## How it works

1. **Ingestion** (`fetcher.py`) — pulls live data from two sources:
   [USDA FoodData Central](https://fdc.nal.usda.gov/) (best for generic/
   whole foods, searched by name) and [Open Food Facts](https://world.openfoodfacts.org/)
   (best for exact branded products, looked up by barcode). Barcode
   lookups are cached locally (`db.py`) to avoid repeat network calls.

2. **Normalization** (`normalize.py`) — the two APIs disagree on units,
   reporting basis, and field names. This layer reconciles both into one
   consistent `Food` schema (per-100g basis, explicit handling of missing
   data — never silently treated as zero).

3. **Rule engine** (`goal.py`, `goals.py`) — each goal is a set of rules
   with a *role*:
   - **Blocking** — must pass, or the verdict is `AVOID`.
   - **Moderation** — matters, but only softens the verdict to
     `MODERATE` (used where current medical guidance treats something as
     a factor to watch rather than a hard limit — e.g. dietary
     cholesterol, per current AHA guidance).
   - **Bonus** — can only help, upgrading a verdict to `RECOMMENDED`.

   Rules also support: ratio-based thresholds (scaling with a food's own
   calorie count, not a flat number — used for ADA's fiber guidance),
   applicability preconditions (a rule can recognize it doesn't apply to
   a given food, e.g. fiber for a zero-carb food), and processing-level-
   aware roles (saturated fat blocks for processed snacks but only
   moderates for whole foods, using the NOVA food classification system).

4. **Persistence** (`db.py`) — SQLite cache for API lookups, plus a
   history log of every food/goal check made.

5. **Interface** (`cli.py`) — interactive terminal app tying it all
   together.

## Setup

```bash
pip install -r requirements.txt
```

Get a free USDA API key: https://fdc.nal.usda.gov/api-key-signup

Create a `.env` file in the project root (never commit this):
```
USDA_API_KEY=your_key_here
```
A `.env.example` is included as a template — copy it and fill in your key:
```bash
cp .env.example .env
```

Open Food Facts requires no key, just a descriptive User-Agent (already
set in `fetcher.py` — update the contact email if you want).

## Run it

```bash
python3 cli.py
```

## Design decisions

Every threshold, source, and structural decision in this project is
documented — including the ones that turned out to be wrong and were
fixed after testing against real data — in [`DECISIONS.md`](./DECISIONS.md).
That log is the more interesting read if you want to see the actual
reasoning behind the rule engine, not just the code.

## Known limitations

- No distinction yet between snack-sized, meal-sized, or ingredient-sized
  portions — calorie-based rules assume a snack-sized serving.
- A few thresholds (Cutting/Muscle Building calorie limits, Diabetes
  sugar bonus) are reasoned heuristics, not backed by an official
  body — flagged as such in `goals.py`.
- The SQLite cache has no expiry.

## Stack

Python, `requests`, `sqlite3`. No frameworks — deliberately kept simple
so the actual logic (normalization, rule engine) stays the focus.
