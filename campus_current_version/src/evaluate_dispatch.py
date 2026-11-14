"""Counterfactual event simulation with constrained, one-truck rebalancing.

All strategies see the same fixed potential demands and initial bicycle IDs.
Only the simulator reads future demand/speed data; policies receive past completed
events, present stock, static transport assumptions and learned model parameters.
"""
import argparse
from collections import Counter, defaultdict
import hashlib
import heapq
import json
import math
from pathlib import Path
import time

from forecast_runtime import Forest
from prepare_forecast_inputs import feature_row, sha256_file

DAY = 86400
HOUR = 3600


def rows(path):
    with Path(path).open() as stream:
        for line in stream:
            yield json.loads(line)


def distances_for(nodes, edges):
    zones = sorted({n['simulation_zone_id'] for n in nodes.values()})
    distances = {(a, b): 0 if a == b else float('inf') for a in zones for b in zones}
    for a, b, length in edges:
        distances[a, b] = distances[b, a] = length
    for k in zones:
        for a in zones:
            for b in zones:
                distances[a, b] = min(distances[a, b], distances[a, k] + distances[k, b])
    return {(a, b): 0.0 if a == b else
            nodes[a]['synthetic_access_distance_m'] + distances[nodes[a]['simulation_zone_id'], nodes[b]['simulation_zone_id']]
            + nodes[b]['synthetic_access_distance_m'] for a in nodes for b in nodes}


def fixed_templates(source, config, nodes):
    borrow, returns = Counter(), Counter()
    low, high = config['training_start_day'], config['training_end_day']
    for trip in rows(source / 'trips.jsonl'):
        for field, node, counter in [('start_time_s', 'origin_node', borrow), ('end_time_s', 'destination_node', returns)]:
            day = trip[field] // DAY
            if low <= day < high:
                counter[trip[node], day % 7, trip[field] // HOUR % 24] += 1
    weekdays = Counter(day % 7 for day in range(low, high))
    templates = {}
    for weekday in range(7):
        for hour in range(*config['operating_hours']):
            net = {node: (borrow[node, weekday, hour] - returns[node, weekday, hour]) / weekdays[weekday] for node in nodes}
            donor = min(net, key=net.get); receiver = max(net, key=net.get)
            amount = min(config['truck_capacity'], max(0, math.floor(-net[donor])), max(0, math.ceil(net[receiver])))
            if amount and donor != receiver:
                templates[weekday, hour] = (donor, receiver, amount)
    return templates


class Policy:
    def __init__(self, kind, config, nodes, distances, templates, forests):
        self.kind, self.config, self.nodes = kind, config, nodes
        self.distances, self.templates, self.forests = distances, templates, forests

    def predict(self, t, borrow, returns):
        hour = t // HOUR
        if self.kind == 'historical':
            return {n: (borrow[hour - 168, n], returns[hour - 168, n]) for n in self.nodes}
        predictions = {}
        for node, metadata in self.nodes.items():
            features = feature_row(node, hour, metadata['function_type'], borrow, returns)
            predictions[node] = tuple(max(0, model.predict(features)) for model in self.forests)
        return predictions

    def task(self, t, stock, borrow, returns, vehicle_node, remaining, *, eligible_receivers=None):
        cfg = self.config
        if self.kind == 'none' or remaining <= 0:
            return None
        if self.kind == 'fixed':
            template = self.templates.get((t // DAY % 7, t // HOUR % 24))
            candidates = [(*template, None)] if template else []
        else:
            forecasts = self.predict(t, borrow, returns)
            gap = {n: max(0, b - r - stock[n]) for n, (b, r) in forecasts.items()}
            spare = {n: max(0, min(stock[n] - cfg['reserve_bicycles'],
                                     stock[n] + r - b - cfg['reserve_bicycles'])) for n, (b, r) in forecasts.items()}
            receivers = sorted((n for n in gap if eligible_receivers is None or n in eligible_receivers),
                               key=gap.get, reverse=True)[:12]
            donors = sorted(spare, key=spare.get, reverse=True)[:12]
            candidates = []
            for receiver in receivers:
                if gap[receiver] <= 0:
                    continue
                for donor in donors:
                    amount = min(cfg['truck_capacity'], remaining,
                                 math.floor(spare[donor]), math.ceil(gap[receiver]))
                    if donor != receiver and amount > 0:
                        candidates.append((donor, receiver, amount, gap[receiver]))
        best = None
        for donor, receiver, requested, gap in candidates:
            amount = min(requested, remaining, cfg['truck_capacity'],
                         max(0, stock[donor] - cfg['reserve_bicycles']))
            if not amount:
                continue
            empty = self.distances[vehicle_node, donor]
            loaded = self.distances[donor, receiver]
            drive_s = math.ceil((empty + loaded) / (cfg['truck_speed_kmh'] / 3.6))
            duration = drive_s + 2 * amount * cfg['handling_seconds_per_bicycle_per_end']
            deadline = min(t + cfg['lookahead_minutes'] * 60,
                           t // DAY * DAY + cfg['operating_hours'][1] * HOUR)
            if t + duration > deadline:
                continue
            expected = min(amount, gap) if gap is not None else amount
            score = expected / max(duration / 60, 1)
            option = dict(origin=donor, destination=receiver, planned_quantity=amount,
                          empty_distance_m=empty, loaded_distance_m=loaded,
                          estimated_duration_s=duration, expected_shortage_relief=expected,
                          priority_score=score, planned_at=t, expected_arrival_s=t + duration)
            if best is None or option['priority_score'] > best['priority_score']:
                best = option
        return best


def simulate(source, config, policy_kind, model_paths=None):
    started = time.perf_counter()
    nodes = {n['node_id']: n for n in rows(source / 'nodes.jsonl')}
    distance = distances_for(nodes, json.loads((source / 'synthetic_zone_edges.json').read_text()))
    nearby = {a: sorted([b for b in nodes if a != b and nodes[a]['simulation_zone_id'] == nodes[b]['simulation_zone_id']
                        and distance[a, b] <= config['walking_radius_m']], key=lambda b: distance[a, b])[:config['max_nearby_candidates']] for a in nodes}
    templates = fixed_templates(source, config, nodes)
    forests = [Forest(p) for p in model_paths] if policy_kind == 'model' else None
    policy = Policy(policy_kind, config, nodes, distance, templates, forests)
    stock = {node: [] for node in nodes}
    for bike in rows(source / 'initial_bikes.jsonl'):
        stock[bike['node_id']].append(bike['bike_id'])
    total_bikes = sum(map(len, stock.values()))
    borrow, returns, transit = Counter(), Counter(), {}
    queue, sequence = [], 0
    def push(t, priority, kind, payload):
        nonlocal sequence
        sequence += 1
        heapq.heappush(queue, (int(t), priority, sequence, kind, payload))
    demands = list(rows(source / 'demands.jsonl'))
    for demand in demands:
        push(demand['time_s'], 1, 'demand', demand)
    low, high = config['test_start_day'], config['test_end_day']
    for day in range(low, high):
        for t in range(day * DAY + config['operating_hours'][0] * HOUR,
                       day * DAY + config['operating_hours'][1] * HOUR,
                       config['decision_interval_minutes'] * 60):
            push(t, 3, 'decision', None)
    vehicle = dict(node=config['depot_node'], busy=False, cargo=[])
    tasks, daily = [], defaultdict(Counter)
    per_node = defaultdict(Counter)
    all_observed = Counter()
    total_available = total_bikes
    checks = 0
    snapshots = []
    def outcome(demand, status):
        if demand['observation']:
            all_observed[status] += 1
        if low <= demand['day_index'] < high and demand['observation']:
            daily[demand['day_index']][status] += 1
            per_node[demand['origin_node']][status] += 1
    while queue:
        t, priority, seq, kind, payload = heapq.heappop(queue)
        if kind == 'return':
            bike, node = payload
            assert bike in transit
            del transit[bike]
            stock[node].append(bike); total_available += 1
            returns[t // HOUR, node] += 1
        elif kind in ('demand', 'retry'):
            if kind == 'demand':
                demand, node, walk = payload, payload['origin_node'], 0
                if not stock[node]:
                    choices = [n for n in nearby[node] if stock[n] and n != demand['destination_node']]
                    if choices:
                        node = choices[0]
                        walk = math.ceil(distance[demand['origin_node'], node] / config['walking_speed_mps'])
                        push(t + walk, 2, 'retry', (demand, node, walk))
                        continue
            else:
                demand, node, walk = payload
            if stock[node]:
                bike = stock[node].pop(); total_available -= 1
                travel = math.ceil(distance[node, demand['destination_node']] / (demand['speed_kmh'] / 3.6)) + demand['handling_s']
                transit[bike] = demand['destination_node']
                push(t + max(60, travel), 0, 'return', (bike, demand['destination_node']))
                borrow[t // HOUR, node] += 1
                outcome(demand, 'served_nearby' if walk else 'served_here')
            else:
                outcome(demand, 'no_bike_after_walk' if walk else 'no_bike')
        elif kind == 'decision':
            day = t // DAY
            if not vehicle['busy']:
                proposal = policy.task(t, {n: len(stock[n]) for n in stock}, borrow, returns,
                                       vehicle['node'], config['daily_move_budget'] - daily[day]['moved'])
                if proposal:
                    proposal.update(task_id=f'{policy_kind}-{len(tasks)+1:04d}', status='planned',
                                    quantity_loaded=0, start_day=day)
                    tasks.append(proposal)
                    vehicle['busy'] = True
                    pickup_t = t + math.ceil(proposal['empty_distance_m'] / (config['truck_speed_kmh'] / 3.6))
                    push(pickup_t, 3, 'pickup', proposal)
            if t // HOUR % 24 in (8, 12, 18):
                snapshots.append(dict(time_s=t, inventory={n: len(stock[n]) for n in nodes},
                                      truck_cargo=len(vehicle['cargo']), user_transit=len(transit)))
        elif kind == 'pickup':
            task = payload; origin = task['origin']; day = task['start_day']
            quantity = min(task['planned_quantity'], max(0, len(stock[origin]) - config['reserve_bicycles']),
                           config['daily_move_budget'] - daily[day]['moved'])
            vehicle['node'] = origin
            daily[day]['transport_m'] += task['empty_distance_m']
            daily[day]['work_s'] += math.ceil(task['empty_distance_m'] / (config['truck_speed_kmh'] / 3.6))
            if not quantity:
                task['status'] = 'cancelled_no_stock_at_pickup'; vehicle['busy'] = False
            else:
                vehicle['cargo'] = [stock[origin].pop() for _ in range(quantity)]
                total_available -= quantity
                daily[day]['moved'] += quantity
                task['quantity_loaded'] = quantity; task['status'] = 'in_transit'
                duration = (math.ceil(task['loaded_distance_m'] / (config['truck_speed_kmh'] / 3.6))
                            + 2 * quantity * config['handling_seconds_per_bicycle_per_end'])
                task['actual_arrival_s'] = t + duration
                daily[day]['work_s'] += duration
                daily[day]['transport_m'] += task['loaded_distance_m']
                push(t + duration, 0, 'delivery', task)
        elif kind == 'delivery':
            task = payload
            assert len(vehicle['cargo']) == task['quantity_loaded']
            stock[task['destination']].extend(vehicle['cargo'])
            total_available += len(vehicle['cargo'])
            vehicle.update(node=task['destination'], cargo=[], busy=False)
            task['status'] = 'delivered'
            assert task['quantity_loaded'] <= config['truck_capacity']
            assert task['actual_arrival_s'] <= task['start_day'] * DAY + config['operating_hours'][1] * HOUR
        assert total_available + len(transit) + len(vehicle['cargo']) == total_bikes
        checks += 1
    assert not transit and not vehicle['cargo']
    identities = [bike for bikes in stock.values() for bike in bikes]
    assert len(identities) == len(set(identities)) == total_bikes
    totals = Counter()
    day_rows = []
    for day in range(low, high):
        counter = daily[day]
        served = counter['served_here'] + counter['served_nearby']
        missed = counter['no_bike'] + counter['no_bike_after_walk']
        assert counter['moved'] <= config['daily_move_budget']
        row = dict(day_index=day, served=served, unmet=missed, potential_demands=served + missed,
                   service_rate=served / (served + missed), moved=counter['moved'],
                   transport_km=counter['transport_m'] / 1000, work_minutes=counter['work_s'] / 60)
        day_rows.append(row)
        totals.update({k: row[k] for k in ['served', 'unmet', 'potential_demands', 'moved', 'transport_km', 'work_minutes']})
    report = dict(strategy=policy_kind, totals=dict(totals), daily=day_rows,
                  per_node={n: dict(counts) for n, counts in per_node.items()}, tasks=tasks,
                  snapshots=snapshots, all_observed_counts=dict(all_observed),
                  event_conservation_checks=checks, final_unique_bicycles=len(set(identities)),
                  elapsed_seconds=time.perf_counter() - started)
    report['totals']['service_rate'] = totals['served'] / totals['potential_demands']
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source', type=Path, default=Path('data/simulated/campus_v1'))
    parser.add_argument('--config', type=Path, default=Path('config/dispatch_v1.json'))
    parser.add_argument('--models', type=Path, default=Path('outputs/dispatch_models_v1'))
    parser.add_argument('--output', type=Path, default=Path('outputs/dispatch_v1'))
    args = parser.parse_args()
    config = json.loads(args.config.read_text())
    if config['truck_count'] != 1 or config['decision_interval_minutes'] != 60:
        raise ValueError('Initial version supports one truck and hour-aligned decisions only')
    model_paths = [args.models / key / 'runtime_model.json' for key in ['borrow', 'return']]
    # Refuse silent v1-model/v2-data mixing before creating experiment output.
    for key in ['borrow', 'return']:
        metrics = json.loads((args.models/key/'metrics.json').read_text())
        expected = metrics['input_contract']['source_sha256']
        for name in ['nodes.jsonl', 'trips.jsonl']:
            if expected.get(name) != sha256_file(args.source/name):
                raise ValueError('Model/source lineage mismatch: '+key+'/'+name)
        test_range = metrics['input_contract']['split_ranges_s']['test']
        if test_range != [config['test_start_day']*DAY, config['test_end_day']*DAY]:
            raise ValueError('Model/config test range mismatch')
    args.output.mkdir(parents=True, exist_ok=False)
    results = {}
    for strategy in ['none', 'fixed', 'historical', 'model']:
        result = simulate(args.source, config, strategy, model_paths)
        if strategy == 'none':
            original = json.loads((args.source / 'summary.json').read_text())['results']
            assert result['all_observed_counts'] == original, (result['all_observed_counts'], original)
        (args.output / (strategy + '.json')).write_text(json.dumps(result, ensure_ascii=False, separators=(',', ':')) + '\n')
        results[strategy] = {**result['totals'], 'tasks': len(result['tasks']),
                             'conservation_checks': result['event_conservation_checks'],
                             'elapsed_seconds': result['elapsed_seconds']}
        print(strategy, json.dumps(results[strategy], ensure_ascii=False), flush=True)
    assert len({r['potential_demands'] for r in results.values()}) == 1
    baseline = results['none']
    for result in results.values():
        result['unmet_reduction'] = baseline['unmet'] - result['unmet']
        result['service_rate_gain_percentage_points'] = (result['service_rate'] - baseline['service_rate']) * 100
        result['avoided_unmet_per_move'] = result['unmet_reduction'] / result['moved'] if result['moved'] else None
    summary = dict(results=results, config=config,
        source_sha256={name: sha256_file(args.source / name) for name in ['demands.jsonl', 'initial_bikes.jsonl', 'nodes.jsonl']},
        model_sha256={str(path): sha256_file(path) for path in model_paths},
        baseline_reproduction=True, common_potential_demand_verified=True,
        evaluation=f"Days [{config['test_start_day']}, {config['test_end_day']}); policies use their own past successful history. No dispatch before test.",
        config_sha256=sha256_file(args.config), code_sha256=sha256_file(Path(__file__)),
        paired_daily=[dict(day_index=a['day_index'], strategy=strategy,
                          unmet_reduction=a['unmet']-b['unmet'],
                          extra_work_minutes=b['work_minutes']-a['work_minutes'])
                      for strategy in ['fixed', 'historical', 'model']
                      for a,b in zip(json.loads((args.output/'none.json').read_text())['daily'],
                                     json.loads((args.output/(strategy+'.json')).read_text())['daily'])],
        limitations=['Synthetic topology and demand, not real operational benefit',
                     'Successful-trip forecasts are availability-censored; latent demand not identified',
                     'Single hypothetical truck and fixed one-hour forecast horizon',
                     'Past test results previously inspected; this is exploratory, needs new scenario validation',
                     'Manual geographic map outlines are unrelated to this hypothetical transport distance graph'])
    (args.output / 'comparison.json').write_text(json.dumps(summary, ensure_ascii=False, indent=2) + '\n')


if __name__ == '__main__':
    main()
