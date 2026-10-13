from pathlib import Path
from unittest.mock import patch

import pytest

from flow_control.slurm import SlurmAllocation, preflight_slurm_allocation, resolve_slurm_allocation


def test_resolve_slurm_allocation_writes_machinefile(tmp_path):
    responses = {
        ("scontrol", "show", "job", "-o", "8096781"): (
            "JobId=8096781 JobState=RUNNING NumNodes=6 NumCPUs=384 NumTasks=384 "
            "NodeList=c11r2n[31-36]\n"
        ),
        ("scontrol", "show", "hostnames", "c11r2n[31-36]"): "\n".join(
            f"c11r2n{node}" for node in range(31, 37)
        ) + "\n",
    }

    with patch("flow_control.slurm._run", side_effect=lambda command: responses[tuple(command)]):
        allocation = resolve_slurm_allocation(tmp_path, job_id="8096781")

    assert allocation.job_id == "8096781"
    assert allocation.num_tasks == 384
    assert allocation.nodes == tuple(f"c11r2n{node}" for node in range(31, 37))
    assert allocation.machinefile_path == (tmp_path / "hosts_8096781.ma").resolve()
    assert allocation.machinefile_path.read_text(encoding="utf-8") == "".join(
        f"c11r2n{node}:64\n" for node in range(31, 37)
    )


def test_resolve_slurm_allocation_rejects_more_processes_than_allocated(tmp_path):
    responses = {
        ("scontrol", "show", "job", "-o", "42"): (
            "JobId=42 JobState=RUNNING NumNodes=2 NumCPUs=128 NumTasks=128 NodeList=n[01-02]\n"
        ),
        ("scontrol", "show", "hostnames", "n[01-02]"): "n01\nn02\n",
    }
    with (
        patch("flow_control.slurm._run", side_effect=lambda command: responses[tuple(command)]),
        pytest.raises(ValueError, match="allocates only 128"),
    ):
        resolve_slurm_allocation(tmp_path, job_id="42", num_tasks=256)


def test_resolve_slurm_allocation_requires_running_job(tmp_path):
    with (
        patch(
            "flow_control.slurm._run",
            return_value="JobId=42 JobState=COMPLETED NumTasks=64 NodeList=(null)\n",
        ),
        pytest.raises(RuntimeError, match="not RUNNING"),
    ):
        resolve_slurm_allocation(tmp_path, job_id="42")


def test_slurm_preflight_checks_every_node_and_reports_existing_star_processes(tmp_path, monkeypatch):
    monkeypatch.setenv("USER", "researcher")
    allocation = SlurmAllocation(
        job_id="42",
        nodes=("n01", "n02"),
        num_tasks=128,
        machinefile_path=tmp_path / "hosts_42.ma",
    )

    def fake_run(command):
        node = command[5]
        return f"{node}\n" + ("3\n" if node == "n01" else "0\n")

    with patch("flow_control.slurm._run", side_effect=fake_run):
        result = preflight_slurm_allocation(allocation)

    assert result.existing_star_process_counts == {"n01": 3, "n02": 0}
    assert "n01=3" in result.warnings[0]
