#!/usr/bin/env python
"""导出并分析 STAR-CCM+ 模板的喷口几何、六区力 report 组成和来流设置。

要回答的问题：

1. 喷口 J01..J24 的真实位置（面心、包围盒），以及它们与 S1L..S3R 的对应关系；
2. S1L..S3R / fz / Fz_Total / Jet_Reaction_Z 各 report 积分了哪些边界；
3. 来流方向、速度和流体密度，S1→S3 沿来流的前后顺序；
4. 喷口面积与动作表质量流量（1.43 / 2.86 kg/s）对应的出口速度是否合理。

集群上（需要 starccm+，几分钟；不求解、不保存 .sim）：
    python scripts/inspect_ccm_geometry.py run --sim cifu0.sim --out-dir runs/_geometry \\
        --starccm "$STARCCM_BIN" --np 8

拷回 runs/_geometry/ccm_geometry_inspection.yaml 后在本地分析：
    python scripts/inspect_ccm_geometry.py analyze runs/_geometry/ccm_geometry_inspection.yaml
"""

from __future__ import annotations

import argparse
import math
import re
import subprocess
import sys
from pathlib import Path
from typing import Any

import yaml

INSPECTION_FILE = "ccm_geometry_inspection.yaml"
MASSFLOW_LEVELS_KG_S = (1.43, 2.86)

# 宏只读模板：创建的临时 report 和用户场函数在结束时删除，不保存 .sim。
# 版本间可能不一致的接口一律走反射，任何单项失败只记录 error，不中断整体导出。
MACRO = r"""import star.common.*;
import star.base.neo.*;
import star.base.report.*;
import java.io.*;
import java.lang.reflect.*;
import java.util.*;

public class InspectCcmGeometry extends StarMacro {
    static final String OUT_PATH = "__OUT_PATH__";
    Simulation sim;
    PrintWriter w;
    ArrayList<Report> temporary = new ArrayList<Report>();
    Report areaReport;
    Report[] centroid = new Report[3];
    Report[] lower = new Report[3];
    Report[] upper = new Report[3];
    UserFieldFunction one;

    public void execute() {
        sim = getActiveSimulation();
        try {
            w = new PrintWriter(new OutputStreamWriter(new FileOutputStream(OUT_PATH), "UTF-8"));
            w.println("inspection_status: ok");
            w.println("sim_name: " + q(sim.getPresentationName()));
            prepareReports();
            section("solver", new Runnable() { public void run() { dumpSolver(); } });
            section("continua", new Runnable() { public void run() { dumpContinua(); } });
            section("regions", new Runnable() { public void run() { dumpRegions(); } });
            section("reports", new Runnable() { public void run() { dumpReports(); } });
            section("monitors", new Runnable() { public void run() { dumpMonitors(); } });
            w.close();
            sim.println("[inspect-geometry] wrote " + OUT_PATH);
        } catch (Exception e) {
            throw new RuntimeException("geometry inspection failed: " + e);
        } finally {
            for (Report r : temporary) call(sim.getReportManager(), "remove", r);
            if (one != null) call(sim.getFieldFunctionManager(), "remove", one);
        }
    }

    private void section(String name, Runnable body) {
        try {
            body.run();
        } catch (Exception e) {
            w.println(name + "_error: " + q(String.valueOf(e)));
        }
    }

    private void prepareReports() {
        one = sim.getFieldFunctionManager().createFieldFunction();
        one.setFunctionName("fc_inspect_one");
        one.setDefinition("1");
        areaReport = makeReport("star.base.report.SurfaceIntegralReport", one);
        Object position = sim.getFieldFunctionManager().getFunction("Position");
        for (int i = 0; i < 3; i++) {
            Object component = call(position, "getComponentFunction", Integer.valueOf(i));
            if (component == null || component == FAILED) {
                UserFieldFunction axis = sim.getFieldFunctionManager().createFieldFunction();
                axis.setFunctionName("fc_inspect_position_" + i);
                axis.setDefinition("$${Position}[" + i + "]");
                component = axis;
            }
            centroid[i] = makeReport("star.base.report.AreaAverageReport", component);
            lower[i] = makeReport("star.base.report.MinReport", component);
            upper[i] = makeReport("star.base.report.MaxReport", component);
        }
    }

    @SuppressWarnings({"unchecked", "rawtypes"})
    private Report makeReport(String className, Object field) {
        try {
            Class cls = Class.forName(className);
            Report report = (Report) sim.getReportManager().createReport(cls);
            call(report, "setFieldFunction", field);
            temporary.add(report);
            return report;
        } catch (Exception e) {
            throw new RuntimeException("cannot create " + className + ": " + e);
        }
    }

    private double value(Report report) {
        Object raw = call(report, "getValue");
        if (!(raw instanceof Number)) raw = call(report, "getReportMonitorValue");
        return raw instanceof Number ? ((Number) raw).doubleValue() : Double.NaN;
    }

    // ------------------------------------------------------------ solver / physics

    private void dumpSolver() {
        w.println("solver:");
        for (Object solver : sim.getSolverManager().getObjects()) {
            w.println("  - name: " + q(name(solver)));
            w.println("    class: " + q(solver.getClass().getSimpleName()));
            Object step = call(solver, "getTimeStep");
            if (step != null) w.println("    time_step: " + q(quantityText(step)));
        }
    }

    private void dumpContinua() {
        w.println("continua:");
        for (Object continuum : sim.getContinuumManager().getObjects()) {
            w.println("  - name: " + q(name(continuum)));
            w.println("    class: " + q(continuum.getClass().getSimpleName()));
            Object models = call(continuum, "getModelManager");
            w.println("    models:");
            for (Object model : objects(models)) {
                w.println("      - name: " + q(name(model)));
                w.println("        class: " + q(model.getClass().getSimpleName()));
                Object material = call(model, "getMaterial");
                if (material == null) continue;
                w.println("        material: " + q(name(material)));
                w.println("        material_properties:");
                for (Object property : objects(call(material, "getMaterialProperties"))) {
                    w.println("          - name: " + q(name(property)));
                    w.println("            value: " + q(propertyText(property)));
                }
            }
            dumpValueManager("reference_values", call(continuum, "getReferenceValues"), "    ");
            dumpValueManager("initial_conditions", call(continuum, "getInitialConditions"), "    ");
        }
    }

    // ------------------------------------------------------------ regions / boundaries

    private void dumpRegions() {
        w.println("regions:");
        for (Object regionObj : sim.getRegionManager().getObjects()) {
            if (!(regionObj instanceof Region)) continue;
            Region region = (Region) regionObj;
            w.println("  - name: " + q(region.getPresentationName()));
            w.println("    continuum: " + q(name(call(region, "getPhysicsContinuum"))));
            geometry(region, "    ", false);
            w.println("    boundaries:");
            for (Object boundaryObj : region.getBoundaryManager().getObjects()) {
                if (!(boundaryObj instanceof Boundary)) continue;
                Boundary b = (Boundary) boundaryObj;
                w.println("      - name: " + q(b.getPresentationName()));
                w.println("        type: " + q(name(call(b, "getBoundaryType"))));
                geometry(b, "        ", true);
                dumpValueManager("values", call(b, "getValues"), "        ");
            }
        }
    }

    private void geometry(Object part, String indent, boolean surface) {
        try {
            if (surface) {
                setParts(areaReport, part);
                w.println(indent + "area_m2: " + num(value(areaReport)));
                double[] c = new double[3];
                for (int i = 0; i < 3; i++) { setParts(centroid[i], part); c[i] = value(centroid[i]); }
                w.println(indent + "centroid_m: [" + num(c[0]) + ", " + num(c[1]) + ", " + num(c[2]) + "]");
            }
            double[] lo = new double[3];
            double[] hi = new double[3];
            for (int i = 0; i < 3; i++) {
                setParts(lower[i], part); lo[i] = value(lower[i]);
                setParts(upper[i], part); hi[i] = value(upper[i]);
            }
            w.println(indent + "bbox_min_m: [" + num(lo[0]) + ", " + num(lo[1]) + ", " + num(lo[2]) + "]");
            w.println(indent + "bbox_max_m: [" + num(hi[0]) + ", " + num(hi[1]) + ", " + num(hi[2]) + "]");
        } catch (Exception e) {
            w.println(indent + "geometry_error: " + q(String.valueOf(e)));
        }
    }

    private void setParts(Object report, Object part) {
        Object parts = call(report, "getParts");
        call(parts, "setQuery", (Object) null);
        ArrayList<Object> list = new ArrayList<Object>();
        list.add(part);
        if (call(parts, "setObjects", list) != FAILED) return;
        // 退回可变参数重载 setObjects(ClientServerObject...)
        for (Method m : parts.getClass().getMethods()) {
            Class<?>[] types = m.getParameterTypes();
            if (!m.getName().equals("setObjects") || types.length != 1 || !types[0].isArray()) continue;
            if (!types[0].getComponentType().isInstance(part)) continue;
            Object array = Array.newInstance(types[0].getComponentType(), 1);
            Array.set(array, 0, part);
            try { m.invoke(parts, array); return; } catch (Exception ignored) {}
        }
        throw new RuntimeException("cannot set report parts for " + name(part));
    }

    // ------------------------------------------------------------ reports / monitors

    private void dumpReports() {
        w.println("reports:");
        for (Object report : sim.getReportManager().getObjects()) {
            if (temporary.contains(report)) continue;
            w.println("  - name: " + q(name(report)));
            w.println("    class: " + q(report.getClass().getSimpleName()));
            Object field = call(report, "getFieldFunction");
            if (field != null && field != FAILED) w.println("    field_function: " + q(name(field)));
            for (String getter : new String[] {"getDirection", "getOrigin", "getAxisVector", "getReferencePressure", "getDefinition"}) {
                Object value = call(report, getter);
                if (value != null && value != FAILED) w.println("    " + getter + ": " + q(quantityText(value)));
            }
            Object parts = call(report, "getParts");
            if (parts != null && parts != FAILED) {
                w.println("    parts:");
                for (Object part : objects(parts)) w.println("      - " + q(name(part)));
            }
            Object value = call(report, "getReportMonitorValue");
            if (value instanceof Number) w.println("    current_value: " + num(((Number) value).doubleValue()));
        }
    }

    private void dumpMonitors() {
        w.println("monitors:");
        for (Object monitor : sim.getMonitorManager().getObjects()) {
            w.println("  - name: " + q(name(monitor)));
            w.println("    class: " + q(monitor.getClass().getSimpleName()));
            Object report = call(monitor, "getReport");
            if (report != null && report != FAILED) w.println("    report: " + q(name(report)));
        }
    }

    // ------------------------------------------------------------ values via reflection

    private void dumpValueManager(String label, Object manager, String indent) {
        if (manager == null || manager == FAILED) return;
        w.println(indent + label + ":");
        for (Object value : objects(manager)) {
            w.println(indent + "  - name: " + q(name(value)));
            w.println(indent + "    class: " + q(value.getClass().getSimpleName()));
            String text = profileText(value);
            if (!text.isEmpty()) w.println(indent + "    value: " + q(text));
        }
    }

    private String profileText(Object profile) {
        for (String methodClass : new String[] {
                "star.common.ConstantScalarProfileMethod", "star.common.ConstantVectorProfileMethod"}) {
            try {
                Object method = call(profile, "getMethod", Class.forName(methodClass));
                if (method == null || method == FAILED) continue;
                return quantityText(call(method, "getQuantity"));
            } catch (Exception ignored) {}
        }
        Object method = call(profile, "getActiveMethod");
        if (method != null && method != FAILED) {
            Object quantity = call(method, "getQuantity");
            if (quantity != null && quantity != FAILED) return quantityText(quantity);
            return "method=" + method.getClass().getSimpleName();
        }
        Object quantity = call(profile, "getQuantity");
        return quantity != null && quantity != FAILED ? quantityText(quantity) : "";
    }

    private String propertyText(Object property) {
        try {
            Object method = call(property, "getMethod", Class.forName("star.material.ConstantMaterialPropertyMethod"));
            if (method != null && method != FAILED) return quantityText(call(method, "getQuantity"));
        } catch (Exception ignored) {}
        Object method = call(property, "getActiveMethod");
        return method == null || method == FAILED ? "" : "method=" + method.getClass().getSimpleName();
    }

    private String quantityText(Object quantity) {
        if (quantity == null || quantity == FAILED) return "";
        StringBuilder text = new StringBuilder();
        Object definition = call(quantity, "getDefinition");
        if (definition != null && definition != FAILED) text.append(definition);
        for (String getter : new String[] {"getRawValue", "getSIValue", "getInternalValue", "getValue", "getRawVector"}) {
            Object raw = call(quantity, getter);
            if (raw == null || raw == FAILED || raw == quantity) continue;
            String rendered = raw.getClass().isArray() ? arrayText(raw) : String.valueOf(raw);
            text.append(text.length() > 0 ? " | " : "").append(getter).append("=").append(rendered);
            break;
        }
        Object units = call(quantity, "getUnits");
        if (units != null && units != FAILED) text.append(" [").append(name(units)).append("]");
        return text.length() > 0 ? text.toString() : String.valueOf(quantity);
    }

    private String arrayText(Object array) {
        StringBuilder text = new StringBuilder("[");
        for (int i = 0; i < Array.getLength(array); i++) {
            if (i > 0) text.append(", ");
            text.append(Array.get(array, i));
        }
        return text.append("]").toString();
    }

    static final Object FAILED = new Object();

    private Object call(Object target, String method, Object... args) {
        if (target == null || target == FAILED) return FAILED;
        for (Method m : target.getClass().getMethods()) {
            if (!m.getName().equals(method) || m.getParameterTypes().length != args.length) continue;
            try {
                return m.invoke(target, args);
            } catch (IllegalArgumentException mismatch) {
                continue;
            } catch (Exception e) {
                return FAILED;
            }
        }
        return FAILED;
    }

    private List<Object> objects(Object manager) {
        ArrayList<Object> result = new ArrayList<Object>();
        Object raw = call(manager, "getObjects");
        if (raw instanceof Collection) result.addAll((Collection<?>) raw);
        else if (raw != null && raw != FAILED && raw.getClass().isArray()) {
            for (int i = 0; i < Array.getLength(raw); i++) result.add(Array.get(raw, i));
        }
        return result;
    }

    private String name(Object obj) {
        if (obj == null || obj == FAILED) return "";
        Object raw = call(obj, "getPresentationName");
        return raw == null || raw == FAILED ? String.valueOf(obj) : String.valueOf(raw);
    }

    private String num(double value) {
        return Double.isNaN(value) || Double.isInfinite(value) ? "null" : String.valueOf(value);
    }

    private String q(String s) {
        if (s == null) return "\"\"";
        return "\"" + s.replace("\\", "\\\\").replace("\"", "\\\"").replace("\n", " ") + "\"";
    }
}
"""


# ---------------------------------------------------------------- 集群：运行 STAR


def run_inspection(args: argparse.Namespace) -> Path:
    sim = args.sim.expanduser().resolve()
    if not sim.is_file():
        raise SystemExit(f"sim file not found: {sim}")
    out_dir = args.out_dir.resolve()
    out_dir.mkdir(parents=True, exist_ok=True)
    result = out_dir / INSPECTION_FILE
    log = out_dir / "inspect_ccm_geometry.log"
    macro = out_dir / "InspectCcmGeometry.java"
    result.unlink(missing_ok=True)
    macro.write_text(MACRO.replace("__OUT_PATH__", result.as_posix()), encoding="utf-8")
    command = [args.starccm]
    if args.np > 1:
        command += ["-np", str(args.np)]
    command += list(args.star_arg or [])
    command += ["-batch", str(macro), str(sim)]
    print("[inspect-geometry] " + " ".join(command), file=sys.stderr)
    with log.open("w", encoding="utf-8") as handle:
        code = subprocess.call(command, stdout=handle, stderr=subprocess.STDOUT, cwd=out_dir)
    if code != 0 or not result.is_file():
        raise SystemExit(f"STAR-CCM+ inspection failed (rc={code}); see {log}")
    print(f"[inspect-geometry] wrote {result}")
    return result


# ---------------------------------------------------------------- 本地：分析


def _boundaries(data: dict[str, Any]) -> dict[str, dict[str, Any]]:
    output: dict[str, dict[str, Any]] = {}
    for region in data.get("regions") or []:
        for boundary in region.get("boundaries") or []:
            output[str(boundary["name"])] = boundary
    return output


def _short(name: str) -> str:
    """STAR 部件名常带 'Region: ' 或 'xxx.' 前缀，比较时只取最后一段。"""

    return re.split(r"[:.]\s*", str(name))[-1].strip()


def _density(data: dict[str, Any]) -> float | None:
    for continuum in data.get("continua") or []:
        for model in continuum.get("models") or []:
            for prop in model.get("material_properties") or []:
                if "密度" in str(prop.get("name")) or "density" in str(prop.get("name")).lower():
                    match = re.search(r"[-+]?\d+(?:\.\d+)?(?:[eE][-+]?\d+)?", str(prop.get("value")))
                    if match:
                        return float(match.group())
    return None


def _fmt_xyz(values: Any) -> str:
    if not isinstance(values, list):
        return "—"
    return "(" + ", ".join("—" if value is None else f"{float(value):.4g}" for value in values) + ")"


def analyze(path: Path) -> str:
    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    boundaries = _boundaries(data)
    by_short = {_short(name): boundary for name, boundary in boundaries.items()}
    density = _density(data)
    lines = [f"# 模板几何检查：{data.get('sim_name', path.name)}", ""]
    for key in ("solver_error", "continua_error", "regions_error", "reports_error", "monitors_error"):
        if data.get(key):
            lines.append(f"- 导出错误 {key}: {data[key]}")

    lines += ["## 物理设置", ""]
    for continuum in data.get("continua") or []:
        lines.append(f"- 连续体 `{continuum.get('name')}`：模型 " + "、".join(str(m.get("name")) for m in continuum.get("models") or []))
        for model in continuum.get("models") or []:
            for prop in model.get("material_properties") or []:
                lines.append(f"  - {model.get('material')} / {prop.get('name')}: {prop.get('value')}")
    for solver in data.get("solver") or []:
        if solver.get("time_step"):
            lines.append(f"- 时间步：{solver['time_step']}")
    lines.append(f"- 解析出的密度：{density if density is not None else '未解析到，需人工查看'} kg/m³")
    lines += ["", "## 非壁面边界（来流方向判断）", ""]
    for name, boundary in boundaries.items():
        if "壁面" in str(boundary.get("type")) or "wall" in str(boundary.get("type")).lower():
            continue
        if re.fullmatch(r"J\d{2}", _short(name)):
            continue
        values = "; ".join(f"{v.get('name')}={v.get('value')}" for v in boundary.get("values") or [] if v.get("value"))
        lines.append(
            f"- `{name}` [{boundary.get('type')}] 面心 {_fmt_xyz(boundary.get('centroid_m'))} "
            f"面积 {boundary.get('area_m2')}：{values}"
        )

    lines += ["", "## 喷口（J = 质量流量入口，JET = 周围壁面分区）", ""]
    header = "| 喷口 | 面心 x,y,z [m] | 面积 [m²] | JET 分区面心 | JET 面积 [m²] |"
    if density:
        header += " " + " | ".join(f"出口速度@{m} kg/s [m/s]" for m in MASSFLOW_LEVELS_KG_S) + " |"
    lines += [header, "|" + "---|" * (header.count("|") - 1)]
    for index in range(1, 25):
        jet = by_short.get(f"J{index:02d}")
        zone = by_short.get(f"JET{index:02d}")
        if jet is None:
            continue
        area = jet.get("area_m2")
        row = (
            f"| J{index:02d} | {_fmt_xyz(jet.get('centroid_m'))} | {area} | "
            f"{_fmt_xyz((zone or {}).get('centroid_m'))} | {(zone or {}).get('area_m2')} |"
        )
        if density:
            row += " " + " | ".join(
                f"{m / (density * float(area)):.3g}" if area else "—" for m in MASSFLOW_LEVELS_KG_S
            ) + " |"
        lines.append(row)

    lines += ["", "## 力 report 组成", ""]
    monitors = {str(m.get("report")): str(m.get("name")) for m in data.get("monitors") or [] if m.get("report")}
    for report in data.get("reports") or []:
        name = str(report.get("name"))
        parts = [str(part) for part in report.get("parts") or []]
        interesting = re.search(r"S[123][LR]|fz|Fz|Jet|Drag|Pitch|Roll|Moment|升力|阻力", name)
        if not interesting:
            continue
        jets = sorted({_short(p) for p in parts if re.fullmatch(r"J(ET)?\d{2}", _short(p))})
        area = sum(float(by_short.get(_short(p), {}).get("area_m2") or 0.0) for p in parts)
        cx = [
            by_short[_short(p)]["centroid_m"]
            for p in parts
            if _short(p) in by_short and isinstance(by_short[_short(p)].get("centroid_m"), list)
        ]
        weights = [float(by_short[_short(p)].get("area_m2") or 0.0) for p in parts if _short(p) in by_short and isinstance(by_short[_short(p)].get("centroid_m"), list)]
        centre = (
            [sum(c[i] * wgt for c, wgt in zip(cx, weights)) / sum(weights) for i in range(3)]
            if cx and sum(weights) > 0
            else None
        )
        lines.append(
            f"- **{name}** ({report.get('class')}, 方向 {report.get('getDirection', '—')}, monitor `{monitors.get(name, '—')}`)："
            f"{len(parts)} 个部件，总面积 {area:.4g} m²，面积加权面心 {_fmt_xyz(centre)}"
        )
        if jets:
            lines.append(f"  - 含喷口相关面：{', '.join(jets)}")
        other = [p for p in parts if not re.fullmatch(r"J(ET)?\d{2}", _short(p))]
        if other:
            lines.append(f"  - 其他部件：{', '.join(other[:12])}{' …' if len(other) > 12 else ''}")
    lines.append("")
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="command", required=True)
    run = sub.add_parser("run", help="在有 starccm+ 的机器上导出模板信息")
    run.add_argument("--sim", type=Path, required=True)
    run.add_argument("--out-dir", type=Path, default=Path("runs/_geometry"))
    run.add_argument("--starccm", default="starccm+")
    run.add_argument("--np", type=int, default=1)
    run.add_argument("--star-arg", action="append", help="额外 starccm+ 参数，可重复")
    report = sub.add_parser("analyze", help="分析导出的 YAML，输出 Markdown")
    report.add_argument("inspection", type=Path)
    report.add_argument("--output", type=Path, help="写入 Markdown 文件（默认打印）")
    args = parser.parse_args()
    if args.command == "run":
        run_inspection(args)
        return 0
    text = analyze(args.inspection)
    if args.output:
        args.output.write_text(text, encoding="utf-8")
        print(f"wrote {args.output}")
    else:
        print(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
