"""train_gen_data：读 manifest 初始瞬态裁剪契约，生成可训练的连续时序表。"""

from .builder import (
    SUMMARY_FIELDS,
    TRAINING_TABLE_FIELDS,
    build_training_tables,
)

__all__ = [
    "SUMMARY_FIELDS",
    "TRAINING_TABLE_FIELDS",
    "build_training_tables",
]
