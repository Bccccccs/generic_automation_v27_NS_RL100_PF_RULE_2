"""Generate one physical-time jet actuation schedule from a configured pattern."""

from __future__ import annotations

import argparse
from dataclasses import replace
from pathlib import Path
from typing import Any

from ..config import load_config_with_system_defaults
from ..excitation_patterns import ActuationConfig, generate_pattern_table, write_pattern_outputs

INPUT_DIRNAME = "input"


def resolve_input_dir(output_dir: str | Path) -> Path:
    """Return the fixed generated-input directory for one output root."""

    return Path(output_dir) / INPUT_DIRNAME


def generate_from_mapping(
    config_data: dict[str, Any],
    *,
    output_dir: str | Path,
) -> ActuationConfig:
    """Generate one configured pattern and write it to ``output_dir``.

    The pattern mode selects one of the six generators in
    :mod:`flow_control.excitation_patterns`.  Callers own directory policy;
    this module only writes the generated schedule and its companion files.
    """

    config = replace(
        ActuationConfig.from_mapping(config_data),
        output_dir=resolve_input_dir(output_dir),
    )
    table, extra, errors = generate_pattern_table(config)
    write_pattern_outputs(config, table, validation_errors=errors, extra=extra)
    return config


def generate_from_yaml(
    config_path: str | Path,
    *,
    output_dir: str | Path,
) -> ActuationConfig:
    """Load a pattern YAML and write its generated schedule to ``output_dir``."""

    return generate_from_mapping(
        load_config_with_system_defaults(config_path),
        output_dir=output_dir,
    )


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Generate a physical-time jet actuation schedule."
    )
    parser.add_argument("--config", required=True, help="Actuation YAML configuration.")
    parser.add_argument(
        "--output-dir",
        required=True,
        help="Output root; generated schedule files are written to <output-dir>/input/.",
    )
    args = parser.parse_args()

    config = generate_from_yaml(args.config, output_dir=args.output_dir)
    print(
        "generated actuation schedule: "
        f"mode={config.mode}, jets={config.n_jets}, output={config.output_dir}"
    )


if __name__ == "__main__":
    main()
