# ROM 模块命令说明

## 统一目录约定

ARX ROM 的随机数据集统一放在：

```text
runs/arx/
```

其中：

```text
runs/arx/training   训练数据集
runs/arx/vaild      验证数据集
```

推荐把模型和验证结果也放在同一个根目录下：

```text
runs/arx/model          训练得到的 ARX 模型
runs/arx/use_<目录名>   使用模型输出的预测 case
runs/arx/validation     验证输出
```

## 1. 训练 ROM

启动脚本会固定生成训练数据集 `runs/arx/training`，然后训练模型到 `runs/arx/model`。

```bash
python examples/run_rom_train.py
```

生成后目录类似：

```text
runs/arx/training/
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
runs/arx/model/
  arx_model.json
  training_summary.json
```

`training_summary.json` 会记录：

```text
validation_performed: false
fit_policy: all usable rows from the explicitly supplied training set; no internal split
```

## 2. 验证 ROM

启动脚本会固定生成验证数据集 `runs/arx/vaild`，然后用 `runs/arx/model/arx_model.json`
输出验证结果到 `runs/arx/validation`。

```bash
python examples/run_rom_validate.py
```

输出：

```text
runs/arx/validation/
  metrics.json
  prediction_timeseries.csv
  prediction_6_load_cells.svg
  error_6_load_cells.svg
  rmse_bar.svg
```

## 3. 使用已有 ARX ROM

使用脚本会罗列当前 `runs` 下包含 `timeseries.csv` 的 case 目录，输入编号或目录路径后，
用 `runs/arx/model/arx_model.json` 对选中的目录生成预测 case。

```bash
python examples/run_rom_use.py
```

输出目录：

```text
runs/arx/use_<选择的目录名>/
```

验证输出：

```text
runs/arx/validation/
  metrics.json
  prediction_timeseries.csv
  prediction_6_load_cells.svg
  error_6_load_cells.svg
  rmse_bar.svg
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
  training/
  vaild/
  model/
  use_<目录名>/
  validation/
```

## 5. 底层 CLI

如需手动调参数，也可以直接使用底层 CLI：

```bash
.venv/bin/python -m flow_control.cli.train_rom \
  --case-dir runs/mock_pilot_sparse24 \
  --out runs/arx/model_single_case
```

也可以直接使用模型输出预测 case：

```bash
.venv/bin/python -m flow_control.cli.use_rom \
  --model runs/arx/model/arx_model.json \
  --case-dir runs/mock_pilot_sparse24 \
  --out runs/arx/prediction_single_case
```

也可以直接验证：

```bash
.venv/bin/python -m flow_control.cli.validate_rom \
  --model runs/arx/model/arx_model.json \
  --case-dir runs/mock_pilot_sparse24 \
  --out runs/arx/validation_single_case
```

## 6. 查看命令参数

```bash
.venv/bin/python -m flow_control.rom.generate_arx_dataset --help
.venv/bin/python -m flow_control.cli.train_rom --help
.venv/bin/python -m flow_control.cli.use_rom --help
.venv/bin/python -m flow_control.cli.validate_rom --help
```
