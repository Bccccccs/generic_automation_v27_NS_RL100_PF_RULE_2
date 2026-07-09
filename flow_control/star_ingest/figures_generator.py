"""Auto-generate diagnostic figures for STAR-exported case data.

Generated figures
=================
- ``force_timeseries.png`` — Fz sensor and total force vs time
- ``jet_schedule.png``    — Jet activation states over time (jet cases only)
- ``massflow_check.png``  — cmd vs actual massflow comparison (jet cases only)
- ``quality_summary.png`` — Summary dashboard card with check results
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import matplotlib.ticker as ticker
import numpy as np


def _to_float_array(rows: list[dict[str, Any]], col: str) -> np.ndarray:
    """Extract a column of floats, replacing missing/NaN with 0."""
    values = []
    for row in rows:
        v = row.get(col)
        if v is None:
            values.append(0.0)
        elif isinstance(v, str):
            try:
                values.append(float(v))
            except (ValueError, TypeError):
                values.append(0.0)
        elif isinstance(v, float) and (np.isnan(v) or np.isinf(v)):
            values.append(0.0)
        else:
            values.append(float(v))
    return np.array(values)


def _time_array(rows: list[dict[str, Any]]) -> np.ndarray:
    return _to_float_array(rows, "physical_time")


def generate_force_timeseries(
    rows: list[dict[str, Any]],
    output_path: str | Path,
) -> Path:
    """Plot Fz sensor forces and total force vs time."""
    t = _time_array(rows)

    n_sensors = 6
    sensor_cols = ["Fz_S1L", "Fz_S1R", "Fz_S2L", "Fz_S2R", "Fz_S3L", "Fz_S3R"]
    sensor_labels = ["S1L", "S1R", "S2L", "S2R", "S3L", "S3R"]
    colors = ["#1f77b4", "#aec7e8", "#ff7f0e", "#ffbb78", "#2ca02c", "#98df8a"]

    fig, axes = plt.subplots(2, 1, figsize=(12, 8), sharex=True)

    # Top: individual sensors
    ax = axes[0]
    for i in range(n_sensors):
        if sensor_cols[i] in rows[0]:
            ax.plot(t, _to_float_array(rows, sensor_cols[i]),
                    label=sensor_labels[i], color=colors[i], linewidth=0.5)
    ax.set_ylabel("Fz (N)")
    ax.set_title("Fz Sensor Forces vs Time")
    ax.legend(fontsize=8, ncol=3)
    ax.grid(True, alpha=0.3)

    # Bottom: Fz_Total if available
    ax = axes[1]
    if "Fz_Total" in rows[0]:
        ax.plot(t, _to_float_array(rows, "Fz_Total"),
                label="Fz_Total", color="black", linewidth=1.0)
    if "Drag_Total" in rows[0]:
        ax.plot(t, _to_float_array(rows, "Drag_Total"),
                label="Drag_Total", color="red", linewidth=0.7, alpha=0.7)
    if "Pitch_Moment" in rows[0]:
        ax.plot(t, _to_float_array(rows, "Pitch_Moment"),
                label="Pitch_Moment", color="green", linewidth=0.7, alpha=0.7)
    if "Roll_Moment" in rows[0]:
        ax.plot(t, _to_float_array(rows, "Roll_Moment"),
                label="Roll_Moment", color="purple", linewidth=0.7, alpha=0.7)
    ax.set_xlabel("Time (s)")
    ax.set_ylabel("Force / Moment")
    ax.set_title("Global Quantities vs Time")
    ax.legend(fontsize=8)
    ax.grid(True, alpha=0.3)

    plt.tight_layout()
    output = Path(output_path)
    fig.savefig(output, dpi=150, bbox_inches="tight")
    plt.close(fig)
    print(f"  Saved: {output}")
    return output


def generate_jet_schedule(
    rows: list[dict[str, Any]],
    output_path: str | Path,
    *,
    case_has_jet: bool | None = None,
) -> Path | None:
    """Plot jet activation states as a binary heatmap.

    If no jet columns were exported, an explicit "unavailable" figure is
    generated.  This keeps every case package structurally complete without
    inventing jet states.
    """
    jet_cols = [col for col in (rows[0] if rows else {}) if col.startswith("JET_")]
    if not jet_cols:
        message = (
            "Not applicable: this case is declared as no_jet."
            if case_has_jet is False
            else "Unavailable: STAR export contains no JET_01 ... JET_24 columns."
        )
        return _generate_unavailable_figure(
            output_path,
            "Jet Activation Schedule",
            message,
        )

    t = _time_array(rows)
    jet_matrix = np.zeros((len(jet_cols), len(rows)))
    for j_idx, col in enumerate(jet_cols):
        jet_matrix[j_idx, :] = _to_float_array(rows, col)

    fig, ax = plt.subplots(figsize=(12, 6))
    im = ax.imshow(jet_matrix, aspect="auto", cmap="RdYlGn",
                   interpolation="nearest", extent=[t[0], t[-1],
                                                     len(jet_cols) - 0.5, -0.5])

    ax.set_xlabel("Time (s)")
    ax.set_ylabel("Jet ID")
    ax.set_title("Jet Activation Schedule")
    ax.set_yticks(range(len(jet_cols)))
    ax.set_yticklabels(jet_cols, fontsize=7)
    cbar = fig.colorbar(im, ax=ax, ticks=[0, 1], shrink=0.8)
    cbar.set_label("On (1) / Off (0)")

    plt.tight_layout()
    output = Path(output_path)
    fig.savefig(output, dpi=150, bbox_inches="tight")
    plt.close(fig)
    print(f"  Saved: {output}")
    return output


def generate_massflow_check(
    rows: list[dict[str, Any]],
    output_path: str | Path,
    *,
    case_has_jet: bool | None = None,
) -> Path | None:
    """Compare cmd_massflow vs actual_massflow for the first few active jets.

    If no massflow columns were exported, an explicit "unavailable" figure is
    generated rather than silently substituting zero flow.
    """
    cmd_cols = [col for col in (rows[0] if rows else {})
                if col.startswith("cmd_massflow")]
    actual_cols = [col for col in (rows[0] if rows else {})
                   if col.startswith("actual_massflow")]

    if not cmd_cols and not actual_cols:
        message = (
            "Not applicable: this case is declared as no_jet."
            if case_has_jet is False
            else
            "Unavailable: cmd_massflow_01 ... 24 and actual_massflow_01 ... 24\n"
            "were not present in the STAR exports."
        )
        return _generate_unavailable_figure(
            output_path,
            "Commanded vs Actual Mass Flow",
            message,
        )

    t = _time_array(rows)
    n_jets_to_plot = min(6, max(len(cmd_cols), len(actual_cols), 1))
    fig, axes = plt.subplots(n_jets_to_plot, 1, figsize=(10, 2.5 * n_jets_to_plot),
                             sharex=True)

    if n_jets_to_plot == 1:
        axes = [axes]

    for i in range(n_jets_to_plot):
        ax = axes[i]
        idx = i + 1
        cmd_col = f"cmd_massflow_{idx:02d}"
        actual_col = f"actual_massflow_{idx:02d}"

        if cmd_col in rows[0]:
            ax.plot(t, _to_float_array(rows, cmd_col),
                    label=f"cmd_massflow_{idx:02d}", color="blue", linewidth=0.7)
        if actual_col in rows[0]:
            ax.plot(t, _to_float_array(rows, actual_col),
                    label=f"actual_massflow_{idx:02d}", color="orange",
                    linewidth=0.7, linestyle="--")
        ax.set_ylabel("Massflow")
        ax.set_title(f"Jet {idx:02d} Massflow")
        ax.legend(fontsize=7)
        ax.grid(True, alpha=0.3)

    axes[-1].set_xlabel("Time (s)")
    plt.tight_layout()
    output = Path(output_path)
    fig.savefig(output, dpi=150, bbox_inches="tight")
    plt.close(fig)
    print(f"  Saved: {output}")
    return output


def _generate_unavailable_figure(
    output_path: str | Path,
    title: str,
    reason: str,
) -> Path:
    """Write a diagnostic placeholder without fabricating measurement data."""
    fig, ax = plt.subplots(figsize=(10, 4))
    ax.axis("off")
    ax.text(0.5, 0.65, title, ha="center", va="center",
            fontsize=16, fontweight="bold", transform=ax.transAxes)
    ax.text(0.5, 0.42, reason, ha="center", va="center",
            fontsize=11, color="#9c2f2f", transform=ax.transAxes)
    ax.text(0.5, 0.2, "Export the missing channels from STAR-CCM+ and ingest again.",
            ha="center", va="center", fontsize=10, color="#555555",
            transform=ax.transAxes)
    output = Path(output_path)
    fig.savefig(output, dpi=150, bbox_inches="tight")
    plt.close(fig)
    print(f"  Saved: {output}")
    return output


def generate_quality_summary(
    result: dict[str, Any],
    output_path: str | Path,
) -> Path:
    """Generate a summary dashboard image with key metrics."""
    fig, ax = plt.subplots(figsize=(10, 6))
    ax.axis("off")

    errors = result.get("errors", [])
    warnings = result.get("warnings", [])
    n_rows = len(result.get("timeseries", []))
    has_jet = result.get("has_jet_data", False)

    lines = [
        f"Case: {result.get('case_id', 'unknown')}",
        f"Pass: {'YES' if not errors else 'NO'}",
        f"",
        f"Timeseries Rows: {n_rows}",
        f"Has Jet Data: {has_jet}",
        f"Errors:   {len(errors)}",
        f"Warnings: {len(warnings)}",
    ]

    if errors:
        lines.append("")
        lines.append("Errors:")
        for e in errors[:8]:
            lines.append(f"  ! {e}")
        if len(errors) > 8:
            lines.append(f"  ... and {len(errors) - 8} more")

    if warnings:
        lines.append("")
        lines.append("Warnings:")
        for w in warnings[:5]:
            lines.append(f"  ? {w}")
        if len(warnings) > 5:
            lines.append(f"  ... and {len(warnings) - 5} more")

    status_color = "green" if not errors else "red"
    ax.text(0.1, 0.95, "Quality Summary", fontsize=16, fontweight="bold",
            transform=ax.transAxes)
    ax.text(0.1, 0.88, f"Status: {'PASS' if not errors else 'FAIL'}",
            fontsize=14, color=status_color, fontweight="bold",
            transform=ax.transAxes)

    y_pos = 0.78
    for line in lines:
        if line.startswith("Errors:") and errors:
            ax.text(0.1, y_pos, line, fontsize=11, color="red",
                    transform=ax.transAxes)
        elif line.startswith("Warnings:") and warnings:
            ax.text(0.1, y_pos, line, fontsize=11, color="orange",
                    transform=ax.transAxes)
        elif line.startswith("Pass:"):
            pass  # already shown above
        else:
            ax.text(0.1, y_pos, line, fontsize=10, color="black",
                    transform=ax.transAxes)
        y_pos -= 0.035

    output = Path(output_path)
    fig.savefig(output, dpi=150, bbox_inches="tight")
    plt.close(fig)
    print(f"  Saved: {output}")
    return output


def generate_all_figures(
    result: dict[str, Any],
    output_dir: str | Path,
) -> dict[str, Path | None]:
    """Generate all diagnostic figures for a case.

    Parameters
    ----------
    result
        The dict returned by :func:`case_data_loader.load_case`.
    output_dir
        Directory where figures will be saved.

    Returns
    -------
    dict mapping figure name (without extension) to output ``Path`` or ``None``.
    """
    out = Path(output_dir)
    out.mkdir(parents=True, exist_ok=True)

    rows = result.get("timeseries", [])

    figs: dict[str, Path | None] = {}

    figs["force_timeseries"] = generate_force_timeseries(
        rows, out / "force_timeseries.png"
    )
    figs["jet_schedule"] = generate_jet_schedule(
        rows, out / "jet_schedule.png",
        case_has_jet=result.get("has_jet_data"),
    )
    figs["massflow_check"] = generate_massflow_check(
        rows, out / "massflow_check.png",
        case_has_jet=result.get("has_jet_data"),
    )
    figs["quality_summary"] = generate_quality_summary(
        result, out / "quality_summary.png"
    )

    return figs
