# GPU 求解手册

STAR-CCM+ GPU 求解的操作步骤、资格文件、失败码、各机器差异和速度数据。
CPU 求解不需要本文，见 [`AGENTS.md`](../AGENTS.md) 第 6.3 节；英伟达新机器从零开跑见 AGENTS.md 第 5 节。代码位置：
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
- 资格文件：`configs/ccm_gpu/qualification.rtxpro6000.json`（用户 2026-10-13 核验；同内容已收进仓库 `examples/ccm_gpu/qualification.rtxpro6000.json`，作为英伟达机器的底稿，见第 4 节）。
- SSH 别名 `Pro6000`（`~/.ssh/config`，用户 ybc）。用户允许时 agent 可以直接只读查看；短时间连接太多次会被服务器断开。

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

## 4. 资格文件（qualification）

### 4.1 它是什么、什么时候要写

- 资格文件是一份 JSON，声明“这台机器 + 这版 STAR + 这份 `.sim` 允许用 GPU 跑”。`--compute-backend gpu --execution-mode run`
  必须用 `--gpu-qualification <文件>` 指定它；`dry-run` 不读它。
- 每次真实运行都会**完整重新校验**：先查格式（`starccm/runtime/gpu_qualification.py`），再在预检里拿本机实时信息逐项比对
  （`starccm/runtime/gpu_preflight.py`）。任何一项不符就 `BLOCKED`，不会启动 STAR。
- **一台机器一份**，放在那台机器的 `configs/ccm_gpu/qualification.<机器名>.json`。英伟达租用机器每换一台都要写一份新的；
  同一台机器只要 STAR、驱动、`.sim` 不变，就可以一直用。
- JSON 里**不能加注释或自定义字段**（如 `"_comment"`），未知字段会被直接拒绝。说明文字写进 `match_rule`、`evidence`、`source` 这些字符串字段里。

### 4.2 英伟达机器：以 PRO 6000 现用文件为基准

[`examples/ccm_gpu/qualification.rtxpro6000.json`](../examples/ccm_gpu/qualification.rtxpro6000.json) 是 PRO 6000 工作站上
**实际在用、用户 2026-10-13 亲自核验过的文件**，从服务器原样拷来（sha256 `bfc492b0…10ecb3`），格式校验通过：

```json
{
  "schema_version": "ccm_gpu_qualification_v1",
  "reviewed_at": "2026-10-13",
  "reviewer": "Yan Bochao",
  "star_release": "2606",
  "star_build": "21.04.007",
  "precision": "double",
  "documentation": [
    {
      "title": "starccm+ -help (Simcenter STAR-CCM+ 2606 Build 21.04.007)",
      "build": "21.04.007",
      "chapter": "-gpgpu / -require-gpgpu-compatibility / -mpi 选项说明",
      "source": "runs/_evidence/rtxpro6000.txt",
      "sha256": null
    }
  ],
  "approved_platforms": [
    {
      "os": "Linux",
      "os_id": "ubuntu",
      "cpu_arch": "x86_64",
      "gpu_vendor": "nvidia",
      "gpu_model": "RTX PRO 6000 Blackwell Server Edition",
      "match_rule": "os/cpu_arch 精确相等；gpu_model 为 nvidia-smi 设备名子串；4 卡同型号",
      "driver_requirement": ">=590.48.01",
      "evidence": "runs/_evidence/rtxpro6000.txt 与 nvidia-smi 实测（2026-10-13）"
    }
  ],
  "approved_launch": {
    "mode": "native",
    "identity": "/home/ybc/apps/Siemens/21.04.007-R8/STAR-CCM+21.04.007-R8/star/bin/starccm+",
    "identity_sha256": null,
    "scheduler": "manual",
    "mpi": "openmpi",
    "mpi_version": "5.0.9-cda-002",
    "launch_method": "独占工作站本机启动，无 machinefile",
    "approved_ranks_per_gpu": 1,
    "approved_gpu_counts": [1, 2, 3, 4],
    "mps_policy": "disabled",
    "single_node_only": true,
    "gpu_count_source": "manual 独占服务器，按 nvidia-smi 实测 4 卡"
  },
  "sim_review": {
    "items": [
      {"name": "Implicit Unsteady",       "kind": "solver", "status": "compatible", "source": "Yan Bochao 于 2026-10-13 人工核验确认可用；模型清单与老集群 cifu0.sim 资格文件一致；2606 文档章节未记录"},
      {"name": "Segregated Flow",         "kind": "model",  "status": "compatible", "source": "（同上）"},
      {"name": "Segregated Fluid Energy", "kind": "model",  "status": "compatible", "source": "（同上）"},
      {"name": "K-Omega Turbulence",      "kind": "model",  "status": "compatible", "source": "（同上）"},
      {"name": "Gas density model",       "kind": "model",  "status": "compatible", "source": "（同上）"},
      {"name": "Motion",                  "kind": "motion", "status": "compatible", "source": "（同上）"}
    ]
  },
  "required_flags": {
    "gpgpu_flag": "-gpgpu",
    "strict_compatibility_flag": "-require-gpgpu-compatibility"
  }
}
```

（为了便于阅读，这里把 `sim_review` 压成了每项一行，后 5 项的 `source` 缩写成“同上”；原文件中每项写的是完整句子。）

当时那台机器的实测值，对照着看哪个字段来自哪里：

```text
$ starccm+ -version
Simcenter STAR-CCM+ 2606 Build 21.04.007 (linux-x86_64-2.28/clang20.1-r8 Double Precision)
$ uname -srm
Linux 6.8.0-90-generic x86_64
$ grep '^ID=' /etc/os-release
ID=ubuntu
$ nvidia-smi --query-gpu=index,name,driver_version,mig.mode.current --format=csv,noheader
0, NVIDIA RTX PRO 6000 Blackwell Server Edition, 590.48.01, Disabled      （共 4 行）
$ ls <STAR 安装目录>/mpi/openmpi/
5.0.9-cda-002
```

### 4.3 换一台新的英伟达机器：5 步

```bash
# 在新机器的仓库根目录执行；STARCCM_BIN 换成这台机器上 starccm+ 的绝对路径
STARCCM_BIN=/path/to/STAR-CCM+21.04.007-R8/star/bin/starccm+
MACHINE=<机器名，如 rtxpro5000>

# 1. 取证：一次收齐版本、系统、GPU、MPI 信息，留作 evidence（只读，不改任何东西）
mkdir -p runs/_evidence configs/ccm_gpu
bash examples/ccm_gpu/collect_environment_evidence.sh "$STARCCM_BIN" > runs/_evidence/$MACHINE.txt 2>&1
"$STARCCM_BIN" -version
uname -srm; grep '^ID=' /etc/os-release
nvidia-smi --query-gpu=index,name,driver_version,mig.mode.current --format=csv,noheader
ls "$(dirname "$(dirname "$(dirname "$STARCCM_BIN")")")/mpi/openmpi/"

# 2. 以 PRO 6000 文件为底稿
cp examples/ccm_gpu/qualification.rtxpro6000.json configs/ccm_gpu/qualification.$MACHINE.json

# 3. 按下表改字段（编辑器手改）

# 4. 离线校验格式（只查格式，平台/驱动/版本要到第 5 步才实时比对）
PYTHONPATH=. python -c "from pathlib import Path; from starccm.runtime.gpu_qualification import load_gpu_qualification as f; f(Path('configs/ccm_gpu/qualification.$MACHINE.json')); print('格式校验通过')"

# 5. 真实运行（--scheduler 必须与文件里的 scheduler 一致），然后看预检是否通过
python3 -c "import json,sys;r=json.load(open(sys.argv[1]));print('预检:',(r.get('evidence') or [{}])[0].get('state'));print('最终:',r.get('state'),r.get('failure_code'));print(r.get('failure_detail') or '')" <case_dir>/raw_star/gpu_execution.json
#    “预检: PREFLIGHT_PASSED”即资格文件被接受；“最终”一行仍会是 FAILED GPU_EXECUTION_UNCONFIRMED（见第 1 节），
#    被拒时“预检”为 BLOCKED，failure_code / failure_detail 说明是哪个字段不符（对照 4.6）。
#    注意：按 2.2 的变通方法 cd 到 Case 目录运行时，--gpu-qualification 要写绝对路径。
```

第 3 步逐字段改法（“PRO 6000 值”一列即样例里的值）：

| 字段 | PRO 6000 值 | 新机器怎么填 | 换机器要改？ | 运行时怎么比对 |
|---|---|---|---|---|
| `reviewed_at` | `2026-10-13` | 今天的日期 `YYYY-MM-DD` | 改 | 不能晚于今天 |
| `reviewer` | `Yan Bochao` | 核验人姓名 | 一般不改 | 非空 |
| `star_release` | `2606` | `-version` 输出里 `STAR-CCM+` 后面的发布号 | STAR 不同才改 | 只记录 |
| `star_build` | `21.04.007` | `-version` 输出里 `Build` 后面的号 | STAR 不同才改 | 必须出现在 `-version` 输出里，否则 `STAR_BUILD_MISMATCH` |
| `precision` | `double` | `-version` 输出括号里的精度 | STAR 不同才改 | 只记录 |
| `documentation[].build` | `21.04.007` | 与 `star_build` 相同 | 随 `star_build` 改 | 必须等于 `star_build` |
| `documentation[].source` | `runs/_evidence/rtxpro6000.txt` | `runs/_evidence/<机器>.txt` | 改 | 非空 |
| `approved_platforms[].os` / `cpu_arch` | `Linux` / `x86_64` | `uname -srm` 第一项和最后一项 | 通常不改 | 与本机精确相等（不分大小写） |
| `approved_platforms[].os_id` | `ubuntu` | `/etc/os-release` 的 `ID=` | 系统不同才改 | 与本机精确相等；不想比对可删掉这个字段 |
| `approved_platforms[].gpu_vendor` | `nvidia` | `nvidia` | 不改 | 决定用 `nvidia-smi` 查卡 |
| `approved_platforms[].gpu_model` | `RTX PRO 6000 Blackwell Server Edition` | `nvidia-smi` 设备名去掉前缀 `NVIDIA ` | **改** | 必须是每张要用的卡的设备名子串（不分大小写），否则 `PLATFORM_NOT_APPROVED`。注意 Server Edition 与 Workstation Edition 名字不同 |
| `approved_platforms[].driver_requirement` | `>=590.48.01` | `>=` + 本机 `driver_version` | **改** | 只比较数值；本机驱动低于它就拒绝。只能写 `>=版本` 或 `==版本` |
| `approved_platforms[].match_rule` / `evidence` | 见样例 | 照样例改成本机的卡数、取证文件名和日期 | 改 | 非空，`match_rule` 里不能有 `*` |
| `approved_launch.identity` | `/home/ybc/apps/.../starccm+` | `$STARCCM_BIN` 的绝对路径 | **改** | 非空（运行时不比对路径） |
| `approved_launch.identity_sha256` | `null` | `null`，或 `sha256sum "$STARCCM_BIN"` 的结果 | 可选 | 64 位小写十六进制或 `null` |
| `approved_launch.scheduler` | `manual` | 租用机器、工作站用 `manual`；Slurm 集群用 `slurm` | 视机器 | 必须等于命令行的 `--scheduler`，否则 `LAUNCH_NOT_APPROVED` |
| `approved_launch.mpi` / `mpi_version` | `openmpi` / `5.0.9-cda-002` | `openmpi` / `mpi/openmpi/` 下的目录名 | STAR 不同才改 | 非空（不比对） |
| `approved_launch.launch_method` | `独占工作站本机启动，无 machinefile` | 如实描述 | 视机器 | 非空 |
| `approved_launch.approved_gpu_counts` | `[1, 2, 3, 4]` | 从 1 到本机卡数，如 2 卡写 `[1, 2]` | **改** | `--gpgpu auto:N:nomps` 的 N 必须在列表里，否则 `GPU_COUNT_NOT_APPROVED` |
| `approved_launch.gpu_count_source` | `manual 独占服务器，按 nvidia-smi 实测 4 卡` | 改成本机卡数 | 改 | 非空 |
| `approved_launch.approved_ranks_per_gpu` / `mps_policy` / `single_node_only` | `1` / `disabled` / `true` | 保持不变 | 不改 | 只能是这三个值 |
| `sim_review.items` | 6 项，见样例 | 见 4.4 | 一般不改 | 每一项都必须是 `compatible` |
| `required_flags` | `-gpgpu` / `-require-gpgpu-compatibility` | 保持不变 | 不改 | 必须与代码一致 |
| `schema_version` | `ccm_gpu_qualification_v1` | 保持不变 | 不改 | 必须相等 |

例：2 × RTX PRO 5000 租用容器（NexGPU，`/workspace` 路径）相对底稿只需改这些字段（卡名和驱动以该机器的 `nvidia-smi` 实测为准）：

```diff
-  "reviewed_at": "2026-10-13",
+  "reviewed_at": "<核验当天日期>",
-      "source": "runs/_evidence/rtxpro6000.txt",
+      "source": "runs/_evidence/rtxpro5000.txt",
-      "gpu_model": "RTX PRO 6000 Blackwell Server Edition",
+      "gpu_model": "<nvidia-smi 设备名去掉 NVIDIA 前缀>",
-      "match_rule": "os/cpu_arch 精确相等；gpu_model 为 nvidia-smi 设备名子串；4 卡同型号",
+      "match_rule": "os/cpu_arch 精确相等；gpu_model 为 nvidia-smi 设备名子串；2 卡同型号",
-      "driver_requirement": ">=590.48.01",
+      "driver_requirement": ">=595.84",
-      "evidence": "runs/_evidence/rtxpro6000.txt 与 nvidia-smi 实测（2026-10-13）"
+      "evidence": "runs/_evidence/rtxpro5000.txt 与 nvidia-smi 实测（<日期>）"
-    "identity": "/home/ybc/apps/Siemens/21.04.007-R8/STAR-CCM+21.04.007-R8/star/bin/starccm+",
+    "identity": "/workspace/Siemens/21.04.007-R8/STAR-CCM+21.04.007-R8/star/bin/starccm+",
-    "launch_method": "独占工作站本机启动，无 machinefile",
+    "launch_method": "租用容器本机启动，root 运行，已放开 OpenMPI root 限制，无 machinefile",
-    "approved_gpu_counts": [1, 2, 3, 4],
+    "approved_gpu_counts": [1, 2],
-    "gpu_count_source": "manual 独占服务器，按 nvidia-smi 实测 4 卡"
+    "gpu_count_source": "manual 独占容器，按 nvidia-smi 实测 2 卡"
```

`star_build`、`mpi_version`、`sim_review` 不变，因为这台容器装的也是 21.04.007，跑的也是同一份 `cifu0.sim`。

### 4.4 `sim_review`：唯一需要人判断的部分

- 内容是 `cifu0.sim` 实际启用的求解器和物理模型。PRO 6000 文件里的 6 项是用户本人核验过的清单
  （Implicit Unsteady、Segregated Flow、Segregated Fluid Energy、K-Omega Turbulence、Gas density model、Motion）。
  海光集群 20.02 的运行日志里加载的模块与之对应：`SegregatedFlowModel/Solver`、`SegregatedFluidIsothermalModel`、
  `SstKwTurbModel`、`KwTurbSolver`、`MotionModel`。
- **同一份 `.sim` + 同一个 STAR 版本**：直接沿用这 6 项（NexGPU 的文件就是这样做的）。
- **换了 `.sim`，或 STAR 换了大版本**：必须由用户本人在 STAR GUI（Continua → Physics、Solvers）里重新核对，
  对照该版本的 GPU 支持说明确认后再写 `compatible`，`source` 里写清是谁、哪天、依据什么确认的。
  agent 可以先用只读宏导出清单交给用户核对（任何 `.sim` 都能用，不修改、不保存 `.sim`）：

  ```bash
  cd /tmp     # 不要在仓库根目录运行，避免 STAR 2606 的 starccm 模块被遮蔽（见 2.2）
  "$STARCCM_BIN" -batch "$REPO/examples/ccm_gpu/ListSimPhysics.java" -np 1 /abs/path/<模板>.sim > sim_physics.log 2>&1
  grep '^SIM_' sim_physics.log      # SIM_MODEL / SIM_SOLVER 行就是候选的 sim_review.items
  ```

  2026-10-20 在 PRO 6000 上用同样的宏核对另一份测试模板时，得到的是 `Three Dimensional`、`Gas`、`Segregated Flow`、`Ideal Gas`、
  `Segregated Fluid Isothermal`、`Implicit Unsteady`、`K-Omega Turbulence`、`SST (Menter) K-Omega`、`All y+ Wall Treatment` 等条目；
  用户确认后，那次运行写了一份专用资格文件，并在 Case 目录保存了 `user_confirmation.json`（确认时间、原话、模板 sha256）。
  已有运行日志时，也可以用 `grep -E "Loading module|Initialization of" <case>/raw_star/starccm_flow_control.log | sort -u` 粗看一遍。
- agent 不得代填 `compatible`。拿不准就写 `unknown`：运行会被拒绝，这正是这个检查的用途。不能为了通过检查去关闭物理模型。
- `kind` 只能取 `solver` / `model` / `interface` / `motion` / `report` / `derived_part` / `mesh`；`status` 只能取 `compatible` / `incompatible` / `unknown`。
  旧模板里的 `sim_sha256` 已不再校验，可以删掉。

### 4.5 海光集群的文件与英伟达的差别

海光从 `examples/ccm_gpu/qualification.hygon.example.json` 起步（或直接用集群上已有的 `configs/ccm_gpu/qualification.hygon.json`）。
2026-10-16 B06 运行实际使用的值（来自该次 `gpu_execution.json`）：

| 字段 | 海光值 |
|---|---|
| `star_release` / `star_build` | `2502` / `20.02.007`（`-version`：`Simcenter STAR-CCM+ 2502 Build 20.02.007 (linux-x86_64-2.28/clang17.0-r8 Double Precision)`） |
| `os_id` | `sugon` |
| `gpu_vendor` / `gpu_model` | `hygon` / `BW`（`hy-smi --showproductname` 的 Card Series；预检拿它去匹配 `C-3000 IC Design Co., Ltd. BW`） |
| `driver_requirement` | `>=6.3.31`（实测 `6.3.31-V1.5.0a`，只比较数值前缀） |
| `identity` | `/public/home/yanbochao/apprepo/starccmplus/20.02.007-glibc2.28_double/app/20.02.007-R8/STAR-CCM+20.02.007-R8/star/bin/starccm+` |
| `scheduler` | `slurm` |
| `mpi` / `mpi_version` | `openmpi` / `4.1.5-cda-003` |
| `approved_gpu_counts` | `[8]` |
| `gpu_count_source` | `scontrol show job -o 的 Gres/TRES 字段（gres/dcu）` |

### 4.6 被拒时看什么

| 报错 / 失败码 | 原因 |
|---|---|
| `QUALIFICATION_MISSING` | 没传 `--gpu-qualification`，或路径不存在 |
| `QUALIFICATION_INVALID: ...test_only=true` | 从 example 复制后没删 `"test_only": true` |
| `QUALIFICATION_INVALID: ...未知字段` | 加了注释字段或拼错字段名 |
| `QUALIFICATION_INVALID: reviewed_at ... 晚于今天` | 日期写错 |
| `QUALIFICATION_INVALID: driver_requirement 必须写成 '>=版本'` | 写了 `latest`、`590+` 之类 |
| `QUALIFICATION_INVALID: ...属于无限通配` | 平台字段写了 `*`、`any`、`unknown` |
| `QUALIFICATION_INVALID: sim_review 存在未确认或不兼容项` | 有一项不是 `compatible` |
| `STAR_VERSION_UNAVAILABLE` | 预检执行 `starccm+ -version` 失败：路径错了、没加载 STAR 环境或许可证（如没 `. ~/.config/starccm/env.sh`），或者在仓库根目录启动导致 2606 报 `No module named starccm.__main__`（2026-10-20 在 Pro6000 上真实出现过，改为在 Case 目录启动后解决） |
| `STAR_BUILD_MISMATCH` | `star_build` 不在 `-version` 输出里，检查 `--starccm-path` 是否指错版本 |
| `PLATFORM_NOT_APPROVED` | `os` / `cpu_arch` / `os_id` 不符，或 `gpu_model` 不是设备名子串，或驱动低于下限 |
| `LAUNCH_NOT_APPROVED` | 文件里写的 `scheduler` 与命令行的 `--scheduler` 不一致 |
| `GPU_COUNT_NOT_APPROVED` / `RANK_GPU_MISMATCH` | 卡数不在 `approved_gpu_counts` 里 / `--np` 不等于卡数 |
| `MIG_NOT_APPROVED` | `nvidia-smi` 显示 MIG 已启用（只接受 `Disabled`、`[N/A]`、`[Not Supported]`） |
| `GPU_DEVICE_UNAVAILABLE` | `CUDA_VISIBLE_DEVICES` 限制后可见卡数少于请求卡数 |

英伟达 manual 模式下默认用本机全部可见卡里的前 N 张；要指定具体哪几张，在运行前设置 `CUDA_VISIBLE_DEVICES`。
卡上已有其他进程时预检不会拦截，只记录在证据里，所以运行前请先用 `nvidia-smi` 人工确认卡是空闲的。

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
