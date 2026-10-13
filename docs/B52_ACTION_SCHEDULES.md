# B52 标定、训练和验证动作表

所有 B52 动作配置均使用统一入口 `scripts/workflow.py actions` 生成，
输出遵循标准 Case 目录结构 `<case-dir>/input/actuation_schedule.csv`。

## 第一阶段：只生成标定表

现有动作生成器可直接读取以下两份正式配置：

- `configs/b52/calibration_1p43.yaml`
- `configs/b52/calibration_2p86.yaml`

分别生成 STAR 标准计划表：

```bash
python scripts/workflow.py actions \
  --config configs/b52/calibration_1p43.yaml \
  --output-dir runs/b52_calibration_1p43

python scripts/workflow.py actions \
  --config configs/b52/calibration_2p86.yaml \
  --output-dir runs/b52_calibration_2p86
```

主输出分别为：

```text
runs/b52_calibration_1p43/input/actuation_schedule.csv
runs/b52_calibration_2p86/input/actuation_schedule.csv
```

训练和独立验证动作也通过相同入口分别生成：

```bash
python scripts/workflow.py actions \
  --config configs/b52/training.yaml \
  --output-dir runs/b52/training

python scripts/workflow.py actions \
  --config configs/b52/validation.yaml \
  --output-dir runs/b52/validation
```

训练和验证的标准主输出为：

- `runs/b52/training/input/actuation_schedule.csv`
- `runs/b52/validation/input/actuation_schedule.csv`

两张标定表的总时长均为 3.0 s：前 0.5 s 全关，0.5–0.6 s 只喷本表对应
质量流量，0.6–3.0 s 全关并观察恢复。两档独立运行，避免前一档的残余响应
污染后一档。训练和验证表的动作顺序由各自随机种子固定，可重复生成。

统一生成器会将每个动作窗口展开为逐求解器时间步的 STAR 动作输入。例如运行低档标定：

```bash
python scripts/workflow.py actions \
  --config configs/b52/calibration_1p43.yaml \
  --output-dir runs/b52_calibration_1p43

python scripts/workflow.py ccm \
  --schedule runs/b52_calibration_1p43/input/actuation_schedule.csv \
  --sim /path/to/model.sim \
  --out runs/b52_calibration_1p43/raw_star \
  --time-step 0.0001
```

## 冻结参数

当前只标定 `JET_02`。两档流量、3.0 s 总时长、前置 0.5 s 和喷气 0.1 s
均集中在 YAML 配置中。

自动报告检查统一列格式、物理时间连续、开关与质量流量一致、单时刻仅一个喷口、
无喷气恢复间隔以及每张表对应的唯一质量流量。
