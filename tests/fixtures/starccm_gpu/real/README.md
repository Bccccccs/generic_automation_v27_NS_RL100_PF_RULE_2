# 真实脱敏 GPU 日志（real）—— 当前为空，BLOCKED

本目录用于存放**真实 STAR-CCM+ 20.02 GPU 运行**的脱敏日志。它是
`parse_gpu_log()` 生产注册表 `REGISTERED_LOG_PATTERNS` 的唯一合法来源。

对应阻塞项：`docs/gpu/20.02-evidence-register.md` 的 **B-07**。

## 目前为什么是空的

本次改造在没有目标 Linux 计算节点、没有 20.02 可执行文件、没有 GPU 的环境下完成
（取证机器是 macOS arm64，`starccm+` / `nvidia-smi` / `scontrol` 均不存在）。因此：

- 没有采集到任何真实 20.02 日志；
- 生产注册表为空，`parse_gpu_log()` 对任何 build 都返回
  `UNVERIFIED_BUILD_NOT_REGISTERED`；
- GPU run 即使 STAR 退出码为 0，也只会得到 `FAILED / GPU_EXECUTION_UNCONFIRMED`。

这是有意设计：**不允许凭猜测编写 Siemens 日志句式让 GPU run 通过**。

## 解除 B-07 时需要放进来的东西

1. 一次受控 smoke run 的原始日志（G0 资格批准后执行）。
2. 脱敏处理：删除 PoD 密钥、许可证服务器地址、完整环境变量、用户主目录、
   主机真实名称（如需保留，替换为 `gpu01` 之类占位并注明已替换）。
3. 一个同名 `.md` 说明文件，记录：
   - 完整 STAR build（例如 `20.02.007`）与精度；
   - 采集日期、节点类型、GPU 厂商/型号/数量；
   - 使用的 `-gpgpu` 选择器与 rank 数；
   - 哪些行构成 GPU solver 执行证据、哪些行构成回退/OOM/模型不支持证据。
4. 据此在 `starccm/runtime/gpu_evidence.py` 的 `REGISTERED_LOG_PATTERNS` 里登记
   该 build 的句式，并补充以本目录文件为输入的测试。

## 关于文件后缀

仓库 `.gitignore` 第 26 行忽略 `*.log`。放入真实日志时要么使用 `.txt` 后缀，
要么为本目录单独添加 `.gitignore` 例外规则；后者属于本次白名单之外的改动，
需要单独授权。合成样本已统一使用 `.txt`。

## 禁止事项

- 不要把 `../synthetic/` 里的文件复制或改名到这里。
- 不要从其他 STAR 版本（例如 2406 镜像文档）移植句式后当作 20.02 证据。
- 不要用 `nvidia-smi` 利用率或日志里出现 `GPU` 字样当作执行证据。
- 未登记句式的 build 一律按未知处理，宁可判未确认，也不能误判通过。
