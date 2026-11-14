// Decision evidence: historical tendencies and current prediction stay separate.
let evidenceHour=null,evidenceLoading=false,evidenceError='',evidenceDirection='out';
const ReviewEvidence={
  hour(clock,selected){return selected??(Number.isFinite(clock)?Math.floor(clock/3600)%24:new Date().getHours());},
  aggregate(p){
    const hours=Array.from({length:24},(_,hour)=>({hour,borrows:0,returns:0,inventory:0,groups:{}}));
    for(const r of p.rows){const h=hours[r.hour];h.borrows+=r.borrows;h.returns+=r.returns;h.inventory+=r.inventory;h.groups[r.function_type]=(h.groups[r.function_type]||0)+r.inventory;}
    return hours;
  },
  flows(p,hour){const rows=p.rows.filter(r=>r.hour===hour);return {out:rows.filter(r=>r.net_outflow>0).sort((a,b)=>b.net_outflow-a.net_outflow).slice(0,3),into:rows.filter(r=>r.net_outflow<0).sort((a,b)=>a.net_outflow-b.net_outflow).slice(0,3)};}
};
if(typeof module!=='undefined')module.exports=ReviewEvidence;
function evidenceChart(hours,hour){
  const max=Math.max(1,...hours.flatMap(h=>[h.borrows,h.returns]));
  const x=h=>44+h/23*620,y=v=>176-v/max*144;
  const path=key=>hours.map((h,i)=>(i?'L':'M')+x(h.hour).toFixed(1)+' '+y(h[key]).toFixed(1)).join(' ');
  return `<svg class="evidence-chart" viewBox="0 0 700 216" role="img" aria-label="全天成功借出与归还次数趋势；当前选择${hour}点"><text x="6" y="25">${max}</text><text x="20" y="180">0</text>${[.5,1].map(n=>`<line x1="44" x2="664" y1="${y(max*n)}" y2="${y(max*n)}" stroke="#e5e8ec"/>`).join('')}<line x1="44" x2="664" y1="176" y2="176" stroke="#d0d6de"/>${[0,6,12,18,23].map(h=>`<text x="${x(h)}" y="204" text-anchor="middle">${h}:00</text>`).join('')}<path d="${path('borrows')}" stroke="#2769ad"/><path d="${path('returns')}" stroke="#8496a8" stroke-dasharray="6 4"/><line x1="${x(hour)}" x2="${x(hour)}" y1="28" y2="176" stroke="#566272" stroke-dasharray="3 4"/><circle cx="${x(hour)}" cy="${y(hours[hour].borrows)}" r="4" fill="#2769ad"/></svg>`;
}
function evidenceFlow(rows,into=false){
  const max=Math.max(1,...rows.map(r=>Math.abs(r.net_outflow)));
  return rows.length?rows.map(r=>`<div class="evidence-flow"><span>${esc(r.name)}</span><span class="evidence-flow-track"><i style="width:${Math.abs(r.net_outflow)/max*100}%" class="${into?'into':''}"></i></span><strong>${Math.abs(r.net_outflow)} 次</strong></div>`).join(''):'<p class="review-muted">这个时段没有净流动记录。</p>';
}
function evidenceHistory(m=null){
  if(!patternData)return `<section class="evidence-history"><h3>借还统计</h3><p role="status">${esc(evidenceError||'正在读取借还统计…')}</p>${evidenceError?'<button data-evidence-retry>重新读取统计</button>':''}</section>`;
  const h=ReviewEvidence.hour(m?.clock_s,evidenceHour),hours=ReviewEvidence.aggregate(patternData),selected=hours[h],flow=ReviewEvidence.flows(patternData,h),peak=hours.reduce((a,b)=>b.borrows>a.borrows?b:a);
  const different=m&&(h!==ReviewEvidence.hour(m.clock_s,null)||patternData.day_index!==Math.floor(m.clock_s/86400));
  const grouped=Object.entries(selected.groups).sort((a,b)=>b[1]-a[1]),sum=selected.inventory;
  return `<section class="evidence-history"><div class="evidence-heading"><div><h3>借还统计</h3><p>第${patternData.day_index+1}日 · 全天历史模拟，非当前预测</p></div><label>查看时段<select data-evidence-hour aria-label="统计参照小时">${hours.map(r=>`<option value="${r.hour}" ${h===r.hour?'selected':''}>${String(r.hour).padStart(2,'0')}:00—${r.hour===23?'次日00':String(r.hour+1).padStart(2,'0')}:00</option>`).join('')}</select></label></div>
    ${different?`<p class="evidence-time-warning" role="status">参照时段与调度实例不同（${simTime(m.clock_s)}）。<button data-evidence-align>对齐实例小时</button></p>`:''}<div class="evidence-layout"><section class="evidence-trend"><p class="evidence-takeaway">${peak.borrows?`借出高峰 ${peak.hour}:00 · 所选时段借出 ${selected.borrows} 次`:'当日暂无成功借出记录'}</p><div class="evidence-key"><span><i></i>成功借出</span><span><i></i>归还</span><span>次 / 小时</span></div>${evidenceChart(hours,h)}<p class="review-muted">仅统计成功骑行。</p></section><section class="evidence-flows"><div class="evidence-flow-switch" role="group" aria-label="净流动方向"><button data-evidence-direction="out" aria-pressed="${evidenceDirection==='out'}">净流出</button><button data-evidence-direction="into" aria-pressed="${evidenceDirection==='into'}">净流入</button></div><h4>${h}:00 · ${evidenceDirection==='out'?'净流出':'净流入'}前三处</h4>${evidenceFlow(flow[evidenceDirection],evidenceDirection==='into')}<p class="review-muted">借还差值，非缺车数量。</p></section></div>
    <details class="evidence-distribution"><summary>${h}:00 整点库存分布</summary><div class="evidence-stockbar" role="img" aria-label="${esc(grouped.map(([k,v])=>(kinds[k]||k)+' '+v+'辆').join('，'))}">${grouped.map(([k,v],i)=>`<span style="flex:${v};background:var(--stock-${i%5})" title="${esc(kinds[k]||k)} ${v}辆"></span>`).join('')}</div><div class="evidence-stocklabels">${grouped.map(([k,v],i)=>`<span><i style="background:var(--stock-${i%5})"></i>${esc(kinds[k]||k)} <strong>${v}辆</strong></span>`).join('')}</div><p class="review-muted">仅停放车辆合计 ${sum} 辆，不含骑行中或调度车载货；这是整点快照，不代表整小时持续有车。</p></details>
    <details class="evidence-method"><summary>数据口径</summary><p class="evidence-boundary">全天历史参照，含实例当前时刻之后的记录；仅供回顾，不参与当前预测。</p></details></section>`;
}
const beforeEvidenceDiagnosis=reviewDiagnosis;
reviewDiagnosis=function(m){return `<section class="evidence-live">${beforeEvidenceDiagnosis(m)}</section>`+evidenceHistory(m);};
const beforeEvidenceReview=renderManagerReview;
renderManagerReview=function(){
  beforeEvidenceReview();const root=$('[data-results-body]');
  $('[data-results-view]>h2').textContent='调度工作台';
  $('.scope-note').textContent='校园模拟数据';
  const names={diagnosis:'数据研判',draft:'建议与修改',trial:'效果模拟',execution:'执行复盘'};
  root.querySelectorAll('[data-review-step]').forEach(b=>b.textContent=names[b.dataset.reviewStep]);
  const hour=root.querySelector('[data-evidence-hour]');if(hour)hour.onchange=e=>{evidenceHour=Number(e.target.value);renderManagerReview();};
  const align=root.querySelector('[data-evidence-align]');if(align)align.onclick=()=>{evidenceHour=null;renderManagerReview();};
  root.querySelectorAll('[data-evidence-direction]').forEach(b=>b.onclick=()=>{evidenceDirection=b.dataset.evidenceDirection;renderManagerReview();});
  const retry=root.querySelector('[data-evidence-retry]');if(retry)retry.onclick=()=>{evidenceError='';loadDecisionEvidence();};
  if(!patternData&&!evidenceError&&reviewMode==='diagnosis')loadDecisionEvidence();
};
async function loadDecisionEvidence(){
  if(evidenceLoading)return;evidenceLoading=true;
  try{const controller=new AbortController(),timer=setTimeout(()=>controller.abort(),8000);let r;try{r=await fetch('/api/patterns',{signal:controller.signal});if(!r.ok)throw Error('http');const p=await r.json();if(p.rows?.length!==3408||p.nodes?.length!==142)throw Error('incomplete');patternData=p;}finally{clearTimeout(timer);}}
  catch(e){evidenceError='历史统计暂不可用；可继续核查当前预测，或重新读取。';}
  finally{evidenceLoading=false;if(reviewMode==='diagnosis'&&!$('[data-results-view]').hidden)renderManagerReview();}
}
