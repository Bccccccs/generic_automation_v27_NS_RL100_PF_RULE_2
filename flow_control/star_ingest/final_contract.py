"""Validation for the B01 contract based on the 0816 STAR case exports."""

from __future__ import annotations

import re
import warnings
from collections.abc import Iterable


_ACTION_COLUMN = re.compile(
    r"^(?:JET|cmd_massflow|actual_massflow)_(0[1-9]|1\d|2[0-4])$"
)
_STAR_UNDERSCORELESS_JET = re.compile(r"^JET(0[1-9]|1\d|2[0-4])$")


def validate_final_contract_columns(
    columns: Iterable[str], *, table_kind: str, strict: bool = True
) -> list[str]:
    """Validate names without renaming actual 0816 fields.

    ``Fz_Total`` and ``Jet_Reaction_Z`` are valid current fields.  Their
    physical meanings are documented in B01_report_mapping.csv; this function
    only rejects the dangerous mix-up where a STAR underbody zone ``JETNN`` is
    supplied as an action-table column.
    """
    if table_kind not in {"actuation", "timeseries"}:
        raise ValueError("table_kind must be 'actuation' or 'timeseries'")

    diagnostics: list[str] = []
    for raw_column in columns:
        column = str(raw_column).strip()
        if table_kind == "actuation" and _STAR_UNDERSCORELESS_JET.fullmatch(column):
            diagnostics.append(
                f"STAR underbody zone {column} cannot be used as an action column; "
                f"use JET_{column[-2:]} for the action that maps to J{column[-2:]}"
            )
        elif table_kind == "actuation" and column.startswith(("JET_", "cmd_massflow_", "actual_massflow_")) and not _ACTION_COLUMN.fullmatch(column):
            diagnostics.append(f"invalid 0816 action column {column}: expected a numbered 01..24 field")

    if diagnostics:
        message = "0816 STAR field contract rejected input; " + "; ".join(diagnostics)
        warnings.warn(message, UserWarning, stacklevel=2)
        if strict:
            raise ValueError(message)
    return diagnostics
