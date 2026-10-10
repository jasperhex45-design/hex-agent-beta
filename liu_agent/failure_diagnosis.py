"""Second-phase, read-only failure attribution bound to an isolated first review.

Code verifies provenance and quoted evidence. Semantic comparisons remain host
judgments: a valid report is neither causal proof nor permission to generate.
"""
from __future__ import annotations

import argparse
from copy import deepcopy
import json
from pathlib import Path
from typing import Literal

from pydantic import Field

from .contracts import Strict
from . import generation_evidence, harness, host
from .io import AgentError, digest, read_json, within, write_json
from .workflow import read_packet, validate_trial

LAYERS = ('intent', 'design', 'compiler', 'dispatch', 'generation', 'preference')
PAIRS = {'intent': ('request', 'need'), 'design': ('need', 'design'),
         'compiler': ('design', 'prompt'), 'dispatch': ('intended_call', 'recorded_call'),
         'generation': ('prompt', 'review'), 'preference': ('feedback',)}


class Citation(Strict):
    source: Literal['request', 'need', 'design', 'prompt', 'intended_call', 'recorded_call', 'review', 'feedback']
    pointer: str = Field(max_length=500)
    quote: str = Field(min_length=1, max_length=4000)


class Comparison(Strict):
    layer: Literal['intent', 'design', 'compiler', 'dispatch', 'generation', 'preference']
    verdict: Literal['aligned', 'diverged', 'unknown']
    explanation: str = Field(min_length=3, max_length=2000)
    evidence: list[Citation] = Field(max_length=12)


class Issue(Strict):
    id: str = Field(min_length=1, max_length=100)
    criterion_id: str = Field(min_length=1, max_length=100)
    comparisons: list[Comparison] = Field(min_length=6, max_length=6)


class Assessment(Strict):
    format: Literal['hex.failure-assessment.v1']
    bundle_digest: str
    request_id: str
    candidate_sha256: str
    review_digest: str
    generation_evidence_digest: str
    origin: Literal['host_semantic_review_not_tool_authenticated', 'synthetic_fixture']
    issues: list[Issue] = Field(min_length=1, max_length=30)


def _trial(run: Path, case: dict) -> dict:
    name = 'image-tests.json' if case['route'] == 'api' else 'host-image-tests.json'
    trials = read_json(run / name) if (run / name).exists() else []
    matches = [row for row in trials if row.get('request_id', row.get('generation_evidence', {}).get('request_id'))
               == case['attempt']['request_id']]
    if len(matches) != 1 or not matches[0].get('review'):
        raise AgentError('先登记当前候选的首次评审，不能提前开放第二阶段诊断')
    return matches[0]


def prepare(run: Path, request_id: str | None = None) -> dict:
    """Expose original author material only after a registered isolated review."""
    run = Path(run).resolve()
    case = harness.case_record(run, request_id)
    if not case['candidate']:
        raise AgentError('无已完成候选；只报告执行状态，不推断成图原因')
    trial = _trial(run, case)
    review = trial['review']
    whole = review.get('holistic') or {}
    if whole.get('image_access') != 'viewed' or whole.get('review_context') != 'isolated':
        raise AgentError('首次评审须声明实际看图及隔离上下文；历史作者自评不升级为隔离评审')
    packet = read_packet(run)
    contract = read_json(run / 'acceptance.json')
    validate_trial(packet, run, contract, trial)
    image = within(run, trial['file']).read_bytes()
    evidence = (generation_evidence.for_api(run, packet, contract, image, case['attempt']['request_id'])
                if case['route'] == 'api' else generation_evidence.for_host(run, packet, contract, image))
    if trial.get('generation_evidence') != evidence:
        raise AgentError('首评与原生成证据不一致，不能诊断另一张图')
    if case['route'] == 'host':
        row = next(r for r in host.read_dispatches(run) if r['request']['id'] == evidence['request_id'])
        intended = deepcopy(row['request']['arguments'])
        recorded = read_json(within(run, row['actual_arguments']['file']))
        if 'arguments' in recorded:
            recorded = recorded['arguments']
    else:
        from .api_recovery import load_api_state, _verified
        row = next(r for r in load_api_state(run)['requests'] if r['id'] == evidence['request_id'])
        snapshot = _verified(within(run, row['snapshot']))
        intended = deepcopy(snapshot['request'])
        # Durable API snapshots bind the saved call, not a remote tool trace.
        recorded = deepcopy(snapshot['request'])
    feedback = None
    if case['evaluation']['human_acceptance'] != 'not_recorded':
        from .feedback import verified_feedback
        _, selected, feedback = verified_feedback(run)
        if selected['sha256'] != evidence['candidate_sha256']:
            raise AgentError('反馈未绑定当前候选')
    sources = {'request': {'frozen_request': contract['request'],
                            'frozen_request_authority': 'frozen_contract_not_user_endorsement',
                            'messages': [deepcopy(m) for m in packet.get('input_sources', {}).get('messages', [])
                                         if m.get('actor') == 'user']},
               'need': deepcopy(packet['need']),
               'design': {key: deepcopy(evidence['solution'][key]) for key in ('spec', 'design')},
               'prompt': evidence['actual_prompt'], 'intended_call': intended, 'recorded_call': recorded,
               'review': deepcopy(review), 'feedback': deepcopy(feedback)}
    body = {'format': 'hex.failure-diagnosis-input.v1', 'phase': 'after_registered_isolated_review',
            'request_id': evidence['request_id'], 'candidate_sha256': evidence['candidate_sha256'],
            'candidate_file': str(within(run, trial['file'])), 'packet_digest': packet['digest'],
            'contract_digest': contract['digest'], 'review_digest': digest(review),
            'generation_evidence_digest': evidence['digest'], 'sources': sources,
            'source_digests': {key: digest(value) for key, value in sources.items()},
            'criteria': deepcopy(contract['checks']), 'layers': list(LAYERS),
            'human_acceptance': case['evaluation']['human_acceptance'],
            'dispatch_evidence': {'verdict': 'aligned', 'scope': 'saved_receipt_consistency',
                                  'authority': evidence['authority'], 'tool_trace_authenticated': False},
            'generation_calls': 0, 'writes_to_run': 0,
            'notice': '首评的隔离/看图为宿主声明。正文与摘录一致不证明语义等价；最早偏差按每个问题分别记录，不证明因果。'}
    return {**body, 'digest': digest(body)}


def _value(source, pointer: str):
    if not pointer:
        return source
    if not pointer.startswith('/'):
        raise AgentError('证据位置须为JSON pointer，正文根位置使用空字符串')
    try:
        for key in pointer[1:].split('/'):
            key = key.replace('~1', '/').replace('~0', '~')
            source = source[int(key)] if isinstance(source, list) else source[key]
        return source
    except (KeyError, IndexError, TypeError, ValueError) as exc:
        raise AgentError('证据位置不存在') from exc


def _evidence(bundle: dict, comparison: Comparison) -> None:
    allowed = set(PAIRS[comparison.layer])
    for item in comparison.evidence:
        if item.source not in allowed:
            raise AgentError('证据必须属于该层的比较对象')
        value = _value(bundle['sources'][item.source], item.pointer)
        matches = (item.quote in value if isinstance(value, str) else
                   item.quote == json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':')))
        if not matches:
            raise AgentError('证据摘录不在绑定原文位置，不能伪造原话或语义')
    if comparison.verdict != 'unknown' and {item.source for item in comparison.evidence} != allowed:
        raise AgentError('已对齐/已偏移须同时引用该层全部比较对象；缺证据标unknown')


def diagnose(run: Path, assessment: dict, request_id: str | None = None) -> dict:
    bundle = prepare(run, request_id)
    assessment = Assessment.model_validate(assessment)
    binding = {'bundle_digest': bundle['digest'], **{key: bundle[key] for key in
                ('request_id', 'candidate_sha256', 'review_digest', 'generation_evidence_digest')}}
    if any(getattr(assessment, key) != value for key, value in binding.items()):
        raise AgentError('诊断绑定已变化；不得换图、换首评或使用当前方案替代原调用')
    if len({issue.id for issue in assessment.issues}) != len(assessment.issues):
        raise AgentError('问题ID须唯一')
    review = bundle['sources']['review']
    observed = {row['criterion_id']: row['verdict'] for row in review['items']}
    observed.update({'goal.' + row['dimension']: row['verdict'] for row in review['holistic']['judgments']})
    issues = []
    for issue in assessment.issues:
        if issue.criterion_id not in observed:
            raise AgentError('诊断问题必须对应首评中的具体标准')
        if [row.layer for row in issue.comparisons] != list(LAYERS):
            raise AgentError('每个问题须按六层各记录一次，不强迫选择单一整图原因')
        for row in issue.comparisons:
            _evidence(bundle, row)
            if row.layer == 'intent' and row.verdict != 'unknown':
                messages = bundle['sources']['request']['messages']
                valid = {'/messages/' + str(i) + '/text' for i, message in enumerate(messages)
                         if isinstance(message.get('text'), str) and message['text'].strip()}
                if not any(cite.source == 'request' and cite.pointer in valid for cite in row.evidence):
                    raise AgentError('理解判断须引用真实用户消息，宿主摘要不能代替原话')
            if row.layer == 'dispatch' and row.verdict == 'diverged':
                raise AgentError('保存的原调用回执已核验一致；不能声称其已偏移，未捕获现场则标unknown')
            if row.layer == 'generation' and row.verdict != 'unknown':
                expected = 'not_met' if row.verdict == 'diverged' else 'met'
                if observed[issue.criterion_id] != expected:
                    raise AgentError('输出判断须与首评对应标准一致，不能把uncertain变成通过或失败')
                paths = ['/items/' + str(i) + '/candidate_evidence' for i, item in enumerate(review['items'])
                         if item['criterion_id'] == issue.criterion_id]
                paths.extend('/holistic/judgments/' + str(i) + '/evidence' for i, item in enumerate(review['holistic']['judgments'])
                             if 'goal.' + item['dimension'] == issue.criterion_id)
                if any(cite.source == 'review' and cite.pointer not in paths for cite in row.evidence):
                    raise AgentError('输出证据须引用同一问题的首评项目，不能引用其他标准或整体结论')
            if row.layer == 'preference' and row.verdict != 'unknown' and bundle['sources']['feedback'] is None:
                raise AgentError('没有当前候选的用户反馈，偏好层只能unknown')
            if row.layer == 'preference' and row.verdict != 'unknown':
                expected = 'diverged' if bundle['human_acceptance'] == 'rejected_by_user' else 'aligned'
                if row.verdict != expected or not any(cite.pointer == '/user_quote' for cite in row.evidence):
                    raise AgentError('偏好判断须引用当前候选用户原话并保持接受/拒绝方向')
        divergences = [row.layer for row in issue.comparisons if row.verdict == 'diverged']
        earliest = divergences[0] if divergences else None
        unknown_before = [row.layer for row in issue.comparisons[:LAYERS.index(earliest)]
                          if row.verdict == 'unknown'] if earliest else []
        issues.append({**issue.model_dump(), 'earliest_reported_divergence': earliest,
                       'unverified_earlier_layers': unknown_before,
                       'generation_unrealized_supported_by_host': earliest == 'generation' and not unknown_before,
                       'causal_attribution': 'not_proven'})
    body = {'format': 'hex.failure-diagnosis.v1', **binding,
            'authority': assessment.origin, 'status': 'evidence_bound_report',
            'issues': issues, 'human_acceptance': bundle['human_acceptance'],
            'semantic_truth': 'host_judgment_not_verified_by_code', 'visual_quality': 'not_verified_by_code',
            'generation_calls': 0, 'writes_to_run': 0, 'automatic_repair': False,
            'notice': '这里只核验证据、顺序和来源；报告不改变首评、生产选图、权限或预算。'}
    return {**body, 'digest': digest(body)}


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', choices=('prepare', 'diagnose', 'schema'))
    parser.add_argument('--run', type=Path)
    parser.add_argument('--request-id')
    parser.add_argument('--assessment', type=Path)
    parser.add_argument('--out', type=Path)
    args = parser.parse_args(argv)
    if args.action != 'schema' and not args.run:
        parser.error('--run required')
    if args.action == 'diagnose' and not args.assessment:
        parser.error('--assessment required')
    if args.out and args.run and args.out.resolve().is_relative_to(args.run.resolve()):
        parser.error('诊断材料/报告须写在原任务之外')
    if args.out and args.assessment and args.out.resolve() == args.assessment.resolve():
        parser.error('报告不能覆盖评估输入')
    try:
        result = (Assessment.model_json_schema() if args.action == 'schema' else
                  prepare(args.run, args.request_id) if args.action == 'prepare' else
                  diagnose(args.run, read_json(args.assessment), args.request_id))
        if args.out: write_json(args.out, result)
    except (AgentError, OSError, ValueError, KeyError, TypeError, StopIteration) as exc:
        print(str(exc), file=__import__('sys').stderr)
        return 2
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
