# B01 Mock Full Demo From Clean Clone

## 目的

从一个新的临时目录 clone `week3_real_data`，重新加载 Python 环境，生成动作表和喷气检查表，启动 mock，并重新运行数据质量检查和全量 pytest。

## 临时目录

```text
/Users/yanbochao/week3_B01_mock_clean_clone_20260727212651
```

## 命令

```bash
SOURCE_REPO="/Users/yanbochao/generic_automation_v27_NS_RL100_PF_RULE_2"
CLEAN_ROOT="/Users/yanbochao/week3_B01_mock_clean_clone_20260727212651"

git clone --branch week3_real_data "$SOURCE_REPO" "$CLEAN_ROOT"
cd "$CLEAN_ROOT"

git branch --show-current
git rev-parse HEAD
git rev-list -n 1 week2_final_b31

python3 -m venv .venv
.venv/bin/python -m pip install --upgrade pip
.venv/bin/python -m pip install -r requirements.txt
```

生成动作表：

```bash
.venv/bin/python -m flow_control.generator.schedule_generator \
  --config configs/actions/pulse_singlejet.yaml \
  --output-dir runs/B01_mock_full_demo
```

生成喷气检查表：

```bash
.venv/bin/python - <<'PY'
import csv
from pathlib import Path

schedule_path = Path("runs/B01_mock_full_demo/input/actuation_schedule.csv")
check_path = Path("runs/B01_mock_full_demo/input/jet_check_table.csv")
rows = list(csv.DictReader(schedule_path.open(newline="", encoding="utf-8")))
fieldnames = [
    "jet",
    "switch_column",
    "cmd_massflow_column",
    "activation_count",
    "command_nonzero_count",
    "max_cmd_massflow",
    "switch_massflow_errors",
    "status",
]
check_rows = []
for idx in range(1, 25):
    switch_col = f"JET_{idx:02d}"
    mass_col = f"cmd_massflow_{idx:02d}"
    activation_count = 0
    command_nonzero_count = 0
    max_mass = 0.0
    errors = []
    for row_idx, row in enumerate(rows):
        switch = float(row.get(switch_col, 0) or 0)
        mass = float(row.get(mass_col, 0) or 0)
        if switch > 0.5:
            activation_count += 1
        if abs(mass) > 0.0:
            command_nonzero_count += 1
        max_mass = max(max_mass, mass)
        if switch <= 0.5 and abs(mass) > 1e-12:
            errors.append(f"row {row_idx}: off switch has massflow {mass}")
        if switch > 0.5 and mass <= 0.0:
            errors.append(f"row {row_idx}: on switch has nonpositive massflow {mass}")
    check_rows.append({
        "jet": f"JET_{idx:02d}",
        "switch_column": switch_col,
        "cmd_massflow_column": mass_col,
        "activation_count": activation_count,
        "command_nonzero_count": command_nonzero_count,
        "max_cmd_massflow": max_mass,
        "switch_massflow_errors": "; ".join(errors),
        "status": "pass" if not errors else "fail",
    })
with check_path.open("w", newline="", encoding="utf-8") as handle:
    writer = csv.DictWriter(handle, fieldnames=fieldnames)
    writer.writeheader()
    writer.writerows(check_rows)
print(f"jet_check_table: {check_path}")
print(f"jet_check_rows: {len(check_rows)}")
print(f"jet_check_failures: {sum(row['status'] != 'pass' for row in check_rows)}")
PY
```

启动 mock：

```bash
.venv/bin/python -m flow_control.cli.run_mock_dynamic24x6 \
  --schedule runs/B01_mock_full_demo/input/actuation_schedule.csv \
  --config configs/mock_dynamic24x6.yaml \
  --out runs/B01_mock_full_demo
```

重新运行数据质量检查：

```bash
.venv/bin/python - <<'PY'
from pathlib import Path
from flow_control.star_ingest.case_data_loader import write_quality_report

case_dir = Path("runs/B01_mock_full_demo")
report = write_quality_report(case_dir, require_complete_schema=True, check_mode="mock")
print(f"quality_report: {case_dir / 'quality_report.json'}")
print(f"quality_report_errors={report['num_errors']}")
print(f"quality_report_warnings={report['num_warnings']}")
print(f"run_success_flag={report.get('run_success_flag')}")
PY
```

检查输出和溯源字段：

```bash
.venv/bin/python - <<'PY'
import json
import yaml
from pathlib import Path

case_dir = Path("runs/B01_mock_full_demo")
summary = yaml.safe_load((case_dir / "input" / "config_summary.yaml").read_text(encoding="utf-8"))
manifest = yaml.safe_load((case_dir / "case_manifest.yaml").read_text(encoding="utf-8"))
quality = json.loads((case_dir / "quality_report.json").read_text(encoding="utf-8"))
print("schedule_git_commit=" + str(summary.get("git_commit")))
print("case_manifest_git_commit=" + str(manifest.get("git_commit")))
print("quality_report_run_success_flag=" + str(quality.get("run_success_flag")))
PY
```

运行测试：

```bash
.venv/bin/python -m pytest -q
```

## 结果

- clean clone commit: `f82ee15198966a1079b478902d53de594f14c634`
- 第二周 tag 指向: `fe48df1c9b92279c3e10ccd560cf506372df99b7`
- 动作表: `runs/B01_mock_full_demo/input/actuation_schedule.csv`
- 喷气检查表: `runs/B01_mock_full_demo/input/jet_check_table.csv`
- 喷气检查结果: `jet_check_rows=24`, `jet_check_failures=0`
- mock 标准 case: `runs/B01_mock_full_demo/`
- 数据质量检查: `quality_report_errors=0`, `quality_report_warnings=2`, `run_success_flag=True`
- warning 内容: manifest 未记录 `units` 和 `sign_convention`，这是 mock manifest 元数据提示，不影响本次数据结构检查通过。
- pytest: `88 passed in 10.13s`

完整日志见 `docs/week3/B01_mock_full_demo.log`。

关键输出副本保存在 `docs/week3/B01_mock_full_demo_outputs/`。
