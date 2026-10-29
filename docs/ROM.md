# 数据集与降阶模型（B52 → B53 → B54/B55/B56 → B06）

编号对照：用户任务单里的“任务 3 数据整理筛选”= 代码 **B53**；“B4 第一版真实 ROM”= 代码 **B54**；
产物文件名沿用 `B03_` / `B04_` 前缀。所有产物在 `artifacts/`（不入库，本地保留）。

## 1. 物理背景（解读结果时需要）

- 来流 166.67 m/s（`cifu0.sim`），方向 S1 → S2 → S3，S1 在最前。
- 底部流场强非定常：无喷气时任意 0.1 s 内六区力标准差约 0.9–1.4 kN，与喷气响应（1–7 kN）同量级，
  所以**基准必须用时间对齐的无喷气参考**，常数基准或局部喷气前基准都不可用。
- 单喷口响应：0–5 ms 本区；5–20 ms 同段镜像侧；20–100 ms **上游**区段升高（S2 喷口 → S1 约 +2 kN，
  S3 喷口 → S2 约 +2.5 kN），下游略降。要提升某段升力，用它后面一段的喷口；S3 没有下游喷口，持续控制能力弱。
- 喷气算例与参考算例从同一初始条件出发，喷气后流场逐渐分岔，留下确定性 ROM 无法解释的残差底噪：
  S1 ≈ 125 N、S2 ≈ 340 N、S3 ≈ 450 N。

## 2. B52：动作表

| 配置 | 用途 |
|---|---|
| `configs/b52/training_dt1e-4.yaml` | 训练集：6 个代表喷口（J02/J03/J09/J11/J17/J19）随机单喷，1.43/2.86 kg/s，脉宽 0.1 s，恢复 0.3 s，10.1 s |
| `configs/b52/validation_dt1e-4.yaml` | 验证集：同一设计、不同随机顺序与次数 |
| `configs/b52/calibration_*.yaml` | 早期标定 |
| `configs/b52/training.yaml`、`validation.yaml` | 旧版（非 dt1e-4），不再使用 |
| `configs/actions/temp_no_jet_11s_dt1e-4.yaml` | 无喷气参考（`runs/cifu0_nojet_11s`） |

训练/验证都是 GPU 求解（海光 8 DCU），结果在 `runs/b52_v2/training`、`runs/b52_v2/validation`。

## 3. B53：训练/验证数据集

- 代码 `flow_control/b53_dataset/`，入口 `scripts/build_b53_dataset.py`，配置 `configs/b53/data_filter.yaml`。
- 每个 Case 在 `sources` 中显式登记 `case_dir` + `split`（`training`/`validation`）；按整个 Case 分组，从不按时间行随机拆分。
- `reference_case: runs/cifu0_nojet_11s`；每个样本 `delta_Fz_* = Fz(t) − Fz_ref(t)`。参考必须无喷气、同模板、
  同时间步、覆盖全部样本时刻（否则 `REFERENCE_TIME_MISMATCH`），且不能同时登记为数据。
- 启动剔除 0.5 s；每事件取前 0.1 s 基准 + 0.2 s 恢复（`recovery_search_s`）。相邻放行事件拼成连续 `segment_id`，
  被剔除事件切断片段；ROM 只在片段内构造滞后。
- 输入 Case 至少要有 `actuation_schedule.csv`、`processed/timeseries.csv`（`physical_time`、`actual_massflow_01..24`、
  `Fz_S1L..Fz_S3R`）。命令流量不能代替实际流量，缺失值不填零。
- 现状：训练 Case 24 个事件放行 23 个（首个 0.5 s 事件因基准落在剔除段被拒）；训练集为 0.5001–10.0 s 共 95000 行。

```bash
python scripts/build_b53_dataset.py --config configs/b53/data_filter.yaml --output-dir artifacts/B53_training_data
```

产出 `training_dataset.csv`、`validation_dataset.csv`、`B03_data_quality_summary.csv`、`B03_anomalous_windows.csv`、
`B03_six_jet_response_summary.csv`。

## 4. B54：ARX + 岭回归（当前主模型）

- 代码 `flow_control/rom/b54_from_b53.py`，入口 `scripts/analysis/run_b54_rom_from_b53.py`。
  旧入口 `run_b54_real_rom.py` / `b54_real_rom.py`（常数尾段基准）已被取代，保留但不要用。
- 输入 6 路实际质量流量（J02/J03/J09/J11/J17/J19），输出六区 ΔFz；stride 10 → 有效 dt 1 ms；
  输入历史 200 步（0.2 s）、输出滞后 10、α=10、不含当前步输入。超参数只在训练集内部分块交叉验证选取。
- `configs/b54/real_rom_b53.yaml` 主模型；`real_rom_b53_aux.yaml` 辅助模型（输出整车量 `Fz_Total`、`Drag_Total`、力矩等）。

```bash
python scripts/analysis/run_b54_rom_from_b53.py --config configs/b54/real_rom_b53.yaml   # --require-validation 时验证集为空返回非零
```

产出 `artifacts/B04_real_ROM_{metrics.json,model.json,result.md,*_predictions.csv,*.png}`。
结果（B52 验证，连续滚动预测 RMSE，单位 N）：S1L 130、S1R 130、S2L 367、S2R 370、S3L 494、S3R 551；
一步预测 18–31 N；主响应区、方向全部一致，延迟误差 < 1 ms。状态 `COMPLETE`，工程验收 `REVIEW_REQUIRED`（待人工评审）。
滚动误差已接近残差底噪，说明继续提升主要靠更多、更多样的数据，而不是更复杂的模型。

## 5. B55：ARX + 非线性残差网络

- 代码 `flow_control/rom/residual_rom.py`、`train_residual_rom.py`；配置 `configs/b55/residual_rom.yaml`。
- 固定 B54 的 ARX，并联一个 57 → 32（tanh）→ 6 的输入残差分支（NumPy 实现，无需 PyTorch）；最后一层零初始化，训练前严格等于 ARX。

```bash
python scripts/workflow.py train-residual-rom --config configs/b55/residual_rom.yaml --output-dir artifacts/B55_<日期>_trial01
```

结果（`artifacts/B55_residual_20261018_trial01`）：验证滚动 RMSE 与 B54 几乎相同（差 < 0.5 N），没有实质改善。

## 6. B56：概率集成网络（参照 Phys. Fluids 论文 DOI 10.1063/5.0287427）

- 代码 `flow_control/rom/probabilistic_*.py`、`train_probabilistic_rom.py`、`evaluate_probabilistic_rom.py`；
  配置 `configs/b56/probabilistic_rom.yaml`（默认 v1：5 成员 1260→300×3→12，绝对状态）和
  `configs/b56/paper_improvement_study.yaml`（预登记的 6 候选开发研究，含可选 `prediction_mode: state_increment`）。
- 需要 PyTorch：`pip install -r requirements-rom-neural.txt`（基础流程不需要）。CPU 训练建议 `OMP_NUM_THREADS=1`。

```bash
OMP_NUM_THREADS=1 MKL_NUM_THREADS=1 OPENBLAS_NUM_THREADS=1 python -u scripts/workflow.py train-probabilistic-rom \
  --config configs/b56/probabilistic_rom.yaml --output-dir artifacts/B56_<日期>_trial01 --require-validation
python scripts/workflow.py train-probabilistic-rom --config configs/b56/paper_improvement_study.yaml \
  --output-dir artifacts/B56_paper_study_<日期> --development-study          # 只在训练集内部选候选，再全量重训一次
python scripts/workflow.py evaluate-probabilistic-rom --model-dir <模型目录> --config <配置> --output-dir <新目录>
```

结果：
- v1 默认（`artifacts/B56_real_20261018_trial01`，24 min）：训练集比 B54 好 19.5–27.1%，**验证集六区全部变差 14.3–24.6%**，
  95% 区间实际覆盖只有 52–71%（欠覆盖）→ 泛化不足。
- 论文改进研究（`artifacts/B56_paper_study_20261018_trial01`）：5 个候选 × 3 种子完成，`increment/H20/300` 内部开发分数最好（0.2922）；
  600 宽度候选跑到一半被用户暂停（SIGINT，退出 130）。**尚未生成 `selection.json`、未做最终重训和外部评估**。
  CLI 不支持在原目录续跑，继续时用新目录，并参考该目录的 `pause_status.json`、`AGENT_HANDOFF.md`。
- 结论：当前只有 1 个训练 Case（24 次单喷），覆盖不足是主要瓶颈；不要只靠扩大网络。

## 7. B06：独立预测验证

- 配置 `configs/b06_validation/`：`control_w100_q1p43`、`duration_w050/w200/w400`、`amplitude_q0p715/q2p145/q2p86`
  （六口各单喷一次，5.8 s），以及 `pilot_*` 探针（`pilot_combo_s123_q1p43_w100` 用 `balanced_singlejet_pulses` 的 `jet_groups` 做多口同时喷）。
- 已跑完并拷回的：`pilot_6region_q1p43_w200`（0.2 s 长脉冲）、`pilot_s123_2level_w100`（0.715/2.145 kg/s），
  导入在 `runs/b06_import_20261018/`，B53 数据集在 `artifacts/B53_b06_validation_20261018/`。其余 B06 配置是否已在集群运行：未确认。
- 冻结 B54 测试结果（`artifacts/B06_linear_test_20261018/README.md`）：平均滚动 RMSE 约 291 N，相对“恒预测 0”降低约 50%；
  长脉冲 6/6 主区与方向一致但峰值低估 3–23%；2.145 kg/s 插值 3/3 一致；**0.715 kg/s 低档外推 3 个事件中方向只对 1 个**。

## 8. 后续方向（B05 建议，`artifacts/B05_next_step_recommendation.md`）

在现有 6 个代表喷口上增加独立训练 Case，丰富激励（多流量档、多脉宽、多口同时喷、不同初始相位），
并留出冻结测试 Case；暂不扩展到 12/24 个喷口。所有结论目前都是“已知数据离线评估”，
`rl_ready=false`、`cfd_replacement_accepted=false`，不能报告为 CFD 已被替代。
