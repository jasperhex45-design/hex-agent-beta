"""Explicit, scoped experience cards over existing verified visual evidence.

No embeddings, automatic learning, image calls or production recall. Original
feedback remains in its native task; this index only controls a card's use.
"""
from __future__ import annotations

import argparse
from copy import deepcopy
import json
from pathlib import Path
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, StringConstraints

from .io import AgentError, digest, now, read_json, sha, write_json
from . import visual_evidence_store as store

FORMAT = 'hex.visual-experience.v1'
Nonblank = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=2000)]


class CardSpec(BaseModel):
    model_config = ConfigDict(extra='forbid')
    id: str = Field(pattern=r'^[A-Za-z][A-Za-z0-9_-]{0,63}$')
    purpose: Nonblank
    task_relation: Nonblank
    problem_type: Nonblank
    applicability: list[Nonblank] = Field(min_length=1, max_length=8)
    preference_scope: Nonblank
    source_family: Nonblank
    split: Literal['development', 'heldout']
    reference_asset_ids: list[str] = Field(default_factory=list, max_length=8)
    region_asset_ids: list[str] = Field(default_factory=list, max_length=8)


def _seal(body: dict) -> dict:
    return {**body, 'digest': digest(body)}


def _empty() -> dict:
    return _seal({'format': FORMAT, 'cards': [], 'withdrawals': []})


def _load(path: Path) -> dict:
    if not path.exists():
        return _empty()
    data = read_json(path)
    if (not isinstance(data, dict) or data.get('format') != FORMAT
            or data.get('digest') != digest({key: value for key, value in data.items() if key != 'digest'})
            or not isinstance(data.get('cards'), list) or not isinstance(data.get('withdrawals'), list)):
        raise AgentError('视觉经验库格式或摘要无效')
    cards = {card['id']: card for card in data['cards']}
    if len(cards) != len(data['cards']):
        raise AgentError('视觉经验卡 ID 重复')
    for card in cards.values():
        if card.get('digest') != digest({key: value for key, value in card.items() if key != 'digest'}):
            raise AgentError('视觉经验卡摘要无效')
        spec = CardSpec.model_validate(card['spec'])
        pointers = [card['result_image'], *card['references'], *card['regions']]
        if card['id'] != spec.id or card.get('source_keys') != _source_keys(
                pointers, spec.source_family, card['context']['input_references']):
            raise AgentError('经验卡来源族与图像谱系不一致')
    groups = _groups(data['cards'])
    splits = {}
    for card in data['cards']:
        group = groups[card['id']]
        split = card['spec']['split']
        if group in splits and splits[group] != split:
            raise AgentError('经验库同来源谱系跨 development/heldout，不得召回')
        splits[group] = split
    seen = set()
    for event in data['withdrawals']:
        card = cards.get(event.get('card_id'))
        if (card is None or event['card_id'] in seen or event.get('card_digest') != card['digest']
                or event.get('origin') != 'explicit_card_use_withdrawal'
                or any(not isinstance(event.get(key), str) or not event[key].strip()
                       for key in ('user_quote', 'source', 'recorded_at'))):
            raise AgentError('撤回须绑定原经验卡及明确用户原话')
        seen.add(event['card_id'])
    return data


def _pointer(index: Path, ident: str) -> dict:
    asset = store.inspect(index, ident)
    asset.pop('notice', None)
    return {'index': str(index.resolve()), 'asset_id': ident, 'asset_digest': digest(asset),
            'asset': asset}


def _run(asset: dict) -> Path:
    packets = [Path(proof['file']) for proof in asset['source_evidence']
               if Path(proof['file']).name == 'packet.json']
    if len(packets) != 1 or asset.get('execution_authority') == 'external_case_not_native_dispatch':
        raise AgentError('经验卡目前需要一份原生结果任务；外部批次不能推断逐图认可')
    return packets[0].resolve().parent


def _native_context(asset: dict) -> dict:
    from .feedback import verified_candidate, verified_feedback
    from .candidate_feedback import verified_candidate_feedback, projection
    from .feedback_sources import EVIDENCE
    run = _run(asset)
    request_id = asset['identity'].get('request_id')
    if asset['role'] != 'candidate' or not request_id:
        raise AgentError('经验结果须为已登记原生 candidate；局部通过 region_asset_ids 引用')
    packet, result, trial = verified_candidate(run, request_id)
    if trial['sha256'] != asset['source_sha256'] or packet['digest'] != asset['identity'].get('packet_digest'):
        raise AgentError('经验卡结果图与原任务身份不一致')
    feedback = []
    outcome = 'unaccepted'
    if (run / 'user-feedback.json').exists():
        _, bound, report = verified_feedback(run)
        if bound['sha256'] != asset['source_sha256']:
            raise AgentError('用户反馈绑定的图片不同')
        feedback.append({'kind': 'candidate_decision', 'value': report})
        # Legacy free-text receipts remain traceable but are insufficient for
        # promoting a card into an explicitly accepted reference.
        if report.get('source_evidence') == EVIDENCE:
            outcome = 'user_accepted' if report['decision'] == 'accepted_by_user' else 'user_rejected'
        elif report['decision'] == 'rejected_by_user':
            outcome = 'user_rejected'
    positive = verified_candidate_feedback(run)
    if positive:
        feedback.append({'kind': 'positive_candidate_feedback', 'value': projection(positive)})
        if outcome == 'unaccepted':
            outcome = 'positive_only'
    from .harness import case_record
    case = case_record(run, request_id)
    return {'run': str(run), 'domain': packet['need']['domain'],
            'request_id': request_id, 'packet_digest': packet['digest'],
            'candidate_sha256': trial['sha256'], 'contract_digest': result['acceptance']['digest'],
            'input_references': [{key: ref.get(key) for key in ('id', 'sha256', 'original_sha256')}
                                 for ref in packet['references']],
            'outcome': outcome, 'accepted_template': outcome == 'user_accepted',
            'feedback': feedback, 'model_conditions': {
                **case['model'], 'executor': case['executor'],
                'request_digest': case['attempt']['request_digest'],
                'generation_spec': trial.get('generation_spec'),
                'actual_generation_evidence': trial.get('generation_evidence'),
                'origin': trial['origin']},
            'scope': 'this_candidate_and_declared_applicability_not_global_taste'}


def _source_keys(pointers: list[dict], family: str, input_references: list[dict]) -> list[str]:
    keys = {'family:' + family}
    # Shared original inputs connect otherwise different output images, even
    # when a card highlights only one of the task's references.
    for ref in input_references:
        for key in ('sha256', 'original_sha256'):
            if ref.get(key):
                keys.add('image:' + ref[key])

    def lineage(value):
        if isinstance(value, dict):
            for key, item in value.items():
                if isinstance(item, str) and key.endswith('packet_digest'):
                    keys.add('packet:' + item)
                if isinstance(item, str) and key == 'parent_candidate_sha256':
                    keys.add('image:' + item)
                if isinstance(item, str) and key in ('run', 'parent_run'):
                    keys.add('run:' + str(Path(item).resolve()).casefold())
                lineage(item)
        elif isinstance(value, list):
            for item in value:
                lineage(item)

    for pointer in pointers:
        asset = pointer['asset']
        for value in (asset['source_sha256'], (asset.get('raw_original') or {}).get('sha256')):
            if value:
                keys.add('image:' + value)
        identity = asset['identity']
        if identity.get('packet_digest'):
            keys.add('packet:' + identity['packet_digest'])
        for proof in asset['source_evidence']:
            keys.add('proof_path:' + str(Path(proof['file']).resolve()).casefold())
            keys.add('proof_content:' + proof['sha256'])
            if Path(proof['file']).name == 'packet.json':
                keys.add('run:' + str(Path(proof['file']).resolve().parent).casefold())
        lineage(asset.get('lineage'))
    return sorted(keys)


def _groups(cards: list[dict]) -> dict[str, str]:
    parent = {card['id']: card['id'] for card in cards}

    def root(ident):
        while parent[ident] != ident:
            parent[ident] = parent[parent[ident]]
            ident = parent[ident]
        return ident

    seen = {}
    for card in cards:
        for key in card['source_keys']:
            if key in seen:
                parent[root(card['id'])] = root(seen[key])
            else:
                seen[key] = card['id']
    return {ident: root(ident) for ident in parent}


def add(library: Path, index: Path, asset_id: str, spec: CardSpec | dict) -> dict:
    spec = CardSpec.model_validate(spec).model_dump()
    data = _load(library)
    if any(card['id'] == spec['id'] for card in data['cards']):
        raise AgentError('经验卡 ID 已存在；保留原记录，不覆盖证据')
    primary = _pointer(index, asset_id)
    context = _native_context(primary['asset'])
    pointers = [primary]
    refs, regions = [], []
    indexed = store._load(index)
    task_references = [asset for asset in indexed['assets'] if asset['role'] == 'reference'
                       and asset['identity'].get('packet_digest') == context['packet_digest']]
    reference_ids = spec['reference_asset_ids'] or [asset['id'] for asset in task_references]
    for ident in reference_ids:
        pointer = _pointer(index, ident)
        if (pointer['asset']['role'] != 'reference' or
                pointer['asset']['identity'].get('packet_digest') != context['packet_digest']):
            raise AgentError('原图参考必须属于本结果的同一任务')
        refs.append(pointer)
        pointers.append(pointer)
    for ident in spec['region_asset_ids']:
        pointer = _pointer(index, ident)
        if pointer['asset']['role'] != 'crop' or pointer['asset']['parent_asset_id'] != asset_id:
            raise AgentError('局部必须绑定本结果图，不能用其他来源区域')
        regions.append(pointer)
        pointers.append(pointer)
    protected = [Path(context['run']), *[Path(pointer['index']) for pointer in pointers]]
    if any(library.resolve() == path.resolve() or library.resolve().is_relative_to(path.resolve())
           for path in protected):
        raise AgentError('经验库须在原任务和资产索引之外，不能覆盖源证据')
    card = _seal({'id': spec['id'], 'spec': spec, 'result_image': primary,
                  'references': refs, 'regions': regions, 'context': context,
                  'source_keys': _source_keys(pointers, spec['source_family'], context['input_references']),
                  'source_family_authority': 'host_declared_with_recorded_source_overlap_checks',
                  'recorded_at': now()})
    all_cards = [*data['cards'], card]
    groups = _groups(all_cards)
    joined = [row for row in all_cards if groups[row['id']] == groups[card['id']]]
    if len({row['spec']['split'] for row in joined}) != 1:
        raise AgentError('同来源族、原图或派生谱系不得跨 development/heldout')
    body = {key: value for key, value in data.items() if key != 'digest'}
    body['cards'] = all_cards
    write_json(library, _seal(body))
    return card


def _verify(card: dict) -> None:
    for pointer in [card['result_image'], *card['references'], *card['regions']]:
        current = _pointer(Path(pointer['index']), pointer['asset_id'])
        if current['asset_digest'] != pointer['asset_digest']:
            raise AgentError('经验卡图像/区域来源已变化，须重建卡片')
    if _native_context(card['result_image']['asset']) != card['context']:
        raise AgentError('经验卡用户反馈或结果条件已变化，旧快照不可继续采用')


def inspect(library: Path, ident: str, *, require_active: bool = False) -> dict:
    data = _load(library)
    card = next((card for card in data['cards'] if card['id'] == ident), None)
    if card is None:
        raise AgentError('经验卡 ID 不存在')
    withdrawn = next((event for event in data['withdrawals'] if event['card_id'] == ident), None)
    if withdrawn and require_active:
        raise AgentError('经验卡已撤回，不得继续影响新任务')
    _verify(card)
    return {**deepcopy(card), 'use_state': 'withdrawn' if withdrawn else 'active',
            'withdrawal': withdrawn, 'production_recall_enabled': False}


def search(library: Path, query: str, *, domain: str | None = None,
           task_relation: str | None = None, problem_type: str | None = None,
           mode: str = 'creation', limit: int = 3) -> dict:
    if mode not in ('creation', 'diagnostic') or type(limit) is not int or not 1 <= limit <= 3:
        raise AgentError('经验检索 mode 须为 creation/diagnostic，limit 为1至3')
    from .retrieval import tokens
    data = _load(library)
    withdrawn = {event['card_id'] for event in data['withdrawals']}
    groups = _groups(data['cards'])
    terms = set(tokens(query))
    scored = []
    for card in data['cards']:
        spec, context = card['spec'], card['context']
        if (card['id'] in withdrawn or spec['split'] != 'development'
                or (domain is not None and context['domain'] != domain)
                or (task_relation is not None and spec['task_relation'] != task_relation)
                or (problem_type is not None and spec['problem_type'] != problem_type)
                or (mode == 'creation' and context['outcome'] not in ('user_accepted', 'positive_only'))):
            continue
        searchable = json.dumps({'spec': spec, 'domain': context['domain'],
                                 'feedback': context['feedback']}, ensure_ascii=False)
        matches = terms & set(tokens(searchable))
        if matches:
            scored.append((len(matches), card))
    scored.sort(key=lambda row: (-row[0], row[1]['id']))
    hits, used_groups = [], set()
    for score, card in scored:
        if groups[card['id']] in used_groups:
            continue
        _verify(card)
        used_groups.add(groups[card['id']])
        hits.append({'id': card['id'], 'card_digest': card['digest'], 'metadata_matches': score,
                     'source_family': card['spec']['source_family'], 'outcome': card['context']['outcome'],
                     'accepted_template': card['context']['accepted_template'],
                     'purpose': card['spec']['purpose'], 'applicability': card['spec']['applicability'],
                     'preference_scope': card['spec']['preference_scope'],
                     'result_asset_id': card['result_image']['asset_id']})
        if len(hits) == limit:
            break
    return {'format': 'hex.visual-experience-recall.v1', 'query': query, 'mode': mode,
            'hits': hits, 'method': 'scoped_metadata_overlap_source_diverse',
            'production_recall_enabled': False,
            'notice': '命中是待实际看图的案例；不是审美分、全任务验收或长期偏好；留出来源不进入开发召回。'}


def validate_recall(library: Path, hit: dict) -> dict:
    """Saved recalls must recheck the current card and withdrawal state on use."""
    card = inspect(library, hit['id'], require_active=True)
    if hit.get('card_digest') != card['digest'] or card['spec']['split'] != 'development':
        raise AgentError('召回快照已失效或来自 heldout，不可用于开发')
    return card


def withdraw(library: Path, ident: str, user_quote: str, source: str) -> dict:
    if not user_quote.strip() or not source.strip():
        raise AgentError('撤回必须记录明确用户原话和来源')
    data = _load(library)
    card = next((card for card in data['cards'] if card['id'] == ident), None)
    if card is None:
        raise AgentError('经验卡 ID 不存在')
    event = next((event for event in data['withdrawals'] if event['card_id'] == ident), None)
    if event is None:
        event = {'card_id': ident, 'card_digest': card['digest'], 'user_quote': user_quote.strip(),
                 'source': source.strip(), 'recorded_at': now(), 'origin': 'explicit_card_use_withdrawal'}
        body = {key: value for key, value in data.items() if key != 'digest'}
        body['withdrawals'] = [*body['withdrawals'], event]
        write_json(library, _seal(body))
    return {'status': 'withdrawn', 'event': event, 'original_feedback_preserved': True}


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description='Hex 有作用域的本地视觉经验；不生成、不自动采用')
    commands = parser.add_subparsers(dest='command', required=True)
    for name in ('add', 'search', 'inspect', 'withdraw'):
        sub = commands.add_parser(name)
        sub.add_argument('--library', type=Path, required=True)
        if name == 'add':
            sub.add_argument('--index', type=Path, required=True)
            sub.add_argument('--asset-id', required=True)
            sub.add_argument('--spec', type=Path, required=True)
        elif name == 'search':
            sub.add_argument('--query', required=True)
            sub.add_argument('--domain')
            sub.add_argument('--task-relation')
            sub.add_argument('--problem-type')
            sub.add_argument('--mode', choices=['creation', 'diagnostic'], default='creation')
            sub.add_argument('--limit', type=int, default=3)
        else:
            sub.add_argument('--id', required=True)
        if name == 'withdraw':
            sub.add_argument('--user-quote', required=True)
            sub.add_argument('--source', required=True)
    args = parser.parse_args(argv)
    try:
        if args.command == 'add':
            result = add(args.library, args.index, args.asset_id, read_json(args.spec))
        elif args.command == 'search':
            result = search(args.library, args.query, domain=args.domain, task_relation=args.task_relation,
                            problem_type=args.problem_type, mode=args.mode, limit=args.limit)
        elif args.command == 'inspect':
            result = inspect(args.library, args.id)
        else:
            result = withdraw(args.library, args.id, args.user_quote, args.source)
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0
    except (AgentError, ValueError, OSError, KeyError) as exc:
        print(json.dumps({'error': str(exc)}, ensure_ascii=False))
        return 2


if __name__ == '__main__':
    raise SystemExit(main())
