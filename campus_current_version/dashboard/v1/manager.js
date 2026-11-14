// Manager actions extend the existing map; geometry and camera behavior stay shared.
let managerBusy=false,planDraft=null,trialResult=null,trialDraftDirty=false;
const taskLabels={confirmed:'已预留 · 待执行',to_pickup:'前往供给点',loading:'装车中',transporting:'运输中',unloading:'卸车中',completed:'已完成',cancelled:'已取消',cancel_empty:'取消中 · 返回供给点',cancel_delivery:'取消中 · 安全送达',cancelled_delivered:'已卸车后取消'};
const simTime=t=>`第${Math.floor(t/86400)+1}日 ${String(Math.floor(t/3600)%24).padStart(2,'0')}:${String(Math.floor(t/60)%60).padStart(2,'0')}`;
const oldRenderTask=renderTask,oldTimeline=renderTimeline,oldProcessing=renderProcessing,oldChoose=choose;
choose=function(id){oldChoose(id);const detail=$('[data-place-details]');if(detail)detail.open=true;};

async function managerAction(body){
  if(managerBusy||historical)return;
  managerBusy=true;renderProcessing();$('.error').hidden=true;
  try{
    const response=await fetch('/api/manager',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({...body,view:'future',op_id:crypto.randomUUID()})});
    const result=await response.json();if(!response.ok)throw new Error(result.error||'操作未完成，请重试');
    if(body.action==='trial'){trialDraftDirty=false;if(typeof stopTrialPlayback==='function'){stopTrialPlayback();trialFrame=0;trialPoint=null;trialStrategy='current_plan';}planDraft={version:result.source_state_version,tasks:result.proposed_tasks.map((t,i)=>({...body.tasks[i],...t,status:'pending_confirmation',reason:'来源、载量与时序已通过模拟核验；尚未确认'}))};trialResult=result;showTrial();}else{planDraft=null;await loadSnapshot();}
    return result;
  }catch(error){$('.error').hidden=false;$('.error').textContent=error.message;await loadSnapshot();}
  finally{managerBusy=false;renderProcessing();if(data?.manager&&!historical)renderTask();if(!$('[data-results-view]').hidden)renderResults();}
}
function draftTasks(){
  const plan=data.manager.plan;
  if(planDraft?.version!==data.manager.state_version)planDraft={version:data.manager.state_version,tasks:structuredClone(plan.tasks)};
  return planDraft.tasks;
}
renderTask=function(){
  if(historical&&liveSnapshot?.manager){$('[data-task]').textContent='历史只读；切换未来风险后制定和执行任务。';return;}
  const m=data?.manager;if(!m)return oldRenderTask();
  const box=$('[data-task]');
  if(historical){box.textContent='历史只读；切换未来风险后制定和执行任务。';return;}
  const active=m.tasks.filter(t=>!['completed','cancelled','cancelled_delivered'].includes(t.status));
  if(!m.forecast_available)planDraft=null;
  const candidates=m.forecast_available?draftTasks():[];
  const show=active.length?active:candidates;
  const planMode=!active.length;
  box.innerHTML=(m.forecast_error&&active.length?`<p class="task-meta" role="status">${esc(m.forecast_error)}</p>`:'')+
    (show.length?show.map((t,i)=>`<details class="manager-task"><summary>${esc(t.origin_name)} → ${esc(t.destination_name)}<br><strong>${t.quantity} 辆</strong> · ${planMode?'待确认':esc(taskLabels[t.status]||t.status)}<span class="ops-incoming">出发 ${simTime(t.departure_s)} · 卸毕 ${simTime(t.finish_s)}</span></summary><p class="task-meta">出发 ${simTime(t.departure_s)}<br>预计送达卸毕 ${simTime(t.finish_s)}<br>${esc(t.reason||'')}<br>${t.late_warning?'到达可能晚于预计风险时段，无法避免前段缺车。':''}</p>${planMode?`<label>搬运数量<input data-task-quantity="${i}" type="number" min="1" max="${t.quantity}" value="${t.quantity}"></label><label>推迟出发（分钟）<input data-task-delay="${i}" type="number" min="0" max="30" value="0"></label>`:`<button data-cancel-task="${esc(t.id)}" ${managerBusy?'disabled':''}>取消此任务及后续待发任务</button>`}<div class="task-actions"><button data-manager-point="${esc(t.origin_parking)}">供给点</button><button data-manager-point="${esc(t.destination_parking)}">接收点</button></div></details>`).join(''):`<p class="task-meta">${esc(m.plan.reason||'当前无可行方案')}。${m.forecast_available?'可推进时刻观察需求变化。':'恢复计算后重新规划。'}</p>`)+
    (planMode&&show.length?`<div class="task-actions"><button class="primary" data-trial-plan ${managerBusy||!m.forecast_available?'disabled':''}>模拟并评估方案</button></div>`:'')+
    (active.length?`<div class="task-actions"><button class="primary" data-next-stage ${managerBusy||data.processing?.status!=='running'?'disabled':''}>模拟下一阶段</button></div>`:'')+
    `<details class="manager-resources"><summary>车辆与预留状态</summary><p class="task-meta">${reviewTruckAvailability(m)}</p><p class="task-meta">停放 ${whole(data.points.reduce((a,p)=>a+p.inventory,0))} · 骑行 ${data.in_transit} · 载货 ${data.truck_cargo} = ${data.total_bicycles} 辆<br>预留 ${m.tasks.filter(t=>['confirmed','to_pickup'].includes(t.status)).reduce((a,t)=>a+t.quantity,0)} 辆已含在停放车辆中</p></details>`+
    (m.tasks.some(t=>['completed','cancelled','cancelled_delivered'].includes(t.status))?`<details class="manager-task"><summary>近期执行记录</summary>${m.tasks.filter(t=>['completed','cancelled','cancelled_delivered'].includes(t.status)).slice(-5).reverse().map(t=>`<p class="task-meta">${esc(t.origin_name)} → ${esc(t.destination_name)} · ${t.quantity}辆 · ${taskLabels[t.status]}</p>`).join('')}</details>`:'');
  box.querySelectorAll('[data-manager-point]').forEach(b=>b.onclick=()=>{choose(b.dataset.managerPoint);focus(b.dataset.managerPoint);});
  box.querySelectorAll('[data-cancel-task]').forEach(b=>b.onclick=()=>reviewCancelTask(m,b.dataset.cancelTask));
  function edited(){return candidates.map((t,i)=>({...t,quantity:Number(box.querySelector(`[data-task-quantity="${i}"]`).value),departure_s:t.departure_s+Number(box.querySelector(`[data-task-delay="${i}"]`).value)*60}));}
  box.querySelectorAll('[data-task-quantity],[data-task-delay]').forEach(input=>input.oninput=()=>{trialDraftDirty=true;});
  const confirm=box.querySelector('[data-confirm-plan]'),trial=box.querySelector('[data-trial-plan]');
  if(confirm)confirm.onclick=()=>managerAction({action:'confirm',tasks:edited(),state_version:m.state_version});
  if(trial)trial.onclick=()=>{const tasks=edited();planDraft={version:m.state_version,tasks};managerAction({action:'trial',tasks,state_version:m.state_version,horizon_minutes:60});};
  const nextStage=box.querySelector('[data-next-stage]');if(nextStage)nextStage.onclick=()=>{
    const field={confirmed:'departure_s',to_pickup:'pickup_s',cancel_empty:'pickup_s',loading:'loaded_s',transporting:'arrival_s',unloading:'finish_s',cancel_delivery:'finish_s'};
    managerAction({action:'advance',to_time_s:Math.max(m.clock_s,Math.min(...active.map(t=>t[field[t.status]])))});
  };
};
renderProcessing=function(){
  oldProcessing();if(!data?.manager)return;
  const button=$('[data-replay]');button.textContent=managerBusy?'正在处理…':'推进15分钟';button.disabled=managerBusy||data.processing?.status!=='running';
  if(historical)return;
  $('[data-engine]').textContent=data.processing?.status==='running'?'Spark增量计算运行中 · 单车调运模拟':'计算已停止或失去心跳 · 当前风险不可用';
};
renderTimeline=function(){
  oldTimeline();document.body.dataset.riskView=historical?'history':'future';
  if(historical){$('[data-footer-scope]').textContent='橙色为原无调度模拟的上小时借车失败；蓝色仅表示历史库存较多。这是历史参照，不是当前执行实例的回放。';return;}
  if(!data?.manager)return;
  if(historical)return;
  const m=data.manager;
  $('[data-window]').textContent='模拟时刻 '+simTime(m.clock_s);
  $('[data-coverage]').textContent=`预测起点 ${simTime(m.forecast_origin_s)} · 风险窗口 至${simTime(m.window_end_s)} · 校园模拟数据`;
  $('[data-count]').textContent='状态版本 '+m.state_version+' · 库存更新于 '+new Date(m.inventory_updated_at).toLocaleTimeString('zh-CN',{hour12:false});
  $('[data-updated]').textContent='模拟时钟暂停待推进；重用已训练模型';
  $('.assumption').textContent='校园数据及运输时延为模拟假设。预测目标是成功借车量，不能代表全部潜在需求。15分钟比例仅估计风险时段；预留属于停放车辆，不重复计数。';
  $('[data-legend-shortage]').textContent='未来预计缺车';$('[data-legend-surplus]').textContent='未来可调出富余';
  if(!m.forecast_available){$('[data-advice]').textContent=m.forecast_error;$('[data-risk-details]').hidden=true;}
  const p=reading(selected);
  if(p)$('[data-stock]').textContent=whole(p.available_to_borrow)+' 辆';
  if(p?.reserved)$('[data-advice]').textContent=`已预留 ${p.reserved} 辆；仍可借 ${p.available_to_borrow} 辆。预留受任务约束。`;
  if(p?.first_shortage_minute!=null)$('[data-risk-timing]').textContent=`预计在当前时刻后 ${Math.max(0,Math.floor(p.first_shortage_minute/15)*15-15)}—${Math.ceil(p.first_shortage_minute/15)*15} 分钟附近出现缺口；这是小时模型的区间估计，不是精确缺车时间。`;
};
// Capture before legacy generator's click handler; simulation uses potential demand.
$('[data-replay]').addEventListener('click',e=>{if(!data?.manager)return;e.stopImmediatePropagation();managerAction({action:'advance',to_time_s:data.manager.clock_s+900});},true);
function showTrial(){showReviewTrial();}
