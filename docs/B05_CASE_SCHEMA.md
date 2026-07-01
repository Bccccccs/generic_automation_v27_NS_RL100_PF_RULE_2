# B05 Unified Case Data Schema

B05 建立统一的数据存储与日志规范，用于标准化管理 CFD、RL rollout
和 mock plant 的所有算例结果。核心目标是让不同数据来源输出同一种
case archive，使后续对比、复现、质量筛查和训练数据整理不再依赖
各自临时文件格式。

## 1. Scope

B05 的实现集中在 `flow_control/data_schema.py`，新增 `CaseSchema` 作为
统一 IO 入口，同时保留原有 `ControlAction`、`Schedule`、`PlantObservation`
和 `ExperimentConfig` 数据结构。

当前覆盖范围：

- 严格定义 `timeseries.csv` 标准列。
- 严格校验 `case_manifest.yaml` 必填字段。
- 自动创建标准 run 目录。
- 自动写入 `case_manifest.yaml`、`actuation_schedule.csv`、`timeseries.csv`
  和 `quality_report.json`。
- 自动生成 `logs/case_io.log`。
- 自动生成质量报告。
- 支持 DataFrame-like、dict-of-lists、list-of-dicts 三类表格输入。
- 支持额外扩展字段，但必填字段和必填列必须完整。

## 2. Standard Directory Layout

每个 case 必须输出到：

```text
runs/<case_id>/
├── case_manifest.yaml
├── actuation_schedule.csv
├── timeseries.csv
├── quality_report.json
├── figures/
└── logs/
```

`CaseSchema.build_run_directory(case_id)` 会自动创建：

- `runs/<case_id>/`
- `runs/<case_id>/figures/`
- `runs/<case_id>/logs/`

`case_id` 必须是普通目录名，不允许包含路径分隔符。这样可以避免调用方
意外写入 `runs/` 之外的位置。

## 3. Timeseries Schema

`timeseries.csv` 是统一数据格式中最重要的文件。每一行代表一个时间步或
控制窗口的观测结果，列顺序由 `TIMESERIES_REQUIRED_COLUMNS` 固定。

### Time And Control Columns

```text
physical_time
window_id
JET_01
JET_02
...
JET_24
```

说明：

- `physical_time`：物理时间，单位由 manifest 中的实验配置解释。
- `window_id`：控制窗口 ID，必须按行连续递增，步长为 1。
- `JET_01` 到 `JET_24`：24 个喷气输入通道。数值可以是 0/1 开关，也可以是
  幅值、质量流量或归一化控制量，但必须在同一批 case 中保持一致语义。

### Load Columns

```text
Fz_S1L
Fz_S1R
Fz_S2L
Fz_S2R
Fz_S3L
Fz_S3R
```

说明：

- 三组载荷单元，每组左右两侧，共 6 个 `Fz` 输出。
- 这 6 列用于横向比较不同 solver、mock plant 和 RL rollout 的局部载荷响应。

### Global Output Columns

```text
Fz_Total
Drag_Total
Pitch_Moment
Roll_Moment
Jet_Reaction_Z
solver_status
```

说明：

- `Fz_Total`：总竖向载荷。
- `Drag_Total`：总阻力。
- `Pitch_Moment`：俯仰力矩。
- `Roll_Moment`：滚转力矩。
- `Jet_Reaction_Z`：喷气反作用力在 Z 方向的分量。
- `solver_status`：求解或 rollout 状态。自动质量报告会把
  `ok`、`success`、`converged`、`stable`、`1`、`true` 视为成功状态。

## 4. Manifest Schema

`case_manifest.yaml` 记录复现一个 run 所需的核心元数据。必填字段由
`MANIFEST_REQUIRED_FIELDS` 固定：

```yaml
geometry_version: geom-v1
mesh_version: mesh-v1
flow_velocity: 45.0
gap: 0.012
time_step: 0.01
jet_amplitude: 1.0
window_duration: 0.1
random_seed: 1234
git_commit: abc123
created_time: "2026-06-25T00:00:00+00:00"
```

字段含义：

- `geometry_version`：几何版本，用于追踪 CAD/几何定义。
- `mesh_version`：网格版本，用于追踪网格参数或 mesh cache。
- `flow_velocity`：入口或参考流速。
- `gap`：间隙参数。
- `time_step`：仿真或 rollout 时间步长。
- `jet_amplitude`：喷气幅值定义。
- `window_duration`：控制窗口时长。
- `random_seed`：随机种子。
- `git_commit`：生成该 case 时的代码版本。
- `created_time`：创建时间，建议使用 ISO 8601。

如果调用 `CaseSchema.write_case()` 时没有提供 `git_commit` 或 `created_time`，
模块会自动补充当前 git commit 和 UTC 时间。

## 5. Quality Report

`quality_report.json` 由 `CaseSchema.write_case()` 自动生成，也允许调用方传入
额外字段覆盖或补充。最终文件必须包含：

```json
{
  "stability_score": 1.0,
  "constraint_violation_count": 0,
  "jet_activation_stats": {},
  "correlation_matrix_summary": {},
  "data_completeness": {},
  "run_success_flag": true
}
```

### Generated Metrics

`stability_score`

成功状态行数除以总行数。成功状态由 `solver_status` 判断。

`constraint_violation_count`

当前实现中等于非成功状态行数。后续可以扩展为包含载荷阈值、喷气约束、
姿态约束等物理约束违规计数。

`jet_activation_stats`

每个 `JET_01` 到 `JET_24` 生成：

- `activation_count`
- `activation_fraction`
- `mean_command`
- `max_command`
- `min_command`

`correlation_matrix_summary`

对 6 个局部载荷和 5 个全局数值量计算成对 Pearson 相关性的摘要：

- `columns`
- `mean_abs_offdiag`
- `max_abs_offdiag`
- `strongest_pair`

`data_completeness`

记录缺失数据情况：

- `missing_count`
- `total_cells`
- `complete`

`run_success_flag`

当前默认规则为：没有失败状态，且没有缺失数据。

## 6. Validation Rules

写入前必须通过严格校验。`CaseSchema.write_case()` 会在写文件前执行：

- `timeseries.csv` 必填列完整。
- `JET_01` 到 `JET_24` 全部存在。
- `window_id` 按行连续，步长为 1。
- 不允许 `None`、空字符串或 NaN-like 值。
- `case_manifest.yaml` 必填字段完整。
- 标准目录结构存在。

失败时会抛出 `ValueError`，并在 `runs/<case_id>/logs/case_io.log` 中记录失败原因。

## 7. IO API

### `CaseSchema.validate_timeseries(df)`

校验 timeseries 表格，返回错误列表。空列表表示通过。

支持输入：

- pandas DataFrame-like 对象。
- `dict[str, list]`。
- `list[dict]`。

示例：

```python
errors = CaseSchema.validate_timeseries(timeseries_rows)
if errors:
    raise ValueError(errors)
```

### `CaseSchema.validate_manifest(yaml_dict)`

校验 manifest 字典，返回错误列表。空列表表示通过。

```python
errors = CaseSchema.validate_manifest(manifest)
```

### `CaseSchema.build_run_directory(case_id)`

创建标准目录并返回 `Path`。

```python
run_dir = CaseSchema.build_run_directory("pilot_case_001")
```

### `CaseSchema.write_case(case_data)`

统一写入完整 case bundle。

```python
from flow_control.data_schema import CaseSchema

result = CaseSchema.write_case(
    {
        "case_id": "pilot_case_001",
        "manifest": {
            "geometry_version": "geom-v1",
            "mesh_version": "mesh-v1",
            "flow_velocity": 45.0,
            "gap": 0.012,
            "time_step": 0.01,
            "jet_amplitude": 1.0,
            "window_duration": 0.1,
            "random_seed": 1234,
            "git_commit": "abc123",
            "created_time": "2026-06-25T00:00:00+00:00",
        },
        "timeseries": timeseries_rows,
    }
)
```

返回值包含：

- `case_id`
- `run_dir`
- `files`
- `quality_report`

如果未传入 `actuation_schedule`，模块会从 `timeseries` 中抽取：

```text
physical_time, window_id, JET_01 ... JET_24
```

并自动生成 `actuation_schedule.csv`。

### `CaseSchema.load_case(case_id)`

加载标准 case bundle，并重新运行严格校验。

```python
case = CaseSchema.load_case("pilot_case_001")
manifest = case["manifest"]
timeseries = case["timeseries"]
quality_report = case["quality_report"]
```

## 8. Compatibility Design

### STAR-CCM+ Actual Data

STAR-CCM+ 适配层可以把 report、monitor 和 Java 宏导出的载荷结果映射到
`timeseries.csv` 标准列。必须保证：

- 每个控制窗口都有一行。
- 24 个喷气列即使全为 0 也必须存在。
- 局部载荷和全局载荷列不可缺失。
- solver 状态写入 `solver_status`。

### Mock Plant Output

mock plant 可以直接把输入向量 `u` 映射为 `JET_01` 到 `JET_24`，把 6 维输出
映射为 6 个 `Fz_*` 列，同时派生全局量。这样 mock 数据和真实 CFD 数据可以
使用同一套质量报告和对比脚本。

### RL Rollout Data

RL rollout 可以把 action 写入 `JET_*`，把 environment observation 写入载荷和
全局输出。`random_seed`、`git_commit` 和 `created_time` 使 rollout 可复现，
`window_id` 连续性保证训练轨迹可被严格切片。

## 9. Extensibility

B05 遵循 strict core + expandable edges：

- 核心必填列和字段必须完整。
- 允许在 `timeseries.csv` 中追加额外列，例如 residual、pressure_loss、
  policy_id、reward、episode_id。
- 允许在 manifest 中追加额外元数据，例如 solver backend、case family、
  STAR-CCM+ version、RL policy checkpoint。
- 允许在 quality report 中追加自定义质量指标。

扩展字段不得替代核心字段。横向比较、质量筛查和基础可视化应该只依赖核心字段。

## 10. Tests

B05 新增测试文件：

```text
tests/test_case_schema.py
```

覆盖内容：

- 写入并读取完整 case bundle。
- 自动创建标准目录和日志文件。
- 缺少 `JET_24` 时校验失败。
- 出现 NaN 时校验失败。
- manifest 缺少必填字段时校验失败。
- `window_id` 不连续时校验失败。

验证命令：

```bash
PYTHONPATH=. .venv/bin/pytest tests/test_case_schema.py
```

在提交时，相关验证命令为：

```bash
PYTHONPATH=. .venv/bin/pytest tests/test_actuation_schedule_generator.py tests/test_case_schema.py
```

结果为 `7 passed`。

## 11. Operational Notes

建议所有后续 case 写入都通过 `CaseSchema.write_case()` 进入 `runs/<case_id>/`。
不要在 solver、mock plant 或 RL 逻辑中分散手写 CSV/YAML/JSON。统一入口可以
保证：

- 强一致性：schema strict，错误在写入前暴露。
- 可扩展：额外字段可以保留，不破坏核心规范。
- 可追溯：manifest 记录几何、网格、种子、commit 和创建时间。
- 可对比：不同 solver 和 rollout 输出同一套列名。
- 可复查：日志记录写入开始、失败和完成。

## 12. Integration Status

B05 schema 已接入以下本地流程：

- B03 actuation schedule generation：`flow_control/schedule_generator.py`
  在写入调度、统计 CSV 和 SVG 时，同时生成标准 case 文件：
  `case_manifest.yaml`、`actuation_schedule.csv`、`timeseries.csv`、
  `quality_report.json`、`figures/` 和 `logs/`。
- B04 mock plant rollout：`flow_control/run_mock_demo.py` 在写入 mock 输入、
  输出、相关性和影响排序时，同时把 24 输入、6 输出映射到 B05 标准
  `timeseries.csv`，并生成标准 manifest、quality report、figures 和日志。
- B02 flow-control package boundary：`flow_control/__init__.py` 已导出
  `CaseSchema` 和核心 schema 常量，后续模块可以通过统一入口复用。

仍待接入的生产入口：

- STAR-CCM+ report 收集阶段。
- RL rollout/offline replay 输出阶段。
- sweep runner 的批量 case 汇总阶段。

随着这些入口继续接入，`runs/` 将成为统一的实验数据仓库。
