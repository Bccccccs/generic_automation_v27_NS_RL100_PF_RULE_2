"""GPU 执行证据、失败分类与状态机测试（任务 5）。

本文件使用的日志样本全部是 **synthetic（合成）**，只用于验证状态机与错误优先级。
真实 20.02 日志正则尚未采集（docs/gpu/20.02-evidence-register.md 的 B-07），
因此生产注册表为空，任何 build 的真实日志都只能判“未确认”。
"""

from __future__ import annotations

import json
import os
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from flow_control.adapters.starccm_runner import (
    FlowControlStarCCMRunConfig,
    FlowControlStarCCMRunner,
)
from flow_control.excitation_patterns.common import ActuationConfig, write_pattern_outputs
from flow_control.excitation_patterns.pulse import generate as generate_pulse
from starccm.runtime.gpu_config import GPUExecutionConfig
from starccm.runtime.gpu_evidence import (
    LOCK_NAME,
    SIDECAR_NAME,
    STATE_FAILED,
    STATE_GPU_CONFIRMED,
    STATE_RUNNING,
    GPUExecutionError,
    GPULogPatternSet,
    parse_gpu_log,
    read_gpu_evidence,
    validate_gpu_completion,
    write_gpu_evidence,
)

from tests.test_starccm_gpu_preflight import (  # noqa: F401  复用任务 3/4 的 GPU run 夹具
    gpu_run_config,
    gpu_run_inputs,
    gpu_runner_guards,
    four_gpu_stub,
)

@pytest.fixture(autouse=True)
def _isolate_cuda_visible_devices(monkeypatch):
    """默认清空 CUDA_VISIBLE_DEVICES；需要它的测试自行用 monkeypatch 设置。

    与 tests/test_starccm_gpu_preflight.py 里的同名 fixture 各自独立，避免在真实
    GPU 节点上跑测试时（分配内该变量必然被设置）出现意外失败。
    """
    monkeypatch.delenv("CUDA_VISIBLE_DEVICES", raising=False)


SYNTHETIC_PATTERNS = GPULogPatternSet(
    star_build="20.02.007",
    solver_execution=(r"\[SYNTHETIC\] solving on device (?P<device>\d+)",),
    device_initialization=(r"\[SYNTHETIC\] initialized device (?P<device>\d+)",),
    cpu_fallback=(r"\[SYNTHETIC\] falling back to host for (?P<solver>\S+)",),
    out_of_memory=(r"\[SYNTHETIC\] device out of memory",),
    model_unsupported=(r"\[SYNTHETIC\] model unsupported on device: (?P<model>.+)",),
    fatal_errors=(r"\[SYNTHETIC\] FATAL: (?P<detail>.+)",),
)


def synthetic_log(*lines: str) -> str:
    return "\n".join(lines) + "\n"


def running_record(**overrides) -> dict:
    record = {
        "schema_version": "ccm_gpu_execution_v1",
        "request": {
            "backend": "gpu",
            "selection": "auto:2:nomps",
            "mpi_processes": 2,
            "requested_gpu_count": 2,
            "strict_compatibility": True,
        },
        "state": STATE_RUNNING,
        "actual_backend": "unknown",
        "star_return_code": 0,
        "failure_code": None,
        "evidence": [],
        "node": "gpu01",
        "devices": [
            {"index": 0, "local_index": 0, "uuid": "GPU-1111-aaaa"},
            {"index": 1, "local_index": 1, "uuid": "GPU-2222-bbbb"},
        ],
        "inputs": {},
        "timing": {},
        "outputs": {"timeseries": "runs/gpu/timeseries.csv", "row_count": 12},
        "log_analysis": None,
    }
    record.update(overrides)
    return record


def analysed_record(*lines: str, pattern_set=SYNTHETIC_PATTERNS, **overrides) -> dict:
    record = running_record(**overrides)
    record["log_analysis"] = parse_gpu_log(
        synthetic_log(*lines),
        star_build="20.02.007",
        pattern_set=pattern_set,
    )
    return record


# --- 原子写与读取 ---


def test_write_gpu_evidence_is_atomic_and_leaves_no_temp_files(tmp_path):
    target = tmp_path / SIDECAR_NAME
    write_gpu_evidence(target, {"state": "UNVERIFIED"})
    write_gpu_evidence(target, {"state": STATE_RUNNING})

    assert json.loads(target.read_text(encoding="utf-8"))["state"] == STATE_RUNNING
    assert [path.name for path in tmp_path.iterdir()] == [SIDECAR_NAME]


def test_write_gpu_evidence_creates_missing_parent(tmp_path):
    target = tmp_path / "nested" / SIDECAR_NAME

    write_gpu_evidence(target, {"state": "UNVERIFIED"})

    assert read_gpu_evidence(target) == {"state": "UNVERIFIED"}


def test_read_gpu_evidence_returns_none_for_missing_or_corrupt(tmp_path):
    assert read_gpu_evidence(tmp_path / "absent.json") is None
    corrupt = tmp_path / SIDECAR_NAME
    corrupt.write_text("{not json", encoding="utf-8")
    assert read_gpu_evidence(corrupt) is None


# --- 日志解析：未注册 build 只能判未确认 ---


def test_parse_without_registered_patterns_never_confirms_gpu():
    result = parse_gpu_log(
        synthetic_log(
            "STAR-CCM+ 20.02.007 starting",
            "GPU solver running on 2 devices",
            "Using GPGPU for segregated fluid solver",
        ),
        star_build="20.02.007",
    )

    assert result["parser_status"] == "UNVERIFIED_BUILD_NOT_REGISTERED"
    assert result["pattern_source"] == "none"
    assert result["gpu_solver_lines"] == []
    assert result["log_line_count"] == 3


def test_parse_does_not_treat_bare_gpu_keyword_as_success():
    result = parse_gpu_log(
        synthetic_log("GPU", "gpu", "nvidia-smi shows 100% utilization"),
        star_build="20.02.007",
    )

    assert result["parser_status"] != "MATCHED"
    assert result["gpu_solver_lines"] == []


def test_parse_records_unknown_build_explicitly():
    result = parse_gpu_log("anything\n", star_build="17.06.007")

    assert result["star_build"] == "17.06.007"
    assert result["parser_status"] == "UNVERIFIED_BUILD_NOT_REGISTERED"


def test_parse_with_synthetic_patterns_classifies_lines():
    result = parse_gpu_log(
        synthetic_log(
            "[SYNTHETIC] initialized device 0",
            "[SYNTHETIC] solving on device 0",
            "[SYNTHETIC] solving on device 1",
            "unrelated line",
        ),
        star_build="20.02.007",
        pattern_set=SYNTHETIC_PATTERNS,
    )

    assert result["parser_status"] == "MATCHED"
    assert result["pattern_source"] == "synthetic_test_only"
    assert len(result["gpu_solver_lines"]) == 2
    assert len(result["device_init_lines"]) == 1
    assert result["cpu_fallback_lines"] == []


# --- 完成判定：任务 5 的判定表 ---


def test_exit_zero_without_solver_evidence_is_unconfirmed():
    record = analysed_record("[SYNTHETIC] initialized device 0")

    with pytest.raises(GPUExecutionError) as excinfo:
        validate_gpu_completion(record, expected_node="gpu01")

    assert excinfo.value.failure_code == "GPU_EXECUTION_UNCONFIRMED"


def test_unregistered_build_log_is_unconfirmed_even_with_exit_zero():
    record = running_record()
    record["log_analysis"] = parse_gpu_log("GPU solver active\n", star_build="20.02.007")

    with pytest.raises(GPUExecutionError) as excinfo:
        validate_gpu_completion(record, expected_node="gpu01")

    assert excinfo.value.failure_code == "GPU_EXECUTION_UNCONFIRMED"


def test_cpu_fallback_is_detected():
    record = analysed_record(
        "[SYNTHETIC] solving on device 0",
        "[SYNTHETIC] solving on device 1",
        "[SYNTHETIC] falling back to host for segregated-fluid",
    )

    with pytest.raises(GPUExecutionError) as excinfo:
        validate_gpu_completion(record, expected_node="gpu01")

    assert excinfo.value.failure_code == "CPU_FALLBACK_DETECTED"
    assert "segregated-fluid" in str(excinfo.value)


def test_missing_device_evidence_is_detected():
    record = analysed_record(
        "[SYNTHETIC] initialized device 0",
        "[SYNTHETIC] solving on device 0",
    )

    with pytest.raises(GPUExecutionError) as excinfo:
        validate_gpu_completion(record, expected_node="gpu01")

    assert excinfo.value.failure_code == "GPU_DEVICE_EVIDENCE_MISSING"
    assert "1" in str(excinfo.value)


def test_out_of_memory_wins_over_fallback():
    record = analysed_record(
        "[SYNTHETIC] falling back to host for segregated-fluid",
        "[SYNTHETIC] device out of memory",
    )

    with pytest.raises(GPUExecutionError) as excinfo:
        validate_gpu_completion(record, expected_node="gpu01")

    assert excinfo.value.failure_code == "GPU_OUT_OF_MEMORY"


def test_unsupported_model_is_detected():
    record = analysed_record("[SYNTHETIC] model unsupported on device: reacting-flow")

    with pytest.raises(GPUExecutionError) as excinfo:
        validate_gpu_completion(record, expected_node="gpu01")

    assert excinfo.value.failure_code == "GPU_MODEL_UNSUPPORTED"
    assert "reacting-flow" in str(excinfo.value)


def test_node_mismatch_is_detected():
    record = analysed_record(
        "[SYNTHETIC] initialized device 0",
        "[SYNTHETIC] initialized device 1",
        "[SYNTHETIC] solving on device 0",
        "[SYNTHETIC] solving on device 1",
    )

    with pytest.raises(GPUExecutionError) as excinfo:
        validate_gpu_completion(record, expected_node="gpu02")

    assert excinfo.value.failure_code == "NODE_MISMATCH"


def test_nonzero_star_exit_is_preserved():
    record = analysed_record(
        "[SYNTHETIC] solving on device 0",
        "[SYNTHETIC] solving on device 1",
        star_return_code=17,
    )

    with pytest.raises(GPUExecutionError) as excinfo:
        validate_gpu_completion(record, expected_node="gpu01")

    assert excinfo.value.failure_code == "STAR_NONZERO_EXIT"
    assert "17" in str(excinfo.value)


def test_missing_outputs_are_detected():
    record = analysed_record(
        "[SYNTHETIC] initialized device 0",
        "[SYNTHETIC] initialized device 1",
        "[SYNTHETIC] solving on device 0",
        "[SYNTHETIC] solving on device 1",
        outputs={"timeseries": "", "row_count": 0},
    )

    with pytest.raises(GPUExecutionError) as excinfo:
        validate_gpu_completion(record, expected_node="gpu01")

    assert excinfo.value.failure_code == "OUTPUTS_INCOMPLETE"


def test_record_without_launch_is_rejected():
    record = running_record(state="PREFLIGHT_PASSED")

    with pytest.raises(GPUExecutionError) as excinfo:
        validate_gpu_completion(record, expected_node="gpu01")

    assert excinfo.value.failure_code == "NOT_LAUNCHED"


def test_fully_evidenced_run_passes_validation():
    record = analysed_record(
        "[SYNTHETIC] initialized device 0",
        "[SYNTHETIC] initialized device 1",
        "[SYNTHETIC] solving on device 0",
        "[SYNTHETIC] solving on device 1",
    )

    validate_gpu_completion(record, expected_node="gpu01")


# --- runner 生命周期 ---


CONFIRMED_LOG = synthetic_log(
    "[SYNTHETIC] initialized device 0",
    "[SYNTHETIC] initialized device 1",
    "[SYNTHETIC] solving on device 0",
    "[SYNTHETIC] solving on device 1",
)


def _stub_launch(returncode=0, log_text="", raise_exc=None, timeseries_rows=0, result_sim=False):
    def _fake_launch(command, *, log_file, cwd):
        if raise_exc is not None:
            raise raise_exc
        log_file.write(log_text)
        if timeseries_rows:
            rows = ["physical_time,window_id"] + [
                f"{0.1 * (index + 1):.4f},0" for index in range(timeseries_rows)
            ]
            (Path(cwd) / "timeseries.csv").write_text(
                "\n".join(rows) + "\n", encoding="utf-8"
            )
        if result_sim:
            (Path(cwd) / "flow_control_result.sim").write_bytes(b"result-sim")
        return SimpleNamespace(returncode=returncode)

    return _fake_launch


def _patched_synthetic_parse(text, *, star_build):
    return parse_gpu_log(text, star_build=star_build, pattern_set=SYNTHETIC_PATTERNS)


def test_gpu_run_exit_zero_without_real_patterns_is_failed(gpu_run_inputs):
    out_dir = gpu_run_inputs["tmp_path"] / "gpu_run"
    guard_run, guard_os = gpu_runner_guards(gpu_run_inputs)

    with (
        guard_run,
        guard_os,
        patch(
            "flow_control.adapters.starccm_runner._run_starccm_command",
            _stub_launch(returncode=0, log_text="STAR-CCM+ finished\n"),
        ),
        pytest.raises(GPUExecutionError) as excinfo,
    ):
        FlowControlStarCCMRunner().run(gpu_run_config(gpu_run_inputs, out_dir))

    assert excinfo.value.failure_code == "GPU_EXECUTION_UNCONFIRMED"
    record = read_gpu_evidence(out_dir / SIDECAR_NAME)
    assert record["state"] == STATE_FAILED
    assert record["star_return_code"] == 0
    assert record["actual_backend"] == "unknown"
    assert not (out_dir / LOCK_NAME).exists()


def test_gpu_run_confirms_execution_with_registered_patterns(gpu_run_inputs):
    out_dir = gpu_run_inputs["tmp_path"] / "gpu_run"
    guard_run, guard_os = gpu_runner_guards(gpu_run_inputs)
    log_text = synthetic_log(
        "[SYNTHETIC] initialized device 0",
        "[SYNTHETIC] initialized device 1",
        "[SYNTHETIC] solving on device 0",
        "[SYNTHETIC] solving on device 1",
    )

    with (
        guard_run,
        guard_os,
        patch(
            "flow_control.adapters.starccm_runner._run_starccm_command",
            _stub_launch(
                returncode=0, log_text=log_text, timeseries_rows=12, result_sim=True
            ),
        ),
        patch(
            "flow_control.adapters.starccm_runner.parse_gpu_log",
            _patched_synthetic_parse,
        ),
    ):
        result = FlowControlStarCCMRunner().run(gpu_run_config(gpu_run_inputs, out_dir))

    assert result.returncode == 0
    record = read_gpu_evidence(out_dir / SIDECAR_NAME)
    assert record["state"] == STATE_GPU_CONFIRMED
    assert record["actual_backend"] == "gpu"
    assert record["star_return_code"] == 0
    assert len(record["devices"]) == 2
    assert not (out_dir / LOCK_NAME).exists()


def test_gpu_run_nonzero_exit_keeps_real_return_code(gpu_run_inputs):
    out_dir = gpu_run_inputs["tmp_path"] / "gpu_run"
    guard_run, guard_os = gpu_runner_guards(gpu_run_inputs)

    with (
        guard_run,
        guard_os,
        patch(
            "flow_control.adapters.starccm_runner._run_starccm_command",
            _stub_launch(returncode=17, log_text="STAR-CCM+ crashed\n"),
        ),
        pytest.raises(RuntimeError, match="exited with code 17"),
    ):
        FlowControlStarCCMRunner().run(gpu_run_config(gpu_run_inputs, out_dir))

    record = read_gpu_evidence(out_dir / SIDECAR_NAME)
    assert record["state"] == STATE_FAILED
    assert record["failure_code"] == "STAR_NONZERO_EXIT"
    assert record["star_return_code"] == 17
    assert not (out_dir / LOCK_NAME).exists()


def test_gpu_run_interrupted_keeps_completed_steps(gpu_run_inputs):
    out_dir = gpu_run_inputs["tmp_path"] / "gpu_run"
    guard_run, guard_os = gpu_runner_guards(gpu_run_inputs)

    with (
        guard_run,
        guard_os,
        patch(
            "flow_control.adapters.starccm_runner._run_starccm_command",
            _stub_launch(raise_exc=KeyboardInterrupt()),
        ),
        pytest.raises(KeyboardInterrupt),
    ):
        FlowControlStarCCMRunner().run(gpu_run_config(gpu_run_inputs, out_dir))

    record = read_gpu_evidence(out_dir / SIDECAR_NAME)
    assert record["state"] == STATE_FAILED
    assert record["failure_code"] == "INTERRUPTED"
    assert record["star_return_code"] is None
    assert record["outputs"]["row_count"] == 0
    assert not (out_dir / LOCK_NAME).exists()


def test_gpu_run_launch_error_is_classified(gpu_run_inputs):
    out_dir = gpu_run_inputs["tmp_path"] / "gpu_run"
    guard_run, guard_os = gpu_runner_guards(gpu_run_inputs)

    with (
        guard_run,
        guard_os,
        patch(
            "flow_control.adapters.starccm_runner._run_starccm_command",
            _stub_launch(raise_exc=FileNotFoundError("starccm+ not found")),
        ),
        pytest.raises(FileNotFoundError),
    ):
        FlowControlStarCCMRunner().run(gpu_run_config(gpu_run_inputs, out_dir))

    record = read_gpu_evidence(out_dir / SIDECAR_NAME)
    assert record["state"] == STATE_FAILED
    assert record["failure_code"] == "LAUNCH_FAILED"
    assert "starccm+ not found" in record["failure_detail"]


def test_gpu_run_writes_running_state_before_launch(gpu_run_inputs):
    out_dir = gpu_run_inputs["tmp_path"] / "gpu_run"
    guard_run, guard_os = gpu_runner_guards(gpu_run_inputs)
    observed: dict[str, object] = {}

    def _fake_launch(command, *, log_file, cwd):
        observed["record"] = read_gpu_evidence(out_dir / SIDECAR_NAME)
        log_file.write("nothing\n")
        return SimpleNamespace(returncode=1)

    with (
        guard_run,
        guard_os,
        patch("flow_control.adapters.starccm_runner._run_starccm_command", _fake_launch),
        pytest.raises(RuntimeError),
    ):
        FlowControlStarCCMRunner().run(gpu_run_config(gpu_run_inputs, out_dir))

    assert observed["record"]["state"] == STATE_RUNNING
    assert observed["record"]["actual_backend"] == "unknown"


# --- fixture 驱动：合成日志不能认证真实解析器 ---

FIXTURE_DIR = Path("tests/fixtures/starccm_gpu")

EXPECTED_FIXTURE_OUTCOMES = {
    "gpu_solver_confirmed.txt": None,
    "gpu_device_evidence_missing.txt": "GPU_DEVICE_EVIDENCE_MISSING",
    "gpu_cpu_fallback.txt": "CPU_FALLBACK_DETECTED",
    "gpu_out_of_memory.txt": "GPU_OUT_OF_MEMORY",
    "gpu_model_unsupported.txt": "GPU_MODEL_UNSUPPORTED",
}


@pytest.mark.parametrize("name", sorted(EXPECTED_FIXTURE_OUTCOMES))
def test_synthetic_fixtures_never_confirm_gpu_on_production_path(name):
    """生产注册表为空，因此合成日志只能判未确认，不能冒充真机验收。"""
    text = (FIXTURE_DIR / "synthetic" / name).read_text(encoding="utf-8")
    record = running_record()
    record["log_analysis"] = parse_gpu_log(text, star_build="20.02.007")

    assert record["log_analysis"]["parser_status"] == "UNVERIFIED_BUILD_NOT_REGISTERED"
    with pytest.raises(GPUExecutionError) as excinfo:
        validate_gpu_completion(record, expected_node="gpu01")
    assert excinfo.value.failure_code == "GPU_EXECUTION_UNCONFIRMED"


@pytest.mark.parametrize("name", sorted(EXPECTED_FIXTURE_OUTCOMES))
def test_synthetic_fixtures_drive_state_machine(name):
    text = (FIXTURE_DIR / "synthetic" / name).read_text(encoding="utf-8")
    record = running_record()
    record["log_analysis"] = parse_gpu_log(
        text, star_build="20.02.007", pattern_set=SYNTHETIC_PATTERNS
    )
    expected = EXPECTED_FIXTURE_OUTCOMES[name]

    if expected is None:
        validate_gpu_completion(record, expected_node="gpu01")
        return
    with pytest.raises(GPUExecutionError) as excinfo:
        validate_gpu_completion(record, expected_node="gpu01")
    assert excinfo.value.failure_code == expected


def test_production_pattern_registry_matches_real_fixture_availability():
    """登记句式必须有真实脱敏日志支撑；两者必须同时出现（B-07）。"""
    from starccm.runtime.gpu_evidence import REGISTERED_LOG_PATTERNS

    real_dir = FIXTURE_DIR / "real"
    real_logs = sorted([*real_dir.glob("*.log"), *real_dir.glob("*.txt")])

    assert bool(REGISTERED_LOG_PATTERNS) == bool(real_logs)


def test_synthetic_fixtures_are_marked_synthetic():
    for path in sorted((FIXTURE_DIR / "synthetic").glob("*.txt")):
        assert "[SYNTHETIC]" in path.read_text(encoding="utf-8"), path


# --- 脱敏 argv：许可证 token 不得进入 GPU 证据文件 ---


def test_redact_command_masks_podkey():
    from starccm.runtime.gpu_evidence import redact_command

    assert redact_command(
        ["starccm+", "-np", "2", "-podkey", "SECRET-TOKEN", "-batch", "m.java", "c.sim"]
    ) == ["starccm+", "-np", "2", "-podkey", "REDACTED", "-batch", "m.java", "c.sim"]


def test_redact_command_keeps_other_tokens():
    from starccm.runtime.gpu_evidence import redact_command

    command = [
        "/apps/starccm+",
        "-machinefile",
        "/work/hosts.ma",
        "-rsh",
        "ssh",
        "-mppflags",
        "-x UCX_DC_MLX5_NUM_DCI=8",
        "-gpgpu",
        "auto:2:nomps",
        "-require-gpgpu-compatibility",
        "-batch",
        "/work/macro.java",
        "/work/case.sim",
    ]

    assert redact_command(command) == command


def test_gpu_run_sidecar_records_redacted_command_without_podkey(gpu_run_inputs):
    out_dir = gpu_run_inputs["tmp_path"] / "gpu_run"
    guard_run, guard_os = gpu_runner_guards(gpu_run_inputs)
    config = gpu_run_config(gpu_run_inputs, out_dir)
    config = replace(config, pod_key="SUPER-SECRET-POD-KEY")

    with (
        guard_run,
        guard_os,
        patch(
            "flow_control.adapters.starccm_runner._run_starccm_command",
            _stub_launch(returncode=1, log_text="failed\n"),
        ),
        pytest.raises(RuntimeError),
    ):
        FlowControlStarCCMRunner().run(config)

    text = (out_dir / SIDECAR_NAME).read_text(encoding="utf-8")
    assert "SUPER-SECRET-POD-KEY" not in text
    record = read_gpu_evidence(out_dir / SIDECAR_NAME)
    assert record["command"][record["command"].index("-podkey") + 1] == "REDACTED"
    assert "-gpgpu" in record["command"]


def test_gpu_dry_run_sidecar_records_planned_command(gpu_run_inputs):
    out_dir = gpu_run_inputs["tmp_path"] / "gpu_dry"

    FlowControlStarCCMRunner().run(gpu_run_config(gpu_run_inputs, out_dir, mode="dry-run"))

    record = read_gpu_evidence(out_dir / SIDECAR_NAME)
    assert record["state"] == "UNVERIFIED"
    command = record["command"]
    assert command[-6:-2] == [
        "-gpgpu",
        "auto:2:nomps",
        "-require-gpgpu-compatibility",
        "-batch",
    ]
    assert command[-2].endswith("FlowControlRunMacro.java")
    assert command[-1].endswith("template.sim")
    assert record["devices"] == []


def test_gpu_run_without_result_sim_is_incomplete(gpu_run_inputs):
    """配置要求保存结果 sim 时，缺文件不能判 GPU_CONFIRMED。"""
    out_dir = gpu_run_inputs["tmp_path"] / "gpu_run"
    guard_run, guard_os = gpu_runner_guards(gpu_run_inputs)

    with (
        guard_run,
        guard_os,
        patch(
            "flow_control.adapters.starccm_runner._run_starccm_command",
            _stub_launch(returncode=0, log_text=CONFIRMED_LOG, timeseries_rows=12),
        ),
        patch("flow_control.adapters.starccm_runner.parse_gpu_log", _patched_synthetic_parse),
        pytest.raises(GPUExecutionError) as excinfo,
    ):
        FlowControlStarCCMRunner().run(gpu_run_config(gpu_run_inputs, out_dir))

    assert excinfo.value.failure_code == "OUTPUTS_INCOMPLETE"
    record = read_gpu_evidence(out_dir / SIDECAR_NAME)
    assert record["state"] == "FAILED"
    assert record["star_return_code"] == 0
    assert record["actual_backend"] == "unknown"


def test_gpu_run_with_fewer_steps_than_expected_is_incomplete(gpu_run_inputs):
    """退出码 0 但只跑了极少步，不能算完成预期时段。"""
    out_dir = gpu_run_inputs["tmp_path"] / "gpu_run"
    guard_run, guard_os = gpu_runner_guards(gpu_run_inputs)
    config = replace(gpu_run_config(gpu_run_inputs, out_dir), time_step=0.05)

    with (
        guard_run,
        guard_os,
        patch(
            "flow_control.adapters.starccm_runner._run_starccm_command",
            _stub_launch(
                returncode=0, log_text=CONFIRMED_LOG, timeseries_rows=1, result_sim=True
            ),
        ),
        patch("flow_control.adapters.starccm_runner.parse_gpu_log", _patched_synthetic_parse),
        pytest.raises(GPUExecutionError) as excinfo,
    ):
        FlowControlStarCCMRunner().run(config)

    assert excinfo.value.failure_code == "OUTPUTS_INCOMPLETE"
    record = read_gpu_evidence(out_dir / SIDECAR_NAME)
    assert record["state"] == "FAILED"
    assert record["expectations"]["total_steps"] >= 2
    assert record["outputs"]["row_count"] == 1


def test_gpu_dry_run_refuses_to_destroy_confirmed_evidence(gpu_run_inputs):
    """P1-2：dry-run 不得用离线请求记录覆盖已有的真实 GPU 结论。"""
    out_dir = gpu_run_inputs["tmp_path"] / "gpu_dry"
    out_dir.mkdir()
    sidecar = out_dir / SIDECAR_NAME
    confirmed = {
        "schema_version": "ccm_gpu_execution_v1",
        "state": "GPU_CONFIRMED",
        "actual_backend": "gpu",
        "star_return_code": 0,
        "devices": [{"index": 0, "uuid": "GPU-1111-aaaa"}],
    }
    sidecar.write_text(json.dumps(confirmed), encoding="utf-8")
    before = sidecar.read_bytes()

    with pytest.raises(GPUExecutionError) as excinfo:
        FlowControlStarCCMRunner().run(
            gpu_run_config(gpu_run_inputs, out_dir, mode="dry-run")
        )

    assert excinfo.value.failure_code == "SIDECAR_OVERWRITE_FORBIDDEN"
    assert sidecar.read_bytes() == before


def test_gpu_dry_run_may_refresh_its_own_request_record(gpu_run_inputs):
    out_dir = gpu_run_inputs["tmp_path"] / "gpu_dry"
    FlowControlStarCCMRunner().run(gpu_run_config(gpu_run_inputs, out_dir, mode="dry-run"))
    first = read_gpu_evidence(out_dir / SIDECAR_NAME)
    assert first["state"] == "UNVERIFIED"

    FlowControlStarCCMRunner().run(
        gpu_run_config(gpu_run_inputs, out_dir, mode="dry-run", selection="0,1:nomps")
    )

    second = read_gpu_evidence(out_dir / SIDECAR_NAME)
    assert second["state"] == "UNVERIFIED"
    assert second["request"]["selection"] == "0,1:nomps"


# --- 第二轮审查修复：配置校验顺序与 lock 释放健壮性 ---


def test_gpu_dry_run_without_selection_reports_accurate_error(gpu_run_inputs):
    """修复 5：缺 selection 时不能报“GPU selection 必须全小写: 'None'”。"""
    from starccm.runtime.gpu_config import GPUConfigurationError

    config = replace(
        gpu_run_config(gpu_run_inputs, gpu_run_inputs["tmp_path"] / "gpu_dry", mode="dry-run"),
        gpu=GPUExecutionConfig(backend="gpu"),
    )

    with pytest.raises(GPUConfigurationError) as excinfo:
        FlowControlStarCCMRunner().run(config)

    message = str(excinfo.value)
    assert "必须提供 GPU selection" in message
    assert "全小写" not in message


def test_release_gpu_lock_swallows_os_error(tmp_path):
    """修复 6：常在 finally 里调用，unlink 失败不能掩盖原始异常。"""
    from starccm.runtime.gpu_evidence import acquire_gpu_lock, release_gpu_lock

    lock_path = acquire_gpu_lock(tmp_path)

    with patch("pathlib.Path.unlink", side_effect=OSError("device busy")):
        release_gpu_lock(lock_path)


def test_release_gpu_lock_keeps_lock_from_another_host(tmp_path):
    lock_path = tmp_path / LOCK_NAME
    lock_path.write_text(
        json.dumps({"pid": os.getpid(), "host": "some-other-host"}), encoding="utf-8"
    )

    from starccm.runtime.gpu_evidence import release_gpu_lock

    release_gpu_lock(lock_path)

    assert lock_path.is_file()
