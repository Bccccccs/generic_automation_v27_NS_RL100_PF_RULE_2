"""STAR-CCM+ export ingestion utilities."""

from .case_data_loader import ingest_star_export, ingest_star_product_dir, load_case
from .ccm_package import package_ccm_run_case
from .star_export_reader import (
    discover_star_export_csvs,
    read_star_export_bundle,
    read_star_export_csv,
)

__all__ = [
    "ingest_star_export",
    "ingest_star_product_dir",
    "load_case",
    "package_ccm_run_case",
    "run_star_ingest_pipeline",
    "discover_star_export_csvs",
    "read_star_export_bundle",
    "read_star_export_csv",
]


def __getattr__(name: str):
    if name == "run_star_ingest_pipeline":
        from .pipeline import run_star_ingest_pipeline

        return run_star_ingest_pipeline
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
