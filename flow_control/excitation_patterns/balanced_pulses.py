"""可复现的平衡随机单喷口脉冲激励模式；可选 jet_groups 按指定组合同时开启。"""

from __future__ import annotations

import random

from .common import ActuationConfig, ScheduleTable, empty_switches, jet_index


def _balanced_events(config: ActuationConfig) -> list[tuple[int, float]]:
    events = [
        (jet, config.mass_flow_levels[repeat_index % len(config.mass_flow_levels)])
        for jet in config.jet_ids
        for repeat_index in range(config.repetitions_per_jet)
    ]
    random.Random(config.random_seed).shuffle(events)
    for index in range(1, len(events)):
        if events[index][0] == events[index - 1][0]:
            swap_index = next(
                (
                    candidate
                    for candidate in range(index + 1, len(events))
                    if events[candidate][0] != events[index - 1][0]
                    and (
                        candidate + 1 == len(events)
                        or events[candidate + 1][0] != events[index][0]
                    )
                ),
                None,
            )
            if swap_index is not None:
                events[index], events[swap_index] = events[swap_index], events[index]
    return events


def _group_events(config: ActuationConfig) -> list[tuple[tuple[int, ...], float]]:
    """jet_groups 组合事件：按给定顺序排列，不打乱；每轮重复按序号轮换流量档。"""

    return [
        (group, config.mass_flow_levels[repeat_index % len(config.mass_flow_levels)])
        for repeat_index in range(config.repetitions_per_jet)
        for group in config.jet_groups
    ]


def _group_errors(config: ActuationConfig) -> list[str]:
    errors: list[str] = []
    allowed = set(config.jet_ids)
    for group_index, group in enumerate(config.jet_groups):
        if not group:
            errors.append(f"jet_groups[{group_index}] must not be empty")
        if len(set(group)) != len(group):
            errors.append(f"jet_groups[{group_index}] must not contain duplicate jets")
        for jet in group:
            try:
                jet_index(jet, config.n_jets)
            except ValueError as exc:
                errors.append(str(exc))
            if allowed and jet not in allowed:
                errors.append(f"jet_groups[{group_index}] jet {jet} is not listed in jet_ids")
    return errors


def generate(config: ActuationConfig) -> tuple[ScheduleTable, dict[str, object], list[str]]:
    errors: list[str] = []
    if config.jet_groups:
        errors.extend(_group_errors(config))
    elif not config.jet_ids:
        errors.append("balanced_singlejet_pulses requires jet_ids")
    if not config.mass_flow_levels or any(level <= 0.0 for level in config.mass_flow_levels):
        errors.append("balanced_singlejet_pulses requires positive mass_flow_levels")
    if config.repetitions_per_jet <= 0:
        errors.append("repetitions_per_jet must be positive")
    if config.checkpoint_duration <= 0.0:
        errors.append("checkpoint_duration must be positive")
    if config.recovery_duration <= 0.0:
        errors.append("recovery_duration must be positive")
    if len(set(config.jet_ids)) != len(config.jet_ids):
        errors.append("jet_ids must not contain duplicates")
    for jet in config.jet_ids:
        try:
            jet_index(jet, config.n_jets)
        except ValueError as exc:
            errors.append(str(exc))
    if errors:
        return ScheduleTable([], []), {}, errors

    if config.jet_groups:
        events = _group_events(config)
    else:
        events = [((jet,), level) for jet, level in _balanced_events(config)]
    switches = empty_switches(1 + 2 * len(events), config.n_jets)
    massflows = [[0.0] * config.n_jets for _ in switches]
    durations = [config.checkpoint_duration]
    for event_index, (group, level) in enumerate(events):
        row_index = 1 + event_index * 2
        for jet in group:
            column_index = jet_index(jet, config.n_jets)
            switches[row_index][column_index] = 1
            massflows[row_index][column_index] = level
        durations.extend((config.actuation_window_duration, config.recovery_duration))

    return (
        ScheduleTable(
            switches=switches,
            massflows=massflows,
            window_durations=durations,
        ),
        {
            "jet_ids": list(config.jet_ids),
            "mass_flow_levels": list(config.mass_flow_levels),
            "jet_groups": [list(group) for group in config.jet_groups],
            "repetitions_per_jet": config.repetitions_per_jet,
            "checkpoint_duration": config.checkpoint_duration,
            "recovery_duration": config.recovery_duration,
            "random_seed": config.random_seed,
        },
        [],
    )
