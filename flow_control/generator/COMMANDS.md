# 动作表生成命令

项目只使用统一启动脚本：

```bash
python scripts/workflow.py actions \
  --config configs/week4/G01_J02_pulse.yaml \
  --output-dir runs/week4/J02_work
```

主输出为：

```text
runs/week4/J02_work/input/actuation_schedule.csv
```

动作表每行对应一个求解器物理时间步；连续且 `window_id` 相同的行
共同组成一个喷气窗口，窗口内动作指令必须保持不变。

查看参数：

```bash
python scripts/workflow.py actions --help
```
