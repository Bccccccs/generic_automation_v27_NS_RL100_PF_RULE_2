from __future__ import annotations

import subprocess
import sys
import os
from pathlib import Path
from typing import Any


ACTION_NAMES = [
    "no_jet_reference",
    "pulse_singlejet",
    "step_singlejet",
    "chirp_keyjets",
    "prbs_demo",
    "pilot_sparse24",
]

ACTION_LABELS = [
    "1) no_jet_reference  - 无喷气参考段",
    "2) pulse_singlejet   - 单喷气脉冲",
    "3) step_singlejet    - 单喷气阶跃",
    "4) chirp_keyjets     - 关键喷气区扫频",
    "5) prbs_demo         - PRBS 伪随机开关",
    "6) pilot_sparse24    - 稀疏随机分组",
]


def project_root() -> Path:
    return Path(__file__).resolve().parents[1]


def configure_project_root() -> Path:
    root = project_root()
    if str(root) not in sys.path:
        sys.path.insert(0, str(root))
    os.chdir(root)
    return root


def preferred_python() -> Path:
    venv_python = project_root() / ".venv" / "bin" / "python"
    if venv_python.exists() and venv_python.is_file():
        return venv_python
    return Path(sys.executable)


def reexec_with_project_python() -> None:
    python = preferred_python()
    if Path(sys.executable) != python:
        os.execv(python.as_posix(), [python.as_posix(), *sys.argv])


def run_module(module: str, *args: str) -> None:
    subprocess.run([preferred_python().as_posix(), "-m", module, *args], cwd=project_root(), check=True)


def choose_action(prompt: str) -> str:
    print(prompt)
    print("\n".join(ACTION_LABELS))
    choice = input("动作编号: ").strip()
    if choice not in {"1", "2", "3", "4", "5", "6"}:
        raise SystemExit(f"无效输入：{choice}。请输入 1-6。")
    return ACTION_NAMES[int(choice) - 1]


def normalize_run_dir(value: str) -> Path:
    if not value:
        raise SystemExit("目录不能为空。")
    path = Path(value)
    if path.is_absolute():
        return path
    if not str(path).startswith("runs/") and path.parts[:1] != ("runs",):
        path = Path("runs") / path
    return path


def find_schedule(case_dir: Path) -> Path:
    input_schedule = case_dir / "input" / "actuation_schedule.csv"
    root_schedule = case_dir / "actuation_schedule.csv"
    if input_schedule.is_file():
        return input_schedule
    if root_schedule.is_file():
        return root_schedule
    raise SystemExit(f"未找到动作表：{input_schedule} 或 {root_schedule}")


def find_timeseries(case_dir: Path) -> Path:
    processed = case_dir / "processed" / "timeseries.csv"
    legacy = case_dir / "timeseries.csv"
    if processed.is_file():
        return processed
    if legacy.is_file():
        return legacy
    raise SystemExit(f"未找到时序数据：{processed} 或 {legacy}")


def discover_case_dirs_with_timeseries(root: Path = Path("runs")) -> list[Path]:
    cases = set()
    for timeseries in root.rglob("timeseries.csv"):
        if "processed" in timeseries.parts and timeseries.parent.name == "processed":
            candidate = timeseries.parent.parent
        else:
            candidate = timeseries.parent
        if is_case_root(candidate):
            cases.add(candidate)
    return sorted(cases)


def discover_case_dirs_with_quality_report(root: Path = Path("runs")) -> list[Path]:
    return sorted(
        path.parent
        for path in root.rglob("quality_report.json")
        if is_case_root(path.parent)
    )


def is_case_root(path: Path) -> bool:
    if any(part in {"legacy", "logs", "figures", "raw_star", "processed", "input"} for part in path.parts):
        return False
    return (
        (path / "case_manifest.yaml").is_file()
        and (path / "actuation_schedule.csv").is_file()
        and (path / "quality_report.json").is_file()
        and ((path / "processed" / "timeseries.csv").is_file() or (path / "timeseries.csv").is_file())
    )


def list_dirs(paths: list[Path]) -> None:
    for path in paths:
        print(path.as_posix())


def choose_path_or_prompt(paths: list[Path], prompt: str = "目录: ") -> Path:
    selection = input(prompt).strip()
    if not selection:
        raise SystemExit("目录不能为空。")
    if selection.isdigit():
        idx = int(selection)
        if idx < 1 or idx > len(paths):
            raise SystemExit(f"无效编号：{selection}")
        return paths[idx - 1]
    return normalize_run_dir(selection)


def list_numbered_dirs(paths: list[Path]) -> None:
    for idx, path in enumerate(paths, start=1):
        print(f"{idx}) {path.as_posix()}")


def prompt_name(prompt: str) -> str:
    value = input(prompt).strip()
    if not value:
        raise SystemExit("名称不能为空。")
    path = Path(value)
    if path.name != value or value in {".", ".."}:
        raise SystemExit(f"名称不能包含路径分隔符：{value}")
    return value


def discover_arx_models() -> list[Path]:
    models_root = Path("runs/arx/models")
    return sorted(
        path
        for path in models_root.glob("*")
        if path.is_dir() and (path / "arx_model.json").is_file()
    )


def choose_arx_model() -> Path:
    models = discover_arx_models()
    if not models:
        raise SystemExit("未找到已训练模型。请先运行：python examples/run_rom_train.py")

    print("当前可用 ARX 模型：")
    for idx, model_dir in enumerate(models, start=1):
        print(f"{idx}) {model_dir.name}  ({model_dir.as_posix()})")

    print("\n请输入模型编号，或直接输入模型名：")
    selection = input("模型: ").strip()
    if not selection:
        raise SystemExit("模型不能为空。")
    if selection.isdigit():
        idx = int(selection)
        if idx < 1 or idx > len(models):
            raise SystemExit(f"无效编号：{selection}")
        return models[idx - 1]

    model_dir = Path("runs/arx/models") / selection
    if not (model_dir / "arx_model.json").is_file():
        raise SystemExit(f"模型不存在：{model_dir}/arx_model.json")
    return model_dir


def read_ccm_config() -> dict[str, Any]:
    import yaml

    path = Path("configs/ccm_runtime.yaml")
    if not path.exists():
        raise SystemExit("CCM 配置文件不存在：configs/ccm_runtime.yaml")
    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    ccm = data.get("ccm") if isinstance(data, dict) else None
    if not isinstance(ccm, dict):
        raise SystemExit("CCM 配置缺失：ccm")

    required_keys = ["sim_path", "starccm_path", "num_cores", "region"]
    for key in required_keys:
        if ccm.get(key) in (None, ""):
            raise SystemExit(f"CCM 配置缺失或为空：ccm.{key}")
    return ccm


def ccm_command_args(
    *,
    sim_path: str,
    out_dir: Path,
    starccm_path: str,
    num_cores: Any,
    region_name: str,
    podkey: str | None = None,
) -> list[str]:
    args = [
        "--sim",
        sim_path,
        "--out",
        out_dir.as_posix(),
        "--starccm-path",
        starccm_path,
        "--np",
        str(num_cores),
        "--region",
        region_name,
    ]
    if podkey:
        args.extend(["--podkey", podkey])
    return args
