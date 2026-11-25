import argparse
import asyncio
import json
import logging
import time
from collections.abc import Awaitable, Callable
from datetime import datetime
from pathlib import Path

import httpx

from app import entity_grounding, ollama
from app.agents import nodes
from app.config import settings
from app.prompt_versions import PROMPT_VERSIONS
from probes.cases import (
    ACTIONS,
    FUZZY,
    FUZZY_HOUSEHOLD,
    GATE,
    HOUSEHOLD,
    RESEARCH,
    ROUTING,
    TODAY,
)

RESULTS_DIR = Path(__file__).parent / "results"


class _FallbackCounter(logging.Handler):
    """Collects warnings from `app` loggers during a case, which include
    the one `ollama.complete_structured_or_default` logs when a decision
    call fails, so a broken call can't pass as its default answer."""

    def __init__(self) -> None:
        super().__init__(level=logging.WARNING)
        self.messages: list[str] = []

    def emit(self, record: logging.LogRecord) -> None:
        self.messages.append(record.getMessage())


_fallbacks = _FallbackCounter()

SUITE_PROMPTS = {
    "routing": ("supervisor",),
    "action": ("action_target", "log_fields", "schedule_kind", "schedule_detail", "last_done_date"),
    "fuzzy": ("fuzzy_entity_match",),
    "gate": ("grounding_gate",),
    "research": ("research_tool",),
}


def _matches(got, wanted) -> bool:
    return got in wanted if isinstance(wanted, tuple) else got == wanted


def case_key(case) -> str:
    """Identifies a case across runs: its query, marked when it carries
    history or a scratchpad, since those reuse another case's query."""
    key = case.query
    if getattr(case, "history", ()):
        key += " [after history]"
    if getattr(case, "scratchpad", ()):
        key += " [after a tool round]"
    return key


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


async def _gate_case(case) -> tuple[bool, str]:
    got = await nodes.needs_grounding(case.query)
    return got == case.needs_grounding, f"got {got}, wanted {case.needs_grounding}"


async def _research_case(case) -> tuple[bool, str]:
    decision = await nodes.decide_research_tool(case.query, list(case.history), list(case.scratchpad))
    tool = decision.get("tool") or None
    location = nodes._validated_location(decision.get("location"), case.query)
    misses = []
    if tool != case.tool:
        misses.append(f"tool={tool} (wanted {case.tool})")
    elif tool in ("get_weather", "get_weather_forecast"):
        if case.location is None and location is not None:
            misses.append(f"location={location!r} (wanted none)")
        if case.location is not None and (location is None or case.location.lower() not in location.lower()):
            misses.append(f"location={location!r} (wanted {case.location!r})")
        if case.days is not None and decision.get("days") != case.days:
            misses.append(f"days={decision.get('days')!r} (wanted {case.days})")
    return not misses, "; ".join(misses) or json.dumps(decision)


SUITES: dict[str, tuple[tuple, Callable[..., Awaitable[tuple[bool, str]]]]] = {
    "routing": (ROUTING, _routing_case),
    "action": (ACTIONS, _action_case),
    "fuzzy": (FUZZY, _fuzzy_case),
    "gate": (GATE, _gate_case),
    "research": (RESEARCH, _research_case),
}


async def _model_is_available() -> bool:
    try:
        resp = await ollama.get_client().get("/api/tags", timeout=10)
        resp.raise_for_status()
    except httpx.HTTPError:
        return False
    return settings.ollama_model in {m["name"] for m in resp.json().get("models", [])}


async def _warm_up() -> None:
    """Loads the model with the same options the cases use, so the first
    timed case doesn't pay for the load."""
    await ollama.complete_structured(
        [{"role": "user", "content": "ok"}],
        {"type": "object", "properties": {"ok": {"type": "boolean"}}, "required": ["ok"]},
    )


async def _attempt(case, run_case) -> tuple[bool, str, float]:
    """One run of one case: (passed, detail, seconds)."""
    _fallbacks.messages.clear()
    started = time.perf_counter()
    try:
        ok, detail = await run_case(case)
    except Exception as exc:
        ok, detail = False, f"error: {type(exc).__name__}: {exc}"
    if _fallbacks.messages:
        ok, detail = False, f"fell back: {'; '.join(_fallbacks.messages)}"
    return ok, detail, time.perf_counter() - started


def case_result(key: str, attempts: list[tuple[bool, str, float]]) -> dict:
    """Combines a case's attempts. It passes only if every attempt passed;
    `detail` is the first failure's, so a flaky case shows how it fails."""
    passes = sum(ok for ok, _, _ in attempts)
    failed = [detail for ok, detail, _ in attempts if not ok]
    return {
        "key": key,
        "pass": passes == len(attempts),
        "passes": passes,
        "runs": len(attempts),
        "detail": failed[0] if failed else attempts[-1][1],
        "seconds": round(sum(seconds for _, _, seconds in attempts) / len(attempts), 2),
    }


def _status(result: dict) -> str:
    passes, runs = _counts(result)
    if runs == 1:
        return "PASS" if result["pass"] else "FAIL"
    label = "PASS" if result["pass"] else "FLAKY" if passes else "FAIL"
    return f"{label} {passes}/{runs}"


async def run(suite_names: list[str], repeat: int = 1) -> dict:
    """Runs each case of each suite `repeat` times and returns the run
    record that gets saved."""
    if not await _model_is_available():
        raise SystemExit(f"{settings.ollama_model} is not available at {settings.ollama_host}")
    print(
        f"model {settings.ollama_model}, num_ctx {settings.ollama_num_ctx}, "
        f"host {settings.ollama_host}, {repeat} run(s) per case"
    )
    await _warm_up()
    record = {
        "started_at": datetime.now().isoformat(timespec="seconds"),
        "model": settings.ollama_model,
        "num_ctx": settings.ollama_num_ctx,
        "host": settings.ollama_host,
        "repeat": repeat,
        "suites": {},
    }
    for name in suite_names:
        cases, run_case = SUITES[name]
        prompt_versions = {p: PROMPT_VERSIONS[p] for p in SUITE_PROMPTS[name]}
        print(f"\n{name} ({', '.join(f'{p} v{v}' for p, v in prompt_versions.items())})")
        results = []
        for case in cases:
            attempts = [await _attempt(case, run_case) for _ in range(repeat)]
            result = case_result(case_key(case), attempts)
            results.append(result)
            print(f"  {_status(result)}  {result['key']}  -> {result['detail']}")
        passed = sum(r["pass"] for r in results)
        flaky = [r["key"] for r in results if 0 < r["passes"] < r["runs"]]
        seconds_per_case = round(sum(r["seconds"] for r in results) / len(results), 2)
        record["suites"][name] = {
            "prompt_versions": prompt_versions,
            "passed": passed,
            "total": len(results),
            "flaky": len(flaky),
            "seconds_per_case": seconds_per_case,
            "cases": results,
        }
        summary = f"  {name}: {passed}/{len(results)}, {seconds_per_case:.1f}s per case"
        if repeat > 1:
            summary += f", {len(flaky)} flaky over {repeat} runs"
        print(summary)
        for key in flaky:
            print(f"    flaky: {key}")
    return record


def failures(record: dict) -> int:
    return sum(s["total"] - s["passed"] for s in record["suites"].values())


def save(record: dict, results_dir: Path = RESULTS_DIR) -> Path:
    results_dir.mkdir(exist_ok=True)
    stamp = record["started_at"].replace(":", "").replace("-", "")
    path = results_dir / f"{stamp}-{'-'.join(record['suites'])}.json"
    path.write_text(json.dumps(record, indent=2), encoding="utf-8")
    return path


def latest_result(suite_names: list[str], results_dir: Path = RESULTS_DIR) -> Path | None:
    """The newest saved run that included every one of `suite_names`,
    skipping files that aren't run records."""
    paths = sorted(results_dir.glob("*.json"), reverse=True) if results_dir.is_dir() else []
    for path in paths:
        try:
            suites = json.loads(path.read_text(encoding="utf-8"))["suites"]
        except (ValueError, KeyError, TypeError):
            continue
        if set(suite_names) <= set(suites):
            return path
    return None


def _rate(case: dict) -> str:
    passes, runs = _counts(case)
    return f"{passes}/{runs}"


def _counts(case: dict) -> tuple[int, int]:
    """(passes, runs), reading a record saved before `-Repeat` as one run."""
    return case.get("passes", int(case["pass"])), case.get("runs", 1)


def _rate_changed(old: dict, new: dict) -> bool:
    """Whether the pass fraction moved, so 1/1 -> 5/5 or 0/1 -> 0/5 is no change."""
    (old_passes, old_runs), (new_passes, new_runs) = _counts(old), _counts(new)
    return old_passes * new_runs != new_passes * old_runs


def compare(previous: dict, current: dict) -> list[str]:
    """Lines describing what changed between two run records, for the
    suites both ran: score, speed, prompt versions, and every case that
    flipped, appeared or disappeared."""
    lines = []
    for field_name, missing in (("model", None), ("num_ctx", None), ("repeat", 1)):
        before, now = previous.get(field_name, missing), current.get(field_name, missing)
        if before != now:
            lines.append(f"{field_name}: {before} -> {now}")
    for name, now in current["suites"].items():
        before = previous["suites"].get(name)
        if before is None:
            lines.append(f"{name}: not in the previous run")
            continue
        lines.append(
            f"{name}: {before['passed']}/{before['total']} -> {now['passed']}/{now['total']}, "
            f"{before['seconds_per_case']:.1f}s -> {now['seconds_per_case']:.1f}s per case"
        )
        for prompt, version in now["prompt_versions"].items():
            old_version = before["prompt_versions"].get(prompt)
            if old_version is None:
                lines.append(f"  {prompt} v{version} (new)")
            elif old_version != version:
                lines.append(f"  {prompt} v{old_version} -> v{version}")
        before_cases = {c["key"]: c for c in before["cases"]}
        now_cases = {c["key"]: c for c in now["cases"]}
        for key, case in now_cases.items():
            old = before_cases.get(key)
            if old is None:
                lines.append(f"  new        {_status(case)}  {key}")
            elif old["pass"] and not case["pass"]:
                lines.append(f"  PASS->FAIL  {key}  ({_rate(old)} -> {_rate(case)})  -> {case['detail']}")
            elif not old["pass"] and case["pass"]:
                lines.append(f"  FAIL->PASS  {key}  ({_rate(old)} -> {_rate(case)})")
            elif _rate_changed(old, case):
                lines.append(f"  {_rate(old)} -> {_rate(case)} passed  {key}")
        lines.extend(f"  removed    {key}" for key in before_cases if key not in now_cases)
    return lines


def main() -> None:
    parser = argparse.ArgumentParser(description="Score the LLM decision steps against the real model.")
    parser.add_argument("--suite", choices=[*SUITES, "all"], default="all")
    parser.add_argument(
        "--compare",
        metavar="FILE",
        help="a saved run to compare against, or 'last' for the newest one in probes/results "
        "that included these suites",
    )
    parser.add_argument(
        "--repeat",
        type=int,
        default=1,
        metavar="N",
        help="run each case N times; a case passes only if every run does, and any mixed result is listed as flaky",
    )
    args = parser.parse_args()
    if args.repeat < 1:
        parser.error("--repeat must be at least 1")
    logging.basicConfig(level=logging.ERROR)
    for handler in logging.getLogger().handlers:
        handler.setLevel(logging.ERROR)
    app_logger = logging.getLogger("app")
    app_logger.setLevel(logging.WARNING)
    app_logger.addHandler(_fallbacks)

    names = list(SUITES) if args.suite == "all" else [args.suite]
    baseline_path = None
    if args.compare == "last":
        baseline_path = latest_result(names)
        if baseline_path is None:
            raise SystemExit(f"no saved run in {RESULTS_DIR} has suites {', '.join(names)}")
    elif args.compare:
        baseline_path = Path(args.compare)
        if not baseline_path.is_file():
            raise SystemExit(f"no saved run at {baseline_path}")

    record = asyncio.run(run(names, args.repeat))
    path = save(record)
    print(f"\nsaved {path}")

    if baseline_path is not None:
        previous = json.loads(baseline_path.read_text(encoding="utf-8"))
        print(f"\ncompared with {baseline_path.name}")
        for line in compare(previous, record) or ["no suites in common"]:
            print(line)
    raise SystemExit(1 if failures(record) else 0)


if __name__ == "__main__":
    main()
