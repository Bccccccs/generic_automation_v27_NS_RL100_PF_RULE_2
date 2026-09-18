"""CLI 命令行入口点包，聚合所有 flow-control 工作流的命令行接口。

子模块（对应具体工作流）：
  - run_starccm:      在 STAR-CCM+ 中执行激励计划
  - train_rom:        训练 ARX 降阶模型
  - use_rom:          使用已训练的 ARX 模型进行预测
  - validate_rom:     验证已训练的 ARX 模型
  - run_mock_dynamic24x6: 运行 24 输入/6 输出 mock plant
  - summarize_single_jet: 单喷气响应汇总（B06 实验）

使用方法示例：
  python -m flow_control.cli.run_starccm --schedule ... --sim ... --out ...
"""
