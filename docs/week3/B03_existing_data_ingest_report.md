# B03 Existing STAR Data Ingest Report

本报告只整理两个已有算例 `out_put` 的 STAR 产物；已有 `timeseries.csv` 视为派生产物，不作为真实 STAR monitor 来源。
所有用于 `processed/timeseries.csv` 的 STAR CSV 均按 `physical_time` 外连接合并。指令 schedule 只提供 `JET_*` 和 `cmd_massflow_*`，不会生成或冒充 `actual_massflow_*`。

## 可重复执行入口

当前 B03 的 raw 到 real data case 整理入口是：

```bash
cd /Users/yanbochao/generic_automation_v27_NS_RL100_PF_RULE_2
.venv/bin/python examples/run_ccm_ingest_step1_timeseries.py
```

Step1 现在会列出可整理的 STAR `out_put` 目录，并将其整理/更新为：

```text
runs/real_star/<case_id>/
  raw_star/out_put/
  processed/timeseries.csv
  case_manifest.yaml
  actuation_schedule.csv
  quality_report.json
  figures/
  logs/
```

Step1 的关键规则：

- 原始 STAR CSV 复制到 `raw_star/out_put/`。
- 多个 STAR CSV 按 `physical_time` 外连接合并到 `processed/timeseries.csv`。
- `actuation_schedule.csv` 只提供 `JET_*` 和 `cmd_massflow_*` 指令列。
- 不从 `JET_* x cmd_massflow_*` 合成 `actual_massflow_*`；除非 STAR raw 输出本身存在实际质量流量列，否则它保持缺失。
- 原来跑出来的派生产物已移到 `runs/real_star/legacy/<case_id>/`，只用于前后对比，不作为质量检查 case。

整理后运行 Step2 做质量检查：

```bash
.venv/bin/python examples/run_ccm_ingest_step2_check.py
```

Step2 只列标准 case 根目录，会跳过 `runs/real_star/legacy/`。

## 当前逻辑整理结论

- B03 负责回答“已有 STAR 文件能整理到哪一步”：两个 case 都已经形成标准目录、保留 `raw_star/`、生成 `processed/timeseries.csv`、`case_manifest.yaml`、`actuation_schedule.csv`、`quality_report.json` 和基础图。
- 数据检查现在分为 `mock` 和 `ccm` 两个 profile；这两个真实 case 使用 `check_mode=ccm`。
- 基础格式检查通过不再等价于物理接口正确；B04 的方向、力平衡、喷气反作用力等检查仍需要单独解释。
- `G00_nojet_existing` 的 Step1 整理产物完整，Step2 基础质量检查可读入 `processed/timeseries.csv` 并通过必需列检查；但 B04 曾指出无喷气 case 的 `Jet_Reaction_Z` 明显非零，需要浩坤确认该 report 是否适用于 no-jet case。
- `G01_JET01_existing` 能整理出现有 STAR 力/力矩时序，但缺少 24 个 `actual_massflow_*`，完整 jet case 仍不成立；程序没有用 `cmd_massflow_*` 冒充实际质量流量。
- 方向、坐标、喷气边界面积/法向仍属于“需要浩坤判断”，不能从列名自动证明。

## G00_nojet_existing

- 状态：`processed_complete_physics_blocked`
- 来源：`/Users/yanbochao/generic_automation_v27_NS_RL100_PF_RULE_2/runs/real_star/G00_nojet_existing/raw_star/out_put`
- raw_star：`/Users/yanbochao/generic_automation_v27_NS_RL100_PF_RULE_2/runs/real_star/G00_nojet_existing/raw_star`
- legacy 对比归档：`runs/real_star/legacy/G00_nojet_existing/`
- timeseries 行列：30000 行，63 列
- 基础质量错误/警告：0 / 1
- B04 物理阻断：1
- 停止原因：标准文件已整理完成；但无喷气 case 的 `Jet_Reaction_Z` 非零，B04 物理一致性检查阻断。

### 原始文件与列

| 文件 | 行数 | 分类 | 映射标准列 | 未映射列 |
|---|---:|---|---|---|
| `out_put/Drag_Monitor_绘图_image_30000.csv` | 30000 | timeseries_monitor | physical_time, Drag_Total | - |
| `out_put/FZ_image_30000.csv` | 30000 | timeseries_monitor | physical_time, Fz_S1L, Fz_S1R, Fz_S2L, Fz_S2R, Fz_S3L, Fz_S3R | - |
| `out_put/Fz_Monitor_绘图_image_30000.csv` | 30000 | timeseries_monitor | physical_time, Fz_Total | - |
| `out_put/Jet_Reaction_Z_Monitor_绘图_image_30000.csv` | 30000 | timeseries_monitor | physical_time, Jet_Reaction_Z | - |
| `out_put/Pitch_Moment_Monitor_绘图_image_30000.csv` | 30000 | timeseries_monitor | physical_time, Pitch_Moment | - |
| `out_put/Roll_Moment_Monitor_绘图_image_30000.csv` | 30000 | timeseries_monitor | physical_time, Roll_Moment | - |
| `out_put/报告绘图_image_30000.csv` | 30000 | time_only | physical_time | S1 Monitor: S1 Monitor (Pa), S2 Monitor: S2 Monitor (Pa), S3 Monitor: S3 Monitor (Pa), 入口 Monitor: 入口 Monitor (Pa), 出口 Monitor: 出口 Monitor (Pa), 右中 Monitor: 右中 Monitor (Pa), 右前 Monitor: 右前 Monitor (Pa), 右后 Monitor: 右后 Monitor (Pa), 左中 Monitor: 左中 Monitor (Pa), 左前 Monitor: 左前 Monitor (Pa), 左后 Monitor: 左后 Monitor (Pa) |

### 处理后列

`physical_time`, `window_id`, `Fz_S1L`, `Fz_S1R`, `Fz_S2L`, `Fz_S2R`, `Fz_S3L`, `Fz_S3R`, `Fz_Total`, `Drag_Total`, `Pitch_Moment`, `Roll_Moment`, `Jet_Reaction_Z`, `solver_status`, `JET_01`, `JET_02`, `JET_03`, `JET_04`, `JET_05`, `JET_06`, `JET_07`, `JET_08`, `JET_09`, `JET_10`, `JET_11`, `JET_12`, `JET_13`, `JET_14`, `JET_15`, `JET_16`, `JET_17`, `JET_18`, `JET_19`, `JET_20`, `JET_21`, `JET_22`, `JET_23`, `JET_24`, `cmd_massflow_01`, `cmd_massflow_02`, `cmd_massflow_03`, `cmd_massflow_04`, `cmd_massflow_05`, `cmd_massflow_06`, `cmd_massflow_07`, `cmd_massflow_08`, `cmd_massflow_09`, `cmd_massflow_10`, `cmd_massflow_11`, `cmd_massflow_12`, `cmd_massflow_13`, `cmd_massflow_14`, `cmd_massflow_15`, `cmd_massflow_16`, `cmd_massflow_17`, `cmd_massflow_18`, `cmd_massflow_19`, `cmd_massflow_20`, `cmd_massflow_21`, `cmd_massflow_22`, `cmd_massflow_23`, `cmd_massflow_24`, `case_stage`

### 缺失与未确认

- `column_name_unmapped` `S1 Monitor: S1 Monitor (Pa)`：Raw column exists in out_put/报告绘图_image_30000.csv but has no standard B03 mapping.
- `column_name_unmapped` `S2 Monitor: S2 Monitor (Pa)`：Raw column exists in out_put/报告绘图_image_30000.csv but has no standard B03 mapping.
- `column_name_unmapped` `S3 Monitor: S3 Monitor (Pa)`：Raw column exists in out_put/报告绘图_image_30000.csv but has no standard B03 mapping.
- `column_name_unmapped` `入口 Monitor: 入口 Monitor (Pa)`：Raw column exists in out_put/报告绘图_image_30000.csv but has no standard B03 mapping.
- `column_name_unmapped` `出口 Monitor: 出口 Monitor (Pa)`：Raw column exists in out_put/报告绘图_image_30000.csv but has no standard B03 mapping.
- `column_name_unmapped` `右中 Monitor: 右中 Monitor (Pa)`：Raw column exists in out_put/报告绘图_image_30000.csv but has no standard B03 mapping.
- `column_name_unmapped` `右前 Monitor: 右前 Monitor (Pa)`：Raw column exists in out_put/报告绘图_image_30000.csv but has no standard B03 mapping.
- `column_name_unmapped` `右后 Monitor: 右后 Monitor (Pa)`：Raw column exists in out_put/报告绘图_image_30000.csv but has no standard B03 mapping.
- `column_name_unmapped` `左中 Monitor: 左中 Monitor (Pa)`：Raw column exists in out_put/报告绘图_image_30000.csv but has no standard B03 mapping.
- `column_name_unmapped` `左前 Monitor: 左前 Monitor (Pa)`：Raw column exists in out_put/报告绘图_image_30000.csv but has no standard B03 mapping.
- `column_name_unmapped` `左后 Monitor: 左后 Monitor (Pa)`：Raw column exists in out_put/报告绘图_image_30000.csv but has no standard B03 mapping.
- `direction_unconfirmed` `load_direction_convention`：STAR monitor values are preserved; positive aerodynamic direction is not independently confirmed.

### 数据异常

- `warning` no_jet Jet_Reaction_Z is nonzero; max_abs=2970.04.
- `error` B04 physics check: no-jet case should have `Jet_Reaction_Z = 0` or explicitly not applicable; current processed data has nonzero values.
## G01_JET01_existing

- 状态：`incomplete_case_missing_required_data`
- 来源：`/Users/yanbochao/generic_automation_v27_NS_RL100_PF_RULE_2/runs/real_star/G01_JET01_existing/raw_star/out_put`
- raw_star：`/Users/yanbochao/generic_automation_v27_NS_RL100_PF_RULE_2/runs/real_star/G01_JET01_existing/raw_star`
- legacy 对比归档：`runs/real_star/legacy/G01_JET01_existing/`
- timeseries 行列：30000 行，63 列
- 基础质量错误/警告：25 / 0
- B04 物理阻断：0；B04 质量流量警告：缺少 `actual_massflow_*`，未用 `cmd_massflow_*` 替代
- 停止原因：Existing STAR exports do not contain all required data for a full jet case.

### 原始文件与列

| 文件 | 行数 | 分类 | 映射标准列 | 未映射列 |
|---|---:|---|---|---|
| `out_put/Drag_Monitor_绘图_image_30000.csv` | 30000 | timeseries_monitor | physical_time, Drag_Total | - |
| `out_put/Excel兼容_UTF8_BOM/Drag_Monitor_绘图_image_30000.csv` | 30000 | timeseries_monitor | physical_time, Drag_Total | - |
| `out_put/Excel兼容_UTF8_BOM/FZ_image_30000.csv` | 30000 | timeseries_monitor | physical_time, Fz_S1L, Fz_S1R, Fz_S2L, Fz_S2R, Fz_S3L, Fz_S3R | - |
| `out_put/Excel兼容_UTF8_BOM/Fz_Monitor_绘图_image_30000.csv` | 30000 | timeseries_monitor | physical_time, Fz_Total | - |
| `out_put/Excel兼容_UTF8_BOM/Jet_Reaction_Z_Monitor_绘图_image_30000.csv` | 30000 | timeseries_monitor | physical_time, Jet_Reaction_Z | - |
| `out_put/Excel兼容_UTF8_BOM/Pitch_Moment_Monitor_绘图_image_30000.csv` | 30000 | timeseries_monitor | physical_time, Pitch_Moment | - |
| `out_put/Excel兼容_UTF8_BOM/Roll_Moment_Monitor_绘图_image_30000.csv` | 30000 | timeseries_monitor | physical_time, Roll_Moment | - |
| `out_put/Excel兼容_UTF8_BOM/报告绘图_image_30000.csv` | 30000 | time_only | physical_time | S1 Monitor: S1 Monitor (Pa), S2 Monitor: S2 Monitor (Pa), S3 Monitor: S3 Monitor (Pa), 入口 Monitor: 入口 Monitor (Pa), 出口 Monitor: 出口 Monitor (Pa), 右中 Monitor: 右中 Monitor (Pa), 右前 Monitor: 右前 Monitor (Pa), 右后 Monitor: 右后 Monitor (Pa), 左中 Monitor: 左中 Monitor (Pa), 左前 Monitor: 左前 Monitor (Pa), 左后 Monitor: 左后 Monitor (Pa) |
| `out_put/FZ_image_30000.csv` | 30000 | timeseries_monitor | physical_time, Fz_S1L, Fz_S1R, Fz_S2L, Fz_S2R, Fz_S3L, Fz_S3R | - |
| `out_put/Fz_Monitor_绘图_image_30000.csv` | 30000 | timeseries_monitor | physical_time, Fz_Total | - |
| `out_put/Jet_Reaction_Z_Monitor_绘图_image_30000.csv` | 30000 | timeseries_monitor | physical_time, Jet_Reaction_Z | - |
| `out_put/Pitch_Moment_Monitor_绘图_image_30000.csv` | 30000 | timeseries_monitor | physical_time, Pitch_Moment | - |
| `out_put/Roll_Moment_Monitor_绘图_image_30000.csv` | 30000 | timeseries_monitor | physical_time, Roll_Moment | - |
| `out_put/报告绘图_image_30000.csv` | 30000 | time_only | physical_time | S1 Monitor: S1 Monitor (Pa), S2 Monitor: S2 Monitor (Pa), S3 Monitor: S3 Monitor (Pa), 入口 Monitor: 入口 Monitor (Pa), 出口 Monitor: 出口 Monitor (Pa), 右中 Monitor: 右中 Monitor (Pa), 右前 Monitor: 右前 Monitor (Pa), 右后 Monitor: 右后 Monitor (Pa), 左中 Monitor: 左中 Monitor (Pa), 左前 Monitor: 左前 Monitor (Pa), 左后 Monitor: 左后 Monitor (Pa) |

### 处理后列

`physical_time`, `window_id`, `Fz_S1L`, `Fz_S1R`, `Fz_S2L`, `Fz_S2R`, `Fz_S3L`, `Fz_S3R`, `Fz_Total`, `Drag_Total`, `Pitch_Moment`, `Roll_Moment`, `Jet_Reaction_Z`, `solver_status`, `JET_01`, `JET_02`, `JET_03`, `JET_04`, `JET_05`, `JET_06`, `JET_07`, `JET_08`, `JET_09`, `JET_10`, `JET_11`, `JET_12`, `JET_13`, `JET_14`, `JET_15`, `JET_16`, `JET_17`, `JET_18`, `JET_19`, `JET_20`, `JET_21`, `JET_22`, `JET_23`, `JET_24`, `cmd_massflow_01`, `cmd_massflow_02`, `cmd_massflow_03`, `cmd_massflow_04`, `cmd_massflow_05`, `cmd_massflow_06`, `cmd_massflow_07`, `cmd_massflow_08`, `cmd_massflow_09`, `cmd_massflow_10`, `cmd_massflow_11`, `cmd_massflow_12`, `cmd_massflow_13`, `cmd_massflow_14`, `cmd_massflow_15`, `cmd_massflow_16`, `cmd_massflow_17`, `cmd_massflow_18`, `cmd_massflow_19`, `cmd_massflow_20`, `cmd_massflow_21`, `cmd_massflow_22`, `cmd_massflow_23`, `cmd_massflow_24`, `case_stage`

### 缺失与未确认

- `column_absent` `actual_massflow_01`：Missing required column: actual_massflow_01
- `column_absent` `actual_massflow_02`：Missing required column: actual_massflow_02
- `column_absent` `actual_massflow_03`：Missing required column: actual_massflow_03
- `column_absent` `actual_massflow_04`：Missing required column: actual_massflow_04
- `column_absent` `actual_massflow_05`：Missing required column: actual_massflow_05
- `column_absent` `actual_massflow_06`：Missing required column: actual_massflow_06
- `column_absent` `actual_massflow_07`：Missing required column: actual_massflow_07
- `column_absent` `actual_massflow_08`：Missing required column: actual_massflow_08
- `column_absent` `actual_massflow_09`：Missing required column: actual_massflow_09
- `column_absent` `actual_massflow_10`：Missing required column: actual_massflow_10
- `column_absent` `actual_massflow_11`：Missing required column: actual_massflow_11
- `column_absent` `actual_massflow_12`：Missing required column: actual_massflow_12
- `column_absent` `actual_massflow_13`：Missing required column: actual_massflow_13
- `column_absent` `actual_massflow_14`：Missing required column: actual_massflow_14
- `column_absent` `actual_massflow_15`：Missing required column: actual_massflow_15
- `column_absent` `actual_massflow_16`：Missing required column: actual_massflow_16
- `column_absent` `actual_massflow_17`：Missing required column: actual_massflow_17
- `column_absent` `actual_massflow_18`：Missing required column: actual_massflow_18
- `column_absent` `actual_massflow_19`：Missing required column: actual_massflow_19
- `column_absent` `actual_massflow_20`：Missing required column: actual_massflow_20
- `column_absent` `actual_massflow_21`：Missing required column: actual_massflow_21
- `column_absent` `actual_massflow_22`：Missing required column: actual_massflow_22
- `column_absent` `actual_massflow_23`：Missing required column: actual_massflow_23
- `column_absent` `actual_massflow_24`：Missing required column: actual_massflow_24
- `column_name_unmapped` `S1 Monitor: S1 Monitor (Pa)`：Raw column exists in out_put/Excel兼容_UTF8_BOM/报告绘图_image_30000.csv but has no standard B03 mapping.
- `column_name_unmapped` `S2 Monitor: S2 Monitor (Pa)`：Raw column exists in out_put/Excel兼容_UTF8_BOM/报告绘图_image_30000.csv but has no standard B03 mapping.
- `column_name_unmapped` `S3 Monitor: S3 Monitor (Pa)`：Raw column exists in out_put/Excel兼容_UTF8_BOM/报告绘图_image_30000.csv but has no standard B03 mapping.
- `column_name_unmapped` `入口 Monitor: 入口 Monitor (Pa)`：Raw column exists in out_put/Excel兼容_UTF8_BOM/报告绘图_image_30000.csv but has no standard B03 mapping.
- `column_name_unmapped` `出口 Monitor: 出口 Monitor (Pa)`：Raw column exists in out_put/Excel兼容_UTF8_BOM/报告绘图_image_30000.csv but has no standard B03 mapping.
- `column_name_unmapped` `右中 Monitor: 右中 Monitor (Pa)`：Raw column exists in out_put/Excel兼容_UTF8_BOM/报告绘图_image_30000.csv but has no standard B03 mapping.
- `column_name_unmapped` `右前 Monitor: 右前 Monitor (Pa)`：Raw column exists in out_put/Excel兼容_UTF8_BOM/报告绘图_image_30000.csv but has no standard B03 mapping.
- `column_name_unmapped` `右后 Monitor: 右后 Monitor (Pa)`：Raw column exists in out_put/Excel兼容_UTF8_BOM/报告绘图_image_30000.csv but has no standard B03 mapping.
- `column_name_unmapped` `左中 Monitor: 左中 Monitor (Pa)`：Raw column exists in out_put/Excel兼容_UTF8_BOM/报告绘图_image_30000.csv but has no standard B03 mapping.
- `column_name_unmapped` `左前 Monitor: 左前 Monitor (Pa)`：Raw column exists in out_put/Excel兼容_UTF8_BOM/报告绘图_image_30000.csv but has no standard B03 mapping.
- `column_name_unmapped` `左后 Monitor: 左后 Monitor (Pa)`：Raw column exists in out_put/Excel兼容_UTF8_BOM/报告绘图_image_30000.csv but has no standard B03 mapping.
- `column_name_unmapped` `S1 Monitor: S1 Monitor (Pa)`：Raw column exists in out_put/报告绘图_image_30000.csv but has no standard B03 mapping.
- `column_name_unmapped` `S2 Monitor: S2 Monitor (Pa)`：Raw column exists in out_put/报告绘图_image_30000.csv but has no standard B03 mapping.
- `column_name_unmapped` `S3 Monitor: S3 Monitor (Pa)`：Raw column exists in out_put/报告绘图_image_30000.csv but has no standard B03 mapping.
- `column_name_unmapped` `入口 Monitor: 入口 Monitor (Pa)`：Raw column exists in out_put/报告绘图_image_30000.csv but has no standard B03 mapping.
- `column_name_unmapped` `出口 Monitor: 出口 Monitor (Pa)`：Raw column exists in out_put/报告绘图_image_30000.csv but has no standard B03 mapping.
- `column_name_unmapped` `右中 Monitor: 右中 Monitor (Pa)`：Raw column exists in out_put/报告绘图_image_30000.csv but has no standard B03 mapping.
- `column_name_unmapped` `右前 Monitor: 右前 Monitor (Pa)`：Raw column exists in out_put/报告绘图_image_30000.csv but has no standard B03 mapping.
- `column_name_unmapped` `右后 Monitor: 右后 Monitor (Pa)`：Raw column exists in out_put/报告绘图_image_30000.csv but has no standard B03 mapping.
- `column_name_unmapped` `左中 Monitor: 左中 Monitor (Pa)`：Raw column exists in out_put/报告绘图_image_30000.csv but has no standard B03 mapping.
- `column_name_unmapped` `左前 Monitor: 左前 Monitor (Pa)`：Raw column exists in out_put/报告绘图_image_30000.csv but has no standard B03 mapping.
- `column_name_unmapped` `左后 Monitor: 左后 Monitor (Pa)`：Raw column exists in out_put/报告绘图_image_30000.csv but has no standard B03 mapping.
- `direction_unconfirmed` `load_direction_convention`：STAR monitor values are preserved; positive aerodynamic direction is not independently confirmed.

## 汇总交付物

- `runs/real_star/G00_nojet_existing/`
- `runs/real_star/G01_JET01_existing/`
- `runs/real_star/legacy/G00_nojet_existing/`
- `runs/real_star/legacy/G01_JET01_existing/`
- `docs/week3/B03_missing_data_matrix.csv`
- `docs/week3/B03_existing_data_ingest_report.md`
