# 喷气动作生成器命令说明

## 统一规则

生成器入口：

```bash
.venv/bin/python -m flow_control.generator.schedule_generator
```

每次都必须指定两项：

- `--config`：动作模式对应的 YAML 配置。
- `--output-dir`：本次运行的输出根目录。

生成器不会使用 `case_id`，也不会读取配置中的 `output.run_dir` 来决定写入位置。所有生成文件固定写入：

```text
<output-dir>/input/
```

其中主动作表为：

```text
<output-dir>/input/actuation_schedule.csv
```

同一 `input/` 目录还包含：

```text
config_summary.yaml
validation_report.json
actuation_heatmap.svg
total_mass_flow.csv
total_mass_flow_curve.svg
```

## 六种动作模式

如果要生成计划表并继续运行 mock，使用 `examples` 中的启动脚本：

```bash
bash examples/run_one_action.sh
```

脚本会提示选择动作，输入数字 `1` 到 `6` 即可。

### 1. 无喷气参考段

所有 24 路喷气关闭，用于建立基准。

```bash
.venv/bin/python -m flow_control.generator.schedule_generator \
  --config configs/actions/no_jet_reference.yaml \
  --output-dir runs/no_jet_reference
```

输出主表：`runs/no_jet_reference/input/actuation_schedule.csv`

### 2. 单喷气脉冲

在指定窗口短暂开启一个喷气区，用于观察瞬态响应和时延。

```bash
.venv/bin/python -m flow_control.generator.schedule_generator \
  --config configs/actions/pulse_singlejet.yaml \
  --output-dir runs/pulse_singlejet
```

### 3. 单喷气阶跃

从指定窗口起持续开启一个喷气区，用于观察稳态或平均响应。

```bash
.venv/bin/python -m flow_control.generator.schedule_generator \
  --config configs/actions/step_singlejet.yaml \
  --output-dir runs/step_singlejet
```

### 4. 关键喷气区扫频

让配置中指定的关键喷气区按递增频率变化，用于频率响应测试。

```bash
.venv/bin/python -m flow_control.generator.schedule_generator \
  --config configs/actions/chirp_keyjets.yaml \
  --output-dir runs/chirp_keyjets
```

### 5. PRBS 伪随机开关

生成可复现的伪随机喷气输入，适合动态辨识和 ROM 测试。

```bash
.venv/bin/python -m flow_control.generator.schedule_generator \
  --config configs/actions/prbs_demo.yaml \
  --output-dir runs/prbs_demo
```

### 6. 稀疏随机分组

生成 24 路喷气的受约束稀疏分组输入，用于公平筛查单喷口与组合影响。

```bash
.venv/bin/python -m flow_control.generator.schedule_generator \
  --config configs/actions/pilot_sparse24.yaml \
  --output-dir runs/pilot_sparse24
```

## 一次生成全部六种模式

在项目根目录运行：

```bash
bash examples/run_all_actions.sh
```

每一种动作的主表都会位于：

```text
runs/<模式名>/input/actuation_schedule.csv
```

## 查看命令参数

```bash
.venv/bin/python -m flow_control.generator.schedule_generator --help
```
