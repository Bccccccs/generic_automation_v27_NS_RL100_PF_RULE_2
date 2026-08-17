# Unified launcher

所有用户流程统一从一个脚本启动：

```bash
python scripts/workflow.py --help
```

六个子命令为 `actions`、`mock`、`ccm`、`organize`、`check` 和 `figures`。
子命令必须放在选项前面。例如生成动作表时使用：

```bash
python scripts/workflow.py actions --config <actions.yaml> --output-dir <case-dir>
```

`ccm` 子命令再通过 `--execution-mode` 区分功能：

| 模式 | 功能 |
| --- | --- |
| `run` | 生成宏、启动 STAR-CCM+、收集并整理结果 |
| `dry-run` | 只生成宏和运行计划，不启动 STAR-CCM+ |
| `package-only` | 打包并校验已有的运行时 CSV |
| `validate-only` | 只校验已打包 Case，为兼容模式 |

其中 `package-only` 和 `validate-only` 都属于“处理已有结果”，不会启动 STAR-CCM+。可用以下命令查看当前可用选项：

```bash
python scripts/workflow.py ccm --help
```

### `ccm run`：启动 STAR-CCM+ 并自动整理

```bash
python scripts/workflow.py ccm \
  --schedule <case-dir>/input/actuation_schedule.csv \
  --sim <template.sim> \
  --out <case-dir>/raw_star \
  --starccm-path '<STAR-CCM+可执行文件>' \
  --region <Region名称> \
  --time-step <求解器物理时间步> \
  --np <并行核数> \
  --execution-mode run
```

必需输入：动作表、STAR 模板 `.sim`、STAR 可执行文件。运行后会将输出放在 `<case-dir>/raw_star`，并自动整理到 `<case-dir>`。

### `ccm dry-run`：只生成宏和运行计划

```bash
python scripts/workflow.py ccm \
  --schedule <case-dir>/input/actuation_schedule.csv \
  --sim <template.sim> \
  --out <case-dir>/raw_star \
  --region <Region名称> \
  --time-step <求解器物理时间步> \
  --execution-mode dry-run
```

该模式不启动 STAR-CCM+，主要检查生成的 `FlowControlRunMacro.java` 和 `starccm_runtime_plan.json`。

### `ccm package-only`：打包已有运行时 CSV

```bash
python scripts/workflow.py ccm \
  --schedule <case-dir>/input/actuation_schedule.csv \
  --sim <template.sim> \
  --out <case-dir>/raw_star \
  --execution-mode package-only
```

执行前必须已存在 `<case-dir>/raw_star/timeseries.csv`。该模式会生成 `<case-dir>/processed/timeseries.csv`、`quality_report.json` 和质量检查图，不会启动 STAR-CCM+。

如果手中是拆分的 STAR Monitor CSV，而不是 `raw_star/timeseries.csv`，应使用 `workflow.py organize`。

### `ccm validate-only`：校验已打包 Case

```bash
python scripts/workflow.py ccm \
  --schedule <case-dir>/input/actuation_schedule.csv \
  --sim <template.sim> \
  --out <case-dir>/raw_star \
  --execution-mode validate-only
```

执行前必须已存在 `<case-dir>/processed/timeseries.csv` 等标准 Case 文件。当 `--out` 为 `<case-dir>/raw_star` 时，程序校验的是其父目录 `<case-dir>`。

### 直接从动作 YAML 生成调度

上述四个模板中，都可将：

```bash
--schedule <case-dir>/input/actuation_schedule.csv
```

替换为：

```bash
--actuation-config <actions.yaml>
```

`--schedule` 和 `--actuation-config` 必须二选一，不能同时传入。

参数与完整示例见项目根目录 `README.md`。
