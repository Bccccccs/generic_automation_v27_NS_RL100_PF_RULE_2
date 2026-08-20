"""Resolve a running Slurm allocation into a STAR-CCM+ machinefile."""

from __future__ import annotations

import os
import re
import socket
import subprocess
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class SlurmAllocation:
    job_id: str
    nodes: tuple[str, ...]
    num_tasks: int
    machinefile_path: Path


def resolve_slurm_allocation(
    output_dir: Path,
    *,
    job_id: str | None = None,
    num_tasks: int | None = None,
) -> SlurmAllocation:
    """Find a running allocation and write a Gridview-style machinefile.

    An explicit job id wins, followed by ``SLURM_JOB_ID``.  Gridview shells do
    not always expose Slurm job variables, so the final fallback finds the one
    running job whose node list contains the current host.
    """

    resolved_job_id = str(job_id or os.environ.get("SLURM_JOB_ID", "")).strip()
    if not resolved_job_id:
        resolved_job_id = _discover_job_for_current_host()
    if not re.fullmatch(r"\d+(?:_[0-9]+)?", resolved_job_id):
        raise ValueError(f"invalid Slurm job id: {resolved_job_id!r}")

    job = _parse_key_values(_run(["scontrol", "show", "job", "-o", resolved_job_id]))
    state = job.get("JobState", "")
    if state != "RUNNING":
        raise RuntimeError(f"Slurm job {resolved_job_id} is not RUNNING (state={state or 'unknown'})")
    node_expr = job.get("NodeList")
    if not node_expr or node_expr == "(null)":
        raise RuntimeError(f"Slurm job {resolved_job_id} has no allocated NodeList")
    nodes = _expand_nodelist(node_expr)

    allocation_counts = tuple(
        count
        for count in (_positive_int(job.get("NumTasks")), _positive_int(job.get("NumCPUs")))
        if count is not None
    )
    allocated_tasks = max(allocation_counts, default=None)
    requested_tasks = allocated_tasks if num_tasks is None else num_tasks
    if requested_tasks is None:
        raise RuntimeError(
            f"cannot infer process count for Slurm job {resolved_job_id}; pass --np explicitly"
        )
    if requested_tasks < 1:
        raise ValueError(f"num_tasks must be positive, got {requested_tasks}")
    if allocated_tasks is not None and requested_tasks > allocated_tasks:
        raise ValueError(
            f"requested {requested_tasks} STAR processes but Slurm job {resolved_job_id} "
            f"allocates only {allocated_tasks} tasks/CPUs"
        )
    if requested_tasks < len(nodes):
        raise ValueError(
            f"requested {requested_tasks} STAR processes for {len(nodes)} Slurm nodes; "
            "use at least one process per node"
        )

    output_dir = Path(output_dir).expanduser().resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    machinefile_path = output_dir / f"hosts_{resolved_job_id}.ma"
    base, remainder = divmod(requested_tasks, len(nodes))
    lines = [f"{node}:{base + int(index < remainder)}" for index, node in enumerate(nodes)]
    machinefile_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return SlurmAllocation(
        job_id=resolved_job_id,
        nodes=nodes,
        num_tasks=requested_tasks,
        machinefile_path=machinefile_path,
    )


def _discover_job_for_current_host() -> str:
    user = os.environ.get("USER", "").strip()
    if not user:
        raise RuntimeError("cannot auto-detect a Slurm job because USER is unset; pass --slurm-job-id")
    current_host = socket.gethostname().split(".", 1)[0]
    output = _run(["squeue", "-u", user, "-h", "-t", "R", "-o", "%i|%N"])
    matches: list[str] = []
    for raw_line in output.splitlines():
        job_id, separator, node_expr = raw_line.strip().partition("|")
        if separator and current_host in _expand_nodelist(node_expr):
            matches.append(job_id)
    if not matches:
        raise RuntimeError(
            f"no running Slurm job contains current host {current_host}; pass --slurm-job-id"
        )
    if len(matches) > 1:
        raise RuntimeError(
            f"multiple running Slurm jobs contain current host {current_host}: "
            f"{', '.join(matches)}; pass --slurm-job-id"
        )
    return matches[0]


def _expand_nodelist(node_expr: str) -> tuple[str, ...]:
    output = _run(["scontrol", "show", "hostnames", node_expr])
    nodes = tuple(line.strip() for line in output.splitlines() if line.strip())
    if not nodes:
        raise RuntimeError(f"Slurm NodeList expanded to no hosts: {node_expr!r}")
    return nodes


def _parse_key_values(text: str) -> dict[str, str]:
    return dict(re.findall(r"(?:^|\s)([A-Za-z][A-Za-z0-9_]*)=(\S*)", text))


def _positive_int(value: str | None) -> int | None:
    if value is None or not value.isdigit():
        return None
    parsed = int(value)
    return parsed if parsed > 0 else None


def _run(command: list[str]) -> str:
    try:
        completed = subprocess.run(
            command,
            check=True,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
        )
    except FileNotFoundError as exc:
        raise RuntimeError(f"required Slurm command not found: {command[0]}") from exc
    except subprocess.CalledProcessError as exc:
        detail = (exc.stderr or exc.stdout or "unknown error").strip()
        raise RuntimeError(f"Slurm command failed ({' '.join(command)}): {detail}") from exc
    return completed.stdout
