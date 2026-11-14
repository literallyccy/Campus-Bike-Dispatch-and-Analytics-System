"""Bounded campus decision loop: observable forecasts, one truck, durable operations.

The simulator alone reads potential trips. Forecasts use only completed bins.
Existing legacy runs are untouched; this engine is explicitly enabled per run.
"""
from collections import Counter
import copy
from datetime import datetime, timezone
from functools import lru_cache
import hashlib
import json
import math
from pathlib import Path
import sqlite3

from dispatch_advice import context
from forecast_runtime import Forest
from prepare_forecast_inputs import feature_row
from risk_projection import load_profile

ROOT=Path(__file__).resolve().parents[1]
HOUR=3600
DAY=86400
TERMINAL={'completed','cancelled','cancelled_delivered'}
RESERVED={'confirmed','to_pickup'}
DELIVERING=RESERVED|{'loading','transporting','unloading','cancel_delivery'}


class Conflict(ValueError):
    pass


def stamp():return datetime.now(timezone.utc).isoformat()


def database(run):
    db=sqlite3.connect(str(Path(run)/'state.sqlite'),timeout=20)
    db.row_factory=sqlite3.Row
    return db


def read_config(run):return json.loads((Path(run)/'config.json').read_text())


def dataset_path(cfg):
    path=(ROOT/cfg.get('metadata',{}).get('dataset_dir','data/simulated/campus_v1')).resolve()
    if ROOT not in path.parents:raise Conflict('模拟数据路径超出项目范围')
    return path


def load_state(db):return json.loads(db.execute('select payload from manager_state where id=1').fetchone()[0])


def reserved(s):
    result=Counter()
    for t in s['tasks']:
        if t['status'] in RESERVED:result[t['origin']]+=t['quantity']
    return result


def conserved(s,cfg):
    r=reserved(s)
    assert all(type(n) is int and n>=0 for n in s['stock'].values())
    assert all(r[n]<=s['stock'][n] for n in r)
    assert s['riding']>=0 and s['truck']['cargo']>=0
    assert sum(s['stock'].values())+s['riding']+s['truck']['cargo']==cfg['total_bicycles']
    assert s['truck']['cargo']<=context()[2]['truck_capacity']


def store(db,s,cfg):
    conserved(s,cfg)
    db.execute('update manager_state set payload=? where id=1',(json.dumps(s,ensure_ascii=False),))
    for p in cfg['points']:db.execute('update stock set inventory=? where point_id=?',(s['stock'][p['node_id']],p['id']))


def observe(s,t,node,kind,q=1):
    key=str(int(t)//HOUR)+'|'+node
    values=s['history'].setdefault(key,[0,0]);values[0 if kind=='borrow' else 1]+=q


def initialize(db,cfg):
    start=cfg['metadata']['reference_time_s']
    s=dict(clock=start,initial_clock=start,demand_cutoff=start,version=0,
        stock={p['node_id']:p['initial_inventory'] for p in cfg['points']},history={},
        riding=cfg['initial_in_transit'],external_riding=0,returns=[],retries=[],tasks=[],
        truck=dict(node=context()[2]['depot_node'],cargo=0,active=None),moved_by_day={},
        metrics=dict(potential=0,served=0,unmet=0,moved=0,distance_m=0.,work_s=0.),
        updated_at=stamp(),last_unmet={})
    # Historical observed completed orders only; never load future demand here.
    for line in (dataset_path(cfg)/'trips.jsonl').open():
        trip=json.loads(line)
        if trip['start_time_s']<=start<trip['end_time_s']:
            s['returns'].append(dict(kind='return',time_s=trip['end_time_s'],node=trip['destination_node']))
        for field,node,kind in [('start_time_s','origin_node','borrow'),('end_time_s','destination_node','return')]:
            t=trip[field]
            if start-168*HOUR<=t<=start:observe(s,t,trip[node],kind)
    if len(s['returns'])!=s['riding']:raise Conflict('初始在骑车辆与待归还记录不匹配')
    db.executescript('create table manager_state(id integer primary key,payload text not null);'
                     'create table manager_ops(op_id text primary key,request text not null,response text not null);')
    db.execute('insert into manager_state values(1,?)',(json.dumps(s,ensure_ascii=False),))
    conserved(s,cfg)


@lru_cache(maxsize=4)
def forests(bindings,signatures):
    values=[]
    for name,digest in bindings:
        p=(ROOT/name).resolve()
        if ROOT not in p.parents or hashlib.sha256(p.read_bytes()).hexdigest()!=digest:
            raise Conflict('模型文件缺失或版本变化，预测不可用')
        values.append((name,Forest(p)))
    return [next(m for n,m in values if '/'+kind+'/' in n) for kind in ['borrow','return']]


def predictions(s,cfg):
    hour=s['clock']//HOUR;borrow,returns=Counter(),Counter()
    for key,v in s['history'].items():
        h,n=key.split('|');h=int(h)
        if hour-168<=h<hour:borrow[h,n],returns[h,n]=v
    bindings=tuple(sorted(cfg['metadata']['model_sha256'].items()))
    signatures=tuple(((ROOT/n).stat().st_mtime_ns,(ROOT/n).stat().st_size) for n,d in bindings)
    models=forests(bindings,signatures)
    return {p['node_id']:tuple(max(0.,m.predict(feature_row(p['node_id'],hour,p['kind'],borrow,returns))) for m in models)
            for p in cfg['points']}


def risks(s,cfg):
    if not 42*DAY<=s['clock']<56*DAY:raise Conflict('已超出校园模型声明的测试期，预测不可用')
    estimates=predictions(s,cfg);origin=s['clock']//HOUR*HOUR;end=origin+HOUR
    profile_path=ROOT/cfg['metadata']['risk_profile_path']
    if hashlib.sha256(profile_path.read_bytes()).hexdigest()!=cfg['metadata']['risk_profile_sha256']:raise Conflict('时段比例文件已变化，预测不可用')
    profile=load_profile(cfg['metadata']['risk_profile_path'],cfg['metadata']['risk_profile_sha256'])
    holds=reserved(s);points=[]
    def fraction(t,values):
        pos=max(0.,min(4.,(t-origin)/900));i=min(3,int(pos))
        return sum(values[:i])+values[i]*min(1.,pos-i)
    for p in cfg['points']:
        n=p['node_id'];b,r=estimates[n];shape=profile['profiles'][p['kind']][str(origin//HOUR%24)]
        bf=shape['borrow']['fractions'];rf=shape['return']['fractions']
        changes=[]
        for t in s['tasks']:
            if t['status'] in RESERVED and t['origin']==n:changes.append((t['pickup_s'],-t['quantity']))
            if t['status'] in DELIVERING and t['destination']==n:changes.append((t['finish_s'],t['quantity']))
        times={s['clock'],end}|{origin+i*900 for i in range(1,4) if origin+i*900>s['clock']}
        times|={t for t,q in changes if s['clock']<t<=end}
        curve=[]
        for at in sorted(times):
            value=s['stock'][n]+r*(fraction(at,rf)-fraction(s['clock'],rf))-b*(fraction(at,bf)-fraction(s['clock'],bf))
            value+=sum(q for t,q in changes if s['clock']<=t<at)
            if any(t==at for t,q in changes):curve.append(dict(minute=(at-s['clock'])/60,inventory=value,phase='before_transport_change'))
            value+=sum(q for t,q in changes if t==at and t>=s['clock'])
            curve.append(dict(minute=(at-s['clock'])/60,inventory=value))
        minimum=min(q['inventory'] for q in curve)
        first=next((q['minute'] for q in curve if q['inventory']<0),None)
        point=dict(p,inventory=s['stock'][n],reserved=holds[n],available_to_borrow=s['stock'][n]-holds[n],
            expected_borrows=b*(1-fraction(s['clock'],bf)),expected_returns=r*(1-fraction(s['clock'],rf)),
            shortage=max(0.,-minimum),spare=max(0.,min(s['stock'][n]-holds[n],minimum-p['reserve'])),
            projected_stock_curve=curve,projected_min_inventory=minimum,projected_end_inventory=curve[-1]['inventory'],
            first_shortage_minute=first,risk_time_precision='15-minute expected intervals, not exact onset',
            forecast_origin_s=origin,forecast_end_s=end)
        point['status']='shortage' if point['shortage']>=cfg['shortage_threshold'] else 'surplus' if point['spare']>=cfg['surplus_threshold'] else 'normal'
        points.append(point)
    return points


def queue_end(s):
    tasks=[t for t in s['tasks'] if t['status'] not in TERMINAL]
    if not tasks:return s['clock'],s['truck']['node']
    last=max(tasks,key=lambda t:t['pickup_s'] if t['status']=='cancel_empty' else t['finish_s'])
    end,node=(last['pickup_s'],last['origin']) if last['status']=='cancel_empty' else (last['finish_s'],last['destination'])
    return max(s['clock'],end),node


def task_spec(s,origin,destination,quantity,departure):
    nodes,distance,c=context();at,node=queue_end(s)
    if type(quantity) is not int or not 0<quantity<=c['truck_capacity']:raise Conflict('搬运数量超出载量或不是正整数')
    if origin not in s['stock'] or destination not in s['stock'] or origin==destination:raise Conflict('来源与目的地无效')
    if type(departure) is not int or departure<at:raise Conflict('与既有车辆任务时间冲突')
    day=departure//DAY;pending=sum(t['quantity'] for t in s['tasks'] if t['status'] in RESERVED and t['departure_s']//DAY==day)
    if s['moved_by_day'].get(str(day),0)+pending+quantity>c['daily_move_budget']:raise Conflict('当天搬运预算不足')
    empty=distance[node,origin];loaded=distance[origin,destination]
    if not math.isfinite(empty+loaded):raise Conflict('模拟运输图中没有可达路径')
    pickup=departure+math.ceil(empty/(c['truck_speed_kmh']/3.6))
    loaded_at=pickup+quantity*c['handling_seconds_per_bicycle_per_end']
    arrival=loaded_at+math.ceil(loaded/(c['truck_speed_kmh']/3.6))
    finish=arrival+quantity*c['handling_seconds_per_bicycle_per_end']
    if departure%DAY<c['operating_hours'][0]*HOUR or finish>day*DAY+c['operating_hours'][1]*HOUR:
        raise Conflict('超出作业时间预算')
    if finish>min(s['clock']+c['lookahead_minutes']*60,(s['clock']//HOUR+1)*HOUR):raise Conflict('任务到达过晚，超出已有预测覆盖的规划窗口')
    return dict(origin=origin,destination=destination,quantity=quantity,departure_s=departure,pickup_s=pickup,
        loaded_s=loaded_at,arrival_s=arrival,finish_s=finish,empty_distance_m=empty,loaded_distance_m=loaded,
        origin_name=nodes[origin]['name'],destination_name=nodes[destination]['name'],
        origin_parking=nodes[origin]['parking_id'],destination_parking=nodes[destination]['parking_id'])


def proposals(s,cfg,limit=3):
    virtual=copy.deepcopy(s);answer=[];failures=Counter();reason='当前预测没有达到异常阈值的未安排缺口'
    for _ in range(limit):
        booked={t['destination'] for t in virtual['tasks'] if t['status'] in DELIVERING}
        ps=risks(virtual,cfg);receivers=sorted((p for p in ps if p['status']=='shortage' and p['node_id'] not in booked),key=lambda p:-p['shortage'])[:10]
        donors=sorted(ps,key=lambda p:-p['spare'])[:10];best=None
        for dest in receivers:
            for donor in donors:
                q=min(context()[2]['truck_capacity'],math.floor(donor['spare']),math.ceil(dest['shortage']))
                if q<=0 or donor['node_id']==dest['node_id']:
                    failures['来源可用库存扣除预留和后续保留量后不足']+=1;continue
                try:t=task_spec(virtual,donor['node_id'],dest['node_id'],q,int(queue_end(virtual)[0])+60)
                except Conflict as e:failures[str(e)]+=1;continue
                score=min(q,dest['shortage'])/max(1.,(t['finish_s']-t['departure_s'])/60)
                if best is None or score>best['priority']:
                    best=dict(t,priority=score,reason='来源保留后可调出；目的地存在未来窗口缺口',
                        late_warning=dest['first_shortage_minute'] is not None and (t['finish_s']-s['clock'])/60>dest['first_shortage_minute'])
        if best is None:break
        best['id']='proposal-'+hashlib.sha256(json.dumps([s['version'],best],sort_keys=True).encode()).hexdigest()[:16]
        best['status']='pending_confirmation';answer.append(best)
        virtual['tasks'].append(dict(best,status='confirmed'))
    if failures:reason='；'.join(k for k,v in failures.most_common(2))
    if not answer and any(t['status'] in DELIVERING for t in s['tasks']):reason='已有确认任务占用资源或在途；送达后重新规划，仍可能保留未解决的风险'
    return dict(tasks=answer,reason=None if answer else reason,state_version=s['version'])


def confirm(s,cfg,items,version):
    if version!=s['version']:raise Conflict('状态已更新，请重新生成方案')
    if not isinstance(items,list) or not 1<=len(items)<=3:raise Conflict('每次确认1至3项任务')
    for item in items:
        if not isinstance(item,dict):raise Conflict('任务格式无效')
        origin=item.get('origin');dest=item.get('destination');q=item.get('quantity')
        if any(t['destination']==dest and t['status'] in DELIVERING for t in s['tasks']):raise Conflict('目的地已有在途或待执行调入任务，送达后重新规划')
        t=task_spec(s,origin,dest,q,item.get('departure_s'))
        point=next(p for p in risks(s,cfg) if p['node_id']==origin)
        if q>math.floor(point['spare']):raise Conflict('来源库存或后续保留量不足，不能重复分配车辆')
        target=next(p for p in risks(s,cfg) if p['node_id']==dest)
        if q>math.ceil(target['shortage']) or target['shortage']<=0:raise Conflict('超过当前目的地预计缺口，请重新试算')
        t.update(id='task-'+str(len(s['tasks'])+1),status='confirmed',cancel_requested=False,
                 priority=q/max(1.,(t['finish_s']-t['departure_s'])/60),reason='已确认并预留来源车辆；尚未装车')
        s['tasks'].append(t)


def cancel(s,task_id):
    task=next((t for t in s['tasks'] if t['id']==task_id),None)
    if task is None:raise Conflict('任务不存在')
    if task['status'] in TERMINAL:
        if task['status']=='completed':raise Conflict('已完成任务不能撤销实际搬运')
        return
    if task.get('cancel_requested'):return
    task['cancel_requested']=True
    # Later reservations depended on the old truck destination; cancel them
    # explicitly instead of pretending that the truck can teleport.
    for later in s['tasks']:
        if later is not task and later['status']=='confirmed' and later['departure_s']>=task['departure_s']:
            later.update(status='cancelled',reason='前序任务取消，释放资源并重新规划')
    if task['status']=='confirmed':task['status']='cancelled'
    elif task['status']=='to_pickup':task['status']='cancel_empty'
    else:task['status']='cancel_delivery';task['reason']='已装车，继续送达原目的地安全卸车后完成取消'


def task_next(s):
    times=[]
    for t in s['tasks']:
        status=t['status']
        if status=='confirmed':times.append(t['departure_s'])
        elif status in ('to_pickup','cancel_empty'):times.append(t['pickup_s'])
        elif status=='loading':times.append(t['loaded_s'])
        elif status=='transporting':times.append(t['arrival_s'])
        elif status in ('unloading','cancel_delivery'):times.append(t['finish_s'])
    return min(times) if times else math.inf


def progress_tasks(s,cfg):
    changed=True
    while changed:
        changed=False
        for t in s['tasks']:
            status=t['status'];now=s['clock']
            if status=='confirmed' and now>=t['departure_s']:
                if s['truck']['active'] is not None:raise Conflict('调度车已有任务')
                s['truck']['active']=t['id'];t['status']='to_pickup';changed=True
            elif status in ('to_pickup','cancel_empty') and now>=t['pickup_s']:
                s['truck']['node']=t['origin']
                if status=='cancel_empty':t['status']='cancelled';s['truck']['active']=None
                else:
                    q=t['quantity'];s['stock'][t['origin']]-=q;s['truck']['cargo']+=q;t['status']='loading';t['reason']='车辆已转入调度车，正在装车'
                    day=str(t['departure_s']//DAY);s['moved_by_day'][day]=s['moved_by_day'].get(day,0)+q;s['metrics']['moved']+=q
                changed=True
            elif status=='loading' and now>=t['loaded_s']:t['status']='transporting';t['reason']='装车完成，按模拟时延运输';changed=True
            elif status=='transporting' and now>=t['arrival_s']:t['status']='unloading';t['reason']='到达目的地，卸毕后才进入可用库存';s['truck']['node']=t['destination'];changed=True
            elif status in ('unloading','cancel_delivery') and now>=t['finish_s']:
                s['stock'][t['destination']]+=s['truck']['cargo'];s['truck'].update(node=t['destination'],cargo=0,active=None)
                t['status']='cancelled_delivered' if t['cancel_requested'] else 'completed';t['reason']='已安全卸车并计入目的地库存';changed=True
            conserved(s,cfg)


@lru_cache(maxsize=4)
def demands_for_day(day,source='data/simulated/campus_v1'):
    # Only the simulator calls this function; prediction/planning never does.
    return [r for r in map(json.loads,(ROOT/source/'demands.jsonl').open()) if r['day_index']==day]


def elapsed_work(s,to):
    task=next((t for t in s['tasks'] if t['id']==s['truck']['active']),None)
    if task:
        s['metrics']['work_s']+=to-s['clock']
        for lo,hi,distance in [(task['departure_s'],task['pickup_s'],task['empty_distance_m']),
                               (task['loaded_s'],task['arrival_s'],task['loaded_distance_m'])]:
            if hi>lo:s['metrics']['distance_m']+=max(0,min(to,hi)-max(s['clock'],lo))/(hi-lo)*distance
    s['clock']=to


def advance(s,cfg,to,demands=None):
    if type(to) is not int or to<s['clock'] or to-s['clock']>7200:raise Conflict('每次只能向前推进0至120分钟')
    if to>56*DAY:raise Conflict('超出已声明的校园模拟观测范围')
    if demands is None:
        demands=[d for day in range(s['clock']//DAY,to//DAY+1) for d in demands_for_day(day,str(dataset_path(cfg).relative_to(ROOT)))
                 if s['demand_cutoff']<d['time_s']<=to]
    queue=[dict(kind='demand',time_s=d['time_s'],demand=d) for d in demands]+s['returns']+s['retries']
    s['returns']=[];s['retries']=[]
    while True:
        queue.sort(key=lambda x:(x['time_s'],0 if x['kind']=='return' else 1))
        next_at=min(queue[0]['time_s'] if queue else math.inf,task_next(s))
        if next_at>to:break
        elapsed_work(s,max(s['clock'],next_at));progress_tasks(s,cfg)
        while queue and queue[0]['time_s']<=s['clock']:
            e=queue.pop(0);now=e['time_s'];kind=e['kind']
            if kind=='return':
                s['stock'][e['node']]+=1;s['riding']-=1;observe(s,now,e['node'],'return')
            else:
                d=e['demand'];node=e.get('node',d['origin_node']);holds=reserved(s)
                if kind=='demand':s['metrics']['potential']+=1
                if s['stock'][node]-holds[node]>0:
                    s['stock'][node]-=1;s['riding']+=1;s['metrics']['served']+=1;observe(s,now,node,'borrow')
                    travel=math.ceil(context()[1][node,d['destination_node']]/(d['speed_kmh']/3.6))+d['handling_s']
                    queue.append(dict(kind='return',time_s=now+max(60,travel),node=d['destination_node']))
                else:
                    # Same bounded nearby search as the offline simulator.
                    nodes,dist,c=context()
                    nearby=sorted((n for n in s['stock'] if n!=node and n!=d['destination_node'] and
                        nodes[n]['simulation_zone_id']==nodes[node]['simulation_zone_id'] and
                        dist[node,n]<=c['walking_radius_m']),key=lambda n:dist[node,n])[:c['max_nearby_candidates']]
                    candidate=next((n for n in nearby if s['stock'][n]-holds[n]>0),None) if kind=='demand' else None
                    if candidate:queue.append(dict(kind='retry',time_s=now+math.ceil(dist[node,candidate]/c['walking_speed_mps']),node=candidate,demand=d))
                    else:s['metrics']['unmet']+=1;s['last_unmet'][node]=s['last_unmet'].get(node,0)+1
            conserved(s,cfg)
            queue.sort(key=lambda x:(x['time_s'],0 if x['kind']=='return' else 1))
        if next_at==to and task_next(s)>to and (not queue or min(q['time_s'] for q in queue)>to):break
    elapsed_work(s,to);progress_tasks(s,cfg)
    s['returns']=[q for q in queue if q['kind']=='return'];s['retries']=[q for q in queue if q['kind']=='retry']
    s['demand_cutoff']=to
    low=to//HOUR-168;s['history']={k:v for k,v in s['history'].items() if int(k.split('|')[0])>=low}
    conserved(s,cfg)


def read(run):
    with database(run) as db:return load_state(db)


def transact(run,body):
    cfg=read_config(run)
    if not cfg.get('manager_enabled'):raise Conflict('此实例尚未启用决策闭环')
    if body.get('view')!='future':raise Conflict('历史回放只读，不能改写当前状态')
    op=body.get('op_id')
    if not isinstance(op,str) or not 1<=len(op)<=128:raise Conflict('缺少操作幂等标识')
    request=json.dumps(body,sort_keys=True,ensure_ascii=False)
    with database(run) as db:
        db.execute('begin immediate')
        old=db.execute('select request,response from manager_ops where op_id=?',(op,)).fetchone()
        if old:
            if old['request']!=request:raise Conflict('相同操作标识的内容冲突')
            return json.loads(old['response'])
        s=load_state(db);action=body.get('action')
        if action=='confirm':confirm(s,cfg,body.get('tasks'),body.get('state_version'))
        elif action=='cancel':cancel(s,body.get('task_id'))
        elif action=='advance':advance(s,cfg,body.get('to_time_s'))
        else:raise Conflict('不支持的状态操作')
        s['version']+=1;s['updated_at']=stamp();store(db,s,cfg)
        result=dict(ok=True,state_version=s['version'],clock_s=s['clock'])
        db.execute('insert into manager_ops values(?,?,?)',(op,request,json.dumps(result)));db.commit()
        return result


def apply_stream(db,cfg,records):
    s=load_state(db);mapping={p['id']:p['node_id'] for p in cfg['points']}
    for r in records:
        n=mapping[r['point_id']];q=r['quantity'];kind=r['event_type']
        if kind=='borrow':
            if s['stock'][n]-reserved(s)[n]<q:return 'inventory_conflict_reserved_stock'
            s['stock'][n]-=q;s['riding']+=q;s['external_riding']+=q
        else:
            if s['external_riding']<q:return 'inventory_conflict_no_external_riding_bicycles'
            s['stock'][n]+=q;s['riding']-=q;s['external_riding']-=q
        observe(s,s['clock'],n,kind,q)
    s['version']+=1;s['updated_at']=stamp();store(db,s,cfg)
    return None


def snapshot(run):
    cfg=read_config(run)
    with database(run) as db:
        db.execute('begin');s=load_state(db)
        recent=[dict(r) for r in db.execute('select * from events order by rowid desc limit 5')]
        batches=db.execute('select count(*) from batches').fetchone()[0]
        rejected=db.execute('select count(*) from rejected').fetchone()[0]
    error=None
    try:points=risks(s,cfg);plan=proposals(s,cfg)
    except (ValueError,OSError,KeyError) as e:
        error=str(e);holds=reserved(s)
        points=[dict(p,inventory=s['stock'][p['node_id']],reserved=holds[p['node_id']],available_to_borrow=s['stock'][p['node_id']]-holds[p['node_id']],status='unavailable',expected_borrows=None,expected_returns=None) for p in cfg['points']];plan=dict(tasks=[],reason='预测不可用')
    origin=s['clock']//HOUR*HOUR
    meta=dict(cfg['metadata'],reference_time_s=origin,reference_time_label=f'第{origin//DAY+1}日 {origin//HOUR%24:02d}:00起',
        forecast_kind='clock_aligned_model_inference',forecast='Portable inference from fitted MLlib forests using observed completed bins',
        risk='Current inventory plus forecast flows and confirmed transport changes to the current hour end',
        live_updates='Observed completed bins only; no per-event retraining')
    return dict(kind='manager_decision_live',version=s['version'],batches=batches,rejected=rejected,recent=recent,
        snapshot_at=s['updated_at'],updated_at=s['updated_at'],metadata=meta,points=points,bbox=cfg['bbox'],boundary=cfg['boundary'],background=cfg['background'],
        forecast_kind='clock_aligned_model_inference',total_bicycles=cfg['total_bicycles'],in_transit=s['riding'],truck_cargo=s['truck']['cargo'],
        recommendation=None,manager=dict(clock_s=s['clock'],initial_clock_s=s['initial_clock'],pending_walks=len(s['retries']),inventory_updated_at=s['updated_at'],forecast_origin_s=origin,
            window_start_s=s['clock'],window_end_s=origin+HOUR,forecast_error=error,forecast_available=error is None,
            tasks=s['tasks'],plan=plan,truck=s['truck'],metrics=s['metrics'],state_version=s['version'],
            unmet_locations=[dict(node_id=n,point_id=next(p['id'] for p in cfg['points'] if p['node_id']==n),name=context()[0][n]['name'],unmet=count) for n,count in sorted(s['last_unmet'].items(),key=lambda x:-x[1])],
            risk_precision='小时模型与训练期15分钟比例，只提供时段估计',source='campus synthetic'))


def trial(run,body):
    cfg=read_config(run);s=read(run)
    if not cfg.get('manager_enabled'):raise Conflict('此实例尚未启用决策闭环')
    if body.get('view')!='future':raise Conflict('请在未来风险视图试算')
    horizon=body.get('horizon_minutes',60)
    if type(horizon) is not int or not 15<=horizon<=120:raise Conflict('试算窗口应为15至120分钟')
    baseline=copy.deepcopy(s);chosen=copy.deepcopy(s)
    confirm(chosen,cfg,body.get('tasks'),body.get('state_version'))
    proposed=copy.deepcopy(chosen['tasks'][len(s['tasks']):])
    end=s['clock']+horizon*60
    potential=[d for day in range(s['clock']//DAY,end//DAY+1) for d in demands_for_day(day,str(dataset_path(cfg).relative_to(ROOT))) if s['demand_cutoff']<d['time_s']<=end]
    results={};location_counts={};playback={}
    # Both strategies see the same potential arrivals at each sample. Successful
    # rides and their returns remain endogenous to each isolated branch.
    times=sorted({s['clock'],end,*range(s['clock']+300,end,300),
                  *(t[k] for t in chosen['tasks'] for k in ['departure_s','pickup_s','loaded_s','arrival_s','finish_s'] if s['clock']<t[k]<end)})
    def frame(branch):
        holds=reserved(branch)
        return dict(time_s=branch['clock'],metrics=dict(branch['metrics'],pending_walks=len(branch['retries'])),
                    available={n:q-holds[n] for n,q in branch['stock'].items()},
                    stock=dict(branch['stock']),riding=branch['riding'],cargo=branch['truck']['cargo'],
                    unmet={n:q-s['last_unmet'].get(n,0) for n,q in branch['last_unmet'].items()},
                    tasks=[dict(id=t['id'],status=t['status']) for t in branch['tasks']])
    for name,branch in [('no_new_dispatch',baseline),('current_plan',chosen)]:
        branch['metrics']={k:0 for k in s['metrics']};branch['metrics']['potential']=len(s['retries'])
        frames=[frame(branch)]
        for at in times[1:]:
            arrivals=[d for d in potential if branch['clock']<d['time_s']<=at]
            advance(branch,cfg,at,copy.deepcopy(arrivals));frames.append(frame(branch))
        playback[name]=frames
        delta=dict(branch['metrics'])
        delta.update(service_rate=delta['served']/delta['potential'] if delta['potential'] else None,
            pending_walks=len(branch['retries']),transport_km=delta['distance_m']/1000,work_minutes=delta['work_s']/60)
        results[name]=delta
        location_counts[name]={n:count-s['last_unmet'].get(n,0) for n,count in branch['last_unmet'].items()}
    return dict(results=results,potential_demands=len(potential)+len(s['retries']),new_potential_demands=len(potential),carry_over_walks=len(s['retries']),same_potential_hash=hashlib.sha256(json.dumps(potential,sort_keys=True).encode()).hexdigest(),
        source_state_version=s['version'],window_start_s=s['clock'],window_end_s=end,proposed_tasks=proposed,
        playback=playback,playback_interval_s=300,
        location_effects=[dict(node_id=p['node_id'],point_id=p['id'],name=p['name'],baseline=location_counts['no_new_dispatch'].get(p['node_id'],0),plan=location_counts['current_plan'].get(p['node_id'],0)) for p in cfg['points'] if location_counts['no_new_dispatch'].get(p['node_id'],0) or location_counts['current_plan'].get(p['node_id'],0)],
        scope='Isolated synthetic counterfactual; existing commitments honored in both branches',
        limitation='截点仍在步行的请求单列；不代表现实校园收益或旧历史实验重算')
