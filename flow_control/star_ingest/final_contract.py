"""Validation for the week-4 final STAR naming contract.

This validator deliberately has no compatibility auto-renaming.  A caller that
needs to import a historical case must run an explicit migration and retain its
mapping/audit record instead of silently changing physical meaning.
"""

from __future__ import annotations

import re
import warnings
from collections.abc import Iterable


LEGACY_REPORT_COLUMNS = {
    "Fz_Total": "historical JET01..JET24 + tail +Z force; not final vehicle_lift",
    "Jet_Reaction_Z": "historical J01..J24 +Z surface force; not jet_momentum_reaction_z",
}
_LEGACY_ACTION = re.compile(r"^(?:JET_\d{1,2}|cmd_massflow_\d{1,2}|actual_massflow_\d{1,2})$", re.I)
_FINAL_ACTION = re.compile(r"^J(0[1-9]|1\d|2[0-4])_(?:switch|cmd_massflow_kg_s|actual_massflow_kg_s)$")


def validate_final_contract_columns(
    columns: Iterable[str], *, table_kind: str, strict: bool = True
) -> list[str]:
    """Return contract diagnostics, warning/erroring rather than guessing.

    ``table_kind`` is ``"actuation"`` or ``"timeseries"``.  In strict final
    mode a legacy/ambiguous field raises ``ValueError`` after emitting a
    ``DeprecationWarning`` so both batch logs and interactive users see it.
    """
    if table_kind not in {"actuation", "timeseries"}:
        raise ValueError("table_kind must be 'actuation' or 'timeseries'")

    diagnostics: list[str] = []
    for raw_column in columns:
        column = str(raw_column).strip()
        if column in LEGACY_REPORT_COLUMNS:
            diagnostics.append(f"legacy column {column}: {LEGACY_REPORT_COLUMNS[column]}")
        elif table_kind == "actuation" and _LEGACY_ACTION.fullmatch(column):
            diagnostics.append(
                f"ambiguous legacy action column {column}: use JNN_switch or "
                "JNN_cmd_massflow_kg_s / JNN_actual_massflow_kg_s"
            )
        elif table_kind == "actuation" and column.startswith("J") and not _FINAL_ACTION.fullmatch(column):
            diagnostics.append(f"unmapped J action column {column}: exact final JNN field name required")

    if diagnostics:
        message = "Final STAR contract rejected input; " + "; ".join(diagnostics)
        warnings.warn(message, DeprecationWarning, stacklevel=2)
        if strict:
            raise ValueError(message)
    return diagnostics
