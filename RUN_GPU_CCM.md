# 启动一次 GPU（海光 DCU）CCM 仿真

本文档记录在超算集群上用海光 DCU 跑通一次 STAR-CCM+ GPU 仿真的完整步骤，基于
2026-09-18 在节点 f11r2n13（8 卡 BW/C-3000，驱动 6.3.31-V1.5.0a）上真机调试
跑通后整理。详细的调试过程、每个问题的根因见
[`docs/gpu/20.02-evidence-register.md`](docs/gpu/20.02-evidence-register.md) 第 4.3 节；
参数语法、失败分类等完整参考见 [`docs/STARCCM_GPU.md`](docs/STARCCM_GPU.md)。

CPU 路径不需要本文档任何步骤，直接用 [`run_python.slurm`](run_python.slurm) 提交即可。

---

## 0. 前提

- 项目代码已同步到集群（`git pull` 或 `scp`/`rsync` 整个仓库）。
- 有一份人工核验过的 GPU 资格文件（`--gpu-qualification` 参数需要），可以从
  [`examples/ccm_gpu/qualification.hygon.example.json`](examples/ccm_gpu/qualification.hygon.example.json)
  改出来。关键字段必须是目标节点的真实实测值，不能照抄模板：
  - `gpu_model`：`hy-smi --showproductname` 的 Card Series（本节点是 `BW`）
  - `driver_requirement`：只写数值点分前缀，例如 `>=6.3.31`（驱动串
    `6.3.31-V1.5.0a` 带的 `-V1.5.0a` 后缀不参与比较，也不用写进去）
  - `os_id`：`/etc/os-release` 的 `ID=`
  - `identity` / `identity_sha256`：STAR-CCM+ 可执行文件路径和它的 sha256
  - `sim_review.items`：你的 `.sim` 文件里实际用到的求解器/物理模型清单，
    必须是你本人核实过的，不能瞎填 `compatible`
  - 删掉模板里的 `"test_only": true` 这一行，否则会被拒绝
  - `approved_gpu_counts` 要包含你打算用的卡数

## 1. 申请 Slurm 分配

```bash
srun -p hx1hdnormal01 -N 1 --gres=dcu:8 --pty bash
```

（卡数按需要调整，`--gres=dcu:N`；这个分区的 GresTypes 同时认 `dcu` 和 `gpu`
两个资源名，写 `dcu` 是跟现有站点脚本一致的习惯用法。）

进去之后确认分配到手：

```bash
echo $SLURM_JOB_ID
scontrol show job -o $SLURM_JOB_ID | grep -o 'AllocTRES=[^ ]*'
```

## 2. 确认目标卡是空闲的

```bash
hy-smi --showpids
```

应该是 `No KFD PIDs currently running!`。如果不是空的，说明这台节点上有别的
作业/进程在用卡：

- 如果是你自己另一个正在跑的作业（比如还开着的 STAR-CCM+ GUI 会话）：
  确认不再需要后 `scancel <那个作业的 JobID>` 释放，或者干脆用那个作业继续跑，
  不要再开一个抢同一批卡。
- 如果是别人的作业：换一个节点，不要抢占。

**不要**因为"嫌麻烦"就跳过这一步去跑——两个真实 GPU 求解共享同一批物理卡会导致
显存不够崩溃或者两边结果都不可信，预检代码会正确地拦下这种情况
（`DEVICE_IN_USE`），这是设计好的保护，不是 bug。

## 3. 激活环境 + source GPU 运行环境（关键，容易漏）

```bash
cd /public/home/acn6k38urd/bc/generic_automation_v27_NS_RL100_PF_RULE_2
source /public/home/acn6k38urd/miniconda3/etc/profile.d/conda.sh
conda activate py3.10

# 必须：这一步不做，STAR-CCM+ 会把这批海光 DCU 的 GPU 编译目标架构识别错
# （内部默认成 AMD gfx908，实际这批卡是 gfx936），报 LLVM ERROR 然后 SIGABRT
source /public/home/acn6k38urd/apprepo/starccmplus/20.02.007-glibc2.28_double/scripts/env.sh
```

`env.sh` 会 `export HIP_COMGR_ARCH=gfx936` / `ROCBLAS_GPU_ARCHNAME=gfx936` 等，
并 `module load` 一批 DTK/UCX/RCCL 相关模块，是海光官方 GUI 提交脚本
（`job_GUI.slurm`）启动前一定会做的步骤，命令行手动跑必须自己补上，因为
`run_python.slurm`/`workflow.py` 都不会自动帮你 source 它。

## 4. 设置 schedule 路径

复用之前 CPU 跑出来的 `actuation_schedule.csv`（不用重新生成）：

```bash
export CCM_SCHEDULE="runs/nojet_3s_<某次CPU跑成功的JOB_ID>/input/actuation_schedule.csv"
ls -l "$CCM_SCHEDULE"   # 确认文件存在
```

这个变量只在当前终端会话里有效，换终端/新开 `srun` 都要重新 `export`。

## 5.（可选）先 dry-run 验证参数拼接

不碰真实硬件，只生成宏和 runtime plan：

```bash
PYTHONPATH=. python scripts/workflow.py ccm \
  --schedule "$CCM_SCHEDULE" \
  --sim cifutest.sim \
  --out /tmp/gpu_dry_run \
  --starccm-path /public/home/acn6k38urd/apprepo/starccmplus/20.02.007-glibc2.28_double/app/20.02.007-R8/STAR-CCM+20.02.007-R8/star/bin/starccm+ \
  --region Region \
  --np 8 --compute-backend gpu --gpgpu auto:8:nomps \
  --execution-mode dry-run
```

检查 `/tmp/gpu_dry_run/gpu_execution.json` 的 `state` 是 `UNVERIFIED`，argv 里
有 `-gpgpu auto:8:nomps -require-gpgpu-compatibility`。

## 6. 真实 run

```bash
rm -rf /tmp/gpu_real_run   # 换新目录或清空旧目录，避免 OUTPUT_REUSE_FORBIDDEN

PYTHONPATH=. python scripts/workflow.py ccm \
  --schedule "$CCM_SCHEDULE" \
  --sim cifutest.sim \
  --out /tmp/gpu_real_run \
  --starccm-path /public/home/acn6k38urd/apprepo/starccmplus/20.02.007-glibc2.28_double/app/20.02.007-R8/STAR-CCM+20.02.007-R8/star/bin/starccm+ \
  --region Region \
  --scheduler slurm --slurm-job-id "$SLURM_JOB_ID" \
  --np 8 --compute-backend gpu --gpgpu auto:8:nomps \
  --mpi-driver openmpi \
  --gpu-qualification my_qualification.json --execution-mode run
```

参数说明：

- `--np` 必须等于 `--gpgpu auto:N:nomps` 里的 N（首批固定 1 GPU = 1 rank）
- `--mpi-driver openmpi` 不是这次问题的根因（问题是第 3 步的 `env.sh`），但保留
  它没有坏处，CPU 路径不受影响；不想用可以去掉这个参数
- `:nomps` 后缀不能去掉——预检代码目前只支持禁用 MPS 的形式，带 MPS 的写法
  （GUI 默认用的 `auto:8`，不带 `:nomps`）需要单独的资格证据，本次未开放

## 7.（可选）另开终端实时监控 DCU 利用率

```bash
watch -n 2 hy-smi
```

跑起来之后应该能看到 `HCU%` 从 0 开始跳动、`VRAM%` 明显上升，说明真的在用 GPU
算，而不是卡在初始化或者偷偷退回 CPU。

## 8. 检查结果

```bash
PYTHONPATH=. python scripts/workflow.py ccm-status --out /tmp/gpu_real_run
```

或者直接读证据文件：

```bash
python3 -c "
import json
r = json.load(open('/tmp/gpu_real_run/gpu_execution.json'))
print('state:', r['state'])
print('failure_code:', r.get('failure_code'))
print('actual_backend:', r.get('actual_backend'))
"
```

`state` 应该是 `GPU_CONFIRMED`。如果是 `FAILED`，看 `failure_code` 对照
[`docs/STARCCM_GPU.md`](docs/STARCCM_GPU.md) 第 7 节的失败分类表定位问题。

---

## 已知问题记录（真机调试踩过的坑，代码已修复的部分不用再管）

以下问题在本仓库的预检代码里已经修好（同步最新代码即可，不需要手动绕过）：

- Slurm 这套集群的 Gres 资源名是 `dcu` 不是 `gpu`，且 `scontrol show job -o`
  只给 `AllocTRES=`/`ReqTRES=`，没有裸的 `Gres=`/`TRES=` 键——预检代码已同时
  兼容这两点。
- 海光驱动版本串（如 `6.3.31-V1.5.0a`）带非数字后缀——资格文件的
  `driver_requirement` 比较已改成只取数值点分前缀。

以下问题**不是代码能修的**，必须靠第 3 步的 `source env.sh`：

- 不 source `env.sh` 时，STAR-CCM+ 内置的 GPU 编译支持组件
  （`amdgpu/rocm_compiler_support/6.2.0-cda-001`）会把这批海光 DCU 错误识别成
  AMD `gfx908`（实际是 `gfx936`），报 `LLVM ERROR: Missing device library for
  gfx908` 后 SIGABRT（退出码 134）。`env.sh` 里的 `HIP_COMGR_ARCH=gfx936` /
  `ROCBLAS_GPU_ARCHNAME=gfx936` 补上了这个信息。
