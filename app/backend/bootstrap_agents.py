"""Bootstrap persistent Foundry **prompt agents** for the FoundryAirlines demo.

Uses the new Foundry projects (v2) API with `PromptAgentDefinition`, which is
the modern declarative agent type that pairs with the Responses API for chat.

Doc reference:
  https://learn.microsoft.com/en-us/azure/foundry/agents/quickstarts/prompt-agent?tabs=python

This script:
  1. Deletes any legacy Assistants-API agents (asst_*) referenced in the local
     cache so we don't leave orphans behind in the Foundry project.
  2. Creates two prompt agents (or upserts a new version if they already exist):
       - vueling-flights-agent  — formats low-occupancy flights as JSON
       - vueling-events-agent   — proposes one cultural event per flight
  3. Caches the agent NAMES (prompt agents are referenced by name in the
     Responses API, not by id) into `app/agents.json`.

Run with:
    python -m app.backend.bootstrap_agents          # idempotent upsert
    python -m app.backend.bootstrap_agents --reset  # delete + recreate
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from typing import Any, Dict

from azure.ai.projects import AIProjectClient
from azure.ai.projects.models import PromptAgentDefinition
from azure.identity import DefaultAzureCredential
from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent
load_dotenv(ROOT / ".env", override=True)

PROJECT_ENDPOINT = os.environ["PROJECT_ENDPOINT"]
MODEL = os.getenv("MODEL_DEPLOYMENT_NAME", "gpt-4.1")
CACHE_PATH = ROOT / "agents.json"

FLIGHTS_AGENT_NAME = "vueling-flights-agent"
EVENTS_AGENT_NAME = "vueling-events-agent"

FLIGHTS_INSTRUCTIONS = (
    "You are a FoundryAirlines revenue analyst. The user message contains a JSON "
    "array of raw flight rows from the internal flights database. Return ONLY a "
    "JSON array (no prose, no code fences) preserving each row and exposing "
    "exactly these fields per flight: id, code, origin, destination, "
    "destination_city, destination_country, date, occupancy_pct, price_eur. "
    "Round occupancy_pct to one decimal."
)

EVENTS_INSTRUCTIONS = (
    "You are a cultural concierge for FoundryAirlines. The conversation history "
    "contains a JSON array of flights with destination_city, destination_country "
    "and date. For EACH flight, propose ONE plausible public event (recurring "
    "festival, typical seasonal happening, well-known cultural or sports event "
    "for that city in that month) that a traveler could enjoy. Use only general "
    "knowledge — do NOT invent unverifiable specifics. Return ONLY a JSON array "
    "(same length and order as the input flights). Each element must have: "
    "flight_id (matching the input), title (max 8 words) and short_description "
    "(max 14 words). No prose, no code fences."
)


def _load_cache() -> Dict[str, Any]:
    if CACHE_PATH.exists():
        try:
            return json.loads(CACHE_PATH.read_text(encoding="utf-8"))
        except Exception:
            return {}
    return {}


def _save_cache(data: Dict[str, Any]) -> None:
    CACHE_PATH.write_text(json.dumps(data, indent=2), encoding="utf-8")


def _delete_legacy_assistants(cache: Dict[str, Any]) -> None:
    """Best-effort cleanup of any old Assistants-API (asst_*) agents we created
    in a previous bootstrap run. Safe to call repeatedly."""
    legacy_ids = [v for k, v in cache.items()
                  if k.endswith("_id") and isinstance(v, str) and v.startswith("asst_")]
    if not legacy_ids:
        return
    try:
        from azure.ai.agents import AgentsClient
        legacy_client = AgentsClient(
            endpoint=PROJECT_ENDPOINT, credential=DefaultAzureCredential()
        )
        for aid in legacy_ids:
            try:
                legacy_client.delete_agent(aid)
                print(f"[bootstrap] deleted legacy assistant {aid}")
            except Exception as ex:
                print(f"[bootstrap] could not delete legacy assistant {aid}: {ex}")
    except Exception as ex:
        print(f"[bootstrap] legacy cleanup skipped: {ex}")


def _delete_prompt_agent(project: AIProjectClient, name: str) -> None:
    try:
        project.agents.delete_agent(agent_name=name)
        print(f"[bootstrap] deleted prompt agent {name}")
    except Exception as ex:
        print(f"[bootstrap] no prior prompt agent named {name} (ok): {ex.__class__.__name__}")


def ensure_persistent_agents(reset: bool = False) -> Dict[str, str]:
    """Create (or upsert) the two prompt agents server-side.

    Returns {"flights_name": ..., "events_name": ...}. Idempotent: calling
    `create_version` on an existing agent name simply adds a new version, which
    becomes the active one used by the Responses API.
    """
    cache = _load_cache()
    project = AIProjectClient(
        endpoint=PROJECT_ENDPOINT, credential=DefaultAzureCredential()
    )

    # 1) Always clean up any legacy asst_* assistants from prior runs.
    _delete_legacy_assistants(cache)

    # 2) Optionally wipe existing prompt agents so we start fresh.
    if reset:
        _delete_prompt_agent(project, FLIGHTS_AGENT_NAME)
        _delete_prompt_agent(project, EVENTS_AGENT_NAME)

    # 3) Create (or version) both prompt agents.
    flights_agent = project.agents.create_version(
        agent_name=FLIGHTS_AGENT_NAME,
        definition=PromptAgentDefinition(
            model=MODEL, instructions=FLIGHTS_INSTRUCTIONS,
        ),
    )
    print(f"[bootstrap] flights prompt agent: name={flights_agent.name} "
          f"id={flights_agent.id} version={flights_agent.version}")

    events_agent = project.agents.create_version(
        agent_name=EVENTS_AGENT_NAME,
        definition=PromptAgentDefinition(
            model=MODEL, instructions=EVENTS_INSTRUCTIONS,
        ),
    )
    print(f"[bootstrap] events prompt agent:  name={events_agent.name} "
          f"id={events_agent.id} version={events_agent.version}")

    out = {
        "flights_name": flights_agent.name,
        "events_name": events_agent.name,
        "flights_id": flights_agent.id,
        "events_id": events_agent.id,
        "agent_kind": "prompt-agent-v2",
    }
    _save_cache(out)
    return out


if __name__ == "__main__":
    reset = "--reset" in sys.argv
    ids = ensure_persistent_agents(reset=reset)
    print(json.dumps(ids, indent=2))
