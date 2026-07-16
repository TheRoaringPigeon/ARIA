"""Generates services/frontend/src/domains/generated.ts from the Pydantic
entity models in this package. Python is the source of truth; this script
is the one-way sync step (see docs/scaling-debt.md item #1).

Regenerate whenever a domain is added/removed, or a field/status/log-type/
Literal option changes on any *Attrs class:

    uv run --package aria-shared python -m aria_shared.models.entities.export_ts \
        --out services/frontend/src/domains/generated.ts

libs/shared/tests/test_export_ts.py fails `pytest` if the committed file
doesn't match this script's current output.
"""

from __future__ import annotations

import argparse
import types
from datetime import date, datetime
from pathlib import Path
from typing import Annotated, Literal, Union, get_args, get_origin

from . import ENTITY_DOMAINS

_UNION_ORIGINS = {Union, types.UnionType}


def _unwrap_optional(annotation: object) -> tuple[object, bool]:
    if get_origin(annotation) in _UNION_ORIGINS:
        args = get_args(annotation)
        non_none = [a for a in args if a is not type(None)]
        if type(None) in args and len(non_none) == 1:
            return non_none[0], True
        raise NotImplementedError(f"Unsupported union annotation: {annotation!r}")
    return annotation, False


def _ts_type(annotation: object) -> str:
    """Maps a non-Optional Python annotation to a TS type string."""
    origin = get_origin(annotation)
    if origin is Annotated:
        return _ts_type(get_args(annotation)[0])
    if origin is Literal:
        return " | ".join(repr(v) for v in get_args(annotation))
    if origin is list:
        (inner,) = get_args(annotation)
        return f"{_ts_type(inner)}[]"
    if annotation is str:
        return "string"
    if annotation in (int, float):
        return "number"
    if annotation is bool:
        return "boolean"
    if annotation in (date, datetime):
        return "string"
    raise NotImplementedError(f"No TS mapping for {annotation!r} - extend export_ts._ts_type")


def _literal_values(annotation: object) -> list[str] | None:
    inner, _ = _unwrap_optional(annotation)
    return list(get_args(inner)) if get_origin(inner) is Literal else None


def _pascal(domain: str) -> str:
    return "".join(part.capitalize() for part in domain.split("_"))


def generate_ts() -> str:
    lines = [
        "// GENERATED FILE — do not edit by hand.",
        "// Source of truth: libs/shared/src/aria_shared/models/entities/*.py",
        "// Regenerate (from repo root):",
        "//   uv run --package aria-shared python -m aria_shared.models.entities.export_ts \\",
        "//     --out services/frontend/src/domains/generated.ts",
        "// Drift is caught by libs/shared/tests/test_export_ts.py (pytest).",
        "",
        f"export const ENTITY_DOMAINS = [{', '.join(repr(d) for d in ENTITY_DOMAINS)}] as const",
        "export type GeneratedEntityDomain = (typeof ENTITY_DOMAINS)[number]",
        "",
    ]

    all_log_types: list[str] = []
    generated_lines = ["export const GENERATED = {"]

    for domain, cls in ENTITY_DOMAINS.items():
        lines.append(f"export interface Generated{_pascal(domain)}Attrs {{")
        literal_options: dict[str, list[str]] = {}
        for name, field in cls.model_fields.items():
            inner, optional = _unwrap_optional(field.annotation)
            ts_type = _ts_type(inner) + (" | null" if optional else "")
            lines.append(f"  {name}{'?' if optional else ''}: {ts_type}")
            if name != "domain":
                lit = _literal_values(field.annotation)
                if lit is not None:
                    literal_options[name] = lit
        lines += ["}", ""]

        for lt in cls.LOG_TYPES:
            if lt not in all_log_types:
                all_log_types.append(lt)

        generated_lines.append(f"  {domain}: {{")
        generated_lines.append(f"    statuses: [{', '.join(repr(s) for s in cls.VALID_STATUSES)}] as const,")
        generated_lines.append(f"    logTypes: [{', '.join(repr(t) for t in cls.LOG_TYPES)}] as const,")
        if literal_options:
            generated_lines.append("    literalOptions: {")
            for field_name, values in literal_options.items():
                generated_lines.append(f"      {field_name}: [{', '.join(repr(v) for v in values)}] as const,")
            generated_lines.append("    },")
        else:
            generated_lines.append("    literalOptions: {},")
        generated_lines.append("  },")

    generated_lines.append("} as const")
    lines += generated_lines
    lines.append("")
    lines.append(f"export const LOG_TYPES = [{', '.join(repr(t) for t in all_log_types)}] as const")
    lines.append("export type LogType = (typeof LOG_TYPES)[number]")
    lines.append("export type LogTypeFor<D extends GeneratedEntityDomain> = (typeof GENERATED)[D]['logTypes'][number]")
    lines.append("")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--out", required=True, help="path to write generated.ts to, relative to CWD")
    parser.add_argument(
        "--check", action="store_true", help="exit 1 if --out doesn't match current output; don't write"
    )
    args = parser.parse_args(argv)

    content = generate_ts()
    out_path = Path(args.out)
    if args.check:
        existing = out_path.read_text(encoding="utf-8") if out_path.exists() else ""
        if existing != content:
            print(f"{out_path} is stale - run without --check to regenerate.")
            return 1
        return 0
    out_path.write_text(content, encoding="utf-8", newline="\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
