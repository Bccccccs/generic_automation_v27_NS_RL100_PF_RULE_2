"""GPU 执行配置与选择器校验（任务 1）。

对应计划第 5.1/5.2 节。这里只测纯配置与 token 生成，不探测硬件；
`-gpgpu` / `-require-gpgpu-compatibility` 的语法本身仍属 UNVERIFIED
（见 docs/gpu/20.02-evidence-register.md 的 B-01）。
"""

from __future__ import annotations

import dataclasses
import inspect
from pathlib import Path
from unittest.mock import patch

import pytest

from starccm.runtime.gpu_config import (
    GPUConfigurationError,
    GPUExecutionConfig,
    gpu_command_tokens,
)


def test_default_config_is_cpu():
    config = GPUExecutionConfig()

    assert config.backend == "cpu"
    assert config.selection is None
    assert config.qualification_path is None


def test_default_cpu_needs_no_gpu():
    assert gpu_command_tokens(GPUExecutionConfig()) == ()


def test_explicit_cpu_backend_returns_no_tokens():
    assert gpu_command_tokens(GPUExecutionConfig(backend="cpu")) == ()


@pytest.mark.parametrize("gpu_count", [1, 2, 4, 8])
def test_gpu_auto_selection_adds_only_required_tokens(gpu_count):
    config = GPUExecutionConfig(backend="gpu", selection=f"auto:{gpu_count}:nomps")

    assert gpu_command_tokens(config) == (
        "-gpgpu",
        f"auto:{gpu_count}:nomps",
        "-require-gpgpu-compatibility",
    )


def test_gpu_adds_only_required_tokens():
    assert gpu_command_tokens(
        GPUExecutionConfig(backend="gpu", selection="auto:2:nomps")
    ) == ("-gpgpu", "auto:2:nomps", "-require-gpgpu-compatibility")


@pytest.mark.parametrize(
    "selection",
    ["0:nomps", "0,1:nomps", "0,1,2:nomps", "0,1,2,3:nomps", "3,0:nomps"],
)
def test_gpu_explicit_device_list_is_accepted(selection):
    assert gpu_command_tokens(
        GPUExecutionConfig(backend="gpu", selection=selection)
    ) == ("-gpgpu", selection, "-require-gpgpu-compatibility")


def test_strict_compatibility_flag_cannot_be_disabled():
    """严格兼容参数自动附加且不可关闭，因此函数只接受配置本身。"""
    signature = inspect.signature(gpu_command_tokens)

    assert list(signature.parameters) == ["config"]


def test_config_is_frozen():
    config = GPUExecutionConfig(backend="gpu", selection="auto:1:nomps")

    with pytest.raises(dataclasses.FrozenInstanceError):
        config.selection = "auto:2:nomps"


def test_tokens_are_returned_as_tuple():
    tokens = gpu_command_tokens(GPUExecutionConfig(backend="gpu", selection="auto:1:nomps"))

    assert isinstance(tokens, tuple)


def test_qualification_path_with_spaces_is_preserved():
    path = Path("/opt/my apps/ccm gpu/qualification v1.json")
    config = GPUExecutionConfig(
        backend="gpu",
        selection="auto:2:nomps",
        qualification_path=path,
    )

    assert config.qualification_path == path
    assert gpu_command_tokens(config)[1] == "auto:2:nomps"


@pytest.mark.parametrize(
    ("selection", "reason"),
    [
        ("auto", "裸 auto 不受约束"),
        ("auto:", "缺少数量"),
        ("auto:0:nomps", "数量必须为正"),
        ("auto:-1:nomps", "负数"),
        ("auto:2", "缺少 :nomps"),
        ("auto:2:mps", "未核验的 MPS 后缀"),
        ("auto:02:nomps", "前导零"),
        ("auto:2:nomps:extra", "多余后缀"),
        ("force:0", "force: 被禁止"),
        ("force:auto:2", "force: 被禁止"),
        ("file:/tmp/gpus.txt", "本次不实现 file: 选择器"),
        ("0,0:nomps", "重复设备"),
        ("0,1,1,2:nomps", "重复设备"),
        ("-1:nomps", "负数设备号"),
        ("0,-1:nomps", "负数设备号"),
        ("01:nomps", "前导零设备号"),
        (":nomps", "空设备列表"),
        ("0,1", "缺少 :nomps"),
        ("", "空选择器"),
        ("   ", "空选择器"),
        (" auto:2:nomps", "首尾空白"),
        ("auto:2:nomps ", "首尾空白"),
        ("auto:2:nomps 0:nomps", "混用多个选择机制"),
        ("auto:2:0,1:nomps", "混用多个选择机制"),
        ("auto:2:nomps;reboot", "shell 片段"),
        ("auto:2:nomps && rm -rf /", "shell 片段"),
        ("auto:2:nomps|cat", "shell 片段"),
        ("auto:2:nomps\n-batch", "换行"),
        ("auto:2:nomps`id`", "shell 片段"),
        ("$(nvidia-smi)", "shell 片段"),
        ("auto:all:nomps", "非数字数量"),
        ("AUTO:2:NOMPS", "大小写不匹配"),
        ("gpu", "未知选择器"),
        ("0:nomps:nomps", "重复后缀"),
    ],
)
def test_illegal_selections_are_rejected(selection, reason):
    with pytest.raises(GPUConfigurationError) as excinfo:
        gpu_command_tokens(GPUExecutionConfig(backend="gpu", selection=selection))

    assert repr(selection) in str(excinfo.value)


def test_gpu_without_selection_is_rejected():
    with pytest.raises(GPUConfigurationError, match="selection"):
        gpu_command_tokens(GPUExecutionConfig(backend="gpu"))


def test_gpu_with_empty_selection_is_rejected():
    with pytest.raises(GPUConfigurationError):
        gpu_command_tokens(GPUExecutionConfig(backend="gpu", selection=""))


@pytest.mark.parametrize("backend", ["auto", "GPU", "cuda", "", "cpu "])
def test_unknown_or_implicit_backends_are_rejected(backend):
    with pytest.raises(GPUConfigurationError):
        gpu_command_tokens(GPUExecutionConfig(backend=backend, selection="auto:1:nomps"))


def test_auto_backend_is_never_accepted():
    """不设“检测到显卡就启用”的自动模式。"""
    with pytest.raises(GPUConfigurationError):
        gpu_command_tokens(GPUExecutionConfig(backend="auto"))


def test_cpu_with_selection_is_rejected():
    with pytest.raises(GPUConfigurationError, match="cpu"):
        gpu_command_tokens(GPUExecutionConfig(backend="cpu", selection="auto:1:nomps"))


def test_cpu_with_qualification_path_is_rejected():
    with pytest.raises(GPUConfigurationError, match="cpu"):
        gpu_command_tokens(
            GPUExecutionConfig(backend="cpu", qualification_path=Path("/tmp/qual.json"))
        )


def test_token_generation_does_not_probe_hardware():
    config = GPUExecutionConfig(backend="gpu", selection="auto:2:nomps")

    with (
        patch("subprocess.run", side_effect=AssertionError("配置解析不允许启动子进程")),
        patch("subprocess.Popen", side_effect=AssertionError("配置解析不允许启动子进程")),
        patch("shutil.which", side_effect=AssertionError("配置解析不允许探测可执行文件")),
    ):
        assert gpu_command_tokens(config) == (
            "-gpgpu",
            "auto:2:nomps",
            "-require-gpgpu-compatibility",
        )
