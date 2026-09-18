"""STAR-CCM+ GPU 执行配置与选择器校验。

本模块只做纯配置解析和 argv token 生成：不探测硬件、不启动子进程、不读取资格
文件、不访问网络。GPU 必须在 CLI 或 Python 配置里显式选择，不提供“检测到显卡
就启用”的自动模式，也不提供自动回退 CPU 的能力。

命令行 token（``-gpgpu`` / ``-require-gpgpu-compatibility``）在目标 20.02 build
上仍属 UNVERIFIED，正式 GPU run 前必须完成
``docs/gpu/20.02-evidence-register.md`` 中 B-01 的同版本核验。
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

GPGPU_FLAG = "-gpgpu"
STRICT_COMPATIBILITY_FLAG = "-require-gpgpu-compatibility"

_AUTO_SELECTION_RE = re.compile(r"^auto:(?P<count>[1-9][0-9]*):nomps$")
_DEVICE_SELECTION_RE = re.compile(r"^(?P<devices>[0-9]+(?:,[0-9]+)*):nomps$")
_DEVICE_LIST_ONLY_RE = re.compile(r"^[0-9]+(?:,[0-9]+)*$")
_DEVICE_INDEX_RE = re.compile(r"^(?:0|[1-9][0-9]*)$")
_ALLOWED_CHARACTERS_RE = re.compile(r"^[0-9A-Za-z:,]+$")
_NOMPS_SUFFIX = ":nomps"


@dataclass(frozen=True)
class GPUExecutionConfig:
    """单次 CCM 运行的计算后端选择。

    ``backend`` 默认为 ``cpu``，保证既有调用方（含位置参数构造）行为不变。
    """

    backend: Literal["cpu", "gpu"] = "cpu"
    selection: str | None = None
    qualification_path: Path | None = None


class GPUConfigurationError(ValueError):
    """GPU 配置矛盾或选择器不受支持。"""


def gpu_command_tokens(config: GPUExecutionConfig) -> tuple[str, ...]:
    """校验配置并返回需要插入 STAR argv 的 GPU token。

    CPU 返回空 tuple；GPU 返回带数量的选择器与不可关闭的严格兼容参数。
    矛盾配置（CPU 携带 selection 或资格文件）在此报错，不会被静默忽略。
    """

    backend = _validated_backend(config.backend)
    if backend == "cpu":
        _reject_cpu_gpu_fields(config)
        return ()
    if config.selection is None:
        raise GPUConfigurationError(
            "compute_backend='gpu' 必须提供 GPU selection（CLI 参数 --gpgpu）"
        )
    return (GPGPU_FLAG, _validated_selection(config.selection), STRICT_COMPATIBILITY_FLAG)


def gpu_selection_device_count(selection: str) -> int:
    """选择器要求的 GPU 数量：``auto:N:nomps`` 返回 N，显式卡列表返回卡数。"""

    validated = _validated_selection(selection)
    auto_match = _AUTO_SELECTION_RE.fullmatch(validated)
    if auto_match is not None:
        return int(auto_match.group("count"))
    return len(gpu_selection_device_indices(validated) or ())


def gpu_selection_device_indices(selection: str) -> tuple[int, ...] | None:
    """显式卡列表的设备编号；``auto:N:nomps`` 形式返回 ``None``。"""

    validated = _validated_selection(selection)
    device_match = _DEVICE_SELECTION_RE.fullmatch(validated)
    if device_match is None:
        return None
    return tuple(int(index) for index in device_match.group("devices").split(","))


def _validated_backend(backend: str) -> str:
    if backend not in ("cpu", "gpu"):
        raise GPUConfigurationError(
            f"compute_backend 只接受 'cpu' 或 'gpu'，收到 {backend!r}；"
            "不提供 'auto' 自动检测模式，GPU 必须显式选择"
        )
    return backend


def _reject_cpu_gpu_fields(config: GPUExecutionConfig) -> None:
    if config.selection is not None:
        raise GPUConfigurationError(
            f"compute_backend='cpu' 不允许携带 GPU selection，收到 {config.selection!r}；"
            "CPU 路径不注入 -gpgpu"
        )
    if config.qualification_path is not None:
        raise GPUConfigurationError(
            "compute_backend='cpu' 不允许携带 GPU 资格文件 "
            f"{str(config.qualification_path)!r}；CPU 路径不做 GPU 资格校验"
        )


def _validated_selection(selection: str) -> str:
    if not selection.strip():
        raise GPUConfigurationError(f"GPU selection 不能为空: {selection!r}")
    if selection != selection.strip():
        raise GPUConfigurationError(
            f"GPU selection 不允许首尾空白: {selection!r}；请作为单个 argv 元素传入"
        )
    if any(character.isspace() for character in selection):
        raise GPUConfigurationError(
            f"GPU selection 不允许包含空白或换行: {selection!r}；"
            "不支持在同一参数里混用多个选择机制"
        )
    if not _ALLOWED_CHARACTERS_RE.fullmatch(selection):
        rejected = sorted({character for character in selection if not re.match(r"[0-9A-Za-z:,]", character)})
        raise GPUConfigurationError(
            f"GPU selection 包含不允许的字符 {rejected}: {selection!r}；"
            "只接受数字、小写字母、':' 和 ','，不接受 shell 片段、路径或设备文件"
        )
    if selection.lower() != selection:
        raise GPUConfigurationError(
            f"GPU selection 必须全小写: {selection!r}；不做大小写归一化"
        )
    if selection.startswith("force:"):
        raise GPUConfigurationError(f"GPU selection 禁止使用 force: 前缀: {selection!r}")
    if selection.startswith("file:"):
        raise GPUConfigurationError(
            f"本次不实现 file: 选择器: {selection!r}；"
            "仅支持单节点内的数量选择或显式卡列表"
        )
    if selection.startswith("auto"):
        return _validated_auto_selection(selection)
    if selection == _NOMPS_SUFFIX:
        raise GPUConfigurationError(f"GPU selection 的设备列表不能为空: {selection!r}")
    if _DEVICE_LIST_ONLY_RE.fullmatch(selection):
        raise GPUConfigurationError(
            f"显式卡列表必须带 :nomps 后缀: {selection!r}；"
            "不带该后缀的形式需要资格文件证明站点允许对应 MPS 行为，本次未开放"
        )
    match = _DEVICE_SELECTION_RE.fullmatch(selection)
    if match is None:
        raise GPUConfigurationError(
            f"不支持的 GPU selection 语法: {selection!r}；"
            "本批次只接受 auto:N:nomps 或形如 0,1:nomps 的显式卡列表"
        )
    return _validated_device_list(selection, match.group("devices"))


def _validated_auto_selection(selection: str) -> str:
    if selection == "auto":
        raise GPUConfigurationError(
            f"GPU selection 不接受不受约束的裸 auto: {selection!r}；必须写成 auto:N:nomps"
        )
    if "," in selection:
        raise GPUConfigurationError(
            f"GPU selection 不允许混用 auto 数量与显式卡列表: {selection!r}"
        )
    match = _AUTO_SELECTION_RE.fullmatch(selection)
    if match is None:
        raise GPUConfigurationError(
            f"auto 形式的 GPU selection 必须是 auto:N:nomps，N 为不带前导零的正整数，"
            f"收到 {selection!r}；不带 :nomps 的形式需要资格文件证明站点允许对应 MPS 行为，"
            "本次未开放"
        )
    return selection


def _validated_device_list(selection: str, devices: str) -> str:
    indices: list[int] = []
    for raw_index in devices.split(","):
        if not _DEVICE_INDEX_RE.fullmatch(raw_index):
            raise GPUConfigurationError(
                f"GPU selection 的设备编号必须是不带前导零的非负整数，"
                f"在 {selection!r} 中发现 {raw_index!r}"
            )
        index = int(raw_index)
        if index in indices:
            raise GPUConfigurationError(
                f"GPU selection 不允许重复选择同一张卡: {selection!r}（设备 {index} 重复）"
            )
        indices.append(index)
    return selection
