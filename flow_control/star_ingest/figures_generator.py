"""
Auto-generate diagnostic figures for STAR-exported case data.

自动为 STAR 导出的 Case 数据生成诊断图表。
生成的图表文件存储于 Case 的 figures/ 目录:

- ``force_timeseries.png`` — Fz 传感器力及总力随时间变化曲线
  包含每个传感器的 Fz 分量和总力/阻力/力矩等全局量。
- ``jet_schedule.png``    — 喷气阀门激活状态热力图(仅喷气工况)
- ``massflow_check_01_06.png`` ... ``massflow_check_19_24.png`` —
  指令质量流量与实际质量流量对比(仅喷气工况),覆盖全部 24 个阀门。
- ``quality_summary.png`` — 质量检查结果的摘要仪表板卡片
  显示 Pass/Fail 状态、错误数、警告数及具体内容。

设计原则:
- 数据缺失时不虚构数据,而是生成明确的"不可用"占位图
- 使用 matplotlib 的 Agg 后端(非交互式),适合批量/脚本化运行
- 所有数值替换为 0.0 时在图中隐式表明数据出现问题
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import matplotlib
# 使用 Agg 非交互式后端,适合无图形界面的服务端/CI 环境运行
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import matplotlib.ticker as ticker
import numpy as np

from flow_control.data_schema import initial_transient_crop_end_s


def _to_float_array(rows: list[dict[str, Any]], col: str) -> np.ndarray:
    """Extract a column of floats, replacing missing/NaN with 0.

    从数据行中提取指定列的数值,转换为 NumPy 数组。
    对于缺失值、NaN、无穷大等无效值统一替换为 0.0,
    避免在绘图时产生异常或空白。
    """
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
    """
    从数据行中提取 physical_time 列作为时间数组的快捷函数。
    """
    return _to_float_array(rows, "physical_time")


def _crop_initial_transient(
    rows: list[dict[str, Any]],
    manifest: dict[str, Any] | None,
) -> list[dict[str, Any]]:
    """裁掉 manifest 声明的初始瞬态段（统一契约：前 0.5 s）。

    仅保留 ``physical_time >= initial_transient_crop.end_time_s`` 的行。
    如果裁剪会删光全部数据（整段都落在瞬态窗内的短/合成序列），
    则原样返回，避免出图退化为无意义的"不可用"占位图。
    """
    if not rows:
        return rows
    crop_end_s = initial_transient_crop_end_s(manifest)
    times = _time_array(rows)
    cropped = [row for row, t in zip(rows, times) if t >= crop_end_s]
    return cropped or rows


def generate_force_timeseries(
    rows: list[dict[str, Any]],
    output_path: str | Path,
) -> Path:
    """Plot Fz sensor forces and total force vs time.

    生成力时间序列图,包含两张子图:
    - 上图:六个底部传感器(Fz_S1L~Fz_S3R)各自的力随时间变化
    - 下图:全局量(Fz_Total / Drag_Total / Pitch_Moment / Roll_Moment)

    每个传感器使用不同颜色以便区分。
    全局量中的 Fz_Total 使用黑色粗线突出显示,其他量使用半透明细线。
    """
    if not rows:
        return _generate_unavailable_figure(
            output_path,
            "Force Timeseries",
            "Unavailable: the standard case contains no timeseries rows.",
        )

    t = _time_array(rows)

    n_sensors = 6
    sensor_cols = ["Fz_S1L", "Fz_S1R", "Fz_S2L", "Fz_S2R", "Fz_S3L", "Fz_S3R"]
    sensor_labels = ["S1L", "S1R", "S2L", "S2R", "S3L", "S3R"]
    colors = ["#1f77b4", "#aec7e8", "#ff7f0e", "#ffbb78", "#2ca02c", "#98df8a"]

    fig, axes = plt.subplots(2, 1, figsize=(12, 8), sharex=True)

    # Top: individual sensors / 上图:单个传感器力
    ax = axes[0]
    for i in range(n_sensors):
        if sensor_cols[i] in rows[0]:
            ax.plot(t, _to_float_array(rows, sensor_cols[i]),
                    label=sensor_labels[i], color=colors[i], linewidth=0.5)
    ax.set_ylabel("Fz (N)")
    ax.set_title("Fz Sensor Forces vs Time")
    ax.legend(fontsize=8, ncol=3)
    ax.grid(True, alpha=0.3)

    # Bottom: Fz_Total if available / 下图:全局量(总力/阻力/力矩)
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

    生成喷气阀门激活状态热力图。
    横轴为时间,纵轴为 24 个喷气阀门。
    使用红色/绿色(红绿渐变)表示阀门关闭/打开:
    - 红色(0):阀门关闭
    - 绿色(1):阀门打开

    如果 Case 没有喷气数据(no_jet),则生成明确的"不可用"占位图,
    而不是虚构喷气状态数据。
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
    # 构建二维矩阵:行=阀门,列=时间点
    jet_matrix = np.zeros((len(jet_cols), len(rows)))
    for j_idx, col in enumerate(jet_cols):
        jet_matrix[j_idx, :] = _to_float_array(rows, col)

    x_min, x_max = (t[0], t[-1]) if len(t) > 1 else (t[0] - 0.5, t[0] + 0.5)
    fig, ax = plt.subplots(figsize=(12, 6))
    im = ax.imshow(jet_matrix, aspect="auto", cmap="RdYlGn",
                   interpolation="nearest", extent=[x_min, x_max,
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
) -> dict[str, Path | None]:
    """Compare cmd_massflow vs actual_massflow for all 24 jets.

    If no massflow columns were exported, an explicit "unavailable" figure is
    generated rather than silently substituting zero flow.

    生成指令质量流量与实际质量流量的对比图。
    生成 4 张图,每张显示 6 个阀门,覆盖 JET_01 到 JET_24 的
    cmd_massflow(蓝色实线)和 actual_massflow(橙色虚线)随时间的变化曲线。

    通过比较两者的差异可以评估控制回路的跟踪性能:
    - 两者重合:跟踪良好
    - 存在偏差:需要关注控制误差

    如果缺少质量流量数据,生成"不可用"占位图而非虚构零值。
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
        placeholder = _generate_unavailable_figure(
            output_path,
            "Commanded vs Actual Mass Flow",
            message,
        )
        return {"massflow_check": placeholder}

    t = _time_array(rows)
    output = Path(output_path)
    generated: dict[str, Path | None] = {}
    for start_idx in range(1, 25, 6):
        end_idx = start_idx + 5
        page_name = f"massflow_check_{start_idx:02d}_{end_idx:02d}"
        page_path = output.with_name(f"{page_name}{output.suffix or '.png'}")
        fig, axes = plt.subplots(6, 1, figsize=(10, 15), sharex=True)

        for axis_idx, idx in enumerate(range(start_idx, end_idx + 1)):
            ax = axes[axis_idx]
            cmd_col = f"cmd_massflow_{idx:02d}"
            actual_col = f"actual_massflow_{idx:02d}"

            plotted = False
            if cmd_col in rows[0]:
                ax.plot(t, _to_float_array(rows, cmd_col),
                        label=cmd_col, color="blue", linewidth=0.7)
                plotted = True
            if actual_col in rows[0]:
                ax.plot(t, _to_float_array(rows, actual_col),
                        label=actual_col, color="orange",
                        linewidth=0.7, linestyle="--")
                plotted = True
            if not plotted:
                ax.text(0.5, 0.5, "massflow columns unavailable",
                        ha="center", va="center", fontsize=9,
                        color="#9c2f2f", transform=ax.transAxes)
            ax.set_ylabel("Massflow")
            ax.set_title(f"Jet {idx:02d} Massflow")
            if plotted:
                ax.legend(fontsize=7)
            ax.grid(True, alpha=0.3)

        axes[-1].set_xlabel("Time (s)")
        fig.suptitle(f"Commanded vs Actual Mass Flow: JET_{start_idx:02d}-JET_{end_idx:02d}")
        plt.tight_layout(rect=[0, 0.02, 1, 0.98])
        fig.savefig(page_path, dpi=150, bbox_inches="tight")
        plt.close(fig)
        print(f"  Saved: {page_path}")
        generated[page_name] = page_path

    return generated


def _generate_unavailable_figure(
    output_path: str | Path,
    title: str,
    reason: str,
) -> Path:
    """Write a diagnostic placeholder without fabricating measurement data.

    生成"数据不可用"的占位图。
    当 Case 缺少某类数据(如无喷气工况无喷气信号)时,
    用此占位图替代,保持目录结构完整,同时避免虚构数据。
    图中包含标题、原因说明和解决建议。
    """
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
    """Generate a summary dashboard image with key metrics.

    生成质量检查摘要仪表板图。
    以文本形式展示以下信息:
    - Case ID
    - 检查结果(PASS/FAIL,以绿色/红色标示)
    - 时间序列行数
    - 是否含喷气数据
    - 错误数和警告数
    - 具体的错误和警告内容(截取前若干条)

    错误文本显示为红色,警告显示为橙色。
    """
    fig, ax = plt.subplots(figsize=(10, 6))
    ax.axis("off")

    errors = result.get("errors", [])
    warnings = result.get("warnings", [])
    n_rows = len(result.get("timeseries", []))
    has_jet = result.get("has_jet_data", False)

    # 构建要显示的文本行
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
        # 最多显示前 8 条错误,避免图面过长
        for e in errors[:8]:
            lines.append(f"  ! {e}")
        if len(errors) > 8:
            lines.append(f"  ... and {len(errors) - 8} more")

    if warnings:
        lines.append("")
        lines.append("Warnings:")
        # 最多显示前 5 条警告
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
            pass  # already shown above / 已在标题处显示
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

    统一生成一个 Case 的所有诊断图表,是图表的统一入口。
    按顺序生成:
    1. force_timeseries.png — 力时间序列(始终生成)
    2. jet_schedule.png — 喷气热力图(有喷气数据时生成,否则占位图)
    3. massflow_check_*.png — 质量流量对比,有数据时生成 4 张覆盖 24 路
    4. quality_summary.png — 质量摘要(始终生成)
    """
    out = Path(output_dir)
    out.mkdir(parents=True, exist_ok=True)

    rows = _crop_initial_transient(
        result.get("timeseries", []), result.get("manifest")
    )

    figs: dict[str, Path | None] = {}

    figs["force_timeseries"] = generate_force_timeseries(
        rows, out / "force_timeseries.png"
    )
    figs["jet_schedule"] = generate_jet_schedule(
        rows, out / "jet_schedule.png",
        case_has_jet=result.get("has_jet_data"),
    )
    figs.update(generate_massflow_check(
        rows, out / "massflow_check.png",
        case_has_jet=result.get("has_jet_data"),
    ))
    figs["quality_summary"] = generate_quality_summary(
        result, out / "quality_summary.png"
    )

    return figs
