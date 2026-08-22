from pathlib import Path

import yaml

from flow_control.star_ingest.manifest_builder import (
    finalize_manifest,
    finish_runtime_manifest,
    prepare_preflight_manifest,
    read_star_runtime_metadata,
    start_runtime_manifest,
)


def test_preflight_and_star_snapshot_are_merged(tmp_path: Path):
    template = tmp_path / "template.yaml"
    template.write_text("schema_version: test\nsolver_time: {}\n", encoding="utf-8")
    sim = tmp_path / "template.sim"
    sim.write_bytes(b"sim")
    schedule = tmp_path / "schedule.csv"
    schedule.write_text("window_id,t_start,t_end\n0,0.0,0.1\n", encoding="utf-8")
    preflight = prepare_preflight_manifest(
        template_path=template, sim_path=sim, schedule_path=schedule, output_dir=tmp_path, time_step=0.0001
    )
    snapshot = tmp_path / "sim_template_snapshot.yaml"
    snapshot.write_text(
        "snapshot_status: ok\nsurface_properties:\n  surfaces:\n    J01:\n      area_m2: 0.1\n",
        encoding="utf-8",
    )
    output = finalize_manifest(preflight_path=preflight, snapshot_path=snapshot, output_path=tmp_path / "case_manifest.yaml")
    manifest = yaml.safe_load(output.read_text(encoding="utf-8"))
    assert manifest["manifest_status"] == "finalized_from_star_template_snapshot"
    assert manifest["surface_properties"]["surfaces"]["J01"]["area_m2"] == 0.1


def test_runtime_log_metadata_is_read_into_manifest(tmp_path: Path):
    template = tmp_path / "template.yaml"
    template.write_text(
        "schema_version: test\n"
        "star:\n"
        "  sim_file_hash_sha256: abcdef1234567890\n"
        "  region_names: [减运算]\n",
        encoding="utf-8",
    )
    sim = tmp_path / "template.sim"
    sim.write_bytes(b"sim")
    schedule = tmp_path / "schedule.csv"
    schedule.write_text("window_id,t_start,t_end\n0,0.0,0.1\n", encoding="utf-8")
    preflight = prepare_preflight_manifest(
        template_path=template,
        sim_path=sim,
        schedule_path=schedule,
        output_dir=tmp_path,
        time_step=0.1,
    )
    snapshot = tmp_path / "sim_template_snapshot.yaml"
    snapshot.write_text("snapshot_status: ok\n", encoding="utf-8")
    log = tmp_path / "starccm.log"
    log.write_text(
        "Simcenter STAR-CCM+ 2506.0001 Build 20.04.008 (win64/clang17.0vc14.2-r8)\n"
        "Saved by:\n"
        "  Simcenter STAR-CCM+ 2210 Build 17.06.007 (win64/clang11.1vc14.2-r8) Serial\n"
        "  减运算 (index 0): 19254821 cells, 57592581 faces, 20468928 verts.\n",
        encoding="utf-8",
    )

    parsed = read_star_runtime_metadata(log)
    assert parsed["runtime"]["release_version"] == "20.04.008-R8"
    assert parsed["input_sim_saved_by"]["release_version"] == "17.06.007-R8"
    assert parsed["mesh"]["total_cells"] == 19254821

    output = finalize_manifest(
        preflight_path=preflight,
        snapshot_path=snapshot,
        output_path=tmp_path / "case_manifest.yaml",
        runtime_log_path=log,
    )
    manifest = yaml.safe_load(output.read_text(encoding="utf-8"))
    assert manifest["star"]["version"] == "20.04.008-R8"
    assert manifest["star"]["product_version"] == "2506.0001"
    assert manifest["star"]["input_sim_saved_by"]["release_version"] == "17.06.007-R8"
    assert manifest["star"]["mesh"]["regions"][0]["name"] == "减运算"
    assert manifest["mesh_version"].endswith("-c19254821-f57592581-v20468928")


def test_runtime_manifest_is_written_before_launch_and_finalized_after_success(tmp_path: Path):
    template = tmp_path / "template.yaml"
    template.write_text("schema_version: test\n", encoding="utf-8")
    sim = tmp_path / "template.sim"
    sim.write_bytes(b"sim")
    schedule = tmp_path / "schedule.csv"
    schedule.write_text("window_id,t_start,t_end\n0,0.0,0.1\n", encoding="utf-8")
    preflight = prepare_preflight_manifest(
        template_path=template,
        sim_path=sim,
        schedule_path=schedule,
        output_dir=tmp_path,
        time_step=0.0001,
    )
    manifest_path = start_runtime_manifest(
        preflight_path=preflight,
        output_path=tmp_path / "case_manifest.yaml",
        star={"version": "17.06.007-R8", "version_source": "path"},
        runtime={
            "scheduler": "slurm",
            "slurm_job_id": "8096781",
            "nodes": ["n01", "n02"],
            "requested_processes": 128,
            "total_steps": 10000,
        },
    )
    running = yaml.safe_load(manifest_path.read_text(encoding="utf-8"))
    assert running["runtime"]["status"] == "running"
    assert running["runtime"]["requested_processes"] == 128

    snapshot = tmp_path / "sim_template_snapshot.yaml"
    snapshot.write_text("snapshot_status: ok\n", encoding="utf-8")
    log = tmp_path / "starccm.log"
    log.write_text(
        "Simcenter STAR-CCM+ 2210 Build 17.06.007 (linux-x86_64-r8)\n"
        "1 copy of ccmpsuite checked out\n"
        "Server::start -host n01:47827\n"
        "MPI Distribution : Open MPI-4.1.2\n"
        "Host 0 -- n01 -- Ranks 0-63\n"
        "Host 1 -- n02 -- Ranks 64-127\n"
        "Total number of processes: 128\n",
        encoding="utf-8",
    )
    finalize_manifest(
        preflight_path=preflight,
        snapshot_path=snapshot,
        output_path=manifest_path,
        runtime_log_path=log,
    )
    finish_runtime_manifest(
        manifest_path=manifest_path,
        status="completed",
        return_code=0,
        runtime_log_path=log,
        completed_steps=10000,
        outputs={"timeseries": "timeseries.csv"},
    )

    completed = yaml.safe_load(manifest_path.read_text(encoding="utf-8"))
    assert completed["manifest_status"] == "finalized_from_star_template_snapshot"
    assert completed["star"]["version"] == "17.06.007-R8"
    assert completed["star"]["license"]["feature"] == "ccmpsuite"
    assert completed["runtime"]["status"] == "completed"
    assert completed["runtime"]["actual_processes"] == 128
    assert completed["runtime"]["mpi_distribution"] == "Open MPI-4.1.2"
    assert completed["runtime"]["actual_process_distribution"][1]["rank_count"] == 64
    assert completed["runtime"]["server"]["host"] == "n01"
    assert completed["runtime"]["progress_percent"] == 100.0


def test_failed_runtime_manifest_keeps_failure_classification(tmp_path: Path):
    path = tmp_path / "case_manifest.yaml"
    path.write_text("runtime:\n  status: running\n  total_steps: 100\n", encoding="utf-8")

    finish_runtime_manifest(
        manifest_path=path,
        status="failed",
        return_code=1,
        completed_steps=12,
        failure_summary="selected pml cm, but peer selected pml ucx",
    )

    manifest = yaml.safe_load(path.read_text(encoding="utf-8"))
    assert manifest["manifest_status"] == "runtime_failed"
    assert manifest["runtime"]["failure_type"] == "mpi_pml_mismatch"
    assert manifest["runtime"]["progress_percent"] == 12.0
