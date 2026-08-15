# B01 最终 STAR 数据契约

版本：`B01_final_star_contract_v1`。本契约自发布起替代 week3 中容易混淆的输出命名；旧 case 只能作为迁移输入，不能伪装成最终模板数据。

## 1. 不可混用的对象名称

| 名称 | 唯一含义 | 可用于 |
| --- | --- | --- |
| `J01` … `J24` | STAR 喷气口边界（质量流量入口） | 动作开关、指令/实际质量流量、J 表面受力、喷气动量反作用力 |
| `JET01` … `JET24` | STAR 车底受力区域 | 六区域升力、车底六区合力、车底相关力矩 |

最终动作表的列固定为 `J01_switch` … `J24_switch`、`J01_cmd_massflow_kg_s` … `J24_cmd_massflow_kg_s`，可选回读列为 `J01_actual_massflow_kg_s` … `J24_actual_massflow_kg_s`。第 `NN` 列只能写入/读取 `JNN`，绝不对应 `JETNN`。完整一一对应、面积与法向见 `B01_J_JET_mapping.csv`。

质量流量在项目标准中统一以“从 `JNN` 喷口流入流场”为正。STAR 入口 report 如因边界外法向约定导出负值，导入标准 case 时转换为非负流量大小；`raw_star/` 中的原始值不修改。质量流量跟踪验收直接比较转换后的正值指令与正值实际流量，不在质检阶段临时取绝对值。

## 2. 最终输出字段

所有力使用 STAR `Laboratory` 坐标系；正值沿给定方向/轴。report 的积分方法为压力 + 剪切，参考压力为 0 Pa。每一行定义均在 `B01_report_mapping.csv`，不得只依据列名推断物理含义。

| 字段 | 单位 | 含义 |
| --- | --- | --- |
| `underbody_lift_s1l` … `underbody_lift_s3r` | N | 6 个互斥 `JET` 车底分区的 +Z 力 |
| `underbody_6zone_lift` | N | 上述 6 个车底分区力之和；派生量，不是整车升力 |
| `vehicle_lift` | N | 经确认的整车全部外表面 +Z 力 |
| `vehicle_drag` | N | 经确认的整车全部外表面 +X 力 |
| `vehicle_pitch_moment` | N-m | 经确认整车表面绕 +Y 轴、指定力矩中心的力矩 |
| `vehicle_roll_moment` | N-m | 经确认整车表面绕 +X 轴、指定力矩中心的力矩 |
| `j_surface_force_z` | N | 所有 `J01..J24` 喷气口表面的 +Z 压力+剪切力；不是动量反作用力 |
| `jet_momentum_reaction_z` | N | 由每个 `J` 喷口真实质量流量与出口速度/动量通量按 manifest 符号约定计算的反作用力 |

`vehicle_lift`、`vehicle_drag` 和两个整车力矩仅在 STAR report 的积分面明确为 `vehicle_all_external_surfaces` 后才可写入；在此之前必须留为缺失并报错，不能以 `JET01..JET24 + tail` 代替。

## 3. 旧名迁移与禁止猜测

`Fz_Total` 和 `Jet_Reaction_Z` 不属于最终输出字段。`Fz_Total` 历史上实际是 `JET01..JET24 + tail` 的 +Z 力，既不是车底六区合力也不是整车升力；`Jet_Reaction_Z` 历史上实际是 `J01..J24` 的 +Z 表面力，且不是动量反作用力。它们的保留映射和处理策略写在 `B01_report_mapping.csv`。

导入程序必须执行以下规则：

1. 看见旧标准列 `Fz_Total` 或 `Jet_Reaction_Z` 时，发出可见 `DeprecationWarning`，并在 strict/final 模式抛出 `ValueError`；不得自动改名。
2. 看见 `JET_01..JET_24`、`cmd_massflow_01..24` 或未带 `J` 喷口编号的动作列时，报错；不得猜测为 `J01..J24`。
3. 当列名匹配多个 report，或 report 缺少单位、坐标系、方向、积分表面之一时，报错；不得填零或从名称猜方向。
4. 历史 raw STAR report 可依据映射表由专门的迁移步骤导入，但迁移产物必须带 `source_report`、`mapping_version` 与告警记录。

## 4. 时间与采样约定

一个动作表行是物理时间窗口 `[t_start, t_end]` 的控制命令，`window_id` 唯一且连续。当前 CLI 宏在窗口开始写入 J 喷口质量流量、推进到 `t_end` 后采样；故样本归属规则是 `t_start < t_sample <= t_end`。`time_step` 是求解器时间步，`sampling_interval` 是报告导出间隔，二者都必须记录，不能用 iteration 替代物理时间。

## 5. Case manifest 的最低追溯要求

每个 final case 使用 `configs/week4/case_manifest_template.yaml`：记录 24 个 J/JET 对、每个面积和法向、坐标方向、每个 report 的积分表面、时间步、动作窗口及采样间隔。未知值用 `UNCONFIRMED` 显式标记；final 模式不得运行。
