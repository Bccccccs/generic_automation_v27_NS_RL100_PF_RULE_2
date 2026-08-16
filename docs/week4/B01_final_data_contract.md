# B01：0816 STAR 最终数据字段契约

本契约的字段名完全取自 2026-08-16 已组织的三个算例：`runs/week4/j02_pluse_0816`、`runs/week4/j06_pluse_0816`、`runs/week4/no_0816`。不得用本文件创建新的动作或载荷列；未在当前算例导出的量只能记录为“未导出”，不能另造字段替代。

## 1. J 与 JET 的固定含义

| STAR 名称 | 含义 | 0816 数据字段中的对应关系 |
| --- | --- | --- |
| `J01..J24` | 喷气口 | `JET_01..JET_24` 开关、`cmd_massflow_01..24` 指令质量流量、`actual_massflow_01..24` 回读质量流量按相同编号对应 `J01..J24` |
| `JET01..JET24` | 车底受力区域 | 仅用于六个 `Fz_S*` report 的积分面；不得当作喷气口 |

注意：`JET_01`（带下划线）是当前 CSV 的动作列名；`JET01`（不带下划线）是 STAR 车底区域。两者字符串相近但对象不同。`B01_J_JET_mapping.csv` 是唯一编号与面积/法向对照来源；面积来自 0816 case 的 `面积计算结果.xlsx` 中“star实际面积(m^2)”列，J 和 JET 法向均为 `[0.0, 0.0, 1.0]`。

## 2. 0816 实际输出字段

| 当前字段 | 当前 STAR 导出 report | 单位 | 状态/含义 |
| --- | --- | --- | --- |
| `Fz_S1L`、`Fz_S1R`、`Fz_S2L`、`Fz_S2R`、`Fz_S3L`、`Fz_S3R` | `S1L..S3R Monitor` | N | 六个车底区域 +Z 力 |
| `fz` | `fz Monitor`（仅 raw STAR CSV） | N | 车底六区合力/底面整体升力；应与六个 `Fz_S*` 分区升力之和一致，待后续算例验证 |
| `Fz_Total` | `Fz Monitor` | N | 带车壳的升力，即底面 `JET01..JET24` 加 `tail` 车壳的 +Z 力；不能替代 `fz` |
| `Drag_Total` | `Drag Monitor` | N | 当前导出阻力 report |
| `Pitch_Moment` | `Pitch_Moment Monitor` | N-m | 当前导出俯仰力矩 report |
| `Roll_Moment` | `Roll_Moment Monitor` | N-m | 当前导出滚转力矩 report |
| `Jet_Reaction_Z` | `Jet_Reaction_Z Monitor` | N | 当前 `J01..J24` 表面 +Z 力；其是否可作为“喷气动量反作用力”使用，等待浩坤确认 |
| `actual_massflow_01..24` | `actual_massflow_01..24 Monitor` | kg/s | 24 个 J 喷气口质量流量回读 |

浩坤已确认：六区分项由 `FZ_image_10000.csv` 导出，六区合力/底面整体升力为 `fz.csv` 的 `fz`，其校验关系是 `fz = Fz_S1L + Fz_S1R + Fz_S2L + Fz_S2R + Fz_S3L + Fz_S3R`；后续算例必须验证该关系。带车壳升力为 `Fz_Monitor_绘图_image_10000.csv` 的 `Fz_Total`，不得与 `fz` 互相替代。`Jet_Reaction_Z` 是否为项目定义的喷气动量反作用力，等待浩坤确认，在确认前只能按“J 表面 +Z 力”解释。

## 3. 方向、积分面与时间

`B01_report_mapping.csv` 逐项记录每个已导出 report 的单位、Laboratory 坐标方向、积分面和确认状态。缺失或未确认项必须告警并保留 `UNCONFIRMED`，不得猜测。

0816 case 的求解器时间步 `time_step` 为 `0.0001 s`，逻辑动作窗口 `action_window_duration` 为 `0.1 s`，因此一个动作窗口固定包含 `1000` 个求解器时间步。动作表和 report 仍以 `0.0001 s` 行间隔/采样间隔保存：同一 `0.1 s` 动作窗口的命令在连续 1000 行中保持不变；它们是窗口内的求解/采样行，不是 1000 个独立动作。原始输出文件名为 `*_10000.csv`，即每 case 10,000 个采样。动作行字段为 `physical_time, window_id, t_start, t_end`；当前 case manifest 记录的输出模式为 `partial_timeseries`。

## 4. 导入保护规则

1. 导入动作表时，`JET_NN`、`cmd_massflow_NN` 和 `actual_massflow_NN` 必须三者以相同 `NN` 映射 `JNN`；`JETNN` 不得被用作动作列。
2. 导入 `fz`、`Fz_Total` 或 `Jet_Reaction_Z` 时保留原字段名，并按 `B01_report_mapping.csv` 写入其实际 report 含义；`Jet_Reaction_Z` 在浩坤确认前不得自动标注为动量反作用力。
3. 发现未列入映射表的 report、同一字段匹配多个 report，或 report 缺少单位/方向/积分面确认时，程序必须报错或告警，不得推断。
