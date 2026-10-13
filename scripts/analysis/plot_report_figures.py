"""按当前 B04 ROM 结果重画汇报用的图 2/5/6/7（数字全部取自 B54 产物，不手填）。"""
import argparse
import csv
import json
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np

plt.rcParams["font.sans-serif"] = ["Heiti SC", "Arial Unicode MS", "DejaVu Sans"]
plt.rcParams["axes.unicode_minus"] = False

ZONES = ["Fz_S1L", "Fz_S1R", "Fz_S2L", "Fz_S2R", "Fz_S3L", "Fz_S3R"]
LABELS = [z.replace("Fz_", "") for z in ZONES]
JETS = ["02", "03", "09", "11", "17", "19"]
DT = 1e-4
C_TRUTH, C_PRED, C_NOISE, C_TRAIN, C_VAL = "#222222", "#d62728", "#c8c4bc", "#9ec5f0", "#2b7bd6"


def read_csv(path):
    with open(path, newline="") as f:
        return list(csv.DictReader(f))


def jet_intervals(t, flow, thr=0.1):
    on = flow > thr
    edges = np.flatnonzero(np.diff(on.astype(int)))
    starts = list(t[edges[on[edges + 1]] + 1]) if len(edges) else []
    ends = list(t[edges[~on[edges + 1]]]) if len(edges) else []
    if on[0]:
        starts.insert(0, t[0])
    if on[-1]:
        ends.append(t[-1])
    return list(zip(starts, ends))


def fig2_raw_vs_delta(case_dir, ref_dir, tmin, out):
    def load(d):
        rows = read_csv(Path(d) / "processed/timeseries.csv")
        t = np.array([float(r["physical_time"]) for r in rows])
        cols = ZONES + [f"actual_massflow_{j}" for j in JETS]
        return np.rint(t / DT).astype(int), {c: np.array([float(r[c]) for r in rows]) for c in cols}

    sc, case = load(case_dir)
    sr, ref = load(ref_dir)
    common, ia, ib = np.intersect1d(sc, sr, return_indices=True)
    keep = common * DT >= tmin
    ia, ib, t = ia[keep], ib[keep], common[keep] * DT
    flow = np.max([case[f"actual_massflow_{j}"][ia] for j in JETS], axis=0)
    spans = jet_intervals(t, flow)

    fig, axes = plt.subplots(len(ZONES), 1, figsize=(15, 2.3 * len(ZONES)), sharex=True)
    for ax, z, lab in zip(axes, ZONES, LABELS):
        x, r = case[z][ia], ref[z][ib]
        for a, b in spans:
            ax.axvspan(a, b, color="#f2c94c", alpha=0.35, lw=0)
        ax.plot(t, x, color="C0", lw=0.6, label="有喷气 Fz")
        ax.plot(t, r, color="C1", lw=0.6, label="无喷气基准 Fz")
        ax.plot(t, x - r, color=C_PRED, lw=0.8, label="差值 ΔFz")
        ax.axhline(0, color="gray", lw=0.5)
        ax.set_ylabel(f"{lab} [N]")
        ax.grid(alpha=0.3)
    axes[0].legend(ncol=4, fontsize=9, loc="upper right",
                   handles=axes[0].get_lines()[:3] + [plt.Rectangle((0, 0), 1, 1, color="#f2c94c", alpha=0.35)],
                   labels=["有喷气 Fz", "无喷气基准 Fz", "差值 ΔFz = 有喷气 − 无喷气", "喷气时段"])
    axes[0].set_title("训练集：六区升力做差前后对比（同一纵轴比例）")
    axes[-1].set_xlabel("物理时间 [s]")
    fig.tight_layout()
    fig.savefig(out, dpi=150)
    plt.close(fig)


def fig5_validation(pred_csv, out):
    rows = read_csv(pred_csv)
    t = np.array([float(r["physical_time"]) for r in rows])
    avail = np.array([r["prediction_available"].lower() in ("true", "1") for r in rows])
    t_pred0 = t[avail][0]

    fig, axes = plt.subplots(1 + len(ZONES), 1, figsize=(15, 2.2 * (1 + len(ZONES))), sharex=True,
                             gridspec_kw={"height_ratios": [1.1] + [1] * len(ZONES)})
    ax = axes[0]
    for i, j in enumerate(JETS):
        ax.plot(t, [float(r[f"actual_massflow_{j}"]) for r in rows], color=f"C{i}", lw=1.2, label=f"J{j}")
    ax.set_ylabel("实际质量流量\n[kg/s]")
    ax.legend(ncol=6, fontsize=9, loc="lower center", bbox_to_anchor=(0.5, 1.0), frameon=False)
    ax.set_ylim(-0.1, 3.2)
    ax.grid(alpha=0.3)
    fig.suptitle("独立验证集：ΔFz 真值 vs 模型连续滚动预测", y=0.995)
    for ax, z, lab in zip(axes[1:], ZONES, LABELS):
        truth = np.array([float(r[f"delta_{z}_truth"]) for r in rows])
        roll = np.array([float(r[f"delta_{z}_rolling"]) if r[f"delta_{z}_rolling"] else np.nan for r in rows])
        roll[~avail] = np.nan
        ax.plot(t, truth, color=C_TRUTH, lw=0.8, label="真值")
        ax.plot(t, roll, color=C_PRED, lw=0.8, label="滚动预测")
        ax.axhline(0, color="gray", lw=0.5)
        ax.set_ylabel(f"Δ{lab} [N]")
        ax.grid(alpha=0.3)
    for ax in axes:
        ax.axvspan(t[0], t_pred0, color="#e0e0e0", alpha=0.6, lw=0)
    axes[1].legend(ncol=2, fontsize=9, loc="upper right")
    axes[-1].text(0.0, -0.32, f"灰色区：前 {t_pred0 - t[0]:.1f} s 用作模型历史窗口，没有预测值", transform=axes[-1].transAxes,
                  fontsize=9, color="#555555")
    axes[-1].set_xlabel("物理时间 [s]")
    fig.tight_layout()
    fig.savefig(out, dpi=150)
    plt.close(fig)


def _region_metrics(m, split):
    by = m["metrics"][split]["rolling"]["by_region"]
    noise = m["noise_floor_N"][split]
    return [(by[z]["rmse"], by[z]["nrmse"], by[z]["correlation"], noise[z]) for z in ZONES]


def fig6_table(m, out):
    vals = _region_metrics(m, "validation")
    header = ["区域", "滚动 RMSE [N]", "NRMSE*", "相关系数", "底噪 [N]", "RMSE / 底噪"]
    cells = [[lab, f"{r:.0f}", f"{n * 100:.1f}%", f"{c:.3f}", f"{s:.0f}", f"{r / s:.2f}"]
             for lab, (r, n, c, s) in zip(LABELS, vals)]
    fig, ax = plt.subplots(figsize=(10, 3.2))
    ax.axis("off")
    tb = ax.table(cellText=cells, colLabels=header, loc="center", cellLoc="center")
    tb.auto_set_font_size(False)
    tb.set_fontsize(11)
    tb.scale(1, 1.7)
    for (i, j), cell in tb.get_celld().items():
        cell.set_edgecolor("#dddddd")
        if i == 0:
            cell.set_facecolor("#f2f2f2")
            cell.set_text_props(weight="bold")
        elif j == 5 and float(cells[i - 1][5]) >= 1.15:
            cell.set_text_props(color=C_PRED, weight="bold")
    ax.set_title("验证集连续滚动预测指标（底噪 = 验证集各事件喷气前差值噪声中位数）", fontsize=12, pad=6)
    fig.text(0.5, 0.03, "* NRMSE = RMSE / 真值极差（max − min）；红色表示超出底噪 15% 以上", ha="center", fontsize=9,
             color="#555555")
    fig.savefig(out, dpi=180, bbox_inches="tight")
    plt.close(fig)


def fig7_error_vs_noise(m, out):
    tr, va = _region_metrics(m, "training"), _region_metrics(m, "validation")
    x = np.arange(len(ZONES))
    fig, (a1, a2) = plt.subplots(1, 2, figsize=(15, 5.2))

    w = 0.2
    bars = [(-1.5, [v[3] for v in tr], C_NOISE, "训练集 底噪", None),
            (-0.5, [v[0] for v in tr], C_TRAIN, "训练集 滚动 RMSE", None),
            (0.5, [v[3] for v in va], C_NOISE, "验证集 底噪", "//"),
            (1.5, [v[0] for v in va], C_VAL, "验证集 滚动 RMSE", None)]
    for off, ys, color, lab, hatch in bars:
        b = a1.bar(x + off * w, ys, w, color=color, label=lab, hatch=hatch, edgecolor="white" if not hatch else "#8a857c")
        if lab == "验证集 滚动 RMSE":
            a1.bar_label(b, fmt="%.0f", fontsize=8, padding=2)
    a1.set_xticks(x, LABELS)
    a1.set_ylabel("N")
    a1.legend(fontsize=9, frameon=False)
    a1.set_title("滚动预测误差与残差底噪（各数据集用各自的底噪）", loc="left")
    a1.grid(axis="y", alpha=0.3)
    a1.spines[["top", "right"]].set_visible(False)

    rt = [v[0] / v[3] for v in tr]
    rv = [v[0] / v[3] for v in va]
    a2.bar(x - 0.18, rt, 0.36, color=C_TRAIN, label="训练集")
    b = a2.bar(x + 0.18, rv, 0.36, color=C_VAL, label="验证集")
    a2.bar_label(b, fmt="%.2f", fontsize=8, padding=2)
    a2.axhline(1.0, color="#555555", lw=1, ls="--", label="底噪（确定性模型的误差下限）")
    a2.set_ylim(0.8, max(rv + rt) * 1.08)
    a2.set_xticks(x, LABELS)
    a2.set_ylabel("滚动 RMSE / 底噪")
    a2.legend(fontsize=9, frameon=False, loc="upper left")
    a2.set_title("验证集误差相对底噪：S1、S3L 高出 2%～7%，S2 与 S3R 高出 17%～24%", loc="left")
    a2.grid(axis="y", alpha=0.3)
    a2.spines[["top", "right"]].set_visible(False)
    fig.tight_layout()
    fig.savefig(out, dpi=150)
    plt.close(fig)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--metrics", default="artifacts/B04_real_ROM_metrics.json")
    ap.add_argument("--val-pred", default="artifacts/B04_real_ROM_validation_predictions.csv")
    ap.add_argument("--train-case", default="runs/b52_v2/training")
    ap.add_argument("--reference", default="runs/cifu0_nojet_11s")
    ap.add_argument("--tmin", type=float, default=0.5)
    ap.add_argument("--out-dir", default="artifacts/B04_figures/report")
    a = ap.parse_args()
    out = Path(a.out_dir)
    out.mkdir(parents=True, exist_ok=True)
    m = json.loads(Path(a.metrics).read_text())
    fig2_raw_vs_delta(a.train_case, a.reference, a.tmin, out / "图2_训练集做差前后对比.png")
    fig5_validation(a.val_pred, out / "图5_验证集真值vs滚动预测.png")
    fig6_table(m, out / "图6_验证集滚动预测指标表.png")
    fig7_error_vs_noise(m, out / "图7_误差与底噪对比.png")
    for p in sorted(out.glob("图*.png")):
        print(p)


main()
