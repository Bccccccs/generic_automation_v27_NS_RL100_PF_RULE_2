"""Shared path rules for case-oriented flow-control workflows."""

from __future__ import annotations

from pathlib import Path


def resolve_case_dir(
    *,
    case_id: str | None = None,
    case_dir: str | Path | None = None,
    runs_root: str | Path = "runs",
) -> Path:
    """Resolve a standard case directory.

    The preferred form is ``case_id`` which maps to ``runs/<case_id>``.
    ``case_dir`` is kept for explicit legacy or temporary paths.
    """

    if case_dir is not None:
        return Path(case_dir)
    if not case_id or Path(case_id).name != case_id:
        raise ValueError("case_id must be a plain directory name when case_dir is omitted")
    return Path(runs_root) / case_id


def resolve_case_input_dir(
    *,
    case_id: str | None = None,
    case_dir: str | Path | None = None,
    runs_root: str | Path = "runs",
) -> Path:
    """Return ``runs/<case_id>/input`` for a standard case."""

    return resolve_case_dir(case_id=case_id, case_dir=case_dir, runs_root=runs_root) / "input"
