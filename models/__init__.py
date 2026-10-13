"""Backward-compatible reduced-order model imports.

New code should import from ``flow_control.rom``.
"""

from .arx_model import ARXModel

__all__ = ["ARXModel"]
