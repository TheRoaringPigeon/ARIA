from probes.__main__ import SUITE_PROMPTS, SUITES, case_key, compare, latest_result, save
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


def test_every_suite_names_versioned_prompts():
    assert set(SUITE_PROMPTS) == set(SUITES)
    for prompts in SUITE_PROMPTS.values():
        assert all(p in PROMPT_VERSIONS for p in prompts)


def test_compare_reports_flips_new_and_removed_cases():
    previous = _record({"routing": _suite([("a", True), ("b", False), ("gone", True)])})
    current = _record({"routing": _suite([("a", False), ("b", True), ("fresh", True)], seconds=2.0)})

    lines = compare(previous, current)

    assert lines[0] == "routing: 2/3 -> 2/3, 1.0s -> 2.0s per case"
    assert "  PASS->FAIL  a  -> detail a" in lines
    assert "  FAIL->PASS  b" in lines
    assert "  new        PASS  fresh" in lines
    assert "  removed    gone" in lines


def test_compare_reports_model_and_prompt_version_changes():
    previous = _record({"gate": _suite([("a", True)], {"grounding_gate": 1})}, model="old")
    current = _record({"gate": _suite([("a", True)], {"grounding_gate": 2})}, model="new")

    lines = compare(previous, current)

    assert "model: old -> new" in lines
    assert "  grounding_gate v1 -> v2" in lines


def test_compare_skips_suites_only_one_run_has():
    previous = _record({"routing": _suite([("a", True)])})
    current = _record({"gate": _suite([("a", True)])})

    assert compare(previous, current) == ["gate: not in the previous run"]


def test_latest_result_is_the_newest_run_with_those_suites(tmp_path):
    assert latest_result(["routing"], tmp_path) is None
    older = save(_record({"routing": _suite([("a", True)])}, started_at="2026-01-01T00:00:00"), tmp_path)
    newer = save(_record({"gate": _suite([("a", True)])}, started_at="2026-01-02T00:00:00"), tmp_path)

    assert newer.name == "20260102T000000-gate.json"
    assert latest_result(["gate"], tmp_path) == newer
    assert latest_result(["routing"], tmp_path) == older
    assert latest_result(["routing", "gate"], tmp_path) is None


def test_latest_result_skips_files_that_are_not_run_records(tmp_path):
    good = save(_record({"gate": _suite([("a", True)])}, started_at="2026-01-01T00:00:00"), tmp_path)
    (tmp_path / "20260102T000000-gate.json").write_text("{truncated", encoding="utf-8")
    (tmp_path / "20260103T000000-gate.json").write_text("[]", encoding="utf-8")

    assert latest_result(["gate"], tmp_path) == good
