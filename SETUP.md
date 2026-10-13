# 配置要求

本项目分两部分运行：**本地电脑**做准备、数据处理和 ROM；**超算集群**跑 STAR-CCM+。

## 1. 软件要求

| 项目 | 要求 | 说明 |
|---|---|---|
| Python | 3.10 及以上 | 集群用 conda 环境 `py3.10`；本地可用任意 ≥3.10 的 venv |
| Python 依赖 | `requirements.txt`：numpy ≥1.24、PyYAML ≥6.0、matplotlib ≥3.7、pytest ≥8.0 | 其余为标准库 |
| STAR-CCM+ | 20.02.007-R8，双精度（`20.02.007-glibc2.28_double`） | 只在集群上需要；需许可证 |
| 调度系统 | Slurm | `--scheduler slurm` 只读取已有分配，不会替你申请 |
| GPU（可选） | 海光 DCU（BW / C-3000，驱动 ≥6.3.31），`hy-smi` 可用 | 还需要 STAR 自带的 `scripts/env.sh` 和 GPU 资格文件 |
| 操作系统 | 集群 Linux x86_64；本地 macOS/Linux 均可 | Windows 只支持 CPU 的旧 batch 方式 |

## 2. 本地环境

```bash
cd <仓库根目录>
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt
python scripts/workflow.py --help        # 能显示子命令列表即可
python -m pytest -q                      # 可选：跑全部测试
```

本地不需要 STAR-CCM+；`actions`、`organize`、`check`、`figures`、`mock`、B53、B54、
`ccm --execution-mode dry-run` 都可以在本地跑。

## 3. 集群环境（新账号 `yanbochao`）

### 3.1 固定路径

| 名称 | 路径 |
|---|---|
| 仓库 | `/public/home/yanbochao/bc/generic_automation_v27_NS_RL100_PF_RULE_2` |
| STAR 安装根 | `/public/home/yanbochao/apprepo/starccmplus/20.02.007-glibc2.28_double` |
| `starccm+` | `<STAR 安装根>/app/20.02.007-R8/STAR-CCM+20.02.007-R8/star/bin/starccm+` |
| GPU 环境脚本 | `<STAR 安装根>/scripts/env.sh`（按旧账号目录结构推断，首次使用先 `ls` 确认） |
| `.sim` 模板 | `<仓库>/cifu0.sim`（`.sim` 不入库，需单独上传） |
| 分区 | `hx1hdnormal01`（CPU 与 DCU 共用；DCU 用 `--gres=dcu:N`） |
| module | `anaconda3/2023.09`、`starccmplus-20.02.007-glibc2.28_double` |

换账号或换集群时，以上路径要同步改到 `run_python.slurm`、`scripts/inspect_ccm_geometry.slurm`
和 `docs/RUN_GPU_CCM.md`。

### 3.2 一次性初始化（登录节点）

```bash
cd /public/home/yanbochao/bc/generic_automation_v27_NS_RL100_PF_RULE_2
bash scripts/setup_cluster_env.sh       # 创建 conda 环境 py3.10 并安装依赖；已存在则跳过
```

### 3.3 代码与数据同步

- 代码用 `git pull`；**未提交/未跟踪文件**（新 YAML、资格文件、`.sim`）需要 `scp`/`rsync`
  单独传，并用 `sha256sum`（macOS 用 `shasum -a 256`）核对两端一致。
- 不要用带 `--delete` 的同步覆盖集群上的 `runs/`。

### 3.4 资源建议

| 场景 | 建议 |
|---|---|
| CPU 求解 | 1 节点，`--ntasks-per-node=16`，`--mem=128G`，STAR `--np 8`（`run_python.slurm` 现行设置） |
| GPU 求解 | 1 节点，`--gres=dcu:N`，`--ntasks=N`，`--np N`，`--mem=128G` 起 |
| 内存 | `--mem` 是主机内存，与显存无关；集群限制每核约 8G |

## 4. `.sim` 模板要求

- 有一个区域包含 `J01`～`J24` 共 24 个喷口边界；`cifu0.sim` 的区域名是 **`区域`**（不是默认的 `Region`）；
- 有 `JET01`～`JET24` 底部受力面，且不与喷口面混用；
- 瞬态求解，允许的最大物理时间不小于本次总时长；
- 有 `total`、`drag`、`Pitch_Moment`、`Roll_Moment`、`Jet_Reaction_Z` 及六区力 report；
- 换模板后用 `scripts/inspect_ccm_template.py` 重写 `configs/week4/case_manifest_template.yaml`
  的 `star:` 段（见 `docs/RUN_GPU_CCM.md` 第 1 步）。

## 5. GPU 额外要求

- 每个新 shell 都要 `source <STAR 安装根>/scripts/env.sh`，否则会报 `gfx908` 错误并 SIGABRT；
- 资格文件：从 `examples/ccm_gpu/qualification.hygon.example.json` 复制，填真实取证值，
  删除 `test_only`，存为 `configs/ccm_gpu/qualification.hygon.json`；
- `--np` = 卡数，`--gpgpu auto:N:nomps`，`--mpi-driver openmpi`。

完整步骤见 [`docs/RUN_GPU_CCM.md`](docs/RUN_GPU_CCM.md)。
