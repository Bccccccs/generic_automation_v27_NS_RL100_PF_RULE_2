from __future__ import annotations

import json
from pathlib import Path

import flow_control.cli.generate_figures as generate_figures


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

    assert generate_figures.main([]) == 0
    report = json.loads(report_path.read_text(encoding="utf-8"))
    assert set(report["summary_figures"]) == {
        "input_heatmap",
        "fz_regions",
        "fz_total",
        "spatial_nonuniformity",
        "total_massflow",
    }


def test_figures_requires_quality_report(tmp_path: Path) -> None:
    case_dir = tmp_path / "case_without_report"
    case_dir.mkdir()

    try:
        generate_figures.main(["--case-dir", str(case_dir)])
    except FileNotFoundError as exc:
        assert "请先运行" in str(exc)
    else:
        raise AssertionError("missing quality report should fail")
