# B53 真实训练数据自动整理与筛选

## 当前交付状态

流程已完成，但当前仓库没有正式训练数据。默认配置的 `sources` 为空，因此运行后：

- `training_dataset.csv`、`validation_dataset.csv` 只有稳定表头，没有伪造样本；
- `B03_data_quality_summary.csv` 明确记录 `NO_DATA`；
- `B03_six_jet_response_summary.csv` 为六个代表喷口各保留一条 `NO_DATA` 记录；
- `B03_anomalous_windows.csv` 只有表头，因为“尚无输入”不是异常动作窗口。

`runs/week4/no_0816`、`j02_pluse_0816` 和 `j06_pluse_0816` 只用于适配数据格式。
三者 B04 均为 `FAIL`，不得登记为正式数据源。

## 输入 Case 契约

每个正式 Case 至少包含：

```text
<case>/actuation_schedule.csv
<case>/processed/timeseries.csv
<case>/case_manifest.yaml            # 推荐，追溯信息会写入数据集
<case>/raw_star/                     # 推荐，保留 STAR 原始导出
```

动作表必须包含 `time/window_id/t_start/t_end`、`JET_01..24` 和
`cmd_massflow_01..24`。时间序列必须包含 `physical_time`、
`actual_massflow_01..24` 和六区力：

```text
Fz_S1L, Fz_S1R, Fz_S2L, Fz_S2R, Fz_S3L, Fz_S3R
```

六区力也兼容 `underbody_lift_s1l..s3r` 输入别名；输出始终使用 `Fz_*` 标准名。
命令质量流量不能代替实际质量流量，缺失值也不会填零。

## 运行

正式数据到位后，优先在 `configs/b53/data_filter.yaml` 中显式登记 Case 及角色：

```yaml
sources:
  - case_dir: runs/b53_real_star/training_run_001
    split: training
  - case_dir: runs/b53_real_star/validation_run_001
    split: validation
```

然后运行：

```bash
python scripts/build_b53_dataset.py \
  --config configs/b53/data_filter.yaml \
  --output-dir artifacts/B53_training_data \
  --require-data
```

也可以临时显式传入 Case；该参数会替代配置中的 `sources`：

```bash
python scripts/build_b53_dataset.py \
  --case-dir runs/b53_real_star/training_run_001::training \
  --case-dir runs/b53_real_star/validation_run_001::validation \
  --output-dir artifacts/B53_training_data \
  --require-data
```

只有显式传入 `--input-root` 才会递归发现 Case，默认绝不扫描历史 `runs/`，以免把
0816、旧副本或错误样例误纳入训练。`--require-data` 会在没有任何通过门禁的窗口时
返回非零，适合正式训练流水线；不加该参数时可成功生成当前空数据交付物。

## 对齐与窗口定义

STAR 样本归属哪个动作窗口由 `case_manifest.yaml` 的 `sample_ownership_rule` 显式声明，
不由行数、文件名或时间列名推断：

| 数据来源 | 语义 | 说明 |
|---|---|---|
| CLI runtime | `right_closed`，即 `(t_start, t_end]` | 宏在写入 row i 的质量流量后推进到 `t_end` 再采样 report，因此 `t_end` 样本归属 `window_id=i` |
| 当前 B52 monitor-only 导出 | `left_closed`，即 `[t_start, t_end)` | 样本时间等于窗口起点时归属该窗口 |
| runtime CSV 自带可信 `window_id` | `embedded` | 直接采信并校验其与样本时间是否矛盾，不重新按时间猜测 |

未声明语义的历史 CLI Case 保持 `right_closed` 兼容，但质量报告的
`time_alignment_mode` 会带上 `_legacy_default` 后缀以示区别。

动作表可以是一窗口一行，时间序列可以是一求解步一行；两者不要求行数相等，也绝不
按行号拼接。解析命中的窗口 `t_start`/`t_end` 会写回 `processed/timeseries.csv` 供
审计，并用 `window_id` 二次核验。样本落在动作表整体跨度之外且超出浮点容差、或落入
区间空洞时按错位处理，不静默 clamp。

连续、同一 `window_id`、同一开启喷口的动作行被合并为一个事件。训练/验证拆分以
完整 STAR Case 为最小组，同一 Case 的窗口和时间点不会跨集合，避免时间泄漏。

## 自动质量门禁

每个动作事件独立评估，并原子放行或剔除：

1. 从动作前无喷气区间截取最后一段局部基准，同时强制剔除 Case 起始最小时长；
2. 六区基准均通过漂移门禁后，以各区基准中位数计算 `delta_Fz_*`；
3. 用实际质量流量确定真实开启/关闭边沿，并检查与动作窗口的最大错位；
4. 每区提取响应延迟、带符号峰值、恢复时间、SNR；
5. 六区至少一个达到配置的 SNR 下限才放行。某个非主响应区较弱不会单独删列；
6. 时间错位、实际质量流量缺失、基准持续漂移或六区响应全部低于噪声时，整个事件
   的所有行都不会进入训练集或验证集。

恢复未在有限观察段内完成会写入异常清单的 `WARNING`，但不会伪造恢复时间，也不会
单独触发用户指定的四类阻断门禁。

## 可追溯性

每条样本保留：

- `source_case_id/source_case_dir/source_raw_star_dir`；
- 源 timeseries、动作表的绝对路径和原始 CSV 行号；
- `action_window_id/event_id`、动作起止时刻与相位；
- manifest 中的 git commit、STAR `.sim` 标识和 SHA-256（若已提供）；
- 原始六区力、基准、基准噪声、六区变化量；
- 24 路动作开关、命令质量流量和实际质量流量。

因此任何训练样本都能回到唯一 STAR Case、动作事件及源数据行。

## 拒绝代码

主要阻断代码如下：

| 代码 | 含义 |
|---|---|
| `TIME_MISALIGNMENT` | 时间非单调、动作区间空洞/重叠、window_id 不符或流量边沿错位 |
| `ACTUAL_MASSFLOW_MISSING` | 24 路实际质量流量列或事件上下文数值缺失 |
| `BASELINE_DRIFT` | 任一区域喷气前基准持续漂移 |
| `RESPONSE_BELOW_NOISE` | 六区最大 SNR 仍低于门禁 |
| `INITIAL_TRANSIENT_OR_SHORT_BASELINE` | 启动剔除后没有足够稳定基准点 |
| `MASSFLOW_NOT_DETECTED` | 有实际流量列，但目标喷口未送达 |
| `SIX_REGION_FORCE_MISSING` | 六区力列或数值不完整 |

所有阈值、观测值、事件起止与区域指标均可在质量摘要或异常窗口清单中审计。
