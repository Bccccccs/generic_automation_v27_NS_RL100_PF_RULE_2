"""
STAR-CCM+ export ingestion utilities.

该模块是 star_ingest 子包的入口,集中导出所有对外公开的接口函数。
外部代码通过 from flow_control.star_ingest import xxx 即可使用这些功能。
"""

# === 导入子模块中需要对外暴露的函数 ===
from .case_data_loader import ingest_star_export, ingest_star_product_dir, load_case
from .b3_acceptance import validate_b3_case_set, write_b3_acceptance_report
from .ccm_package import package_ccm_run_case
from .final_contract import validate_final_contract_columns
from .star_export_reader import (
    discover_star_export_csvs,
    read_star_export_bundle,
    read_star_export_csv,
)

# __all__ 定义了 from star_ingest import * 时导出的符号列表
# 集中管理包的外部 API,方便调用方了解可用功能
__all__ = [
    "ingest_star_export",          # 入口函数:将 STAR 导出文件摄入为标准 Case
    "ingest_star_product_dir",     # 从 STAR 产品目录批量摄入
    "load_case",                   # 加载已摄取的标准 Case
    "package_ccm_run_case",        # 将 CCM 运行结果打包为标准 Case
    "run_star_ingest_pipeline",    # 一键运行完整摄入流水线(懒加载)
    "discover_star_export_csvs",   # 发现 STAR 导出的 CSV 文件
    "read_star_export_bundle",     # 批量读取一组 STAR CSV 文件
    "read_star_export_csv",        # 读取单个 STAR CSV 文件
    "validate_final_contract_columns",  # 0816 STAR 字段契约校验
    "validate_b3_case_set",       # week4 B3 三算例顺序验收
    "write_b3_acceptance_report", # 写入 B3 验收报告
]


def __getattr__(name: str):
    """
    模块级懒加载(即 Lazy Import)。
    当用户首次访问 run_star_ingest_pipeline 时才导入 pipeline 模块,
    避免包初始化时加载所有子模块,提升启动速度。
    """
    if name == "run_star_ingest_pipeline":
        from .pipeline import run_star_ingest_pipeline

        return run_star_ingest_pipeline
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
