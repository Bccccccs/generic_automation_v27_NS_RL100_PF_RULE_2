"""海光 HyHAL（hy-smi）GPU 预检测试。

字段格式全部来自真机 ``hy-smi`` 实测输出（8 卡 C-3000/BW 节点，驱动
6.3.31-V1.5.0a），见 docs/gpu/20.02-evidence-register.md 与
docs/STARCCM_GPU.md 的海光小节。所有外部诊断仍用 stub 替换，不在测试机上
启动真实命令；真机 STAR-CCM+ GPU 执行仍属 BLOCKED，本文件只验证预检这一层
的设备枚举/占用检测/MIG 判定逻辑。
"""

from __future__ import annotations

import hashlib
from dataclasses import replace
from pathlib import Path
from unittest.mock import patch

import pytest

from starccm.runtime.gpu_config import GPUExecutionConfig
from starccm.runtime.gpu_preflight import GPUPreflightError, preflight_gpu
from tests.test_starccm_gpu_preflight import (
    _diag,
    os_release_fixture,
    write_qualification,
)

STARCCM_PATH = "/apps/star/20.02.007/bin/starccm+"
VERSION_STDOUT = "STAR-CCM+ 20.02.007-R8 (double precision) linux-x86_64\n"
UNAME_STDOUT = "Linux 5.10.134-17.1.3.sga8.x86_64 x86_64\n"

SHOWUNIQUEID_JSON = (
    '{"card0": {"Unique ID": "TCA24620040801"}, '
    '"card1": {"Unique ID": "TAP34123070901"}}'
)
SHOWBUS_JSON = (
    '{"card0": {"PCI Bus": "0000:09:00.0 --> SN: 01-000206-07SCC2 --> OAM ID: 1"}, '
    '"card1": {"PCI Bus": "0000:36:00.0 --> SN: 01-000206-07SCC2 --> OAM ID: 2"}}'
)
SHOWPRODUCTNAME_JSON = (
    '{"card0": {"Card Series": "BW", "Card Vendor": "C-3000 IC Design Co., Ltd."}, '
    '"card1": {"Card Series": "BW", "Card Vendor": "C-3000 IC Design Co., Ltd."}}'
)
SHOWMEMINFO_JSON = (
    '{"card0": {"vram Total Memory (MiB)": "65520", "vram Total Used Memory (MiB)": "9703"}, '
    '"card1": {"vram Total Memory (MiB)": "65520", "vram Total Used Memory (MiB)": "9651"}}'
)
SHOWDRIVERVERSION_TEXT = (
    "\n================================= System Management Interface =====\n"
    "================================================================================================\n"
    "Driver Version: 6.3.31-V1.5.0a\n"
    "================================================================================================\n"
    "======================================== End of SMI Log ========================================\n"
)
MIG_TABLE_NORMAL_TEXT = (
    "HCU     Temp     AvgPwr     Perf     PwrCap     VRAM%      HCU%      Dec%      Enc%      Mode     \n"
    "0       51.0C    87.0W      auto     1000.0W    15%        0.0%      0.0%      0.0%      Normal   \n"
    "1       51.0C    91.0W      auto     1000.0W    15%        0.0%      0.0%      0.0%      Normal   \n"
)
MIG_TABLE_ENABLED_TEXT = (
    "HCU     Temp     AvgPwr     Perf     PwrCap     VRAM%      HCU%      Dec%      Enc%      Mode     \n"
    "0       51.0C    87.0W      auto     1000.0W    15%        0.0%      0.0%      0.0%      Normal   \n"
    "1       51.0C    91.0W      auto     1000.0W    15%        0.0%      0.0%      0.0%      MIG      \n"
)
SHOWPIDS_EMPTY_TEXT = (
    "\n================================= System Management Interface =====\n"
    "================================================================================================\n"
    "PIDs for KFD processes:\n"
    "\n"
    "================================================================================================\n"
    "======================================== End of SMI Log ========================================\n"
)


def _showpids_busy_text(pci_bus: str, pid: str = "2004092", vram_used: str = "9649") -> str:
    return (
        "PIDs for KFD processes:\n"
        "\n"
        f"PID: {pid}\n"
        "\tPASID: 32770\n"
        "\tHCU Node(Include CPU sort): ['9'] \n"
        "\tHCU Index: ['1'] \n"
        "\tGPUID: ['34430'] \n"
        f"\tPCI BUS: ['{pci_bus}'] \n"
        f"\tVRAM USED(MiB): {vram_used}\n"
        "\tVRAM USED(%): 15\n"
        "\tSDMA USED: 0\n"
        "\n"
        "================================================================================================\n"
    )


def hygon_qualification() -> dict:
    return {
        "schema_version": "ccm_gpu_qualification_v1",
        "reviewed_at": "2026-09-18",
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
                "os_id": "kylin",
                "cpu_arch": "x86_64",
                "gpu_vendor": "hygon",
                "gpu_model": "BW",
                "match_rule": "os 与 cpu_arch 精确相等，gpu_model 取 Card Series 子串",
                "driver_requirement": ">=6.3.31",
                "evidence": "docs/gpu/20.02-evidence-register.md 海光小节",
            }
        ],
        "approved_launch": {
            "mode": "native",
            "identity": STARCCM_PATH,
            "identity_sha256": "b" * 64,
            "scheduler": "slurm",
            "mpi": "hmpi",
            "mpi_version": "1.0",
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
            ],
        },
        "required_flags": {
            "gpgpu_flag": "-gpgpu",
            "strict_compatibility_flag": "-require-gpgpu-compatibility",
        },
    }


@pytest.fixture(autouse=True)
def _isolate_visible_devices_env(monkeypatch):
    monkeypatch.delenv("CUDA_VISIBLE_DEVICES", raising=False)
    monkeypatch.delenv("HIP_VISIBLE_DEVICES", raising=False)


@pytest.fixture
def hygon_inputs(tmp_path):
    sim_path = tmp_path / "template.sim"
    sim_path.write_bytes(b"placeholder-sim-bytes")
    schedule_path = tmp_path / "actuation_schedule.csv"
    schedule_path.write_text("window_id,t_start,t_end\n0,0.0,0.1\n", encoding="utf-8")
    qualification = hygon_qualification()
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
    }


def hygon_diagnostic_stub(
    *,
    showpids_stdout: str = SHOWPIDS_EMPTY_TEXT,
    mig_stdout: str = MIG_TABLE_NORMAL_TEXT,
    driver_stdout: str = SHOWDRIVERVERSION_TEXT,
    uniqueid_stdout: str = SHOWUNIQUEID_JSON,
    bus_stdout: str = SHOWBUS_JSON,
    productname_stdout: str = SHOWPRODUCTNAME_JSON,
    meminfo_stdout: str = SHOWMEMINFO_JSON,
    scontrol_job_stdout: str = (
        "JobId=9001 JobName=ccm JobState=RUNNING NodeList=f11r2n19 NumCPUs=16 NumTasks=2 "
        "TRES=cpu=16,gres/dcu=2 Gres=dcu:2(IDX:0,1)\n"
    ),
    scontrol_hostnames_stdout: str = "f11r2n19\n",
):
    def _fake(command):
        command = [str(item) for item in command]
        if command[0] == STARCCM_PATH:
            return _diag(command, VERSION_STDOUT)
        program = Path(command[0]).name
        if program == "uname":
            return _diag(command, UNAME_STDOUT)
        if program == "scontrol":
            if "hostnames" in command:
                return _diag(command, scontrol_hostnames_stdout)
            return _diag(command, scontrol_job_stdout)
        if program == "hy-smi":
            if command[1:] == ["--showuniqueid", "--json"]:
                return _diag(command, uniqueid_stdout)
            if command[1:] == ["--showbus", "--json"]:
                return _diag(command, bus_stdout)
            if command[1:] == ["--showproductname", "--json"]:
                return _diag(command, productname_stdout)
            if command[1:] == ["--showmeminfo", "vram", "--json"]:
                return _diag(command, meminfo_stdout)
            if command[1:] == ["--showdriverversion"]:
                return _diag(command, driver_stdout)
            if command[1:] == ["--mig"]:
                return _diag(command, mig_stdout)
            if command[1:] == ["--showpids"]:
                return _diag(command, showpids_stdout)
        raise AssertionError(f"未预期的诊断命令: {command}")

    return _fake


def call_hygon_preflight(inputs, *, selection="auto:2:nomps", num_processes=2, stub=None,
                          os_release='ID="kylin"\n', node="f11r2n19", job_id="9001"):
    tmp_path = inputs["tmp_path"]
    os_release_path = os_release_fixture(tmp_path, os_release)
    config = GPUExecutionConfig(
        backend="gpu",
        selection=selection,
        qualification_path=inputs["qualification_path"],
    )
    with (
        patch("starccm.runtime.gpu_preflight._run", stub or hygon_diagnostic_stub()),
        patch("starccm.runtime.gpu_preflight.OS_RELEASE_PATH", os_release_path),
    ):
        return preflight_gpu(
            config,
            starccm_path=STARCCM_PATH,
            num_processes=num_processes,
            node=node,
            scheduler="slurm",
            scheduler_job_id=job_id,
            output_dir=inputs["output_dir"],
            sim_path=inputs["sim_path"],
            schedule_path=inputs["schedule_path"],
            machinefile_path=None,
        )


def test_hygon_preflight_passes_with_real_hy_smi_field_shapes(hygon_inputs):
    result = call_hygon_preflight(hygon_inputs)

    devices = result.evidence["devices"]
    assert [device["index"] for device in devices] == [0, 1]
    assert [device["uuid"] for device in devices] == ["TCA24620040801", "TAP34123070901"]
    assert devices[0]["pci_bus_id"] == "0000:09:00.0"
    assert devices[0]["name"] == "C-3000 IC Design Co., Ltd. BW"
    assert devices[0]["memory_total"] == "65520 MiB"
    assert devices[0]["driver_version"] == "6.3.31-V1.5.0a"
    assert devices[0]["mig_mode_current"] == "disabled"
    assert devices[0]["gpu_vendor"] == "hygon"
    assert result.evidence["state"] == "PREFLIGHT_PASSED"


def test_hygon_mig_enabled_blocks_run(hygon_inputs):
    stub = hygon_diagnostic_stub(mig_stdout=MIG_TABLE_ENABLED_TEXT)

    with pytest.raises(GPUPreflightError) as excinfo:
        call_hygon_preflight(hygon_inputs, stub=stub)

    assert excinfo.value.failure_code == "MIG_NOT_APPROVED"


def test_hygon_busy_target_device_blocks_run(hygon_inputs):
    stub = hygon_diagnostic_stub(showpids_stdout=_showpids_busy_text("0000:09:00.0"))

    with pytest.raises(GPUPreflightError) as excinfo:
        call_hygon_preflight(hygon_inputs, stub=stub)

    assert excinfo.value.failure_code == "DEVICE_IN_USE"


def test_hygon_busy_process_on_other_device_does_not_block(hygon_inputs):
    # 只请求 auto:2:nomps 中的前两张卡 card0/card1；这里的占用发生在
    # 0000:d5:00.0（card6，未被本次选中），不应影响预检结果。
    stub = hygon_diagnostic_stub(showpids_stdout=_showpids_busy_text("0000:d5:00.0"))

    result = call_hygon_preflight(hygon_inputs, stub=stub)

    assert result.evidence["state"] == "PREFLIGHT_PASSED"
    assert result.evidence["device_occupancy"]["busy"] == []


def test_hygon_missing_tool_is_reported_as_gpu_tool_failed(hygon_inputs):
    def _fake(command):
        command = [str(item) for item in command]
        if command[0] == STARCCM_PATH:
            return _diag(command, VERSION_STDOUT)
        program = Path(command[0]).name
        if program == "uname":
            return _diag(command, UNAME_STDOUT)
        if program == "hy-smi":
            return _diag(command, "", returncode=None, stderr="command not found: hy-smi", status="not_found")
        raise AssertionError(f"未预期的诊断命令: {command}")

    with pytest.raises(GPUPreflightError) as excinfo:
        call_hygon_preflight(hygon_inputs, stub=_fake)

    assert excinfo.value.failure_code == "GPU_TOOL_FAILED"


def test_hygon_driver_version_with_build_suffix_is_compared_numerically(hygon_inputs):
    # 资格文件要求 >=6.3.31；真机驱动串 "6.3.31-V1.5.0a" 的数值前缀满足，且带
    # 后缀不应导致解析失败（回归：修复前任何海光驱动串都会被判定为“无法解析”）。
    result = call_hygon_preflight(hygon_inputs)

    assert result.evidence["approved_platform"]["driver_requirement"] == ">=6.3.31"


def test_hygon_driver_version_below_requirement_blocks_run(hygon_inputs):
    qualification = hygon_qualification()
    qualification["sim_review"]["sim_sha256"] = hashlib.sha256(
        hygon_inputs["sim_path"].read_bytes()
    ).hexdigest()
    qualification["approved_platforms"][0]["driver_requirement"] = ">=7.0.0"
    hygon_inputs["qualification_path"] = write_qualification(hygon_inputs["tmp_path"], qualification)

    with pytest.raises(GPUPreflightError) as excinfo:
        call_hygon_preflight(hygon_inputs)

    assert excinfo.value.failure_code == "PLATFORM_NOT_APPROVED"


def test_hygon_hip_visible_devices_conflicting_with_slurm_allocation_blocks(hygon_inputs, monkeypatch):
    # 默认 stub 的 Slurm 分配是 Gres=dcu:2(IDX:0,1)；HIP_VISIBLE_DEVICES 只留 1 张，
    # 两者指向的设备集合不一致，必须判 ALLOCATION_MISMATCH，不能静默取交集。
    monkeypatch.setenv("HIP_VISIBLE_DEVICES", "1")

    with pytest.raises(GPUPreflightError) as excinfo:
        call_hygon_preflight(hygon_inputs, selection="auto:2:nomps", num_processes=2)

    assert excinfo.value.failure_code == "ALLOCATION_MISMATCH"


def test_hygon_hip_visible_devices_matching_slurm_allocation_passes(hygon_inputs, monkeypatch):
    # HIP_VISIBLE_DEVICES 与 Slurm Gres IDX 指向同一组设备（card0/card1）时应放行。
    monkeypatch.setenv("HIP_VISIBLE_DEVICES", "0,1")

    result = call_hygon_preflight(hygon_inputs, selection="auto:2:nomps", num_processes=2)

    assert result.evidence["state"] == "PREFLIGHT_PASSED"
    assert result.evidence["visibility"]["source"] == "slurm_gres_idx+HIP_VISIBLE_DEVICES"


def test_vendor_not_declared_in_qualification_is_blocked(hygon_inputs):
    """资格文件只批准 nvidia 时，海光节点必须被 VENDOR_BLOCKED，而不是被当成 nvidia 查询。"""
    qualification = hygon_qualification()
    qualification["sim_review"]["sim_sha256"] = hashlib.sha256(
        hygon_inputs["sim_path"].read_bytes()
    ).hexdigest()
    qualification["approved_platforms"][0]["gpu_vendor"] = "amd"
    hygon_inputs["qualification_path"] = write_qualification(hygon_inputs["tmp_path"], qualification)

    with pytest.raises(GPUPreflightError) as excinfo:
        call_hygon_preflight(hygon_inputs)

    assert excinfo.value.failure_code == "VENDOR_BLOCKED"


# 真机实测（f11r2n19，job 846214，STARCCM_PLUS_GUI 提交方式）：这套 Slurm 的
# `scontrol show job -o` 完全没有裸的 Gres=/TRES= 键，资源计数只出现在
# AllocTRES=/ReqTRES= 里；回归前 _allocated_gpus 只认 Gres/TRES，导致真实集群
# 上任何 GPU run 都会被误判 ALLOCATION_MISMATCH。
SCONTROL_JOB_ALLOC_TRES_ONLY = (
    "JobId=846214 JobName=STARCCM_PLUS_GUI_0918_181119 UserId=acn6k38urd(30951) "
    "JobState=RUNNING NodeList=f11r2n19 NumCPUs=128 NumTasks=8 "
    "ReqTRES=cpu=128,mem=486G,node=1,billing=128,gres/dcu=8 "
    "AllocTRES=cpu=128,mem=486G,node=1,billing=128,gres/dcu=8 "
    "GresEnforceBind=Yes TresPerNode=gres/dcu:8 TresPerTask=cpu=16\n"
)


def test_hygon_allocation_with_only_alloctres_field_is_recognized(hygon_inputs):
    # 只请求 2 张（qualification 的 approved_gpu_counts 默认是 [1, 2]），
    # AllocTRES 说明这个作业分配了 8 张，2 <= 8，应该放行。
    stub = hygon_diagnostic_stub(scontrol_job_stdout=SCONTROL_JOB_ALLOC_TRES_ONLY)

    result = call_hygon_preflight(hygon_inputs, job_id="846214", stub=stub)

    assert result.evidence["state"] == "PREFLIGHT_PASSED"
    assert result.evidence["allocation"]["gpu_count"] == 8
    assert result.evidence["allocation"]["gpu_indices"] is None
    assert result.evidence["allocation"]["gpu_count_source"] == "TRES"
