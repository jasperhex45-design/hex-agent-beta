"""Read-only candidate evidence for isolated visual review and later attribution.

No text matching claims semantic equivalence. No adapter generates or retries.
"""
from __future__ import annotations

import argparse
from copy import deepcopy
import json
from pathlib import Path

from .io import AgentError, digest, sha, within


def visual_input(run: Path, request_id: str | None = None) -> dict:
    from .feedback import verified_candidate
    from .harness import case_record, _input_hashes
    from .quality_review import goal_first_payload
    run = Path(run).resolve()
    before = _input_hashes(run)
    packet, result, trial = verified_candidate(run, request_id)
    ident = trial.get('request_id') or (trial.get('generation_evidence') or {}).get('request_id')
    case = case_record(run, ident)
    refs = [deepcopy(row) for row in packet.get('references', [])]
    payload = goal_first_payload(packet, result['acceptance'], refs, trial['sha256'], trial.get('generation_evidence'))
    payload['images_in_order'][-1]['file'] = trial['file']
    for row in payload['images_in_order']:
        row['file'] = str(within(run, row['file']))
    body = {'format': 'hex.intent-visual-input.v1', 'phase': 'isolated_first_review',
            'request_id': ident, 'candidate_sha256': trial['sha256'],
            'packet_digest': packet['digest'], 'contract_digest': result['acceptance']['digest'],
            'input': payload, 'visual_verdict': 'not_verified',
            'generation_calls': 0, 'writes_to_run': 0}
    if before != _input_hashes(run):
        raise AgentError('评审准备时原任务发生变化，请从稳定任务重读')
    for image in payload['images_in_order']:
        if sha(Path(image['file']).read_bytes()) != image['sha256']:
            raise AgentError('评审导出时图片字节已变化，不能沿用旧候选或参考摘要')
    return {**body, 'digest': digest(body)}


def chain(run: Path, request_id: str | None = None) -> dict:
    from .harness import case_record
    from .failure_diagnosis import prepare
    case = case_record(Path(run), request_id)
    if not case['candidate']:
        raise AgentError('意图核查需要真实已返回候选；没有图像不能填视觉通过')
    bundle = prepare(Path(run), request_id)
    # Semantic verdicts must cite both sides, then use the existing diagnosis validator.
    body = {'format': 'hex.intent-evidence-chain.v1', 'phase': 'after_registered_isolated_review',
            'request_id': bundle['request_id'], 'candidate_sha256': bundle['candidate_sha256'],
            'binding': {key: bundle[key] for key in ('packet_digest', 'contract_digest', 'review_digest', 'generation_evidence_digest')},
            'goal_to_plan': {'original': bundle['sources']['request'], 'need': bundle['sources']['need'],
                             'plan': bundle['sources']['design'], 'verdict': 'not_verified'},
            'plan_to_actual_input': {'plan': bundle['sources']['design'], 'actual_prompt': bundle['sources']['prompt'],
                                    'intended_arguments': bundle['sources']['intended_call'],
                                    'recorded_arguments': bundle['sources']['recorded_call'], 'semantic_verdict': 'not_verified',
                                    'deterministic_receipt_check': bundle['dispatch_evidence']},
            'goal_to_image': {'original': bundle['sources']['request'], 'image': bundle['candidate_file'],
                              'first_review': bundle['sources']['review'], 'authority': 'host_report_not_user_acceptance'},
            'source_bundle': bundle, 'model_route': case['model'], 'generation_calls': 0,
            'writes_to_run': 0, 'notice': '同义改写由语义评审判断；保存输入一致不证明原始意图已在图中实现。'}
    if sha(Path(bundle['candidate_file']).read_bytes()) != bundle['candidate_sha256']:
        raise AgentError('证据链导出时实际候选发生变化，请重新核验')
    return {**body, 'digest': digest(body)}


def call_api(prompt: str, options: dict, context: dict) -> dict:
    """Promptfoo read-only provider. Existing Hex owns calls, budgets and recovery."""
    config = options.get('config', {})
    run = config.get('run') or context.get('vars', {}).get('run')
    if not run:
        raise AgentError('只读评测适配器需要已有 Hex run')
    mode = config.get('mode', 'visual')
    if mode not in ('visual', 'chain'):
        raise AgentError('评测适配器仅支持 visual/chain 证据导出')
    data = (visual_input if mode == 'visual' else chain)(Path(run), config.get('request_id'))
    return {'output': json.dumps(data, ensure_ascii=False),
            'metadata': {'run': str(Path(run).resolve()), 'request_id': data['request_id'],
                         'candidate_sha256': data['candidate_sha256'], 'generation_calls': 0,
                         'visual_quality': 'not_scored_by_adapter'}}


def main(argv=None):
    parser = argparse.ArgumentParser(description='导出已有候选的意图证据，不生成或评分')
    parser.add_argument('mode', choices=['visual', 'chain'])
    parser.add_argument('--run', type=Path, required=True); parser.add_argument('--request-id')
    args = parser.parse_args(argv)
    try:
        result = (visual_input if args.mode == 'visual' else chain)(args.run, args.request_id)
        print(json.dumps(result, ensure_ascii=False, indent=2)); return 0
    except (AgentError, ValueError, OSError, KeyError) as exc:
        print(json.dumps({'error': str(exc)}, ensure_ascii=False)); return 2


if __name__ == '__main__':
    raise SystemExit(main())
