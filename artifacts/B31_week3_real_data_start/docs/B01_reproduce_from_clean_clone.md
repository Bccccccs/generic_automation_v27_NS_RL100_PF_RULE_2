# B01 Reproduce From Clean Clone

## 固定版本

- 第二周最终 commit: `fe48df1c9b92279c3e10ccd560cf506372df99b7`
- 第二周 Git tag: `week2_final_b31`
- 第三周工作分支: `week3_real_data`
- 本次 clean clone 验证 commit: `6918c023f00cfca1c5e319d89f958f083b8e68a7`
- clean clone 目录: `/Users/yanbochao/week3_b31_runs_clean_clone_20260727075455`

## 复现命令

```bash
git clone --branch week3_real_data \
  /Users/yanbochao/generic_automation_v27_NS_RL100_PF_RULE_2 \
  /Users/yanbochao/week3_b31_runs_clean_clone_20260727075455

cd /Users/yanbochao/week3_b31_runs_clean_clone_20260727075455
git branch --show-current
git rev-parse HEAD
git rev-list -n 1 week2_final_b31

python3 -m venv .venv
.venv/bin/python -m pip install --upgrade pip
.venv/bin/python -m pip install -r requirements.txt
```

只生成动作表：

```bash
.venv/bin/python -m flow_control.generator.schedule_generator \
  --config configs/actions/pulse_singlejet.yaml \
  --output-dir runs/week3_clean_schedule
```

准备已有 STAR 输出目录，并只做整理、检查和画图，不启动 STAR：

```bash
mkdir -p runs/week3_clean_star_ingest/input runs/week3_clean_star_ingest/out_put
cp runs/B31_source_star_export/input/actuation_schedule.csv \
  runs/week3_clean_star_ingest/input/actuation_schedule.csv
cp runs/B31_source_star_export/out_put/*.csv \
  runs/week3_clean_star_ingest/out_put/

printf 'runs/week3_clean_star_ingest\n' | .venv/bin/python examples/run_ccm_ingest_step1_timeseries.py
printf 'runs/week3_clean_star_ingest\n' | .venv/bin/python examples/run_ccm_ingest_step2_check.py
printf 'runs/week3_clean_star_ingest\n' | .venv/bin/python examples/run_ccm_ingest_step3_figures.py
```

运行测试：

```bash
.venv/bin/python -m pytest -q
```

检查溯源字段：

```bash
.venv/bin/python - <<'PY'
import json
import yaml
from pathlib import Path

summary = yaml.safe_load(Path("runs/week3_clean_schedule/input/config_summary.yaml").read_text(encoding="utf-8"))
manifest = yaml.safe_load(Path("runs/week3_clean_star_ingest/case_manifest.yaml").read_text(encoding="utf-8"))
report = json.loads(Path("runs/week3_clean_star_ingest/quality_report.json").read_text(encoding="utf-8"))

print("schedule_git_commit=" + str(summary.get("git_commit")))
print("case_manifest_git_commit=" + str(manifest.get("git_commit")))
print("quality_report_errors=" + str(report.get("num_errors")))
print("quality_report_warnings=" + str(report.get("num_warnings")))
PY
```

## 结果

- 动作表生成成功: `runs/week3_clean_schedule/input/actuation_schedule.csv`
- STAR 输出整理成功: `runs/week3_clean_star_ingest/timeseries.csv`
- 质量检查通过: `quality_report_errors=0`, `quality_report_warnings=0`
- 测试通过: `80 passed in 8.68s`
- 动作表 `config_summary.yaml` 记录 `git_commit=6918c023f00cfca1c5e319d89f958f083b8e68a7`
- 标准 case `case_manifest.yaml` 记录 `git_commit=6918c023f00cfca1c5e319d89f958f083b8e68a7`

完整运行日志见 `pytest_week3_start.log`。
