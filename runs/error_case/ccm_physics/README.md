# 真实 STAR 数据检查错误样例

这些样例用于回归测试 B04/ccm 数据检查。每个样例都故意触发一个主要检查分类，方便确认程序能够把不同类型的问题分开报告。

运行方式可以使用 `check_mode="ccm"`，也可以启动交互脚本并选择 ccm 模式：

```bash
.venv/bin/python examples/run_ccm_ingest_step2_check.py
```

| 样例 | 目标分类 |
| --- | --- |
| `E01_format_time_nonmonotonic` | `format_errors` |
| `E02_name_coordinate_bad_lift_direction` | `name_or_coordinate_errors` |
| `E03_massflow_off_jet_actual_leak` | `massflow_errors` |
| `E04_force_accounting_missing_vehicle_force` | `force_accounting_errors` |
| `E05_numerical_instability_force_spike` | `numerical_instability_warnings` |
| `E06_physical_question_unconfirmed_direction` | `physical_questions_for_haokun` |

每个样例都保留 `raw_star/`、`processed/timeseries.csv`、`actuation_schedule.csv`、`case_manifest.yaml`、本地映射文件和 `quality_report.json`。这些样例只用于验证检查程序能发现指定问题，不代表真实 CFD 结果。
