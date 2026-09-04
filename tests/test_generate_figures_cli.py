from __future__ import annotations

import json
from pathlib import Path

import numpy as np

import flow_control.cli.generate_figures as generate_figures
from flow_control.data_schema import initial_transient_crop_end_s
from flow_control.mock.mock_plant import write_single_series_svg


def test_figures_uses_interactive_case_and_updates_report(
    tmp_path: Path, monkeypatch
) -> None:
    case_dir = tmp_path / "runs" / "case_a"
    case_dir.mkdir(parents=True)
    report_path = case_dir / "quality_report.json"
    report_path.write_text('{"run_success_flag": true}', encoding="utf-8")
    (case_dir / "figures").mkdir()
    rows = [
        {
            "physical_time": 0.1,
            "Fz_S1L": 1.0,
            "Fz_S1R": 2.0,
            "Fz_S2L": 3.0,
            "Fz_S2R": 4.0,
            "Fz_S3L": 5.0,
            "Fz_S3R": 6.0,
            "Fz_Total": 21.0,
        }
    ]

    monkeypatch.setattr(generate_figures, "_choose_directory", lambda root, label: case_dir)
    monkeypatch.setattr(generate_figures, "load_case", lambda case_dir, **kwargs: {"timeseries": rows})
    monkeypatch.setattr(
        generate_figures,
        "write_plots",
        lambda run_dir, result: [
            (run_dir / "figures" / f"{name}.svg").write_text("<svg/>", encoding="utf-8")
            for name in (
                "input_heatmap",
                "fz_regions",
                "fz_total",
                "spatial_nonuniformity",
                "total_massflow",
            )
        ],
    )

    assert generate_figures.main(["--start-time", "0"]) == 0
    report = json.loads(report_path.read_text(encoding="utf-8"))
    assert set(report["summary_figures"]) == {
        "input_heatmap",
        "fz_regions",
        "fz_total",
        "spatial_nonuniformity",
        "total_massflow",
    }
    assert report["summary_figure_options"] == {
        "start_time_s": 0.0,
        "end_time_s": 0.1,
        "sample_count": 1,
    }


def test_summary_plot_data_drops_initial_transient() -> None:
    rows = [
        {"physical_time": 0.2, "Fz_S1L": 1000.0},
        {"physical_time": 0.3, "Fz_S1L": 3.0},
        {"physical_time": 0.4, "Fz_S1L": 4.0},
    ]

    result = generate_figures._summary_plot_data(
        {"timeseries": rows},
        start_time=0.3,
    )

    assert result["physical_time"].tolist() == [0.3, 0.4]
    assert result["outputs"][:, 0].tolist() == [3.0, 4.0]


def test_summary_plot_data_defaults_to_manifest_transient_crop() -> None:
    rows = [
        {"physical_time": 0.2, "Fz_S1L": 1.0},
        {"physical_time": 0.5, "Fz_S1L": 2.0},
        {"physical_time": 0.7, "Fz_S1L": 3.0},
    ]
    manifest = {
        "initial_transient_crop": {
            "end_time_s": 0.5,
            "keep_rule": "physical_time >= 0.5 s",
        }
    }

    result = generate_figures._summary_plot_data(
        {"timeseries": rows, "manifest": manifest}
    )

    assert result["physical_time"].tolist() == [0.5, 0.7]


def test_initial_transient_crop_end_s_falls_back_to_uniform_default() -> None:
    assert initial_transient_crop_end_s(None) == 0.5
    assert initial_transient_crop_end_s({}) == 0.5
    assert initial_transient_crop_end_s(
        {"initial_transient_crop": {"end_time_s": "bad"}}
    ) == 0.5
    assert initial_transient_crop_end_s(
        {"initial_transient_crop": {"end_time_s": 0.5}}
    ) == 0.5


def test_line_plot_uses_absolute_half_second_major_grid(tmp_path: Path) -> None:
    output = tmp_path / "line.svg"
    write_single_series_svg(
        output,
        np.asarray([0.3, 0.5, 1.0, 1.2]),
        np.asarray([1.0, 2.0, 3.0, 2.0]),
        "Time axis check",
    )

    svg = output.read_text(encoding="utf-8")
    assert "Displayed time: 0.3 s to 1.2 s" in svg
    assert ">0.5 s</text>" in svg
    assert ">1.0 s</text>" in svg
    assert ">0.3 s</text>" not in svg


def test_figures_requires_quality_report(tmp_path: Path) -> None:
    case_dir = tmp_path / "case_without_report"
    case_dir.mkdir()

    try:
        generate_figures.main(["--case-dir", str(case_dir)])
    except FileNotFoundError as exc:
        assert "请先运行" in str(exc)
    else:
        raise AssertionError("missing quality report should fail")
