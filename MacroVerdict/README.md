# MacroVerdict

**A smart food & nutrition decision engine** that evaluates whether a food fits your specific health goals — and explains *why*, with granular rule breakdowns, confidence levels, and grounded AI insights. Not just a yes or no. An evidence-based verdict with complete transparency.

🌐 **[Live Demo](https://macro-verdict.vercel.app)** <!-- update with real URL after deploy -->

---

## Features

- 🔍 **Food search** — live lookup from USDA FoodData Central (100,000+ foods)
- 📷 **Barcode scanner** — scan any UPC/EAN barcode with your camera or upload a photo; product data pulled from Open Food Facts
- 🎯 **Multi-goal stacking** — check a food against multiple health goals simultaneously (e.g. High Cholesterol + Blood Pressure at once)
- 🧠 **Rule engine** — calibrated against AHA, ADA, and FDA guidelines; rules carry a *role* (blocking / moderation / bonus) so the verdict reflects real clinical nuance
- 💡 **AI tips** — Gemini-powered contextual suggestions grounded in the specific verdict and nutrient thresholds
- 📊 **History** — food and goal evaluation logs

### Supported Health Goals

| Goal | Primary Sources |
|---|---|
| High Cholesterol Management | AHA dietary guidelines |
| Blood Pressure Management | DASH diet / AHA |
| Diabetes Management | ADA Standards of Care |
| Cutting (Fat Loss) | ACSM / caloric principles |
| Muscle Building | ISSN protein guidelines |

---

## How it works

1. **Ingestion** (`fetcher.py`) — pulls live data from [USDA FoodData Central](https://fdc.nal.usda.gov/) (name search) and [Open Food Facts](https://world.openfoodfacts.org/) (barcode lookup). Results are cached in SQLite to avoid repeat API calls.

2. **Normalization** (`normalize.py`) — reconciles differing units, field names, and reporting bases into one consistent `Food` schema (per-100g, with explicit handling of missing data — never silently treated as zero).

3. **Rule engine** (`goal.py`, `goals.py`) — each goal is a set of rules with a *role*:
   - **Blocking** — must pass, or the verdict is `AVOID`
   - **Moderation** — softens verdict to `MODERATE` (used where guidance treats something as a factor to watch, not a hard limit — e.g. dietary cholesterol, per current AHA)
   - **Bonus** — can only upgrade a verdict to `RECOMMENDED`

   Rules also support: ratio-based thresholds (fiber scaled to calorie count per ADA guidance), applicability preconditions, and processing-level-aware roles (NOVA classification — saturated fat blocks for processed snacks but only moderates for whole foods).

4. **Persistence** (`db.py`) — SQLite cache for API lookups and a history log of every food/goal check.

5. **API** (`api.py`) — FastAPI backend exposing REST endpoints consumed by the React frontend.

6. **Frontend** (`frontend/`) — React + Vite SPA with barcode scanning via `@zxing/browser`.

---

## Stack

| Layer | Technology |
|---|---|
| Backend | Python, FastAPI, uvicorn |
| Frontend | React, Vite |
| Barcode scanning | `@zxing/browser` (ZXing port) |
| AI tips | Google Gemini API |
| Food data | USDA FoodData Central, Open Food Facts |
| Database | SQLite |
| Hosting | Render (API) + Vercel (frontend) |

---

## Local Development

### Backend

```bash
cd MacroVerdict
pip install -r requirements.txt
```

Create a `.env` file (see `.env.example`):
```
USDA_API_KEY=your_usda_key
GEMINI_API_KEY=your_gemini_key
```

Get a free USDA API key: https://fdc.nal.usda.gov/api-key-signup  
Get a free Gemini API key: https://aistudio.google.com/api-keys

```bash
uvicorn api:app --reload
# API running at http://localhost:8000
# Docs at http://localhost:8000/docs
```

### Frontend

```bash
cd MacroVerdict/frontend
npm install
npm run dev
# App running at http://localhost:5173
```

---

## Deployment

- **Backend** → [Render](https://render.com) — `render.yaml` is included; set `USDA_API_KEY` and `GEMINI_API_KEY` in the Render dashboard
- **Frontend** → [Vercel](https://vercel.com) — set root directory to `MacroVerdict/frontend` and add `VITE_API_BASE=https://<your-render-url>/api`

---

## Community & Discussions

Nutritional schemas and food databases are complex and evolving. If you notice a threshold that should be refined or want to suggest new features, join our [GitHub Discussions](https://github.com/mohammadr33/MacroVerdict/discussions).

---

## Known Limitations

- **Free Tier Cold Starts**: On Render's free tier, the web service spins down when idle. The initial request after inactivity may take ~30 seconds to wake up.
- No portion-size distinction (snack vs. meal vs. ingredient) — calorie-based rules assume snack-sized serving
- A few thresholds (Cutting/Muscle Building calorie limits, Diabetes sugar bonus) are reasoned heuristics, not backed by an official body — flagged in `goals.py`
- SQLite history resets on Render redeploys (ephemeral filesystem on free tier)

---

## Medical Disclaimer

MacroVerdict is designed solely for informational and educational purposes based on public health frameworks (AHA, ADA, FDA). **It does not provide medical, diagnostic, or clinical dietary advice.** Nutritional needs differ significantly based on individual health conditions; always consult a licensed physician or registered dietitian before making significant dietary changes.
