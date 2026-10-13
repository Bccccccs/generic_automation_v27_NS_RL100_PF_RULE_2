# Project Module Overview

本文档梳理当前项目的主要执行链路与各模块职责。项目整体是一个面向 STAR-CCM+ 的自动化仿真系统，包含单算例运行、网格/求解分阶段执行、在线强化学习调参、外部监控、profiling 输出和离线回放分析。

## 1. Overall Workflow

主流程从 `ga.py case` 或 `generic_automation.cli.run_case` 开始：

1. 读取 YAML/JSON 配置文件。
2. 通过 `project_config.py` 解析并规范化为统一的 `Case` 对象。
3. 创建或更新 `run_context.json`。
4. 当 `ai_optimization.enabled=true` 时，启动内嵌 RL monitor。
5. 通过 `StarCCMAdapter` 生成 `AutoSetupMacro.java`。
6. 调用 STAR-CCM+ 执行仿真。
7. 仿真过程中 monitor 持续读取 STAR 日志，并按固定迭代间隔触发 RL 决策。
8. RL 决策通过文件握手机制传递给 STAR-CCM+ Java 宏。
9. 仿真结束后收集 report、profiling、RL action/observation、summary 等结果。

支持的运行模式：

- `full_run`: 从模板 `.sim` 开始，设置边界/网格/求解器，生成网格，运行求解并导出结果。
- `mesh_only`: 从模板 `.sim` 开始，只生成网格并保存 mesh-ready `.sim`。
- `solve_only`: 打开已有 mesh-ready `.sim`，只配置求解器/report/RL 并求解。
- `resume`: 打开 checkpoint `.sim`，刷新运行时控制并继续求解。

## 2. Configuration And Case Model

### `adapter_base.py`

定义核心数据模型 `Case`。它是项目中最重要的参数载体，包含：

- 基础工况：入口速度、温度、出口压力等。
- 网格参数：base mesh、surface mesh、prism layer、局部 volume/surface controls。
- STAR-CCM+ 路径与资源：`starccm_path`、`template_sim`、`num_cores`、`pod_key`。
- 边界与区域名称：region、inlet、outlet、wall、ground、symmetry。
- 求解器参数：最大迭代数、残差目标、压力/速度 relaxation ramp、AMG cycle 等。
- RL 运行时可调参数。
- report 名称与输出配置。
- run mode、input sim、checkpoint 等运行控制字段。

### `project_config.py`

负责读取和规范化配置：

- 支持 YAML 和 JSON。
- 校验必填工况字段。
- 将顶层配置和 `case:` 配置合并成 `Case`。
- 处理默认值、类型转换、布尔值解析和列表解析。
- 规范化 report 名称。
- 生成 volume mesh controls 和 surface mesh controls。
- 解析 `run_mode`、`input_sim`、`mesh_cache_key`、`checkpoint_interval`。
- 校验部分网格参数是否合理。

### `configs/config.yaml` / `configs/config_rl_build_amg_match_mesh.yaml` / phase configs

这些文件是运行配置入口，定义 STAR 路径、模板文件、工况、网格参数、求解器参数、RL 参数、安全约束和触发间隔。

## 3. Entrypoints

### `ga.py case` / `generic_automation.cli.run_case`

单算例主入口。主要职责：

- 加载配置。
- 解析 `Case`。
- 创建结果目录。
- 创建 run context。
- 根据配置启动内嵌 `AIParameterGenerator`。
- 调用 `SimulationAdapter` 执行仿真。
- 结束时停止 monitor，并更新 run context 状态。

命令示例：

```bash
python ga.py case --config configs/config.yaml
python ga.py case --config configs/config.yaml --run-mode solve_only --input-sim /path/to/mesh_ready.sim
python ga.py case --config configs/config.yaml --no-monitor
```

### `ga.py monitor` / `generic_automation.cli.run_monitor_only`

外部监控入口。适合 STAR-CCM+ 已经由 SLURM 或其他进程启动时，单独运行 Python monitor。

职责：

- 读取同一份配置。
- 定位或覆盖 case_dir。
- 创建 `AICaseModifier` 和 `AIParameterGenerator`。
- 监听 `logs/starccm.log`。
- 检测 `sim_done.flag` 后退出。
- 可在 `ai_optimization.enabled=false` 时进入 observe-only/profiling-only 模式。

### `ga.py sweep` / `generic_automation.cli.run_sweep`

CSV 参数扫描入口：

- 读取基础配置。
- 读取 `cases/cases.csv`。
- 为每个 case 生成独立 JSON 配置。
- 调用 `generic_automation.cli.run_case` 顺序运行。

### `ga.py replay` / `generic_automation.cli.offline_replay`

离线回放工具：

- 读取已有 profiling timeseries 和 action logs。
- 不启动 STAR-CCM+。
- 用历史数据重放 RL 决策。
- 可用于比较历史动作与当前策略，或重新训练 Q 表。

## 4. STAR-CCM+ Adapter And Macro

### `simulation_adapter.py`

适配器门面。目前只支持 `starccm` backend。它包装 `StarCCMAdapter`，为未来增加其他仿真后端保留接口。

### `starccm_adapter.py`

STAR-CCM+ 执行适配器。主要职责：

- 根据 run mode 解析输入 `.sim`。
- 计算或补充 mesh cache key。
- 生成 `AutoSetupMacro.java`。
- 构造 STAR-CCM+ 命令行。
- 将 STAR 输出写入 `logs/starccm.log`。
- 运行失败时返回日志尾部便于排错。
- 成功后收集 report、写输出并清理临时宏。

### `starccm_macro_builder.py`

负责把 `Case` 参数注入 Java 模板：

- 读取 `starccm_macro_template.java`。
- 替换 `{{PLACEHOLDER}}`。
- 生成 mesh-ready/result/checkpoint `.sim` 文件路径。
- 注入 RL 握手文件路径。
- 注入额外 volume/surface mesh control 更新代码。

### `starccm_macro_template.java`

STAR-CCM+ 端真正执行操作的 Java 宏。主要能力：

- 设置边界条件。
- 设置全局网格参数。
- 设置局部 volume/surface mesh controls。
- 设置 train surface mesh。
- 设置 solver 参数。
- 设置 pressure/velocity relaxation ramp。
- 设置 pressure/velocity AMG cycle 和部分 AMG 数值参数。
- 支持 `full_run`、`mesh_only`、`solve_only`、`resume`。
- 读取 `rl/param_update.json`。
- 应用 Python monitor 发出的运行时参数变更。
- 写 `rl/action_ack_events.jsonl` 和 `rl/param_update_ack.json`。
- 写 report、solver profiling、summary。
- 保存 mesh-ready、solver-init、result、checkpoint `.sim`。

## 5. Online RL Monitor

### `ai_parameter_generator.py`

在线 monitor 主体。职责：

- 持续读取 `logs/starccm.log`。
- 通过 `StarCCMLogReader` 解析新增迭代行。
- 维护 observation window。
- 写 observation stream。
- 写 profiling timeseries JSONL/CSV。
- 根据 `RLTrigger` 判断是否触发决策。
- 调用 `RLRuntimeService` 执行 RL 更新。
- 写 action events。
- 消费 STAR-CCM+ 侧写回的 ack events。
- 停止时写 `experiment_summary.json` 和 profiling summary。

### `ai_case_modifier.py`

参数变更过滤与握手文件写入层。职责：

- 限制可修改参数白名单。
- 按 steady/transient 区分可调参数。
- 对参数做类型转换。
- 根据配置约束做 min/max clamp。
- 跳过与当前值相同的修改。
- 写 `rl/param_update.json`。
- 写 `rl/pending_action.json`。
- 追加 `rl/ai_update_history.jsonl`。
- 读取 STAR-CCM+ ack events。

### `rl_runtime_service.py`

RL 运行时编排层。它连接 controller、modifier、安全规则和 monitor：

- 检查是否还有 pending action 未被 STAR ack。
- 调用 `ReinforcementLearningController` 获取动作。
- 应用 catastrophic safety override。
- 应用 pressure relaxation instability block。
- 应用 manual intervention rules。
- 根据 `intervention_enabled` 决定是否真正写参数更新。
- 同步 `Case` 中的当前运行时参数。
- 记录 action 是否成功、是否 pending、是否被安全规则阻断。

### `rl_trigger.py`

RL 触发器。目前实际触发逻辑以固定迭代间隔为主：

- 等待 `rl_start_iteration`。
- 按 `decision_interval_iterations` 划分 bucket。
- 每个 bucket 最多触发一次。

配置中的 output metrics 会被解析，但当前触发并不依赖复杂的停滞/恶化判断。

## 6. Reinforcement Learning Controller

### `rl_controller.py`

Q-learning 控制器。主要职责：

- 根据 observation window 构造状态。
- 根据 epsilon-greedy 选择动作。
- 根据上一动作执行后的效果更新 Q 表。
- 计算 reward。
- 保存 `rl/rl_controller_state.json`。
- 写 `rl/rl_controller_trace.jsonl`。

动作包括：

- `hold`
- `pressure_relaxation_up`
- `pressure_relaxation_down`
- `pressure_relaxation_initial_value_up`
- `pressure_relaxation_initial_value_down`
- `pressure_relaxation_end_iteration_up`
- `pressure_relaxation_end_iteration_down`
- `pressure_amg_cycle_v`
- `pressure_amg_cycle_w`
- `velocity_amg_cycle_flex`
- `velocity_amg_cycle_v`

### `rl_action_space.py`

定义动作空间和动作到参数变更的映射：

- 判断当前参数在约束下哪些动作有效。
- 对 pressure relaxation factor 做上下调。
- 对 pressure relaxation initial value 做上下调。
- 对 pressure relaxation end iteration 做上下调。
- 对 pressure AMG cycle 在 V/W 之间切换。
- 对 velocity AMG cycle 在 Flex/V 之间切换。
- 根据 instability guard 过滤不安全动作。

### `rl_controller_settings.py`

从配置中解析 RL 超参数：

- learning rate
- discount factor
- epsilon / epsilon decay / min epsilon
- relaxation step
- observation window
- allowed parameters
- allowed actions
- reward baseline
- physics gate
- instability guard 阈值

### `rl_runtime_registry.py`

定义当前 RL 运行时安全可调参数集合：

- `pressure_relaxation_factor`
- `pressure_relaxation_initial_value`
- `pressure_relaxation_end_iteration`
- `pressure_amg_cycle`
- `velocity_amg_cycle`

## 7. Safety And Manual Rules

### `rl_safety_override.py`

灾难性安全保护和压力松弛上调保护：

- 残差超过上限时触发 recovery。
- 残差增长过快时触发 recovery。
- 压力过高或压力振幅过大时触发 recovery。
- turbulent viscosity limited cells 过多时触发 recovery。
- 可选择 trip 后暂停或 cooldown。
- 在不稳定时阻止 `pressure_relaxation_factor` 上调。

### `rl_manual_rules.py`

人工规则层，用于把工程经验叠加到 RL 提案上：

- 启动阶段限制压力松弛参数。
- 启动阶段可阻止 velocity cycle 变化。
- pressure stress 时强制更保守参数。
- TKE rebound 时进入恢复策略。
- 稳定阶段可推动 velocity/pressure AMG cycle 到加速组合。

## 8. Log Parsing And Metrics

### `starccm_log_reader.py`

STAR 日志增量读取器。它解析：

- 原生 iteration table。
- residual columns。
- report columns。
- total solver CPU time。
- turbulent viscosity limited cells。
- solver iterations。
- AMG cycles。
- 每行 observation 的时间增量和派生指标。

### `starccm_log_parser.py`

日志后处理工具：

- 一次性读取完整 STAR 日志。
- 提取 mesh cell count。
- 诊断 solver metric 解析覆盖情况。
- 为 profiling summary 提供辅助信息。

### `residual_metrics.py`

残差诊断工具：

- 计算 residual log slope。
- 判断 rebound。
- 判断 oscillation。
- 判断 stagnation。
- 推断 relaxation scheme。

### `rl_reward_components.py`

reward 组成工具：

- 根据残差下降和 CPU 时间计算 speed score。
- 计算 stagnation/divergence/oscillation penalty。
- 计算参数频繁变化 penalty。
- 根据 total force 偏离 baseline 计算 physics drift penalty。
- 计算 convergence bonus。
- 维护不同残差阶段的自适应速度 baseline。

### `rl_observation_state.py`

负责将 observation window 转换成 RL 状态：

- 汇总最近残差、阻力、压力、total force。
- 计算参数 bucket。
- 计算决策区间 chunk metrics。
- 生成离散 state key。

### `rl_controller_utils.py`

RL 通用工具：

- clip。
- bucketize。
- 参数变化比较。
- allowed parameters/actions 解析。
- stage baseline 解析。

## 9. Outputs And Result Files

### `ai_monitor_outputs.py`

集中定义输出文件名与输出记录结构：

- `logs/starccm.log`
- `rl/rl_observation_stream.jsonl`
- `rl/rl_action_events.jsonl`
- `rl/observations.jsonl`
- `rl/actions.jsonl`
- `profiling/profiling_timeseries.jsonl`
- `profiling/profiling_timeseries.csv`
- `profiling/profiling_actions.jsonl`
- `profiling/profiling_summary.json`
- `experiment_summary.json`

同时负责构建 observation record、action event record、profiling timeseries record 和 summary。

### `starccm_result_files.py`

仿真结束后的结果收集：

- 读取 `result_reports.csv`。
- 从 STAR iteration table 最后一行回填 report。
- 写 canonical report CSV。
- 调用 solver profiling finalize。
- 清理临时 `AutoSetupMacro.java`。

### `starccm_solver_profiling.py`

solver profiling 后处理：

- 读取 `profiling/solver_profiling.csv`。
- 用 STAR 日志回填 residual、solver iterations、AMG cycles。
- 回填 mesh cell count。
- 写 `profiling/solver_profiling_summary.json`。
- 记录各字段是否可用、是否未观测到、是否当前 solver 不暴露。

### `runtime_metadata.py`

运行协议与元数据工具：

- 定义 protocol version。
- 定义 RL 握手文件名。
- 创建和更新 `run_context.json`。
- 计算 mesh cache key。
- 生成默认 mesh-ready/result/solver-init/checkpoint `.sim` 文件名。
- 提供 JSON/JSONL 读写工具。

## 10. Other Utilities

### `runtime_value_utils.py`

通用值转换工具：

- safe float/int。
- bool 解析。
- 数值 rounding。
- CSV scalar 转换。
- 均值等基础工具。

### `ga.py force-update` / `generic_automation.cli.force_param_update`

用于手动写入参数更新文件，通常用于调试 RL 文件握手或强制 STAR 宏应用某个参数。

### `scripts/pipelines/run_full_pipeline.sh`

Shell 级完整 pipeline 启动脚本，适合将 mesh/solve/monitor 等步骤串起来运行。

### `cases/cases.csv`

参数扫描输入文件，由 `ga.py sweep` 或 `generic_automation.cli.run_sweep` 读取。

## 11. Runtime File Protocol

Python monitor 与 STAR-CCM+ Java 宏之间通过文件通信：

- Python 写：`rl/param_update.json`
- Python 写：`rl/pending_action.json`
- STAR 读：`rl/param_update.json`
- STAR 写：`rl/param_update_ack.json`
- STAR 追加：`rl/action_ack_events.jsonl`
- Python 读：`rl/action_ack_events.jsonl`

这个协议保证：

- 同一时间只有一个 pending action。
- STAR 是否消费动作可被 Python monitor 感知。
- action 成功、失败、部分应用或被忽略都有记录。
- RL 控制器只在上一动作被 ack 后继续提出新动作。

## 12. Important Design Notes

- 当前项目只支持 `starccm` backend。
- 当前 RL 控制器不是 LLM 调参，而是 Q-learning。
- RL 默认只调安全运行时参数，不改几何和网格参数。
- mesh 参数变更通常需要重新网格或重启，因此不在运行时白名单内。
- `ai_optimization.enabled=false` 时，独立 monitor 可退化为 observe-only/profiling-only。
- `intervention_enabled=false` 时，RL 可以观察和记录建议，但不会写入 STAR 参数更新。
- `solve_only` 和 `resume` 必须提供可用的 `input_sim`，或 case_dir/sims 中已有匹配 mesh-ready 文件。
