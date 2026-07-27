# B02 Open Questions for Haokun

只保留必须由浩坤确认、不能由当前代码或 CSV 列名可靠推出的问题。

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
