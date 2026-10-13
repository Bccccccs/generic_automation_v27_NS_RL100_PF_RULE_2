# 启动一次 GPU（海光 DCU）CCM 仿真

本文档是在超算集群上用海光 DCU 跑一次 STAR-CCM+ GPU 仿真的**操作手册**，按顺序
执行即可。首次真机跑通是 2026-09-18（节点 f11r2n13，8 卡 BW/C-3000，驱动
6.3.31-V1.5.0a）；本版按 2026-10-13 的代码与新集群账号（`yanbochao`）更新。

- 调试过程与每个问题的根因：[`docs/gpu/20.02-evidence-register.md`](gpu/20.02-evidence-register.md) 第 4.3 节
- 参数语法、预检项、失败分类表：[`docs/STARCCM_GPU.md`](STARCCM_GPU.md)
- 项目整体运行流程与 AGENT 交接规则：[`AGENTS.md`](../AGENTS.md)

CPU 路径不需要本文档，直接 `sbatch run_python.slurm`（见 [`AGENTS.md`](../AGENTS.md)）。

---

## 总览

| 步骤 | 在哪执行 | 做什么 | 只需做一次？ |
|---|---|---|---|
| 0 | 登录节点 | 同步代码、建 conda 环境 | 是（换账号/换集群再做） |
| 1 | 计算节点 | 把 `.sim` 信息写入 `case_manifest` 模板 | 换 `.sim` 才重做 |
| 2 | 计算节点 + 本人核验 | 写 GPU 资格文件（qualification） | 换节点型号/驱动/STAR/`.sim` 才重做 |
| 3 | 登录节点 | 用动作 YAML 生成动作表 | 每个算例 |
| 4 | — | 申请 DCU 分配 | 每次 |
| 5 | 计算节点 | 确认目标卡空闲 | 每次 |
| 6 | 计算节点 | 激活环境 + `source env.sh` | 每次（每个新 shell） |
| 7 | 计算节点 | dry-run | 参数变化时 |
| 8 | 计算节点 | 真实 run | 每次 |
| 9 | 任意 | 监控与验收 | 每次 |

下文的站点路径变量统一如下（第 6 步会 `export`，前几步可直接替换）：

```bash
PROJECT_DIR=/public/home/yanbochao/bc/generic_automation_v27_NS_RL100_PF_RULE_2
STAR_ROOT=/public/home/yanbochao/apprepo/starccmplus/20.02.007-glibc2.28_double
STARCCM_BIN=$STAR_ROOT/app/20.02.007-R8/STAR-CCM+20.02.007-R8/star/bin/starccm+
SIM_PATH=$PROJECT_DIR/cifu0.sim
```

`PROJECT_DIR`、`STARCCM_BIN`、`SIM_PATH` 与 [`run_python.slurm`](../run_python.slurm) 一致；
`env.sh` 的位置 `$STAR_ROOT/scripts/env.sh` 是按旧账号的目录结构推出来的，
新账号首次使用前先 `ls -l $STAR_ROOT/scripts/env.sh` 确认存在。

---

## 0. 一次性准备（登录节点）

```bash
cd $PROJECT_DIR
git status --short && git log -1 --oneline     # 确认代码版本与本地一致
bash scripts/setup_cluster_env.sh              # 创建 py3.10 环境并安装依赖（已存在则跳过创建）
ls -l cifu0.sim "$STARCCM_BIN"
```

未提交/未跟踪的文件（例如新 YAML、资格文件）不会随 `git pull` 过去，需要单独
`scp`/`rsync`，并用 `sha256sum` 对一下两端是否一致。

---

## 1. 把 `.sim` 信息写入 manifest 模板

### 1.1 这是什么模板、为什么要写

`scripts/workflow.py ccm` 默认带 `--manifest-template configs/week4/case_manifest_template.yaml`，
每次运行开始前会用它预填 `raw_star/case_manifest.yaml`。模板里的 `star:` 段记录
本次用的是哪个 `.sim`、什么 STAR 版本、什么网格，B04/B53/B54 后续溯源依赖这些字段。
字段写成 `待浩坤确认` 表示未填。

当前工作区里这份模板**已按 `cifu0.sim` 填好**（尚未提交）：

```yaml
star:
  version: "20.02.007-R8"
  sim_file: "cifu0.sim"
  sim_file_hash_sha256: "435a91557dccbbb5ab93129ada3056367d0d9a7e9d3bf5717919b96fb8abbf04"
  geometry_version: "geom-ece80e5835f9"
  mesh_version: "sim-435a91557dcc-c29172886-f87251653-v31171999"
  region_names: [区域]
```

继续用 `cifu0.sim` 就**不用重做**，只需在集群上核对哈希没变：

```bash
sha256sum $SIM_PATH    # 应等于上面的 sim_file_hash_sha256
```

> 注意 `region_names` 是 **`区域`**（中文），不是代码默认的 `Region`。后面
> `--region` 必须传 `区域`，否则找不到 J01～J24 喷口边界。

### 1.2 换了 `.sim` 怎么写

用 [`scripts/inspect_ccm_template.py`](../scripts/inspect_ccm_template.py) 自动读取，
**不要手抄**。它只读模板、不求解、不保存 `.sim`，需要在能跑 STAR 的计算节点执行
（申请一个短的 CPU 分配即可）：

```bash
# 1) 先把模板中要更新的字段改回占位符 "待浩坤确认"（脚本只覆盖占位字段，
#    已有值不会被改动；region_names 不一致只告警不写入，需要手动改）
# 2) 在线读取并比较（不加 --write 时只打印，不改文件）
python scripts/inspect_ccm_template.py \
  --sim /path/to/new.sim --out-dir runs/_inspect/new_sim \
  --starccm "$STARCCM_BIN" --np 8 \
  --manifest configs/week4/case_manifest_template.yaml
# 3) 输出确认无误后，加 --write 真正写入
python scripts/inspect_ccm_template.py \
  --sim /path/to/new.sim --out-dir runs/_inspect/new_sim \
  --starccm "$STARCCM_BIN" --np 8 \
  --manifest configs/week4/case_manifest_template.yaml --write
```

在线读取会生成 `runs/_inspect/new_sim/ccm_template_inspection.yaml`（区域、边界、
面积）和 `inspect_ccm_template.log`。把这两个文件拷回本地后，也可以离线重算：

```bash
python scripts/inspect_ccm_template.py --sim /cluster/path/new.sim --sha256 <集群上算的哈希> \
  --inspection runs/_inspect/new_sim/ccm_template_inspection.yaml \
  --log runs/_inspect/new_sim/inspect_ccm_template.log \
  --manifest configs/week4/case_manifest_template.yaml --write
```

写入后检查：`region_names` 中包含 J01～J24 所在的区域；`geometry_version` 是
内容指纹（几何部件 + 边界名/类型/面积），不是 STAR 自带的版本号。
需要喷口几何、六区力 report 组成时，另跑 `sbatch scripts/inspect_ccm_geometry.slurm`。

### 1.3 `.sim` 模板本身要满足什么

runner 不会修改原 `.sim`，而是在同一份模板上逐窗口改喷口质量流量后求解，最后另存为
`raw_star/flow_control_result.sim`。因此模板必须：

- 有一个区域（`--region`）包含名为 `J01`～`J24` 的 24 个喷口边界（大小写不敏感），
  缺任一个默认直接失败（`--non-strict-boundaries` 才会跳过，正式算例不要用）；
- 是瞬态求解，时间步以 `--time-step` 为准（不传时回退到模板里的时间步）；
- 已有 `total`、`drag`、`Pitch_Moment`、`Roll_Moment`、`Jet_Reaction_Z` 等 report；
- 其中的求解器和物理模型都在第 2 步资格文件的 `sim_review.items` 里登记为 `compatible`。

运行开始时宏会先写一份 `raw_star/sim_template_snapshot.yaml`（区域/边界/面积），
可用它事后核对实际用的模板。

---

## 2. 写 GPU 资格文件（qualification）

`--execution-mode run` 的 GPU 运行必须带 `--gpu-qualification <文件>`，否则直接拒绝。
资格文件是**本人核验过**的声明：这台节点、这版 STAR、这份 `.sim` 允许用 GPU 跑。

### 2.1 复制模板

```bash
mkdir -p configs/ccm_gpu
cp examples/ccm_gpu/qualification.hygon.example.json configs/ccm_gpu/qualification.hygon.json
```

模板里所有 `BLOCKED-...` 和“占位”都要替换掉，并删除 `"test_only": true` 这一行
（有它就会被拒绝）。

### 2.2 在目标计算节点上取证

```bash
hy-smi --showproductname        # Card Series → gpu_model（本集群实测 BW）
hy-smi --showdriverversion      # → driver_requirement（只写数值前缀）
grep '^ID=' /etc/os-release     # → os_id
uname -m                        # → cpu_arch
"$STARCCM_BIN" -version         # → star_build（须包含 20.02.007）
sha256sum "$STARCCM_BIN"        # → identity_sha256（可选，填了必须是真实值）
ls $STAR_ROOT/app/20.02.007-R8/STAR-CCM+20.02.007-R8/mpi/openmpi/   # → mpi_version
```

也可以直接跑 [`examples/ccm_gpu/collect_environment_evidence.sh`](../examples/ccm_gpu/collect_environment_evidence.sh)
一次收齐，结果留作证据。

### 2.3 逐字段填写

| 字段 | 填什么 | 代码怎么检查 |
|---|---|---|
| `schema_version` | 保持 `ccm_gpu_qualification_v1` | 必须相等 |
| `test_only` | **删除这一行** | 为 true 即拒绝 |
| `reviewed_at` | 核验日期 `YYYY-MM-DD` | 不能晚于今天 |
| `reviewer` | 你的名字 | 非空 |
| `star_release` / `star_build` / `precision` | `2502` / `20.02.007` / `double` | `star_build` 必须是完整 build，且运行时 `starccm+ -version` 输出里要能找到它，否则 `STAR_BUILD_MISMATCH` |
| `documentation[]` | 依据的官方文档：标题、章节、URL 或本地路径 | 至少一条；每条 `build` 必须等于 `star_build`；`sha256` 不知道就写 `null`，不能编 |
| `approved_platforms[].os` / `cpu_arch` | `Linux` / `uname -m` 的值 | 与本机精确相等 |
| `approved_platforms[].os_id` | `/etc/os-release` 的 `ID=` | 填了就必须与本机一致 |
| `approved_platforms[].gpu_vendor` | `hygon` | 决定用 `hy-smi` 查询 |
| `approved_platforms[].gpu_model` | `hy-smi` 的 Card Series，例如 `BW` | 子串匹配；本次用的卡必须同型号 |
| `approved_platforms[].driver_requirement` | 例如 `>=6.3.31` | 只允许 `>=`/`==` + 数值；`-V1.5.0a` 后缀不写 |
| `approved_platforms[].match_rule` / `evidence` | 文字说明、证据位置 | 非空，不能是 `*`/`any` 之类通配 |
| `approved_launch.mode` | `native` | `native`/`container`/`wrapper` |
| `approved_launch.identity` | `$STARCCM_BIN` 的绝对路径 | 非空（运行时不比对路径，但要写真实值） |
| `approved_launch.identity_sha256` | `sha256sum` 结果或 `null` | 64 位小写十六进制或 null |
| `approved_launch.scheduler` | `slurm` | `manual`/`slurm` |
| `approved_launch.mpi` / `mpi_version` | `openmpi` / 安装目录下实际版本 | 非空 |
| `approved_launch.approved_ranks_per_gpu` | `1` | 首批只允许 1 |
| `approved_launch.approved_gpu_counts` | 打算用的卡数，例如 `[1, 2, 4, 8]` | 本次 `--gpgpu` 的卡数必须在列表里，否则 `GPU_COUNT_NOT_APPROVED` |
| `approved_launch.mps_policy` | `disabled` | 与 `:nomps` 对应 |
| `approved_launch.single_node_only` | `true` | 必须为 true |
| `sim_review.sim_sha256` | **可以删掉** | 已不校验（模板里的全 0 值没有意义） |
| `sim_review.items[]` | `.sim` 里实际用到的 solver / 物理模型 / interface / report，每项 `name`、`kind`、`status`、`source` | 至少一项；`kind` ∈ solver/model/interface/motion/report/derived_part/mesh；任何一项不是 `compatible` 就拒绝 |
| `required_flags` | 保持 `-gpgpu` / `-require-gpgpu-compatibility` | 必须与代码一致 |

`sim_review.items` 是唯一需要真正动脑的部分：打开 `cifu0.sim`（STAR GUI 的
Continua → Physics 和 Solvers 节点），把实际启用的求解器与物理模型逐项列出，对照
20.02 文档的 GPU 支持列表，**确认支持才写 `compatible`**，`source` 写对应章节。
不确定就写 `unknown`——运行会被拒绝，这是预期行为；不能为了通过而关闭物理模型或
硬写 `compatible`。换 `.sim` 后哈希不再被检查，但清单是否仍适用需要重新确认。

### 2.4 离线校验资格文件

```bash
PYTHONPATH=. python -c "
from pathlib import Path
from starccm.runtime.gpu_qualification import load_gpu_qualification
load_gpu_qualification(Path('configs/ccm_gpu/qualification.hygon.json'))
print('资格文件结构校验通过')"
```

这一步只检查字段格式；平台、驱动、卡型号、STAR 版本要到第 8 步实时预检才核对。

---

## 3. 生成动作表（登录节点即可）

选动作 YAML：B52 训练/验证集用 `configs/b52/training_dt1e-4.yaml` /
`configs/b52/validation_dt1e-4.yaml`；无喷气参考用 `configs/actions/temp_no_jet_*_dt1e-4.yaml`。
每个算例用一个新目录：

```bash
CASE_DIR=$PROJECT_DIR/runs/gpu/<工况名>_$(date +%Y%m%d_%H%M)
PYTHONPATH=. python scripts/workflow.py actions \
  --config configs/b52/training_dt1e-4.yaml \
  --output-dir "$CASE_DIR"
ls -l "$CASE_DIR/input/actuation_schedule.csv"
```

检查 `$CASE_DIR/input/validation_report.json` 无错误、`config_summary.yaml` 的窗口数和
时间步符合预期。动作窗口与求解时间步不一定相等，CSV 行数不等于求解步数。

---

## 4. 申请 Slurm 分配

```bash
srun -p hx1hdnormal01 -N 1 --ntasks=8 --cpus-per-task=1 --mem=128G --gres=dcu:8 --pty bash
```

- `--gres=dcu:N` 的 N 与后面 `--np`、`--gpgpu auto:N:nomps` 一致；分区同时认
  `dcu` 和 `gpu` 两个资源名。新账号是否有该分区的 DCU 权限，先 `sinfo -p hx1hdnormal01`
  确认。
- `--mem` 是**主机内存**，与显存无关；128G 沿用 CPU 脚本的取值，按实际峰值调整。
- `--scheduler slurm` 只读取这个已有分配，不会替你申请资源。

进入后记录分配：

```bash
hostname; echo $SLURM_JOB_ID
scontrol show job -o $SLURM_JOB_ID | grep -o 'AllocTRES=[^ ]*'
```

## 5. 确认目标卡空闲

```bash
hy-smi --showpids
```

应为 `No KFD PIDs currently running!`。有进程时：是你自己不再需要的旧作业就
`scancel <JobID>`；是别人的就换节点。预检**不再拦截**占用（只在证据
`device_occupancy.busy_overridden` 里记录），两个真实求解共享同一批卡会显存不足或
结果不可信，这一步必须人工把关。

## 6. 激活环境 + source GPU 运行环境（关键，容易漏）

```bash
cd $PROJECT_DIR
module load anaconda3/2023.09
source "$(conda info --base)/etc/profile.d/conda.sh"
conda activate py3.10

# 必须：不做这一步，STAR-CCM+ 会把海光 DCU 的编译目标识别成 gfx908（实际 gfx936），
# 报 LLVM ERROR: Missing device library for gfx908 后 SIGABRT（退出码 134）
source $STAR_ROOT/scripts/env.sh

printenv HIP_COMGR_ARCH ROCBLAS_GPU_ARCHNAME    # 应输出 gfx936
command -v python && python --version
```

然后设置本次运行的变量（全部在同一个 shell 里，换 shell/新 `srun` 要重设）：

```bash
export CCM_STARCCM_PATH=$STARCCM_BIN
export CCM_SIM=$SIM_PATH
export CCM_REGION=区域
export CCM_DT=1e-4
export CCM_SCHEDULE=$CASE_DIR/input/actuation_schedule.csv
export CCM_GPU_N=8
export CCM_QUAL=$PROJECT_DIR/configs/ccm_gpu/qualification.hygon.json
ls -l "$CCM_SIM" "$CCM_SCHEDULE" "$CCM_QUAL" && test -x "$CCM_STARCCM_PATH"
```

`env.sh` 会 `module load` 一批 DTK/UCX/RCCL 模块并设置架构变量；
`run_python.slurm` 和 `workflow.py` 都不会替你 source。

## 7. dry-run（首次或参数变化时）

```bash
PYTHONPATH=. python scripts/workflow.py ccm \
  --schedule "$CCM_SCHEDULE" --sim "$CCM_SIM" --out "$CASE_DIR/dry_run" \
  --starccm-path "$CCM_STARCCM_PATH" --region "$CCM_REGION" --time-step "$CCM_DT" \
  --np $CCM_GPU_N --compute-backend gpu --gpgpu auto:$CCM_GPU_N:nomps \
  --mpi-driver openmpi \
  --execution-mode dry-run
```

检查 `$CASE_DIR/dry_run/gpu_execution.json`：`state` 为 `UNVERIFIED`，argv 中有
`-gpgpu auto:8:nomps -require-gpgpu-compatibility -mpi openmpi`。dry-run 不碰硬件，
不说明卡可用。

## 8. 真实 run

```bash
PYTHONPATH=. python -u scripts/workflow.py ccm \
  --schedule "$CCM_SCHEDULE" --sim "$CCM_SIM" --out "$CASE_DIR/raw_star" \
  --starccm-path "$CCM_STARCCM_PATH" --region "$CCM_REGION" --time-step "$CCM_DT" \
  --scheduler slurm --slurm-job-id "$SLURM_JOB_ID" \
  --np $CCM_GPU_N --compute-backend gpu --gpgpu auto:$CCM_GPU_N:nomps \
  --mpi-driver openmpi \
  --gpu-qualification "$CCM_QUAL" --execution-mode run \
  2>&1 | tee "$CASE_DIR/ccm_gpu_run.log"
```

长算例建议在 `tmux`/`screen` 里跑，避免交互会话断开导致求解中止。

参数要点：

- `--np` 必须等于 `auto:N:nomps` 的 N（1 卡 1 rank）；N 必须在资格文件
  `approved_gpu_counts` 里。
- `:nomps` 不能去掉；不加 `--gpu-qualification` 的 run 会被拒绝。
- `--region 区域` 和 `--time-step 1e-4` 显式传入，不依赖默认值。
- `--out` 用新目录。已有 `timeseries.csv`、STAR 日志或真实 GPU 结论的目录会报
  `OUTPUT_REUSE_FORBIDDEN`；重试时新建 `CASE_DIR`，不要 `rm -rf` 旧证据。
- 运行开始时会按第 1 步的 manifest 模板预填 `raw_star/case_manifest.yaml`。

## 9. 监控与验收

另开终端（同一节点）观察 DCU：

```bash
watch -n 2 hy-smi
```

`HCU%` 跳动、`VRAM%` 上升说明在用卡，但这只是辅助观察，不能代替证据。

查看状态：

```bash
PYTHONPATH=. python scripts/workflow.py ccm-status --out "$CASE_DIR/raw_star"
tail -f "$CASE_DIR/raw_star/starccm_flow_control.log"
```

读 GPU 证据：

```bash
python3 -c "
import json, sys
r = json.load(open(sys.argv[1]))
print('state:', r['state']); print('failure_code:', r.get('failure_code'))
print('actual_backend:', r.get('actual_backend'))" "$CASE_DIR/raw_star/gpu_execution.json"
```

通过条件：`state=GPU_CONFIRMED`、`actual_backend=gpu`，且 `raw_star/` 下有完整
`timeseries.csv` 和 `flow_control_result.sim`。被预检拦下（`BLOCKED`）的目录没有
`case_manifest.yaml`，`ccm-status` 会报 `FileNotFoundError`，此时直接读上面的 JSON。
失败码对照 [`docs/STARCCM_GPU.md`](STARCCM_GPU.md) 第 7 节；作业结束后用
`sacct -j $SLURM_JOB_ID --format=JobID,State,ExitCode,Elapsed,MaxRSS,AllocTRES%80`
记录资源与退出状态。

`GPU_CONFIRMED` 只确认“用 GPU 跑完且结果完整”；与 CPU 结果是否等价、是否更快，
需要另外对比。

---

## 常见问题速查

| 现象 | 原因 / 处理 |
|---|---|
| `LLVM ERROR: Missing device library for gfx908`，退出码 134 | 当前 shell 没有 `source env.sh`（第 6 步） |
| `Could not determine CPU architecture ... Falling back to Intel MKL` 后崩溃 | 先查是否漏了 `env.sh`（真正根因）；`--mpi-driver openmpi` 也保留，避免选中 Intel MPI |
| 资格文件 `test_only=true` 被拒 | 删掉该行 |
| `sim_review 存在未确认或不兼容项` | 有 item 不是 `compatible`；核实后再改，不能硬写 |
| `GPU_COUNT_NOT_APPROVED` | 卡数不在 `approved_gpu_counts` |
| `RANK_GPU_MISMATCH` | `--np` ≠ `--gpgpu` 卡数 |
| `STAR_BUILD_MISMATCH` | `star_build` 与 `starccm+ -version` 不符，检查 `STARCCM_BIN` |
| `PLATFORM_NOT_APPROVED` | `os`/`os_id`/`cpu_arch` 与节点不符，回第 2.2 步取证 |
| `ALLOCATION_MISMATCH` | 不在分配节点上，或分配卡数与可见卡对不上 |
| `OUTPUT_REUSE_FORBIDDEN` | `--out` 换新目录 |
| `GPU_LOCK_HELD` | 目录里有 `gpu_execution.lock`；先确认持锁进程已退出 |
| 找不到 J01 等喷口边界 | `--region` 写错；`cifu0.sim` 的区域名是 `区域` |

以下问题代码已修好，同步最新代码即可：Slurm Gres 资源名为 `dcu`、`scontrol` 只给
`AllocTRES=`；海光驱动串带 `-V1.5.0a` 后缀；预检不再校验 `.sim` 哈希；预检不再因
卡被占用而拦截。
