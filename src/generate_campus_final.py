"""Synthetic SJTU-inspired campus dataset v1; no third-party dependencies."""
import argparse
from collections import Counter, defaultdict
import hashlib
import heapq
import json
import math
from pathlib import Path
import random

ROOT = Path(__file__).resolve().parents[1]
DAY = 86400


def poisson(rng, mean):
    if mean > 40:
        return poisson(rng, mean / 2) + poisson(rng, mean / 2)
    bound, product, count = math.exp(-mean), 1., 0
    while product > bound:
        product *= rng.random()
        count += 1
    return max(0, count - 1)


def generate(output, seed=42, days=56, scenario_path=None):
    if output.exists() and any(output.iterdir()):raise FileExistsError('Use a new directory; generated data will not overwrite existing evidence')
    scenario_path = Path(scenario_path) if scenario_path else ROOT/'config/simulation_scenario.json'
    scenario = json.loads(scenario_path.read_text())
    total_bikes = scenario['total_bikes']
    if type(total_bikes) is not int or total_bikes <= 0:
        raise ValueError('total_bikes must be a positive integer')
    if not 0 <= scenario['initial_dorm_share'] <= 1:
        raise ValueError('initial_dorm_share must be in [0,1]')
    for key in ['weekday_activity_shares', 'weekend_activity_shares', 'class_start_weights']:
        values = scenario[key]
        if not values or any(x < 0 for x in values) or abs(sum(values)-1) > 1e-8:
            raise ValueError(key+' must be nonnegative and sum to one')
    if any(len(scenario[k]) != 6 for k in ['weekday_activity_shares', 'weekend_activity_shares']):
        raise ValueError('six activity shares required')
    if len(scenario['class_start_hours']) != len(scenario['class_start_weights']):
        raise ValueError('class hours and weights differ in length')
    if any(not 0 <= h < 24 for h in scenario['class_start_hours']):
        raise ValueError('class_start_hours must be in [0,24)')
    for key in ['walking_speed_mps', 'class_minutes', 'weekday_activity_legs', 'weekend_activity_legs']:
        if scenario[key] <= 0: raise ValueError(key+' must be positive')
    if scenario['walking_radius_m'] < 0 or type(scenario['max_nearby_candidates']) is not int or scenario['max_nearby_candidates'] < 0:
        raise ValueError('invalid nearby search parameters')
    rng, stock_rng = random.Random(seed), random.Random(seed + 10001)
    source = ROOT / 'data/reference/campus_nodes_draft.json'
    registry = json.loads(source.read_text())['nodes']
    coordinates = {z: (i % 3, 2-i//3) for i, z in enumerate('ABCDEFGHI')}
    # Abstract campus topology, NOT surveyed roads. Eastern boundary crossings at two rows.
    edges = []
    for a, (x,y) in coordinates.items():
        for b, (xx,yy) in coordinates.items():
            if a < b and abs(x-xx)+abs(y-yy)==1 and not (x != xx and min(x,xx)==1 and y==1):
                edges.append((a,b,650))
    def zone_distance(a,b):
        queue, seen = [(0,a)], set()
        while queue:
            d,z = heapq.heappop(queue)
            if z==b:return d
            if z in seen:continue
            seen.add(z)
            for u,v,w in edges:
                if z==u:heapq.heappush(queue,(d+w,v))
                if z==v:heapq.heappush(queue,(d+w,u))
        raise ValueError('Disconnected synthetic zone network')
    assumed_zones = {'BLD-ENERGY08':'B','BLD-CRQ':'H','BLD-C06':'C',
                     **{f'GATE-{i:02d}':z for i,z in enumerate(['G','H','I','F','A','B','C','D','G'],1)}}
    nodes, groups = {}, defaultdict(list)
    for n in registry:
        key = n['node_id']; z = n['proposed_zone_id'] or assumed_zones[key]
        nodes[key] = dict(node_id=key,name=n['node_name'],function_type=n['function_type'],
                          source_entity_type=n['entity_type'], source_url=n['source_url'],
                          proposed_zone_id=n['proposed_zone_id'],simulation_zone_id=z,
                          zone_status='model_assumption_not_surveyed',parking_id='SIM-PARK-'+key,
                          longitude=None,latitude=None,coordinate_system=None,
                          synthetic_access_distance_m=rng.uniform(60,330),
                          attraction_weight=rng.uniform(.7,1.3),is_simulated_parking=True,
                          parking_capacity=None,operating_status='assumed_for_scenario')
        groups[n['function_type']].append(key)
    distances = {}
    for a in nodes:
        for b in nodes:
            distances[a,b] = 0. if a==b else (nodes[a]['synthetic_access_distance_m']+
                zone_distance(nodes[a]['simulation_zone_id'],nodes[b]['simulation_zone_id'])+
                nodes[b]['synthetic_access_distance_m'])
    nearby = {a:sorted([b for b in nodes if b!=a and nodes[b]['simulation_zone_id']==nodes[a]['simulation_zone_id']
                        and distances[a,b]<=scenario['walking_radius_m']],key=lambda b:distances[a,b])[:scenario['max_nearby_candidates']] for a in nodes}
    def pick(kind,origin=None):
        candidates = [x for x in groups[kind] if x!=origin]
        weights=[]
        for x in candidates:
            d=distances[origin,x] if origin else 0
            factor=(.15 if d<300 else .5 if d<600 else 1.) if origin else 1.
            weights.append(nodes[x]['attraction_weight']*math.exp(-d/1500)*factor)
        return rng.choices(candidates,weights)[0]
    demands, activities, weather = [], [], []
    def add_demand(t,a,b,purpose,activity):
        key=f'DEM-{len(demands)+1:08d}'
        rr=random.Random(f'{seed}:{key}:journey')
        demands.append(dict(demand_id=key,time_s=int(t),day_index=int(t)//DAY,
                            weekday=int(t)//DAY%7,origin_node=a,destination_node=b,purpose=purpose,
                            activity_id=activity,speed_kmh=rr.uniform(9,15),handling_s=rr.randint(60,180),
                            observation=0<=t<days*DAY,is_synthetic=True))
        return key
    def leg(start,a,b,purpose,activity):
        # Short journeys are less likely to be cycled; failed/no-cycling legs may be walked.
        d=distances[a,b]
        probability=.15 if d<300 else .5 if d<600 else .95
        if rng.random()<probability:return add_demand(start,a,b,purpose,activity)
        return None
    for day in range(-1,days):
        weekend=day%7>=5
        w=rng.choices(['dry','light_rain','heavy_rain'],[.70,.25,.05])[0]
        weather_factor={'dry':1.,'light_rain':.75,'heavy_rain':.45}[w]
        sigma=math.sqrt(math.log(1+.15**2));variation=rng.lognormvariate(-sigma**2/2,sigma)
        weather.append(dict(day_index=day,weather=w,weather_factor=weather_factor,daily_factor=variation,
                            observation=day>=0,is_synthetic=True))
        # Gross activity-leg budget; actual bicycle demand is lower after mode selection.
        budget=scenario['weekend_activity_legs' if weekend else 'weekday_activity_legs']*weather_factor*variation
        shares=scenario['weekend_activity_shares' if weekend else 'weekday_activity_shares']
        if scenario.get('behavior_model')=='linked_student_day_v2':
            from campus_behavior_v2 import generate_day
            generate_day(day,rng,scenario,nodes,groups,distances,pick,add_demand,activities,weather_factor,variation)
            continue
        for category,share in zip(['class','meal','study','research','sport','outing'],shares):
            for _ in range(poisson(rng,budget*share/2)):
                activity=f'ACT-{len(activities)+1:08d}'
                origin=pick('dormitory')
                if category=='class':
                    destination=pick('teaching',origin)
                    start=rng.choices(scenario['class_start_hours'],scenario['class_start_weights'])[0]*3600+rng.randint(-180,180)
                    end=start+scenario['class_minutes']*60+rng.randint(0,600)
                    depart=start-20*60+rng.randint(-300,300)
                    return_node=pick(rng.choices(['dormitory','dining','library'],[.55,.30,.15])[0],destination)
                elif category=='meal':
                    origin=pick(rng.choices(['dormitory','teaching','library','research'],[.45,.30,.20,.05])[0])
                    destination=pick('dining',origin)
                    hour=rng.choices([7.,11.5,17.5],[.15,.4,.45])[0]
                    depart=int(hour*3600+rng.triangular(-1200,1800,0))
                    start=depart+20*60;end=start+int(rng.triangular(20,70,35)*60)
                    return_node=pick(rng.choices(['dormitory','teaching','library'],[.55,.20,.25])[0],destination)
                elif category=='study':
                    destination=pick('library',origin)
                    depart=int(rng.choices([9.,14.,19.],[.25,.35,.40])[0]*3600+rng.randint(-1200,1200))
                    start=depart+20*60;end=min(23*3600,start+int(rng.triangular(90,300,180)*60))
                    return_node=origin
                elif category=='research':
                    destination=pick('research',origin)
                    depart=int(rng.triangular(7.5,14,9)*3600)
                    start=depart+20*60;end=start+int(rng.triangular(120,600,360)*60)
                    return_node=origin
                elif category=='sport':
                    destination=pick('sports',origin)
                    # Weekly simulated event produces concentrated venue departure.
                    start=(18*3600+rng.randint(-300,300)) if day%7 in [2,5] else int(rng.triangular(15,21,18)*3600)
                    depart=start-20*60;end=start+(90*60 if day%7 in [2,5] else rng.randint(45,120)*60)
                    return_node=origin
                else:
                    destination=pick('gate_transfer',origin)
                    purpose=rng.choices(['eat_out','shopping_errands','leisure'],[.35,.20,.45] if weekend else [.65,.20,.15])[0]
                    if purpose=='eat_out':
                        hour=rng.triangular(10.5,13,11.5) if rng.random()<.4 else rng.triangular(16.5,20,18)
                        stay=int(rng.triangular(45,150,90)*60)
                    else:
                        hour=rng.triangular(9,18,12) if weekend else rng.triangular(14,20,17)
                        stay=int(rng.triangular(120,480,240)*60) if purpose=='leisure' else int(rng.triangular(60,240,120)*60)
                    start=int(hour*3600);depart=start-20*60;end=start+stay;return_node=origin
                absolute_depart=day*DAY+depart;absolute_end=day*DAY+end
                if category=='outing':
                    outbound=leg(absolute_depart,origin,destination,'gate_exit_'+purpose,activity) if rng.random()<.8 else None
                    inbound=leg(absolute_end,destination,return_node,'gate_entry_'+purpose,activity) if rng.random()<.8 else None
                else:
                    outbound=leg(absolute_depart,origin,destination,category+'_arrival',activity)
                    inbound=leg(absolute_end,destination,return_node,category+'_departure',activity)
                activities.append(dict(activity_id=activity,category=category,origin_node=origin,
                                       activity_node=destination,return_node=return_node,
                                       scheduled_start_s=int(day*DAY+start),scheduled_end_s=int(absolute_end),
                                       outbound_demand_id=outbound,inbound_demand_id=inbound,
                                       outing_purpose=purpose if category=='outing' else None,is_synthetic=True))
    demands.sort(key=lambda x:(x['time_s'],x['demand_id']))
    initial=[];inventory={x:[] for x in nodes};location={}
    for i in range(total_bikes):
        choices=groups['dormitory'] if i<round(total_bikes*scenario['initial_dorm_share']) else [x for x in nodes if nodes[x]['function_type']!='dormitory']
        node=stock_rng.choices(choices,[nodes[x]['attraction_weight'] for x in choices])[0]
        bike=f'SIM-BIKE-{i+1:05d}';inventory[node].append(bike);location[bike]=node
        initial.append(dict(bike_id=bike,node_id=node))
    queue=[];sequence=0
    def push(t,priority,kind,payload):
        nonlocal sequence
        sequence+=1;heapq.heappush(queue,(int(t),priority,sequence,kind,payload))
    for d in demands:push(d['time_s'],1,'demand',d)
    for t in range(0,days*DAY+1,1800):push(t,3,'snapshot',None)
    transit={};results=[];trips=[];ledger=[];snapshots=[];previous={x['bike_id']:(-float('inf'),x['node_id']) for x in initial}
    counters=Counter();type_counts=Counter();day_counts=defaultdict(Counter);checks=0
    # Incremental counts validate inventory after every event; full identity check at snapshots.
    available=total_bikes
    output.mkdir(parents=True,exist_ok=True)
    snapshot_file=(output/'inventory.jsonl').open('w')
    def result(d,status,pickup=None,walk=0,served_time=None,bike=None):
        r=dict(demand_id=d['demand_id'],status=status,requested_node=d['origin_node'],actual_pickup_node=pickup,
               walk_seconds=walk,served_time_s=served_time,bike_id=bike,
               observation=d['observation'],strategy_id='no_dispatch')
        results.append(r)
        if d['observation']:
            counters[status]+=1;day_counts[d['day_index']][status]+=1
    while queue:
        t,priority,seq,kind,payload=heapq.heappop(queue)
        if kind=='return':
            bike,node=payload
            assert bike in transit and location[bike]=='TRANSIT'
            del transit[bike];inventory[node].append(bike);location[bike]=node;available+=1
            previous[bike]=(t,node)
            ledger.append(dict(time_s=t,event='return',bike_id=bike,node_id=node))
        elif kind in ['demand','retry']:
            if kind=='demand':
                d=payload;node=d['origin_node'];walk=0
                if d['observation']:type_counts[nodes[node]['function_type']]+=1
                if not inventory[node]:
                    choices=[b for b in nearby[node] if inventory[b] and b!=d['destination_node']]
                    if choices:
                        alternate=choices[0];walk=math.ceil(distances[node,alternate]/scenario['walking_speed_mps'])
                        push(t+walk,2,'retry',(d,alternate,walk))
                        continue
            else:d,node,walk=payload
            if inventory[node]:
                bike=inventory[node].pop();assert location[bike]==node and previous[bike][0]<=t
                available-=1;location[bike]='TRANSIT'
                travel=math.ceil(distances[node,d['destination_node']]/(d['speed_kmh']/3.6))+d['handling_s']
                end=t+max(60,travel);transit[bike]=(end,d['destination_node'])
                push(end,0,'return',(bike,d['destination_node']))
                trips.append(dict(trip_id=f'TRIP-{len(trips)+1:08d}',demand_id=d['demand_id'],bike_id=bike,
                                  start_time_s=t,end_time_s=end,origin_node=node,destination_node=d['destination_node'],
                                  synthetic_distance_m=round(distances[node,d['destination_node']],2),
                                  observation=d['observation'],is_synthetic=True))
                ledger.append(dict(time_s=t,event='borrow',bike_id=bike,node_id=node))
                result(d,'served_nearby' if walk else 'served_here',node,walk,t,bike)
            else:result(d,'no_bike_after_walk' if walk else 'no_bike',walk=walk)
        else:
            all_ids=[b for stock in inventory.values() for b in stock]
            assert len(all_ids)==available and len(set(all_ids))==available
            assert not set(all_ids).intersection(transit)
            for node in nodes:
                snapshot_file.write(json.dumps(dict(time_s=t,node_id=node,available_bikes=len(inventory[node]),
                                         system_in_transit=len(transit),is_synthetic=True))+'\n')
        assert available+len(transit)==total_bikes and available>=0
        checks+=1
    snapshot_file.close()
    assert not transit and len(results)==len(demands)
    assert len(trips)==sum(r['status'].startswith('served') for r in results)
    assert len({x['demand_id'] for x in demands})==len(demands)
    total=sum(counters.values());served=counters['served_here']+counters['served_nearby']
    config=dict(version='campus-synthetic-v2' if scenario.get('behavior_model')=='linked_student_day_v2' else 'campus-synthetic-v1.1',seed=seed,observation_days=days,warmup_days=1,
                calendar='relative day 0 Monday, no actual academic calendar',bikes=total_bikes,
                weekday_gross_activity_legs=scenario['weekday_activity_legs'],weekend_gross_activity_legs=scenario['weekend_activity_legs'],
                distance_model='hypothetical zone graph plus access legs, not measured campus routes',
                walking_radius_m=scenario['walking_radius_m'],walking_speed_mps=scenario['walking_speed_mps'],max_alternatives=scenario['max_nearby_candidates'],max_retries=1,
                nearby_information='current stock known, not reserved during walk',
                split=dict(train_days=[0,27],validation_days=[28,41],test_days=[42,55]) if days==56 else None,
                generator_only_parameters=scenario,
                generator_sha256=hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
                behavior_builder_sha256=hashlib.sha256((ROOT/'src/campus_behavior_v2.py').read_bytes()).hexdigest() if scenario.get('behavior_model')=='linked_student_day_v2' else None,
                note='No dispatch. No hard parking capacity. No bikes outside campus. No real user identity.')
    summary=dict(nodes=len(nodes),node_types=dict(Counter(x['function_type'] for x in nodes.values())),
                 observation_demands=total,observation_trips=served,unmet_demands=total-served,
                 service_rate=served/total,results=dict(counters),origin_types=dict(type_counts),
                 core_origin_share=sum(v for k,v in type_counts.items() if k in ['dormitory','teaching','dining','library'])/total,
                 daily=[dict(day_index=d,**day_counts[d]) for d in range(days)],
                 cross_day_activities=sum(a['scheduled_start_s']//DAY!=a['scheduled_end_s']//DAY for a in activities),
                 event_conservation_checks=checks,validation_passed=True)
    for name,records in [('nodes',list(nodes.values())),('initial_bikes',initial),('weather',weather),
                         ('activities',activities),('demands',demands),('outcomes',results),('trips',trips),('bike_events',ledger)]:
        with (output/(name+'.jsonl')).open('w') as f:
            for x in records:f.write(json.dumps(x,ensure_ascii=False)+'\n')
    for name,obj in [('config',config),('summary',summary),('synthetic_zone_edges',edges)]:
        (output/(name+'.json')).write_text(json.dumps(obj,ensure_ascii=False,indent=2)+'\n')
    hashes={p.name:hashlib.sha256(p.read_bytes()).hexdigest() for p in sorted(output.iterdir()) if p.is_file() and p.name!='manifest.json'}
    (output/'manifest.json').write_text(json.dumps(hashes,indent=2)+'\n')
    return summary


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--seed',type=int,default=42)
    parser.add_argument('--days',type=int,default=56)
    parser.add_argument('--scenario',type=Path,default=ROOT/'config/simulation_scenario.json')
    parser.add_argument('--output',type=Path,default=ROOT/'data/simulated/campus_v1')
    args=parser.parse_args()
    if not 1<=args.days<=56:parser.error('--days must be 1..56')
    s=generate(args.output,args.seed,args.days,args.scenario)
    print(json.dumps({k:v for k,v in s.items() if k!='daily'},ensure_ascii=False,indent=2))
