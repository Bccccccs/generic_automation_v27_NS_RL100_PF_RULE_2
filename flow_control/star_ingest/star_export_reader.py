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
    star_actual_massflow_01 … star_actual_massflow_24,
    actual_massflow_01 … actual_massflow_24

The reader preserves the columns actually exported by STAR-CCM+.  It only
computes quantities that can be derived directly from present columns, such as
``Fz_Total`` from the six bottom-force sensors.  It does not pad missing
physical quantities with placeholder zeroes.

该模块负责读取 STAR-CCM+ 仿真软件导出的原始 CSV 文件,
并将其转换为流水线后续步骤可以统一处理的"标准时间序列"格式。

STAR-CCM+ 导出的 CSV 文件具有以下特点:
- 列名包含中文标签(如"时间")、监视器名称和单位(如 "(N)")
- 力/力矩监视器可能分散在多个 CSV 文件中
- 不同文件的采样时间点可能存在微小浮点差异

模块通过正则表达式匹配,将 STAR 的非标准列名映射到标准列名,
从而屏蔽仿真软件与流水线之间的列名差异,实现数据格式的统一。
"""

from __future__ import annotations

import csv
import re
from pathlib import Path
from typing import Any

# ── STAR export column-name patterns ──────────────────────────────────────────

# Maps standard column names to regex patterns that match STAR-CCM+ export headers.
# The STAR column format is typically:  "<MonitorName>: <Description> (<Unit>)"
#
# 该字典定义了"标准列名 → 匹配正则"的映射关系。
# STAR 导出的 CSV 列名通常包含中文(如"时间")或英文描述,
# 正则表达式同时兼容中英文,以便处理不同语言版本的 STAR 导出文件。
STAR_COLUMN_PATTERNS: dict[str, re.Pattern] = {
    "physical_time": re.compile(r"时间|time|physical.?time|Time", re.IGNORECASE),
    # Current STAR files contain both ``fz Monitor`` (underbody six-region
    # total) and ``Fz Monitor`` (whole vehicle).  Letter case is significant.
    "fz": re.compile(r"^fz\s*Monitor:"),
    "Fz_Total": re.compile(r"^Fz\s*Monitor:"),
    "Fz_S1L": re.compile(r"S1L.*Monitor", re.IGNORECASE),
    "Fz_S1R": re.compile(r"S1R.*Monitor", re.IGNORECASE),
    "Fz_S2L": re.compile(r"S2L.*Monitor", re.IGNORECASE),
    "Fz_S2R": re.compile(r"S2R.*Monitor", re.IGNORECASE),
    "Fz_S3L": re.compile(r"S3L.*Monitor", re.IGNORECASE),
    "Fz_S3R": re.compile(r"S3R.*Monitor", re.IGNORECASE),
    # 阻力(中英文兼容)
    "Drag_Total": re.compile(r"drag|阻力", re.IGNORECASE),
    # 俯仰力矩(中英文兼容)
    "Pitch_Moment": re.compile(r"pitch|俯仰", re.IGNORECASE),
    # 滚转力矩(中英文兼容)
    "Roll_Moment": re.compile(r"roll|滚转", re.IGNORECASE),
    # 喷气反力 Z 方向(中英文兼容)
    "Jet_Reaction_Z": re.compile(r"jet.*reaction|喷气.*反力|reaction.*z", re.IGNORECASE),
}

# 下面三个正则分别匹配:算法侧喷气开关信号(JET_xx)、
# 指令质量流量(cmd_massflow_xx)和实际质量流量(actual_massflow_xx)。
# JET_xx 表示第 xx 号喷气口控制通道的开关状态(0/1),
# cmd_massflow_xx 是控制系统发出的质量流量指令,
# actual_massflow_xx 是仿真计算返回的实际质量流量。项目标准将
# “从喷口流入流场”定义为正；STAR 质量流量入口 report 可能按边界
# 外法向输出负值，因此标准 actual_massflow 存储为非负的流量大小。
#
# 注意:STAR 中 JET01..JET24 是底部受力区域,不是喷气口;不能把 JET01
# 这样的 STAR 边界名映射成算法侧 JET_01 开关列。
JET_COLUMN_PATTERN = re.compile(r"^JET_(\d{1,2})$", re.IGNORECASE)
MASSFLOW_CMD_PATTERN = re.compile(r"cmd.*mass.?flow[_\s]?(\d{1,2})", re.IGNORECASE)
MASSFLOW_ACTUAL_PATTERN = re.compile(r"(?:actual|real).*mass.?flow[_\s]?(\d{1,2})", re.IGNORECASE)

# Standard column names
# 标准列名常量定义:
# FZ_SENSOR_COLUMNS: 六个底部力传感器的 Fz(法向力)分量
#   S1L/S1R = 传感器1左右; S2L/S2R = 传感器2左右; S3L/S3R = 传感器3左右
# GLOBAL_COLUMNS: 全局力和力矩(总力、阻力、俯仰力矩、滚转力矩、喷气反力)
# STANDARD_LOAD_COLUMNS: 所有载荷列的组合
FZ_SENSOR_COLUMNS = ("Fz_S1L", "Fz_S1R", "Fz_S2L", "Fz_S2R", "Fz_S3L", "Fz_S3R")
GLOBAL_COLUMNS = (
    "fz",
    "Fz_Total",
    "Drag_Total",
    "Pitch_Moment",
    "Roll_Moment",
    "Jet_Reaction_Z",
)
STANDARD_LOAD_COLUMNS = (*FZ_SENSOR_COLUMNS, *GLOBAL_COLUMNS)

# 24 个算法喷气开关列名,格式为 JET_01 ~ JET_24(开关信号)
JET_COLUMNS = tuple(f"JET_{idx:02d}" for idx in range(1, 25))
# 24 个喷气口指令质量流量列名 cmd_massflow_01 ~ cmd_massflow_24
CMD_MASSFLOW_COLUMNS = tuple(f"cmd_massflow_{idx:02d}" for idx in range(1, 25))
# 24 个喷气口 STAR 原始带符号实际质量流量
STAR_ACTUAL_MASSFLOW_COLUMNS = tuple(
    f"star_actual_massflow_{idx:02d}" for idx in range(1, 25)
)
# 24 个喷气口算法侧实际质量流量，向计算域喷入为正
ACTUAL_MASSFLOW_COLUMNS = tuple(f"actual_massflow_{idx:02d}" for idx in range(1, 25))


def normalize_actual_massflow(value: float, *, sign_to_domain: float = -1.0) -> float:
    """Return project-standard jet mass flow with injection defined positive.

    STAR inlet reports may be negative because their sign follows the boundary
    outward normal.  The flow-control contract instead stores the physical
    injection rate using a signed direction transform.  Reverse flow therefore
    remains negative instead of being hidden by ``abs``.
    """
    return float(sign_to_domain) * float(value)

# 需要忽略的 STAR 产品目录下的 CSV 文件名模式。
# "报告"和"pressure"类型的文件与力/力矩时间序列无关,跳过。
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

    在 STAR 仿真结果的产品目录中,查找可以映射为时间序列列的监视器 CSV 文件。
    返回的列表按文件名排序,确保后续处理的顺序稳定。
    """
    root = Path(product_dir)
    # 验证目录是否存在,不存在则直接报错
    if not root.is_dir():
        raise NotADirectoryError(f"STAR product directory not found: {root}")

    selected: list[Path] = []
    # 遍历目录下所有 CSV 文件(排序保证结果确定性)
    for path in sorted(root.glob("*.csv")):
        # 跳过已知的报告/压力文件,它们不属于力/力矩时间序列
        if any(pattern.lower() in path.name.lower() for pattern in IGNORED_PRODUCT_NAME_PATTERNS):
            continue
        try:
            # 只读取首行获取列名,不加载全部数据(性能优化)
            with path.open("r", encoding="utf-8-sig", newline="") as handle:
                headers = next(csv.reader(handle))
            # 尝试将 STAR 列名映射为标准列名
            mapping = detect_star_column_mapping(headers)
        except (StopIteration, ValueError):
            # 空文件或无法识别的列名,跳过
            continue
        # 至少包含一个非 physical_time 的列才认为是有用的数据文件
        if any(column != "physical_time" for column in mapping):
            selected.append(path)
    return selected


def detect_star_column_mapping(headers: list[str]) -> dict[str, str]:
    """Match STAR-CCM+ export header names to standard column names.

    Returns a dict of ``{standard_name: star_header_name}``.

    Raises ``ValueError`` if a required column cannot be matched.

    该函数是列名映射的核心逻辑:
    1. 遍历 STAR CSV 的每个原始列名
    2. 按优先级依次尝试匹配:标准列名 → 算法喷气开关信号 → 指令质量流量 → 实际质量流量
    3. 返回 {标准名: 原始列名} 的映射字典
    """
    mapping: dict[str, str] = {}
    unknown: list[str] = []

    for header in headers:
        # 去除首尾空白和可能的引号,STAR 导出的列名可能被引号包裹
        header_stripped = header.strip().strip('"')
        matched = False

        # Try standard patterns first
        # 首先尝试匹配标准列名(力/力矩/时间等),使用正则模糊匹配
        for standard_name, pattern in STAR_COLUMN_PATTERNS.items():
            if pattern.search(header_stripped):
                mapping[standard_name] = header
                matched = True
                break

        if matched:
            continue

        # Try algorithm-side jet switch columns (JET_01 … JET_24).
        # Do not match STAR JET01 bottom-region names here.
        # 尝试匹配算法侧喷气开关信号,编号范围 01~24;这里不能匹配 STAR 的 JET01 底面区域名。
        jet_match = JET_COLUMN_PATTERN.match(header_stripped)
        if jet_match:
            idx = int(jet_match.group(1))
            if 1 <= idx <= 24:
                mapping[f"JET_{idx:02d}"] = header
                continue

        # Try cmd_massflow columns
        # 尝试匹配指令质量流量列,编号范围 01~24
        cmd_match = MASSFLOW_CMD_PATTERN.match(header_stripped)
        if cmd_match:
            idx = int(cmd_match.group(1))
            if 1 <= idx <= 24:
                mapping[f"cmd_massflow_{idx:02d}"] = header
                continue

        # Try actual_massflow columns
        # 尝试匹配实际质量流量列,编号范围 01~24
        actual_match = MASSFLOW_ACTUAL_PATTERN.match(header_stripped)
        if actual_match:
            idx = int(actual_match.group(1))
            if 1 <= idx <= 24:
                mapping[f"star_actual_massflow_{idx:02d}"] = header
                mapping[f"actual_massflow_{idx:02d}"] = header
                continue

        unknown.append(header_stripped)

    # physical_time 是必须存在的列,如果找不到则报错
    if "physical_time" not in mapping:
        raise ValueError(
            f"Could not find physical_time column in STAR export headers. "
            f"Looked for patterns matching '时间', 'time', 'physical_time'. "
            f"Available headers: {headers[:10]}"
        )

    return mapping


def _is_float(value: str) -> bool:
    """
    判断一个字符串是否可以安全地转换为浮点数。
    用于在读取 CSV 时区分数值列和文本列(如 "NaN"、"success" 等)。
    """
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

    读取单个 STAR-CCM+ 导出的 CSV 文件,返回标准化后的数据字典。
    整个过程分为三步:
    1. 读取原始列名并建立映射(STAR 列名 → 标准列名)
    2. 从列名中提取单位信息(如 "(N)" → 牛顿)
    3. 逐行读取数据,将数值字符串转为 float
    """
    path = Path(path)
    if not path.exists():
        raise FileNotFoundError(f"STAR export CSV not found: {path}")

    # utf-8-sig transparently removes the BOM produced by STAR's
    # "Excel compatible" export while also accepting ordinary UTF-8 files.
    # 使用 utf-8-sig 编码可以自动移除 STAR 的"Excel 兼容"导出产生的 BOM 头,
    # 同时也能正确处理普通的 UTF-8 文件。
    with path.open("r", encoding="utf-8-sig", newline="") as f:
        reader = csv.reader(f)
        raw_headers = next(reader)

    mapping = detect_star_column_mapping(raw_headers)

    # Detect units from column headers
    # 从 STAR 列名中提取单位信息,列名通常以 "(单位)" 结尾,
    # 例如 "S1L Monitor: S1L Monitor (N)" → 单位是 "N"(牛顿)
    units: dict[str, str] = {}
    for standard_name, star_header in mapping.items():
        unit_match = re.search(r"\(([^)]+)\)", star_header)
        if unit_match:
            units[standard_name] = unit_match.group(1)

    # Read all data rows
    # 逐行读取 CSV 数据并标准化
    with path.open("r", encoding="utf-8-sig", newline="") as f:
        reader = csv.DictReader(f)
        rows: list[dict[str, Any]] = []
        for row in reader:
            normalized: dict[str, Any] = {}
            for standard_name, star_header in mapping.items():
                raw = row.get(star_header, "").strip().strip('"')
                # Try numeric conversion
                # 尝试转换为浮点数;转换失败则保留原始字符串(如 "NaN"、"success" 等)
                if _is_float(raw):
                    value = float(raw)
                    if standard_name in ACTUAL_MASSFLOW_COLUMNS:
                        value = normalize_actual_massflow(value)
                    normalized[standard_name] = value
                else:
                    normalized[standard_name] = raw  # keep as string (e.g. "NaN", "success")
            rows.append(normalized)

    # Build column order: physical_time, Fz sensors, globals, jets, massflows
    # 按标准顺序排列列名: 物理时间 → 力传感器 → 全局量 → 喷气信号 → 质量流量
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

    批量读取多个 STAR 导出 CSV 文件,将其合并为一个统一的时间序列。
    设计背景:STAR 仿真通常将力、力矩、喷气信号等数据导出到不同的 CSV 文件中,
    这些文件的时间戳可能存在微小浮点差异。
    合并策略:
    - 以 physical_time 为键进行外连接(outer join)
    - 对时间戳四舍五入到 12 位小数,消除浮点噪声
    - 最终按时间排序输出
    """
    datasets = [read_star_export_csv(p) for p in file_paths]

    if not datasets:
        raise ValueError("at least one STAR export file is required")

    # One physical quantity must have exactly one source.  Silent overwrite is
    # especially dangerous for STAR reports whose names differ only by case.
    owners: dict[str, list[str]] = {}
    for dataset in datasets:
        source = dataset["source_files"][0]
        for column in dataset["columns"]:
            if column == "physical_time":
                continue
            owners.setdefault(column, []).append(source)
    duplicates = {column: sources for column, sources in owners.items() if len(sources) > 1}
    if duplicates:
        details = "; ".join(
            f"{column} <- {', '.join(sources)}"
            for column, sources in sorted(duplicates.items())
        )
        raise ValueError(f"multiple STAR exports map to the same standard column: {details}")

    # Collect all columns
    # 收集所有数据集中出现的列名,去重后按标准顺序排列
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
    # 按 physical_time 合并行。不同监视器的导出文件
    # 可能因浮点数格式化噪声存在微小时间差,因此使用四舍五入后的键值。
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
            # 四舍五入到 12 位小数以消除浮点精度差异
            key = round(float(t), 12)
            if key not in merged:
                merged[key] = {"physical_time": float(t)}
            # update 会合并相同时间点的不同列(外连接语义)
            merged[key].update(row)

    # 按时间排序,确保时间序列单调递增
    merged_rows = [merged[t] for t in sorted(merged)]

    # Merge units and mappings
    # 合并各个数据集的单位信息、列映射关系和数据来源
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
    """Compute the underbody six-region total from its regions if absent.

    Modifies rows in place and returns them.
    ``fz = Fz_S1L + ... + Fz_S3R``.
    ``Fz_Total`` remains the independently exported whole-vehicle lift.
    The total is computed only when all six sensor values are present.

    如果数据中缺少车底六区合力列,则通过六个底部传感器
    的 Fz 值求和计算得到。只有当全部六个传感器的值都存在且合法时
    才进行计算,避免不完整数据的累计误差。

    注意:此函数直接修改传入的 rows 列表(就地修改),同时将其返回,
    兼顾性能和链式调用便利性。
    """
    for row in rows:
        if "fz" in row and row["fz"] is not None:
            continue  # already present / 已存在总力值则跳过
        values: list[float] = []
        for col in FZ_SENSOR_COLUMNS:
            v = row.get(col)
            if isinstance(v, (int, float)) and not _is_nan_like(v):
                values.append(v)
        # 只有当六个传感器的值全部有效时才求和
        if len(values) == len(FZ_SENSOR_COLUMNS):
            row["fz"] = sum(values)
    return rows


def ensure_standard_columns(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Deprecated compatibility shim.

    Missing physical quantities must remain missing so the quality checker can
    report them.  This function intentionally leaves rows unchanged.

    已废弃的兼容垫片(shim)。
    之前此函数负责填充缺失列,但设计决策改为:缺失的物理量应保持缺失,
    由 quality_checker(质量检查器)报告缺失情况,而不是默默填充零值。
    因此本函数现在不做任何操作,直接返回原始数据。
    """
    return rows


def _is_nan_like(value: Any) -> bool:
    """
    判断一个值是否为"类 NaN"(无法参与数值计算)的值。
    包括:
    - None
    - 字符串形式的 "nan"、"inf"、"-inf"、空字符串
    - Python 浮点数的 float('nan') 或 float('inf')

    用于数据清洗时过滤无效数值,确保只使用合法数值进行计算。
    """
    import math
    if value is None:
        return True
    if isinstance(value, str):
        return value.strip().lower() in {"nan", "inf", "-inf", ""}
    if isinstance(value, float):
        return math.isnan(value) or math.isinf(value)
    return False


def _order_columns(present: list[str]) -> list[str]:
    """Return columns in standard order, preserving extras at the end.

    按标准优先级顺序排列列名,未识别的列追加到末尾。
    顺序规则:
    1. physical_time(物理时间)——最左侧
    2. Fz 传感器列(S1L~S3R)
    3. 全局量(总力、阻力、力矩等)
    4. 算法喷气开关信号(JET_01~JET_24)
    5. 指令质量流量(cmd_massflow_01~cmd_massflow_24)
    6. 实际质量流量(actual_massflow_01~actual_massflow_24)
    7. 其他未识别的列(保持原始顺序)

    这个顺序保证了生成的时间序列 CSV 有良好的可读性和一致性。
    """
    priority = ("physical_time", *FZ_SENSOR_COLUMNS, *GLOBAL_COLUMNS,
                *JET_COLUMNS, *CMD_MASSFLOW_COLUMNS,
                *STAR_ACTUAL_MASSFLOW_COLUMNS, *ACTUAL_MASSFLOW_COLUMNS)
    ordered = [c for c in priority if c in present]
    extras = [c for c in present if c not in priority]
    return ordered + extras
