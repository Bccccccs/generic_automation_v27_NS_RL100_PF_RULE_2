# Mock 仿真命令

使用已生成的动作表：

```bash
python scripts/workflow.py mock \
  --schedule runs/week4/J02_work/input/actuation_schedule.csv \
  --config configs/mock_dynamic24x6.yaml \
  --out runs/week4/J02_mock
```

也可直接从动作 YAML 生成输入并运行 Mock：

```bash
python scripts/workflow.py mock \
  --actuation-config configs/week4/G01_J02_pulse.yaml \
  --config configs/mock_dynamic24x6.yaml \
  --out runs/week4/J02_mock
```

查看参数：

```bash
python scripts/workflow.py mock --help
```
