# B02 Real Data Contract

本文件是第三周真实 STAR 数据接入的数据契约。目标是把算法标准列名、STAR 对象名、物理方向和物理时间关系逐项对齐。凡是当前仓库不能可靠证明的信息，都必须保留为“待浩坤确认”，不能根据截图、文件名或列名自行补全。

## 1. 配套文件

| 文件 | 作用 |
| --- | --- |
| `docs/week3/B02_boundary_mapping.csv` | 24 个喷气区的标准编号、候选 STAR 名称、Region/Boundary、承载区、面积、法向确认表。 |
| `docs/week3/B02_report_mapping.csv` | 6 个区域力、整车总力、阻力、俯仰、滚转和喷气反作用力的 report 对照表。 |
| `docs/week3/B02_case_manifest_template.yaml` | 标准 case manifest 模板，记录 STAR、网格、时间、坐标、动作表哈希和代码 commit。 |
| `docs/week3/B02_open_questions.md` | 必须由浩坤确认的问题清单。 |

当前 B32 的真实 STAR 导出样本放在两个标准 case 目录：

| case | 作用 | STAR 原始导出目录 | 标准输出 |
| --- | --- | --- | --- |
| `runs/temp_j01_3s_mass2p57_dt1e-4` | `JET_01` 打开，`cmd_massflow_01=2.57 kg/s`，持续 3 s | `runs/temp_j01_3s_mass2p57_dt1e-4/out_put/` | `timeseries.csv`, `actuation_schedule.csv`, `case_manifest.yaml`, `quality_report.json` |
| `runs/temp_no_jet_3s_dt1e-4` | 24 个喷气全关，持续 3 s | `runs/temp_no_jet_3s_dt1e-4/out_put/` | `timeseries.csv`, `actuation_schedule.csv`, `case_manifest.yaml`, `quality_report.json` |

## 2. 标准命名

算法侧统一使用以下喷气动作列：

```text
JET_01 ... JET_24
cmd_massflow_01 ... cmd_massflow_24
actual_massflow_01 ... actual_massflow_24
```

`JET_XX` 表示算法侧开关，`cmd_massflow_XX` 表示动作表给 STAR 的指令质量流量，`actual_massflow_XX` 表示 STAR 或后处理得到的实际质量流量。

当前 CCM 界面已确认 STAR 命名口径：

| STAR 名称 | 当前含义 |
| --- | --- |
| `JET01..JET24` | 底部边界 / 底部受力分区 |
| `J01..J24` | 喷气口 |

因此算法侧 `JET_01..JET_24` 如果表示喷气控制，应映射到 `J01..J24`，不是 `JET01..JET24`。建模人已确认最终喷气建模方式是把 `J01..J24` 改成质量流量入口；当前 `.sim` 中 `J01` 仍显示为 wall boundary，说明当前状态尚未切换到最终喷气边界条件。

算法侧统一使用以下力和力矩列：

```text
Fz_S1L, Fz_S1R, Fz_S2L, Fz_S2R, Fz_S3L, Fz_S3R
Fz_Total, Drag_Total, Pitch_Moment, Roll_Moment, Jet_Reaction_Z
```

这些列名是算法接口名，不是物理方向证明。尤其是 `Fz_Total` 和 `Jet_Reaction_Z`，不能因为名称里有 `Fz` 或 `Z` 就默认它们一定是全局竖直方向。

## 3. 升力方向核实规则

“升力”的真实定义必须来自 STAR report 本身，至少包含：

| 字段 | 要求 |
| --- | --- |
| STAR original report name | 真实 report 名称，不用截图文字代替。 |
| Coordinate System | STAR report 使用的坐标系。 |
| Direction Vector | STAR report 中的方向向量。 |
| Integrated Surfaces | report 参与积分的表面集合。 |
| Unit | 从 STAR export header 或 report 定义读取。 |

在这些字段确认前，`Fz_Total` 只能叫“标准总力列”，不能在训练解释里直接叫“升力”。如果 STAR 的 Direction Vector 不是期望升力方向，必须在 `docs/week3/B02_report_mapping.csv` 中改标准列含义或新增明确列名，不能静默沿用。

当前两个 temp case 已确认的 STAR 导出 header 如下，单位来自 CSV header：

| 统一列名 | STAR 导出 header | 单位 |
| --- | --- | --- |
| `Fz_S1L` | `S1L Monitor: S1L Monitor (N)` | N |
| `Fz_S1R` | `S1R Monitor: S1R Monitor (N)` | N |
| `Fz_S2L` | `S2L Monitor: S2L Monitor (N)` | N |
| `Fz_S2R` | `S2R Monitor: S2R Monitor (N)` | N |
| `Fz_S3L` | `S3L Monitor: S3L Monitor (N)` | N |
| `Fz_S3R` | `S3R Monitor: S3R Monitor (N)` | N |
| `Fz_Total` | `Fz Monitor: Fz Monitor (N)` | N |
| `Drag_Total` | `Drag Monitor: Drag Monitor (N)` | N |
| `Pitch_Moment` | `Pitch_Moment Monitor: Pitch_Moment Monitor (N-m)` | N-m |
| `Roll_Moment` | `Roll_Moment Monitor: Roll_Moment Monitor (N-m)` | N-m |
| `Jet_Reaction_Z` | `Jet_Reaction_Z Monitor: Jet_Reaction_Z Monitor (N)` | N |

这些 header 只能证明导出列名和单位。当前 CCM 界面已进一步确认：`Fz`、`Jet_Reaction_Z` 和 6 个区域力均为 `Laboratory` 坐标系、方向 `[0.0, 0.0, 1.0]`、力选项“压力 + 剪切”、参考压力 `0.0 Pa`。其中 6 个区域力选中互斥的 `JET` 底部边界分区，`Fz` 选中全部 `JET01..JET24` 加 `tail`，`Jet_Reaction_Z` 选中全部 `J01..J24` 喷气口。`Drag_Total`、`Pitch_Moment` 和 `Roll_Moment` 的方向、坐标系、轴和力矩中心仍需确认。

## 4. 动作窗口和 STAR 输出时间

当前动作表约定来自 `docs/week2/B02_喷气激励数据字典.md`：

```text
physical_time, window_id, t_start, t_end,
JET_01 ... JET_24,
cmd_massflow_01 ... cmd_massflow_24
```

每一行表示一个物理时间窗口，不是求解器迭代步。默认解释为：

```text
row i defines one physical action window from t_start to t_end seconds
physical_time == t_start
```

也就是说，`t_start` 是本行动作开始生效的物理时间，`t_end` 是本行动作结束边界。STAR 内部可以在同一个物理窗口里做多个 time step 或内迭代，但后续训练和对齐只能使用物理时间 `s`，不能用 solver iteration 代替。

当前两个 temp case 的实际文件关系是：

```text
actuation_schedule.csv: 30000 rows, t_start = 0.0 ... 2.9999, t_end = 0.0001 ... 3.0
STAR monitor CSV:       30000 rows, physical_time = 0.0001 ... approximately 3.0
time_step:              0.0001 s
sampling interval:      0.0001 s in these exports
```

当前标准化 `timeseries.csv` 按行序把 STAR 第一个输出样本 `0.0001 s` 对齐到 `window_id=0`，即动作表第一行 `0.0-0.0001 s`。这等价于把窗口终点样本当作该窗口响应。这个行为符合当前两个 temp case 的 ingest 结果，但仍需要浩坤确认 STAR 宏在窗口边界的真实切换规则。

## 5. 对齐规则

1. 读取 STAR CSV 时，先把原始 header 映射到 `docs/week3/B02_report_mapping.csv` 的统一列名。
2. 对喷气数据，先用 `docs/week3/B02_boundary_mapping.csv` 确认 `JET_XX` 对应的真实 STAR Boundary，再读取或写入对应 profile/report。
3. 对当前 temp case，`timeseries.csv.window_id` 已由 ingest 按动作表行序写入，训练优先使用该 `window_id` 对齐。
4. 如果未来要只靠 `t_sample` 自动对齐动作窗口，必须先确认边界规则：

```text
t_start <= t_sample < t_end
or
t_start < t_sample <= t_end
```

5. 当前两个 temp case 的采样刚好落在窗口终点，所以不能在未确认前把通用规则硬编码为 `[t_start, t_end)`。
6. 缺失的物理量保持缺失并写入质量报告，不允许补 0。无喷气 case 的喷气开关可以为 0，但真实反作用力、实际质量流量不能凭空伪造。

## 6. Manifest 要求

每个真实 STAR case 必须包含 `case_manifest.yaml`，字段参考 `docs/week3/B02_case_manifest_template.yaml`。至少要追溯：

```text
STAR version
sim file identifier/hash
geometry_version
mesh_version
solver physical time step
monitor sampling interval
coordinate directions
action schedule hash
git commit
boundary/report mapping file versions
```

凡是未确认字段，必须显式写 `待浩坤确认`，不能删掉字段。

## 7. 当前已知与未知

已知：

| 项 | 当前状态 |
| --- | --- |
| 算法喷气列 | `JET_01..JET_24`、`cmd_massflow_01..cmd_massflow_24`。 |
| STAR 命名口径 | `JET01..JET24` 是底部边界，`J01..J24` 是喷气口。 |
| 控制候选边界 | 算法侧 `JET_01..JET_24` 控制候选对象应是 STAR `J01..J24`。 |
| 当前 J01 边界状态 | 当前 `.sim` 中 `J01` 显示为 wall boundary；建模人确认最终喷气建模需改成质量流量入口。 |
| 动作窗口语义 | 每行表示物理时间窗口，`physical_time == t_start`。 |
| 当前真实样本 | 两个 temp case 均为 30000 个动作窗口和 30000 个 STAR 输出样本，`dt=0.0001 s`。 |
| 当前 STAR 导出列 | 已确认 6 个区域力、总 Fz、Drag、Pitch、Roll、Jet_Reaction_Z 的 CSV header 和单位。 |
| 标准载荷列 | 6 个区域力、整车总力、阻力、俯仰、滚转、喷气反作用力。 |
| Z 向力 report 口径 | 6 个区域力、`Fz`、`Jet_Reaction_Z` 均为 `Laboratory +Z` 压力+剪切力。 |
| 6 区域力积分面 | `S1L=JET01/JET02/JET05/JET06`，`S1R=JET03/JET04/JET07/JET08`，`S2L=JET09/JET10/JET13/JET14`，`S2R=JET11/JET12/JET15/JET16`，`S3L=JET17/JET18/JET21/JET22`，`S3R=JET19/JET20/JET23/JET24`。 |
| `Fz_Total` 积分面 | 全部 `JET01..JET24` 加 `tail`，不含 `J01..J24`；不能直接称为整车全表面升力。 |
| `Jet_Reaction_Z` 积分面 | 全部 `J01..J24`，不含 `JET01..JET24`；在当前 wall 状态是喷气口壁面 +Z 力，在最终质量流量入口状态可作为喷气口边界 +Z 力 report。 |
| STAR CSV 读取 | 当前代码按 header 正则映射标准列，并保留缺失列。 |

未知，必须确认：

| 项 | 原因 |
| --- | --- |
| `J01..J24` 的面积和法向 | 当前只确认命名和边界类型，面积、法向、正负号仍需建模人确认。 |
| 喷气施加方式 | 建模人确认最终方式是把 `J01..J24` 改成质量流量入口；仍需确认 profile 绑定、正负号和动作窗口切换。 |
| `actual_massflow_01` 真实回读 | 当前未找到 mass flow report/monitor；需确认是否存在实际质量流量输出及其正负号。 |
| `Fz_Total` 是否可作为项目升力 | 当前只确认它是 `JET01..JET24 + tail` 的 +Z 力，不是整车全表面力。 |
| 阻力、俯仰、滚转方向 | 必须来自 STAR report 定义。 |
| STAR 窗口边界切换规则 | 当前样本在窗口终点输出，必须确认这个输出属于刚结束窗口还是下一窗口。 |
