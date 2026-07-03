from flow_control.data_schema import CaseSchema
from flow_control.workflow import run_actuation_to_mock


def test_schedule_to_mock_timeseries_pipeline_smoke(tmp_path):
    schedule_config = tmp_path / "schedule.yaml"
    mock_config = tmp_path / "mock_dynamic24x6.yaml"
    schedule_dir = tmp_path / "schedule_case"
    run_dir = tmp_path / "mock_case"

    schedule_config.write_text(
        "actuation:\n"
        "  mode: pulse_singlejet\n"
        "  n_jets: 24\n"
        "  jet_ids: [3]\n"
        "  pulse_windows: [1, 2]\n"
        "  total_windows: 5\n"
        "  mass_flow_rate: 0.5\n"
        "  window_duration: 0.1\n"
        "  random_seed: 7\n"
        "output:\n"
        f"  run_dir: {schedule_dir}\n",
        encoding="utf-8",
    )
    mock_config.write_text(
        "mock_dynamic24x6:\n"
        "  random_seed: 11\n"
        "  fz_noise_std: 0.0\n"
        "  drag_noise_std: 0.0\n"
        "  moment_noise_std: 0.0\n",
        encoding="utf-8",
    )

    result = run_actuation_to_mock(
        actuation_config_path=schedule_config,
        mock_config_path=mock_config,
        schedule_output_dir=schedule_dir,
        mock_output_dir=run_dir,
    )

    assert (schedule_dir / "actuation_schedule.csv").exists()
    assert result["files"]["timeseries"].exists()
    old_root = CaseSchema.runs_root
    CaseSchema.runs_root = tmp_path
    try:
        loaded = CaseSchema.load_case(run_dir.name)
    finally:
        CaseSchema.runs_root = old_root
    assert loaded["quality_report"]["run_success_flag"] is True
    assert len(loaded["timeseries"]) == 5
    assert (run_dir / "figures" / "input_heatmap.svg").exists()
