# GPU 求解手册

STAR-CCM+ GPU 求解的操作步骤、资格文件、失败码、各机器差异和速度数据。
CPU 求解不需要本文，见 [`AGENTS.md`](../AGENTS.md) 第 5.3 节。代码位置：
`starccm/runtime/gpu_preflight.py`（预检）、`gpu_qualification.py`（资格文件）、
`gpu_evidence.py`（日志证据）、`flow_control/adapters/starccm_runner.py`（启动）。

## 1. 先读：GPU 结论目前怎么判

- 默认后端是 CPU。只有传 `--compute-backend gpu` 才会走 GPU 预检和 `-gpgpu` 启动。
- `--execution-mode run` 的 GPU 运行必须带 `--gpu-qualification <文件>`，否则直接拒绝。
- **`starccm/runtime/gpu_evidence.py` 的 `REGISTERED_LOG_PATTERNS` 目前为空**：还没有任何
  STAR build 的真实 GPU 日志句式登记过。所以即使求解完整跑完，`raw_star/gpu_execution.json`
  也会是 `state=FAILED`、`failure_code=GPU_EXECUTION_UNCONFIRMED`、`actual_backend=unknown`。
  本地所有真实 GPU 算例（`runs/cifu0_nojet_11s`、`runs/b52_v2/training` 等）都是这个状态。
- 因此当前的验收口径是：预检通过（证据里出现过 `PREFLIGHT_PASSED`）+ `processed/timeseries.csv`
  行数等于求解步数 + `quality_report.json` 中 `run_success_flag=true`、blocker 为 0。
  `GPU_CONFIRMED` 只有在登记真实日志句式后才可能出现。
- 不允许凭猜测往 `REGISTERED_LOG_PATTERNS` 里填句式。要登记，必须先把某个 build 的真实脱敏日志
  放进 `tests/fixtures/starccm_gpu/real/` 并写测试，再经用户同意。

## 2. 机器一览

| 机器 | 硬件 / STAR | 调度 | 关键差异 |
|---|---|---|---|
| 海光集群（`hx1hdnormal01`） | 8 × 海光 DCU BW（gfx936，64 GB），驱动 6.3.31-V1.5.0a；STAR 20.02.007-R8 双精度 | Slurm | 每个 shell 都要 `source env.sh`；QOS 限制见 2.1 |
| RTX PRO 6000 工作站（node01，用户 ybc） | 4 × RTX PRO 6000 Blackwell，驱动 590.48；STAR 2606（21.04.007）双精度 | 无（`--scheduler manual`） | 仓库 `starccm/` 包会遮蔽 STAR 自带模块，见 2.2 |
| NexGPU 租用容器 | 2 × RTX PRO 5000 Blackwell；STAR 21.04.007 在 `/workspace/Siemens/...` | 无 | root 运行需放开 OpenMPI，见 2.3 |
| A800 租用容器（曾被误称 H800） | 1 × A800-SXM4-80GB，18 CPU 配额 | 无 | 只有速度数据 |

agent 无法 SSH 到上述任何机器：由用户执行命令并把输出贴回来。

### 2.1 海光集群

固定路径：

```bash
PROJECT_DIR=/public/home/yanbochao/bc/generic_automation_v27_NS_RL100_PF_RULE_2
STAR_ROOT=/public/home/yanbochao/apprepo/starccmplus/20.02.007-glibc2.28_double
STARCCM_BIN=$STAR_ROOT/app/20.02.007-R8/STAR-CCM+20.02.007-R8/star/bin/starccm+
SIM_PATH=$PROJECT_DIR/cifu0.sim     # sha256 435a9155…bf04，不入库
```

Slurm 限制（2026-10-14 实测）：

- `DefMemPerCPU=3800M`：每核内存超过 3.8 GB 会被拒，按 CPU 数而不是 `--mem` 定规模
  （例如 128 GB 至少要 34 个 CPU）；
- `QOSMinGRES`：**任何作业**（包括纯 CPU）都必须至少申请 `--gres=dcu:1`；
- 交互用 `srun -n 1 -c N`；写成 `-n N` 会让 srun 起 N 份进程，导致内存不足（OOM）。

用户常用方式：在门户里开 STAR GUI 会话（作业名 `STARCCM_PLUS_GUI_<日期>`，8 DCU、128 CPU、
约 475 GB、3 天上限），在其中用 `nohup` 跑 `workflow.py ccm`。这个 shell 没有 `$SLURM_JOB_ID`，
用 `squeue -u $USER -w <节点>` 查；GUI 进程会触发“已有 STAR 进程”预检警告；**关闭 GUI 会话会杀掉求解**。

### 2.2 RTX PRO 6000 工作站

- `. ~/.config/starccm/env.sh` 设置 `$STARCCM_HOME` 和许可证；conda 环境 `ccm`。
- STAR 2606 的启动器会执行自带 python 的 `-m starccm`，当前目录是仓库根时会被仓库里的 `starccm/`
  包遮蔽（报 `No module named starccm.__main__`）。**变通方法**：`cd "$CASE_DIR"` 后用绝对路径调用
  `$REPO/scripts/workflow.py`，`--manifest-template` 也传绝对路径。
- 资格文件：`configs/ccm_gpu/qualification.rtxpro6000.json`（只在该机器上，`sim_review` 已由用户 2026-10-14 确认）。

### 2.3 NexGPU PRO5000 容器

- 仓库 `/workspace/generic_automation_v27_NS_RL100_PF_RULE_2`；Python 环境 `/venv/ccm`（默认 shell 激活的是 `/venv/main`）。
- 以 root 运行，启动前需要：
  `export OMPI_ALLOW_RUN_AS_ROOT=1 OMPI_ALLOW_RUN_AS_ROOT_CONFIRM=1 PRTE_ALLOW_RUN_AS_ROOT=1 PRTE_ALLOW_RUN_AS_ROOT_CONFIRM=1`
- 每次 SSH 登录会自动接入同一个 tmux 会话 `ssh_tmux`，多个窗口共享输入；`touch ~/.no_auto_tmux` 后重连可获得独立 shell。
- 同样需要 2.2 的 `cd "$CASE_DIR"` 变通方法。资格文件 `configs/ccm_gpu/qualification.rtxpro5000.json`（manual，卡数 [1,2]）。

## 3. 海光集群逐步操作

| 步骤 | 在哪 | 做什么 | 频率 |
|---|---|---|---|
| 0 | 登录节点 | `git pull`、`bash scripts/setup_cluster_env.sh` | 首次 |
| 1 | 计算节点 | `.sim` 信息写入 manifest 模板（换 `.sim` 才做，见第 5 节） | 换模板 |
| 2 | 计算节点 + 用户本人 | 写资格文件（第 4 节） | 换节点型号/驱动/STAR/`.sim` |
| 3 | 登录节点 | `workflow.py actions` 生成动作表 | 每个算例 |
| 4–8 | 计算节点 | 申请分配 → 查卡空闲 → 激活环境 → dry-run → run | 每次 |
| 9 | 任意 | 监控与验收 | 每次 |

```bash
# 3. 生成动作表（每个算例一个新目录）
CASE_DIR=$PROJECT_DIR/runs/<工况>_$(date +%Y%m%d_%H%M)
python scripts/workflow.py actions --config <动作YAML> --output-dir "$CASE_DIR"

# 4. 申请 8 卡（N 与后面的 --np、auto:N:nomps 一致）。
#    按 2.1 的 QOS 规则写成 -n 1 -c <CPU数>；这一写法与 --scheduler slurm 预检的配合
#    尚未实测（未确认）。旧写法 --ntasks=8 --cpus-per-task=1 --mem=128G 会因超出 3.8 GB/核被拒。
#    也可以直接用门户 STAR GUI 会话（见 2.1）。
srun -p hx1hdnormal01 -N 1 -n 1 -c 32 --gres=dcu:8 --pty bash

# 5. 确认卡空闲：应输出 No KFD PIDs currently running!（预检不再拦截占用，必须人工把关）
hy-smi --showpids

# 6. 激活环境；source env.sh 必不可少，漏了会报 gfx908 LLVM 错误并以 134 退出
cd $PROJECT_DIR
module load anaconda3/2023.09
source "$(conda info --base)/etc/profile.d/conda.sh"
conda activate py3.10
source $STAR_ROOT/scripts/env.sh
printenv HIP_COMGR_ARCH ROCBLAS_GPU_ARCHNAME      # 应为 gfx936

# 7. dry-run：gpu_execution.json 为 UNVERIFIED，argv 含 -gpgpu auto:8:nomps -require-gpgpu-compatibility -mpi openmpi
python scripts/workflow.py ccm \
  --schedule "$CASE_DIR/input/actuation_schedule.csv" --sim "$SIM_PATH" --out "$CASE_DIR/dry_run" \
  --starccm-path "$STARCCM_BIN" --region 区域 --time-step 1e-4 \
  --np 8 --compute-backend gpu --gpgpu auto:8:nomps --mpi-driver openmpi --execution-mode dry-run

# 8. 真实运行（长算例放在 tmux/screen/nohup 里）
python -u scripts/workflow.py ccm \
  --schedule "$CASE_DIR/input/actuation_schedule.csv" --sim "$SIM_PATH" --out "$CASE_DIR/raw_star" \
  --starccm-path "$STARCCM_BIN" --region 区域 --time-step 1e-4 \
  --scheduler slurm --slurm-job-id "$SLURM_JOB_ID" \
  --np 8 --compute-backend gpu --gpgpu auto:8:nomps --mpi-driver openmpi \
  --gpu-qualification configs/ccm_gpu/qualification.hygon.json --execution-mode run \
  2>&1 | tee "$CASE_DIR/ccm_gpu_run.log"
```

参数要点：`--np` 必须等于卡数（1 卡 1 rank）；`:nomps` 不能省；卡数必须在资格文件
`approved_gpu_counts` 里；`--out` 必须是新目录（已有结果会报 `OUTPUT_REUSE_FORBIDDEN`，不要删旧证据）。

```bash
# 9. 监控
python scripts/workflow.py ccm-monitor --out "$CASE_DIR"         # 每秒刷新步速、进度、ETA（只读）
python scripts/workflow.py ccm-status --out "$CASE_DIR/raw_star"
watch -n 2 hy-smi                                                # HCU%/VRAM% 只作辅助观察
python3 -c "import json,sys;r=json.load(open(sys.argv[1]));print(r['state'],r.get('failure_code'),r.get('actual_backend'))" "$CASE_DIR/raw_star/gpu_execution.json"
```

被预检拦下（`BLOCKED`）的目录没有 `case_manifest.yaml`，`ccm-status` 会报错，此时直接读 `gpu_execution.json`。

## 4. 资格文件

从 `examples/ccm_gpu/qualification.hygon.example.json`（NVIDIA 用 `qualification.example.json`）复制到
`configs/ccm_gpu/qualification.<机器>.json`，把所有 `BLOCKED-...` 和占位值换成真实值，**删除 `"test_only": true`**。

取证命令（海光）：`hy-smi --showproductname`（gpu_model，实测 `BW`）、`hy-smi --showdriverversion`
（driver_requirement，只写数值前缀如 `>=6.3.31`）、`grep '^ID=' /etc/os-release`、`uname -m`、
`"$STARCCM_BIN" -version`（star_build 须包含 `20.02.007`）、`sha256sum "$STARCCM_BIN"`（可为 null）。
也可以运行 `bash examples/ccm_gpu/collect_environment_evidence.sh "$STARCCM_BIN"` 一次收齐。

| 字段 | 要求 |
|---|---|
| `schema_version` | `ccm_gpu_qualification_v1` |
| `reviewed_at` / `reviewer` | 核验日期（不晚于今天）/ 本人姓名 |
| `star_release` / `star_build` / `precision` | 如 `2502` / `20.02.007` / `double`；运行时 `-version` 输出里找不到 build 会报 `STAR_BUILD_MISMATCH` |
| `documentation[]` | 至少一条官方文档依据，`build` 等于 `star_build`；`sha256` 不知道写 `null` |
| `approved_platforms[]` | `os`、`cpu_arch`、`os_id` 与本机一致；`gpu_vendor`（`hygon`/`nvidia`）；`gpu_model` 子串匹配；`driver_requirement` 只允许 `>=`/`==` + 数值 |
| `approved_launch` | `mode=native`，`identity`=starccm+ 绝对路径，`scheduler`=`slurm`/`manual`，`mpi`/`mpi_version`，`approved_ranks_per_gpu=1`，`approved_gpu_counts`，`mps_policy=disabled`，`single_node_only=true` |
| `sim_review.items[]` | `.sim` 实际启用的 solver/物理模型/interface/report，`kind` ∈ solver/model/interface/motion/report/derived_part/mesh；**任一项不是 `compatible` 就拒绝** |
| `required_flags` | 保持 `-gpgpu` / `-require-gpgpu-compatibility` |

`sim_review.items` 必须由用户本人在 STAR GUI 里核对后填写；agent 不得代填 `compatible`，
不确定写 `unknown`（运行会被拒绝，这是预期行为）。离线检查格式：

```bash
PYTHONPATH=. python -c "from pathlib import Path; from starccm.runtime.gpu_qualification import load_gpu_qualification as f; f(Path('configs/ccm_gpu/qualification.hygon.json')); print('ok')"
```

## 5. 换 `.sim` 模板

`ccm` 默认用 `configs/week4/case_manifest_template.yaml` 预填 `raw_star/case_manifest.yaml`；
其 `star:` 段（版本、sim 哈希、几何/网格指纹、`region_names`）供后续溯源。换模板时：

1. 把要更新的字段改回占位符 `待浩坤确认`（脚本只覆盖占位字段）；
2. 在能跑 STAR 的计算节点预览，再加 `--write` 写入：

```bash
python scripts/inspect_ccm_template.py --sim <new.sim> --out-dir runs/_inspect/<名字> \
  --starccm "$STARCCM_BIN" --np 8 --manifest configs/week4/case_manifest_template.yaml   # 预览
# 确认无误后同一命令加 --write；需要喷口几何时：CCM_SIM=<new.sim> sbatch scripts/inspect_ccm_geometry.slurm
```

模板必须满足：某个区域含 `J01`～`J24` 喷口边界（`cifu0.sim` 的区域名是 **`区域`**）；`JET01`～`JET24`
为底部受力面；瞬态求解；有 `total`、`drag`、`Pitch_Moment`、`Roll_Moment`、`Jet_Reaction_Z` 和六区力 report。
换模板后还要重新确认 `--region`、资格文件的 `sim_review.items`，并**补跑同模板、同时间步的无喷气参考**。

已知坏模板：`cifu-j0-ramp_v2002.sim`（集群 `~/bc/templates/cifu-j0/`，dt 5e-4）的无喷气算例
`runs/j0ramp_nojet_11s_dt5e-4_gpu8_20261014` 六区力以 0.33 Hz、±200–270 kN 同相振荡（约为 cifu0 的 120 倍），
出口回流，**不能作为参考**，在修好前不要在该模板上跑喷气算例。

## 6. 失败码

| 情况 | 结果 |
|---|---|
| 退出 0 但日志里无已登记的 GPU solver 句式 | `FAILED / GPU_EXECUTION_UNCONFIRMED`（当前所有真实运行都落在这里，见第 1 节） |
| 设备初始化成功但 solver 回退 CPU | `FAILED / CPU_FALLBACK_DETECTED` |
| 某张请求的卡缺执行证据 | `FAILED / GPU_DEVICE_EVIDENCE_MISSING` |
| 显存不足 | `FAILED / GPU_OUT_OF_MEMORY`（不自动减卡重试） |
| 物理模型不支持 GPU | `FAILED / GPU_MODEL_UNSUPPORTED` |
| STAR 非零退出 | `FAILED / STAR_NONZERO_EXIT`（保留真实退出码） |
| 启动失败 / Ctrl-C | `FAILED / LAUNCH_FAILED` / `FAILED / INTERRUPTED` |
| 步数不足或结果 `.sim` 缺失 | `FAILED / OUTPUTS_INCOMPLETE` |
| MIG 启用 / 分配卡数对不上 / 可见卡重复 | 预检 `BLOCKED / MIG_NOT_APPROVED` / `ALLOCATION_MISMATCH` / `GPU_DEVICE_UNAVAILABLE` |
| `--np` ≠ 卡数 / 卡数不在批准列表 | `RANK_GPU_MISMATCH` / `GPU_COUNT_NOT_APPROVED` |
| 平台或 STAR 版本不符 | `PLATFORM_NOT_APPROVED` / `STAR_BUILD_MISMATCH` |
| 输出目录已有结果 / 目录有锁 | `OUTPUT_REUSE_FORBIDDEN` / `GPU_LOCK_HELD` |
| dry-run 想覆盖真实结论 | `SIDECAR_OVERWRITE_FORBIDDEN` |
| 目标卡上有其他进程 | 不拦截，只记录 `device_occupancy.busy_overridden` |
| dry-run | `UNVERIFIED`，`actual_backend=unknown` |

优先级：OOM > 模型不支持 > CPU 回退 > 设备证据缺失 > 未确认。

常见现象：`LLVM ERROR: Missing device library for gfx908`、退出码 134 → 当前 shell 没有 `source env.sh`；
`Falling back to Intel MKL` 警告与崩溃无关；找不到 J01 等边界 → `--region` 写错。

## 7. 证据登记摘要

代码注释和测试里引用的 B-xx 编号含义（详细原始记录已存档，不入库）：

| 编号 | 内容 | 状态 |
|---|---|---|
| B-01 | `-gpgpu` / `-require-gpgpu-compatibility` 命令行语法 | 海光 20.02 上已被接受并跑通求解；未做完整官方核验 |
| B-02 | 官方 20.02 手册 GPU 支持矩阵 | 未取得 |
| B-03 | 目标节点设备与拓扑 | 海光已解除（2026-09-18，`hy-smi` 各字段实测；Slurm Gres 名为 `dcu`，只给 `AllocTRES=`） |
| B-04 | Slurm GPU 分配与可见性 | 海光单节点已实测可用 |
| B-05 | `.sim` 模型清单与 GPU 兼容性 | 由用户在资格文件 `sim_review` 中逐项确认 |
| B-06 | Java 宏在 20.02 下编译与导出 | 已在海光真机跑通 |
| B-07 | 真实脱敏 GPU 日志句式 | **未登记** → 所有运行判 `GPU_EXECUTION_UNCONFIRMED` |
| B-08 | GPU/CPU 物理数值容差 | 未冻结，未做 CPU/GPU 等价性对比 |
| B-09 | 站点 sbatch 示例 | 未编写（GPU 作业目前交互启动） |

## 8. 速度与价格（用户实测，dt=1e-4，每步 5 个内迭代）

| 配置 | s/步 | 价格 | 10 万步约耗时 | 10 万步约花费 |
|---|---:|---:|---:|---:|
| 4 × RTX PRO 6000 | 1.2–1.33 | ¥86.13/h | 33–37 h | ¥2,871 |
| 4 × RTX 5090 | 1.33 | ¥31.00/h | 37 h | ¥1,145 |
| 16 × RTX 3080 | 1.42 | ¥80/h | 39 h | ¥3,156 |
| 8 × 海光 BW | 约 1.4–2.4 | 集群 | 39–67 h | — |
| 8 × V100 | 1.66 | ¥26.68/h | 46 h | ¥1,233 |
| 4 × RTX 4090 | 1.8 | ¥26.68/h | 50 h | ¥1,334 |
| 8 × RTX 3080 | 2.17 | ¥40/h | 60 h | ¥2,411 |
| 2 × RTX 5090 / 2 × RTX PRO 6000 | 2.5 | ¥15.36/h / ¥37.35/h | 69 h | ¥1,067 / ¥2,594 |
| 2 × RTX PRO 5000 | 3.0 | ¥20.26/h | 83 h | ¥1,688 |
| 1 × H100 SXM | 3.1 | ¥23.35/h | 86 h | ¥2,010 |
| 4 × V100 | 3.34 | ¥8.84/h（用户实付约 ¥7/h） | 93 h | ¥820 |
| 1 × A800 80GB / 1 × RTX PRO 6000 | 5.0 | ¥5.59/h / ¥8.67/h | 139 h | ¥776 / ¥1,204 |
| 4 × RTX 3080 | 显存不足 | — | — | — |

规律：速度主要受显存带宽限制，不受 FP64 算力限制（PRO 6000 与 A800 单卡相当）。同型号卡数增加时
PRO 6000 近似线性；3080 从 8 卡到 16 卡只有约 76% 效率（可能跨节点，未确认）。生产首选 4 × PRO 6000；
新租卡建议显存 ≥80 GB 且带宽高。是否每次测速都用同一 `.sim` 未确认。
