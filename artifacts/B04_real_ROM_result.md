# B04 第一版真实 ROM 结果

## 状态

**BLOCKED：未训练模型，也未生成或展示伪造的真实验证指标。**

## 阻塞项

- baseline case /Users/yanbochao/Documents/研究生/项目/generic_automation_v27_NS_RL100_PF_RULE_2/runs/b52/b52_no: B04 real-data quality gate is not PASS
- training case /Users/yanbochao/Documents/研究生/项目/generic_automation_v27_NS_RL100_PF_RULE_2/runs/b52/training: B04 real-data quality gate is not PASS
- validation case /Users/yanbochao/Documents/研究生/项目/generic_automation_v27_NS_RL100_PF_RULE_2/runs/b52/validation: B04 real-data quality gate is not PASS

动作表不是实际质量流量或气动力结果。必须先完成独立训练/验证 STAR 算例，生成各自的 `processed/timeseries.csv`，由上游 runner 在 `case_manifest.yaml` 中签发 `source_run_evidence` 和 `rom_compatibility`，再重跑 B04 以生成绑定当前 CSV/manifest 哈希的 PASS `quality_report.json`，最后重跑 ROM：

```bash
'/Users/yanbochao/Documents/研究生/项目/generic_automation_v27_NS_RL100_PF_RULE_2/.venv/bin/python' '/Users/yanbochao/Documents/研究生/项目/generic_automation_v27_NS_RL100_PF_RULE_2/scripts/analysis/run_b54_real_rom.py' --config '/Users/yanbochao/Documents/研究生/项目/generic_automation_v27_NS_RL100_PF_RULE_2/configs/b54/real_rom.yaml' --output-dir '/Users/yanbochao/Documents/研究生/项目/generic_automation_v27_NS_RL100_PF_RULE_2/artifacts'
```
