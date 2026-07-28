# B07 当前阻塞问题

## 是否能训练真实 ROM

不能。当前真实数据的成熟度到“接口可读、JET_01 单喷气趋势可观察”，还没到“可训练真实 ROM”。

## 阻塞项

1. 缺少实际质量流量报告量

`G01_JET01_existing` 缺少 `actual_massflow_01..24`。当前只有 `cmd_massflow_01 = 2.57 kg/s`，这是边界指令，不是 STAR 独立回读的实际质量流量。不能用 `cmd_massflow_*` 冒充 `actual_massflow_*`。

2. 喷气区覆盖不足

真实单喷气 case 只有 JET_01。24 个喷气区中只有 1 个有真实响应，缺少 S2/S3 区域、相邻喷口、远端喷口和面积差异样本，因此无法识别空间影响矩阵。

3. 激励方式不足

现有 JET_01 是从第一个样本开始的 3 秒常值开启。没有喷前基准段、没有关断恢复段、没有脉冲/PRBS/chirp，所以延迟、恢复时间和动态阶次只能低置信估计。

4. 基准波动需要重复确认

以 `Fz_S1R` 为例，JET_01 相对 no-jet 的平均变化约 `+1215 N`、峰值约 `+4577 N`，但 no-jet 同区域全时段标准差约 `3915 N`、峰峰值约 `59105 N`。需要 no-jet repeat 和 JET_01 repeat 区分真实喷气响应与 run-to-run/瞬态波动。

5. 报告量和积分面口径仍需固定

现有数据已有六个底部区域力、总 Fz、阻力、俯仰/滚转力矩、喷气口 `Jet_Reaction_Z`。但后续正式 ROM 数据还需要固定 STAR monitor 命名、单位、正方向、积分面覆盖、是否包含 tail，以及 `Jet_Reaction_Z` 在 no-jet 壁面状态下的物理含义。

6. Mock ARX 不能作为真实有效性证据

第二周 ARX 只保留为 Mock 回归测试。Mock 数据训练出的高准确率只能证明代码可运行，不能证明真实喷气控制有效，也不能作为真实 ROM 训练已成熟的验收证据。

## 下一批必须补的内容

- 报告量：`actual_massflow_01..24` 或明确等价的 STAR 实际质量流量 monitor；保留 `cmd_massflow_01..24`；继续导出六区 Fz、`Fz_Total`、`Drag_Total`、`Pitch_Moment`、`Roll_Moment`、`Jet_Reaction_Z`。
- 时间长度：每个 case 至少 3.0 s；pulse case 需要包含喷前、开启、关断恢复三个阶段。
- 喷气区：至少补 JET_02、JET_11、JET_24，再加 JET_01 repeat；它们分别覆盖相邻 S1、中部 S2、远端 S3 和面积/质量通量差异。
- 激励方式：至少 step repeat 和 pulse；在这些通过后再进入 PRBS 或 chirp。

下一批建议详见 `artifacts/reports/B07_next_case_suggestion.csv`。
