# 项目结构改造说明

本文档基于 `old_structure/` 原始项目结构与当前仓库结构的对比，说明本次目录改造的目标、差异、迁移映射和后续使用方式。

## 1. 总体结论

旧结构以根目录脚本为中心：业务代码、CLI 入口、配置文件、运行日志和结果样例混放在同一层级，适合快速迭代，但随着 STAR-CCM+ 适配、在线监控、RL 控制、离线回放和批量运行能力增加，维护边界逐渐模糊。

当前结构改为“包内分层 + 外部资源目录 + 兼容入口”：

- `generic_automation/` 承载可复用 Python 业务代码。
- `generic_automation/core/` 放配置、Case 模型、运行时元数据等基础能力。
- `generic_automation/adapters/` 放仿真后端适配门面和 STAR-CCM+ adapter。
- `generic_automation/starccm/` 放 STAR-CCM+ 专属宏、日志解析、结果文件和 profiling 逻辑。
- `generic_automation/monitor/` 放在线监控、参数生成、参数写回和监控输出。
- `generic_automation/rl/` 放强化学习控制器、动作空间、状态、奖励、安全规则和运行时服务。
- `generic_automation/cli/` 放命令行入口实现。
- `ga.py` 提供统一入口；`scripts/entrypoints/` 保留历史脚本形式的兼容包装。
- `configs/`、`cases/`、`docs/`、`tests/`、`examples/`、`archive/` 分别承载配置、算例表、文档、测试、示例和历史归档。

## 2. 旧结构的主要问题

`old_structure/` 中的主要问题不是功能缺失，而是职责边界不清：

| 问题 | 旧结构表现 | 新结构处理 |
| --- | --- | --- |
| 模块边界弱 | 大量 `.py` 文件平铺在根目录 | 按 core、adapter、starccm、monitor、rl、cli 分包 |
| 入口与业务耦合 | `run_case.py`、`run_sweep.py` 等既是入口又在根层参与 import | 入口实现进入 `generic_automation/cli/`，外层只保留 wrapper 或统一 `ga.py` |
| 配置与代码混放 | `config.yaml`、phase 配置在根目录 | 配置统一进入 `configs/` |
| 运行产物混放 | `nohup.out`、`slurm-*.out`、`mesh_only_bg.log` 与源码同层 | 历史产物进入 `archive/legacy/...`，当前运行产物应写入 `results/` 或配置指定目录 |
| 结果样例污染源码视图 | `results/`、`results_validation/` 位于旧项目主体中 | 历史结果归档到 `archive/legacy/...` |
| 测试与示例缺位 | 旧结构未形成独立测试/示例目录 | 当前新增 `tests/`、`examples/` 和 `flow_control/` 相关能力 |

## 3. 目录级差异

### 旧结构

```text
old_structure/
├── *.py                         # core / adapter / monitor / rl / cli 混放
├── *.yaml                       # 运行配置混放在根目录
├── starccm_macro_template.java  # STAR-CCM+ 宏模板混放在根目录
├── cases/cases.csv
├── results/                     # 历史运行结果
├── results_validation/          # 历史验证结果
├── slurm-*.out, *.log, nohup.out
└── README.md
```

### 当前结构

```text
.
├── ga.py
├── generic_automation/
│   ├── core/
│   ├── adapters/
│   ├── starccm/
│   ├── monitor/
│   ├── rl/
│   └── cli/
├── scripts/
│   ├── entrypoints/
│   └── pipelines/
├── configs/
├── cases/
├── docs/
├── tests/
├── examples/
├── flow_control/
├── archive/
└── runs/
```

核心变化可以概括为：

```text
旧：文件按“出现时间”堆在一起
新：文件按“职责边界”分到包和资源目录
```

## 4. 文件迁移映射

### core 基础层

| 旧位置 | 新位置 | 说明 |
| --- | --- | --- |
| `old_structure/adapter_base.py` | `generic_automation/core/adapter_base.py` | `Case` 数据模型 |
| `old_structure/project_config.py` | `generic_automation/core/project_config.py` | 配置读取、合并、校验 |
| `old_structure/runtime_metadata.py` | `generic_automation/core/runtime_metadata.py` | run context、文件协议、JSON/JSONL 工具 |
| `old_structure/runtime_value_utils.py` | `generic_automation/core/runtime_value_utils.py` | 类型转换、数值和 CSV 工具 |

### 仿真适配与 STAR-CCM+ 层

| 旧位置 | 新位置 | 说明 |
| --- | --- | --- |
| `old_structure/simulation_adapter.py` | `generic_automation/adapters/simulation_adapter.py` | 仿真 backend 门面 |
| `old_structure/starccm_adapter.py` | `generic_automation/adapters/starccm_adapter.py` | STAR-CCM+ 执行适配器 |
| `old_structure/starccm_macro_builder.py` | `generic_automation/starccm/starccm_macro_builder.py` | Java 宏生成 |
| `old_structure/starccm_macro_template.java` | `generic_automation/starccm/starccm_macro_template.java` | Java 宏模板 |
| `old_structure/starccm_log_reader.py` | `generic_automation/starccm/starccm_log_reader.py` | STAR 日志增量读取 |
| `old_structure/starccm_log_parser.py` | `generic_automation/starccm/starccm_log_parser.py` | 日志内容解析 |
| `old_structure/starccm_result_files.py` | `generic_automation/starccm/starccm_result_files.py` | 结果文件约定 |
| `old_structure/starccm_solver_profiling.py` | `generic_automation/starccm/starccm_solver_profiling.py` | solver profiling 输出 |

### monitor 层

| 旧位置 | 新位置 | 说明 |
| --- | --- | --- |
| `old_structure/ai_parameter_generator.py` | `generic_automation/monitor/ai_parameter_generator.py` | 在线 monitor 主循环 |
| `old_structure/ai_case_modifier.py` | `generic_automation/monitor/ai_case_modifier.py` | 参数写回、白名单和握手文件 |
| `old_structure/ai_monitor_outputs.py` | `generic_automation/monitor/ai_monitor_outputs.py` | 监控输出文件辅助逻辑 |

### RL 层

| 旧位置 | 新位置 |
| --- | --- |
| `old_structure/rl_controller.py` | `generic_automation/rl/rl_controller.py` |
| `old_structure/rl_action_space.py` | `generic_automation/rl/rl_action_space.py` |
| `old_structure/rl_observation_state.py` | `generic_automation/rl/rl_observation_state.py` |
| `old_structure/rl_reward_components.py` | `generic_automation/rl/rl_reward_components.py` |
| `old_structure/rl_safety_override.py` | `generic_automation/rl/rl_safety_override.py` |
| `old_structure/rl_manual_rules.py` | `generic_automation/rl/rl_manual_rules.py` |
| `old_structure/rl_runtime_service.py` | `generic_automation/rl/rl_runtime_service.py` |
| `old_structure/rl_trigger.py` | `generic_automation/rl/rl_trigger.py` |
| `old_structure/rl_controller_settings.py` | `generic_automation/rl/rl_controller_settings.py` |
| `old_structure/rl_controller_storage.py` | `generic_automation/rl/rl_controller_storage.py` |
| `old_structure/rl_controller_utils.py` | `generic_automation/rl/rl_controller_utils.py` |
| `old_structure/rl_runtime_registry.py` | `generic_automation/rl/rl_runtime_registry.py` |
| `old_structure/rl_runtime_settings.py` | `generic_automation/rl/rl_runtime_settings.py` |
| `old_structure/residual_metrics.py` | `generic_automation/rl/residual_metrics.py` |

### CLI、脚本和配置

| 旧位置 | 新位置 | 说明 |
| --- | --- | --- |
| `old_structure/run_case.py` | `generic_automation/cli/run_case.py`、`scripts/entrypoints/run_case.py` | 单算例入口实现与兼容脚本 |
| `old_structure/run_monitor_only.py` | `generic_automation/cli/run_monitor_only.py`、`scripts/entrypoints/run_monitor_only.py` | 外部 monitor 入口 |
| `old_structure/run_sweep.py` | `generic_automation/cli/run_sweep.py`、`scripts/entrypoints/run_sweep.py` | 批量运行入口 |
| `old_structure/offline_replay.py` | `generic_automation/cli/offline_replay.py`、`scripts/entrypoints/offline_replay.py` | 离线回放入口 |
| `old_structure/force_param_update.py` | `generic_automation/cli/force_param_update.py`、`scripts/entrypoints/force_param_update.py` | 手动参数更新入口 |
| `old_structure/run_full_pipeline.sh` | `scripts/pipelines/run_full_pipeline.sh` | pipeline shell 脚本 |
| `old_structure/config.yaml` | `configs/config.yaml` | 默认配置 |
| `old_structure/config_rl_build_amg_match_mesh.yaml` | `configs/config_rl_build_amg_match_mesh.yaml` | RL 构建配置 |
| `old_structure/phase2_mesh_only.yaml` | `configs/phase2_mesh_only.yaml` | mesh-only 阶段配置 |
| `old_structure/phase2_solve_only_external_monitor.yaml` | `configs/phase2_solve_only_external_monitor.yaml` | solve-only + 外部 monitor 配置 |
| `old_structure/cases/cases.csv` | `cases/cases.csv` | 批量算例表 |

### 归档内容

旧目录中的运行日志、SLURM 输出、旧结果和验证结果已经按历史版本归档到：

```text
archive/legacy/generic_automation_v09_2stage/
```

这类文件不再作为当前源码结构的一部分参与开发，但保留用于复盘历史实验、结果对比和排错。

## 5. 运行方式变化

推荐使用统一入口：

```bash
python ga.py case --config configs/config.yaml
python ga.py monitor --config configs/config.yaml
python ga.py sweep --config configs/config.yaml --cases cases/cases.csv
python ga.py replay --case-dir /path/to/case_dir
```

兼容历史脚本形式：

```bash
python scripts/entrypoints/run_case.py --config configs/config.yaml
python scripts/entrypoints/run_monitor_only.py --config configs/config.yaml
python scripts/entrypoints/run_sweep.py --config configs/config.yaml --cases cases/cases.csv
python scripts/entrypoints/offline_replay.py --case-dir /path/to/case_dir
```

开发代码时优先 import 包路径，例如：

```python
from generic_automation.core.project_config import load_config
from generic_automation.adapters.simulation_adapter import SimulationAdapter
from generic_automation.monitor.ai_parameter_generator import AIParameterGenerator
```

不要再依赖旧式根目录平铺 import，例如：

```python
from project_config import load_config
```

## 6. 新增能力与结构补强

相较于 `old_structure/`，当前结构还新增了几类工程化支撑：

- `tests/`：覆盖配置 schema、流控 smoke、schedule generator 等基础行为。
- `examples/`：放可运行示例，降低新用户上手成本。
- `flow_control/`：独立的流控数据 schema、schedule 生成、mock plant、结果分析与校验能力。
- `docs/`：集中放环境搭建、结构优化、模块总览、case schema 和专题说明。
- `archive/`：隔离历史项目与运行产物，避免污染当前源码视图。

## 7. 改造收益

本次结构改造带来的直接收益：

1. 可维护性更高：同一职责的代码集中在一个包内，定位问题更快。
2. import 更稳定：使用 `generic_automation.*` 绝对包路径，减少运行目录变化导致的导入失败。
3. 入口更清晰：`ga.py` 统一承接 case、monitor、sweep、replay 等命令。
4. 运行产物更可控：历史日志和结果进入 archive，当前运行产物不再混入源码根层。
5. 测试和示例更容易扩展：新增功能可以自然补测试、补示例、补文档。
6. 后续支持多后端更容易：`adapters/` 与 `starccm/` 的边界为未来接入其他仿真 backend 留出空间。

## 8. 后续建议

- 清理当前工作区中的 `__pycache__/`，避免缓存文件进入版本控制视野。
- 为 `ga.py` 和 `scripts/entrypoints/` 增加 CLI smoke test，确保兼容入口长期可用。
- 在 README 中把推荐命令统一更新为 `python ga.py ...`。
- 为 `archive/legacy/...` 添加简短索引，说明每个历史版本对应的实验阶段。
- 若后续继续增加仿真 backend，将公共接口放在 `generic_automation/adapters/`，backend 专属逻辑单独建包。

## 9. 可视化图片生成 Prompt

如果需要用图片解释结构变化，可把下面 prompt 交给 GPT 或图像生成工具：

```text
Create a clean technical infographic showing a Python engineering project restructuring.

Canvas: 16:9 horizontal, white background, crisp vector-like style, no decorative gradients.
Title: "Project Structure Refactor: Flat Scripts to Modular Package".

Left side labeled "Before: old_structure/":
- Show one large folder containing many mixed files: run_case.py, run_sweep.py, config.yaml, rl_controller.py, starccm_adapter.py, starccm_macro_template.java, slurm logs, results.
- Convey that code, configs, CLI scripts, logs, and results are mixed together.

Right side labeled "After: current repository":
- Show a structured tree:
  ga.py
  generic_automation/
    core/
    adapters/
    starccm/
    monitor/
    rl/
    cli/
  scripts/
    entrypoints/
    pipelines/
  configs/
  cases/
  docs/
  tests/
  examples/
  archive/
- Use arrows from representative old files to their new groups:
  project_config.py -> core
  starccm_* -> starccm
  ai_* -> monitor
  rl_* -> rl
  run_*.py -> cli and scripts/entrypoints
  *.yaml -> configs
  logs/results -> archive

Add three small callouts on the bottom:
1. Clear responsibility boundaries
2. Stable package imports
3. Cleaner operations and historical archive

Style: restrained engineering diagram, black and dark gray text, light gray folder blocks, one orange accent arrow, readable labels, no people, no 3D, no cartoon style.
```

