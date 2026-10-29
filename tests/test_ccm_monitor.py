import pytest
import subprocess
import sys

from flow_control.cli.ccm_monitor import CsvProgress, RateMeter, _processes, main


def test_progress_only_counts_complete_rows_and_handles_truncation(tmp_path):
    path = tmp_path / 'timeseries.csv'
    path.write_text('physical_time,solver_dt_s\n0.001,0.001\n0.002,')
    reader = CsvProgress(path)
    assert reader.read() == (1, 0.001)
    with path.open('a') as stream:
        stream.write('0.001\n')
    assert reader.read() == (2, 0.002)
    path.write_text('physical_time\n0.01\n')
    assert reader.read() == (1, 0.01)


def test_rate_uses_elapsed_wall_time_and_resets_after_restart():
    rate = RateMeter()
    assert rate.update(10, 100, 0.1) == (None, None)
    assert rate.update(12, 104, 0.104) == pytest.approx((2, 0.002))
    assert rate.update(13, 1, 0.001) == (None, None)


def test_monitor_once_is_read_only_and_does_not_infer_total_from_schedule(tmp_path, capsys):
    raw = tmp_path / 'raw_star'
    raw.mkdir()
    (raw / 'starccm_flow_control.log').write_text('loading\n')
    (raw / 'timeseries.csv').write_text('physical_time\n0.001\n0.002\n')
    (raw / 'actuation_schedule.csv').write_text('physical_time\n0\n1\n')
    before = {p.name: p.read_bytes() for p in raw.iterdir()}
    assert main(['--out', str(tmp_path), '--once']) == 0
    output = capsys.readouterr().out
    assert '2/未知' in output
    assert '首轮采样' in output
    assert before == {p.name: p.read_bytes() for p in raw.iterdir()}


def test_process_monitor_matches_real_process_by_case_path(tmp_path):
    raw = tmp_path / 'raw_star'
    raw.mkdir()
    script = raw / 'starccm_monitor_test.py'
    script.write_text('import time\ntime.sleep(20)\n')
    process = subprocess.Popen([sys.executable, str(script)])
    try:
        records, error = _processes(raw, None)
        assert not error
        assert str(process.pid) in {row[0] for row in records}
        records, _ = _processes(raw, process.pid)
        assert str(process.pid) in {row[0] for row in records}
    finally:
        process.terminate()
        process.wait(timeout=5)
