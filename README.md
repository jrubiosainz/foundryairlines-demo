# FoundryAirlines Sales Demo — 3-Agent Orchestration with Azure AI Foundry, Bing Grounding, and gpt-image-2

![demo](docs/screenshot.png)

## What It Does

This demo showcases a real-time multi-agent AI sales pipeline that discovers low-occupancy flights, researches destination events, and generates promotional banners—all orchestrated through FastAPI with live streaming updates.

**Agent pipeline:**
- **Agent 1** (Flight Analyst): Azure AI Foundry agent with custom SQLite function tool queries the internal flight database to find the 5 flights with the lowest occupancy.
- **Agent 2** (Event Finder): Direct Bing Web Search API call retrieves event data for each destination, then Azure AI Foundry agent extracts the most relevant event with reasoning.
- **Agent 3** (Banner Generator): Direct REST call to gpt-image-2 generates a 1536×1024 promotional banner for each flight, themed to the event, with FoundryAirlines branding and current price.

Each agent's progress streams to the browser in real-time via Server-Sent Events (SSE), and all banners are displayed as they complete.

## Architecture

```
┌─────────────────────────────────────────────────────────────┐
│                       Browser (UI)                          │
│     FoundryAirlines palette: #FFCC00 yellow, Inter font     │
└──────────────────────┬──────────────────────────────────────┘
                       │ SSE /api/run
                       ▼
┌─────────────────────────────────────────────────────────────┐
│                 FastAPI (port 8765)                         │
│              uvicorn app.backend.main:app                   │
└──────────────────────┬──────────────────────────────────────┘
                       │
                       ▼
        ┌──────────────────────────────┐
        │   orchestrate() generator    │
        │  (app/backend/agents.py)     │
        └──────────────┬───────────────┘
                       │
        ┌──────────────┴───────────────────┐
        │                                  │
        ▼                                  ▼
┌─────────────────────────────┐   ┌─────────────────────┐
│  Foundry Responses API      │   │  Post-workflow      │
│  shared `conversation`      │   │  fan-out (asyncio)  │
│                             │   │                     │
│  Agent 1 ──► Agent 2        │   │  Agent 3 (image)    │
│  flights    events          │   │  gpt-image-2 REST   │
│  (Foundry prompt agents)    │   │  (5 banners ‖=2)    │
└─────────────────────────────┘   └─────────────────────┘
        │                                  │
        ▼                                  ▼
   SQLite DB (flights.db)            output/banner_*.png
```

**Orchestration:** Native Foundry **prompt agents** (declarative
`PromptAgentDefinition`) chained sequentially via the **Responses API**.
Both agents share a single Foundry `conversation` so agent #2 sees agent #1's
output as conversation history — sequential multi-agent orchestration done
inside Foundry, no external orchestrator framework required. Banner
generation runs as a concurrent post-workflow step because gpt-image-2 is an
image model, not a chat agent. References:
<https://learn.microsoft.com/en-us/azure/foundry/agents/quickstarts/prompt-agent?tabs=python>

## Repository Layout

```
C:\Users\jrubiosainz\OneDrive - Microsoft\Desktop\demos\vueling\
├── README.md                          # This file
├── requirements.txt                   # Python dependencies
├── .gitignore                         # Git exclusions
├── .squad/                            # Squad orchestration
│   ├── agents/
│   │   └── maverick/
│   │       └── history.md             # Session notes
│   ├── decisions.md                   # Architectural decisions
│   └── ...
├── app/
│   ├── .env.example                   # Environment variable template
│   ├── .env                           # (git-ignored) secrets
│   ├── backend/
│   │   ├── main.py                    # FastAPI app, static routes
│   │   ├── agents.py                  # 3-agent orchestration
│   │   ├── sse.py                     # SSE event formatter
│   │   ├── seed_db.py                 # Database seeder script
│   │   └── __init__.py
│   ├── data/
│   │   └── flights.db                 # SQLite database (auto-created)
│   ├── output/
│   │   └── banner_*.png               # Generated banners (git-ignored)
│   └── frontend/
│       ├── index.html                 # Main UI
│       ├── app.js                     # SSE consumer + event handlers
│       └── styles.css                 # FoundryAirlines brand styling
└── infra/                             # Azure infrastructure (see infra/azure-setup.md)
    └── azure-setup.md                 # CLI setup steps for resource group
```

## Prerequisites

- **Python 3.11+** with pip
- **Azure CLI** (for infrastructure setup; see `infra/azure-setup.md`)
- **Azure subscription** with quota for:
  - `gpt-4.1` deployment in East US 2 (GlobalStandard)
  - `gpt-image-2` deployment in East US 2 (subscription-wide RPM quota is 2)
- **Microsoft Entra ID** identity (`az login`) — all auth is bearer-token, no API keys

## Azure Setup

All Azure resources live in a single resource group: **`ocupacion-busqueda-banner-rg`**.

### Quick Reference
- **Foundry account**: `vueling-image-8400` (East US 2) — hosts the project + both model deployments
- **Foundry project**: `vueling-image-8400/vueling-demo`
- **Bing Grounding resource**: `vueling-bing-8400` (global) — provisioned for future Foundry connection (see Agent 2 note)
- **Project Endpoint**: `https://vueling-image-8400.services.ai.azure.com/api/projects/vueling-demo`
- **Image Endpoint**: `https://vueling-image-8400.openai.azure.com/`
- **Model Deployments** (both on the same account):
  - `gpt-4.1` (GlobalStandard) — Foundry agents (chat, tools, threads)
  - `gpt-image-2` (Standard) — banner generation

> **Single-account architecture.** All inference runs from one Foundry account in **East US 2** (`vueling-image-8400`), which hosts both `gpt-4.1` and `gpt-image-2` plus the Foundry project where the agents and threads live. This region was chosen because the subscription-wide `gpt-image-2` RPM quota in Sweden Central is fully consumed by another team's deployment; East US 2 has a separate quota bucket.

**For the complete Azure CLI setup steps, infrastructure code, and resource creation**, refer to [`infra/azure-setup.md`](infra/azure-setup.md). That file provides the full CLI script for provisioning the resource group, deployments, and connections.

## Environment Variables

All variables are read from `app/.env` using `python-dotenv`. Create this file by copying `app/.env.example`:

```bash
cp app/.env.example app/.env
```

Then edit `app/.env` with your deployment values:

| Variable | Example | Purpose |
|----------|---------|---------|
| `PROJECT_ENDPOINT` | `https://vueling-image-8400.services.ai.azure.com/api/projects/vueling-demo` | Azure AI Foundry project endpoint (see `infra/azure-setup.md`) |
| `MODEL_DEPLOYMENT_NAME` | `gpt-4.1` | Model deployment for Foundry agents |
| `BING_API_KEY` | (32-char hex key) | Azure Bing Search v7 API subscription key |
| `BING_ENDPOINT` | `https://api.bing.microsoft.com/v7.0/search` | Bing Web Search API endpoint |
| `IMAGE_ENDPOINT` | `https://vueling-image-8400.openai.azure.com/` | Azure OpenAI endpoint for gpt-image-2 (same account as PROJECT_ENDPOINT) |
| `IMAGE_DEPLOYMENT` | `gpt-image-2` | Image model deployment name |
| `IMAGE_API_VERSION` | `2025-04-01-preview` | Azure OpenAI API version |

⚠️ **Never commit `.env` to git.** It is already in `.gitignore`. Use `app/.env.example` as the template for your team.

## Run Locally

### 1. Install Dependencies

```bash
pip install -r requirements.txt
```

### 2. Seed the Database (optional, auto-runs on first query)

The database is created on-demand, but you can pre-populate it:

```bash
python app/backend/seed_db.py
```

This creates `app/data/flights.db` with 25 realistic FoundryAirlines flights (5 low-occupancy target flights, 20 controls).

### 3. Start the Backend Server

```bash
uvicorn app.backend.main:app --port 8765
```

You should see:
```
Uvicorn running on http://127.0.0.1:8765
```

### 4. Open the Demo in Your Browser

Navigate to:
```
http://127.0.0.1:8765/
```

Click **"Run Demo"** to trigger the 3-agent orchestration. Watch the pipeline in real-time as:
1. Agent 1 finds the 5 flights
2. Agent 2 researches 5 events (parallel)
3. Agent 3 generates 5 banners (parallel, 60–120 seconds)

**Tip:** Check the **"Use cached banners"** checkbox for a fast demo (~15 seconds) that reuses pre-generated images.

## Orchestration with Foundry Prompt Agents + Responses API

The pipeline uses native **Foundry prompt agents** (declarative
`PromptAgentDefinition` from `azure-ai-projects >= 2.0.0`) invoked through
the **Azure OpenAI Responses API** exposed by the Foundry project. Two
prompt agents are chained sequentially by sharing a single Foundry
`conversation` — agent #2 reads agent #1's output as conversation history.
No external orchestration framework is needed; sequential multi-agent
orchestration is a first-class capability of Foundry. Reference:
<https://learn.microsoft.com/en-us/azure/foundry/agents/quickstarts/prompt-agent?tabs=python>.

```python
from azure.ai.projects import AIProjectClient
from azure.ai.projects.models import PromptAgentDefinition
from azure.identity import DefaultAzureCredential

project = AIProjectClient(endpoint=PROJECT_ENDPOINT, credential=DefaultAzureCredential())

# Bootstrap (once): create the two declarative prompt agents
project.agents.create_version(
    agent_name="vueling-flights-agent",
    definition=PromptAgentDefinition(model="gpt-4.1", instructions=FLIGHTS_INSTRUCTIONS),
)
project.agents.create_version(
    agent_name="vueling-events-agent",
    definition=PromptAgentDefinition(model="gpt-4.1", instructions=EVENTS_INSTRUCTIONS),
)

# Run (per request): one conversation chains the two agents
openai = project.get_openai_client()
conv = openai.conversations.create()

flights_resp = openai.responses.create(
    conversation=conv.id,
    extra_body={"agent_reference": {"name": "vueling-flights-agent", "type": "agent_reference"}},
    input=f"Format these flights as JSON:\n{json.dumps(flights_rows)}",
)
flights_text = flights_resp.output_text

events_resp = openai.responses.create(
    conversation=conv.id,    # SAME conversation → agent 2 sees agent 1's turn
    extra_body={"agent_reference": {"name": "vueling-events-agent", "type": "agent_reference"}},
    input="Now propose one event per flight from the JSON in the previous turn.",
)
events_text = events_resp.output_text
```

The backend translates each step into front-end SSE event types
(`agent_start`, `agent_log`, `agent_done`, `flight`, `banner`, `done`) inside
`orchestrate()` in `app/backend/agents.py`.

## Persistent Foundry Prompt Agents

Both prompt agents are created **once** by `app/backend/bootstrap_agents.py`
via `AIProjectClient.agents.create_version(...)`. `create_version` is
idempotent at the agent-name level: if the agent already exists, a new
version is added and becomes the active one — perfect for refreshing
instructions without recreating the agent.

```bash
# Create or upsert the prompt agents (safe to run any time)
python -m app.backend.bootstrap_agents

# Force a clean slate: delete + recreate the prompt agents
python -m app.backend.bootstrap_agents --reset
```

The bootstrap also deletes any **legacy Assistants-API** agents (`asst_*`)
left behind by previous iterations, then writes the new agent names + ids
into `app/agents.json`:

```json
{
  "flights_name": "vueling-flights-agent",
  "events_name":  "vueling-events-agent",
  "flights_id":   "vueling-flights-agent:1",
  "events_id":    "vueling-events-agent:1",
  "agent_kind":   "prompt-agent-v2"
}
```

The Responses API references prompt agents **by name** (not by id), so
agent versioning is transparent — bumping the version updates the active
agent without touching the call sites.

**Where each agent shows up in the Foundry portal:**

| Agent                   | Type                              | Portal location                                                          |
| ----------------------- | --------------------------------- | ------------------------------------------------------------------------ |
| `vueling-flights-agent` | Foundry **prompt agent**          | <https://ai.azure.com> → project `vueling-demo` → **Agents**              |
| `vueling-events-agent`  | Foundry **prompt agent**          | <https://ai.azure.com> → project `vueling-demo` → **Agents**              |
| `gpt-image-2`           | Azure OpenAI deployment           | <https://ai.azure.com> → project `vueling-demo` → **Models + endpoints**  |

You can open either prompt agent in the portal, click **Try it / Playground**,
and chat with the same agent the demo uses. Each agent shows its
declarative definition (model + instructions) and version history.

## Standalone Agent Invocation

Each agent can be invoked on its own without going through the orchestration:

```bash
# Agent 1: flights — uses the persistent Foundry agent + function tool
python -m app.scripts.run_flights_agent

# Agent 2: events — pass a sample flights JSON to the persistent Foundry agent
python -m app.scripts.run_events_agent

# Agent 3: banner — calls gpt-image-2 directly with one flight + event
python -m app.scripts.run_banner
```

These scripts share the exact same Responses-API wiring as the orchestrator
(see `run_flights_agent_standalone()` and `run_events_agent_standalone()` in
`app/backend/agents.py`), so any change to instructions or model applies
uniformly to both standalone runs and the orchestrated demo. To refresh
instructions, edit `FLIGHTS_INSTRUCTIONS` / `EVENTS_INSTRUCTIONS` in
`app/backend/bootstrap_agents.py` and re-run `python -m app.backend.bootstrap_agents`
— a new agent version is published in place.

## The 3 Agents in Detail

### Agent 1: Flight Analyst (prompt agent `vueling-flights-agent`)

**Model:** `gpt-4.1` (Azure AI Foundry)  
**Type:** Foundry **prompt agent** (`PromptAgentDefinition`) — declarative agent referenced by name through the Responses API  
**Input:** JSON array of raw flight rows pre-fetched from SQLite by the backend (no in-agent tool call needed in this Responses-API flow)  
**Output:** JSON list of 5 flights with lowest occupancy, normalized to the demo schema  
**Persistence:** Created once on first startup by `app/backend/bootstrap_agents.py`, name + version cached in `app/agents.json`. Visible in the Foundry portal at <https://ai.azure.com> → project `vueling-demo` → **Agents** tab.  
**Standalone:** `python -m app.scripts.run_flights_agent`  
**Code:** `_call_prompt_agent()` and `run_flights_agent_standalone()` in [`app/backend/agents.py`](app/backend/agents.py)

The agent is instructed to:
- Receive a raw JSON array of flight rows in the user message
- Return structured JSON with fields: `id`, `code`, `origin`, `destination`, `destination_city`, `destination_country`, `date`, `occupancy_pct`, `price_eur`
- Fallback: if parsing the agent's JSON fails, the backend falls back to the raw SQL rows

**Occupancy targeting:** Flights are seeded with occupancy ranging 35–95%. The demo targets the 5 lowest (35–55%) for sales opportunity.

---

### Agent 2: Event Finder (prompt agent `vueling-events-agent`)

**Model:** `gpt-4.1` (Azure AI Foundry)  
**Type:** Foundry **prompt agent** (`PromptAgentDefinition`)  
**Input:** Reads agent #1's JSON flights list directly from the shared Foundry `conversation` history — no manual prompt-stuffing required  
**Output:** JSON array, one event per flight, with fields `flight_id`, `title` (max 8 words), `short_description` (max 14 words)  
**Persistence:** Created once on first startup, visible in the Foundry portal at <https://ai.azure.com> → project `vueling-demo` → **Agents** tab. Same lifecycle as agent #1.  
**Standalone:** `python -m app.scripts.run_events_agent`  
**Code:** `_call_prompt_agent()` and `run_events_agent_standalone()` in [`app/backend/agents.py`](app/backend/agents.py)

Sequential chaining is achieved by reusing the same `conversation.id` across
both `responses.create(...)` calls — the events prompt agent automatically
sees agent #1's flights JSON as the previous turn. It proposes one plausible
public event per destination (recurring festival, typical seasonal happening,
well-known cultural/sports event) using only general knowledge — clearly
framed as "plausible" rather than "verified live event".

**Note on Bing:** Earlier iterations attempted to ground events with Bing Web Search. Standalone **Bing Search v7** was retired in **August 2025**, and the **Bing Grounding** SKU (`Microsoft.Bing/accounts` of kind `Bing.Grounding`, SKU G1) only works *via* a Foundry project connection. The Foundry Bing connection PUT is currently broken on this subscription (`500` from `credential.vienna-{region}.svc/.../secrets:putbatch`) in both **swedencentral** and **eastus2**. The Bing.Grounding resource (`vueling-bing-8400`) is provisioned anyway so the demo can switch back to grounded results once Microsoft restores the connection endpoint.

**Important note on Bing in this demo:**
- Standalone **Bing Search v7** was retired in **August 2025**. The new **Bing Grounding** SKU (`Microsoft.Bing/accounts` of kind `Bing.Grounding`, SKU G1) only works *via* a Foundry project connection — its API key cannot be used directly against `api.bing.microsoft.com`.
- The Foundry **Bing connection** PUT is currently broken on this subscription: it returns `500` from the Foundry credential service (`credential.vienna-{region}.svc/.../secrets:putbatch`) in both **swedencentral** and **eastus2**. Reproduced repeatedly with the canonical body shape (category=`GroundingWithBingSearch`, metadata.ApiType=`Azure`, metadata.ResourceId=Bing resource id). The credential service appears to require a Key Vault link that standalone Foundry projects don't have.
- We provisioned the Bing.Grounding resource (`vueling-bing-8400`) inside the new RG anyway, so the demo is ready to switch to Mode A the moment Microsoft restores the Foundry connection endpoint or the Bing key works standalone again. Re-enabling is a one-line connection PUT plus removing the placeholder branch in `_run_event_agent_sync()`.

**Concurrency:** All 5 flights are searched in parallel using `asyncio.gather()`.

---

### Agent 3: Banner Generator (gpt-image-2)

**API:** Direct REST call to gpt-image-2 (Azure OpenAI deployment, NOT a Foundry Agent)  
**Output:** 1536×1024 PNG image saved to `app/output/banner_{flight_id}.png`  
**Persistence:** This is an **Azure OpenAI image deployment**, not a Foundry Agent. It does NOT appear in the Foundry portal **Agents** tab. Instead, find it under <https://ai.azure.com> → project `vueling-demo` → **Models + endpoints** → deployment `gpt-image-2`.  
**Why it's outside the prompt-agent workflow:** the Responses API chains chat agents that exchange text turns. gpt-image-2 is an image-generation model with no chat surface, so it runs as a concurrent post-workflow step (`asyncio.Semaphore(2)` to respect the 2 RPM quota).  
**Standalone:** `python -m app.scripts.run_banner`  
**Code:** See `generate_banner()` in [`app/backend/agents.py`](app/backend/agents.py)

**Banner prompt includes:**
- Destination city and country
- Event theme (title + description)
- Cinematic photography at golden hour
- FoundryAirlines branding: **yellow #FFCC00, white, light gray**
- Bold typography: city name on left, **price tag in yellow on right**
- Minimal, clean design

**Concurrency & Rate Limits:**
- Runs all 5 banners in parallel with `Semaphore(2)` (gpt-image-2 has 2 RPM quota)
- Retry logic: on 429 or 503, backs off 35–65 seconds per attempt, up to 6 attempts
- Quality: set to `low` (~60–90 seconds per image) to stay under gpt-image-2 quota

---

## Frontend Notes

**Styling:** All UI uses the FoundryAirlines brand palette defined in `app/frontend/styles.css`:
- Primary: `--vueling-yellow: #FFCC00`
- Background: `--vueling-white`, `--vueling-light-gray: #F5F5F5`
- Text: `--vueling-gray: #444444`
- Font: Inter (Google Fonts), fallback to system-ui

**Layout:**
- Header with FoundryAirlines logo (yellow) and demo title
- Pipeline section showing 3 agent cards (Flight DB, Event Finder, Banner Gen)
- Real-time logs showing each agent's progress
- Grid of generated banners, updated as each completes

**SSE Events:**
The frontend listens for these Server-Sent Events:
- `agent_start`: Agent beginning (show spinner)
- `agent_log`: Progress message (append to log)
- `agent_done`: Agent finished (mark complete)
- `flight`: New flight from Agent 1 (add flight card)
- `banner`: New banner ready from Agent 3 (add to grid)
- `done`: All agents done (re-enable Run button)
- `error`: Error occurred (show error toast)

See [`app/frontend/app.js`](app/frontend/app.js) for the full EventSource consumer.

## Cost & Rate Limits

### gpt-image-2 (Tight Quota)

- **Quota:** 2 requests per minute (2 RPM)
- **Demo impact:** 5 banners = ~2.5 minutes minimum with retry logic
- **Mitigation:**
  - Backend uses `Semaphore(2)` to respect concurrency
  - Exponential backoff on 429: `35 + attempt * 10` seconds
  - Retry up to 6 times on rate-limit or server error
  - For fast demos, toggle **"Use cached banners"** to skip generation

### Foundry Agent Polling

- **Cost:** Small per-run charge for gpt-4.1 inference on flight + event queries
- **Note:** Agents are polled synchronously (wrapped with `asyncio.to_thread`)
- **Typical latency:** 10–30 seconds per agent

### Image API Calls

- Full banner generation pipeline: 60–120 seconds (due to image generation time + queue delays)
- Cached mode: 15 seconds (reuses pre-generated PNGs)

## Running with Docker (Optional)

To build and run the demo in a container:

```bash
# Build Docker image
docker build -t vueling-demo:latest .

# Run container
docker run -p 8765:8765 \
  -e PROJECT_ENDPOINT="your-endpoint" \
  -e FOUNDRY_API_KEY="your-key" \
  -e IMAGE_ENDPOINT="your-image-endpoint" \
  -e IMAGE_API_KEY="your-image-key" \
  vueling-demo:latest
```

(Dockerfile not included in this repo; add if deploying to container registry.)

## Troubleshooting

### "Agent run status: failed"
- Check that `PROJECT_ENDPOINT` and `FOUNDRY_API_KEY` are correct in `app/.env`
- Verify the Foundry project exists and you have access
- Check Azure portal for project health

### "gpt-image-2: 429 Too Many Requests"
- Expected behavior (quota: 2 RPM); backend auto-retries with backoff
- Use **"Use cached banners"** mode for quick demos
- Request quota increase via Azure portal if needed

### "Bing Grounding: connection failed"
- Verify `BING_CONNECTION_NAME` matches the Bing connection name in your Foundry project
- Re-run `infra/azure-setup.md` to ensure Bing connection is configured
- Check Foundry project connections in Azure portal

### "No flights returned"
- Ensure `app/data/flights.db` exists and is populated
- Run: `python app/backend/seed_db.py`
- Check database: `sqlite3 app/data/flights.db "SELECT COUNT(*) FROM flights;"`

### Banner images not appearing
- Check `app/output/` directory has PNG files
- Verify `/output` static route is mounted in FastAPI (`app/backend/main.py`)
- Check browser network console for 404 on image URLs

## License

MIT. Built as a 1-hour FoundryAirlines sales demo.

---

**Questions or issues?** Contact the demo team or refer to [`infra/azure-setup.md`](infra/azure-setup.md) for Azure infrastructure details.
