"""Backward-compatible import for the ARX ROM model.

New code should import ``flow_control.rom.ARXModel``.
"""

from flow_control.rom.arx_model import ARXModel

__all__ = ["ARXModel"]
