// Presentation of the existing manager contract. No new demand/model computation.
let confirmedTrialReceipt=null;
function reviewTruckAvailability(m){
  const active=reviewTaskState(m).active;
  if(!active.length)return '1 辆调度车 · 当前无任务占用';
  const ends=active.map(t=>t.status==='cancel_empty'?t.pickup_s:t.finish_s);
  return `1 辆调度车 · ${active.length} 项任务占用${ends.every(Number.isFinite)?` · 预计 ${simTime(Math.max(...ends))} 释放`:''}`;
}
function reviewCancelTask(m,id){
  const t=reviewTaskState(m).active.find(t=>t.id===id);if(!t||managerBusy)return;
  const later=reviewTaskState(m).active.filter(x=>x.id!==id&&x.status==='confirmed'&&x.departure_s>=t.departure_s);
  const effect=t.status==='confirmed'?'释放该任务预留车辆':t.status==='to_pickup'?'返回供给点后结束任务':'已开始装运，继续送达原目的地并卸车后结束';
  if(window.confirm(`取消 ${t.origin_name} → ${t.destination_name}？\n${effect}；另取消 ${later.length} 项后续待发任务。重新规划后仍须模拟再确认。`))return managerAction({action:'cancel',task_id:id});
}
function reviewActivePlan(m){
  const active=reviewTaskState(m).active;
  return `<div class="review-section-heading"><h3>已确认方案 · ${active.length} 项任务</h3><p>${simTime(m.clock_s)}</p></div>
    <p class="workbench-resource">${esc(reviewTruckAvailability(m))}</p>
    <div class="workbench-task-list">${active.map(t=>`<article class="workbench-task"><div><strong>${esc(t.origin_name)} → ${esc(t.destination_name)}</strong><span>${t.quantity} 辆 · ${esc(taskLabels[t.status]||t.status)}</span></div><p>出发 ${simTime(t.departure_s)} · 卸毕 ${simTime(t.finish_s)}</p>${t.late_warning?'<p class="review-status">送达可能晚于风险时段，前段缺口仍存在。</p>':''}<details><summary>调整任务</summary><p>取消会同时释放后续待发任务；已装车辆继续安全送达。取消完成后重新规划、模拟和确认。</p><button data-workbench-cancel="${esc(t.id)}" ${managerBusy||t.cancel_requested?'disabled':''}>${t.cancel_requested?'已申请取消':'取消此任务及后续待发任务'}</button></details></article>`).join('')}</div>
    <div class="review-actions"><button class="primary" data-ops-tasks>查看任务与推进模拟</button><span>已确认任务不能原地改数量；先取消并释放资源，再制定新方案。</span></div>
    <details class="workbench-receipt"><summary>确认依据</summary>${confirmedTrialReceipt?`<p>本页面会话最近一次确认 · ${simTime(confirmedTrialReceipt.window_start_s)}—${simTime(confirmedTrialReceipt.window_end_s)} · 来源版本 ${esc(confirmedTrialReceipt.source_state_version)}</p><p>${esc(ReviewMath.verdict(confirmedTrialReceipt.results.no_new_dispatch,confirmedTrialReceipt.results.current_plan).text)}</p><p>这份记录是确认时的试算，不是执行效果；刷新后不保留。</p>`:'<p>本页面没有该次确认的试算记录，不能从当前库存倒推当时收益。可在执行复盘查看实际模拟累计结果。</p>'}</details>`;
}
const workbenchDraft=reviewDraft;
reviewDraft=function(m){return m&&reviewTaskState(m).active.length?reviewActivePlan(m):workbenchDraft(m);};
const workbenchTrial=reviewTrial;
reviewTrial=function(m){
  if(!trialResult&&m&&reviewTaskState(m).active.length)return `<div class="review-empty"><h3>当前方案已确认，任务尚未结束</h3><p>${esc(reviewTruckAvailability(m))}</p><p>如需修改，先查看已确认方案并取消相关任务。取消完成后重新模拟，不直接改写已确认任务。</p><button class="primary" data-workbench-plan>查看已确认方案</button></div>`;
  return workbenchTrial(m);
};
const workbenchExecution=reviewExecution;
reviewExecution=function(m){
  if(m&&!(m.metrics?.potential>0))return `<div class="review-empty"><h3>${m.clock_s===m.initial_clock_s?'模拟尚未推进':'当前时段暂无借车需求'}</h3><p>${simTime(m.initial_clock_s)}—${simTime(m.clock_s)} · 不能计算满足率或判断调度收益。</p>${reviewTaskSummary(m)}${m.metrics?.moved?reviewCost(m.metrics):''}<button class="primary" data-ops-tasks>查看任务与推进模拟</button></div>`;
  return workbenchExecution(m);
};
const workbenchAction=managerAction;
managerAction=async function(body){
  const receipt=body.action==='confirm'&&trialResult?structuredClone(trialResult):null;
  const result=await workbenchAction(body);
  if(result&&receipt)confirmedTrialReceipt=receipt;
  return result;
};
const workbenchReview=renderManagerReview;
renderManagerReview=function(){
  if(!liveSnapshot?.manager){
    const root=$('[data-results-body]');
    const failed=typeof snapshotLoadState!=='undefined'&&snapshotLoadState==='error';
    $('[data-results-view]>h2').textContent='调度工作台';
    $('.scope-note').textContent='校园模拟数据 · 正在连接调度实例';
    root.innerHTML=`<section class="review-sheet"><div class="review-empty" role="status"><h3>${failed?'调度实例暂时连接不上':'正在读取调度实例'}</h3><p>${failed?'请检查本地服务后重新读取。':'库存和任务正在加载，读取完成后显示。'}</p><button data-workbench-retry ${typeof snapshotLoadState!=='undefined'&&snapshotLoadState==='loading'?'disabled':''}>重新读取</button></div></section>`;
    root.querySelector('[data-workbench-retry]').onclick=async()=>{const pending=loadSnapshot();renderManagerReview();await pending;renderManagerReview();};
    return;
  }
  workbenchReview();
  const root=$('[data-results-body]'),m=liveSnapshot?.manager;
  const scope=$('.scope-note');scope.textContent=m?`${simTime(m.clock_s)} · 当前调度实例 · 校园模拟数据`:'校园模拟数据 · 等待调度实例';
  root.querySelectorAll('[data-workbench-cancel]').forEach(b=>b.onclick=()=>reviewCancelTask(m,b.dataset.workbenchCancel));
  root.querySelectorAll('[data-workbench-plan]').forEach(b=>b.onclick=()=>{if(historical)openDispatchRun();reviewMode='draft';renderManagerReview();});
};
