# B3 三个标准算例流程

## 1. 固定算例与顺序

只允许以下三个 case_id：

1. `G00_nojet_baseline`
2. `G01_J02_pulse`
3. `G02_J06_pulse`

G00 质量报告未通过时，G01 不得放行；G01 未通过时，G02 不得放行。最终使用 `examples/run_week4_b3_acceptance.py` 执行机器可读的顺序门禁。

## 2. 动作表

仓库中的三份配置统一使用 `1.0e-4 s` 求解时间步，总时长 `1.0 s`。J02/J06 都是：

- `0.0–0.4 s`：喷气前基准段；
- `0.4–0.5 s`：单喷口 `1 kg/s` pulse；
- `0.5–1.0 s`：喷气后恢复段。

如浩坤确认的质量流量或三段时长变更，必须同时修改 G01/G02，然后重新生成动作表。B3 验收会拒绝没有前基准、没有恢复、多段 pulse，以及 J02/J06 波形不一致的数据。

## 3. STAR 与追溯

`.sim` 不提交仓库，但三个 `case_manifest.yaml` 必须记录同一个 checkpoint/template 的外部标识和 SHA-256，并记录网格版本、时间步、每步内迭代数、report 采样间隔和求解设置。

STAR 原始 report 不做修改。质量流量入口如果在 STAR 中为负，导入后按项目约定存为正的喷气流量。`Fz Monitor` 和 `fz Monitor` 大小写有物理含义，必须分开保存，不得相互覆盖。

## 4. 目录与验收

每个算例使用：

```text
runs/real_star/<case_id>/
  raw_star/
  processed/timeseries.csv
  actuation_schedule.csv
  case_manifest.yaml
  quality_report.json
  figures/
  logs/
```

完成 Step 1/2/3 后先执行 B04 真实数据质量检查（命令见 `B04_real_quality_workflow.md`），三个算例全部 PASS 后再执行：

```bash
python examples/run_week4_b3_acceptance.py
```

脚本会同时检查目录、manifest、非空求解设置、B04 PASS 状态、三阶段 pulse、24 路实际质量流量、六区力、整车力/力矩、J02/J06 波形一致性和三算例顺序，并写入 `runs/real_star/B3_acceptance_report.json`。不能跳过 B04 后直接把 B3 判为通过。
