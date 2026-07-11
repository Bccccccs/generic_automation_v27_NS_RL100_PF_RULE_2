"""Quality checks for STAR-exported case data.

All checks are designed as methods on ``QualityChecker`` so they can be used
standalone or composed by :func:`case_data_loader.load_case`.

Acceptance criteria
===================
1. Missing required columns → **ERROR**
2. ``physical_time`` not monotonically increasing → **ERROR**
3. NaN values in any numeric column → **ERROR**
4. Missing units or sign-direction documentation → **WARNING**
5. Jet on/off (JET_NN) does not match ``cmd_massflow_NN`` → **ERROR**
6. ``cmd_massflow_NN`` and ``actual_massflow_NN`` are NOT separate → **ERROR**
7. No-jet case: ``Jet_Reaction_Z`` = 0 is NOT treated as data loss → **WARNING**

该模块实现了流控流水线的"验收标准"检查,共有 7 项检查规则。
所有检查方法都是 QualityChecker 类的静态方法,既可单独调用,
也可由 case_data_loader.load_case 统一组合使用。

检查结果分为两类:
- ERROR(错误):数据必须修复,否则无法进入后续处理
- WARNING(警告):数据可用但建议完善文档或确认

设计原则:
- 每一项检查都是独立的,可以单独启用/禁用
- 检查结果以字符串列表形式返回,便于日志记录和报告生成
- 数值比较使用容差(1e-12)以避免浮点精度问题
"""

from __future__ import annotations

import math
import re
import warnings as _warnings
from typing import Any

# Standard column set
# 必选的基础列集合,包含:
# - physical_time: 物理时间(仿真步进时间)
# - Fz_S1L~Fz_S3R: 六个底部力传感器的法向力分量
# - Fz_Total: 总法向力(六个传感器之和或 STAR 直接导出的总力)
# - Drag_Total: 总阻力
# - Pitch_Moment: 俯仰力矩
# - Roll_Moment: 滚转力矩
# - Jet_Reaction_Z: 喷气反力(Z 方向)
REQUIRED_BASE_COLUMNS = (
    "physical_time",
    "Fz_S1L", "Fz_S1R",
    "Fz_S2L", "Fz_S2R",
    "Fz_S3L", "Fz_S3R",
    "Fz_Total",
    "Drag_Total",
    "Pitch_Moment",
    "Roll_Moment",
    "Jet_Reaction_Z",
)

# 喷气阀门开关列(24 个阀门,编号 01~24)
JET_COLUMNS = tuple(f"JET_{idx:02d}" for idx in range(1, 25))
# 指令质量流量列(24 个阀门)
CMD_MASSFLOW_COLUMNS = tuple(f"cmd_massflow_{idx:02d}" for idx in range(1, 25))
# 实际质量流量列(24 个阀门)
ACTUAL_MASSFLOW_COLUMNS = tuple(f"actual_massflow_{idx:02d}" for idx in range(1, 25))


class QualityChecker:
    """Collection of static quality-check methods for case data.

    质量检查器:包含所有 7 项数据质量检查的静态方法集合。
    设计为静态方法类,无实例状态,可以安全地重复调用或并行使用。
    """

    # ── Helpers ───────────────────────────────────────────────────────

    @staticmethod
    def _get_column(rows: list[dict[str, Any]], col: str) -> list[Any]:
        """
        从行数据中提取指定列的所有值。
        相当于对 rows 列表做列投影(column projection)。
        """
        return [row.get(col) for row in rows]

    @staticmethod
    def _to_float(value: Any) -> float | None:
        """
        安全地将任意类型值转换为浮点数。
        对于 None、NaN、Inf、空字符串等特殊值返回 None,
        避免在后续计算中引发异常或产生不可预期的结果。
        """
        if value is None:
            return None
        if isinstance(value, str):
            stripped = value.strip().lower()
            if stripped in {"nan", "inf", "-inf", ""}:
                return None
        try:
            v = float(value)
            if math.isnan(v) or math.isinf(v):
                return None
            return v
        except (ValueError, TypeError):
            return None

    @staticmethod
    def _is_nan(value: Any) -> bool:
        """
        判断值是否为 NaN 或类似 NaN 的无效值。
        覆盖常见的 NaN 表现形态:None、字符串 "nan"/"inf"、
        Python 浮点数的 float('nan') 等。
        """
        if value is None:
            return True
        if isinstance(value, str):
            return value.strip().lower() in {"nan", "inf", "-inf", "", "none"}
        if isinstance(value, float):
            return math.isnan(value) or math.isinf(value)
        return False

    # ── Check 1: Required columns ─────────────────────────────────────
    # 检查 1:验证所有必需的列是否都存在

    @staticmethod
    def check_required_columns(
        rows: list[dict[str, Any]],
        required: tuple[str, ...],
    ) -> list[str]:
        """Return ERROR for every missing required column.

        检查所有必需的列是否存在于时间序列数据中。
        通过检查第一行数据的键集合来确定数据包含哪些列。
        缺失任何一个必需列都返回 ERROR。
        """
        if not rows:
            return ["timeseries is empty — cannot check columns"]

        present = set(rows[0].keys())
        missing = [col for col in required if col not in present]
        return [
            f"Missing required column: {col}" for col in missing
        ]

    # ── Check 2: Monotonic time ───────────────────────────────────────
    # 检查 2:验证 physical_time 是否严格单调递增

    @staticmethod
    def check_monotonic_time(rows: list[dict[str, Any]]) -> list[str]:
        """Return ERROR if ``physical_time`` is not strictly increasing.

        A small tolerance (1e-12) is allowed for floating-point drift.

        仿真时间必须严格单调递增。非单调的时间序列可能意味着:
        - 数据导出时发生了错乱
        - 多个 CSV 文件合并时时间戳对齐出错
        - 仿真本身存在时间回退(物理上不应发生)

        允许 1e-12 的微小容差以处理浮点舍入误差。
        重复的时间值也会被报告(可能导致后续插值/差分计算问题)。
        """
        errors: list[str] = []
        if not rows:
            return errors

        times = []
        for row in rows:
            t = QualityChecker._to_float(row.get("physical_time"))
            if t is None:
                errors.append(
                    "physical_time is missing or NaN — cannot check monotonicity"
                )
                return errors
            times.append(t)

        for i in range(1, len(times)):
            # 检查时间倒流(递减)
            if times[i] < times[i - 1] - 1e-12:
                errors.append(
                    f"physical_time not monotonically increasing at row {i}: "
                    f"{times[i-1]} → {times[i]} (diff = {times[i] - times[i-1]})"
                )
            # 检查时间重复(相同值)
            elif times[i] == times[i - 1]:
                errors.append(
                    f"physical_time has duplicate value at row {i}: {times[i]}"
                )

        return errors

    # ── Check 3: NaN values ───────────────────────────────────────────
    # 检查 3:验证所有数值列中没有 NaN 或无穷大值

    @staticmethod
    def check_nan_values(rows: list[dict[str, Any]]) -> list[str]:
        """Return ERROR for every cell that is NaN, None, or infinite.

        逐行逐列地检查每个单元格的值是否为无效值。
        NaN 可能来自:
        - STAR 导出时某些监视器在特定时间步未输出数据
        - CSV 解析时的格式错误
        - 物理量在仿真发散时产生无穷大

        每个 NaN 细胞都单独报告,以便精确定位问题。
        """
        errors: list[str] = []
        if not rows:
            return errors

        for row_idx, row in enumerate(rows):
            for col, value in row.items():
                if QualityChecker._is_nan(value):
                    errors.append(
                        f"NaN/missing value at row {row_idx}, column '{col}'"
                    )
        return errors

    # ── Check 4: Units and sign direction ─────────────────────────────
    # 检查 4:验证 Manifest 中是否记录了单位信息和正方向约定

    @staticmethod
    def check_units_and_direction(manifest: dict[str, Any]) -> list[str]:
        """Return WARNING if units or sign-direction are not documented.

        Looks for manifest keys like ``units``, ``sign_convention``,
        ``force_units``, ``moment_units``, or any description of
        positive-force direction.

        检查清单(manifest)中是否包含单位和符号方向约定文档。
        这些元数据虽然不影响数据本身的数值正确性,
        但对后续的数据使用和理解至关重要:
        - 力的单位通常是 N(牛顿),力矩单位是 Nm(牛米)
        - 正方向约定:例如"正 Fz = 升力向上"或"正 Fz = 垂向力向上"

        缺少这些信息时只产生 WARNING(不阻断流程),
        因为数据本身仍然可用,只是需要补充文档。
        """
        warnings: list[str] = []
        unit_keys = {"units", "force_units", "moment_units", "massflow_units"}
        direction_keys = {"sign_convention", "positive_direction", "direction"}

        found_unit_info = any(
            key in manifest for key in unit_keys
        ) or "N" in str(manifest.get("notes", ""))

        found_direction_info = any(
            key in manifest for key in direction_keys
        ) or re.search(
            r"正方向|positive|direction|sign", str(manifest), re.IGNORECASE
        )

        if not found_unit_info:
            warnings.append(
                "Units not documented in manifest — "
                "add 'units' or 'force_units' field (e.g. 'N' for force, 'Nm' for moment)"
            )

        if not found_direction_info:
            warnings.append(
                "Sign/direction convention not documented in manifest — "
                "add 'sign_convention' field describing positive force direction "
                "(e.g. 'positive Fz = lift upward')"
            )

        return warnings

    # ── Check 5: Jet on/off vs massflow consistency ───────────────────
    # 检查 5:验证喷气阀门开关信号与指令质量流量之间的一致性

    @staticmethod
    def check_jet_massflow_consistency(rows: list[dict[str, Any]]) -> list[str]:
        """Return ERROR when JET_NN state contradicts ``cmd_massflow_NN``.

        For each row and each jet::

            JET_NN == 0  and  cmd_massflow_NN > 0  →  inconsistent
            JET_NN == 1  and  cmd_massflow_NN == 0 →  inconsistent

        喷气阀门开关(JET_NN 的 0/1 值)应与指令质量流量一致:
        - 阀门打开(JET=1)时,指令质量流量应大于 0
        - 阀门关闭(JET=0)时,指令质量流量应为 0

        逻辑关系:
        JET_NN 是控制系统发出的开关指令(布尔值),
        cmd_massflow_NN 是相应的质量流量数值指令,
        两者应该逻辑一致。
        不一致可能表明数据导出或后处理中发生了错误。
        """
        errors: list[str] = []
        if not rows:
            return errors

        has_cmd = all(f"cmd_massflow_{idx:02d}" in rows[0] for idx in range(1, 25))
        if not has_cmd:
            return errors  # skip if no cmd_massflow columns / 没有 cmd_massflow 列则跳过

        for row_idx, row in enumerate(rows):
            for idx in range(1, 25):
                jet_col = f"JET_{idx:02d}"
                cmd_col = f"cmd_massflow_{idx:02d}"

                jet_val = QualityChecker._to_float(row.get(jet_col))
                cmd_val = QualityChecker._to_float(row.get(cmd_col))

                if jet_val is None or cmd_val is None:
                    continue

                jet_on = abs(jet_val) > 0.5  # treat >0.5 as "on" / 大于 0.5 视为"开"
                cmd_nonzero = abs(cmd_val) > 1e-12

                # 阀门打开但质量流量为零:控制逻辑错误
                if jet_on and not cmd_nonzero:
                    errors.append(
                        f"Row {row_idx}: {jet_col}={jet_val} (ON) but "
                        f"{cmd_col}={cmd_val} (zero massflow) — inconsistent"
                    )
                # 阀门关闭但质量流量非零:控制逻辑错误
                if not jet_on and cmd_nonzero:
                    errors.append(
                        f"Row {row_idx}: {jet_col}={jet_val} (OFF) but "
                        f"{cmd_col}={cmd_val} (nonzero massflow) — inconsistent"
                    )

        return errors

    # ── Check 6: cmd vs actual massflow separation ────────────────────
    # 检查 6:验证指令质量流量与实际质量流量必须分开存储

    @staticmethod
    def check_massflow_separation(
        rows: list[dict[str, Any]],
        *,
        allow_identical_actual: bool = False,
    ) -> list[str]:
        """Return ERROR if ``cmd_massflow_NN`` and ``actual_massflow_NN``
        are not stored as separate columns.

        When both exist, also warn if they are identical to machine precision
        (which would indicate the actual was never recorded separately).

        指令质量流量(cmd_massflow)和实际质量流量(actual_massflow)
        在物理意义上是不同的量:
        - cmd_massflow:控制系统发出的质量流量指令(期望值)
        - actual_massflow:仿真计算返回的实际质量流量(响应值)

        两者必须分别存储,因为:
        1. 它们的差值反映了控制回路的跟踪误差
        2. 将它们混为一个列会导致重要信息丢失

        如果两者在数值上完全相同(达到机器精度),则说明实际质量流量可能
        从未被独立记录,这会导致无法评估控制跟踪性能。
        """
        errors: list[str] = []
        if not rows:
            return errors

        first_row = rows[0]
        cmd_cols = [col for col in first_row if col.startswith("cmd_massflow")]
        actual_cols = [col for col in first_row if col.startswith("actual_massflow")]

        if not cmd_cols and not actual_cols:
            return errors  # no massflow data at all — skip / 没有质量流量数据则跳过

        if cmd_cols and not actual_cols:
            errors.append(
                "cmd_massflow columns found but actual_massflow columns missing — "
                "they must be stored separately"
            )
            return errors

        if actual_cols and not cmd_cols:
            errors.append(
                "actual_massflow columns found but cmd_massflow columns missing — "
                "they must be stored separately"
            )
            return errors

        # Check that cmd and actual are NOT identical within tolerance
        # 检查 cmd 和 actual 在容差范围内是否完全相同(表明 actual 未独立记录)
        identical_count = 0
        for row_idx, row in enumerate(rows):
            for idx in range(1, 25):
                cmd_col = f"cmd_massflow_{idx:02d}"
                actual_col = f"actual_massflow_{idx:02d}"
                if cmd_col in row and actual_col in row:
                    cmd_v = QualityChecker._to_float(row[cmd_col])
                    actual_v = QualityChecker._to_float(row[actual_col])
                    if cmd_v is not None and actual_v is not None:
                        if abs(cmd_v - actual_v) < 1e-12:
                            identical_count += 1

        # 如果所有行所有阀门的 cmd 和 actual 都完全一致,发出警告
        if (
            identical_count > 0
            and identical_count == len(rows) * 24
            and not allow_identical_actual
        ):
            _warnings.warn(
                "cmd_massflow and actual_massflow are identical for all rows — "
                "actual massflow may not have been recorded separately"
            )

        return errors

    # ── Check 7: No-jet case Jet_Reaction_Z ───────────────────────────
    # 检查 7:对于无喷气工况,验证 Jet_Reaction_Z 的正确性

    @staticmethod
    def check_no_jet_jrz(rows: list[dict[str, Any]]) -> list[str]:
        """Return WARNING for no-jet case when ``Jet_Reaction_Z`` is 0.

        In a no-jet case (no JET_NN columns), ``Jet_Reaction_Z`` = 0 is
        expected (no reaction force).  This is NOT data loss — it is the
        correct physical result.  We warn only if the column is absent,
        which would be unusual.

        对于无喷气工况(数据中没有 JET_NN 列):
        - Jet_Reaction_Z = 0 是预期的正确物理结果(没有喷气反力)
        - Jet_Reaction_Z = 0 不是数据丢失!这是正确的物理结果
        - 如果 Jet_Reaction_Z 列完全缺失,则建议补充(但可以接受)

        这项检查的目的是防止用户将"物理上正确"的零值误判为数据缺失。
        同时,如果无喷气工况下出现了非零的喷气反力,则需要确认是否合理。
        """
        warnings: list[str] = []
        if not rows:
            return warnings

        first_row = rows[0]
        if "Jet_Reaction_Z" not in first_row:
            warnings.append(
                "No-jet case with no Jet_Reaction_Z column — this is acceptable "
                "if no jet reaction data was exported. Add the column for completeness."
            )
        else:
            # Check that Jet_Reaction_Z is indeed 0 or near-zero
            values = [
                QualityChecker._to_float(row.get("Jet_Reaction_Z"))
                for row in rows
            ]
            values = [v for v in values if v is not None]
            if values:
                max_abs = max(abs(v) for v in values)
                if max_abs > 1e-6:
                    warnings.append(
                        f"No-jet case but Jet_Reaction_Z has non-zero values "
                        f"(max |val| = {max_abs:.6e}) — verify this is correct"
                    )
                else:
                    warnings.append(
                        "No-jet case confirmed: Jet_Reaction_Z ≈ 0.0 "
                        "(expected physical result — NOT data loss)"
                    )

        return warnings

    # ── Run all checks ────────────────────────────────────────────────
    # 统一运行所有检查的入口方法

    @classmethod
    def run_all(
        cls,
        rows: list[dict[str, Any]],
        manifest: dict[str, Any],
        *,
        has_jet_data: bool | None = None,
        check_mode: str = "star_ingest",
    ) -> dict[str, list[str]]:
        """Run all applicable checks and return ``{"errors": [...], "warnings": [...]}``.

        统一运行所有 7 项质量检查,返回分类的检查结果。
        该方法自动识别数据是否包含喷气信号,并相应启用/禁用
        与喷气相关的检查(检查 5、6、7)。

        参数:
            rows: 时间序列数据行列表
            manifest: 案例的清单元数据(用于单位和方向检查)
            has_jet_data: 是否包含喷气数据。若为 None 则自动检测。
            check_mode: 检查模式,影响某些检查的严格程度。
                - "star_ingest": 标准严格模式
                - "mock"/"arx_use"/"ccm": 宽松模式(允许 cmd=actual)
        """
        # 自动检测是否包含喷气数据(通过检查列名前缀)
        if has_jet_data is None:
            has_jet_data = any(col.startswith("JET_") for col in (rows[0] if rows else {}))

        errors: list[str] = []
        warnings: list[str] = []

        # 前 4 项检查对所有数据都适用
        errors.extend(cls.check_required_columns(rows, REQUIRED_BASE_COLUMNS))  # 检查1:必需列
        errors.extend(cls.check_monotonic_time(rows))                           # 检查2:时间单调性
        errors.extend(cls.check_nan_values(rows))                               # 检查3:NaN 值
        warnings.extend(cls.check_units_and_direction(manifest))                # 检查4:单位与方向

        if has_jet_data:
            # 有喷气数据时:检查喷气/质量流量一致性和分离性
            errors.extend(cls.check_jet_massflow_consistency(rows))  # 检查5:一致性
            errors.extend(
                cls.check_massflow_separation(
                    rows,
                    # 在某些模式(mock/arx_use/ccm)下允许 cmd=actual,
                    # 因为这些数据来源可能没有独立记录 actual 值
                    allow_identical_actual=check_mode in {"mock", "arx_use", "ccm"},
                )
            )  # 检查6:分离性
        else:
            # 无喷气数据时:检查 Jet_Reaction_Z 的合理性
            warnings.extend(cls.check_no_jet_jrz(rows))  # 检查7:无喷气工况

        return {"errors": errors, "warnings": warnings}
