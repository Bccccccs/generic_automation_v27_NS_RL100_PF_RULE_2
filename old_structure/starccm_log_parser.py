from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from adapter_base import Case
from starccm_log_reader import (
    StarCCMLogReader,
    _AMG_CYCLE_PHRASES,
    _SOLVER_ITERATION_PHRASES,
    _detect_solver_metric_target,
    _extract_solver_metric_updates_from_line,
)

_MESH_TOTAL_CELL_COUNT_RE = re.compile(
    r"(?:new\s+)?total cell count\s*=\s*(\d+)",
    re.IGNORECASE,
)
_MESH_CANDIDATE_CELL_COUNT_RE = re.compile(
    r"Starting optimization on\s+(\d+)\s+candidate cells",
    re.IGNORECASE,
)
_MESH_EXTRUSION_CELL_COUNT_RE = re.compile(
    r"\[Global\]\s+(\d+)\s+cells\b",
    re.IGNORECASE,
)
_SOLVER_METRIC_GENERIC_HINT_TOKENS = (
    "linear solver",
    "solver iter",
    "solver iteration",
    "inner iter",
    "amg",
    "multigrid",
    "cycle",
)


def _diagnose_solver_metric_candidate_line(line: str) -> dict[str, Any] | None:
    line_lower = line.lower()
    target_key = _detect_solver_metric_target(line_lower)
    has_iteration_hint = any(phrase in line_lower for phrase in _SOLVER_ITERATION_PHRASES)
    has_cycle_hint = any(phrase in line_lower for phrase in _AMG_CYCLE_PHRASES)
    has_generic_hint = any(token in line_lower for token in _SOLVER_METRIC_GENERIC_HINT_TOKENS)
    if not (has_iteration_hint or has_cycle_hint or has_generic_hint):
        return None

    updates = _extract_solver_metric_updates_from_line(line)
    if updates:
        return {
            "matched": True,
            "reason": "matched",
            "target_key": target_key,
        }

    has_numeric_count = re.search(r"\d", line_lower) is not None
    if target_key is not None:
        return {
            "matched": False,
            "reason": (
                "target_detected_but_pattern_unmatched"
                if has_numeric_count
                else "target_detected_but_no_numeric_count"
            ),
            "target_key": target_key,
        }
    if has_numeric_count:
        return {
            "matched": False,
            "reason": "metric_hint_without_target",
            "target_key": None,
        }
    return None


def parse_starccm_log_rows(case: Case, log_path: Path) -> list[dict[str, Any]]:
    return StarCCMLogReader(case).read_all_rows(log_path)


def parse_starccm_solver_metric_diagnostics(
    log_path: Path,
    sample_limit: int = 20,
) -> dict[str, Any]:
    if not log_path.exists():
        return {
            "matched_lines": 0,
            "sample_lines": [],
            "rejected_candidate_lines": 0,
            "rejected_candidate_sample_lines": [],
            "rejected_candidate_reasons_sample": [],
        }

    matched_lines = 0
    sample_lines: list[str] = []
    rejected_candidate_lines = 0
    rejected_candidate_sample_lines: list[str] = []
    rejected_candidate_reasons_sample: list[str] = []
    with log_path.open(encoding="utf-8", errors="ignore") as handle:
        for raw_line in handle:
            stripped = raw_line.strip()
            if not stripped:
                continue
            diagnostic = _diagnose_solver_metric_candidate_line(stripped)
            if diagnostic is None:
                continue
            if diagnostic.get("matched"):
                matched_lines += 1
                if len(sample_lines) < sample_limit:
                    sample_lines.append(stripped)
                continue
            rejected_candidate_lines += 1
            if len(rejected_candidate_sample_lines) < sample_limit:
                rejected_candidate_sample_lines.append(stripped)
            if len(rejected_candidate_reasons_sample) < sample_limit:
                reason = str(diagnostic.get("reason") or "unknown")
                target_key = diagnostic.get("target_key")
                if target_key:
                    rejected_candidate_reasons_sample.append(
                        f"{reason} (target={target_key})"
                    )
                else:
                    rejected_candidate_reasons_sample.append(reason)

    return {
        "matched_lines": matched_lines,
        "sample_lines": sample_lines,
        "rejected_candidate_lines": rejected_candidate_lines,
        "rejected_candidate_sample_lines": rejected_candidate_sample_lines,
        "rejected_candidate_reasons_sample": rejected_candidate_reasons_sample,
    }


def parse_starccm_mesh_cell_count(log_path: Path) -> int | None:
    if not log_path.exists():
        return None

    last_total_cell_count: int | None = None
    last_trimmed_total_cell_count: int | None = None
    last_extrusion_cell_count: int | None = None
    last_candidate_cell_count: int | None = None
    expecting_extrusion_cells = False
    inside_trimmed_mesh_block = False

    with log_path.open(encoding="utf-8", errors="ignore") as handle:
        for raw_line in handle:
            line = raw_line.strip()
            if not line:
                expecting_extrusion_cells = False
                inside_trimmed_mesh_block = False
                continue

            if "Trimmed mesh contains" in line:
                inside_trimmed_mesh_block = True
                continue

            if "Extrusion mesh contains" in line:
                expecting_extrusion_cells = True
                continue

            match = _MESH_CANDIDATE_CELL_COUNT_RE.search(line)
            if match:
                last_candidate_cell_count = int(match.group(1))

            match = _MESH_TOTAL_CELL_COUNT_RE.search(line)
            if match:
                last_total_cell_count = int(match.group(1))
                if inside_trimmed_mesh_block:
                    last_trimmed_total_cell_count = last_total_cell_count
                continue

            if expecting_extrusion_cells:
                match = _MESH_EXTRUSION_CELL_COUNT_RE.search(line)
                if match:
                    last_extrusion_cell_count = int(match.group(1))
                    expecting_extrusion_cells = False

    if last_candidate_cell_count is not None:
        return last_candidate_cell_count
    if (
        last_trimmed_total_cell_count is not None
        and last_extrusion_cell_count is not None
    ):
        return last_trimmed_total_cell_count + last_extrusion_cell_count
    if last_total_cell_count is not None:
        return last_total_cell_count
    return None
