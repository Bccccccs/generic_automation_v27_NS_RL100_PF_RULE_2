# ROM 模块命令说明

## 统一目录约定

ARX ROM 的多模型产物统一放在：

```text
runs/arx/
```

其中：

```text
runs/arx/trains/<模型名>        训练数据集
runs/arx/models/<模型名>        训练得到的 ARX 模型
runs/arx/vaild_cases/<模型名>   自动生成的验证数据集
runs/arx/validations/<模型名>   验证输出
```

## 1. 训练 ROM

启动脚本会要求输入模型名称，例如 `train01`。训练数据集会写到
`runs/arx/trains/train01`，模型会写到 `runs/arx/models/train01`。

```bash
python examples/run_rom_train.py
```

生成后目录类似：

```text
runs/arx/trains/train01/
  index.csv
  index.json
  sparse24_seed_<seed>/
    input/
      actuation_schedule.csv
    actuation_schedule.csv
    timeseries.csv
    quality_report.json
    mock_config_used.yaml
    figures/
runs/arx/models/train01/
  arx_model.json
  training_summary.json
```

`training_summary.json` 会记录：

```text
validation_performed: false
fit_policy: all usable rows from the explicitly supplied training set; no internal split
```

## 2. 验证 ROM

启动脚本提供两种验证方式：

- `existing-case`：选择当前已有标准 case，用其中的 `timeseries.csv` 真实输出计算预测误差。
- `auto-10-cases`：自动生成验证数据集 `runs/arx/vaild_cases/<模型名>`，再用 10 个 mock case 计算预测误差。

验证入口会先选择已训练模型，验证结果固定保存到 `runs/arx/validations/<模型名>`。

```bash
python examples/run_rom_validate.py
```

输出：

```text
runs/arx/validations/<模型名>/
  metrics.json
  prediction_timeseries.csv
  prediction_6_load_cells.svg
  error_6_load_cells.svg
  rmse_bar.svg
```

## 3. 使用已有 ARX ROM

使用脚本会罗列当前 `runs` 下包含 `actuation_schedule.csv` 且尚未生成
`timeseries.csv` 的纯 schedule 目录，输入编号或目录路径后，用
所选模型的 `arx_model.json` 对选中的 schedule 递推生成预测输出。

这个模式不要求输入目录已有 `timeseries.csv`。由于 ARX 需要前几步输出历史，
纯 schedule 预测会把前 `max_lag` 行输出初始化为 0，然后从第 `max_lag` 行开始递推预测。
预测结果会写回所选原目录，生成与 mock case 对齐的标准产物：
`timeseries.csv`、`case_manifest.yaml`、`quality_report.json`、`config_used.yaml`、
`mock_dynamic24x6_summary.json` 以及 `figures/*.svg`。

```bash
python examples/run_rom_use.py
```

输出文件：

```text
<选择的原 schedule 目录>/timeseries.csv
<选择的原 schedule 目录>/quality_report.json
<选择的原 schedule 目录>/figures/
```

## 4. 完整流程

从项目根目录依次运行：

```bash
python examples/run_rom_train.py
python examples/run_rom_validate.py
python examples/run_rom_use.py
```

完整输出会落在：

```text
runs/arx/
  trains/<模型名>/
  models/<模型名>/
  vaild_cases/<模型名>/
  validations/<模型名>/
```

## 5. 底层 CLI

如需手动调参数，也可以直接使用底层 CLI：

```bash
.venv/bin/python -m flow_control.cli.train_rom \
  --case-dir runs/mock_pilot_sparse24 \
  --out runs/arx/models/manual_single_case
```

也可以直接使用模型对纯 schedule 输出预测 case：

```bash
.venv/bin/python -m flow_control.cli.use_rom \
  --model runs/arx/models/train01/arx_model.json \
  --schedule runs/pulse_singlejet/input/actuation_schedule.csv \
  --out runs/pulse_singlejet
```

也可以直接验证：

```bash
.venv/bin/python -m flow_control.cli.validate_rom \
  --model runs/arx/models/train01/arx_model.json \
  --case-dir runs/mock_pilot_sparse24 \
  --out runs/arx/validations/train01
```

## 6. 查看命令参数

```bash
.venv/bin/python -m flow_control.rom.generate_arx_dataset --help
.venv/bin/python -m flow_control.cli.train_rom --help
.venv/bin/python -m flow_control.cli.use_rom --help
.venv/bin/python -m flow_control.cli.validate_rom --help
```
