# AGENTS.md：本项目运行交接说明

> 给任何 AI agent（Claude、Codex、Cursor 等）的说明。把本文件整份复制给对方即可。
> 读完后，agent 应能了解项目流程、向用户问清缺的信息，并给出**可直接复制执行**的命令，
> 用来运行项目的各个部分。

## 0. 项目一句话

用 STAR-CCM+ 仿真 24 个底部喷口（J01～J24）的喷气对六区升力（`Fz_S1L … Fz_S3R`）的影响，
产出训练数据并识别 ARX 降阶模型。流水线：

```text
动作 YAML → actions → ccm（STAR 求解，CPU 或海光 DCU GPU）→ 自动整理 → check/figures
         → B53 数据集（相对无喷气参考的 ΔFz）→ B54 ARX ROM
```

## 1. Agent 工作规则

1. **先问再给命令。** 命令里的路径、配置、卡数等都要先确认，不能把 `<尖括号>` 占位符原样交给用户。
   能从仓库读到的信息自己读，只问需要用户决定的事。
2. **分清在哪执行。** 本地电脑：准备、dry-run、整理、B53、B54。集群登录节点：同步、建环境、
   生成动作表、`sbatch`。集群计算节点：STAR 真实求解、GPU 相关命令。不要在登录节点跑真实求解。
3. **统一入口。** 用 `python scripts/workflow.py <子命令>`，不要另写启动 STAR 或生成动作表的脚本。
4. **不伪造证据。** 不手改 `gpu_execution.json`、不把资格文件里的 `unknown` 改成 `compatible`、
   不删旧日志来强行复用目录、不为了能跑通而关闭物理模型。未确认的事写“未确认”。
5. **每次运行一个新目录。** 推荐 `runs/<工况>_<日期或JobID>`；重试时新建目录，并在说明里关联上一次。
6. **区分“已启动”和“已完成”。** 交付 JobID、日志路径和查看状态的命令，作业还在跑就说“已启动，未完成”。
7. 需要提交 git 时，提交说明用中文。

## 2. 开工前向用户确认

按需提问，已知的不重复问（通常 3～5 个问题）：

1. 要做哪一部分？（生成动作表 / CPU 求解 / GPU 求解 / 查看状态 / 整理质检 / B53 / B54 / 换 `.sim` 模板）
2. 用哪个动作配置 YAML？不知道的话，列出 `configs/actions/`、`configs/b52/` 的候选，读文件后用表格比较
   模式、喷口、总时长、窗口时长、时间步。
3. 在哪台机器跑？代码是否已同步？（集群默认路径见第 3 节）
4. CPU 还是 GPU？GPU 用几张卡？资格文件是否已写好？
5. agent 只给命令、由用户执行，还是 agent 有权限直接执行？

执行前把方案整理给用户确认：

```text
部分：<例如 GPU 求解>
机器 / 仓库：<主机 + 路径>
动作配置：<YAML；模式、时长、时间步>
模板 / 区域：<.sim 路径；区域名>
资源：<CPU 进程数 或 GPU 卡数；分区；内存>
输出目录：<新 case 目录>
未确认项：<没有就写“无”>
```

## 3. 环境事实（2026-10-13）

| 项目 | 值 |
|---|---|
| 集群仓库 | `/public/home/yanbochao/bc/generic_automation_v27_NS_RL100_PF_RULE_2` |
| STAR | 20.02.007-R8 双精度，`/public/home/yanbochao/apprepo/starccmplus/20.02.007-glibc2.28_double/app/20.02.007-R8/STAR-CCM+20.02.007-R8/star/bin/starccm+` |
| 模板 | `cifu0.sim`（仓库根，不入库），**区域名 `区域`**，喷口边界 J01～J24 |
| 求解时间步 | `1e-4` s（当前 B52 v2 与无喷气参考） |
| Python | 集群 conda 环境 `py3.10`（`module load anaconda3/2023.09`）；本地 `.venv` |
| 分区 | `hx1hdnormal01`；GPU 用 `--gres=dcu:N` |
| 无喷气参考 | `runs/cifu0_nojet_11s`（11 s，dt=1e-4） |
| B52 v2 结果 | `runs/b52_v2/training`、`runs/b52_v2/validation` |

完整配置要求见 [`SETUP.md`](SETUP.md)。

## 4. 各部分怎么运行

下面的命令都在**仓库根目录**执行。本地用 `.venv/bin/python` 或已激活的 venv；集群先执行：

```bash
module load anaconda3/2023.09
source "$(conda info --base)/etc/profile.d/conda.sh"
conda activate py3.10
cd /public/home/yanbochao/bc/generic_automation_v27_NS_RL100_PF_RULE_2
```

### 4.1 首次使用：环境初始化（集群登录节点，只做一次）

```bash
bash scripts/setup_cluster_env.sh
```

### 4.2 生成动作表（任意机器）

```bash
python scripts/workflow.py actions --config <动作YAML> --output-dir <case_dir>
```

- 产出 `<case_dir>/input/actuation_schedule.csv`，同目录还有 `config_summary.yaml`、`validation_report.json`。
- 检查：`validation_report.json` 无错误；无喷气工况质量流量应全零；
  总时长 = 窗口数 × 窗口时长。CSV 行数不一定等于求解步数（窗口可能长于时间步）。
- 常用配置：B52 训练 `configs/b52/training_dt1e-4.yaml`、验证 `configs/b52/validation_dt1e-4.yaml`、
  无喷气 `configs/actions/temp_no_jet_<时长>_dt1e-4.yaml`。

### 4.3 CPU 求解（集群）

最简单的方式是批处理脚本，它会自动生成动作表并启动求解：

```bash
CCM_REGION=区域 sbatch run_python.slurm                                                  # 训练集
CCM_REGION=区域 CCM_ACTION_CONFIG=configs/b52/validation_dt1e-4.yaml sbatch run_python.slurm  # 验证集
```

- 输出到 `runs/b52_v2/<配置名>_<JobID>/`；作业日志 `b52-ccm-<JobID>.out/.err`。
- 脚本里 `REGION` 的默认值是 `Region`，与 `cifu0.sim` 不符，**必须传 `CCM_REGION=区域`**。
- 脚本固定 `--np 8`、`--mem=128G`、`--time-step 1e-4`；其他工况先读脚本再决定能否直接用。

在已有分配中手动启动（或本地工作站）：

```bash
python scripts/workflow.py ccm \
  --schedule <case_dir>/input/actuation_schedule.csv --sim <cifu0.sim 路径> \
  --out <case_dir>/raw_star --starccm-path <starccm+ 路径> \
  --region 区域 --time-step 1e-4 --np 8 \
  --scheduler slurm --slurm-job-id "$SLURM_JOB_ID" \
  --execution-mode run
```

把 `--execution-mode run` 改成 `dry-run` 可只生成宏和运行计划、不启动 STAR，适合先在本地检查参数。

### 4.4 GPU 求解（海光 DCU，集群计算节点）

步骤较多，**按 [`docs/RUN_GPU_CCM.md`](docs/RUN_GPU_CCM.md) 逐步执行**。要点：

1. 资格文件：从 `examples/ccm_gpu/qualification.hygon.example.json` 复制为
   `configs/ccm_gpu/qualification.hygon.json`，按手册第 2 节逐字段填真实值，删除 `test_only`。
   `sim_review.items` 必须由用户本人核实，agent 不得代填 `compatible`。
2. 交互申请：`srun -p hx1hdnormal01 -N 1 --ntasks=8 --cpus-per-task=1 --mem=128G --gres=dcu:8 --pty bash`
3. `hy-smi --showpids` 确认卡空闲。
4. 激活 conda 后**必须** `source /public/home/yanbochao/apprepo/starccmplus/20.02.007-glibc2.28_double/scripts/env.sh`
   （漏了会报 `gfx908` LLVM 错误，退出码 134）。
5. 启动：

```bash
python -u scripts/workflow.py ccm \
  --schedule <case_dir>/input/actuation_schedule.csv --sim <cifu0.sim 路径> \
  --out <case_dir>/raw_star --starccm-path <starccm+ 路径> \
  --region 区域 --time-step 1e-4 \
  --scheduler slurm --slurm-job-id "$SLURM_JOB_ID" \
  --np 8 --compute-backend gpu --gpgpu auto:8:nomps --mpi-driver openmpi \
  --gpu-qualification configs/ccm_gpu/qualification.hygon.json --execution-mode run
```

`--np` 必须等于卡数；`:nomps` 不能省略。验收标准：`raw_star/gpu_execution.json` 中
`state=GPU_CONFIRMED`、`actual_backend=gpu`。失败码对照 `docs/STARCCM_GPU.md` 第 7 节。

### 4.5 查看运行状态

```bash
python scripts/workflow.py ccm-status --out <case_dir>/raw_star
watch -n 10 'python scripts/workflow.py ccm-status --out <case_dir>/raw_star --tail 2'
tail -f <case_dir>/raw_star/starccm_flow_control.log
squeue -u $USER
sacct -j <JobID> --format=JobID,State,ExitCode,Elapsed,MaxRSS,AllocTRES%80   # 作业结束后
```

被 GPU 预检拦下的目录没有 `case_manifest.yaml`，此时 `ccm-status` 会报错，直接读 `raw_star/gpu_execution.json`。

### 4.6 整理、质检、作图（本地或集群）

`ccm run` 正常结束会自动整理成 `<case_dir>/processed/timeseries.csv`。需要补整理或检查时：

```bash
python scripts/workflow.py organize --input-dir <case_dir> --output-dir <case_dir>
python scripts/workflow.py check   --case-dir <case_dir> --mode ccm     # 未完成的算例加 --partial
python scripts/workflow.py figures --case-dir <case_dir> --mode ccm     # 默认从 0.3 s 开始画
```

验收：`quality_report.json` 中 `run_success_flag=true`、blocker 为 0；`JET_*`、`cmd_massflow_*`、
`actual_massflow_*` 各 24 列齐全。从集群拷回本地时，至少拷 `input/`、`raw_star/` 中的 CSV/YAML/日志、
`processed/`、`case_manifest.yaml`（`.sim` 体积大，按需拷）。

### 4.7 B53：构建训练/验证数据集（本地）

1. 在 `configs/b53/data_filter.yaml` 中登记 Case（`case_dir` + `split: training|validation`），
   `reference_case` 指向无喷气参考（当前 `runs/cifu0_nojet_11s`，必须同模板、同时间步）。
2. 运行：

```bash
python scripts/build_b53_dataset.py --config configs/b53/data_filter.yaml --output-dir artifacts/B53_training_data
```

产出 `training_dataset.csv`、`validation_dataset.csv` 和质量汇总。口径说明见 `docs/B53_TRAINING_DATA.md`。

### 4.8 B54：识别 ARX ROM（本地）

```bash
python scripts/analysis/run_b54_rom_from_b53.py --config configs/b54/real_rom_b53.yaml
```

输入为 B53 数据集，产出 `artifacts/B04_real_ROM_*`（metrics、model、预测 CSV/PNG、result.md）。
`--require-validation` 会在验证集为空时返回非零。说明见 `docs/B54_REAL_ROM.md`。
`scripts/analysis/run_b54_real_rom.py` 是旧入口，不要使用。

### 4.9 换 `.sim` 模板时

```bash
python scripts/inspect_ccm_template.py --sim <new.sim> --out-dir runs/_inspect/<名字> \
  --starccm <starccm+ 路径> --np 8 --manifest configs/week4/case_manifest_template.yaml          # 先预览
# 确认输出后加 --write 写入模板；需要喷口几何时：CCM_SIM=<new.sim> sbatch scripts/inspect_ccm_geometry.slurm
```

需在能运行 STAR 的计算节点执行。脚本只覆盖值为 `待浩坤确认` 的字段，详见 `docs/RUN_GPU_CCM.md` 第 1 节。
换模板后还要重新确认区域名、GPU 资格文件的 `sim_review.items`。

### 4.10 测试（本地）

```bash
python -m pytest -q
python -m pytest -q tests/test_b53_dataset.py tests/test_b54_from_b53.py   # 只测部分模块
```

## 5. 常见问题

| 现象 | 处理 |
|---|---|
| 找不到 J01 等喷口边界 | `--region` 写错，`cifu0.sim` 用 `区域` |
| `OUTPUT_REUSE_FORBIDDEN` | 换新的输出目录，不要删除旧证据 |
| GPU 退出码 134、`gfx908` | 当前 shell 没有 `source env.sh` |
| `RANK_GPU_MISMATCH` / `GPU_COUNT_NOT_APPROVED` | `--np` 与卡数不一致 / 卡数不在资格文件批准列表中 |
| Slurm `OUT_OF_MEMORY` | 主机内存不足，看 `sacct` 的 MaxRSS 后调整 `--mem` |
| B53 报 `REFERENCE_TIME_MISMATCH` | 参考算例的时间步或时长与数据 Case 不一致 |
| 结果只有每窗口一行 | 代码版本过旧，同步最新代码 |

## 6. 交接记录

多个 agent 或多轮会话接力时，在本次 `<case_dir>/AGENT_HANDOFF.md` 中记录，接手前先读并用
`squeue`/`ccm-status` 确认作业状态，避免重复提交：

```markdown
# 交接记录
- 更新时间 / 负责 agent：
- 用户授权范围：仅给命令 / 代执行；准备 / dry-run / 真实运行
- 当前阶段：准备中 / 运行中 / 已完成 / 失败 / 阻塞
- 机器 / 仓库路径 / git commit：
- 动作配置 / 模板 / 区域 / 时间步：
- Slurm JobID / 节点 / 资源（CPU 进程数或 GPU 卡数）：
- 实际启动命令：
- 输出目录 / 日志 / gpu_execution.json 状态：
- 已确认的事实：
- 未确认项 / 失败码及日志位置：
- 下一步：
```

## 7. 深入阅读

| 需要 | 文档 |
|---|---|
| 所有子命令参数、输出目录结构、字段定义 | `docs/WORKFLOW_COMMANDS.md` |
| GPU 逐步手册、模板与资格文件填写 | `docs/RUN_GPU_CCM.md` |
| GPU 预检项、失败分类 | `docs/STARCCM_GPU.md` |
| CPU 人工运行示例 | `docs/MANUAL_STAR_CCM_RUN_WORKFLOW.md` |
| B52 / B53 / B54 | `docs/B52_ACTION_SCHEDULES.md`、`docs/B53_TRAINING_DATA.md`、`docs/B54_REAL_ROM.md` |

调用链（定位代码用）：`scripts/workflow.py` → `flow_control/cli/run_starccm.py` →
`flow_control/adapters/starccm_runner.py`；GPU 预检在 `starccm/runtime/gpu_preflight.py`、
`gpu_qualification.py`；动作生成在 `flow_control/generator/`。文档与代码冲突时以代码为准。
