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


# --- GPU sidecar 只读展示（任务 5） ---

import json  # noqa: E402


def _status_case(tmp_path, *, manifest_status="running", sidecar=None, corrupt=False):
    raw_dir = tmp_path / "case" / "raw_star"
    raw_dir.mkdir(parents=True)
    (raw_dir / "case_manifest.yaml").write_text(
        yaml.safe_dump(
            {
                "star": {"version": "20.02.007", "version_source": "runtime"},
                "runtime": {
                    "status": manifest_status,
                    "requested_processes": 2,
                    "total_steps": 4,
                    "runtime_log": str(raw_dir / "starccm_flow_control.log"),
                },
            },
            sort_keys=False,
        ),
        encoding="utf-8",
    )
    (raw_dir / "timeseries.csv").write_text("physical_time\n0.1\n0.2\n", encoding="utf-8")
    (raw_dir / "starccm_flow_control.log").write_text("iteration 2\n", encoding="utf-8")
    sidecar_path = raw_dir / "gpu_execution.json"
    if corrupt:
        sidecar_path.write_text("{not json", encoding="utf-8")
    elif sidecar is not None:
        sidecar_path.write_text(json.dumps(sidecar, ensure_ascii=False), encoding="utf-8")
    return tmp_path / "case", sidecar_path


def _unverified_sidecar():
    return {
        "schema_version": "ccm_gpu_execution_v1",
        "request": {
            "backend": "gpu",
            "selection": "auto:2:nomps",
            "mpi_processes": 2,
            "requested_gpu_count": 2,
            "strict_compatibility": True,
        },
        "state": "UNVERIFIED",
        "actual_backend": "unknown",
        "star_return_code": None,
        "failure_code": None,
        "devices": [],
    }


def test_ccm_status_without_sidecar_prints_no_gpu_lines(tmp_path, capsys):
    case_dir, _ = _status_case(tmp_path)

    assert main(["--out", str(case_dir), "--tail", "0"]) == 0

    assert "GPU" not in capsys.readouterr().out


def test_ccm_status_keeps_cpu_lines_and_shows_confirmed_gpu(tmp_path, capsys):
    sidecar = _unverified_sidecar()
    sidecar.update(
        {
            "state": "GPU_CONFIRMED",
            "actual_backend": "gpu",
            "star_return_code": 0,
            "devices": [{"index": 0, "uuid": "GPU-1111-aaaa"}, {"index": 1, "uuid": "GPU-2222-bbbb"}],
        }
    )
    case_dir, _ = _status_case(tmp_path, sidecar=sidecar)

    assert main(["--out", str(case_dir), "--tail", "0"]) == 0

    output = capsys.readouterr().out
    assert "MPI进程: actual=待日志确认 requested=2" in output
    assert "Step: 2/4 (50.00%)" in output
    assert "GPU state: GPU_CONFIRMED" in output
    assert "GPU requested: selection=auto:2:nomps mpi_processes=2 gpu_count=2" in output
    assert "GPU actual: backend=gpu devices=2" in output
    assert "GPU-1111-aaaa" in output


def test_ccm_status_unverified_request_does_not_claim_actual_devices(tmp_path, capsys):
    case_dir, _ = _status_case(tmp_path, sidecar=_unverified_sidecar())

    assert main(["--out", str(case_dir), "--tail", "0"]) == 0

    output = capsys.readouterr().out
    assert "GPU requested: selection=auto:2:nomps mpi_processes=2 gpu_count=2" in output
    assert "GPU actual: backend=unknown devices=未采集（离线请求）" in output
    assert "GPU actual: backend=unknown devices=2" not in output


def test_ccm_status_returns_nonzero_on_gpu_failure(tmp_path, capsys):
    sidecar = _unverified_sidecar()
    sidecar.update(
        {
            "state": "FAILED",
            "failure_code": "GPU_EXECUTION_UNCONFIRMED",
            "failure_detail": "日志未确认 GPU 求解执行\n第二行不应打印",
            "star_return_code": 0,
        }
    )
    case_dir, _ = _status_case(tmp_path, manifest_status="completed", sidecar=sidecar)

    assert main(["--out", str(case_dir), "--tail", "0"]) == 1

    output = capsys.readouterr().out
    assert "GPU state: FAILED" in output
    assert "GPU failure: GPU_EXECUTION_UNCONFIRMED" in output
    assert "日志未确认 GPU 求解执行" in output
    assert "第二行不应打印" not in output


def test_ccm_status_returns_nonzero_on_blocked_gpu(tmp_path, capsys):
    sidecar = _unverified_sidecar()
    sidecar.update({"state": "BLOCKED", "failure_code": "INPUT_HASH_MISMATCH"})
    case_dir, _ = _status_case(tmp_path, manifest_status="completed", sidecar=sidecar)

    assert main(["--out", str(case_dir), "--tail", "0"]) == 1
    assert "GPU state: BLOCKED" in capsys.readouterr().out


def test_ccm_status_reports_corrupt_sidecar(tmp_path, capsys):
    case_dir, _ = _status_case(tmp_path, corrupt=True)

    assert main(["--out", str(case_dir), "--tail", "0"]) == 1
    assert "GPU state: UNREADABLE" in capsys.readouterr().out


def test_ccm_status_reports_unknown_sidecar_state(tmp_path, capsys):
    sidecar = _unverified_sidecar()
    sidecar["state"] = "SOMETHING_INVENTED"
    case_dir, _ = _status_case(tmp_path, sidecar=sidecar)

    assert main(["--out", str(case_dir), "--tail", "0"]) == 1
    assert "GPU state: SOMETHING_INVENTED" in capsys.readouterr().out


def test_ccm_status_does_not_rewrite_gpu_sidecar(tmp_path, capsys):
    sidecar = _unverified_sidecar()
    sidecar.update({"state": "FAILED", "failure_code": "GPU_OUT_OF_MEMORY"})
    case_dir, sidecar_path = _status_case(tmp_path, sidecar=sidecar)
    before = sidecar_path.read_bytes()

    main(["--out", str(case_dir), "--tail", "0"])

    assert sidecar_path.read_bytes() == before


def test_ccm_status_flags_stale_running_sidecar_without_lock(tmp_path, capsys):
    """P2-8：写入者被杀死后 sidecar 停在 RUNNING，不能报成功。"""
    sidecar = _unverified_sidecar()
    sidecar.update({"state": "RUNNING", "devices": [{"index": 0, "uuid": "GPU-1111-aaaa"}]})
    case_dir, sidecar_path = _status_case(tmp_path, sidecar=sidecar)

    assert main(["--out", str(case_dir), "--tail", "0"]) == 1

    output = capsys.readouterr().out
    assert "GPU state: RUNNING" in output
    assert "GPU warning" in output
    assert "写入者可能已被杀死" in output


def test_ccm_status_running_with_lock_is_not_a_failure(tmp_path, capsys):
    sidecar = _unverified_sidecar()
    sidecar.update({"state": "RUNNING", "devices": [{"index": 0, "uuid": "GPU-1111-aaaa"}]})
    case_dir, sidecar_path = _status_case(tmp_path, sidecar=sidecar)
    (sidecar_path.parent / "gpu_execution.lock").write_text(
        json.dumps({"pid": 4242, "host": "gpu01"}), encoding="utf-8"
    )

    assert main(["--out", str(case_dir), "--tail", "0"]) == 0

    output = capsys.readouterr().out
    assert "GPU state: RUNNING" in output
    assert "GPU warning" not in output


def test_ccm_status_labels_preflight_devices_as_not_actual_usage(tmp_path, capsys):
    """P2-10：BLOCKED 时的设备数是预检采集值，不能读成本次用了这么多卡。"""
    sidecar = _unverified_sidecar()
    sidecar.update(
        {
            "state": "BLOCKED",
            "failure_code": "INPUT_HASH_MISMATCH",
            "devices": [{"index": 0, "uuid": "GPU-1111-aaaa"}, {"index": 1, "uuid": "GPU-2222-bbbb"}],
        }
    )
    case_dir, _ = _status_case(tmp_path, sidecar=sidecar)

    assert main(["--out", str(case_dir), "--tail", "0"]) == 1

    output = capsys.readouterr().out
    assert "GPU actual: backend=unknown devices=2（预检采集，非本次实际使用）" in output
