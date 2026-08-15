# Flow Control Workflow

项目只保留一个用户启动入口：

```bash
python scripts/workflow.py <command> [options]
```

查看所有流程：

```bash
python scripts/workflow.py --help
```

## 1. 生成动作表

```bash
python scripts/workflow.py actions \
  --config configs/week4/G01_J02_pulse.yaml \
  --output-dir runs/week4/J02_pulse_1kgps_1s_dt1e-4
```

主输出：

```text
<output-dir>/input/actuation_schedule.csv
<output-dir>/input/config_summary.yaml
<output-dir>/input/validation_report.json
```

`actuation_schedule.csv` 一行对应一个求解器物理时间步。连续行共用同一
`window_id` 时，它们属于同一个喷气动作窗口，窗口内指令必须保持不变。

## 2. Mock 仿真

使用已有动作表：

```bash
python scripts/workflow.py mock \
  --schedule runs/week4/J02_pulse_1kgps_1s_dt1e-4/input/actuation_schedule.csv \
  --config configs/mock_dynamic24x6.yaml \
  --out runs/week4/J02_mock
```

也可以直接使用动作 YAML：

```bash
python scripts/workflow.py mock \
  --actuation-config configs/week4/G01_J02_pulse.yaml \
  --config configs/mock_dynamic24x6.yaml \
  --out runs/week4/J02_mock
```

## 3. CCM 宏生成与仿真

只生成 Java 宏和 runtime plan：

```bash
python scripts/workflow.py ccm \
  --schedule runs/week4/J02_pulse_1kgps_1s_dt1e-4/input/actuation_schedule.csv \
  --sim /path/to/frozen.sim \
  --out runs/week4/J02_case/raw_star \
  --time-step 0.0001 \
  --execution-mode dry-run
```

真实启动 STAR-CCM+：

```bash
python scripts/workflow.py ccm \
  --schedule <actuation_schedule.csv> \
  --sim <frozen.sim> \
  --out <case-dir>/raw_star \
  --starccm-path <starccm+> \
  --execution-mode run
```

执行端会将相同 `window_id` 的物理时间步聚合成一个动作窗口，并只在窗口末端采样。

## 4. 整理 CCM 输出

输入必须是一个以动作表为中心的工作目录：

```text
<input-dir>/
  input/actuation_schedule.csv
  raw_star/out_put/*.csv
```

`actuation_schedule.csv` 也可以放在 `<input-dir>` 根目录；CCM 监视器 CSV
也可以放在 `raw_star/`、`out_put/` 或输入根目录。程序会自动识别动作类型。

```bash
python scripts/workflow.py organize \
  --input-dir runs/week4/J02_work \
  --output-dir runs/week4/J02_standard_case
```

该步只整理数据，不做最终验收；成功时必须生成
`<output-dir>/processed/timeseries.csv`。

## 5. 质量检查和图表生成

```bash
python scripts/workflow.py check \
  --case-dir runs/week4/G00_nojet_baseline \
  --mode ccm
```

输出：

```text
quality_report.json
figures/force_timeseries.png
figures/jet_schedule.png
figures/massflow_check_01_06.png
figures/massflow_check_07_12.png
figures/massflow_check_13_18.png
figures/massflow_check_19_24.png
figures/quality_summary.png
```

## 标准 Case 结构

```text
<case-dir>/
  input/
    actuation_schedule.csv
  raw_star/
    out_put/
      *.csv
  processed/
    timeseries.csv
  figures/
    *.png
  actuation_schedule.csv
  case_manifest.yaml
  quality_report.json
  notes.md
```

## 测试

```bash
python -m pytest -q
```
