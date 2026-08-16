from pathlib import Path

import yaml

from flow_control.star_ingest.manifest_builder import finalize_manifest, prepare_preflight_manifest


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
