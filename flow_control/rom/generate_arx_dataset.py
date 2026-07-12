"""Generate sparse24 schedule/mock cases for ARX ROM training datasets.

为 ARX 降阶模型（ROM）训练生成 sparse24 调度表和模拟案例数据集。
本模块生成大量（默认 100 个）具有不同随机种子的测试案例，
每个案例包含稀疏随机调度的 actuation_schedule.csv 和 MockDynamic24x6 仿真产生的 timeseries.csv。
通过递增全局随机种子，每个案例拥有不同的随机激励模式，为 ARX 模型提供多样化的训练数据。

生成流程（每个案例）：
1. 加载基线的 sparse24 调度配置 YAML
2. 用递增的全局随机种子覆盖配置中的种子
3. 运行 generate_from_mapping 生成 actuation_schedule.csv
4. 加载 MockDynamic24x6 配置并设置对应种子
5. 运行 write_mock_dynamic_case 生成 timeseries.csv
6. 记录案例元数据到 index.csv / index.json
"""

from __future__ import annotations

import argparse
import csv
import json
import shutil
from copy import deepcopy
from pathlib import Path
from typing import Any

import yaml

from flow_control.config import load_config_with_system_defaults, load_system_config
from flow_control.generator import generate_from_mapping
from flow_control.mock import write_mock_dynamic_case


# CLI 入口函数：解析命令行参数，调用 generate_arx_sparse24_dataset 生成数据集
def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Generate many sparse24 schedule + mock cases for ARX ROM training."
    )
    parser.add_argument(
        "--actuation-config",
        default="configs/actions/pilot_sparse24.yaml",
        help="Base sparse24 actuation YAML.",
    )
    parser.add_argument(
        "--mock-config",
        default="configs/mock_dynamic24x6.yaml",
        help="Base mock dynamic 24x6 YAML.",
    )
    parser.add_argument(
        "--system-config",
        default=None,
        help="Shared system YAML that owns the global random seed. Defaults to configs/system.yaml or FLOW_CONTROL_SYSTEM_CONFIG.",
    )
    parser.add_argument(
        "--out",
        default="runs/arx_test",
        help="Output dataset directory.",
    )
    parser.add_argument("--count", type=int, default=100, help="Number of cases to generate.")
    parser.add_argument(
        "--start-seed",
        type=int,
        default=None,
        help="First global random seed. Defaults to system.random_seed from the shared system config.",
    )
    parser.add_argument(
        "--case-prefix",
        default="sparse24_seed",
        help="Prefix for per-case directories.",
    )
    parser.add_argument(
        "--overwrite",
        action="store_true",
        help="Overwrite an existing output directory.",
    )
    args = parser.parse_args(argv)

    records = generate_arx_sparse24_dataset(
        actuation_config_path=args.actuation_config,
        mock_config_path=args.mock_config,
        system_config_path=args.system_config,
        output_dir=args.out,
        count=args.count,
        start_seed=args.start_seed,
        case_prefix=args.case_prefix,
        overwrite=args.overwrite,
    )

    print(f"generated ARX dataset cases: {len(records)}")
    print(f"dataset directory: {Path(args.out)}")
    print(f"index csv: {Path(args.out) / 'index.csv'}")
    print(f"index json: {Path(args.out) / 'index.json'}")
    return 0


# 核心函数：通过递增全局随机种子批量生成 sparse24 模式模拟案例
# 每个案例包含独立的 actuation_schedule.csv（稀疏随机调度）和
# timeseries.csv（MockDynamic24x6 动力系统仿真结果）
def generate_arx_sparse24_dataset(
    *,
    actuation_config_path: str | Path = "configs/actions/pilot_sparse24.yaml",
    mock_config_path: str | Path = "configs/mock_dynamic24x6.yaml",
    system_config_path: str | Path | None = None,
    output_dir: str | Path = "runs/arx_test",
    count: int = 100,
    start_seed: int | None = None,
    case_prefix: str = "sparse24_seed",
    overwrite: bool = False,
) -> list[dict[str, Any]]:
    """Create many sparse24 cases by incrementing one global random seed.

    Each case goes through both steps needed for ARX training data:

    1. generate ``actuation_schedule.csv`` from the sparse24 actuation config;
    2. run ``MockDynamic24x6`` to produce a standard case with ``timeseries.csv``.

    By default, each case uses the same seed value for schedule generation and
    mock dynamics. For example, case 0 uses 20260618 for both, case 1 uses
    20260619 for both, and so on.
    """

    if count <= 0:
        raise ValueError("count must be positive")

    dataset_dir = Path(output_dir)
    _prepare_output_dir(dataset_dir, overwrite=overwrite)

    # 加载系统配置以获取基准随机种子（或使用用户指定值）
    system_config = load_system_config(system_config_path)
    first_seed = _system_seed(system_config) if start_seed is None else int(start_seed)
    # 加载基础配置（含系统默认值合并）
    base_actuation = load_config_with_system_defaults(
        actuation_config_path,
        system_config_path=system_config_path,
    )
    base_mock = load_config_with_system_defaults(
        mock_config_path,
        system_config_path=system_config_path,
    )
    actuation_time_step = _actuation_time_step(base_actuation)

    records: list[dict[str, Any]] = []
    for idx in range(count):
        # 每个案例使用不同的种子：first_seed, first_seed+1, first_seed+2, ...
        global_seed = first_seed + idx
        case_id = f"{case_prefix}_{global_seed}"
        case_dir = dataset_dir / case_id
        mock_config_used = case_dir / "mock_config_used.yaml"

        # 步骤1：用当前种子生成稀疏调度表（actuation_schedule.csv）
        _generate_schedule_case(
            base_config=base_actuation,
            global_seed=global_seed,
            output_dir=case_dir,
        )
        # 步骤2：生成对应的 Mock 配置（覆盖 random_seed），写入临时文件
        _write_mock_config(
            base_config=base_mock,
            global_seed=global_seed,
            time_step=actuation_time_step,
            path=mock_config_used,
        )
        # 步骤3：运行 MockDynamic24x6 仿真生成 timeseries.csv
        result = write_mock_dynamic_case(
            schedule_path=case_dir / "input" / "actuation_schedule.csv",
            config_path=mock_config_used,
            output_dir=case_dir,
            time_step=actuation_time_step,
        )

        # 记录该案例的元数据到索引
        record = {
            "case_index": idx,
            "case_id": case_id,
            "global_seed": global_seed,
            "schedule_seed": global_seed,       # 调度表和 Mock 使用相同种子
            "mock_seed": global_seed,
            "time_step": actuation_time_step,
            "case_dir": str(case_dir),
            "schedule_dir": str(case_dir / "input"),
            "schedule_path": str(case_dir / "input" / "actuation_schedule.csv"),
            "input_schedule_path": str(case_dir / "input" / "actuation_schedule.csv"),
            "case_schedule_path": str(case_dir / "actuation_schedule.csv"),
            "timeseries_path": str(case_dir / "timeseries.csv"),
            "quality_report_path": str(case_dir / "quality_report.json"),
            "mock_config_path": str(mock_config_used),
            "run_success_flag": bool(result["quality_report"].get("run_success_flag", False)),
        }
        records.append(record)
        print(
            f"[{idx + 1:03d}/{count:03d}] {case_id} "
            f"global_seed={global_seed}"
        )

    # 生成数据集的索引文件
    _write_index(dataset_dir, records)
    return records


# 准备输出目录：如果目录已存在且非空且 overwrite=False 则报错；
# 否则若 overwrite=True 则先删除再重建
def _prepare_output_dir(path: Path, *, overwrite: bool) -> None:
    if path.exists() and any(path.iterdir()) and not overwrite:
        raise FileExistsError(
            f"{path} already exists and is not empty; pass --overwrite to regenerate it"
        )
    if path.exists() and overwrite:
        shutil.rmtree(path)
    path.mkdir(parents=True, exist_ok=True)


# 使用指定种子生成单个案例的稀疏调度表
# 对 base_config 做深拷贝后覆盖 random_seed，移除 actuation 级别的种子实现全局种子统一控制
def _generate_schedule_case(
    *,
    base_config: dict[str, Any],  # 基础稀疏调度配置
    global_seed: int,             # 全局随机种子值
    output_dir: Path,             # 输出目录
) -> None:
    config_data = deepcopy(base_config)
    # 在 system 层级设置种子（取代原有系统配置中的种子）
    config_data.setdefault("system", {})["random_seed"] = int(global_seed)
    # 移除 actuation 层级的种子，使调度生成只受系统种子控制
    config_data.setdefault("actuation", {}).pop("random_seed", None)
    if config_data.get("actuation", {}).get("mode") != "sparse_random_groups":
        raise ValueError(
            "ARX sparse24 dataset generation requires actuation.mode=sparse_random_groups"
        )
    generate_from_mapping(config_data, output_dir=output_dir)


# 生成并写入 Mock 动力系统的配置文件（临时 YAML）
# 覆盖 random_seed 后写出到案例目录，供 write_mock_dynamic_case 使用
def _write_mock_config(
    *,
    base_config: dict[str, Any],  # 基础 Mock 配置
    global_seed: int,             # 全局随机种子
    time_step: float,             # 从 actuation YAML 读取的响应采样时间步
    path: Path,                   # 输出的 YAML 文件路径
) -> None:
    config = deepcopy(base_config)
    config.setdefault("system", {})["random_seed"] = int(global_seed)
    mock_section = config.setdefault("mock_dynamic24x6", {})
    mock_section.pop("random_seed", None)
    mock_section["time_step"] = float(time_step)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        yaml.safe_dump(config, sort_keys=False, allow_unicode=True),
        encoding="utf-8",
    )


def _actuation_time_step(config: dict[str, Any]) -> float:
    actuation = config.get("actuation", {}) if isinstance(config, dict) else {}
    time_step = actuation.get("time_step", actuation.get("window_duration", 0.0))
    try:
        value = float(time_step)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"actuation.time_step must be numeric, got {time_step!r}") from exc
    if value <= 0.0:
        window_duration = float(actuation.get("window_duration", 0.0))
        if window_duration <= 0.0:
            raise ValueError("actuation.time_step or actuation.window_duration must be positive")
        return window_duration
    return value


# 生成数据集的索引文件 index.csv 和 index.json
# CSV 格式便于人类阅读和训练模块读取，JSON 格式包含完整元数据
def _write_index(dataset_dir: Path, records: list[dict[str, Any]]) -> None:
    json_path = dataset_dir / "index.json"
    csv_path = dataset_dir / "index.csv"
    json_path.write_text(
        json.dumps({"cases": records}, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    if not records:
        return
    with csv_path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(records[0]))
        writer.writeheader()
        writer.writerows(records)


# 从系统配置中提取 random_seed 值，用作案例种子序列的起点
def _system_seed(config: dict[str, Any]) -> int:
    system = config.get("system", {})
    if "random_seed" not in system:
        raise ValueError("shared system config must define system.random_seed")
    return int(system["random_seed"])


if __name__ == "__main__":
    raise SystemExit(main())
