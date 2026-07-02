from pathlib import Path

from generic_automation.core.adapter_base import Case
from generic_automation.starccm.starccm_macro_builder import build_macro as legacy_build_macro
from starccm_runtime.starccm_macro_builder import build_macro


def _case() -> Case:
    return Case(
        case_name="runtime_import_smoke",
        inlet_velocity=30.0,
        inlet_temperature=300.0,
        outlet_pressure=0.0,
        base_mesh_size=0.1,
    )


def test_macro_builder_uses_top_level_runtime_template(tmp_path: Path):
    macro = build_macro(_case(), tmp_path, check_interval=10)

    assert "class AutoSetupMacro" in macro
    assert "runtime_import_smoke" in macro


def test_legacy_starccm_imports_forward_to_runtime(tmp_path: Path):
    macro = legacy_build_macro(_case(), tmp_path, check_interval=10)

    assert "class AutoSetupMacro" in macro
