from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from adapter_base import Case

PROTOCOL_VERSION = 2

RUN_CONTEXT_FILE = "run_context.json"
PARAM_UPDATE_FILE = "rl/param_update.json"
PARAM_ACK_FILE = "rl/param_update_ack.json"
PENDING_ACTION_FILE = "rl/pending_action.json"
ACTION_ACK_LOG_FILE = "rl/action_ack_events.jsonl"
CANONICAL_OBSERVATIONS_FILE = "rl/observations.jsonl"
CANONICAL_ACTIONS_FILE = "rl/actions.jsonl"


def resolve_sims_dir(case_dir: Path) -> Path:
    return case_dir.parent / "sims"


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def read_json(path: Path, default: Any = None) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, TypeError):
        return default


def write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(payload, indent=2, ensure_ascii=False, sort_keys=False),
        encoding="utf-8",
    )


def append_jsonl(path: Path, record: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(record, ensure_ascii=False) + "\n")


def compute_mesh_cache_key(case: Case) -> str:
    mesh_payload = {
        "geometry_file": _case_text(case, "geometry_file"),
        "base_mesh_size": getattr(case, "base_mesh_size", None),
        "surface_mesh_size": getattr(case, "surface_mesh_size", None),
        "min_surface_size": getattr(case, "min_surface_size", None),
        "surface_growth_rate": getattr(case, "surface_growth_rate", None),
        "num_prism_layers": getattr(case, "num_prism_layers", None),
        "prism_layer_thickness": getattr(case, "prism_layer_thickness", None),
        "prism_layer_stretching": getattr(case, "prism_layer_stretching", None),
        "prism_wall_thickness": getattr(case, "prism_wall_thickness", None),
        "train_target_size": getattr(case, "train_target_size", None),
        "train_min_size": getattr(case, "train_min_size", None),
        "train_prism_thickness": getattr(case, "train_prism_thickness", None),
        "train_prism_layers": getattr(case, "train_prism_layers", None),
        "zone1_mesh_size": getattr(case, "zone1_mesh_size", None),
        "zone2_mesh_size": getattr(case, "zone2_mesh_size", None),
        "zone3_mesh_size": getattr(case, "zone3_mesh_size", None),
        "zone4_mesh_size": getattr(case, "zone4_mesh_size", None),
        "volume_mesh_controls": getattr(case, "volume_mesh_controls", []),
        "surface_mesh_controls": getattr(case, "surface_mesh_controls", []),
        "simulation_type": _case_text(case, "simulation_type"),
        "turbulence_model": _case_text(case, "turbulence_model"),
        "energy_equation": bool(getattr(case, "energy_equation", False)),
        "fluid": _case_text(case, "fluid"),
        "domain_corner1": getattr(case, "domain_corner1", []),
        "domain_corner2": getattr(case, "domain_corner2", []),
        "ground_sliding": bool(getattr(case, "ground_sliding", False)),
    }
    encoded = json.dumps(mesh_payload, sort_keys=True, ensure_ascii=False).encode("utf-8")
    return hashlib.sha1(encoded).hexdigest()[:12]


def _case_text(case: Case, field: str, default: str = "") -> str:
    return str(getattr(case, field, default) or default)


def _new_run_context(
    case_dir: Path,
    case: Case,
    *,
    entrypoint: str,
    input_sim: str,
    run_id: str,
    started_at: str,
    mesh_cache_key: str,
    now: str,
) -> dict[str, Any]:
    return {
        "protocol_version": PROTOCOL_VERSION,
        "run_id": run_id,
        "case_name": case.case_name,
        "case_dir": str(case_dir.resolve()),
        "entrypoint": entrypoint,
        "run_mode": _case_text(case, "run_mode", "full_run").strip().lower(),
        "input_sim": input_sim,
        "mesh_cache_key": mesh_cache_key,
        "mesh_ready_sim": str(
            (resolve_sims_dir(case_dir) / default_mesh_ready_filename(case, mesh_cache_key)).resolve()
        ),
        "started_at": started_at,
        "last_seen_at": now,
        "status": "running",
    }


def default_mesh_ready_filename(case: Case, mesh_cache_key: str | None = None) -> str:
    cache_key = mesh_cache_key or compute_mesh_cache_key(case)
    case_name = str(getattr(case, "case_name", "case") or "case")
    return f"{case_name}_mesh_ready_{cache_key}.sim"


def default_result_sim_filename(case: Case) -> str:
    case_name = str(getattr(case, "case_name", "case") or "case")
    return f"{case_name}_result.sim"


def default_solver_init_filename(case: Case) -> str:
    case_name = str(getattr(case, "case_name", "case") or "case")
    return f"{case_name}_solver_init_iter0.sim"


def default_periodic_checkpoint_filename(case: Case, iteration: int) -> str:
    case_name = str(getattr(case, "case_name", "case") or "case")
    return f"{case_name}_periodic_checkpoint_iter{max(int(iteration), 0)}.sim"


def load_or_create_run_context(
    case_dir: Path,
    case: Case,
    *,
    entrypoint: str,
    input_sim: str | None = None,
) -> dict[str, Any]:
    path = case_dir / RUN_CONTEXT_FILE
    existing = read_json(path, default={})
    now = utc_now_iso()
    mesh_cache_key = _case_text(case, "mesh_cache_key") or compute_mesh_cache_key(case)
    resolved_input_sim = str(input_sim or getattr(case, "input_sim", "") or "").strip()

    should_reuse = (
        isinstance(existing, dict)
        and bool(existing.get("run_id"))
        and str(existing.get("status", "")).strip().lower() == "running"
    )
    run_id = str(existing.get("run_id")) if should_reuse else f"{case.case_name}-{now}"
    started_at = str(existing.get("started_at")) if should_reuse else now

    context = _new_run_context(
        case_dir,
        case,
        entrypoint=entrypoint,
        input_sim=resolved_input_sim,
        run_id=run_id,
        started_at=started_at,
        mesh_cache_key=mesh_cache_key,
        now=now,
    )
    write_json(path, context)
    return context


def update_run_context(case_dir: Path, *, status: str | None = None, **fields: Any) -> dict[str, Any]:
    path = case_dir / RUN_CONTEXT_FILE
    context = read_json(path, default={})
    if not isinstance(context, dict):
        context = {}
    context.update(fields)
    if status is not None:
        context["status"] = status
        if status == "completed":
            context["completed_at"] = utc_now_iso()
        elif status == "failed":
            context["failed_at"] = utc_now_iso()
    context["last_seen_at"] = utc_now_iso()
    write_json(path, context)
    return context
