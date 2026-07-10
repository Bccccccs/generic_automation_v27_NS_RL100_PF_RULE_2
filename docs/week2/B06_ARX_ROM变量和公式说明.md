# B06 ROM 变量和公式说明

## 1. 这一步在做什么

B06 做的是一个最小输入-输出 ARX ROM。它不是强化学习控制器，也不是最终物理模型，而是一个轻量的被控对象响应模型：

```text
过去喷气输入 + 过去载荷输出 -> 当前/下一步载荷输出
```

本周目标是验证 ROM 代码流程：读数据、批量生成 mock 训练数据、训练 ARX、加载已有模型做递推验证、保存指标和画图。Mock 数据用于训练和验证；真实 STAR 数据本周只用于读取、画图、质量检查和单喷气响应摘要。

代码已经收拢到 `flow_control.rom` 模块：

- `flow_control/rom/arx_model.py`：ARX 模型本体。
- `flow_control/rom/identifier.py`：CSV 读取、输入/输出列定义、指标计算和图表输出。
- `flow_control/rom/generate_arx_dataset.py`：批量生成 sparse24 schedule + mock case，用于准备训练/验证数据。
- `flow_control/rom/training.py`：只负责训练并落盘 `arx_model.json`。
- `flow_control/rom/validation.py`：只负责加载已有模型并递推验证。
- `flow_control/cli/train_rom.py`：训练命令行入口。
- `flow_control/cli/validate_rom.py`：验证命令行入口。
- `examples/train_rom_mock.py`：最小命令行示例，内部调用 `flow_control.rom.train_arx_rom_from_case()`。

## 2. 输入变量

ARX ROM 使用 48 个输入变量：

- `JET_01` 到 `JET_24`：喷口开关，通常为 0 或 1。
- `cmd_massflow_01` 到 `cmd_massflow_24`：喷口质量流量指令。

Mock `timeseries.csv` 中通常只有 `JET_*` 和载荷输出；`cmd_massflow_*` 在同目录 `actuation_schedule.csv` 中。训练脚本会自动按 `window_id` 或 `physical_time` 合并。

## 3. 输出变量

模型训练和指标计算使用 7 个输出：

- `Fz_S1L`
- `Fz_S1R`
- `Fz_S2L`
- `Fz_S2R`
- `Fz_S3L`
- `Fz_S3R`
- `Fz_Total`

展示图按验收要求重点画 6 个载荷单元：`Fz_S1L` 到 `Fz_S3R`。`Fz_Total` 也会进入 `metrics.json`。

## 4. ARX 公式

ARX 是 AutoRegressive with eXogenous input，意思是“用过去输出和外部输入预测当前输出”。

本实现采用多输出线性形式：

```text
y[t] = c
     + A1 y[t-1] + A2 y[t-2] + ... + A_na y[t-na]
     + B0 u[t]   + B1 u[t-1] + ... + B_nb u[t-nb+1]
```

其中：

- `u[t]` 是第 `t` 个时间窗口的 48 维喷气输入。
- `y[t]` 是第 `t` 个时间窗口的 7 维载荷输出。
- `na` 是输出滞后阶数，默认 3。
- `nb` 是输入滞后块数，默认 2，包含当前输入 `u[t]` 和上一时刻输入 `u[t-1]`。
- `c, A, B` 是最小二乘训练得到的系数。

训练时使用 ridge 正则化的最小二乘：

```text
theta = argmin ||X theta - Y||^2 + lambda ||theta||^2
```

默认 `lambda = 1.0`，只是为了避免小样本和相关输入导致矩阵病态。当前 Mock 演示只有 80 个时间点，输入又包含 `JET_*` 和与之强相关的 `cmd_massflow_*`，所以这里使用偏保守的正则。

## 5. 训练和验证边界

当前训练和验证已经拆开：

```text
训练入口 train_rom：
  只读取指定 case/dataset，拟合 ARX 系数，保存 arx_model.json。

验证入口 validate_rom：
  只读取已有 arx_model.json，在指定 case/dataset 上递推预测，保存指标和图。
```

dataset 训练时按 `index.csv` 中列出的 case 逐个读取。每个 case 内部独立构造滞后特征，历史不会跨 case 边界。当前 `runs/arx_test` 的 100 个 case 全部用于训练，不做验证切分。

验证采用递推预测：

1. 验证段开始前的真实输出只作为历史初值。
2. 进入验证段后，预测 `y[t]` 时只使用当前/过去输入和过去预测输出。
3. 不使用未来输入以外的未来输出，也不使用验证段当前真实输出去预测当前真实输出。

因此验证比“一步预测”更接近后续控制器调用 ROM 的方式。

## 6. 如何运行

### 6.1 生成训练数据

从全局 seed `configs/system.yaml -> system.random_seed` 开始，生成 100 个 sparse24 schedule + mock case：

```bash
.venv/bin/python -m flow_control.rom.generate_arx_dataset \
  --out runs/arx_test \
  --count 100 \
  --overwrite
```

默认会生成：

```text
runs/arx_test/index.csv
runs/arx_test/index.json
runs/arx_test/sparse24_seed_20260618/
...
runs/arx_test/sparse24_seed_20260717/
```

### 6.2 训练

```bash
.venv/bin/python -m flow_control.cli.train_rom \
  --dataset-dir runs/arx_test \
  --out runs/arx_test/arx_train_all
```

训练输出：

```text
runs/arx_test/arx_train_all/arx_model.json
runs/arx_test/arx_train_all/metrics.json
```

本次 mock 训练使用 100 个 case、7700 行训练样本。

### 6.3 生成验证数据

从 `20260718` 开始再生成 10 个新 case：

```bash
.venv/bin/python -m flow_control.rom.generate_arx_dataset \
  --out runs/arx_validate \
  --count 10 \
  --start-seed 20260718 \
  --overwrite
```

默认会生成：

```text
runs/arx_validate/sparse24_seed_20260718/
...
runs/arx_validate/sparse24_seed_20260727/
```

### 6.4 验证已有模型

```bash
.venv/bin/python -m flow_control.cli.validate_rom \
  --model runs/arx_test/arx_train_all/arx_model.json \
  --dataset-dir runs/arx_validate \
  --out runs/arx_result
```

验证输出：

```text
runs/arx_result/metrics.json
runs/arx_result/prediction_timeseries.csv
runs/arx_result/prediction_6_load_cells.svg
runs/arx_result/error_6_load_cells.svg
runs/arx_result/rmse_bar.svg
```

### 6.5 单 case 兼容示例

单个 case 仍可训练：

```bash
.venv/bin/python -m flow_control.cli.train_rom \
  --case-dir runs/mock_full_prbs_demo \
  --out runs/rom_mock_demo
```

旧的 `models.ARXModel` 和顶层 `rom_identifier` 路径保留了薄兼容层；新代码建议统一从 `flow_control.rom` 导入。

## 7. 指标含义

`metrics.json` 对每个输出保存：

- `rmse`：均方根误差，越小越好。
- `nrmse_range`：RMSE 除以验证段真实值范围，便于不同输出之间比较。
- `correlation`：真实值和预测值相关系数，越接近 1 越好。
- `mean_error`：平均误差。
- `max_abs_error`：最大绝对误差。

## 8. 误差来源怎么解释

这次 Mock 演示里的误差主要可能来自：

- 延迟：Mock plant 内部有传播延迟；如果 ARX 滞后阶数不足，预测峰值会提前或滞后。
- 噪声：Mock 输出有随机噪声，ARX 只能拟合平均动态，不能完全重构噪声。
- 模型阶数：阶数太低会欠拟合，一阶惯性和延迟响应学不全；阶数太高会在小样本下不稳定。
- 输入相关性：PRBS 示例中多个喷口可能同时打开，单个喷口贡献不容易完全分离。
- 数据量不足：默认演示只有 80 个时间点，训练段约 56 行，只适合流程验收，不适合做最终模型。

## 9. 当前 mock 验证结果

当前模型：

```text
runs/arx_test/arx_train_all/arx_model.json
```

验证数据：

```text
runs/arx_validate
10 个新 case，770 行验证样本
```

验证结果：

```text
Fz_S1L   RMSE 0.035320   corr 0.999802
Fz_S1R   RMSE 0.035998   corr 0.999788
Fz_S2L   RMSE 0.035005   corr 0.999772
Fz_S2R   RMSE 0.034485   corr 0.999768
Fz_S3L   RMSE 0.035397   corr 0.999705
Fz_S3R   RMSE 0.036063   corr 0.999689
Fz_Total RMSE 0.086205   corr 0.999905
```

结论：ARX 在当前 mock 数据上拟合和泛化效果很好，误差接近 mock 噪声量级。但这只说明 mock 数据链路和 ARX 形式有效，不能直接替代真实 STAR-CCM+ 数据上的训练和验证。

## 10. 与 Mock、STAR 和 RL 的关系

```text
Mock Plant / STAR-CCM+ 数据
          ↓
      训练 ARX ROM
          ↓
  给 RL / MPC / 控制器提供快速响应预测
```

Mock 是手写的假环境；ARX ROM 是从数据训练出来的轻量响应模型；RL 是学习控制策略。ARX ROM 不代替 RL，它给 RL 或后续 MPC 提供更快的系统响应近似。
