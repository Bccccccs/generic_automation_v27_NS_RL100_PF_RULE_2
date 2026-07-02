from __future__ import annotations

import logging
import math
import re
import sys
import time
from pathlib import Path
from typing import Any

from generic_automation.core.adapter_base import Case
from generic_automation.core.runtime_value_utils import safe_float as _safe_float

log = logging.getLogger(__name__)

_HEADER_MARKER = "iteration"
_STARCCM_INVALID_SENTINEL_MIN = -sys.float_info.max / 2.0
_STARCCM_CPU_TIME_COLUMN = "Total Solver CPU Time"
_STARCCM_CPU_TIME_ALIAS = "total_solver_cpu_time"
_TURBULENT_VISCOSITY_LIMITED_RE = re.compile(
    r"Turbulent viscosity limited on\s+(\d+)\s+cells",
    re.IGNORECASE,
)
_SOLVER_ITERATION_FIELD_BY_TARGET = {
    "pressure": "pressure_solver_iterations",
    "velocity": "velocity_solver_iterations",
    "tke": "tke_solver_iterations",
    "sdr": "sdr_solver_iterations",
    "energy": "energy_solver_iterations",
}
_AMG_CYCLE_FIELD_BY_TARGET = {
    "pressure": "pressure_amg_cycles",
    "velocity": "velocity_amg_cycles",
    "tke": "tke_amg_cycles",
    "sdr": "sdr_amg_cycles",
    "energy": "energy_amg_cycles",
}
_SOLVER_METRIC_TARGET_PATTERNS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("pressure", ("pressure", "continuity")),
    ("velocity", ("velocity", "x-momentum", "y-momentum", "z-momentum", "momentum")),
    ("tke", ("tke", "turbulent kinetic energy")),
    ("sdr", ("sdr", "specific dissipation rate")),
    ("energy", ("energy",)),
)
_SOLVER_ITERATION_PHRASES = (
    "linear solver iterations",
    "linear solver iteration",
    "solver iterations",
    "solver iteration",
    "inner iterations",
    "inner iteration",
    "amg iterations",
    "amg iteration",
)
_AMG_CYCLE_PHRASES = (
    "amg cycles",
    "amg cycle",
    "multigrid cycles",
    "multigrid cycle",
)
_STARCCM_RESIDUAL_COLUMNS = (
    "Continuity",
    "X-momentum",
    "Y-momentum",
    "Z-momentum",
    "Tke",
    "Sdr",
    "Energy",
)
_RESIDUAL_OBSERVATION_ALIASES = {
    "Continuity": "continuity_residual",
    "X-momentum": "x_momentum_residual",
    "Y-momentum": "y_momentum_residual",
    "Z-momentum": "z_momentum_residual",
    "Tke": "tke_residual",
    "Sdr": "sdr_residual",
    "Energy": "energy_residual",
}


class StarCCMLogReader:
    def __init__(self, case: Case) -> None:
        self._case = case
        self._live_log_pos: int = 0
        self._live_log_partial_line: str = ""
        self._starccm_table_headers: list[str] = []
        self._starccm_table_spans: list[tuple[str, int, int | None]] = []
        self._first_observation_monotonic: float | None = None
        self._warned_primary_metric_fallback: bool = False
        self._warned_missing_pressure_metric: bool = False
        self._warned_invalid_row_reason: set[str] = set()
        self._pending_turbulent_viscosity_limited_cells: int | None = None
        self._pending_solver_metrics: dict[str, float] = {}

    def read_new_rows(
        self,
        live_log: Path,
        previous_row: dict[str, Any] | None,
    ) -> list[dict[str, Any]]:
        if not live_log.exists():
            return []

        rows: list[dict[str, Any]] = []
        with live_log.open("rb") as f:
            f.seek(self._live_log_pos)
            chunk = f.read()
            self._live_log_pos = f.tell()

        if not chunk:
            return rows

        text = self._live_log_partial_line + chunk.replace(b"\x00", b"\n").decode(
            "utf-8",
            errors="ignore",
        )
        self._live_log_partial_line = ""

        current_previous = previous_row
        for line in text.splitlines(keepends=True):
            if line.endswith("\n") or line.endswith("\r"):
                parsed = self._parse_starccm_line(line.rstrip("\r\n"))
                if parsed is None:
                    continue
                self._enrich_observation_row(parsed, current_previous)
                is_valid, invalid_reason = self._validate_observation_row(
                    parsed,
                    current_previous,
                )
                if not is_valid:
                    self._warn_invalid_row(invalid_reason, parsed)
                    continue
                rows.append(parsed)
                current_previous = parsed
            else:
                self._live_log_partial_line = line
        return rows

    def read_all_rows(self, log_path: Path) -> list[dict[str, Any]]:
        if not log_path.exists():
            return []

        rows: list[dict[str, Any]] = []
        previous_row: dict[str, Any] | None = None
        self._live_log_pos = 0
        self._live_log_partial_line = ""
        self._starccm_table_headers = []
        self._starccm_table_spans = []
        self._first_observation_monotonic = None
        self._pending_turbulent_viscosity_limited_cells = None
        self._pending_solver_metrics = {}

        with log_path.open(encoding="utf-8", errors="ignore") as handle:
            for raw_line in handle:
                parsed = self._parse_starccm_line(raw_line.rstrip("\r\n"))
                if parsed is None:
                    continue
                self._enrich_observation_row(parsed, previous_row)
                is_valid, invalid_reason = self._validate_observation_row(
                    parsed,
                    previous_row,
                )
                if not is_valid:
                    self._warn_invalid_row(invalid_reason, parsed)
                    continue
                rows.append(parsed)
                previous_row = parsed
        return rows

    @staticmethod
    def public_row_observation(row: dict[str, Any]) -> dict[str, Any]:
        return {
            key: value
            for key, value in row.items()
            if not str(key).startswith("_")
        }

    def _parse_starccm_line(self, line: str) -> dict[str, Any] | None:
        stripped = line.strip()
        if not stripped:
            self._starccm_table_headers = []
            self._starccm_table_spans = []
            return None

        turb_match = _TURBULENT_VISCOSITY_LIMITED_RE.search(stripped)
        if turb_match:
            self._pending_turbulent_viscosity_limited_cells = int(turb_match.group(1))
            return None

        solver_metrics = _extract_solver_metric_updates_from_line(stripped)
        if solver_metrics:
            self._pending_solver_metrics.update(solver_metrics)
            return None

        headers = self._parse_starccm_header(line)
        if headers is not None:
            return None

        if not self._starccm_table_headers or not self._starccm_table_spans:
            return None

        token_matches = list(re.finditer(r"\S+", line))
        used_token_indexes: set[int] = set()
        row: dict[str, Any] = {}
        for header, start, end in self._starccm_table_spans:
            token = _extract_starccm_token_for_span(
                token_matches,
                start,
                end,
                used_token_indexes,
            )
            if not token:
                continue
            if header == "Iteration":
                if not token.isdigit():
                    return None
                row["iteration"] = int(token)
                continue
            normalized_name = _normalize_starccm_column_name(header)
            if not normalized_name:
                continue
            try:
                numeric_value = float(token)
            except ValueError:
                continue
            if not _is_valid_starccm_value(numeric_value):
                continue
            row[normalized_name] = numeric_value

        if "iteration" not in row:
            return None

        residual_values = [
            abs(float(row[name]))
            for name in _STARCCM_RESIDUAL_COLUMNS
            if name in row and math.isfinite(float(row[name]))
        ]
        if residual_values:
            row["max_residual"] = max(residual_values)

        cpu_time_value = row.get(_STARCCM_CPU_TIME_COLUMN)
        if isinstance(cpu_time_value, (int, float)) and math.isfinite(float(cpu_time_value)):
            row[_STARCCM_CPU_TIME_ALIAS] = float(cpu_time_value)

        for source_name, alias_name in _RESIDUAL_OBSERVATION_ALIASES.items():
            if source_name in row:
                row[alias_name] = float(row[source_name])

        train_pressure_name = str(self._case.train_surface_pressure_report_name or "").strip()
        if train_pressure_name:
            pressure_aliases = [
                train_pressure_name,
                str(self._case.pressure_report_name or "").strip(),
                "pressure",
            ]
            if train_pressure_name not in row:
                for alias in pressure_aliases:
                    if alias and alias in row:
                        row[train_pressure_name] = row[alias]
                        row["pressure_metric_source"] = (
                            "iteration_table:primary"
                            if alias == train_pressure_name
                            else f"iteration_table:alias:{alias}"
                        )
                        break
                if train_pressure_name not in row and not self._warned_missing_pressure_metric:
                    log.warning(
                        "STAR-CCM+ iteration table did not expose pressure metric '%s'; "
                        "RL observation will continue without it.",
                        train_pressure_name,
                    )
                    self._warned_missing_pressure_metric = True
            else:
                row["pressure_metric_source"] = "iteration_table:primary"

        primary_metric_name = str(self._case.drag_report_name or "").strip()
        total_metric_name = str(self._case.total_report_name or "").strip()
        if total_metric_name and total_metric_name in row:
            row["total_force_source"] = "iteration_table:total"
        if primary_metric_name and primary_metric_name not in row and total_metric_name:
            fallback_value = row.get(total_metric_name)
            if fallback_value is not None:
                row[primary_metric_name] = fallback_value
                row["drag_metric_source"] = "iteration_table:total_fallback"
                if not self._warned_primary_metric_fallback:
                    log.warning(
                        "STAR-CCM+ iteration table did not expose primary metric '%s'; "
                        "falling back to '%s' so RL can continue.",
                        primary_metric_name,
                        total_metric_name,
                    )
                    self._warned_primary_metric_fallback = True
        elif primary_metric_name and primary_metric_name in row:
            row["drag_metric_source"] = "iteration_table:primary"

        if primary_metric_name and primary_metric_name not in row:
            return None

        if self._pending_turbulent_viscosity_limited_cells is not None:
            row["turbulent_viscosity_limited_cells"] = int(
                self._pending_turbulent_viscosity_limited_cells
            )
            self._pending_turbulent_viscosity_limited_cells = None
        if self._pending_solver_metrics:
            for key, value in self._pending_solver_metrics.items():
                if key not in row:
                    row[key] = float(value)
            self._pending_solver_metrics = {}

        return row

    def _parse_starccm_header(self, line: str) -> list[str] | None:
        if _HEADER_MARKER not in line.lower():
            return None

        header_positions = [
            (line.index(label), label)
            for label in self._starccm_header_candidates()
            if label in line
        ]
        headers = [label for _, label in sorted(header_positions)]
        if not headers or headers[0].lower() != "iteration":
            return None

        normalized_headers = [_normalize_starccm_column_name(header) for header in headers]
        primary_candidates = {
            name
            for name in {
                str(self._case.drag_report_name or "").strip(),
                str(self._case.total_report_name or "").strip(),
            }
            if name
        }
        if primary_candidates and not any(name in normalized_headers for name in primary_candidates):
            return None

        ordered_positions = sorted(header_positions)
        spans: list[tuple[str, int, int | None]] = []
        for idx, (start, label) in enumerate(ordered_positions):
            end = ordered_positions[idx + 1][0] if idx + 1 < len(ordered_positions) else None
            spans.append((label, start, end))
        self._starccm_table_headers = headers
        self._starccm_table_spans = spans
        return headers

    def _validate_observation_row(
        self,
        row: dict[str, Any],
        previous_row: dict[str, Any] | None,
    ) -> tuple[bool, str | None]:
        current_iteration = int(row.get("iteration", 0))
        if current_iteration <= 0:
            return False, "non_positive_iteration"

        if previous_row is not None:
            previous_iteration = int(previous_row.get("iteration", 0))
            if current_iteration <= previous_iteration:
                return False, "non_monotonic_iteration"

        current_residual = _safe_float(row.get("max_residual"))
        if current_residual is None or current_residual <= 0.0:
            return False, "missing_max_residual"

        current_cpu_seconds = _safe_float(row.get(_STARCCM_CPU_TIME_ALIAS))
        if current_cpu_seconds is not None and current_cpu_seconds < 0.0:
            return False, "negative_total_solver_cpu_time"

        if previous_row is not None:
            previous_cpu_seconds = _safe_float(previous_row.get(_STARCCM_CPU_TIME_ALIAS))
            if (
                current_cpu_seconds is not None
                and previous_cpu_seconds is not None
                and current_cpu_seconds + 1.0e-9 < previous_cpu_seconds
            ):
                return False, "non_monotonic_total_solver_cpu_time"

        return True, None

    def _warn_invalid_row(
        self,
        reason: str | None,
        row: dict[str, Any],
    ) -> None:
        reason_key = reason or "unknown"
        if reason_key in self._warned_invalid_row_reason:
            return
        self._warned_invalid_row_reason.add(reason_key)
        log.warning(
            "Ignoring malformed STAR-CCM+ observation row at iter=%s: %s; row keys=%s",
            row.get("iteration"),
            reason_key,
            sorted(row.keys()),
        )

    def _starccm_header_candidates(self) -> list[str]:
        candidates = [
            "Iteration",
            *_STARCCM_RESIDUAL_COLUMNS,
            f"{_STARCCM_CPU_TIME_COLUMN} (s)",
            f"{self._case.drag_report_name} (N)",
        ]
        total_report_name = str(self._case.total_report_name or "").strip()
        if total_report_name:
            candidates.append(f"{total_report_name} (N)")
        pressure_report_name = str(self._case.pressure_report_name or "").strip()
        train_pressure_name = str(self._case.train_surface_pressure_report_name or "").strip()
        if pressure_report_name or train_pressure_name:
            candidates.append("pressure (Pa)")
        if pressure_report_name:
            candidates.append(f"{pressure_report_name} (Pa)")
        if train_pressure_name:
            candidates.append(f"{train_pressure_name} (Pa)")
        deduped: list[str] = []
        seen: set[str] = set()
        for label in candidates:
            if not label or label in seen:
                continue
            seen.add(label)
            deduped.append(label)
        return deduped

    def _enrich_observation_row(
        self,
        row: dict[str, Any],
        previous_row: dict[str, Any] | None,
    ) -> None:
        arrival_monotonic = time.monotonic()
        row["_arrival_monotonic"] = arrival_monotonic

        current_iteration = int(row.get("iteration", 0))
        previous_iteration = int(previous_row.get("iteration", 0)) if previous_row else 0
        iteration_delta = max(current_iteration - previous_iteration, 0)
        if iteration_delta > 0:
            row["iteration_delta"] = iteration_delta

        current_cpu_seconds = _safe_float(row.get(_STARCCM_CPU_TIME_ALIAS))
        previous_cpu_seconds = _safe_float(
            previous_row.get(_STARCCM_CPU_TIME_ALIAS) if previous_row else None
        )
        num_cores = max(1, int(getattr(self._case, "num_cores", 1) or 1))

        if current_cpu_seconds is not None and current_cpu_seconds >= 0.0:
            wall_time_since_start = current_cpu_seconds / float(num_cores)
            row["wall_time_since_start"] = wall_time_since_start
            row["cpu_hours_so_far"] = current_cpu_seconds / 3600.0
            if previous_cpu_seconds is not None:
                delta_cpu_seconds = max(0.0, current_cpu_seconds - previous_cpu_seconds)
                row["cpu_time_per_chunk"] = delta_cpu_seconds
                row["wall_time_per_chunk"] = delta_cpu_seconds / float(num_cores)
                if iteration_delta > 0:
                    row["cpu_time_per_iteration"] = delta_cpu_seconds / float(iteration_delta)
                    row["wall_time_per_iteration"] = (
                        row["wall_time_per_chunk"] / float(iteration_delta)
                    )
        else:
            if self._first_observation_monotonic is None:
                self._first_observation_monotonic = arrival_monotonic
            wall_time_since_start = max(
                0.0,
                arrival_monotonic - self._first_observation_monotonic,
            )
            row["wall_time_since_start"] = wall_time_since_start
            row["cpu_hours_so_far"] = (wall_time_since_start / 3600.0) * float(num_cores)
            if previous_row is not None:
                previous_arrival = _safe_float(previous_row.get("_arrival_monotonic"))
                if previous_arrival is not None:
                    row["wall_time_per_chunk"] = max(
                        0.0,
                        arrival_monotonic - previous_arrival,
                    )
                    row["cpu_time_per_chunk"] = (
                        row["wall_time_per_chunk"] * float(num_cores)
                    )
                    if iteration_delta > 0:
                        row["wall_time_per_iteration"] = (
                            row["wall_time_per_chunk"] / float(iteration_delta)
                        )
                        row["cpu_time_per_iteration"] = (
                            row["cpu_time_per_chunk"] / float(iteration_delta)
                        )


def _extract_starccm_token_for_span(
    token_matches: list[re.Match[str]],
    start: int,
    end: int | None,
    used_token_indexes: set[int],
) -> str | None:
    best_index: int | None = None
    best_score: tuple[int, int] | None = None
    span_end = end if end is not None else math.inf

    for index, match in enumerate(token_matches):
        if index in used_token_indexes:
            continue
        token_start = match.start()
        token_end = match.end()
        if token_start >= span_end:
            break
        if token_end <= start:
            continue

        distance = 0
        if token_start > start:
            distance = token_start - start
        score = (distance, token_start)
        if best_score is None or score < best_score:
            best_score = score
            best_index = index

    if best_index is None:
        return None

    used_token_indexes.add(best_index)
    return token_matches[best_index].group(0).strip()


def _normalize_starccm_column_name(header: str) -> str:
    return re.sub(r"\s+\([^()]+\)$", "", header.strip())


def _is_valid_starccm_value(value: float) -> bool:
    if not math.isfinite(value):
        return False
    if value <= _STARCCM_INVALID_SENTINEL_MIN:
        return False
    return True


def _extract_solver_metric_updates_from_line(line: str) -> dict[str, float]:
    line_lower = line.lower()
    target_key = _detect_solver_metric_target(line_lower)
    if target_key is None:
        return {}

    updates: dict[str, float] = {}

    solver_iterations = _extract_named_count(line_lower, _SOLVER_ITERATION_PHRASES)
    if solver_iterations is not None:
        field_name = _SOLVER_ITERATION_FIELD_BY_TARGET.get(target_key)
        if field_name is not None:
            updates[field_name] = float(solver_iterations)

    amg_cycles = _extract_named_count(line_lower, _AMG_CYCLE_PHRASES)
    if amg_cycles is not None:
        field_name = _AMG_CYCLE_FIELD_BY_TARGET.get(target_key)
        if field_name is not None:
            updates[field_name] = float(amg_cycles)

    return updates


def _detect_solver_metric_target(line_lower: str) -> str | None:
    for target_key, aliases in _SOLVER_METRIC_TARGET_PATTERNS:
        if any(alias in line_lower for alias in aliases):
            return target_key
    return None


def _extract_named_count(line_lower: str, phrases: tuple[str, ...]) -> int | None:
    for phrase in phrases:
        escaped_phrase = re.escape(phrase)
        patterns = (
            rf"{escaped_phrase}\s*(?:=|:)?\s*(\d+)",
            rf"(\d+)\s*{escaped_phrase}",
            rf"{escaped_phrase}[^\d]{{0,20}}(\d+)",
            rf"(\d+)[^\d]{{0,20}}{escaped_phrase}",
        )
        for pattern in patterns:
            match = re.search(pattern, line_lower, flags=re.IGNORECASE)
            if match:
                return int(match.group(1))
    return None
