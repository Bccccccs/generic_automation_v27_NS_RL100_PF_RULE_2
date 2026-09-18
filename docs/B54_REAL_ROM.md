# B54 第一版真实 ROM

## 当前状态

本项目已准备 B54 的独立运行入口和参数配置，但当前仓库中还没有以下三类真实 STAR-CCM+ 标准 Case 输出：

- `runs/b52_nojet_baseline`：独立无喷气基准；
- `runs/b52_training`：只用于拟合的真实训练序列；
- `runs/b52_validation`：只用于评价的独立验证序列。

`artifacts/B52_action_schedules/` 中的训练和验证动作表只是 STAR 输入，没有六区气动力输出，不能单独用来训练 ROM。当前 B53 训练/验证 CSV 也只有表头，且其设计为事件局部片段，不能把若干片段直接拼成一条连续 ARX 时序。现有 `runs/week4` 数据只覆盖无喷气、J02 和 J06，且当前 B04 真实数据质量检查未通过，不得用它们生成或宣称已验收的真实 ROM 结果。

因此，在三个默认 Case 补齐且通过质量门禁前，入口只可用于预检：它会以退出码 2 结束，并写出状态为 `BLOCKED` 的 metrics/result；不会生成模型、预测 CSV 或预测图。不得手工伪造 `B04_real_ROM_*` 成功产物。

当前仓库的 CCM/整理 runner 还没有自动签发本文后述的 `source_run_evidence` 和 `rom_compatibility`。这是真实 Case 数据生产端的明确前置项：应在每次独立 STAR 运行完成时由上游 runner 一次生成 UUID、完整源产物哈希和物理签名，然后再运行 B04 将当前 manifest/CSV 哈希绑定到质量报告。B54 只负责验证这些证据，不在模型训练时临时伪造。

如果同一输出目录残留较早成功运行的模型、CSV 或 PNG，新的 `BLOCKED` 运行会把这些固定文件名移入 `artifacts/B04_real_ROM_stale/`，避免旧模型与本轮阻塞指标混用。

## 模型口径

第一版真实 ROM 只使用 ARX 与岭回归，不加入 POD、神经网络、MPC 或强化学习。

六个输入严格为代表喷口的 STAR 实际质量流量，按配置顺序为：

```text
actual_massflow_02
actual_massflow_03
actual_massflow_09
actual_massflow_11
actual_massflow_17
actual_massflow_19
```

程序不得用 `JET_*` 开关或 `cmd_massflow_*` 指令列替代实际质量流量，缺失、空值或非有限值必须阻断运行。

六个输出为六个承载区域相对独立无喷气基准的力变化：

```text
ΔFz_S1L, ΔFz_S1R, ΔFz_S2L, ΔFz_S2R, ΔFz_S3L, ΔFz_S3R
```

基准值由无喷气 Case 末尾 `baseline.tail_fraction` 比例的选定数据计算，同一组冻结基准同时应用于训练和验证；不得使用验证输出重新拟合基准或预处理参数。程序会在 JSON/MD 中报告该尾段每区域的标准差、峰峰值和首尾窗口漂移；B04 的相关物理问题与这些数值仍需在 `acceptance_status: REVIEW_REQUIRED` 下人工复核，未设定阈值时不声称尾段已自动判稳。

## 数据和门禁

每个 Case 必须至少包含：

```text
<case>/processed/timeseries.csv
<case>/case_manifest.yaml
<case>/quality_report.json
```

`case_manifest.yaml` 不能只用手工填写的 `run_id` 自证独立性。每个 Case 必须绑定由上游采集/runner 产生的完整源运行 UUID 和可校验源产物：

```yaml
run_id: training-run-001  # 仅作补充标识
source_run_evidence:
  run_uuid: 9e2908fe-6d19-4ffc-9ef5-245c5e2d44b9
  sequence_scope: full_run
  artifact_kind: solver_result_sim  # 或 raw_star_bundle
  source_artifact_path: raw_star/final_training_run.sim
  source_artifact_sha256: <完整 .sim 文件或 raw_star 目录树的真实 SHA-256>
```

`solver_result_sim` 必须指向存在的 `.sim` 文件；`raw_star_bundle` 必须指向包含原始 STAR 导出的非空目录。源路径不能指向 `processed/`。程序会重算哈希，并同时比较 UUID、解析路径和内容哈希；因此同一原始运行即使被复制到不同路径、切片后重置时间、再改写 `run_id` 也会因完整源产物哈希重复而阻断。基准、训练、验证三方不能共享任一源运行证据；同一分区内也不得重复配置同一 Case。

计算与 B54 相同口径的源产物哈希：

```bash
.venv/bin/python -c 'from flow_control.rom.b54_real_rom import sha256_source_artifact; print(sha256_source_artifact("runs/b52_training/raw_star/final_training_run.sim"))'
```

`processed/timeseries.csv` 必须包含严格递增且均匀采样的 `physical_time`、全部 24 个 `actual_massflow_01..24` 列和六个 `Fz_S*` 列。模型特征仍只取六个代表喷口；读取其余 18 列只是为了证明它们没有开启，防止六输入模型被未建模的喷口混杂。不接受 Case 根目录下的旧版 `timeseries.csv` 回退。

训练和验证必须是不同的完整 Case，且 `physical_time` 必须从独立运行原点开始；不允许将一条时序随机拆成训练集和验证集。程序记录 Case ID、解析后路径、时序文件 SHA-256、行数和时间步，并对基准/训练/验证三方检查路径、ID、内容哈希和源运行标识。

为冻结物理口径，每个 manifest 还必须显式包含如下块，且三类 Case 的整个块必须完全一致：

```yaml
rom_compatibility:
  geometry_id: underbody-geometry-v1
  mesh_id: production-mesh-v1
  template_sim_path: templates/frozen_production_template.sim
  template_sim_sha256: <64 位真实 template .sim SHA-256>
  flow_condition_id: velocity-gap-condition-v1
  force_definition_id: six-bearing-regions-pressure-shear-fz-v1
  force_unit: N
  massflow_unit: kg/s
  sign_convention_id: positive-z-up-v1
```

程序会对 `template_sim_path` 指向的真实 `.sim` 重算 SHA-256，不得把示例 ID、占位哈希或 generic runtime 名称当作真实物理版本。

对无喷气基准，B04 `no_jet_physics` 中的漂移或跳变发现即使属于“需工程判断”而不计入 B04 blocking count，B54 仍会 fail-closed。只有完成工程复核后，才可在基准 manifest 中设置 `b54_baseline_review_approved: true`，并重跑 B04 将审批后 manifest 哈希绑定到质量报告。该批准状态和 B04 发现数量会写入 ROM metrics/result。

`quality.require_b04_pass` 在正式 B04 产物中必须为 `true`；配成 `false` 会在预检阶段直接阻断，不存在使用固定成功文件名的“未验证模式”。任一基准、训练或验证 Case 的 `quality_report.json` 未包含已通过的 B04 真实数据门禁时，流程必须失败且不生成模型或结果。顶层 `run_success_flag` 与嵌套 `B04_real_quality.summary.run_success_flag` 必须同时是严格布尔 `true`，不得只检查任意一层；还必须核对 `B04_real_quality` 的 schema、Case ID、汇总状态、阻塞项计数、行数、实际开启喷口和声明的 Case 类型。B04 报告中的 `timeseries_sha256` 与 `case_manifest_sha256` 还必须分别与当前 CSV 和 manifest 完全一致；修改任何流量、力数据或源/物理声明后都必须重跑 B04，陈旧 PASS 报告会被拒绝。

在三个默认 Case 到位后，先生成与 B54 六喷口口径一致的质量报告：

```bash
.venv/bin/python -m flow_control.star_ingest.b04_real_quality \
  runs/b52_nojet_baseline runs/b52_training runs/b52_validation \
  --output-dir artifacts/reports \
  --expected-case-count 3 \
  --allowed-active-jets 2,3,9,11,17,19
```

## 参数

默认配置位于 `configs/b54/real_rom.yaml`。所有相对路径均以 `project_root` 为基准解析。

- `preprocessing.sample_stride: 5`：B52 training/validation 的实际 STAR 采样间隔为 `0.0002 s`，每 5 点取一个样本，有效时间步为 `0.001 s`。
- `baseline.tail_fraction: 0.2`：用独立无喷气序列最后 20% 的样本计算六区基准。
- `model.input_lags: 50`：在默认有效时间步下覆盖约 `50 ms` 输入历史。
- `model.output_lags: 20`：覆盖约 `20 ms` 输出历史。
- `model.include_current_input: false`：默认使用严格过去的实际流量，避免隐式零延迟通道。
- `model.ridge_alpha: 1.0`：岭回归正则强度。
- `diagnostics`：定义喷气开启和输出响应的阈值，用于报告主要响应区域、变化方向和时间延迟。

无喷气基准算例 `runs/b52/b52_no` 的采样间隔是 `0.0001 s`，与两个喷气算例的
`0.0002 s` 不一致。程序**不会**发现这种不一致：它只校验每条序列自身严格递增且
均匀采样，并互比训练与验证的有效时间步，从不与任何期望常量比较，`sample_stride`
也是无条件套用。基准只用于计算尾段均值、不参与滞后构造，因此该差异不影响模型口径，
但 `sample_stride` 的取值必须按喷气算例的真实 `dt` 手工定对，否则 `input_lags`
覆盖的时长会与文档所述不符。

这些参数只能预先冻结，或仅根据训练数据确定。不得根据独立验证指标反复调参。

逐喷口主区域/方向/延迟诊断只使用可归因的隔离事件：事件前置窗口内全部喷口必须关闭，响应窗口内只允许目标喷口动作一次。多喷口同步/重叠事件不会被重复归因到各喷口；若任一代表喷口在训练或验证中没有完整隔离事件，流程直接阻断。同时，每个喷口至少需有一个按冻结噪声/峰值阈值可检出真实响应的事件；未检出真实响应的单个事件会标为不可评价，不会默认归到 S1L 或计入匹配率。

## 运行

先确认三个默认 Case 已补齐、字段完整且 B04 门禁通过，再执行：

```bash
.venv/bin/python -m scripts.analysis.run_b54_real_rom \
  --config configs/b54/real_rom.yaml
```

也可以直接运行脚本文件：

```bash
.venv/bin/python scripts/analysis/run_b54_real_rom.py \
  --config configs/b54/real_rom.yaml
```

该入口只转发给 `flow_control.rom.b54_real_rom.main(argv)`，不会自动启动 STAR-CCM+。
脚本入口的默认配置路径锚定到仓库中的 `configs/b54/real_rom.yaml`，因此使用绝对脚本路径时不依赖当前工作目录；显式传入的相对 `--config` 仍按调用目录解析。

## 产物

默认 `output_dir: artifacts`，成功运行后应生成：

```text
artifacts/B04_real_ROM_model.json
artifacts/B04_real_ROM_metrics.json
artifacts/B04_real_ROM_result.md
artifacts/B04_real_ROM_training_predictions.csv
artifacts/B04_real_ROM_validation_predictions.csv
artifacts/B04_real_ROM_training_prediction.png
artifacts/B04_real_ROM_validation_prediction.png
```

每个预测 CSV 同时保存六区真值、一步预测、连续滚动预测及对应误差。每张 PNG 按六个承载区域分面板绘制，并在同一时间轴上同时展示 `truth`、`one-step` 和 `rolling`，以便直接比较两种预测方式。

模型加载器会严格核对 6×6 名称、特征数、系数形状、有限值和训练缩放尺度，拒绝可被 NumPy 广播成错误六区结果的损坏快照。metrics/model 同时记录配置、runner、B54 模块、ARX 模块、B04 质量模块的 SHA-256，以及每个 Case 的 CSV、manifest、质量报告哈希；成功交付还记录 Python、NumPy、PyYAML 和 Matplotlib 版本。

`B04_real_ROM_metrics.json` 必须对六个区域分别给出以下四组指标：

- 训练序列一步预测；
- 训练序列连续滚动预测；
- 独立验证序列一步预测；
- 独立验证序列连续滚动预测。

每组至少包含 `RMSE`、按真值范围归一化的 `NRMSE` 和 Pearson 相关系数。常值序列导致无法定义的 NRMSE 或相关系数应以 JSON `null` 及原因记录，不应输出非标准 `NaN`。

`B04_real_ROM_result.md` 除数值指标外，还必须明确说明独立验证序列上的主要响应区域、真值/预测变化方向和响应延迟。训练图只能标作拟合诊断，不得代替独立验证结论。

成功运行的 JSON 使用 `run_status: COMPLETE`；由于本任务没有给定 RMSE/相关性/延迟的自动通过阈值，`acceptance_status` 保持 `REVIEW_REQUIRED`，不擅自将“程序跑完”宣称为“工程验收通过”。

## 验收前检查

1. 模型 JSON 中只有 6 个 `actual_massflow_*` 输入和 6 个基准差分输出。
2. 基准、训练和验证 Case 的路径、Case ID、时序 SHA-256 和源运行标识均不同。
3. 一步预测明确使用真实历史输出；连续滚动预测只在最前 `max_lag` 行使用真实值初始化，之后不再读取真实输出。
4. 训练和验证各有一张六区合并预测图和一个预测 CSV，且都同时包含真值、一步预测、连续滚动预测及逐区域指标。
5. 独立验证结果能正确反映主要响应区域、变化方向和时间延迟，且报告没有用训练集拟合图代替该结论。
