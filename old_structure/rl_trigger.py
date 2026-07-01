from __future__ import annotations

import logging
from typing import Any

from adapter_base import Case

log = logging.getLogger(__name__)


class RLTrigger:
    def __init__(
        self,
        case: Case,
        sim_type: str,
        decision_interval: int,
        trigger_cfg: dict[str, Any],
        start_iteration: int = 0,
    ) -> None:
        self._case = case
        self._sim_type = sim_type
        self._decision_interval = max(1, decision_interval)
        self._start_iteration = max(0, start_iteration)
        if sim_type == "transient":
            self._min_window = int(trigger_cfg.get("min_window", 50))
        else:
            self._min_window = int(trigger_cfg.get("min_window", 200))
        self._trigger_metric_specs = self._build_trigger_metric_specs(trigger_cfg)
        self.metric_names = [spec["name"] for spec in self._trigger_metric_specs]

    @property
    def min_window(self) -> int:
        return self._min_window

    def should_trigger(
        self,
        window: list[dict[str, Any]],
        last_attempt_iter: int,
    ) -> bool:
        latest_iter = int(window[-1].get("iteration", 0))
        first_trigger_iter = (
            self._start_iteration
            if self._start_iteration > 0
            else self._decision_interval
        )
        if latest_iter < first_trigger_iter:
            log.debug(
                "[RLTrigger] no trigger: waiting for first trigger at iter=%d (current=%d)",
                first_trigger_iter,
                latest_iter,
            )
            return False

        current_bucket = latest_iter // self._decision_interval
        last_bucket = (
            last_attempt_iter // self._decision_interval
            if last_attempt_iter >= 0
            else -1
        )
        if current_bucket <= last_bucket:
            next_target = (last_bucket + 1) * self._decision_interval
            log.debug(
                "[RLTrigger] no trigger: current iter=%d, next decision bucket starts at %d",
                latest_iter,
                next_target,
            )
            return False

        target_iter = current_bucket * self._decision_interval
        log.debug(
            "[RLTrigger] fixed cadence trigger: latest_iter=%d interval=%d target=%d",
            latest_iter,
            self._decision_interval,
            target_iter,
        )
        return True

    def _build_trigger_metric_specs(self, trigger_cfg: dict[str, Any]) -> list[dict[str, Any]]:
        raw_metrics = trigger_cfg.get("output_metrics")
        parsed: list[dict[str, Any]] = []

        if isinstance(raw_metrics, str) and raw_metrics.strip():
            raw_metrics = [item.strip() for item in raw_metrics.split(",") if item.strip()]

        if isinstance(raw_metrics, list):
            for item in raw_metrics:
                if isinstance(item, str):
                    name = item.strip()
                    if not name:
                        continue
                    parsed.append({"name": name, "goal": "minimize"})
                    continue

                if not isinstance(item, dict):
                    continue

                name = str(item.get("name", "")).strip()
                if not name:
                    continue

                goal = str(item.get("goal", "minimize")).strip().lower()
                if goal not in {"minimize", "maximize"}:
                    goal = "minimize"

                spec: dict[str, Any] = {"name": name, "goal": goal}
                parsed.append(spec)

        if not parsed:
            parsed = self._default_trigger_metric_specs()

        deduped: list[dict[str, Any]] = []
        seen_names: set[str] = set()
        for spec in parsed:
            name = spec["name"]
            if name in seen_names:
                continue
            seen_names.add(name)
            deduped.append(spec)
        return deduped

    def _default_trigger_metric_specs(self) -> list[dict[str, Any]]:
        defaults = [
            {"name": self._case.drag_report_name, "goal": "minimize"},
            {"name": self._case.train_surface_pressure_report_name, "goal": "minimize"},
        ]
        deduped: list[dict[str, Any]] = []
        seen_names: set[str] = set()
        for spec in defaults:
            name = spec["name"]
            if not name or name in seen_names:
                continue
            seen_names.add(name)
            deduped.append(spec)
        return deduped
