# Week2 STAR-CCM+ 模块重构说明

## 1. 当前目标

这次重构的目标不是把旧 STAR-CCM+ 代码复制给 `flow_control`，而是把 STAR-CCM+ 相关能力收成一个统一模块：

```text
starccm/
  control/
  runtime/
```

然后两条业务线各自只保留自己的 adapter 和 translator：

```text
generic_automation/
  adapters/starccm_adapter.py
  starccm_translator.py

flow_control/
  adapters/starccm_adapter.py
  starccm_translator.py
```

这样 `generic_automation` 和 `flow_control` 都能接 STAR-CCM+，但不会维护两套 STAR 底层代码。

## 2. 当前目录关系

核心目录现在是：

```text
flow_control/
generic_automation/
starccm/
  control/
  runtime/
configs/
```

职责划分：

```text
flow_control/
  喷气调度、mock plant、喷气控制数据 schema、喷气业务 adapter/translator。

generic_automation/
  原 STAR-CCM+ 自动化主线：case、sweep、monitor、RL 调求解器参数。

starccm/control/
  STAR 控制契约层。定义 JET、载荷点、report 名称、结果映射。

starccm/runtime/
  STAR 执行运行层。定义 runtime command、macro builder、Java macro 模板、
  日志解析、结果收集、solver profiling。

configs/
  业务配置和共享系统配置。
```

根目录下不再保留：

```text
starccm_control/
starccm_runtime/
```

新代码应从统一模块导入：

```python
from starccm.control import StarCCMControlLayer
from starccm.runtime import StarCCMCommandPlan
from starccm.runtime.starccm_macro_builder import build_macro
```

## 3. `starccm/control` 负责什么

位置：

```text
starccm/control/
  __init__.py
  control_spec.py
  controller.py
  result_mapper.py
```

这一层回答的是：

```text
我们要控制什么？
每个控制量在 STAR-CCM+ 里叫什么？
STAR report 结果如何变成 flow_control 的标准 timeseries 行？
```

已经固定的输入列：

```text
JET_01 ... JET_24
```

已经固定的 6 个载荷输出列：

```text
Fz_S1L
Fz_S1R
Fz_S2L
Fz_S2R
Fz_S3L
Fz_S3R
```

默认 report 映射：

```text
Fz_S1L -> fc_load_S1L
Fz_S1R -> fc_load_S1R
Fz_S2L -> fc_load_S2L
Fz_S2R -> fc_load_S2R
Fz_S3L -> fc_load_S3L
Fz_S3R -> fc_load_S3R
```

默认喷口命名规则：

```text
JET_03
  boundary_name = fc_jet_03
  profile_name  = fc_jet_03_mass_flow
```

这一层不启动 STAR，也不解析日志。它只是统一名字、字段和结果契约。

## 4. `starccm/runtime` 负责什么

位置：

```text
starccm/runtime/
  __init__.py
  commands.py
  starccm_macro_builder.py
  starccm_macro_template.java
  starccm_result_files.py
  starccm_log_reader.py
  starccm_log_parser.py
  starccm_solver_profiling.py
```

这一层回答的是：

```text
拿到 STAR runtime command 后，怎么生成 macro？
STAR 跑完后，怎么读日志、读 report、整理 profiling？
```

`commands.py` 里定义了通用 runtime command：

```text
SetBoundaryProfile   设置边界/profile，例如喷气口质量流量
SetSolverParameter   设置求解器参数，例如 relaxation 或 AMG cycle
SetReportBinding     绑定 report 和 part，例如 6 个载荷 report
RunIterations        推进固定迭代步
RunTimeWindow        推进一个物理/控制时间窗口
ReadReports          读取指定 STAR reports
StarCCMCommandPlan   一组有序命令及其 metadata
```

这些命令是业务无关的。喷气控制和原求解器优化都先翻译成这些命令，再交给 STAR runtime。

当前 runtime 已经承接了原来 `generic_automation/starccm/` 里的底层能力：

```text
生成 AutoSetupMacro.java
读取 Java macro template
解析 logs/starccm.log
收集 result_reports.csv
写 result.json
整理 profiling/solver_profiling.csv
写 profiling summary
```

`generic_automation/starccm/*.py` 现在只是旧路径兼容 wrapper，真实实现已经在 `starccm/runtime/`。

## 5. `generic_automation` 的 STAR 接入

相关文件：

```text
generic_automation/adapters/starccm_adapter.py
generic_automation/starccm_translator.py
generic_automation/starccm/*.py
```

`generic_automation/starccm_translator.py` 负责把原来的 CFD case 求解器参数翻译成 runtime command：

```text
Case
  -> SetSolverParameter
  -> RunIterations
  -> ReadReports
```

典型参数：

```text
pressure_relaxation_factor
pressure_amg_cycle
velocity_amg_cycle
max_iterations
report_names
```

`generic_automation/adapters/starccm_adapter.py` 负责真实启动 STAR-CCM+：

```text
1. 解析 template_sim / input_sim。
2. 写 starccm_control_context.json。
3. 写 starccm_runtime_plan.json。
4. 调 starccm.runtime.starccm_macro_builder.build_macro()。
5. 生成 AutoSetupMacro.java。
6. 拼 starccm+ -batch 命令。
7. 启动 STAR-CCM+。
8. 写 logs/starccm.log。
9. 收集 report。
10. 写 result.json。
```

## 6. `flow_control` 的 STAR 接入

相关文件：

```text
flow_control/adapters/starccm_adapter.py
flow_control/starccm_translator.py
```

`flow_control/starccm_translator.py` 负责把单个喷气控制窗口翻译成 runtime command：

```text
JET_01...JET_24
  -> 24 个 SetBoundaryProfile
  -> 6 个 SetReportBinding
  -> RunTimeWindow
  -> ReadReports
```

示例：

```text
JET_03 = 0.025

翻译成：
SetBoundaryProfile(
  boundary_name="fc_jet_03",
  profile_name="fc_jet_03_mass_flow",
  value=0.025
)
```

`flow_control/adapters/starccm_adapter.py` 是喷气控制业务 adapter。它目前负责：

```text
1. 读取 actuation_schedule.csv。
2. 优先使用 cmd_massflow_01..24 作为真实质量流量命令。
3. 如果没有 cmd_massflow_*，兼容使用 JET_01..24 数值。
4. 按窗口调用 FlowControlStarCCMTranslator。
5. 将多个窗口展平成一个 StarCCMCommandPlan。
6. 写 starccm_runtime_plan.json。
```

这一步已经把 `flow_control` 接到了 STAR runtime command 层，但还没有让 Java macro 真正执行喷气 runtime plan。

## 7. Mock 全流程仍然保留

`flow_control` 当前仍然可以完整跑 mock plant：

```text
actuation config
  -> actuation_schedule.csv
  -> MockDynamic24x6
  -> timeseries.csv
  -> quality_report.json
  -> figures/
```

入口：

```text
examples/run_mock_dynamic24x6.py
```

6 个动作模式可以这样跑：

```bash
for name in no_jet_reference pulse_singlejet step_singlejet chirp_keyjets prbs_demo pilot_sparse24; do
  PYTHONPATH=. .venv/bin/python examples/run_mock_dynamic24x6.py \
    --actuation-config "configs/${name}.yaml" \
    --config configs/mock_dynamic24x6.yaml \
    --schedule-out "runs/mock_full_${name}/actuation_input" \
    --out "runs/mock_full_${name}"
done
```

说明：

```text
actuation_input/actuation_heatmap.svg
  画的是 JET_01..24 开关状态。

actuation_input/total_mass_flow_curve.svg
  画的是总质量流量。

figures/input_heatmap.svg
  画的是 mock plant 实际看到的有效喷气输入。
```

例如 `chirp_keyjets` 的 key jets 会长期处于开启状态，所以开关热图看起来是连续开启；正弦/啁啾变化在 `cmd_massflow_*` 和有效输入图里。

## 8. 共享系统参数

共享系统参数已经抽到：

```text
configs/system.yaml
```

当前内容：

```yaml
system:
  random_seed: 20260702
```

统一加载器：

```text
flow_control/config.py
```

加载规则：

```text
configs/system.yaml 作为默认系统参数。
具体业务 config 覆盖系统参数。
```

所以默认情况下：

```text
actuation config
  读取 system.random_seed

mock_dynamic24x6 config
  读取 system.random_seed
```

但仍可局部覆盖：

```yaml
actuation:
  random_seed: 20260618

mock_dynamic24x6:
  random_seed: 20260703
```

当前保留的局部覆盖：

```text
configs/pilot_sparse24.yaml
  保留 actuation.random_seed: 20260618，避免旧 sparse24 结果变化。

configs/mock_dynamic24x6.yaml
  保留 mock_dynamic24x6.random_seed: 20260703，保持 mock plant 原 seed。
```

也可以通过环境变量切换共享系统参数文件：

```bash
FLOW_CONTROL_SYSTEM_CONFIG=configs/my_system.yaml \
PYTHONPATH=. .venv/bin/python examples/run_mock_dynamic24x6.py ...
```

## 9. 为什么这样分层

最终分层是：

```text
业务线
  flow_control
  generic_automation

STAR-CCM+ 共用模块
  starccm/control
  starccm/runtime

共享运行参数
  configs/system.yaml
```

这样做的好处：

```text
1. `flow_control` 不复制 `generic_automation` 的 STAR 底层代码。
2. 两条业务线都能复用同一套 STAR 命名、report、runtime command。
3. 喷气控制和求解器参数优化的 action/reward/observation 不会混在一起。
4. 后续接真实 STAR-CCM+ 时，只需要补 runtime 执行命令，不需要重写 flow_control schema。
5. 随机种子等复现参数集中到系统配置，实验更容易复现。
```

## 10. 当前完成状态

已经完成：

```text
1. 新增统一 STAR 模块 starccm/。
2. 将 control 契约移动到 starccm/control/。
3. 将 runtime 执行组件移动到 starccm/runtime/。
4. 删除根目录 starccm_control/ 和 starccm_runtime/。
5. 保留 generic_automation/starccm/*.py 作为旧业务线兼容 wrapper。
6. 新增 flow_control/adapters/starccm_adapter.py。
7. flow_control 已能从 actuation_schedule.csv 生成 starccm_runtime_plan.json。
8. 新增 configs/system.yaml 和 flow_control/config.py。
9. actuation/mock 配置支持共享 system.random_seed。
10. mock 全流程仍可运行。
```

当前测试：

```bash
PYTHONPATH=. .venv/bin/pytest
```

最近结果：

```text
33 passed
```

## 11. 还没有完成的部分

还没有完成的是 Java macro 对 flow-control runtime plan 的真实执行。

下一步应做：

```text
1. 在 starccm/runtime 的 macro 生成或 Java macro 中读取 starccm_runtime_plan.json。
2. 将 SetBoundaryProfile 映射为 STAR-CCM+ 边界/profile 赋值。
3. 将 SetReportBinding 映射为 ForceReport 创建或绑定。
4. 将 RunTimeWindow 映射为真实物理时间窗口推进。
5. 将 ReadReports 读回的结果映射到 flow_control timeseries schema。
```

目标链路：

```text
flow_control actuation_schedule.csv
  -> flow_control/adapters/starccm_adapter.py
  -> flow_control/starccm_translator.py
  -> starccm_runtime_plan.json
  -> starccm/runtime Java macro
  -> STAR-CCM+
  -> STAR reports
  -> flow_control timeseries.csv
```

到这一步完成后，`flow_control` 就可以从 mock plant 切到真实 STAR-CCM+ 后端。
