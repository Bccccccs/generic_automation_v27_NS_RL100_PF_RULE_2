# CCM GPU：环境取证与单节点提交示例

对应计划任务 4。**本目录不是已核验的站点配置**：所有需要站点信息的字段都保留
占位符并标注 BLOCKED，取得目标站点规则后才能填写。

当前状态见 `docs/gpu/20.02-evidence-register.md`：G0 未通过，正式 GPU run 被阻塞。

---

## 1. 只读环境取证

在**已经分配给本次任务的计算节点**、且处于实际 STAR 启动上下文（原生安装或站点
已核验的容器/launcher wrapper）里执行：

```bash
bash examples/ccm_gpu/collect_environment_evidence.sh /absolute/path/to/starccm+
```

脚本只做只读查询，不安装驱动、不改环境、不启动 STAR 求解、不申请资源。它采集：

- `starccm+ -version` / `-help` / `-phelp:gpgpu`（B-01 的命令语法核验）
- `uname -srm`、`/etc/os-release`
- 启动上下文是原生安装还是容器/wrapper（含可执行文件 hash）
- NVIDIA 节点的 `nvidia-smi -L` / 设备明细 / `nvidia-smi topo -m`
- `CUDA_VISIBLE_DEVICES`、`NVIDIA_VISIBLE_DEVICES`（只打印，不修改）
- Slurm 作业分配与节点清单（只记录资源字段，不打印完整环境和许可证）

输出写到 `stdout`，由操作人员自行重定向保存并脱敏后归档。

AMD 节点：脚本只会在检测到 `nvidia-smi` 时调用它。AMD 平台当前 **BLOCKED**
（B-03），需要先由目标驱动自带工具的 `--help` 确认只读等效命令，再单独实现
vendor 分支；不得用 NVIDIA 的 mock 结果认证 AMD。

## 2. 资格文件

`--gpu-qualification` 指向的 JSON 必须是人工核验后的结果，schema 为
`ccm_gpu_qualification_v1`。字段要求见计划第 5.4 节，校验实现在
`starccm/runtime/gpu_qualification.py`。

`qualification.example.json` 是**结构示例**，`test_only=true`，生产 GPU run 会
直接拒绝它。不要把它改成 `test_only=false` 当作真实资格使用：真实文件里的
build、驱动、型号、文档章节和 sim hash 都必须来自实际取证。

要点：

- `star_build` 必须是完整 build（例如 `20.02.007`），不能只写 `20.02` 或目录名。
- `approved_platforms` 不允许 `*` / `any` 之类通配。
- `driver_requirement` 必须写成可核验的 `>=版本` 或 `==版本`。
- `approved_launch.single_node_only` 必须为 `true`；本次不实现跨节点 GPU。
- `approved_launch.approved_ranks_per_gpu` 首批只批准 `1`（每 GPU 一个 MPI rank）。
- `approved_launch.mps_policy` 首批只接受 `disabled` / `not_applicable`；启用 MPS
  需要独立资格证据，且选择器不带 `:nomps` 的形式在此之前不开放。
- `sim_review.items` 里任何 `unknown` 或 `incompatible` 都会阻止正式 GPU run。
- `approved_launch.occupancy_ignore_process_names` 可选，用于跳过站点常驻代理
  （MPS server、DCGM 等）的占用冲突判定。必须有站点依据，不允许通配或空串；
  默认空列表表示任何占用都判冲突。
- 设备启用 MIG 一律拒绝（`MIG_NOT_APPROVED`）：MIG 需要独立资格证据，且其
  子设备 UUID 会让占用检查失效。
- `required_flags` 必须与本实现生成的 `-gpgpu` /
  `-require-gpgpu-compatibility` 一致；目标 build 使用不同参数时，先按同版本手册
  更新 token 生成再放行。

## 3. 单节点 Slurm 提交（BLOCKED）

本项目现有 `--scheduler slurm` 只**消费已经 RUNNING 的分配**：解析分配、生成
machinefile、再用 SSH/MPI 启动。它不会自动 `sbatch`，也不使用
`-batchsystem slurm`。GPU 模式沿用同一方式，不新增调度器。

因此提交流程是两步：先由站点作业脚本拿到单节点 GPU 分配，再在分配内运行 CCM。

第二步（本次已实现，G0 通过后才可真机执行）：

```bash
PYTHONPATH=. .venv/bin/python scripts/workflow.py ccm \
  --schedule "$CCM_SCHEDULE" --sim "$CCM_SIM" --out "$CCM_GPU_OUT" \
  --starccm-path "$CCM_BIN" --region "$CCM_REGION" \
  --scheduler slurm --slurm-job-id "$SLURM_JOB_ID" \
  --np "$CCM_GPU_COUNT" --compute-backend gpu \
  --gpgpu "auto:${CCM_GPU_COUNT}:nomps" \
  --gpu-qualification "$CCM_GPU_QUALIFICATION" --execution-mode run
```

首批配置要求 `CCM_GPU_RANKS == CCM_GPU_COUNT`（每 GPU 一个 rank）。`--np` 是
STAR 进程数，不是显卡数，也不能沿用 CPU 满核配置。

第一步的 sbatch 脚本 **BLOCKED**（B-09）。下面是需要站点填实的字段，未取得规则
前不得提交：

```text
#SBATCH --partition=<站点 GPU 分区，BLOCKED>
#SBATCH --account=<站点账户，BLOCKED>
#SBATCH --nodes=1                 # 本次固定单节点
#SBATCH --ntasks=<等于 GPU 数>
#SBATCH --gpus-per-node=<GPU 数>   # 或站点要求的 --gres=gpu:<型号>:<数量>
#SBATCH --time=<站点上限，BLOCKED>
#SBATCH --qos=<站点 QOS，BLOCKED>
```

未取得站点 partition/account/GRES/QOS 规则时，任何填好的数值都是编造的，不能作为
真实 provenance 记录。

## 4. 独占服务器（manual）

`--scheduler manual` 时没有作业分配证据，预检会把
`allocation.gpu_count_source` 记为 `manual_exclusive_server_no_allocation_evidence`。
因此 manual 只允许在**明确保留给本次任务的独占 Linux 服务器**上使用；共享节点上
"看起来空闲"的 GPU 不能直接占用。资格文件的 `approved_launch.scheduler` 必须与
实际使用的一致，否则预检以 `LAUNCH_NOT_APPROVED` 拒绝。

manual 模式若提供 `--machinefile`，该文件只能指向当前节点，且 slot 数必须覆盖
`--np`；否则分别以 `MACHINEFILE_NOT_SINGLE_NODE` /
`MACHINEFILE_SLOTS_INSUFFICIENT` 拒绝。

## 5. 不在本目录范围内的内容

- 跨节点 GPU 调度、逐主机 GPU 配置文件、远端设备发现、多节点状态机：本次不实现。
- PBS scheduler：目标站点若使用 PBS，需先以经过站点审核的外部作业脚本分配资源，
  再接 manual + machinefile，并提供 GPU 分配证据；没有适配证据就是 BLOCKED。
- 容器启动命令整串塞进 `--starccm-path`：不允许。该参数只接收单个可执行文件路径；
  仅有容器部署时需要站点已核验的 launcher wrapper，并记录其 hash。
