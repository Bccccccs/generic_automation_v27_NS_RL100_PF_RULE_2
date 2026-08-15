# J02 短时 STAR 联调样例

此样例以 `solver_dt_s=0.001` 运行 8 个时间步：J02 在 `[0.002, 0.006] s` 以 `0.015 kg/s` 开启，其余窗口和其余 23 个 J 喷口均为零。

先用 `dry-run` 生成 `raw_star/FlowControlRunMacro.java`；该模式不会启动 STAR。宏为每个 `J01..J24` 建立或复用 `actual_massflow_NN` 的 `MassFlowReport`，每个窗口均显式设置所有 24 路质量流量，并在窗口末端输出 `solver_dt_s`、`action_window_s`、`sample_interval_s`、命令质量流量和实际质量流量。

将 `template.sim` 替换为浩坤冻结的 `.sim` 后，以 `execution_mode="run"` 运行同一配置。随后用 `execution_mode="package-only"` 对已有 `flow_control_timeseries.csv` 一次完成标准 Case 整理和完整性校验。`execution_mode="validate-only"` 仅保留给已整理 Case 的单独复查；这两种模式都不会启动 STAR。`B02_massflow_alignment_check.csv` 在真实求解完成前保持 `NOT_RUN`，不能作为实际质量流量证据。
