# B06 ROM 变量和公式说明

## 1. 这一步在做什么

B06 做的是一个最小输入-输出 ARX ROM。它不是强化学习控制器，也不是最终物理模型，而是一个轻量的被控对象响应模型：

```text
过去喷气输入 + 过去载荷输出 -> 当前/下一步载荷输出
```

本周目标是验证 ROM 代码流程：读数据、按时间切分、训练、递推验证、保存指标和画图。Mock 数据用于训练和验证；真实 STAR 数据本周只用于读取、画图、质量检查和单喷气响应摘要。

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

## 5. 时间序列切分规则

训练集和验证集按整段时间序列切分：

```text
前 70% 时间点 -> 训练
后 30% 时间点 -> 验证
```

不允许随机打散时间点。

验证采用递推预测：

1. 验证段开始前的真实输出只作为历史初值。
2. 进入验证段后，预测 `y[t]` 时只使用当前/过去输入和过去预测输出。
3. 不使用未来输入以外的未来输出，也不使用验证段当前真实输出去预测当前真实输出。

因此这个验证比“一步预测”更接近后续控制器调用 ROM 的方式。

## 6. 如何运行

在仓库根目录执行：

```bash
python3 examples/train_rom_mock.py
```

默认读取：

```text
runs/mock_full_prbs_demo/timeseries.csv
runs/mock_full_prbs_demo/actuation_schedule.csv
```

默认输出：

```text
runs/rom_mock_demo/metrics.json
runs/rom_mock_demo/arx_model.json
runs/rom_mock_demo/prediction_timeseries.csv
runs/rom_mock_demo/prediction_6_load_cells.svg
runs/rom_mock_demo/error_6_load_cells.svg
runs/rom_mock_demo/rmse_bar.svg
B06_single_jet_response_summary.csv
```

可调参数示例：

```bash
python3 examples/train_rom_mock.py \
  --case-dir runs/mock_full_prbs_demo \
  --out runs/rom_mock_demo \
  --train-fraction 0.70 \
  --input-lags 2 \
  --output-lags 3
```

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

## 9. 与 Mock、STAR 和 RL 的关系

```text
Mock Plant / STAR-CCM+ 数据
          ↓
      训练 ARX ROM
          ↓
  给 RL / MPC / 控制器提供快速响应预测
```

Mock 是手写的假环境；ARX ROM 是从数据训练出来的轻量响应模型；RL 是学习控制策略。ARX ROM 不代替 RL，它给 RL 或后续 MPC 提供更快的系统响应近似。
