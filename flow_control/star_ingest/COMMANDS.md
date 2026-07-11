# STAR Ingest 模块命令说明

## 模块边界

`star_ingest` 负责把 STAR-CCM+ 导出的 CSV 或 product 目录整理成标准 case：

```text
STAR CSV / STAR product dir
  -> timeseries.csv
  -> actuation_schedule.csv
  -> input/actuation_schedule.csv
  -> case_manifest.yaml
  -> quality_report.json
  -> figures/
```

现在推荐入口都放在本模块下：

```text
flow_control/star_ingest/pipeline.py                  一步完成
flow_control/star_ingest/step1_generate_timeseries.py 三步流程 Step 1
flow_control/star_ingest/step2_check_case.py          三步流程 Step 2
flow_control/star_ingest/step3_generate_figures.py    三步流程 Step 3
```

`examples/ingest_star_case.py` 只保留为兼容包装，内部转发到 `flow_control.star_ingest.pipeline`。

## 输出目录约定

STAR case 推荐写到：

```text
runs/star_ingest/<case_name>/
```

典型输出结构：

```text
runs/star_ingest/<case_name>/
  input/
    actuation_schedule.csv
  figures/
    force_timeseries.png
    jet_schedule.png
    massflow_check.png
    quality_summary.png
  logs/
  flow_snapshots/
  actuation_schedule.csv
  case_manifest.yaml
  quality_report.json
  timeseries.csv
  notes.md
```

## 模式 1：一步完成

一步入口会完成：

```text
读取 STAR 导出
  -> 写标准 case
  -> 写 quality_report.json
  -> 生成 figures
```

### 从 STAR product 目录导入

```bash
.venv/bin/python -m flow_control.star_ingest.pipeline \
  --star-dir runs/star_product/no_jet_reference \
  --case-dir runs/star_ingest/no_jet_reference \
  --case-type no_jet \
  --check-mode star_ingest \
  --force
```

### 从单个 STAR CSV 导入

```bash
.venv/bin/python -m flow_control.star_ingest.pipeline \
  --star-file runs/star_product/no_jet_reference/FZ.csv \
  --case-dir runs/star_ingest/no_jet_reference \
  --case-type no_jet \
  --check-mode star_ingest \
  --force
```

### 从多个 STAR CSV 合并导入

多个 CSV 会按 `physical_time` 合并：

```bash
.venv/bin/python -m flow_control.star_ingest.pipeline \
  --star-file runs/star_product/jet_case/FZ.csv \
  --star-file runs/star_product/jet_case/Drag.csv \
  --star-file runs/star_product/jet_case/Pitch.csv \
  --star-file runs/star_product/jet_case/Roll.csv \
  --case-dir runs/star_ingest/jet_case \
  --case-type jet_on \
  --check-mode star_ingest \
  --force
```

### 部分 timeseries 导入

如果当前只拿到部分 STAR monitor，例如只有 Fz，没有 drag/moment/jet 列，可以先用 `--partial`：

```bash
.venv/bin/python -m flow_control.star_ingest.pipeline \
  --star-file runs/star_product/partial/FZ.csv \
  --case-dir runs/star_ingest/partial_fz \
  --case-type unknown \
  --partial \
  --check-mode star_ingest \
  --force
```

`--partial` 会把 manifest 中的 `validation_mode` 标记为 `partial_timeseries`，质量检查只强制要求 `physical_time`。

## 模式 2：三步执行

三步入口适合调试真实 STAR 导出时使用。每一步都有明确输出，方便定位是哪一步出错。

### Step 1：生成 timeseries 和标准 case 骨架

从 product 目录导入：

```bash
.venv/bin/python -m flow_control.star_ingest.step1_generate_timeseries \
  --star-dir runs/star_product/no_jet_reference \
  --case-dir runs/star_ingest/no_jet_reference \
  --case-type no_jet \
  --check-mode star_ingest \
  --force
```

从一个或多个 CSV 导入：

```bash
.venv/bin/python -m flow_control.star_ingest.step1_generate_timeseries \
  --star-file runs/star_product/jet_case/FZ.csv \
  --star-file runs/star_product/jet_case/Drag.csv \
  --case-dir runs/star_ingest/jet_case \
  --case-type jet_on \
  --check-mode star_ingest \
  --force
```

如果是部分数据：

```bash
.venv/bin/python -m flow_control.star_ingest.step1_generate_timeseries \
  --star-file runs/star_product/partial/FZ.csv \
  --case-dir runs/star_ingest/partial_fz \
  --case-type unknown \
  --partial \
  --check-mode star_ingest \
  --force
```

### Step 2：质量检查

完整 case 检查：

```bash
.venv/bin/python -m flow_control.star_ingest.step2_check_case \
  --case-dir runs/star_ingest/no_jet_reference \
  --check-mode star_ingest
```

部分 timeseries 检查：

```bash
.venv/bin/python -m flow_control.star_ingest.step2_check_case \
  --case-dir runs/star_ingest/partial_fz \
  --partial \
  --check-mode star_ingest
```

### Step 3：生成诊断图

完整 case：

```bash
.venv/bin/python -m flow_control.star_ingest.step3_generate_figures \
  --case-dir runs/star_ingest/no_jet_reference
```

部分 timeseries：

```bash
.venv/bin/python -m flow_control.star_ingest.step3_generate_figures \
  --case-dir runs/star_ingest/partial_fz \
  --partial
```

## 使用 case-id

三步入口支持 `--case-id` 和 `--runs-root`，等价于写入或读取 `<runs-root>/<case-id>`：

```bash
.venv/bin/python -m flow_control.star_ingest.step1_generate_timeseries \
  --star-dir runs/star_product/no_jet_reference \
  --case-id no_jet_reference \
  --runs-root runs/star_ingest \
  --case-type no_jet \
  --check-mode star_ingest \
  --force

.venv/bin/python -m flow_control.star_ingest.step2_check_case \
  --case-id no_jet_reference \
  --runs-root runs/star_ingest \
  --check-mode star_ingest

.venv/bin/python -m flow_control.star_ingest.step3_generate_figures \
  --case-id no_jet_reference \
  --runs-root runs/star_ingest
```

一步入口推荐直接使用 `--case-dir`，避免真实 STAR 导入时目录含义不清。

## 检查模式 check-mode

`check_mode` 表示当前 case 来自哪条链路，会写入 `case_manifest.yaml` 和 `quality_report.json`。

可选值：

```text
star_ingest   真实 STAR 导出 CSV/product 目录导入
mock          mock plant 生成的标准 case
arx_use       ARX 使用模型输出的预测 case
ccm           CCM runtime 先生成原始 flow_control_timeseries.csv，再整理出的标准 case
```

区别：

- `star_ingest`：真实 STAR 数据，检查更偏向发现导出缺列、单位和方向说明问题。
- `mock`：本地虚拟模型，允许 `actual_massflow` 与 `cmd_massflow` 完全一致。
- `arx_use`：模型预测 case，允许 `actual_massflow` 与 `cmd_massflow` 完全一致。
- `ccm`：CCM runtime 输出不是完整标准 timeseries，会先整理成标准 case，再由检查报告指出缺失列。

## 什么时候用一步，什么时候用三步

推荐规则：

- 日常导入一个完整 STAR case：用 `pipeline` 一步完成。
- 调试新 STAR 导出格式：用 Step 1 / Step 2 / Step 3 三步执行。
- 只有部分 monitor CSV：加 `--partial`，先形成可检查的中间 case。
- 需要看每一步产物是否正确：用三步执行。

## 查看命令参数

```bash
.venv/bin/python -m flow_control.star_ingest.pipeline --help
.venv/bin/python -m flow_control.star_ingest.step1_generate_timeseries --help
.venv/bin/python -m flow_control.star_ingest.step2_check_case --help
.venv/bin/python -m flow_control.star_ingest.step3_generate_figures --help
```
