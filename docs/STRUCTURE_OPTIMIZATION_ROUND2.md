# 第二次结构优化说明

本文档记录在第一次模块化重构之后，对项目入口层和历史文件归档做的第二次整理。

## 1. 本次优化目标

第一次整理已经把业务代码从根目录移动到 `generic_automation/` 包内，并按 `core`、`adapters`、`starccm`、`monitor`、`rl`、`cli` 分层。本次优化继续处理两个问题：

- 根目录仍保留多个 Python wrapper 和 shell launcher，视觉上仍像脚本集合。
- 旧版 `generic_automation_v09_2stage/` 完整目录继续放在根目录，容易和当前工程结构混淆。

本次没有重写仿真、RL、monitor 或稀疏喷气控制功能，只调整入口组织和文档说明。

## 2. 整理前

根目录仍直接包含：

- `run_case.py`
- `run_sweep.py`
- `run_monitor_only.py`
- `offline_replay.py`
- `force_param_update.py`
- `run_full_pipeline.sh`
- `generic_automation_v09_2stage/`

这些文件虽然大多已经是薄 wrapper，但放在根目录会削弱第一次重构后的工程边界。

## 3. 整理后

根目录新增统一入口：

- `ga.py`

兼容 wrapper 下沉到：

- `scripts/entrypoints/run_case.py`
- `scripts/entrypoints/run_sweep.py`
- `scripts/entrypoints/run_monitor_only.py`
- `scripts/entrypoints/offline_replay.py`
- `scripts/entrypoints/force_param_update.py`

pipeline launcher 下沉到：

- `scripts/pipelines/run_full_pipeline.sh`

旧版完整目录归档到：

- `archive/legacy/generic_automation_v09_2stage/`

脚本目录说明见：

- `scripts/README.md`

## 4. 新入口用法

推荐新命令：

```bash
python ga.py case --config configs/config.yaml
python ga.py sweep --config configs/config.yaml --cases cases/cases.csv
python ga.py monitor --config configs/config.yaml
python ga.py replay --help
python ga.py force-update --help
```

也可以直接使用包入口：

```bash
python -m generic_automation.cli.run_case --config configs/config.yaml
python -m generic_automation.cli.run_sweep --config configs/config.yaml --cases cases/cases.csv
python -m generic_automation.cli.run_monitor_only --config configs/config.yaml
```

旧脚本名仍保留在 `scripts/entrypoints/`，用于兼容历史运行笔记或外部调度脚本：

```bash
python scripts/entrypoints/run_case.py --config configs/config.yaml
python scripts/entrypoints/run_sweep.py --config configs/config.yaml --cases cases/cases.csv
python scripts/entrypoints/run_monitor_only.py --config configs/config.yaml
```

完整 SLURM pipeline 入口变为：

```bash
bash scripts/pipelines/run_full_pipeline.sh configs/config.yaml
```

## 5. 本次优化收益

- 根目录从多个独立入口脚本收敛为一个统一 launcher。
- 兼容脚本集中在 `scripts/entrypoints/`，历史入口和当前工程实现分离。
- shell pipeline 集中在 `scripts/pipelines/`，便于后续增加集群运行、批处理或调度模板。
- 旧版工程目录进入 `archive/legacy/`，保留追溯价值，但不再干扰当前结构。
- 核心 Python 包和正在开发的 `flow_control/` 原型未被强行移动，降低对现有测试和开发分支的影响。

## 6. PPT 版总结

本次优化是在第一次模块化重构基础上的入口层收敛：核心业务代码保持不变，将根目录中剩余的兼容 wrapper 和 pipeline 脚本统一下沉到 `scripts/`，新增 `ga.py` 作为统一命令入口，并把旧版完整工程归档到 `archive/legacy/`。整理后根目录更接近标准 Python 工程形态，入口更集中，历史文件与当前代码边界更清楚，同时仍保留旧脚本名以支持历史运行方式迁移。
