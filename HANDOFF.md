# Vueling AI Demo — Handoff

## How to start

```powershell
cd "C:\Users\jrubiosainz\OneDrive - Microsoft\Desktop\demos\vueling"
$env:BING_CONNECTION_NAME="bing-grounding"
uvicorn app.backend.main:app --host 127.0.0.1 --port 8765
```

Open: http://127.0.0.1:8765/

> **Backend already running** in the current session on port 8765.

## What it does

3 orchestrated agents streamed live to the UI via Server-Sent Events:

1. **Flight DB Agent** — Foundry Agent Service (`gpt-4.1`) + custom Python
   function tool that queries SQLite (`app/data/flights.db`, 25 routes) and
   returns the 5 lowest-occupancy flights as JSON.
2. **Events Agent (Bing Grounded)** — one Foundry agent per destination, each
   with the Bing Grounding tool, finds a real event in that city around the
   flight date (concerts, festivals, sports). Runs in parallel.
3. **Banner Agent (gpt-image-2)** — calls the Foundry image API for each
   flight, generates a 1536×1024 PNG banner with destination, event theme,
   price and Vueling branding (yellow #FFCC00). Files in `app/output/`.

## Two demo modes

The **"Fast demo mode"** checkbox (on by default) controls Agent 3:

| Mode | Agent 1 | Agent 2 | Agent 3 | Total |
|------|---------|---------|---------|-------|
| Fast (cached, default) | live (~20s) | live Bing (~7s) | reuses cached PNGs | **~35s** |
| Full live (uncheck box) | live | live Bing | calls gpt-image-2 live | **~10 min** ⚠️ |

Why slow live? `gpt-image-2` quota in this subscription is **2 RPM**, so 5
banners serialize over ~8 minutes. For the live demo, leave Fast mode ON —
Agents 1 & 2 still execute against real Foundry agents, so you keep all the
"wow" of live SQL agent + live Bing grounding.

## Talking points

- All 3 agents run on **Azure AI Foundry Agent Service** (project
  `jrubiosainz-8867`).
- Agent 1 shows **function calling** with a custom Python tool wrapping the
  internal flight DB.
- Agent 2 shows **Bing Grounding** — real-time public web data per city.
- Agent 3 shows **multimodal generation** with `gpt-image-2` directly from
  Foundry (model id `gpt-image-2`, deployment `gpt-image-2-1`).
- Backend is FastAPI + async; frontend is plain HTML/JS with SSE — every
  step you see in the UI is streamed live as the agents run.

## Files

- `app/backend/agents.py` — 3-agent orchestration (Foundry SDK + image REST)
- `app/backend/main.py` — FastAPI + SSE
- `app/frontend/{index.html,styles.css,app.js}` — Vueling-styled UI
- `app/data/flights.db` — seeded SQLite, 25 flights
- `app/output/banner_{1..5}.png` — pre-generated banners (cached mode)
- `app/.env` — credentials

## Fallbacks if something fails live

- If Foundry agents stall: `agents.py` falls back to direct SQL + placeholder
  events; the demo keeps going and banners still render.
- If Bing returns nothing: each city gets a generic "City Highlights" event.
- If image API 429s in live mode: retries 6× with backoff; cached PNGs are
  always available as a fallback.
- Backup image deployment: `gpt-image-1` in `saitama` resource (also tested
  working).

## Regenerating banners

If you want a fresh set of cached banners (e.g. different events):

```powershell
Remove-Item app/output/banner_*.png -Force
# Then run once with Fast mode UNCHECKED — wait ~10 min — banners cache to disk
```
