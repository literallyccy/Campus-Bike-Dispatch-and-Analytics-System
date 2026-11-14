# 校园共享单车调度分析系统

基于 Apache Spark 的校园共享单车数据处理与调度决策支持系统。系统将订单统计、借还预测、库存风险分析和方案模拟整合到校园地图中，帮助管理者判断何时、从哪里、向哪里调运多少辆单车，并在执行前比较方案效果。

校园场景以上海交通大学闵行校区为背景，订单与需求采用模拟数据。

## 功能

- **运营统计**：按地点、时段和功能区查看借还分布，识别出行潮汐。
- **风险研判**：结合当前库存和未来一小时借还预测，查看缺车风险及地点详情。
- **调运建议**：在车辆载量、搬运预算和保留库存等约束下生成候选任务，支持人工修改。
- **方案模拟**：比较“不新增调运”和当前方案的服务效果、搬运量与作业时间，查看逐地点差异和过程回放。
- **场景管理**：按时段创建独立场景，保存草稿与试算结果；修改方案后，旧试算失效。
- **执行复盘**：确认任务、推进模拟时钟，查看任务进度与累计服务结果。不利方案继续执行时需要填写理由。

## 技术架构

```text
校园模拟订单
    │
    ▼
Spark SQL / DataFrame
清洗、聚合、JOIN、窗口特征
    │
    ▼
日期分区 Parquet ──► MLlib 随机森林 ──► 借还预测与库存风险
    │                                         │
    └──► 历史统计与时段回放                     ▼
                                       调运建议与方案模拟
                                              │
增量事件 ──► Structured Streaming ──► SQLite 状态与任务事务
                                              │
                                              ▼
                                    HTTP 接口与校园地图前端
```

| 模块 | 技术与用途 |
| --- | --- |
| 数据处理 | Spark SQL、DataFrame、GROUP BY、JOIN、Window |
| 分析存储 | Parquet 与日期分区 |
| 借还预测 | Spark MLlib 随机森林、时间留出评价、上周同期基线 |
| 增量处理 | Structured Streaming 文件源、微批处理、检查点恢复 |
| 运行状态 | SQLite 事务、事件幂等、库存守恒 |
| 管理界面 | 原生 JavaScript、校园地图、建筑轮廓与统计图表 |

## 环境要求

- Python 3.10 或 3.11
- Java 11 或 17
- macOS 或 Linux
- Python 依赖见 [requirements.txt](requirements.txt)

启动前确认 Python 和 Java 可用：

```bash
python3 --version
java -version
```

在 macOS 上，若使用 Java 17，请设置 `JAVA_HOME`，避免启动器默认查找 Java 11：

```bash
export JAVA_HOME=$(/usr/libexec/java_home -v 17)
```

## 快速开始

下载仓库后，在项目根目录执行以下命令。

### 1. 安装依赖

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt
```

### 2. 准备数据

```bash
python scripts/prepare_data.py
```

脚本先校验随附文件，再将压缩数据恢复到 `data/simulated/campus_v2/`。已有且内容一致的数据会保留。仓库已包含训练好的借车、还车模型，体验系统无需重新训练。

### 3. 启动系统

```bash
python src/run_initial_version.py \
  --run outputs/local_demo \
  --config config/manager_state_v2.json \
  --port 8871
```

浏览器打开 **[http://127.0.0.1:8871/v1/](http://127.0.0.1:8871/v1/)**。

启动命令同时运行 Spark 流处理和 HTTP 服务。首次启动需要等待 Spark 就绪；终端按 `Ctrl+C` 停止。运行状态保存在 `outputs/local_demo/`，再次使用相同命令可恢复该实例。

如需后台启动，可改用：

```bash
python src/start_local_demo.py \
  --run outputs/local_demo \
  --config config/manager_state_v2.json \
  --port 8871
```

后台方式返回后，服务继续运行。进程信息见 `outputs/local_demo/supervisor.json`。

## 使用流程

1. **选择时段**：查看该时段统计和风险，进入或创建独立调度场景。
2. **查看地点**：在地图中选择建筑，查看库存、借还情况和风险依据。
3. **制定方案**：采用系统建议，或修改调运地点和数量。
4. **模拟比较**：比较方案与不新增调运的未满足次数、搬运辆次和作业分钟。
5. **调整或确认**：效果不理想时返回修改或保留现状；修改后需重新模拟。
6. **执行复盘**：确认后推进模拟时钟，查看任务执行与服务结果。

场景中的“执行”指模拟执行，不会向现实车辆或运营平台下发指令。浏览其他时段展示的是对应参照场景，不代表当前实例的执行历史。

## 目录结构

```text
├── config/                         # 管理场景和调度约束配置
├── dashboard/
│   ├── v1/                         # 管理界面
│   ├── assets/                     # 校园底图等静态资源
│   └── vendor/                     # 前端第三方库
├── data/
│   ├── packed/                     # 压缩的校园 v2 订单与需求
│   └── simulated/                  # 节点、运输拓扑和恢复后的输入
├── outputs/
│   ├── dispatch_models_v2/         # 借车、还车冻结模型
│   ├── dispatch_profiles_v2/       # 时段借还画像
│   ├── temporal_replay_v2/         # 时间回放数据
│   ├── business_patterns_v2/       # 历史统计
│   └── local_demo/                 # 启动后生成的本地状态与日志
├── scripts/prepare_data.py         # 文件校验与数据解压
├── sql/                            # 分析查询
├── src/                            # 服务与数据处理源码
├── FILE_MANIFEST.json              # 随附文件校验清单
└── requirements.txt
```

`data/simulated/campus_v1/` 中的节点与运输图仍由调度器引用，请保留。`outputs/` 中随附的模型、统计和回放文件是运行资源；`outputs/local_demo/` 则是本机启动后生成的状态。

## 数据处理与分析入口

| 文件 | 用途 |
| --- | --- |
| `src/process_campus_spark.py` | 校园订单清洗、节点小时聚合、特征与标签构建 |
| `src/train_campus_forecast.py` | MLlib 借还模型训练与基线评价 |
| `src/export_forecast_runtime.py` | 导出便携推理模型 |
| `src/process_dispatch_profiles_spark.py` | 构建时段借还画像 |
| `src/evaluate_dispatch.py` | 四策略受约束模拟评价 |
| `src/benchmark_processing.py` | CSV、Parquet、分区与查询性能对照 |
| `src/live_map_stream.py` | 增量事件处理与状态更新 |
| `src/scenario_service.py` | 独立场景、草稿、试算与确认 |

各入口的参数可通过 `python src/文件名.py --help` 查看。部分分析脚本默认使用 v1 路径，处理 v2 数据时应显式指定输入、模型和新的输出目录。

## 数据与结果说明

随附校园 v2 数据包含 **227,514 条清洗订单**，其中观察期订单为 **224,290 条**。节点小时表与预测特征分别采用不同口径，不能直接作为订单数比较。

正式四策略模拟评价使用同一14日窗口、共 **61,917 次潜在需求**：

| 策略 | 未满足次数 | 满足率 | 搬运辆次 | 作业分钟 |
| --- | ---: | ---: | ---: | ---: |
| 不调度 | 3,084 | 95.0191% | 0 | 0.00 |
| 固定策略 | 3,032 | 95.1031% | 210 | 1,092.23 |
| 历史策略 | 1,639 | 97.3529% | 730 | 1,172.83 |
| 模型策略 | 1,552 | 97.4934% | 763 | 1,386.23 |

模型策略在该窗口内减少了未满足次数，同时增加了运输投入，并非每一天或每个场景都更好。结果见 `outputs/dispatch_v2_round/comparison.json`。

使用时请注意：

- 校园订单、潜在需求和调度收益均为模拟结果，不能当作交大实际运营结论。
- 模型预测成功借还量，受车辆可用性影响，不能直接代表全部潜在需求。
- 借车预测 MAE 优势的2日块95%区间跨零，现有结果不足以宣称显著改善。
- 库存表示某时刻状态，未满足次数表示窗口累计；两者含义不同。
- 方案比较属于模拟评估，现实执行效果仍需现场数据验证。

## 常见问题

**提示端口被占用**

确认已有实例是否正在运行，或将启动命令的 `--port 8871` 改为其他空闲端口，并使用对应地址访问。

**模型或数据缺失、文件校验失败**

确认已完整下载仓库并执行 `python scripts/prepare_data.py`。不要单独移动模型或修改配置中的模型哈希。若主动修改了源码或配置，校验清单也需要相应维护。

**页面可以打开，但流处理未就绪**

检查 Java 环境，并查看 `outputs/local_demo/stream.log` 和 `server.log`。Spark 驱动与 Python 工作进程应使用同一虚拟环境。

**希望使用新的独立实例**

将 `--run outputs/local_demo` 改为新的目录，例如 `--run outputs/demo_02`。旧目录保留原有状态。

## 数据来源与致谢

- [上海交通大学校园地图](https://map.sjtu.edu.cn/)：校园底图与建筑地理参考。
- [Citi Bike System Data](https://citibikenyc.com/system-data)：真实订单处理链路的数据来源；原始归档未随本仓库提供。
- Apache Spark 及前端第三方库：第三方文件保留原有版权声明，使用与再分发请遵循各自条款。

本项目使用 AI 辅助开发、排障及文档整理。
