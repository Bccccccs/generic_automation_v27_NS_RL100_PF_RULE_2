from __future__ import annotations

from pathlib import Path
from typing import Any

from adapter_base import Case
from starccm_adapter import StarCCMAdapter

_SUPPORTED_BACKEND = "starccm"


class SimulationAdapter:
    def __init__(self, backend: str = "starccm", check_interval: int = 500) -> None:
        backend_name = str(backend).strip().lower()
        if backend_name != _SUPPORTED_BACKEND:
            raise NotImplementedError(
                f"Unsupported backend {backend!r}. Only '{_SUPPORTED_BACKEND}' is available."
            )
        self.backend = backend_name
        self._adapter = StarCCMAdapter(check_interval=check_interval)

    def run(
        self,
        case: Case,
        case_dir: Path,
        run_context: dict[str, Any] | None = None,
    ) -> None:
        self._adapter.run(case, case_dir, run_context=run_context)
