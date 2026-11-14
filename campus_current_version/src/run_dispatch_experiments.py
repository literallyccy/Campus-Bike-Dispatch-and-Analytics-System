"""Independent seeded transfer checks and one-factor dispatch sensitivity."""
import argparse
import json
from pathlib import Path
import statistics

from evaluate_dispatch import simulate
from generate_campus_final import generate
from prepare_forecast_inputs import sha256_file


def write(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + '\n')


def comparison(source, config, model_paths):
    result = {}
    for strategy in ['none', 'fixed', 'historical', 'model']:
        run = simulate(source, config, strategy, model_paths)
        if strategy == 'none':
            expected = json.loads((source / 'summary.json').read_text())['results']
            assert run['all_observed_counts'] == expected
        result[strategy] = dict(totals=run['totals'], daily=run['daily'],
                                checks=run['event_conservation_checks'], tasks=len(run['tasks']))
    assert len({r['totals']['potential_demands'] for r in result.values()}) == 1
    baseline = result['none']['totals']
    for run in result.values():
        run['totals']['unmet_reduction'] = baseline['unmet'] - run['totals']['unmet']
        run['totals']['service_gain_pp'] = 100 * (run['totals']['service_rate'] - baseline['service_rate'])
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, default=Path('outputs/dispatch_experiments_v1'))
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[1]
    spec = json.loads((root / 'config/dispatch_experiments_v1.json').read_text())
    base = json.loads((root / 'config/dispatch_v1.json').read_text())
    models = [root / 'outputs/dispatch_models_v1' / target / 'runtime_model.json' for target in ['borrow', 'return']]
    manifest = dict(specification=spec,
                    frozen_policy_sha256=sha256_file(root / 'src/evaluate_dispatch.py'),
                    generator_sha256=sha256_file(root / 'src/generate_campus_final.py'),
                    model_sha256={str(p.relative_to(root)): sha256_file(p) for p in models})
    args.output.mkdir(parents=True, exist_ok=True)
    manifest_path = args.output / 'manifest.json'
    if manifest_path.exists() and json.loads(manifest_path.read_text()) != manifest:
        raise ValueError('Frozen inputs changed; use a new experiment directory')
    write(manifest_path, manifest)
    cases = []
    for seed in spec['independent_seeds']:
        case_path = args.output / f'seed{seed}'
        case_path.mkdir(exist_ok=True)
        output = case_path / 'comparison.json'
        if output.exists():
            cases.append(json.loads(output.read_text()))
            continue
        source = case_path / 'data'
        if not (source / 'manifest.json').exists():
            if source.exists():
                raise ValueError('Partial generation exists; preserve it and choose a new output')
            generate(source, seed=seed, days=spec['independent_days'])
        config = dict(base, training_start_day=0, training_end_day=spec['history_days'],
                      test_start_day=spec['evaluation_days'][0], test_end_day=spec['evaluation_days'][1])
        runs = comparison(source, config, models)
        case = dict(seed=seed, result=runs, config=config,
                    potential_demand_sha256=sha256_file(source / 'demands.jsonl'),
                    initial_bikes_sha256=sha256_file(source / 'initial_bikes.jsonl'),
                    kind='independent_seed_transfer_with_frozen_models')
        write(output, case); cases.append(case)
        print('seed', seed, {key: round(v['totals']['service_rate'], 4) for key, v in runs.items()}, flush=True)
    sensitivity = []
    original = root / 'data/simulated/campus_v1'
    for setting in spec['sensitivity']:
        output = args.output / ('sensitivity_' + setting['name'] + '.json')
        if output.exists():
            sensitivity.append(json.loads(output.read_text())); continue
        config = dict(base, **{k: v for k, v in setting.items() if k != 'name'})
        result = {}
        for policy in ['historical', 'model']:
            run = simulate(original, config, policy, models)
            result[policy] = run['totals']
        case = dict(setting=setting, result=result, scope='one-factor on development scenario, exploratory')
        write(output, case); sensitivity.append(case)
        print('sensitivity', setting['name'], flush=True)
    summary = {}
    for policy in ['none', 'fixed', 'historical', 'model']:
        values = [case['result'][policy]['totals']['service_gain_pp'] for case in cases]
        summary[policy] = dict(repetitions=len(values), service_gain_pp_values=values,
                               mean=statistics.mean(values), sample_sd=statistics.stdev(values),
                               min=min(values), max=max(values))
    differences = [100 * (case['result']['model']['totals']['service_rate'] -
                          case['result']['historical']['totals']['service_rate']) for case in cases]
    report = dict(independent_summary=summary, independent_cases=cases,
                  model_minus_historical_pp=dict(values=differences, mean=statistics.mean(differences),
                                                 min=min(differences), max=max(differences)),
                  sensitivity=sensitivity,
                  limitations=['Five simulated seeds, not five real campuses',
                               'Frozen-model transfer test, not per-input retraining',
                               'Seven history days in transfer scenes; development uses longer history',
                               'Ranges and sample SD, no population significance claim',
                               'Sensitivity stays on development scene and is exploratory'])
    write(args.output / 'summary.json', report)


if __name__ == '__main__':
    main()
