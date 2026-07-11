# ROM 模块命令说明

## 统一目录约定

ARX ROM 的随机数据集统一放在：

```text
runs/arx/
```

其中：

```text
runs/arx/arx_training   训练数据集
runs/arx/arx_valid      验证数据集
```

推荐把模型和验证结果也放在同一个根目录下：

```text
runs/arx/model          训练得到的 ARX 模型
runs/arx/prediction     使用模型输出的预测 case
runs/arx/validation     验证输出
```

## 1. 生成训练数据集

训练数据集使用 sparse24 动作配置，先调用 generator 生成动作表，再调用 mock 生成标准 case。

```bash
.venv/bin/python -m flow_control.rom.generate_arx_dataset \
  --actuation-config configs/actions/pilot_sparse24.yaml \
  --mock-config configs/mock_dynamic24x6.yaml \
  --out runs/arx/arx_training \
  --count 100 \
  --overwrite
```

如果不指定 `--start-seed`，会从共享系统配置里的 `system.random_seed` 开始递增。

生成后目录类似：

```text
runs/arx/arx_training/
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
```

## 2. 生成验证数据集

验证数据集必须使用和训练集不同的 seed 段。比如训练集从默认 seed 开始生成 100 个 case，则验证集可以从后续 seed 开始：

```bash
.venv/bin/python -m flow_control.rom.generate_arx_dataset \
  --actuation-config configs/actions/pilot_sparse24.yaml \
  --mock-config configs/mock_dynamic24x6.yaml \
  --out runs/arx/arx_valid \
  --count 10 \
  --start-seed 20260718 \
  --overwrite
```

生成后目录类似：

```text
runs/arx/arx_valid/
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
```

## 3. 训练 ARX ROM

训练阶段只做拟合，不做验证，也不会在训练数据内部切分 validation set。

```bash
.venv/bin/python -m flow_control.cli.train_rom \
  --dataset-dir runs/arx/arx_training \
  --out runs/arx/model \
  --input-lags 2 \
  --output-lags 3 \
  --ridge-alpha 1.0
```

训练输出：

```text
runs/arx/model/
  arx_model.json
  training_summary.json
```

`training_summary.json` 会记录：

```text
validation_performed: false
fit_policy: all usable rows from the explicitly supplied training set; no internal split
```

## 4. 使用已有 ARX ROM

使用阶段加载已经训练好的 `arx_model.json`，读取一个标准 case 的输入序列，输出一个新的预测 case。

```bash
.venv/bin/python -m flow_control.cli.use_rom \
  --model runs/arx/model/arx_model.json \
  --case-dir runs/arx/arx_valid/sparse24_seed_20260718 \
  --out runs/arx/prediction
```

输出：

```text
runs/arx/prediction/
  input/
    actuation_schedule.csv
  actuation_schedule.csv
  timeseries.csv
  case_manifest.yaml
  quality_report.json
```

说明：

- 前 `max_lag` 行作为 ARX 历史 warmup。
- 后续行使用 ARX 递推预测输出。
- 输出 case 会经过 `star_ingest` 检查，`quality_report.json` 中记录 `check_mode: arx_use`。

## 5. 验证已有 ARX ROM

验证阶段加载已经训练好的 `arx_model.json`，在独立验证数据集上递推预测。

```bash
.venv/bin/python -m flow_control.cli.validate_rom \
  --model runs/arx/model/arx_model.json \
  --dataset-dir runs/arx/arx_valid \
  --out runs/arx/validation
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

## 6. 完整流程

从项目根目录依次运行：

```bash
.venv/bin/python -m flow_control.rom.generate_arx_dataset \
  --actuation-config configs/actions/pilot_sparse24.yaml \
  --mock-config configs/mock_dynamic24x6.yaml \
  --out runs/arx/arx_training \
  --count 100 \
  --overwrite

.venv/bin/python -m flow_control.rom.generate_arx_dataset \
  --actuation-config configs/actions/pilot_sparse24.yaml \
  --mock-config configs/mock_dynamic24x6.yaml \
  --out runs/arx/arx_valid \
  --count 10 \
  --start-seed 20260718 \
  --overwrite

.venv/bin/python -m flow_control.cli.train_rom \
  --dataset-dir runs/arx/arx_training \
  --out runs/arx/model \
  --input-lags 2 \
  --output-lags 3 \
  --ridge-alpha 1.0

.venv/bin/python -m flow_control.cli.use_rom \
  --model runs/arx/model/arx_model.json \
  --case-dir runs/arx/arx_valid/sparse24_seed_20260718 \
  --out runs/arx/prediction

.venv/bin/python -m flow_control.cli.validate_rom \
  --model runs/arx/model/arx_model.json \
  --dataset-dir runs/arx/arx_valid \
  --out runs/arx/validation
```

完整输出会落在：

```text
runs/arx/
  arx_training/
  arx_valid/
  model/
  prediction/
  validation/
```

## 7. 单个 case 训练、使用或验证

如果已经有一个标准 case，也可以直接训练：

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

## 8. 查看命令参数

```bash
.venv/bin/python -m flow_control.rom.generate_arx_dataset --help
.venv/bin/python -m flow_control.cli.train_rom --help
.venv/bin/python -m flow_control.cli.use_rom --help
.venv/bin/python -m flow_control.cli.validate_rom --help
```
