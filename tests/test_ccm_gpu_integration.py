"""CCM CLI 的 GPU 接入与执行模式隔离（任务 2）。

覆盖计划第 5.1 节的三个新参数、GPU 必须显式 --np、CPU 冲突参数在启动前报错，
以及四种执行模式中只有 run 才允许发生 GPU 取证。
"""

from __future__ import annotations

import importlib.util
from pathlib import Path
from unittest.mock import patch

import pytest

from flow_control.adapters.starccm_runner import (
    FlowControlStarCCMRunConfig,
    FlowControlStarCCMRunner,
)
from flow_control.cli.run_starccm import main
from flow_control.excitation_patterns.common import ActuationConfig, write_pattern_outputs
from flow_control.excitation_patterns.pulse import generate as generate_pulse
from starccm.runtime.gpu_config import GPUExecutionConfig


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


def _base_argv(tmp_path: Path, schedule_path: Path, out_dir: Path, *extra: str) -> list[str]:
    return [
        "--schedule",
        str(schedule_path),
        "--sim",
        str(tmp_path / "template.sim"),
        "--out",
        str(out_dir),
        "--starccm-path",
        "/apps/starccm+",
        "--manifest-template",
        "",
        *extra,
    ]


def _run_main(argv: list[str]) -> dict[str, object]:
    """执行 main 并记录传给 runner 的配置。"""
    captured: dict[str, object] = {}
    original_run = FlowControlStarCCMRunner.run

    def _recording_run(self, config):
        captured["config"] = config
        return original_run(self, config)

    with patch.object(FlowControlStarCCMRunner, "run", _recording_run):
        captured["exit_code"] = main(argv)
    return captured


def _no_external_process_guards():
    return (
        patch("subprocess.run", side_effect=AssertionError("不允许启动外部进程")),
        patch("subprocess.Popen", side_effect=AssertionError("不允许启动外部进程")),
        patch("shutil.which", side_effect=AssertionError("不允许探测可执行文件")),
    )


def test_legacy_argv_still_builds_default_cpu_config(tmp_path):
    schedule_path = _write_schedule(tmp_path)
    out_dir = tmp_path / "raw_star"

    with patch("subprocess.Popen", side_effect=AssertionError("dry-run 不应启动 STAR")):
        captured = _run_main(_base_argv(tmp_path, schedule_path, out_dir, "--execution-mode", "dry-run"))

    config = captured["config"]
    assert captured["exit_code"] == 0
    assert config.gpu == GPUExecutionConfig()
    assert config.num_cores == 1
    assert not (out_dir / "gpu_execution.json").exists()


def test_explicit_cpu_backend_keeps_legacy_np_inference(tmp_path):
    schedule_path = _write_schedule(tmp_path)

    captured = _run_main(
        _base_argv(
            tmp_path,
            schedule_path,
            tmp_path / "raw_star",
            "--compute-backend",
            "cpu",
            "--execution-mode",
            "dry-run",
        )
    )

    assert captured["exit_code"] == 0
    assert captured["config"].num_cores == 1
    assert captured["config"].gpu.backend == "cpu"


def test_gpu_argv_builds_expected_config_and_command(tmp_path, capsys):
    schedule_path = _write_schedule(tmp_path)
    out_dir = tmp_path / "gpu_dry"

    captured = _run_main(
        _base_argv(
            tmp_path,
            schedule_path,
            out_dir,
            "--np",
            "2",
            "--compute-backend",
            "gpu",
            "--gpgpu",
            "auto:2:nomps",
            "--execution-mode",
            "dry-run",
        )
    )
    printed = capsys.readouterr().out

    config = captured["config"]
    assert captured["exit_code"] == 0
    assert config.gpu == GPUExecutionConfig(backend="gpu", selection="auto:2:nomps")
    assert config.num_cores == 2
    assert "command: /apps/starccm+ -np 2 -gpgpu auto:2:nomps -require-gpgpu-compatibility -batch" in printed


def test_gpu_dry_run_does_not_require_qualification(tmp_path):
    schedule_path = _write_schedule(tmp_path)

    captured = _run_main(
        _base_argv(
            tmp_path,
            schedule_path,
            tmp_path / "gpu_dry",
            "--np",
            "1",
            "--compute-backend",
            "gpu",
            "--gpgpu",
            "auto:1:nomps",
            "--execution-mode",
            "dry-run",
        )
    )

    assert captured["exit_code"] == 0
    assert captured["config"].gpu.qualification_path is None


def test_gpu_qualification_path_is_passed_through(tmp_path):
    schedule_path = _write_schedule(tmp_path)
    qualification = tmp_path / "gpu qualification v1.json"
    qualification.write_text("{}", encoding="utf-8")

    captured = _run_main(
        _base_argv(
            tmp_path,
            schedule_path,
            tmp_path / "gpu_dry",
            "--np",
            "2",
            "--compute-backend",
            "gpu",
            "--gpgpu",
            "auto:2:nomps",
            "--gpu-qualification",
            str(qualification),
            "--execution-mode",
            "dry-run",
        )
    )

    assert captured["config"].gpu.qualification_path == qualification


def test_gpu_requires_explicit_np(tmp_path, capsys):
    schedule_path = _write_schedule(tmp_path)

    with pytest.raises(SystemExit) as excinfo:
        main(
            _base_argv(
                tmp_path,
                schedule_path,
                tmp_path / "gpu",
                "--compute-backend",
                "gpu",
                "--gpgpu",
                "auto:2:nomps",
                "--execution-mode",
                "dry-run",
            )
        )

    assert excinfo.value.code == 2
    assert "GPU模式必须显式指定 --np" in capsys.readouterr().err


def test_gpu_run_requires_qualification_before_any_launch(tmp_path, capsys):
    schedule_path = _write_schedule(tmp_path)
    sim_path = tmp_path / "template.sim"
    sim_path.write_bytes(b"placeholder")
    run_guard, popen_guard, which_guard = _no_external_process_guards()

    with run_guard, popen_guard, which_guard, pytest.raises(SystemExit) as excinfo:
        main(
            _base_argv(
                tmp_path,
                schedule_path,
                tmp_path / "gpu_run",
                "--np",
                "1",
                "--compute-backend",
                "gpu",
                "--gpgpu",
                "auto:1:nomps",
                "--execution-mode",
                "run",
            )
        )

    assert excinfo.value.code == 2
    assert "--gpu-qualification" in capsys.readouterr().err
    assert not (tmp_path / "gpu_run").exists()


def test_cpu_with_gpgpu_is_rejected_before_slurm_resolution(tmp_path, capsys):
    schedule_path = _write_schedule(tmp_path)
    run_guard, popen_guard, which_guard = _no_external_process_guards()

    with run_guard, popen_guard, which_guard, pytest.raises(SystemExit) as excinfo:
        main(
            _base_argv(
                tmp_path,
                schedule_path,
                tmp_path / "cpu",
                "--gpgpu",
                "auto:1:nomps",
                "--scheduler",
                "slurm",
                "--slurm-job-id",
                "12345",
                "--execution-mode",
                "dry-run",
            )
        )

    assert excinfo.value.code == 2
    assert "--gpgpu 需要 --compute-backend gpu" in capsys.readouterr().err


def test_cpu_with_gpu_qualification_is_rejected(tmp_path, capsys):
    schedule_path = _write_schedule(tmp_path)

    with pytest.raises(SystemExit) as excinfo:
        main(
            _base_argv(
                tmp_path,
                schedule_path,
                tmp_path / "cpu",
                "--gpu-qualification",
                str(tmp_path / "qual.json"),
                "--execution-mode",
                "dry-run",
            )
        )

    assert excinfo.value.code == 2
    assert "--gpu-qualification 需要 --compute-backend gpu" in capsys.readouterr().err


@pytest.mark.parametrize("selection", ["force:0", "auto", "auto:2", "0,0:nomps", "auto:2:nomps;reboot"])
def test_gpu_invalid_selector_is_reported_by_cli(tmp_path, capsys, selection):
    schedule_path = _write_schedule(tmp_path)

    with pytest.raises(SystemExit) as excinfo:
        main(
            _base_argv(
                tmp_path,
                schedule_path,
                tmp_path / "gpu",
                "--np",
                "1",
                "--compute-backend",
                "gpu",
                "--gpgpu",
                selection,
                "--execution-mode",
                "dry-run",
            )
        )

    assert excinfo.value.code == 2
    error_text = capsys.readouterr().err
    assert "GPU selection" in error_text or "auto 形式" in error_text


def test_gpu_rejects_zero_np(tmp_path, capsys):
    schedule_path = _write_schedule(tmp_path)

    with pytest.raises(SystemExit) as excinfo:
        main(
            _base_argv(
                tmp_path,
                schedule_path,
                tmp_path / "gpu",
                "--np",
                "0",
                "--compute-backend",
                "gpu",
                "--gpgpu",
                "auto:1:nomps",
                "--execution-mode",
                "dry-run",
            )
        )

    assert excinfo.value.code == 2
    assert "--np 必须 >= 1" in capsys.readouterr().err


def test_help_lists_gpu_arguments(tmp_path, capsys):
    with pytest.raises(SystemExit) as excinfo:
        main(["--help"])

    assert excinfo.value.code == 0
    help_text = capsys.readouterr().out
    assert "--compute-backend" in help_text
    assert "--gpgpu" in help_text
    assert "--gpu-qualification" in help_text


def test_cpu_and_gpu_generate_byte_identical_macro_and_plan(tmp_path):
    """全局约束 6：相同输入/相同输出路径下 CPU 宏与 GPU 宏逐字节一致。"""
    schedule_path = _write_schedule(tmp_path)
    out_dir = tmp_path / "shared"
    common = {
        "schedule_path": schedule_path,
        "sim_path": tmp_path / "template.sim",
        "output_dir": out_dir,
        "starccm_path": "/apps/starccm+",
        "num_cores": 2,
        "dry_run": True,
    }
    macro_path = out_dir / "FlowControlRunMacro.java"
    plan_path = out_dir / "starccm_runtime_plan.json"

    cpu_result = FlowControlStarCCMRunner().run(FlowControlStarCCMRunConfig(**common))
    cpu_macro = macro_path.read_bytes()
    cpu_plan = plan_path.read_bytes()

    gpu_result = FlowControlStarCCMRunner().run(
        FlowControlStarCCMRunConfig(
            **common,
            gpu=GPUExecutionConfig(backend="gpu", selection="auto:2:nomps"),
        )
    )

    assert macro_path.read_bytes() == cpu_macro
    assert plan_path.read_bytes() == cpu_plan
    assert gpu_result.macro_path == cpu_result.macro_path
    assert "-gpgpu" not in cpu_result.command
    assert "-gpgpu" in gpu_result.command


def test_workflow_dispatch_table_is_unchanged():
    spec = importlib.util.spec_from_file_location(
        "_workflow_dispatch_probe", Path("scripts/workflow.py")
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    assert set(module.COMMANDS) == {
        "actions",
        "mock",
        "ccm",
        "ccm-status",
        "organize",
        "check",
        "figures",
    }


def test_slurm_machinefile_mutual_exclusion_is_preserved(tmp_path, capsys):
    schedule_path = _write_schedule(tmp_path)
    run_guard, popen_guard, which_guard = _no_external_process_guards()

    with run_guard, popen_guard, which_guard, pytest.raises(SystemExit) as excinfo:
        main(
            _base_argv(
                tmp_path,
                schedule_path,
                tmp_path / "cpu",
                "--scheduler",
                "slurm",
                "--machinefile",
                str(tmp_path / "hosts.ma"),
                "--execution-mode",
                "dry-run",
            )
        )

    assert excinfo.value.code == 2
    assert "--machinefile cannot be combined with --scheduler slurm" in capsys.readouterr().err


def test_gpu_package_only_does_not_probe_or_overwrite_sidecar(tmp_path, capsys):
    schedule_path = _write_schedule(tmp_path)
    out_dir = tmp_path / "raw_star"
    out_dir.mkdir(parents=True)
    sidecar = out_dir / "gpu_execution.json"
    sidecar.write_text('{"schema_version":"ccm_gpu_execution_v1","state":"GPU_CONFIRMED"}\n', encoding="utf-8")
    before = sidecar.read_bytes()
    run_guard, popen_guard, which_guard = _no_external_process_guards()

    with run_guard, popen_guard, which_guard, pytest.raises(FileNotFoundError):
        main(
            _base_argv(
                tmp_path,
                schedule_path,
                out_dir,
                "--np",
                "2",
                "--compute-backend",
                "gpu",
                "--gpgpu",
                "auto:2:nomps",
                "--execution-mode",
                "package-only",
            )
        )

    assert sidecar.read_bytes() == before


def test_gpu_validate_only_does_not_probe_or_overwrite_sidecar(tmp_path):
    schedule_path = _write_schedule(tmp_path)
    out_dir = tmp_path / "raw_star"
    out_dir.mkdir(parents=True)
    sidecar = out_dir / "gpu_execution.json"
    sidecar.write_text('{"state":"FAILED"}\n', encoding="utf-8")
    before = sidecar.read_bytes()
    run_guard, popen_guard, which_guard = _no_external_process_guards()

    with run_guard, popen_guard, which_guard, pytest.raises(RuntimeError, match="validate-only failed"):
        main(
            _base_argv(
                tmp_path,
                schedule_path,
                out_dir,
                "--np",
                "2",
                "--compute-backend",
                "gpu",
                "--gpgpu",
                "auto:2:nomps",
                "--execution-mode",
                "validate-only",
            )
        )

    assert sidecar.read_bytes() == before
