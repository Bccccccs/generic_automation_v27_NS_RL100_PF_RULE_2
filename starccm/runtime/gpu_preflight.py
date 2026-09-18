"""GPU run 的单节点实时预检。

只在本次已分配的计算节点上取证，核对 STAR build、平台、设备、作业分配、可见性
重映射、machinefile 目标节点和输入 hash。本模块不提交作业、不做跨节点发现、
不遍历节点、不推断显存是否装得下算例，也不修改调度器设置的
``CUDA_VISIBLE_DEVICES``。

所有外部诊断都用参数列表调用并带超时，捕获返回码/stdout/stderr；超时不是
“设备不存在”的同义词，单独分类记录。诊断只在当前节点运行，不拼接用户原始
shell 片段。真机取证在 G0 解除前仍是 BLOCKED，见
``docs/gpu/20.02-evidence-register.md``。
"""

from __future__ import annotations

import json
import os
import re
import socket
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from starccm.runtime.gpu_config import (
    GPUConfigurationError,
    GPUExecutionConfig,
    gpu_command_tokens,
    gpu_selection_device_count,
    gpu_selection_device_indices,
)
from starccm.runtime.gpu_evidence import sha256_file, utc_now
from starccm.runtime.gpu_qualification import (
    GPUQualificationError,
    load_gpu_qualification,
    parse_driver_rule,
)

DIAGNOSTIC_TIMEOUT_SECONDS = 15.0
OS_RELEASE_PATH = Path("/etc/os-release")

NVIDIA_QUERY_FIELDS = "index,uuid,name,pci.bus_id,memory.total,driver_version,mig.mode.current"
NVIDIA_DEVICE_KEYS = (
    "index",
    "uuid",
    "name",
    "pci_bus_id",
    "memory_total",
    "driver_version",
    "mig_mode_current",
)
SUPPORTED_GPU_VENDORS = ("nvidia", "hygon")

# 海光 HyHAL（hy-smi）：厂商证据来自 docs/gpu/20.02-evidence-register.md 的真机
# 诊断输出（型号 C-3000/BW，8 卡节点）。只用已实测过的 --show*/--json 组合，
# 不假设未验证过的参数拼接（例如把多个 --show* 参数合并成一次调用）。
HYGON_VISIBLE_DEVICES_ENV = "HIP_VISIBLE_DEVICES"
_HYGON_UNIQUE_ID_RE = re.compile(r"^[A-Z][A-Z0-9]{5,19}$")
_HYGON_BUS_LINE_RE = re.compile(
    r"^(?P<pci_bus_id>\S+)\s*-->\s*SN:\s*(?P<serial>\S+)\s*-->\s*OAM ID:\s*(?P<oam_id>\d+)$"
)
_HYGON_DRIVER_VERSION_RE = re.compile(r"Driver Version:\s*(\S+)")
_HYGON_MIG_TABLE_ROW_RE = re.compile(
    r"^(?P<index>\d+)\s+.*\S\s+(?P<mode>\S+)\s*$"
)
_HYGON_PID_BLOCK_RE = re.compile(
    r"PID:\s*(?P<pid>\d+)\s*\n"
    r"(?:.*\n)*?"
    r"\s*PCI BUS:\s*\[(?P<pci_bus>[^\]]*)\]\s*\n"
    r"(?:.*\n)*?"
    r"\s*VRAM USED\(MiB\):\s*(?P<vram_used>\d+)",
)

_EVIDENCE_TEXT_LIMIT = 4000
_SLURM_KEY_VALUE_RE = re.compile(r"(?:^|\s)([A-Za-z][A-Za-z0-9_]*)=(\S*)")
# Slurm 上加速卡的 Gres/TRES 资源名不统一：NVIDIA 站点通常叫 gpu，海光 DCU 站点
# 常见叫 dcu（与本仓库既有 run_python.slurm 的 --gres=dcu:8 一致，见
# docs/STARCCM_GPU.md 海光小节）。两个名字都只做资源计数解析，不代表已经确认
# 卡的厂商——厂商判定仍然完全来自 hy-smi/nvidia-smi 的实时查询。
_SLURM_GRES_ACCELERATOR_NAMES = ("gpu", "dcu")
_TRES_GPU_RE = re.compile(
    r"(?:^|,)gres/(?:" + "|".join(_SLURM_GRES_ACCELERATOR_NAMES) + r")(?::[a-z0-9_]+)?=(\d+)"
)
_GRES_GPU_RE = re.compile(
    r"(?:" + "|".join(_SLURM_GRES_ACCELERATOR_NAMES) + r")(?::[A-Za-z0-9_.-]+)?:(?P<count>\d+)"
    r"(?:\(IDX:(?P<indices>[0-9,]+)\))?"
)
_OS_RELEASE_ID_RE = re.compile(r"^\s*ID\s*=\s*\"?([A-Za-z0-9._-]+)\"?\s*$")
_CUDA_UUID_RE = re.compile(r"^GPU-[0-9A-Za-z-]+$")
_DRIVER_VERSION_NUMERIC_PREFIX_RE = re.compile(r"^\d+(?:\.\d+)*")
# machinefile 支持 Gridview hostname:slots、Open MPI hostname slots=N 和重复裸主机名。
_MACHINEFILE_GRIDVIEW_RE = re.compile(r".+:(\d+)$")
_MACHINEFILE_OPENMPI_RE = re.compile(r"(?:^|\s)slots\s*=\s*(\d+)(?:\s|$)")


class GPUPreflightError(RuntimeError):
    """GPU 预检未通过；异常本身携带已采集的证据，供 runner 写入 sidecar。"""

    def __init__(
        self,
        message: str,
        *,
        failure_code: str = "PREFLIGHT_FAILED",
        evidence: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(message)
        self.failure_code = failure_code
        self.evidence: dict[str, Any] = evidence or {}


@dataclass(frozen=True)
class DiagnosticResult:
    command: tuple[str, ...]
    returncode: int | None
    stdout: str
    stderr: str
    status: str
    duration_seconds: float

    def as_evidence(self) -> dict[str, Any]:
        return {
            "command": [str(item) for item in self.command],
            "returncode": self.returncode,
            "status": self.status,
            "duration_seconds": round(self.duration_seconds, 3),
            "stdout_tail": _tail(self.stdout),
            "stderr_tail": _tail(self.stderr),
        }


@dataclass(frozen=True)
class GPUPreflightResult:
    evidence: dict[str, Any]
    command_selection: str


def preflight_gpu(
    config: GPUExecutionConfig,
    *,
    starccm_path: str,
    num_processes: int,
    node: str,
    scheduler: str,
    scheduler_job_id: str,
    output_dir: Path,
    sim_path: Path,
    schedule_path: Path,
    machinefile_path: Path | None = None,
) -> GPUPreflightResult:
    """仅 GPU run 调用；失败抛 :class:`GPUPreflightError` 并保留证据。

    ``machinefile_path`` 沿用 runner 已解析的结果，只用于确认启动目标就是当前
    节点；本函数不重新解析 Slurm 分配，也不做远端发现。
    """

    if config.backend != "gpu":
        raise GPUPreflightError(
            f"GPU 预检只服务于 compute_backend='gpu'，收到 {config.backend!r}",
            failure_code="BACKEND_MISMATCH",
        )
    try:
        gpu_command_tokens(config)
    except GPUConfigurationError as exc:
        raise GPUPreflightError(str(exc), failure_code="CONFIG_INVALID") from exc

    selection = str(config.selection)
    requested_count = gpu_selection_device_count(selection)
    requested_indices = gpu_selection_device_indices(selection)
    evidence: dict[str, Any] = {
        "state": "PREFLIGHT_RUNNING",
        "preflight_at": utc_now(),
        "host": socket.gethostname(),
        "node": node,
        "output_dir": str(output_dir),
        "request": {
            "backend": config.backend,
            "selection": selection,
            "mpi_processes": num_processes,
            "requested_gpu_count": requested_count,
            "requested_device_indices": list(requested_indices) if requested_indices else None,
            "strict_compatibility": True,
        },
        "diagnostics": [],
        "devices": [],
        "allocation": {},
        "platform": {},
        "inputs": {},
        "star_build": None,
        "star_version_output": None,
    }

    def _fail(message: str, failure_code: str) -> GPUPreflightError:
        evidence["state"] = "BLOCKED"
        evidence["failure_code"] = failure_code
        return GPUPreflightError(message, failure_code=failure_code, evidence=dict(evidence))

    if config.qualification_path is None:
        raise _fail(
            "GPU run 必须提供资格文件（--gpu-qualification）；不做任何默认自动加载",
            "QUALIFICATION_MISSING",
        )
    try:
        qualification = load_gpu_qualification(config.qualification_path)
    except FileNotFoundError as exc:
        raise _fail(f"GPU 资格文件不存在: {config.qualification_path}", "QUALIFICATION_MISSING") from exc
    except GPUQualificationError as exc:
        raise _fail(f"GPU 资格文件校验失败: {exc}", "QUALIFICATION_INVALID") from exc

    launch = qualification["approved_launch"]
    approved_counts = [int(item) for item in launch["approved_gpu_counts"]]
    ranks_per_gpu = int(launch["approved_ranks_per_gpu"])

    if num_processes < 1:
        raise _fail(f"GPU 模式的 STAR 进程数必须 >= 1，收到 {num_processes}", "RANK_GPU_MISMATCH")
    expected_processes = requested_count * ranks_per_gpu
    if num_processes != expected_processes:
        raise _fail(
            f"GPU 选择器 {selection!r} 要求 {requested_count} 张卡、每卡 {ranks_per_gpu} 个 rank，"
            f"即 {expected_processes} 个 STAR 进程，但收到 --np={num_processes}；"
            "--np 是 STAR 进程数，不能沿用 CPU 满核配置",
            "RANK_GPU_MISMATCH",
        )
    if requested_count not in approved_counts:
        raise _fail(
            f"资格文件批准的 GPU 数量为 {approved_counts}，本次请求 {requested_count} 张（{selection!r}）",
            "GPU_COUNT_NOT_APPROVED",
        )
    if scheduler != launch["scheduler"]:
        raise _fail(
            f"资格文件批准的 scheduler 是 {launch['scheduler']!r}，本次使用 {scheduler!r}",
            "LAUNCH_NOT_APPROVED",
        )

    evidence["inputs"] = {
        "qualification_path": str(config.qualification_path),
        "qualification_sha256": sha256_file(config.qualification_path),
        "sim_path": str(sim_path),
        "sim_sha256": sha256_file(sim_path) if Path(sim_path).is_file() else None,
        "schedule_path": str(schedule_path),
        "schedule_sha256": sha256_file(schedule_path) if Path(schedule_path).is_file() else None,
    }
    evidence["star_build_reviewed"] = qualification["star_build"]
    evidence["precision_reviewed"] = qualification["precision"]
    evidence["launch_reviewed"] = dict(launch)

    _verify_star_build(evidence, starccm_path, qualification["star_build"], _fail)
    platform_rows = _verify_platform_identity(evidence, qualification["approved_platforms"], _fail)
    physical_devices = _query_physical_devices(evidence, platform_rows, _fail)
    if scheduler == "slurm":
        evidence["allocation"] = _verify_slurm_allocation(
            evidence,
            job_id=scheduler_job_id,
            node=node,
            requested_count=requested_count,
            _fail=_fail,
        )
    else:
        evidence["allocation"] = {
            "scheduler": scheduler,
            "job_id": scheduler_job_id or None,
            "nodes": [node],
            "node_count": 1,
            "gpu_count": None,
            "gpu_indices": None,
            "gpu_count_source": "manual_exclusive_server_no_allocation_evidence",
        }
    visible_devices = _resolve_visible_devices(
        evidence,
        physical_devices,
        requested_count,
        requested_indices,
        list(launch.get("occupancy_ignore_process_names") or []),
        _fail,
    )
    _verify_device_platform(evidence, visible_devices, platform_rows, _fail)
    _verify_machinefile(evidence, machinefile_path, node, num_processes, _fail)
    _verify_sim_hash(evidence, qualification, _fail)

    evidence["state"] = "PREFLIGHT_PASSED"
    evidence.pop("failure_code", None)
    # 实际下发的选择器与预检核验过的选择器保持一致；不做静默重映射。
    return GPUPreflightResult(evidence=evidence, command_selection=selection)


def _verify_star_build(evidence, starccm_path, star_build, _fail) -> None:
    result = _run([str(starccm_path), "-version"])
    evidence["diagnostics"].append(result.as_evidence())
    if result.status != "ok":
        raise _fail(
            f"无法读取 STAR 版本（{result.status}）: {starccm_path} -version → "
            f"{_tail(result.stderr) or _tail(result.stdout) or '无输出'}",
            "STAR_VERSION_UNAVAILABLE",
        )
    evidence["star_version_output"] = _tail(result.stdout)
    if not _build_appears(star_build, result.stdout):
        raise _fail(
            f"实时 STAR 版本与资格文件不一致：资格文件要求 build {star_build!r}，"
            f"实际 -version 输出为 {_tail(result.stdout)!r}",
            "STAR_BUILD_MISMATCH",
        )
    evidence["star_build"] = star_build


def _build_appears(star_build: str, version_output: str) -> bool:
    """按边界匹配 build，避免 20.02.00 命中 20.02.007-R8。"""

    return (
        re.search(
            rf"(?<![0-9A-Za-z.]){re.escape(star_build)}(?![0-9A-Za-z])",
            version_output,
        )
        is not None
    )


def _verify_platform_identity(evidence, approved_platforms, _fail) -> list[dict[str, Any]]:
    uname_result = _run(["uname", "-srm"])
    evidence["diagnostics"].append(uname_result.as_evidence())
    if uname_result.status != "ok":
        raise _fail(f"uname -srm 执行失败（{uname_result.status}）", "PLATFORM_NOT_APPROVED")
    tokens = uname_result.stdout.split()
    live_os = tokens[0] if tokens else ""
    live_arch = tokens[-1] if len(tokens) > 1 else ""
    live_kernel = tokens[1] if len(tokens) > 2 else ""
    live_os_id = _read_os_release_id()
    evidence["platform"] = {
        "os": live_os,
        "kernel": live_kernel,
        "cpu_arch": live_arch,
        "os_id": live_os_id,
        "os_release_path": str(OS_RELEASE_PATH),
    }

    reasons: list[str] = []
    vendor_rows: list[dict[str, Any]] = []
    for row in approved_platforms:
        row_reasons: list[str] = []
        if str(row["os"]).lower() != live_os.lower():
            row_reasons.append(f"os 要求 {row['os']!r}，实际 {live_os!r}")
        if str(row["cpu_arch"]).lower() != live_arch.lower():
            row_reasons.append(f"cpu_arch 要求 {row['cpu_arch']!r}，实际 {live_arch!r}")
        required_os_id = row.get("os_id")
        if required_os_id:
            if live_os_id is None:
                row_reasons.append(
                    f"资格文件要求 os_id={required_os_id!r}，但 {OS_RELEASE_PATH} 不可读或未声明 ID"
                )
            elif str(required_os_id).lower() != live_os_id.lower():
                row_reasons.append(f"os_id 要求 {required_os_id!r}，实际 {live_os_id!r}")
        if row_reasons:
            reasons.append(f"[{row['gpu_vendor']}/{row['gpu_model']}] " + "；".join(row_reasons))
            continue
        if str(row["gpu_vendor"]).lower() in SUPPORTED_GPU_VENDORS:
            vendor_rows.append(row)
        else:
            reasons.append(
                f"[{row['gpu_vendor']}/{row['gpu_model']}] 平台身份匹配，但厂商 "
                f"{row['gpu_vendor']!r} 尚无经核验的只读设备查询命令（见 B-03），该平台 BLOCKED"
            )
    if not vendor_rows:
        raise _fail(
            "当前节点平台不在资格文件批准范围内：" + ("；".join(reasons) or "无可用批准行"),
            "VENDOR_BLOCKED" if reasons and all("BLOCKED" in item for item in reasons) else "PLATFORM_NOT_APPROVED",
        )
    evidence["approved_platform_candidates"] = [dict(row) for row in vendor_rows]
    return vendor_rows


def _query_physical_devices(evidence, platform_rows, _fail) -> list[dict[str, Any]]:
    """读取当前节点物理可见的 GPU；不做任何资格判断。

    按资格文件里实际批准的厂商分发到对应的只读查询命令；只查询在
    ``platform_rows``（已通过厂商白名单过滤）中出现过的厂商，不会去探测未批准
    的厂商工具。
    """

    approved_vendors = sorted({str(row["gpu_vendor"]).lower() for row in platform_rows})
    devices: list[dict[str, Any]] = []
    for vendor in approved_vendors:
        if vendor == "nvidia":
            devices.extend(_query_nvidia_devices(evidence, _fail))
        elif vendor == "hygon":
            devices.extend(_query_hygon_devices(evidence, _fail))
        else:  # pragma: no cover - 上游已按 SUPPORTED_GPU_VENDORS 过滤
            raise _fail(f"厂商 {vendor!r} 没有已注册的设备查询实现", "VENDOR_BLOCKED")
    evidence["devices"] = devices
    if not devices:
        raise _fail(
            "当前节点没有可见的 GPU 设备；容器内可见性不能由宿主机代替",
            "GPU_DEVICE_UNAVAILABLE",
        )
    return devices


def _query_nvidia_devices(evidence, _fail) -> list[dict[str, Any]]:
    result = _run(
        [
            "nvidia-smi",
            f"--query-gpu={NVIDIA_QUERY_FIELDS}",
            "--format=csv,noheader",
        ]
    )
    evidence["diagnostics"].append(result.as_evidence())
    if result.status == "timeout":
        raise _fail(
            f"nvidia-smi 在 {DIAGNOSTIC_TIMEOUT_SECONDS} 秒内未返回；超时不等于设备不存在，"
            "需要人工在该节点复核驱动与工具状态",
            "GPU_TOOL_TIMEOUT",
        )
    if result.status != "ok":
        raise _fail(
            f"nvidia-smi 执行失败（status={result.status}, returncode={result.returncode}）: "
            f"{_tail(result.stderr) or _tail(result.stdout) or '无输出'}",
            "GPU_TOOL_FAILED",
        )
    return _parse_nvidia_devices(result.stdout, _fail)


def _query_hygon_devices(evidence, _fail) -> list[dict[str, Any]]:
    """用 hy-smi 枚举海光 DCU/HCU 设备。

    每个 ``--show*`` 查询单独调用并各自记录诊断证据；不假设未实测过的参数
    组合方式。字段来源（均为真机实测，见 docs/gpu/20.02-evidence-register.md）：
    ``--showuniqueid --json`` → uuid；``--showbus --json`` → pci_bus_id；
    ``--showproductname --json`` → name；``--showmeminfo vram --json`` →
    memory_total；``--showdriverversion``（不支持 --json）→ 全卡共用的驱动版本；
    ``--mig``（不支持 --json）→ MIG 状态所在的汇总表格。
    """

    unique_ids = _hygon_json_query(evidence, ["hy-smi", "--showuniqueid", "--json"], _fail)
    bus_info = _hygon_json_query(evidence, ["hy-smi", "--showbus", "--json"], _fail)
    product_names = _hygon_json_query(evidence, ["hy-smi", "--showproductname", "--json"], _fail)
    mem_info = _hygon_json_query(evidence, ["hy-smi", "--showmeminfo", "vram", "--json"], _fail)
    driver_version = _hygon_driver_version(evidence, _fail)
    mig_modes = _hygon_mig_modes(evidence, _fail)

    card_keys = sorted(unique_ids)
    if not card_keys:
        return []
    for other_name, other in (
        ("--showbus", bus_info),
        ("--showproductname", product_names),
        ("--showmeminfo vram", mem_info),
    ):
        if sorted(other) != card_keys:
            raise _fail(
                f"hy-smi --showuniqueid 返回的设备集合 {card_keys} 与 {other_name} 返回的 "
                f"{sorted(other)} 不一致；不猜测字段含义",
                "GPU_TOOL_FAILED",
            )

    devices: list[dict[str, Any]] = []
    for card_key in card_keys:
        card_match = re.fullmatch(r"card(\d+)", card_key)
        if card_match is None:
            raise _fail(
                f"hy-smi --json 输出的设备键无法解析: {card_key!r}；不猜测字段含义",
                "GPU_TOOL_FAILED",
            )
        index = int(card_match.group(1))
        uuid = str(unique_ids[card_key].get("Unique ID", "")).strip()
        if not uuid:
            raise _fail(f"hy-smi --showuniqueid 的 {card_key} 缺少 Unique ID", "GPU_TOOL_FAILED")

        bus_match = _HYGON_BUS_LINE_RE.fullmatch(str(bus_info[card_key].get("PCI Bus", "")).strip())
        if bus_match is None:
            raise _fail(
                f"hy-smi --showbus 的 {card_key} 输出无法解析: {bus_info[card_key]!r}",
                "GPU_TOOL_FAILED",
            )
        pci_bus_id = bus_match.group("pci_bus_id")

        product = product_names[card_key]
        series = str(product.get("Card Series", "")).strip()
        vendor_name = str(product.get("Card Vendor", "")).strip()
        if not series or not vendor_name:
            raise _fail(f"hy-smi --showproductname 的 {card_key} 缺少 Card Series/Card Vendor", "GPU_TOOL_FAILED")
        name = f"{vendor_name} {series}"

        mem = mem_info[card_key]
        memory_total_raw = str(mem.get("vram Total Memory (MiB)", "")).strip()
        if not memory_total_raw.isdigit():
            raise _fail(
                f"hy-smi --showmeminfo vram 的 {card_key} 总显存无法解析: {mem!r}",
                "GPU_TOOL_FAILED",
            )
        memory_total = f"{memory_total_raw} MiB"

        devices.append(
            {
                "index": index,
                "uuid": uuid,
                "name": name,
                "pci_bus_id": pci_bus_id,
                "memory_total": memory_total,
                "driver_version": driver_version,
                "mig_mode_current": mig_modes.get(index, "unknown"),
                "gpu_vendor": "hygon",
            }
        )
    devices.sort(key=lambda device: device["index"])
    return devices


def _hygon_json_query(evidence, command, _fail) -> dict[str, Any]:
    result = _run(command)
    evidence["diagnostics"].append(result.as_evidence())
    if result.status == "timeout":
        raise _fail(
            f"{' '.join(command)} 在 {DIAGNOSTIC_TIMEOUT_SECONDS} 秒内未返回；超时不等于设备不存在，"
            "需要人工在该节点复核驱动与工具状态",
            "GPU_TOOL_TIMEOUT",
        )
    if result.status != "ok":
        raise _fail(
            f"{' '.join(command)} 执行失败（status={result.status}, returncode={result.returncode}）: "
            f"{_tail(result.stderr) or _tail(result.stdout) or '无输出'}",
            "GPU_TOOL_FAILED",
        )
    try:
        payload = json.loads(result.stdout)
    except json.JSONDecodeError as exc:
        raise _fail(
            f"{' '.join(command)} 的 JSON 输出无法解析: {_tail(result.stdout)!r}（{exc}）",
            "GPU_TOOL_FAILED",
        ) from exc
    if not isinstance(payload, dict):
        raise _fail(
            f"{' '.join(command)} 的 JSON 输出顶层不是对象: {type(payload).__name__}",
            "GPU_TOOL_FAILED",
        )
    return payload


def _hygon_driver_version(evidence, _fail) -> str:
    result = _run(["hy-smi", "--showdriverversion"])
    evidence["diagnostics"].append(result.as_evidence())
    if result.status == "timeout":
        raise _fail(
            f"hy-smi --showdriverversion 在 {DIAGNOSTIC_TIMEOUT_SECONDS} 秒内未返回",
            "GPU_TOOL_TIMEOUT",
        )
    if result.status != "ok":
        raise _fail(
            f"hy-smi --showdriverversion 执行失败（status={result.status}）: "
            f"{_tail(result.stderr) or _tail(result.stdout) or '无输出'}",
            "GPU_TOOL_FAILED",
        )
    match = _HYGON_DRIVER_VERSION_RE.search(result.stdout)
    if match is None:
        raise _fail(
            f"hy-smi --showdriverversion 输出无法解析: {_tail(result.stdout)!r}",
            "GPU_TOOL_FAILED",
        )
    return match.group(1)


def _hygon_mig_modes(evidence, _fail) -> dict[int, str]:
    """解析 ``hy-smi --mig`` 汇总表格里每张卡的 Mode 列。

    该表格与默认 ``hy-smi`` 输出同构，不支持 ``--json``。观测到的正常（未启用
    MIG）取值为 ``Normal``；这里把它归一化成与 NVIDIA 分支相同的 'disabled'
    词汇，其余取值原样保留，交给通用的 MIG 校验按“不在已知安全取值内”拒绝。
    """

    result = _run(["hy-smi", "--mig"])
    evidence["diagnostics"].append(result.as_evidence())
    if result.status == "timeout":
        raise _fail(f"hy-smi --mig 在 {DIAGNOSTIC_TIMEOUT_SECONDS} 秒内未返回", "GPU_TOOL_TIMEOUT")
    if result.status != "ok":
        raise _fail(
            f"hy-smi --mig 执行失败（status={result.status}）: "
            f"{_tail(result.stderr) or _tail(result.stdout) or '无输出'}",
            "GPU_TOOL_FAILED",
        )
    modes: dict[int, str] = {}
    for raw_line in result.stdout.splitlines():
        line = raw_line.strip()
        if not line or not line[0].isdigit():
            continue
        match = _HYGON_MIG_TABLE_ROW_RE.match(line)
        if match is None:
            continue
        index = int(match.group("index"))
        mode = match.group("mode")
        modes[index] = "disabled" if mode.strip().lower() == "normal" else mode
    return modes


def _resolve_visible_devices(
    evidence, physical, requested_count, requested_indices, ignore_process_names, _fail
):
    """解析本次作业实际可用的 GPU，并给出 STAR/CUDA 本地序号。

    宿主机物理编号、容器内局部编号和 Slurm 分配的编号可能不同。显式卡列表使用
    的是 STAR/CUDA（或海光对应）本地序号，因此这里必须做重映射核验，且绝不
    修改可见性环境变量本身。

    可见性环境变量随厂商而不同：NVIDIA 用 ``CUDA_VISIBLE_DEVICES``，海光
    HyHAL 用 ``HIP_VISIBLE_DEVICES``（本次改造范围内不支持混合厂商，因此按
    物理设备的唯一厂商选择对应变量）。
    """

    vendor = str(physical[0]["gpu_vendor"]).lower() if physical else "nvidia"
    env_name = HYGON_VISIBLE_DEVICES_ENV if vendor == "hygon" else "CUDA_VISIBLE_DEVICES"
    uuid_re = _HYGON_UNIQUE_ID_RE if vendor == "hygon" else _CUDA_UUID_RE
    env_raw = os.environ.get(env_name)
    restricted, form = _restrict_by_visible_devices_env(physical, env_raw, env_name, uuid_re, _fail)
    allocated_indices = evidence["allocation"].get("gpu_indices")
    if allocated_indices is not None:
        allocated = _devices_by_host_index(physical, allocated_indices, _fail)
        if restricted is None:
            visible, source = allocated, "slurm_gres_idx"
        elif [device["uuid"] for device in allocated] == [device["uuid"] for device in restricted]:
            visible, source = allocated, f"slurm_gres_idx+{env_name}"
        else:
            raise _fail(
                f"Slurm 分配的设备编号 {allocated_indices} 与 {env_name}={env_raw!r} "
                f"指向的设备不一致（分配 UUID {[d['uuid'] for d in allocated]}，"
                f"环境可见 UUID {[d['uuid'] for d in restricted]}）；"
                "无法确认各 rank 实际会使用哪些卡",
                "ALLOCATION_MISMATCH",
            )
    elif restricted is not None:
        visible, source = restricted, env_name
    else:
        allocated_count = evidence["allocation"].get("gpu_count")
        if isinstance(allocated_count, int) and len(physical) > allocated_count:
            raise _fail(
                f"作业分配了 {allocated_count} 张 GPU，但节点物理可见 {len(physical)} 张，"
                "且既没有 Gres IDX 也没有 CUDA_VISIBLE_DEVICES 指明具体是哪几张；"
                "无法证明本次 rank 会落在已分配的卡上",
                "ALLOCATION_MISMATCH",
            )
        visible, source = list(physical), "all_physical_devices"

    visible = [dict(device, local_index=position) for position, device in enumerate(visible)]
    uuids = [device["uuid"] for device in visible]
    if len(set(uuids)) != len(uuids):
        duplicated = sorted({uuid for uuid in uuids if uuids.count(uuid) > 1})
        raise _fail(
            f"本次可见设备集合出现重复的物理卡 {duplicated}（来源 {source}）；"
            "一张卡不能被算作多张，否则 rank 会共用同一设备",
            "GPU_DEVICE_UNAVAILABLE",
        )
    evidence["devices"] = visible
    evidence["visibility"] = {
        "source": source,
        "form": form,
        "visible_devices_env": env_name,
        "visible_devices_env_value": env_raw,
        "physical_device_count": len(physical),
        "physical_uuids": [device["uuid"] for device in physical],
        "visible_device_count": len(visible),
        "visible_uuids": [device["uuid"] for device in visible],
        "mixed_physical_models": len({device["name"] for device in physical}) > 1,
    }
    if not visible:
        raise _fail(
            f"本次作业没有可用 GPU：物理设备 {len(physical)} 张，但可见集合为空"
            f"（{env_name}={env_raw!r}）；不占用其他作业的设备",
            "GPU_DEVICE_UNAVAILABLE",
        )
    if len(visible) < requested_count:
        raise _fail(
            f"选择器要求 {requested_count} 张 GPU，但本次作业实际可见 {len(visible)} 张"
            f"（节点物理 {len(physical)} 张，可见来源 {source}）；不静默少用卡",
            "GPU_DEVICE_UNAVAILABLE",
        )
    if requested_indices is not None:
        out_of_range = [index for index in requested_indices if index >= len(visible)]
        if out_of_range:
            raise _fail(
                f"显式选择的设备号 {out_of_range} 超出本次可见范围 "
                f"0..{len(visible) - 1}；显式卡列表使用 STAR/CUDA 本地序号，"
                f"不是宿主机物理编号（可见来源 {source}，物理编号 "
                f"{[device['index'] for device in visible]}）",
                "GPU_DEVICE_UNAVAILABLE",
            )
    if requested_indices is not None:
        target_devices = [visible[index] for index in requested_indices]
    else:
        target_devices = visible[:requested_count]
    _verify_devices_not_in_use(evidence, target_devices, ignore_process_names, _fail)
    return visible


def _verify_devices_not_in_use(evidence, target_devices, ignore_process_names, _fail) -> None:
    """只读检查本次要用的 GPU 上是否已有其他计算进程。

    只针对本次真正会使用的设备，不检查节点上其他卡。这只是冲突检查，不是原子
    调度器：通过不代表启动瞬间仍然空闲。共享节点上“看起来空闲”的卡不能直接
    占用，manual 模式尤其依赖本检查。

    ``ignore_process_names`` 来自资格文件的
    ``approved_launch.occupancy_ignore_process_names``，用于站点级常驻代理
    （例如 MPS server、DCGM）；必须是人工核验并写明依据的名单，被忽略的进程
    仍会记录在证据里。
    """

    if not target_devices:
        return
    vendor = str(target_devices[0]["gpu_vendor"]).lower()
    if vendor == "hygon":
        _verify_hygon_devices_not_in_use(evidence, target_devices, ignore_process_names, _fail)
        return
    _verify_nvidia_devices_not_in_use(evidence, target_devices, ignore_process_names, _fail)


def _verify_nvidia_devices_not_in_use(evidence, target_devices, ignore_process_names, _fail) -> None:
    result = _run(
        [
            "nvidia-smi",
            "--query-compute-apps=gpu_uuid,pid,process_name,used_memory",
            "--format=csv,noheader",
        ]
    )
    evidence["diagnostics"].append(result.as_evidence())
    if result.status == "timeout":
        raise _fail(
            f"nvidia-smi 占用查询在 {DIAGNOSTIC_TIMEOUT_SECONDS} 秒内未返回；"
            "无法确认目标设备是否空闲",
            "GPU_TOOL_TIMEOUT",
        )
    if result.status != "ok":
        raise _fail(
            f"nvidia-smi 占用查询失败（status={result.status}, "
            f"returncode={result.returncode}）: "
            f"{_tail(result.stderr) or _tail(result.stdout) or '无输出'}",
            "GPU_TOOL_FAILED",
        )

    target_uuids = {device["uuid"] for device in target_devices}
    ignored_names = [str(name).lower() for name in ignore_process_names if str(name).strip()]
    busy: list[str] = []
    ignored: list[str] = []
    for raw_line in result.stdout.splitlines():
        line = raw_line.strip()
        if not line:
            continue
        fields = [field.strip() for field in line.split(",")]
        if len(fields) != 4:
            raise _fail(
                f"nvidia-smi 占用查询输出无法解析: {raw_line!r}；不猜测字段含义",
                "GPU_TOOL_FAILED",
            )
        gpu_uuid, pid, process_name, used_memory = fields
        if gpu_uuid not in target_uuids:
            continue
        entry = f"{gpu_uuid}(pid={pid}, {process_name}, {used_memory})"
        if any(name in process_name.lower() for name in ignored_names):
            ignored.append(entry)
            continue
        busy.append(entry)
    evidence["device_occupancy"] = {
        "checked_uuids": sorted(target_uuids),
        "busy": busy,
        "ignored": ignored,
        "ignore_process_names": list(ignore_process_names),
    }
    if busy:
        raise _fail(
            f"本次要使用的 GPU 上已有其他计算进程: {'; '.join(busy)}；"
            "不得占用其他作业的显卡，请在正确的作业上下文或独占服务器上重试",
            "DEVICE_IN_USE",
        )


def _verify_hygon_devices_not_in_use(evidence, target_devices, ignore_process_names, _fail) -> None:
    """用 ``hy-smi --showpids`` 只读检查目标 HCU 上是否已有其他计算进程。

    该命令不支持 ``--json``，也不上报进程名，因此按 PCI 总线号匹配目标设备
    （--showbus 已核验过每卡的总线号唯一），并读本机 ``/proc/<pid>/comm``
    解析进程名用于 ``ignore_process_names`` 白名单匹配；读不到就当作未知
    进程名（不会被白名单命中，按“忙”处理，不放宽判定）。
    """

    result = _run(["hy-smi", "--showpids"])
    evidence["diagnostics"].append(result.as_evidence())
    if result.status == "timeout":
        raise _fail(
            f"hy-smi --showpids 在 {DIAGNOSTIC_TIMEOUT_SECONDS} 秒内未返回；"
            "无法确认目标设备是否空闲",
            "GPU_TOOL_TIMEOUT",
        )
    if result.status != "ok":
        raise _fail(
            f"hy-smi --showpids 执行失败（status={result.status}, "
            f"returncode={result.returncode}）: "
            f"{_tail(result.stderr) or _tail(result.stdout) or '无输出'}",
            "GPU_TOOL_FAILED",
        )

    target_bus_ids = {device["pci_bus_id"] for device in target_devices}
    ignored_names = [str(name).lower() for name in ignore_process_names if str(name).strip()]
    busy: list[str] = []
    ignored: list[str] = []
    text = result.stdout if result.stdout.endswith("\n") else result.stdout + "\n"
    for match in _HYGON_PID_BLOCK_RE.finditer(text):
        pid = match.group("pid")
        pci_bus_entries = [item.strip().strip("'\"") for item in match.group("pci_bus").split(",") if item.strip()]
        hit_bus_ids = [bus_id for bus_id in pci_bus_entries if bus_id in target_bus_ids]
        if not hit_bus_ids:
            continue
        process_name = _read_proc_comm(pid)
        entry = f"{sorted(hit_bus_ids)}(pid={pid}, {process_name or 'unknown'}, {match.group('vram_used')}MiB)"
        if process_name and any(name in process_name.lower() for name in ignored_names):
            ignored.append(entry)
            continue
        busy.append(entry)
    evidence["device_occupancy"] = {
        "checked_pci_bus_ids": sorted(target_bus_ids),
        "busy": busy,
        "ignored": ignored,
        "ignore_process_names": list(ignore_process_names),
    }
    if busy:
        raise _fail(
            f"本次要使用的 GPU 上已有其他计算进程: {'; '.join(busy)}；"
            "不得占用其他作业的显卡，请在正确的作业上下文或独占服务器上重试",
            "DEVICE_IN_USE",
        )


def _read_proc_comm(pid: str) -> str:
    """只读取本机 ``/proc/<pid>/comm``；进程不存在或不可读时返回空字符串。"""

    try:
        return Path(f"/proc/{pid}/comm").read_text(encoding="utf-8", errors="replace").strip()
    except OSError:
        return ""


def _restrict_by_visible_devices_env(physical, env_raw, env_name, uuid_re, _fail):
    """按厂商对应的可见性环境变量过滤物理设备；未设置时返回 (None, None)。

    ``env_name``/``uuid_re`` 由调用方按物理设备的厂商选定（NVIDIA 用
    ``CUDA_VISIBLE_DEVICES`` + ``GPU-<uuid>`` 形式，海光用
    ``HIP_VISIBLE_DEVICES`` + hy-smi 的 Unique ID 形式）。重复条目会让同一张
    物理卡被枚举成多个逻辑设备，等于用一张卡冒充多卡，因此直接拒绝而不是去重
    后放行。
    """

    if env_raw is None:
        return None, None
    entries = [entry.strip() for entry in env_raw.split(",") if entry.strip()]
    if not entries:
        return [], "empty"
    duplicates = sorted({entry for entry in entries if entries.count(entry) > 1})
    if duplicates:
        raise _fail(
            f"{env_name}={env_raw!r} 含重复条目 {duplicates}；"
            "同一张物理卡不能被算作多张可见卡",
            "GPU_DEVICE_UNAVAILABLE",
        )
    if all(uuid_re.fullmatch(entry) for entry in entries):
        by_uuid = {device["uuid"]: device for device in physical}
        selected: list[dict[str, Any]] = []
        for entry in entries:
            device = by_uuid.get(entry)
            if device is None:
                raise _fail(
                    f"{env_name} 指定的 UUID {entry!r} 不在当前节点物理设备 "
                    f"{sorted(by_uuid)} 内",
                    "GPU_DEVICE_UNAVAILABLE",
                )
            selected.append(device)
        return selected, "uuid"
    selected = []
    by_index = {device["index"]: device for device in physical}
    for entry in entries:
        if not entry.isdigit():
            raise _fail(
                f"{env_name} 含无法解析的条目 {entry!r}；"
                "只接受设备编号或设备 UUID/Unique ID 形式",
                "GPU_DEVICE_UNAVAILABLE",
            )
        device = by_index.get(int(entry))
        if device is None:
            raise _fail(
                f"{env_name} 指定的设备编号 {entry} 不在当前节点物理设备 "
                f"{sorted(by_index)} 内",
                "GPU_DEVICE_UNAVAILABLE",
            )
        selected.append(device)
    return selected, "index"


def _devices_by_host_index(physical, indices, _fail) -> list[dict[str, Any]]:
    by_index = {device["index"]: device for device in physical}
    selected: list[dict[str, Any]] = []
    for index in indices:
        device = by_index.get(int(index))
        if device is None:
            raise _fail(
                f"Slurm 分配的设备编号 {index} 在当前节点物理设备 {sorted(by_index)} 中不存在",
                "ALLOCATION_MISMATCH",
            )
        selected.append(device)
    return selected


def _verify_device_platform(evidence, visible_devices, platform_rows, _fail) -> dict[str, Any]:
    """在本次实际可见的设备上核对 MIG、型号与驱动；混合型号需要独立资格证据。"""

    mig_enabled = [
        device["uuid"]
        for device in visible_devices
        if str(device.get("mig_mode_current", "")).strip().lower()
        not in ("disabled", "[not supported]", "[n/a]")
    ]
    if mig_enabled:
        raise _fail(
            f"设备 {mig_enabled} 启用了 MIG；MIG 需要独立资格证据，本次未开放。"
            "注意 MIG 下计算进程上报的是 MIG-* 子设备 UUID，占用检查无法映射到父卡",
            "MIG_NOT_APPROVED",
        )

    reasons: list[str] = []
    for row in platform_rows:
        mismatches = _device_platform_mismatches(visible_devices, row)
        if not mismatches:
            evidence["approved_platform"] = dict(row)
            return row
        reasons.append(f"[{row['gpu_vendor']}/{row['gpu_model']}] " + "；".join(mismatches))
    raise _fail(
        "本次可见设备不匹配资格文件里任何批准平台行：" + "；".join(reasons),
        "PLATFORM_NOT_APPROVED",
    )


def _device_platform_mismatches(visible_devices, platform_row) -> list[str]:
    model = str(platform_row["gpu_model"]).lower()
    operator, required_driver = parse_driver_rule(str(platform_row["driver_requirement"]))
    mismatches: list[str] = []
    for device in visible_devices:
        if model not in str(device["name"]).lower():
            mismatches.append(
                f"设备 {device['index']}（本地序号 {device['local_index']}）型号 "
                f"{device['name']!r} 不在批准范围 {platform_row['gpu_model']!r}"
            )
            continue
        driver = str(device["driver_version"])
        numeric_prefix = _DRIVER_VERSION_NUMERIC_PREFIX_RE.match(driver)
        if numeric_prefix is None:
            mismatches.append(
                f"设备 {device['index']} 的驱动版本 {driver!r} 无法解析，不能与 "
                f"{platform_row['driver_requirement']!r} 比较"
            )
            continue
        # 只取数值点分前缀参与比较（例如海光 "6.3.31-V1.5.0a" → "6.3.31"）；
        # 完整原始字符串仍保留在证据里，不影响审计。
        actual = tuple(int(part) for part in numeric_prefix.group().split("."))
        satisfied = actual >= required_driver if operator == ">=" else actual == required_driver
        if not satisfied:
            mismatches.append(
                f"设备 {device['index']} 驱动 {driver} 不满足批准要求 "
                f"{platform_row['driver_requirement']}"
            )
    if mismatches:
        mismatches.append(
            "首批配置要求本次使用的 GPU 同型号且驱动达标；不自动安装驱动或 CUDA"
        )
    return mismatches


def _verify_machinefile(evidence, machinefile_path, node, num_processes, _fail) -> None:
    """GPU 模式的 machinefile 只允许指向当前节点，且 slot 数覆盖本次 rank 数。"""

    if machinefile_path is None:
        evidence["machinefile"] = None
        return
    path = Path(machinefile_path)
    if not path.is_file():
        raise _fail(f"GPU 模式的 machinefile 不存在: {path}", "MACHINEFILE_NOT_SINGLE_NODE")
    hosts, slots = _parse_machinefile(path, _fail)
    evidence["machinefile"] = {
        "path": str(path),
        "hosts": hosts,
        "slots": slots,
    }
    foreign = [host for host in hosts if _normalize_host(host) != _normalize_host(node)]
    if foreign:
        raise _fail(
            f"GPU 模式的 machinefile {path} 指向当前节点 {node!r} 之外的主机 {foreign}；"
            "本次改造固定单节点，不得让 rank 落到未分配 GPU 的节点",
            "MACHINEFILE_NOT_SINGLE_NODE",
        )
    if slots < num_processes:
        raise _fail(
            f"machinefile {path} 只提供 {slots} 个 slot，但本次 GPU run 需要 "
            f"{num_processes} 个 STAR 进程",
            "MACHINEFILE_SLOTS_INSUFFICIENT",
        )


def _parse_machinefile(path: Path, _fail) -> tuple[list[str], int]:
    hosts: list[str] = []
    slots = 0
    for line_number, raw_line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        line = raw_line.split("#", 1)[0].strip()
        if not line:
            continue
        first_token = line.split()[0]
        gridview = _MACHINEFILE_GRIDVIEW_RE.fullmatch(first_token)
        openmpi = _MACHINEFILE_OPENMPI_RE.search(line)
        if gridview is not None:
            line_slots = int(gridview.group(1))
            host = first_token.rsplit(":", 1)[0]
        elif openmpi is not None:
            line_slots = int(openmpi.group(1))
            host = first_token
        else:
            line_slots = 1
            host = first_token
        if line_slots < 1:
            raise _fail(
                f"machinefile {path} 第 {line_number} 行 slot 数非法: {raw_line!r}",
                "MACHINEFILE_SLOTS_INSUFFICIENT",
            )
        if host not in hosts:
            hosts.append(host)
        slots += line_slots
    if slots == 0:
        raise _fail(f"machinefile {path} 没有任何主机条目", "MACHINEFILE_NOT_SINGLE_NODE")
    return hosts, slots


def _verify_slurm_allocation(evidence, *, job_id, node, requested_count, _fail) -> dict[str, Any]:
    if not str(job_id).strip():
        raise _fail(
            "GPU 模式在 slurm 下必须提供作业 ID（--slurm-job-id 或 $SLURM_JOB_ID）",
            "ALLOCATION_MISMATCH",
        )
    job_result = _run(["scontrol", "show", "job", "-o", str(job_id)])
    evidence["diagnostics"].append(job_result.as_evidence())
    if job_result.status != "ok":
        raise _fail(
            f"scontrol show job 执行失败（{job_result.status}）: {_tail(job_result.stderr)}",
            "ALLOCATION_MISMATCH",
        )
    job = dict(_SLURM_KEY_VALUE_RE.findall(job_result.stdout))
    state = job.get("JobState", "")
    if state != "RUNNING":
        raise _fail(
            f"Slurm 作业 {job_id} 状态为 {state or 'unknown'}，不是 RUNNING；"
            "runner 只消费已存在的分配，不申请资源",
            "ALLOCATION_MISMATCH",
        )
    node_expr = job.get("NodeList", "")
    if not node_expr or node_expr == "(null)":
        raise _fail(f"Slurm 作业 {job_id} 没有 NodeList", "ALLOCATION_MISMATCH")
    hosts_result = _run(["scontrol", "show", "hostnames", node_expr])
    evidence["diagnostics"].append(hosts_result.as_evidence())
    if hosts_result.status != "ok":
        raise _fail(
            f"scontrol show hostnames 执行失败（{hosts_result.status}）: {_tail(hosts_result.stderr)}",
            "ALLOCATION_MISMATCH",
        )
    nodes = [line.strip() for line in hosts_result.stdout.splitlines() if line.strip()]
    if len(nodes) != 1:
        raise _fail(
            f"Slurm 作业 {job_id} 分配了 {len(nodes)} 个节点 {nodes}；"
            "本次改造固定单节点，不实现跨节点 GPU",
            "ALLOCATION_MISMATCH",
        )
    if _normalize_host(nodes[0]) != _normalize_host(node):
        raise _fail(
            f"当前节点 {node!r} 不属于 Slurm 作业 {job_id} 的分配 {nodes}；"
            "不得占用其他作业的 GPU",
            "NODE_MISMATCH",
        )
    gpu_count, gpu_indices = _allocated_gpus(job)
    if gpu_count is None:
        raise _fail(
            f"Slurm 作业 {job_id} 的分配中没有加速卡资源（Gres/TRES 均无 "
            f"gres/{{{'|'.join(_SLURM_GRES_ACCELERATOR_NAMES)}}}）；"
            "节点上有卡不等于本次作业已获授权",
            "ALLOCATION_MISMATCH",
        )
    if gpu_count < requested_count:
        raise _fail(
            f"Slurm 作业 {job_id} 只分配了 {gpu_count} 张 GPU，本次请求 {requested_count} 张",
            "ALLOCATION_MISMATCH",
        )
    if gpu_indices is not None and len(gpu_indices) < requested_count:
        raise _fail(
            f"Slurm 作业 {job_id} 的 Gres IDX 只列出 {gpu_indices}，"
            f"不足本次请求的 {requested_count} 张",
            "ALLOCATION_MISMATCH",
        )
    return {
        "scheduler": "slurm",
        "job_id": str(job_id),
        "job_state": state,
        "nodes": nodes,
        "node_count": len(nodes),
        "gpu_count": gpu_count,
        "gpu_indices": gpu_indices,
        "gpu_count_source": "Gres" if job.get("Gres") else "TRES",
        "num_cpus": job.get("NumCPUs"),
        "num_tasks": job.get("NumTasks"),
        "tres": job.get("TRES"),
        "gres": job.get("Gres"),
    }


def _verify_sim_hash(evidence, qualification, _fail) -> None:
    expected = str(qualification["sim_review"]["sim_sha256"])
    actual = evidence["inputs"].get("sim_sha256")
    if actual is None:
        raise _fail(
            f"无法计算 sim 文件 hash（文件不存在）: {evidence['inputs'].get('sim_path')}",
            "INPUT_HASH_MISMATCH",
        )
    if actual != expected:
        raise _fail(
            f"sim 文件 hash 与资格文件审核的模板不一致：审核值 {expected}，实际 {actual}；"
            "旧资格不能为换 .sim 自动背书",
            "INPUT_HASH_MISMATCH",
        )


def _allocated_gpus(job: dict[str, str]) -> tuple[int | None, list[int] | None]:
    """从 Slurm 作业记录解析分配的 GPU 数量和设备编号（IDX 可能缺省）。

    不同 Slurm 版本/站点配置下 ``scontrol show job -o`` 的字段不一致：较老版本
    给单独的 ``Gres=``/``TRES=`` 键；启用了按 TRES 精细记账的站点（真机实测：
    本仓库目标集群）只给 ``AllocTRES=``/``ReqTRES=``，没有裸的 ``Gres=``/
    ``TRES=`` 键。``Gres=`` 能给出 IDX（具体卡号），TRES 系列字段只能给数量，
    因此按“先找 IDX 来源，再按优先级找计数来源”的顺序尝试，不假设任一键必然
    存在。``AllocTRES`` 优先于 ``ReqTRES``：前者是实际授予的资源，后者只是
    请求值，两者理论上可能不同。
    """

    gres = job.get("Gres", "")
    if gres and gres != "(null)":
        match = _GRES_GPU_RE.search(gres)
        if match is not None:
            indices = match.group("indices")
            return int(match.group("count")), (
                [int(item) for item in indices.split(",") if item] if indices else None
            )
    for tres_field in ("TRES", "AllocTRES", "ReqTRES"):
        tres = job.get(tres_field, "")
        if not tres or tres == "(null)":
            continue
        match = _TRES_GPU_RE.search(tres)
        if match is not None:
            return int(match.group(1)), None
    return None, None


def _parse_nvidia_devices(stdout: str, _fail) -> list[dict[str, Any]]:
    devices: list[dict[str, Any]] = []
    for raw_line in stdout.splitlines():
        line = raw_line.strip()
        if not line:
            continue
        fields = [field.strip() for field in line.split(",")]
        if len(fields) != len(NVIDIA_DEVICE_KEYS) or not all(fields):
            raise _fail(
                f"nvidia-smi 输出行无法按 {NVIDIA_QUERY_FIELDS} 解析: {raw_line!r}；"
                "不猜测字段含义",
                "GPU_TOOL_FAILED",
            )
        values = dict(zip(NVIDIA_DEVICE_KEYS, fields))
        if not values["index"].isdigit():
            raise _fail(
                f"nvidia-smi 返回的设备编号无法解析: {raw_line!r}",
                "GPU_TOOL_FAILED",
            )
        values["index"] = int(values["index"])
        values["gpu_vendor"] = "nvidia"
        devices.append(values)
    return devices


def _read_os_release_id() -> str | None:
    try:
        text = Path(OS_RELEASE_PATH).read_text(encoding="utf-8")
    except OSError:
        return None
    for line in text.splitlines():
        match = _OS_RELEASE_ID_RE.match(line)
        if match is not None:
            return match.group(1)
    return None


def _normalize_host(name: str) -> str:
    return str(name).strip().split(".", 1)[0].lower()


def _tail(text: str, limit: int = _EVIDENCE_TEXT_LIMIT) -> str:
    value = str(text or "")
    if len(value) <= limit:
        return value
    return value[-limit:]


def _run(command: list[str]) -> DiagnosticResult:
    """执行只读诊断命令；永不抛出，按 ok/failed/timeout/not_found 分类。"""

    argv = [str(item) for item in command]
    started = time.monotonic()
    try:
        completed = subprocess.run(
            argv,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=DIAGNOSTIC_TIMEOUT_SECONDS,
            check=False,
        )
    except FileNotFoundError:
        return DiagnosticResult(
            command=tuple(argv),
            returncode=None,
            stdout="",
            stderr=f"command not found: {argv[0]}",
            status="not_found",
            duration_seconds=time.monotonic() - started,
        )
    except subprocess.TimeoutExpired:
        return DiagnosticResult(
            command=tuple(argv),
            returncode=None,
            stdout="",
            stderr=f"timeout after {DIAGNOSTIC_TIMEOUT_SECONDS} seconds",
            status="timeout",
            duration_seconds=time.monotonic() - started,
        )
    return DiagnosticResult(
        command=tuple(argv),
        returncode=completed.returncode,
        stdout=completed.stdout or "",
        stderr=completed.stderr or "",
        status="ok" if completed.returncode == 0 else "failed",
        duration_seconds=time.monotonic() - started,
    )
