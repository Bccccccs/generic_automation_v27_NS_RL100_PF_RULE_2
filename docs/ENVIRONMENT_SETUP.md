# 环境安装说明

本文档说明如何从“没有环境”开始安装本项目的运行环境。

## 1. 环境分层

本项目实际需要两层环境：

1. Python 环境：用于读取配置、生成 STAR-CCM+ Java 宏、监控日志、运行 RL 控制器、整理结果。
2. STAR-CCM+ 环境：用于真正执行 CFD 仿真。

Python 环境可以本地安装；STAR-CCM+ 需要已有软件授权和安装路径，通常在集群或服务器上通过固定路径或 module 加载。

## 2. Python 版本

建议使用 Python 3.10 或更高版本。

检查版本：

```bash
python3 --version
```

## 3. 创建虚拟环境

在项目根目录执行：

```bash
cd /Users/yanbochao/generic_automation_v27_NS_RL100_PF_RULE_2
python3 -m venv .venv
source .venv/bin/activate
```

激活后，命令行前面通常会出现 `(.venv)`。

## 4. 安装 Python 依赖

```bash
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
```

当前代码的第三方 Python 依赖很少，主要是：

- `PyYAML`: 读取 `configs/config.yaml`

其余主要使用 Python 标准库。

## 5. 验证 Python 环境

执行语法检查：

```bash
python -m py_compile \
  run_case.py \
  run_sweep.py \
  starccm_adapter.py \
  simulation_adapter.py \
  ai_parameter_generator.py \
  rl_action_space.py \
  rl_observation_state.py \
  rl_safety_override.py \
  offline_replay.py \
  project_config.py
```

执行配置解析检查：

```bash
python - <<'PY'
from pathlib import Path
from project_config import load_config, parse_case

cfg = load_config(Path("configs/config.yaml"))
case = parse_case(cfg)
print("case_name:", case.case_name)
print("run_mode:", case.run_mode)
print("starccm_path:", case.starccm_path)
PY
```

如果这一步成功，说明 Python 环境已经可用。

## 6. 安装或确认 STAR-CCM+

STAR-CCM+ 不能通过 `pip` 安装，需要已有安装包、许可证和集群/服务器环境。

当前 `configs/config.yaml` 中的路径示例：

```yaml
starccm_path: "/work/home/acn6k38urd/apprepo/starccmplus/17.06.007-none/app/17.06.007-R8/STAR-CCM+17.06.007-R8/star/bin/starccm_wrapper"
```

你需要确认该路径在实际运行机器上存在：

```bash
ls -l "/work/home/acn6k38urd/apprepo/starccmplus/17.06.007-none/app/17.06.007-R8/STAR-CCM+17.06.007-R8/star/bin/starccm_wrapper"
```

或者如果集群使用 module：

```bash
module avail starccm
module load starccm/17.06.007
which starccm+
```

然后把 `configs/config.yaml` 中的 `starccm_path` 改成实际可执行路径。

## 7. 修改配置中的关键路径

至少需要确认这些配置：

```yaml
starccm_path: /path/to/starccm+
template_sim: /path/to/template.sim
num_cores: 128
result_root: results
case_name: case_train_c80_g
```

如果使用 `solve_only` 或 `resume`，还需要确认：

```yaml
case:
  run_mode: solve_only
  input_sim: /path/to/mesh_ready.sim
```

## 8. 不启动 STAR 的检查方式

只检查配置和代码：

```bash
python - <<'PY'
from pathlib import Path
from project_config import load_config, parse_case, resolve_case_dir

config_path = Path("configs/config.yaml").resolve()
cfg = load_config(config_path)
case = parse_case(cfg)
case_dir = resolve_case_dir(config_path, cfg, case.case_name)
print("case_dir:", case_dir)
print("template_sim:", case.template_sim)
print("input_sim:", case.input_sim)
PY
```

## 9. 启动单算例

确认 STAR-CCM+ 路径和 `.sim` 文件都存在后：

```bash
python run_case.py --config configs/config.yaml
```

如果只想启动 STAR，不启动内嵌 RL monitor：

```bash
python run_case.py --config configs/config.yaml --no-monitor
```

## 10. 单独启动 monitor

如果 STAR-CCM+ 在 SLURM 或外部进程中运行，可以单独启动 monitor：

```bash
python run_monitor_only.py --config configs/config.yaml
```

monitor 会读取：

```text
<case_dir>/logs/starccm.log
```

并写入：

```text
<case_dir>/rl/
<case_dir>/profiling/
<case_dir>/experiment_summary.json
```

## 11. 常见问题

### 报错：`ModuleNotFoundError: No module named 'yaml'`

说明没有安装 `PyYAML`。执行：

```bash
source .venv/bin/activate
python -m pip install -r requirements.txt
```

### 报错：找不到 STAR-CCM+

检查 `configs/config.yaml` 中的 `starccm_path` 是否存在并可执行：

```bash
ls -l /path/to/starccm+
```

### 报错：找不到 `.sim`

检查：

```yaml
template_sim: ...
case:
  input_sim: ...
```

`full_run` 和 `mesh_only` 需要 `template_sim`。  
`solve_only` 和 `resume` 需要 `case.input_sim`。

## 12. 推荐最小验证顺序

1. 创建虚拟环境。
2. 安装 `requirements.txt`。
3. 运行 `py_compile`。
4. 运行配置解析检查。
5. 确认 `starccm_path`。
6. 确认 `template_sim` 或 `input_sim`。
7. 先用 `--no-monitor` 跑一次。
8. 再打开 RL monitor。

