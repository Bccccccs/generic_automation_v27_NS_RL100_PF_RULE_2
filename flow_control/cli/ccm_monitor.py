"""每秒刷新 STAR 进度与本机进程；不改写运行证据。"""
from __future__ import annotations

import argparse
import csv
import io
import math
import os
from pathlib import Path
import socket
import subprocess
import sys
import time
from collections import deque
from datetime import datetime

import yaml

from flow_control.cli.ccm_status import _gpu_status_lines


class CsvProgress:
    """增量读完整 CSV 行，避免每秒从头扫描增长中的结果文件。"""

    def __init__(self, path: Path):
        self.path = path
        self.identity = None
        self.offset = 0
        self.pending = b''
        self.header = None
        self.steps = 0
        self.physical = None
        self.generation = 0

    def read(self):
        try:
            with self.path.open('rb') as stream:
                stat = os.fstat(stream.fileno())
                identity = (stat.st_dev, stat.st_ino)
                if identity != self.identity or stat.st_size < self.offset:
                    self.identity = identity
                    self.offset, self.pending, self.header = 0, b'', None
                    self.steps, self.physical = 0, None
                    self.generation += 1
                stream.seek(self.offset)
                while chunk := stream.read(1024 * 1024):
                    self.offset += len(chunk)
                    lines = (self.pending + chunk).split(b'\n')
                    self.pending = lines.pop()
                    for line in lines:
                        if not line.strip():
                            continue
                        row = next(csv.reader([line.decode('utf-8-sig', errors='replace')]))
                        if self.header is None:
                            self.header = row
                            continue
                        if len(row) != len(self.header) or 'physical_time' not in self.header:
                            continue
                        try:
                            physical = float(row[self.header.index('physical_time')])
                        except (ValueError, IndexError):
                            continue
                        if math.isfinite(physical):
                            self.steps += 1
                            self.physical = physical
        except FileNotFoundError:
            pass
        return self.steps, self.physical


class RateMeter:
    def __init__(self):
        self.samples = deque()

    def update(self, now, steps, physical):
        if self.samples and (steps < self.samples[-1][1] or
                             (physical is not None and self.samples[-1][2] is not None
                              and physical < self.samples[-1][2])):
            self.samples.clear()
        self.samples.append((now, steps, physical))
        while len(self.samples) > 2 and now - self.samples[0][0] > 30:
            self.samples.popleft()
        start, count, sim = self.samples[0]
        elapsed = now - start
        if elapsed <= 0:
            return None, None
        return ((steps - count) / elapsed,
                (physical - sim) / elapsed if physical is not None and sim is not None else None)


def _tail(path, count=5):
    try:
        with path.open('rb') as stream:
            stream.seek(0, 2)
            stream.seek(max(0, stream.tell() - 16384))
            return stream.read().decode('utf-8', errors='replace').splitlines()[-count:]
    except OSError:
        return []


def _processes(raw, root_pid):
    """只显示指定 PID 树，或命令行/工作目录匹配算例的 STAR 进程树。"""
    try:
        result = subprocess.run(
            ['ps', '-eo', 'pid=,ppid=,pcpu=,rss=,stat=,etime=,args='],
            capture_output=True, text=True, timeout=0.5, check=True)
    except (OSError, subprocess.SubprocessError):
        return [], '本机 ps 不可用'
    records = []
    selected = {root_pid} if root_pid else set()
    for line in result.stdout.splitlines():
        fields = line.strip().split(None, 6)
        if len(fields) != 7:
            continue
        try:
            pid, parent = int(fields[0]), int(fields[1])
        except ValueError:
            continue
        records.append((pid, parent, fields))
        if root_pid or pid == os.getpid():
            continue
        command = fields[6].lower()
        if not any(word in command for word in ('starccm', 'star-ccm', 'workflow.py ccm ', 'mpirun')):
            continue
        matches = str(raw) in fields[6] or str(raw.parent) in fields[6]
        try:
            matches = matches or Path(f'/proc/{pid}/cwd').resolve() == raw
        except OSError:
            pass
        if matches:
            selected.add(pid)
    changed = True
    while changed:
        changed = False
        for pid, parent, _ in records:
            if parent in selected and pid not in selected:
                selected.add(pid)
                changed = True
    return [fields for pid, _, fields in records if pid in selected and pid != os.getpid()], ''


def _duration(seconds):
    if seconds is None or not math.isfinite(seconds):
        return '未知'
    seconds = max(0, int(seconds))
    return f'{seconds // 3600:02d}:{seconds // 60 % 60:02d}:{seconds % 60:02d}'


def main(argv=None):
    parser = argparse.ArgumentParser(description='只读监控 STAR 速度和本机进程，默认每 1 秒刷新。')
    parser.add_argument('--out', help='算例目录或 raw_star；省略时选择 runs 下日志最近更新的算例')
    parser.add_argument('--pid', type=int, help='本机 STAR/启动器 PID，显示该进程及子进程')
    parser.add_argument('--interval', type=float, default=1.0, help='刷新间隔（秒），默认 1')
    parser.add_argument('--once', action='store_true', help='仅输出一次快照')
    args = parser.parse_args(argv)
    if not math.isfinite(args.interval) or args.interval <= 0:
        parser.error('--interval 必须为正数')
    if args.pid is not None and args.pid <= 0:
        parser.error('--pid 必须为正整数')
    if args.out:
        path = Path(args.out).expanduser().resolve()
        raw = path / 'raw_star' if (path / 'raw_star').is_dir() else path
        if not raw.is_dir():
            parser.error(f'目录不存在：{raw}')
    else:
        candidates = list(Path('runs').glob('**/starccm_flow_control.log'))
        if not candidates:
            parser.error('runs 下未找到 STAR 日志，请用 --out 指定算例')
        raw = max(candidates, key=lambda p: p.stat().st_mtime).resolve().parent
    progress, rates = CsvProgress(raw / 'timeseries.csv'), RateMeter()
    generation = 0
    last_change = time.monotonic()
    previous_steps = None
    try:
        while True:
            tick = time.monotonic()
            warnings = []
            try:
                manifest = yaml.safe_load((raw / 'case_manifest.yaml').read_text()) or {}
                runtime = manifest.get('runtime') or {}
                if not isinstance(runtime, dict):
                    raise ValueError('runtime 不是映射')
            except (OSError, ValueError, AttributeError, yaml.YAMLError) as exc:
                runtime = {}
                warnings.append(f'运行清单暂不可读：{exc}')
            steps, physical = progress.read()
            if generation != progress.generation:
                rates = RateMeter()
                generation = progress.generation
            speed, sim_speed = rates.update(tick, steps, physical)
            if steps != previous_steps:
                last_change, previous_steps = tick, steps
            try:
                total = int(runtime.get('total_steps') or 0)
            except (TypeError, ValueError):
                total = 0
            eta = max(0, total - steps) / speed if total > 0 and speed and speed > 0 else None
            output = io.StringIO()
            print(f'STAR 运行监控  {datetime.now():%Y-%m-%d %H:%M:%S}  每 {args.interval:g}s 刷新  Ctrl+C 退出', file=output)
            print(f'算例：{raw}' + ('（自动选择，请核对）' if not args.out else ''), file=output)
            print(f'本机：{socket.gethostname()}  JobID：{runtime.get("slurm_job_id") or "未知"}  节点：{runtime.get("nodes") or "未知"}', file=output)
            print(f'清单记录状态：{runtime.get("status", "未知")}（不代表实时调度状态）', file=output)
            print(f'MPI进程记录：actual={runtime.get("actual_processes") or "未确认"}  requested={runtime.get("requested_processes") or "未知"}', file=output)
            print(f'已记录求解步：{steps}/{total if total > 0 else "未知"}' + (f'  {100 * steps / total:.2f}%' if total > 0 else ''), file=output)
            print(f'物理时间：{physical if physical is not None else "未知"} s  距上次新增采样：{tick - last_change:.1f}s', file=output)
            if speed is None:
                print('速度：首轮采样，等待下一次刷新', file=output)
            else:
                print(f'近30秒步速：{speed:.3f} step/s  单步耗时：{1 / speed if speed > 0 else "未知"} s/step', file=output)
                print(f'物理推进速度：{sim_speed if sim_speed is not None else "未知"} 仿真秒/墙钟秒  预计剩余：{_duration(eta)}', file=output)
            processes, error = _processes(raw, args.pid)
            print('\n本机进程（CPU% 为 ps 的进程生存期平均值；100% 约一核）：', file=output)
            print('PID     PPID    CPU%    RSS(MiB) 状态  已运行     命令', file=output)
            for fields in processes[:30]:
                pid, parent, cpu, rss, state, elapsed, command = fields
                print(f'{pid:<7} {parent:<7} {cpu:<7} {int(rss)/1024:<8.1f} {state:<5} {elapsed:<10} {command[:160]}', file=output)
            if not processes:
                print(error or '未匹配到本机进程；远端进程不可见，可在计算节点运行或用 --pid 指定。', file=output)
            if len(processes) > 30:
                print(f'共 {len(processes)} 个匹配进程，仅显示前 30 个。', file=output)
            gpu_lines, _ = _gpu_status_lines(raw)
            for line in gpu_lines + warnings:
                print(line, file=output)
            print('\n最近日志（CSV/日志缓冲可能使速度暂为 0，不能据此判定作业失败）：', file=output)
            for line in _tail(raw / 'starccm_flow_control.log'):
                print(line[:240], file=output)
            if sys.stdout.isatty() and not args.once:
                print('\033[2J\033[H', end='')
            print(output.getvalue(), flush=True)
            if args.once:
                return 0
            time.sleep(max(0, args.interval - (time.monotonic() - tick)))
    except KeyboardInterrupt:
        print('\n监控已退出，求解进程继续运行。')
        return 0


if __name__ == '__main__':
    raise SystemExit(main())
