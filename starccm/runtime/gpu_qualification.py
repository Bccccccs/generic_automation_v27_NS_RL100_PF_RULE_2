"""GPU 资格文件（``ccm_gpu_qualification_v1``）读取与校验。

资格文件代表“具备试跑条件”，不代表 GPU 已成功执行。它把人工核验信息
（版本、平台、启动方式、模型清单、文档来源）与运行时实时证据分开：本模块
只读 JSON、只校验字段与证据引用，不启动任何命令、不访问网络、不做默认自动加载。

任何缺字段、错误类型、不支持 schema、通配平台、未解决的模型兼容性或测试专用
标记都会被拒绝，避免用一份模糊文件为真实 GPU run 背书。
"""

from __future__ import annotations

import json
import re
from datetime import date
from pathlib import Path
from typing import Any

from starccm.runtime.gpu_config import GPGPU_FLAG, STRICT_COMPATIBILITY_FLAG

SCHEMA_VERSION = "ccm_gpu_qualification_v1"

_REQUIRED_TOP_LEVEL = (
    "schema_version",
    "reviewed_at",
    "reviewer",
    "star_release",
    "star_build",
    "precision",
    "documentation",
    "approved_platforms",
    "approved_launch",
    "sim_review",
    "required_flags",
)
_OPTIONAL_TOP_LEVEL = ("test_only",)
_DOCUMENTATION_FIELDS = ("title", "build", "chapter", "source")
_DOCUMENTATION_OPTIONAL = ("sha256",)
_PLATFORM_FIELDS = (
    "os",
    "cpu_arch",
    "gpu_vendor",
    "gpu_model",
    "match_rule",
    "driver_requirement",
    "evidence",
)
_PLATFORM_OPTIONAL = ("os_id",)
_LAUNCH_FIELDS = (
    "mode",
    "identity",
    "scheduler",
    "mpi",
    "mpi_version",
    "launch_method",
    "approved_ranks_per_gpu",
    "approved_gpu_counts",
    "mps_policy",
    "single_node_only",
    "gpu_count_source",
)
_LAUNCH_OPTIONAL = ("identity_sha256", "occupancy_ignore_process_names")
_SIM_ITEM_FIELDS = ("name", "kind", "status", "source")
_FLAG_FIELDS = ("gpgpu_flag", "strict_compatibility_flag")

_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
_BUILD_RE = re.compile(r"^\d+\.\d+\.\d+(?:-[A-Za-z0-9.]+)?$")
_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
_VERSION_RE = re.compile(r"^\d+(?:\.\d+)*$")
_DRIVER_RULE_RE = re.compile(r"^(?P<operator>>=|==)(?P<version>\d+(?:\.\d+)*)$")

_LAUNCH_MODES = ("native", "container", "wrapper")
_SCHEDULERS = ("manual", "slurm")
_MPS_POLICIES = ("disabled", "not_applicable")
_SIM_ITEM_KINDS = ("solver", "model", "interface", "motion", "report", "derived_part", "mesh")
_WILDCARD_VALUES = {"*", "any", "all", "unknown", "n/a", "na", "-", ""}


class GPUQualificationError(ValueError):
    """资格文件缺字段、类型错误、schema 不支持或证据无效。"""


def load_gpu_qualification(path: Path) -> dict[str, Any]:
    """读取并校验 GPU 资格文件，返回原始字典。

    不启动任何命令；文件不存在抛 ``FileNotFoundError``，其余问题抛
    ``GPUQualificationError``。
    """

    resolved = Path(path).expanduser()
    try:
        payload = json.loads(resolved.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        _fail(f"资格文件不是合法 JSON: {resolved}: {exc}")
        return {}
    if not isinstance(payload, dict):
        _fail(f"资格文件顶层必须是 JSON 对象，收到 {type(payload).__name__}: {resolved}")
    _check_schema_version(payload)
    _check_top_level_keys(payload)
    _check_review(payload)
    _check_star_identity(payload)
    _check_documentation(payload)
    _check_platforms(payload)
    _check_launch(payload)
    _check_sim_review(payload)
    _check_required_flags(payload)
    return payload


def parse_driver_rule(rule: str) -> tuple[str, tuple[int, ...]]:
    """把 ``>=550.54.15`` 形式的驱动要求解析为可比较的操作符与版本元组。"""

    match = _DRIVER_RULE_RE.fullmatch(rule.strip())
    if match is None:
        raise GPUQualificationError(
            f"driver_requirement 必须写成 '>=版本' 或 '==版本'（例如 >=550.54.15），收到 {rule!r}；"
            "不接受 latest、推荐值等无法核验的写法"
        )
    version = version_key(match.group("version"))
    if not any(version):
        raise GPUQualificationError(
            f"driver_requirement {rule!r} 等价于驱动通配，属于无限通配；"
            "必须写明目标站点实际核验过的驱动下限"
        )
    return match.group("operator"), version


def version_key(value: str) -> tuple[int, ...]:
    if not _VERSION_RE.fullmatch(value.strip()):
        raise GPUQualificationError(f"无法解析版本号 {value!r}")
    return tuple(int(part) for part in value.strip().split("."))


def _fail(message: str) -> None:
    raise GPUQualificationError(message)


def _check_schema_version(payload: dict[str, Any]) -> None:
    schema_version = payload.get("schema_version")
    if schema_version != SCHEMA_VERSION:
        _fail(
            f"schema_version 必须是 {SCHEMA_VERSION!r}，收到 {schema_version!r}；"
            "不支持的 schema 不能用于 GPU run"
        )
    if payload.get("test_only"):
        _fail("资格文件标记为 test_only=true，生产 GPU run 必须拒绝该文件")


def _check_top_level_keys(payload: dict[str, Any]) -> None:
    missing = [name for name in _REQUIRED_TOP_LEVEL if name not in payload]
    if missing:
        _fail(f"资格文件缺少必需字段: {', '.join(missing)}")
    unknown = [
        name
        for name in payload
        if name not in _REQUIRED_TOP_LEVEL and name not in _OPTIONAL_TOP_LEVEL
    ]
    if unknown:
        _fail(
            f"资格文件包含未知字段: {', '.join(sorted(unknown))}；"
            f"允许字段为 {', '.join((*_REQUIRED_TOP_LEVEL, *_OPTIONAL_TOP_LEVEL))}"
        )


def _check_review(payload: dict[str, Any]) -> None:
    reviewer = _non_empty_string(payload, "reviewer")
    if not reviewer.strip():
        _fail("reviewer 不能为空白")
    reviewed_at = _non_empty_string(payload, "reviewed_at")
    if not _DATE_RE.fullmatch(reviewed_at):
        _fail(f"reviewed_at 必须是 YYYY-MM-DD 日期，收到 {reviewed_at!r}")
    try:
        reviewed = date.fromisoformat(reviewed_at)
    except ValueError as exc:
        _fail(f"reviewed_at 不是有效日期 {reviewed_at!r}: {exc}")
        return
    if reviewed > date.today():
        _fail(f"reviewed_at {reviewed_at!r} 晚于今天，属于失效或错误的核验记录")


def _check_star_identity(payload: dict[str, Any]) -> None:
    _non_empty_string(payload, "star_release")
    star_build = _non_empty_string(payload, "star_build")
    if not _BUILD_RE.fullmatch(star_build):
        _fail(
            f"star_build 必须是完整 build（例如 20.02.007），收到 {star_build!r}；"
            "不能只写发布号或安装目录名"
        )
    _non_empty_string(payload, "precision")


def _check_documentation(payload: dict[str, Any]) -> None:
    entries = _list_of_dicts(payload, "documentation", _DOCUMENTATION_FIELDS, _DOCUMENTATION_OPTIONAL)
    if not entries:
        _fail("documentation 不能为空；必须给出目标 build 的正式文档依据")
    star_build = payload["star_build"]
    for index, entry in enumerate(entries):
        for field_name in _DOCUMENTATION_FIELDS:
            if not str(entry[field_name]).strip():
                _fail(f"documentation[{index}].{field_name} 不能为空")
        if entry["build"] != star_build:
            _fail(
                f"documentation[{index}].build={entry['build']!r} 与 star_build={star_build!r} 不一致；"
                "不能借用其他版本的文档作为依据"
            )
        digest = entry.get("sha256")
        if digest is not None and not _SHA256_RE.fullmatch(str(digest)):
            _fail(
                f"documentation[{index}].sha256 必须是 64 位小写十六进制或 null，收到 {digest!r}；"
                "在线文档不得编造 hash"
            )


def _check_platforms(payload: dict[str, Any]) -> None:
    entries = _list_of_dicts(payload, "approved_platforms", _PLATFORM_FIELDS, _PLATFORM_OPTIONAL)
    if not entries:
        _fail("approved_platforms 不能为空；没有平台证据不得批准 GPU run")
    for index, entry in enumerate(entries):
        for field_name in _PLATFORM_FIELDS:
            value = str(entry[field_name]).strip()
            if not value:
                _fail(f"approved_platforms[{index}].{field_name} 不能为空")
            if field_name != "match_rule" and value.lower() in _WILDCARD_VALUES:
                _fail(
                    f"approved_platforms[{index}].{field_name}={value!r} 属于无限通配，"
                    "必须写明具体 OS/架构/厂商/型号"
                )
        if "*" in entry["match_rule"] or str(entry["match_rule"]).lower() in _WILDCARD_VALUES:
            _fail(f"approved_platforms[{index}].match_rule 不能使用通配: {entry['match_rule']!r}")
        parse_driver_rule(str(entry["driver_requirement"]))


def _check_launch(payload: dict[str, Any]) -> None:
    launch = payload["approved_launch"]
    if not isinstance(launch, dict):
        _fail(f"approved_launch 必须是对象，收到 {type(launch).__name__}")
    missing = [name for name in _LAUNCH_FIELDS if name not in launch]
    if missing:
        _fail(f"approved_launch 缺少必需字段: {', '.join(missing)}")
    unknown = [
        name for name in launch if name not in _LAUNCH_FIELDS and name not in _LAUNCH_OPTIONAL
    ]
    if unknown:
        _fail(f"approved_launch 包含未知字段: {', '.join(sorted(unknown))}")
    for field_name in ("mode", "identity", "scheduler", "mpi", "mpi_version", "launch_method", "gpu_count_source"):
        if not str(launch[field_name]).strip():
            _fail(f"approved_launch.{field_name} 不能为空")
    if launch["mode"] not in _LAUNCH_MODES:
        _fail(
            f"approved_launch.mode 必须是 {_LAUNCH_MODES} 之一，收到 {launch['mode']!r}"
        )
    if launch["scheduler"] not in _SCHEDULERS:
        _fail(
            f"approved_launch.scheduler 必须是 {_SCHEDULERS} 之一，收到 {launch['scheduler']!r}"
        )
    if launch["mps_policy"] not in _MPS_POLICIES:
        _fail(
            f"approved_launch.mps_policy 必须是 {_MPS_POLICIES} 之一，收到 {launch['mps_policy']!r}；"
            "本批次只批准 :nomps 选择器，启用 MPS 需要独立资格证据"
        )
    if launch["single_node_only"] is not True:
        _fail(
            f"approved_launch.single_node_only 必须为 true，收到 {launch['single_node_only']!r}；"
            "本次改造固定单节点，不实现跨节点 GPU"
        )
    ranks_per_gpu = launch["approved_ranks_per_gpu"]
    if not isinstance(ranks_per_gpu, int) or isinstance(ranks_per_gpu, bool) or ranks_per_gpu != 1:
        _fail(
            f"approved_launch.approved_ranks_per_gpu 首批只批准 1（每 GPU 一个 MPI rank），"
            f"收到 {ranks_per_gpu!r}"
        )
    counts = launch["approved_gpu_counts"]
    if not isinstance(counts, list) or not counts:
        _fail("approved_launch.approved_gpu_counts 必须是非空列表")
    for count in counts:
        if not isinstance(count, int) or isinstance(count, bool) or count < 1:
            _fail(f"approved_launch.approved_gpu_counts 含非法值 {count!r}")
    ignore_names = launch.get("occupancy_ignore_process_names")
    if ignore_names is not None:
        if not isinstance(ignore_names, list) or not all(
            isinstance(name, str) and name.strip() for name in ignore_names
        ):
            _fail(
                "approved_launch.occupancy_ignore_process_names 必须是非空字符串列表；"
                "它会让占用冲突检查跳过站点常驻代理，必须有站点依据，不能留空串或通配"
            )
        for name in ignore_names:
            if "*" in name or name.strip().lower() in _WILDCARD_VALUES:
                _fail(
                    f"approved_launch.occupancy_ignore_process_names 含通配项 {name!r}；"
                    "只允许写明具体进程名"
                )
    digest = launch.get("identity_sha256")
    if digest is not None and not _SHA256_RE.fullmatch(str(digest)):
        _fail(
            f"approved_launch.identity_sha256 必须是 64 位小写十六进制或 null，收到 {digest!r}"
        )


def _check_sim_review(payload: dict[str, Any]) -> None:
    review = payload["sim_review"]
    if not isinstance(review, dict):
        _fail(f"sim_review 必须是对象，收到 {type(review).__name__}")
    missing = [name for name in ("sim_sha256", "items") if name not in review]
    if missing:
        _fail(f"sim_review 缺少必需字段: {', '.join(missing)}")
    unknown = [name for name in review if name not in ("sim_sha256", "items")]
    if unknown:
        _fail(f"sim_review 包含未知字段: {', '.join(sorted(unknown))}")
    if not _SHA256_RE.fullmatch(str(review["sim_sha256"])):
        _fail(
            f"sim_review.sim_sha256 必须是 64 位小写十六进制，收到 {review['sim_sha256']!r}"
        )
    items = review["items"]
    if not isinstance(items, list) or not items:
        _fail("sim_review.items 不能为空；必须逐项列出真实 sim 的模型/solver/report 清单")
    blocked: list[str] = []
    for index, item in enumerate(items):
        if not isinstance(item, dict):
            _fail(f"sim_review.items[{index}] 必须是对象")
        item_missing = [name for name in _SIM_ITEM_FIELDS if name not in item]
        if item_missing:
            _fail(f"sim_review.items[{index}] 缺少字段: {', '.join(item_missing)}")
        item_unknown = [name for name in item if name not in _SIM_ITEM_FIELDS]
        if item_unknown:
            _fail(f"sim_review.items[{index}] 包含未知字段: {', '.join(sorted(item_unknown))}")
        for field_name in _SIM_ITEM_FIELDS:
            if not str(item[field_name]).strip():
                _fail(f"sim_review.items[{index}].{field_name} 不能为空")
        if item["kind"] not in _SIM_ITEM_KINDS:
            _fail(
                f"sim_review.items[{index}].kind={item['kind']!r} 不在允许范围 {_SIM_ITEM_KINDS}"
            )
        if item["status"] not in ("compatible", "incompatible", "unknown"):
            _fail(
                f"sim_review.items[{index}].status={item['status']!r} 必须是 "
                "compatible/incompatible/unknown"
            )
        if item["status"] != "compatible":
            blocked.append(f"{item['name']}({item['kind']}={item['status']})")
    if blocked:
        _fail(
            "sim_review 存在未确认或不兼容项，正式 GPU run 被阻止: "
            + "; ".join(blocked)
            + "；不得通过关闭物理模型让 GPU 通过"
        )


def _check_required_flags(payload: dict[str, Any]) -> None:
    flags = payload["required_flags"]
    if not isinstance(flags, dict):
        _fail(f"required_flags 必须是对象，收到 {type(flags).__name__}")
    missing = [name for name in _FLAG_FIELDS if name not in flags]
    if missing:
        _fail(f"required_flags 缺少必需字段: {', '.join(missing)}")
    unknown = [name for name in flags if name not in _FLAG_FIELDS]
    if unknown:
        _fail(f"required_flags 包含未知字段: {', '.join(sorted(unknown))}")
    expected = {
        "gpgpu_flag": GPGPU_FLAG,
        "strict_compatibility_flag": STRICT_COMPATIBILITY_FLAG,
    }
    for field_name, expected_value in expected.items():
        actual = flags[field_name]
        if actual != expected_value:
            _fail(
                f"required_flags.{field_name}={actual!r} 与本实现生成的 {expected_value!r} 不一致；"
                "目标 build 使用不同参数时，必须先按同版本手册更新 token 生成，再放行 GPU run"
            )


def _non_empty_string(payload: dict[str, Any], field_name: str) -> str:
    value = payload.get(field_name)
    if not isinstance(value, str):
        _fail(f"{field_name} 必须是字符串，收到 {type(value).__name__}")
    if not value.strip():
        _fail(f"{field_name} 不能为空")
    return value


def _list_of_dicts(
    payload: dict[str, Any],
    field_name: str,
    required: tuple[str, ...],
    optional: tuple[str, ...],
) -> list[dict[str, Any]]:
    entries = payload.get(field_name)
    if not isinstance(entries, list):
        _fail(f"{field_name} 必须是列表，收到 {type(entries).__name__}")
    for index, entry in enumerate(entries):
        if not isinstance(entry, dict):
            _fail(f"{field_name}[{index}] 必须是对象，收到 {type(entry).__name__}")
        missing = [name for name in required if name not in entry]
        if missing:
            _fail(f"{field_name}[{index}] 缺少字段: {', '.join(missing)}")
        unknown = [name for name in entry if name not in required and name not in optional]
        if unknown:
            _fail(f"{field_name}[{index}] 包含未知字段: {', '.join(sorted(unknown))}")
    return entries
