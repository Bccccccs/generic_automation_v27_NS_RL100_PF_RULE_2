import argparse
import csv
import hashlib

from flow_control.cli.run_starccm import (
    _build_runtime_manifest,
    _standard_case_dir_for_output,
)
from flow_control.sampling import resolve_schedule_time_step


def test_standard_case_dir_uses_parent_when_output_is_raw_star(tmp_path):
    case_dir = tmp_path / "runs" / "real_star" / "G02_nojet_3s"

    assert _standard_case_dir_for_output(case_dir / "raw_star") == case_dir
    assert _standard_case_dir_for_output(case_dir) == case_dir


def test_runtime_manifest_fills_starccm_version_and_sim_hash(tmp_path):
    sim_path = tmp_path / "cifu0.sim"
    sim_path.write_bytes(b"template sim")
    schedule_path = tmp_path / "runs" / "real_star" / "G03_J01" / "raw_star" / "input" / "actuation_schedule.csv"
    schedule_path.parent.mkdir(parents=True)
    with schedule_path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(
            handle,
            fieldnames=["window_id", "JET_01", "cmd_massflow_01"],
        )
        writer.writeheader()
        writer.writerow({"window_id": 0, "JET_01": 1, "cmd_massflow_01": 2.57})

    raw_dir = schedule_path.parents[1]
    raw_timeseries = raw_dir / "flow_control_timeseries.csv"
    raw_timeseries.write_text("physical_time,window_id\n0.0,0\n", encoding="utf-8")
    result = argparse.Namespace(
        timeseries_path=raw_timeseries,
        macro_path=raw_dir / "FlowControlRunMacro.java",
        runtime_plan_path=raw_dir / "starccm_runtime_plan.json",
        log_path=raw_dir / "starccm_flow_control.log",
        result_sim_path=raw_dir / "flow_control_result.sim",
        command=("starccm+", "-batch", "macro.java", str(sim_path)),
    )
    args = argparse.Namespace(
        sim=str(sim_path),
        starccm_path="/work/app/STAR-CCM+17.06.007-R8/star/bin/starccm+",
        np=8,
        podkey="",
        region="Region",
        time_step=None,
        non_strict_boundaries=False,
        no_save_result_sim=False,
    )

    manifest = _build_runtime_manifest(
        args=args,
        result=result,
        schedule_path=schedule_path,
        raw_output_dir=raw_dir,
    )

    assert manifest["case_id"] == "G03_J01"
    assert manifest["case_type"] == "jet_on"
    assert manifest["source_product_dir"] == "raw_star"
    assert manifest["source_schedule"] == "actuation_schedule.csv"
    assert manifest["star"]["version"] == "17.06.007-R8"
    assert manifest["star"]["sim_file_name"] == "cifu0.sim"
    assert manifest["star"]["sim_file_hash_sha256"] == hashlib.sha256(b"template sim").hexdigest()
    assert manifest["runtime"]["num_cores"] == 8


def test_solver_time_step_is_read_separately_from_actuation_window(tmp_path):
    input_dir = tmp_path / "case" / "input"
    input_dir.mkdir(parents=True)
    schedule_path = input_dir / "actuation_schedule.csv"
    schedule_path.write_text(
        "physical_time,window_id,t_start,t_end\n0.0,0,0.0,0.1\n",
        encoding="utf-8",
    )
    (input_dir / "config_summary.yaml").write_text(
        "actuation_window_duration_seconds: 0.1\n"
        "solver_time_step_seconds: 0.0001\n",
        encoding="utf-8",
    )

    time_step, source = resolve_schedule_time_step(schedule_path)

    assert time_step == 1.0e-4
    assert source == "config_summary"
