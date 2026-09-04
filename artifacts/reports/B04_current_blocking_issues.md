# B04 当前阻塞问题

本文件由真实数据质量检查自动生成。画图成功不代表数据通过，缺失字段未补 0。

## b52_no

- [阻塞 / force_definition_errors] 同名 Jet_Reaction_Z report 在无喷气基准中仍非零，不能作为喷气动量反作用力；若为 J 表面压力/剪切合力，必须使用不同名称
- [阻塞 / force_definition_errors] 无喷气算例缺少独立的喷气动量反作用力字段；缺失字段不能补 0
- [需浩坤判断 / physical_questions_for_haokun] 无喷气基准存在明显漂移，请浩坤判断启动段、收敛性和取样区间
- [需浩坤判断 / physical_questions_for_haokun] 无喷气基准存在异常跳变，请浩坤判断求解稳定性或数据导出

## training

- [警告 / time_errors] timeseries 是动作表的连续前缀，按提前结束处理；不阻塞，但不得当成完整运行
- [阻塞 / force_definition_errors] 同名 Jet_Reaction_Z report 在无喷气基准中仍非零，不能作为喷气动量反作用力；若为 J 表面压力/剪切合力，必须使用不同名称

## validation

- [阻塞 / force_definition_errors] 同名 Jet_Reaction_Z report 在无喷气基准中仍非零，不能作为喷气动量反作用力；若为 J 表面压力/剪切合力，必须使用不同名称
