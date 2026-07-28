# Week 3 Real Data Startup

本文件用于第三周真实 STAR 数据接入。第二周冻结版本是 `fe48df1c9b92279c3e10ccd560cf506372df99b7`，标签是 `week2_final_b31`；第三周真实数据接入工作在 `week3_real_data` 分支进行。

## 1. 环境准备

从仓库根目录执行：

```bash
git checkout week3_real_data

python3 -m venv .venv
.venv/bin/python -m pip install --upgrade pip
.venv/bin/python -m pip install -r requirements.txt
```

后续命令统一使用 `.venv/bin/python`，不要依赖系统里一定存在 `python`。

STAR-CCM+ 可执行文件不写死在仓库中。真实启动 STAR 前，可按本机环境设置：

```bash
export STARCCM_PATH=/path/to/starccm+
```

真实 `.sim` 文件路径只在本机命令行或本机私有配置中填写，不提交到仓库。

## 2. 只生成动作表

本节是独立用法，只验证动作生成器。执行本节后，输出目录是 `runs/week3_pulse_singlejet`。

```bash
.venv/bin/python -m flow_control.generator.schedule_generator \
  --config configs/actions/pulse_singlejet.yaml \
  --output-dir runs/week3_pulse_singlejet
```

输出：

```text
runs/week3_pulse_singlejet/input/actuation_schedule.csv
runs/week3_pulse_singlejet/input/config_summary.yaml
runs/week3_pulse_singlejet/input/validation_report.json
```

`config_summary.yaml` 会记录 `git_commit`、随机种子、窗口长度和动作模式。

## 3. 本地 mock 全流程演示

本节是 README 推荐的本地完整演示流程，不需要 STAR 许可证。执行本节后，标准 case 目录只应是 `runs/week3_mock_full_demo`。

用 mock 验证动作表、喷气检查表、标准 case 和质量检查链路：

```bash
.venv/bin/python -m flow_control.generator.schedule_generator \
  --config configs/actions/pulse_singlejet.yaml \
  --output-dir runs/week3_mock_full_demo

.venv/bin/python -m flow_control.cli.run_mock_dynamic24x6 \
  --schedule runs/week3_mock_full_demo/input/actuation_schedule.csv \
  --config configs/mock_dynamic24x6.yaml \
  --out runs/week3_mock_full_demo

printf 'runs/week3_mock_full_demo\nmock\n' | \
  .venv/bin/python examples/run_ccm_ingest_step2_check.py

printf 'runs/week3_mock_full_demo\n' | \
  .venv/bin/python examples/run_ccm_ingest_step3_figures.py
```

执行完成后，`runs/week3_mock_full_demo` 下应包含：

```text
input/actuation_schedule.csv
input/validation_report.json
quality_report.json
figures/force_timeseries.png
figures/jet_schedule.png
figures/massflow_check_01_06.png
figures/massflow_check_07_12.png
figures/massflow_check_13_18.png
figures/massflow_check_19_24.png
figures/quality_summary.png
```

不要同时保留 `runs/B01_mock_full_demo` 或 `runs/readme_real_data_check` 作为 README 输出目录；它们只是历史复现或临时验证时用过的本地目录名。

B01 是交付物编号；B01 clean clone 复现时已经跑通过一次完整演示，命令和日志在：

```text
docs/week3/B01_mock_full_demo_from_clean_clone.md
docs/week3/B01_mock_full_demo.log
docs/week3/B01_mock_full_demo_outputs/
```

该演示结果：

```text
jet_check_failures=0
quality_report_errors=0
run_success_flag=True
88 passed
```

## 4. 只整理已有 STAR 输出

推荐真实 STAR case 目录结构：

```text
runs/<case_dir>/
  input/actuation_schedule.csv
  raw_star/out_put/
    *.csv
```

当前 Step1 兼容旧目录名 `out_put/`。如果已有导出放在 `raw_star/out_put/`，先复制或链接到 `out_put/` 再运行 Step1：

```bash
mkdir -p runs/<case_dir>/out_put
cp runs/<case_dir>/raw_star/out_put/*.csv runs/<case_dir>/out_put/
```

执行三步，不启动 STAR：

```bash
printf 'runs/<case_dir>\n' | \
  .venv/bin/python examples/run_ccm_ingest_step1_timeseries.py

printf 'runs/<case_dir>\nccm\n' | \
  .venv/bin/python examples/run_ccm_ingest_step2_check.py

printf 'runs/<case_dir>\n' | \
  .venv/bin/python examples/run_ccm_ingest_step3_figures.py
```

主要输出：

```text
runs/<case_dir>/processed/timeseries.csv
runs/<case_dir>/case_manifest.yaml
runs/<case_dir>/quality_report.json
runs/<case_dir>/figures/
```

兼容旧 mock/ROM case 时，时序文件也可能在：

```text
runs/<case_dir>/timeseries.csv
```

`case_manifest.yaml` 和 `quality_report.json` 会记录 `git_commit`。

## 5. 只做数据检查

Step2 现在会先选择 case 目录，再选择检查模式。用管道输入时必须给两行：

```text
第 1 行：runs/<case_dir>
第 2 行：ccm 或 mock
```

如果已经执行过第 3 节 mock 全流程演示，下面这条命令可以直接点击运行：

```bash
printf 'runs/week3_mock_full_demo\nmock\n' | \
  .venv/bin/python examples/run_ccm_ingest_step2_check.py
```

这一步只更新 `quality_report.json`。如果需要同时生成质量诊断图表，继续运行：

```bash
printf 'runs/week3_mock_full_demo\n' | \
  .venv/bin/python examples/run_ccm_ingest_step3_figures.py
```

如果只想用编号交互选择，也可以直接运行脚本，然后在提示中选择 case 目录和检查模式：

```bash
.venv/bin/python examples/run_ccm_ingest_step2_check.py
```

真实 STAR/CCM 标准 case 不能直接点击下面的模板运行，需要先把 `<case_dir>` 替换成真实目录名：

```bash
printf 'runs/<case_dir>\nccm\n' | \
  .venv/bin/python examples/run_ccm_ingest_step2_check.py

printf 'runs/<case_dir>\n' | \
  .venv/bin/python examples/run_ccm_ingest_step3_figures.py
```

例如真实 case 放在 `runs/real_star/G01_JET01_existing` 时：

```bash
printf 'runs/real_star/G01_JET01_existing\nccm\n' | \
  .venv/bin/python examples/run_ccm_ingest_step2_check.py

printf 'runs/real_star/G01_JET01_existing\n' | \
  .venv/bin/python examples/run_ccm_ingest_step3_figures.py
```

Step2 检查项包括必选列、时间单调性、NaN、喷气开关和质量流量一致性、实际质量流量列、单位和方向提示。`ccm` 模式还会包含 B03/B04 真实 STAR/CCM 物理接口检查。Step3 会生成 `figures/force_timeseries.png`、`figures/jet_schedule.png`、`figures/massflow_check_*.png` 和 `figures/quality_summary.png`，并把图表路径写回 `quality_report.json`。

### 5.1 `mock` 和 `ccm` 的区别

`mock` 用于本地 mock/ROM case。它主要检查：

```text
case_manifest.yaml
actuation_schedule.csv
timeseries.csv 或 processed/timeseries.csv
quality_report.json
喷气开关 JET_01..JET_24
cmd_massflow_01..cmd_massflow_24
actual_massflow_01..actual_massflow_24
```

`ccm` 用于真实 STAR/CCM case。除了基础数据检查，还会检查：

```text
processed/timeseries.csv 是否存在
processed/、figures/、logs/ 标准目录是否存在
是否保留 raw STAR 或 CCM runtime 来源证据
docs/week3/B02_boundary_mapping.csv
docs/week3/B02_report_mapping.csv
B04 physics_consistency
```

因此：

```text
mock case 选 mock
真实 STAR/CCM case 选 ccm
不要为了让真实数据少报错而选 mock
```

### 5.2 如何判断检查是否通过

打开 `runs/<case_dir>/quality_report.json`，优先看这些字段：

```json
{
  "check_profile": "mock 或 ccm",
  "num_errors": 0,
  "num_warnings": 0,
  "run_success_flag": true
}
```

`ccm` 模式还需要看：

```json
{
  "num_ccm_contract_blocking_issues": 0,
  "num_physics_blocking_issues": 0
}
```

判定规则：

```text
mock 通过：num_errors=0 且 run_success_flag=true
ccm 通过：num_errors=0，num_ccm_contract_blocking_issues=0，num_physics_blocking_issues=0，且 run_success_flag=true
```

`warnings` 不一定阻塞，但必须阅读。比如 mock manifest 缺少 `units` 或 `sign_convention` 时会有 warning；真实 STAR/CCM 数据中，方向、边界或质量流量相关 warning 需要在 `docs/week3/B02_open_questions.md` 或 mapping 表里继续追踪。

### 5.3 直接查看报告摘要

如果检查的是第 3 节 mock 演示目录，可以直接运行：

```bash
.venv/bin/python - <<'PY'
import json
from pathlib import Path

report = json.loads(Path("runs/week3_mock_full_demo/quality_report.json").read_text(encoding="utf-8"))
for key in [
    "check_profile",
    "num_errors",
    "num_warnings",
    "run_success_flag",
]:
    print(f"{key}={report.get(key)}")
PY
```

真实 STAR/CCM case 需要先把 `<case_dir>` 替换成真实目录名：

```bash
.venv/bin/python - <<'PY'
import json
from pathlib import Path

report = json.loads(Path("runs/<case_dir>/quality_report.json").read_text(encoding="utf-8"))
for key in [
    "check_profile",
    "num_errors",
    "num_warnings",
    "num_ccm_contract_blocking_issues",
    "num_physics_blocking_issues",
    "run_success_flag",
]:
    print(f"{key}={report.get(key)}")
PY
```

## 6. 条件满足时启动 STAR

本节是真实 STAR-CCM+ 路径。执行成功后，真实运行输出目录是 `runs/starccm_week3_pulse_singlejet`，不是 mock 演示目录。

只有满足以下条件才启动真实 STAR：

- `actuation_schedule.csv` 已生成且 `validation_report.json` 通过。
- 本机有 STAR-CCM+ 许可证和可执行文件。
- 真实 `.sim` 文件存在。
- `--region`、喷气口边界 `J01..J24`、report 名称与 STAR 模型一致。
- 已确认算法列名 `JET_01..JET_24` 只是开关列，落到 STAR 控制时必须映射到喷气口 `J01..J24`，不能映射到底面区域 `JET01..JET24`。

命令：

```bash
.venv/bin/python -m flow_control.cli.run_starccm \
  --schedule runs/week3_pulse_singlejet/input/actuation_schedule.csv \
  --sim /path/to/input.sim \
  --out runs/starccm_week3_pulse_singlejet \
  --starccm-path "${STARCCM_PATH:-starccm+}" \
  --np 1 \
  --region Region
```

如只想检查宏和运行计划，可加 `--dry-run`，但仍需要提供存在的 `.sim` 文件。

## 7. 溯源规则

回答某个标准 case 由哪个代码版本生成时，优先查看：

```bash
.venv/bin/python - <<'PY'
import yaml
from pathlib import Path

manifest = yaml.safe_load(Path("runs/<case_dir>/case_manifest.yaml").read_text(encoding="utf-8"))
print(manifest.get("git_commit"))
PY
```

动作表生成版本查看：

```bash
.venv/bin/python - <<'PY'
import yaml
from pathlib import Path

summary = yaml.safe_load(Path("runs/<case_dir>/input/config_summary.yaml").read_text(encoding="utf-8"))
print(summary.get("git_commit"))
PY
```

第二周冻结版本：`fe48df1c9b92279c3e10ccd560cf506372df99b7` / `week2_final_b31`。

第三周真实数据分支：`week3_real_data`。
