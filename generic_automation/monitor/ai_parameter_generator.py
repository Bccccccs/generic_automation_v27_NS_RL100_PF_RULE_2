from __future__ import annotations

import csv
import json
import logging
import threading
from pathlib import Path
from typing import Any

from generic_automation.core.adapter_base import Case
from generic_automation.monitor.ai_monitor_outputs import (
    ACTION_LOG_NAME,
    OBS_STREAM_NAME,
    PROFILING_ACTIONS_NAME,
    PROFILING_SUMMARY_NAME,
    PROFILING_TIMESERIES_CSV_NAME,
    PROFILING_TIMESERIES_FIELDS,
    PROFILING_TIMESERIES_NAME,
    SOLVER_PROFILING_SUMMARY_NAME,
    STARCCM_LOG_NAME,
    SUMMARY_NAME,
    build_action_event_record,
    build_experiment_summary,
    build_observation_record,
    build_profiling_summary,
    build_profiling_timeseries_record,
)
from generic_automation.monitor.ai_case_modifier import AICaseModifier
from generic_automation.rl.rl_controller import ReinforcementLearningController
from generic_automation.rl.rl_runtime_registry import RL_RUNTIME_PARAMETER_NAMES
from generic_automation.rl.rl_runtime_service import RLRuntimeService
from generic_automation.rl.rl_trigger import RLTrigger
from generic_automation.core.runtime_metadata import (
    ACTION_ACK_LOG_FILE,
    CANONICAL_ACTIONS_FILE,
    CANONICAL_OBSERVATIONS_FILE,
    PROTOCOL_VERSION,
    append_jsonl,
)
from generic_automation.core.runtime_value_utils import optional_text, safe_float, safe_int, to_csv_scalar
from generic_automation.starccm.starccm_log_reader import StarCCMLogReader
from generic_automation.starccm.starccm_log_parser import parse_starccm_mesh_cell_count

log = logging.getLogger(__name__)


def _normalize_controller_mode(ai_config: dict[str, Any]) -> str:
    controller_mode = str(
        ai_config.get("controller", ai_config.get("strategy", "reinforcement_learning"))
    ).strip().lower()
    if controller_mode in {
        "rl",
        "reinforcement_learning",
        "reinforcement-learning",
        "q_learning",
        "q-learning",
    }:
        return "reinforcement_learning"
    raise ValueError(
        "Only controller=reinforcement_learning is supported; "
        "the LLM controller path has been removed."
    )


class AIParameterGenerator:
    def __init__(
        self,
        ai_config: dict[str, Any],
        case_dir: Path,
        case: Case,
        modifier: AICaseModifier,
        run_context: dict[str, Any] | None = None,
    ) -> None:
        self._ai_config = ai_config
        self._case_dir = case_dir
        self._case = case
        self._modifier = modifier
        self._run_context = run_context or {}
        self._run_id = str(self._run_context.get("run_id", "") or "")

        self._controller_mode = _normalize_controller_mode(ai_config)

        rl_config = ai_config.get("reinforcement_learning", {})
        self._rl_controller: ReinforcementLearningController | None = None
        if self._controller_mode == "reinforcement_learning":
            self._rl_controller = ReinforcementLearningController(
                rl_config=rl_config,
                case_dir=case_dir,
                case=case,
            )

        self._poll_interval: float = float(ai_config.get("poll_interval", 2.0))
        self._decision_interval: int = max(
            1,
            int(ai_config.get("decision_interval_iterations", 30)),
        )
        trigger_cfg_all = ai_config.get("trigger", {})
        self._sim_type = case.simulation_type.lower()
        trigger_cfg = (
            trigger_cfg_all.get("transient", {})
            if self._sim_type == "transient"
            else trigger_cfg_all.get("steady", {})
        )

        self._log_reader = StarCCMLogReader(case=case)
        rl_start_iteration = max(0, int(ai_config.get("rl_start_iteration", 0)))
        self._trigger = RLTrigger(
            case=case,
            sim_type=self._sim_type,
            decision_interval=self._decision_interval,
            trigger_cfg=trigger_cfg,
            start_iteration=rl_start_iteration,
        )
        self._runtime_service = RLRuntimeService(
            rl_config=rl_config,
            case=case,
            modifier=modifier,
            controller=self._rl_controller,
            decision_interval=self._decision_interval,
        )

        self._stop_event = threading.Event()
        self._thread: threading.Thread | None = None
        self._window: list[dict[str, Any]] = []
        self._obs_stream_path = case_dir / OBS_STREAM_NAME
        self._action_log_path = case_dir / ACTION_LOG_NAME
        self._canonical_observations_path = case_dir / CANONICAL_OBSERVATIONS_FILE
        self._canonical_actions_path = case_dir / CANONICAL_ACTIONS_FILE
        self._ack_events_path = case_dir / ACTION_ACK_LOG_FILE
        self._summary_path = case_dir / SUMMARY_NAME
        self._profiling_timeseries_path = case_dir / PROFILING_TIMESERIES_NAME
        self._profiling_timeseries_csv_path = case_dir / PROFILING_TIMESERIES_CSV_NAME
        self._profiling_actions_path = case_dir / PROFILING_ACTIONS_NAME
        self._profiling_summary_path = case_dir / PROFILING_SUMMARY_NAME
        self._solver_profiling_summary_path = case_dir / SOLVER_PROFILING_SUMMARY_NAME
        self._mesh_cells_hint: int | None = None
        self._divergence_events: int = 0
        self._blocked_action_count: int = 0

    def start(self) -> None:
        if self._thread is not None and self._thread.is_alive():
            return
        self._thread = threading.Thread(
            target=self._watch_loop,
            name="ai-monitor",
            daemon=True,
        )
        self._thread.start()
        log.info("[ParamGenerator] monitor started, poll interval %.1fs", self._poll_interval)

    def stop(self) -> None:
        if self._thread is None:
            self._write_experiment_summary()
            return
        self._stop_event.set()
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=10)
        log.info(
            "[ParamGenerator] monitor stopped, trigger_count=%d",
            self._runtime_service.trigger_count,
        )
        self._write_experiment_summary()

    @property
    def trigger_count(self) -> int:
        return self._runtime_service.trigger_count

    def _watch_loop(self) -> None:
        live_log = self._case_dir / STARCCM_LOG_NAME
        log.debug(
            "[ParamGenerator] monitor starting: log=%s case_dir=%s sim_type=%s "
            "controller=%s min_window=%s decision_interval=%s drag_key=%s total_key=%s "
            "trigger_metrics=%s",
            live_log.resolve(),
            self._case_dir.resolve(),
            self._sim_type,
            self._controller_mode,
            self._trigger.min_window,
            self._decision_interval,
            self._case.drag_report_name,
            self._case.total_report_name,
            self._trigger.metric_names,
        )

        no_file_warned = False
        poll_count = 0
        while not self._stop_event.is_set():
            poll_count += 1
            try:
                no_file_warned, new_rows = self._read_rows_for_poll(
                    live_log,
                    poll_count,
                    no_file_warned,
                )
                if new_rows:
                    self._handle_new_rows(new_rows, poll_count)
                self._sync_action_ack_events()
                self._maybe_trigger_update(new_rows)

            except Exception as exc:
                log.warning("[ParamGenerator] monitor loop ignored exception: %s", exc)
                log.debug("[ParamGenerator] monitor loop traceback", exc_info=True)

            self._stop_event.wait(timeout=self._poll_interval)

    def _read_rows_for_poll(
        self,
        live_log: Path,
        poll_count: int,
        no_file_warned: bool,
    ) -> tuple[bool, list[dict[str, Any]]]:
        if not live_log.exists():
            if not no_file_warned or poll_count % 30 == 0:
                log.debug(
                    "[ParamGenerator] starccm.log not found yet (poll=%d): %s",
                    poll_count,
                    live_log.resolve(),
                )
            return True, []

        if no_file_warned:
            log.debug(
                "[ParamGenerator] starccm.log detected, start reading (poll=%d)",
                poll_count,
            )

        previous_row = self._window[-1] if self._window else None
        new_rows = self._log_reader.read_new_rows(live_log, previous_row)
        return False, new_rows

    def _handle_new_rows(self, new_rows: list[dict[str, Any]], poll_count: int) -> None:
        log.debug(
            "[ParamGenerator] poll=%d read %d rows, window_after=%d latest_iter=%s",
            poll_count,
            len(new_rows),
            len(self._window) + len(new_rows),
            new_rows[-1].get("iteration", "?"),
        )
        self._track_divergence_events(new_rows)
        self._window.extend(new_rows)
        self._write_obs_stream_rows(new_rows)
        self._write_profiling_rows(new_rows)

    def _maybe_trigger_update(self, new_rows: list[dict[str, Any]]) -> None:
        if not new_rows:
            return
        if not self._trigger.should_trigger(
            self._window,
            self._runtime_service.last_attempt_iter,
        ):
            return

        latest = self._window[-1]
        current_iter = int(latest.get("iteration", 0))
        log.debug(
            "[ParamGenerator] trigger matched, requesting controller at iter=%d",
            current_iter,
        )
        log.info("[ParamGenerator] trigger matched at iter=%d", current_iter)
        trigger_count_before = self._runtime_service.trigger_count
        self._runtime_service.perform_update(
            current_iter=current_iter,
            latest_obs=latest,
            window=self._window,
            current_values=self._current_modifiable_values(),
        )
        if self._runtime_service.last_update_result.get("decision_emitted", True):
            self._write_action_event(
                current_iter=current_iter,
                params_changed=self._runtime_service.trigger_count > trigger_count_before,
            )

    def _sync_action_ack_events(self) -> None:
        ack_events = self._modifier.read_new_ack_events()
        if not ack_events:
            return
        self._runtime_service.consume_action_ack_events(ack_events)
        log.info("[ParamGenerator] consumed %d STAR ack event(s)", len(ack_events))

    def _current_modifiable_values(self) -> dict[str, Any]:
        return {
            key: getattr(self._case, key)
            for key in self._modifier._whitelist
            if hasattr(self._case, key)
        }

    def _observation_id_for_row(self, row: dict[str, Any]) -> str:
        iteration = int(row.get("iteration", 0) or 0)
        return f"{self._run_id or self._case.case_name}:obs:{iteration}"

    def _track_divergence_events(self, new_rows: list[dict[str, Any]]) -> None:
        prev_row = self._window[-1] if self._window else None
        for row in new_rows:
            if prev_row is not None:
                prev_r = safe_float(prev_row.get("max_residual"))
                curr_r = safe_float(row.get("max_residual"))
                if prev_r and curr_r and curr_r > 3.0 * prev_r:
                    self._divergence_events += 1
            prev_row = row

    def _write_obs_stream_rows(self, new_rows: list[dict[str, Any]]) -> None:
        if self._rl_controller is None:
            return
        try:
            diagnostics = self._rl_controller.get_window_diagnostics(self._window)
            last_action = self._rl_controller.last_action
            current_params = self._current_runtime_parameter_snapshot()
            with self._obs_stream_path.open("a", encoding="utf-8") as stream_file:
                for row in new_rows:
                    record = build_observation_record(
                        row=row,
                        diagnostics=diagnostics,
                        last_action=last_action,
                        current_params=current_params,
                    )
                    stream_file.write(json.dumps(record, ensure_ascii=False) + "\n")
                    append_jsonl(
                        self._canonical_observations_path,
                        {
                            "protocol_version": PROTOCOL_VERSION,
                            "record_type": "observation",
                            "run_id": self._run_id or None,
                            "observation_id": self._observation_id_for_row(row),
                            **record,
                        },
                    )
        except Exception as exc:
            log.debug("[ParamGenerator] obs stream write failed: %s", exc)

    def _write_profiling_rows(self, new_rows: list[dict[str, Any]]) -> None:
        if not new_rows:
            return
        try:
            current_params = self._current_runtime_parameter_snapshot()
            solver_meta = self._read_solver_profiling_metadata()
            start_index = len(self._window) - len(new_rows)
            csv_needs_header = not self._profiling_timeseries_csv_path.exists()
            with self._profiling_timeseries_path.open("a", encoding="utf-8") as jsonl_file:
                with self._profiling_timeseries_csv_path.open(
                    "a",
                    newline="",
                    encoding="utf-8",
                ) as csv_file:
                    writer = csv.DictWriter(
                        csv_file,
                        fieldnames=list(PROFILING_TIMESERIES_FIELDS),
                    )
                    if csv_needs_header:
                        writer.writeheader()
                    for offset, row in enumerate(new_rows):
                        row_index = start_index + offset
                        record = build_profiling_timeseries_record(
                            case=self._case,
                            controller_mode=self._controller_mode,
                            row=row,
                            row_index=row_index,
                            window=self._window,
                            current_params=current_params,
                            solver_meta=solver_meta,
                        )
                        jsonl_file.write(json.dumps(record, ensure_ascii=False) + "\n")
                        writer.writerow(
                            {
                                field: to_csv_scalar(record.get(field))
                                for field in PROFILING_TIMESERIES_FIELDS
                            }
                        )
        except Exception as exc:
            log.debug("[ParamGenerator] profiling stream write failed: %s", exc)

    def _write_action_event(self, current_iter: int, params_changed: bool) -> None:
        if self._rl_controller is None:
            return
        try:
            solver_meta = self._read_solver_profiling_metadata()
            meta = self._rl_controller.last_suggest_metadata
            if meta is None:
                return
            update = self._runtime_service.last_update_result
            action = meta.get("action", "")
            if not params_changed and action != "hold":
                self._blocked_action_count += 1
            record = build_action_event_record(
                case=self._case,
                controller_mode=self._controller_mode,
                run_id=self._run_id,
                current_iter=current_iter,
                params_changed=params_changed,
                meta=meta,
                update=update,
                solver_meta=solver_meta,
                intervention_enabled=self._runtime_service.intervention_enabled,
                pending_action_id=self._runtime_service.pending_action_id,
            )
            self._append_json_line(self._action_log_path, record)
            self._append_json_line(self._profiling_actions_path, record)
            append_jsonl(
                self._canonical_actions_path,
                {
                    "record_type": "action",
                    **record,
                },
            )
        except Exception as exc:
            log.debug("[ParamGenerator] action event write failed: %s", exc)

    def _append_json_line(self, path: Path, record: dict[str, Any]) -> None:
        with path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(record, ensure_ascii=False) + "\n")

    def _write_experiment_summary(self) -> None:
        try:
            summary = build_experiment_summary(
                case=self._case,
                controller_mode=self._controller_mode,
                run_id=self._run_id,
                window=self._window,
                attempt_count=self._runtime_service.attempt_count,
                trigger_count=self._runtime_service.trigger_count,
                blocked_action_count=self._blocked_action_count,
                divergence_events=self._divergence_events,
                intervention_enabled=self._runtime_service.intervention_enabled,
            )
            self._summary_path.write_text(
                json.dumps(summary, indent=2, ensure_ascii=False),
                encoding="utf-8",
            )
            last_row = self._window[-1] if self._window else {}
            profiling_summary = self._build_profiling_summary(
                base_summary=summary,
                last_row=last_row,
            )
            self._profiling_summary_path.write_text(
                json.dumps(profiling_summary, indent=2, ensure_ascii=False),
                encoding="utf-8",
            )
            log.info("[ParamGenerator] experiment summary written: %s", self._summary_path)
        except Exception as exc:
            log.warning("[ParamGenerator] failed to write experiment summary: %s", exc)

    def _build_profiling_summary(
        self,
        base_summary: dict[str, Any],
        last_row: dict[str, Any],
    ) -> dict[str, Any]:
        solver_meta = self._read_solver_profiling_metadata()
        return build_profiling_summary(
            case=self._case,
            base_summary=base_summary,
            last_row=last_row,
            solver_meta=solver_meta,
            profiling_outputs={
                "timeseries_jsonl": self._profiling_timeseries_path.name,
                "timeseries_csv": self._profiling_timeseries_csv_path.name,
                "actions_jsonl": self._profiling_actions_path.name,
                "summary_json": self._profiling_summary_path.name,
                "solver_profiling_summary_json": self._solver_profiling_summary_path.name,
                "observation_stream_jsonl": self._obs_stream_path.name,
                "canonical_observations_jsonl": self._canonical_observations_path.name,
                "canonical_actions_jsonl": self._canonical_actions_path.name,
                "action_ack_events_jsonl": self._ack_events_path.name,
                "legacy_action_log_jsonl": self._action_log_path.name,
                "legacy_summary_json": self._summary_path.name,
            },
        )

    def _read_solver_profiling_metadata(self) -> dict[str, Any]:
        payload: dict[str, Any] = {}
        try:
            payload = json.loads(
                self._solver_profiling_summary_path.read_text(encoding="utf-8")
            )
        except (OSError, json.JSONDecodeError):
            payload = {}

        mesh_cells = safe_int(payload.get("mesh_cells"))
        if mesh_cells is None and self._mesh_cells_hint is None:
            self._mesh_cells_hint = parse_starccm_mesh_cell_count(
                self._case_dir / STARCCM_LOG_NAME
            )
        if mesh_cells is None:
            mesh_cells = self._mesh_cells_hint

        return {
            "solver_type": optional_text(payload.get("solver_type")),
            "mesh_cells": mesh_cells,
            "starccm_version": optional_text(payload.get("starccm_version")),
        }

    def _current_runtime_parameter_snapshot(self) -> dict[str, Any]:
        return {
            key: getattr(self._case, key)
            for key in RL_RUNTIME_PARAMETER_NAMES
            if hasattr(self._case, key)
        }
