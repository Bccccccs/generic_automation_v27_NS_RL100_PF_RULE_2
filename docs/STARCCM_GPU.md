# STAR-CCM+ 20.02 GPU 求解（CCM 模块）

对应计划：`docs/superpowers/plans/2026-09-07-starccm-20.02-gpu-qorder-plan.md`
证据登记：`docs/gpu/20.02-evidence-register.md`

**当前状态：代码已实现，真机验收 BLOCKED。** 关卡 G0 未通过（没有目标 Linux
节点、没有 20.02 可执行文件、没有 GPU），因此：

- `-gpgpu` / `-require-gpgpu-compatibility` 是**候选语法，UNVERIFIED**；
- 生产日志句式注册表为空，任何 GPU run 即使 STAR 退出码为 0 也只会得到
  `FAILED / GPU_EXECUTION_UNCONFIRMED`；
- 本文所有“支持矩阵”“性能”章节都是 BLOCKED，不得当作已验证结论引用。

---

## 1. 默认行为没有变化

不加新参数就是 CPU，argv、宏、产物、异常行为与改造前一致：

```bash
PYTHONPATH=. .venv/bin/python scripts/workflow.py ccm \
  --schedule "$CCM_SCHEDULE" --sim "$CCM_SIM" --out "$CCM_CPU_OUT" \
  --starccm-path "$CCM_BIN" --region "$CCM_REGION" \
  --np "$CCM_CPU_RANKS" --execution-mode run
```

CPU 路径不会：检查 GPU、探测驱动、调用 `nvidia-smi`、要求 20.02、注入 `-gpgpu`、
产生 `gpu_execution.json`。`--np` 仍然是 STAR 进程数。旧 17.06 CPU 配置继续可用。

守护测试：`tests/test_ccm_cpu_compatibility.py`、`tests/test_ccm_gpu_integration.py`。

## 2. 新增的三个参数

| 参数 | 默认 | 约束 |
|---|---|---|
| `--compute-backend {cpu,gpu}` | `cpu` | 不接受 `auto`；没有“检测到显卡就启用”的模式 |
| `--gpgpu SELECTION` | 无 | GPU 必填；CPU 携带它直接报参数错误 |
| `--gpu-qualification PATH` | 无 | GPU 的 `run` 必填；dry-run 可不填；不会自动从项目 YAML 查找 |

GPU 还必须显式给出 `--np`（>= 1），不会沿用 CPU 的核心数推断。没有
`--allow-cpu-fallback`：GPU 失败不会自动改用 CPU 重跑，严格兼容参数自动附加且
不可关闭。

### 选择器语法

首批只接受单节点形式：

- 按数量：`auto:1:nomps`、`auto:2:nomps`、`auto:4:nomps`
- 按显式卡号（STAR/CUDA **本地**序号，不是宿主机物理编号）：`0:nomps`、`0,1:nomps`

拒绝：`force:` 前缀、裸 `auto`、`auto:0`、负数、重复卡号、前导零、`file:` 选择器、
缺少 `:nomps`、大小写不符、任何空白/换行/shell 元字符、混用两种选择机制。

不带 `:nomps` 的形式只在资格文件证明站点允许对应 MPS 行为时才开放，本次未开放，
也不会“默默删掉后缀重试”。

## 3. 离线生成 GPU 启动方案（dry-run）

```bash
PYTHONPATH=. .venv/bin/python scripts/workflow.py ccm \
  --schedule "$CCM_SCHEDULE" --sim "$CCM_SIM" --out "$CCM_GPU_DRY_OUT" \
  --starccm-path "$CCM_BIN" --region "$CCM_REGION" \
  --np 2 --compute-backend gpu --gpgpu auto:2:nomps \
  --execution-mode dry-run
```

产物与 CPU dry-run 相同（宏 + runtime plan），另加一份 `gpu_execution.json`，
状态 `UNVERIFIED`、`actual_backend=unknown`、`devices=[]`，并记录计划下发的脱敏
argv（`-podkey` 的值写成 `REDACTED`）。dry-run 不做任何 GPU/STAR 探测，也不会把
请求的设备写成实际设备。

对同一个目录重复 dry-run 只会刷新自己留下的离线请求记录；如果目录里的 sidecar 已经
是 `GPU_CONFIRMED` / `FAILED` / `BLOCKED`，dry-run 会以
`SIDECAR_OVERWRITE_FORBIDDEN` 拒绝，真实结论不会被销毁。

**这个命令不能说明机器上有两张可用卡。**

## 4. 真实 GPU run

单节点两卡：

```bash
PYTHONPATH=. .venv/bin/python scripts/workflow.py ccm \
  --schedule "$CCM_SCHEDULE" --sim "$CCM_SIM" --out "$CCM_GPU_OUT" \
  --starccm-path "$CCM_BIN" --region "$CCM_REGION" \
  --np 2 --compute-backend gpu --gpgpu auto:2:nomps \
  --gpu-qualification "$CCM_GPU_QUALIFICATION" --execution-mode run
```

已有 Slurm 分配内（不申请资源，只消费 RUNNING 分配）：

```bash
PYTHONPATH=. .venv/bin/python scripts/workflow.py ccm \
  --schedule "$CCM_SCHEDULE" --sim "$CCM_SIM" --out "$CCM_GPU_OUT" \
  --starccm-path "$CCM_BIN" --region "$CCM_REGION" \
  --scheduler slurm --slurm-job-id "$SLURM_JOB_ID" \
  --np "$CCM_GPU_COUNT" --compute-backend gpu \
  --gpgpu "auto:${CCM_GPU_COUNT}:nomps" \
  --gpu-qualification "$CCM_GPU_QUALIFICATION" --execution-mode run
```

首批配置要求 rank 数 == GPU 数（每 GPU 一个 MPI rank）。`2 ranks / 2 GPUs` 是
待 G0 审核的初始测试配置，不是普遍最优建议。

### run 的执行顺序

1. 输出目录复用检查：已有 `timeseries.csv`、STAR 日志、结果 sim 或带实际执行
   证据的 sidecar → `OUTPUT_REUSE_FORBIDDEN`。dry-run 留下的 `UNVERIFIED` 请求
   记录允许被覆盖。
2. 独占创建 `gpu_execution.lock`；已存在则 `GPU_LOCK_HELD`，不自动移除别人的
   lock，也不杀其他 STAR/MPS 进程。
3. 写 `UNVERIFIED` sidecar（含 sim/schedule/资格文件 hash）。
4. 实时预检（见第 5 节）。失败 → sidecar 记 `BLOCKED` + 失败码，释放 lock，抛出；
   **此时还没有写宏和 runtime plan**，不会留下半套产物。
5. 预检通过 → sidecar 记 `PREFLIGHT_PASSED`，并用核验过的选择器构造 argv。
6. 启动前记 `RUNNING`。
7. STAR 正常退出后、写 completed manifest 之前解析日志并确认 GPU 执行。
8. 无论成功失败，本进程持有的 lock 都会释放。

## 5. 预检核对什么

- STAR `-version` 是否包含资格文件的完整 build（不一致 → `STAR_BUILD_MISMATCH`）
- `uname -srm` 与 `/etc/os-release` 是否落在批准平台内（→ `PLATFORM_NOT_APPROVED`）
- GPU 厂商是否有经核验的只读查询命令；NVIDIA 之外当前 → `VENDOR_BLOCKED`
- `nvidia-smi` 的设备、UUID、PCI 地址、显存、驱动（超时 → `GPU_TOOL_TIMEOUT`，
  与“设备不存在”分开；失败 → `GPU_TOOL_FAILED`）
- 本次作业**实际可见**的卡：Slurm `Gres` 的 `IDX` 与 `CUDA_VISIBLE_DEVICES`
  交叉核验，给出 STAR/CUDA 本地序号与宿主机物理编号的映射。两者冲突 →
  `ALLOCATION_MISMATCH`；可见数不足或含重复物理卡 → `GPU_DEVICE_UNAVAILABLE`
- 分配了 N 张但节点物理卡更多、且既无 `IDX` 也无 `CUDA_VISIBLE_DEVICES` 指明具体
  是哪几张 → `ALLOCATION_MISMATCH`（无法证明 rank 会落在已分配的卡上）
- MIG：设备查询包含 `mig.mode.current`，启用即 → `MIG_NOT_APPROVED`。MIG 下计算
  进程上报的是 `MIG-*` 子设备 UUID，占用检查无法映射到父卡，因此必须显式拒绝
  而不是静默失效
- Slurm 作业是否 RUNNING、是否单节点、当前节点是否在分配内
  （→ `ALLOCATION_MISMATCH` / `NODE_MISMATCH`）
- 可见集合里是否有重复的物理卡（`CUDA_VISIBLE_DEVICES=0,0` 或 Slurm `IDX:1,1`
  这类写法会让一张卡被算作两张）→ `GPU_DEVICE_UNAVAILABLE`
- 本次要用的每张卡上是否已有其他计算进程（`nvidia-smi --query-compute-apps`，
  只读）→ `DEVICE_IN_USE`。显式卡列表只检查选中的卡，`auto:N` 检查前 N 张可见卡；
  节点上其他卡的作业不会误伤本次运行。这只是冲突检查，不是原子调度器；manual
  模式没有作业分配证据，本检查是唯一防线。
  站点常驻代理（MPS server、DCGM 等）只有在资格文件的
  `approved_launch.occupancy_ignore_process_names` 里显式列出才会跳过，被跳过的
  进程仍记录在 `device_occupancy.ignored` 里；该名单不允许通配或空串
- machinefile 是否只指向当前节点、slot 是否覆盖 rank 数
  （→ `MACHINEFILE_NOT_SINGLE_NODE` / `MACHINEFILE_SLOTS_INSUFFICIENT`）
- sim 文件 hash 是否等于资格文件审核的模板（→ `INPUT_HASH_MISMATCH`）
- rank 数是否等于 GPU 数 × 每卡 rank 数（→ `RANK_GPU_MISMATCH`）；请求卡数是否在
  批准范围内（→ `GPU_COUNT_NOT_APPROVED`）

预检**不会**修改 `CUDA_VISIBLE_DEVICES`，不会用显存总量推断算例装得下，不会
申请资源，不做跨节点发现。

## 6. GPU 证据文件 `raw_star/gpu_execution.json`

状态序列：`UNVERIFIED → PREFLIGHT_PASSED → RUNNING → GPU_CONFIRMED`；
失败为 `BLOCKED`（未启动）或 `FAILED`（已启动）。

只有 runner 写这个文件，`ccm-status` 只读。它不改 `timeseries.csv`、runtime plan
或 B04/B53/B54 schema，也不把 GPU 结论塞进 `case_manifest.yaml`（manifest 的
preflight/finalize 会覆盖字段）。

`actual_backend` 取值 `unknown/gpu/cpu/mixed`。`gpu` 只表示纳入资格审查的求解路径
使用了 GPU，不宣称 Python、Java、IO 或每个控制操作都在显卡上。请求值永远不会被
写进 `actual_*`。

`GPU_CONFIRMED` 只确认“GPU 执行且运行完整”。物理等价性和性能收益是另外两个独立
结论，不由它背书。

判“运行完整”需要同时满足：STAR 退出码 0；日志解析命中该 build 已登记的句式；
每张请求的卡都有 solver 执行证据；没有 OOM / 模型不支持 / CPU 回退；节点与预期
一致；`timeseries.csv` 行数不少于按 schedule 与时间步算出的期望步数；配置要求保存
`flow_control_result.sim` 时该文件确实存在。确认动作发生在模板快照检查之后、写
completed manifest 之前，因此不会出现 sidecar 说 `GPU_CONFIRMED` 而运行随后失败的
矛盾状态。

## 7. 失败分类

| 情况 | 结果 |
|---|---|
| 退出 0 但无 GPU solver 证据 | `FAILED / GPU_EXECUTION_UNCONFIRMED` |
| 日志句式未登记或 build 不符 | `FAILED / GPU_EXECUTION_UNCONFIRMED` |
| 设备初始化成功但 solver 回退 | `FAILED / CPU_FALLBACK_DETECTED` |
| 某张请求的卡缺执行证据 | `FAILED / GPU_DEVICE_EVIDENCE_MISSING` |
| OOM | `FAILED / GPU_OUT_OF_MEMORY`（不改网格、不自动少卡重试） |
| 物理模型不支持 | `FAILED / GPU_MODEL_UNSUPPORTED`（保留原始日志行） |
| STAR 非零退出 | `FAILED / STAR_NONZERO_EXIT`（保留真实退出码） |
| 启动失败 | `FAILED / LAUNCH_FAILED` |
| 目标卡上有其他计算进程 | 预检 `BLOCKED / DEVICE_IN_USE` |
| 设备启用 MIG | 预检 `BLOCKED / MIG_NOT_APPROVED` |
| 分配卡数少于节点物理卡且无法定位 | 预检 `BLOCKED / ALLOCATION_MISMATCH` |
| 可见集合含重复物理卡 | 预检 `BLOCKED / GPU_DEVICE_UNAVAILABLE` |
| 步数不足或结果 sim 缺失 | `FAILED / OUTPUTS_INCOMPLETE` |
| dry-run 想覆盖真实 GPU 结论 | `SIDECAR_OVERWRITE_FORBIDDEN`，原证据保留 |
| Ctrl-C | `FAILED / INTERRUPTED`（保留已完成步数，不自动接续） |
| dry-run 或只有请求参数 | `UNVERIFIED`，`actual_backend=unknown` |
| 历史 CPU 目录无 sidecar | 旧输出、旧返回码逐字保持 |

优先级：OOM > 模型不支持 > CPU 回退 > 设备证据缺失 > 未确认。

## 8. 查看状态

```bash
PYTHONPATH=. .venv/bin/python scripts/workflow.py ccm-status --out "$CCM_GPU_OUT"
```

原有 MPI/Step/错误行保持不变；只在存在 sidecar 时额外打印 `GPU state`、
`GPU requested`、`GPU actual`、`GPU devices`、`GPU failure` 和证据路径。
GPU 状态为 `FAILED`/`BLOCKED`/无法解析/未知时返回码为 1。
`requested=2、actual=unknown` 会如实显示，不会打印成 `actual=2`；`actual_backend`
不是 `gpu` 时，设备数会标注“预检采集，非本次实际使用”。

sidecar 停在 `RUNNING`/`PREFLIGHT_PASSED` 但 `gpu_execution.lock` 已不存在时，说明
写入者被杀死（例如 SIGKILL），此时打印 `GPU warning` 并返回 1，不会报成功。

### 已知限制：被阻塞或 dry-run 的目录

`ccm-status` 沿用既有目录判定，要求目录下存在 `case_manifest.yaml` 或
`starccm_flow_control.log`。GPU dry-run 和被预检阻塞（`BLOCKED`）的运行**故意**
不产生这两样东西，因此对这些目录执行 `ccm-status` 会抛 `FileNotFoundError`。
此时直接读证据文件：

```bash
PYTHONPATH=. .venv/bin/python -c "import json,sys;\
r=json.load(open(sys.argv[1]));\
print(r['state'], r['failure_code']); print(r['failure_detail'])" \
  "$CCM_GPU_OUT/gpu_execution.json"
```

没有放宽 `ccm-status` 的目录判定，是因为放宽后既有的 bootstrap 逻辑会在被阻塞的
目录里**新建** `case_manifest.yaml` 并打印 `状态: running` —— 既污染本次运行的证据
目录，又给出与 `BLOCKED` 矛盾的误导状态。这属于既有 CPU 状态逻辑，不在本次白名单
的修改意图内；如需改进应单独立项。

## 9. 支持矩阵（全部 BLOCKED）

| 平台 / 配置 | 状态 | 解除条件 |
|---|---|---|
| Linux + NVIDIA + 20.02，单卡 1 rank | BLOCKED | B-01/B-03/B-05/B-06/B-07 |
| Linux + NVIDIA + 20.02，单节点 2 卡 2 rank | BLOCKED | 同上；这是正式验收目标 |
| Linux + AMD + 20.02 | BLOCKED | 无 AMD 证据；不得用 NVIDIA mock 认证 |
| 混合型号 / MIG / 启用 MPS | BLOCKED | 需独立资格证据 |
| 跨节点 GPU | 不实现 | 本次范围固定单节点 |
| CPU（含 17.06、Windows batch、Slurm、manual） | 保持不变 | 由既有测试守护 |

## 10. 验收状态

| 维度 | 状态 |
|---|---|
| `implementation_status` | 任务 0–5 的代码与测试已交付；任务 6 未开始 |
| `cpu_compatibility_status` | PASS（本地 498+ 测试；argv/宏/产物逐字节冻结测试通过） |
| `gpu_execution_status` | BLOCKED（B-01/B-03/B-04/B-05/B-06/B-07） |
| `numerical_acceptance_status` | BLOCKED（B-05/B-08：无真实 sim、无冻结容差） |
| `performance_acceptance_status` | BLOCKED（依赖 gpu_execution 与 numerical 通过） |

数值与性能验收的方法（比较列、容差冻结、重复次数、speedup 公式）见计划第 8 节；
在没有真机证据前不填任何倍数。

## 11. 恢复到 CPU

显式使用原 CPU 命令或 `--compute-backend cpu`，去掉本次命令里的 GPU 专用参数，
使用原始冻结输入和新的输出目录。不能把更高版本保存的 GPU 结果 sim 直接当作旧
CPU build 的输入；原模板始终保留。
