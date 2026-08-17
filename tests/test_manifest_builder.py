from pathlib import Path

import yaml

from flow_control.star_ingest.manifest_builder import (
    finalize_manifest,
    prepare_preflight_manifest,
    read_star_runtime_metadata,
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
