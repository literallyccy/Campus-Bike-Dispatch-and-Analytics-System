# 数据

本目录保存项目数据。

- packed/：校园v2大文件的gzip归档。
- simulated/campus_v2/：节点、初始车辆、天气及情景配置。
- simulated/campus_v1/：调度器使用的兼容节点与运输图。
- raw/citibike/：2025年1月、2月JC订单原始归档及来源记录。

在项目根目录执行：

```bash
python scripts/prepare_data.py
```

脚本先校验项目文件，再恢复校园v2数据。已有不同内容的文件不会被覆盖。

校园v2为56天模拟数据：成功订单227514条（含预热），观察期224290条。订单包含起终点，不含真实骑行轨迹。

Citi Bike来源：https://citibikenyc.com/system-data
校园底图来源：https://map.sjtu.edu.cn/
