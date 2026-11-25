from datetime import date

from probes.__main__ import (
    SUITE_PROMPTS,
    SUITES,
    case_key,
    case_result,
    compare,
    latest_baseline,
    save,
    stated_dates,
    unsupported_dates,
)
from probes.cases import ActionCase, AnswerCase
from app.prompt_versions import PROMPT_VERSIONS


def _record(suites: dict, model: str = "m", num_ctx: int = 8192, started_at: str = "2026-01-01T00:00:00") -> dict:
    return {"started_at": started_at, "model": model, "num_ctx": num_ctx, "host": "h", "suites": suites}


def _suite(cases: list[tuple[str, bool]], versions: dict | None = None, seconds: float = 1.0) -> dict:
    return {
        "prompt_versions": versions or {"p": 1},
        "passed": sum(ok for _, ok in cases),
        "total": len(cases),
        "seconds_per_case": seconds,
        "cases": [{"key": key, "pass": ok, "detail": f"detail {key}", "seconds": seconds} for key, ok in cases],
    }


def test_case_keys_are_unique_within_each_suite():
    for name, (cases, _) in SUITES.items():
        keys = [case_key(case) for case in cases]
        assert len(keys) == len(set(keys)), name


def test_case_key_marks_a_case_with_its_own_date_or_no_records():
    assert case_key(ActionCase("q", None)) == "q"
    assert case_key(ActionCase("q", None, today=date(2026, 12, 31))) == "q [today 2026-12-31]"
    assert case_key(AnswerCase("q", "VEHICLE_PERSONA")) == "q [no records]"


def test_stated_dates_reads_iso_named_day_first_and_slash_dates():
    text = "Done 2026-09-14, due March 14th, 2027 or Sept. 3; also 12/1/2026 and 5th of June. Mayor 3 isn't one."
    assert stated_dates(text) == {(2026, 9, 14), (2027, 3, 14), (None, 9, 3), (2026, 12, 1), (None, 6, 5)}


def test_stated_dates_ignores_numbers_that_are_not_dates():
    assert stated_dates("61,500 miles, every 90 days, a 1/2 inch socket, 3/4 full, 2021 Ford") == set()


def test_unsupported_dates_checks_the_year_only_when_stated():
    context = "- 2026-09-14 (service): Oil change"
    assert unsupported_dates("on September 14 and 14 Sept, 2026", context) == []
    assert unsupported_dates("on September 14, 2025", context) == ["9/14/2025"]
    assert unsupported_dates("due October 1", context) == ["10/1"]


def test_every_suite_names_versioned_prompts():
    assert set(SUITE_PROMPTS) == set(SUITES)
    for prompts in SUITE_PROMPTS.values():
        assert all(p in PROMPT_VERSIONS for p in prompts)


def test_compare_reports_flips_new_and_removed_cases():
    previous = _record({"routing": _suite([("a", True), ("b", False), ("gone", True)])})
    current = _record({"routing": _suite([("a", False), ("b", True), ("fresh", True)], seconds=2.0)})

    lines = compare(previous, current)

    assert lines[0] == "routing: 2/3 -> 2/3, 1.0s -> 2.0s per case"
    assert "  PASS->FAIL  a  (1/1 -> 0/1)  -> detail a" in lines
    assert "  FAIL->PASS  b  (0/1 -> 1/1)" in lines
    assert "  new        PASS  fresh" in lines
    assert "  removed    gone" in lines


def test_compare_reports_model_and_prompt_version_changes():
    previous = _record({"gate": _suite([("a", True)], {"grounding_gate": 1})}, model="old")
    current = _record({"gate": _suite([("a", True)], {"grounding_gate": 2, "added": 1})}, model="new")

    lines = compare(previous, current)

    assert "model: old -> new" in lines
    assert "  grounding_gate v1 -> v2" in lines
    assert "  added v1 (new)" in lines


def test_compare_skips_suites_only_one_run_has():
    previous = _record({"routing": _suite([("a", True)])})
    current = _record({"gate": _suite([("a", True)])})

    assert compare(previous, current) == ["gate: not in the previous run"]


def test_latest_baseline_takes_each_suite_from_its_newest_run(tmp_path):
    assert latest_baseline(["routing"], tmp_path) is None
    routing = _suite([("a", True)])
    older = save(
        _record({"routing": routing, "gate": _suite([("old", True)])}, model="old", started_at="2026-01-01T00:00:00"),
        tmp_path,
    )
    new_gate = _suite([("new", True)])
    newer = save(_record({"gate": new_gate}, model="new", started_at="2026-01-02T00:00:00"), tmp_path)

    baseline, sources = latest_baseline(["routing", "gate", "chain"], tmp_path)

    assert baseline["suites"] == {"gate": new_gate, "routing": routing}
    assert baseline["model"] == "new"
    assert sources == [newer.name, older.name]


def test_latest_baseline_skips_files_that_are_not_run_records(tmp_path):
    good = save(_record({"gate": _suite([("a", True)])}, started_at="2026-01-01T00:00:00"), tmp_path)
    (tmp_path / "20260102T000000-gate.json").write_text("{truncated", encoding="utf-8")
    (tmp_path / "20260103T000000-gate.json").write_text("[]", encoding="utf-8")

    assert latest_baseline(["gate"], tmp_path)[1] == [good.name]


def test_case_result_passes_only_when_every_run_passes():
    stable = case_result("a", [(True, "ok", 1.0), (True, "ok", 3.0)])
    flaky = case_result("b", [(True, "ok", 1.0), (False, "got x", 1.0), (False, "got y", 1.0)])
    failing = case_result("c", [(False, "got x", 2.0)])

    assert stable == {"key": "a", "pass": True, "passes": 2, "runs": 2, "detail": "ok", "seconds": 2.0}
    assert (flaky["pass"], flaky["passes"], flaky["runs"], flaky["detail"]) == (False, 1, 3, "got x")
    assert (failing["pass"], failing["passes"], failing["runs"]) == (False, 0, 1)


def test_compare_reports_pass_rate_changes_without_a_flip():
    def repeated(passes: int) -> dict:
        suite = _suite([("a", False)])
        suite["cases"][0].update(passes=passes, runs=5)
        return suite

    previous = _record({"routing": repeated(1)})
    current = _record({"routing": repeated(4)})
    previous["repeat"] = current["repeat"] = 5

    assert "  1/5 -> 4/5 passed  a" in compare(previous, current)


def test_compare_reports_a_repeat_change():
    previous = _record({"routing": _suite([("a", True)])})
    current = _record({"routing": _suite([("a", True)])})
    current["repeat"] = 3

    assert "repeat: 1 -> 3" in compare(previous, current)


def _repeated_case(key: str, passes: int, runs: int) -> dict:
    return {"key": key, "pass": passes == runs, "passes": passes, "runs": runs, "detail": "d", "seconds": 1.0}


def test_compare_ignores_unchanged_cases_when_the_repeat_count_changes():
    previous = _record({"routing": _suite([])})
    previous["suites"]["routing"]["cases"] = [_repeated_case("a", 1, 1), _repeated_case("b", 0, 1)]
    current = _record({"routing": _suite([])})
    current["suites"]["routing"]["cases"] = [_repeated_case("a", 5, 5), _repeated_case("b", 0, 5)]

    case_lines = [line for line in compare(previous, current) if line.startswith("  ")]

    assert case_lines == []


def test_compare_reports_a_fail_saved_before_repeat_that_is_now_flaky():
    previous = _record({"routing": _suite([("a", False)])})
    current = _record({"routing": _suite([])})
    current["suites"]["routing"]["cases"] = [_repeated_case("a", 3, 5), _repeated_case("fresh", 2, 5)]

    lines = compare(previous, current)

    assert "  0/1 -> 3/5 passed  a" in lines
    assert "  new        FLAKY 2/5  fresh" in lines
