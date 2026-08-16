"""B45/B05：提取 J02、J06 相对无喷气基准的单喷气真实响应。

该脚本只做响应统计，不会绕过 B04 质量门禁，也不会训练 ARX。历史字段
``Jet_Reaction_Z`` 按最终数据契约迁移为 J 表面压力/剪切力；喷气动量反作用力
在源数据缺失时保持缺失，绝不由前者代替。
"""

from __future__ import annotations

import argparse
import csv
import json
import math
from pathlib import Path
from statistics import fmean, pstdev


REGIONS = {
    "Fz_S1L": "underbody_lift_s1l",
    "Fz_S1R": "underbody_lift_s1r",
    "Fz_S2L": "underbody_lift_s2l",
    "Fz_S2R": "underbody_lift_s2r",
    "Fz_S3L": "underbody_lift_s3l",
    "Fz_S3R": "underbody_lift_s3r",
}
ZONE_JETS = {
    "S1L": "J01/J02/J05/J06",
    "S1R": "J03/J04/J07/J08",
    "S2L": "J09/J10/J13/J14",
    "S2R": "J11/J12/J15/J16",
    "S3L": "J17/J18/J21/J22",
    "S3R": "J19/J20/J23/J24",
}
LEGACY_FORCES = {
    "Fz_Total": ("legacy_underbody_plus_tail_force_z", "N"),
    "Drag_Total": ("legacy_limited_surface_drag", "N"),
    "Pitch_Moment": ("legacy_limited_surface_pitch_moment", "N-m"),
    "Roll_Moment": ("legacy_limited_surface_roll_moment", "N-m"),
}
SUMMARY_FIELDS = (
    "case_id",
    "jet_id",
    "signal_name",
    "quantity_group",
    "unit",
    "source_field",
    "availability",
    "baseline_case_id",
    "on_time_s",
    "off_time_s",
    "actual_massflow_mean_kg_s",
    "pre_delta_mean",
    "pre_delta_std",
    "peak_delta",
    "peak_time_aligned_s",
    "mean_delta",
    "response_delay_s",
    "recovery_time_s",
    "recovered_by_end",
    "fluctuation_std",
    "snr_linear",
    "snr_db",
    "quality_status",
    "interpretation_status",
    "notes",
)


def _read_rows(path: Path) -> list[dict[str, str]]:
    with path.open(encoding="utf-8-sig", newline="") as handle:
        return list(csv.DictReader(handle))


def _quality_status(case_dir: Path) -> str:
    path = case_dir / "quality_report.json"
    if not path.exists():
        return "MISSING"
    report = json.loads(path.read_text(encoding="utf-8"))
    b04 = report.get("B04_real_quality", report)
    return str(b04.get("summary", {}).get("overall_status", "UNKNOWN"))


def _float_rows(rows: list[dict[str, str]], column: str) -> list[float]:
    return [float(row[column]) for row in rows]


def _assert_aligned(reference: list[dict[str, str]], case: list[dict[str, str]]) -> None:
    if len(reference) != len(case):
        raise ValueError("基准与喷气算例行数不一致")
    for index, (left, right) in enumerate(zip(reference, case)):
        if not math.isclose(float(left["physical_time"]), float(right["physical_time"]), abs_tol=1e-9):
            raise ValueError(f"第 {index} 行 physical_time 未对齐")


def _first_sustained(values: list[float], threshold: float, start: int, stop: int, count: int = 5) -> int | None:
    for index in range(start, max(start, stop - count + 1)):
        if all(abs(values[position]) >= threshold for position in range(index, index + count)):
            return index
    return None


def _recovery(values: list[float], start: int, pre_mean: float, band: float, window: int = 100) -> tuple[int | None, bool]:
    if len(values) - start < window:
        return None, False
    rms = []
    for index in range(start, len(values) - window + 1):
        value = math.sqrt(fmean((values[position] - pre_mean) ** 2 for position in range(index, index + window)))
        rms.append((index, value))
    first_start = next((index for index, value in rms if value <= band), None)
    first = None if first_start is None else first_start + window - 1
    return first, rms[-1][1] <= band


def _metric_row(
    *,
    baseline_id: str,
    case_id: str,
    jet_id: str,
    signal_name: str,
    quantity_group: str,
    unit: str,
    source_field: str,
    reference: list[dict[str, str]],
    case: list[dict[str, str]],
    actual_column: str,
    quality_status: str,
    notes: str = "",
) -> dict[str, object]:
    times = _float_rows(case, "physical_time")
    actual = _float_rows(case, actual_column)
    active = [index for index, value in enumerate(actual) if value > 1e-8]
    if not active:
        raise ValueError(f"{case_id} 未检测到 {jet_id} actual_massflow")
    on_index, last_on_index = active[0], active[-1]
    dt = times[1] - times[0]
    on_time = times[on_index] - dt
    off_time = times[last_on_index]
    delta = [float(row[source_field]) - float(base[source_field]) for row, base in zip(case, reference)]
    pre = delta[:on_index]
    response = delta[on_index : last_on_index + 1]
    pre_mean = fmean(pre)
    pre_std = pstdev(pre)
    peak_relative = max(response, key=lambda value: abs(value - pre_mean))
    peak_index = on_index + max(range(len(response)), key=lambda index: abs(response[index] - pre_mean))
    response_threshold = max(3.0 * pre_std, 0.05 * abs(peak_relative - pre_mean), 1e-12)
    delay_index = _first_sustained(
        [value - pre_mean for value in delta], response_threshold, on_index, last_on_index + 1
    )
    recovery_band = max(3.0 * pre_std, 0.10 * abs(peak_relative - pre_mean), 1e-12)
    recovery_index, recovered_by_end = _recovery(delta, last_on_index + 1, pre_mean, recovery_band)
    signal_rms = math.sqrt(fmean((value - pre_mean) ** 2 for value in response))
    if pre_std == 0.0:
        snr_linear, snr_db = math.inf, math.inf
        snr_note = "喷气前与 G00 逐点完全一致，SNR 为 +inf"
    else:
        snr_linear = signal_rms / pre_std
        snr_db = 20.0 * math.log10(snr_linear) if snr_linear > 0.0 else -math.inf
        snr_note = ""
    return {
        "case_id": case_id,
        "jet_id": jet_id,
        "signal_name": signal_name,
        "quantity_group": quantity_group,
        "unit": unit,
        "source_field": source_field,
        "availability": "available",
        "baseline_case_id": baseline_id,
        "on_time_s": on_time,
        "off_time_s": off_time,
        "actual_massflow_mean_kg_s": fmean(actual[index] for index in active),
        "pre_delta_mean": pre_mean,
        "pre_delta_std": pre_std,
        "peak_delta": peak_relative - pre_mean,
        "peak_time_aligned_s": times[peak_index] - on_time,
        "mean_delta": fmean(response) - pre_mean,
        "response_delay_s": "" if delay_index is None else times[delay_index] - on_time,
        "recovery_time_s": "" if recovery_index is None else times[recovery_index] - off_time,
        "recovered_by_end": recovered_by_end,
        "fluctuation_std": pstdev(response),
        "snr_linear": snr_linear,
        "snr_db": snr_db,
        "quality_status": quality_status,
        "interpretation_status": "provisional_qc_failed" if quality_status != "PASS" else "accepted",
        "notes": "; ".join(value for value in (notes, snr_note) if value),
    }


def _missing_momentum_row(base: dict[str, object]) -> dict[str, object]:
    row = {field: "" for field in SUMMARY_FIELDS}
    row.update(
        {
            "case_id": base["case_id"],
            "jet_id": base["jet_id"],
            "signal_name": "jet_momentum_reaction_z",
            "quantity_group": "jet_momentum_reaction",
            "unit": "N",
            "source_field": "",
            "availability": "missing",
            "baseline_case_id": base["baseline_case_id"],
            "on_time_s": base["on_time_s"],
            "off_time_s": base["off_time_s"],
            "actual_massflow_mean_kg_s": base["actual_massflow_mean_kg_s"],
            "quality_status": base["quality_status"],
            "interpretation_status": "blocked_missing_definition",
            "notes": "源数据无独立动量通量/出口速度，禁止用历史 Jet_Reaction_Z 代替",
        }
    )
    return row


def build_summary(baseline_dir: Path, cases: list[tuple[Path, str, str]]) -> list[dict[str, object]]:
    reference = _read_rows(baseline_dir / "processed" / "timeseries.csv")
    output: list[dict[str, object]] = []
    for case_dir, jet_id, actual_column in cases:
        case = _read_rows(case_dir / "processed" / "timeseries.csv")
        _assert_aligned(reference, case)
        quality = _quality_status(case_dir)
        case_rows: list[dict[str, object]] = []
        for source, canonical in REGIONS.items():
            case_rows.append(
                _metric_row(
                    baseline_id=baseline_dir.name,
                    case_id=case_dir.name,
                    jet_id=jet_id,
                    signal_name=canonical,
                    quantity_group="underbody_region_lift",
                    unit="N",
                    source_field=source,
                    reference=reference,
                    case=case,
                    actual_column=actual_column,
                    quality_status=quality,
                )
            )
        for source, (canonical, unit) in LEGACY_FORCES.items():
            case_rows.append(
                _metric_row(
                    baseline_id=baseline_dir.name,
                    case_id=case_dir.name,
                    jet_id=jet_id,
                    signal_name=canonical,
                    quantity_group="legacy_vehicle_scope_aerodynamic_load",
                    unit=unit,
                    source_field=source,
                    reference=reference,
                    case=case,
                    actual_column=actual_column,
                    quality_status=quality,
                    notes="历史积分面不是已确认的整车全部外表面，不得标作 vehicle_lift/drag/moment",
                )
            )
        surface = _metric_row(
            baseline_id=baseline_dir.name,
            case_id=case_dir.name,
            jet_id=jet_id,
            signal_name="j_surface_force_z",
            quantity_group="j_surface_pressure_shear_force",
            unit="N",
            source_field="Jet_Reaction_Z",
            reference=reference,
            case=case,
            actual_column=actual_column,
            quality_status=quality,
            notes="历史字段仅迁移为 J 表面 +Z 压力/剪切力，不代表动量反作用力",
        )
        case_rows.append(surface)
        case_rows.append(_missing_momentum_row(surface))
        output.extend(case_rows)
    return output


def _fmt(value: object, digits: int = 3) -> str:
    if value == "" or value is None:
        return "NA"
    if isinstance(value, bool):
        return "是" if value else "否"
    if isinstance(value, (float, int)):
        if math.isinf(float(value)):
            return "+inf" if float(value) > 0 else "-inf"
        return f"{float(value):.{digits}f}"
    return str(value)


def _write_csv(path: Path, rows: list[dict[str, object]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=SUMMARY_FIELDS)
        writer.writeheader()
        writer.writerows(rows)


def _write_comparison(path: Path, rows: list[dict[str, object]], baseline_status: str) -> None:
    region_rows = [row for row in rows if row["quantity_group"] == "underbody_region_lift"]
    by_jet = {jet: [row for row in region_rows if row["jet_id"] == jet] for jet in ("J02", "J06")}
    lines = [
        "# B05 J02/J06 单喷气响应对比",
        "",
        "## 结论",
        "",
        f"本次采用同物理时间逐点差分 `ΔF(t)=F_jet(t)-F_G00(t)`；G00 B04 状态为 **{baseline_status}**。",
        "三组数据的格式、时间和实际质量流量可用于初步响应分析，但力定义门禁未通过，因此下表是临时工程判断，不是已验收物理结论。",
        "",
    ]
    for jet, values in by_jet.items():
        dominant = max(values, key=lambda row: abs(float(row["mean_delta"])))
        lines.append(
            f"- {jet} 按开启段平均变化主要影响 `{dominant['signal_name']}`："
            f"平均 {_fmt(dominant['mean_delta'])} N，峰值 {_fmt(dominant['peak_delta'])} N，"
            f"响应延迟 {_fmt(dominant['response_delay_s'], 4)} s；"
            f"关闭后末段{'已回到判据带内' if dominant['recovered_by_end'] else '未回到判据带内'}。"
        )
    lines += [
        "",
        "J02 和 J06 的几何归属都在 S1L 分组（S1L 对应 J01/J02/J05/J06），但两者观测到的平均主响应均在 S1R；在 STAR 模板、report 积分面和编号映射冻结前，不能把这一非局部结果解释成确定的气动耦合。",
        "",
        "## 2×6 初步影响表",
        "",
        "| 喷口 | 区域 | 峰值变化 (N) | 平均变化 (N) | 延迟 (s) | 恢复时间 (s) | 末段恢复 | 波动标准差 (N) | SNR (dB) |",
        "| --- | --- | ---: | ---: | ---: | ---: | --- | ---: | ---: |",
    ]
    for jet in ("J02", "J06"):
        for row in by_jet[jet]:
            zone = str(row["signal_name"]).removeprefix("underbody_lift_").upper()
            lines.append(
                f"| {jet} | {zone} | {_fmt(row['peak_delta'])} | {_fmt(row['mean_delta'])} | "
                f"{_fmt(row['response_delay_s'], 4)} | {_fmt(row['recovery_time_s'], 4)} | "
                f"{_fmt(row['recovered_by_end'])} | {_fmt(row['fluctuation_std'])} | {_fmt(row['snr_db'])} |"
            )
    lines += [
        "",
        "## 统计口径",
        "",
        "- 开启/关闭时刻由目标喷口 `actual_massflow` 大于 `1e-8 kg/s` 的首末样本确定；两组均为 0.4–0.5 s、约 1 kg/s。",
        "- 峰值取开启段内相对喷气前差分均值的最大绝对偏差；平均变化与波动标准差取完整开启段。",
        "- 响应延迟为连续 5 点超过 `max(3σ_pre, 5%峰值)` 的首次时刻。恢复为关闭后 0.01 s 滑窗 RMS 首次进入 `max(3σ_pre, 10%峰值)` 时的窗口末时刻；末段恢复按最后一个滑窗判断。",
        "- SNR 为开启段响应 RMS / 喷气前差分标准差，并以 `20log10` 转为 dB。J02 喷气前与 G00 逐点完全一致，因此分母为 0、SNR 记为 `+inf`，这不等价于无限物理测量精度。",
        "",
        "## 三类力严格分离",
        "",
        "- 车体气动力：当前只有历史有限积分面字段 `Fz_Total/Drag_Total/Pitch_Moment/Roll_Moment`，已在 CSV 中单列为 `legacy_vehicle_scope_aerodynamic_load`，不能冒充最终整车力。",
        "- J 表面受力：历史 `Jet_Reaction_Z` 按契约迁移为 `j_surface_force_z`，表示 J01..J24 表面的 +Z 压力+剪切力。",
        "- 喷气动量反作用力：源数据缺少独立的动量通量/出口速度，CSV 保留 `jet_momentum_reaction_z` 缺失行，绝不由 J 表面力填充。",
        "",
        "## 门禁、ROM 与联合确认",
        "",
        "三个算例当前均未通过 B04，尤其 G00 有定义类阻塞和漂移/跳变/左右不对称待浩坤判断；因此不执行 1 输入 6 输出 ARX 冒烟测试。只有修正后三算例全部 PASS，才允许任选一个喷口做流程冒烟，且结果不得作为真实 ROM 结论。",
        "当前两条独立单喷口轨迹只够做初步响应对比，远不足以训练 24 输入 ROM；仍需覆盖其余区域、喷口内重复性、幅值变化和独立验证数据。联合汇报前需与浩坤冻结 J/JET 名称、面积、`Fz/fz`、`actual_massflow`、J 表面力与动量反作用力定义及 STAR 模板版本。",
        "",
    ]
    path.write_text("\n".join(lines), encoding="utf-8")


def _write_plan(path: Path) -> None:
    fields = (
        "priority",
        "case_type",
        "jet_id",
        "expected_zone",
        "decision",
        "prerequisite",
        "purpose",
    )
    rows = [
        (0, "no_jet_repeat", "", "all", "RUN_FIRST", "冻结 STAR 模板与力定义", "复跑 G00；只有 B04 PASS 才放行任何喷气算例"),
        (1, "single_jet_repeat", "J02", "S1L", "HOLD", "新 G00 PASS", "验证同模板配对差分、重复性与已观察 S1R 主响应"),
        (2, "single_jet_repeat", "J06", "S1L", "HOLD", "J02 repeat PASS", "验证 J06 重复性及与 J02 的同区差异"),
        (3, "single_jet_new", "J03", "S1R", "NEXT_BATCH", "三算例全部 PASS", "覆盖 S1R，并检查左右/编号映射"),
        (4, "single_jet_new", "J09", "S2L", "NEXT_BATCH", "三算例全部 PASS", "覆盖尚未激励的 S2L"),
        (5, "single_jet_new", "J11", "S2R", "NEXT_BATCH", "三算例全部 PASS", "覆盖尚未激励的 S2R"),
        (6, "single_jet_new", "J17", "S3L", "NEXT_BATCH", "三算例全部 PASS", "覆盖尚未激励的 S3L"),
        (7, "single_jet_new", "J19", "S3R", "NEXT_BATCH", "三算例全部 PASS", "覆盖尚未激励的 S3R"),
        (8, "single_jet_new", "J01", "S1L", "OPTIONAL_REPLICATE", "优先覆盖五个未测区域后", "同区第三喷口，估计喷口内空间差异"),
    ]
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(fields)
        writer.writerows(rows)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--baseline", default="runs/week4/no_0816")
    parser.add_argument("--j02", default="runs/week4/j02_pluse_0816")
    parser.add_argument("--j06", default="runs/week4/j06_pluse_0816")
    parser.add_argument("--output-dir", default="artifacts/reports")
    args = parser.parse_args(argv)
    baseline = Path(args.baseline)
    rows = build_summary(
        baseline,
        [(Path(args.j02), "J02", "actual_massflow_02"), (Path(args.j06), "J06", "actual_massflow_06")],
    )
    output = Path(args.output_dir)
    _write_csv(output / "B05_single_jet_response_summary.csv", rows)
    _write_comparison(output / "B05_J02_J06_response_comparison.md", rows, _quality_status(baseline))
    _write_plan(output / "B05_next_case_plan.csv")
    print(f"B45/B05 delivery written to {output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
