"""Train the minimal B06 ARX ROM on an explicit mock training dataset.

Default run:

    python3 examples/train_rom_mock.py

Every case listed in ``runs/arx_test/index.csv`` is used for fitting.  This
script performs no validation and creates no validation metrics or plots; run
``examples/validate_rom_mock.py`` with a separate dataset for those outputs.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from flow_control.cli.train_rom import main


if __name__ == "__main__":
    raise SystemExit(
        main(
            [
                "--dataset-dir",
                "runs/arx_test",
                "--out",
                "runs/rom_mock_demo/model",
            ]
            + sys.argv[1:]
        )
    )
