# 数据检查错误样例

这个目录统一存放数据质量检查的错误样例。它把之前版本和当前版本的样例放在同一个入口下，但仍按检查逻辑分组，方便回归测试和人工核对。

## 分组

- `legacy_quality_checker/`：早期基础 CSV 质量检查样例，覆盖缺文件、缺列、NaN、时间非单调、基础喷气质量流量一致性等问题。
- `ccm_physics/`：当前 `ccm` 检查模式样例，覆盖 B03/B33 标准算例目录契约，以及 B04 真实物理接口检查分类。

## 如何运行

使用交互式检查脚本。先选择算例目录，再选择检查模式：

```bash
.venv/bin/python examples/run_ccm_ingest_step2_check.py
```

检查 `ccm_physics` 下的样例时选择 `ccm`。检查 `legacy_quality_checker` 下的基础样例时通常选择 `mock`；如果想观察更严格的 ccm 逻辑如何分类旧样例，也可以手动选择 `ccm`。

## 索引

完整清单见 `error_case_index.csv`。表里的英文分类名是程序使用的稳定枚举，中文含义如下：

- `format_errors`：格式错误。
- `name_or_coordinate_errors`：名称或坐标错误。
- `massflow_errors`：质量流量错误。
- `force_accounting_errors`：力核算错误。
- `numerical_instability_warnings`：数值不稳定警告。
- `physical_questions_for_haokun`：需要浩坤判断的物理问题。

| 分组 | 样例 | 目标分类 | 检查模式 |
| --- | --- | --- | --- |
| `legacy_quality_checker` | `error_case_jet_massflow_mismatch` | `massflow_errors` | `mock/legacy` |
| `legacy_quality_checker` | `error_case_missing_actual_massflow` | `massflow_errors` | `mock/legacy` |
| `legacy_quality_checker` | `error_case_missing_required_column` | `format_errors` | `mock/legacy` |
| `legacy_quality_checker` | `error_case_missing_required_file` | `format_errors` | `mock/legacy` |
| `legacy_quality_checker` | `error_case_nan_value` | `format_errors` | `mock/legacy` |
| `legacy_quality_checker` | `error_case_no_jet_reaction_nonzero` | `massflow_errors` | `mock/legacy` |
| `legacy_quality_checker` | `error_case_non_monotonic_time` | `format_errors` | `mock/legacy` |
| `ccm_physics` | `E01_format_time_nonmonotonic` | `format_errors` | `ccm` |
| `ccm_physics` | `E02_name_coordinate_bad_lift_direction` | `name_or_coordinate_errors` | `ccm` |
| `ccm_physics` | `E03_massflow_off_jet_actual_leak` | `massflow_errors` | `ccm` |
| `ccm_physics` | `E04_force_accounting_missing_vehicle_force` | `force_accounting_errors` | `ccm` |
| `ccm_physics` | `E05_numerical_instability_force_spike` | `numerical_instability_warnings` | `ccm` |
| `ccm_physics` | `E06_physical_question_unconfirmed_direction` | `physical_questions_for_haokun` | `ccm` |
