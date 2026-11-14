# 校园共享单车调度分析系统

## 安装

将代码、数据、模型与实验三个包解压到同一目录，合并同名文件夹。项目目录应同时包含 src、dashboard、config、data 和 outputs。

环境：Python 3.10或3.11，Java 11或17。

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
python scripts/prepare_data.py
python src/start_local_demo.py --run outputs/local_demo --config config/manager_state_v2.json --port 8871
```

打开 http://127.0.0.1:8871/v1/ 。再次运行同一启动命令可恢复已有状态。

## 分析与评价

```bash
python src/process_campus_spark.py --source data/simulated/campus_v2 --output outputs/reproduced_sql --confirm-continuous-coverage
python src/analyze_forecast_errors.py --config config/forecast_diagnostics_v2.json --output outputs/reproduced_diagnostics
python src/evaluate_dispatch.py --source data/simulated/campus_v2 --config config/dispatch_v1.json --models outputs/dispatch_models_v2 --output outputs/reproduced_dispatch
```

输出目录必须新建。校园数据为模拟数据；UTM部署脚本需按自己的集群配置调整。

代码与界面：src/、dashboard/；配置：config/；数据说明：data/README.md；模型与实验说明：outputs/README.md。
