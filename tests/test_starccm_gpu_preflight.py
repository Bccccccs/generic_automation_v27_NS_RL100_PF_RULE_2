"""GPU 资格文件校验、单节点预检与已分配资源核验测试（任务 3、任务 4）。

对应计划第 5.3/5.4 节与任务 4。所有外部诊断都用 stub 替换，不在测试机上启动真实
命令；真机证据仍属 BLOCKED（docs/gpu/20.02-evidence-register.md 的 B-01/B-03/B-04）。
"""

from __future__ import annotations

import copy
import hashlib
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
    GPUExecutionError,
    read_gpu_evidence,
)
from starccm.runtime.gpu_preflight import (
    DiagnosticResult,
    GPUPreflightError,
    preflight_gpu,
)
from starccm.runtime.gpu_qualification import (
    GPUQualificationError,
    load_gpu_qualification,
)

NVIDIA_TWO_GPU_CSV = (
    "0, GPU-1111-aaaa, NVIDIA A100-SXM4-40GB, 00000000:1B:00.0, 40960 MiB, 550.54.15, Disabled\n"
    "1, GPU-2222-bbbb, NVIDIA A100-SXM4-40GB, 00000000:3B:00.0, 40960 MiB, 550.54.15, Disabled\n"
)
SCONTROL_JOB_TWO_GPU = (
    "JobId=4242 JobName=ccm JobState=RUNNING NodeList=gpu01 NumCPUs=16 NumTasks=2 "
    "TRES=cpu=16,gres/gpu=2 Gres=gpu:a100:2\n"
)


def valid_qualification() -> dict:
    return {
        "schema_version": "ccm_gpu_qualification_v1",
        "reviewed_at": "2026-09-05",
        "reviewer": "站点运维（示例数据）",
        "star_release": "2502",
        "star_build": "20.02.007",
        "precision": "double",
        "documentation": [
            {
                "title": "Simcenter STAR-CCM+ User Guide",
                "build": "20.02.007",
                "chapter": "Command-Line Reference / GPGPU Options",
                "source": "/apps/star/20.02.007/doc/userguide.pdf",
                "sha256": "a" * 64,
            }
        ],
        "approved_platforms": [
            {
                "os": "Linux",
                "os_id": "rhel",
                "cpu_arch": "x86_64",
                "gpu_vendor": "nvidia",
                "gpu_model": "A100-SXM4-40GB",
                "match_rule": "os 与 cpu_arch 精确相等，gpu_model 取设备名子串",
                "driver_requirement": ">=550.54.15",
                "evidence": "docs/gpu/20.02-evidence-register.md B-03",
            }
        ],
        "approved_launch": {
            "mode": "native",
            "identity": "/apps/star/20.02.007/bin/starccm+",
            "identity_sha256": "b" * 64,
            "scheduler": "slurm",
            "mpi": "intelmpi",
            "mpi_version": "2021.13",
            "launch_method": "machinefile + rsh ssh",
            "approved_ranks_per_gpu": 1,
            "approved_gpu_counts": [1, 2],
            "mps_policy": "disabled",
            "single_node_only": True,
            "gpu_count_source": "scontrol show job -o 的 Gres 字段",
        },
        "sim_review": {
            "sim_sha256": "c" * 64,
            "items": [
                {
                    "name": "Segregated Fluid Solver",
                    "kind": "solver",
                    "status": "compatible",
                    "source": "User Guide / GPGPU Computation",
                },
                {
                    "name": "k-epsilon",
                    "kind": "model",
                    "status": "compatible",
                    "source": "User Guide / GPGPU Computation",
                },
                {
                    "name": "Jet_Reaction_Z",
                    "kind": "report",
                    "status": "compatible",
                    "source": "User Guide / GPGPU Computation",
                },
            ],
        },
        "required_flags": {
            "gpgpu_flag": "-gpgpu",
            "strict_compatibility_flag": "-require-gpgpu-compatibility",
        },
    }


def write_qualification(tmp_path: Path, qualification: dict | None = None) -> Path:
    path = tmp_path / "gpu_qualification.json"
    payload = valid_qualification() if qualification is None else qualification
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return path


def _diag(command, stdout="", returncode=0, stderr="", status="ok") -> DiagnosticResult:
    return DiagnosticResult(
        command=tuple(str(item) for item in command),
        returncode=returncode,
        stdout=stdout,
        stderr=stderr,
        status=status,
        duration_seconds=0.01,
    )


def diagnostic_stub(
    *,
    starccm_path: str = "/apps/star/20.02.007/bin/starccm+",
    version_stdout: str = "STAR-CCM+ 20.02.007-R8 (double precision) linux-x86_64\n",
    uname_stdout: str = "Linux 5.14.0-362.el9.x86_64 x86_64\n",
    nvidia_stdout: str = NVIDIA_TWO_GPU_CSV,
    nvidia_result: DiagnosticResult | None = None,
    compute_apps_stdout: str = "",
    compute_apps_result: DiagnosticResult | None = None,
    scontrol_job_stdout: str = SCONTROL_JOB_TWO_GPU,
    scontrol_hostnames_stdout: str = "gpu01\n",
):
    """默认假设目标设备空闲（compute_apps_stdout 为空）。"""

    def _fake(command):
        program = Path(str(command[0])).name
        if str(command[0]) == starccm_path:
            return _diag(command, version_stdout)
        if program == "uname":
            return _diag(command, uname_stdout)
        if program == "nvidia-smi":
            if any("--query-compute-apps" in str(item) for item in command):
                if compute_apps_result is not None:
                    return replace(compute_apps_result, command=tuple(str(i) for i in command))
                return _diag(command, compute_apps_stdout)
            if nvidia_result is not None:
                return replace(nvidia_result, command=tuple(str(i) for i in command))
            return _diag(command, nvidia_stdout)
        if program == "scontrol":
            if "hostnames" in command:
                return _diag(command, scontrol_hostnames_stdout)
            return _diag(command, scontrol_job_stdout)
        raise AssertionError(f"未预期的诊断命令: {command}")

    return _fake


@pytest.fixture(autouse=True)
def _isolate_cuda_visible_devices(monkeypatch):
    """默认清空 CUDA_VISIBLE_DEVICES；需要它的测试自行用 monkeypatch 设置。"""
    monkeypatch.delenv("CUDA_VISIBLE_DEVICES", raising=False)


def os_release_fixture(tmp_path: Path, text: str = 'ID="rhel"\nVERSION_ID="9.3"\n') -> Path:
    path = tmp_path / "os-release"
    path.write_text(text, encoding="utf-8")
    return path


@pytest.fixture
def preflight_inputs(tmp_path):
    sim_path = tmp_path / "template.sim"
    sim_path.write_bytes(b"placeholder-sim-bytes")
    schedule_path = tmp_path / "actuation_schedule.csv"
    schedule_path.write_text("window_id,t_start,t_end\n0,0.0,0.1\n", encoding="utf-8")
    qualification = valid_qualification()
    qualification["sim_review"]["sim_sha256"] = hashlib.sha256(sim_path.read_bytes()).hexdigest()
    qualification_path = write_qualification(tmp_path, qualification)
    output_dir = tmp_path / "raw_star"
    output_dir.mkdir()
    return {
        "tmp_path": tmp_path,
        "sim_path": sim_path,
        "schedule_path": schedule_path,
        "qualification_path": qualification_path,
        "output_dir": output_dir,
        "starccm_path": "/apps/star/20.02.007/bin/starccm+",
    }


def call_preflight(inputs, *, selection="auto:2:nomps", num_processes=2, scheduler="slurm",
                   job_id="4242", node="gpu01", stub=None, os_release='ID="rhel"\n',
                   machinefile_path=None):
    tmp_path = inputs["tmp_path"]
    os_release_path = os_release_fixture(tmp_path, os_release)
    config = GPUExecutionConfig(
        backend="gpu",
        selection=selection,
        qualification_path=inputs["qualification_path"],
    )
    with (
        patch(
            "starccm.runtime.gpu_preflight._run",
            stub or diagnostic_stub(starccm_path=inputs["starccm_path"]),
        ),
        patch("starccm.runtime.gpu_preflight.OS_RELEASE_PATH", os_release_path),
    ):
        return preflight_gpu(
            config,
            starccm_path=inputs["starccm_path"],
            num_processes=num_processes,
            node=node,
            scheduler=scheduler,
            scheduler_job_id=job_id,
            output_dir=inputs["output_dir"],
            sim_path=inputs["sim_path"],
            schedule_path=inputs["schedule_path"],
            machinefile_path=machinefile_path,
        )


# --- 资格文件校验 ---


def test_valid_qualification_is_accepted(tmp_path):
    path = write_qualification(tmp_path)

    loaded = load_gpu_qualification(path)

    assert loaded["star_build"] == "20.02.007"
    assert loaded["schema_version"] == "ccm_gpu_qualification_v1"


def test_qualification_without_full_build_is_rejected(tmp_path):
    qualification = valid_qualification()
    qualification["star_build"] = "20.02"

    with pytest.raises(GPUQualificationError, match="star_build"):
        load_gpu_qualification(write_qualification(tmp_path, qualification))


@pytest.mark.parametrize(
    "missing",
    [
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
    ],
)
def test_missing_required_field_is_rejected(tmp_path, missing):
    qualification = valid_qualification()
    del qualification[missing]

    with pytest.raises(GPUQualificationError, match=missing):
        load_gpu_qualification(write_qualification(tmp_path, qualification))


def test_unknown_schema_version_is_rejected(tmp_path):
    qualification = valid_qualification()
    qualification["schema_version"] = "ccm_gpu_qualification_v2"

    with pytest.raises(GPUQualificationError, match="schema_version"):
        load_gpu_qualification(write_qualification(tmp_path, qualification))


def test_unknown_top_level_field_is_rejected(tmp_path):
    qualification = valid_qualification()
    qualification["approved_platform"] = qualification.pop("approved_platforms")

    with pytest.raises(GPUQualificationError, match="approved_platform"):
        load_gpu_qualification(write_qualification(tmp_path, qualification))


def test_test_only_qualification_is_rejected_in_production(tmp_path):
    qualification = valid_qualification()
    qualification["test_only"] = True

    with pytest.raises(GPUQualificationError, match="test_only"):
        load_gpu_qualification(write_qualification(tmp_path, qualification))


def test_documentation_without_source_is_rejected(tmp_path):
    qualification = valid_qualification()
    del qualification["documentation"][0]["source"]

    with pytest.raises(GPUQualificationError, match="source"):
        load_gpu_qualification(write_qualification(tmp_path, qualification))


def test_documentation_with_malformed_hash_is_rejected(tmp_path):
    qualification = valid_qualification()
    qualification["documentation"][0]["sha256"] = "not-a-hash"

    with pytest.raises(GPUQualificationError, match="sha256"):
        load_gpu_qualification(write_qualification(tmp_path, qualification))


@pytest.mark.parametrize("field", ["os", "cpu_arch", "gpu_vendor", "gpu_model"])
def test_wildcard_platform_field_is_rejected(tmp_path, field):
    qualification = valid_qualification()
    qualification["approved_platforms"][0][field] = "*"

    with pytest.raises(GPUQualificationError, match="通配"):
        load_gpu_qualification(write_qualification(tmp_path, qualification))


def test_unparseable_driver_requirement_is_rejected(tmp_path):
    qualification = valid_qualification()
    qualification["approved_platforms"][0]["driver_requirement"] = "latest"

    with pytest.raises(GPUQualificationError, match="driver_requirement"):
        load_gpu_qualification(write_qualification(tmp_path, qualification))


def test_future_review_date_is_rejected(tmp_path):
    qualification = valid_qualification()
    qualification["reviewed_at"] = "2999-01-01"

    with pytest.raises(GPUQualificationError, match="reviewed_at"):
        load_gpu_qualification(write_qualification(tmp_path, qualification))


def test_multi_node_launch_approval_is_rejected(tmp_path):
    qualification = valid_qualification()
    qualification["approved_launch"]["single_node_only"] = False

    with pytest.raises(GPUQualificationError, match="单节点"):
        load_gpu_qualification(write_qualification(tmp_path, qualification))


def test_non_unit_rank_per_gpu_is_rejected(tmp_path):
    qualification = valid_qualification()
    qualification["approved_launch"]["approved_ranks_per_gpu"] = 4

    with pytest.raises(GPUQualificationError, match="approved_ranks_per_gpu"):
        load_gpu_qualification(write_qualification(tmp_path, qualification))


def test_enabled_mps_policy_is_rejected(tmp_path):
    qualification = valid_qualification()
    qualification["approved_launch"]["mps_policy"] = "enabled"

    with pytest.raises(GPUQualificationError, match="mps_policy"):
        load_gpu_qualification(write_qualification(tmp_path, qualification))


def test_empty_sim_model_list_is_rejected(tmp_path):
    qualification = valid_qualification()
    qualification["sim_review"]["items"] = []

    with pytest.raises(GPUQualificationError, match="sim_review"):
        load_gpu_qualification(write_qualification(tmp_path, qualification))


@pytest.mark.parametrize("status", ["unknown", "incompatible"])
def test_unresolved_sim_item_blocks_run(tmp_path, status):
    qualification = valid_qualification()
    qualification["sim_review"]["items"][1]["status"] = status

    with pytest.raises(GPUQualificationError, match="k-epsilon"):
        load_gpu_qualification(write_qualification(tmp_path, qualification))


def test_flag_mismatch_is_rejected(tmp_path):
    qualification = valid_qualification()
    qualification["required_flags"]["gpgpu_flag"] = "-gpu"

    with pytest.raises(GPUQualificationError, match="required_flags"):
        load_gpu_qualification(write_qualification(tmp_path, qualification))


def test_invalid_json_is_rejected(tmp_path):
    path = tmp_path / "broken.json"
    path.write_text("{not json", encoding="utf-8")

    with pytest.raises(GPUQualificationError, match="JSON"):
        load_gpu_qualification(path)


def test_missing_qualification_file_raises(tmp_path):
    with pytest.raises(FileNotFoundError):
        load_gpu_qualification(tmp_path / "absent.json")


# --- 预检：通过路径 ---


def test_preflight_passes_and_keeps_requested_selection(preflight_inputs):
    result = call_preflight(preflight_inputs)

    assert result.command_selection == "auto:2:nomps"
    assert result.evidence["request"]["mpi_processes"] == 2
    assert result.evidence["state"] == "PREFLIGHT_PASSED"
    assert len(result.evidence["devices"]) == 2
    assert result.evidence["devices"][0]["uuid"] == "GPU-1111-aaaa"
    assert result.evidence["devices"][0]["memory_total"] == "40960 MiB"
    assert result.evidence["allocation"]["gpu_count"] == 2
    assert result.evidence["allocation"]["nodes"] == ["gpu01"]
    assert result.evidence["inputs"]["sim_sha256"] == hashlib.sha256(
        preflight_inputs["sim_path"].read_bytes()
    ).hexdigest()
    assert result.evidence["star_build"] == "20.02.007"


def test_preflight_records_diagnostics_with_return_codes(preflight_inputs):
    result = call_preflight(preflight_inputs)

    programs = [item["command"][0] for item in result.evidence["diagnostics"]]
    assert any("starccm+" in program for program in programs)
    assert any("nvidia-smi" in program for program in programs)
    assert all(item["status"] == "ok" for item in result.evidence["diagnostics"])


def test_preflight_explicit_device_list_is_accepted(preflight_inputs):
    result = call_preflight(preflight_inputs, selection="0,1:nomps", num_processes=2)

    assert result.command_selection == "0,1:nomps"
    assert result.evidence["request"]["requested_device_indices"] == [0, 1]


def test_preflight_single_gpu_matches_single_rank(preflight_inputs):
    result = call_preflight(
        preflight_inputs,
        selection="auto:1:nomps",
        num_processes=1,
        stub=diagnostic_stub(
            starccm_path=preflight_inputs["starccm_path"],
            nvidia_stdout=NVIDIA_TWO_GPU_CSV.splitlines()[0] + "\n",
            scontrol_job_stdout=(
                "JobId=4242 JobState=RUNNING NodeList=gpu01 NumCPUs=8 NumTasks=1 "
                "TRES=cpu=8,gres/gpu=1 Gres=gpu:a100:1\n"
            ),
        ),
    )

    assert result.command_selection == "auto:1:nomps"
    assert len(result.evidence["devices"]) == 1


# --- 预检：拒绝路径 ---


def test_preflight_rejects_rank_count_different_from_gpu_count(preflight_inputs):
    with pytest.raises(GPUPreflightError) as excinfo:
        call_preflight(preflight_inputs, selection="auto:2:nomps", num_processes=8)

    assert excinfo.value.failure_code == "RANK_GPU_MISMATCH"
    assert "8" in str(excinfo.value)


def test_preflight_rejects_unapproved_gpu_count(preflight_inputs):
    qualification = valid_qualification()
    qualification["approved_launch"]["approved_gpu_counts"] = [1]
    preflight_inputs["qualification_path"] = write_qualification(
        preflight_inputs["tmp_path"], qualification
    )

    with pytest.raises(GPUPreflightError) as excinfo:
        call_preflight(preflight_inputs, selection="auto:2:nomps", num_processes=2)

    assert excinfo.value.failure_code == "GPU_COUNT_NOT_APPROVED"


def test_preflight_rejects_star_build_mismatch(preflight_inputs):
    stub = diagnostic_stub(
        starccm_path=preflight_inputs["starccm_path"],
        version_stdout="STAR-CCM+ 17.06.007-R8 linux-x86_64\n",
    )

    with pytest.raises(GPUPreflightError) as excinfo:
        call_preflight(preflight_inputs, stub=stub)

    assert excinfo.value.failure_code == "STAR_BUILD_MISMATCH"
    assert "17.06.007" in str(excinfo.value)


def test_preflight_rejects_unapproved_platform(preflight_inputs):
    stub = diagnostic_stub(
        starccm_path=preflight_inputs["starccm_path"],
        uname_stdout="Linux 6.5.0-generic aarch64\n",
    )

    with pytest.raises(GPUPreflightError) as excinfo:
        call_preflight(preflight_inputs, stub=stub)

    assert excinfo.value.failure_code == "PLATFORM_NOT_APPROVED"
    assert "aarch64" in str(excinfo.value)


def test_preflight_rejects_unverifiable_os_id(preflight_inputs):
    with pytest.raises(GPUPreflightError) as excinfo:
        call_preflight(preflight_inputs, os_release="")

    assert excinfo.value.failure_code == "PLATFORM_NOT_APPROVED"


def test_preflight_rejects_old_driver(preflight_inputs):
    stub = diagnostic_stub(
        starccm_path=preflight_inputs["starccm_path"],
        nvidia_stdout=(
            "0, GPU-1111-aaaa, NVIDIA A100-SXM4-40GB, 00000000:1B:00.0, 40960 MiB, 535.104.05, Disabled\n"
            "1, GPU-2222-bbbb, NVIDIA A100-SXM4-40GB, 00000000:3B:00.0, 40960 MiB, 535.104.05, Disabled\n"
        ),
    )

    with pytest.raises(GPUPreflightError) as excinfo:
        call_preflight(preflight_inputs, stub=stub)

    assert excinfo.value.failure_code == "PLATFORM_NOT_APPROVED"
    assert "535.104.05" in str(excinfo.value)


def test_preflight_rejects_unapproved_gpu_model(preflight_inputs):
    stub = diagnostic_stub(
        starccm_path=preflight_inputs["starccm_path"],
        nvidia_stdout=(
            "0, GPU-1111-aaaa, NVIDIA L40S, 00000000:1B:00.0, 46068 MiB, 550.54.15, Disabled\n"
            "1, GPU-2222-bbbb, NVIDIA L40S, 00000000:3B:00.0, 46068 MiB, 550.54.15, Disabled\n"
        ),
    )

    with pytest.raises(GPUPreflightError) as excinfo:
        call_preflight(preflight_inputs, stub=stub)

    assert excinfo.value.failure_code == "PLATFORM_NOT_APPROVED"
    assert "L40S" in str(excinfo.value)


def test_preflight_rejects_insufficient_visible_devices(preflight_inputs):
    stub = diagnostic_stub(
        starccm_path=preflight_inputs["starccm_path"],
        nvidia_stdout=NVIDIA_TWO_GPU_CSV.splitlines()[0] + "\n",
    )

    with pytest.raises(GPUPreflightError) as excinfo:
        call_preflight(preflight_inputs, selection="auto:2:nomps", num_processes=2, stub=stub)

    assert excinfo.value.failure_code == "GPU_DEVICE_UNAVAILABLE"
    assert excinfo.value.evidence["devices"]


def test_preflight_rejects_no_supported_device(preflight_inputs):
    stub = diagnostic_stub(starccm_path=preflight_inputs["starccm_path"], nvidia_stdout="\n")

    with pytest.raises(GPUPreflightError) as excinfo:
        call_preflight(preflight_inputs, stub=stub)

    assert excinfo.value.failure_code == "GPU_DEVICE_UNAVAILABLE"


def test_preflight_classifies_tool_timeout_separately(preflight_inputs):
    stub = diagnostic_stub(
        starccm_path=preflight_inputs["starccm_path"],
        nvidia_result=DiagnosticResult(
            command=("nvidia-smi",),
            returncode=None,
            stdout="",
            stderr="",
            status="timeout",
            duration_seconds=15.0,
        ),
    )

    with pytest.raises(GPUPreflightError) as excinfo:
        call_preflight(preflight_inputs, stub=stub)

    assert excinfo.value.failure_code == "GPU_TOOL_TIMEOUT"
    assert "超时" in str(excinfo.value)


def test_preflight_classifies_tool_failure(preflight_inputs):
    stub = diagnostic_stub(
        starccm_path=preflight_inputs["starccm_path"],
        nvidia_result=DiagnosticResult(
            command=("nvidia-smi",),
            returncode=9,
            stdout="",
            stderr="NVIDIA-SMI has failed because it couldn't communicate with the driver.",
            status="failed",
            duration_seconds=0.2,
        ),
    )

    with pytest.raises(GPUPreflightError) as excinfo:
        call_preflight(preflight_inputs, stub=stub)

    assert excinfo.value.failure_code == "GPU_TOOL_FAILED"
    assert excinfo.value.evidence["diagnostics"][-1]["returncode"] == 9


def test_preflight_rejects_sim_hash_mismatch(preflight_inputs):
    preflight_inputs["sim_path"].write_bytes(b"a-different-template")

    with pytest.raises(GPUPreflightError) as excinfo:
        call_preflight(preflight_inputs)

    assert excinfo.value.failure_code == "INPUT_HASH_MISMATCH"
    assert "sim" in str(excinfo.value)


def test_preflight_rejects_slurm_allocation_without_gpu(preflight_inputs):
    stub = diagnostic_stub(
        starccm_path=preflight_inputs["starccm_path"],
        scontrol_job_stdout=(
            "JobId=4242 JobState=RUNNING NodeList=gpu01 NumCPUs=16 NumTasks=2 TRES=cpu=16\n"
        ),
    )

    with pytest.raises(GPUPreflightError) as excinfo:
        call_preflight(preflight_inputs, stub=stub)

    assert excinfo.value.failure_code == "ALLOCATION_MISMATCH"


def test_preflight_rejects_multi_node_allocation(preflight_inputs):
    stub = diagnostic_stub(
        starccm_path=preflight_inputs["starccm_path"],
        scontrol_job_stdout=(
            "JobId=4242 JobState=RUNNING NodeList=gpu[01-02] NumCPUs=32 NumTasks=4 "
            "TRES=cpu=32,gres/gpu=4 Gres=gpu:a100:4\n"
        ),
        scontrol_hostnames_stdout="gpu01\ngpu02\n",
    )

    with pytest.raises(GPUPreflightError) as excinfo:
        call_preflight(preflight_inputs, stub=stub)

    assert excinfo.value.failure_code == "ALLOCATION_MISMATCH"
    assert "单节点" in str(excinfo.value)


def test_preflight_rejects_node_outside_allocation(preflight_inputs):
    with pytest.raises(GPUPreflightError) as excinfo:
        call_preflight(preflight_inputs, node="login01")

    assert excinfo.value.failure_code == "NODE_MISMATCH"


def test_preflight_rejects_scheduler_not_approved(preflight_inputs):
    with pytest.raises(GPUPreflightError) as excinfo:
        call_preflight(preflight_inputs, scheduler="manual", job_id="")

    assert excinfo.value.failure_code == "LAUNCH_NOT_APPROVED"


def test_preflight_requires_qualification_path(preflight_inputs):
    config = GPUExecutionConfig(backend="gpu", selection="auto:2:nomps")

    with pytest.raises(GPUPreflightError) as excinfo:
        preflight_gpu(
            config,
            starccm_path=preflight_inputs["starccm_path"],
            num_processes=2,
            node="gpu01",
            scheduler="slurm",
            scheduler_job_id="4242",
            output_dir=preflight_inputs["output_dir"],
            sim_path=preflight_inputs["sim_path"],
            schedule_path=preflight_inputs["schedule_path"],
        )

    assert excinfo.value.failure_code == "QUALIFICATION_MISSING"


def test_preflight_rejects_cpu_config():
    with pytest.raises(GPUPreflightError) as excinfo:
        preflight_gpu(
            GPUExecutionConfig(),
            starccm_path="/apps/starccm+",
            num_processes=1,
            node="gpu01",
            scheduler="manual",
            scheduler_job_id="",
            output_dir=Path("/tmp/out"),
            sim_path=Path("/tmp/case.sim"),
            schedule_path=Path("/tmp/schedule.csv"),
        )

    assert excinfo.value.failure_code == "BACKEND_MISMATCH"


def test_preflight_does_not_swallow_unknown_device_fields(preflight_inputs):
    stub = diagnostic_stub(
        starccm_path=preflight_inputs["starccm_path"],
        nvidia_stdout=(
            "0, GPU-1111-aaaa, NVIDIA A100-SXM4-40GB, 00000000:1B:00.0, 40960 MiB, 550.54.15, Disabled\n"
            "1, malformed-line\n"
        ),
    )

    with pytest.raises(GPUPreflightError) as excinfo:
        call_preflight(preflight_inputs, stub=stub)

    assert excinfo.value.failure_code == "GPU_TOOL_FAILED"
    assert "malformed-line" in str(excinfo.value)


# --- runner 隔离：CPU 路径不得探测 GPU ---


def test_cpu_dry_run_never_probes_gpu(tmp_path):
    action = ActuationConfig(
        mode="no_jet_reference",
        total_windows=1,
        window_duration=0.1,
        output_dir=tmp_path / "schedule",
    )
    table, extra, errors = generate_pulse(action)
    assert errors == []
    write_pattern_outputs(action, table, extra=extra)
    config = FlowControlStarCCMRunConfig(
        schedule_path=action.output_dir / "actuation_schedule.csv",
        sim_path=tmp_path / "not-needed.sim",
        output_dir=tmp_path / "cpu",
        dry_run=True,
    )
    with patch(
        "flow_control.adapters.starccm_runner.preflight_gpu",
        side_effect=AssertionError("CPU路径不允许探测GPU"),
    ) as probe:
        result = FlowControlStarCCMRunner().run(config)
    probe.assert_not_called()
    assert "-gpgpu" not in result.command
    assert not (config.output_dir / "gpu_execution.json").exists()


def test_cpu_package_only_never_probes_gpu(tmp_path):
    action = ActuationConfig(
        mode="no_jet_reference",
        total_windows=1,
        window_duration=0.1,
        output_dir=tmp_path / "schedule",
    )
    table, extra, errors = generate_pulse(action)
    assert errors == []
    write_pattern_outputs(action, table, extra=extra)
    sim_path = tmp_path / "template.sim"
    sim_path.write_bytes(b"placeholder")
    config = FlowControlStarCCMRunConfig(
        schedule_path=action.output_dir / "actuation_schedule.csv",
        sim_path=sim_path,
        output_dir=tmp_path / "cpu_run",
        execution_mode="package-only",
    )
    with patch(
        "flow_control.adapters.starccm_runner.preflight_gpu",
        side_effect=AssertionError("CPU路径不允许探测GPU"),
    ) as probe:
        with pytest.raises(FileNotFoundError):
            FlowControlStarCCMRunner().run(config)
    probe.assert_not_called()
    assert not (config.output_dir / "gpu_execution.json").exists()


def test_qualification_copy_is_not_mutated_by_preflight(preflight_inputs):
    qualification = copy.deepcopy(valid_qualification())

    call_preflight(preflight_inputs)

    assert valid_qualification() == qualification


# --- runner 层：GPU 门禁、sidecar 与 lock ---


def manual_qualification(sim_sha256: str) -> dict:
    qualification = valid_qualification()
    qualification["approved_launch"]["scheduler"] = "manual"
    qualification["approved_launch"]["launch_method"] = "本机独占，无 machinefile"
    qualification["sim_review"]["sim_sha256"] = sim_sha256
    return qualification


@pytest.fixture
def gpu_run_inputs(tmp_path):
    action = ActuationConfig(
        mode="no_jet_reference",
        total_windows=1,
        window_duration=0.1,
        output_dir=tmp_path / "schedule",
    )
    table, extra, errors = generate_pulse(action)
    assert errors == []
    write_pattern_outputs(action, table, extra=extra)
    sim_path = tmp_path / "template.sim"
    sim_path.write_bytes(b"placeholder-sim-bytes")
    qualification_path = tmp_path / "gpu_qualification.json"
    qualification_path.write_text(
        json.dumps(
            manual_qualification(hashlib.sha256(sim_path.read_bytes()).hexdigest()),
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    os_release_path = os_release_fixture(tmp_path)
    return {
        "tmp_path": tmp_path,
        "schedule_path": action.output_dir / "actuation_schedule.csv",
        "sim_path": sim_path,
        "qualification_path": qualification_path,
        "starccm_path": "/apps/star/20.02.007/bin/starccm+",
        "os_release_path": os_release_path,
    }


def gpu_run_config(inputs, out_dir: Path, *, mode: str = "run", num_cores: int = 2,
                   selection: str = "auto:2:nomps") -> FlowControlStarCCMRunConfig:
    return FlowControlStarCCMRunConfig(
        schedule_path=inputs["schedule_path"],
        sim_path=inputs["sim_path"],
        output_dir=out_dir,
        starccm_path=inputs["starccm_path"],
        num_cores=num_cores,
        execution_mode=mode,
        gpu=GPUExecutionConfig(
            backend="gpu",
            selection=selection,
            qualification_path=inputs["qualification_path"] if mode == "run" else None,
        ),
    )


def gpu_runner_guards(inputs, stub=None):
    return (
        patch(
            "starccm.runtime.gpu_preflight._run",
            stub or diagnostic_stub(starccm_path=inputs["starccm_path"]),
        ),
        patch("starccm.runtime.gpu_preflight.OS_RELEASE_PATH", inputs["os_release_path"]),
    )


def test_gpu_run_blocked_by_preflight_writes_sidecar_and_releases_lock(gpu_run_inputs):
    out_dir = gpu_run_inputs["tmp_path"] / "gpu_run"
    gpu_run_inputs["sim_path"].write_bytes(b"a-different-template")
    guard_run, guard_os = gpu_runner_guards(gpu_run_inputs)

    with guard_run, guard_os, pytest.raises(GPUPreflightError) as excinfo:
        FlowControlStarCCMRunner().run(gpu_run_config(gpu_run_inputs, out_dir))

    assert excinfo.value.failure_code == "INPUT_HASH_MISMATCH"
    record = read_gpu_evidence(out_dir / SIDECAR_NAME)
    assert record["state"] == "BLOCKED"
    assert record["failure_code"] == "INPUT_HASH_MISMATCH"
    assert record["actual_backend"] == "unknown"
    assert record["star_return_code"] is None
    assert record["evidence"][0]["diagnostics"]
    assert not (out_dir / LOCK_NAME).exists()
    # 预检失败发生在宏和 runtime plan 写入之前，不留下半套产物
    assert not (out_dir / "FlowControlRunMacro.java").exists()
    assert not (out_dir / "starccm_runtime_plan.json").exists()


def test_gpu_run_preflight_passed_uses_verified_selection(gpu_run_inputs):
    out_dir = gpu_run_inputs["tmp_path"] / "gpu_run"
    guard_run, guard_os = gpu_runner_guards(gpu_run_inputs)
    launched: dict[str, object] = {}

    def _fake_launch(command, *, log_file, cwd):
        launched["command"] = list(command)
        log_file.write("fake star output\n")
        return SimpleNamespace(returncode=1)

    with (
        guard_run,
        guard_os,
        patch("flow_control.adapters.starccm_runner._run_starccm_command", _fake_launch),
        pytest.raises(RuntimeError, match="exited with code 1"),
    ):
        FlowControlStarCCMRunner().run(gpu_run_config(gpu_run_inputs, out_dir))

    assert launched["command"][
        launched["command"].index("-gpgpu") : launched["command"].index("-gpgpu") + 3
    ] == ["-gpgpu", "auto:2:nomps", "-require-gpgpu-compatibility"]
    record = read_gpu_evidence(out_dir / SIDECAR_NAME)
    # 任务 5 之后：STAR 非零退出会把状态推进到 FAILED，但预检证据必须保留。
    assert record["state"] == "FAILED"
    assert record["failure_code"] == "STAR_NONZERO_EXIT"
    assert record["star_return_code"] == 1
    assert record["actual_backend"] == "unknown"
    assert len(record["devices"]) == 2
    assert record["evidence"][0]["state"] == "PREFLIGHT_PASSED"
    assert not (out_dir / LOCK_NAME).exists()


def test_gpu_run_refuses_reused_output_dir(gpu_run_inputs):
    out_dir = gpu_run_inputs["tmp_path"] / "gpu_run"
    out_dir.mkdir()
    (out_dir / "timeseries.csv").write_text("physical_time\n0.1\n", encoding="utf-8")
    guard_run, guard_os = gpu_runner_guards(gpu_run_inputs)

    with (
        guard_run,
        guard_os,
        patch(
            "flow_control.adapters.starccm_runner.preflight_gpu",
            side_effect=AssertionError("输出复用被拒绝时不应执行预检"),
        ),
        pytest.raises(GPUExecutionError) as excinfo,
    ):
        FlowControlStarCCMRunner().run(gpu_run_config(gpu_run_inputs, out_dir))

    assert excinfo.value.failure_code == "OUTPUT_REUSE_FORBIDDEN"
    assert not (out_dir / SIDECAR_NAME).exists()
    assert not (out_dir / LOCK_NAME).exists()


def test_gpu_dry_run_writes_unverified_request_only(gpu_run_inputs):
    out_dir = gpu_run_inputs["tmp_path"] / "gpu_dry"
    config = gpu_run_config(gpu_run_inputs, out_dir, mode="dry-run")

    with patch(
        "flow_control.adapters.starccm_runner.preflight_gpu",
        side_effect=AssertionError("dry-run 不允许执行 GPU 预检"),
    ) as probe:
        result = FlowControlStarCCMRunner().run(config)

    probe.assert_not_called()
    assert "-gpgpu" in result.command
    record = read_gpu_evidence(out_dir / SIDECAR_NAME)
    assert record["state"] == "UNVERIFIED"
    assert record["actual_backend"] == "unknown"
    assert record["devices"] == []
    assert record["star_return_code"] is None
    assert record["node"] is None
    assert record["request"]["selection"] == "auto:2:nomps"
    assert not (out_dir / LOCK_NAME).exists()


def test_gpu_run_may_replace_dry_run_request_record(gpu_run_inputs):
    out_dir = gpu_run_inputs["tmp_path"] / "gpu_shared"
    FlowControlStarCCMRunner().run(gpu_run_config(gpu_run_inputs, out_dir, mode="dry-run"))
    assert read_gpu_evidence(out_dir / SIDECAR_NAME)["state"] == "UNVERIFIED"
    gpu_run_inputs["sim_path"].write_bytes(b"changed-after-dry-run")
    guard_run, guard_os = gpu_runner_guards(gpu_run_inputs)

    with guard_run, guard_os, pytest.raises(GPUPreflightError):
        FlowControlStarCCMRunner().run(gpu_run_config(gpu_run_inputs, out_dir))

    record = read_gpu_evidence(out_dir / SIDECAR_NAME)
    assert record["state"] == "BLOCKED"
    assert record["failure_code"] == "INPUT_HASH_MISMATCH"


def test_gpu_run_refuses_to_steal_existing_lock(gpu_run_inputs):
    out_dir = gpu_run_inputs["tmp_path"] / "gpu_run"
    out_dir.mkdir()
    lock_path = out_dir / LOCK_NAME
    foreign = '{"pid": 999999, "host": "gpu01", "acquired_at": "2026-09-07T00:00:00+00:00"}\n'
    lock_path.write_text(foreign, encoding="utf-8")
    guard_run, guard_os = gpu_runner_guards(gpu_run_inputs)

    with (
        guard_run,
        guard_os,
        patch(
            "flow_control.adapters.starccm_runner.preflight_gpu",
            side_effect=AssertionError("lock 被占用时不应执行预检"),
        ),
        pytest.raises(GPUExecutionError) as excinfo,
    ):
        FlowControlStarCCMRunner().run(gpu_run_config(gpu_run_inputs, out_dir))

    assert excinfo.value.failure_code == "GPU_LOCK_HELD"
    assert lock_path.read_text(encoding="utf-8") == foreign


def test_gpu_package_only_keeps_existing_sidecar_untouched(gpu_run_inputs):
    out_dir = gpu_run_inputs["tmp_path"] / "gpu_pkg"
    out_dir.mkdir()
    sidecar = out_dir / SIDECAR_NAME
    sidecar.write_text('{"state":"GPU_CONFIRMED"}\n', encoding="utf-8")
    before = sidecar.read_bytes()
    config = gpu_run_config(gpu_run_inputs, out_dir, mode="package-only")

    with patch(
        "flow_control.adapters.starccm_runner.preflight_gpu",
        side_effect=AssertionError("package-only 不允许执行 GPU 预检"),
    ) as probe, pytest.raises(FileNotFoundError):
        FlowControlStarCCMRunner().run(config)

    probe.assert_not_called()
    assert sidecar.read_bytes() == before


# --- 任务 4：单节点多卡、可见性重映射与已分配资源核验 ---

NVIDIA_FOUR_GPU_CSV = (
    "0, GPU-1111-aaaa, NVIDIA A100-SXM4-40GB, 00000000:1B:00.0, 40960 MiB, 550.54.15, Disabled\n"
    "1, GPU-2222-bbbb, NVIDIA A100-SXM4-40GB, 00000000:3B:00.0, 40960 MiB, 550.54.15, Disabled\n"
    "2, GPU-3333-cccc, NVIDIA A100-SXM4-40GB, 00000000:5E:00.0, 40960 MiB, 550.54.15, Disabled\n"
    "3, GPU-4444-dddd, NVIDIA A100-SXM4-40GB, 00000000:86:00.0, 40960 MiB, 550.54.15, Disabled\n"
)
NVIDIA_MIXED_MODEL_CSV = (
    "0, GPU-1111-aaaa, NVIDIA A100-SXM4-40GB, 00000000:1B:00.0, 40960 MiB, 550.54.15, Disabled\n"
    "1, GPU-2222-bbbb, NVIDIA A100-SXM4-40GB, 00000000:3B:00.0, 40960 MiB, 550.54.15, Disabled\n"
    "2, GPU-5555-eeee, NVIDIA L40S, 00000000:5E:00.0, 46068 MiB, 550.54.15, Disabled\n"
    "3, GPU-6666-ffff, NVIDIA L40S, 00000000:86:00.0, 46068 MiB, 550.54.15, Disabled\n"
)


def four_gpu_stub(inputs, nvidia_stdout=NVIDIA_FOUR_GPU_CSV, job_stdout=SCONTROL_JOB_TWO_GPU):
    return diagnostic_stub(
        starccm_path=inputs["starccm_path"],
        nvidia_stdout=nvidia_stdout,
        scontrol_job_stdout=job_stdout,
    )


def test_visible_index_remap_is_recorded(preflight_inputs, monkeypatch):
    monkeypatch.setenv("CUDA_VISIBLE_DEVICES", "2,3")

    result = call_preflight(preflight_inputs, stub=four_gpu_stub(preflight_inputs))

    devices = result.evidence["devices"]
    assert [device["local_index"] for device in devices] == [0, 1]
    assert [device["index"] for device in devices] == [2, 3]
    assert [device["uuid"] for device in devices] == ["GPU-3333-cccc", "GPU-4444-dddd"]
    assert result.evidence["visibility"]["visible_devices_env"] == "CUDA_VISIBLE_DEVICES"
    assert result.evidence["visibility"]["visible_devices_env_value"] == "2,3"
    assert result.evidence["visibility"]["physical_device_count"] == 4
    assert result.evidence["visibility"]["source"] == "CUDA_VISIBLE_DEVICES"


def test_explicit_selection_uses_local_indices(preflight_inputs, monkeypatch):
    monkeypatch.setenv("CUDA_VISIBLE_DEVICES", "2,3")

    result = call_preflight(
        preflight_inputs, selection="0,1:nomps", stub=four_gpu_stub(preflight_inputs)
    )

    assert result.command_selection == "0,1:nomps"
    assert [device["index"] for device in result.evidence["devices"]] == [2, 3]


def test_explicit_host_indices_outside_visible_set_are_rejected(preflight_inputs, monkeypatch):
    monkeypatch.setenv("CUDA_VISIBLE_DEVICES", "2,3")

    with pytest.raises(GPUPreflightError) as excinfo:
        call_preflight(
            preflight_inputs, selection="2,3:nomps", stub=four_gpu_stub(preflight_inputs)
        )

    assert excinfo.value.failure_code == "GPU_DEVICE_UNAVAILABLE"
    assert "本地" in str(excinfo.value)


def test_fewer_visible_than_physical_blocks_run(preflight_inputs, monkeypatch):
    monkeypatch.setenv("CUDA_VISIBLE_DEVICES", "0")

    with pytest.raises(GPUPreflightError) as excinfo:
        call_preflight(preflight_inputs, stub=four_gpu_stub(preflight_inputs))

    assert excinfo.value.failure_code == "GPU_DEVICE_UNAVAILABLE"
    assert "物理" in str(excinfo.value)


def test_empty_cuda_visible_devices_blocks_run(preflight_inputs, monkeypatch):
    monkeypatch.setenv("CUDA_VISIBLE_DEVICES", "")

    with pytest.raises(GPUPreflightError) as excinfo:
        call_preflight(preflight_inputs, stub=four_gpu_stub(preflight_inputs))

    assert excinfo.value.failure_code == "GPU_DEVICE_UNAVAILABLE"


def test_cuda_visible_devices_uuid_form_is_resolved(preflight_inputs, monkeypatch):
    monkeypatch.setenv("CUDA_VISIBLE_DEVICES", "GPU-3333-cccc,GPU-4444-dddd")

    result = call_preflight(preflight_inputs, stub=four_gpu_stub(preflight_inputs))

    assert [device["index"] for device in result.evidence["devices"]] == [2, 3]
    assert result.evidence["visibility"]["form"] == "uuid"


def test_cuda_visible_devices_unknown_index_blocks(preflight_inputs, monkeypatch):
    monkeypatch.setenv("CUDA_VISIBLE_DEVICES", "9")

    with pytest.raises(GPUPreflightError) as excinfo:
        call_preflight(preflight_inputs, stub=four_gpu_stub(preflight_inputs))

    assert excinfo.value.failure_code == "GPU_DEVICE_UNAVAILABLE"


def test_cuda_visible_devices_unknown_uuid_blocks(preflight_inputs, monkeypatch):
    monkeypatch.setenv("CUDA_VISIBLE_DEVICES", "GPU-does-not-exist")

    with pytest.raises(GPUPreflightError) as excinfo:
        call_preflight(preflight_inputs, stub=four_gpu_stub(preflight_inputs))

    assert excinfo.value.failure_code == "GPU_DEVICE_UNAVAILABLE"


def test_preflight_does_not_modify_environment(preflight_inputs, monkeypatch):
    monkeypatch.setenv("CUDA_VISIBLE_DEVICES", "2,3")
    before = dict(os.environ)

    call_preflight(preflight_inputs, stub=four_gpu_stub(preflight_inputs))

    assert dict(os.environ) == before


def test_slurm_gres_idx_limits_visible_devices(preflight_inputs):
    stub = four_gpu_stub(
        preflight_inputs,
        job_stdout=(
            "JobId=4242 JobState=RUNNING NodeList=gpu01 NumCPUs=16 NumTasks=2 "
            "TRES=cpu=16,gres/gpu=2 Gres=gpu:a100:2(IDX:2,3)\n"
        ),
    )

    result = call_preflight(preflight_inputs, stub=stub)

    assert [device["index"] for device in result.evidence["devices"]] == [2, 3]
    assert result.evidence["visibility"]["source"] == "slurm_gres_idx"
    assert result.evidence["allocation"]["gpu_indices"] == [2, 3]


def test_slurm_gres_idx_conflicting_with_cuda_visible_devices_blocks(preflight_inputs, monkeypatch):
    monkeypatch.setenv("CUDA_VISIBLE_DEVICES", "0,1")
    stub = four_gpu_stub(
        preflight_inputs,
        job_stdout=(
            "JobId=4242 JobState=RUNNING NodeList=gpu01 NumCPUs=16 NumTasks=2 "
            "TRES=cpu=16,gres/gpu=2 Gres=gpu:a100:2(IDX:2,3)\n"
        ),
    )

    with pytest.raises(GPUPreflightError) as excinfo:
        call_preflight(preflight_inputs, stub=stub)

    assert excinfo.value.failure_code == "ALLOCATION_MISMATCH"


def test_slurm_gres_idx_fewer_than_requested_blocks(preflight_inputs):
    stub = four_gpu_stub(
        preflight_inputs,
        job_stdout=(
            "JobId=4242 JobState=RUNNING NodeList=gpu01 NumCPUs=16 NumTasks=2 "
            "TRES=cpu=16,gres/gpu=1 Gres=gpu:a100:1(IDX:0)\n"
        ),
    )

    with pytest.raises(GPUPreflightError) as excinfo:
        call_preflight(preflight_inputs, stub=stub)

    assert excinfo.value.failure_code == "ALLOCATION_MISMATCH"


def test_mixed_model_node_is_rejected_without_visibility_restriction(preflight_inputs):
    # 分配覆盖全部 4 张物理卡，先排除“无法证明用的是哪几张”这一层，
    # 才能真正验证混合型号被拒。
    stub = four_gpu_stub(
        preflight_inputs,
        nvidia_stdout=NVIDIA_MIXED_MODEL_CSV,
        job_stdout=(
            "JobId=4242 JobState=RUNNING NodeList=gpu01 NumCPUs=16 NumTasks=2 "
            "TRES=cpu=16,gres/gpu=4 Gres=gpu:a100:4\n"
        ),
    )

    with pytest.raises(GPUPreflightError) as excinfo:
        call_preflight(preflight_inputs, stub=stub)

    assert excinfo.value.failure_code == "PLATFORM_NOT_APPROVED"
    assert "L40S" in str(excinfo.value)


def test_mixed_model_node_allowed_when_visibility_restricts_to_approved_model(
    preflight_inputs, monkeypatch
):
    monkeypatch.setenv("CUDA_VISIBLE_DEVICES", "0,1")
    stub = four_gpu_stub(preflight_inputs, nvidia_stdout=NVIDIA_MIXED_MODEL_CSV)

    result = call_preflight(preflight_inputs, stub=stub)

    assert [device["name"] for device in result.evidence["devices"]] == [
        "NVIDIA A100-SXM4-40GB",
        "NVIDIA A100-SXM4-40GB",
    ]
    assert result.evidence["visibility"]["mixed_physical_models"] is True


def write_machinefile(tmp_path: Path, text: str) -> Path:
    path = tmp_path / "hosts.ma"
    path.write_text(text, encoding="utf-8")
    return path


def test_machinefile_pointing_to_another_node_is_rejected(preflight_inputs):
    machinefile = write_machinefile(preflight_inputs["tmp_path"], "gpu02:2\n")

    with pytest.raises(GPUPreflightError) as excinfo:
        call_preflight(preflight_inputs, machinefile_path=machinefile)

    assert excinfo.value.failure_code == "MACHINEFILE_NOT_SINGLE_NODE"
    assert "gpu02" in str(excinfo.value)


def test_machinefile_fqdn_of_current_node_is_accepted(preflight_inputs):
    machinefile = write_machinefile(preflight_inputs["tmp_path"], "gpu01.cluster.local:2\n")

    result = call_preflight(preflight_inputs, machinefile_path=machinefile)

    assert result.evidence["machinefile"]["hosts"] == ["gpu01.cluster.local"]
    assert result.evidence["machinefile"]["slots"] == 2


def test_machinefile_with_too_few_slots_is_rejected(preflight_inputs):
    machinefile = write_machinefile(preflight_inputs["tmp_path"], "gpu01:1\n")

    with pytest.raises(GPUPreflightError) as excinfo:
        call_preflight(preflight_inputs, machinefile_path=machinefile)

    assert excinfo.value.failure_code == "MACHINEFILE_SLOTS_INSUFFICIENT"


def test_machinefile_repeated_bare_host_lines_are_counted(preflight_inputs):
    machinefile = write_machinefile(preflight_inputs["tmp_path"], "gpu01\ngpu01\n")

    result = call_preflight(preflight_inputs, machinefile_path=machinefile)

    assert result.evidence["machinefile"]["slots"] == 2


def test_duplicate_device_selection_is_rejected_before_diagnostics(preflight_inputs):
    with pytest.raises(GPUPreflightError) as excinfo:
        call_preflight(preflight_inputs, selection="0,0:nomps", num_processes=2)

    assert excinfo.value.failure_code == "CONFIG_INVALID"


@pytest.mark.parametrize(
    "text",
    [
        "gpu01:2\n",
        "gpu01 slots=4\n",
        "gpu01\ngpu01\n",
        "gpu01:2\ngpu01:3\n",
        "# comment\ngpu01:1\n",
        "gpu01.cluster.local:8\n",
        "\n  gpu01:1  \n",
    ],
)
def test_machinefile_slot_parsing_matches_runner_helper(tmp_path, text):
    """GPU 侧的 machinefile 解析必须与既有 CPU 解析给出相同 slot 数。"""
    from flow_control.adapters.starccm_runner import _machinefile_slot_count
    from starccm.runtime.gpu_preflight import _parse_machinefile

    def _fail(message, failure_code):
        raise ValueError(f"{failure_code}: {message}")

    path = tmp_path / "hosts.ma"
    path.write_text(text, encoding="utf-8")

    assert _parse_machinefile(path, _fail)[1] == _machinefile_slot_count(path)


def test_example_qualification_is_structurally_valid_but_test_only(tmp_path):
    """示例文件必须结构完整（可直接照抄修改），但生产 GPU run 必须拒绝它。"""
    example = Path("examples/ccm_gpu/qualification.example.json")
    payload = json.loads(example.read_text(encoding="utf-8"))
    assert payload["test_only"] is True

    with pytest.raises(GPUQualificationError, match="test_only"):
        load_gpu_qualification(example)

    payload["test_only"] = False
    candidate = tmp_path / "candidate.json"
    candidate.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")

    assert load_gpu_qualification(candidate)["star_build"] == "20.02.007"


# --- 审查修复：P1-1 重复可见条目、P1-4 设备占用、P2-5/P2-6 ---


def test_duplicate_cuda_visible_entries_are_rejected(preflight_inputs, monkeypatch):
    """P1-1：同一张物理卡不能被算作两张可见卡。"""
    monkeypatch.setenv("CUDA_VISIBLE_DEVICES", "0,0")

    with pytest.raises(GPUPreflightError) as excinfo:
        call_preflight(preflight_inputs, stub=four_gpu_stub(preflight_inputs))

    assert excinfo.value.failure_code == "GPU_DEVICE_UNAVAILABLE"
    assert "重复" in str(excinfo.value)


def test_duplicate_cuda_visible_uuids_are_rejected(preflight_inputs, monkeypatch):
    monkeypatch.setenv("CUDA_VISIBLE_DEVICES", "GPU-1111-aaaa,GPU-1111-aaaa")

    with pytest.raises(GPUPreflightError) as excinfo:
        call_preflight(preflight_inputs, stub=four_gpu_stub(preflight_inputs))

    assert excinfo.value.failure_code == "GPU_DEVICE_UNAVAILABLE"


def test_duplicate_slurm_gres_idx_is_rejected(preflight_inputs):
    stub = four_gpu_stub(
        preflight_inputs,
        job_stdout=(
            "JobId=4242 JobState=RUNNING NodeList=gpu01 NumCPUs=16 NumTasks=2 "
            "TRES=cpu=16,gres/gpu=2 Gres=gpu:a100:2(IDX:1,1)\n"
        ),
    )

    with pytest.raises(GPUPreflightError) as excinfo:
        call_preflight(preflight_inputs, stub=stub)

    assert excinfo.value.failure_code == "GPU_DEVICE_UNAVAILABLE"
    assert "重复" in str(excinfo.value)


def test_busy_target_device_blocks_run(preflight_inputs):
    """P1-4：目标卡上已有其他计算进程时不得启动。"""
    stub = four_gpu_stub(
        preflight_inputs,
        nvidia_stdout=NVIDIA_TWO_GPU_CSV,
    )
    busy_stub = diagnostic_stub(
        starccm_path=preflight_inputs["starccm_path"],
        nvidia_stdout=NVIDIA_TWO_GPU_CSV,
        compute_apps_stdout="GPU-1111-aaaa, 424242, /usr/bin/python3, 20480 MiB\n",
    )

    with pytest.raises(GPUPreflightError) as excinfo:
        call_preflight(preflight_inputs, stub=busy_stub)

    assert excinfo.value.failure_code == "DEVICE_IN_USE"
    assert "424242" in str(excinfo.value)
    assert stub is not busy_stub


def test_process_on_other_device_does_not_block_run(preflight_inputs):
    """只检查本次要用的卡；节点上别的卡在跑别人的作业不影响本次分配。"""
    stub = diagnostic_stub(
        starccm_path=preflight_inputs["starccm_path"],
        nvidia_stdout=NVIDIA_TWO_GPU_CSV,
        compute_apps_stdout="GPU-9999-zzzz, 424242, /usr/bin/python3, 20480 MiB\n",
    )

    result = call_preflight(preflight_inputs, stub=stub)

    assert result.evidence["device_occupancy"]["busy"] == []
    assert result.evidence["device_occupancy"]["checked_uuids"] == [
        "GPU-1111-aaaa",
        "GPU-2222-bbbb",
    ]


def test_manual_mode_also_checks_device_occupancy(preflight_inputs):
    """manual 没有作业分配证据，占用冲突检查是唯一防线。"""
    qualification = manual_qualification(
        hashlib.sha256(preflight_inputs["sim_path"].read_bytes()).hexdigest()
    )
    preflight_inputs["qualification_path"] = write_qualification(
        preflight_inputs["tmp_path"], qualification
    )
    stub = diagnostic_stub(
        starccm_path=preflight_inputs["starccm_path"],
        nvidia_stdout=NVIDIA_TWO_GPU_CSV,
        compute_apps_stdout="GPU-2222-bbbb, 31337, /opt/other/starccm+, 40000 MiB\n",
    )

    with pytest.raises(GPUPreflightError) as excinfo:
        call_preflight(preflight_inputs, scheduler="manual", job_id="", stub=stub)

    assert excinfo.value.failure_code == "DEVICE_IN_USE"


def test_unparseable_occupancy_output_is_rejected(preflight_inputs):
    stub = diagnostic_stub(
        starccm_path=preflight_inputs["starccm_path"],
        nvidia_stdout=NVIDIA_TWO_GPU_CSV,
        compute_apps_stdout="garbage-line-without-fields\n",
    )

    with pytest.raises(GPUPreflightError) as excinfo:
        call_preflight(preflight_inputs, stub=stub)

    assert excinfo.value.failure_code == "GPU_TOOL_FAILED"


def test_occupancy_query_timeout_is_classified_separately(preflight_inputs):
    stub = diagnostic_stub(
        starccm_path=preflight_inputs["starccm_path"],
        nvidia_stdout=NVIDIA_TWO_GPU_CSV,
        compute_apps_result=DiagnosticResult(
            command=("nvidia-smi",),
            returncode=None,
            stdout="",
            stderr="",
            status="timeout",
            duration_seconds=15.0,
        ),
    )

    with pytest.raises(GPUPreflightError) as excinfo:
        call_preflight(preflight_inputs, stub=stub)

    assert excinfo.value.failure_code == "GPU_TOOL_TIMEOUT"


def test_shorter_qualification_build_does_not_match_longer_actual(preflight_inputs):
    """P2-5：20.02.00 不能命中实际的 20.02.007-R8。"""
    qualification = valid_qualification()
    qualification["star_build"] = "20.02.00"
    for entry in qualification["documentation"]:
        entry["build"] = "20.02.00"
    preflight_inputs["qualification_path"] = write_qualification(
        preflight_inputs["tmp_path"], qualification
    )

    with pytest.raises(GPUPreflightError) as excinfo:
        call_preflight(preflight_inputs)

    assert excinfo.value.failure_code == "STAR_BUILD_MISMATCH"


def test_second_approved_platform_row_can_match_devices(preflight_inputs):
    """P2-6：批准了多种型号时，命中第二行的设备不应被误判。"""
    qualification = valid_qualification()
    qualification["sim_review"]["sim_sha256"] = hashlib.sha256(
        preflight_inputs["sim_path"].read_bytes()
    ).hexdigest()
    qualification["approved_platforms"].append(
        {
            "os": "Linux",
            "os_id": "rhel",
            "cpu_arch": "x86_64",
            "gpu_vendor": "nvidia",
            "gpu_model": "L40S",
            "match_rule": "os 与 cpu_arch 精确相等，gpu_model 取设备名子串",
            "driver_requirement": ">=550.54.15",
            "evidence": "docs/gpu/20.02-evidence-register.md B-03",
        }
    )
    preflight_inputs["qualification_path"] = write_qualification(
        preflight_inputs["tmp_path"], qualification
    )
    stub = diagnostic_stub(
        starccm_path=preflight_inputs["starccm_path"],
        nvidia_stdout=(
            "0, GPU-5555-eeee, NVIDIA L40S, 00000000:1B:00.0, 46068 MiB, 550.54.15, Disabled\n"
            "1, GPU-6666-ffff, NVIDIA L40S, 00000000:3B:00.0, 46068 MiB, 550.54.15, Disabled\n"
        ),
    )

    result = call_preflight(preflight_inputs, stub=stub)

    assert result.evidence["approved_platform"]["gpu_model"] == "L40S"
    assert len(result.evidence["approved_platform_candidates"]) == 2


@pytest.mark.parametrize("rule", [">=0", "==0", ">=0.0", ">=0.0.0"])
def test_degenerate_driver_rule_is_rejected(tmp_path, rule):
    """P2-7：'>=0' 等价于驱动通配，与不使用无限通配的要求冲突。"""
    qualification = valid_qualification()
    qualification["approved_platforms"][0]["driver_requirement"] = rule

    with pytest.raises(GPUQualificationError, match="通配"):
        load_gpu_qualification(write_qualification(tmp_path, qualification))


# --- 第二轮审查修复：占用范围、忽略名单、MIG、分配歧义 ---

NVIDIA_MIG_CSV = (
    "0, GPU-1111-aaaa, NVIDIA A100-SXM4-40GB, 00000000:1B:00.0, 40960 MiB, 550.54.15, Enabled\n"
    "1, GPU-2222-bbbb, NVIDIA A100-SXM4-40GB, 00000000:3B:00.0, 40960 MiB, 550.54.15, Enabled\n"
)


def test_explicit_selection_only_checks_target_cards(preflight_inputs):
    """修复 1：只用 0 号卡时，邻居卡上有别人的作业不该阻塞本次运行。"""
    stub = diagnostic_stub(
        starccm_path=preflight_inputs["starccm_path"],
        nvidia_stdout=NVIDIA_TWO_GPU_CSV,
        compute_apps_stdout="GPU-2222-bbbb, 31337, /opt/other/starccm+, 40000 MiB\n",
    )

    result = call_preflight(preflight_inputs, selection="0:nomps", num_processes=1, stub=stub)

    assert result.evidence["device_occupancy"]["checked_uuids"] == ["GPU-1111-aaaa"]
    assert result.evidence["device_occupancy"]["busy"] == []


def test_auto_selection_checks_all_requested_cards(preflight_inputs):
    stub = diagnostic_stub(
        starccm_path=preflight_inputs["starccm_path"],
        nvidia_stdout=NVIDIA_TWO_GPU_CSV,
        compute_apps_stdout="GPU-2222-bbbb, 31337, /opt/other/starccm+, 40000 MiB\n",
    )

    with pytest.raises(GPUPreflightError) as excinfo:
        call_preflight(preflight_inputs, selection="auto:2:nomps", num_processes=2, stub=stub)

    assert excinfo.value.failure_code == "DEVICE_IN_USE"


def test_site_agent_can_be_ignored_by_qualification(preflight_inputs):
    """修复 2：MPS/DCGM 这类节点常驻代理需要资格文件显式声明才跳过。"""
    mps_line = "GPU-1111-aaaa, 999, nvidia-cuda-mps-server, 0 MiB\n"
    busy_stub = diagnostic_stub(
        starccm_path=preflight_inputs["starccm_path"],
        nvidia_stdout=NVIDIA_TWO_GPU_CSV,
        compute_apps_stdout=mps_line,
    )

    with pytest.raises(GPUPreflightError) as excinfo:
        call_preflight(preflight_inputs, stub=busy_stub)
    assert excinfo.value.failure_code == "DEVICE_IN_USE"

    qualification = valid_qualification()
    qualification["sim_review"]["sim_sha256"] = hashlib.sha256(
        preflight_inputs["sim_path"].read_bytes()
    ).hexdigest()
    qualification["approved_launch"]["occupancy_ignore_process_names"] = [
        "nvidia-cuda-mps-server"
    ]
    preflight_inputs["qualification_path"] = write_qualification(
        preflight_inputs["tmp_path"], qualification
    )

    result = call_preflight(preflight_inputs, stub=busy_stub)

    assert result.evidence["device_occupancy"]["busy"] == []
    assert result.evidence["device_occupancy"]["ignored"] == [
        "GPU-1111-aaaa(pid=999, nvidia-cuda-mps-server, 0 MiB)"
    ]


def test_mig_enabled_devices_are_rejected(preflight_inputs):
    """修复 2：MIG 未验证不开放，且必须显式拒绝而不是让占用检查静默失效。"""
    stub = diagnostic_stub(
        starccm_path=preflight_inputs["starccm_path"],
        nvidia_stdout=NVIDIA_MIG_CSV,
    )

    with pytest.raises(GPUPreflightError) as excinfo:
        call_preflight(preflight_inputs, stub=stub)

    assert excinfo.value.failure_code == "MIG_NOT_APPROVED"
    assert "GPU-1111-aaaa" in str(excinfo.value)


def test_slurm_without_idx_and_more_physical_cards_is_ambiguous(preflight_inputs):
    """修复 3：分配 2 张但节点有 4 张，且无 IDX/CVD 指明是哪两张 → 拒绝。"""
    stub = four_gpu_stub(preflight_inputs)

    with pytest.raises(GPUPreflightError) as excinfo:
        call_preflight(preflight_inputs, stub=stub)

    assert excinfo.value.failure_code == "ALLOCATION_MISMATCH"
    assert "无法证明" in str(excinfo.value)


def test_slurm_without_idx_matching_physical_count_is_allowed(preflight_inputs):
    stub = diagnostic_stub(
        starccm_path=preflight_inputs["starccm_path"],
        nvidia_stdout=NVIDIA_TWO_GPU_CSV,
        scontrol_job_stdout=SCONTROL_JOB_TWO_GPU,
    )

    result = call_preflight(preflight_inputs, stub=stub)

    assert result.evidence["visibility"]["source"] == "all_physical_devices"


@pytest.mark.parametrize("names", [["*"], [""], ["nvidia*"], "not-a-list", [3]])
def test_wildcard_or_empty_ignore_names_are_rejected(tmp_path, names):
    qualification = valid_qualification()
    qualification["approved_launch"]["occupancy_ignore_process_names"] = names

    with pytest.raises(GPUQualificationError):
        load_gpu_qualification(write_qualification(tmp_path, qualification))
