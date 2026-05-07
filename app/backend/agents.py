"""3-agent FoundryAirlines demo orchestrated with the Foundry **Responses API**.

Pipeline:
  Agent 1 (flights)  : prompt agent — formats the lowest-occupancy flights as JSON
  Agent 2 (events)   : prompt agent — proposes one cultural event per flight
  Agent 3 (banner)   : direct REST call to gpt-image-2 (image model, no chat agent)

Agents 1 and 2 are **Foundry prompt agents** (declarative `PromptAgentDefinition`)
created by `bootstrap_agents.py`. They are invoked via the Azure OpenAI
Responses API exposed by the Foundry project, sharing one `conversation` so
agent 2 sees agent 1's output as conversation history. This is sequential
multi-agent orchestration done natively in Foundry — no external orchestrator
framework required.

Doc references:
  https://learn.microsoft.com/en-us/azure/foundry/agents/quickstarts/prompt-agent?tabs=python
"""
from __future__ import annotations

import asyncio
import base64
import json
import os
import sqlite3
import time
from pathlib import Path
from typing import Any, AsyncGenerator, Dict, List, Optional

import httpx
from dotenv import load_dotenv
from azure.identity import DefaultAzureCredential

load_dotenv(Path(__file__).parent.parent / ".env", override=True)

PROJECT_ENDPOINT = os.environ["PROJECT_ENDPOINT"]
MODEL_DEPLOYMENT_NAME = os.getenv("MODEL_DEPLOYMENT_NAME", "gpt-4.1")
IMAGE_ENDPOINT = os.getenv("IMAGE_ENDPOINT", "").rstrip("/")
IMAGE_DEPLOYMENT = os.getenv("IMAGE_DEPLOYMENT")
IMAGE_API_VERSION = os.getenv("IMAGE_API_VERSION", "2025-04-01-preview")

ROOT = Path(__file__).parent.parent
DB_PATH = ROOT / "data" / "flights.db"
OUTPUT_PATH = ROOT / "output"
OUTPUT_PATH.mkdir(parents=True, exist_ok=True)


# ----------------------- Shared helpers -----------------------------------

def _query_low_occupancy(limit: int = 5) -> List[Dict[str, Any]]:
    conn = sqlite3.connect(str(DB_PATH))
    cur = conn.cursor()
    cur.execute(
        """
        SELECT id, code, origin, destination, destination_city, destination_country,
               date, total_seats, sold_seats, price_eur,
               CAST(sold_seats AS REAL)/total_seats*100 AS occupancy_pct
        FROM flights ORDER BY occupancy_pct ASC LIMIT ?
        """,
        (limit,),
    )
    rows = cur.fetchall()
    conn.close()
    return [
        {
            "id": r[0], "code": r[1], "origin": r[2], "destination": r[3],
            "destination_city": r[4], "destination_country": r[5],
            "date": r[6], "total_seats": r[7], "sold_seats": r[8],
            "price_eur": r[9], "occupancy_pct": round(r[10], 1),
        }
        for r in rows
    ]


def _extract_json(text: str) -> Any:
    text = text.strip()
    if "```json" in text:
        text = text.split("```json", 1)[1].split("```", 1)[0]
    elif "```" in text:
        text = text.split("```", 1)[1].split("```", 1)[0]
    text = text.strip()
    for opener, closer in (("[", "]"), ("{", "}")):
        s = text.find(opener)
        e = text.rfind(closer)
        if s != -1 and e > s:
            try:
                return json.loads(text[s : e + 1])
            except Exception:
                continue
    return json.loads(text)


# ----------------------- Image API Entra ID auth --------------------------

_image_credential = DefaultAzureCredential()
_token_cache = {"token": None, "expires_on": 0}


def _get_cs_token() -> str:
    now = time.time()
    if _token_cache["token"] is None or _token_cache["expires_on"] - now < 300:
        t = _image_credential.get_token("https://cognitiveservices.azure.com/.default")
        _token_cache["token"] = t.token
        _token_cache["expires_on"] = t.expires_on
    return _token_cache["token"]


# ----------------------- Foundry prompt-agent wiring ----------------------

_agent_names: Optional[Dict[str, str]] = None
_project_client = None
_openai_client = None


def _get_agent_names() -> Dict[str, str]:
    """Lazy-load the prompt-agent NAMES from the bootstrap cache, creating
    the Foundry prompt agents on first call if they don't yet exist."""
    global _agent_names
    if _agent_names is None:
        from .bootstrap_agents import ensure_persistent_agents
        ids = ensure_persistent_agents()
        _agent_names = {
            "flights": ids.get("flights_name", "vueling-flights-agent"),
            "events": ids.get("events_name", "vueling-events-agent"),
        }
    return _agent_names


def _get_project_client():
    """Singleton AIProjectClient pointed at our Foundry project."""
    global _project_client
    if _project_client is None:
        from azure.ai.projects import AIProjectClient
        _project_client = AIProjectClient(
            endpoint=PROJECT_ENDPOINT, credential=DefaultAzureCredential()
        )
    return _project_client


def _get_openai_client():
    """Azure OpenAI client bound to the Foundry project. Used for the
    Responses API + Conversations endpoints that drive prompt agents."""
    global _openai_client
    if _openai_client is None:
        _openai_client = _get_project_client().get_openai_client()
    return _openai_client


def _agent_reference(name: str) -> Dict[str, Any]:
    """The `extra_body` shape that targets a named prompt agent on a Responses
    API call."""
    return {"agent_reference": {"name": name, "type": "agent_reference"}}


async def _call_prompt_agent(conversation_id: str, agent_name: str, user_input: str) -> str:
    """Invoke a named prompt agent inside an existing conversation and return
    its output_text. Runs the blocking SDK call in a thread to keep the SSE
    event loop unblocked."""
    openai = _get_openai_client()

    def _sync_call() -> str:
        resp = openai.responses.create(
            conversation=conversation_id,
            extra_body=_agent_reference(agent_name),
            input=user_input,
        )
        return resp.output_text or ""

    return await asyncio.to_thread(_sync_call)


# ----------------------- Standalone agent runners (for scripts) -----------

async def run_flights_agent_standalone(
    prompt: Optional[str] = None,
) -> str:
    """Invoke just the flights prompt agent and return its text response.
    Useful for `python -m app.scripts.run_flights_agent`."""
    names = _get_agent_names()
    openai = _get_openai_client()

    def _sync():
        flights_rows = _query_low_occupancy(5)
        user_input = prompt or (
            "Format the following flight rows as the JSON array described in your "
            f"instructions:\n{json.dumps(flights_rows)}"
        )
        conv = openai.conversations.create()
        resp = openai.responses.create(
            conversation=conv.id,
            extra_body=_agent_reference(names["flights"]),
            input=user_input,
        )
        return resp.output_text or ""

    return await asyncio.to_thread(_sync)


async def run_events_agent_standalone(flights_json: str) -> str:
    """Invoke just the events prompt agent over a flights JSON payload."""
    names = _get_agent_names()
    openai = _get_openai_client()

    def _sync():
        conv = openai.conversations.create()
        # Seed the conversation with the flights JSON as context, then ask.
        prompt = (
            "The following JSON array of flights is the input. Propose one event "
            f"per flight as instructed:\n\n{flights_json}"
        )
        resp = openai.responses.create(
            conversation=conv.id,
            extra_body=_agent_reference(names["events"]),
            input=prompt,
        )
        return resp.output_text or ""

    return await asyncio.to_thread(_sync)


# ----------------------- Banner generation (gpt-image-2) ------------------

def _banner_prompt(flight: Dict[str, Any], event: Dict[str, Any]) -> str:
    return (
        f"Wide horizontal travel advertising banner, 3:2 aspect ratio, for the airline "
        f"FoundryAirlines. Destination: {flight['destination_city']}, {flight['destination_country']}. "
        f"Theme inspired by: {event['title']} ({event['short_description']}). "
        f"Cinematic high-resolution photography of {flight['destination_city']} at golden "
        f"hour, vibrant atmosphere. Bold modern typography overlay. Left side: large city "
        f"name '{flight['destination_city'].upper()}'. Right side: large yellow #FFCC00 "
        f"price tag '{int(round(flight['price_eur']))} EUR'. Clean composition with white "
        f"space, FoundryAirlines brand colors: yellow #FFCC00, white, light gray. Premium minimal "
        f"design. No clutter, accurate spelling, no extra text."
    )


async def generate_banner(
    flight: Dict[str, Any], event: Dict[str, Any], client: httpx.AsyncClient
) -> Dict[str, Any]:
    url = (
        f"{IMAGE_ENDPOINT}/openai/deployments/{IMAGE_DEPLOYMENT}/images/generations"
        f"?api-version={IMAGE_API_VERSION}"
    )
    payload = {
        "prompt": _banner_prompt(flight, event),
        "size": "1536x1024",
        "quality": "low",
        "n": 1,
        "output_format": "png",
    }
    last_err = None
    for attempt in range(6):
        token = await asyncio.to_thread(_get_cs_token)
        r = await client.post(
            url,
            headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
            json=payload,
        )
        if r.status_code == 200:
            data = r.json()["data"][0]
            img_bytes = base64.b64decode(data["b64_json"])
            out = OUTPUT_PATH / f"banner_{flight['id']}.png"
            out.write_bytes(img_bytes)
            return {
                "flight_id": flight["id"],
                "image_url": f"/output/banner_{flight['id']}.png",
                "event": event,
            }
        last_err = f"{r.status_code}: {r.text[:200]}"
        if r.status_code in (429, 503):
            await asyncio.sleep(35 + attempt * 10)
            continue
        break
    raise RuntimeError(f"image API failed after retries: {last_err}")


def _placeholder_event(flight: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "title": f"{flight['destination_city']} City Highlights",
        "short_description": (
            f"Top experiences awaiting you in {flight['destination_city']} this week."
        ),
    }


# ----------------------- Public orchestrator ------------------------------

async def orchestrate(use_cached_banners: bool = False) -> AsyncGenerator[Dict[str, Any], None]:
    """SSE-style async generator driving the full 3-agent demo.

    Sequential orchestration is achieved by sharing a single Foundry
    `conversation` across the two prompt-agent invocations: agent 2 sees
    agent 1's output as conversation history.

    Front-end contract (event types): agent_start, agent_log, flight, banner,
    agent_done, done, error.
    """
    try:
        names = _get_agent_names()
        openai = _get_openai_client()

        # ---- Open one conversation to chain agent 1 → agent 2 ----------
        conv = await asyncio.to_thread(openai.conversations.create)
        conversation_id = conv.id

        # ============= Agent 1: flights prompt agent ====================
        yield {"type": "agent_start", "agent": "flights",
               "message": f"Foundry prompt agent #1 ({names['flights']}) — Responses API"}
        yield {"type": "agent_log", "agent": "flights",
               "message": f"conversation {conversation_id[:18]}… opened on Foundry project"}

        flights_rows = _query_low_occupancy(5)
        yield {"type": "agent_log", "agent": "flights",
               "message": f"Local DB query returned {len(flights_rows)} candidate flights — sending to prompt agent for formatting"}

        flights_input = (
            "Format the following raw flight rows as the JSON array described in "
            f"your instructions:\n{json.dumps(flights_rows)}"
        )
        flights_text = await _call_prompt_agent(conversation_id, names["flights"], flights_input)

        flights: List[Dict[str, Any]] = []
        try:
            parsed = _extract_json(flights_text)
            if isinstance(parsed, list) and parsed:
                flights = parsed
        except Exception:
            pass
        if not flights:
            yield {"type": "agent_log", "agent": "flights",
                   "message": "Prompt agent output unparseable — falling back to raw DB rows"}
            flights = flights_rows

        yield {"type": "agent_log", "agent": "flights",
               "message": f"Agent returned {len(flights)} flights with lowest occupancy"}
        for f in flights:
            yield {"type": "flight", "data": f}
        yield {"type": "agent_done", "agent": "flights"}

        # ============= Agent 2: events prompt agent =====================
        yield {"type": "agent_start", "agent": "events",
               "message": f"Foundry prompt agent #2 ({names['events']}) — same conversation, sees prior turn"}
        yield {"type": "agent_log", "agent": "events",
               "message": "Asking prompt agent to propose one event per flight from prior turn"}

        events_input = (
            "Now propose one plausible cultural or seasonal event per flight from "
            "the JSON array in the previous turn, following your instructions."
        )
        events_text = await _call_prompt_agent(conversation_id, names["events"], events_input)

        events_by_id: Dict[int, Dict[str, Any]] = {}
        try:
            parsed_events = _extract_json(events_text)
            if isinstance(parsed_events, list):
                for e in parsed_events:
                    if isinstance(e, dict) and e.get("title"):
                        fid = e.get("flight_id")
                        try:
                            events_by_id[int(fid)] = {
                                "title": str(e["title"])[:80],
                                "short_description": str(e.get("short_description", ""))[:160],
                            }
                        except (TypeError, ValueError):
                            continue
        except Exception:
            pass

        events: List[Dict[str, Any]] = []
        for f in flights:
            ev = events_by_id.get(f["id"]) or _placeholder_event(f)
            events.append(ev)
            yield {"type": "agent_log", "agent": "events",
                   "message": f"{f['destination_city']}: {ev['title']}"}
        yield {"type": "agent_done", "agent": "events"}

        # ============= Agent 3: gpt-image-2 banners =====================
        yield {"type": "agent_start", "agent": "banners",
               "message": "Calling gpt-image-2 to generate 5 banners (post-workflow concurrent step)…"}

        if use_cached_banners:
            yield {"type": "agent_log", "agent": "banners",
                   "message": "[cached mode] reusing previously generated banners for fast demo"}
            for f, e in zip(flights, events):
                cached_path = OUTPUT_PATH / f"banner_{f['id']}.png"
                if cached_path.exists():
                    await asyncio.sleep(2.0)
                    yield {"type": "banner", "data": {
                        "flight_id": f["id"],
                        "image_url": f"/output/banner_{f['id']}.png",
                        "event": e,
                    }}
                    yield {"type": "agent_log", "agent": "banners",
                           "message": f"✓ banner ready for flight #{f['id']} ({f['destination_city']})"}
            yield {"type": "agent_done", "agent": "banners"}
            yield {"type": "done", "message": "All agents completed"}
            return

        yield {"type": "agent_log", "agent": "banners",
               "message": "This is the slow step (~60-120 s). Banners stream as they finish."}

        async with httpx.AsyncClient(timeout=httpx.Timeout(600.0)) as client:
            sem = asyncio.Semaphore(2)

            async def _gen(f, e):
                async with sem:
                    return await generate_banner(f, e, client)

            banner_tasks = [
                asyncio.create_task(_gen(f, e))
                for f, e in zip(flights, events)
            ]
            for done in asyncio.as_completed(banner_tasks):
                try:
                    result = await done
                    yield {"type": "banner", "data": result}
                    yield {"type": "agent_log", "agent": "banners",
                           "message": f"✓ banner ready for flight #{result['flight_id']}"}
                except Exception as ex:
                    yield {"type": "agent_log", "agent": "banners",
                           "message": f"✗ banner failed: {ex}"}

        yield {"type": "agent_done", "agent": "banners"}
        yield {"type": "done", "message": "All agents completed"}

    except Exception as e:
        import traceback
        traceback.print_exc()
        yield {"type": "error", "message": str(e)}
        yield {"type": "done", "message": "halted"}
