#!/usr/bin/env python3

from __future__ import annotations

import argparse
import logging
import time
from pathlib import Path

from ai_case_modifier import AICaseModifier
from ai_parameter_generator import AIParameterGenerator
from project_config import load_config, parse_case, resolve_case_dir
from runtime_metadata import load_or_create_run_context, update_run_context

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
log = logging.getLogger(__name__)

DONE_FLAG = "sim_done.flag"
LIVE_LOG = "logs/starccm.log"


def _flag_enabled(value: object, default: bool = False) -> bool:
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "yes", "on"}
    return bool(value)


def _prepare_ai_config(cfg: dict) -> tuple[dict, bool]:
    ai_cfg = dict(cfg.get("ai_optimization") or {})
    rl_cfg = dict(ai_cfg.get("reinforcement_learning") or {})

    enabled = _flag_enabled(ai_cfg.get("enabled", True), default=True)
    intervention_enabled = _flag_enabled(rl_cfg.get("intervention_enabled", False))
    if enabled:
        return ai_cfg, intervention_enabled

    if intervention_enabled:
        print(
            "[Monitor] ai_optimization.enabled=false; forcing observe-only mode and disabling parameter updates.",
            flush=True,
        )
    else:
        print(
            "[Monitor] ai_optimization.enabled=false; continuing in profiling-only observe-only mode.",
            flush=True,
        )

    rl_cfg["intervention_enabled"] = False
    ai_cfg["reinforcement_learning"] = rl_cfg
    return ai_cfg, False


def _print_startup(
    config_path: Path,
    case_dir: Path,
    ai_cfg: dict,
    intervention_enabled: bool,
) -> None:
    print(f"[Monitor] config:   {config_path}", flush=True)
    print(f"[Monitor] case_dir: {case_dir}", flush=True)
    print(f"[Monitor] controller: {ai_cfg.get('controller', 'reinforcement_learning')}", flush=True)
    mode_label = "intervention enabled" if intervention_enabled else "observe-only (no parameter updates)"
    print(f"[Monitor] RL mode: {mode_label}", flush=True)
    print(f"[Monitor] Watching {case_dir / LIVE_LOG} for native STAR-CCM+ iteration data...", flush=True)
    print(f"[Monitor] Will exit when {case_dir / DONE_FLAG} appears.", flush=True)
    print("[Monitor] Press Ctrl+C to stop early.", flush=True)
    print("", flush=True)


def _check_done_flag(case_dir: Path) -> tuple[bool, float | None]:
    done_flag = case_dir / DONE_FLAG
    if not done_flag.exists():
        return True, None

    done_mtime = done_flag.stat().st_mtime
    live_log = case_dir / LIVE_LOG
    live_log_mtime = live_log.stat().st_mtime if live_log.exists() else None

    if live_log_mtime is not None and live_log_mtime > done_mtime:
        print(
            "[Monitor] WARNING: sim_done.flag 已存在，但比 starccm.log 更旧；"
            "本次视为陈旧标志，只有当 sim_done.flag 被重新写入时才会退出。",
            flush=True,
        )
        print(f"[Monitor] stale done flag: {done_flag}", flush=True)
        print("", flush=True)
        return True, done_mtime

    print(
        "[Monitor] ERROR: sim_done.flag 已存在，当前 case_dir 看起来已经结束。",
        flush=True,
    )
    print(
        "[Monitor] Remove the old flag or use a fresh case_name / case_dir before starting the monitor.",
        flush=True,
    )
    print(f"[Monitor] existing done flag: {done_flag}", flush=True)
    return False, None


def main() -> int:
    parser = argparse.ArgumentParser(
        description="RL monitor — run on login node alongside a SLURM simulation job."
    )
    parser.add_argument("--config", default="config.yaml", help="Path to config.yaml")
    parser.add_argument(
        "--case-dir",
        default=None,
        help="Override case_dir path (default: derived from config result_root + case_name)",
    )
    args = parser.parse_args()

    config_path = Path(args.config).resolve()
    cfg = load_config(config_path)
    ai_cfg, intervention_enabled = _prepare_ai_config(cfg)

    case = parse_case(cfg)
    case.config_path = str(config_path)
    case.config_dir = str(config_path.parent)
    case_dir = Path(args.case_dir).resolve() if args.case_dir else resolve_case_dir(
        config_path,
        cfg,
        case.case_name,
    )
    run_context = load_or_create_run_context(
        case_dir=case_dir,
        case=case,
        entrypoint="run_monitor_only.py",
        input_sim=case.input_sim,
    )
    _print_startup(config_path, case_dir, ai_cfg, intervention_enabled)

    can_continue, ignore_done_flag_mtime = _check_done_flag(case_dir)
    if not can_continue:
        return 1

    constraints = ai_cfg.get("parameter_constraints", {})
    modifier = AICaseModifier(
        case_dir=case_dir,
        sim_type=case.simulation_type,
        constraints=constraints,
        run_context=run_context,
    )
    generator = AIParameterGenerator(
        ai_config=ai_cfg,
        case_dir=case_dir,
        case=case,
        modifier=modifier,
        run_context=run_context,
    )

    generator.start()
    monitor_status = "completed"
    done_flag = case_dir / DONE_FLAG
    try:
        while True:
            if done_flag.exists():
                done_mtime = done_flag.stat().st_mtime
                if (
                    ignore_done_flag_mtime is None
                    or done_mtime > ignore_done_flag_mtime
                ):
                    log.info("[Monitor] sim_done.flag detected — simulation finished.")
                    break
            time.sleep(5)
    except KeyboardInterrupt:
        log.info("[Monitor] Interrupted by user.")
        monitor_status = "interrupted"
    finally:
        generator.stop()
        if monitor_status == "completed":
            update_run_context(case_dir, status="completed")
        else:
            update_run_context(case_dir, status="running", monitor_status=monitor_status)

    print(f"\n[Monitor] Done. AI triggered {generator.trigger_count} time(s).", flush=True)
    history_path = case_dir / AICaseModifier.HISTORY_FILE
    if history_path.exists():
        print(f"[Monitor] History: {history_path}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
