"""Utilities for the B06 minimal input-output ARX ROM workflow.

B06 最小输入输出 ARX 降阶模型(ROM)工作流的工具函数模块。
本模块提供数据加载、CSV/JSON 读写、指标计算、SVG 可视化等基础工具，
被 training、inference、validation 等上层模块调用。
"""

from __future__ import annotations

import csv
import html
import json
from pathlib import Path
from typing import Any

import numpy as np

from starccm.control.control_spec import JET_COLUMNS, LOAD_COLUMNS

# 质量流量命令列名：cmd_massflow_01 到 cmd_massflow_24，对应 24 个喷射器的质量流量指令
MASSFLOW_COLUMNS = tuple(f"cmd_massflow_{idx:02d}" for idx in range(1, 25))
# ROM 的输出列：包含所有载荷列（区域力/力矩）加上总轴向力 Fz_Total
ROM_OUTPUT_COLUMNS = (*LOAD_COLUMNS, "Fz_Total")
# ROM 的输入列：包含所有喷射器状态列加上质量流量命令列
ROM_INPUT_COLUMNS = (*JET_COLUMNS, *MASSFLOW_COLUMNS)
# 区域输出列：仅包含载荷列（用于区域级别的误差分析）
REGIONAL_OUTPUT_COLUMNS = tuple(LOAD_COLUMNS)


# ---------------------------------------------------------------------------
# 加载案例数据表：读取 timeseries.csv，若存在 actuation_schedule.csv 则合并
# ---------------------------------------------------------------------------
def load_case_table(case_dir: str | Path) -> list[dict[str, str]]:
    """Load and merge ``timeseries.csv`` with ``actuation_schedule.csv`` if present."""
    # case_dir 可以是字符串或 Path 对象，统一转为 Path 以便路径操作
    case_path = Path(case_dir)
    timeseries_path = case_path / "timeseries.csv"
    schedule_path = case_path / "actuation_schedule.csv"
    rows = read_csv_rows(timeseries_path)
    if not rows:
        raise ValueError(f"{timeseries_path} contains no rows")
    if schedule_path.exists():
        # 若调度文件存在，通过 window_id 或 physical_time 做键值匹配，合并质量流量列
        rows = merge_schedule_columns(rows, read_csv_rows(schedule_path))
    return rows


# 读取 CSV 文件为字典列表（每行一个字典，键为列名，值为字符串）
def read_csv_rows(path: str | Path) -> list[dict[str, str]]:
    with Path(path).open("r", encoding="utf-8", newline="") as handle:
        return list(csv.DictReader(handle))


# 将调度表中的质量流量列合并到时间序列行中
# 优先使用 window_id 进行匹配，回退到四舍五入的 physical_time
def merge_schedule_columns(
    timeseries_rows: list[dict[str, str]],
    schedule_rows: list[dict[str, str]],
) -> list[dict[str, str]]:
    """Attach mass-flow command columns to timeseries rows.

    Prefer ``window_id`` when available, otherwise fall back to rounded
    ``physical_time``.  Existing timeseries columns are preserved.
    """
    if not schedule_rows:
        # 调度表为空时，直接返回 time series 的深拷贝
        return [dict(row) for row in timeseries_rows]
    # 将调度表按 key 建立索引（key 由 _row_key 统一生成，基于 window_id 或 physical_time）
    schedule_by_key = {_row_key(row): row for row in schedule_rows}
    merged: list[dict[str, str]] = []
    for row in timeseries_rows:
        record = dict(row)
        schedule = schedule_by_key.get(_row_key(row), {})
        # 只合并 timeseries 中缺少的列，避免覆盖已有数据
        for column in MASSFLOW_COLUMNS:
            if column not in record and column in schedule:
                record[column] = schedule[column]
        merged.append(record)
    return merged


# 校验数据行是否包含指定的所有列，若缺少则抛出 ValueError
def require_columns(rows: list[dict[str, str]], columns: tuple[str, ...] | list[str]) -> None:
    # 从首行提取列名集合做快速查表
    available = set(rows[0]) if rows else set()
    missing = [column for column in columns if column not in available]
    if missing:
        raise ValueError("missing required columns: " + ", ".join(missing))


# 从 CSV 行数据中提取指定列为 NumPy 矩阵（float64 类型）
def matrix_from_rows(rows: list[dict[str, str]], columns: tuple[str, ...] | list[str]) -> np.ndarray:
    # 先校验所需列是否存在，避免索引错误
    require_columns(rows, columns)
    # 预分配零矩阵，形状为 (行数, 列数)
    data = np.zeros((len(rows), len(columns)), dtype=float)
    for row_idx, row in enumerate(rows):
        for col_idx, column in enumerate(columns):
            # 使用 _to_float 将字符串转为浮点数，空值或 None 按 0 处理
            data[row_idx, col_idx] = _to_float(row.get(column, "0"), column, row_idx)
    return data


# 从行数据中提取时间轴数值
# 优先使用 physical_time 列，若不存在则使用整数索引作为虚拟时间
def time_values_from_rows(rows: list[dict[str, str]]) -> np.ndarray:
    if rows and "physical_time" in rows[0]:
        # 使用 matrix_from_rows 提取并展平为一维数组
        return matrix_from_rows(rows, ["physical_time"]).ravel()
    # 回退方案：直接使用 0, 1, 2, ... 作为时间值
    return np.arange(len(rows), dtype=float)


# 计算预测结果相对于真实值的各项指标
# 对每个输出列分别计算：RMSE（均方根误差）、NRMSE（归一化 RMSE）、
# Pearson 相关系数、平均误差、最大绝对误差
def compute_metrics(
    truth: np.ndarray,
    prediction: np.ndarray,
    output_columns: tuple[str, ...] | list[str],
) -> dict[str, dict[str, float]]:
    metrics: dict[str, dict[str, float]] = {}
    # errors 矩阵：预测值减真实值，正数表示高估，负数表示低估
    errors = prediction - truth
    for idx, column in enumerate(output_columns):
        y = truth[:, idx]
        y_hat = prediction[:, idx]
        err = errors[:, idx]
        # RMSE：均方根误差，对误差平方取均值再开方
        rmse = float(np.sqrt(np.mean(err * err)))
        # 数据范围（最大值 - 最小值），用于 NRMSE 归一化
        value_range = float(np.max(y) - np.min(y))
        # NRMSE = RMSE / range，当 range 接近零时返回 NaN（常数序列）
        nrmse = rmse / value_range if value_range > 1.0e-12 else float("nan")
        # Pearson 相关系数：当任一序列标准差接近零时无法计算，返回 NaN
        if np.std(y) <= 1.0e-12 or np.std(y_hat) <= 1.0e-12:
            corr = float("nan")
        else:
            corr = float(np.corrcoef(y, y_hat)[0, 1])
        metrics[column] = {
            "rmse": rmse,
            "nrmse_range": float(nrmse),
            "correlation": corr,
            "mean_error": float(np.mean(err)),       # 平均误差，反映系统偏差方向
            "max_abs_error": float(np.max(np.abs(err))),  # 最大绝对误差
        }
    return metrics


# 将字典写入 JSON 文件（自动创建父目录，格式化输出，支持 NaN/Inf 等特殊值）
def write_json(path: str | Path, payload: dict[str, Any]) -> None:
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    Path(path).write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, allow_nan=True) + "\n",
        encoding="utf-8",
    )


# 将预测结果写入 CSV 文件
# 对每个输出列生成三列：*_true（真实值）、*_pred（预测值）、*_error（预测 - 真实）
def write_prediction_csv(
    path: str | Path,
    time_values: np.ndarray,
    truth: np.ndarray,
    prediction: np.ndarray,
    output_columns: tuple[str, ...] | list[str],
) -> None:
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    fieldnames = ["physical_time"]
    for column in output_columns:
        fieldnames.extend([f"{column}_true", f"{column}_pred", f"{column}_error"])
    with Path(path).open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        for idx, t in enumerate(time_values):
            row: dict[str, Any] = {"physical_time": float(t)}
            for col_idx, column in enumerate(output_columns):
                row[f"{column}_true"] = float(truth[idx, col_idx])
                row[f"{column}_pred"] = float(prediction[idx, col_idx])
                row[f"{column}_error"] = float(prediction[idx, col_idx] - truth[idx, col_idx])
            writer.writerow(row)


# 生成预测对比 SVG 图：每个输出列一个面板，蓝色曲线为真实值，红色为预测值
def write_prediction_svg(
    path: str | Path,
    time_values: np.ndarray,
    truth: np.ndarray,
    prediction: np.ndarray,
    output_columns: tuple[str, ...] | list[str] = REGIONAL_OUTPUT_COLUMNS,
) -> None:
    panels = []
    for idx, column in enumerate(output_columns):
        panels.append((column, truth[:, idx], prediction[:, idx]))
    # pair_mode=True：每个面板绘制两条曲线（真实 + 预测），并显示图例
    _write_panel_svg(path, time_values, panels, "ARX ROM validation: true vs prediction", pair_mode=True)


# 生成误差 SVG 图：每个面板显示预测值减去真实值的误差曲线
def write_error_svg(
    path: str | Path,
    time_values: np.ndarray,
    truth: np.ndarray,
    prediction: np.ndarray,
    output_columns: tuple[str, ...] | list[str] = REGIONAL_OUTPUT_COLUMNS,
) -> None:
    panels = []
    for idx, column in enumerate(output_columns):
        # 将 second 设为 None，每个面板只绘制一条误差曲线
        panels.append((column, prediction[:, idx] - truth[:, idx], None))
    # pair_mode=False：每个面板只画一条曲线，无图例
    _write_panel_svg(path, time_values, panels, "ARX ROM validation error", pair_mode=False)


# 生成 RMSE 柱状图 SVG：每个载荷通道一个柱子，直观比较各通道预测误差
def write_rmse_bar_svg(
    path: str | Path,
    metrics: dict[str, dict[str, float]],
    output_columns: tuple[str, ...] | list[str] = REGIONAL_OUTPUT_COLUMNS,
) -> None:
    # SVG 画布尺寸和边距定义
    width = 920
    height = 360
    margin_left = 72
    margin_bottom = 58
    # 绘图区域尺寸（扣除边距）
    plot_w = width - margin_left - 32
    plot_h = height - 70 - margin_bottom
    values = [float(metrics[column]["rmse"]) for column in output_columns]
    max_value = max(values) if values else 1.0
    max_value = max(max_value, 1.0e-12)  # 防止除零
    # 计算柱宽和间距
    bar_w = plot_w / max(len(values), 1) * 0.62
    gap = plot_w / max(len(values), 1)
    lines = _svg_header(width, height, "ARX ROM RMSE by load cell")
    # 绘图区域的白色背景和边框
    lines.append(f'<rect x="{margin_left}" y="42" width="{plot_w}" height="{plot_h}" fill="#fbfcfe" stroke="#d0d7de"/>')
    # 绘制 5 条水平网格线及对应的刻度数值
    for tick in range(5):
        y = 42 + plot_h - tick * plot_h / 4
        val = max_value * tick / 4
        lines.append(f'<line x1="{margin_left}" y1="{y:.2f}" x2="{margin_left + plot_w}" y2="{y:.2f}" stroke="#edf2f7"/>')
        lines.append(_text(8, y + 3, f"{val:.3g}", 10, "#57606a"))
    # 为每个输出列绘制一个矩形柱，上方标注数值，下方标注列名
    for idx, column in enumerate(output_columns):
        h = values[idx] / max_value * plot_h  # 柱高按最大值比例缩放
        x = margin_left + idx * gap + (gap - bar_w) / 2
        y = 42 + plot_h - h
        lines.append(f'<rect x="{x:.2f}" y="{y:.2f}" width="{bar_w:.2f}" height="{h:.2f}" fill="#1864ab"/>')
        lines.append(_text(x + bar_w / 2 - 24, 42 + plot_h + 22, column, 10, "#24292f"))
        lines.append(_text(x + bar_w / 2 - 20, y - 6, f"{values[idx]:.3g}", 10, "#24292f"))
    lines.append("</svg>")
    _write_text(path, "\n".join(lines))


# 对每个喷射器的上升沿事件，分析输出响应的峰值和时延，生成摘要 CSV
# 用于快速评估每个喷射器对载荷的主导影响
def write_single_jet_response_summary(
    path: str | Path,
    rows: list[dict[str, str]],
    *,
    horizon_steps: int = 8,        # 上升沿后监测的步长（采样点数量）
    baseline_steps: int = 2,       # 上升沿前用于计算基线的步长
) -> None:
    """Summarize output response after each jet rising edge."""
    # 过滤出实际存在的列（避免写入空数据）
    output_columns = tuple(column for column in ROM_OUTPUT_COLUMNS if rows and column in rows[0])
    jet_columns = tuple(column for column in JET_COLUMNS if rows and column in rows[0])
    time_values = time_values_from_rows(rows) if rows else np.asarray([], dtype=float)
    # CSV 输出列定义
    fieldnames = [
        "jet",               # 喷射器名称
        "event_count",       # 上升沿事件总数
        "dominant_output",   # 响应最显著的输出通道
        "peak_delta",        # 峰值变化量（相对于基线）
        "peak_abs_delta",    # 峰值绝对变化量
        "peak_lag_steps",    # 峰值出现的延迟（采样步数）
        "peak_lag_seconds",  # 峰值出现的延迟（秒）
        "note",             # 备注
    ]
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    with Path(path).open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        if not rows or not jet_columns or not output_columns:
            # 数据不可用，写入一行说明后提前返回
            writer.writerow(
                {
                    "jet": "NA",
                    "event_count": 0,
                    "dominant_output": "NA",
                    "peak_delta": "",
                    "peak_abs_delta": "",
                    "peak_lag_steps": "",
                    "peak_lag_seconds": "",
                    "note": "no jet/output columns available for single-jet response summary",
                }
            )
            return

        jets = matrix_from_rows(rows, jet_columns)
        outputs = matrix_from_rows(rows, output_columns)
        # 通过 time_values 中位差分估算采样间隔 dt
        dt = float(np.median(np.diff(time_values))) if len(time_values) > 1 else 1.0
        for jet_idx, jet in enumerate(jet_columns):
            # 检测喷射器的开/关状态（阈值 0.5）
            active = jets[:, jet_idx] > 0.5
            # 找出所有上升沿位置：当前为开且前一步为关
            rising_edges = [idx for idx in range(1, len(active)) if active[idx] and not active[idx - 1]]
            # 遍历所有上升沿事件，记录响应最显著的那个
            best: dict[str, Any] | None = None
            for event_idx in rising_edges:
                # 取上升沿前 baseline_steps 个点的均值作为基线
                baseline_start = max(0, event_idx - baseline_steps)
                baseline = np.mean(outputs[baseline_start:event_idx], axis=0) if event_idx > baseline_start else outputs[event_idx]
                # 在 horizon_steps 范围内监测响应
                stop = min(len(outputs), event_idx + horizon_steps + 1)
                delta = outputs[event_idx:stop] - baseline      # 相对于基线的变化量
                abs_delta = np.abs(delta)                        # 绝对变化量，用于定位峰值
                flat_idx = int(np.argmax(abs_delta))             # 在展平数组中找到最大值位置
                lag_idx, out_idx = np.unravel_index(flat_idx, abs_delta.shape)  # 反解出时延和输出通道索引
                candidate = {
                    "dominant_output": output_columns[out_idx],
                    "peak_delta": float(delta[lag_idx, out_idx]),
                    "peak_abs_delta": float(abs_delta[lag_idx, out_idx]),
                    "peak_lag_steps": int(lag_idx),
                    "peak_lag_seconds": float(lag_idx * dt),
                }
                # 保留所有事件中峰值绝对值最大的那个
                if best is None or candidate["peak_abs_delta"] > best["peak_abs_delta"]:
                    best = candidate
            if best is None:
                # 没有检测到上升沿（喷射器始终关闭或始终开启）
                best = {
                    "dominant_output": "NA",
                    "peak_delta": "",
                    "peak_abs_delta": "",
                    "peak_lag_steps": "",
                    "peak_lag_seconds": "",
                }
            writer.writerow(
                {
                    "jet": jet,
                    "event_count": len(rising_edges),
                    "note": "rising edge summary from available case data",
                    **best,
                }
            )


# 生成行匹配键值：优先使用 window_id（转为整数再转字符串以保证一致性），
# 回退到四舍五入到 12 位有效数字的 physical_time 字符串
def _row_key(row: dict[str, str]) -> tuple[str, str]:
    if "window_id" in row and row.get("window_id", "") != "":
        return ("window_id", str(int(float(row["window_id"]))))
    return ("physical_time", f"{float(row.get('physical_time', 0.0)):.12g}")


# 安全地将字符串转为浮点数，遇到非数值时抛出带上下文的 ValueError
def _to_float(value: str | None, column: str, row_idx: int) -> float:
    try:
        return float(value if value not in (None, "") else 0.0)
    except ValueError as exc:
        raise ValueError(f"non-numeric value in row {row_idx}, column {column}: {value!r}") from exc


# 核心 SVG 面板绘制函数：创建多个面板的时序图
# 每个面板可包含一条（单曲线模式）或两条（配对模式）曲线
# 参数 panels 结构：[(标签, 第一组值, 第二组值或 None), ...]
def _write_panel_svg(
    path: str | Path,
    time_values: np.ndarray,
    panels: list[tuple[str, np.ndarray, np.ndarray | None]],
    title: str,
    *,
    pair_mode: bool,  # True 时显示两条曲线及图例，False 时只显示一条
) -> None:
    # SVG 布局参数：宽度 1040，每个面板高 170
    width = 1040
    panel_h = 170
    top = 44
    height = top + panel_h * len(panels) + 30
    left = 76
    plot_w = width - left - 30
    plot_h = 116
    lines = _svg_header(width, height, title)
    for panel_idx, (label, first, second) in enumerate(panels):
        y0 = top + panel_idx * panel_h
        # 确定统一的 Y 轴范围（包含两组数据的 min/max，保证对比一致性）
        series_values = first if second is None else np.column_stack([first, second])
        min_y = float(np.min(series_values))
        max_y = float(np.max(series_values))
        if abs(max_y - min_y) < 1.0e-12:
            # 常数序列的处理：将范围扩大 ±1，避免除零
            max_y += 1.0
            min_y -= 1.0
        # 面板标签
        lines.append(_text(12, y0 + 18, label, 13, "#24292f"))
        # 面板背景矩形
        lines.append(f'<rect x="{left}" y="{y0}" width="{plot_w}" height="{plot_h}" fill="#fbfcfe" stroke="#d0d7de"/>')
        # 4 条水平网格线
        for tick in range(4):
            gy = y0 + tick * plot_h / 3
            lines.append(f'<line x1="{left}" y1="{gy:.2f}" x2="{left + plot_w}" y2="{gy:.2f}" stroke="#edf2f7"/>')
        # 主曲线（蓝色）
        lines.append(_polyline(time_values, first, left, y0, plot_w, plot_h, min_y, max_y, "#0b7285", 1.8))
        if second is not None:
            # 配对模式：第二曲线（红色）
            lines.append(_polyline(time_values, second, left, y0, plot_w, plot_h, min_y, max_y, "#c92a2a", 1.5))
            lines.append(_text(left + 10, y0 + 16, "true", 10, "#0b7285"))
            lines.append(_text(left + 54, y0 + 16, "pred", 10, "#c92a2a"))
        elif pair_mode:
            lines.append(_text(left + 10, y0 + 16, "series", 10, "#0b7285"))
        # Y 轴刻度（最大值和最小值）
        lines.append(_text(8, y0 + 42, f"{max_y:.3g}", 9, "#57606a"))
        lines.append(_text(8, y0 + plot_h, f"{min_y:.3g}", 9, "#57606a"))
    lines.append("</svg>")
    _write_text(path, "\n".join(lines))


# 生成 SVG 折线元素字符串
# 将时间序列数据映射到 SVG 坐标空间，返回 <polyline> 标签
def _polyline(
    time_values: np.ndarray,
    values: np.ndarray,
    left: float,           # 绘图区域左边界
    top: float,            # 绘图区域上边界
    width: float,          # 绘图区域宽度
    height: float,         # 绘图区域高度
    min_y: float,          # Y 轴最小值（数据范围下界）
    max_y: float,          # Y 轴最大值（数据范围上界）
    color: str,            # 折线颜色
    stroke_width: float,   # 线宽
) -> str:
    min_t = float(np.min(time_values))
    max_t = float(np.max(time_values))
    if abs(max_t - min_t) < 1.0e-12:
        max_t = min_t + 1.0
    points = []
    for t, value in zip(time_values, values):
        # X 映射：时间值在 [min_t, max_t] 到 [left, left + width]
        x = left + (float(t) - min_t) / (max_t - min_t) * width
        # Y 映射：数据值在 [min_y, max_y] 到 [top + height, top]（SVG Y 轴向下）
        y = top + height - (float(value) - min_y) / (max_y - min_y) * height
        points.append(f"{x:.2f},{y:.2f}")
    return f'<polyline fill="none" stroke="{color}" stroke-width="{stroke_width}" points="{" ".join(points)}"/>'


# 生成 SVG 文档头部：xmlns、画布尺寸、白色背景、标题文本
def _svg_header(width: int, height: int, title: str) -> list[str]:
    return [
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" viewBox="0 0 {width} {height}">',
        '<rect width="100%" height="100%" fill="white"/>',
        _text(14, 24, title, 16, "#24292f"),
    ]


# 辅助函数：生成 SVG <text> 元素字符串
# 自动对文本内容进行 HTML 转义，防止 XSS 或特殊字符破坏 SVG 结构
def _text(x: float, y: float, value: str, size: int, color: str) -> str:
    return (
        f'<text x="{x:.2f}" y="{y:.2f}" font-family="Arial, sans-serif" '
        f'font-size="{size}" fill="{color}">{html.escape(str(value))}</text>'
    )


# 将字符串内容写入文本文件（自动创建父目录，追加换行符）
def _write_text(path: str | Path, content: str) -> None:
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    Path(path).write_text(content + "\n", encoding="utf-8")
