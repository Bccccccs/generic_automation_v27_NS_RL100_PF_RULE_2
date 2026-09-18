import importlib.util
from pathlib import Path


SCRIPT = Path(__file__).parents[1] / "scripts" / "analysis" / "run_b45_single_jet_response.py"
SPEC = importlib.util.spec_from_file_location("run_b45_single_jet_response", SCRIPT)
MODULE = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
SPEC.loader.exec_module(MODULE)


def test_recovery_requires_window_inside_band():
    values = [10.0] * 5 + [0.2] * 100
    index, recovered = MODULE._recovery(values, 5, 0.0, 1.0, window=10)
    assert index == 14
    assert recovered is True


def test_missing_momentum_is_not_filled_from_surface_force():
    row = MODULE._missing_momentum_row(
        {
            "case_id": "G01",
            "jet_id": "J02",
            "baseline_case_id": "G00",
            "on_time_s": 0.4,
            "off_time_s": 0.5,
            "actual_massflow_mean_kg_s": 1.0,
            "quality_status": "FAIL",
        }
    )
    assert row["availability"] == "missing"
    assert row["peak_delta"] == ""
    assert row["signal_name"] == "jet_momentum_reaction_z"
