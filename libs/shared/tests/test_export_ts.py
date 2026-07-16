from pathlib import Path

from aria_shared.models.entities.export_ts import generate_ts

# tests/ -> libs/shared -> libs -> repo root
REPO_ROOT = Path(__file__).resolve().parents[3]
GENERATED_TS_PATH = REPO_ROOT / "services" / "frontend" / "src" / "domains" / "generated.ts"

REGENERATE_CMD = (
    "uv run --package aria-shared python -m aria_shared.models.entities.export_ts "
    "--out services/frontend/src/domains/generated.ts"
)


def test_generated_ts_matches_committed_file():
    """Fails if a *Attrs model (or VALID_STATUSES/LOG_TYPES/a Literal field)
    changed but services/frontend/src/domains/generated.ts wasn't
    regenerated and committed.

    Fix (from repo root): run `{REGENERATE_CMD}` and commit the diff.
    """
    assert GENERATED_TS_PATH.exists(), f"{GENERATED_TS_PATH} is missing - run: {REGENERATE_CMD}"
    committed = GENERATED_TS_PATH.read_text(encoding="utf-8")
    current = generate_ts()
    assert current == committed, (
        "services/frontend/src/domains/generated.ts is stale relative to the "
        f"Pydantic entity models. Run:\n  {REGENERATE_CMD}\nand commit the diff."
    )
