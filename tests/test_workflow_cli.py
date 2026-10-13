from __future__ import annotations

from scripts import workflow
from flow_control.cli import run_starccm


def test_option_before_command_prints_actionable_hint(capsys) -> None:
    assert workflow.main(["--config", "actions"]) == 2

    captured = capsys.readouterr()
    assert "unknown command '--config'" in captured.err
    assert "options must follow a command" in captured.err
    assert "workflow.py actions --config <actions.yaml>" in captured.err


def test_top_level_help_describes_all_ccm_capabilities(capsys) -> None:
    assert workflow.main(["--help"]) == 0

    captured = capsys.readouterr()
    assert "ccm" in captured.out
    assert "打包/校验已有结果" in captured.out


def test_ccm_help_contains_templates_for_every_execution_mode(capsys) -> None:
    try:
        run_starccm.main(["--help"])
    except SystemExit as exc:
        assert exc.code == 0

    help_text = capsys.readouterr().out
    for mode in ("run", "dry-run", "package-only", "validate-only"):
        assert f"--execution-mode {mode}" in help_text
    assert "--schedule <actuation_schedule.csv>" in help_text
    assert "--actuation-config <actions.yaml>" in help_text
    assert "package-only 要求 <out>/timeseries.csv 已存在" in help_text
