# B04 真实数据质量检查与自动出图

当前输入为 `runs/real_star` 下三个 B3 标准算例目录。检查结果只反映输入数据，画图成功不会覆盖失败状态，缺失字段不会填 0。

## 运行命令

```bash
PYTHONPATH=. .venv/bin/python scripts/analysis/run_b04_real_quality.py \
  runs/real_star/G00_nojet_baseline \
  runs/real_star/G01_J02_pulse \
  runs/real_star/G02_J06_pulse \
  --output-dir artifacts/reports \
  --expected-case-count 3
```

命令在任一算例存在阻塞错误时返回非零退出码，这是预期的质量门禁行为。每个算例的结果写入自身 `quality_report.json` 的 `B04_real_quality` 节点；汇总表、阻塞清单和图片写入 `artifacts/reports/`。

## 替换正确数据后的流程

1. 保留每个算例的 `case_manifest.yaml`、`actuation_schedule.csv`、`processed/timeseries.csv` 目录合同。
2. 使用 `docs/week4/B01_final_data_contract.md` 中的最终字段名；历史 `Fz_Total`、`Jet_Reaction_Z`、`JET_01`、`cmd_massflow_01` 不会被静默猜测或改名。
3. 再次执行上述命令。旧的 B04 报告和图片会被同名新结果覆盖，原始 `raw_star/` 数据不会修改。
4. B04 三个算例全部 PASS 后，执行 `python examples/run_week4_b3_acceptance.py` 检查 pulse 三阶段、同 checkpoint/网格/求解设置、J02/J06 波形一致性和 G00→G01→G02 顺序门禁。

质量报告固定分为 `format_errors`、`time_errors`、`massflow_errors`、`force_definition_errors` 和 `physical_questions_for_haokun`。前四类是自动阻塞项；最后一类仅供浩坤做物理判断。
