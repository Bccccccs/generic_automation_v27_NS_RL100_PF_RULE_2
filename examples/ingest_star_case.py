#!/usr/bin/env python3
"""Compatibility wrapper for the STAR ingest one-step pipeline."""

from __future__ import annotations

import sys
from pathlib import Path

# Allow running from project root
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from flow_control.star_ingest.pipeline import main


if __name__ == "__main__":
    raise SystemExit(main())
