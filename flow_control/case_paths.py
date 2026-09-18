"""标准路径解析工具，用于 flow-control 实验中 case 目录的路径管理。

约定目录结构：
    runs/<case_id>/          — case 根目录
        raw_star/            — STAR 原始输出（真实 STAR case）
        processed/           — 标准化处理结果
        figures/             — 诊断图
        logs/                — 求解器/运行时日志
        processed/timeseries.csv  — 传感器时序数据
        actuation_schedule.csv  — 喷气激励计划
        case_manifest.yaml   — case 元数据
        quality_report.json  — 质量检查报告

兼容说明：
    旧的 mock/ROM case 仍可能把 timeseries.csv 放在 case 根目录。
    读取侧应优先使用 processed/timeseries.csv，并在不存在时回退到根目录
    timeseries.csv；写入真实 STAR/CCM case 时使用 processed/timeseries.csv。
"""

from __future__ import annotations

from pathlib import Path


def resolve_case_dir(
    *,
    case_id: str | None = None,
    case_dir: str | Path | None = None,
    runs_root: str | Path = "runs",
) -> Path:
    """解析标准 case 目录路径。

    首选通过 case_id 解析为 ``runs/<case_id>`` 的格式；
    也支持通过 case_dir 显式指定遗留或临时路径。
    两者必须且只能提供一个。

    Args:
        case_id: 标准 runs/<case_id> 中的 case 名称。
        case_dir: 显式指定的目录路径（用于临时/遗留情形）。
        runs_root: runs 根目录，默认 "runs"。

    Returns:
        解析后的 Path 对象。
    """
    if case_dir is not None:
        return Path(case_dir)
    if not case_id or Path(case_id).name != case_id:
        raise ValueError("case_id must be a plain directory name when case_dir is omitted")
    return Path(runs_root) / case_id


def resolve_case_input_dir(
    *,
    case_id: str | None = None,
    case_dir: str | Path | None = None,
    runs_root: str | Path = "runs",
) -> Path:
    """返回 ``runs/<case_id>/input`` 输入目录路径。

    该目录存储后端的激励输入文件（如 actuation_schedule.csv），
    用于 STAR-CCM+ 或 Mock Plant 读取。

    Returns:
        输入目录的 Path 对象。
    """
    return resolve_case_dir(case_id=case_id, case_dir=case_dir, runs_root=runs_root) / "input"


def case_timeseries_path(case_dir: str | Path) -> Path:
    """Return the canonical timeseries path for the current standard case layout."""

    return Path(case_dir) / "processed" / "timeseries.csv"


def legacy_case_timeseries_path(case_dir: str | Path) -> Path:
    """Return the legacy root-level timeseries path."""

    return Path(case_dir) / "timeseries.csv"


def find_case_timeseries_path(case_dir: str | Path) -> Path:
    """Find a case timeseries, preferring the current processed/ location.

    This keeps existing mock/ROM cases readable while making real STAR and CCM
    outputs follow the B33 standard case layout.
    """

    case_path = Path(case_dir)
    processed = case_timeseries_path(case_path)
    if processed.is_file():
        return processed
    return legacy_case_timeseries_path(case_path)
