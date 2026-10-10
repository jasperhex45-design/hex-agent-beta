"""Versioned compilation-method candidates and leakage-safe offline selection.

Reports describe the evaluator actually used. Literal fixture checks cannot prove
semantic understanding, aesthetic improvement, or user satisfaction.
"""
from __future__ import annotations

import argparse
from copy import deepcopy
import json
import math
from pathlib import Path

from liu_agent.io import AgentError, digest, read_json, write_json
from liu_agent.research_adapters.dataset import METRICS, partition, verify_dataset

COMPONENT = 'compilation_rules'


def sealed(body: dict) -> dict:
    return {**body, 'digest': digest(body)}


def verify(value: dict) -> None:
    if value.get('digest') != digest({k: v for k, v in value.items() if k != 'digest'}):
        raise AgentError('Research artifact digest mismatch')


def verify_method(value: dict) -> None:
    verify(value)
    fields = {'format', 'component', 'version', 'parent_digest', 'text', 'status', 'production_enabled', 'digest'}
    if (set(value) != fields or value['format'] != 'hex.method-candidate.v1'
            or value['component'] != COMPONENT or type(value['version']) is not int or value['version'] < 1
            or value['status'] != 'offline_candidate' or value['production_enabled'] is not False
            or not isinstance(value['text'], str) or not 1 <= len(value['text'].strip()) <= 18000
            or (value['parent_digest'] is not None and
                (not isinstance(value['parent_digest'], str) or len(value['parent_digest']) != 64
                 or any(c not in '0123456789abcdef' for c in value['parent_digest'])))):
        raise AgentError('Only a versioned offline compilation_rules method candidate is allowed')


def candidate(text: str, *, parent: dict | None = None) -> dict:
    if not isinstance(text, str) or not 1 <= len(text.strip()) <= 18000:
        raise AgentError('Method text must contain 1..18000 characters')
    if parent:
        verify_method(parent)
    return sealed({'format': 'hex.method-candidate.v1', 'component': COMPONENT,
                   'version': parent['version'] + 1 if parent else 1,
                   'parent_digest': parent['digest'] if parent else None,
                   'text': text, 'status': 'offline_candidate', 'production_enabled': False})


def literal_metrics(output: dict, expected: dict) -> dict:
    """Strict fixture rubric; explicit exact/literal scope instead of semantic score."""
    constraints = set(output.get('constraints', []))
    required = set(expected.get('constraints', []))
    questions = set(output.get('questions', []))
    roles = output.get('reference_roles', {})
    wanted_roles = expected.get('reference_roles', {})
    values = {
        'constraint_loss': len(required - constraints),
        'invented_constraints': len(constraints - required),
        'unnecessary_questions': len(questions - set(expected.get('allowed_questions', []))),
        'reference_role_misuse': sum(roles.get(k) != v for k, v in wanted_roles.items()) + len(set(roles) - set(wanted_roles)),
        'design_goal_mismatch': sum(term not in output.get('goal', '') for term in expected.get('goal_terms', [])),
        'compilation_drift': sum(term not in output.get('compiled_text', '') for term in expected.get('compile_terms', [])),
    }
    return {'metrics': values, 'score': 1.0 / (1.0 + sum(values.values())),
            'scope': 'synthetic_exact_fields_and_literal_fragments_not_semantic_judgment',
            'feedback': '; '.join(f'{key}={value}' for key, value in values.items() if value) or 'All declared literal fixture checks pass'}


def evaluate_candidate(method: dict, dataset: dict, split: str, execute, *, host: str,
                       selection: dict | None = None) -> dict:
    """execute(method_text, row) returns output, score, metrics, feedback, scope.

    This is an in-process offline adapter, not a provider or generation runtime.
    Model-backed callbacks must be wrapped in ResearchCalls before being supplied.
    """
    verify_method(method)
    verify_dataset(dataset)
    if split not in ('train', 'validation', 'holdout'):
        raise AgentError('Unknown split')
    if split == 'holdout':
        if not selection:
            raise AgentError('Freeze validation selection before opening holdout')
        verify(selection)
        if selection['candidate_digest'] != method['digest'] or selection['dataset_digest'] != dataset['digest']:
            raise AgentError('Holdout must evaluate the frozen validation winner on the same dataset')
    rows = [row for row in dataset['rows'] if row['split'] == split]
    if not rows or not host.strip():
        raise AgentError('Evaluation requires a nonempty split and an identified execution host')
    results = []
    for row in rows:
        result = execute(method['text'], deepcopy(row))
        metrics = result.get('metrics', {})
        if set(metrics) != set(METRICS) or any(type(v) not in (float, int) or not math.isfinite(v) or v < 0 for v in metrics.values()):
            raise AgentError('Evaluator must report all six nonnegative failure metrics')
        if type(result.get('score')) not in (int, float) or not 0 <= result['score'] <= 1:
            raise AgentError('Evaluator score must be within 0..1')
        if not result.get('feedback') or not result.get('scope'):
            raise AgentError('Evaluator must declare actionable feedback and actual evaluation scope')
        results.append({'case_id': row['case_id'], 'origin': row.get('origin', 'existing_native_evidence'),
                        'evidence_digest': row.get('evidence', {}).get('case', {}).get('digest'),
                        **{key: result[key] for key in ('output', 'metrics', 'score', 'feedback', 'scope')}})
    verify_dataset(dataset)
    return sealed({'format': 'hex.method-evaluation.v1', 'candidate_digest': method['digest'],
                   'dataset_digest': dataset['digest'], 'split': split, 'host': host,
                   'results': results, 'mean_score': sum(r['score'] for r in results) / len(results),
                   'visual_quality': 'not_verified', 'user_acceptance': 'not_recorded'})


def select_validation(baseline: dict, challengers: list[dict]) -> dict:
    verify(baseline)
    if baseline['split'] != 'validation':
        raise AgentError('Candidate selection only consumes validation reports')
    winner, comparisons = baseline, []
    base = {r['case_id']: r for r in baseline['results']}
    for report in challengers:
        verify(report)
        if any(report[k] != baseline[k] for k in ('dataset_digest', 'split', 'host')):
            raise AgentError('Candidate reports must share dataset, validation split and host')
        rows = {r['case_id']: r for r in report['results']}
        if set(rows) != set(base):
            raise AgentError('Candidate reports must cover the same validation cases')
        regressions = [{'case_id': key, 'metric': metric, 'before': base[key]['metrics'][metric],
                        'after': rows[key]['metrics'][metric]}
                       for key in base for metric in METRICS
                       if rows[key]['metrics'][metric] > base[key]['metrics'][metric]]
        comparisons.append({'candidate_digest': report['candidate_digest'], 'regressions': regressions,
                            'evaluation_digest': report['digest']})
        if not regressions and report['mean_score'] > winner['mean_score']:
            winner = report
    return sealed({'format': 'hex.method-selection.v1', 'dataset_digest': baseline['dataset_digest'],
                   'candidate_digest': winner['candidate_digest'], 'host': baseline['host'],
                   'baseline_digest': baseline['candidate_digest'],
                   'validation_report_digest': winner['digest'], 'comparisons': comparisons,
                   'selection_basis': 'validation_only_no_metric_regressions', 'production_enabled': False})


def promotion_review(method: dict, selection: dict, holdout: dict) -> dict:
    """Reviewable recommendation only; cannot turn machine evidence into acceptance."""
    for artifact in (method, selection, holdout):
        verify(artifact)
    verify_method(method)
    if (method['digest'] != selection['candidate_digest'] or holdout['candidate_digest'] != method['digest']
            or holdout['dataset_digest'] != selection['dataset_digest'] or holdout['split'] != 'holdout'
            or holdout['host'] != selection['host']):
        raise AgentError('Promotion inputs do not match the frozen candidate and dataset')
    reasons = []
    if any(r['origin'].startswith('synthetic') for r in holdout['results']):
        reasons.append('synthetic evidence cannot establish production semantic or visual benefit')
    if any(sum(r['metrics'].values()) for r in holdout['results']):
        reasons.append('holdout contains recorded failures')
    reasons.extend(['independent semantic/visual review and real user calibration not recorded',
                    'production loading requires a separately reviewed explicit version change'])
    return sealed({'format': 'hex.method-promotion-review.v1', 'candidate_digest': method['digest'],
                   'version': method['version'], 'selection_digest': selection['digest'],
                   'holdout_digest': holdout['digest'], 'status': 'review_required',
                   'production_enabled': False, 'reasons': reasons})


def synthetic_demo(out: Path) -> dict:
    """Runnable zero-call example of evaluation/selection, explicitly not GEPA."""
    out = Path(out).resolve()
    if out.exists() and any(out.iterdir()):
        raise AgentError('Use an empty output directory to preserve the previous research record')
    rows = [{'case_id': name, 'split': split, 'source_family': name,
             'synthetic_text': text, 'expected': {'constraints': [text], 'compile_terms': [text],
                                               'goal_terms': [text], 'reference_roles': {}}}
            for name, split, text in [('T', 'train', '仅改青色车漆'),
                                      ('V', 'validation', '保留人物身份'),
                                      ('H', 'holdout', '日期为2026年11月12日')]]
    dataset = partition(rows)
    baseline = candidate('Synthetic compiler v1: returns empty compilation fields.')
    improved = candidate('Synthetic compiler v2: copy literal fixture request into compilation fields.', parent=baseline)
    def execute(method, row):
        text = row['synthetic_text']
        output = ({'constraints': [text], 'goal': text, 'compiled_text': text}
                  if method == improved['text'] else {})
        return {'output': output, **literal_metrics(output, row['expected'])}
    train = evaluate_candidate(improved, dataset, 'train', execute, host='synthetic-local-fixture')
    a = evaluate_candidate(baseline, dataset, 'validation', execute, host='synthetic-local-fixture')
    b = evaluate_candidate(improved, dataset, 'validation', execute, host='synthetic-local-fixture')
    selection = select_validation(a, [b])
    heldout = evaluate_candidate(improved, dataset, 'holdout', execute,
                                 host='synthetic-local-fixture', selection=selection)
    promotion = promotion_review(improved, selection, heldout)
    for name, value in [('dataset', dataset), ('baseline', baseline), ('candidate', improved),
                        ('train', train), ('validation-a', a), ('validation-b', b),
                        ('selection', selection), ('holdout', heldout), ('promotion-review', promotion)]:
        write_json(out / (name + '.json'), value)
    summary = {'format': 'hex.offline-demo.v1', 'scope': 'synthetic_literal_fixture_only',
               'candidate_selected': selection['candidate_digest'] == improved['digest'],
               'gepa_execution': 'not_run', 'model_calls': 0, 'generation_calls': 0,
               'visual_quality': 'not_tested', 'user_acceptance': 'not_recorded',
               'production_enabled': False, 'output': str(out)}
    write_json(out / 'summary.json', summary)
    return summary


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', choices=['synthetic-demo'])
    parser.add_argument('--out', type=Path, required=True)
    args = parser.parse_args(argv)
    print(json.dumps(synthetic_demo(args.out), ensure_ascii=False, indent=2))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
