# Mock 模块命令说明

## 统一规则

Mock 入口：

```bash
python examples/run_mock_from_action.py
python examples/run_mock_from_existing_dir.py
```

交互式脚本会负责提示动作或已有目录，并自动设置输出目录。

底层 CLI 也可以直接使用：

```bash
.venv/bin/python -m flow_control.cli.run_mock_dynamic24x6
```

底层 CLI 参数：

- `--out`：本次 mock case 的输出根目录。
- `--config`：mock 动态系统参数，默认是 `configs/mock_dynamic24x6.yaml`。
- `--actuation-config`：喷气动作 YAML。未提供 `--schedule` 时，会先调用 generator 生成输入表。
- `--schedule`：已有的 `actuation_schedule.csv`。提供它时，mock 不再调用 generator。

Mock 输出会写入：

```text
<out>/
```

如果由 mock 入口调用 generator，生成的输入固定写入：

```text
<out>/input/actuation_schedule.csv
```

## 模式 1：由动作配置生成输入并运行 mock

这是最常用模式。命令会先调用 generator，再把生成的动作表送入 MockDynamic24x6。

```bash
python examples/run_mock_from_action.py
```

调用链：

```text
configs/actions/pilot_sparse24.yaml
  -> generator 写 <out>/input/actuation_schedule.csv
  -> mock plant 读取该动作表
  -> mock plant 写 <out>/timeseries.csv 和质量报告
```

## 模式 2：使用已有动作表运行 mock

如果已经有 `actuation_schedule.csv`，可以选择 `runs` 下已有目录并直接在原目录写出 mock 结果。

```bash
python examples/run_mock_from_existing_dir.py
```

这种模式不会重新生成动作表，只会读取所选目录里的 `input/actuation_schedule.csv`
或 `actuation_schedule.csv`，并把 mock case 写回该目录。

## 常用动作配置

可以把 generator 的六种动作模式直接接到 mock：

```bash
.venv/bin/python -m flow_control.cli.run_mock_dynamic24x6 \
  --actuation-config configs/actions/no_jet_reference.yaml \
  --config configs/mock_dynamic24x6.yaml \
  --out runs/mock_no_jet_reference

.venv/bin/python -m flow_control.cli.run_mock_dynamic24x6 \
  --actuation-config configs/actions/pulse_singlejet.yaml \
  --config configs/mock_dynamic24x6.yaml \
  --out runs/mock_pulse_singlejet

.venv/bin/python -m flow_control.cli.run_mock_dynamic24x6 \
  --actuation-config configs/actions/step_singlejet.yaml \
  --config configs/mock_dynamic24x6.yaml \
  --out runs/mock_step_singlejet

.venv/bin/python -m flow_control.cli.run_mock_dynamic24x6 \
  --actuation-config configs/actions/chirp_keyjets.yaml \
  --config configs/mock_dynamic24x6.yaml \
  --out runs/mock_chirp_keyjets

.venv/bin/python -m flow_control.cli.run_mock_dynamic24x6 \
  --actuation-config configs/actions/prbs_demo.yaml \
  --config configs/mock_dynamic24x6.yaml \
  --out runs/mock_prbs_demo

.venv/bin/python -m flow_control.cli.run_mock_dynamic24x6 \
  --actuation-config configs/actions/pilot_sparse24.yaml \
  --config configs/mock_dynamic24x6.yaml \
  --out runs/mock_pilot_sparse24
```

## 一次运行全部六种动作

在项目根目录运行：

```bash
for name in no_jet_reference pulse_singlejet step_singlejet chirp_keyjets prbs_demo pilot_sparse24; do
  .venv/bin/python -m flow_control.cli.run_mock_dynamic24x6 \
    --actuation-config "configs/actions/${name}.yaml" \
    --config configs/mock_dynamic24x6.yaml \
    --out "runs/mock_examples/${name}"
done
```

每个 case 的主输出位于：

```text
runs/mock_examples/<模式名>/timeseries.csv
```

对应输入位于：

```text
runs/mock_examples/<模式名>/input/actuation_schedule.csv
```

## 输出文件

典型输出目录：

```text
<out>/
  input/
    actuation_schedule.csv
  actuation_schedule.csv
  timeseries.csv
  case_manifest.yaml
  quality_report.json
  config_used.yaml
  mock_dynamic24x6_summary.json
  figures/
    input_heatmap.svg
    fz_regions.svg
    fz_total.svg
    spatial_nonuniformity.svg
    total_massflow.svg
```

说明：

- `input/actuation_schedule.csv`：输入侧动作表，和 generator 的输出约定一致。
- `actuation_schedule.csv`：标准 case 根目录下的动作表副本，供后续 case schema 读取。
- `timeseries.csv`：mock 生成的 24 路喷气开关和 6 区域载荷时序。
- `quality_report.json`：mock case 的质量报告。
- `figures/`：输入热图、区域载荷、总升力、空间不均匀度和总质量流量图。

## 查看命令参数

```bash
.venv/bin/python -m flow_control.cli.run_mock_dynamic24x6 --help
```

## 快速验证

```bash
.venv/bin/python -m flow_control.cli.run_mock_dynamic24x6 \
  --actuation-config configs/actions/pulse_singlejet.yaml \
  --config configs/mock_dynamic24x6.yaml \
  --out /tmp/flow_control_mock_check
```

成功后应看到：

```text
/tmp/flow_control_mock_check/input/actuation_schedule.csv
/tmp/flow_control_mock_check/timeseries.csv
/tmp/flow_control_mock_check/quality_report.json
```
