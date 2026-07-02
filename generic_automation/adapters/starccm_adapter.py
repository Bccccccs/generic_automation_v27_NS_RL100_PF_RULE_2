from __future__ import annotations

import json
import logging
import subprocess
from pathlib import Path
from typing import Any

from generic_automation.core.adapter_base import Case
from generic_automation.starccm_translator import GenericAutomationStarCCMTranslator
from generic_automation.monitor.ai_monitor_outputs import STARCCM_LOG_NAME
from starccm_runtime.starccm_macro_builder import build_macro
from starccm_runtime.starccm_result_files import (
    cleanup_intermediate_outputs,
    collect_reports,
    write_output,
)
from generic_automation.core.runtime_metadata import (
    compute_mesh_cache_key,
    default_mesh_ready_filename,
    update_run_context,
)
from starccm_control import StarCCMControlLayer

logger = logging.getLogger(__name__)


class StarCCMAdapter:
    def __init__(self, check_interval: int = 500) -> None:
        self._check_interval = check_interval

    def _resolve_config_base_dir(self, case: Case) -> Path | None:
        config_path = str(getattr(case, "config_path", "") or "").strip()
        if config_path:
            return Path(config_path).expanduser().resolve().parent

        config_dir = str(getattr(case, "config_dir", "") or "").strip()
        if config_dir:
            return Path(config_dir).expanduser().resolve()
        return None

    def _resolve_existing_path(
        self,
        raw_path: str,
        *,
        case: Case,
        case_dir: Path,
        label: str,
        include_case_dir_fallback: bool = False,
        include_cwd_fallback: bool = False,
    ) -> Path:
        expanded = Path(raw_path).expanduser()
        candidates: list[tuple[str, Path]] = []

        if expanded.is_absolute():
            candidates.append(("absolute", expanded.resolve()))
        else:
            config_base = self._resolve_config_base_dir(case)
            if config_base is not None:
                candidates.append(
                    (f"config_dir={config_base}", (config_base / expanded).resolve())
                )
            if include_case_dir_fallback:
                candidates.append(
                    (f"case_dir={case_dir.resolve()}", (case_dir / expanded).resolve())
                )
            if include_cwd_fallback:
                cwd = Path.cwd().resolve()
                candidates.append((f"cwd={cwd}", (cwd / expanded).resolve()))
            if not candidates:
                cwd = Path.cwd().resolve()
                candidates.append((f"cwd={cwd}", (cwd / expanded).resolve()))

        for _, candidate in candidates:
            if candidate.exists():
                return candidate

        config_path = str(getattr(case, "config_path", "") or "").strip() or "(not set)"
        checked = "; ".join(f"{source} -> {path}" for source, path in candidates)
        raise FileNotFoundError(
            f"{label} not found. raw={raw_path!r}; config_path={config_path}; checked={checked}"
        )

    def run(
        self,
        case: Case,
        case_dir: Path,
        run_context: dict[str, Any] | None = None,
    ) -> None:
        case_dir.mkdir(parents=True, exist_ok=True)
        if not case.mesh_cache_key:
            case.mesh_cache_key = compute_mesh_cache_key(case)
        template_path = self._resolve_input_sim(case, case_dir)
        if run_context is not None:
            update_run_context(
                case_dir,
                status="running",
                run_mode=case.run_mode,
                input_sim=str(template_path.resolve()),
                mesh_cache_key=case.mesh_cache_key,
            )

        self._write_control_context(case_dir)
        self._write_runtime_plan(case, case_dir)
        macro_path = case_dir / "AutoSetupMacro.java"
        macro_path.write_text(
            build_macro(
                case,
                case_dir,
                self._check_interval,
                run_context=run_context,
            ),
            encoding="utf-8",
        )
        logger.info("Macro written: %s", macro_path)

        cmd = self._build_command(case, macro_path, template_path)

        log_path = case_dir / STARCCM_LOG_NAME
        log_path.parent.mkdir(parents=True, exist_ok=True)
        with log_path.open("w", encoding="utf-8") as log_f:
            if case.starccm_path.lower().endswith(".bat"):
                inner = subprocess.list2cmdline(cmd)
                cmd_str = f'cmd /c "{inner}"'
                logger.info("Command: %s", cmd_str)
                proc = subprocess.run(
                    cmd_str,
                    stdout=log_f,
                    stderr=subprocess.STDOUT,
                    cwd=case_dir,
                )
            else:
                logger.info("Command: %s", " ".join(cmd))
                proc = subprocess.run(
                    cmd,
                    stdout=log_f,
                    stderr=subprocess.STDOUT,
                    cwd=case_dir,
                )

        if proc.returncode != 0:
            tail_lines: list[str] = []
            try:
                with log_path.open(encoding="utf-8", errors="replace") as _lf:
                    tail_lines = _lf.readlines()[-80:]
            except Exception:
                pass
            tail_text = "".join(tail_lines).strip() if tail_lines else "(log not readable)"
            raise RuntimeError(
                f"STAR-CCM+ exited with code {proc.returncode}. Log: {log_path}\n"
                f"--- last log lines ---\n{tail_text}"
            )

        reports = collect_reports(case, case_dir)
        write_output(case, case_dir, reports)
        cleanup_intermediate_outputs(case_dir, macro_path)

    def _write_control_context(self, case_dir: Path) -> None:
        context_path = case_dir / "starccm_control_context.json"
        context = StarCCMControlLayer().macro_context()
        context_path.write_text(json.dumps(context, indent=2), encoding="utf-8")

    def _write_runtime_plan(self, case: Case, case_dir: Path) -> None:
        plan_path = case_dir / "starccm_runtime_plan.json"
        plan = GenericAutomationStarCCMTranslator().translate(
            case,
            check_interval=self._check_interval,
        )
        plan.write_json(plan_path)

    def _resolve_input_sim(self, case: Case, case_dir: Path) -> Path:
        run_mode = str(getattr(case, "run_mode", "full_run") or "full_run").strip().lower()
        if run_mode not in {"full_run", "mesh_only", "solve_only", "resume"}:
            raise ValueError(
                f"Unsupported run_mode {run_mode!r}. Expected full_run, mesh_only, solve_only, or resume."
            )

        if run_mode in {"solve_only", "resume"}:
            input_sim = str(getattr(case, "input_sim", "") or "").strip()
            if not input_sim:
                default_mesh_ready = case_dir / default_mesh_ready_filename(case, case.mesh_cache_key)
                if default_mesh_ready.exists():
                    input_sim = str(default_mesh_ready)
            if not input_sim:
                raise ValueError(
                    f"run_mode={run_mode} requires case.input_sim (or an existing mesh-ready sim in {case_dir})."
                )
            return self._resolve_existing_path(
                input_sim,
                case=case,
                case_dir=case_dir,
                label=f"Input .sim for run_mode={run_mode}",
                include_case_dir_fallback=True,
            )

        if not case.template_sim:
            raise ValueError(
                "'template_sim' must be set in config when using the starccm adapter."
            )
        return self._resolve_existing_path(
            case.template_sim,
            case=case,
            case_dir=case_dir,
            label="Template .sim",
            include_cwd_fallback=True,
        )

    def _build_command(self, case: Case, macro_path: Path, template_path: Path) -> list[str]:
        cmd = [case.starccm_path]
        if case.num_cores > 1:
            cmd += ["-np", str(case.num_cores)]
        if case.pod_key:
            cmd += ["-podkey", case.pod_key]
        cmd += ["-batch", str(macro_path)]
        cmd.append(str(template_path))
        return cmd
