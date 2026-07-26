# Week 3 Real Data Startup

本文件用于第三周真实 STAR 数据接入。第二周冻结版本是 `fe48df1`，标签是 `week2_final_b31`；第三周所有真实数据接入工作在 `week3_real_data` 分支进行。

## 1. 环境准备

```bash
git checkout week3_real_data
python3 -m venv .venv
. .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
```

仓库内不再写死个人电脑或集群绝对路径。STAR-CCM+ 可执行文件默认使用 `starccm+`，也可以运行前设置：

```bash
export STARCCM_PATH=/path/to/starccm+
```

真实 `.sim` 文件路径只在本机运行配置或命令行中填写，不提交到仓库。

## 2. 只生成动作表

```bash
python -m flow_control.generator.schedule_generator \
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

## 3. 只整理已有 STAR 输出

前提目录结构：

```text
runs/<case_dir>/
  input/actuation_schedule.csv
  out_put/
    *.csv
```

执行三步：

```bash
printf 'runs/<case_dir>\n' | python examples/run_ccm_ingest_step1_timeseries.py
printf 'runs/<case_dir>\n' | python examples/run_ccm_ingest_step2_check.py
printf 'runs/<case_dir>\n' | python examples/run_ccm_ingest_step3_figures.py
```

输出：

```text
runs/<case_dir>/timeseries.csv
runs/<case_dir>/case_manifest.yaml
runs/<case_dir>/quality_report.json
runs/<case_dir>/figures/
```

`case_manifest.yaml` 和 `quality_report.json` 会记录 `git_commit`。

## 4. 只做数据检查

已有标准 case 时，只运行质量检查：

```bash
printf 'runs/<case_dir>\n' | python examples/run_ccm_ingest_step2_check.py
```

检查项包括必选列、时间单调性、NaN、喷气开关和质量流量一致性、实际质量流量列、单位和方向提示。

## 5. 条件满足时启动 STAR

只有满足以下条件才启动真实 STAR：

- `actuation_schedule.csv` 已生成且 `validation_report.json` 通过。
- 本机有 STAR-CCM+ 许可证和可执行文件。
- 真实 `.sim` 文件存在。
- `--region`、喷口边界、report 名称与 STAR 模型一致。

命令：

```bash
python -m flow_control.cli.run_starccm \
  --schedule runs/week3_pulse_singlejet/input/actuation_schedule.csv \
  --sim /path/to/input.sim \
  --out runs/starccm_week3_pulse_singlejet \
  --starccm-path "${STARCCM_PATH:-starccm+}" \
  --np 1 \
  --region Region
```

如只想检查宏和运行计划，可加 `--dry-run`，但仍需要提供存在的 `.sim` 文件。

## 6. 溯源规则

回答某个标准 case 由哪个代码版本生成时，优先查看：

```bash
python - <<'PY'
import yaml
from pathlib import Path
manifest = yaml.safe_load(Path("runs/<case_dir>/case_manifest.yaml").read_text())
print(manifest.get("git_commit"))
PY
```

第二周冻结版本：`fe48df1` / `week2_final_b31`。

第三周真实数据分支：`week3_real_data`。
