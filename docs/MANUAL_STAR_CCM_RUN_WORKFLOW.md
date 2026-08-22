# 人工完成一次 STAR-CCM+ 算例运行

本文档记录如何从动作生成开始，手工完成一次真实 STAR-CCM+ 算例运行、进度监控和结果验收。示例使用当前项目的“无喷气参考、15000 窗口”算例。

本文中的“训练”指启动并跑完一次 STAR-CCM+ CFD 算例，不是训练机器学习模型。

## 1. 本次示例参数

| 参数 | 值 |
| --- | --- |
| 动作模式 | `no_jet_reference` |
| 喷口数 | 24 |
| 动作窗口数 | 15000 |
| 每个窗口时长 | `1.0e-4 s` |
| 求解器时间步 | `1.0e-4 s` |
| 总物理时间 | `1.5 s` |
| 喷气指令 | 24 个喷口全程为零 |
| 动作配置 | `configs/actions/temp_no_jet_1p5s_dt1e-4.yaml` |
| 标准 Case 目录 | `runs/real_star/G00_nojet_15000` |
| STAR 模板 | 仓库根目录下的 `cifu0.sim` |
| Region | `Region` |

## 2. 总体流程

```text
更新代码
  -> 生成并校验动作表
  -> 确认 Slurm 作业和节点
  -> 检查节点上是否有旧 STAR 进程
  -> CCM dry-run
  -> 核对 machinefile 和运行计划
  -> 正式启动 CCM
  -> 监控 Slurm、MPI、Step 和错误
  -> 等待自动整理
  -> 检查标准结果和质量报告
```

## 3. 进入项目并更新代码

所有命令都在仓库根目录执行。

```bash
cd /work/home/acn6k38urd/<项目所在目录>/generic_automation_v27_NS_RL100_PF_RULE_2-week5
git checkout week5
git pull --ff-only origin week5
```

确认统一入口可用：

```bash
python scripts/workflow.py --help
```

## 4. 生成 15000 窗口无喷气动作表

先定义 Case 目录。正式重跑时应更换新目录名，不要把两次真实计算放在同一个 `raw_star` 中。

```bash
CASE_DIR=runs/real_star/G00_nojet_15000
```

生成动作：

```bash
python scripts/workflow.py actions \
  --config configs/actions/temp_no_jet_1p5s_dt1e-4.yaml \
  --output-dir "$CASE_DIR"
```

生成的主要文件位于：

```text
<CASE_DIR>/input/actuation_schedule.csv
<CASE_DIR>/input/config_summary.yaml
<CASE_DIR>/input/validation_report.json
<CASE_DIR>/input/actuation_heatmap.svg
<CASE_DIR>/input/total_mass_flow.csv
<CASE_DIR>/input/total_mass_flow_curve.svg
```

### 4.1 检查动作表

```bash
wc -l "$CASE_DIR/input/actuation_schedule.csv"
cat "$CASE_DIR/input/validation_report.json"
tail -n 2 "$CASE_DIR/input/actuation_schedule.csv"
```

预期结果：

- CSV 共 `15001` 行，包含 1 行表头和 15000 行数据；
- `validation_report.json` 中的 `passed` 为 `true`；
- 最后一行的 `t_end` 为 `1.5`；
- 所有 `JET_01 ... JET_24` 为 `0`；
- 所有 `cmd_massflow_01 ... cmd_massflow_24` 为 `0.0`。

## 5. 确认 Slurm 作业和节点

查看当前用户的正在运行作业：

```bash
squeue -u "$USER" -t R -o "%.18i %.10T %.8D %.8C %.30N"
```

示例输出：

```text
JOBID      STATE    NODES    CPUS    NODELIST
8190201    RUNNING  4        256     c11r1n[20-23]
```

确认当前主机：

```bash
hostname
```

当前主机必须属于准备使用的作业，或者后续命令必须显式传入正确的 Job ID。

设置资源变量：

```bash
JOB_ID=8190201
NP=256
```

查看作业详情和节点：

```bash
scontrol show job -o "$JOB_ID"
squeue -j "$JOB_ID" -h -o "%N"
scontrol show hostnames "$(squeue -j "$JOB_ID" -h -o "%N")"
```

需要确认：

- `JobState=RUNNING`；
- 节点数与申请的数量一致；
- `NP` 不超过作业分配的 `NumTasks`/`NumCPUs`；
- `NP` 至少为节点数，以便每个节点至少分配一个进程。

## 6. 设置 STAR-CCM+ 运行参数

本次服务器的 STAR-CCM+ 路径为：

```bash
STAR_BIN='/work/home/acn6k38urd/apprepo/starccmplus/17.06.007-none/app/17.06.007-R8/STAR-CCM+17.06.007-R8/star/bin/starccm+'
SIM_FILE="$PWD/cifu0.sim"
REGION='Region'
```

确认路径和动作表：

```bash
test -x "$STAR_BIN" && echo "STAR 可执行文件正常" || echo "STAR 路径异常"
test -f "$SIM_FILE" && echo "SIM 模板正常" || echo "SIM 模板不存在"
test -f "$CASE_DIR/input/actuation_schedule.csv" && echo "动作表正常" || echo "动作表不存在"
```

三项都正常后再继续。

## 7. 正式运行前检查旧 STAR 进程

当前代码在发现旧 STAR 进程时会输出警告，但不会自动阻止新计算。因此必须在正式启动前人工检查。

对当前作业的所有节点执行：

```bash
for node in $(scontrol show hostnames "$(squeue -j "$JOB_ID" -h -o "%N")"); do
  echo "===== $node ====="
  ssh "$node" "pgrep -u $USER -af '[s]tar-ccm|[s]tarccm'"
done
```

正常结果是每个节点只显示标题，没有进程行。

如果出现进程，先查看其启动时间、运行时长、父进程和完整命令：

```bash
ssh <节点名> \
  "ps -u $USER -o pid,ppid,lstart,etime,cmd | grep -E '[s]tar-ccm|[s]tarccm'"
```

不要在未确认用途时直接 `kill`，这些进程可能属于其他正在计算的算例。必须先根据完整命令、日志路径和启动时间确认归属。

## 8. CCM dry-run

dry-run 会解析 Slurm 作业、生成 machinefile、Java 宏和运行计划，但不启动 STAR-CCM+。

```bash
python scripts/workflow.py ccm \
  --schedule "$CASE_DIR/input/actuation_schedule.csv" \
  --sim "$SIM_FILE" \
  --out "$CASE_DIR/raw_star" \
  --starccm-path "$STAR_BIN" \
  --region "$REGION" \
  --time-step 1.0e-4 \
  --scheduler slurm \
  --slurm-job-id "$JOB_ID" \
  --np "$NP" \
  --execution-mode dry-run
```

dry-run 成功后应生成：

```text
<CASE_DIR>/raw_star/hosts_<JOB_ID>.ma
<CASE_DIR>/raw_star/FlowControlRunMacro.java
<CASE_DIR>/raw_star/starccm_runtime_plan.json
```

检查 machinefile：

```bash
cat "$CASE_DIR/raw_star/hosts_${JOB_ID}.ma"
```

4 节点、256 进程的示例结果为：

```text
c11r1n20:64
c11r1n21:64
c11r1n22:64
c11r1n23:64
```

确认宏和运行计划存在：

```bash
ls -lh \
  "$CASE_DIR/raw_star/FlowControlRunMacro.java" \
  "$CASE_DIR/raw_star/starccm_runtime_plan.json"
```

15000 窗口会使 `starccm_runtime_plan.json` 较大，当前约为 100 MB。这不会阻止运行，但启动前应确认存储空间充足：

```bash
df -h .
```

## 9. 正式启动 CCM

为防止 SSH 会话断开，建议在 `tmux` 中启动：

```bash
tmux new -s nojet15000
```

正式启动命令与 dry-run 只有执行模式不同：

```bash
python scripts/workflow.py ccm \
  --schedule "$CASE_DIR/input/actuation_schedule.csv" \
  --sim "$SIM_FILE" \
  --out "$CASE_DIR/raw_star" \
  --starccm-path "$STAR_BIN" \
  --region "$REGION" \
  --time-step 1.0e-4 \
  --scheduler slurm \
  --slurm-job-id "$JOB_ID" \
  --np "$NP" \
  --execution-mode run
```

启动时应先看到类似信息：

```text
[flow_control] Slurm job=<JOB_ID> nodes=<节点数> processes=<MPI进程数>
[flow_control] Slurm 预检：检查节点 SSH 和已有 STAR 进程
[flow_control] Slurm 预检通过：<节点数> 个节点均可访问
[flow_control] 开始启动 STAR-CCM+
```

如果出现以下警告，表示本次启动前已经有 STAR 进程：

```text
预检警告：existing STAR-related processes were detected before launch
```

当前程序发出警告后仍会继续启动。如果尚未确认旧进程归属，应立即按 `Ctrl+C` 中断新启动，再按第 7 节检查进程。

从 `tmux` 临时退出但保持计算运行：

```text
Ctrl+B，然后按 D
```

重新进入：

```bash
tmux attach -t nojet15000
```

## 10. 监控计算

在另一个终端进入同一仓库，重新设置 Case 变量：

```bash
cd /work/home/acn6k38urd/<项目所在目录>/generic_automation_v27_NS_RL100_PF_RULE_2-week5
CASE_DIR=runs/real_star/G00_nojet_15000
```

### 10.1 统一状态命令

```bash
python scripts/workflow.py ccm-status \
  --out "$CASE_DIR/raw_star" \
  --tail 5
```

它会显示：

- 运行状态；
- STAR-CCM+ 版本；
- Slurm Job 状态；
- 节点列表；
- 请求/实际 MPI 进程数；
- 已完成 Step 及百分比；
- 日志路径和近期错误。

持续刷新：

```bash
watch -n 10 "python scripts/workflow.py ccm-status --out '$CASE_DIR/raw_star' --tail 3"
```

### 10.2 直接查看 STAR 日志

```bash
tail -f "$CASE_DIR/raw_star/starccm_flow_control.log"
```

只查看最后 100 行：

```bash
tail -n 100 "$CASE_DIR/raw_star/starccm_flow_control.log"
```

搜索常见错误：

```bash
grep -Ei 'UCX ERROR|Failed to create UCP|selected pml|mpi_errors_are_fatal|pam_slurm_adopt|Authentication failed|Fatal|Exception' \
  "$CASE_DIR/raw_star/starccm_flow_control.log" | tail -n 50
```

### 10.3 检查 Slurm 作业

```bash
squeue -j "$JOB_ID" -o "%.18i %.10T %.8D %.8C %.30N"
```

## 11. 成功结束后的验收

`ccm --execution-mode run` 是前台阻塞命令。STAR 计算成功结束后，它会继续自动整理数据并运行质量检查。在终端重新出现 shell 提示符之前，不要把“STAR 求解完成”误认为“整个流程完成”。

先查看状态：

```bash
python scripts/workflow.py ccm-status \
  --out "$CASE_DIR/raw_star" \
  --tail 10
```

查看关键文件：

```bash
ls -lh \
  "$CASE_DIR/raw_star/case_manifest.yaml" \
  "$CASE_DIR/raw_star/starccm_flow_control.log" \
  "$CASE_DIR/raw_star/timeseries.csv" \
  "$CASE_DIR/raw_star/flow_control_result.sim" \
  "$CASE_DIR/processed/timeseries.csv" \
  "$CASE_DIR/quality_report.json"
```

检查时间序列行数：

```bash
wc -l \
  "$CASE_DIR/raw_star/timeseries.csv" \
  "$CASE_DIR/processed/timeseries.csv"
```

完整计算应有 15000 个数据行；如果 CSV 含一行表头，`wc -l` 结果应为 `15001`。

查看运行结论：

```bash
python - <<'PY'
import json
from pathlib import Path

case_dir = Path("runs/real_star/G00_nojet_15000")
report = json.loads((case_dir / "quality_report.json").read_text(encoding="utf-8"))
print("run_success_flag:", report.get("run_success_flag"))
print("blocking_issue_count:", report.get("blocking_issue_count"))
PY
```

需要时可手动重新运行质量检查和生成图：

```bash
python scripts/workflow.py check --case-dir "$CASE_DIR" --mode ccm
python scripts/workflow.py figures --case-dir "$CASE_DIR" --mode ccm
```

## 12. 异常中断后怎么做

### 12.1 人工按了 `Ctrl+C`

当前运行器会把 manifest 标记为失败，并记录返回码 `130`。先查看：

```bash
python scripts/workflow.py ccm-status --out "$CASE_DIR/raw_star" --tail 20
```

如果 STAR 进程没有退出，先确认它属于本次被中断的计算，再进行终止处理。

### 12.2 STAR 非零退出

查看：

```bash
tail -n 100 "$CASE_DIR/raw_star/starccm_flow_control.log"
python scripts/workflow.py ccm-status --out "$CASE_DIR/raw_star" --tail 20
```

`case_manifest.yaml` 会保留退出码、失败分类、完成 Step 和日志摘要。

### 12.3 重跑规则

正式重跑推荐使用新目录，例如：

```bash
CASE_DIR=runs/real_star/G00_nojet_15000_retry01
```

然后从第 4 节重新生成动作表。这样能保留失败现场，也能避免旧 CSV、日志和新计算混在一起。

## 13. 常见问题

### 13.1 找不到 Slurm 作业

显式传入：

```bash
--scheduler slurm --slurm-job-id "$JOB_ID"
```

并确认作业是 `RUNNING`，不是 `PENDING`。

### 13.2 machinefile 进程数不对

查看：

```bash
cat "$CASE_DIR/raw_star/hosts_${JOB_ID}.ma"
```

每行格式应为 `主机名:进程数`，所有行的进程数之和应等于 `NP`。

### 13.3 SSH 预检失败

手工检查：

```bash
ssh <节点名> hostname
```

返回的主机名必须与预期节点一致。

### 13.4 发现已有 STAR 进程

不要直接再启动。先按第 7 节确认进程归属。如果启动命令已经继续执行，先按 `Ctrl+C` 阻止新的重复计算。

### 13.5 Region 或喷口边界不存在

默认使用：

```bash
REGION='Region'
```

模板中必须有 `J01 ... J24` 边界。正式算例不建议使用 `--non-strict-boundaries` 跳过缺失边界，否则可能得到看似完成但动作未正确应用的结果。

### 13.6 无喷气算例中 `Jet_Reaction_Z` 非零

当前模板中该 Report 可能表示喷口表面上的压力和剪切力，因此无喷气时不一定严格为零。这需要结合实际质量流量列和质量报告判断，不能只根据字段名称判定喷气未关闭。

## 14. 通用命令模板

用于其他算例时，只需替换下列变量：

```bash
CASE_DIR='<新 Case 目录>'
ACTION_CONFIG='<动作 YAML>'
JOB_ID='<Slurm Job ID>'
NP='<MPI 进程数>'
SIM_FILE='<模板 .sim 绝对路径>'
STAR_BIN='<starccm+ 绝对路径>'
REGION='<STAR Region 名称>'
TIME_STEP='<物理时间步>'
```

生成动作：

```bash
python scripts/workflow.py actions \
  --config "$ACTION_CONFIG" \
  --output-dir "$CASE_DIR"
```

dry-run：

```bash
python scripts/workflow.py ccm \
  --schedule "$CASE_DIR/input/actuation_schedule.csv" \
  --sim "$SIM_FILE" \
  --out "$CASE_DIR/raw_star" \
  --starccm-path "$STAR_BIN" \
  --region "$REGION" \
  --time-step "$TIME_STEP" \
  --scheduler slurm \
  --slurm-job-id "$JOB_ID" \
  --np "$NP" \
  --execution-mode dry-run
```

正式运行：

```bash
python scripts/workflow.py ccm \
  --schedule "$CASE_DIR/input/actuation_schedule.csv" \
  --sim "$SIM_FILE" \
  --out "$CASE_DIR/raw_star" \
  --starccm-path "$STAR_BIN" \
  --region "$REGION" \
  --time-step "$TIME_STEP" \
  --scheduler slurm \
  --slurm-job-id "$JOB_ID" \
  --np "$NP" \
  --execution-mode run
```

监控：

```bash
python scripts/workflow.py ccm-status --out "$CASE_DIR/raw_star" --tail 5
```
