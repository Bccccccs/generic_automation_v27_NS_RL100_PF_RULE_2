"""Example wrapper for training the minimal B06 ARX ROM on mock data.

Default run:

    python3 examples/train_rom_mock.py

The script uses a chronological split.  It never shuffles time points and the
validation forecast feeds back only previous predictions, not future measured
outputs.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from flow_control.cli.train_rom import main


if __name__ == "__main__":
    raise SystemExit(
        main(
            [
                "--case-dir",
                "runs/mock_full_prbs_demo",
                "--out",
                "runs/rom_mock_demo",
                "--single-jet-case-dir",
                "runs/mock_full_step_singlejet",
            ]
            + sys.argv[1:]
        )
    )
