"""Render checked error diagnostics for the course evidence and manager review."""
import argparse
import json
import os
from pathlib import Path
from prepare_forecast_inputs import sha256_file

ROOT=Path(__file__).resolve().parents[1]
NAMES={'campus_borrow':'校园模拟借车','campus_return':'校园模拟还车','citibike_borrow':'真实 Citi Bike JC 借车'}


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source',type=Path,default=ROOT/'outputs/forecast_diagnostics_v1')
    parser.add_argument('--document',type=Path,default=ROOT/'docs/预测可靠性分层分析.md')
    args=parser.parse_args();meta=json.loads((args.source/'analysis.json').read_text())
    verification=json.loads((args.source/'full_validation.json').read_text())
    if verification.get('passed') is not True:raise ValueError('Require independent verification before reporting')
    os.environ.setdefault('MPLCONFIGDIR',str(ROOT/'tmp/matplotlib'))
    import matplotlib
    matplotlib.use('Agg')
    import matplotlib.pyplot as plt
    plt.rcParams.update({'font.family':'DejaVu Sans','font.size':10,'axes.spines.top':False,'axes.spines.right':False})
    fig,axes=plt.subplots(1,2,figsize=(12,4.8),layout='constrained')
    keys=list(meta['runs']);labels=['Campus borrow (simulated)','Campus return (simulated)','Citi Bike JC borrow (real)']
    for i,key in enumerate(keys):
        entry=next(r for r in meta['runs'][key]['day_block_bootstrap'] if r['block_days']==meta['settings']['primary_block_length'])
        value=entry['mae_gain'];low,high=entry['percentile_95_interval']
        axes[0].errorbar(value,i,xerr=[[value-low],[high-value]],fmt='o',color='#1269bc',capsize=5)
        axes[0].annotate(f'{value:.3f} [{low:.3f}, {high:.3f}]',(value,i),xytext=(0,13),textcoords='offset points',ha='center',fontsize=9)
    axes[0].axvline(0,color='#8d959c',linewidth=1)
    axes[0].set_yticks(range(3),labels);axes[0].set_ylim(-.65,2.6)
    axes[0].set_xlim(-.01,.16);axes[0].set_xlabel('Baseline MAE minus forest MAE (count / node-hour)')
    axes[0].set_title('A  Paired error comparison',loc='left',fontweight='bold',pad=18)
    source={r['bucket']:r for r in meta['runs']['campus_borrow']['volume_slices']}
    bands=['zero','one_to_four','at_least_five'];positions=list(range(3))
    for shift,field,label,color in [(-.18,'model_mae','Forest','#1269bc'),(.18,'baseline_mae','Weekly baseline','#99a6b3')]:
        values=[source[b][field] for b in bands]
        axes[1].bar([x+shift for x in positions],values,width=.34,label=label,color=color)
        for x,value in zip(positions,values):
            axes[1].text(x+shift,value+.08,f'{value:.3f}',ha='center',fontsize=9)
    axes[1].set_xticks(positions,[f'Actual = 0\nn = {source[bands[0]]["rows"]:,}',
        f'Actual = 1-4\nn = {source[bands[1]]["rows"]:,}',f'Actual >= 5\nn = {source[bands[2]]["rows"]:,}'])
    axes[1].set_ylim(0,5.9);axes[1].set_ylabel('MAE (count / node-hour)')
    axes[1].set_title('B  Campus borrow by observed volume',loc='left',fontweight='bold',pad=18)
    axes[1].legend(frameon=False,loc='upper left')
    fig.suptitle('Frozen held-out predictions: accuracy varies by operating condition',fontsize=14,fontweight='bold')
    fig.supxlabel('14 days per run. A: 3-day circular blocks, 4,000 resamples, percentile 95% intervals. B: diagnostic label strata only.',fontsize=9)
    figure=args.source/'forecast_reliability.png';fig.savefig(figure,dpi=180);plt.close(fig)
    text=['# 预测可靠性分层分析','','更新：2026-11-04。使用已冻结的测试集预测，新增本机 Spark SQL 诊断及独立对账，没有重新训练或根据测试结果调参。','',
        '## 管理者应如何理解结果','',
        '三组模型的整体 MAE 均低于各自历史基线，但模型在实际零订单时段的误差更大，高订单量时段仍有低估。调度需要结合当前库存、借还方向及运输约束，不能只根据平均预测误差决定搬车。',
        '',f'![预测误差比较与校园借车分层](../{figure.relative_to(ROOT).as_posix()})','',
        '图 A 的横轴是“基线 MAE − 模型 MAE”，正值代表模型误差较小。区间是误差差值的重采样区间。图 B 使用测试标签划分订单量，不能在预测时提前知道所属组。','',
        '## 整体与逐日表现','',
        '| 数据与目标 | 样本数 | 零订单占比 | 模型 / 基线 MAE | 模型误差较低的天数 | 3天块误差改善区间 |',
        '|---|---:|---:|---:|---:|---:|']
    for key in keys:
        run=meta['runs'][key];overall=run['overall'];primary=next(r for r in run['day_block_bootstrap'] if r['block_days']==meta['settings']['primary_block_length'])
        low,high=primary['percentile_95_interval']
        text.append(f'| {NAMES[key]} | {overall["rows"]:,} | {100*overall["zero_rows"]/overall["rows"]:.2f}% | {overall["model_mae"]:.4f} / {overall["baseline_mae"]:.4f} | {run["daily_model_wins"]} / {run["days"]} | [{low:.4f}, {high:.4f}] |')
    text+=['','MAE 单位为次/节点小时。校园还车使用昨日同期基线，其余使用上周同期。Citi Bike JC 保留原城市和站点身份，不代表交大预测精度。',
        '', '## 不能被整体均值掩盖的误差','',
        '| 数据与目标 | 实际订单量 | 节点小时数 | 模型 / 基线 MAE | 模型平均偏差 |',
        '|---|---|---:|---:|---:|']
    band_names={'zero':'0','one_to_four':'1—4','at_least_five':'≥5'}
    for key in keys:
        for row in sorted(meta['runs'][key]['volume_slices'],key=lambda r:bands.index(r['bucket'])):
            text.append(f'| {NAMES[key]} | {band_names[row["bucket"]]} | {row["rows"]:,} | {row["model_mae"]:.3f} / {row["baseline_mae"]:.3f} | {row["model_bias"]:+.3f} |')
    text+=['','平均偏差 = 预测 − 实际，负值表示平均低估。模型对零订单时段有小量正预测，因此即使整体误差下降，也可能形成多余的风险提示。高订单量组的低估说明热点管理仍须关注风险，不能将模型当作精确需求表。','',
        '校园借车累计低估较多的小时为 '+ '、'.join(r['bucket']+'时' for r in meta['runs']['campus_borrow']['worst_underprediction_hours'])+'；这是测试期成功订单误差描述，不是真实缺车时段排名。',
        '', '## Spark SQL 工作量与验证','',
        f'实际读取 3 组 Parquet 预测，共 {sum(r["rows"] for r in meta["provenance"].values()):,} 条。检查非负有限数、唯一节点小时键、整日完整网格、目标时窗与小时一致性。校园记录 LEFT JOIN 节点功能，真实站点不套用校园功能标签。',
        '',f'使用 CTE、CASE、LATERAL VIEW STACK 和 GROUP BY 生成 {meta["slice_rows"]} 组总体、逐日、小时、功能、节点与订单量分层统计；计算 MAE、RMSE、WAPE、偏差和低估量。结果按 run_id / dimension 分区保存，执行计划保留 Exchange / 聚合过程。',
        '', '独立检查器不调用统计实现，使用原始预测 JSON 和 Python math.fsum 重算全部结果，再实际读取输入与输出 Parquet 逐项核对。485 组统计和 9 组重采样区间全部一致。','',
        '## 区间计算规则与限制','',
        '每条节点小时记录的配对误差差值为 |基线−实际| − |模型−实际|。Spark 先按天保存误差和及行数。重采样以整天为单位，保留同一天节点和小时的相关性，连续循环抽取日块，截取到原有天数后按行数加权计算 MAE 差。',
        '', '预先指定 1、3、7 天块，主要展示 3 天，4,000 次重复，固定种子 20261104。其余块长结果保留在 analysis.json，没有根据区间是否好看来选择块长。只有14个测试日，日间相关与循环首尾拼接仍是近似。这里不作总体显著性声明，不把区间当成下一小时预测范围或缺车概率。',
        '', '测试标签分组只供事后诊断，不能作为实时预测特征、调度条件或重新选参依据。后续若依据这些发现改模型，需要新的未参与选择的评价时段。成功订单误差也不能量化真实潜在需求或实际缺车。','',
        '## 复现入口','', '在项目根目录、已安装依赖并设置 Java 11 的环境运行，输出目录须为新目录：','',
        '```bash', '.venv/bin/python src/analyze_forecast_errors.py --config config/forecast_diagnostics.json --output outputs/forecast_diagnostics_new',
        '.venv/bin/python src/verify_forecast_errors.py --output outputs/forecast_diagnostics_new --verify-parquet --report outputs/forecast_diagnostics_new/full_validation.json',
        '.venv/bin/python src/build_forecast_diagnostic_report.py --source outputs/forecast_diagnostics_new --document docs/预测可靠性分层分析_新运行.md', '```','',
        '证据：`outputs/forecast_diagnostics_v1/analysis.json`、`full_validation.json`、`query_plan.txt`、`error_slices/` 与 `worker.log`。本页和图片由检查后的保存结果生成，生成图片不代表再次训练。Linux/UTM 部署继续暂停。']
    args.document.write_text('\n'.join(text)+'\n')
    manifest=dict(kind='Report rendered from checked saved diagnostics, not a new training run',
        inputs={p.name:sha256_file(p) for p in [args.source/'analysis.json',args.source/'full_validation.json']},
        outputs={str(p.relative_to(ROOT)):sha256_file(p) for p in [figure,args.document]})
    (args.source/'report_manifest.json').write_text(json.dumps(manifest,indent=2)+'\n')
    print(json.dumps(manifest,indent=2))


if __name__=='__main__':main()
