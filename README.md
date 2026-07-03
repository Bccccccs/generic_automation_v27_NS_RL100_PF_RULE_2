# generic_automation / maglev_sparse_jet_9w

本仓库包含两条主要工作线：

```text
generic_automation/   真实 STAR-CCM+ 自动化、求解器参数调优、在线监控和 RL
flow_control/         24 喷气区激励动作表生成、mock plant、本地数据链路验证
```

当前 B12 重点在 `flow_control/`：生成统一格式的喷气动作输入表，
并可把动作输入送入本地 mock plant 做 24 输入、6 输出的快速验证。

## 1. 快速开始

本地 `flow_control` 示例和测试不需要 STAR-CCM+。

```bash
git clone <repo-url>
cd generic_automation_v27_NS_RL100_PF_RULE_2

python3 -m venv .venv
. .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -r requirements.txt

python -m pytest
```

如果当前环境已经有 `.venv`，可以直接使用：

```bash
.venv/bin/python -m pytest
```

## 2. 当前项目结构

```text
generic_automation/
  原 STAR-CCM+ 自动化主线：case、sweep、monitor、RL 调求解器参数

flow_control/
  喷气流控原型：激励动作表、mock plant、数据 schema、STAR 命令翻译

starccm_control/
  24 个喷气区、6 个载荷点、report 名称和结果映射的共用契约

starccm_runtime/
  STAR-CCM+ runtime 命令模型、macro builder、日志解析和结果收集

configs/
  YAML 配置文件

docs/
  项目说明、B02/B03/B04/B05 文档、week2 说明

tests/
  自动测试

runs/
  本地生成结果，通常不作为源码提交
```

## 3. B12 喷气激励动作表

B12 的目标是生成 CFD 实验输入表，而不是判断哪个喷气区最好，也不是做 MPC 控制。

核心输出是：

```text
actuation_schedule.csv
```

统一列名为：

```text
physical_time, window_id, t_start, t_end,
JET_01 ... JET_24,
cmd_massflow_01 ... cmd_massflow_24
```

含义：

```text
physical_time    当前窗口开始物理时间，单位秒
window_id        第几个喷气窗口，从 0 开始
t_start          窗口开始时间，单位秒
t_end            窗口结束时间，单位秒
JET_01..JET_24   喷气区开关，1 表示打开，0 表示关闭
cmd_massflow_*   对应喷气区的指令质量流量
```

强制规则：

```text
JET_xx = 0 时，cmd_massflow_xx 必须等于 0
JET_xx = 1 时，cmd_massflow_xx 必须大于 0
physical_time、t_start、t_end 都是物理时间秒，不是求解器迭代步
```

## 4. 六种激励动作

当前支持 6 种动作，配置文件都在 `configs/`：

| 动作 | 配置文件 | 作用 |
|---|---|---|
| 无喷气参考段 | `configs/no_jet_reference.yaml` | 所有喷口关闭，作为无喷气基准 |
| 单喷气区脉冲 | `configs/pulse_singlejet.yaml` | 一个喷口短时间打开，观察瞬时响应和延迟 |
| 单喷气区阶跃 | `configs/step_singlejet.yaml` | 一个喷口持续打开一段时间，观察平均影响 |
| 关键喷气区扫频 | `configs/chirp_keyjets.yaml` | 几个喷口的喷气量按越来越快的节奏变化 |
| PRBS 伪随机开关 | `configs/prbs_demo.yaml` | 可复现随机开关，为 ROM/动态辨识提供输入 |
| 稀疏随机分组 | `configs/pilot_sparse24.yaml` | 每个主窗口开 3 个喷口，公平筛查 24 个喷气区 |

生成某一种动作表：

```bash
.venv/bin/python -m flow_control.schedule_generator --config configs/pulse_singlejet.yaml
```

生成结果默认写到：

```text
runs/schedule_examples/<动作名>/
```

例如：

```text
runs/schedule_examples/pulse_singlejet/actuation_schedule.csv
runs/schedule_examples/prbs_demo/actuation_schedule.csv
runs/schedule_examples/sparse24/actuation_schedule.csv
```

每个示例目录通常包含：

```text
actuation_schedule.csv       主动作表
config_summary.yaml          配置摘要、随机种子、统计量和校验结果
validation_report.json       校验结果
actuation_heatmap.svg        喷气开关热图
total_mass_flow.csv          每个窗口总质量流量
total_mass_flow_curve.svg    总质量流量曲线
```

## 5. flow_control 代码逻辑

只生成动作表时，入口是：

```text
flow_control/schedule_generator.py
```

调用链：

```text
configs/*.yaml
  -> schedule_generator.py
  -> excitation_patterns/common.py 读取统一配置
  -> 根据 actuation.mode 分发到具体生成器
  -> excitation_patterns/pulse.py / step.py / chirp.py / prbs.py / sparse_groups.py
  -> common.py 统一写 actuation_schedule.csv、summary、图和 validation
```

具体动作生成器：

```text
flow_control/excitation_patterns/reference.py       无喷气参考段
flow_control/excitation_patterns/pulse.py           单喷气区脉冲
flow_control/excitation_patterns/step.py            单喷气区阶跃
flow_control/excitation_patterns/chirp.py           关键喷气区扫频
flow_control/excitation_patterns/prbs.py            PRBS 伪随机开关
flow_control/excitation_patterns/sparse_groups.py   稀疏随机分组
flow_control/excitation_patterns/common.py          共用配置、表格、校验和输出
```

## 6. 生成动作并运行 mock plant

如果想生成动作后立刻送入本地 mock plant，入口是：

```text
flow_control/run_mock_demo.py
```

示例：

```bash
.venv/bin/python -m flow_control.run_mock_demo \
  --config configs/prbs_demo.yaml \
  --output-dir runs/mock_examples/prbs_demo
```

调用链：

```text
configs/*.yaml
  -> run_mock_demo.py
  -> actuation_workflow.py 生成动作并转成 24 x T 输入矩阵
  -> mock_rollout.py 运行 MockPlant
  -> mock_case_bundle.py 写 mock_inputs、mock_outputs、timeseries 和图
```

mock 输出通常包括：

```text
actuation_schedule.csv
mock_inputs.csv
mock_outputs.csv
mock_input_output_correlations.csv
mock_hidden_jet_influence_ranking.csv
mock_input_heatmap.svg
mock_output_timeseries.svg
timeseries.csv
case_manifest.yaml
quality_report.json
mock_demo_summary.json
```

注意：mock plant 是本地虚拟模型，用于验证数据链路，不代表真实 STAR-CCM+ 结果。

## 7. 运行测试

推荐从仓库根目录运行：

```bash
.venv/bin/python -m pytest -q
```

只测 flow_control 相关内容：

```bash
.venv/bin/python -m pytest -q \
  tests/test_actuation_schedule_generator.py \
  tests/test_mock_plant_contract.py \
  tests/test_flow_control_smoke.py \
  tests/test_case_schema.py \
  tests/test_starccm_runtime_translators.py
```

## 8. STAR-CCM+ 自动化主线

`generic_automation/` 仍然保留原来的 STAR-CCM+ 自动化能力。

运行单个 case：

```bash
python ga.py case --config configs/config.yaml
```

运行构建匹配的 RL case：

```bash
python ga.py case --config configs/config_rl_build_amg_match_mesh.yaml
```

运行在线 monitor：

```bash
python ga.py monitor --config configs/config.yaml
```

运行 sweep：

```bash
python ga.py sweep --config configs/config.yaml --cases cases/cases.csv
```

这些命令需要可用的 STAR-CCM+ 安装和有效的 `.sim` 文件路径。

## 9. 运行模式

case 配置支持：

```text
full_run    从 template .sim 开始，设置、网格、求解、导出
mesh_only   只生成网格并保存 mesh-ready .sim
solve_only  打开已有 mesh-ready .sim 并求解
resume      打开 checkpoint .sim 继续求解
```

示例：

```yaml
case:
  run_mode: solve_only
  input_sim: /path/to/case_mesh_ready_abc123.sim
  checkpoint_interval: 250
```

也可以从 CLI 覆盖：

```bash
python ga.py case \
  --config configs/config.yaml \
  --run-mode solve_only \
  --input-sim /path/to/mesh_ready.sim
```

## 10. 重要文档

```text
docs/week2/B02_喷气激励数据字典.md
  详细解释 actuation_schedule.csv、summary、图和每种激励的含义

docs/B02_喷气激励信号说明.md
  解释六种喷气激励信号为什么要生成

docs/B03_SPARSE_RANDOM_SCHEDULE_GENERATOR.md
  稀疏随机分组 sparse24 的约束和生成逻辑

docs/B04_MOCK_PLANT.md
  mock plant 说明

docs/B05_CASE_SCHEMA.md
  标准 case bundle / timeseries schema

docs/week2/STARCCM_RUNTIME_REFACTOR.md
  STAR-CCM+ runtime 分层和翻译层说明
```

## 11. 当前边界

当前 `flow_control` 已经可以：

```text
生成 6 类喷气动作表
把动作表送入本地 mock plant
输出统一格式的 actuation_schedule.csv
输出标准 case bundle
把喷气动作翻译成 STAR-CCM+ runtime 命令计划
```

当前还没有完成：

```text
一键启动 STAR-CCM+ 并逐窗口真实修改喷口边界条件
真实 CFD 喷气闭环控制
喷气 RL / MPC 控制器
```

也就是说，现在 B12 交付的是喷气实验输入生成和本地 mock 验证链路；
真实 STAR-CCM+ 喷口边界接入是下一步工作。
