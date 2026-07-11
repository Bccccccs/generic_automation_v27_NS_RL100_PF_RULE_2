# B03 STAR 导出数据导入说明

本模块的目的不是直接训练算法，而是把 STAR-CCM+ 导出的监视器 CSV
整理成算法后续能读取、检查和追溯的标准数据包。

代码位置：

```text
flow_control/star_ingest/
```

主要文件：

```text
flow_control/star_ingest/star_export_reader.py
flow_control/star_ingest/case_data_loader.py
flow_control/star_ingest/quality_checker.py
flow_control/star_ingest/figures_generator.py
flow_control/star_ingest/step1_generate_timeseries.py
flow_control/star_ingest/step2_check_case.py
flow_control/star_ingest/step3_generate_figures.py
examples/ingest_star_case.py
examples/build_real_star_ingest_demo.py
```

约定：`runs/` 目录只放输入数据、生成的数据包、图和质量报告，不放启动脚本。
正式三步入口放在 `flow_control/star_ingest/` 模块里，旧的一步式示例保留在
`examples/`。

## 一个跑完的 case 应该包括什么

统一约定：只要使用 `--case-id <case_id>`，输出就固定写到：

```text
runs/<case_id>/
```

一个完整 case bundle 应该长这样：

```text
runs/<case_id>/
  case_manifest.yaml
  actuation_schedule.csv
  timeseries.csv
  quality_report.json
  notes.md
  input/
    actuation_schedule.csv
    config_summary.yaml
    validation_report.json
    actuation_heatmap.svg
    total_mass_flow_curve.svg
  figures/
    force_timeseries.png
    jet_schedule.png
    massflow_check.png
    quality_summary.png
  logs/
  flow_snapshots/
```

这套结构用于统一三种来源：

| 来源 | 进入方式 | 最终产物 |
| --- | --- | --- |
| mock 仿真 | mock runner 直接写标准列 | `runs/<case_id>/timeseries.csv` |
| ARX/ROM 数据 | 训练或验证数据按 `window_id`/`physical_time` 对齐 | `runs/<case_id>/timeseries.csv` |
| 实际 STAR-CCM+ | Step 1 从 STAR 导出 CSV 主动合成 | `runs/<case_id>/timeseries.csv` |

也就是说，STAR-CCM+ 原始产物里没有 `timeseries.csv` 是正常的；
`timeseries.csv` 是本模块生成出来的标准中间层。

`input/` 和根目录里的 `actuation_schedule.csv` 含义不同：

- `runs/<case_id>/input/actuation_schedule.csv`：后端输入，给 mock、STAR-CCM+ 或后续控制器用。
- `runs/<case_id>/actuation_schedule.csv`：标准 case 副本，给检查器、ARX/ROM 和算法读取。

目前两份内容保持一致，目的是同时满足“运行可追溯”和“算法读取路径稳定”。

完整 `timeseries.csv` 的公共列至少包括：

```text
physical_time
window_id
Fz_S1L
Fz_S1R
Fz_S2L
Fz_S2R
Fz_S3L
Fz_S3R
Fz_Total
Drag_Total
Pitch_Moment
Roll_Moment
Jet_Reaction_Z
solver_status
```

无喷气 case 会把 `JET_01 ... JET_24` 写成 0，用来和 mock/ARX 的输入列对齐。
但无喷气 case 不会伪造 `actual_massflow_*`，因为实际质量流量不是凭空生成的数据。
喷气 case 才要求 `cmd_massflow_01 ... cmd_massflow_24` 和
`actual_massflow_01 ... actual_massflow_24` 分开保存。

## 0. 推荐三步流程

现在推荐把 STAR 导入拆成三步，方便定位问题：

```text
Step 1: STAR CSV/目录 -> timeseries.csv 和标准 case 骨架
Step 2: 检查标准 case 数据格式，写 quality_report.json
Step 3: 根据检查结果生成 figures/
```

以根目录下 `无喷气(1)` 为例：

### Step 1: 生成 timeseries

```bash
python -m flow_control.star_ingest.step1_generate_timeseries \
  --star-dir "无喷气(1)" \
  --case-id no_jet_star_case \
  --case-type no_jet \
  --force
```

这一步只生成文件，不做质量判定，不画图。主要输出：

```text
runs/no_jet_star_case/timeseries.csv
runs/no_jet_star_case/actuation_schedule.csv
runs/no_jet_star_case/case_manifest.yaml
runs/no_jet_star_case/quality_report.json
runs/no_jet_star_case/notes.md
runs/no_jet_star_case/input/
runs/no_jet_star_case/input/actuation_schedule.csv
runs/no_jet_star_case/figures/
runs/no_jet_star_case/logs/
runs/no_jet_star_case/flow_snapshots/
```

如果只想先生成动作喷气表，不跑 mock 或 CCM，可以单独运行：

```bash
python -m flow_control.generator.schedule_generator \
  --config configs/actions/pilot_sparse24.yaml \
  --output-dir runs/my_case
```

输出会写到：

```text
runs/my_case/input/actuation_schedule.csv
```

之后 mock 使用同一个输出目录继续跑：

```bash
python examples/run_mock_dynamic24x6.py \
  --schedule runs/my_case/input/actuation_schedule.csv \
  --out runs/my_case \
  --config configs/mock_dynamic24x6.yaml
```

mock 完成后会直接生成：

```text
runs/my_case/timeseries.csv
runs/my_case/actuation_schedule.csv
runs/my_case/quality_report.json
```

同一个 case 也可以直接交给 ARX/ROM：

```bash
python -m flow_control.cli.train_rom \
  --case-id my_case \
  --out runs/my_case/arx_model
```

验证已有模型时也可以用 `case_id`：

```bash
python -m flow_control.cli.validate_rom \
  --model runs/my_case/arx_model/arx_model.json \
  --case-id my_case \
  --out runs/my_case/arx_validation
```

### Step 2: 检查数据格式

```bash
python -m flow_control.star_ingest.step2_check_case \
  --case-id no_jet_star_case
```

这一步读取标准 case 目录，检查：

```text
文件是否齐全
必要列是否存在
physical_time 是否单调递增
是否有 NaN
单位和正方向是否写清楚
无喷气 Jet_Reaction_Z 是否为 0 或需要解释
喷气 case 的 JET/cmd_massflow/actual_massflow 是否完整且一致
```

检查结果写入：

```text
runs/no_jet_star_case/quality_report.json
```

### Step 3: 生成图

```bash
python -m flow_control.star_ingest.step3_generate_figures \
  --case-id no_jet_star_case
```

这一步只画图，并把图路径写回 `quality_report.json`：

```text
runs/no_jet_star_case/figures/force_timeseries.png
runs/no_jet_star_case/figures/jet_schedule.png
runs/no_jet_star_case/figures/massflow_check.png
runs/no_jet_star_case/figures/quality_summary.png
```

旧的 `examples/ingest_star_case.py` 仍然保留，它会一口气完成三步；
但正式调试时优先使用上面的三步入口。

## 1. 两种运行模式

当前支持两种数据检查模式。

### partial_timeseries

用于单个 STAR 导出文件，例如现在的 `FZ.csv`。

这个文件本质上是一个 timeseries 子集：

```text
physical_time
Fz_S1L
Fz_S1R
Fz_S2L
Fz_S2R
Fz_S3L
Fz_S3R
```

程序会自动计算：

```text
Fz_Total = Fz_S1L + Fz_S1R + Fz_S2L + Fz_S2R + Fz_S3L + Fz_S3R
```

但不会伪造还没有导出的列，例如：

```text
Drag_Total
Pitch_Moment
Roll_Moment
Jet_Reaction_Z
JET_01 ... JET_24
cmd_massflow_01 ... cmd_massflow_24
actual_massflow_01 ... actual_massflow_24
```

也就是说，`partial_timeseries` 适合先把浩坤给的一个 CSV 清洗进系统。
之后有新的阻力、力矩、喷气质量流量 CSV，再继续补。

### full_case

用于最终完整数据包验收。

这时缺列必须报错，时间不单调必须报错，NaN 必须报错。
这是给算法训练、回放和后续控制器使用的严格模式。

## 2. 安装依赖

在项目根目录运行：

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt
```

如果已经有可用环境，只需要确认这些依赖存在：

```text
numpy
PyYAML
pytest
matplotlib
```

## 3. 跑当前 FZ 例子

项目里已经放了一个 demo：

```text
runs/real_star_ingest_demo/FZ.csv
```

运行：

```bash
python examples/build_real_star_ingest_demo.py
```

这个脚本会读取 `FZ.csv`，生成标准 case 目录内容：

```text
runs/real_star_ingest_demo/
  FZ.csv
  case_manifest.yaml
  actuation_schedule.csv
  timeseries.csv
  quality_report.json
  figures/
    force_timeseries.png
    quality_summary.png
  notes.md
```

当前 demo 使用的是 `partial_timeseries` 模式，所以输出的
`timeseries.csv` 只包含当前已经真实导出的列：

```text
physical_time
Fz_S1L
Fz_S1R
Fz_S2L
Fz_S2R
Fz_S3L
Fz_S3R
Fz_Total
```

检查输出：

```bash
cat runs/real_star_ingest_demo/quality_report.json
head runs/real_star_ingest_demo/timeseries.csv
open runs/real_star_ingest_demo/figures/force_timeseries.png
```

如果运行成功，`quality_report.json` 里应该能看到：

```json
{
  "validation_mode": "partial_timeseries",
  "num_errors": 0,
  "num_warnings": 0
}
```

## 4. 导入一个新的 STAR CSV

如果拿到新的 STAR 导出文件，比如：

```text
/path/to/FZ.csv
```

可以直接运行：

```bash
python examples/ingest_star_case.py \
  --star-file /path/to/FZ.csv \
  --case-dir runs/my_star_case \
  --case-type no_jet \
  --partial \
  --force
```

参数含义：

| 参数 | 含义 |
| --- | --- |
| `--star-file` | STAR 导出的 CSV 文件 |
| `--case-dir` | 输出的标准 case 目录 |
| `--case-type` | `no_jet`、`jet_on` 或 `unknown` |
| `--partial` | 表示这是一个 timeseries 子集，不做完整列验收 |
| `--force` | 如果输出目录已存在，允许覆盖 |

生成后查看：

```bash
cat runs/my_star_case/quality_report.json
head runs/my_star_case/timeseries.csv
ls runs/my_star_case/figures
```

## 5. 做完整 case 验收

当后续已经把阻力、力矩、喷气反作用力、喷气开关和质量流量都补齐以后，
就不要加 `--partial`。

例如：

```bash
python examples/ingest_star_case.py \
  --star-file /path/to/full_star_export.csv \
  --case-dir runs/my_full_star_case \
  --case-type jet_on \
  --force
```

完整模式下必要列包括：

```text
physical_time
Fz_S1L
Fz_S1R
Fz_S2L
Fz_S2R
Fz_S3L
Fz_S3R
Fz_Total
Drag_Total
Pitch_Moment
Roll_Moment
Jet_Reaction_Z
```

喷气算例还要求：

```text
JET_01 ... JET_24
cmd_massflow_01 ... cmd_massflow_24
actual_massflow_01 ... actual_massflow_24
```

这时如果缺列，`quality_report.json` 会报错。

## 6. 直接在 Python 里调用

也可以不走命令行，直接在脚本里调用：

```python
from flow_control.star_ingest.case_data_loader import ingest_star_export

result = ingest_star_export(
    ["/path/to/FZ.csv"],
    case_dir="runs/my_star_case",
    manifest={
        "case_type": "no_jet",
        "units": {
            "force": "N",
            "moment": "Nm",
            "massflow": "kg/s",
        },
        "sign_convention": (
            "positive Fz = lift upward; "
            "positive Drag = downstream; "
            "positive Pitch = nose up; "
            "positive Roll = right wing down"
        ),
    },
    overwrite=True,
    require_complete_schema=False,
)

print(result["errors"])
print(result["warnings"])
```

`require_complete_schema=False` 对应 `partial_timeseries`。
如果要做完整 case 验收，改成：

```python
require_complete_schema=True
```

## 7. 当前 FZ.csv 的定位

现在这个 `FZ.csv` 是一个真实 STAR timeseries 示例，但不是完整实验数据包。

它能验证：

- 中文时间列能否识别为 `physical_time`。
- STAR 监视器列名能否映射成标准列名。
- 时间是否单调递增。
- 是否有 NaN。
- `Fz_Total` 是否能自动计算。
- 图是否能生成。

它不能验证：

- `Drag_Total` 是否正确。
- `Pitch_Moment`、`Roll_Moment` 是否正确。
- 喷气开关和质量流量是否一致。
- `cmd_massflow` 和 `actual_massflow` 是否分开保存。
- 有喷气算例的 `Jet_Reaction_Z` 是否完整。

这些要等后续 STAR 导出更多 timeseries 文件后继续补。

## 8. 跑测试

运行：

```bash
pytest tests/test_case_data_loader.py -q
```

当前测试覆盖：

- 缺列报错。
- 时间不单调报错。
- NaN 报错。
- 单位和正方向缺失警告。
- 无喷气 `Jet_Reaction_Z = 0` 不当成数据丢失。
- 喷气开关和质量流量一致性检查。
- `cmd_massflow` 和 `actual_massflow` 分开保存检查。
