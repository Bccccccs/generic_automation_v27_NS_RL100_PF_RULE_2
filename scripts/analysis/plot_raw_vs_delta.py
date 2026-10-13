"""画训练/验证算例的原始物理量与相对无喷气基准的差值(ΔX = X_case - X_ref,同时刻相减)。"""
import argparse
import csv
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np

plt.rcParams["font.sans-serif"] = ["Heiti SC", "Arial Unicode MS", "DejaVu Sans"]
plt.rcParams["axes.unicode_minus"] = False

ZONES = ["Fz_S1L", "Fz_S1R", "Fz_S2L", "Fz_S2R", "Fz_S3L", "Fz_S3R"]
OTHERS = ["Fz_Total", "Drag_Total", "Pitch_Moment", "Roll_Moment"]
STD_KEYS = ["Fz_Total", "Drag_Total", "Pitch_Moment", "Roll_Moment", "Jet_Reaction_Z"]
DT = 1e-4


def load(path):
    with open(path, newline="") as f:
        rows = list(csv.DictReader(f))
    cols = ZONES + OTHERS + ["Jet_Reaction_Z"] + [f"star_actual_massflow_{i:02d}" for i in range(1, 25)]
    t = np.array([float(r["physical_time"]) for r in rows])
    data = {c: np.array([float(r[c]) for r in rows]) for c in cols}
    data["_step"] = np.rint(t / DT).astype(int)
    data["t"] = t
    return data


def plot_case(name, case, ref, tmin, out, keys, suffix):
    common, ia, ib = np.intersect1d(case["_step"], ref["_step"], return_indices=True)
    keep = common * DT >= tmin
    ia, ib, t = ia[keep], ib[keep], common[keep] * DT
    fig, axes = plt.subplots(len(keys), 1, figsize=(15, 2.6 * len(keys)), sharex=True)
    for ax, k in zip(np.atleast_1d(axes), keys):
        x, r = case[k][ia], ref[k][ib]
        ax.plot(t, x, color="C0", lw=0.7, label="有喷气")
        ax.plot(t, r, color="C1", lw=0.7, label="无喷气基准")
        ax.plot(t, x - r, color="C3", lw=0.8, label="差值 Δ=有喷气−无喷气")
        ax.axhline(0, color="gray", lw=0.5)
        ax.set_ylabel(k)
        ax.grid(alpha=0.3)
        ax.legend(ncol=3, fontsize=7, loc="upper right")
    np.atleast_1d(axes)[0].set_title(f"{name}:做差前后同一比例尺对比")
    np.atleast_1d(axes)[-1].set_xlabel("物理时间 [s]")
    fig.tight_layout()
    fig.savefig(out, dpi=130)
    plt.close(fig)
    print(out, f"{len(t)} 点, t={t[0]:.4f}~{t[-1]:.4f}")


def plot_case_std(name, case, ref, tmin, out, keys):
    """标准化：x_n = (x - mean_ref) / std_ref，Δ_n = (x - ref) / std_ref；统计量取参考算例在同一时间窗内的值。"""
    common, ia, ib = np.intersect1d(case["_step"], ref["_step"], return_indices=True)
    keep = common * DT >= tmin
    ia, ib, t = ia[keep], ib[keep], common[keep] * DT
    fig, axes = plt.subplots(len(keys), 1, figsize=(15, 2.6 * len(keys)), sharex=True)
    for ax, k in zip(np.atleast_1d(axes), keys):
        x, r = case[k][ia], ref[k][ib]
        mu, sd = r.mean(), r.std()
        ax.plot(t, (x - mu) / sd, color="C0", lw=0.7, label="有喷气")
        ax.plot(t, (r - mu) / sd, color="C1", lw=0.7, label="无喷气基准")
        ax.plot(t, (x - r) / sd, color="C3", lw=0.8, label="差值 Δ/σ_ref")
        ax.axhline(0, color="gray", lw=0.5)
        ax.set_ylabel(f"{k}\n(σ_ref={sd:.4g})")
        ax.grid(alpha=0.3)
        ax.legend(ncol=3, fontsize=7, loc="upper right")
    np.atleast_1d(axes)[0].set_title(f"{name}:标准化对比（(x−μ_ref)/σ_ref，Δ 按 σ_ref 缩放）")
    np.atleast_1d(axes)[-1].set_xlabel("物理时间 [s]")
    fig.tight_layout()
    fig.savefig(out, dpi=130)
    plt.close(fig)
    print(out, f"{len(t)} 点, t={t[0]:.4f}~{t[-1]:.4f}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--reference", default="runs/cifu0_nojet_11s")
    ap.add_argument("--cases", nargs="+", default=["训练集=runs/b52_v2/training", "验证集=runs/b52_v2/validation"])
    ap.add_argument("--tmin", type=float, default=0.5)
    ap.add_argument("--out-dir", default="artifacts/B04_figures")
    ap.add_argument("--only-std", action="store_true", help="只生成标准化图，不重画原有的 Fz/others 图")
    a = ap.parse_args()
    ref = load(Path(a.reference) / "processed/timeseries.csv")
    Path(a.out_dir).mkdir(parents=True, exist_ok=True)
    for spec in a.cases:
        name, d = spec.split("=")
        case = load(Path(d) / "processed/timeseries.csv")
        tag = Path(d).name
        if not a.only_std:
            plot_case(name, case, ref, a.tmin, Path(a.out_dir) / f"raw_vs_delta_{tag}_Fz.png", ZONES + ["Fz_Total"], "Fz")
            plot_case(name, case, ref, a.tmin, Path(a.out_dir) / f"raw_vs_delta_{tag}_others.png", OTHERS[1:], "others")
        plot_case_std(name, case, ref, a.tmin, Path(a.out_dir) / f"raw_vs_delta_{tag}_others_std.png", STD_KEYS)


main()
