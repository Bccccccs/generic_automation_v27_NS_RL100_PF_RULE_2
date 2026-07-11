"""Read raw STAR-CCM+ export CSVs and normalize to standard timeseries format.

STAR-CCM+ exports force/moment monitors as CSV files with descriptive
column names that include Chinese labels, monitor names, and units.
This module maps those names to the standard column vocabulary used
by the flow-control pipeline and computes derived quantities.

Typical STAR export columns (from FZ.csv)::

    "时间","S1L Monitor: S1L Monitor (N)","S1R Monitor: S1R Monitor (N)", ...

Standard output columns may include any subset of::

    physical_time, Fz_S1L, Fz_S1R, Fz_S2L, Fz_S2R, Fz_S3L, Fz_S3R,
    Fz_Total, Drag_Total, Pitch_Moment, Roll_Moment, Jet_Reaction_Z,
    JET_01 … JET_24, cmd_massflow_01 … cmd_massflow_24,
    actual_massflow_01 … actual_massflow_24

The reader preserves the columns actually exported by STAR-CCM+.  It only
computes quantities that can be derived directly from present columns, such as
``Fz_Total`` from the six bottom-force sensors.  It does not pad missing
physical quantities with placeholder zeroes.
"""

from __future__ import annotations

import csv
import re
from pathlib import Path
from typing import Any

# ── STAR export column-name patterns ──────────────────────────────────────────

# Maps standard column names to regex patterns that match STAR-CCM+ export headers.
# The STAR column format is typically:  "<MonitorName>: <Description> (<Unit>)"
STAR_COLUMN_PATTERNS: dict[str, re.Pattern] = {
    "physical_time": re.compile(r"时间|time|physical.?time|Time", re.IGNORECASE),
    # Keep the STAR total-force monitor separate from the six local sensors.
    "Fz_Total": re.compile(r"(?:^|[\s:\"])Fz\s*Monitor", re.IGNORECASE),
    "Fz_S1L": re.compile(r"S1L.*Monitor", re.IGNORECASE),
    "Fz_S1R": re.compile(r"S1R.*Monitor", re.IGNORECASE),
    "Fz_S2L": re.compile(r"S2L.*Monitor", re.IGNORECASE),
    "Fz_S2R": re.compile(r"S2R.*Monitor", re.IGNORECASE),
    "Fz_S3L": re.compile(r"S3L.*Monitor", re.IGNORECASE),
    "Fz_S3R": re.compile(r"S3R.*Monitor", re.IGNORECASE),
    "Drag_Total": re.compile(r"drag|阻力", re.IGNORECASE),
    "Pitch_Moment": re.compile(r"pitch|俯仰", re.IGNORECASE),
    "Roll_Moment": re.compile(r"roll|滚转", re.IGNORECASE),
    "Jet_Reaction_Z": re.compile(r"jet.*reaction|喷气.*反力|reaction.*z", re.IGNORECASE),
}

JET_COLUMN_PATTERN = re.compile(r"JET[_\s]?(\d{1,2})", re.IGNORECASE)
MASSFLOW_CMD_PATTERN = re.compile(r"cmd.*mass.?flow[_\s]?(\d{1,2})", re.IGNORECASE)
MASSFLOW_ACTUAL_PATTERN = re.compile(r"(?:actual|real).*mass.?flow[_\s]?(\d{1,2})", re.IGNORECASE)

# Standard column names
FZ_SENSOR_COLUMNS = ("Fz_S1L", "Fz_S1R", "Fz_S2L", "Fz_S2R", "Fz_S3L", "Fz_S3R")
GLOBAL_COLUMNS = ("Fz_Total", "Drag_Total", "Pitch_Moment", "Roll_Moment", "Jet_Reaction_Z")
STANDARD_LOAD_COLUMNS = (*FZ_SENSOR_COLUMNS, *GLOBAL_COLUMNS)

JET_COLUMNS = tuple(f"JET_{idx:02d}" for idx in range(1, 25))
CMD_MASSFLOW_COLUMNS = tuple(f"cmd_massflow_{idx:02d}" for idx in range(1, 25))
ACTUAL_MASSFLOW_COLUMNS = tuple(f"actual_massflow_{idx:02d}" for idx in range(1, 25))

IGNORED_PRODUCT_NAME_PATTERNS = (
    "报告",
    "pressure",
)


def discover_star_export_csvs(product_dir: str | Path) -> list[Path]:
    """Return STAR monitor CSVs in a product directory that map to timeseries columns.

    STAR result folders may contain plotting/report CSVs that are useful later
    but do not currently belong in the unified force/moment ``timeseries.csv``.
    This helper keeps files with at least one recognized data column in addition
    to ``physical_time`` and skips known report/pressure exports for now.
    """
    root = Path(product_dir)
    if not root.is_dir():
        raise NotADirectoryError(f"STAR product directory not found: {root}")

    selected: list[Path] = []
    for path in sorted(root.glob("*.csv")):
        if any(pattern.lower() in path.name.lower() for pattern in IGNORED_PRODUCT_NAME_PATTERNS):
            continue
        try:
            with path.open("r", encoding="utf-8-sig", newline="") as handle:
                headers = next(csv.reader(handle))
            mapping = detect_star_column_mapping(headers)
        except (StopIteration, ValueError):
            continue
        if any(column != "physical_time" for column in mapping):
            selected.append(path)
    return selected


def detect_star_column_mapping(headers: list[str]) -> dict[str, str]:
    """Match STAR-CCM+ export header names to standard column names.

    Returns a dict of ``{standard_name: star_header_name}``.

    Raises ``ValueError`` if a required column cannot be matched.
    """
    mapping: dict[str, str] = {}
    unknown: list[str] = []

    for header in headers:
        header_stripped = header.strip().strip('"')
        matched = False

        # Try standard patterns first
        for standard_name, pattern in STAR_COLUMN_PATTERNS.items():
            if pattern.search(header_stripped):
                mapping[standard_name] = header
                matched = True
                break

        if matched:
            continue

        # Try jet on/off columns (JET_01 … JET_24)
        jet_match = JET_COLUMN_PATTERN.match(header_stripped)
        if jet_match:
            idx = int(jet_match.group(1))
            if 1 <= idx <= 24:
                mapping[f"JET_{idx:02d}"] = header
                continue

        # Try cmd_massflow columns
        cmd_match = MASSFLOW_CMD_PATTERN.match(header_stripped)
        if cmd_match:
            idx = int(cmd_match.group(1))
            if 1 <= idx <= 24:
                mapping[f"cmd_massflow_{idx:02d}"] = header
                continue

        # Try actual_massflow columns
        actual_match = MASSFLOW_ACTUAL_PATTERN.match(header_stripped)
        if actual_match:
            idx = int(actual_match.group(1))
            if 1 <= idx <= 24:
                mapping[f"actual_massflow_{idx:02d}"] = header
                continue

        unknown.append(header_stripped)

    if "physical_time" not in mapping:
        raise ValueError(
            f"Could not find physical_time column in STAR export headers. "
            f"Looked for patterns matching '时间', 'time', 'physical_time'. "
            f"Available headers: {headers[:10]}"
        )

    return mapping


def _is_float(value: str) -> bool:
    try:
        float(value)
        return True
    except (ValueError, TypeError):
        return False


def read_star_export_csv(path: str | Path) -> dict[str, Any]:
    """Read a STAR-CCM+ export CSV and return normalized data.

    Returns
    -------
    dict with keys:
        - ``columns``: list of standard column names
        - ``rows``: list of dicts mapping standard names to float values
        - ``units``: dict mapping standard column names to detected units
        - ``mapping``: the raw header→standard mapping used
        - ``source_files``: list of source file paths
    """
    path = Path(path)
    if not path.exists():
        raise FileNotFoundError(f"STAR export CSV not found: {path}")

    # utf-8-sig transparently removes the BOM produced by STAR's
    # "Excel compatible" export while also accepting ordinary UTF-8 files.
    with path.open("r", encoding="utf-8-sig", newline="") as f:
        reader = csv.reader(f)
        raw_headers = next(reader)

    mapping = detect_star_column_mapping(raw_headers)

    # Detect units from column headers
    units: dict[str, str] = {}
    for standard_name, star_header in mapping.items():
        unit_match = re.search(r"\(([^)]+)\)", star_header)
        if unit_match:
            units[standard_name] = unit_match.group(1)

    # Read all data rows
    with path.open("r", encoding="utf-8-sig", newline="") as f:
        reader = csv.DictReader(f)
        rows: list[dict[str, Any]] = []
        for row in reader:
            normalized: dict[str, Any] = {}
            for standard_name, star_header in mapping.items():
                raw = row.get(star_header, "").strip().strip('"')
                # Try numeric conversion
                if _is_float(raw):
                    normalized[standard_name] = float(raw)
                else:
                    normalized[standard_name] = raw  # keep as string (e.g. "NaN", "success")
            rows.append(normalized)

    # Build column order: physical_time, Fz sensors, globals, jets, massflows
    present_columns = list(mapping.keys())
    ordered = _order_columns(present_columns)

    return {
        "columns": ordered,
        "rows": rows,
        "units": units,
        "mapping": mapping,
        "source_files": [str(path)],
    }


def read_star_export_bundle(file_paths: list[str | Path]) -> dict[str, Any]:
    """Read multiple STAR export CSVs and merge them into one timeseries.

    All files are merged on ``physical_time`` (outer join).  This is useful
    when Fz, drag, moments, and jet data are exported to separate files.
    """
    datasets = [read_star_export_csv(p) for p in file_paths]

    if not datasets:
        raise ValueError("at least one STAR export file is required")

    # Collect all columns
    all_columns: list[str] = []
    seen: set[str] = set()
    for ds in datasets:
        for col in ds["columns"]:
            if col not in seen:
                all_columns.append(col)
                seen.add(col)

    ordered_cols = _order_columns(all_columns)

    # Merge rows by physical_time.  STAR exports from separate monitors may
    # differ by tiny floating-point formatting noise, so use a rounded key.
    merged: dict[float, dict[str, Any]] = {}
    for ds in datasets:
        for row in ds["rows"]:
            t = row.get("physical_time")
            if t is None:
                continue
            if isinstance(t, str):
                try:
                    t = float(t)
                except (ValueError, TypeError):
                    continue
            key = round(float(t), 12)
            if key not in merged:
                merged[key] = {"physical_time": float(t)}
            merged[key].update(row)

    merged_rows = [merged[t] for t in sorted(merged)]

    # Merge units and mappings
    all_units: dict[str, str] = {}
    all_mappings: dict[str, str] = {}
    all_sources: list[str] = []
    for ds in datasets:
        all_units.update(ds["units"])
        all_mappings.update(ds["mapping"])
        all_sources.extend(ds["source_files"])

    return {
        "columns": ordered_cols,
        "rows": merged_rows,
        "units": all_units,
        "mapping": all_mappings,
        "source_files": all_sources,
    }


def compute_fz_total(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Compute ``Fz_Total`` from individual sensor columns if absent.

    Modifies rows in place and returns them.
    ``Fz_Total = Fz_S1L + Fz_S1R + Fz_S2L + Fz_S2R + Fz_S3L + Fz_S3R``.
    The total is computed only when all six sensor values are present.
    """
    for row in rows:
        if "Fz_Total" in row and row["Fz_Total"] is not None:
            continue  # already present
        values: list[float] = []
        for col in FZ_SENSOR_COLUMNS:
            v = row.get(col)
            if isinstance(v, (int, float)) and not _is_nan_like(v):
                values.append(v)
        if len(values) == len(FZ_SENSOR_COLUMNS):
            row["Fz_Total"] = sum(values)
    return rows


def ensure_standard_columns(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Deprecated compatibility shim.

    Missing physical quantities must remain missing so the quality checker can
    report them.  This function intentionally leaves rows unchanged.
    """
    return rows


def _is_nan_like(value: Any) -> bool:
    import math
    if value is None:
        return True
    if isinstance(value, str):
        return value.strip().lower() in {"nan", "inf", "-inf", ""}
    if isinstance(value, float):
        return math.isnan(value) or math.isinf(value)
    return False


def _order_columns(present: list[str]) -> list[str]:
    """Return columns in standard order, preserving extras at the end."""
    priority = ("physical_time", *FZ_SENSOR_COLUMNS, *GLOBAL_COLUMNS,
                *JET_COLUMNS, *CMD_MASSFLOW_COLUMNS, *ACTUAL_MASSFLOW_COLUMNS)
    ordered = [c for c in priority if c in present]
    extras = [c for c in present if c not in priority]
    return ordered + extras
