"""Quality checks for STAR-exported case data.

All checks are designed as methods on ``QualityChecker`` so they can be used
standalone or composed by :func:`case_data_loader.load_case`.

Acceptance criteria
===================
1. Missing required columns → **ERROR**
2. ``physical_time`` not monotonically increasing → **ERROR**
3. NaN values in any numeric column → **ERROR**
4. Missing units or sign-direction documentation → **WARNING**
5. Jet on/off (JET_NN) does not match ``cmd_massflow_NN`` → **ERROR**
6. ``cmd_massflow_NN`` and ``actual_massflow_NN`` are NOT separate → **ERROR**
7. No-jet case: ``Jet_Reaction_Z`` = 0 is NOT treated as data loss → **WARNING**
"""

from __future__ import annotations

import math
import re
import warnings as _warnings
from typing import Any

# Standard column set
REQUIRED_BASE_COLUMNS = (
    "physical_time",
    "Fz_S1L", "Fz_S1R",
    "Fz_S2L", "Fz_S2R",
    "Fz_S3L", "Fz_S3R",
    "Fz_Total",
    "Drag_Total",
    "Pitch_Moment",
    "Roll_Moment",
    "Jet_Reaction_Z",
)

JET_COLUMNS = tuple(f"JET_{idx:02d}" for idx in range(1, 25))
CMD_MASSFLOW_COLUMNS = tuple(f"cmd_massflow_{idx:02d}" for idx in range(1, 25))
ACTUAL_MASSFLOW_COLUMNS = tuple(f"actual_massflow_{idx:02d}" for idx in range(1, 25))


class QualityChecker:
    """Collection of static quality-check methods for case data."""

    # ── Helpers ───────────────────────────────────────────────────────

    @staticmethod
    def _get_column(rows: list[dict[str, Any]], col: str) -> list[Any]:
        return [row.get(col) for row in rows]

    @staticmethod
    def _to_float(value: Any) -> float | None:
        if value is None:
            return None
        if isinstance(value, str):
            stripped = value.strip().lower()
            if stripped in {"nan", "inf", "-inf", ""}:
                return None
        try:
            v = float(value)
            if math.isnan(v) or math.isinf(v):
                return None
            return v
        except (ValueError, TypeError):
            return None

    @staticmethod
    def _is_nan(value: Any) -> bool:
        if value is None:
            return True
        if isinstance(value, str):
            return value.strip().lower() in {"nan", "inf", "-inf", "", "none"}
        if isinstance(value, float):
            return math.isnan(value) or math.isinf(value)
        return False

    # ── Check 1: Required columns ─────────────────────────────────────

    @staticmethod
    def check_required_columns(
        rows: list[dict[str, Any]],
        required: tuple[str, ...],
    ) -> list[str]:
        """Return ERROR for every missing required column."""
        if not rows:
            return ["timeseries is empty — cannot check columns"]

        present = set(rows[0].keys())
        missing = [col for col in required if col not in present]
        return [
            f"Missing required column: {col}" for col in missing
        ]

    # ── Check 2: Monotonic time ───────────────────────────────────────

    @staticmethod
    def check_monotonic_time(rows: list[dict[str, Any]]) -> list[str]:
        """Return ERROR if ``physical_time`` is not strictly increasing.

        A small tolerance (1e-12) is allowed for floating-point drift.
        """
        errors: list[str] = []
        if not rows:
            return errors

        times = []
        for row in rows:
            t = QualityChecker._to_float(row.get("physical_time"))
            if t is None:
                errors.append(
                    "physical_time is missing or NaN — cannot check monotonicity"
                )
                return errors
            times.append(t)

        for i in range(1, len(times)):
            if times[i] < times[i - 1] - 1e-12:
                errors.append(
                    f"physical_time not monotonically increasing at row {i}: "
                    f"{times[i-1]} → {times[i]} (diff = {times[i] - times[i-1]})"
                )
            elif times[i] == times[i - 1]:
                errors.append(
                    f"physical_time has duplicate value at row {i}: {times[i]}"
                )

        return errors

    # ── Check 3: NaN values ───────────────────────────────────────────

    @staticmethod
    def check_nan_values(rows: list[dict[str, Any]]) -> list[str]:
        """Return ERROR for every cell that is NaN, None, or infinite."""
        errors: list[str] = []
        if not rows:
            return errors

        for row_idx, row in enumerate(rows):
            for col, value in row.items():
                if QualityChecker._is_nan(value):
                    errors.append(
                        f"NaN/missing value at row {row_idx}, column '{col}'"
                    )
        return errors

    # ── Check 4: Units and sign direction ─────────────────────────────

    @staticmethod
    def check_units_and_direction(manifest: dict[str, Any]) -> list[str]:
        """Return WARNING if units or sign-direction are not documented.

        Looks for manifest keys like ``units``, ``sign_convention``,
        ``force_units``, ``moment_units``, or any description of
        positive-force direction.
        """
        warnings: list[str] = []
        unit_keys = {"units", "force_units", "moment_units", "massflow_units"}
        direction_keys = {"sign_convention", "positive_direction", "direction"}

        found_unit_info = any(
            key in manifest for key in unit_keys
        ) or "N" in str(manifest.get("notes", ""))

        found_direction_info = any(
            key in manifest for key in direction_keys
        ) or re.search(
            r"正方向|positive|direction|sign", str(manifest), re.IGNORECASE
        )

        if not found_unit_info:
            warnings.append(
                "Units not documented in manifest — "
                "add 'units' or 'force_units' field (e.g. 'N' for force, 'Nm' for moment)"
            )

        if not found_direction_info:
            warnings.append(
                "Sign/direction convention not documented in manifest — "
                "add 'sign_convention' field describing positive force direction "
                "(e.g. 'positive Fz = lift upward')"
            )

        return warnings

    # ── Check 5: Jet on/off vs massflow consistency ───────────────────

    @staticmethod
    def check_jet_massflow_consistency(rows: list[dict[str, Any]]) -> list[str]:
        """Return ERROR when JET_NN state contradicts ``cmd_massflow_NN``.

        For each row and each jet::

            JET_NN == 0  and  cmd_massflow_NN > 0  →  inconsistent
            JET_NN == 1  and  cmd_massflow_NN == 0 →  inconsistent
        """
        errors: list[str] = []
        if not rows:
            return errors

        has_cmd = all(f"cmd_massflow_{idx:02d}" in rows[0] for idx in range(1, 25))
        if not has_cmd:
            return errors  # skip if no cmd_massflow columns

        for row_idx, row in enumerate(rows):
            for idx in range(1, 25):
                jet_col = f"JET_{idx:02d}"
                cmd_col = f"cmd_massflow_{idx:02d}"

                jet_val = QualityChecker._to_float(row.get(jet_col))
                cmd_val = QualityChecker._to_float(row.get(cmd_col))

                if jet_val is None or cmd_val is None:
                    continue

                jet_on = abs(jet_val) > 0.5  # treat >0.5 as "on"
                cmd_nonzero = abs(cmd_val) > 1e-12

                if jet_on and not cmd_nonzero:
                    errors.append(
                        f"Row {row_idx}: {jet_col}={jet_val} (ON) but "
                        f"{cmd_col}={cmd_val} (zero massflow) — inconsistent"
                    )
                if not jet_on and cmd_nonzero:
                    errors.append(
                        f"Row {row_idx}: {jet_col}={jet_val} (OFF) but "
                        f"{cmd_col}={cmd_val} (nonzero massflow) — inconsistent"
                    )

        return errors

    # ── Check 6: cmd vs actual massflow separation ────────────────────

    @staticmethod
    def check_massflow_separation(
        rows: list[dict[str, Any]],
        *,
        allow_identical_actual: bool = False,
    ) -> list[str]:
        """Return ERROR if ``cmd_massflow_NN`` and ``actual_massflow_NN``
        are not stored as separate columns.

        When both exist, also warn if they are identical to machine precision
        (which would indicate the actual was never recorded separately).
        """
        errors: list[str] = []
        if not rows:
            return errors

        first_row = rows[0]
        cmd_cols = [col for col in first_row if col.startswith("cmd_massflow")]
        actual_cols = [col for col in first_row if col.startswith("actual_massflow")]

        if not cmd_cols and not actual_cols:
            return errors  # no massflow data at all — skip

        if cmd_cols and not actual_cols:
            errors.append(
                "cmd_massflow columns found but actual_massflow columns missing — "
                "they must be stored separately"
            )
            return errors

        if actual_cols and not cmd_cols:
            errors.append(
                "actual_massflow columns found but cmd_massflow columns missing — "
                "they must be stored separately"
            )
            return errors

        # Check that cmd and actual are NOT identical within tolerance
        identical_count = 0
        for row_idx, row in enumerate(rows):
            for idx in range(1, 25):
                cmd_col = f"cmd_massflow_{idx:02d}"
                actual_col = f"actual_massflow_{idx:02d}"
                if cmd_col in row and actual_col in row:
                    cmd_v = QualityChecker._to_float(row[cmd_col])
                    actual_v = QualityChecker._to_float(row[actual_col])
                    if cmd_v is not None and actual_v is not None:
                        if abs(cmd_v - actual_v) < 1e-12:
                            identical_count += 1

        if (
            identical_count > 0
            and identical_count == len(rows) * 24
            and not allow_identical_actual
        ):
            _warnings.warn(
                "cmd_massflow and actual_massflow are identical for all rows — "
                "actual massflow may not have been recorded separately"
            )

        return errors

    # ── Check 7: No-jet case Jet_Reaction_Z ───────────────────────────

    @staticmethod
    def check_no_jet_jrz(rows: list[dict[str, Any]]) -> list[str]:
        """Return WARNING for no-jet case when ``Jet_Reaction_Z`` is 0.

        In a no-jet case (no JET_NN columns), ``Jet_Reaction_Z`` = 0 is
        expected (no reaction force).  This is NOT data loss — it is the
        correct physical result.  We warn only if the column is absent,
        which would be unusual.
        """
        warnings: list[str] = []
        if not rows:
            return warnings

        first_row = rows[0]
        if "Jet_Reaction_Z" not in first_row:
            warnings.append(
                "No-jet case with no Jet_Reaction_Z column — this is acceptable "
                "if no jet reaction data was exported. Add the column for completeness."
            )
        else:
            # Check that Jet_Reaction_Z is indeed 0 or near-zero
            values = [
                QualityChecker._to_float(row.get("Jet_Reaction_Z"))
                for row in rows
            ]
            values = [v for v in values if v is not None]
            if values:
                max_abs = max(abs(v) for v in values)
                if max_abs > 1e-6:
                    warnings.append(
                        f"No-jet case but Jet_Reaction_Z has non-zero values "
                        f"(max |val| = {max_abs:.6e}) — verify this is correct"
                    )
                else:
                    warnings.append(
                        "No-jet case confirmed: Jet_Reaction_Z ≈ 0.0 "
                        "(expected physical result — NOT data loss)"
                    )

        return warnings

    # ── Run all checks ────────────────────────────────────────────────

    @classmethod
    def run_all(
        cls,
        rows: list[dict[str, Any]],
        manifest: dict[str, Any],
        *,
        has_jet_data: bool | None = None,
        check_mode: str = "star_ingest",
    ) -> dict[str, list[str]]:
        """Run all applicable checks and return ``{"errors": [...], "warnings": [...]}``."""
        if has_jet_data is None:
            has_jet_data = any(col.startswith("JET_") for col in (rows[0] if rows else {}))

        errors: list[str] = []
        warnings: list[str] = []

        errors.extend(cls.check_required_columns(rows, REQUIRED_BASE_COLUMNS))
        errors.extend(cls.check_monotonic_time(rows))
        errors.extend(cls.check_nan_values(rows))
        warnings.extend(cls.check_units_and_direction(manifest))

        if has_jet_data:
            errors.extend(cls.check_jet_massflow_consistency(rows))
            errors.extend(
                cls.check_massflow_separation(
                    rows,
                    allow_identical_actual=check_mode in {"mock", "arx_use", "ccm"},
                )
            )
        else:
            warnings.extend(cls.check_no_jet_jrz(rows))

        return {"errors": errors, "warnings": warnings}
