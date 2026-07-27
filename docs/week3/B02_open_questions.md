# B32 / B5 Open Questions for Haokun

只保留必须由浩坤确认、不能由当前代码或 CSV 列名可靠推出的问题。当前目标是先把 B32 真实数据接入口径和 B5 无喷气/单喷气升力反常诊断口径对齐，避免直接用不同时间点的瞬时值做结论。

## 1. 当前已出现的疑问

1. 无喷气样例中 `Jet_Reaction_Z` 非零，最大绝对值约 `2.97e3 N`。需要确认这是否符合 STAR monitor 定义；如果符合，它代表哪个边界、哪个力方向或哪个参考量。
2. 浩坤已指出无喷气和 `JET_01` 的升力数据存在反常。需要先确认反常是从 `Fz_S1L/S1R/S2L/S2R/S3L/S3R`、`Fz_Total`、`Jet_Reaction_Z`、`actual_massflow_01` 还是合成有效力中最先出现。
3. 目前只能确认导出 header 和单位，不能由 `Fz`、`Z`、`Lift` 等列名直接判断正方向、坐标系和积分表面。
4. 当前两个 temp case 的动作表和 STAR monitor 都是 `30000` 行、`dt=0.0001 s`、总时长约 `3.0 s`，但仍需确认它们是否真正从同一 checkpoint 和同一数据窗口开始。

## 2. B5 异常升力诊断前必须确认

1. 喷气开启时刻应该按哪个量定义为 `t=0`：动作表 `JET_01` 由 0 变 1 的时刻、`cmd_massflow_01` 非零的时刻、`actual_massflow_01` 非零的时刻，还是力曲线首次响应的时刻？
2. 无喷气和 `JET_01` 是否从同一个 checkpoint、同一网格、同一 solver 设置、同一物理时间步和同一初始流场继续计算？
3. 两个 case 的可比较基准段是哪一段时间？喷气前基准段应取多长，是否存在需要排除的初始过渡段？
4. 两个 case 的喷气后比较窗口是哪一段时间？是否都已经达到可比较的准稳态，还是仍处于漂移或瞬态恢复中？
5. 6 个区域力的正方向是否一致？`Fz_S1L/S1R/S2L/S2R/S3L/S3R` 的正值到底表示向上升力、向下压力，还是 STAR report 指定方向上的力？
6. `Fz_Total` 是否真实等于整车升力？它的 Direction Vector、Coordinate System 和积分表面是什么？
7. 6 个区域力之和与 `Fz_Total` 的关系是什么：应该近似相等、只覆盖局部表面、还是可能与整车力存在重叠或遗漏？
8. 喷气面上的力是否已经计入 `Fz_Total` 或某些区域力？是否存在“车体气动力”和“喷气边界反作用力”重复计入的问题？
9. `Jet_Reaction_Z` 的符号约定是什么？正值表示喷气对车体的直接反作用力向上，还是 STAR 对流体/边界的力方向？
10. `Jet_Reaction_Z` 统计全部喷口边界还是只统计打开喷口？无喷气时是否应严格为零？
11. `actual_massflow_01` 的正负号、单位和统计口径是什么？正值表示流入计算域还是流出计算域？是单喷口质量流量还是多个面的合计？
12. `actual_massflow_01` 的大小是否应该等于 `cmd_massflow_01=2.57 kg/s`？允许的偏差范围是多少？
13. `JET_01` 在 STAR 中对应的真实边界是否唯一？是否可能同时绑定到多个面或错误面？
14. 若车体气动力变化和喷气直接反作用力合成，项目最终要看的“有效总力”定义应为 `Fz_Total + Jet_Reaction_Z`，还是需要反号或排除某些表面？
15. 如果以上数据定义、方向、重复计入、质量流量和 checkpoint 都不能解释异常，是否同意把“喷气破坏原有高压区、增强泄漏或产生卷吸”列为待复工后进一步判断的物理可能性？

## 3. B32 / 真实数据接入仍需确认

1. 24 个喷气区的真实 CAD 面名称、STAR Region、STAR Boundary 完整路径分别是什么？当前 `JET01` / `Document.Document_Body.JET01` 只能作为候选命名模式。
2. 每个喷气区对应的承载区、面积 `m^2` 和面法向向量是什么？是否有局部坐标系或旋转坐标系参与定义？
3. 6 个区域力 `S1L/S1R/S2L/S2R/S3L/S3R` 的 STAR 原始 report 名称、Direction Vector、Coordinate System 和参与积分的表面分别是什么？
4. `Fz_Total` 是否就是本项目要训练/评估的“升力”？如果是，它在 STAR report 中的 Direction Vector、Coordinate System、正方向和积分表面是什么？
5. `Drag_Total` 的正方向是什么？是来流方向、反来流方向，还是 STAR report 中指定的其他 Direction Vector？
6. `Pitch_Moment` 和 `Roll_Moment` 的力矩中心、坐标系、轴方向和正号约定是什么？
7. `Jet_Reaction_Z` 的 STAR 原始 report 名称、Direction Vector、Coordinate System、积分表面是什么？它是否统计全部喷口边界，还是只统计打开喷口？
8. 当前两个 temp case 的 STAR monitor 采样为 `0.0001, 0.0002, ..., ~3.0 s`，动作表窗口为 `0.0-0.0001, ..., 2.9999-3.0 s`。STAR 宏/求解器在 `t == t_end` 时刻输出的是刚结束窗口的响应，还是下一窗口的响应？
9. STAR 宏读取动作表时，`t_end` 时刻是否已经切换到下一行动作？需要明确边界规则是 `[t_start, t_end)`、`(t_start, t_end]`，还是当前 ingest 使用的“输出样本按同序号窗口对齐”。
10. 当前真实 `.sim` 文件的 STAR 版本、sim 文件标识/哈希、网格版本分别是什么？当前 manifest 已记录 `time_step=0.0001` 和当前导出采样间隔 `0.0001 s`，但 STAR 版本和网格仍未知。

## 4. 建议请浩坤优先回答的最小集合

1. `Fz_Total`、6 个区域力和 `Jet_Reaction_Z` 的 Direction Vector、Coordinate System、积分表面和正号。
2. `Jet_Reaction_Z` 无喷气非零是否符合 monitor 定义，以及是否可能与车体气动力重复计入。
3. `actual_massflow_01` 的正负号、单位、统计面和是否应等于 `cmd_massflow_01=2.57 kg/s`。
4. 无喷气和 `JET_01` 是否从同一 checkpoint 开始，数据窗口和采样窗口是否一致。
5. 动作窗口边界规则：STAR 输出在 `t_end` 时刻属于刚结束窗口还是下一窗口。
