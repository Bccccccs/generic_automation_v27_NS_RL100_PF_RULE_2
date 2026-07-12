# generic_automation / maglev_sparse_jet_9w

本仓库包含两条主要工作线：

```text
generic_automation/   历史 STAR-CCM+ 自动化、求解器参数调优、在线监控和 RL
flow_control/         24 喷气区激励动作表生成、mock plant、本地数据链路验证
```

当前 B12 重点在 `flow_control/`：生成统一格式的喷气动作输入表，
并可把动作输入送入本地 mock plant 做 24 输入、6 输出的快速验证。
`generic_automation/` 中的 RL 代码只作为历史代码保留，不参与本周喷气控制结果。

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
  喷气流控原型：激励动作表、mock plant、ROM、数据 schema、STAR 命令翻译

starccm/
  control/：24 个喷气区、6 个载荷点、report 名称和结果映射的共用契约
  runtime/：STAR-CCM+ runtime 命令模型、macro builder、日志解析和结果收集

configs/
  YAML 配置文件

docs/
  项目说明、B02/B03/B04/B05/B06 文档、week2 说明

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

当前支持 6 种动作，配置文件都在 `configs/actions/`：

| 动作 | 配置文件 | 作用 |
|---|---|---|
| 无喷气参考段 | `configs/actions/no_jet_reference.yaml` | 所有喷口关闭，作为无喷气基准 |
| 单喷气区脉冲 | `configs/actions/pulse_singlejet.yaml` | 一个喷口短时间打开，观察瞬时响应和延迟 |
| 单喷气区阶跃 | `configs/actions/step_singlejet.yaml` | 一个喷口持续打开一段时间，观察平均影响 |
| 关键喷气区扫频 | `configs/actions/chirp_keyjets.yaml` | 几个喷口的喷气量按越来越快的节奏变化 |
| PRBS 伪随机开关 | `configs/actions/prbs_demo.yaml` | 可复现随机开关，为 ROM/动态辨识提供输入 |
| 稀疏随机分组 | `configs/actions/pilot_sparse24.yaml` | 每个主窗口开 3 个喷口，公平筛查 24 个喷气区 |

推荐通过 `examples` 中的启动脚本生成动作表。

选择一种动作，生成计划表并运行 mock：

```bash
bash examples/run_one_action.sh
```

脚本会提示选择动作，输入数字 `1` 到 `6` 即可。

输出写到：

```text
runs/mock_<动作名称>/
```

一次生成全部六种动作：

```bash
bash examples/run_all_actions.sh
```

生成结果固定写到 `runs/<动作名称>/input/`：

```text
runs/<动作名称>/input/
```

例如：

```text
runs/pulse_singlejet/input/actuation_schedule.csv
runs/prbs_demo/input/actuation_schedule.csv
runs/pilot_sparse24/input/actuation_schedule.csv
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

完整的六种动作命令见 [Generator 命令说明](flow_control/generator/COMMANDS.md)。

只生成动作表时，入口是：

```text
flow_control/generator/schedule_generator.py
```

调用链：

```text
configs/actions/*.yaml
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

## 6. 喷气控制数据链路

本周喷气控制结果按下面的数据链路理解：

```text
STAR / Mock
  -> 原始数据
  -> 数据标准化
  -> 降维
  -> ROM
  -> 控制器
  -> 喷气指令
```

| 箭头 | 传递的数据 | 单位 | 维度 |
|---|---|---|---|
| `STAR / Mock -> 原始数据` | STAR-CCM+ 导出的监视器时间序列，或 mock plant 生成的等价时间序列。典型字段包括物理时间、24 路喷气开关/质量流量、6 个载荷输出、全局力/力矩输出。 | 时间为 `s`；喷气开关无量纲；质量流量为 `kg/s`；力为 `N`；力矩为 `N*m`。 | 时间序列表，`N_t × N_col`。当前喷气输入通常是 24 路开关加 24 路质量流量，载荷输出通常是 6 路区域载荷加若干全局输出。 |
| `原始数据 -> 数据标准化` | 对不同来源的数据做统一列名、时间对齐、缺失检查、喷气指令和实际输出一致性检查后的标准 case 数据。 | 保留原始物理单位；若后续训练步骤显式归一化，则归一化后的训练矩阵为无量纲。 | 标准 case 表，仍是 `N_t × N_col`；schema 固定后，列含义稳定。 |
| `数据标准化 -> 降维` | 从标准 case 中抽取训练用状态和输入，例如喷气质量流量向量 `u(t)`、载荷输出 `y(t)`，或经过 PCA/POD/特征选择后的低维状态。 | 输入质量流量为 `kg/s`；载荷输出为 `N` 或 `N*m`；若做归一化/PCA/POD，低维系数通常无量纲。 | 高维状态可写作 `x(t) in R^N`；降维后为 `z(t) in R^r`，其中 `r << N`。当前 ARX ROM 主要使用 24 路喷气输入和若干载荷输出，不等同于完整流场 POD。 |
| `降维 -> ROM` | 低维状态、历史输入和历史输出，供 ROM 拟合或预测喷气-载荷响应。 | 若输入为物理量则保留 `kg/s`、`N`、`N*m`；若输入为标准化特征则无量纲。 | 单步状态 `z(t) in R^r`；ARX 训练样本通常由 `p` 阶历史输出和 `q` 阶历史输入拼成特征向量。 |
| `ROM -> 控制器` | ROM 对当前或未来载荷响应的估计，例如 `y_hat(t+1)` 或一个预测窗口内的 `y_hat(t:t+H)`。 | 输出为载荷单位，通常是 `N` 或 `N*m`；若控制器使用标准化误差，则中间量可为无量纲。 | 当前预测 `y_hat(t) in R^m`；预测轨迹 `H × m`，其中 `m` 是被控制/观察的输出数量。 |
| `控制器 -> 喷气指令` | 控制器给出的喷口开关和质量流量命令，写回 `actuation_schedule.csv` 或 STAR runtime 命令计划。 | 喷气开关无量纲；`cmd_massflow_01..24` 为 `kg/s`；时间窗口为 `s`。 | 每个窗口一行：24 路 `JET_01..JET_24` 加 24 路 `cmd_massflow_01..24`，可记作 `u(t) in R^24`，另有对应开关向量 `b(t) in {0,1}^24`。 |

验收时需要能用自己的话说明：每个模块吃什么、吐什么、单位是什么、维度是多少。具体变量口径以 `docs/week2/B02_喷气激励数据字典.md`、`docs/week2/B06_ARX_ROM变量和公式说明.md` 和实际 case schema 为准。

## 7. examples 启动脚本

所有面向用户直接运行的启动脚本都放在 `examples/`。这些命令都从仓库根目录运行，不依赖个人电脑路径；脚本会优先使用 `.venv/bin/python`，如果不存在则回退到 `python3`。

### 7.1 推荐快速启动

| 目标 | 命令 | 说明 | 主要输出 |
|---|---|---|---|
| 交互式选择动作和运行方式 | `bash examples/run_one_action.sh` | 推荐入口。先选择 1-6 种喷气激励，再选择 mock 或 CCM。 | mock 输出到 `runs/mock_<动作名称>/`；CCM 输出到 `runs/starccm_<动作名称>/` |
| 一次生成全部动作表 | `bash examples/run_all_actions.sh` | 只生成 6 种激励的 `actuation_schedule.csv`，不运行 plant。 | `runs/<动作名称>/input/` |

### 7.2 Mock 本地验证

| 目标 | 命令 | 说明 | 主要输出 |
|---|---|---|---|
| 选择动作并运行 mock | `bash examples/run_mock_from_action.sh` | 选择 1-6 种激励，生成动作表后直接运行 `MockDynamicPlant24x6`。 | `runs/mock_<动作名称>/` |
| 用已有动作表运行 mock | `bash examples/run_mock_from_existing_dir.sh` | 从 `runs/` 下已有目录选择 `actuation_schedule.csv`，原地生成 mock 结果。 | 所选 `runs/<目录>/` |

mock 结果目录通常包含：

```text
input/actuation_schedule.csv
timeseries.csv
quality_report.json
figures/
```

### 7.3 STAR-CCM+ 运行

| 目标 | 命令 | 说明 | 主要输出 |
|---|---|---|---|
| 选择动作并启动 CCM | `bash examples/run_ccm_from_action.sh` | 选择 1-6 种激励，生成动作表后启动 STAR-CCM+。 | `runs/starccm_<动作名称>/` |
| 用已有动作表启动 CCM | `bash examples/run_ccm_from_existing_dir.sh` | 从 `runs/` 下已有目录选择动作表，并在该目录运行 STAR-CCM+。 | 所选 `runs/<目录>/` |

CCM 启动脚本固定读取：

```text
configs/ccm_runtime.yaml
```

其中 `ccm.sim_path` 和 `ccm.starccm_path` 必须提前填写；配置缺失或为空会直接报错退出。

### 7.4 CCM 结果导入三步

真实 CCM 跑完后，如果目录里有 `out_put/` 监视器 CSV，可以按下面三步整理成标准 case：

| 步骤 | 命令 | 说明 | 主要输出 |
|---|---|---|---|
| Step 1 | `bash examples/run_ccm_ingest_step1_timeseries.sh` | 从 STAR-CCM+ monitor CSV 和动作表生成标准 `timeseries.csv`。 | `timeseries.csv`、`case_manifest.yaml`、快速 SVG 图 |
| Step 2 | `bash examples/run_ccm_ingest_step2_check.sh` | 检查必选列、时间单调性、缺失值、喷气开关和质量流量一致性。 | `quality_report.json` |
| Step 3 | `bash examples/run_ccm_ingest_step3_figures.sh` | 基于质量检查结果生成诊断图。 | `figures/*.png` |

### 7.5 ROM 训练、验证和使用

| 目标 | 命令 | 说明 | 主要输出 |
|---|---|---|---|
| 训练 ARX ROM | `bash examples/run_rom_train.sh` | 先生成 mock 训练集，再拟合 ARX ROM。 | `runs/arx/training/`、`runs/arx/model/arx_model.json` |
| 验证 ARX ROM | `bash examples/run_rom_validate.sh` | 生成独立验证集并计算预测误差。 | `runs/arx/validation/metrics.json`、预测对比图 |
| 用 ROM 预测已有 case | `bash examples/run_rom_use.sh` | 选择已有 `timeseries.csv` case，使用已训练模型递推预测。 | `runs/arx/use_<case_name>/` |

ROM 相关脚本只使用本地 mock 数据和标准 case，不调用历史 `generic_automation/rl/` 代码。

## 8. 运行测试

推荐从仓库根目录运行：

```bash
.venv/bin/python -m pytest -q
```

只测 flow_control 相关内容：

```bash
.venv/bin/python -m pytest -q \
  tests/test_actuation_schedule_generator.py \
  tests/test_mock_dynamic24x6.py \
  tests/test_flow_control_smoke.py \
  tests/test_case_schema.py \
  tests/test_starccm_runtime_translators.py
```

## 9. 历史 STAR-CCM+ 自动化主线

`generic_automation/` 仍然保留原来的 STAR-CCM+ 自动化能力和求解器参数 RL 代码。
这部分只作为历史代码和对照资料保留，不参与本周喷气控制结果；本周结果以 `flow_control/`、`starccm/control/` 和 `starccm/runtime/` 链路为准。

运行单个 case：

```bash
python ga.py case --config configs/config.yaml
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

## 10. 运行模式

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

## 11. 重要文档

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

docs/week2/B06_ARX_ROM变量和公式说明.md
  ARX ROM 输入输出、数据生成、训练/验证入口和当前 mock 验证指标说明

flow_control/star_ingest/COMMANDS.md
  STAR ingest 模块命令说明，一步完成和三步执行入口都在模块内

flow_control/rom/COMMANDS.md
  ROM 模块命令说明，约定训练数据集在 runs/arx/training，验证数据集在 runs/arx/vaild

docs/FLOW_CONTROL_MODULE_STARTUP_GUIDE.md
  schedule、mock、STAR-CCM+、数据导入/分析和 ARX 各模块启动方式

docs/week2/STARCCM_RUNTIME_REFACTOR.md
  STAR-CCM+ runtime 分层和翻译层说明
```

## 12. 当前边界

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
旧的计算效率提升 RL 代码不得作为本周喷气控制结果的一部分运行、统计或展示。
