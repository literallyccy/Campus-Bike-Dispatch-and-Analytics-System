// No permanent HTTP connection. Historical playback is fully read-only/offline.
let snapshotController,livePollTimer,livePollGeneration=0;
async function loadSnapshot(){
  if(snapshotRequest)return snapshotRequest;
  const controller=new AbortController();snapshotController=controller;
  const timeout=setTimeout(()=>controller.abort(),8000);
  snapshotRequest=(async()=>{
    try{
      const response=await fetch('/api/snapshot',{cache:'no-store',signal:controller.signal});
      if(!response.ok)throw new Error('HTTP '+response.status);
      await loadBuildings(controller.signal);applySnapshot(await response.json());return true;
    }catch(error){
      if(!initialized&&!historical)showMapNotice('地图暂时无法加载','正在重试读取本地服务。',{retry:true});
      if(initialized&&!historical&&data?.manager){
        const unavailable=structuredClone(data);unavailable.snapshot_at='connection-lost-'+Date.now();
        unavailable.processing={status:'connection_lost'};
        unavailable.manager.forecast_available=false;unavailable.manager.forecast_error='数据连接已中断，当前风险不可用；恢复连接后重算';
        unavailable.manager.plan={tasks:[],reason:unavailable.manager.forecast_error};
        unavailable.points.forEach(p=>{p.status='unavailable';p.expected_borrows=null;p.expected_returns=null;delete p.projected_stock_curve;});
        applySnapshot(unavailable);
      }
      return false;
    }finally{clearTimeout(timeout);if(snapshotController===controller)snapshotController=null;snapshotRequest=null;}
  })();
  return snapshotRequest;
}
function stopLivePolling(){
  livePollGeneration++;clearTimeout(livePollTimer);livePollTimer=null;snapshotController?.abort();
}
function startLivePolling(){
  stopLivePolling();
  if(historical||document.hidden||!$('[data-results-view]').hidden)return;
  const generation=livePollGeneration;
  async function poll(){
    if(generation!==livePollGeneration||historical||document.hidden)return;
    const ok=await loadSnapshot();
    if(generation!==livePollGeneration||historical||document.hidden)return;
    const status=$('[data-connection]');status.textContent=ok?'实时库存连接正常':'暂时连接不上，正在重试';status.dataset.ok=String(ok);
    livePollTimer=setTimeout(poll,2500);
  }
  poll();
}
function connectData(){
  if(location.protocol!=='file:')return;
  showMapNotice('请从本地服务入口打开','请使用 http://127.0.0.1:8871/v1/ 。',{file:true});
  $('[data-connection]').textContent='当前是文件预览';$('[data-task]').textContent='连接项目服务后显示';
  document.querySelectorAll('[data-view]').forEach(button=>button.disabled=true);
}
