"""Explicitly selected local reference collections, separate from old feedback.

Recall is advisory. Images stay local until a host attaches and observes a
specific evaluation reference; this module never grants generation rights.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Literal

from pydantic import Field

from .contracts import Strict, AestheticCard
from .io import AgentError, ROOT, digest, now, read_json, sha, write_json


class CollectionConfig(Strict):
    version: Literal[1] = 1
    enabled: bool = True
    library: str
    library_sha256: str = Field(pattern=r'^[0-9a-f]{64}$')
    source_root: str
    collection_id: str = Field(min_length=3, max_length=100)
    domains: list[Literal['vehicle', 'person', 'hybrid']] = Field(min_length=1, max_length=3)
    max_cards: int = Field(default=3, ge=1, le=3)
    user_quote: str = Field(min_length=8, max_length=3000)
    source_kind: Literal['host_transcription'] = 'host_transcription'
    configured_at: str


def config_path(kb: Path) -> Path:
    return kb.parent / 'local-library' / 'assets' / 'reference-library-config.json'


def configure(library: Path, source_root: Path, user_quote: str, *,
              destination: Path, collection_id: str, enabled: bool = True) -> dict:
    from .aesthetic_library import load, validate
    validate(library)
    root = source_root.resolve()
    if any(not Path(c.source_path).resolve().is_relative_to(root) for c in load(library).cards):
        raise AgentError('参考库含指定素材根之外的来源')
    config = CollectionConfig(library=str(library.resolve()), library_sha256=sha(library.read_bytes()),
                              source_root=str(root), collection_id=collection_id,
                              domains=['vehicle', 'person', 'hybrid'], user_quote=user_quote,
                              configured_at=now(), enabled=enabled)
    write_json(destination, config.model_dump())
    return {'status': 'configured', 'config': str(destination), 'collection_id': collection_id,
            'enabled': enabled, 'generation_authorized': False, 'images_submitted': False}


def initialize(packet: dict, kb: Path) -> None:
    """Only called by creation. Existing tasks never acquire a new collection."""
    file = config_path(kb)
    if not file.is_file():
        return
    config = CollectionConfig.model_validate(read_json(file))
    if config.enabled and packet['brief']['domain'] in config.domains:
        packet['local_reference_config'] = config.model_dump()


def _query(packet: dict) -> str:
    need = packet['need']
    return ' '.join([need['intent'], *need.get('desired_feeling', []),
                     *(n['query'] for n in need.get('knowledge_needs', []) if n['kind'] != 'task')])


def _editing(packet: dict) -> bool:
    return (packet['brief'].get('scope', 'create') != 'create' or
            packet['need'].get('understanding', {}).get('task_mode') == 'local_edit')


def _car_only(packet: dict) -> bool:
    need = packet['need']
    if need['domain'] != 'vehicle':
        return False
    from .visual_decision import CAR_PROFILES
    if packet.get('visual_decision_policy', {}).get('profile') in CAR_PROFILES:
        return True
    text = ' '.join([need['intent'], *need.get('must_keep', []), *need.get('avoid', [])])
    return any(term in text for term in ('纯车', '无人物', '不要人物', '不加人物', '不加人', '无人')) or any(
        term in {'人物', '模特', '人'} for term in need.get('avoid', []))


def search_cards(library: Path, query: str, domain: str, limit: int = 3, *, car_only: bool = False) -> dict:
    from .aesthetic_library import search
    limit = max(1, min(limit, 3))
    result = search(library, query, domain, limit=limit, positive_only=True, car_only=car_only)
    selected, seen = [], set()
    for hit in result['hits']:
        card = hit['card']
        if car_only and card['domain'] != 'vehicle':
            continue
        if card['source_sha256'] in seen:
            continue
        seen.add(card['source_sha256'])
        selected.append(hit)
        if len(selected) >= limit:
            break
    result['hits'] = selected
    result['car_only'] = car_only
    return result


def refresh(packet: dict) -> None:
    packet.pop('local_reference_context', None)
    if 'local_reference_config' not in packet:
        return
    config = CollectionConfig.model_validate(packet['local_reference_config'])
    if not config.enabled:
        return
    library = Path(config.library)
    if not library.is_file() or sha(library.read_bytes()) != config.library_sha256:
        raise AgentError('授权参考卡片库已改变；重新核对集合后配置，不静默替换旧任务来源')
    need = packet['need']
    suppressed = _editing(packet) or need['domain'] not in config.domains
    result = {'hits': [], 'skipped': [], 'method': 'curated_card_bm25_seen_images'} if suppressed else search_cards(
        library, _query(packet), need['domain'], config.max_cards, car_only=_car_only(packet))
    hits, seen = [], set()
    for hit in result['hits']:
        card = hit['card']
        if not Path(card['source_path']).resolve().is_relative_to(Path(config.source_root).resolve()):
            raise AgentError('命中卡片来源超出授权素材根')
        if card['source_sha256'] in seen:
            continue
        seen.add(card['source_sha256'])
        hits.append(hit)
        if len(hits) == config.max_cards:
            break
    context = {'version': 1, 'authority': 'user_selected_collection_advisory',
               'config_digest': digest(config.model_dump()), 'collection_id': config.collection_id,
               'query': _query(packet), 'need_digest': digest(need), 'domain': need['domain'],
               'status': 'suppressed_for_scope' if suppressed else ('candidates_need_observation' if hits else 'no_relevant_cases'),
               'method': result['method'], 'hits': hits, 'skipped': result['skipped'],
               'images_submitted': False, 'feedback_recalled': False,
               'notice': '仅供本题选择参考。采用前attach本次评价参考并实际看原图、observe；允许零采用。不得继承车型、身份、原构图或上传许可。'}
    context['digest'] = digest(context)
    packet['local_reference_context'] = context


def validate_context(packet: dict) -> None:
    if 'local_reference_config' not in packet:
        if packet.get('local_reference_context'):
            raise AgentError('本地参考上下文缺少明确选库来源')
        return
    config = CollectionConfig.model_validate(packet['local_reference_config'])
    context = packet.get('local_reference_context')
    if not config.enabled:
        if context:
            raise AgentError('关闭的参考集合不能带入候选')
        return
    if not isinstance(context, dict) or context.get('version') != 1:
        raise AgentError('本地参考上下文缺失或版本错误')
    if (context.get('config_digest') != digest(config.model_dump()) or
            context.get('need_digest') != digest(packet['need']) or context.get('query') != _query(packet) or
            context.get('domain') != packet['need']['domain'] or
            context.get('collection_id') != config.collection_id or
            context.get('digest') != digest({k: v for k, v in context.items() if k != 'digest'})):
        raise AgentError('本地参考来源、需求或卡片摘要不一致')
    if (context.get('images_submitted') is not False or context.get('feedback_recalled') is not False or
            context.get('authority') != 'user_selected_collection_advisory'):
        raise AgentError('本地参考集合不得上传图片或召回旧反馈')
    hits = context.get('hits')
    if not isinstance(hits, list) or len(hits) > config.max_cards:
        raise AgentError('本地参考最多三张')
    if (_editing(packet) or packet['need']['domain'] not in config.domains) and hits:
        raise AgentError('局部编辑或范围外任务不自动重设风格')
    seen = set()
    from .aesthetic_library import load
    library = Path(config.library)
    if not library.is_file() or sha(library.read_bytes()) != config.library_sha256:
        raise AgentError('授权参考卡片库已改变；不能重新计算摘要冒充原集合')
    approved = {c.id: c for c in load(library).cards}
    for hit in hits:
        card = AestheticCard.model_validate(hit['card'])
        source = Path(card.source_path).resolve()
        member = approved.get(card.id)
        domain = packet['need']['domain']
        applicable = (card.domain == domain or domain in card.transfer_domains or
                      (card.domain == 'hybrid' and domain in {'person', 'vehicle'}))
        if (member is None or digest(member.model_dump()) != digest(card.model_dump()) or not applicable or
                (_car_only(packet) and card.domain != 'vehicle')):
            raise AgentError('命中卡片不属于冻结授权集合或不适用当前领域')
        if (hit.get('id') != card.id or card.status != 'curated' or card.source_sha256 in seen or
                not source.is_relative_to(Path(config.source_root).resolve()) or
                not source.is_file() or sha(source.read_bytes()) != card.source_sha256):
            raise AgentError('本地参考卡片未观察、重复、越界或来源已改变')
        seen.add(card.source_sha256)
    for ref in packet.get('references', []):
        provenance = ref.get('local_card_source')
        if provenance is None:
            continue
        member = approved.get(provenance.get('card_id'))
        if (member is None or provenance.get('collection_id') != config.collection_id or
                provenance.get('source_sha256') != member.source_sha256 or
                provenance.get('card_digest') != digest(member.model_dump()) or
                ref.get('original_sha256') != member.source_sha256 or
                ref.get('evaluation_only') is not True or ref.get('rights') != 'unknown'):
            raise AgentError('本地卡片附件须保持评价用途与原集合来源，不能变为生成输入')


def instruction(packet: dict) -> str:
    context = packet.get('local_reference_context', {})
    if not context.get('hits'):
        return ''
    return '\n本题local_reference_context来自用户明确选定的本地素材集合，最多三张，属于待观察参考建议。先按当前难点选用，允许零采用；需要的候选用python -m hex_agent.reference_library attach --run RUN --card-id CARD --reference-id REF加入本次evaluation_only参考，实际看原图并observe，再用既有choices.reference_uses和同项checks采用。先区分图中事实、可迁移方法与本题自主决定，只取服务目标的少数关系；不照搬车型、人物、品牌或未经指定的姿势布局，不开启旧反馈库，不把词法命中当审美证明。\n'


def attach(run: Path, card_id: str, reference_id: str) -> dict:
    from .workflow import read_packet, add_reference, save_packet, ensure_references_mutable
    ensure_references_mutable(run)
    packet = read_packet(run)
    validate_context(packet)
    hit = next((h for h in packet.get('local_reference_context', {}).get('hits', []) if h['id'] == card_id), None)
    if hit is None:
        raise AgentError('只可加入本题实际召回的卡片')
    card = hit['card']
    note = '本地审美参考，身份不迁移。主导关系：' + card['dominant_decision'] + '；适用：' + '；'.join(card['transfer_conditions'])
    packet = add_reference(run, Path(card['source_path']), reference_id, 'aesthetic_positive',
                           note, 'unknown', evaluation_only=True, max_edge=2048,
                           allow_large_evaluation_source=True)
    packet['references'][-1]['local_card_source'] = {
        'card_id': card_id, 'source_sha256': card['source_sha256'],
        'card_digest': digest(card), 'collection_id': packet['local_reference_config']['collection_id']}
    return save_packet(packet, run)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description='Hex 本地素材卡片检索；不生成、不上传')
    commands = parser.add_subparsers(dest='command', required=True)
    p = commands.add_parser('configure')
    p.add_argument('--library', type=Path, required=True)
    p.add_argument('--source-root', type=Path, required=True)
    p.add_argument('--user-quote', required=True)
    p.add_argument('--collection-id', required=True)
    p.add_argument('--disabled', action='store_true')
    p.add_argument('--config', type=Path, default=config_path(ROOT / 'knowledge'))
    p = commands.add_parser('search')
    p.add_argument('--library', type=Path, required=True)
    p.add_argument('--query', required=True)
    p.add_argument('--domain', choices=['vehicle', 'person', 'hybrid'], required=True)
    p.add_argument('--car-only', action='store_true')
    p = commands.add_parser('attach')
    p.add_argument('--run', type=Path, required=True)
    p.add_argument('--card-id', required=True)
    p.add_argument('--reference-id', required=True)
    args = parser.parse_args(argv)
    try:
        if args.command == 'configure':
            result = configure(args.library, args.source_root, args.user_quote, destination=args.config,
                               collection_id=args.collection_id, enabled=not args.disabled)
        elif args.command == 'search':
            result = search_cards(args.library, args.query, args.domain, car_only=args.car_only)
        else:
            packet = attach(args.run, args.card_id, args.reference_id)
            result = {'status': 'evaluation_reference_attached', 'packet_digest': packet['digest']}
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0
    except (AgentError, ValueError) as exc:
        print(json.dumps({'error': str(exc)}, ensure_ascii=False))
        return 2


if __name__ == '__main__':
    raise SystemExit(main())
