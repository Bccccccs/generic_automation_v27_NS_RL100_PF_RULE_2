# generic_automation / maglev_sparse_jet_9w

本项目当前重点是 `flow_control/`：围绕 24 个喷气区生成统一的 `actuation_schedule.csv`，把动作表送入 mock、STAR-CCM+ 或 ROM 链路，并把结果整理成统一 case 目录。

`generic_automation/` 中保留了历史 STAR-CCM+ 自动化、求解器参数调优、在线监控和 RL 代码；它不是当前喷气控制主线的默认入口。

## 1. 从 0 部署

从干净环境开始：

```bash
git clone <repo-url>
cd generic_automation_v27_NS_RL100_PF_RULE_2

python3 -m venv .venv
. .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
```

确认环境可用：

```bash
.venv/bin/python -m pytest -q
```

之后所有示例脚本都从仓库根目录运行。脚本会优先使用 `.venv/bin/python`，所以也可以写成：

```bash
python examples/run_one_action.py
```

## 2. 先生成喷气动作表

动作表是本项目的核心输入：

```text
actuation_schedule.csv
```

统一列名：

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
cmd_massflow_*   对应喷气区的指令质量流量，单位 kg/s
```

强制规则：

```text
JET_xx = 0 时，cmd_massflow_xx 必须等于 0
JET_xx = 1 时，cmd_massflow_xx 必须大于 0
physical_time、t_start、t_end 都是物理时间秒，不是求解器迭代步
```

交互式选择一种动作，只生成动作表：

```bash
python examples/run_one_action.py
```

一次生成全部六种动作：

```bash
python examples/run_all_actions.py
```

输出位置：

```text
runs/<动作名称>/input/actuation_schedule.csv
```

同时会生成输入热图、总质量流量曲线和校验摘要。

当前支持 6 种动作：

| 动作 | 配置文件 | 作用 |
|---|---|---|
| 无喷气参考段 | `configs/actions/no_jet_reference.yaml` | 所有喷口关闭，作为无喷气基准 |
| 单喷气区脉冲 | `configs/actions/pulse_singlejet.yaml` | 一个喷口短时间打开，观察瞬时响应和延迟 |
| 单喷气区阶跃 | `configs/actions/step_singlejet.yaml` | 一个喷口持续打开一段时间，观察平均影响 |
| 关键喷气区扫频 | `configs/actions/chirp_keyjets.yaml` | 几个喷口的喷气量按越来越快的节奏变化 |
| PRBS 伪随机开关 | `configs/actions/prbs_demo.yaml` | 可复现随机开关，为 ROM/动态辨识提供输入 |
| 稀疏随机分组 | `configs/actions/pilot_sparse24.yaml` | 每个主窗口开 3 个喷口，公平筛查 24 个喷气区 |

## 3. 本地 mock 跑通完整 case

如果只想验证链路，不需要 STAR-CCM+ 许可证，先跑 mock。

选择动作并生成 mock 结果：

```bash
python examples/run_mock_from_action.py
```

或者对已有动作表原地运行 mock：

```bash
python examples/run_mock_from_existing_dir.py
```

mock 输出是标准 case 目录，通常包含：

```text
runs/mock_<动作名称>/
  input/actuation_schedule.csv
  actuation_schedule.csv
  timeseries.csv
  case_manifest.yaml
  quality_report.json
  config_used.yaml
  mock_dynamic24x6_summary.json
  figures/
```

其中 `timeseries.csv` 是喷气输入和载荷输出的标准时间序列表，后续 ROM、质量检查、图表都读这一层。

## 4. STAR-CCM+ 运行和结果导入

### 4.1 配置 STAR-CCM+

STAR-CCM+ 运行配置在：

```text
configs/ccm_runtime.yaml
```

至少需要填写：

```yaml
ccm:
  sim_path: /path/to/case.sim
  starccm_path: starccm+
  num_cores: 1
  region: Region
  podkey: ""
```

### 4.2 启动 STAR-CCM+

选择动作并启动 CCM：

```bash
python examples/run_ccm_from_action.py
```

或者使用已有动作表启动 CCM：

```bash
python examples/run_ccm_from_existing_dir.py
```

输出会进入 `runs/starccm_<动作名称>/` 或所选目录。

### 4.3 导入 STAR-CCM+ monitor CSV

真实 CCM 跑完后，如果目录里有 `out_put/` 监视器 CSV，按三步整理为标准 case：

```bash
python examples/run_ccm_ingest_step1_timeseries.py
python examples/run_ccm_ingest_step2_check.py
python examples/run_ccm_ingest_step3_figures.py
```

三步含义：

| 步骤 | 作用 | 主要输出 |
|---|---|---|
| Step 1 | 从 STAR monitor CSV 和动作表生成标准 `timeseries.csv` | `timeseries.csv`、`case_manifest.yaml`、快速 SVG 图 |
| Step 2 | 检查必选列、时间单调性、NaN、喷气开关和质量流量一致性 | `quality_report.json` |
| Step 3 | 基于质量检查结果生成诊断图 | `figures/*.png` |

Step 1/2/3 的交互列表都支持输入编号或直接输入目录路径。

### 4.4 第三周真实数据常用命令

只生成动作表，不启动 STAR：

```bash
python -m flow_control.generator.schedule_generator \
  --config configs/actions/pulse_singlejet.yaml \
  --output-dir runs/week3_pulse_singlejet
```

只整理已有 STAR 输出，不启动 STAR：

```bash
printf 'runs/<case_dir>\n' | python examples/run_ccm_ingest_step1_timeseries.py
printf 'runs/<case_dir>\n' | python examples/run_ccm_ingest_step2_check.py
printf 'runs/<case_dir>\n' | python examples/run_ccm_ingest_step3_figures.py
```

只做数据检查：

```bash
printf 'runs/<case_dir>\n' | python examples/run_ccm_ingest_step2_check.py
```

只有同时满足以下条件才启动 STAR：本机能执行 `starccm+` 或已设置 `STARCCM_PATH`、已有真实 `.sim` 文件、`configs/ccm_runtime.yaml` 已填好、动作表已通过校验。

```bash
python -m flow_control.cli.run_starccm \
  --schedule runs/week3_pulse_singlejet/input/actuation_schedule.csv \
  --sim /path/to/input.sim \
  --out runs/starccm_week3_pulse_singlejet \
  --starccm-path "${STARCCM_PATH:-starccm+}" \
  --np 1 \
  --region Region
```

动作表的 `input/config_summary.yaml`、mock/ROM 标准 case 的 `case_manifest.yaml`、真实 STAR 整理后的 `case_manifest.yaml` 都会记录 `git_commit`，用于回答某个标准 case 由哪个代码 commit 生成。

## 5. ROM 训练、验证和使用

ARX ROM 支持多模型目录，统一放在：

```text
runs/arx/trains/<模型名>        训练数据集
runs/arx/models/<模型名>        arx_model.json、training_summary.json
runs/arx/vaild_cases/<模型名>   自动生成的验证 case
runs/arx/validations/<模型名>   验证指标和图
```

### 5.1 训练模型

```bash
python examples/run_rom_train.py
```

脚本会要求输入模型名，例如 `train01`。训练过程会自动生成 100 个 mock 训练 case，然后拟合 ARX 模型。

### 5.2 验证模型

```bash
python examples/run_rom_validate.py
```

验证入口会先选择模型，再选择两种模式之一：

```text
1) existing-case  选择当前已有标准 case 验证
2) auto-10-cases  自动生成 10 个 mock case 后验证
```

验证输出在：

```text
runs/arx/validations/<模型名>/
```

### 5.3 用模型预测纯 schedule

```bash
python examples/run_rom_use.py
```

这个入口用于没有真实输出的纯 `actuation_schedule.csv`。脚本会先选择模型，再选择 schedule 目录，然后把预测结果写回所选原目录，生成与 mock case 对齐的标准产物：

```text
<所选目录>/timeseries.csv
<所选目录>/case_manifest.yaml
<所选目录>/quality_report.json
<所选目录>/figures/*.svg
```

因为纯 schedule 没有真实载荷历史，ARX 会用 0 初始化前 `max_lag` 行输出历史，然后从第 `max_lag` 行开始递推预测。

ROM 的离散索引是时间步样本编号，不是喷气窗口编号。纯 schedule
预测时可以输入 `time_step`；留空表示使用 schedule 窗口长度，
填写例如 `0.01` 会把每个喷气窗口按 0.01 s 展开成多行样本。

## 6. 常见质量检查样例

本地 `runs/` 下可以放错误样例，用来测试检查器。例如：

```text
runs/error_case_missing_required_file
runs/error_case_missing_required_column
runs/error_case_non_monotonic_time
runs/error_case_nan_value
runs/error_case_jet_massflow_mismatch
runs/error_case_missing_actual_massflow
runs/error_case_no_jet_reaction_nonzero
```

这些目录用于测试 `python examples/run_ccm_ingest_step2_check.py` 的质量报告生成。

其中 `error_case_no_jet_reaction_nonzero` 是 warning-only：无喷气 case 中 `Jet_Reaction_Z` 非零，表示需要确认 STAR monitor 的物理口径。

## 7. 模块作用

```text
examples/
  面向用户的一键/交互脚本。推荐从这里启动日常流程。

configs/
  动作配置、mock 参数、CCM runtime 配置。

flow_control/generator/
  读取 configs/actions/*.yaml，生成 actuation_schedule.csv。

flow_control/excitation_patterns/
  六种激励模式的具体生成逻辑：reference、pulse、step、chirp、prbs、sparse_groups。

flow_control/mock/
  MockDynamicPlant24x6，本地模拟 24 输入到 6 载荷输出，并写出标准 case。

flow_control/star_ingest/
  STAR monitor CSV 导入、标准 timeseries 合成、质量检查、诊断图生成。

flow_control/rom/
  ARX ROM 训练、验证、推理和指标图输出。

starccm/control/
  24 个喷气区、6 个载荷点、report 名称和结果映射的共用契约。

starccm/runtime/
  STAR-CCM+ runtime 命令模型、macro builder、日志解析和结果收集。

generic_automation/
  历史 STAR-CCM+ 自动化、求解器参数调优、在线监控和 RL。当前喷气控制默认不走这里。

tests/
  自动测试。

runs/
  本地生成结果，通常不作为源码提交。

artifacts/
  可提交的实验归档、报告表格和复现日志；不要把这类交付物继续平铺在仓库根目录。
```

## 8. 数据链路和变量口径

当前喷气控制数据链路：

```text
STAR / Mock
  -> 原始数据
  -> 数据标准化
  -> ROM
  -> 控制器
  -> 喷气指令
```

| 环节 | 数据 | 单位 | 维度 |
|---|---|---|---|
| STAR / Mock -> 原始数据 | STAR monitor CSV 或 mock `timeseries.csv`，包含时间、24 路喷气、6 个载荷输出和全局力/力矩 | 时间 `s`；质量流量 `kg/s`；力 `N`；力矩 `N*m` | `N_t × N_col` |
| 数据标准化 -> ROM | 标准 case 中的输入 `JET_*`、`cmd_massflow_*` 和输出 `Fz_*`、`Fz_Total` 等 | 保留物理单位 | 输入通常 48 列，输出通常 6 个区域载荷加全局输出 |
| ROM -> 控制器 | ROM 对当前或未来载荷响应的估计 | 通常为 `N` 或 `N*m` | 当前预测 `y_hat(t)` 或预测轨迹 `H × m` |
| 控制器 -> 喷气指令 | 喷口开关和质量流量命令，写回 `actuation_schedule.csv` 或 STAR runtime 计划 | 开关无量纲；质量流量 `kg/s` | 每个窗口 24 路开关加 24 路质量流量 |

几个容易混淆的字段：

```text
cmd_massflow_XX     控制器希望喷口 XX 达到的质量流量
actual_massflow_XX  plant / STAR 实际达到或返回的质量流量
Jet_Reaction_Z      喷气相关 Z 向反力；无喷气时理论上应接近 0，若非零需确认 monitor 口径
```

详细变量口径见：

```text
docs/week2/B02_喷气激励数据字典.md
docs/week2/B06_ARX_ROM变量和公式说明.md
```

## 9. 自动测试

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

## 10. 历史 STAR-CCM+ 自动化主线

`generic_automation/` 仍保留历史 STAR-CCM+ 自动化能力。

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

这些命令需要可用的 STAR-CCM+ 安装和有效 `.sim` 文件路径。

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
  input_sim: /path/to/mesh_ready.sim
  checkpoint_interval: 250
```

## 11. 重要文档

```text
flow_control/generator/COMMANDS.md
  动作表生成命令说明

flow_control/mock/COMMANDS.md
  mock plant 命令说明

flow_control/star_ingest/COMMANDS.md
  STAR 导入、检查和诊断图命令说明

flow_control/rom/COMMANDS.md
  ROM 训练、验证和使用命令说明

docs/week2/B02_喷气激励数据字典.md
  actuation_schedule.csv、summary、图和每种激励含义

docs/week2/B03_STAR导出数据导入说明.md
  STAR 导出数据如何整理成标准 case

docs/week2/B06_ARX_ROM变量和公式说明.md
  ARX ROM 输入输出、公式、训练/验证入口和指标说明

docs/week2/B07_joint_interface_report.md
  B07 联合数据接力接口报告

docs/week2/B07_current_blocking_issues.md
  B07 当前待确认问题，尤其是无喷气 Jet_Reaction_Z 非零口径

docs/STARCCM_RUNTIME_REFACTOR.md
  STAR-CCM+ runtime 分层和翻译层说明
```

## 12. 当前边界

当前已具备：

```text
生成 6 类喷气动作表
把动作表送入本地 mock plant
把 STAR monitor CSV 整理成标准 case
执行质量检查并生成诊断图
训练/验证/使用多模型 ARX ROM
把喷气动作翻译成 STAR-CCM+ runtime 命令计划
```

当前还没有完成：

```text
一键启动 STAR-CCM+ 并逐窗口真实修改喷口边界条件
真实 CFD 喷气闭环控制
喷气 RL / MPC 控制器
```

也就是说，现在交付重点是喷气实验输入生成、本地 mock 验证、STAR 数据导入、ROM 建模和质量检查链路；真实 STAR-CCM+ 喷口边界闭环接入是下一步工作。
