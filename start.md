# AGENT 协助用户完成一次 STAR-CCM+ 仿真启动

本文档写给 AGENT：你的任务是通过提问、检查和命令指导，协助用户完成一次
STAR-CCM+ 仿真启动（CPU 或 GPU/海光 DCU），并让用户知道是否真正启动、如何查看状态。
用户要求完整运行与验收时，再继续跟进到结束。
执行时必须逐阶段检查输入、产物和通过条件，不能只复制命令。参数用 `<尖括号>` 标出，
必须先替换为已确认的值；不要把占位符直接交给 shell。本文命令在仓库根目录执行，
Slurm/海光命令在目标 Linux 计算节点执行，本地电脑只做准备和静态检查。

- 只跑 **CPU**：做完步骤 0-2，然后跳到步骤 6a。
- 要跑 **GPU（海光 DCU）**：步骤 0-5 全做，然后步骤 6b。GPU 路径每一步都不能省，
  尤其是步骤 5 的 `source env.sh`；已记录的海光环境缺失会触发 SIGABRT。

更详细的 GPU 踩坑记录、失败分类表见 [`RUN_GPU_CCM.md`](RUN_GPU_CCM.md)、
[`docs/gpu/20.02-evidence-register.md`](docs/gpu/20.02-evidence-register.md)、
[`docs/STARCCM_GPU.md`](docs/STARCCM_GPU.md)；本文件规定 AGENT 从接手任务到交付证据的执行顺序。


## 先与用户对齐：不要直接套用示例命令

**AGENT 应主动询问本次输入和运行方式，再生成属于这一次任务的命令。**
本文中的配置、路径、区域、时长和卡数都是待确认项；示例不代表用户选择。
先整理用户已经提供的信息，不重复问已明确的问题。能从仓库读取的信息先读取，
需要用户决定的选择再询问；不要让用户填写一长串本来可以自动查到的技术字段。

### 第一轮：确认工况、路径与协助方式

可以直接向用户这样提问（删去已知项，通常控制在 3～5 个问题）：

> 我先确认这次运行的输入和环境，再给你对应的启动命令：
>
> 1. 这次使用哪个动作配置 YAML？请给仓库相对路径或绝对路径。
>    如果还没选好，请告诉我是否喷气、希望跑多久、时间步要求，我先帮你找现有配置。
> 2. 使用哪个 `.sim` 模板？请给路径，并说明这是本机路径还是集群上的路径。
> 3. 准备在哪台机器运行，项目在那里的目录是什么？这次使用 CPU 还是 GPU/海光 DCU？
> 4. 结果希望保存在哪个目录？没有偏好时，我会提出一个新的 case 目录供你确认。
> 5. 希望我给出命令、由你在终端执行，还是在有访问权限时由我代为执行？
>    本次只需检查/准备，还是要实际启动仿真？

“告诉我怎么启动”不自动等于授权 AGENT 远程提交；用户已经明确要求代为启动时，
无需再次确认相同授权。用户只要求启动时，交付运行中的真实 JobID/进程和状态查看方法，
即可说明“已启动”；不能提前称为“已完成”。

### 用户不知道路径或配置时，AGENT 怎么帮

- **不知道用哪个 YAML**：列出 `configs/actions/` 和相关场景目录的候选，读取文件后用表格
  比较模式、喷口数、总窗口数、窗口时长、求解时间步及总时长，让用户选择。
  不只列文件名，也不把 `temp_` 文件直接当成正式工况。
- **没有合适配置**：先确认所需物理参数，再提出新文件路径和内容；用户已授权创建时
  才创建，保留原模板，不为方便启动擅自改变物理工况。
- **不知道 `.sim` 在哪里**：在用户指定的项目/存放目录查找候选，或提供限定目录的
  查找命令让用户执行。说明本机找到的文件不一定已同步到集群，不默认选第一个。
- **不知道区域名**：说明需要模板内包含喷口边界的区域名称。让用户从 STAR 界面核实，
  或使用已有可用的模板检查方式取证；不能把默认 `Region` 当成确认结果。
- **不知道 STAR 路径或环境**：先检查既有提交脚本和站点说明，给出候选供确认；
  能访问目标终端时现场检查。不要直接套用其他机器的安装路径。
- **没有远程访问权限**：给用户一小组可复制命令，每组说明在哪个终端执行、预期看到什么，
  请用户返回关键输出；读完结果再给下一组，不假装已在目标节点执行。

### 第二轮：根据 CPU/GPU 分支补齐信息

不要在第一轮就把所有集群细节都交给用户。先选定分支，再问缺失项：

| 条件 | 向用户确认的问题 | AGENT 可先做的检查 |
|---|---|---|
| 远端运行 | 代码和输入是否已同步？目标仓库路径是什么？ | 比较可访问的版本、文件和摘要 |
| CPU | 准备用多少进程？本地运行还是 Slurm？ | 读取既有脚本的资源设置 |
| Slurm | 使用哪个分区、多少主机内存？是否已有作业分配？ | 查询实际分配，避免重复申请 |
| GPU | 使用几张卡？资格文件路径在哪里？ | 检查资格字段和批准卡数，不能代填兼容结论 |
| 海光 DCU | 配套 `env.sh` 和 Python 环境在哪里？ | 从站点脚本查候选并核实路径 |
| 模板设置不明 | 区域名、初始物理时间是否已确认？ | 检查可用模板信息和运行要求 |
| 时间参数有冲突 | 按 YAML 的时间步还是另有明确要求？ | 展示差异及其对步数/时长的影响 |

参数未明确时可继续查配置和准备命令草案，但不要启动依赖这些参数的真实求解。

### 执行前向用户展示本次方案

将已确认值整理成一段简短清单，并给出**已替换占位符**的命令：

```text
运行位置：<主机 + 仓库目录>
动作配置：<YAML 路径；模式、喷口数、时长>
仿真模板：<目标机器上的 .sim 路径；区域名>
时间设置：<窗口长度；求解时间步；初始时间及目标时间范围>
求解资源：<CPU/GPU；进程/卡数；分区；主机内存>
运行环境：<Python；STAR；GPU 环境脚本/资格文件（如适用）>
本次输出：<新 case 目录>
执行方式：<用户执行 / AGENT 代执行；准备 / dry-run / 真实启动>
尚未确认：<没有则写无>
```

仍有关键选择时请用户补齐；已有明确授权且参数齐全时，直接按后续步骤推进。
每一步告诉用户“这一步做什么、成功依据是什么、下一步是什么”。若由用户执行，
请其返回退出状态和相关日志片段，不索要许可证密钥或无关环境变量。

## AGENT 先读：任务边界与证据规则

1. 先读用户本次要求和适用的 `AGENTS.md`，再读本文。只要求准备输入或 dry-run 时，
   到对应阶段即停止；只要求协助启动时，确认作业已启动并交付状态查看方法；
   要求完整求解时再跟进最终验收。已经授权的动作不重复询问。
2. 先执行 `git status --short`、`git rev-parse HEAD`，记录代码版本和未提交文件。
   不回退、清理或提交其他 AGENT 的改动。需要提交、推送时，相关说明必须使用中文。
3. 统一入口是 `scripts/workflow.py`。不要另写一套动作生成/STAR 启动逻辑。
   当前调用链是 `actions → flow_control/generator/schedule_generator.py` 和
   `ccm → flow_control/cli/run_starccm.py → flow_control/adapters/starccm_runner.py`。
4. 当前代码事实优先于旧文档。发生矛盾时记录文件位置和差异；不得通过删除预检、
   伪造资格字段、改证据状态、减少物理模型等方式让运行显示成功。
5. 记录真实命令、退出码、节点、作业 ID、文件路径和摘要；未知项写“未确认”。
   不编造设备 UUID、哈希、网格版本、求解完成时间或真机验收结果。
6. 多个 AGENT **通过文档交流**：确定本次算例目录后，在其中维护 `AGENT_HANDOFF.md`，
   写明负责人、正在执行的阶段、作业 ID、修改范围、证据位置和下一步。
   启动前先读交接记录并检查作业，避免重复提交。同一算例同一时刻只由一个 AGENT 启动。
   交接文件是协作约定，不替代 Slurm 分配或 GPU 运行锁。

### 启动前填写参数表

先从用户要求、现有配置和目标机器取证；只有影响执行且无法确定的项目才需要询问。

| 项目 | 必须确认的内容 | 缺失时的处理 |
|---|---|---|
| 目标阶段 | 仅输入 / dry-run / 真实 run；CPU 或 GPU | 不擅自启动昂贵求解 |
| 目标机器 | 主机、仓库绝对路径、代码版本及未提交补丁 | 先核对同步状态 |
| Python | 解释器绝对路径或环境名、依赖可导入 | 不复制本机虚拟环境到集群 |
| 工况 | YAML 路径、模式、喷口数、总时长、窗口长度、求解时间步 | 不靠文件名推断参数 |
| 模板 | `.sim` 绝对路径、区域名、边界名、初始物理时间 | 未确认模板适配性时不启动 |
| 资源 | 分区、节点数、主机 RAM、CPU ranks、GPU 卡数 | 不把显存当主机 RAM |
| STAR | 可执行文件、版本、精度、许可证环境 | 缺许可证时记录错误，不泄露密钥 |
| GPU 专项 | 环境脚本、资格文件、卡型号、分配和可见卡映射 | 不满足则停在准备阶段 |
| 产物 | 本次独立 case 目录、日志目录、重试编号 | 不覆盖既有真实执行证据 |

建议每次尝试使用 `runs/<工况>_<时间戳或JobID>_<尝试编号>`。重试创建新目录，
在交接文档关联上一次失败目录；不要执行 `rm -rf` 清空旧结果来绕过复用保护。

### 阶段通过条件

| 阶段 | 通过证据 | 可以得出的结论 |
|---|---|---|
| 输入生成 | CSV、配置摘要、验证报告和独立检查一致 | 输入可供后续检查 |
| dry-run | 宏、runtime plan、预期命令参数 | 参数及生成路径已检查 |
| GPU 预检 | 本次实时预检通过 | 启动条件通过，不代表求解完成 |
| 真实运行 | 最终日志、退出码、完整时间序列、需要的结果 sim | 本次求解完成 |
| GPU 验收 | 以上证据 + `GPU_CONFIRMED` / `actual_backend=gpu` | GPU 执行及完整性得到确认 |

物理正确性、CPU/GPU 等价性、性能提升均需另外验证，不能由退出码或 GPU 状态推断。

---

## 0. 前提

- 项目代码已同步到目标机器。先检查两端版本及工作区状态，再按现有同步方式传输所需文件；
  不盲目 `git pull` 或用带删除选项的同步覆盖远端工作。未提交/未跟踪的 YAML 和补丁
  不会随 `git pull` 传过去，需单独同步并核对摘要。
- 有一份目标节点可读的 `.sim` 模板。建议使用绝对路径，例如 `<repo_root>/<name>.sim`；
  路径可访问不等于模板兼容，还须确认区域和喷口边界以及 GPU 模型支持。
- 只跑 GPU 时，额外需要一份人工核验过的资格文件（`--gpu-qualification` 参数），
  从 [`examples/ccm_gpu/qualification.hygon.example.json`](examples/ccm_gpu/qualification.hygon.example.json)
  改出来，关键字段必须是目标节点的真实实测值：
  - `gpu_model`：`hy-smi --showproductname` 的 Card Series
  - `driver_requirement`：只写数值点分前缀，例如 `>=6.3.31`
  - `os_id`：`/etc/os-release` 的 `ID=`
  - `identity` / `identity_sha256`：STAR-CCM+ 可执行文件路径和它的 sha256
  - `sim_review.items`：这份 `.sim` 里实际用到的求解器/物理模型清单，逐项本人核实过
    才能填 `compatible`；只要清单不为空且没有 `incompatible`/`unknown` 项就能过检查，
    **不再校验 `.sim` 文件本身的哈希**——换 `.sim` 文件不需要重新算哈希，但清单内容
    是否仍然适用于新文件，需要你自己确认
  - 删掉模板里的 `"test_only": true`
  - `approved_gpu_counts` 要包含打算用的卡数

## 1. 用模板生成本次仿真的输入（动作表）

动作表模板在 [`configs/actions/`](configs/actions/) 下（也有 `configs/b52/` 等场景专用
目录），选一个符合本次工况的 YAML（喷气/无喷气、时长、时间步长），例如
`configs/actions/temp_no_jet_10s_dt1e-4.yaml`。

统一用 `scripts/workflow.py actions` 生成，输出固定写到
`<output-dir>/input/actuation_schedule.csv`：

```bash
PYTHONPATH=. python scripts/workflow.py actions \
  --config <configs/actions/xxx.yaml> \
  --output-dir <case_dir>
```

- `--config` 和 `--output-dir` 都是必填，缺一个会直接报错退出（`exit code 2`），
  不会用配置文件里的 `output.run_dir` 兜底。
- `<case_dir>` 建议是 `runs/<有意义的名字>`，例如 `runs/gpu_10s_run_v2`。
- 部分模式使用 `random_seed`。复现时保留 YAML、合并后的 `config_summary.yaml`、
  随机种子和代码版本；不能笼统认为所有模式都没有随机性。可同步整个 `input/`，
  也可在目标机器重新生成，但必须核对 CSV 哈希，不能未经比较就声称两者等价。
- 代码允许覆盖现有 `input/`，但 AGENT 只应在尚未用于真实运行的准备目录重生成。
  输入一旦关联真实作业，应冻结保存；修改工况时创建新 case，避免输入与结果失去对应。

验证一下：

```bash
ls -l <case_dir>/input/actuation_schedule.csv
wc -l <case_dir>/input/actuation_schedule.csv   # 普通一窗口一行配置：total_windows + 1（表头）
```

### 1.1 输入验收（生成命令返回成功仍须执行）

- 阅读 `input/config_summary.yaml` 和 `input/validation_report.json`，确认无验证错误。
- 核对 CSV 列、喷口数、首末时间、时间连续性、激活标志和质量流量。无喷气工况应全零。
- 等长窗口的总时长为 `total_windows × window_duration`；例如当前
  `temp_no_jet_10s_dt1e-4.yaml` 是 100000 个窗口、每窗 1e-4 s，即 10 s。
- 动作窗口与求解时间步是两个参数；窗口长于求解步长时，一个窗口对应多个求解步。
  **CSV 行数不能直接当作所有工况的预期求解步数。**
- 保存整个 `input/`，不要只传 CSV 而丢掉时间步来源和验证报告。

可使用项目已有校验器（按本次喷口数修改 `n_jets`）：

```bash
export CCM_SCHEDULE="<case_dir>/input/actuation_schedule.csv"
PYTHONPATH=. python - <<'PYCODE'
import os
from flow_control.generator.schedule_validator import validate_actuation_schedule_csv
errors = validate_actuation_schedule_csv(os.environ["CCM_SCHEDULE"], n_jets=24)
if errors:
    raise SystemExit("\n".join(errors))
print("动作表格式与基本约束检查通过；仍须核对本次工况参数")
PYCODE
```

## 2. 确认仿真可执行文件和 `.sim` 模板路径

```bash
export CCM_SIM=<repo_root>/<name>.sim          # 或任意可访问路径（CPU 路径不限根目录）
export CCM_STARCCM_PATH=<STAR-CCM+ 可执行文件绝对路径>
export CCM_SCHEDULE="<case_dir>/input/actuation_schedule.csv"
export CCM_REGION="<实际区域名>"
export CCM_DT="<已确认的求解时间步>"
ls -l "$CCM_SIM" "$CCM_STARCCM_PATH" "$CCM_SCHEDULE"
test -r "$CCM_SIM" && test -r "$CCM_SCHEDULE" && test -x "$CCM_STARCCM_PATH"
```

`--time-step` 优先于输入目录配置摘要，再回退到模板设置。本手册显式传入
`CCM_DT`，避免因移动输入文件或模板设置不同而改变实际时间步。`Region` 只是默认值，
应核对 `.sim` 中包含 J01～J24 的真实区域。环境变量只对当前 shell 及其子进程有效；
进入新的 `srun` 会话后重新确认路径、解释器和这些变量。

---

以下是 GPU（海光 DCU）路径专属步骤，CPU 路径直接跳到步骤 6a。

## 3. 申请 Slurm 分配

```bash
srun -p <分区名> -N 1 --ntasks=<rank数> --cpus-per-task=1 \
  --mem=<已确认的主机内存额度> --gres=dcu:<卡数> --pty bash
hostname
echo "$SLURM_JOB_ID"
scontrol show job -o "$SLURM_JOB_ID"
```

记录完整分配输出，确认作业 RUNNING、单节点、当前主机属于分配且资源足够。
`--scheduler slurm` **只读取已有分配，不会替你调用 sbatch 或申请节点**。
已经有适用分配时复用它，不额外提交一份相同作业。主机内存额度须按工况确认，
不能因申请了多张 DCU 就认为主机内存足够。

## 4. 确认目标卡空闲

```bash
hy-smi --showpids
```

`No KFD PIDs currently running!` 表示查询范围内没有占用；有进程时，必须核对
它是否占用本次分配的卡。预检按目标卡检查，不要求节点上所有卡都空闲。
目标卡被占用就换分配；只有确认是自己且不再需要的旧作业，才能释放，
**不要**跳过这一步硬跑——两个真实 GPU 求解共享同一批卡会显存不够崩溃或者两边结果
都不可信。预检**不再拦截**目标卡上已有的进程（已改为只在证据 `device_occupancy` 里记录，
`busy_overridden=true`），是否抢占由你自己负责确认。

## 5. 激活环境 + source GPU 运行环境（关键，容易漏）

```bash
cd <repo_root>
source <miniconda路径>/etc/profile.d/conda.sh
conda activate <环境名，例如 py3.10>

# 必须：不做这一步，STAR-CCM+ 会把海光 DCU 的 GPU 编译目标架构识别错
# （内部默认成 AMD gfx908，实际是 gfx936），报 LLVM ERROR 后 SIGABRT（退出码 134）
source <starccmplus 安装路径>/scripts/env.sh
```

已记录的海光安装中，`env.sh` 会设置 `HIP_COMGR_ARCH=gfx936` / `ROCBLAS_GPU_ARCHNAME=gfx936` 等并
`module load` 一批 DTK/UCX/RCCL 模块。`run_python.slurm`/`workflow.py` 都不会自动
帮你 source 它，必须手动补。以上架构值来自特定海光环境，不可照搬给其他型号。
在同一个启动 shell 中用 `python --version`、`command -v python`、
`printenv HIP_COMGR_ARCH ROCBLAS_GPU_ARCHNAME` 检查环境并记录结果。

---

## 6a. CPU：直接提交

```bash
PYTHONPATH=. python scripts/workflow.py ccm \
  --schedule "$CCM_SCHEDULE" \
  --sim "$CCM_SIM" \
  --out <case_dir>/raw_star \
  --starccm-path "$CCM_STARCCM_PATH" \
  --region "$CCM_REGION" --time-step "$CCM_DT" \
  --np <进程数> \
  --execution-mode run
```

在 Slurm 集群上先取得 CPU 所需分配，再在上述命令增加
`--scheduler slurm --slurm-job-id "$SLURM_JOB_ID"`；不要在登录节点启动真实求解。
本地工作站有足够资源且已授权时可使用上述 manual 命令。

也可参考 [`run_python.slurm`](run_python.slurm)，但提交前必须读完整文件：当前它固定
按 `CCM_ACTION_CONFIG`（默认 B52 训练集）生成输入、使用 `--np 8` 和站点绝对路径，申请 128G 主机内存，
**求解命令为 CPU**。新集群/新账号首次使用前，先运行 `bash scripts/setup_cluster_env.sh`
创建 `py3.10` 环境，并核对脚本中的 `PROJECT_DIR`、`SIM_PATH`、`STARCCM_BIN`。只有这些设置符合本次任务才直接 `sbatch run_python.slurm`；
它不是自动适配任意工况的通用提交器。批处理日志路径由脚本的 `#SBATCH` 指令决定。

## 6b. GPU：先 dry-run，再真实 run

首次运行或输入/启动参数变化后先做 **dry-run**。它不启动真实 STAR 求解，
不证明许可证、硬件、模型支持或实际环境通过预检：

```bash
PYTHONPATH=. python scripts/workflow.py ccm \
  --schedule "$CCM_SCHEDULE" \
  --sim "$CCM_SIM" \
  --out <case_dir>/dry_run \
  --starccm-path "$CCM_STARCCM_PATH" \
  --region "$CCM_REGION" --time-step "$CCM_DT" \
  --np <N> --compute-backend gpu --gpgpu auto:<N>:nomps \
  --execution-mode dry-run
```

检查 `<case_dir>/dry_run/gpu_execution.json` 的 `state` 是 `UNVERIFIED`，argv 里有
`-gpgpu auto:<N>:nomps -require-gpgpu-compatibility`。另查 runtime plan 中模板、输入、
时间步、输出路径与本次参数表一致。dry-run 通过后再核对真实 run 的 Slurm 和资格参数。

**真实 run**：

```bash
PYTHONPATH=. python scripts/workflow.py ccm \
  --schedule "$CCM_SCHEDULE" \
  --sim "$CCM_SIM" \
  --out <case_dir>/raw_star \
  --starccm-path "$CCM_STARCCM_PATH" \
  --region "$CCM_REGION" --time-step "$CCM_DT" \
  --scheduler slurm --slurm-job-id "$SLURM_JOB_ID" \
  --np <N> --compute-backend gpu --gpgpu auto:<N>:nomps \
  --mpi-driver openmpi \
  --gpu-qualification <资格文件.json> --execution-mode run
```

参数说明：

- `--np` 必须等于 `--gpgpu auto:N:nomps` 里的 `N`（首批固定 1 GPU = 1 rank）。
- MPI 实现须与资格文件和站点环境一致；此处 `openmpi` 是海光命令示例，
  不应在未确认兼容性的情况下任意省略或替换。
- `:nomps` 后缀不能去掉——预检代码目前只支持禁用 MPS 的形式。
- **`--out` 用一个没有真实执行证据的新目录**（内容可以覆盖，但一旦某个目录里已经
  有 `timeseries.csv` / STAR 日志 / `GPU_CONFIRMED`/`FAILED`/`BLOCKED` 的 sidecar，
  GPU run 会拒绝复用它，报 `OUTPUT_REUSE_FORBIDDEN`——这是保护真实执行证据不被
  覆盖，跟步骤 1 里"目录可以直接覆盖"是两码事，没有放开）。

## 7.（GPU，可选）另开终端实时监控 DCU 利用率

```bash
watch -n 2 hy-smi
```

跑起来后应该看到 `HCU%` 从 0 开始跳动、`VRAM%` 明显上升，说明真的在用 GPU 算，
但这只是辅助观测：可能是初始化或其他进程在占用，不能据此判定 GPU 求解成功。

## 8. 检查结果

```bash
PYTHONPATH=. python scripts/workflow.py ccm-status --out <case_dir>/raw_star
```

或直接读证据文件（GPU 路径）：

```bash
python3 -c "
import json
r = json.load(open('<case_dir>/raw_star/gpu_execution.json'))
print('state:', r['state'])
print('failure_code:', r.get('failure_code'))
print('actual_backend:', r.get('actual_backend'))
"
```

GPU 路径 `state` 应该是 `GPU_CONFIRMED`。如果是 `FAILED`/`BLOCKED`，看
`failure_code` 对照 [`docs/STARCCM_GPU.md`](docs/STARCCM_GPU.md) 第 7 节的失败
分类表定位问题。

---

## 已知问题（按当前代码与目标环境复核）

- Slurm 集群的 Gres 资源名可能是 `dcu` 而不是 `gpu`，`scontrol show job -o` 可能
  只给 `AllocTRES=`/`ReqTRES=` 没有裸的 `Gres=`/`TRES=` 键——预检代码已兼容。
- 海光驱动版本串（如 `6.3.31-V1.5.0a`）带非数字后缀——资格文件的
  `driver_requirement` 比较已改成只取数值点分前缀。
- 不 source `env.sh` 会 SIGABRT（退出码 134，`LLVM ERROR: Missing device library
  for gfx908`）——在已记录的海光环境中必须加载配套脚本，见步骤 5；其他环境应重新取证。


## 9. 最终验收与失败处理

### 9.1 保存哪些证据

真实运行前保存展开后的命令和参数表；运行后保存 CLI 返回码、STAR 日志和作业状态。
命令返回非零时立即进入失败处理，不继续执行依赖成功结果的后处理。

| 证据 | 位置或获取方式 | 核对内容 |
|---|---|---|
| 输入及摘要 | `<case_dir>/input/` | YAML、CSV、随机种子及时间范围一致 |
| STAR 日志 | `<case_dir>/raw_star/starccm_flow_control.log` | 实际版本、进程数、推进过程、异常 |
| 执行配置 | `raw_star/` 内生成的宏和 runtime plan | 实际路径、时间步、启动参数 |
| 运行清单 | `raw_star/case_manifest.yaml` | 最终状态和实际步数 |
| 时间序列 | `raw_star/timeseries.csv` | 非空、时间范围与预期一致、无缺失/非有限值 |
| 结果模板 | `raw_star/flow_control_result.sim` | 默认要求存在；禁用保存须事先明确 |
| GPU 证据 | `raw_star/gpu_execution.json` | 状态、实际后端、失败码、日志解析证据 |
| 作业会计 | Slurm `sacct` 输出 | 最终 State、ExitCode、主机 RAM 使用情况 |

对输入和模板可用 Linux `sha256sum` 或 macOS `shasum -a 256` 记录摘要。
哈希必须由真实文件计算；模板哈希用于追溯，不应把它解释成独立的网格版本。
当前 GPU 资格校验不再要求 `sim_review.sim_sha256` 相等，但换模板仍需重新确认模型清单。
旧 GPU 文档中若仍写必须哈希匹配，应以当前代码为准，不能把这一差异当作跳过审核的理由。

Slurm 作业结束后执行（运行中查询只是快照，交互分配还未退出时可能仍为 RUNNING）：

```bash
sacct -j "$SLURM_JOB_ID" \
  --format=JobID,State,ExitCode,Elapsed,ReqMem,MaxRSS,AllocCPUS,AllocTRES%80
```

同时检查作业及步骤行；空白 MaxRSS 表示会计信息缺失，不代表内存为零。
`ccm-status` 会更新 manifest 的状态检查字段，不是完全只读命令；它返回 0 也可能只是
没有已识别失败，不能单独代替完成验收。原始 GPU sidecar 由 runner 管理，AGENT 不手改。

### 9.2 失败后按证据定位

| 现象或失败码 | 下一步 | 不允许的捷径 |
|---|---|---|
| 输入验证错误 | 修正工况配置并在准备目录重新生成、校验 | 直接手改 CSV 掩盖配置错误 |
| `OUTPUT_REUSE_FORBIDDEN` | 新建尝试目录，关联旧证据 | 删除旧日志或 sidecar 强行复用 |
| `GPU_LOCK_HELD` | 查交接文档、作业和持锁进程 | 未确认进程退出就删除锁 |
| （已取消拦截）占用只记录在 `device_occupancy` | 核对 `busy` 里的进程是否都是自己的 | 杀别人的进程 |
| `ALLOCATION_MISMATCH` | 核对分配、可见设备和当前节点 | 编造卡序号/UUID 或盲改可见性变量 |
| 资格/版本/平台错误 | 收集本机实测值及同版本支持依据 | 只删 `test_only` 或把 unknown 改 compatible |
| LLVM gfx908 / 退出 134 | 检查本次 shell 是否加载正确海光环境脚本 | 将其他型号硬编码成 gfx936 |
| Slurm `OUT_OF_MEMORY` | 检查作业会计、主机 RAM 申请与实际峰值 | 把主机 RAM OOM 当显存不足 |
| `GPU_OUT_OF_MEMORY` | 保存显存与求解日志，评估资源方案 | 擅自改网格、物理模型或少卡盲重试 |
| 退出 0 但 GPU 未确认 | 查 sidecar 中缺失的 solver/设备证据 | 用利用率截图代替执行证据 |
| `OUTPUTS_INCOMPLETE` | 对比期望步数、时间范围和结果 sim | 将不完整序列标记为完成 |

每次重试必须记录“失败证据 → 修正原因 → 变更参数 → 新目录”。缺少权限、资格证据或
必要资源时，完成能做的准备，写清阻塞项后交接；不要无变化重复提交。

## 10. AGENT 交接与最终回复模板

在 `<case_dir>/AGENT_HANDOFF.md` 使用以下模板。涉及凭据的命令必须脱敏。
每次准备启动、进入运行、遇到失败和完成验收时更新；长作业交接必须提供实际 JobID，
后继 AGENT 先查询现有作业，不能重新启动同一算例。

```markdown
# 本次仿真交接
- 负责人 / 更新时间：
- 用户授权范围：仅输入 / dry-run / CPU run / GPU run
- 当前阶段：准备中 / 输入已验证 / dry-run 已验证 / 运行中 / 阻塞 / 失败 / 已验收
- 目标主机 / 仓库路径 / commit / 未提交变更：
- 工况 YAML / 输入目录 / 输入摘要：
- 模板路径 / 模板摘要 / 区域 / 初始物理时间：
- 动作窗口 / 求解时间步 / 目标时长 / 预期求解步数及依据：
- Python / STAR build / 环境脚本 / 资格文件：
- Slurm JobID / 节点 / 分配资源 / 请求 ranks 和卡数：
- 实际启动命令（脱敏）：
- CLI 退出码 / STAR 退出码 / Slurm 最终状态（未结束则写运行中）：
- 日志 / manifest / sidecar / timeseries / result sim 路径：
- 已验证事实及证据：
- 未确认项或失败码及原始错误位置：
- 下一步 / 由谁处理 / 允许重试的条件：
- 上一次尝试目录（如有）：
```

最终向用户回复：本次做到哪一步、实际输出位置、启动/验收证据、剩余阻塞。
仅协助启动的任务，还应交付 JobID 或实际进程信息、日志位置和查看状态的命令；
作业仍在运行就明确“已启动，尚未完成”，不要为了写最终验收结论擅自扩大任务。
只做了输入或 dry-run 就明确如此；不能使用“仿真已跑通”代替尚未取得的真机结果。

## 11. 查证入口

- [工作流入口](scripts/workflow.py)：子命令路由。
- [动作生成器](flow_control/generator/schedule_generator.py)：输入目录与生成行为。
- [动作校验器](flow_control/generator/schedule_validator.py)：CSV 基本约束。
- [CCM CLI](flow_control/cli/run_starccm.py)：参数、资源选择和时间步优先级。
- [STAR runner](flow_control/adapters/starccm_runner.py)：宏、启动、输出与 GPU 最终确认。
- [GPU 资格校验](starccm/runtime/gpu_qualification.py)及[实时预检](starccm/runtime/gpu_preflight.py)：阻塞条件。
- [状态查询](flow_control/cli/ccm_status.py)：状态展示及 manifest 更新。

本文核对基于 2026-09-22 工作区；未提交代码可能与目标集群不同。后续 AGENT 若发现
行为变化，先查实际同步版本和对应实现，再更新文档，不要把本文当成跨版本保证。
