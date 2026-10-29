# 命令参考

所有命令在仓库根目录执行。统一入口 `python scripts/workflow.py <子命令> [选项]`，子命令必须写在选项前；
`python scripts/workflow.py <子命令> --help` 显示完整参数（以 `--help` 为准，本文只列常用项）。

| 子命令 | 作用 | 实现 |
|---|---|---|
| `actions` | 动作 YAML → `input/actuation_schedule.csv` | `flow_control/generator/`、`excitation_patterns/` |
| `ccm` | 生成宏、启动 STAR（CPU/GPU）、自动整理 | `flow_control/cli/run_starccm.py` → `adapters/starccm_runner.py` |
| `ccm-status` | Slurm、MPI、步数和错误状态（会回写清单） | `flow_control/cli/ccm_status.py` |
| `ccm-monitor` | 每秒只读刷新步速、进度、ETA、本机进程 | `flow_control/cli/ccm_monitor.py` |
| `organize` | STAR 输出 → 标准 Case | `flow_control/cli/organize_outputs.py` |
| `check` | 质量检查 + 诊断 PNG → `quality_report.json` | `flow_control/cli/check_case.py` |
| `figures` | 5 张 SVG 汇总图（默认从 manifest 的 0.5 s 剔除点开始） | `flow_control/cli/generate_figures.py` |
| `mock` | 不启动 STAR，用 MockDynamic24x6 跑通流程 | `flow_control/cli/run_mock_dynamic24x6.py` |
| `train-residual-rom` | B55 训练 | `flow_control/rom/train_residual_rom.py` |
| `train-probabilistic-rom` / `evaluate-probabilistic-rom` | B56 训练 / 评估 | `flow_control/rom/` |

独立脚本：`scripts/build_b53_dataset.py`（B53）、`scripts/analysis/run_b54_rom_from_b53.py`（B54）、
`scripts/inspect_ccm_template.py` / `inspect_ccm_geometry.py`（`.sim` 检查）、`scripts/setup_cluster_env.sh`（集群环境）、
`scripts/analysis/plot_raw_vs_delta.py`、`plot_report_figures.py`（汇报图）。数据集和 ROM 见 [`ROM.md`](ROM.md)。

## 1. 时间、窗口与动作表

- `time_step`：求解器物理时间步（当前 1e-4 s）；`window_duration`：一个动作窗口的长度；`total_windows`：窗口数。
- 总物理时间 = `total_windows × window_duration`；窗口长度必须是时间步的整数倍。
- 动作 YAML 的 `time_step`、`ccm --time-step` 与 STAR 实际时间步必须一致。
- 宏在**每个求解时间步**结束后采样一行；只有每窗口一行说明代码过旧。

动作模式（`actuation.mode`）：`no_jet_reference`、`pulse_singlejet`、`step_singlejet`、`chirp_keyjets`、`prbs_demo`、
`sparse_random_groups`、`balanced_singlejet_pulses`（B52/B06 使用；可选 `jet_groups` 让指定组合同时开启）。
`pulse_singlejet`/`step_singlejet` 只读 `jet_ids[0]`。随机种子默认取 `configs/system.yaml` 的 `system.random_seed`，
可用 `actuation.random_seed` 覆盖，或用环境变量 `FLOW_CONTROL_SYSTEM_CONFIG` 指向其他 system.yaml。

## 2. `actions`

```bash
python scripts/workflow.py actions --config <动作YAML> --output-dir <case_dir>
```

产出 `<case_dir>/input/{actuation_schedule.csv, config_summary.yaml, validation_report.json}`。检查
`validation_report.json` 的 `passed=true` 且无 `errors`；无喷气工况质量流量应全零。

## 3. `ccm`

```bash
python scripts/workflow.py ccm \
  --schedule <case_dir>/input/actuation_schedule.csv --sim <模板.sim> --out <case_dir>/raw_star \
  --starccm-path <starccm+> --region 区域 --time-step 1e-4 --np 8 \
  [--scheduler slurm --slurm-job-id "$SLURM_JOB_ID" | --machinefile <hosts>] \
  [--compute-backend gpu --gpgpu auto:N:nomps --mpi-driver openmpi --gpu-qualification <资格文件>] \
  --execution-mode run
```

| 模式 | 行为 |
|---|---|
| `run` | 生成宏、启动 STAR、收集输出、自动整理到 `<case_dir>` |
| `dry-run` | 只生成宏和运行计划，不启动 STAR（本地可跑，用于检查参数） |
| `package-only` | 打包并校验已有的 `<out>/timeseries.csv` |
| `validate-only` | 只校验已打包的 Case（兼容旧流程） |

其他常用参数：`--actuation-config <YAML>`（代替 `--schedule`，现场生成动作表）、`--manifest-template`
（默认 `configs/week4/case_manifest_template.yaml`）、`--report <名称>`（额外采样的 report）、`--podkey`、
`--mpi-env NAME=VALUE`、`--no-save-result-sim`、`--non-strict-boundaries`（缺喷口边界时跳过，正式算例不要用）。
`--scheduler slurm` 只读取已有分配并生成 hostfile，不会替你申请资源。GPU 细节见 [`GPU.md`](GPU.md)。

## 4. 状态与监控

```bash
python scripts/workflow.py ccm-monitor [--out <case_dir>] [--pid <PID>] [--once] [--interval 1]
python scripts/workflow.py ccm-status --out <case_dir>/raw_star [--tail 2]
tail -f <case_dir>/raw_star/starccm_flow_control.log
squeue -u $USER
sacct -j <JobID> --format=JobID,State,ExitCode,Elapsed,MaxRSS,AllocTRES%80
```

`ccm-monitor` 不加 `--out` 时自动选择 `runs/` 下日志最近更新的目录（首行显示，注意核对）；只看本机进程，
CPU% 为 `ps` 生存期平均；剩余时间用清单里的 `total_steps`。它不修改任何文件，`Ctrl+C` 只退出监控。

## 5. 整理、质检、作图

```bash
python scripts/workflow.py organize --input-dir <case_dir> --output-dir <case_dir> [--star-output-dir <dir>] [--force]
python scripts/workflow.py check   --case-dir <case_dir> --mode ccm [--partial]
python scripts/workflow.py figures --case-dir <case_dir> --mode ccm [--start-time 0]
```

- `organize` 的 `--sample-ownership`：`auto` 只在 runtime CSV 自带 `window_id` 时推断为 `embedded`；只有监视器导出的旧数据必须显式指定。
- `check` 中的物理一致性检查默认读取 `configs/mappings/B02_boundary_mapping.csv`、`B02_report_mapping.csv`；
  manifest 顶层或 `star:` 段的 `boundary_mapping_file` / `report_mapping_file` 可覆盖。
- 验收：`quality_report.json` 的 `run_success_flag=true`、blocker 为 0；`JET_*`、`cmd_massflow_*`、`actual_massflow_*` 各 24 列齐全。

## 6. 标准 Case 目录

```text
<case_dir>/
  input/                  actuation_schedule.csv、config_summary.yaml、validation_report.json
  raw_star/               FlowControlRunMacro.java、starccm_runtime_plan.json、starccm_flow_control.log、
                          sim_template_snapshot.yaml、timeseries.csv、flow_control_result.sim、
                          case_manifest.yaml、gpu_execution.json（GPU）、
                          output/（自动收集的轻量 CSV）、out_put/（整理时归档的原始证据）
  processed/timeseries.csv
  figures/
  actuation_schedule.csv、case_manifest.yaml、quality_report.json、notes.md
```

`processed/timeseries.csv` 主要字段：`physical_time`、`window_id`、`JET_01..24`、`cmd_massflow_01..24`、
`actual_massflow_01..24`（喷入计算域为正）、`Fz_S1L/S1R/S2L/S2R/S3L/S3R`、`Fz_Total`、`Drag_Total`、
`Pitch_Moment`、`Roll_Moment`、`Jet_Reaction_Z`、`solver_status`、`case_stage`。
字段语义与 STAR report 对应关系见 `configs/week4/case_manifest_template.yaml` 和 `configs/mappings/`。

从集群拷回本地时至少拷 `input/`、`raw_star/` 中的 CSV/YAML/日志、`processed/`、`case_manifest.yaml`；`.sim` 按需。

## 7. 测试

```bash
python -m pytest -q                                                      # 全量（无 torch 时依赖 torch 的 B56 测试自动跳过）
python -m pytest -q tests/test_b53_dataset.py tests/test_b54_from_b53.py # 部分模块
```

## 8. 常见问题

| 现象 | 处理 |
|---|---|
| 结果只有每窗口一行 | 代码过旧，同步最新代码 |
| 找不到 J01 等喷口边界 | `--region` 写错；`cifu0.sim` 用 `区域` |
| `actual_massflow_*` 缺失 | 查 `raw_star/starccm_flow_control.log` 与 `raw_star/output/` |
| `organize` 找不到 CSV | 用 `--star-output-dir` 显式指定 |
| `OUTPUT_REUSE_FORBIDDEN` / 重跑混入旧结果 | 换新目录；确需覆盖整理结果才用 `organize --force`，不要删旧证据 |
| STAR 启动失败 | 依次查可执行路径（含空格要加引号）、`.sim`、`--region`、许可证/`--podkey`、残留 STAR 进程、日志第一处错误 |
| Slurm `OUT_OF_MEMORY` | 看 `sacct` 的 MaxRSS；海光集群按 CPU 数（3.8 GB/核）申请内存 |
| B53 `REFERENCE_TIME_MISMATCH` | 参考算例时间步或时长与数据 Case 不一致 |
| STAR 2606 报 `No module named starccm.__main__` | 仓库 `starccm/` 包遮蔽；`cd` 到 Case 目录再用绝对路径运行，见 [`GPU.md`](GPU.md) 2.2 |
