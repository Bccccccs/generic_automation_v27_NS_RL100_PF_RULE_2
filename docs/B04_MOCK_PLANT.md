# B04 Mock Plant

B04 实现了一个用于 RL 控制验证的本地虚拟 CFD 动力系统。它的作用是临时代替真实 STAR-CCM+ 求解器，让 24 路喷气输入到 6 路观测输出的控制链路可以在本地快速跑通。

这个模型只用于算法和流程联调，不代表真实 CFD 物理结果。

## 任务定位

真实流程中，RL 控制器需要把 24 路喷气控制量发送给 STAR-CCM+，再从求解器中读取 6 路响应量。真实求解器运行成本高、调试慢，因此 B04 先提供一个 mock plant：

```text
24 路喷气输入 u(t) -> MockPlant -> 6 路输出 y(t)
```

它用于验证：

- 24 输入到 6 输出的数据链路是否正确。
- RL 是否能处理非线性、时延、惯性和噪声。
- 稀疏喷气调度输入是否能产生稳定可分析的响应。
- 算法能否从输入输出关系中识别隐藏关键喷气口。

## 主要实现

核心文件：

```text
flow_control/mock_plant.py
flow_control/run_mock_demo.py
```

`MockPlant` 提供两个主要接口：

```python
plant = MockPlant()
plant.reset(seed=123)
y = plant.step(u)
```

其中：

- `u` 是 24 维输入向量。
- `y` 是 6 维输出向量。
- `reset(seed)` 用于重置系统状态和随机矩阵。
- `step(u, dt=1.0)` 推进一步动力系统并返回当前输出。

## 动力学内容

B04 的 mock plant 包含以下特性：

- 状态空间模型：`x(t+1) = A x(t) + B u(t) + d(t)`。
- 稀疏输入矩阵 `B`：少量强连接、大量弱连接。
- 输入时延：使用最近 `K` 步历史输入，权重按 `exp(-lambda k)` 衰减。
- 非线性项：包含 `tanh(Wu)` 和 `u^2`。
- 输出惯性：`y(t) = (1-beta)y(t-1) + beta Cx(t)`。
- 高斯噪声：输出端叠加协方差噪声。
- 稳定性保护：状态矩阵谱半径缩放、状态和输出裁剪，避免发散。

内部存在隐藏关键喷气口，这些喷气口对输出影响更强，但不会通过 `MockPlant` 的公开接口显式返回。demo 通过 influence ranking 验证这些关键变量是否能被数据学习出来。

## 运行方式

在项目根目录运行：

```bash
python -m flow_control.run_mock_demo --config configs/pilot_sparse24.yaml
```

如果本地虚拟环境缺少 `numpy`，可以使用当前机器验证过的命令：

```bash
env PYTHONPATH=/Library/Frameworks/Python.framework/Versions/3.14/lib/python3.14/site-packages .venv/bin/python -m flow_control.run_mock_demo --config configs/pilot_sparse24.yaml
```

成功运行后会看到类似输出：

```text
MockPlant demo complete: runs/b04_mock_plant
Stable: True max_abs_y=2.6438
Top influence jets: J03, J14, J07, J18, J22
```

## 输出结果

B04 输出目录：

```text
runs/b04_mock_plant/
```

主要结果文件：

```text
mock_input_heatmap.svg
mock_output_timeseries.svg
mock_input_output_correlations.csv
mock_hidden_jet_influence_ranking.csv
mock_demo_summary.json
mock_inputs.csv
mock_outputs.csv
```

当前版本也会生成标准 case schema 文件：

```text
case_manifest.yaml
actuation_schedule.csv
timeseries.csv
quality_report.json
logs/case_io.log
```

## 图和文件解释

`mock_input_heatmap.svg` 显示 24 路喷气输入随时间窗口变化的稀疏激励热图。它复用 B03 的 `pilot_sparse24` 调度配置。

`mock_output_timeseries.svg` 显示 6 路 mock plant 输出时间序列。合理结果应该有动态波动、惯性响应和噪声，但不能发散。

`mock_input_output_correlations.csv` 给出每个喷气口与每个输出之间的最佳滞后相关性，用于检查输入输出是否存在可学习关系。

`mock_hidden_jet_influence_ranking.csv` 给出 24 个喷气口的影响力排序，用于验证隐藏关键变量是否能从系统响应中被识别。

`mock_demo_summary.json` 汇总运行配置、稳定性检查、输出文件位置和 hidden jet learning check。

## 与 B03 的关系

B03 负责生成稀疏喷气调度：

```text
runs/pilot_sparse24/
```

B04 负责把 B03 风格的 24 路输入送入虚拟 CFD 动力系统，并生成 6 路输出响应：

```text
runs/b04_mock_plant/
```

两者不要混在同一个输出目录中。B03 的热图、总载荷曲线、空间不均匀度曲线描述的是调度本身；B04 的输入热图、6 输出曲线、相关性和影响力排序描述的是 mock plant 响应。

## 验收要点

可以从以下几个方面判断 B04 是否完成：

- `MockPlant` 能接受 24 维输入并返回 6 维输出。
- 输出包含非线性、时延、惯性、稀疏结构和噪声。
- 系统稳定，不出现 NaN、Inf 或持续发散。
- demo 能生成输入热图、6 输出曲线、相关性分析和影响力排序。
- `mock_demo_summary.json` 中 `stability.stable` 为 `true`。
- `hidden_jet_learning_check.passes` 为 `true`。

## 汇报表述

可以这样汇报：

> B04 完成了一个本地虚拟 CFD 动力系统，用于在不启动 STAR-CCM+ 的情况下验证 RL 控制链路。该系统接受 24 路喷气输入，输出 6 路响应，包含稀疏耦合、时延、非线性、惯性和噪声。运行结果显示系统稳定不发散，并且通过输入输出响应可以识别隐藏关键喷气口，说明该 mock plant 可用于后续 RL 控制算法的本地联调和学习能力验证。
