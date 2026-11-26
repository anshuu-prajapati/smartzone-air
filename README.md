# SmartZone Air: breathe smarter in smog season

**PromptWars Virtual · Challenge 6: Urban Air Quality Assistant**

SmartZone Air is a GenAI assistant that helps families in Indian cities get through winter smog. It combines the
**live air-quality forecast** for your city with **your routine** (commute, health sensitivities, cooking fuel, exercise
habits) and gives you a personal plan:

- **Low-emission daily routines:** the cleanest upcoming daytime window for outdoor exercise or errands.
- **Indoor air hacks:** purifier use, a DIY box-fan filter, when to keep windows shut, avoiding indoor sources.
- **Sustainable alternatives:** e.g. car → metro, with the **direct impact**: kg CO₂ saved per week and the % change in your on-road PM exposure.
- **Health protection** first for sensitive groups (children, elderly, asthma, COPD, heart conditions, pregnancy).
- **Ask anything:** a free-text question answered by AI, grounded in your data.

**Live app:** https://anshuu-prajapati.github.io/smartzone-air/ · **Demo video (2 min):** https://anshuu-prajapati.github.io/smartzone-air/demo.html

## How the GenAI is used
1. `app/planner.py` computes every fact deterministically: India NAQI (CPCB breakpoints) from PM2.5/PM10, clean
   forecast windows, and commute CO₂/exposure impact.
2. `app/llm.py` sends **only those facts** to **Google Gemini** (or Anthropic Claude, selected by env) with a strict system
   prompt: use only the given facts, never invent numbers, sensitive groups first, plain language, no diagnoses, and ignore prompt-injection attempts.
3. If no key is set or the AI call fails, a rule-based summary is shown, so the app never breaks.

## Two ways it runs
- **GitHub Pages (live link above):** fully static. `static/engine.js` is a line-for-line JS port of `aqi.py`, `planner.py` and
  `llm.py`, so the browser calls Open-Meteo directly and builds the same plan. A static site cannot keep a secret, so
  AI answers use a Gemini key the visitor pastes (kept only in that browser tab, sent only to Google); without one, the rule-based
  summary is shown. Deployed by `.github/workflows/pages.yml` on every push to `main`.
- **FastAPI server (Docker / Render / Cloud Run):** the full version, with the AI key held on the server, rate limiting and
  security headers. The frontend detects the backend through `/health` and uses `/api/plan` automatically.

## Architecture
```
Browser (accessible HTML/CSS/JS) ──POST /api/plan──► FastAPI
                                                     ├─ aqi.py     → Open-Meteo geocoding + air-quality forecast (15-min cache)
                                                     ├─ planner.py → NAQI, clean windows, actions + impact (pure, tested)
                                                     └─ llm.py     → Gemini / Claude explanation (grounded, with fallback)
```

## Quality checklist (the evaluation criteria)
- **Security:** keys only in env (sent in headers, never in URLs or to the browser); strict CSP and security headers; per-IP rate limit;
  Pydantic validation of every input; text rendered with `textContent` (no HTML injection); runs as a non-root Docker user.
- **Efficiency:** async httpx with a shared client, a 15-minute in-memory cache for geocoding and forecasts, a small payload, and no frontend framework.
- **Testing:** 34 pytest tests (unit + API, with upstream services and the AI mocked via respx) plus ruff in GitHub Actions CI.
- **Accessibility:** semantic landmarks, skip link, labelled fields and fieldsets, `aria-live` status, 44 px touch targets,
  visible focus, colour + text for AQI categories, a table alternative for the chart, dark mode, and reduced-motion support.

## Run locally
```bash
pip install -r requirements-dev.txt
cp .env.example .env   # optional: add GEMINI_API_KEY (free from Google AI Studio)
uvicorn app.main:app --reload
pytest -q
```

## Deploy
- **GitHub Pages:** automatic on push to `main` (Settings → Pages → Source: GitHub Actions).
- **Render:** New → Blueprint → select this repo (uses `render.yaml` + Dockerfile). Add `GEMINI_API_KEY`.
- **Google Cloud Run:** `gcloud run deploy smartzone-air --source . --set-env-vars GEMINI_API_KEY=...`
- Any Docker host works: `docker build -t sza . && docker run -p 8080:8080 -e GEMINI_API_KEY=... sza`

## Data and limitations
Air data: [Open-Meteo Air Quality API](https://open-meteo.com/en/docs/air-quality-api) (CAMS model forecasts; no key needed).
NAQI is estimated from hourly concentrations, not official 24-hour CPCB averages. CO₂ and exposure figures are indicative.
This is not medical advice.

## License
MIT
