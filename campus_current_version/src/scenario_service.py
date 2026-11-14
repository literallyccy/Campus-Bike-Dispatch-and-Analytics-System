"""Persistent isolated decision scenarios. Existing primary run is never mutated."""
import copy
import fcntl
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import sqlite3
import uuid
from contextlib import contextmanager
import manager_loop as M
from scenario_decisions import advice, enrich


def digest(value):
    return hashlib.sha256(json.dumps(value,sort_keys=True,ensure_ascii=False).encode()).hexdigest()


def root(run):
    return Path(run)/'decision_scenarios'


@contextmanager
def lock(run):
    base=root(run);base.mkdir(exist_ok=True)
    with (base/'registry.lock').open('a') as f:
        fcntl.flock(f,fcntl.LOCK_EX);yield base


def resolve(run,sid):
    if not isinstance(sid,str) or not re.fullmatch(r's-[0-9a-f]{32}',sid):raise M.Conflict('场景标识无效')
    p=root(run)/sid
    if not (p/'scenario.json').is_file():raise M.Conflict('场景不存在')
    return p


def frames(run):
    cfg=M.read_config(run)
    p=M.ROOT/cfg['metadata']['replay_path']
    if not p.is_file():raise M.Conflict('历史库存输入不可用')
    return json.loads(p.read_text())['frames']


def catalogue(run):
    cfg=M.read_config(run);available=[]
    # Only complete historical snapshots from the configured, existing replay.
    for f in frames(run):
        t=f['metadata']['reference_time_s']
        if type(t) is int and t%3600==0 and len(f['points'])==len(cfg['points']):
            available.append(dict(time_s=t,label=f'第{t//86400+1}日 {t//3600%24:02d}:00'))
    base=root(run)
    saved=[]
    for path in base.glob('s-*/scenario.json'):
        info=json.loads(path.read_text())
        try:
            with sqlite3.connect((path.parent/'state.sqlite').as_uri()+'?mode=ro',uri=True) as db:
                state=M.load_state(db);draft=read_json(db,'drafts');trial=read_json(db,'trials');decision=read_decision(db)
                active=any(t['status'] not in M.TERMINAL for t in state['tasks'])
                fresh=trial and trial['source_state_version']==state['version'] and trial['draft_revision']==draft['revision']
                retained=decision and decision['action']=='retain' and decision['state_version']==state['version'] and decision['draft_revision']==draft['revision']
                status='已确认' if active else '不新增调运' if retained else '已模拟' if fresh else '草稿待模拟' if draft['tasks'] else '已执行' if state['tasks'] else '未制定方案'
                times=[info['created_at'],state.get('updated_at',''),draft.get('updated_at',''),(trial or {}).get('created_at',''),(decision or {}).get('recorded_at','')]
                info.update(clock_s=state['clock'],status=status,updated_at=max(times))
        except (sqlite3.Error,ValueError,KeyError) as error:
            info.update(status='读取失败',error=str(error),updated_at=info['created_at'])
        saved.append(info)
    saved.sort(key=lambda x:(x['updated_at'],x['id']),reverse=True)
    return dict(times=available,scenarios=saved)


def signatures(cfg):
    # Bind confirmation to actual model/profile/config bytes, not just declared names.
    paths=list(cfg['metadata']['model_sha256'])
    profile=cfg['metadata'].get('risk_profile_path')
    if profile:paths.append(profile)
    paths.extend(str((M.dataset_path(cfg)/name).relative_to(M.ROOT)) for name in ['trips.jsonl','demands.jsonl'])
    paths.append('config/dispatch_v1.json')
    paths.extend(p for p in ['data/simulated/campus_v1/nodes.jsonl','data/simulated/campus_v1/synthetic_zone_edges.json'] if (M.ROOT/p).is_file())
    return digest(dict(config=cfg,files={p:hashlib.sha256((M.ROOT/p).read_bytes()).hexdigest() for p in paths}))


def create(run,time_s,fresh=False):
    if type(time_s) is not int:raise M.Conflict('请选择有效时段')
    with lock(run) as base:
        if not fresh:
            for info in catalogue(run)['scenarios']:
                if info['start_s']==time_s:return view(run,info['id'])
        frame=next((f for f in frames(run) if f['metadata']['reference_time_s']==time_s),None)
        if frame is None or time_s%3600:raise M.Conflict('该时段没有完整历史库存')
        cfg=copy.deepcopy(M.read_config(run));byid={p['node_id']:p for p in frame['points']}
        if set(byid)!={p['node_id'] for p in cfg['points']}:raise M.Conflict('地点集合与输入不一致')
        for p in cfg['points']:
            n=byid[p['node_id']]['inventory']
            if type(n) is not int or n<0:raise M.Conflict('历史库存无效')
            p['initial_inventory']=n
        cfg['initial_in_transit']=frame['in_transit'];cfg['metadata']['reference_time_s']=time_s
        cfg['manager_enabled']=True
        M.demands_for_day.cache_clear()
        if hasattr(M.context,'cache_clear'):M.context.cache_clear()
        sig=signatures(cfg)
        sid='s-'+uuid.uuid4().hex;temporary=base/('.creating-'+sid);temporary.mkdir()
        try:
            (temporary/'config.json').write_text(json.dumps(cfg,ensure_ascii=False))
            with M.database(temporary) as db:
                db.executescript('create table stock(point_id text primary key,inventory integer); create table events(event_id text); create table batches(batch_id integer); create table rejected(reason text);')
                db.executemany('insert into stock values(?,?)',[(p['id'],p['initial_inventory']) for p in cfg['points']])
                M.initialize(db,cfg)  # Validates active returns and conservation, uses past observations.
                db.executescript('create table drafts(id integer primary key,payload text); create table trials(id text primary key,payload text); create table confirmations(id text primary key,payload text);')
                db.execute('insert into drafts values(1,?)',(json.dumps(dict(revision=0,tasks=[],base_version=0)),))
            info=dict(id=sid,start_s=time_s,created_at=M.stamp(),source='historical_no_dispatch',signature=sig,source_frame_hash=digest(frame))
            (temporary/'scenario.json').write_text(json.dumps(info,ensure_ascii=False))
            os.replace(temporary,base/sid)
        except Exception:
            shutil.rmtree(temporary);raise
    return view(run,sid)


def read_json(db,table):
    row=db.execute(f'select payload from {table} order by rowid desc limit 1').fetchone()
    return json.loads(row[0]) if row else None


def read_decision(db):
    exists=db.execute("select 1 from sqlite_master where type='table' and name='decisions'").fetchone()
    return read_json(db,'decisions') if exists else None


def view(run,sid):
    p=resolve(run,sid);v=M.snapshot(p)
    v['processing']=dict(status='running',mode='isolated_frozen_model_inference')
    with M.database(p) as db:
        v['scenario']=dict(json.loads((p/'scenario.json').read_text()),draft=read_json(db,'drafts'),trial=enrich(read_json(db,'trials')),confirmation=read_json(db,'confirmations'),decision=read_decision(db))
    return v


def check_version(s,body):
    if body.get('state_version')!=s['version']:raise M.Conflict('场景状态已改变，请重新读取')


def _operation(run,body):
    action=body.get('action')
    if action=='create':return create(run,body.get('time_s'),body.get('fresh') is True)
    sid=body.get('scenario_id');p=resolve(run,sid);cfg=M.read_config(p)
    op=body.get('op_id')
    if not isinstance(op,str) or not 1<=len(op)<=128:raise M.Conflict('缺少操作标识')
    # Serialize scenario operations and trial reads; old primary writes remain separate.
    with lock(run):
        with M.database(p) as db:
            db.execute('begin immediate')
            encoded=json.dumps(body,sort_keys=True,ensure_ascii=False)
            old=db.execute('select request,response from manager_ops where op_id=?',(op,)).fetchone()
            if old:
                if old['request']!=encoded:raise M.Conflict('操作标识重复但内容不同')
                return json.loads(old['response'])
            s=M.load_state(db);check_version(s,body)
            if signatures(cfg)!=json.loads((p/'scenario.json').read_text())['signature']:raise M.Conflict('场景输入已变化，请新建副本')
            draft=read_json(db,'drafts')
            result=dict(ok=True)
            if action=='draft':
                if body.get('revision')!=draft['revision']:raise M.Conflict('草稿已被修改，请重新读取')
                tasks=body.get('tasks')
                if not isinstance(tasks,list) or len(tasks)>3 or any(not isinstance(t,dict) for t in tasks):raise M.Conflict('最多保存3项任务')
                draft=dict(revision=draft['revision']+1,tasks=tasks,base_version=s['version'],updated_at=M.stamp())
                db.execute('update drafts set payload=? where id=1',(json.dumps(draft,ensure_ascii=False),));result['draft']=draft
            elif action=='trial':
                if body.get('revision')!=draft['revision']:raise M.Conflict('草稿版本不一致')
                if not draft['tasks']:raise M.Conflict('请先添加任务')
                tasks=draft['tasks'];horizon=body.get('horizon_minutes',60)
                # M.trial reads the committed state through a separate read-only connection.
                tested=M.trial(p,dict(view='future',tasks=tasks,state_version=s['version'],horizon_minutes=horizon))
                suggested=M.proposals(s,cfg)['tasks'];system=None
                if suggested:
                    system=M.trial(p,dict(view='future',tasks=suggested,state_version=s['version'],horizon_minutes=horizon))
                    if system['same_potential_hash']!=tested['same_potential_hash']:raise M.Conflict('策略需求口径不一致')
                tid='trial-'+uuid.uuid4().hex
                result=dict(tested,id=tid,scenario_id=sid,draft_revision=draft['revision'],draft_hash=digest(tasks),signature=signatures(cfg),system=system,created_at=M.stamp())
                db.execute('insert into trials values(?,?)',(tid,json.dumps(result,ensure_ascii=False)))
            elif action in ('confirm','retain'):
                row=db.execute('select payload from trials where id=?',(body.get('trial_id'),)).fetchone()
                if not row:raise M.Conflict('该场景没有此试算')
                t=json.loads(row[0])
                if (t['source_state_version']!=s['version'] or t['draft_revision']!=draft['revision'] or t['draft_hash']!=digest(draft['tasks']) or t['signature']!=signatures(cfg)):raise M.Conflict('试算已失效，请重新模拟')
                assessment=advice(t)
                reason=body.get('reason','')
                if not isinstance(reason,str) or len(reason.strip())>300:raise M.Conflict('选择理由最多300字')
                reason=reason.strip()
                if action=='confirm' and assessment['requires_reason'] and len(reason)<4:raise M.Conflict('当前试算不推荐新增调运；仍要执行请填写至少4字理由')
                if action=='confirm':
                    start=len(s['tasks']);M.confirm(s,cfg,t['proposed_tasks'],s['version'])
                    s['version']+=1;s['updated_at']=M.stamp();M.store(db,s,cfg)
                    result=dict(ok=True,trial_id=t['id'],scenario_id=sid,state_version=s['version'],tasks=copy.deepcopy(s['tasks'][start:]),confirmed_at=M.stamp(),reason=reason,assessment=assessment)
                    db.execute('insert into confirmations values(?,?)',(op,json.dumps(result,ensure_ascii=False)))
                else:
                    result=dict(ok=True,trial_id=t['id'],scenario_id=sid,state_version=s['version'])
                decision=dict(action=action,trial_id=t['id'],state_version=s['version'],draft_revision=draft['revision'],reason=reason,assessment=assessment,recorded_at=M.stamp())
                db.execute('create table if not exists decisions(id text primary key,payload text)')
                db.execute('insert into decisions values(?,?)',(op,json.dumps(decision,ensure_ascii=False)))
                result['decision']=decision
            elif action in ('advance','cancel'):
                if action=='advance':M.advance(s,cfg,body.get('to_time_s'))
                else:M.cancel(s,body.get('task_id'))
                s['version']+=1;s['updated_at']=M.stamp();M.store(db,s,cfg);result['state_version']=s['version']
            else:raise M.Conflict('不支持的场景操作')
            db.execute('insert into manager_ops values(?,?,?)',(op,encoded,json.dumps(result,ensure_ascii=False)))
            db.commit()
    return result


def operation(run,body):
    """Keep rejected operations as evidence without committing partial state."""
    try:
        return _operation(run,body)
    except (M.Conflict, ValueError, KeyError, TypeError, OSError) as error:
        with lock(run) as base:
            with (base/'failed_operations.jsonl').open('a') as stream:
                stream.write(json.dumps(dict(at=M.stamp(), action=body.get('action'),
                    scenario_id=body.get('scenario_id'), op_id=body.get('op_id'),
                    error=str(error)),ensure_ascii=False)+'\n')
        raise
