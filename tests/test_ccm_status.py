from unittest.mock import Mock, patch

import yaml

from flow_control.cli.ccm_status import main


def test_ccm_status_reports_and_persists_progress(tmp_path, capsys):
    raw_dir = tmp_path / "case" / "raw_star"
    raw_dir.mkdir(parents=True)
    manifest_path = raw_dir / "case_manifest.yaml"
    manifest_path.write_text(
        yaml.safe_dump(
            {
                "star": {"version": "17.06.007-R8", "version_source": "runtime"},
                "runtime": {
                    "status": "running",
                    "slurm_job_id": "8096781",
                    "nodes": ["n01", "n02"],
                    "requested_processes": 128,
                    "total_steps": 4,
                    "runtime_log": str(raw_dir / "starccm_flow_control.log"),
                },
            },
            sort_keys=False,
        ),
        encoding="utf-8",
    )
    (raw_dir / "timeseries.csv").write_text(
        "physical_time\n0.1\n0.2\n",
        encoding="utf-8",
    )
    (raw_dir / "starccm_flow_control.log").write_text(
        "Total number of processes: 128\niteration 2\n",
        encoding="utf-8",
    )
    squeue = Mock(returncode=0, stdout="RUNNING\n")

    with patch("flow_control.cli.ccm_status.subprocess.run", return_value=squeue):
        assert main(["--out", str(tmp_path / "case"), "--tail", "1"]) == 0

    output = capsys.readouterr().out
    assert "MPI进程: actual=128 requested=128" in output
    assert "Step: 2/4 (50.00%)" in output
    updated = yaml.safe_load(manifest_path.read_text(encoding="utf-8"))
    assert updated["runtime"]["completed_steps"] == 2
    assert updated["runtime"]["progress_percent"] == 50.0
    assert updated["runtime"]["slurm_state"] == "RUNNING"


def test_ccm_status_bootstraps_manifest_for_already_running_legacy_job(tmp_path, capsys):
    raw_dir = tmp_path / "case" / "raw_star"
    raw_dir.mkdir(parents=True)
    (raw_dir / "hosts_8096781.ma").write_text("n01:64\nn02:64\n", encoding="utf-8")
    (raw_dir / "actuation_schedule.csv").write_text(
        "physical_time\n0.0\n0.1\n0.2\n",
        encoding="utf-8",
    )
    (raw_dir / "timeseries.csv").write_text("physical_time\n0.1\n", encoding="utf-8")
    (raw_dir / "starccm_flow_control.log").write_text(
        "Simcenter STAR-CCM+ 2210 Build 17.06.007 (linux-x86_64-r8)\n"
        "MPI Distribution : Open MPI-4.1.2\n"
        "Host 0 -- n01 -- Ranks 0-63\n"
        "Host 1 -- n02 -- Ranks 64-127\n"
        "Total number of processes : 128\n",
        encoding="utf-8",
    )

    with patch("flow_control.cli.ccm_status.subprocess.run", return_value=Mock(stdout="RUNNING\n")):
        assert main(["--out", str(raw_dir), "--tail", "0"]) == 0

    output = capsys.readouterr().out
    assert "Slurm Job: 8096781 RUNNING" in output
    assert "MPI进程: actual=128 requested=128" in output
    manifest = yaml.safe_load((raw_dir / "case_manifest.yaml").read_text(encoding="utf-8"))
    assert manifest["manifest_status"] == "runtime_status_bootstrapped_from_existing_log"
    assert manifest["runtime"]["total_steps"] == 3
