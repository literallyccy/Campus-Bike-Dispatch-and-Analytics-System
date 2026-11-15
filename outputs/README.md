# 模型与实验

本目录保存模型及实验结果，请保留目录结构。

- dispatch_models_v2/：借还模型、便携推理JSON、训练指标和冻结测试预测。
- citibike_model_v1/：Citi Bike已有模型、指标及测试预测。
- dispatch_profiles_v2/、temporal_replay_v2/、business_patterns_v2/：风险画像、逐时回放与历史统计。
- dispatch_v2_round/：四策略正式模拟结果。
- forecast_diagnostics_v2_round_retry/：冻结预测诊断。
- benchmark_v2_round_*/：三类查询性能摘要与计划。
- bigdata_round_import/：UTM最终状态、事件日志及失败记录。
- work_records/：历史工作量记录。

14日模拟评价中，不调度、固定、历史、模型策略未满足次数分别为3084、3032、1639、1552。逐日负改善保留。冻结预测仅用于诊断，不据此再次调参。

UTM证据来自一台虚拟机内的两个工作容器；本包不包含虚拟机镜像或运行数据库。
