import argparse
import asyncio
import json
import logging
import re
import time
from collections.abc import Awaitable, Callable
from datetime import datetime
from pathlib import Path
from unittest.mock import patch
from uuid import uuid4

import httpx
from langgraph.checkpoint.memory import MemorySaver

from app import entity_grounding, ollama
from app.adapters import get_adapter
from app.agents import nodes
from app.agents import state as agent_state
from app.agents.graph import build_graph_builder
from app.config import settings
from app.prompt_versions import PROMPT_VERSIONS
from app.routers import chat
from probes.cases import (
    ACTIONS,
    ANSWERS,
    CHAIN,
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

_ACTION_PROMPTS = ("action_target", "log_fields", "schedule_kind", "schedule_detail", "last_done_date")

SUITE_PROMPTS = {
    "routing": ("supervisor",),
    "action": _ACTION_PROMPTS,
    "fuzzy": ("fuzzy_entity_match",),
    "gate": ("grounding_gate",),
    "research": ("research_tool",),
    "chain": ("supervisor", *_ACTION_PROMPTS),
    "answer": ("personas", "answer_context"),
}

_SPECIALISTS = ("maintenance", "vehicle", "research", "general")

_MONTH_NAMES = (
    ("january", "jan"),
    ("february", "feb"),
    ("march", "mar"),
    ("april", "apr"),
    ("may",),
    ("june", "jun"),
    ("july", "jul"),
    ("august", "aug"),
    ("september", "sept", "sep"),
    ("october", "oct"),
    ("november", "nov"),
    ("december", "dec"),
)
_MONTHS = {name: number for number, names in enumerate(_MONTH_NAMES, start=1) for name in names}
_MONTH = "|".join(sorted(_MONTHS, key=len, reverse=True))
_YEAR = r"(?:,?\s+(\d{4})\b)?"
_ISO_DATE = re.compile(r"\b(\d{4})-(\d{2})-(\d{2})\b")
_NAMED_DATE = re.compile(rf"\b({_MONTH})\.?\s+(\d{{1,2}})(?:st|nd|rd|th)?\b{_YEAR}", re.IGNORECASE)
_DAY_FIRST_DATE = re.compile(rf"\b(\d{{1,2}})(?:st|nd|rd|th)?\s+(?:of\s+)?({_MONTH})\b\.?{_YEAR}", re.IGNORECASE)
_SLASH_DATE = re.compile(r"\b(\d{1,2})/(\d{1,2})/(\d{4})\b")


def _matches(got, wanted) -> bool:
    return got in wanted if isinstance(wanted, tuple) else got == wanted


def _contains(text: str, wanted) -> bool:
    """Whether `wanted` (or, for a tuple, any of its alternatives) appears
    in `text`, ignoring case."""
    options = wanted if isinstance(wanted, tuple) else (wanted,)
    return any(option.lower() in text.lower() for option in options)


def stated_dates(text: str) -> set[tuple[int | None, int, int]]:
    """Every (year, month, day) written in `text` as 2026-09-14,
    September 14[, 2026], 14 September or 9/14/2026; year is None when
    not stated. A slash needs a year, so fractions aren't read as dates."""
    def year(value: str) -> int | None:
        return int(value) if value else None

    dates = {(int(y), int(m), int(d)) for y, m, d in _ISO_DATE.findall(text)}
    dates |= {(year(y), _MONTHS[m.lower()], int(d)) for m, d, y in _NAMED_DATE.findall(text)}
    dates |= {(year(y), _MONTHS[m.lower()], int(d)) for d, m, y in _DAY_FIRST_DATE.findall(text)}
    dates |= {(int(y), int(m), int(d)) for m, d, y in _SLASH_DATE.findall(text)}
    return {(y, m, d) for y, m, d in dates if 1 <= m <= 12 and 1 <= d <= 31}


def unsupported_dates(answer: str, context: str) -> list[str]:
    """Dates the answer states that its context doesn't: no context date
    has that month and day, or the answer names a different year."""
    known = stated_dates(context)
    return [
        f"{m}/{d}" + (f"/{y}" if y else "")
        for y, m, d in sorted(stated_dates(answer), key=lambda date: (date[0] or 0, date[1], date[2]))
        if not any(km == m and kd == d and (y is None or ky in (None, y)) for ky, km, kd in known)
    ]


def case_key(case) -> str:
    """Identifies a case across runs: its query, marked when it carries
    history, a scratchpad, its own date or no records, since those reuse
    another case's query."""
    key = case.query
    if getattr(case, "history", ()):
        key += " [after history]"
    if getattr(case, "scratchpad", ()):
        key += " [after a tool round]"
    if getattr(case, "today", TODAY) != TODAY:
        key += f" [today {case.today.isoformat()}]"
    if getattr(case, "entities", None) == ():
        key += " [no records]"
    return key


async def _routing_case(case) -> tuple[bool, str]:
    state = {"query": case.query, "history": list(case.history)}
    agent = (await nodes.supervisor_node(state, {}))["selected_agent"]
    return agent == case.agent, f"got {agent}, wanted {case.agent}"


async def _action_case(case) -> tuple[bool, str]:
    matched = entity_grounding.find_matching_entities(case.query, HOUSEHOLD, uncapped=True)
    proposed = await nodes.decide_action(case.query, matched, case.today)
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


async def _household_entities(cookie):
    return HOUSEHOLD


async def _no_baseline_context(*args, **kwargs):
    return [], [], []


async def _chain_case(case) -> tuple[bool, str]:
    """Runs the compiled graph with the probe household, stopping before a
    specialist runs or the card is confirmed, so nothing is written."""
    graph = build_graph_builder().compile(
        checkpointer=MemorySaver(), interrupt_before=[*_SPECIALISTS, "execute_action"]
    )
    config = {"configurable": {"cookie": None, "thread_id": str(uuid4()), "today": case.today}}
    with (
        patch.object(nodes, "_fetch_entities_for_action", _household_entities),
        patch.object(nodes, "gather_baseline_context", _no_baseline_context),
    ):
        await graph.ainvoke({"query": case.query, "history": list(case.history)}, config)
    final = (await graph.aget_state(config)).values
    agent = final.get("selected_agent")
    if agent != case.agent:
        return False, f"routed to {agent}, wanted {case.agent}"
    proposed = final.get("proposed_action")
    if proposed is None:
        return case.tool is None, "no card" if agent == "action" else f"routed to {agent}"
    if case.tool is None:
        return False, f"unwanted card {proposed['summary']!r}"
    misses = [] if proposed["tool"] == case.tool else [f"tool={proposed['tool']} (wanted {case.tool})"]
    misses += [f"summary lacks {wanted!r}" for wanted in case.summary if not _contains(proposed["summary"], wanted)]
    return not misses, "; ".join([*misses, repr(proposed["summary"])])


async def _answer(messages: list[dict]) -> tuple[str, str | None]:
    """The chat answer as the chat route streams it, reasoning dropped,
    and Ollama's `done_reason` ('length' when generation hit the cap)."""
    stream_filter = get_adapter().create_stream_filter()
    parts = []
    done_reason = None
    async for chunk in ollama.chat_stream(messages):
        message = chunk.get("message") or {}
        segments = stream_filter.feed_thinking(message.get("thinking") or "")
        segments += stream_filter.feed(message.get("content") or "")
        parts += [text for kind, text in segments if kind == "answer"]
        done_reason = chunk.get("done_reason", done_reason)
    parts += [text for kind, text in stream_filter.flush() if kind == "answer"]
    return "".join(parts).strip(), done_reason


async def _answer_case(case) -> tuple[bool, str]:
    system = chat.build_system_prompt(
        getattr(agent_state, case.persona), [], list(case.entities), has_history=False
    )
    messages = [{"role": "system", "content": system}, {"role": "user", "content": case.query}]
    answer, done_reason = await _answer(messages)
    cut = done_reason == "length"
    if not answer:
        return False, "empty answer: reasoning used the whole length cap" if cut else "empty answer"
    misses = ["cut off at the length cap"] if cut else []
    misses += [f"doesn't mention {wanted!r}" for wanted in case.mentions if not _contains(answer, wanted)]
    invented = unsupported_dates(answer, system)
    if invented:
        misses.append("states dates not in its context: " + ", ".join(invented))
    excerpt = " ".join(answer.split())[:160]
    return not misses, "; ".join([*misses, repr(excerpt)])


SUITES: dict[str, tuple[tuple, Callable[..., Awaitable[tuple[bool, str]]]]] = {
    "routing": (ROUTING, _routing_case),
    "action": (ACTIONS, _action_case),
    "fuzzy": (FUZZY, _fuzzy_case),
    "gate": (GATE, _gate_case),
    "research": (RESEARCH, _research_case),
    "chain": (CHAIN, _chain_case),
    "answer": (ANSWERS, _answer_case),
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


def latest_baseline(suite_names: list[str], results_dir: Path = RESULTS_DIR) -> tuple[dict, list[str]] | None:
    """A record holding each of `suite_names` from the newest saved run
    that has it, with model and settings from the newest file used, and
    the names of the files used. None when no saved run has any of them.
    Files that aren't run records are skipped."""
    paths = sorted(results_dir.glob("*.json"), reverse=True) if results_dir.is_dir() else []
    baseline: dict | None = None
    sources: list[str] = []
    for path in paths:
        try:
            record = json.loads(path.read_text(encoding="utf-8"))
            suites = dict(record["suites"])
        except (ValueError, KeyError, TypeError):
            continue
        wanted = {name: suites[name] for name in suite_names if name in suites}
        if baseline is not None:
            wanted = {name: suite for name, suite in wanted.items() if name not in baseline["suites"]}
        if not wanted:
            continue
        if baseline is None:
            baseline = {**record, "suites": {}}
        baseline["suites"].update(wanted)
        sources.append(path.name)
        if len(baseline["suites"]) == len(suite_names):
            break
    return (baseline, sources) if baseline is not None else None


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
        help="a saved run to compare against, or 'last' for each suite's newest saved run in probes/results",
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
    baseline = None
    if args.compare == "last":
        baseline = latest_baseline(names)
        if baseline is None:
            raise SystemExit(f"no saved run in {RESULTS_DIR} has any of suites {', '.join(names)}")
    elif args.compare:
        baseline_path = Path(args.compare)
        if not baseline_path.is_file():
            raise SystemExit(f"no saved run at {baseline_path}")
        baseline = json.loads(baseline_path.read_text(encoding="utf-8")), [baseline_path.name]

    record = asyncio.run(run(names, args.repeat))
    path = save(record)
    print(f"\nsaved {path}")

    if baseline is not None:
        previous, sources = baseline
        print(f"\ncompared with {', '.join(sources)}")
        for line in compare(previous, record) or ["no suites in common"]:
            print(line)
    raise SystemExit(1 if failures(record) else 0)


if __name__ == "__main__":
    main()
