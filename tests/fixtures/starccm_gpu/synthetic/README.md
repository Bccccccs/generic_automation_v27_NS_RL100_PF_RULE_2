# 合成 GPU 日志 fixture（synthetic）

**这些文件不是真实 STAR-CCM+ 输出。** 它们由本项目手工编写，只用于验证
`starccm/runtime/gpu_evidence.py` 的状态机与失败优先级。

仓库 `.gitignore` 忽略 `*.log`，因此合成样本使用 `.txt` 后缀，保证可以被提交。

行首统一带 `[SYNTHETIC]` 标记，句式与
`tests/test_starccm_gpu_evidence.py` 里的 `SYNTHETIC_PATTERNS` 一一对应。真实
STAR 日志不会包含这个标记，因此这些文件不可能被误当成真机证据。

| 文件 | 验证的判定 |
|---|---|
| `gpu_solver_confirmed.txt` | 两张卡都有 solver 执行证据 → 可判 `GPU_CONFIRMED` |
| `gpu_device_evidence_missing.txt` | 只有 0 号卡有执行证据 → `GPU_DEVICE_EVIDENCE_MISSING` |
| `gpu_cpu_fallback.txt` | 设备初始化成功但 solver 回退 → `CPU_FALLBACK_DETECTED` |
| `gpu_out_of_memory.txt` | 显存不足 → `GPU_OUT_OF_MEMORY`（优先于回退） |
| `gpu_model_unsupported.txt` | 物理模型不支持 → `GPU_MODEL_UNSUPPORTED` |

## 合成 fixture 不能做什么

- 不能认证真实日志解析器。生产注册表
  `REGISTERED_LOG_PATTERNS` 当前为空，任何 build 的真实日志都只能判
  “未确认”（`UNVERIFIED_BUILD_NOT_REGISTERED`）。
- 不能作为 GPU 求解验收证据。退出码 0、dry-run、Mock 和 Python 测试通过都不构成
  验收。
- 不能用来推导 Siemens 的日志句式。真实句式必须来自
  `../real/` 里的脱敏日志。
