# Flow Control 喷气算例使用手册

本项目用于完成喷气动作生成、STAR-CCM+ 求解、原始输出收集、标准化整理、质量检查和结果作图。所有用户命令统一从下面的入口执行：

```bash
python scripts/workflow.py <command> [options]
```

查看总帮助或某个子命令的帮助：

```bash
python scripts/workflow.py --help
python scripts/workflow.py ccm --help
```

## 1. 整体流程

完整真实算例的流程为：

```text
动作 YAML
  -> actions 生成逐时间步动作表
  -> ccm 生成宏并启动 STAR-CCM+
  -> 每个求解器时间步结束后采样一次
  -> 自动收集轻量 CSV 到 raw_star/output
  -> 自动执行与 organize 相同的整理逻辑
  -> processed/timeseries.csv
  -> quality_report.json 和诊断图
```

正常情况下只需执行 `actions` 和 `ccm` 两条命令，CCM 结束后会自动完成输出收集与整理。`organize` 仍然保留，用于以下情况：

- 动作生成、STAR 求解和数据整理分开执行；
- STAR 已经计算结束，只需要重新整理输出；
- 整理历史算例；
- 自动整理中断后进行补整理。

## 2. 时间、窗口和动作表

三个时间概念必须区分：

- `time_step`：求解器物理时间步，也是动作表相邻两行的时间间隔；
- `window_duration`：一个喷气动作窗口持续的物理时间；
- `total_windows`：动作窗口总数。

它们满足：

```text
总物理时间 = total_windows × window_duration
每个窗口的时间步数 = window_duration ÷ time_step
动作表总行数 = 总物理时间 ÷ time_step
```

同一个 `window_id` 可以对应多个时间步。喷气指令在窗口内保持不变，但监测值在每个求解器时间步结束后都写一行，而不是每个动作窗口只写一行。

## 3. 环境准备

### 3.1 Python 环境

建议在项目根目录执行：

```bash
python --version
python -m pip install -r requirements.txt
```

如果仓库没有单独的 `requirements.txt`，请使用项目现有的 Python 环境，并确认下面的命令能够正常显示帮助：

```bash
python scripts/workflow.py --help
```

### 3.2 STAR-CCM+ 环境

使用 `--starccm-path <STAR-CCM+可执行文件>` 指定本机 STAR-CCM+。路径包含空格时，需要用单引号包住完整路径。

STAR 模板必须提前满足：

- 存在 `J01` 到 `J24` 的喷口边界；
- 存在 `JET01` 到 `JET24` 的底部受力面，不能把喷口面误作底部受力面；
- 模板允许的最大物理时间不小于本次计算总时长；
- 所需总力、力矩、喷气反力等 Report 可以正常计算；
- 启动新算例前确认没有其他 STAR 服务占满相同 CPU 资源。

## 4. 标准算例快速启动

### 4.1 拉取最新代码

进入项目根目录后拉取当前分支：

```bash
git pull --ff-only
```

### 4.2 生成动作表

```bash
python scripts/workflow.py actions \
  --config <动作配置.yaml> \
  --output-dir <case-dir>
```

### 4.3 可选：启动前只生成宏并检查计划

```bash
python scripts/workflow.py ccm \
  --schedule <case-dir>/input/actuation_schedule.csv \
  --sim <模板.sim> \
  --out <case-dir>/raw_star \
  --region <Region名称> \
  --time-step <求解器时间步> \
  --np <并行核数> \
  --execution-mode dry-run
```

这一步不会启动 STAR，只会生成 Java 宏和运行计划，适合先检查路径、时间步、动作表和边界映射。

### 4.4 启动 STAR-CCM+ 并自动整理

执行：

```bash
python scripts/workflow.py ccm \
  --schedule <case-dir>/input/actuation_schedule.csv \
  --sim <模板.sim> \
  --out <case-dir>/raw_star \
  --starccm-path '<STAR-CCM+可执行文件>' \
  --region <Region名称> \
  --time-step <求解器时间步> \
  --np <并行核数> \
  --execution-mode run
```

建议每次重跑使用新的算例目录，避免旧 CSV 与本次输出混在一起。

## 5. 动作配置文件

动作配置的通用结构如下，尖括号内容需要替换为本次任务的实际参数：

```yaml
actuation:
  mode: <动作模式>
  n_jets: <喷口总数>
  total_windows: <窗口总数>
  window_duration: <窗口长度>
  time_step: <求解器时间步>
  mass_flow_rate: <单喷口质量流量>
  jet_ids: [<喷口编号列表>]
  pulse_windows: [<喷气窗口编号列表>]
```

主要字段含义：

| 字段 | 含义 |
| --- | --- |
| `total_windows` | 动作窗口总数 |
| `window_duration` | 每个窗口长度，单位 s |
| `time_step` | 求解器物理时间步，单位 s |
| `mass_flow_rate` | 喷气开启时的质量流量指令 |
| `jet_ids` | 参与动作的喷口编号列表 |
| `pulse_windows` | 开启喷气的窗口编号列表，从 1 开始 |

修改配置后必须重新运行 `actions`。程序会检查窗口长度能否被时间步整除，并生成逐时间步动作表。

## 6. `actions`：生成动作表

### 命令

```bash
python scripts/workflow.py actions \
  --config <动作配置.yaml> \
  --output-dir <算例目录>
```

### 参数

- `--config`：动作 YAML 配置文件；
- `--output-dir`：动作输出目录，同时通常作为后续标准 Case 根目录。

### 主要输出

```text
<case-dir>/input/actuation_schedule.csv
<case-dir>/input/config_summary.yaml
<case-dir>/input/validation_report.json
<case-dir>/input/actuation_heatmap.svg
<case-dir>/input/total_mass_flow.csv
<case-dir>/input/total_mass_flow_curve.svg
```

`actuation_schedule.csv` 一行对应一个求解器物理时间步。连续行具有相同 `window_id` 时，表示它们属于同一个喷气窗口，窗口内指令保持不变。

## 7. `ccm`：生成宏、启动求解并自动整理

### 7.1 使用已有动作表

```bash
python scripts/workflow.py ccm \
  --schedule <actuation_schedule.csv> \
  --sim <模板.sim> \
  --out <case-dir>/raw_star \
  --starccm-path '<STAR-CCM+可执行文件>' \
  --region <Region名称> \
  --time-step <求解器时间步> \
  --np <并行核数> \
  --execution-mode run
```

### 7.2 直接使用动作 YAML

也可以省略单独执行 `actions`，让 `ccm` 从动作 YAML 生成动作表：

```bash
python scripts/workflow.py ccm \
  --actuation-config <动作配置.yaml> \
  --sim <模板.sim> \
  --out <case-dir>/raw_star \
  --starccm-path '<STAR-CCM+可执行文件>' \
  --region <Region名称> \
  --time-step <求解器时间步> \
  --np <并行核数> \
  --execution-mode run
```

`--schedule` 和 `--actuation-config` 二选一，不能同时使用。

### 7.3 常用参数

| 参数 | 说明 |
| --- | --- |
| `--schedule` | 已生成的逐时间步动作表 |
| `--actuation-config` | 动作 YAML，与 `--schedule` 二选一 |
| `--sim` | STAR-CCM+ 模板 `.sim` 文件 |
| `--out` | STAR 工作目录，推荐固定为 `<case-dir>/raw_star` |
| `--starccm-path` | STAR-CCM+ 可执行文件或 `.bat` 路径 |
| `--np` | STAR 并行核数 |
| `--podkey` | 需要时传入许可证 POD key |
| `--region` | STAR 中需要控制的 Region 名称 |
| `--time-step` | 求解器物理时间步，应与动作配置一致 |
| `--report` | 额外采样的 Report，可重复传入 |
| `--manifest-template` | 可选的 Case manifest 模板 |
| `--non-strict-boundaries` | 放宽边界严格校验，仅在明确知道缺失项可接受时使用 |
| `--no-save-result-sim` | 不保存求解结束后的结果 `.sim` |
| `--execution-mode` | 执行模式，见下表 |

标准输出 Report 和 24 个喷口的实际质量流量监测会自动加入，`--report` 只用于追加其他 Report。

### 7.4 执行模式

| 模式 | 行为 |
| --- | --- |
| `run` | 生成宏、启动 STAR、收集输出并自动整理 |
| `dry-run` | 只生成宏和运行计划，不启动 STAR |
| `package-only` | 只执行打包相关流程 |
| `validate-only` | 只进行输入和配置验证 |

### 7.5 采样和自动整理行为

真实运行时，宏会：

1. 按动作表设置 `J01` 到 `J24` 的质量流量；
2. 每执行完一个求解器物理时间步就采样一次；
3. 写入喷气指令、实际质量流量、总力、分区力、力矩和喷气反力；
4. 把轻量 CSV 收集到 `<case-dir>/raw_star/output/`；
5. 自动调用与 `organize` 相同的整理核心；
6. 生成 `<case-dir>/processed/timeseries.csv` 和质量报告。

注意：`raw_star/output` 保存自动收集的轻量输出；`raw_star/out_put` 保存整理时归档的原始证据。标准结果目录不会额外复制体积较大的 `.sim` 文件。

## 8. `organize`：手动或补充整理

### 命令

```bash
python scripts/workflow.py organize \
  --input-dir <包含动作表和STAR输出的工作目录> \
  --output-dir <标准Case目录>
```

如果 STAR 的输出不在输入目录内部，可以显式指定：

```bash
python scripts/workflow.py organize \
  --input-dir <工作目录> \
  --star-output-dir <STAR输出目录> \
  --output-dir <标准Case目录>
```

目标目录已有文件且确认需要覆盖时，增加：

```bash
--force
```

程序会自动搜索以下常见位置：

```text
<input-dir>/raw_star/output/
<input-dir>/output/
<input-dir>/raw_star/out_put/
<input-dir>/out_put/
<input-dir>/raw_star/
<input-dir>/
```

动作表可以位于 `<input-dir>/input/actuation_schedule.csv` 或输入目录根部。整理器会按 `physical_time` 合并运行时逐步数据和拆分的 STAR Monitor CSV。

## 9. `check`：质量检查

### CCM 算例

```bash
python scripts/workflow.py check \
  --case-dir <case-dir> \
  --mode ccm
```

### Mock 算例

```bash
python scripts/workflow.py check \
  --case-dir <mock-case-dir> \
  --mode mock
```

未完成的中间算例需要检查时可增加 `--partial`。不传 `--case-dir` 时，程序会从 `runs/` 中逐级列出目录供选择。

完整 CCM 算例至少应确认：

- `run_success_flag` 为 `true`；
- blocker 数量为 0；
- 动作表行数与 `processed/timeseries.csv` 行数一致；
- 动作表行数等于“总物理时间除以求解器时间步”；
- `JET_01` 到 `JET_24` 全部存在；
- `cmd_massflow_01` 到 `cmd_massflow_24` 全部存在；
- `actual_massflow_01` 到 `actual_massflow_24` 全部存在；
- 总力、六个分区力、俯仰力矩、滚转力矩和喷气反力均存在。

主要输出：

```text
<case-dir>/quality_report.json
<case-dir>/figures/force_timeseries.png
<case-dir>/figures/jet_schedule.png
<case-dir>/figures/massflow_check_01_06.png
<case-dir>/figures/massflow_check_07_12.png
<case-dir>/figures/massflow_check_13_18.png
<case-dir>/figures/massflow_check_19_24.png
<case-dir>/figures/quality_summary.png
```

## 10. `figures`：生成汇总图

```bash
python scripts/workflow.py figures \
  --case-dir <case-dir> \
  --mode ccm
```

未完成算例可增加 `--partial`。主要输出：

```text
<case-dir>/figures/input_heatmap.svg
<case-dir>/figures/fz_regions.svg
<case-dir>/figures/fz_total.svg
<case-dir>/figures/spatial_nonuniformity.svg
<case-dir>/figures/total_massflow.svg
```

## 11. `mock`：不启动 STAR 的流程测试

使用已有动作表：

```bash
python scripts/workflow.py mock \
  --schedule <case-dir>/input/actuation_schedule.csv \
  --config <Mock配置.yaml> \
  --out <mock-case-dir>
```

直接使用动作 YAML：

```bash
python scripts/workflow.py mock \
  --actuation-config <动作配置.yaml> \
  --config <Mock配置.yaml> \
  --out <mock-case-dir>
```

`mock` 适合验证动作生成、标准 Case、质量检查和作图流程，不代表真实 CFD 结果。

## 12. 标准 Case 目录结构

一次完整真实运行后，目录大致如下：

```text
<case-dir>/
  input/
    actuation_schedule.csv
    config_summary.yaml
    validation_report.json
  raw_star/
    FlowControlRunMacro.java
    starccm_runtime_plan.json
    starccm_flow_control.log
    sim_template_snapshot.yaml
    flow_control_result.sim
    timeseries.csv
    output/
      timeseries.csv
      actuation_schedule.csv
      *_image.csv
    out_put/
      *.csv
  processed/
    timeseries.csv
  figures/
    *.png
    *.svg
  actuation_schedule.csv
  case_manifest.yaml
  quality_report.json
  notes.md
```

是否存在 `flow_control_result.sim` 取决于是否使用 `--no-save-result-sim`。

## 13. 标准时间序列字段

核心文件为：

```text
<case-dir>/processed/timeseries.csv
```

主要字段包括：

- `physical_time`：物理时间；
- `window_id`：动作窗口编号；
- `JET_01` 到 `JET_24`：各喷口开关状态；
- `cmd_massflow_01` 到 `cmd_massflow_24`：指令质量流量；
- `actual_massflow_01` 到 `actual_massflow_24`：STAR 实际质量流量；
- 六个区域的 `Fz`；
- `Fz_Total`、`Drag_Total`；
- `Pitch_Moment`、`Roll_Moment`；
- `Jet_Reaction_Z`；
- `solver_status`、`case_stage`。

实际质量流量会按“喷入计算域为正值”的约定进行标准化，便于直接与正值的指令质量流量比较。

## 14. 常见问题

### 14.1 结果只有每个窗口一行

当前实现应在每个求解器时间步结束后写一行。先确认已拉取目标分支的最新代码，然后检查动作表行数、`raw_star/timeseries.csv` 和 `processed/timeseries.csv` 行数是否一致。

### 14.2 实际质量流量列缺失

检查 STAR 宏日志和 `raw_star/output`。正常运行应包含 24 个喷口的实际质量流量监测，最终列名为 `actual_massflow_01` 到 `actual_massflow_24`。

### 14.3 `organize` 找不到 CSV

优先使用 `--star-output-dir` 明确指定 STAR 输出目录，并确认动作表位于 `input/actuation_schedule.csv` 或输入目录根部。

### 14.4 重跑时混入旧结果

推荐给每次重跑使用新目录。确实要覆盖整理结果时再使用 `organize --force`，不要把不同计算的原始 CSV 放在同一个 `raw_star` 中。

### 14.5 STAR 启动失败

依次检查：

1. `--starccm-path` 是否完整并使用单引号包住含空格的路径；
2. `.sim` 文件是否存在；
3. `--region` 是否与 STAR 模型中的 Region 名称完全一致；
4. 许可证是否可用，是否需要 `--podkey`；
5. 是否有其他 STAR 进程占用资源；
6. `raw_star/starccm_flow_control.log` 中的第一处错误。

### 14.6 时间设置不一致

动作配置中的 `time_step`、`ccm --time-step` 和 STAR 求解器实际时间步必须一致。窗口长度还必须是时间步的整数倍。

## 15. 常用命令速查

```bash
# 总帮助
python scripts/workflow.py --help

# 生成动作
python scripts/workflow.py actions --config <actions.yaml> --output-dir <case-dir>

# 只生成 CCM 宏和计划
python scripts/workflow.py ccm --schedule <schedule.csv> --sim <template.sim> \
  --out <case-dir>/raw_star --time-step <dt> --execution-mode dry-run

# 真实 CCM 计算并自动整理
python scripts/workflow.py ccm --schedule <schedule.csv> --sim <template.sim> \
  --out <case-dir>/raw_star --starccm-path <starccm+> --region <region> \
  --time-step <dt> --np 8 --execution-mode run

# 手动重新整理
python scripts/workflow.py organize --input-dir <work-dir> --output-dir <case-dir>

# 质量检查
python scripts/workflow.py check --case-dir <case-dir> --mode ccm

# 重新生成汇总图
python scripts/workflow.py figures --case-dir <case-dir> --mode ccm

# Mock 全流程测试
python scripts/workflow.py mock --schedule <schedule.csv> \
  --config <Mock配置.yaml> --out <mock-case-dir>
```

## 16. 测试

运行完整测试：

```bash
python -m pytest -q
```

修改 CCM、整理器或动作逻辑后，至少应运行对应测试，并在提交前确认：

```bash
git diff --check
git status --short
```
