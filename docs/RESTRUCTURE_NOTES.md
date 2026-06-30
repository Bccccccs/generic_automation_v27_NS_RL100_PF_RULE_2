# 目录结构重构说明

本文档记录本次模块化重构对项目目录和路径引用的调整。

## 1. 重构目标

本次重构的目标是让项目符合更清晰的模块化编程结构：

- 根目录保持简洁，只保留入口脚本、README、依赖文件、配置/文档/数据/结果目录。
- Python 业务代码统一放入 `generic_automation/` 包。
- 按职责拆分 core、adapter、STAR-CCM+、monitor、RL、CLI。
- 保留原有命令入口，避免用户必须改变常用运行命令。
- 保留 `article/` 目录作为参考文献目录。
- 检查并修复配置路径、import 路径、Java macro 模板路径等引用问题。

## 2. 新目录结构

```text
.
├── article/
├── cases/
├── configs/
├── docs/
├── generic_automation/
│   ├── adapters/
│   ├── cli/
│   ├── core/
│   ├── monitor/
│   ├── rl/
│   └── starccm/
├── logs/
├── results/
├── results_validation/
├── archive/
├── ga.py
├── README.md
├── requirements.txt
└── scripts/
```

## 3. Python 包结构

### `generic_automation/core/`

核心模型和通用基础设施：

- `adapter_base.py`: `Case` 数据模型。
- `project_config.py`: YAML/JSON 配置加载、校验和 `Case` 解析。
- `runtime_metadata.py`: run context、文件协议、mesh cache key、JSON/JSONL 工具。
- `runtime_value_utils.py`: 安全类型转换、数值工具、CSV 输出工具。

### `generic_automation/adapters/`

仿真后端适配层：

- `simulation_adapter.py`: backend 门面。
- `starccm_adapter.py`: STAR-CCM+ 启动、宏生成、命令执行、结果收集。

### `generic_automation/starccm/`

STAR-CCM+ 专属逻辑：

- `starccm_macro_builder.py`
- `starccm_macro_template.java`
- `starccm_log_reader.py`
- `starccm_log_parser.py`
- `starccm_result_files.py`
- `starccm_solver_profiling.py`

### `generic_automation/monitor/`

在线监控和参数写回：

- `ai_parameter_generator.py`
- `ai_case_modifier.py`
- `ai_monitor_outputs.py`

### `generic_automation/rl/`

强化学习控制器相关逻辑：

- `rl_controller.py`
- `rl_action_space.py`
- `rl_observation_state.py`
- `rl_reward_components.py`
- `rl_safety_override.py`
- `rl_manual_rules.py`
- `rl_runtime_service.py`
- `rl_trigger.py`
- `rl_controller_settings.py`
- `rl_controller_storage.py`
- `rl_controller_utils.py`
- `rl_runtime_registry.py`
- `rl_runtime_settings.py`
- `residual_metrics.py`

### `generic_automation/cli/`

命令行入口实现：

- `run_case.py`
- `run_monitor_only.py`
- `run_sweep.py`
- `offline_replay.py`
- `force_param_update.py`

第一次重构时，根目录中的同名脚本是兼容 wrapper，例如：

```python
from generic_automation.cli.run_case import main
```

因此第一次重构后，原来的运行方式仍可使用：

```bash
python run_case.py --config configs/config.yaml
python run_monitor_only.py --config configs/config.yaml
python run_sweep.py --config configs/config.yaml --cases cases/cases.csv
```

第二次结构优化后，兼容 wrapper 已进一步移动到 `scripts/entrypoints/`，
根目录推荐使用统一入口：

```bash
python ga.py case --config configs/config.yaml
python ga.py monitor --config configs/config.yaml
python ga.py sweep --config configs/config.yaml --cases cases/cases.csv
```

详见 `docs/STRUCTURE_OPTIMIZATION_ROUND2.md`。

## 4. 配置文件调整

配置文件从根目录移动到：

```text
configs/
```

包括：

- `configs/config.yaml`
- `configs/config_rl_build_amg_match_mesh.yaml`
- `configs/phase2_mesh_only.yaml`
- `configs/phase2_solve_only_external_monitor.yaml`

因为 `project_config.py` 会把相对路径按配置文件所在目录解析，所以配置移动后同步修复了相对路径：

- `result_root: results` 改为 `result_root: ../results`
- `result_root: results_validation` 改为 `result_root: ../results_validation`
- `input_sim: results/...` 改为 `input_sim: ../results/...`

同时修复了默认 `configs/config.yaml` 中原本无法通过解析的网格参数：

```yaml
surface_mesh_size: 0.08
min_surface_size: 0.06
```

代码要求 `surface_mesh_size > min_surface_size`，原配置中两者均为 `0.08`，会导致 `parse_case()` 直接报错。

## 5. 文档和日志调整

文档移动到：

```text
docs/
```

主要文件：

- `docs/project_module_overview.md`
- `docs/B01 有代码审计报告.md`
- `docs/ENVIRONMENT_SETUP.md`
- `docs/RESTRUCTURE_NOTES.md`

历史运行日志移动到：

```text
logs/
```

参考文献目录统一为：

```text
article/
```

该目录用于放置论文、外部研究资料和项目参考文献，不放运行代码。

## 6. 路径引用修复

本次重构修复了以下路径引用：

- Python import 从扁平导入改成包内绝对导入，例如：

```python
from adapter_base import Case
```

改为：

```python
from generic_automation.core.adapter_base import Case
```

- CLI 默认配置路径从：

```text
config.yaml
```

改为：

```text
configs/config.yaml
```

- `run_sweep.py` 当时调用根目录 wrapper：

```text
run_case.py
```

避免从 `generic_automation/cli/` 内部错误定位脚本。

第二次结构优化后，`run_sweep.py` 已改为调用包入口：

```text
python -m generic_automation.cli.run_case
```

- `run_full_pipeline.sh` 第一次重构时默认配置路径改为：

```bash
CONFIG_PATH="${1:-$SCRIPT_DIR/configs/config.yaml}"
```

第二次结构优化后，脚本移动到 `scripts/pipelines/`，默认配置路径改为按项目根目录解析：

```bash
CONFIG_PATH="${1:-$PROJECT_DIR/configs/config.yaml}"
```

- `starccm_macro_builder.py` 和 `starccm_macro_template.java` 放在同一目录下，仍使用：

```python
Path(__file__).with_name("starccm_macro_template.java")
```

因此 Java 模板引用保持有效。

## 7. 验证结果

已完成以下验证。

### Python 编译检查

```bash
find generic_automation -name '*.py' -print | sort | \
  xargs .venv/bin/python -m py_compile \
  ga.py scripts/entrypoints/run_case.py scripts/entrypoints/run_monitor_only.py \
  scripts/entrypoints/run_sweep.py scripts/entrypoints/offline_replay.py \
  scripts/entrypoints/force_param_update.py
```

结果：通过。

### CLI 加载检查

```bash
.venv/bin/python ga.py case --help
.venv/bin/python ga.py monitor --help
.venv/bin/python ga.py sweep --help
```

结果：通过。

### 配置解析检查

```bash
.venv/bin/python - <<'PY'
from pathlib import Path
from generic_automation.core.project_config import load_config, parse_case, resolve_case_dir

for path in sorted(Path("configs").glob("*.yaml")):
    cfg = load_config(path)
    case = parse_case(cfg)
    print(path, case.case_name, case.run_mode, resolve_case_dir(path.resolve(), cfg, case.case_name))
PY
```

结果：四个 YAML 配置均可解析：

- `configs/config.yaml`
- `configs/config_rl_build_amg_match_mesh.yaml`
- `configs/phase2_mesh_only.yaml`
- `configs/phase2_solve_only_external_monitor.yaml`

### 未执行真实 STAR-CCM+

本次未启动真实 STAR-CCM+ 仿真，因为当前环境是否具备 STAR-CCM+ 可执行文件、许可证和 `.sim` 输入文件取决于集群/服务器环境。

本次验证范围是：

- Python import 是否正确。
- CLI 是否可加载。
- 配置是否能被解析。
- 相对路径是否仍解析到项目根目录下的 `results/` 和 `results_validation/`。

## 8. 后续建议

- 如果后续要打包安装，可补充 `pyproject.toml`。
- 如果继续扩展喷气控制，建议在 `generic_automation/` 下新增 `jet/` 子包，而不是把喷气逻辑混进现有 solver RL action space。
- 运行真实 STAR-CCM+ 前，应确认 `configs/*.yaml` 中的 `starccm_path`、`template_sim`、`input_sim` 在目标机器上存在。
