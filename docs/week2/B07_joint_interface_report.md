# B07 联合数据接力接口报告

## 任务目标

本轮目标是完成一次最小数据流程联调，不做自动控制闭环。链路为：

`博超生成 actuation_schedule.csv -> 浩坤在 STAR 或样例算例中使用 -> 浩坤导出 timeseries.csv/monitor CSV -> 博超读取数据 -> 博超生成 quality_report.json 和自动图`

## 验收结论

浩坤给的数据，博超能读。

两个样例目录均已完成读取、质量检查和自动图生成：

- `runs/temp_no_jet_3s_dt1e-4`
- `runs/temp_j01_3s_mass2p57_dt1e-4`

喷气样例 `temp_j01_3s_mass2p57_dt1e-4` 质量检查结果为 `0 error / 0 warning`。无喷气样例 `temp_no_jet_3s_dt1e-4` 质量检查结果为 `0 error / 1 warning`，warning 是无喷气 case 中 `Jet_Reaction_Z` 非零，属于物理/monitor 口径待确认问题，不属于格式读取问题。

## 数据流结果

| case_id | 类型 | 行数 | 列数 | 时间步长 | 总时长 | 读取状态 | 质量结果 |
| --- | --- | ---: | ---: | --- | --- | --- | --- |
| `temp_no_jet_3s_dt1e-4` | no_jet | 30000 | 87 | `1e-4 s` | `3.0 s` | 可读 | `0 error / 1 warning` |
| `temp_j01_3s_mass2p57_dt1e-4` | jet_on | 30000 | 87 | `1e-4 s` | `3.0 s` | 可读 | `0 error / 0 warning` |

喷气样例中，`JET_01` 全程开启，`cmd_massflow_01 = 2.57 kg/s`，`actual_massflow_01 = 2.57 kg/s`。其他喷口质量流量为 0。

## 已具备字段

两个样例均包含以下字段族：

- 时间列：`physical_time`
- 窗口编号：`window_id`
- 喷口开关：`JET_01` 到 `JET_24`
- 指令质量流量：`cmd_massflow_01` 到 `cmd_massflow_24`
- 实际质量流量：`actual_massflow_01` 到 `actual_massflow_24`
- 六个升力/法向力传感器：`Fz_S1L`、`Fz_S1R`、`Fz_S2L`、`Fz_S2R`、`Fz_S3L`、`Fz_S3R`
- 总量：`Fz_Total`、`Drag_Total`、`Pitch_Moment`、`Roll_Moment`、`Jet_Reaction_Z`
- 状态列：`solver_status`、`case_stage`

## 单位和方向

已从本轮 manifest 和 STAR 列名中记录的单位：

| 量 | 单位 |
| --- | --- |
| `Fz_*`、`Fz_Total`、`Drag_Total`、`Jet_Reaction_Z` | `N` |
| `Pitch_Moment`、`Roll_Moment` | `N-m` |
| `cmd_massflow_*`、`actual_massflow_*` | `kg/s` |

仍需两人联合确认的口径：

- Fz 正方向：目前记录为 STAR monitor export convention，需要浩坤明确是向上为正还是按 STAR 坐标轴为正。
- Drag 正方向：需要明确是否沿来流反向为正。
- Pitch/Roll 正方向：需要明确绕哪个轴、右手系方向。
- Jet_Reaction_Z 正方向：需要明确是否应与喷气反力方向一致，以及无喷气时是否应严格为零。

## 格式问题和物理问题区分

格式问题结论：

- 没有发现阻塞读取的格式问题。
- `physical_time` 单调递增。
- 必需列齐全。
- 没有 NaN/missing cell 导致的质量错误。
- 指令质量流量和实际质量流量已分列，喷气样例二者一致。

物理/口径问题结论：

- 无喷气样例中 `Jet_Reaction_Z` 非零，最大绝对值约 `2.97e3 N`。
- 这不是接口解析失败，而是 monitor 定义、边界选择、正方向或物理残差需要解释。
- 下一步应由浩坤确认 STAR 中 `Jet_Reaction_Z Monitor` 的定义和无喷气基线期望值。

## 自动图

无喷气样例自动图：

- `runs/temp_no_jet_3s_dt1e-4/figures/force_timeseries.png`
- `runs/temp_no_jet_3s_dt1e-4/figures/jet_schedule.png`
- `runs/temp_no_jet_3s_dt1e-4/figures/massflow_check.png`
- `runs/temp_no_jet_3s_dt1e-4/figures/quality_summary.png`

喷气样例自动图：

- `runs/temp_j01_3s_mass2p57_dt1e-4/figures/force_timeseries.png`
- `runs/temp_j01_3s_mass2p57_dt1e-4/figures/jet_schedule.png`
- `runs/temp_j01_3s_mass2p57_dt1e-4/figures/massflow_check.png`
- `runs/temp_j01_3s_mass2p57_dt1e-4/figures/quality_summary.png`

## 交付物位置

- `docs/week2/B07_joint_interface_report.md`
- `B07_next_cfd_case_suggestion.csv`
- `docs/week2/B07_current_blocking_issues.md`
- `runs/temp_no_jet_3s_dt1e-4/quality_report.json`
- `runs/temp_j01_3s_mass2p57_dt1e-4/quality_report.json`
- 两个 case 各自 `figures/` 目录下的四张自动图

## 下周联合展示建议

展示顺序建议：

1. 博超展示 `actuation_schedule.csv`，说明字段、喷口编号、质量流量和持续时间。
2. 浩坤展示 STAR 中读取 schedule、运行样例和导出 monitor CSV 的过程。
3. 博超展示 `timeseries.csv` 读取结果、`quality_report.json` 和四张自动图。
4. 两人共同说明当前唯一待确认问题：无喷气 `Jet_Reaction_Z` 非零属于物理/monitor 口径问题，不是格式问题。
5. 共同选择下一组 CFD case，建议优先跑 `B07_J01_repeat_3s_mass2p57` 和 `B07_no_jet_reference_repeat`。
