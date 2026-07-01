# 项目运行流程说明

本文基于当前目录结构、`docs/PROJECT_STRUCTURE_REFACTOR_GUIDE.md`、`docs/STRUCTURE_OPTIMIZATION_ROUND2.md`、`docs/project_module_overview.md`、`docs/B03_SPARSE_RANDOM_SCHEDULE_GENERATOR.md`、`docs/B04_MOCK_PLANT.md` 和 `docs/B05_CASE_SCHEMA.md`，说明项目结构调整之后，项目到底如何运行起来，以及每一步使用哪些模块。

## 1. 总体结构

当前项目不是单一脚本工程，而是拆成两条主要技术线：

1. `generic_automation/`：真实 STAR-CCM+ 自动化仿真与 RL 求解器参数调优主线。
2. `flow_control/`：喷气流控原型主线，用于生成 24 路喷气调度、运行 mock plant、统一数据格式。

当前推荐入口是：

```bash
python ga.py <command> [args]
```

其中：

```text
ga.py case          # 跑单个 STAR-CCM+ CFD case
ga.py sweep         # 批量跑 CSV 参数扫描
ga.py monitor       # 单独启动外部 monitor
ga.py replay        # 离线回放 RL 决策
ga.py force-update  # 手动写一次参数更新
```

项目结构可以概括为：

```text
.
├── ga.py
├── generic_automation/
│   ├── core/
│   ├── adapters/
│   ├── starccm/
│   ├── monitor/
│   ├── rl/
│   └── cli/
├── flow_control/
├── scripts/
│   ├── entrypoints/
│   └── pipelines/
├── configs/
├── cases/
├── docs/
├── tests/
├── examples/
├── runs/
├── results/
├── results_validation/
├── old_structure/
└── archive/
```

## 2. 两条主线的区别

| 主线 | 目录 | 是否启动 STAR-CCM+ | 当前控制对象 | 主要输出目录 |
|---|---|---:|---|---|
| 真实 CFD / RL 求解器调参 | `generic_automation/` | 是 | STAR-CCM+ 求解器参数 | `results/`、`results_validation/` |
| 喷气流控原型 | `flow_control/` | 否 | `JET_01` 到 `JET_24` 喷气输入 | `runs/` |

一句话理解：

> `generic_automation/` 负责把真实 STAR-CCM+ CFD 跑起来，并让 RL 动态调整求解器数值参数；`flow_control/` 负责喷气控制的前置原型，包括喷气调度生成、mock plant、本地数据链路和统一 case schema。

当前两条线还没有完全接起来。未来真正做喷气控制时，需要把 `flow_control/` 中的 `JET_01...JET_24` 动作接入 STAR-CCM+ Java macro 的喷口边界条件。

## 3. 真实 CFD / RL 求参数如何运行

### 3.1 推荐启动方式

单个 CFD case 推荐使用：

```bash
python ga.py case --config configs/config.yaml
```

等价包入口：

```bash
python -m generic_automation.cli.run_case --config configs/config.yaml
```

兼容旧脚本入口：

```bash
python scripts/entrypoints/run_case.py --config configs/config.yaml
```

如果不想启动 RL monitor，可以加：

```bash
python ga.py case --config configs/config.yaml --no-monitor
```

### 3.2 单 case 的模块调用顺序

| 顺序 | 模块 | 作用 |
|---:|---|---|
| 1 | `ga.py` | 统一命令入口，识别 `case` 子命令 |
| 2 | `generic_automation/cli/run_case.py` | 单算例主入口，解析 CLI 参数 |
| 3 | `generic_automation/core/project_config.py` | 读取 YAML/JSON 配置，生成 `Case` 对象 |
| 4 | `generic_automation/core/runtime_metadata.py` | 创建或更新 `run_context.json` |
| 5 | `generic_automation/monitor/ai_parameter_generator.py` | 若 RL 开启，启动后台 monitor 线程 |
| 6 | `generic_automation/adapters/simulation_adapter.py` | 选择仿真后端，目前只支持 `starccm` |
| 7 | `generic_automation/adapters/starccm_adapter.py` | 解析 `.sim` 输入、生成宏、启动 STAR-CCM+ |
| 8 | `generic_automation/starccm/starccm_macro_builder.py` | 把 `Case` 参数注入 Java macro 模板 |
| 9 | `generic_automation/starccm/starccm_macro_template.java` | STAR-CCM+ 内部执行的 Java macro |
| 10 | STAR-CCM+ | 建网格、设置求解器、运行 CFD |
| 11 | `generic_automation/starccm/starccm_log_reader.py` | 增量读取 `logs/starccm.log` |
| 12 | `generic_automation/rl/rl_observation_state.py` | 将日志数据转成 RL observation/state |
| 13 | `generic_automation/rl/rl_controller.py` | Q-learning 控制器选择动作 |
| 14 | `generic_automation/rl/rl_safety_override.py` | 判断动作是否可能导致数值发散或压力异常 |
| 15 | `generic_automation/rl/rl_runtime_service.py` | 编排 controller、safety、modifier 和 ack 状态 |
| 16 | `generic_automation/monitor/ai_case_modifier.py` | 写入 `rl/param_update.json` 和 `rl/pending_action.json` |
| 17 | STAR Java macro | 读取参数更新，应用到 STAR-CCM+ 求解器 |
| 18 | `generic_automation/starccm/starccm_result_files.py` | 仿真结束后收集 report 和结果文件 |

### 3.3 主流程图

```text
python ga.py case --config configs/config.yaml
  ↓
ga.py
  ↓
generic_automation.cli.run_case
  ↓
project_config.py
  ↓
Case 对象
  ↓
runtime_metadata.py
  ↓
run_context.json
  ↓
AIParameterGenerator 启动 monitor
  ↓
SimulationAdapter
  ↓
StarCCMAdapter
  ↓
starccm_macro_builder.py
  ↓
AutoSetupMacro.java
  ↓
STAR-CCM+
  ↓
logs/starccm.log
  ↓
StarCCMLogReader
  ↓
rl_observation_state.py
  ↓
rl_controller.py
  ↓
rl_safety_override.py
  ↓
ai_case_modifier.py
  ↓
rl/param_update.json
  ↓
STAR Java macro 读取并应用
  ↓
result / profiling / rl logs
```

### 3.4 RL 闭环如何工作

仿真运行中，STAR-CCM+ 会持续输出：

```text
<case_dir>/logs/starccm.log
```

Python monitor 做以下事情：

```text
读取 starccm.log
  ↓
解析 iteration、residual、drag、pressure、CPU time
  ↓
构造 RL observation/state
  ↓
RL controller 选择动作
  ↓
safety 检查动作
  ↓
写 rl/param_update.json
  ↓
STAR Java macro 读取动作
  ↓
应用到求解器参数
  ↓
STAR 写 ack
  ↓
Python 读取 ack，更新上一动作是否生效
```

这个闭环当前调的是 **CFD 求解器参数**，不是喷气参数。

当前 RL 主要控制：

```text
pressure_relaxation_factor
pressure_relaxation_initial_value
pressure_relaxation_end_iteration
pressure_amg_cycle
velocity_amg_cycle
```

这些参数的作用是帮助 STAR-CCM+ 更稳定、更快地求解复杂流体方程组，而不是直接改变流场边界条件。

### 3.5 STAR-CCM+ 如何被启动

`generic_automation/adapters/starccm_adapter.py` 会构造类似命令：

```bash
starccm_path -np num_cores -batch AutoSetupMacro.java input.sim
```

其中：

| 参数 | 来源 |
|---|---|
| `starccm_path` | `configs/config.yaml` |
| `num_cores` | `configs/config.yaml` |
| `AutoSetupMacro.java` | `starccm_macro_builder.py` 自动生成 |
| `input.sim` | `template_sim` 或 `input_sim` |

不同 `run_mode` 的输入逻辑：

| run mode | 输入 `.sim` | 作用 |
|---|---|---|
| `full_run` | `template_sim` | 从模板开始，建网格并求解 |
| `mesh_only` | `template_sim` | 只生成 mesh-ready `.sim` |
| `solve_only` | `input_sim` | 打开已有 mesh-ready `.sim` 继续求解 |
| `resume` | `input_sim` 或 checkpoint | 从已有 checkpoint 继续求解 |

## 4. 集群 pipeline 如何运行

集群 pipeline 入口：

```bash
bash scripts/pipelines/run_full_pipeline.sh configs/config.yaml
```

这个脚本把真实 STAR 求解和 monitor 分成两部分：

| 位置 | 做什么 | 使用模块 |
|---|---|---|
| SLURM 计算节点 | 启动 STAR-CCM+，但不启动内嵌 monitor | `python -m generic_automation.cli.run_case --no-monitor` |
| 登录节点或外部进程 | 单独启动 monitor，监听 STAR 日志 | `scripts/entrypoints/run_monitor_only.py` |

其核心思路：

```text
run_full_pipeline.sh
  ↓
提交 SLURM job
  ↓
SLURM 内部运行 run_case --no-monitor
  ↓
STAR-CCM+ 在计算节点运行
  ↓
登录节点启动 run_monitor_only.py
  ↓
monitor 读取同一个 case_dir/logs/starccm.log
  ↓
看到 sim_done.flag 后退出
```

外部 monitor 也可以手动启动：

```bash
python ga.py monitor --config configs/config.yaml
```

或者：

```bash
python scripts/entrypoints/run_monitor_only.py \
  --config configs/config.yaml \
  --case-dir results/<case_name>
```

`run_monitor_only.py` 会持续监听：

```text
<case_dir>/logs/starccm.log
```

并在检测到：

```text
<case_dir>/sim_done.flag
```

后停止。

## 5. 批量 CFD sweep 如何运行

启动命令：

```bash
python ga.py sweep --config configs/config.yaml --cases cases/cases.csv
```

模块顺序：

| 顺序 | 模块 | 作用 |
|---:|---|---|
| 1 | `ga.py` | 识别 `sweep` 命令 |
| 2 | `generic_automation/cli/run_sweep.py` | 读取基础配置和 CSV |
| 3 | `cases/cases.csv` | 提供多组 case 参数 |
| 4 | `case_config.json` | 为每个 case 生成独立配置 |
| 5 | `generic_automation.cli.run_case` | 对每一行 case 顺序运行单 case 流程 |

简化流程：

```text
run_sweep
  ↓
读取 cases/cases.csv
  ↓
合并 configs/config.yaml
  ↓
为每个 case 生成 case_config.json
  ↓
逐个调用 run_case
  ↓
每个 case 独立输出到 results/<case_name>/
```

## 6. 离线 replay 如何运行

离线 replay 不启动 STAR-CCM+，只读取历史 profiling/action log 来重放 RL 决策。

启动命令示例：

```bash
python ga.py replay \
  --config configs/config.yaml \
  --case-dir results_validation/validation_phase2_solve_only_iter10_cap233_20260610T034023Z
```

模块顺序：

| 顺序 | 模块 | 作用 |
|---:|---|---|
| 1 | `ga.py` | 识别 `replay` 命令 |
| 2 | `generic_automation/cli/offline_replay.py` | 离线回放主逻辑 |
| 3 | `profiling/profiling_timeseries.jsonl` 或 `.csv` | 历史 observation 数据 |
| 4 | `profiling/profiling_actions.jsonl` | 历史动作数据 |
| 5 | `generic_automation/rl/rl_controller.py` | 重建 RL controller 并重新预测动作 |
| 6 | replay 输出目录 | 写 comparison 和 summary |

作用：

```text
检查当前 RL 策略和历史动作是否一致
比较 recorded action 和 predicted action
分析 reward 差异
不启动 STAR-CCM+
```

## 7. flow_control 如何运行

`flow_control/` 是喷气流控原型，不直接启动 STAR-CCM+。它现在解决的是：

```text
24 路喷气输入如何生成
24 输入到 6 输出的数据链路如何跑通
mock 数据和未来真实 CFD 数据如何统一保存
```

### 7.1 B03：生成喷气调度

启动命令：

```bash
python -m flow_control.schedule_generator --config configs/pilot_sparse24.yaml
```

使用模块：

| 顺序 | 模块 | 作用 |
|---:|---|---|
| 1 | `flow_control/schedule_generator.py` | CLI 入口与调度生成主逻辑 |
| 2 | `ActuationConfig.from_yaml()` | 读取 `configs/pilot_sparse24.yaml` |
| 3 | `generate_actuation_matrix()` | 生成 24 路喷气矩阵 |
| 4 | `validate_actuation_matrix()` | 校验稀疏、均衡、无重复、连续开启约束 |
| 5 | `write_actuation_outputs()` | 写出调度、热图、统计和报告 |
| 6 | `flow_control/data_schema.py` | 同步写标准 case schema |

B03 的目标：

```text
生成 80 个控制窗口
其中 72 个 excitation window
8 个 no-jet reference window
每个 excitation window 只开启 3 个喷口
24 个喷口每个正好出现 9 次
不重复 3 喷口组合
不允许单个喷口连续开启超过 2 个窗口
```

输出目录：

```text
runs/pilot_sparse24/
```

主要输出：

```text
actuation_schedule.csv
actuation_heatmap.svg
activation_counts.csv
pairwise_cooccurrence.csv
input_correlation_matrix.csv
mass_flow.csv
validation_report.json
case_manifest.yaml
timeseries.csv
quality_report.json
```

这一阶段没有 CFD，也没有 RL，只是生成喷气输入表。

### 7.2 B04：运行 mock plant

启动命令：

```bash
python -m flow_control.run_mock_demo --config configs/pilot_sparse24.yaml
```

使用模块：

| 顺序 | 模块 | 作用 |
|---:|---|---|
| 1 | `flow_control/run_mock_demo.py` | mock demo 入口 |
| 2 | `ActuationConfig.from_mapping()` | 读取喷气调度配置 |
| 3 | `generate_actuation_matrix()` | 生成 24 路输入矩阵 |
| 4 | `flow_control/mock_plant.py` | 创建虚拟 CFD plant |
| 5 | `MockPlant.reset(seed)` | 初始化虚拟动力系统 |
| 6 | `MockPlant.step(u)` | 每个窗口输入 24 维喷气向量，输出 6 维响应 |
| 7 | `run_mock_demo.py` | 写输入热图、输出曲线、相关性、影响力排序 |
| 8 | `CaseSchema.write_case()` | 写标准 case 文件 |

B04 的核心数据链路：

```text
24 路喷气输入 u(t)
  ↓
MockPlant
  ↓
6 路输出 y(t)
```

这里的 6 路输出对应：

```text
Fz_S1L
Fz_S1R
Fz_S2L
Fz_S2R
Fz_S3L
Fz_S3R
```

输出目录：

```text
runs/b04_mock_plant/
```

主要输出：

```text
mock_input_heatmap.svg
mock_output_timeseries.svg
mock_inputs.csv
mock_outputs.csv
mock_input_output_correlations.csv
mock_hidden_jet_influence_ranking.csv
mock_demo_summary.json
case_manifest.yaml
actuation_schedule.csv
timeseries.csv
quality_report.json
logs/case_io.log
```

这一阶段仍然不启动 STAR-CCM+，它用于本地验证喷气控制数据链路。

### 7.3 B05：统一 case 数据格式

核心模块：

```text
flow_control/data_schema.py
```

B05 规定所有 flow-control run 都输出成统一结构：

```text
runs/<case_id>/
├── case_manifest.yaml
├── actuation_schedule.csv
├── timeseries.csv
├── quality_report.json
├── figures/
└── logs/
```

`timeseries.csv` 必填列：

```text
physical_time
window_id
JET_01 ... JET_24
Fz_S1L Fz_S1R Fz_S2L Fz_S2R Fz_S3L Fz_S3R
Fz_Total
Drag_Total
Pitch_Moment
Roll_Moment
Jet_Reaction_Z
solver_status
```

这个 schema 的作用是让以下数据可以统一保存和比较：

```text
mock plant 数据
真实 STAR-CCM+ 数据
未来 RL rollout 数据
```

## 8. 当前两个体系如何衔接

现在的状态是：

```text
generic_automation/
  已经可以跑真实 STAR-CCM+ 自动化
  已经可以在线读取日志
  已经可以通过文件握手写回求解器参数
  当前 RL 控制的是求解器参数

flow_control/
  已经可以生成 24 路喷气调度
  已经可以本地 mock 24 输入 -> 6 输出
  已经可以保存统一 case schema
  尚未接入真实 STAR-CCM+ 喷口边界条件
```

未来接起来时，大致需要：

```text
flow_control 生成或 RL 产生 JET_01...JET_24 动作
  ↓
写入类似 rl/param_update.json 的运行时控制文件
  ↓
STAR Java macro 读取喷气动作
  ↓
修改 STAR-CCM+ 中真实喷口边界条件
  ↓
STAR 输出载荷/阻力/压力响应
  ↓
映射到 CaseSchema 的 timeseries.csv
  ↓
RL 用真实 CFD 响应继续学习
```

也就是说，未来要把：

```text
当前求解器参数动作
```

替换或扩展为：

```text
JET_01 ... JET_24 喷气动作
```

并让 STAR macro 修改喷气边界，而不是只修改 pressure relaxation 或 AMG cycle。

## 9. 最重要的理解

当前真实 CFD/RL 主线中的 RL 不是直接求流场，也不是喷气控制器。它的角色是：

> CFD 求解器数值参数调优器。

CFD 本身是在求解复杂流体方程组。当前 RL 只是辅助 STAR-CCM+ 调整少量数值求解参数，例如压力松弛因子和 AMG cycle，让具体计算更稳定、更快收敛。

当前 flow_control 主线才是在为喷气控制做准备。它已经开始使用：

```text
JET_01 ... JET_24
```

作为喷气输入通道，但目前只接到了 mock plant，还没有接到真实 STAR-CCM+ 喷口边界条件。

因此可以把当前项目理解为：

```text
一套真实 CFD 自动化与求解器调参系统
  +
一套正在开发中的喷气流控原型系统
```

下一阶段的关键工作是把两者连起来。
