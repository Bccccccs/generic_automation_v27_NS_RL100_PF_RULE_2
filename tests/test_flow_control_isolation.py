import ast
import importlib
import sys
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
ISOLATED_ROOTS = (PROJECT_ROOT / "flow_control", PROJECT_ROOT / "starccm" / "control")


def _project_imports(path: Path) -> list[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    imports: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imports.extend(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
            imports.append(node.module)
    return imports


def test_flow_control_runtime_does_not_import_generic_automation():
    offenders: list[str] = []
    for root in ISOLATED_ROOTS:
        for path in sorted(root.rglob("*.py")):
            for import_name in _project_imports(path):
                if import_name == "generic_automation" or import_name.startswith("generic_automation."):
                    offenders.append(f"{path.relative_to(PROJECT_ROOT)} imports {import_name}")

    assert offenders == []


def test_importing_flow_control_does_not_load_solver_optimization_modules():
    for module_name in list(sys.modules):
        if module_name == "generic_automation" or module_name.startswith("generic_automation."):
            sys.modules.pop(module_name)

    importlib.import_module("flow_control.data_schema")
    importlib.import_module("flow_control.workflow.schedule_generator")
    importlib.import_module("flow_control.workflow.schedule_validator")
    importlib.import_module("flow_control.mock")
    importlib.import_module("starccm.control")

    loaded = [
        module_name
        for module_name in sys.modules
        if module_name == "generic_automation" or module_name.startswith("generic_automation.")
    ]
    assert loaded == []
