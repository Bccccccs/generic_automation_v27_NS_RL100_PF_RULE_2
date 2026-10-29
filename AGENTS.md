# AGENTS.md：接手说明

> 给任何 AI agent（Claude、Codex、Cursor 等）或新同学。读完本文即可接手；细节按需查
> [`docs/COMMANDS.md`](docs/COMMANDS.md)、[`docs/GPU.md`](docs/GPU.md)、[`docs/ROM.md`](docs/ROM.md)。
> 文档与代码冲突时以代码为准，并顺手修正文档。

## 0. 项目一句话

用 STAR-CCM+ 仿真 24 个底部喷口（J01～J24）的喷气对六区升力（`Fz_S1L … Fz_S3R`）的影响，
产出训练数据并识别降阶模型。流水线：

```text
动作 YAML → actions → ccm（STAR 求解，CPU 或 GPU）→ 自动整理 → check/figures
         → B53 数据集（ΔFz = Fz − 同时刻无喷气参考）→ B54 ARX（主模型）/ B55 / B56 → B06 独立验证
```

## 1. 接手前 5 分钟

1. 读第 4 节“当前进度”和第 6 节“已知问题”。
2. 查本地状态：`git status --short`、`git log --oneline -5`、`ls runs artifacts`。
3. 有正在跑的作业时，先看对应 `<case_dir>/AGENT_HANDOFF.md`，再让用户执行 `squeue -u $USER` 或
   `python scripts/workflow.py ccm-status --out <case_dir>/raw_star` 确认，避免重复提交。
4. 跑一遍测试确认环境：`.venv/bin/python -m pytest -q`（2026-10-29 为 631 passed）。

## 2. 工作规则

1. **用简体中文回复**（用户是新加坡人）；代码标识符和命令保持原样。git 提交说明用中文。
2. **先问再给命令。** 路径、配置、卡数等要确认，不能把 `<尖括号>` 占位符原样交给用户。能从仓库读到的自己读，
   只问需要用户决定的事（通常 3～5 个：做哪部分、用哪个 YAML、哪台机器、CPU/GPU 与卡数、agent 只给命令还是代执行）。
3. **agent 无法 SSH 到集群或 GPU 机器**：给出可直接复制的命令，由用户执行并贴回输出。不要在用户命令里加阻塞的 `sleep`。
4. **分清在哪执行。** 本地：准备、dry-run、整理、B53～B56。集群登录节点：同步、建环境、生成动作表、`sbatch`。
   计算节点：真实求解、GPU 命令。不在登录节点跑真实求解。
5. **统一入口。** 用 `python scripts/workflow.py <子命令>`，不要另写启动 STAR 或生成动作表的脚本。
6. **不伪造证据。** 不手改 `gpu_execution.json`；不把资格文件里的 `unknown` 改成 `compatible`（`sim_review.items` 只能由用户本人核实）；
   不往 `REGISTERED_LOG_PATTERNS` 填猜测的日志句式；不删旧日志来复用目录；不为跑通而关闭物理模型。未确认的事写“未确认”。
7. **每次运行一个新目录**：`runs/<工况>_<日期或JobID>`；重试新建目录，并在说明里关联上一次。ROM 产物同理：`artifacts/<模块>_<日期>_trialNN`。
8. **区分“已启动”和“已完成”。** 交付 JobID、日志路径和查看状态的命令；作业还在跑就说“已启动，未完成”。
9. **模型结论要克制。** 目前全部是已知数据的离线评估，`rl_ready=false`、`cfd_replacement_accepted=false`；
   验证集只用于最终计分，不用来选模型或调参。
10. **数据与文档不入库**：`runs/`、`artifacts/`、`archive/`、`*.sim`、`*.zip`、`*.tar.gz` 都被 `.gitignore` 排除。
    文档只保留 `README.md`、本文件和 `docs/` 下三份；阶段性报告写在对应产物目录里（如 `artifacts/<...>/README.md`），
    不要在 `docs/` 新增一次性文档。过时文档移到本地 `archive/docs_<日期>/`。
11. 用户要求暂停时立即停止（训练用 SIGINT），记录停在哪里，不自动恢复。

执行前把方案整理给用户确认：

```text
部分：<例如 GPU 求解>
机器 / 仓库：<主机 + 路径>
动作配置：<YAML；模式、时长、时间步>
模板 / 区域：<.sim 路径；区域名>
资源：<CPU 进程数 或 GPU 卡数；分区；内存>
输出目录：<新目录>
未确认项：<没有就写“无”>
```

## 3. 环境事实

| 项目 | 值 |
|---|---|
| 本地仓库 | `/Users/yanbochao/Documents/研究生/项目/generic_automation_v27_NS_RL100_PF_RULE_2`，`.venv`（Python 3.14，含 torch） |
| git | 单一主分支 `main`（2026-10-29 已把 `GPU` 分支合入）；远端 `origin` |
| 海光集群仓库 | `/public/home/yanbochao/bc/generic_automation_v27_NS_RL100_PF_RULE_2`；conda 环境 `py3.10`（`module load anaconda3/2023.09`） |
| 集群 STAR | 20.02.007-R8 双精度：`/public/home/yanbochao/apprepo/starccmplus/20.02.007-glibc2.28_double/app/20.02.007-R8/STAR-CCM+20.02.007-R8/star/bin/starccm+` |
| 集群分区 | `hx1hdnormal01`；`DefMemPerCPU=3800M`；**每个作业都必须 `--gres=dcu:≥1`**；交互用 `srun -n 1 -c N` |
| GPU 工作站 | 4 × RTX PRO 6000（node01，用户 ybc，STAR 2606/21.04.007，无 Slurm）；另有 NexGPU 2 × RTX PRO 5000 租用容器。差异见 `docs/GPU.md` 第 2 节 |
| 模板 | `cifu0.sim`（仓库根，不入库，sha256 `435a9155…bf04`），**区域名 `区域`**，喷口边界 J01～J24 |
| 求解时间步 | `1e-4` s |
| 无喷气参考 | `runs/cifu0_nojet_11s`（11 s，dt=1e-4，与 B52 同模板、同初始条件） |
| 代表喷口 | J02/J03/J09/J11/J17/J19（六区各一个），流向 S1 → S2 → S3 |

## 4. 当前进度（2026-10-29）

| 部分 | 状态 | 位置 |
|---|---|---|
| 无喷气参考 11 s | 完成（GPU） | `runs/cifu0_nojet_11s` |
| B52 v2 训练 10.1 s / 验证 | 完成（GPU） | `runs/b52_v2/training`、`runs/b52_v2/validation` |
| B53 数据集 | 完成，训练 24 事件放行 23 | `artifacts/B53_training_data/` |
| B54 ARX + 岭回归 | 完成，`REVIEW_REQUIRED`；验证滚动 RMSE S1≈130、S2≈370、S3≈494–551 N，已接近底噪 | `artifacts/B04_real_ROM_*` |
| B55 残差网络 | 完成一次训练，与 B54 几乎无差别 | `artifacts/B55_residual_20261018_trial01` |
| B56 概率集成 | v1 默认训练完成，验证比 B54 差 14–25%；论文改进研究 **被用户暂停**（5/6 候选完成，未选择、未最终重训） | `artifacts/B56_real_20261018_trial01`、`artifacts/B56_paper_study_20261018_trial01` |
| B06 独立验证 | 2 个探针算例已跑并用冻结 B54 测试（低档 0.715 kg/s 外推方向错误）；其余 7 个配置是否已运行未确认 | `configs/b06_validation/`、`runs/b06_import_20261018`、`artifacts/B06_linear_test_20261018` |
| cifu-j0 新模板 | 无喷气参考 0.33 Hz 大幅振荡，不可用，原因未查明 | `runs/j0ramp_nojet_11s_dt5e-4_gpu8_20261014` |

下一步建议（等用户决定）：在 6 个代表喷口上增加独立训练 Case 并丰富激励（多流量档、多脉宽、多口同时喷），
留出冻结测试 Case；或继续 B56 论文研究（新目录，参考暂停目录的 `pause_status.json`）。详见 `docs/ROM.md`。

## 5. 各部分怎么运行

命令都在**仓库根目录**执行。本地用 `.venv/bin/python`；集群先执行：

```bash
module load anaconda3/2023.09
source "$(conda info --base)/etc/profile.d/conda.sh"
conda activate py3.10
cd /public/home/yanbochao/bc/generic_automation_v27_NS_RL100_PF_RULE_2
```

### 5.1 首次使用（集群登录节点，只做一次）

```bash
bash scripts/setup_cluster_env.sh
```

代码用 `git pull` 同步；未入库的文件（`.sim`、资格文件、新数据）用 `scp`/`rsync` 单独传，并用 `sha256sum` 核对。
不要用带 `--delete` 的同步覆盖集群上的 `runs/`。

### 5.2 生成动作表（任意机器）

```bash
python scripts/workflow.py actions --config <动作YAML> --output-dir <case_dir>
```

检查 `<case_dir>/input/validation_report.json` 通过。常用配置：B52 训练 `configs/b52/training_dt1e-4.yaml`、
验证 `configs/b52/validation_dt1e-4.yaml`、无喷气 `configs/actions/temp_no_jet_<时长>_dt1e-4.yaml`、B06 `configs/b06_validation/*.yaml`。
不知道用哪个时，读候选 YAML 后用表格比较模式、喷口、总时长、窗口时长、时间步。

### 5.3 CPU 求解（集群）

```bash
CCM_REGION=区域 sbatch run_python.slurm                                                        # B52 训练集
CCM_REGION=区域 CCM_ACTION_CONFIG=configs/b52/validation_dt1e-4.yaml sbatch run_python.slurm  # 其他配置
```

- 输出 `runs/b52_v2/<配置名>_<JobID>/`，日志 `b52-ccm-<JobID>.out/.err`。
- 脚本 `REGION` 默认 `Region`，与 `cifu0.sim` 不符，**必须传 `CCM_REGION=区域`**。
- 已知问题：脚本的 `--ntasks-per-node=16 --mem=128G` 不满足 2026-10-14 后的 QOS（3.8 GB/核、必须申请 dcu），提交前要先改，见第 6 节。

手动启动（已有分配或工作站）：

```bash
python scripts/workflow.py ccm \
  --schedule <case_dir>/input/actuation_schedule.csv --sim <cifu0.sim 路径> \
  --out <case_dir>/raw_star --starccm-path <starccm+ 路径> \
  --region 区域 --time-step 1e-4 --np 8 \
  --scheduler slurm --slurm-job-id "$SLURM_JOB_ID" \
  --execution-mode run          # 改成 dry-run 只生成宏和计划，适合先在本地检查
```

### 5.4 GPU 求解

**按 [`docs/GPU.md`](docs/GPU.md) 逐步执行。** 要点：资格文件 `configs/ccm_gpu/qualification.<机器>.json`（从
`examples/ccm_gpu/` 复制、删 `test_only`、`sim_review` 由用户本人核实）；海光每个 shell 都要
`source <STAR根>/scripts/env.sh`；`--np` = 卡数、`--gpgpu auto:N:nomps`、`--mpi-driver openmpi`；
STAR 2606 机器上要 `cd` 到 Case 目录再用绝对路径运行。
**当前所有真实 GPU 运行都会判 `GPU_EXECUTION_UNCONFIRMED`**（日志句式未登记），验收看数据完整性和 `quality_report.json`。

### 5.5 查看状态

```bash
python scripts/workflow.py ccm-monitor --out <case_dir>          # 每秒刷新，只读
python scripts/workflow.py ccm-status --out <case_dir>/raw_star --tail 2
tail -f <case_dir>/raw_star/starccm_flow_control.log
sacct -j <JobID> --format=JobID,State,ExitCode,Elapsed,MaxRSS,AllocTRES%80   # 作业结束后
```

### 5.6 整理、质检、作图

```bash
python scripts/workflow.py organize --input-dir <case_dir> --output-dir <case_dir>
python scripts/workflow.py check   --case-dir <case_dir> --mode ccm     # 未完成的算例加 --partial
python scripts/workflow.py figures --case-dir <case_dir> --mode ccm
```

验收：`quality_report.json` 中 `run_success_flag=true`、blocker 为 0；`JET_*`、`cmd_massflow_*`、`actual_massflow_*` 各 24 列齐全。

### 5.7 B53 → B54 → B55 / B56（本地）

```bash
# B53：先在 configs/b53/data_filter.yaml 登记 Case（case_dir + split），reference_case 必须同模板、同时间步
python scripts/build_b53_dataset.py --config configs/b53/data_filter.yaml --output-dir artifacts/B53_training_data
# B54 主模型（旧入口 run_b54_real_rom.py 不要用）
python scripts/analysis/run_b54_rom_from_b53.py --config configs/b54/real_rom_b53.yaml
# B55
python scripts/workflow.py train-residual-rom --config configs/b55/residual_rom.yaml --output-dir artifacts/B55_<日期>_trial01
# B56（需要 torch）
OMP_NUM_THREADS=1 python -u scripts/workflow.py train-probabilistic-rom --config configs/b56/probabilistic_rom.yaml \
  --output-dir artifacts/B56_<日期>_trial01 --require-validation
```

注意：B55/B56 配置引用 `artifacts/B04_real_ROM_model.json` 等本地产物；换了训练 CSV 要先重跑 B54，不能手改哈希。

### 5.8 换 `.sim` 模板

见 `docs/GPU.md` 第 5 节（`scripts/inspect_ccm_template.py` 预览后加 `--write`）。换模板后必须重新确认区域名、
资格文件 `sim_review.items`，并补跑同模板、同时间步的无喷气参考。

## 6. 已知问题与未确认项

| 问题 | 说明 |
|---|---|
| GPU 证据永远“未确认” | `starccm/runtime/gpu_evidence.py` 的 `REGISTERED_LOG_PATTERNS` 为空；登记需真实脱敏日志 + 测试 + 用户同意 |
| `run_python.slurm` 资源过时 | 16 任务 + 128G 违反 3.8 GB/核，且未申请 `dcu`；修改前需与用户确认新取值（未改） |
| STAR 2606 模块遮蔽 | 仓库 `starccm/` 包与 STAR 自带 `starccm` 模块同名；目前靠 `cd` 到 Case 目录规避 |
| GPU 与 CPU 结果等价性 | 未做对比，数值容差未冻结 |
| cifu-j0 模板 | 无喷气参考振荡，原因未查明，不要在其上跑喷气算例 |
| 门户 GUI 会话 | 关闭 GUI 会话会杀掉其中的求解；该 shell 无 `$SLURM_JOB_ID` |
| 旧模块 | `generic_automation/`、`models/`、`rom_identifier.py`、`cases/`、`configs/config*.yaml`、`phase2_*.yaml` 属早期版本，主流程不用，未做清理 |

常见报错速查见 `docs/COMMANDS.md` 第 8 节、`docs/GPU.md` 第 6 节。

## 7. 代码地图

| 需要改什么 | 看哪里 |
|---|---|
| 子命令分发 | `scripts/workflow.py`（新增子命令要同步 `tests/test_ccm_gpu_integration.py` 的命令集合断言） |
| STAR 启动、宏、Slurm | `flow_control/cli/run_starccm.py` → `flow_control/adapters/starccm_runner.py`、`flow_control/slurm.py`、`starccm/control/` |
| GPU 预检 / 资格 / 证据 | `starccm/runtime/gpu_preflight.py`、`gpu_qualification.py`、`gpu_evidence.py`、`gpu_config.py` |
| 动作生成 | `flow_control/generator/`、`flow_control/excitation_patterns/common.py`（`MODES`） |
| 整理与质检 | `flow_control/star_ingest/`（`case_data_loader.py`、`physics_consistency_checker.py`）；映射表 `configs/mappings/` |
| 监控 | `flow_control/cli/ccm_monitor.py`、`ccm_status.py` |
| B53 | `flow_control/b53_dataset/builder.py` |
| ROM | `flow_control/rom/`：`b54_from_b53.py`、`residual_rom.py`、`probabilistic_*.py` |
| 测试 | `tests/`；夹具在 `tests/fixtures/`（含 `error_case/ccm_physics` 质检错误样例） |

## 8. 交接记录

多个 agent 或多轮会话接力时，在本次 `<case_dir>/AGENT_HANDOFF.md`（或 `artifacts/<产物目录>/AGENT_HANDOFF.md`）中记录；
项目级进度变化时更新本文第 4、6 节。

```markdown
# 交接记录
- 更新时间 / 负责 agent：
- 用户授权范围：仅给命令 / 代执行；准备 / dry-run / 真实运行
- 当前阶段：准备中 / 运行中 / 已完成 / 失败 / 阻塞 / 用户暂停
- 机器 / 仓库路径 / git commit：
- 动作配置 / 模板 / 区域 / 时间步：
- Slurm JobID / 节点 / 资源（CPU 进程数或 GPU 卡数）：
- 实际启动命令：
- 输出目录 / 日志 / gpu_execution.json 状态：
- 已确认的事实：
- 未确认项 / 失败码及日志位置：
- 下一步：
```
