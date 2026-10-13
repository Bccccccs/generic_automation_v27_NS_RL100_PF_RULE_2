"""稀疏随机分组激励模式 —— 24 喷口筛选实验的核心设计。

这是最复杂的激励模式，设计目标是：
  1. 在激励窗口期间，每一帧精确激活 n_active_per_window 个喷口
  2. 所有喷口在激励阶段的总激活次数严格相等（公平性）
  3. 同一组合不重复出现（保证激励多样性）
  4. 同一喷口不连续激活超过 max_consecutive_on 次（避免热效应）
  5. 激励窗口结束后插入全关参考窗口

生成算法使用带回溯的约束搜索 + 候选评分机制。

数学约束：
  设 n_jets=24, n_active=3, n_excitation=72
  → 总激活次数 = 72 × 3 = 216
  → 每喷口应激活次数 = 216 / 24 = 9
"""

from __future__ import annotations

import itertools
import random

from .common import ActuationConfig, ScheduleTable, table_from_switches


def generate(config: ActuationConfig) -> tuple[ScheduleTable, dict[str, object], list[str]]:
    """生成稀疏随机分组激励计划。

    Args:
        config: 激励配置，使用 sparse_random_groups 相关参数。

    Returns:
        (ScheduleTable, extra_metadata, errors) 三元组。
    """
    matrix = generate_actuation_matrix(config)
    errors = validate_sparse_matrix(config, matrix)
    table = table_from_switches(matrix, mass_flow_rate=config.mass_flow_rate)
    extra = {
        "n_excitation_windows": config.n_excitation_windows,
        "n_reference_windows": config.n_reference_windows,
        "n_active_per_window": config.n_active_per_window,
        "expected_count_per_jet": config.expected_count_per_jet,
        "random_seed": config.random_seed,
    }
    return table, extra, errors


def generate_actuation_matrix(config: ActuationConfig) -> list[list[int]]:
    """生成可复现的稀疏激励矩阵（激励窗口 + 参考窗口）。

    使用随机搜索 + 回溯策略寻找满足所有约束的喷口组合序列。
    最多尝试 max_generation_attempts 次。

    Returns:
        [n_windows × n_jets] 的 0/1 矩阵。
    """
    _validate_config_shape(config)
    base_rng = random.Random(config.random_seed)
    target_count = config.expected_count_per_jet

    for _ in range(config.max_generation_attempts):
        attempt_seed = base_rng.randrange(0, 2**63)
        rng = random.Random(attempt_seed)
        excitation = _build_excitation_windows(config, [target_count] * config.n_jets, rng)
        if excitation is not None:
            references = [[0] * config.n_jets for _ in range(config.n_reference_windows)]
            return excitation + references

    raise RuntimeError(
        "failed to generate a schedule satisfying all constraints; "
        "increase max_generation_attempts or relax constraints"
    )


def validate_sparse_matrix(config: ActuationConfig, matrix: list[list[int]]) -> list[str]:
    """验证稀疏矩阵是否满足所有约束条件。

    检查项：
      - 总窗口数、列数正确
      - 激励窗口每帧活跃数 = n_active_per_window
      - 参考窗口全零
      - 各喷口激活次数相等（equal_activation_count=True 时）
      - 无重复组合
      - 无连续激活超限

    Returns:
        错误列表，空表示通过。
    """
    errors: list[str] = []
    excitation = matrix[: config.n_excitation_windows]
    references = matrix[config.n_excitation_windows :]

    if len(matrix) != config.sparse_total_windows:
        errors.append(f"expected {config.sparse_total_windows} windows, got {len(matrix)}")
    if any(len(row) != config.n_jets for row in matrix):
        errors.append(f"all windows must have {config.n_jets} jet columns")

    for window_id, row in enumerate(excitation):
        active = sum(row)
        if active != config.n_active_per_window:
            errors.append(
                f"window {window_id} has {active} active jets, expected {config.n_active_per_window}"
            )

    for ref_id, row in enumerate(references, start=config.n_excitation_windows):
        if sum(row) != 0:
            errors.append(f"reference window {ref_id} must have no active jets")

    if config.equal_activation_count:
        expected = config.expected_count_per_jet
        counts = activation_counts(config, matrix)
        for jet_name, count in zip(config.jet_names, counts):
            if count != expected:
                errors.append(f"{jet_name} appears {count} times, expected {expected}")

    combos = [tuple(idx for idx, value in enumerate(row) if value) for row in excitation]
    duplicates = sorted(combo for combo in set(combos) if combos.count(combo) > 1)
    if duplicates:
        rendered = ["+".join(config.jet_names[idx] for idx in combo) for combo in duplicates]
        errors.append(f"duplicate excitation combinations: {', '.join(rendered)}")

    for jet_idx, jet_name in enumerate(config.jet_names):
        streak = 0
        for window_id, row in enumerate(matrix):
            streak = streak + 1 if row[jet_idx] else 0
            if streak > config.max_consecutive_on:
                errors.append(
                    f"{jet_name} exceeds consecutive-on limit at window {window_id} "
                    f"(streak={streak})"
                )
                break
    return errors


def activation_counts(config: ActuationConfig, matrix: list[list[int]]) -> list[int]:
    """统计每个喷口在激励窗口中的激活次数。"""
    excitation = matrix[: config.n_excitation_windows]
    return [sum(row[jet_idx] for row in excitation) for jet_idx in range(config.n_jets)]


def _build_excitation_windows(
    config: ActuationConfig, remaining: list[int], rng: random.Random
) -> list[list[int]] | None:
    """使用带约束的回溯搜索构建激励窗口矩阵。

    这是一个经典的精确覆盖问题变体，使用递归回溯求解。
    每个喷口的剩余激活次数在 remaining 中追踪，当所有次数归零时搜索成功。

    Args:
        config: 配置（n_excitation_windows, n_active_per_window 等）。
        remaining: 每个喷口还需激活的次数（初始化时 = expected_count_per_jet）。
        rng: 随机数生成器，用于候选排序。

    Returns:
        [n_excitation_windows × n_jets] 矩阵，或 None（搜索失败）。
    """
    sequence: list[tuple[int, ...]] = []
    used_combos: set[tuple[int, ...]] = set()

    def recurse(window_idx: int) -> bool:
        """回溯递归：为第 window_idx 个窗口寻找有效的喷口组合。"""
        if window_idx == config.n_excitation_windows:
            return all(count == 0 for count in remaining)

        # 获取候选组合列表（已按评分降序排列）
        candidates = _candidate_combinations(config, remaining, sequence, used_combos, rng)
        for combo in candidates:
            # 尝试这个组合
            for jet_idx in combo:
                remaining[jet_idx] -= 1
            sequence.append(combo)
            used_combos.add(combo)
            if recurse(window_idx + 1):
                return True
            # 回溯：撤销选择
            used_combos.remove(combo)
            sequence.pop()
            for jet_idx in combo:
                remaining[jet_idx] += 1
        return False

    if not recurse(0):
        return None

    # 将组合序列转换为 0/1 矩阵
    rows: list[list[int]] = []
    for combo in sequence:
        row = [0] * config.n_jets
        for jet_idx in combo:
            row[jet_idx] = 1
        rows.append(row)
    return rows


def _candidate_combinations(
    config: ActuationConfig,
    remaining: list[int],
    sequence: list[tuple[int, ...]],
    used_combos: set[tuple[int, ...]],
    rng: random.Random,
) -> list[tuple[int, ...]]:
    """生成候选喷口组合并按评分排序。

    评分策略：
      - 优先选择"稀缺"喷口（剩余激活次数多的组合得分高）
      - 减去平衡惩罚项（鼓励各喷口剩余次数均匀）
      - 加上随机扰动以增加多样性

    Args:
        config: 配置。
        remaining: 剩余激活次数。
        sequence: 已确定的组合序列。
        used_combos: 已使用过的组合集合（避免重复）。
        rng: 随机数生成器。

    Returns:
        按评分降序排列的候选组合列表。
    """
    windows_left_after_pick = config.n_excitation_windows - len(sequence) - 1
    blocked = _currently_blocked_jets(config, sequence)
    available = [
        jet_idx for jet_idx, count in enumerate(remaining) if count > 0 and jet_idx not in blocked
    ]

    candidates: list[tuple[float, tuple[int, ...]]] = []
    for combo in itertools.combinations(available, config.n_active_per_window):
        if combo in used_combos:
            continue
        after = remaining[:]
        for jet_idx in combo:
            after[jet_idx] -= 1
        # 检查剩余次数是否在可行范围内
        if any(count < 0 or count > windows_left_after_pick for count in after):
            continue
        scarcity_score = sum(remaining[jet_idx] for jet_idx in combo)
        balance_penalty = max(after) - min(after)
        candidates.append((scarcity_score * 10 - balance_penalty + rng.random(), combo))

    candidates.sort(reverse=True)
    return [combo for _, combo in candidates]


def _currently_blocked_jets(config: ActuationConfig, sequence: list[tuple[int, ...]]) -> set[int]:
    """计算在当前状态下应被禁止的喷口（连续激活超限）。"""
    if config.max_consecutive_on <= 0 or len(sequence) < config.max_consecutive_on:
        return set()
    recent = sequence[-config.max_consecutive_on :]
    return {
        jet_idx
        for jet_idx in range(config.n_jets)
        if all(jet_idx in combo for combo in recent)
    }


def _validate_config_shape(config: ActuationConfig) -> None:
    """验证 sparse_random_groups 模式的配置约束。"""
    if config.n_jets <= 0:
        raise ValueError("n_jets must be positive")
    if not 0 < config.n_active_per_window <= config.n_jets:
        raise ValueError("n_active_per_window must be in [1, n_jets]")
    if config.n_excitation_windows <= 0:
        raise ValueError("n_excitation_windows must be positive")
    if config.n_reference_windows < 0:
        raise ValueError("n_reference_windows must be non-negative")
    if config.window_duration <= 0:
        raise ValueError("window_duration must be positive")
    if config.max_consecutive_on <= 0:
        raise ValueError("max_consecutive_on must be positive")
    if not config.equal_activation_count:
        raise ValueError("sparse_random_groups requires equal_activation_count: true")
    _ = config.expected_count_per_jet  # 触发整除检查
