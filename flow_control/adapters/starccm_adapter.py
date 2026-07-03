"""Flow-control adapter that emits STAR-CCM+ runtime command plans."""

from __future__ import annotations

import csv
from collections.abc import Iterable
from pathlib import Path
from typing import Any

from flow_control.excitation_patterns.common import MASSFLOW_COLUMNS
from flow_control.starccm_translator import FlowControlStarCCMTranslator
from starccm.control import DEFAULT_STARCCM_SPEC, StarCCMControlSpec
from starccm.control.control_spec import JET_COLUMNS
from starccm.runtime import StarCCMCommand, StarCCMCommandPlan


class FlowControlStarCCMAdapter:
    """Prepare flow-control actuation windows for the shared STAR runtime layer."""

    def __init__(
        self,
        *,
        spec: StarCCMControlSpec = DEFAULT_STARCCM_SPEC,
        translator: FlowControlStarCCMTranslator | None = None,
    ) -> None:
        self.spec = spec.require_valid()
        self.translator = translator or FlowControlStarCCMTranslator(self.spec)

    def plan_from_schedule_csv(
        self,
        schedule_path: str | Path,
        *,
        time_step: float | None = None,
    ) -> StarCCMCommandPlan:
        """Read an actuation schedule CSV and return one flattened runtime plan."""

        path = Path(schedule_path)
        rows = self._read_schedule_rows(path)
        return self.plan_from_schedule_rows(
            rows,
            schedule_path=path,
            time_step=time_step,
        )

    def write_runtime_plan(
        self,
        schedule_path: str | Path,
        output_path: str | Path | None = None,
        *,
        time_step: float | None = None,
    ) -> Path:
        """Write ``starccm_runtime_plan.json`` next to the schedule by default."""

        schedule = Path(schedule_path)
        plan = self.plan_from_schedule_csv(schedule, time_step=time_step)
        destination = Path(output_path) if output_path is not None else schedule.parent / "starccm_runtime_plan.json"
        plan.write_json(destination)
        return destination

    def plan_from_schedule_rows(
        self,
        rows: Iterable[dict[str, Any]],
        *,
        schedule_path: str | Path | None = None,
        time_step: float | None = None,
    ) -> StarCCMCommandPlan:
        """Translate schedule rows into one ordered command plan for STAR-CCM+."""

        schedule_rows = [dict(row) for row in rows]
        if not schedule_rows:
            raise ValueError("actuation schedule must contain at least one row")

        commands: list[StarCCMCommand] = []
        window_ids: list[int] = []
        active_jets: set[str] = set()
        physical_times: list[float] = []

        for row_idx, row in enumerate(schedule_rows):
            window_id = self._window_id(row, row_idx)
            duration = self._window_duration(row)
            jet_commands = self._jet_commands(row)
            window_plan = self.translator.translate_window(
                jet_commands,
                window_id=window_id,
                duration=duration,
                time_step=time_step,
            )
            commands.extend(window_plan.commands)
            window_ids.append(window_id)
            active_jets.update(
                column for column, value in jet_commands.items() if float(value) != 0.0
            )
            if "physical_time" in row:
                physical_times.append(self._float_field(row, "physical_time"))

        metadata: dict[str, Any] = {
            "schedule_path": str(Path(schedule_path)) if schedule_path is not None else None,
            "window_count": len(schedule_rows),
            "window_ids": window_ids,
            "active_jets": sorted(active_jets),
            "command_source": "cmd_massflow_columns",
        }
        if physical_times:
            metadata["physical_time_start"] = physical_times[0]
            metadata["physical_time_end"] = round(
                physical_times[-1] + self._window_duration(schedule_rows[-1]),
                12,
            )

        return StarCCMCommandPlan(
            source="flow_control",
            commands=tuple(commands),
            metadata=metadata,
        )

    def _jet_commands(self, row: dict[str, Any]) -> dict[str, float]:
        has_massflow_columns = all(column in row for column in MASSFLOW_COLUMNS)
        commands: dict[str, float] = {}
        for jet_column, massflow_column in zip(JET_COLUMNS, MASSFLOW_COLUMNS):
            if has_massflow_columns:
                commands[jet_column] = self._float_field(row, massflow_column)
            else:
                commands[jet_column] = self._float_field(row, jet_column)
        return commands

    def _window_duration(self, row: dict[str, Any]) -> float:
        if "t_start" in row and "t_end" in row:
            duration = round(
                self._float_field(row, "t_end") - self._float_field(row, "t_start"),
                12,
            )
            if duration <= 0.0:
                raise ValueError("actuation schedule row has non-positive t_end - t_start")
            return duration
        return float(self.spec.window_duration)

    @staticmethod
    def _read_schedule_rows(path: Path) -> list[dict[str, str]]:
        if not path.exists():
            raise FileNotFoundError(f"actuation schedule not found: {path}")
        with path.open("r", encoding="utf-8", newline="") as handle:
            rows = list(csv.DictReader(handle))
        if not rows:
            raise ValueError(f"actuation schedule is empty: {path}")
        return rows

    @staticmethod
    def _window_id(row: dict[str, Any], fallback: int) -> int:
        value = row.get("window_id", fallback)
        try:
            return int(float(value))
        except (TypeError, ValueError) as exc:
            raise ValueError(f"invalid window_id {value!r}") from exc

    @staticmethod
    def _float_field(row: dict[str, Any], field_name: str) -> float:
        try:
            return float(row.get(field_name, 0.0))
        except (TypeError, ValueError) as exc:
            raise ValueError(f"invalid numeric field {field_name}={row.get(field_name)!r}") from exc
