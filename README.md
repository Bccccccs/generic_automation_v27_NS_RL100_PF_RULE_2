# Flow Control：喷气流动控制的 STAR-CCM+ 数据生产与 ROM 识别

本项目把“喷气动作设计 → STAR-CCM+ CFD 求解 → 结果整理与质检 → 训练数据集 → 降阶模型（ROM）”
串成一条可复现的流水线。研究对象是带 24 个底部喷口（J01～J24）的模型，关注喷气对
六个承载区域升力 `Fz_S1L … Fz_S3R` 的影响。

- 环境与集群配置要求：[`SETUP.md`](SETUP.md)
- 如何运行各个部分（给人看，也可整份复制给 AI agent）：[`AGENTS.md`](AGENTS.md)

## 流水线

```text
动作 YAML (configs/actions, configs/b52)
  └─ actions ──────────► <case>/input/actuation_schedule.csv     逐窗口的 24 喷口质量流量
       └─ ccm（CPU / 海光 DCU GPU）► <case>/raw_star/            STAR 日志、timeseries.csv、结果 .sim
            └─ 自动整理（organize）► <case>/processed/timeseries.csv
                 └─ check / figures ► quality_report.json、诊断图
                      └─ B53 ─────► artifacts/B53_training_data/  训练/验证数据集（相对无喷气参考的 ΔFz）
                           └─ B54 ► artifacts/B04_real_ROM_*      ARX + 岭回归 ROM 与预测评估
```

统一入口：`python scripts/workflow.py <actions|ccm|ccm-status|organize|check|figures|mock>`；
B53/B54 各有独立脚本（见 [`AGENTS.md`](AGENTS.md)）。

## 目录

| 目录 | 内容 |
|---|---|
| `flow_control/` | 主代码：动作生成（`generator`）、STAR 启动（`adapters`、`cli`）、结果整理（`star_ingest`）、B53 数据集（`b53_dataset`）、ROM（`rom`） |
| `starccm/` | STAR-CCM+ 宏模板、日志解析、GPU 预检/资格/证据（`runtime`） |
| `generic_automation/`、`models/`、`rom_identifier.py` | 早期版本模块，为兼容保留 |
| `configs/` | 动作配置（`actions`、`b52`）、B53/B54 配置、manifest 模板（`week4`） |
| `scripts/` | `workflow.py` 统一入口、B53/B54 脚本、`.sim` 模板检查、集群环境脚本 |
| `run_python.slurm` | CPU 批处理提交脚本（B52 训练/验证集） |
| `examples/ccm_gpu/` | GPU 资格文件模板、环境取证脚本 |
| `runs/` | 算例输出（大部分不入库；`runs/real_star/` 入库） |
| `artifacts/` | B04/B53/B54/B05 交付产物 |
| `docs/` | 详细文档，见下 |
| `tests/` | pytest 测试 |
| `archive/` | 历史快照（不入库） |

## 详细文档（docs/）

| 文档 | 内容 |
|---|---|
| [`docs/RUN_GPU_CCM.md`](docs/RUN_GPU_CCM.md) | GPU（海光 DCU）仿真逐步操作手册，含 manifest 模板与资格文件填写 |
| [`docs/WORKFLOW_COMMANDS.md`](docs/WORKFLOW_COMMANDS.md) | `workflow.py` 各子命令的完整参数、输出目录结构、时间序列字段 |
| [`docs/MANUAL_STAR_CCM_RUN_WORKFLOW.md`](docs/MANUAL_STAR_CCM_RUN_WORKFLOW.md) | CPU 算例的人工运行、监控与验收示例 |
| [`docs/STARCCM_GPU.md`](docs/STARCCM_GPU.md) | GPU 后端设计、预检项、失败分类表 |
| [`docs/B52_ACTION_SCHEDULES.md`](docs/B52_ACTION_SCHEDULES.md) | B52 标定/训练/验证动作表 |
| [`docs/B53_TRAINING_DATA.md`](docs/B53_TRAINING_DATA.md) | B53 数据筛选口径与基准 |
| [`docs/B54_REAL_ROM.md`](docs/B54_REAL_ROM.md) | B54 ROM 模型口径与验收 |
| `docs/week1`～`week4`、`docs/gpu` | 阶段性报告与证据登记 |

## 当前状态（2026-10-13）

- CPU 求解：B52 v2 训练集与 11 s 无喷气参考（`runs/cifu0_nojet_11s`）已跑完。
- GPU 求解：海光 DCU 于 2026-09-18 真机跑通首次 GPU 求解，操作见 `docs/RUN_GPU_CCM.md`。
- B53：已登记 `runs/b52_v2/training` 与 `runs/b52_v2/validation`，基准为无喷气参考
  `runs/cifu0_nojet_11s`（训练片段 0.8–10 s，验证片段 0.8–5.2 s）。
- B54：ARX + 岭回归 ROM 已完成训练与验证评估（`run_status=COMPLETE`，
  `acceptance_status=REVIEW_REQUIRED`，待人工评审）。
