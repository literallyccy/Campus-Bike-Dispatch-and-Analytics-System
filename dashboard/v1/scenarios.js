// One selected scenario for map, analysis, editing and execution.
let scene=null,sceneCatalog=null,sceneEpoch=0,scenePending=null,sceneBusy=false,sceneDirty=false,sceneSaveTimer=null,sceneSavePromise=null,sceneEditSerial=0,sceneTasks=[],sceneListOpen=false;
const sceneDone=['completed','cancelled','cancelled_delivered'];
async function sceneJSON(url,body){const r=await fetch(url,{cache:'no-store',...(body?{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body)}:{})});const v=await r.json();if(!r.ok)throw Error(v.error||'读取失败');return v;}
function sceneError(e){$('.error').hidden=false;$('.error').textContent=e.message;}
function sceneAccept(v){
  if(!scene||scene.id!==v.scenario.id){data=null;liveSnapshot=null;initialized=false;}
  $('.error').hidden=true;scene=v.scenario;sceneTasks=structuredClone(scene.draft.tasks);sceneDirty=false;
  trialResult=scene.trial;trialDraftDirty=!!trialResult&&(trialResult.draft_revision!==scene.draft.revision||trialResult.source_state_version!==v.manager.state_version);
  planDraft={version:v.manager.state_version,tasks:sceneTasks};historical=false;
  applySnapshot(v);renderTimeBrowser();if(!$('[data-results-view]').hidden)renderManagerReview();
}
loadSnapshot=async function(){
  if(!scene)return false;
  const epoch=sceneEpoch,sid=scene.id;
  try{const v=await sceneJSON('/api/scenario?id='+encodeURIComponent(sid));if(epoch!==sceneEpoch)return false;if(!sceneDirty)sceneAccept(v);return true;}catch(e){sceneError(e);return false;}
};
stopLivePolling();startLivePolling=function(){};
async function sceneSelect(time,fresh=false,savedId=null){
  if(sceneBusy)return;sceneBusy=true;renderTimeBrowser();renderProcessing();
  document.querySelectorAll('[data-results-body] input,[data-results-body] select,[data-results-body] button').forEach(x=>x.disabled=true);
  try{await sceneSave();const epoch=++sceneEpoch;
    const v=savedId?await sceneJSON('/api/scenario?id='+encodeURIComponent(savedId)):await sceneJSON('/api/scenarios',{action:'create',time_s:Number(time),fresh});if(epoch!==sceneEpoch)return;
    sceneListOpen=false;sceneAccept(v);const u=new URL(location.href);u.searchParams.delete('hour');u.searchParams.delete('view');u.searchParams.set('scenario',scene.id);history.replaceState(null,'',u);
  }catch(e){sceneError(e);}finally{sceneBusy=false;renderTimeBrowser();renderProcessing();if(!$('[data-results-view]').hidden)renderManagerReview();}
}
startLocalHourView=async function(){
  try{sceneCatalog=await sceneJSON('/api/scenarios');const u=new URL(location.href),sid=u.searchParams.get('scenario');
    if(sid){sceneAccept(await sceneJSON('/api/scenario?id='+encodeURIComponent(sid)));return;}
    const hour=u.searchParams.get('hour');const selected=hour!==null?sceneCatalog.times.find(x=>Math.floor(x.time_s/3600)%24===Number(hour)):(sceneCatalog.times.find(x=>Math.floor(x.time_s/3600)%24===9)||sceneCatalog.times[0]);
    if(!selected)throw Error('所选时段没有完整场景输入');await sceneSelect(selected.time_s);
  }catch(e){sceneError(e);}
};
renderTimeBrowser=function(){
  let controls=document.querySelector('[data-scene-picker]');
  if(!controls){controls=document.createElement('div');controls.dataset.scenePicker='';controls.className='scene-picker';document.querySelector('header').append(controls);}
  controls.innerHTML=`<label>分析时段 <select aria-label="分析时段" ${sceneBusy?'disabled':''}>${(sceneCatalog?.times||[]).map(x=>`<option value="${x.time_s}" ${scene?.start_s===x.time_s?'selected':''}>${esc(x.label)}</option>`).join('')}</select></label><span>校园模拟${sceneBusy?' · 加载中':''}</span>${scene?'<button data-scene-new>新建副本</button><button data-scene-list>已有场景</button>':''}`;
  if(sceneListOpen)controls.insertAdjacentHTML('beforeend',sceneSavedList());
  controls.querySelector('[data-scene-list]')?.addEventListener('click',async()=>{if(sceneBusy)return;try{await sceneSave();sceneCatalog=await sceneJSON('/api/scenarios');sceneListOpen=!sceneListOpen;renderTimeBrowser();}catch(e){sceneError(e);}});
  controls.querySelectorAll('[data-scene-resume]').forEach(b=>b.onclick=()=>sceneSelect(0,false,b.dataset.sceneResume));
  controls.querySelector('select').onchange=e=>sceneSelect(e.target.value);
  if(controls.querySelector('[data-scene-new]'))controls.querySelector('[data-scene-new]').disabled=sceneBusy;
  controls.querySelector('[data-scene-new]')?.addEventListener('click',()=>sceneSelect(scene.start_s,true));
  document.querySelector('[data-time-browser]')?.remove();$('[data-mode]').hidden=true;
  $('[data-time-controls]').hidden=true;document.querySelector('[data-browse-context]')?.remove();
};
async function sceneRequest(action,extra={}){
  if(!scene)throw Error('请先选择时段');
  return sceneJSON('/api/scenarios',{action,scenario_id:scene.id,state_version:liveSnapshot.manager.state_version,op_id:crypto.randomUUID(),...extra});
}
async function sceneSave(){
  clearTimeout(sceneSaveTimer);
  if(sceneSavePromise){await sceneSavePromise;if(sceneDirty)return sceneSave();return;}
  if(!sceneDirty||!scene)return;
  const serial=sceneEditSerial,tasks=structuredClone(sceneTasks);
  sceneSavePromise=sceneRequest('draft',{revision:scene.draft.revision,tasks}).then(r=>{scene.draft=r.draft;if(serial===sceneEditSerial)sceneDirty=false;});
  try{await sceneSavePromise;}finally{sceneSavePromise=null;}
  if(sceneDirty)return sceneSave();
  const status=document.querySelector('[data-scene-save]');if(status)status.textContent='已保存';
}
function sceneChanged(){sceneDirty=true;sceneEditSerial++;trialDraftDirty=true;const status=$('[data-scene-save]');if(status)status.textContent='保存中…';clearTimeout(sceneSaveTimer);sceneSaveTimer=setTimeout(()=>sceneSave().catch(e=>{sceneError(e);if(status)status.textContent='保存失败，请重试';}),500);}
window.addEventListener('beforeunload',e=>{if(sceneDirty){e.preventDefault();e.returnValue='';}});
managerAction=async function(body){
  if(sceneBusy||!scene)return;sceneBusy=true;managerBusy=true;renderTimeBrowser();renderProcessing();document.querySelectorAll('[data-results-body] button,[data-results-body] input,[data-results-body] select').forEach(x=>x.disabled=true);
  try{await sceneSave();let result;
    if(body.action==='trial'){
      if(body.tasks){sceneTasks=structuredClone(body.tasks);sceneDirty=true;sceneEditSerial++;await sceneSave();}
      result=await sceneRequest('trial',{revision:scene.draft.revision,horizon_minutes:60});trialResult=result;trialDraftDirty=false;reviewMode='trial';
    }else if(['confirm','retain'].includes(body.action))result=await sceneRequest(body.action,{trial_id:scene.trial?.id||trialResult?.id,reason:body.reason||''});
    else result=await sceneRequest(body.action,{task_id:body.task_id,to_time_s:body.to_time_s});
    await loadSnapshot();return result;
  }catch(e){sceneError(e);}finally{sceneBusy=false;managerBusy=false;renderTimeBrowser();renderProcessing();renderManagerReview();if(data)renderTask();}
};
function sceneRoute(t,i){const options=selected=>liveSnapshot.points.map(p=>`<option value="${esc(p.node_id)}" ${p.node_id===selected?'selected':''}>${esc(p.name)}</option>`).join('');return `<article class="scene-task"><label>调出<select data-origin="${i}">${options(t.origin)}</select></label><span>→</span><label>调入<select data-destination="${i}">${options(t.destination)}</select></label><label>辆数<input data-quantity="${i}" type="number" min="1" value="${t.quantity}"></label><label>出发时间<input data-departure="${i}" type="time" value="${trialShortTime(t.departure_s)}"></label><button data-remove="${i}">移除</button></article>`;}
renderManagerReview=function(){
  stopTrialPlayback();
  const root=$('[data-results-body]');$('[data-results-view]>h2').textContent='调度工作台';
  if(!scene||!liveSnapshot?.manager){root.innerHTML='<p role="status">请选择分析时段，正在读取场景。</p>';return;}
  const m=liveSnapshot.manager,active=m.tasks.filter(t=>!sceneDone.includes(t.status)),risks=DecisionReview.risks(liveSnapshot.points);
  $('.scope-note').textContent=`${simTime(m.clock_s)}${m.clock_s!==scene.start_s?' · 起点 '+trialShortTime(scene.start_s):''}`;
  const modes={diagnosis:'研判',draft:'方案',trial:'模拟比较',execution:'执行复盘'};
  let body='';
  if(reviewMode==='diagnosis')body=`<h3>${risks.length?risks.length+'处需补车':'当前无需补车'}</h3><div class="decision-evidence">${risks.map(p=>`<button data-location="${esc(p.id)}"><span>${esc(p.name)}${reviewIncoming(p,m)}${reviewRiskTiming(p)}</span><strong>${shortageCount(p.shortage)} 辆</strong></button>`).join('')}</div>${!m.forecast_available?`<p role="alert">${esc(m.forecast_error)}</p>`:''}<button class="primary" data-scene-plan>${active.length?'查看已确认任务':m.plan.tasks.length?'查看调运建议':'查看资源与方案'}</button><details><summary>借还统计</summary>${evidenceHistory(m)}</details>`;
  if(reviewMode==='draft')body=active.length?reviewActivePlan(m).replace(/<details class="workbench-receipt">[\s\S]*?<\/details>/,''):`<div class="scene-toolbar"><h3>调运方案</h3><span data-scene-save>${sceneDirty?'未保存':'已保存'}</span><button data-suggest>采用系统建议</button><button data-add ${sceneTasks.length>=3?'disabled':''}>添加任务</button></div>${sceneTasks.map(sceneRoute).join('')||'<p>暂无任务，可采用建议或手动添加。</p>'}<p class="review-muted">${esc(reviewTruckAvailability(m))} · 每轮最多3项</p><button class="primary" data-scene-trial ${!sceneTasks.length||!m.forecast_available?'disabled':''}>模拟未来60分钟</button>`;
  if(reviewMode==='trial'){
    body=trialResult?reviewTrial(m).replace('返回地图调整','修改方案'):`<h3>${sceneTasks.length?'方案尚未模拟':'暂无方案'}</h3><p>${sceneTasks.length} 项任务</p><button class="primary" data-review-map>编辑方案并模拟</button>`;
    if(trialResult?.system){const b=trialResult.results.no_new_dispatch,s=trialResult.system.results.current_plan,p=trialResult.results.current_plan,max=Math.max(1,b.unmet,s.unmet,p.unmet);body=`<section class="scene-comparison"><h3>全窗口未满足次数</h3>${[['不新增调运',b],['系统建议',s],['人工方案',p]].map(([name,r])=>`<div><span>${name}</span><span class="scene-bar ${r.unmet>b.unmet?'loss':r.unmet<b.unmet?'benefit':'neutral'}" role="img" aria-label="${name}未满足${r.unmet}次"><i style="width:${r.unmet/max*100}%"></i></span><strong>${r.unmet}次</strong><small>搬运${r.moved}辆次 · ${number(r.work_minutes)}分钟</small></div>`).join('')}</section>`+body;}
  }
  if(reviewMode==='trial'&&trialResult){body=sceneDecisionPanel(m)+body+sceneEvidencePanel();}
  if(reviewMode==='execution')body=reviewExecution(m)+sceneDecisionReceipt()+(scene.confirmation?`<details><summary>确认依据</summary><p>${scene.confirmation.tasks.length}项任务 · 确认记录已保存</p></details>`:'');
  root.innerHTML=`<nav class="decision-steps">${Object.entries(modes).map(([k,v])=>`<button data-scene-mode="${k}" aria-current="${reviewMode===k?'step':'false'}">${v}</button>`).join('')}</nav><section class="review-sheet">${body}</section>`;
  root.querySelectorAll('[data-scene-mode]').forEach(b=>b.onclick=async()=>{try{await sceneSave();reviewMode=b.dataset.sceneMode;renderManagerReview();}catch(e){sceneError(e);}});
  root.querySelector('[data-scene-plan]')?.addEventListener('click',()=>{reviewMode='draft';renderManagerReview();});
  root.querySelectorAll('[data-location],[data-review-location]').forEach(b=>b.onclick=()=>reviewBack(b.dataset.location||b.dataset.reviewLocation));
  root.querySelector('[data-suggest]')?.addEventListener('click',()=>{sceneTasks=structuredClone(m.plan.tasks);sceneChanged();renderManagerReview();});
  root.querySelector('[data-add]')?.addEventListener('click',()=>{sceneTasks.push({origin:liveSnapshot.points[0].node_id,destination:liveSnapshot.points[1].node_id,quantity:1,departure_s:m.clock_s+60});sceneChanged();renderManagerReview();});
  root.querySelectorAll('[data-remove]').forEach(b=>b.onclick=()=>{sceneTasks.splice(Number(b.dataset.remove),1);sceneChanged();renderManagerReview();});
  for(const key of ['origin','destination','quantity','departure'])root.querySelectorAll('[data-'+key+']').forEach(input=>input.oninput=input.onchange=()=>{const t=sceneTasks[Number(input.dataset[key])];if(key==='departure'){const [h,min]=input.value.split(':').map(Number);t.departure_s=Math.floor(m.clock_s/86400)*86400+h*3600+min*60;}else t[key]=key==='quantity'?Number(input.value):input.value;sceneChanged();});
  root.querySelector('[data-scene-trial]')?.addEventListener('click',()=>managerAction({action:'trial'}));
  
  root.querySelectorAll('[data-review-map],[data-workbench-plan]').forEach(b=>b.onclick=()=>{reviewMode='draft';renderManagerReview();});
  root.querySelector('[data-review-adopt]')?.addEventListener('click',()=>managerAction({action:'confirm',reason:root.querySelector('[data-decision-reason]')?.value||''}));
  root.querySelector('[data-decision-retain]')?.addEventListener('click',()=>managerAction({action:'retain'}));
  root.querySelectorAll('[data-evidence-jump]').forEach(b=>b.onclick=()=>{
    const row=trialResult.decision_evidence[Number(b.dataset.evidenceJump)];trialPoint=row.node_id;
    const time=row.first_sample_with_more_unmet_s??row.first_sample_with_less_available_s;
    trialFrame=Math.max(0,trialResult.playback.current_plan.findIndex(f=>f.time_s===time));
    const playback=root.querySelector('.trial-playback');if(playback){playback.open=true;updateTrialFrame(root);playback.scrollIntoView({block:'start',behavior:'smooth'});}
  });
  if(trialResult?.decision_advice?.requires_reason){const confirm=root.querySelector('[data-review-adopt]');if(confirm){confirm.classList.remove('primary');confirm.textContent='仍要执行此方案';}}
  root.querySelectorAll('[data-ops-tasks]').forEach(b=>b.onclick=reviewOpenTasks);
  root.querySelectorAll('[data-workbench-cancel]').forEach(b=>b.onclick=()=>reviewCancelTask(m,b.dataset.workbenchCancel));
  root.querySelectorAll('[data-evidence-direction]').forEach(b=>b.onclick=()=>{evidenceDirection=b.dataset.evidenceDirection;renderManagerReview();});
  root.querySelector('[data-evidence-hour]')?.addEventListener('change',e=>{evidenceHour=Number(e.target.value);renderManagerReview();});
  root.querySelector('[data-evidence-align]')?.addEventListener('click',()=>{evidenceHour=null;renderManagerReview();});
  if(reviewMode==='diagnosis'&&!patternData&&!evidenceLoading&&!evidenceError)loadDecisionEvidence();
  if(reviewMode==='trial')bindTrialPlayer(root,m);
  const rerun=root.querySelector('[data-review-run]');if(rerun)rerun.onclick=()=>managerAction({action:'trial'});
  root.querySelectorAll('input,select,button').forEach(x=>{if(sceneBusy)x.disabled=true;});
};
const sceneTimeline=renderTimeline;
renderTimeline=function(){sceneTimeline();if(!scene)return;$('[data-window]').textContent=simTime(liveSnapshot.manager.clock_s);$('[data-coverage]').textContent='预测至 '+trialShortTime(liveSnapshot.manager.window_end_s);$('[data-count]').textContent='';$('[data-updated]').textContent='';$('[data-latest]').textContent='';$('.assumption').textContent='';$('[data-engine]').textContent='';$('[data-footer-scope]').textContent='';};
// Keep map task management, but route all writes through the selected scenario.
reviewBack=function(point){historical=false;$('[data-view="map"]').click();if(point){choose(point);focus(point);}renderTimeBrowser();};

// One editor: map shows location and task status, workbench owns editable drafts.
renderTask=function(){
  if(!scene||!data?.manager)return;
  const m=data.manager,active=m.tasks.filter(t=>!sceneDone.includes(t.status));
  $('[data-task]').innerHTML=`${active.length?active.map(t=>`<p><strong>${esc(t.origin_name)} → ${esc(t.destination_name)}</strong><br>${t.quantity}辆 · ${esc(taskLabels[t.status])} · 卸毕${trialShortTime(t.finish_s)}</p>`).join(''):`<p>${sceneTasks.length?'草稿 '+sceneTasks.length+' 项任务':m.plan.tasks.length?'系统建议 '+m.plan.tasks.length+' 项任务':'暂无调运建议'}</p>`}<button class="primary" data-scene-open>查看${active.length?'任务':'方案'}</button><details><summary>车辆资源</summary><p>${esc(reviewTruckAvailability(m))}</p><p>停放 ${whole(data.points.reduce((a,p)=>a+p.inventory,0))} · 骑行 ${data.in_transit} · 载货 ${data.truck_cargo}</p></details>`;
  $('[data-scene-open]').onclick=()=>{reviewMode='draft';$('[data-view="results"]').click();};
};
renderProcessing=function(){
  if(!data?.manager)return;
  const button=$('[data-replay]');button.textContent=managerBusy?'正在处理…':'推进15分钟';button.disabled=managerBusy||sceneBusy;
  $('[data-connection]').textContent='场景已读取';$('[data-engine]').textContent='';
};

function sceneSavedList(){
  const rows=sceneCatalog?.scenarios||[];
  return `<section class="scene-saved" aria-label="已有场景"><p>按最近修改排列 · 选择时段默认恢复最近副本</p>${rows.map(s=>`<button data-scene-resume="${esc(s.id)}" ${s.error||sceneBusy?'disabled':''} ${scene?.id===s.id?'aria-current="true"':''}><strong>${simTime(s.start_s)} · 副本${esc(s.id.slice(-6))}</strong><span>${esc(s.status)} · 推进至${s.clock_s==null?'—':trialShortTime(s.clock_s)}</span><small>修改于 ${esc(new Date(s.updated_at).toLocaleString('zh-CN',{hour12:false}))}${scene?.id===s.id?' · 当前':''}</small></button>`).join('')||'<p>暂无场景</p>'}</section>`;
}
function sceneDecisionReceipt(){
  const d=scene?.decision;if(!d)return '';
  return `<details class="decision-receipt"><summary>最近一次选择记录</summary><p>${d.action==='retain'?'不新增调运':'确认调运'} · ${esc(d.assessment.title)}</p>${d.reason?`<p>理由：${esc(d.reason)}</p>`:''}<p class="review-muted">这是选择时的模拟依据，不是执行效果。</p></details>`;
}
function sceneDecisionPanel(m){
  const t=trialResult,a=t?.decision_advice;if(!a)return '';
  const fresh=trialFresh(m);
  const persisted=scene?.decision?.trial_id===t.id?sceneDecisionReceipt():'';
  return `<section class="decision-choice ${a.code}"><h3>${fresh?esc(a.title):'方案或状态已变化，请重新模拟'}</h3><p>模拟评估后选择${a.code==='unsettled'?' · 仍有步行需求待结算':''}</p>${fresh?`<button data-decision-retain ${managerBusy?'disabled':''} class="${a.requires_reason?'primary':''}">保留不新增调运</button>${a.requires_reason?'<label>仍要执行的理由<textarea data-decision-reason maxlength="300" rows="2" placeholder="例如：优先保障某地点；说明接受的代价"></textarea></label>':''}`:''}${persisted}</section>`;
}
function sceneEvidencePanel(){
  const all=trialResult?.decision_evidence||[];const worse=all.filter(r=>r.delta>0);if(!worse.length)return '';
  const render=rows=>`<div class="decision-table"><table><thead><tr><th>地点</th><th>不新增</th><th>方案</th><th>变化</th><th>过程证据</th></tr></thead><tbody>${rows.map(r=>{
    const facts=[];
    if(r.reserved_at_start)facts.push(`起点预留${r.reserved_at_start}辆`);
    if(r.first_sample_with_less_available_s!=null)facts.push(`${trialShortTime(r.first_sample_with_less_available_s)}采样可借数更少`);
    if(r.first_sample_with_more_unmet_s!=null)facts.push(`${trialShortTime(r.first_sample_with_more_unmet_s)}采样首次多出未满足`);
    if(r.planned_unload_s.length)facts.push('计划卸毕'+r.planned_unload_s.map(trialShortTime).join('、'));
    return `<tr><th>${esc(r.name)}</th><td>${r.baseline}</td><td>${r.plan}</td><td class="loss">+${r.delta}</td><td>${facts.map(esc).join('；')||'暂无过程采样'}${trialResult.playback?.current_plan?.length&&(r.first_sample_with_more_unmet_s!=null||r.first_sample_with_less_available_s!=null)?` <button data-evidence-jump="${all.indexOf(r)}">查看此刻</button>`:''}</td></tr>`;
  }).join('')}</tbody></table></div>`;
  return `<section class="decision-evidence-table"><h3>哪些地点变差了</h3><p class="review-muted">全窗口未满足次数；过程采样说明同时发生的变化，不单独证明原因。</p>${render(worse.slice(0,3))}${worse.length>3?`<details><summary>其余${worse.length-3}处</summary>${render(worse.slice(3))}</details>`:''}</section>`;
}
