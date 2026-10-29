# Flow Control：喷气流动控制的 STAR-CCM+ 数据生产与 ROM 识别

用 STAR-CCM+ 仿真 24 个底部喷口（J01～J24）的喷气对六区升力 `Fz_S1L … Fz_S3R` 的影响，
产出训练数据并识别降阶模型（ROM）。

> 接手开发或让 AI agent 运行项目：先读 [`AGENTS.md`](AGENTS.md)。

## 流水线

```text
动作 YAML（configs/actions、configs/b52、configs/b06_validation）
  └─ actions ─────────► <case>/input/actuation_schedule.csv      逐时间步的 24 喷口质量流量
       └─ ccm（CPU / GPU）► <case>/raw_star/                       STAR 日志、timeseries.csv、结果 .sim
            └─ 自动整理 ───► <case>/processed/timeseries.csv
                 └─ check / figures ► quality_report.json、诊断图
                      └─ B53 ──► artifacts/B53_training_data/        ΔFz = Fz − 同时刻无喷气参考
                           └─ B54 ARX（主模型）/ B55 残差网络 / B56 概率集成
                                └─ B06 冻结模型独立验证
```

统一入口：`python scripts/workflow.py <子命令>`（`--help` 列出全部子命令）。

## 快速开始（本地）

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt                 # numpy、PyYAML、matplotlib、pytest
pip install -r requirements-rom-neural.txt      # 可选：B56 需要 PyTorch
python scripts/workflow.py --help
python -m pytest -q
```

本地不需要 STAR-CCM+：`actions`、`organize`、`check`、`figures`、`mock`、`ccm --execution-mode dry-run`、
B53～B56 都可在本地运行。真实求解在海光集群或 GPU 工作站上做，见 [`docs/GPU.md`](docs/GPU.md)。

## 目录

| 目录 | 内容 |
|---|---|
| `flow_control/` | 主代码：动作生成（`generator`、`excitation_patterns`）、STAR 启动（`adapters`、`cli`）、结果整理与质检（`star_ingest`）、B53 数据集（`b53_dataset`）、ROM（`rom`） |
| `starccm/` | STAR 宏模板、日志解析、GPU 预检 / 资格文件 / 证据（`runtime`） |
| `configs/` | 动作配置、B53～B56 配置、manifest 模板（`week4/`）、喷口与 report 映射表（`mappings/`） |
| `scripts/` | `workflow.py` 统一入口、B53/B54 脚本、`.sim` 检查、集群环境初始化、汇报图（`analysis/`） |
| `run_python.slurm` | 海光集群 CPU 批处理脚本（B52 训练/验证） |
| `examples/ccm_gpu/` | GPU 资格文件模板、环境取证脚本 |
| `tests/` | pytest 测试与夹具（`fixtures/`） |
| `generic_automation/`、`models/`、`rom_identifier.py`、`cases/` | 早期版本模块，为兼容保留，新工作不要使用 |
| `runs/`、`artifacts/`、`archive/` | 算例输出、交付产物、旧文档与旧数据存档：**都只在本地，不入库** |

## 文档

| 文档 | 内容 |
|---|---|
| [`AGENTS.md`](AGENTS.md) | 接手说明：环境、当前进度、各部分运行命令、规则、交接模板 |
| [`docs/COMMANDS.md`](docs/COMMANDS.md) | 全部子命令、Case 目录结构、字段、常见问题 |
| [`docs/GPU.md`](docs/GPU.md) | GPU 求解手册：各机器差异、资格文件、失败码、速度与价格 |
| [`docs/ROM.md`](docs/ROM.md) | B52～B56 与 B06：数据口径、模型、当前结果、后续方向 |

2026-10-29 之前的阶段报告、设计稿和交接记录已存档到本地 `archive/docs_20261029/`（不入库）。
