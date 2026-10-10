"""Promptfoo Python provider/assertion over native HEX evidence, without calls."""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path

from liu_agent import minimal_harness, trajectory_evaluator
from liu_agent.io import AgentError, digest, read_json, write_json
from liu_agent.research_adapters.dataset import evidence_view, task_matrix


def evaluate_existing(variables: dict) -> dict:
    if variables.get('mode', 'process') != 'process':
        raise AgentError('This provider only checks process evidence; visual rubric needs actual images and a separately authorized vision reviewer')
    if not variables.get('run'):
        return {'format': 'hex.promptfoo-evidence.v1', 'status': 'not_run',
                'reason': 'No native task bound; a specification is not a generated result',
                'generation_calls': 0, 'visual_quality': 'not_tested', 'human_acceptance': 'not_recorded'}
    run = Path(variables['run']).resolve()
    view = evidence_view(run, variables.get('request_id'))
    trace = read_json(Path(variables['trace_file'])) if variables.get('trace_file') else None
    process = trajectory_evaluator.evaluate(run, variables.get('request_id'), trace=trace)
    semantic = (minimal_harness.semantic_trace(run, variables['dominant_choice_id'],
                                             variables.get('request_id'))
                if variables.get('dominant_choice_id') else None)
    comparison = minimal_harness.check(Path(variables['comparison_dir'])) if variables.get('comparison_dir') else None
    # Do not let a comparison of a different task pass as this run's comparison.
    if comparison and Path(comparison['source_run']).resolve() != run:
        raise AgentError('Comparison manifest belongs to a different source task')
    if evidence_view(run, variables.get('request_id')) != view:
        raise AgentError('Canonical evidence changed while evaluating')
    body = {'format': 'hex.promptfoo-evidence.v1', 'status': process['status'],
            'evidence': view, 'trajectory': process, 'semantic_trace': semantic,
            'comparison': comparison, 'generation_calls': 0, 'model_calls': 0,
            'visual_quality': 'not_verified', 'semantic_faithfulness': 'not_verified_by_literal_trace',
            'human_acceptance': view['case']['evaluation']['human_acceptance']}
    return {**body, 'digest': digest(body)}


def call_api(prompt, options, context):
    """Official promptfoo Python provider interface; prompt is not an instruction."""
    try:
        variables = {**options.get('config', {}), **context.get('vars', {})}
        result = evaluate_existing(variables)
        return {'output': json.dumps(result, ensure_ascii=False),
                'tokenUsage': {'total': 0, 'prompt': 0, 'completion': 0, 'numRequests': 0}, 'cost': 0}
    except (AgentError, OSError, ValueError, KeyError, TypeError) as exc:
        return {'error': str(exc)}


def get_assert(output, context):
    """Recompute from canonical sources; promptfoo output is not an authority."""
    try:
        raw = json.loads(output) if isinstance(output, str) else output
        current = evaluate_existing(context.get('vars', {}))
        same = raw == current
        passed = same and current['status'] == 'passed'
        return {'pass': passed, 'score': 1.0 if passed else 0.0,
                'reason': ('Saved process evidence is consistent; no semantic/visual/user acceptance claim'
                           if passed else 'Missing, changed, failed or incomplete native evidence: ' + current['status']),
                'named_scores': {'recorded_process_consistency': float(passed)}}
    except (AgentError, OSError, ValueError, KeyError, TypeError) as exc:
        return {'pass': False, 'score': 0.0, 'reason': str(exc)}


def export_config(bindings: list[dict], out: Path) -> dict:
    """Emit JSON accepted as promptfoo config; does not install or run promptfoo."""
    if not bindings:
        raise AgentError('At least one case binding is required')
    out = Path(out).resolve()
    # promptfoo 0.124.0 splits assertion file references at the first colon,
    # including a Windows drive colon. A config-relative path avoids that bug.
    try:
        relative = os.path.relpath(Path(__file__).resolve(), out.parent)
    except ValueError as exc:
        raise AgentError('Write the promptfoo config on the same drive as the Hex package') from exc
    provider = 'file://' + Path(relative).as_posix()
    tests = []
    for index, binding in enumerate(bindings):
        row = dict(binding)
        for name in ('run', 'trace_file', 'comparison_dir'):
            if row.get(name):
                row[name] = str(Path(row[name]).resolve())
                if name == 'run' and out.is_relative_to(Path(row[name])):
                    raise AgentError('Derived config must not be written into a canonical task')
        tests.append({'description': row.pop('description', 'native-evidence-' + str(index + 1)),
                      # The default Python assertion entrypoint is get_assert.
                      'vars': row, 'assert': [{'type': 'python', 'value': provider}]})
    config = {'description': 'HEX read-only native evidence evaluation',
              'prompts': ['Check the bound native evidence; no model execution.'],
              'providers': [provider + ':call_api'], 'tests': tests,
              'evaluateOptions': {'maxConcurrency': 1}}
    write_json(out, config)
    return {'path': str(out), 'model_calls': 0, 'generation_calls': 0,
            'promptfoo_execution': 'not_run', 'install_performed': False}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', choices=('matrix', 'evaluate', 'config'))
    parser.add_argument('--bindings', type=Path)
    parser.add_argument('--run', type=Path)
    parser.add_argument('--request-id')
    parser.add_argument('--trace-file', type=Path)
    parser.add_argument('--dominant-choice-id')
    parser.add_argument('--out', type=Path)
    args = parser.parse_args(argv)
    if args.action == 'matrix':
        result = task_matrix()
    elif args.action == 'config':
        if not args.bindings or not args.out:
            parser.error('config requires --bindings and --out')
        result = export_config(read_json(args.bindings), args.out)
    else:
        result = evaluate_existing({'run': str(args.run.resolve()) if args.run else None,
                                    'request_id': args.request_id,
                                    'trace_file': str(args.trace_file.resolve()) if args.trace_file else None,
                                    'dominant_choice_id': args.dominant_choice_id})
    if args.out and args.action != 'config':
        if args.run and args.out.resolve().is_relative_to(args.run.resolve()):
            parser.error('Reports must be outside canonical tasks')
        write_json(args.out, result)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result.get('status') not in ('failed', 'evidence_incomplete', 'not_run') else 2


if __name__ == '__main__':
    raise SystemExit(main())
