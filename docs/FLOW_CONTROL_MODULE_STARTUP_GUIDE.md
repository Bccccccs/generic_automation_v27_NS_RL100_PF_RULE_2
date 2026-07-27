# Flow Control 模块启动说明

这份文档说明当前 `flow_control` 相关模块如何启动、输入是什么、输出到哪里，以及模块之间如何衔接。

当前主线可以分成五块：

```text
1. schedule 生成模块
   生成 actuation_schedule.csv

2. mock 模块
   用本地虚拟 plant 把喷气输入变成 timeseries.csv

3. STAR-CCM+ 模块
   把 actuation_schedule.csv 翻译成 STAR-CCM+ 宏并运行真实仿真

4. 数据分析 / STAR 数据导入模块
   把 STAR 导出的 CSV 整理成标准 case，并生成质量检查和图

5. ARX ROM 模块
   从标准 case 中读取输入/输出，拟合轻量响应模型
```

注意：ARX 目前是训练管线和模型代码已经接好，不代表已经有真实可用的最终训练模型。真正训练需要先有足够的 `timeseries.csv` 和 `actuation_schedule.csv` 数据。

## 0. 通用启动环境

推荐从仓库根目录执行所有命令：

```bash
cd <repo-root>
```

如果 `.venv` 已经存在：

```bash
.venv/bin/python -m pytest
```

如果需要重新安装环境：

```bash
python3 -m venv .venv
. .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
```

本地 `schedule`、`mock`、`数据导入`、`ARX` 不需要 STAR-CCM+。只有真实 STAR 运行需要本机能调用 STAR-CCM+。

## 1. Schedule 生成模块

### 1.1 模块作用

schedule 模块负责生成喷气动作表：

```text
actuation_schedule.csv
```

这张表是后续所有模块的输入。它描述每个物理时间窗口里 24 个喷口是否开启，以及每个喷口的质量流量指令。

核心列：

```text
physical_time
window_id
t_start
t_end
JET_01 ... JET_24
cmd_massflow_01 ... cmd_massflow_24
```

### 1.2 代码位置

```text
flow_control/generator/schedule_generator.py
flow_control/excitation_patterns/common.py
flow_control/excitation_patterns/reference.py
flow_control/excitation_patterns/pulse.py
flow_control/excitation_patterns/step.py
flow_control/excitation_patterns/chirp.py
flow_control/excitation_patterns/prbs.py
flow_control/excitation_patterns/sparse_groups.py
```

### 1.3 配置文件

当前动作配置在：

```text
configs/actions/no_jet_reference.yaml
configs/actions/pulse_singlejet.yaml
configs/actions/step_singlejet.yaml
configs/actions/chirp_keyjets.yaml
configs/actions/prbs_demo.yaml
configs/actions/pilot_sparse24.yaml
```

常用配置含义：

```text
no_jet_reference.yaml  无喷气参考
pulse_singlejet.yaml   单喷口脉冲
step_singlejet.yaml    单喷口阶跃
chirp_keyjets.yaml     关键喷口扫频
prbs_demo.yaml         PRBS 随机激励，适合后续 ROM 辨识
pilot_sparse24.yaml    24 喷口稀疏随机分组
```

### 1.4 启动命令

生成默认 sparse24 schedule：

```bash
.venv/bin/python -m flow_control.generator.schedule_generator \
  --config configs/actions/pilot_sparse24.yaml \
  --output-dir runs/sparse24
```

生成单喷口脉冲 schedule：

```bash
.venv/bin/python -m flow_control.generator.schedule_generator \
  --config configs/actions/pulse_singlejet.yaml \
  --output-dir runs/pulse_singlejet
```

指定输出根目录：

```bash
.venv/bin/python -m flow_control.generator.schedule_generator \
  --config configs/actions/prbs_demo.yaml \
  --output-dir runs/schedule_examples/prbs_demo_custom
```

### 1.5 输出文件

输出位置由命令的 `--output-dir` 决定，生成器固定写入其 `input/` 子目录：

```text
runs/<输出目录>/input/
```

输出内容通常包括：

```text
actuation_schedule.csv       主喷气动作表
config_summary.yaml          配置摘要和统计信息
validation_report.json       schedule 校验结果
actuation_heatmap.svg        24 路喷气开关热图
total_mass_flow.csv          每个窗口的总质量流量
total_mass_flow_curve.svg    总质量流量曲线
```

### 1.6 调用链

```text
configs/actions/*.yaml
  -> flow_control.generator.schedule_generator.generate_from_yaml()
  -> flow_control.excitation_patterns.common.ActuationConfig
  -> generate_pattern_table()
  -> 具体模式生成器 pulse / step / chirp / prbs / sparse_groups / reference
  -> write_pattern_outputs()
  -> actuation_schedule.csv + 图 + validation_report.json
```

### 1.7 什么时候用

用它来准备 CFD 或 mock 的喷气输入。它只生成输入，不产生载荷响应。

## 2. Mock 模块

### 2.1 模块作用

mock 模块用本地虚拟模型模拟：

```text
24 路喷气输入 -> 6 个局部 Fz 载荷响应 + Fz_Total
```

它的作用是验证数据链路、调试 schedule、给 ARX 流程做 smoke test。它不是物理真实的 STAR-CCM+ 结果。

### 2.2 代码位置

```text
examples/run_mock_dynamic24x6.py
flow_control/mock/pipeline.py
flow_control/mock/mock_plant.py
flow_control/mock/__init__.py
```

### 2.3 配置文件

```text
configs/mock_dynamic24x6.yaml
```

它控制 mock plant 的随机种子、噪声、动态响应参数等。

### 2.4 启动方式 A：生成 schedule 后直接跑 mock

这是最常用方式：

```bash
.venv/bin/python examples/run_mock_dynamic24x6.py \
  --actuation-config configs/actions/pilot_sparse24.yaml \
  --config configs/mock_dynamic24x6.yaml \
  --out runs/mock_dynamic24x6_demo
```

这条命令做两件事：

```text
1. 根据 --actuation-config 生成 actuation_schedule.csv
2. 把 actuation_schedule.csv 输入 mock plant，生成 timeseries.csv
```

### 2.5 启动方式 B：使用已有 schedule 跑 mock

如果已经有 `actuation_schedule.csv`：

```bash
.venv/bin/python examples/run_mock_dynamic24x6.py \
  --schedule runs/prbs_demo/input/actuation_schedule.csv \
  --config configs/mock_dynamic24x6.yaml \
  --out runs/mock_from_existing_schedule
```

### 2.6 输出文件

mock case 输出目录通常包含：

```text
case_manifest.yaml
actuation_schedule.csv
timeseries.csv
quality_report.json
mock_dynamic24x6_summary.json
figures/input_heatmap.svg
figures/fz_regions.svg
figures/fz_total.svg
figures/spatial_nonuniformity.svg
figures/total_massflow.svg
```

最重要的文件：

```text
actuation_schedule.csv  输入：每个时间窗口喷气怎么开
timeseries.csv          输出：mock plant 对每个窗口的载荷响应
quality_report.json     数据质量和 schema 检查结果
```

### 2.7 调用链

```text
examples/run_mock_dynamic24x6.py
  -> flow_control.mock.pipeline.run_actuation_to_mock()
  -> flow_control.generator.schedule_generator.generate_from_yaml()
  -> flow_control.mock.write_mock_dynamic_case()
  -> MockDynamicPlant24x6.simulate()
  -> CaseSchema.write_case()
  -> 标准 case 输出
```

### 2.8 什么时候用

用它验证：

```text
schedule 是否合法
文件格式是否能闭环
后续数据分析和 ARX 代码是否能读取 case
```

不要用 mock 结果替代真实 STAR-CCM+ 结论。

## 3. STAR-CCM+ 模块

这里有两条不同路径：

```text
A. flow_control STAR runner
   把 actuation_schedule.csv 接到 STAR-CCM+ 喷口边界，跑真实流控仿真

B. generic_automation 主线
   原有 STAR-CCM+ case / sweep / monitor / RL 求解器参数自动化
```

本节重点标注 flow_control 相关入口。

### 3.1 flow_control STAR runner 作用

它读取：

```text
actuation_schedule.csv
```

然后生成：

```text
FlowControlRunMacro.java
starccm_runtime_plan.json
STAR-CCM+ 日志
可选 timeseries.csv
可选 flow_control_result.sim
```

### 3.2 代码位置

```text
flow_control/cli/run_starccm.py
flow_control/adapters/starccm_runner.py
flow_control/adapters/starccm_adapter.py
starccm/runtime/starccm_macro_builder.py
starccm/runtime/starccm_macro_template.java
starccm/runtime/commands.py
starccm/control/control_spec.py
```

### 3.3 启动命令

先准备一个 schedule：

```bash
.venv/bin/python -m flow_control.generator.schedule_generator \
  --config configs/actions/pulse_singlejet.yaml \
  --output-dir runs/pulse_singlejet
```

然后 dry-run，只生成宏和运行计划，不真正启动 STAR：

```bash
.venv/bin/python -m flow_control.cli.run_starccm \
  --schedule runs/schedule_examples/pulse_singlejet/actuation_schedule.csv \
  --sim /path/to/input.sim \
  --out runs/starccm_pulse_singlejet_dry_run \
  --dry-run
```

真实启动 STAR：

```bash
.venv/bin/python -m flow_control.cli.run_starccm \
  --schedule runs/schedule_examples/pulse_singlejet/actuation_schedule.csv \
  --sim /path/to/input.sim \
  --out runs/starccm_pulse_singlejet \
  --starccm-path /path/to/starccm+ \
  --np 8 \
  --region Region
```

如果已经设置了环境变量：

```bash
export STARCCM_PATH=/path/to/starccm+
```

则可以省略 `--starccm-path`。

### 3.4 常用参数

```text
--schedule              actuation_schedule.csv 路径，必填
--sim                   输入 .sim 文件，必填
--out                   输出目录，必填
--starccm-path          STAR-CCM+ 可执行文件路径，默认 $STARCCM_PATH 或 starccm+
--np                    并行核数
--podkey                STAR-CCM+ license token
--region                包含 J01..J24 喷气口边界的 Region 名称
--time-step             控制窗口内的 solver time step
--report                额外采样 report，可重复传入
--non-strict-boundaries 缺少喷口边界时警告并跳过
--no-save-result-sim    结束时不保存 flow_control_result.sim
--dry-run               只生成宏、计划和命令，不启动 STAR
```

### 3.5 输出文件

```text
runs/<star_case>/
  FlowControlRunMacro.java
  starccm_runtime_plan.json
  starccm.log
  timeseries.csv                  如果真实运行后成功收集结果
  flow_control_result.sim          如果启用保存
```

### 3.6 调用链

```text
flow_control.cli.run_starccm
  -> FlowControlStarCCMRunner.run()
  -> FlowControlStarCCMAdapter.plan_from_schedule_csv()
  -> starccm_runtime_plan.json
  -> StarCCMMacroBuilder
  -> FlowControlRunMacro.java
  -> starccm+ -batch FlowControlRunMacro.java input.sim
```

### 3.7 generic_automation STAR 主线

原有 STAR 自动化入口仍然在根命令 `ga.py`：

```bash
python ga.py case --config configs/config.yaml
python ga.py sweep --config configs/config.yaml --cases cases/cases.csv
python ga.py monitor --config configs/config.yaml
python ga.py replay --help
python ga.py force-update --help
```

这些入口主要服务原 STAR-CCM+ 自动化、求解器参数调优和 RL 监控，不是专门的 24 喷口 schedule runner。

## 4. 数据分析 / STAR 数据导入模块

### 4.1 模块作用

数据分析模块负责把 STAR 导出的 CSV 转成标准 case：

```text
STAR monitor CSV
  -> timeseries.csv
  -> actuation_schedule.csv
  -> case_manifest.yaml
  -> quality_report.json
  -> figures/
```

它也会生成诊断图和质量报告。这里的“数据分析”主要指数据读取、列名映射、质量检查和图表生成。

### 4.2 代码位置

```text
examples/ingest_star_case.py
examples/build_real_star_ingest_demo.py
flow_control/star_ingest/star_export_reader.py
flow_control/star_ingest/case_data_loader.py
flow_control/star_ingest/quality_checker.py
flow_control/star_ingest/figures_generator.py
flow_control/data_schema.py
```

### 4.3 启动方式 A：导入一个或多个 STAR CSV

单个文件：

```bash
.venv/bin/python examples/ingest_star_case.py \
  --star-file runs/real_star_ingest_demo/FZ.csv \
  --case-dir runs/my_star_case \
  --force \
  --partial
```

多个文件合并：

```bash
.venv/bin/python examples/ingest_star_case.py \
  --star-file /path/to/FZ.csv \
  --star-file /path/to/Drag.csv \
  --star-file /path/to/Pitch.csv \
  --star-file /path/to/Roll.csv \
  --case-dir runs/my_star_case \
  --force \
  --partial
```

如果是带喷气的完整数据：

```bash
.venv/bin/python examples/ingest_star_case.py \
  --star-file /path/to/FZ.csv \
  --star-file /path/to/JetMassFlow.csv \
  --case-dir runs/my_star_jet_case \
  --force \
  --jet \
  --case-type jet_on
```

### 4.4 启动方式 B：构建内置真实 STAR 导入 demo

如果本地有默认源目录：

```text
单喷气/Excel兼容_UTF8_BOM/
```

可以运行：

```bash
.venv/bin/python examples/build_real_star_ingest_demo.py \
  --source-dir starccm_single/Excel兼容_UTF8_BOM \
  --case-dir runs/real_star_ingest_demo \
  --case-type jet_on
```

### 4.5 常用参数

```text
--star-file   STAR 导出的 CSV。可以重复传入多个文件
--case-dir    标准 case 输出目录
--force       覆盖已有 case 目录
--jet         标记为喷气 case，会期待 JET 和 massflow 列
--case-type   unknown / no_jet / jet_on
--partial     允许只导入部分 STAR timeseries，不因缺完整 schema 直接失败
```

### 4.6 输出文件

```text
runs/<case_id>/
  case_manifest.yaml
  timeseries.csv
  actuation_schedule.csv
  quality_report.json
  notes.md
  figures/force_timeseries.png
  figures/jet_schedule.png
  figures/massflow_check_01_06.png
  figures/massflow_check_07_12.png
  figures/massflow_check_13_18.png
  figures/massflow_check_19_24.png
  figures/quality_summary.png
```

是否生成某些图，取决于输入数据里是否有对应列。没有质量流量列时，
`massflow_check.png` 会作为不可用占位图；有质量流量列时，会拆成 4 张图覆盖 24 路。

### 4.7 调用链

```text
examples/ingest_star_case.py
  -> read_star_export_csv() / read_star_export_bundle()
  -> compute_fz_total()
  -> ingest_star_export()
  -> quality_checker
  -> CaseSchema.write_case()
  -> generate_all_figures()
  -> 标准 case 输出
```

### 4.8 与 ARX 的关系

ARX 训练需要标准 case 里同时有：

```text
timeseries.csv
actuation_schedule.csv
```

并且至少包含：

```text
JET_01 ... JET_24
cmd_massflow_01 ... cmd_massflow_24
Fz_S1L ... Fz_S3R
Fz_Total
```

如果真实 STAR 导出的文件没有喷口开关或质量流量列，ARX 训练会缺输入列，不能作为完整训练数据。

## 5. ARX ROM 模块

### 5.1 模块作用

ARX ROM 用已有 case 数据拟合一个轻量线性动态模型：

```text
过去载荷输出 + 当前/过去喷气输入 -> 当前载荷输出
```

它不是强化学习控制器，也不是最终 CFD 物理模型。它是后续 RL / MPC / 控制器可以调用的快速响应近似。

### 5.2 当前状态

当前已经有：

```text
ARX 模型代码
训练 / 验证分离的保存文件流程
CLI 入口
文档和测试
```

当前还没有：

```text
用大规模真实 STAR 数据训练出的可靠最终模型
覆盖 24 个喷口动态响应的完整训练集
可以直接用于真实控制闭环的最终 arx_model.json
```

### 5.3 代码位置

```text
flow_control/rom/arx_model.py
flow_control/rom/generate_arx_dataset.py
flow_control/rom/identifier.py
flow_control/rom/training.py
flow_control/rom/validation.py
flow_control/cli/train_rom.py
flow_control/cli/validate_rom.py
flow_control/cli/summarize_single_jet.py
examples/train_rom_mock.py
examples/validate_rom_mock.py
```

兼容旧路径：

```text
models.ARXModel
rom_identifier.py
```

新代码建议统一从 `flow_control.rom` 导入。

### 5.4 输入要求

ARX 训练输入是一个标准 case 目录：

```text
runs/<case_id>/
  timeseries.csv
  actuation_schedule.csv
```

需要的输入列：

```text
JET_01 ... JET_24
cmd_massflow_01 ... cmd_massflow_24
```

需要的输出列：

```text
Fz_S1L
Fz_S1R
Fz_S2L
Fz_S2R
Fz_S3L
Fz_S3R
Fz_Total
```

如果 `cmd_massflow_*` 在 `actuation_schedule.csv` 里而不在 `timeseries.csv` 里，ARX 模块会按 `window_id` 或 `physical_time` 自动合并。

### 5.5 训练入口

训练入口只负责拟合模型，不负责验证，也没有内部切分参数。必须显式提供 `--dataset-dir` 或 `--case-dir`，程序会使用所指定训练集中的全部可用行。

训练 `runs/arx_test` 中的 100 个 case：

```bash
.venv/bin/python -m flow_control.cli.train_rom \
  --dataset-dir runs/arx_test \
  --out runs/rom_mock_demo/model
```

训练输出：

```text
runs/rom_mock_demo/model/arx_model.json
runs/rom_mock_demo/model/training_summary.json
```

单个 case 兼容入口：

```bash
.venv/bin/python -m flow_control.cli.train_rom \
  --case-dir runs/mock_full_prbs_demo \
  --out runs/rom_mock_demo/model
```

单个 case 也会全部用于训练，不会在内部做 70/30 切分。验证必须另行指定其他 case 或 dataset。

示例包装脚本：

```bash
.venv/bin/python examples/train_rom_mock.py
```

可选模型阶数参数：

```text
--input-lags 2
  使用当前输入 u[t] 和上一时刻输入 u[t-1]。

--output-lags 3
  使用过去 3 步输出 y[t-1], y[t-2], y[t-3]。

--ridge-alpha 1.0
  ridge 正则强度，用于稳定最小二乘拟合。
```

### 5.6 生成 100 个 sparse24 mock 训练 case

为了给 ARX 训练准备数据，可以批量生成 sparse24 schedule，并让每个 schedule 都跑一遍 mock：

```bash
.venv/bin/python -m flow_control.rom.generate_arx_dataset \
  --out runs/arx_test \
  --count 100 \
  --overwrite
```

这个脚本的全局随机种子只从一个地方来：

```text
configs/system.yaml -> system.random_seed
```

当前默认：

```yaml
system:
  random_seed: 20260618
```

第 `N` 个 case 使用：

```text
global_seed = system.random_seed + N
schedule_seed = global_seed
mock_seed = global_seed
```

也就是说，默认会生成：

```text
runs/arx_test/sparse24_seed_20260618/
runs/arx_test/sparse24_seed_20260619/
...
runs/arx_test/sparse24_seed_20260717/
```

每个 case 都包含：

```text
actuation_input/actuation_schedule.csv   原始生成的 schedule
actuation_schedule.csv                   mock case 内复制的 schedule
timeseries.csv                           mock 载荷响应
quality_report.json
mock_config_used.yaml
figures/
```

索引文件：

```text
runs/arx_test/index.csv
runs/arx_test/index.json
```

### 5.7 生成 10 个验证 case

验证数据需要和训练数据分开。当前用于 mock 验证的 10 个 case 从 seed `20260718` 开始：

```bash
.venv/bin/python -m flow_control.rom.generate_arx_dataset \
  --out runs/arx_validate \
  --count 10 \
  --start-seed 20260718 \
  --overwrite
```

输出：

```text
runs/arx_validate/index.csv
runs/arx_validate/index.json
runs/arx_validate/sparse24_seed_20260718/
...
runs/arx_validate/sparse24_seed_20260727/
```

### 5.8 验证入口

验证入口只负责加载已有模型并在指定数据上递推预测，不重新训练模型。

```bash
.venv/bin/python -m flow_control.cli.validate_rom \
  --model runs/rom_mock_demo/model/arx_model.json \
  --dataset-dir runs/arx_validate \
  --out runs/rom_mock_demo
```

验证输出：

```text
runs/rom_mock_demo/metrics.json
runs/rom_mock_demo/prediction_timeseries.csv
runs/rom_mock_demo/prediction_6_load_cells.svg
runs/rom_mock_demo/error_6_load_cells.svg
runs/rom_mock_demo/rmse_bar.svg
```

单喷气响应摘要是独立数据分析命令，不会在训练时顺带执行：

```bash
.venv/bin/python -m flow_control.cli.summarize_single_jet \
  --case-dir runs/mock_full_step_singlejet \
  --out artifacts/reports/B06_single_jet_response_summary.csv
```

也可以只验证 dataset 中的一部分 case：

```bash
.venv/bin/python -m flow_control.cli.validate_rom \
  --model runs/rom_mock_demo/model/arx_model.json \
  --dataset-dir runs/arx_validate \
  --out runs/rom_mock_demo_subset \
  --case-start 0 \
  --case-count 3
```

### 5.9 训练输出文件

```text
runs/rom_mock_demo/model/
  arx_model.json
  training_summary.json
```

### 5.10 训练过程

ARX 训练不是神经网络训练，而是 ridge 正则化最小二乘拟合。

dataset 训练时，`index.csv` 中列出的每个 case 都独立构造滞后特征，历史不会跨 case 边界。所有列出的 case 和全部可用行都会训练，不保留内部验证段。当前 `runs/arx_test` 的 100 个 case 全部用于训练：

```text
100 个 case -> train_rom -> arx_model.json
```

公式：

```text
y[t] = c
     + A1 y[t-1] + A2 y[t-2] + ... + A_na y[t-na]
     + B0 u[t]   + B1 u[t-1] + ... + B_nb u[t-nb+1]
```

其中：

```text
u[t]  48 维喷气输入：24 个 JET 开关 + 24 个质量流量指令
y[t]  7 维载荷输出：6 个局部 Fz + Fz_Total
na    output_lags
nb    input_lags
```

验证时使用递推预测：

```text
每个验证 case 前 max_lag 行：只作为 ARX 历史初值，不拟合
后续验证行：只使用过去预测输出，不偷看当前真实输出
```

### 5.11 当前 mock 验证结果

用 `runs/arx_test` 的 100 个 case 训练，再用 `runs/arx_validate` 的 10 个新 case 验证，当前结果：

```text
validation cases: 10
validation rows: 770

Fz_S1L   RMSE 0.035320   corr 0.999802
Fz_S1R   RMSE 0.035998   corr 0.999788
Fz_S2L   RMSE 0.035005   corr 0.999772
Fz_S2R   RMSE 0.034485   corr 0.999768
Fz_S3L   RMSE 0.035397   corr 0.999705
Fz_S3R   RMSE 0.036063   corr 0.999689
Fz_Total RMSE 0.086205   corr 0.999905
```

这是 mock 数据上的结果，说明 ARX 链路和模型形式有效；真实 STAR 数据需要重新训练和验证。

### 5.12 调用链

```text
flow_control.rom.generate_arx_dataset
  -> sparse24 schedule
  -> MockDynamic24x6
  -> 标准 case

flow_control.cli.train_rom
  -> train_arx_rom_from_dataset()
  -> load_case_table()
  -> matrix_from_rows()
  -> 使用全部显式训练 case 拟合系数

flow_control.cli.validate_rom
  -> validate_arx_rom()
  -> ARXModel.from_dict()
  -> ARXModel.predict_recursive()
  -> compute_metrics()
  -> write_json() / write_prediction_csv() / write_*_svg()
```

### 5.13 什么时候用

有完整 case 数据后使用：

```text
mock case       可以用于流程验证
真实 STAR case  才能训练有物理意义的 ROM
```

如果还没有足够数据，只能说“训练流程能跑”，不能说“模型已经训练好”。

## 6. 推荐完整工作流

### 6.1 本地 mock 闭环

```bash
# 1. 生成明确的训练集
.venv/bin/python -m flow_control.rom.generate_arx_dataset \
  --out runs/arx_test \
  --count 100 \
  --overwrite

# 2. 用不同 seed 生成明确的验证集
.venv/bin/python -m flow_control.rom.generate_arx_dataset \
  --out runs/arx_validate \
  --count 10 \
  --start-seed 20260718 \
  --overwrite

# 3. 只在训练集上拟合模型
.venv/bin/python -m flow_control.cli.train_rom \
  --dataset-dir runs/arx_test \
  --out runs/rom_mock_demo/model

# 4. 只在验证集上评估并输出最终结果
.venv/bin/python -m flow_control.cli.validate_rom \
  --model runs/rom_mock_demo/model/arx_model.json \
  --dataset-dir runs/arx_validate \
  --out runs/rom_mock_demo
```

### 6.2 真实 STAR 流程

```bash
# 1. 生成 schedule
.venv/bin/python -m flow_control.generator.schedule_generator \
  --config configs/actions/pulse_singlejet.yaml \
  --output-dir runs/starccm_pulse_singlejet

# 2. dry-run 检查 STAR 宏和 runtime plan
.venv/bin/python -m flow_control.cli.run_starccm \
  --schedule runs/schedule_examples/pulse_singlejet/actuation_schedule.csv \
  --sim /path/to/input.sim \
  --out runs/starccm_pulse_singlejet_dry_run \
  --dry-run

# 3. 真正运行 STAR
.venv/bin/python -m flow_control.cli.run_starccm \
  --schedule runs/schedule_examples/pulse_singlejet/actuation_schedule.csv \
  --sim /path/to/input.sim \
  --out runs/starccm_pulse_singlejet \
  --starccm-path /path/to/starccm+ \
  --np 8

# 4. 如果 STAR 输出是独立 CSV，导入为标准 case
.venv/bin/python examples/ingest_star_case.py \
  --star-file /path/to/FZ.csv \
  --star-file /path/to/JetMassFlow.csv \
  --case-dir runs/my_star_case \
  --force \
  --jet \
  --case-type jet_on

# 5. 数据完整后，用明确的训练 case 训练 ARX
.venv/bin/python -m flow_control.cli.train_rom \
  --case-dir runs/my_star_train_case \
  --out runs/rom_star_demo/model

# 6. 用另一个明确的验证 case 输出验证结果
.venv/bin/python -m flow_control.cli.validate_rom \
  --model runs/rom_star_demo/model/arx_model.json \
  --case-dir runs/my_star_validation_case \
  --out runs/rom_star_demo
```

## 7. 快速检查命令

查看 schedule 入口是否可用：

```bash
.venv/bin/python -m flow_control.generator.schedule_generator --help
```

查看 STAR runner：

```bash
.venv/bin/python -m flow_control.cli.run_starccm --help
```

查看 ARX 入口：

```bash
.venv/bin/python -m flow_control.cli.train_rom --help
.venv/bin/python -m flow_control.cli.validate_rom --help
```

运行完整测试：

```bash
.venv/bin/python -m pytest
```

只测关键 flow_control 链路：

```bash
.venv/bin/python -m pytest \
  tests/test_actuation_schedule_generator.py \
  tests/test_mock_dynamic24x6.py \
  tests/test_flow_control_smoke.py \
  tests/test_flow_control_starccm_runner.py \
  tests/test_flow_control_rom.py \
  tests/test_case_data_loader.py
```

## 8. 常见判断

### 8.1 只有 schedule，能不能训练 ARX？

不能。schedule 只有输入，没有载荷输出。ARX 还需要 `timeseries.csv` 里的 `Fz_*` 输出。

### 8.2 只有 STAR 的 Fz CSV，能不能训练 ARX？

通常不能。只有输出，没有 `JET_*` 和 `cmd_massflow_*` 输入列，ARX 不知道是哪些喷气动作导致了响应。

### 8.3 mock 训练出来的 ARX 能不能用于真实控制？

不能直接用于真实控制。mock ARX 只能验证流程。真实控制需要用真实 STAR 或实验数据训练。

### 8.4 数据分析模块是不是训练模块？

不是。数据分析 / 导入模块负责把原始 STAR CSV 整理成标准 case、做质量检查和画图。ARX 模块才负责拟合模型。

### 8.5 flow_control STAR runner 和 generic_automation 有什么区别？

`flow_control.cli.run_starccm` 面向 24 喷口 schedule 控制；`ga.py` 面向原有 STAR 自动化主线，包括 case、sweep、monitor 和 RL 参数调整。
