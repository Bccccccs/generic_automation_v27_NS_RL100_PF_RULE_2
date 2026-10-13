# B53 真实训练数据自动整理与筛选

## 当前交付状态

`configs/b53/data_filter.yaml` 已登记 `runs/b52_v2/training`（训练），基准为
`runs/cifu0_nojet_11s`（11 s 无喷气参考，dt=1e-4，与 B52 v2 同模板、同初始条件）。
当前结果：24 个事件放行 23 个，唯一剔除的是 0.5 s 的首个事件——它的喷气前基准
落在 0.5 s 启动剔除段内。`runs/b52_v2/validation` 跑完后在配置中取消注释即可。

`runs/week4` 的 0816 算例仍只用于适配格式，不得登记为正式数据源。

## 基准口径：时间对齐无喷气参考

底部流场本身强非定常：无喷气算例任意 0.1 s 内六区力标准差约 0.9–1.4 kN，
相邻 0.1 s 均值跳动约 1.1 kN，与喷气响应（1–7 kN）同量级。因此：

- 常数基准（如无喷气尾段均值）会把自然脉动当成喷气响应；
- 局部喷气前中位数基准会被漂移门禁大量误杀（同一训练算例只放行 5/24）。

配置 `reference_case` 后，每个样本取参考算例同一物理时刻的六区力：

```text
baseline_mean_Fz_* = Fz_ref(t)
delta_Fz_*         = Fz(t) - Fz_ref(t)
```

喷气前两者几乎重合（0–0.5 s 差值约 10 N）；喷气扰动后流场逐渐分岔，差值留下
约 120 N（S1）、340 N（S2）、450 N（S3）的残差底噪，这是确定性 ROM 不可解释的量级。
参考 Case 必须：不含任何喷口流量；采样步长与数据 Case 一致；覆盖启动剔除后的全部
样本时刻（否则 `REFERENCE_TIME_MISMATCH`）；不能同时登记为训练/验证数据。

参考模式下的质量门禁作用在差值上：漂移的相对限值仍按“3% × 参考力量级”计算；
峰值/SNR/延迟等诊断会先扣除事件前差值的中位数偏置，但写入数据集的 `delta_Fz_*`
不扣偏置，保证同一片段内时序连续。

## 连续片段

相邻放行事件首尾相接（基准 0.1 s + 恢复 0.2 s 恰好铺满 0.3 s 的动作间隔），边界
共享的采样点只保留一次。同一 Case 内时间连续的样本编号为同一 `segment_id`；
被剔除事件会切断片段。B54 只在片段内构造 ARX 滞后项，因此可以直接把
`training_dataset.csv` / `validation_dataset.csv` 作为 ROM 的输入。

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

1. 从动作前无喷气区间截取最后一段基准（参考模式下为差值），同时剔除启动瞬态：
   取 `minimum_initial_discard_s` 与 manifest `initial_transient_crop.end_time_s` 的较大值；
2. 六区基准均通过漂移门禁后计算 `delta_Fz_*`（参考模式：减同一时刻参考力；
   局部模式：减基准中位数）；
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
| `REFERENCE_TIME_MISMATCH` | 参考 Case 采样步长不同或未覆盖本 Case 的全部时刻 |

所有阈值、观测值、事件起止与区域指标均可在质量摘要或异常窗口清单中审计。
