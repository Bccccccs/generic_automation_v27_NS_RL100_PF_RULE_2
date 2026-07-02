# Week2 STAR-CCM+ 控制与 Runtime 分层重构说明

## 1. 重构目标

本次重构的目标是把项目中与 STAR-CCM+ 相关的能力拆成三层：

```text
业务线 translator
    -> STAR-CCM+ 通用 runtime 命令
    -> STAR-CCM+ runtime 执行层
```

这样 `flow_control` 和 `generic_automation` 可以各自保留业务逻辑，但把翻译后的通用命令交给同一个 STAR-CCM+ runtime 执行。

## 2. 当前目录关系

当前核心目录关系：

```text
flow_control/
generic_automation/
starccm_control/
starccm_runtime/
```

职责划分：

```text
flow_control/
  喷气调度、mock plant、喷气控制数据 schema、喷气业务到 STAR 命令的翻译

generic_automation/
  原求解器参数优化业务、原 RL、原 STAR adapter 调用链

starccm_control/
  两条线共用的 STAR 控制契约
  包括 24 个 jet 名称、6 个载荷点名称、report 映射、结果行映射

starccm_runtime/
  两条线共用的 STAR runtime 层
  包括通用命令模型、macro builder、Java macro template、日志解析、结果收集和 solver profiling
```

## 3. 已抽象出的共用部分

### 3.1 STAR 控制契约

位置：

```text
starccm_control/
```

主要文件：

```text
starccm_control/control_spec.py
starccm_control/controller.py
starccm_control/result_mapper.py
```

已经固定：

```text
JET_01 ... JET_24
Fz_S1L Fz_S1R Fz_S2L Fz_S2R Fz_S3L Fz_S3R
```

6 个载荷点对应 STAR-CCM+ report：

```text
Fz_S1L -> fc_load_S1L
Fz_S1R -> fc_load_S1R
Fz_S2L -> fc_load_S2L
Fz_S2R -> fc_load_S2R
Fz_S3L -> fc_load_S3L
Fz_S3R -> fc_load_S3R
```

### 3.2 STAR runtime 命令模型

位置：

```text
starccm_runtime/
```

主要文件：

```text
starccm_runtime/commands.py
```

已经定义的通用命令：

```text
SetBoundaryProfile   设置边界/profile，例如喷气口质量流量
SetSolverParameter   设置求解器参数，例如 relaxation 或 AMG cycle
SetReportBinding     绑定 report 和 part，例如 6 个载荷 report
RunIterations        推进固定迭代步
RunTimeWindow        推进一个物理/控制时间窗口
ReadReports          读取指定 STAR reports
StarCCMCommandPlan   一组有序命令及其 metadata
```

这些命令是业务无关的。喷气线和原求解器线都可以生成它们。

### 3.3 STAR runtime 执行组件

旧的 STAR-CCM+ 底层执行组件已迁移到顶层：

```text
starccm_runtime/starccm_macro_builder.py
starccm_runtime/starccm_macro_template.java
starccm_runtime/starccm_result_files.py
starccm_runtime/starccm_log_reader.py
starccm_runtime/starccm_log_parser.py
starccm_runtime/starccm_solver_profiling.py
```

这些组件负责：

```text
生成 AutoSetupMacro.java
读取 Java macro template
解析 starccm.log
收集 STAR reports
写 result/profiling 输出
整理 solver profiling
```

旧路径仍保留兼容 wrapper：

```text
generic_automation/starccm/starccm_macro_builder.py
generic_automation/starccm/starccm_result_files.py
generic_automation/starccm/starccm_log_reader.py
generic_automation/starccm/starccm_log_parser.py
generic_automation/starccm/starccm_solver_profiling.py
```

这些 wrapper 只转发到 `starccm_runtime`，不再承载真实实现。

## 4. 两条线各自保留的 translator

### 4.1 喷气控制 translator

位置：

```text
flow_control/starccm_translator.py
```

作用：

```text
喷气窗口命令
  -> 24 个 SetBoundaryProfile
  -> 6 个 SetReportBinding
  -> RunTimeWindow
  -> ReadReports
```

示例逻辑：

```text
JET_03 = 1.0
JET_07 = 0.5

翻译成：
SetBoundaryProfile(fc_jet_03, fc_jet_03_mass_flow, 1.0)
SetBoundaryProfile(fc_jet_07, fc_jet_07_mass_flow, 0.5)
RunTimeWindow(duration=...)
ReadReports(fc_load_S1L ... fc_load_S3R)
```

### 4.2 原求解器优化 translator

位置：

```text
generic_automation/starccm_translator.py
```

作用：

```text
Case 求解器参数
  -> SetSolverParameter
  -> RunIterations
  -> ReadReports
```

示例逻辑：

```text
pressure_relaxation_factor = 0.31
pressure_amg_cycle = 1

翻译成：
SetSolverParameter(pressure_relaxation_factor, 0.31)
SetSolverParameter(pressure_amg_cycle, 1)
RunIterations(check_interval)
ReadReports(drag, total, train_surface_pressure_max, ...)
```

## 5. 现在没有共用的部分

下面这些仍然不共用，也不应该直接共用：

```text
generic_automation/rl/
  原求解器参数 RL
  动作是 relaxation、AMG cycle 等数值参数

flow_control/
  喷气调度、MockPlant、喷气数据分析
  后续喷气 RL 应单独放在 flow_control/rl/ 或 jet_rl/
```

原因是两条线的 RL 控制对象不同：

```text
原 RL：控制求解器数值参数
喷气 RL：控制 24 路喷气执行器
```

它们的 action、observation、reward 都不同。

## 6. Runtime 迁移状态

本次已经完成旧 STAR-CCM+ runtime 的顶层迁移。

现在 canonical runtime 路径是：

```text
starccm_runtime/
```

原 adapter 已改为从顶层 runtime 导入：

```text
starccm_runtime.starccm_macro_builder
starccm_runtime.starccm_result_files
```

原 monitor 也已改为从顶层 runtime 导入：

```text
starccm_runtime.starccm_log_reader
starccm_runtime.starccm_log_parser
```

同时，adapter 会写出：

```text
starccm_control_context.json
starccm_runtime_plan.json
```

这两个文件是后续让 Java macro 消费 runtime plan 的稳定中间产物。

## 7. 为什么这样分层

这样做后，每层只负责自己的事情：

```text
flow_control
  只关心喷气业务如何变成 STAR 命令

generic_automation
  只关心求解器优化业务如何变成 STAR 命令

starccm_control
  统一名字、report、结果列

starccm_runtime
  统一 runtime 命令模型，并承载 macro builder、日志解析、report 收集等 STAR 执行组件
```

好处：

```text
1. 喷气控制和原求解器优化不会互相污染。
2. 两条线都能使用同一套 STAR 命名和结果格式。
3. 后续从 mock 切到真实 STAR-CCM+ 时，不需要重写数据 schema。
4. Java macro 后续只需要消费 runtime plan，而不是理解业务逻辑。
5. STAR-CCM+ 底层执行代码已从 `generic_automation/` 解耦，喷气线后续可以直接调用顶层 runtime。
```

## 8. 本次新增/修改文件

新增：

```text
starccm_runtime/__init__.py
starccm_runtime/commands.py
starccm_runtime/starccm_macro_builder.py
starccm_runtime/starccm_macro_template.java
starccm_runtime/starccm_result_files.py
starccm_runtime/starccm_log_reader.py
starccm_runtime/starccm_log_parser.py
starccm_runtime/starccm_solver_profiling.py
flow_control/starccm_translator.py
generic_automation/starccm_translator.py
tests/test_starccm_runtime_translators.py
tests/test_starccm_runtime_imports.py
docs/week2/STARCCM_RUNTIME_REFACTOR.md
```

接入/修改：

```text
generic_automation/adapters/starccm_adapter.py
generic_automation/monitor/ai_parameter_generator.py
generic_automation/starccm/*.py 兼容 wrapper
flow_control/data_schema.py
flow_control/run_mock_demo.py
tests/test_starccm_control_layer.py
```

## 9. 检查结果

已运行：

```bash
.venv/bin/python -m pytest \
  tests/test_starccm_runtime_imports.py \
  tests/test_starccm_control_layer.py \
  tests/test_starccm_runtime_translators.py \
  tests/test_case_schema.py \
  tests/test_actuation_schedule_generator.py \
  tests/test_flow_control_smoke.py \
  tests/test_flow_control_isolation.py
```

结果：

```text
18 passed
```

## 10. 下一步建议

下一步建议不再是搬目录，而是让 Java macro 逐步消费 runtime plan：

```text
业务 translator
  -> StarCCMCommandPlan
  -> starccm_runtime macro builder
  -> STAR-CCM+
```

建议顺序：

```text
1. 在 macro builder 中读取 starccm_runtime_plan.json。
2. 将 SetBoundaryProfile 映射为 STAR boundary/profile 设置。
3. 将 SetReportBinding 映射为 ForceReport 创建/绑定。
4. 将 RunTimeWindow / RunIterations 映射为真实推进逻辑。
5. 将 ReadReports 映射回标准 timeseries.csv。
```
