"""Map STAR-CCM+ report values into the shared flow-control timeseries schema."""

from __future__ import annotations

from typing import Any, Mapping

from .control_spec import (
    DEFAULT_STARCCM_SPEC,
    GLOBAL_OUTPUT_COLUMNS,
    JET_COLUMNS,
    LOAD_COLUMNS,
    StarCCMControlSpec,
)


class StarCCMResultMapper:
    """Convert raw STAR-CCM+ report values to standardized timeseries rows."""

    def __init__(self, spec: StarCCMControlSpec = DEFAULT_STARCCM_SPEC) -> None:
        self.spec = spec.require_valid()

    def map_row(
        self,
        report_values: Mapping[str, Any],
        jet_commands: Mapping[str, Any],
        *,
        physical_time: float,
        window_id: int,
        solver_status: str = "success",
    ) -> dict[str, Any]:
        row: dict[str, Any] = {
            "physical_time": float(physical_time),
            "window_id": int(window_id),
        }
        for jet in self.spec.jets:
            row[jet.column] = float(jet_commands.get(jet.column, 0.0))

        for point in self.spec.load_points:
            row[point.column] = self._read_float(
                report_values,
                point.column,
                point.report_name,
            )

        row["Fz_Total"] = self._read_float(
            report_values,
            "Fz_Total",
            "total_fz",
            default=sum(float(row[column]) for column in LOAD_COLUMNS),
        )
        row["Drag_Total"] = self._read_float(
            report_values,
            "Drag_Total",
            "drag",
            "drag_total",
            default=0.0,
        )
        row["Pitch_Moment"] = self._read_float(
            report_values,
            "Pitch_Moment",
            "pitch_moment",
            default=self._pitch_from_loads(row),
        )
        row["Roll_Moment"] = self._read_float(
            report_values,
            "Roll_Moment",
            "roll_moment",
            default=self._roll_from_loads(row),
        )
        row["Jet_Reaction_Z"] = self._read_float(
            report_values,
            "Jet_Reaction_Z",
            "jet_reaction_z",
            default=sum(float(row[column]) for column in JET_COLUMNS),
        )
        row["solver_status"] = str(solver_status)
        return row

    def required_columns(self) -> tuple[str, ...]:
        return ("physical_time", "window_id", *JET_COLUMNS, *LOAD_COLUMNS, *GLOBAL_OUTPUT_COLUMNS)

    @staticmethod
    def _read_float(
        values: Mapping[str, Any],
        *names: str,
        default: float | None = None,
    ) -> float:
        for name in names:
            if name in values and values[name] not in {None, ""}:
                return float(values[name])
        if default is not None:
            return float(default)
        raise KeyError(f"missing STAR-CCM+ report value for any of: {', '.join(names)}")

    @staticmethod
    def _pitch_from_loads(row: Mapping[str, Any]) -> float:
        front_total = float(row["Fz_S1L"]) + float(row["Fz_S1R"])
        rear_total = float(row["Fz_S3L"]) + float(row["Fz_S3R"])
        return rear_total - front_total

    @staticmethod
    def _roll_from_loads(row: Mapping[str, Any]) -> float:
        left_total = float(row["Fz_S1L"]) + float(row["Fz_S2L"]) + float(row["Fz_S3L"])
        right_total = float(row["Fz_S1R"]) + float(row["Fz_S2R"]) + float(row["Fz_S3R"])
        return right_total - left_total
