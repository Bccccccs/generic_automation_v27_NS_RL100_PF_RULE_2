from __future__ import annotations

from pathlib import Path

import flow_control.cli.check_case as check_case


def test_check_case_uses_interactive_directory_when_argument_is_omitted(
    tmp_path: Path, monkeypatch
) -> None:
    selected = tmp_path / "runs" / "case_a"
    selected.mkdir(parents=True)
    captured: dict[str, Path] = {}

    monkeypatch.setattr(check_case, "_choose_directory", lambda root, label: selected)
    monkeypatch.setattr(
        check_case,
        "write_quality_report",
        lambda case_dir, **kwargs: {"run_success_flag": True, "errors": [], "warnings": []},
    )
    monkeypatch.setattr(
        check_case,
        "load_case",
        lambda case_dir, **kwargs: captured.setdefault("case_dir", case_dir) or {},
    )
    monkeypatch.setattr(check_case, "generate_all_figures", lambda checked, output_dir: {})
    assert check_case.main([]) == 0
    assert captured["case_dir"] == selected


def test_check_case_keeps_explicit_case_dir(monkeypatch, tmp_path: Path) -> None:
    selected = tmp_path / "explicit_case"
    selected.mkdir()
    captured: dict[str, Path] = {}

    monkeypatch.setattr(
        check_case,
        "write_quality_report",
        lambda case_dir, **kwargs: {"run_success_flag": True, "errors": [], "warnings": []},
    )
    monkeypatch.setattr(
        check_case,
        "load_case",
        lambda case_dir, **kwargs: captured.setdefault("case_dir", case_dir) or {},
    )
    monkeypatch.setattr(check_case, "generate_all_figures", lambda checked, output_dir: {})
    assert check_case.main(["--case-dir", str(selected)]) == 0
    assert captured["case_dir"] == selected
