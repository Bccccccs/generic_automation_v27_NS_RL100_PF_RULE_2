# 真实 STAR-CCM+ 数据流程

真实数据和 Mock 共用统一启动脚本：

```bash
python scripts/workflow.py --help
```

真实算例的建议流程是：

```bash
# 1. 生成动作表
python scripts/workflow.py actions \
  --config configs/week4/G01_J02_pulse.yaml \
  --output-dir runs/week4/J02_work

# 2. 生成宏并启动 STAR-CCM+
python scripts/workflow.py ccm \
  --schedule runs/week4/J02_work/input/actuation_schedule.csv \
  --sim /path/to/frozen.sim \
  --out runs/week4/J02_work/raw_star \
  --execution-mode run

# 3. 把动作表和 CCM 输出整理为最终算例
python scripts/workflow.py organize \
  --input-dir runs/week4/J02_work \
  --output-dir runs/week4/J02_final

# 4. 质量检查并生成图表
python scripts/workflow.py check \
  --case-dir runs/week4/J02_final \
  --mode ccm
```

`organize` 的输入目录必须包含 `input/actuation_schedule.csv`
或根目录 `actuation_schedule.csv`，并包含 CCM 监视器 CSV。最终算例必须包含：

```text
<output-dir>/processed/timeseries.csv
```

详细目录结构和其他命令见项目根目录 `README.md`。
