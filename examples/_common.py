from __future__ import annotations

import subprocess
import sys
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
    return root


def preferred_python() -> Path:
    venv_python = project_root() / ".venv" / "bin" / "python"
    if venv_python.exists() and venv_python.is_file():
        return venv_python
    return Path(sys.executable)


def reexec_with_project_python() -> None:
    python = preferred_python()
    if Path(sys.executable) != python:
        import os

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


def list_dirs(paths: list[Path]) -> None:
    for path in paths:
        print(path.as_posix())


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
