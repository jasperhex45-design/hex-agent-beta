"""Source-bound partial feedback and revision handoffs over native candidates.

Actions are user intent records, not a second generation or acceptance ledger.
Adoption still uses feedback.record_feedback; editing still uses refresh/dispatch.
"""
from __future__ import annotations

import argparse
from copy import deepcopy
import json
from pathlib import Path

from .io import AgentError, digest, now, read_json, within, write_json
from .provenance import merge_sources, verified_sources

FILE = 'candidate-actions.json'
FORMAT = 'hex.candidate-actions.v1'
ACTIONS = {'preserve_part', 'local_edit', 'change_direction', 'withdraw'}


def binding(run: Path, request_id: str | None = None) -> dict:
    from .feedback import verified_candidate
    packet, result, trial = verified_candidate(run, request_id)
    ident = trial.get('request_id') or (trial.get('generation_evidence') or {}).get('request_id')
    if not ident:
        raise AgentError('候选缺少实际请求身份')
    return {'candidate_id': ident, 'candidate_sha256': trial['sha256'],
            'packet_digest': packet['digest'], 'contract_digest': result['acceptance']['digest'],
            'parent_version': deepcopy(packet.get('requirement_parent'))}


def inspect(run: Path) -> dict | None:
    run = Path(run)
    if not (run / FILE).is_file():
        return None
    data = read_json(run / FILE)
    if data.get('format') != FORMAT or data.get('digest') != digest({k: v for k, v in data.items() if k != 'digest'}):
        raise AgentError('候选动作摘要或格式无效')
    expected = binding(run, data.get('binding', {}).get('candidate_id'))
    if data.get('binding') != expected:
        raise AgentError('候选动作不属于当前图片或父版本')
    messages = verified_sources(data['sources'])
    positions = {row['id']: i for i, row in enumerate(messages)}
    by_id = {row['id']: row for row in messages}
    seen, latest = {}, -1
    for row in data['events']:
        source = by_id.get(row['source_id'])
        if (source is None or source['actor'] != 'user' or source['origin'] == 'historical_import'
                or positions[row['source_id']] <= latest or row['action'] not in ACTIONS
                or not row['quote'].strip() or row['quote'] not in source['text']
                or row['source_digest'] != digest(source) or row['binding'] != expected
                or row['id'] != digest({k: v for k, v in row.items() if k != 'id'})):
            raise AgentError('候选动作的来源、顺序或图片绑定无效')
        if row['action'] == 'withdraw':
            target = seen.get(row['supersedes'])
            if not target or target['action'] == 'withdraw':
                raise AgentError('撤回须指向既有候选动作')
        elif row['supersedes'] or not row['preserve'] and not row['changes']:
            raise AgentError('候选动作须说明保留或改变范围')
        if row['action'] in ('local_edit', 'change_direction') and (not row['changes'] or not row['expected_improvement'].strip()):
            raise AgentError('修订须有具体目标和预期改善')
        if row['action'] == 'preserve_part' and (not row['preserve'] or row['changes']):
            raise AgentError('局部认可只登记保留内容')
        latest = positions[row['source_id']]
        seen[row['id']] = row
    withdrawn = {row['supersedes'] for row in data['events'] if row['action'] == 'withdraw'}
    active = [row for row in data['events'] if row['action'] != 'withdraw' and row['id'] not in withdrawn]
    return {**data, 'active': active, 'acceptance': 'not_inferred', 'generation_authorized': False}


def record(run: Path, action: str, quote: str, sources_file: Path, source_id: str, *,
           preserve: list[str] | None = None, changes: list[str] | None = None,
           expected_improvement: str = '', supersedes: str = '', request_id: str | None = None) -> dict:
    from .host import dispatch_lock
    if action not in ACTIONS:
        raise AgentError('采用请沿现有 record-feedback accepted_by_user 入口；未知候选动作')
    if not isinstance(quote, str) or not quote.strip():
        raise AgentError('候选动作需要用户原话')
    preserve, changes = preserve or [], changes or []
    if any(not isinstance(v, str) or not v.strip() for v in [*preserve, *changes]):
        raise AgentError('保留和修改内容不能为空')
    run = Path(run)
    with dispatch_lock(run):
        expected = binding(run, request_id)
        previous = inspect(run)
        if previous and previous['binding'] != expected:
            raise AgentError('已有动作绑定另一候选；先沿原候选关联新任务，不覆盖或混写反馈')
        sources = merge_sources(previous['sources'] if previous else None, Path(sources_file))
        source = next((row for row in verified_sources(sources) if row['id'] == source_id), None)
        if not source or source['actor'] != 'user' or source['origin'] == 'historical_import' or quote not in source['text']:
            raise AgentError('候选动作须引用当前真实用户消息')
        events = deepcopy(previous['events']) if previous else []
        body = {'action': action, 'quote': quote, 'source_id': source_id, 'source_digest': digest(source),
                'binding': expected, 'preserve': preserve, 'changes': changes,
                'expected_improvement': expected_improvement, 'supersedes': supersedes, 'recorded_at': now(),
                'interpretation_authority': 'host_interpretation_of_bound_user_message'}
        if events and any(row['source_id'] == source_id for row in events):
            raise AgentError('同一用户来源不能重复记录多个动作；将改变与保留合并在本次动作')
        events.append({**body, 'id': digest(body)})
        content = {'format': FORMAT, 'binding': expected, 'sources': sources, 'events': events}
        # Validate before publishing so a bad append never destroys prior feedback.
        if action == 'withdraw':
            if not previous or supersedes not in {row['id'] for row in previous['active']}:
                raise AgentError('撤回须指向当前有效的候选动作')
        elif supersedes or not preserve and not changes:
            raise AgentError('候选动作须说明保留或改变范围')
        if action in ('local_edit', 'change_direction') and (not changes or not expected_improvement.strip()):
            raise AgentError('修订须有具体目标和预期改善')
        if action == 'preserve_part' and (not preserve or changes):
            raise AgentError('局部认可只登记保留内容')
        positions = {row['id']: i for i, row in enumerate(verified_sources(sources))}
        if events[:-1] and positions[source_id] <= positions[events[-2]['source_id']]:
            raise AgentError('新增动作须来自晚于旧动作的用户消息')
        write_json(run / FILE, {**content, 'digest': digest(content)})
        return inspect(run)


def handoff(run: Path, action_id: str) -> dict:
    data = inspect(run)
    row = next((r for r in (data or {}).get('active', []) if r['id'] == action_id), None)
    if not row or row['action'] not in ('local_edit', 'change_direction'):
        raise AgentError('请选择当前有效的修改动作')
    from .feedback import verified_candidate
    packet, _, trial = verified_candidate(Path(run), data['binding']['candidate_id'])
    image = str(within(Path(run), trial['file']))
    used_ids = {r['id'] for r in packet.get('references', [])}
    reference_id = 'ACTION_PARENT'
    for index in range(1, 100):
        if reference_id not in used_ids:
            break
        reference_id = 'ACTION_PARENT_'+str(index)
    return {'format': 'hex.candidate-revision-handoff.v1', 'action_id': row['id'],
            'binding': data['binding'], 'parent_image': image,
            'sources': {k: data['sources'][k] for k in ('format', 'messages')},
            'edit_reference': {'id': reference_id, 'path': image, 'role': 'identity',
                               'rights': 'user_authorized', 'evaluation_only': False,
                               'note': '来源绑定的局部修改明确指定本张父图作为编辑输入；生成预算仍单独核验'},
            'input_permission_basis': 'bound_user_local_edit_intent_not_generation_permission',
            'user_quote': row['quote'], 'changes': row['changes'],
            'preserve': list(dict.fromkeys([v for event in data['active'] for v in event['preserve']])),
            'expected_improvement': row['expected_improvement'], 'mode': row['action'],
            'next': ('refresh --out NEW_RUN --need NEED --sources SOURCES --edit-reference EDIT_REFERENCE --parent-request-id ID'
                     if row['action'] == 'local_edit' else 'refresh --out NEW_RUN --need NEED --sources SOURCES'),
            'generation_authorized': False, 'notice': '沿原任务建立关联版本；本记录不等于新生图许可或整图通过。'}


def delivery_summary(run: Path) -> dict:
    from .feedback import verified_candidate
    actions = inspect(run)
    ident = actions['binding']['candidate_id'] if actions else None
    packet, _, trial = verified_candidate(Path(run), ident)
    review = trial.get('review') or {}
    return {'candidate': binding(Path(run), ident), 'image': str(within(Path(run), trial['file'])),
            'parent': deepcopy(packet.get('requirement_parent')),
            'improvements': 'not_verified', 'preservation': 'not_verified',
            'remaining_issues': review.get('changes', []), 'review_authority': 'reviewer_reported_not_user_acceptance',
            'actions': (actions or {}).get('active', [])}


def main(argv=None):
    parser = argparse.ArgumentParser(description='针对当前候选记录保留或修改，不生图')
    sub = parser.add_subparsers(dest='command', required=True)
    for name in ('show', 'summary', 'handoff', 'record'):
        p = sub.add_parser(name); p.add_argument('--run', type=Path, required=True)
        if name == 'handoff': p.add_argument('--action-id', required=True)
        if name == 'record':
            p.add_argument('--action', choices=sorted(ACTIONS), required=True)
            p.add_argument('--quote', required=True); p.add_argument('--sources', type=Path, required=True)
            p.add_argument('--source-id', required=True); p.add_argument('--request-id')
            p.add_argument('--preserve', action='append', default=[]); p.add_argument('--change', action='append', default=[])
            p.add_argument('--expected-improvement', default=''); p.add_argument('--supersedes', default='')
    args = parser.parse_args(argv)
    try:
        if args.command == 'show': result = inspect(args.run)
        elif args.command == 'summary': result = delivery_summary(args.run)
        elif args.command == 'handoff': result = handoff(args.run, args.action_id)
        else: result = record(args.run, args.action, args.quote, args.sources, args.source_id,
                    preserve=args.preserve, changes=args.change, expected_improvement=args.expected_improvement,
                    supersedes=args.supersedes, request_id=args.request_id)
        print(json.dumps(result, ensure_ascii=False, indent=2)); return 0
    except (AgentError, OSError, ValueError, KeyError) as exc:
        print(json.dumps({'error': str(exc)}, ensure_ascii=False)); return 2


if __name__ == '__main__':
    raise SystemExit(main())
