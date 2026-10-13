#!/usr/bin/env python
"""Read a STAR-CCM+ .sim template and report the manifest ``star:`` fields.

Fills the fields that case_manifest templates mark "待浩坤确认":
version, sim_file, sim_file_hash_sha256, geometry_version, mesh_version
(and checks region_names).

Online (needs starccm+ on this machine, e.g. a compute node):
    python scripts/inspect_ccm_template.py --sim cifunewest.sim --out-dir runs/_inspect
    python scripts/inspect_ccm_template.py --sim cifunewest.sim --out-dir runs/_inspect \
        --starccm /path/to/starccm+ --np 8 --podkey <key> \
        --manifest runs/real_star/G00_nojet_existing/case_manifest.yaml --write

Offline (reuse the log of an earlier run on the same .sim; no geometry_version):
    python scripts/inspect_ccm_template.py --sim cifunewest.sim --log traceback/starccm_flow_control.log

Offline, .sim not on this machine (files copied back from an online run):
    python scripts/inspect_ccm_template.py --sim /cluster/path/cifu0.sim --sha256 <hex> \
        --inspection runs/_inspect/ccm_template_inspection.yaml --log runs/_inspect/inspect_ccm_template.log

Without --write the manifest is only compared, never modified.
"""

from __future__ import annotations

import argparse
import hashlib
import subprocess
import sys
from pathlib import Path
from typing import Any

import yaml

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from flow_control.star_ingest.manifest_builder import (  # noqa: E402
    _attach_runtime_metadata,
    read_star_runtime_metadata,
)

PENDING = "待浩坤确认"
INSPECTION_FILE = "ccm_template_inspection.yaml"

MACRO = """import star.common.*;
import star.base.report.*;
import star.base.neo.*;
import java.io.*;
import java.util.*;

public class InspectCcmTemplate extends StarMacro {
    static final String OUT_PATH = "%(out_path)s";

    public void execute() {
        Simulation sim = getActiveSimulation();
        try {
            PrintWriter w = new PrintWriter(new OutputStreamWriter(new FileOutputStream(OUT_PATH), "UTF-8"));
            w.println("inspection_status: ok");
            w.println("regions:");
            for (Object regionObj : sim.getRegionManager().getObjects()) {
                if (!(regionObj instanceof Region)) continue;
                Region region = (Region) regionObj;
                w.println("  - name: " + q(region.getPresentationName()));
                w.println("    boundaries:");
                for (Object boundaryObj : region.getBoundaryManager().getObjects()) {
                    if (!(boundaryObj instanceof Boundary)) continue;
                    Boundary b = (Boundary) boundaryObj;
                    w.println("      - name: " + q(b.getPresentationName()));
                    w.println("        type: " + q(typeName(b)));
                    w.println("        area_m2: " + area(sim, b));
                }
            }
            w.println("parts:");
            try {
                for (Object partObj : sim.get(SimulationPartManager.class).getObjects()) {
                    w.println("  - class: " + q(partObj.getClass().getSimpleName()));
                    w.println("    name: " + q(((GeometryPart) partObj).getPresentationName()));
                }
            } catch (Exception e) {
                w.println("parts_error: " + q(String.valueOf(e.getMessage())));
            }
            w.close();
            sim.println("[inspect] wrote " + OUT_PATH);
        } catch (Exception e) {
            throw new RuntimeException("template inspection failed: " + e.getMessage());
        }
    }

    private String typeName(Boundary b) {
        try { return b.getBoundaryType().getPresentationName(); } catch (Exception e) { return "(unknown)"; }
    }

    // Built-in Area field function integrated over the boundary (same as the run macro).
    private String area(Simulation sim, Boundary b) {
        try {
            SurfaceIntegralReport r = sim.getReportManager().createReport(SurfaceIntegralReport.class);
            r.setFieldFunction(sim.getFieldFunctionManager().getFunction("Area"));
            r.getParts().setObjects(new NeoObjectVector(new Object[] {b}));
            double v = r.getValue();
            try { sim.getReportManager().remove(r); } catch (Exception ignored) {}
            return String.valueOf(v);
        } catch (Exception e) {
            return "null";
        }
    }

    private String q(String s) {
        return "\\"" + s.replace("\\\\", "\\\\\\\\").replace("\\"", "\\\\\\"") + "\\"";
    }
}
"""


def sha256_of(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def run_inspection(args: argparse.Namespace, sim: Path) -> tuple[Path, Path]:
    out_dir = args.out_dir.resolve()  # STAR runs with cwd=out_dir, so every path passed to it must be absolute
    out_dir.mkdir(parents=True, exist_ok=True)
    result_path = out_dir / INSPECTION_FILE
    log_path = out_dir / "inspect_ccm_template.log"
    macro_path = out_dir / "InspectCcmTemplate.java"
    result_path.unlink(missing_ok=True)
    macro_path.write_text(MACRO % {"out_path": result_path.resolve().as_posix()}, encoding="utf-8")

    command = [args.starccm]
    if args.np > 1:
        command += ["-np", str(args.np)]
    if args.podkey:
        command += ["-podkey", args.podkey]
    command += list(args.star_arg or [])
    command += ["-batch", str(macro_path), str(sim)]
    print("[inspect] " + " ".join(command), file=sys.stderr)
    with log_path.open("w", encoding="utf-8") as log:
        returncode = subprocess.call(command, stdout=log, stderr=subprocess.STDOUT, cwd=out_dir)
    if returncode != 0 or not result_path.is_file():
        raise SystemExit(f"STAR-CCM+ inspection failed (rc={returncode}); see {log_path}")
    return result_path, log_path


def geometry_fingerprint(inspection: dict[str, Any]) -> str:
    """Content hash of geometry parts + boundary names/types/areas (areas to 6 significant digits)."""
    lines: list[str] = []
    for part in inspection.get("parts") or []:
        lines.append(f"part|{part.get('class')}|{part.get('name')}")
    for region in inspection.get("regions") or []:
        for boundary in region.get("boundaries") or []:
            area = boundary.get("area_m2")
            area_text = "nan" if area is None else f"{float(area):.6g}"
            lines.append(f"boundary|{boundary.get('name')}|{boundary.get('type')}|{area_text}")
    digest = hashlib.sha256("\n".join(sorted(lines)).encode("utf-8")).hexdigest()
    return f"geom-{digest[:12]}"


def collect_star_fields(args: argparse.Namespace, sim: Path) -> tuple[dict[str, Any], list[str]]:
    notes: list[str] = []
    digest = args.sha256 or sha256_of(sim)
    inspection: dict[str, Any] | None = None
    if args.inspection:
        if not args.log:
            raise SystemExit("--inspection also needs --log (the log from the same run)")
        inspection = yaml.safe_load(args.inspection.read_text(encoding="utf-8")) or {}
        log_path = args.log
    elif args.log:
        log_path = args.log
    else:
        result_path, log_path = run_inspection(args, sim)
        inspection = yaml.safe_load(result_path.read_text(encoding="utf-8")) or {}

    data: dict[str, Any] = {"star": {"sim_file": str(sim), "sim_file_hash_sha256": digest}}
    metadata = read_star_runtime_metadata(log_path)
    if not metadata.get("runtime"):
        notes.append(f"no STAR version banner found in {log_path}")
    # Same code path as the run pipeline, so version / mesh_version strings are identical.
    _attach_runtime_metadata(data, metadata)
    star = data["star"]
    if "mesh_version" not in star:
        notes.append(f"no mesh connectivity lines in {log_path}; mesh_version not derived")

    if inspection is not None:
        names = [str(region["name"]) for region in inspection.get("regions") or []]
        star["region_names"] = names
        star["geometry_version"] = args.geometry_version or geometry_fingerprint(inspection)
        if inspection.get("parts_error"):
            notes.append(f"geometry parts unreadable: {inspection['parts_error']}")
        notes.append("geometry_version is a content fingerprint (parts + boundary names/types/areas), not a STAR-native version")
    else:
        mesh_regions = (star.get("mesh") or {}).get("regions") or []
        if mesh_regions:
            star["region_names"] = [str(region["name"]) for region in mesh_regions]
        if args.geometry_version:
            star["geometry_version"] = args.geometry_version
        else:
            notes.append("geometry_version needs the online mode (a .sim cannot be read without STAR-CCM+)")
    return star, notes


def update_manifest(manifest_path: Path, star: dict[str, Any], write: bool) -> None:
    manifest = yaml.safe_load(manifest_path.read_text(encoding="utf-8")) or {}
    section = manifest.setdefault("star", {})
    changed = False
    for key in ("version", "sim_file", "sim_file_hash_sha256", "geometry_version", "mesh_version"):
        if section.get(key) != PENDING:
            continue
        if key not in star:
            print(f"  {key}: still {PENDING} (not available)")
            continue
        section[key] = star[key]
        changed = True
        print(f"  {key}: {PENDING} -> {star[key]}")
    if "region_names" in star and section.get("region_names") != star["region_names"]:
        print(f"  [warn] region_names in manifest {section.get('region_names')} != template {star['region_names']} (left unchanged)")
    if write and changed:
        manifest_path.write_text(yaml.safe_dump(manifest, sort_keys=False, allow_unicode=True), encoding="utf-8")
        print(f"[inspect] updated {manifest_path}")
    elif changed:
        print("[inspect] dry run; add --write to update the manifest")
    else:
        print("[inspect] no pending fields to fill")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--sim", type=Path, required=True, help=".sim template path")
    parser.add_argument("--out-dir", type=Path, default=Path("runs/_ccm_template_inspection"), help="online mode: macro/log/result directory")
    parser.add_argument("--starccm", default="starccm+", help="STAR-CCM+ executable")
    parser.add_argument("--np", type=int, default=1, help="processes for the batch session")
    parser.add_argument("--podkey", default="", help="Power-on-demand key, if required")
    parser.add_argument("--star-arg", action="append", help="extra starccm+ option, repeatable (e.g. --star-arg=-licpath --star-arg=1999@host)")
    parser.add_argument("--log", type=Path, help="offline mode: parse an existing STAR log instead of launching STAR")
    parser.add_argument("--inspection", type=Path, help="offline mode: ccm_template_inspection.yaml from an earlier online run (with --log); gives geometry_version")
    parser.add_argument("--sha256", help="use this sha256 instead of hashing --sim (when the .sim is not on this machine)")
    parser.add_argument("--geometry-version", help="override the derived geometry_version")
    parser.add_argument("--manifest", type=Path, help="case_manifest.yaml whose pending fields should be filled")
    parser.add_argument("--write", action="store_true", help="write the manifest (default: dry run)")
    args = parser.parse_args()

    if args.sha256:
        sim = args.sim  # .sim lives elsewhere (cluster): record the path exactly as given
    else:
        sim = args.sim.expanduser().resolve()
        if not sim.is_file():
            raise SystemExit(f"sim file not found: {sim}")
    if sim.is_file() and sim.stat().st_size < 1024:
        print(f"[warn] {sim} is only {sim.stat().st_size} bytes; is it a placeholder instead of the real template?", file=sys.stderr)

    star, notes = collect_star_fields(args, sim)
    report = {key: star[key] for key in ("version", "sim_file", "sim_file_hash_sha256", "geometry_version", "mesh_version", "region_names") if key in star}
    print(yaml.safe_dump({"star": report}, sort_keys=False, allow_unicode=True))
    for note in notes:
        print(f"[note] {note}", file=sys.stderr)
    if args.manifest:
        update_manifest(args.manifest, star, args.write)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
