from __future__ import annotations

import sys
from pathlib import Path


EXAMPLES_DIR = Path(__file__).resolve().parents[1] / "examples"
if str(EXAMPLES_DIR) not in sys.path:
    sys.path.insert(0, str(EXAMPLES_DIR))

import run_ccm_ingest_step1_timeseries as step1


def _write_star_force_csv(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        '"时间","Drag Monitor: Drag Monitor (N)"\n'
        '0.0001,1.0\n'
        '0.0002,2.0\n',
        encoding="utf-8",
    )


def test_flat_star_directory_is_discovered_but_input_schedule_is_not(tmp_path: Path) -> None:
    product_dir = tmp_path / "runs" / "week4" / "j02"
    _write_star_force_csv(product_dir / "drag.csv")

    input_dir = tmp_path / "runs" / "week4" / "J02_case" / "input"
    input_dir.mkdir(parents=True)
    (input_dir / "actuation_schedule.csv").write_text(
        "physical_time,JET_02\n0.0,1\n",
        encoding="utf-8",
    )

    assert step1.resolve_product_dir(product_dir) == product_dir
    assert step1.discover_product_dirs(tmp_path / "runs") == [product_dir]


def test_flat_jet_export_pairs_with_sibling_action_case(tmp_path: Path) -> None:
    week4_dir = tmp_path / "runs" / "week4"
    product_dir = week4_dir / "j06"
    _write_star_force_csv(product_dir / "drag.csv")
    schedule_path = week4_dir / "G02_J06_pulse" / "input" / "actuation_schedule.csv"
    schedule_path.parent.mkdir(parents=True)
    schedule_path.write_text("physical_time,window_id\n0.0,0\n", encoding="utf-8")

    assert step1.find_companion_case_dir(product_dir) == schedule_path.parents[1]
    assert step1.find_schedule_for_product(product_dir) == schedule_path
    assert step1.resolve_real_star_target_case_dir(product_dir, True) == schedule_path.parents[1]


def test_unpaired_flat_no_jet_case_stays_in_same_week_directory(
    tmp_path: Path, monkeypatch
) -> None:
    product_dir = tmp_path / "runs" / "week4" / "j0"
    _write_star_force_csv(product_dir / "drag.csv")
    monkeypatch.setattr("builtins.input", lambda _: "")

    target = step1.resolve_real_star_target_case_dir(product_dir, False)

    assert target == product_dir.parent / "j0_nojet_existing"


def test_no_jet_schedule_is_derived_from_star_sample_ownership() -> None:
    rows = [{"physical_time": 0.0001}, {"physical_time": 0.0002}]

    schedule = step1.build_no_jet_schedule(rows)

    assert schedule[0]["t_start"] == 0.0
    assert schedule[0]["t_end"] == 0.0001
    assert schedule[1]["t_start"] == 0.0001
    assert schedule[1]["t_end"] == 0.0002
    assert schedule[0]["JET_01"] == 0
    assert schedule[0]["cmd_massflow_24"] == 0.0
