"""CPU 兼容性特征测试（任务 1）。

锁定 `_build_starccm_command()` 与 `FlowControlStarCCMRunConfig` 的既有 CPU 行为，
确保新增 GPU 配置字段和参数不改变默认路径。对应计划第 1、6 节和关卡 A。
"""

from __future__ import annotations

import dataclasses
from pathlib import Path
from unittest.mock import patch

import pytest

from flow_control.adapters.starccm_runner import (
    FlowControlStarCCMRunConfig,
    FlowControlStarCCMRunner,
    _build_starccm_command,
)
from flow_control.excitation_patterns.common import ActuationConfig, write_pattern_outputs
from flow_control.excitation_patterns.pulse import generate as generate_pulse
from starccm.runtime.gpu_config import GPUConfigurationError, GPUExecutionConfig


def test_legacy_cpu_argv_is_unchanged():
    result = _build_starccm_command(
        "/apps/starccm+",
        Path("/work/macro.java"),
        Path("/work/case.sim"),
        num_cores=8,
        machinefile_path=Path("/work/hosts.ma"),
        mpi_env=("UCX_DC_MLX5_NUM_DCI=8",),
        pod_key="test-only",
    )

    assert result == [
        "/apps/starccm+",
        "-machinefile",
        "/work/hosts.ma",
        "-rsh",
        "ssh",
        "-np",
        "8",
        "-mppflags",
        "-x UCX_DC_MLX5_NUM_DCI=8",
        "-podkey",
        "test-only",
        "-batch",
        "/work/macro.java",
        "/work/case.sim",
    ]


def test_single_core_cpu_omits_np():
    """num_cores=1 时 CPU 不添加 -np 的现状必须保留。"""
    result = _build_starccm_command(
        "starccm+",
        Path("/work/macro.java"),
        Path("/work/case.sim"),
        num_cores=1,
        pod_key="",
    )

    assert result == ["starccm+", "-batch", "/work/macro.java", "/work/case.sim"]
    assert "-np" not in result


def test_cpu_command_without_machinefile_or_podkey():
    result = _build_starccm_command(
        "starccm+",
        Path("/m.java"),
        Path("/c.sim"),
        num_cores=64,
        pod_key="",
    )

    assert result == ["starccm+", "-np", "64", "-batch", "/m.java", "/c.sim"]


def test_multiple_mpi_env_entries_share_one_mppflags():
    result = _build_starccm_command(
        "starccm+",
        Path("/m.java"),
        Path("/c.sim"),
        num_cores=4,
        mpi_env=("UCX_DC_MLX5_NUM_DCI=8", "OMPI_MCA_btl=self,tcp"),
        pod_key="",
    )

    assert result == [
        "starccm+",
        "-np",
        "4",
        "-mppflags",
        "-x UCX_DC_MLX5_NUM_DCI=8 -x OMPI_MCA_btl=self,tcp",
        "-batch",
        "/m.java",
        "/c.sim",
    ]


@pytest.mark.parametrize("bad_env", ["NOEQUALS", "=VALUE", "BAD NAME=1", "NAME=", "NAME=a b"])
def test_invalid_mpi_env_still_rejected(bad_env):
    with pytest.raises(ValueError, match="MPI environment"):
        _build_starccm_command(
            "starccm+",
            Path("/m.java"),
            Path("/c.sim"),
            num_cores=2,
            mpi_env=(bad_env,),
            pod_key="",
        )


_CPU_SCENARIOS = [
    pytest.param(
        {
            "starccm_path": "/apps/starccm+",
            "macro_path": Path("/work/macro.java"),
            "sim_path": Path("/work/case.sim"),
            "num_cores": 8,
            "machinefile_path": Path("/work/hosts.ma"),
            "mpi_env": ("UCX_DC_MLX5_NUM_DCI=8",),
            "pod_key": "test-only",
        },
        id="full-cpu-cluster",
    ),
    pytest.param(
        {
            "starccm_path": "starccm+",
            "macro_path": Path("/m.java"),
            "sim_path": Path("/c.sim"),
            "num_cores": 1,
            "machinefile_path": None,
            "mpi_env": (),
            "pod_key": "",
        },
        id="single-process-no-options",
    ),
    pytest.param(
        {
            "starccm_path": "starccm+",
            "macro_path": Path("/m.java"),
            "sim_path": Path("/c.sim"),
            "num_cores": 384,
            "machinefile_path": Path("/hosts.ma"),
            "mpi_env": ("UCX_DC_MLX5_NUM_DCI=8",),
            "pod_key": "",
        },
        id="slurm-style-384-ranks",
    ),
]


@pytest.mark.parametrize("kwargs", _CPU_SCENARIOS)
def test_gpu_argument_absent_or_none_is_byte_identical(kwargs):
    legacy = _build_starccm_command(**kwargs)

    assert _build_starccm_command(**kwargs, gpu=None) == legacy


@pytest.mark.parametrize("kwargs", _CPU_SCENARIOS)
def test_default_cpu_gpu_config_is_byte_identical(kwargs):
    legacy = _build_starccm_command(**kwargs)

    assert _build_starccm_command(**kwargs, gpu=GPUExecutionConfig()) == legacy


def test_gpu_tokens_are_inserted_immediately_before_batch():
    legacy = _build_starccm_command(
        "/apps/starccm+",
        Path("/work/macro.java"),
        Path("/work/case.sim"),
        num_cores=2,
        machinefile_path=Path("/work/hosts.ma"),
        mpi_env=("UCX_DC_MLX5_NUM_DCI=8",),
        pod_key="test-only",
    )
    gpu_command = _build_starccm_command(
        "/apps/starccm+",
        Path("/work/macro.java"),
        Path("/work/case.sim"),
        num_cores=2,
        machinefile_path=Path("/work/hosts.ma"),
        mpi_env=("UCX_DC_MLX5_NUM_DCI=8",),
        pod_key="test-only",
        gpu=GPUExecutionConfig(backend="gpu", selection="auto:2:nomps"),
    )

    batch_index = legacy.index("-batch")
    assert gpu_command == (
        legacy[:batch_index]
        + ["-gpgpu", "auto:2:nomps", "-require-gpgpu-compatibility"]
        + legacy[batch_index:]
    )
    assert gpu_command[-2:] == ["/work/macro.java", "/work/case.sim"]


def test_gpu_does_not_change_num_cores_semantics():
    """--np 仍是 STAR 进程数，不能变成显卡数或与 GPU 数相乘。"""
    gpu_command = _build_starccm_command(
        "starccm+",
        Path("/m.java"),
        Path("/c.sim"),
        num_cores=2,
        pod_key="",
        gpu=GPUExecutionConfig(backend="gpu", selection="auto:2:nomps"),
    )

    assert gpu_command[gpu_command.index("-np") + 1] == "2"


def test_gpu_single_rank_keeps_legacy_np_omission():
    """单 rank 是否显式发出 -np 1 需 G0 同版本核验（B-01）；当前保持 CPU 现状。"""
    gpu_command = _build_starccm_command(
        "starccm+",
        Path("/m.java"),
        Path("/c.sim"),
        num_cores=1,
        pod_key="",
        gpu=GPUExecutionConfig(backend="gpu", selection="auto:1:nomps"),
    )

    assert gpu_command == [
        "starccm+",
        "-gpgpu",
        "auto:1:nomps",
        "-require-gpgpu-compatibility",
        "-batch",
        "/m.java",
        "/c.sim",
    ]


def test_contradictory_cpu_config_is_not_silently_ignored():
    """CPU 携带 selection 的矛盾配置必须在命令构造阶段报错。"""
    with pytest.raises(GPUConfigurationError):
        _build_starccm_command(
            "starccm+",
            Path("/m.java"),
            Path("/c.sim"),
            num_cores=2,
            pod_key="",
            gpu=GPUExecutionConfig(backend="cpu", selection="auto:2:nomps"),
        )


def test_run_config_gpu_field_is_last_and_defaults_to_cpu():
    field_names = [f.name for f in dataclasses.fields(FlowControlStarCCMRunConfig)]

    assert field_names[-1] == "gpu"
    assert FlowControlStarCCMRunConfig(
        schedule_path=Path("/s.csv"),
        sim_path=Path("/c.sim"),
        output_dir=Path("/out"),
    ).gpu == GPUExecutionConfig()


def test_legacy_positional_run_config_still_works():
    config = FlowControlStarCCMRunConfig(
        Path("/s.csv"),
        Path("/c.sim"),
        Path("/out"),
        "starccm+",
        8,
    )

    assert config.schedule_path == Path("/s.csv")
    assert config.sim_path == Path("/c.sim")
    assert config.output_dir == Path("/out")
    assert config.starccm_path == "starccm+"
    assert config.num_cores == 8
    assert config.gpu.backend == "cpu"


def _write_schedule(tmp_path: Path) -> Path:
    action = ActuationConfig(
        mode="no_jet_reference",
        total_windows=1,
        window_duration=0.1,
        output_dir=tmp_path / "schedule",
    )
    table, extra, errors = generate_pulse(action)
    assert errors == []
    write_pattern_outputs(action, table, extra=extra)
    return action.output_dir / "actuation_schedule.csv"


def test_cpu_dry_run_probes_nothing_and_emits_no_gpu_tokens(tmp_path):
    """CPU 路径零 GPU 外部命令：不探测驱动、不调用 nvidia-smi、不产生 sidecar。"""
    schedule_path = _write_schedule(tmp_path)
    output_dir = tmp_path / "cpu"
    config = FlowControlStarCCMRunConfig(
        schedule_path=schedule_path,
        sim_path=tmp_path / "not-needed.sim",
        output_dir=output_dir,
        dry_run=True,
    )

    with (
        patch("subprocess.run", side_effect=AssertionError("CPU dry-run 不允许启动子进程")),
        patch("subprocess.Popen", side_effect=AssertionError("CPU dry-run 不允许启动子进程")),
        patch("shutil.which", side_effect=AssertionError("CPU dry-run 不允许探测可执行文件")),
    ):
        result = FlowControlStarCCMRunner().run(config)

    assert "-gpgpu" not in result.command
    assert "-require-gpgpu-compatibility" not in result.command
    assert not (output_dir / "gpu_execution.json").exists()
    assert (output_dir / "FlowControlRunMacro.java").is_file()
    assert (output_dir / "starccm_runtime_plan.json").is_file()
