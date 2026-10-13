"""GPU 执行证据 sidecar：原子写入、输出复用守卫与单写者 lock。

sidecar 是 GPU 真相的唯一载体，独立于 ``case_manifest.yaml``（manifest 的
preflight/finalize 会覆盖字段，因此不把 GPU 结论塞进去）。CPU 路径永不产生
sidecar，历史 CPU 目录没有 sidecar 时旧输出与旧返回码逐字保持。

未取得真实运行证据时状态只能是 ``UNVERIFIED`` 或 ``BLOCKED``；退出码 0、
dry-run 和 Python 测试通过都不构成 GPU 求解验收。
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import socket
import tempfile
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

SCHEMA_VERSION = "ccm_gpu_execution_v1"
SIDECAR_NAME = "gpu_execution.json"
LOCK_NAME = "gpu_execution.lock"

STATE_UNVERIFIED = "UNVERIFIED"
STATE_PREFLIGHT_PASSED = "PREFLIGHT_PASSED"
STATE_RUNNING = "RUNNING"
STATE_GPU_CONFIRMED = "GPU_CONFIRMED"
STATE_BLOCKED = "BLOCKED"
STATE_FAILED = "FAILED"

GPU_STATES = (
    STATE_UNVERIFIED,
    STATE_PREFLIGHT_PASSED,
    STATE_RUNNING,
    STATE_GPU_CONFIRMED,
    STATE_BLOCKED,
    STATE_FAILED,
)

# GPU run 必须使用全新独立输出目录；这些产物一旦存在就说明目录里可能混有
# 上一次运行的结果，不能让新运行覆盖或续写。
REUSE_FORBIDDEN_FILES = (
    "timeseries.csv",
    "starccm_flow_control.log",
    "flow_control_result.sim",
    SIDECAR_NAME,
)

# dry-run 之后转 run 时，目录里允许存在的离线产物（宏、runtime plan、复制的
# schedule、UNVERIFIED sidecar）不会触发拒绝；判定见 _offline_request_record()。


class GPUExecutionError(RuntimeError):
    """GPU 执行、证据或目录状态不满足要求。"""

    def __init__(self, message: str, *, failure_code: str = "GPU_EXECUTION_FAILED") -> None:
        super().__init__(message)
        self.failure_code = failure_code


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


REDACTED = "REDACTED"
_SECRET_FLAGS = ("-podkey",)


def redact_command(command: Any) -> list[str]:
    """返回可写入证据文件的脱敏 argv；许可证 token 不落地。"""

    tokens = [str(item) for item in (command or [])]
    redacted: list[str] = []
    for position, token in enumerate(tokens):
        if position and tokens[position - 1] in _SECRET_FLAGS:
            redacted.append(REDACTED)
            continue
        redacted.append(token)
    return redacted


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def sidecar_path(output_dir: Path) -> Path:
    return Path(output_dir) / SIDECAR_NAME


def initial_gpu_record(
    *,
    backend: str,
    selection: str | None,
    num_processes: int,
    requested_gpu_count: int | None = None,
    qualification_path: Path | None = None,
    node: str | None = None,
) -> dict[str, Any]:
    """构造初始 sidecar 记录；未验证字段一律为 null，不用请求值填充 actual_*。"""

    return {
        "schema_version": SCHEMA_VERSION,
        "request": {
            "backend": backend,
            "selection": selection,
            "mpi_processes": num_processes,
            "requested_gpu_count": requested_gpu_count,
            "strict_compatibility": backend == "gpu",
        },
        "state": STATE_UNVERIFIED,
        "actual_backend": "unknown",
        "star_return_code": None,
        "failure_code": None,
        "failure_detail": None,
        "command": None,
        "evidence": [],
        "node": node,
        "host": socket.gethostname(),
        "devices": [],
        "inputs": {},
        "expectations": {},
        "timing": {"requested_at": utc_now()},
        "outputs": {},
        "qualification_path": str(qualification_path) if qualification_path else None,
        "log_analysis": None,
        "qualification_sha256": (
            sha256_file(qualification_path)
            if qualification_path and Path(qualification_path).is_file()
            else None
        ),
    }


def write_gpu_evidence(path: Path, data: dict[str, Any]) -> None:
    """临时文件 + 原子替换写入 sidecar；单一 runner 写入者。"""

    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    handle = tempfile.NamedTemporaryFile(
        "w",
        encoding="utf-8",
        prefix=f".{target.name}.",
        suffix=".tmp",
        dir=str(target.parent),
        delete=False,
    )
    try:
        with handle:
            json.dump(data, handle, ensure_ascii=False, indent=2, sort_keys=True)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(handle.name, target)
    except BaseException:
        Path(handle.name).unlink(missing_ok=True)
        raise


def read_gpu_evidence(path: Path) -> dict[str, Any] | None:
    target = Path(path)
    if not target.is_file():
        return None
    try:
        payload = json.loads(target.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, UnicodeDecodeError):
        return None
    return payload if isinstance(payload, dict) else None


def assert_gpu_output_not_reused(output_dir: Path, *, allow_dry_run_residue: bool = True) -> None:
    """GPU run 前检查输出目录，避免旧产物或中断结果冒充本次结果。

    只允许覆盖 dry-run 留下的 ``UNVERIFIED`` 离线请求记录；已有 timeseries、
    STAR 日志、结果 sim 或任何带实际执行证据的 sidecar 都判
    ``OUTPUT_REUSE_FORBIDDEN``。
    """

    directory = Path(output_dir)
    if not directory.exists():
        return
    conflicting: list[str] = []
    for name in REUSE_FORBIDDEN_FILES:
        candidate = directory / name
        if not candidate.exists():
            continue
        if name == SIDECAR_NAME and allow_dry_run_residue:
            reason = _offline_request_record(candidate)
            if reason is None:
                continue
            conflicting.append(f"{SIDECAR_NAME}({reason})")
            continue
        conflicting.append(name)
    if conflicting:
        raise GPUExecutionError(
            "GPU run 拒绝复用已有输出目录 "
            f"{directory}: 发现 {', '.join(conflicting)}；"
            "请使用全新独立输出目录，不要覆盖或续写旧产物",
            failure_code="OUTPUT_REUSE_FORBIDDEN",
        )


def assert_gpu_sidecar_replaceable(output_dir: Path) -> None:
    """覆盖 sidecar 之前确认它不是真实执行证据。

    GPU dry-run 会重写请求记录，但绝不能销毁已有的 ``GPU_CONFIRMED`` /
    ``FAILED`` / ``BLOCKED`` 结论——sidecar 是 GPU 真相的唯一载体。
    """

    path = Path(output_dir) / SIDECAR_NAME
    if not path.exists():
        return
    reason = _offline_request_record(path)
    if reason is not None:
        raise GPUExecutionError(
            f"拒绝覆盖已有 GPU 证据 {path}（{reason}）；"
            "dry-run 只能覆盖自己此前留下的离线请求记录，"
            "真实执行结论必须保留，请使用新的输出目录",
            failure_code="SIDECAR_OVERWRITE_FORBIDDEN",
        )


def _offline_request_record(path: Path) -> str | None:
    """判断 sidecar 是否只是 dry-run 的离线请求记录；返回不可覆盖的原因。"""

    record = read_gpu_evidence(path)
    if record is None:
        return "无法解析"
    if record.get("state") != STATE_UNVERIFIED:
        return f"state={record.get('state')!r}"
    if record.get("devices"):
        return "含实际设备证据"
    if record.get("star_return_code") is not None:
        return "含 STAR 返回码"
    if record.get("actual_backend") not in (None, "unknown"):
        return f"actual_backend={record.get('actual_backend')!r}"
    return None


def acquire_gpu_lock(output_dir: Path) -> Path:
    """以独占创建方式获取 GPU 专用 lock，记录本进程信息。

    不自动移除未知残留 lock，也不杀其他 STAR/MPS 进程。
    """

    directory = Path(output_dir)
    directory.mkdir(parents=True, exist_ok=True)
    lock_path = directory / LOCK_NAME
    payload = {
        "pid": os.getpid(),
        "host": socket.gethostname(),
        "acquired_at": utc_now(),
    }
    try:
        with lock_path.open("x", encoding="utf-8") as handle:
            json.dump(payload, handle, ensure_ascii=False, indent=2)
            handle.write("\n")
    except FileExistsError:
        holder = "unknown"
        try:
            holder = lock_path.read_text(encoding="utf-8").strip() or "empty"
        except OSError:
            holder = "unreadable"
        raise GPUExecutionError(
            f"GPU lock 已存在: {lock_path}（持有者记录: {holder}）；"
            "同一 GPU run 目录只允许一个 writer，本次不自动移除未知残留 lock",
            failure_code="GPU_LOCK_HELD",
        ) from None
    return lock_path


def release_gpu_lock(lock_path: Path) -> None:
    """只释放本进程创建的 lock。"""

    target = Path(lock_path)
    if not target.is_file():
        return
    try:
        payload = json.loads(target.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, UnicodeDecodeError, OSError):
        return
    if (
        isinstance(payload, dict)
        and payload.get("pid") == os.getpid()
        and payload.get("host") == socket.gethostname()
    ):
        try:
            target.unlink(missing_ok=True)
        except OSError:
            # 本函数常在 finally / except 里调用，这里的 OSError 不能掩盖原始异常。
            pass


@dataclass(frozen=True)
class GPULogPatternSet:
    """某个具体 STAR build 的日志句式集合。

    每条句式必须来自该 build 的**真实脱敏日志**（放在
    ``tests/fixtures/starccm_gpu/real/``），不得凭猜测编写 Siemens 日志句式，
    也不得从其他版本移植。命名组 ``device`` 用于识别每张卡的执行证据。
    """

    star_build: str
    solver_execution: tuple[str, ...] = ()
    device_initialization: tuple[str, ...] = ()
    cpu_fallback: tuple[str, ...] = ()
    out_of_memory: tuple[str, ...] = ()
    model_unsupported: tuple[str, ...] = ()
    fatal_errors: tuple[str, ...] = ()


# 生产注册表：build → 已由真实脱敏日志核验的句式。
# 当前为空 —— 20.02 的真实 GPU 日志尚未采集（B-07），因此任何 build 的日志都
# 只能判“未确认”。这里不允许填猜测句式来让 GPU run 通过。
REGISTERED_LOG_PATTERNS: dict[str, GPULogPatternSet] = {}

_PATTERN_CATEGORIES = (
    ("solver_execution", "gpu_solver_lines"),
    ("device_initialization", "device_init_lines"),
    ("cpu_fallback", "cpu_fallback_lines"),
    ("out_of_memory", "out_of_memory_lines"),
    ("model_unsupported", "model_unsupported_lines"),
    ("fatal_errors", "fatal_error_lines"),
)
_LINE_TEXT_LIMIT = 400


def parse_gpu_log(
    text: str,
    *,
    star_build: str,
    pattern_set: GPULogPatternSet | None = None,
) -> dict[str, Any]:
    """按已验证 build 的日志模式返回设备、solver、回退和错误证据。

    ``pattern_set`` 只供测试注入合成句式；生产路径永远使用
    ``REGISTERED_LOG_PATTERNS``，未注册的 build 一律返回未确认结果。
    """

    lines = str(text or "").splitlines()
    result: dict[str, Any] = {
        "star_build": star_build,
        "pattern_source": "none",
        "parser_status": "UNVERIFIED_BUILD_NOT_REGISTERED",
        "log_line_count": len(lines),
        "matched_line_count": 0,
    }
    result.update({key: [] for _, key in _PATTERN_CATEGORIES})

    patterns = pattern_set if pattern_set is not None else REGISTERED_LOG_PATTERNS.get(star_build)
    if patterns is None:
        return result
    if patterns.star_build != star_build:
        result["parser_status"] = "UNVERIFIED_BUILD_MISMATCH"
        result["pattern_build"] = patterns.star_build
        return result

    result["pattern_source"] = "synthetic_test_only" if pattern_set is not None else "registered"
    compiled = [
        (key, [re.compile(expression) for expression in getattr(patterns, category)])
        for category, key in _PATTERN_CATEGORIES
    ]
    matched = 0
    for number, line in enumerate(lines, start=1):
        for key, regexes in compiled:
            for regex in regexes:
                match = regex.search(line)
                if match is None:
                    continue
                entry: dict[str, Any] = {
                    "line_number": number,
                    "text": line.strip()[:_LINE_TEXT_LIMIT],
                }
                device = match.groupdict().get("device")
                if device is not None:
                    entry["device"] = str(device)
                result[key].append(entry)
                matched += 1
                break
    result["matched_line_count"] = matched
    result["parser_status"] = "MATCHED"
    return result


def validate_gpu_completion(evidence: dict[str, Any], *, expected_node: str) -> None:
    """缺失 GPU 实际执行证据或发现回退/分配不符时抛 :class:`GPUExecutionError`。

    只确认“GPU 执行且运行完整”；物理等价性和性能收益是另外两个独立结论，
    不由本函数判定。STAR 的真实退出码必须保留，不得伪装。
    """

    record = dict(evidence or {})
    state = record.get("state")
    if state != STATE_RUNNING:
        raise GPUExecutionError(
            f"GPU 完成判定只适用于已启动的运行，当前 state={state!r}",
            failure_code="NOT_LAUNCHED",
        )
    returncode = record.get("star_return_code")
    if returncode is None:
        raise GPUExecutionError(
            "STAR 未返回退出码，本次运行没有正常结束", failure_code="LAUNCH_INCOMPLETE"
        )
    if _normalize_host(record.get("node")) != _normalize_host(expected_node):
        raise GPUExecutionError(
            f"GPU 运行节点 {record.get('node')!r} 与预期节点 {expected_node!r} 不符",
            failure_code="NODE_MISMATCH",
        )
    if int(returncode) != 0:
        raise GPUExecutionError(
            f"STAR-CCM+ 以非零码 {returncode} 退出；退出码已如实记录，不判 GPU 通过",
            failure_code="STAR_NONZERO_EXIT",
        )

    analysis = dict(record.get("log_analysis") or {})
    if analysis.get("parser_status") != "MATCHED":
        raise GPUExecutionError(
            "日志未确认 GPU 求解执行："
            f"parser_status={analysis.get('parser_status')!r}, build={analysis.get('star_build')!r}；"
            "退出码 0 不构成 GPU 验收，需先采集该 build 的真实脱敏日志并登记句式（B-07）",
            failure_code="GPU_EXECUTION_UNCONFIRMED",
        )
    if analysis.get("out_of_memory_lines"):
        raise GPUExecutionError(
            "检测到 GPU 显存不足（OOM）；不改网格、不降精度、不自动少卡重试。原始日志行: "
            + _describe(analysis["out_of_memory_lines"]),
            failure_code="GPU_OUT_OF_MEMORY",
        )
    if analysis.get("model_unsupported_lines"):
        raise GPUExecutionError(
            "检测到不被 GPU 支持的物理模型；不得通过关闭模型让 GPU 通过。原始日志行: "
            + _describe(analysis["model_unsupported_lines"]),
            failure_code="GPU_MODEL_UNSUPPORTED",
        )
    if analysis.get("cpu_fallback_lines"):
        raise GPUExecutionError(
            "检测到不兼容 solver 回退 CPU（区别于正常的 CPU 辅助开销）。原始日志行: "
            + _describe(analysis["cpu_fallback_lines"]),
            failure_code="CPU_FALLBACK_DETECTED",
        )

    request = dict(record.get("request") or {})
    requested = int(request.get("requested_gpu_count") or len(record.get("devices") or []))
    solver_lines = list(analysis.get("gpu_solver_lines") or [])
    evidenced = sorted(
        {line["device"] for line in solver_lines if isinstance(line.get("device"), str)}
    )
    if not solver_lines:
        raise GPUExecutionError(
            f"请求 {requested} 张 GPU，但日志中没有任何 GPU solver 执行证据",
            failure_code="GPU_EXECUTION_UNCONFIRMED",
        )
    if len(evidenced) < requested:
        raise GPUExecutionError(
            f"请求 {requested} 张 GPU，但日志只有设备 {evidenced} 的执行证据，"
            f"缺少 {requested - len(evidenced)} 张；申请多卡却只使用部分卡不判通过",
            failure_code="GPU_DEVICE_EVIDENCE_MISSING",
        )

    outputs = dict(record.get("outputs") or {})
    expectations = dict(record.get("expectations") or {})
    row_count = int(outputs.get("row_count") or 0)
    if not outputs.get("timeseries") or row_count <= 0:
        raise GPUExecutionError(
            f"GPU 运行缺少必要产物或时段未完成: timeseries={outputs.get('timeseries')!r}, "
            f"row_count={outputs.get('row_count')!r}",
            failure_code="OUTPUTS_INCOMPLETE",
        )
    expected_steps = expectations.get("total_steps")
    if expected_steps is not None and row_count < int(expected_steps):
        raise GPUExecutionError(
            f"GPU 运行未完成预期时段：期望 {expected_steps} 个求解步，"
            f"实际 timeseries 只有 {row_count} 行；退出码 0 不等于跑完",
            failure_code="OUTPUTS_INCOMPLETE",
        )
    if expectations.get("result_sim_required") and not outputs.get("result_sim"):
        raise GPUExecutionError(
            "配置要求保存 flow_control_result.sim，但本次运行没有产出该文件",
            failure_code="OUTPUTS_INCOMPLETE",
        )


def _describe(entries: list[dict[str, Any]], limit: int = 5) -> str:
    return " | ".join(
        f"L{entry.get('line_number')}: {entry.get('text')}" for entry in list(entries)[:limit]
    )


def _normalize_host(name: Any) -> str:
    return str(name or "").strip().split(".", 1)[0].lower()
