// A single manager workflow; saved statistics are evidence, not a dispatch command.
reviewMode='diagnosis';
let reviewEdits=null;
const DecisionReview={
  risks(points){return points.filter(p=>p.status==='shortage').sort((a,b)=>b.shortage-a.shortage);},
  edit(tasks,values){return tasks.map((t,i)=>{
    const q=Number(values[i].quantity),delay=Number(values[i].delay);
    if(!Number.isInteger(q)||q<1||q>t.quantity||!Number.isInteger(delay)||delay<0||delay>30)throw new Error('数量须为建议上限内的正整数，推迟时间须为0—30分钟的整数。');
    return {...t,quantity:q,departure_s:t.departure_s+delay*60};
  });}
};
if(typeof module!=='undefined')module.exports=DecisionReview;
function reviewTaskState(m){
  const tasks=m?.tasks||[],done=['completed','cancelled','cancelled_delivered'];
  const active=tasks.filter(t=>!done.includes(t.status));
  return {active,waiting:active.filter(t=>t.status==='confirmed').length,
    moving:active.filter(t=>t.status!=='confirmed').length,
    completed:tasks.filter(t=>t.status==='completed').length,
    cancelled:tasks.filter(t=>['cancelled','cancelled_delivered'].includes(t.status)).length};
}
function reviewTaskSummary(m){
  const s=reviewTaskState(m);
  return `<div class="ops-status" aria-label="任务进度"><span>待出发 <strong>${s.waiting}</strong></span><span>执行中 <strong>${s.moving}</strong></span><span>已完成 <strong>${s.completed}</strong></span>${s.cancelled?`<span>已取消 <strong>${s.cancelled}</strong></span>`:''}</div>`;
}
function reviewOpenTasks(){reviewBack();decisionStage='plan';renderDecisionFocus();}
function reviewNextAction(m){
  const active=reviewTaskState(m).active.length;
  return `<div class="review-actions">${active?'<button class="primary" data-ops-tasks>查看任务与推进模拟</button>':m?.forecast_available&&m?.plan?.tasks?.length?'<button class="primary" data-decision-start>审阅并修改方案</button>':'<button data-ops-tasks>查看地图与资源</button>'}<span>${active?'已有确认任务；可查看进度，或取消后重新规划。':!m?.forecast_available?'预测不可用，恢复后重新检查':m?.plan?.tasks?.length?'待模拟草稿 · 尚未预留车辆':'暂无可行新方案'}</span></div>`;
}
function reviewRiskTiming(p){
  if(!Number.isFinite(p.first_shortage_minute))return '<span class="ops-incoming">风险时段未提供</span>';
  const t=p.first_shortage_minute;
  return `<span class="ops-incoming">预计 ${Math.max(0,Math.floor(t/15)*15-15)}—${Math.ceil(t/15)*15} 分钟附近出现缺口（区间估计）</span>`;
}
function reviewIncoming(p,m){
  const tasks=reviewTaskState(m).active.filter(t=>t.destination===p.node_id);
  return tasks.length?`<span class="ops-incoming">已安排 ${tasks.reduce((a,t)=>a+t.quantity,0)} 辆 · 最晚卸毕 ${esc(simTime(Math.max(...tasks.map(t=>t.finish_s))))}</span>`:'';
}
function reviewDiagnosis(m){
  if(!m)return '<h3>暂时无法读取调度状态</h3><p>恢复连接后再制定方案。</p>';
  const risks=DecisionReview.risks(liveSnapshot.points),top=risks,max=Math.max(1,...top.map(p=>p.shortage));
  const metrics=m.metrics||{},total=metrics.potential||0;
  return `<div class="decision-reading"><h3>${!m.forecast_available?'预测不可用':risks.length?`${risks.length} 处缺车风险 · ${esc(top[0].name)} 缺口最大`:'暂无明显缺车风险'}</h3><p>${simTime(m.clock_s)} · 调度实例</p></div>
    ${!m.forecast_available?`<p class="review-status">${esc(m.forecast_error||'请检查计算状态；已保存统计不能代替当前预测。')}</p>`:`<h4>预计缺口</h4>${top.length?`<div class="decision-evidence">${top.map(p=>`<button data-review-location="${esc(p.id)}"><span>${esc(p.name)}${reviewIncoming(p,m)}${reviewRiskTiming(p)}</span><span class="decision-bar"><i style="width:${p.shortage/max*100}%"></i></span><strong>剩余缺口 ${shortageCount(p.shortage)} 辆</strong></button>`).join('')}</div><p class="review-muted">计入已确认调运时序后的窗口内缺口；卸毕前仍可能缺车。</p>`:'<p class="review-muted">暂无调运建议。</p>'}`}
    ${reviewTaskSummary(m)}${reviewNextAction(m)}
    <details class="decision-history"><summary>已发生的借车情况${total?` · 未满足 ${whole(metrics.unmet)} 次 / 需求 ${whole(total)} 次`:' · 尚无累计需求'}</summary>${reviewOutcome({...metrics,pending_walks:m.pending_walks},'当前实例累计')}${reviewLocations(m.unmet_locations||[])}<p class="review-muted">累计未满足次数，非当前缺口。</p></details>`;
}
function reviewDraft(m){
  if(!m)return '<p>暂时无法读取方案。</p>';
  if(reviewEdits?.version!==m.state_version){reviewEdits={version:m.state_version,tasks:structuredClone(planDraft?.version===m.state_version?planDraft.tasks:m.plan.tasks),values:null};}
  const tasks=reviewEdits.tasks;
  return `<h3>调运方案</h3><p class="review-muted">${simTime(m.clock_s)} 起，比较同一批未来60分钟模拟需求。修改后需重新模拟。</p>${tasks.length?tasks.map((t,i)=>`<section class="decision-edit"><h4>${esc(t.origin_name)} → ${esc(t.destination_name)}</h4><p>${esc(t.reason||'按库存保留量与运输约束生成')} · 原定出发 ${simTime(t.departure_s)}</p><div><label>搬运数量（辆）<input aria-label="任务${i+1}搬运数量" data-review-quantity="${i}" type="number" min="1" max="${t.quantity}" value="${reviewEdits.values?.[i]?.quantity??t.quantity}"></label><label>推迟出发（分钟）<input aria-label="任务${i+1}推迟分钟" data-review-delay="${i}" type="number" min="0" max="30" value="${reviewEdits.values?.[i]?.delay??0}"></label></div></section>`).join(''):`<p class="review-status">${esc(m.plan.reason||'当前没有可行方案')}。</p>${reviewTaskSummary(m)}${reviewNextAction(m)}`}<p data-draft-error role="alert"></p><div class="review-actions">${tasks.length?`<button class="primary" data-decision-simulate ${managerBusy||!m.forecast_available?'disabled':''}>${managerBusy?'正在模拟…':'模拟此方案'}</button>`:''}<button data-decision-return>返回统计</button></div>`;
}
const beforeDecisionReview=renderManagerReview;
renderManagerReview=function(){
  const requestedMode=reviewMode;
  beforeDecisionReview();
  reviewMode=requestedMode;
  const root=$('[data-results-body]'),m=liveSnapshot?.manager;
  $('#review-plan').hidden=reviewMode!=='trial';
  $('#review-execution').hidden=reviewMode!=='execution';
  const patterns=root.querySelector('#review-patterns');if(patterns)patterns.hidden=reviewMode!=='patterns';
  for(const [selector,label] of [['#review-plan','模拟与调整'],['#review-execution','执行后复盘'],['#review-patterns','历史借还规律']]){const panel=root.querySelector(selector);if(panel){panel.removeAttribute('aria-labelledby');panel.setAttribute('aria-label',label);}}
  $('.scope-note').textContent='校园模拟数据';
  root.querySelector('.review-tabs').remove();
  const steps=document.createElement('nav');steps.className='decision-steps';steps.setAttribute('aria-label','调度决策流程');
  steps.innerHTML=[['diagnosis','判断问题'],['draft','制定方案'],['trial','模拟与调整'],['execution','执行后复盘']].map(([key,label])=>`<button data-review-step="${key}" aria-current="${reviewMode===key?'step':'false'}">${label}</button>`).join('');root.prepend(steps);
  for(const [key,body] of [['diagnosis',reviewDiagnosis(m)],['draft',reviewDraft(m)]]){
    const panel=document.createElement('section');panel.className='review-sheet';panel.dataset.decisionPanel=key;panel.hidden=reviewMode!==key;panel.innerHTML=body;root.insertBefore(panel,root.querySelector('#review-plan'));
  }
  function select(mode){reviewMode=mode;renderManagerReview();}
  function draft(){if(historical)openDispatchRun();select('draft');}
  root.querySelectorAll('[data-review-step]').forEach(b=>b.onclick=()=>b.dataset.reviewStep==='draft'?draft():select(b.dataset.reviewStep));
  root.querySelectorAll('[data-decision-start]').forEach(b=>b.addEventListener('click',draft));
  root.querySelectorAll('[data-ops-tasks]').forEach(b=>b.onclick=reviewOpenTasks);
  root.querySelector('[data-decision-return]')?.addEventListener('click',()=>select('diagnosis'));
  const evidence=root.querySelector('.decision-history');
  if(evidence&&patterns){const historyButton=document.createElement('button');historyButton.className='review-link';historyButton.textContent='查看全天借还规律';historyButton.onclick=()=>{select('patterns');loadPatterns();};evidence.append(historyButton);}
  root.querySelectorAll('[data-review-quantity],[data-review-delay]').forEach(input=>input.oninput=()=>{trialDraftDirty=true;reviewEdits.dirty=true;reviewEdits.values=reviewEdits.tasks.map((_,i)=>({quantity:root.querySelector(`[data-review-quantity="${i}"]`).value,delay:root.querySelector(`[data-review-delay="${i}"]`).value}));});
  root.querySelectorAll('[data-review-location]').forEach(b=>b.onclick=()=>reviewBack(b.dataset.reviewLocation));
  const simulate=root.querySelector('[data-decision-simulate]');if(simulate)simulate.onclick=async()=>{
    const values=reviewEdits.tasks.map((_,i)=>({quantity:root.querySelector(`[data-review-quantity="${i}"]`).value,delay:root.querySelector(`[data-review-delay="${i}"]`).value}));
    const error=root.querySelector('[data-draft-error]');
    try{const tasks=DecisionReview.edit(reviewEdits.tasks,values);reviewEdits.values=values;simulate.disabled=true;simulate.textContent='正在模拟…';const result=await managerAction({action:'trial',tasks,state_version:m.state_version,horizon_minutes:60});if(result){reviewEdits.dirty=false;renderManagerReview();}else{select('draft');$('[data-draft-error]').textContent=$('.error').textContent||'模拟未完成，请检查方案。';}}
    catch(e){error.textContent=e.message;}
  };
  // Keep the strategy comparison, but correction now stays inside this workflow.
  const adjust=root.querySelector('#review-plan [data-review-map]');if(adjust){const hasPlan=!!m?.plan?.tasks?.length;adjust.textContent=trialResult?'修改方案并重新模拟':hasPlan?'审阅并修改方案':'查看任务与资源';adjust.onclick=trialResult||hasPlan?draft:reviewOpenTasks;}
  if(reviewEdits?.dirty){const adopt=root.querySelector('[data-review-adopt]');if(adopt){adopt.disabled=true;adopt.textContent='草稿已修改，请重新模拟';}}
};
