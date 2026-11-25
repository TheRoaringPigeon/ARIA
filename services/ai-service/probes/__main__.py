import argparse
import asyncio
import logging
import time
from collections.abc import Awaitable, Callable

import httpx

from app import entity_grounding, ollama
from app.agents import nodes
from app.config import settings
from app.prompt_versions import PROMPT_VERSIONS
from probes.cases import ACTIONS, FUZZY, FUZZY_HOUSEHOLD, HOUSEHOLD, ROUTING, TODAY

SUITE_PROMPTS = {
    "routing": ("supervisor",),
    "action": ("action_target", "log_fields", "schedule_kind", "schedule_detail"),
    "fuzzy": ("fuzzy_entity_match",),
}


def _matches(got, wanted) -> bool:
    return got in wanted if isinstance(wanted, tuple) else got == wanted


async def _routing_case(case) -> tuple[bool, str]:
    state = {"query": case.query, "history": list(case.history)}
    agent = (await nodes.supervisor_node(state, {}))["selected_agent"]
    return agent == case.agent, f"got {agent}, wanted {case.agent}"


async def _action_case(case) -> tuple[bool, str]:
    matched = entity_grounding.find_matching_entities(case.query, HOUSEHOLD, uncapped=True)
    proposed = await nodes.decide_action(case.query, matched, TODAY)
    if proposed is None:
        return case.tool is None, "no proposal"
    if case.tool is None:
        return False, f"unwanted {proposed['summary']!r}"
    args = proposed["args"]
    misses = [
        f"{key}={args.get(key)!r} (wanted {wanted!r})"
        for key, wanted in case.expect.items()
        if not _matches(args.get(key), wanted)
    ]
    if proposed["tool"] != case.tool:
        misses.insert(0, f"tool={proposed['tool']} (wanted {case.tool})")
    return not misses, "; ".join(misses) or proposed["summary"]


async def _fuzzy_case(case) -> tuple[bool, str]:
    match = await entity_grounding.resolve_fuzzy_entity_match(case.query, FUZZY_HOUSEHOLD)
    got = match[0]["id"] if match else None
    return got == case.entity_id, f"got {got}, wanted {case.entity_id}"


SUITES: dict[str, tuple[tuple, Callable[..., Awaitable[tuple[bool, str]]]]] = {
    "routing": (ROUTING, _routing_case),
    "action": (ACTIONS, _action_case),
    "fuzzy": (FUZZY, _fuzzy_case),
}


async def _model_is_available() -> bool:
    try:
        resp = await ollama.get_client().get("/api/tags", timeout=10)
        resp.raise_for_status()
    except httpx.HTTPError:
        return False
    return settings.ollama_model in {m["name"] for m in resp.json().get("models", [])}


async def run(suite_names: list[str]) -> int:
    if not await _model_is_available():
        raise SystemExit(f"{settings.ollama_model} is not available at {settings.ollama_host}")
    print(f"model {settings.ollama_model}, num_ctx {settings.ollama_num_ctx}, host {settings.ollama_host}")
    failures = 0
    for name in suite_names:
        cases, run_case = SUITES[name]
        versions = ", ".join(f"{p} v{PROMPT_VERSIONS[p]}" for p in SUITE_PROMPTS[name])
        print(f"\n{name} ({versions})")
        passed = 0
        started = time.perf_counter()
        for case in cases:
            ok, detail = await run_case(case)
            passed += ok
            print(f"  {'PASS' if ok else 'FAIL'}  {case.query}  -> {detail}")
        elapsed = time.perf_counter() - started
        failures += len(cases) - passed
        print(f"  {name}: {passed}/{len(cases)}, {elapsed / len(cases):.1f}s per case")
    return failures


def main() -> None:
    parser = argparse.ArgumentParser(description="Score the LLM decision steps against the real model.")
    parser.add_argument("--suite", choices=[*SUITES, "all"], default="all")
    args = parser.parse_args()
    logging.basicConfig(level=logging.ERROR)
    names = list(SUITES) if args.suite == "all" else [args.suite]
    raise SystemExit(1 if asyncio.run(run(names)) else 0)


if __name__ == "__main__":
    main()
