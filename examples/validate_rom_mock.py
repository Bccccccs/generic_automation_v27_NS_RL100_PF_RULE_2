"""Validate the B06 ARX ROM on an explicit, separate mock dataset.

Default run:

    python3 examples/validate_rom_mock.py

The command loads ``runs/rom_mock_demo/model/arx_model.json`` and evaluates all
cases listed by ``runs/arx_validate/index.csv``.  It never refits the model.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from flow_control.cli.validate_rom import main


if __name__ == "__main__":
    raise SystemExit(
        main(
            [
                "--model",
                "runs/rom_mock_demo/model/arx_model.json",
                "--dataset-dir",
                "runs/arx_validate",
                "--out",
                "runs/rom_mock_demo",
            ]
            + sys.argv[1:]
        )
    )
