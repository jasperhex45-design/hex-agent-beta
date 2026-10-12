"""Explicitly selected local reference collections, separate from old feedback.

Recall is advisory. Images stay local until a host attaches and observes a
specific evaluation reference; this module never grants generation rights.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import tempfile
from pathlib import Path
from typing import Literal, get_args

from pydantic import Field

from .contracts import Strict, AestheticCard, AestheticLibrary
from .io import AgentError, ROOT, digest, now, read_json, sha, write_json


CollectionDomain = Literal['vehicle', 'person', 'hybrid', 'illustration', 'poster', 'character', 'prop']
COLLECTION_DOMAINS = get_args(CollectionDomain)


class CollectionConfig(Strict):
    # Keep the v1 fields and serialization unchanged: running tasks bind the
    # complete config digest, including defaults already present in v1.
    version: Literal[1] = 1
    enabled: bool = True
    library: str
    library_sha256: str = Field(pattern=r'^[0-9a-f]{64}$')
    source_root: str
    collection_id: str = Field(min_length=3, max_length=100)
    domains: list[CollectionDomain] = Field(min_length=1, max_length=7)
    max_cards: int = Field(default=3, ge=1, le=3)
    user_quote: str = Field(min_length=8, max_length=3000)
    source_kind: Literal['host_transcription'] = 'host_transcription'
    configured_at: str


def config_path(kb: Path) -> Path:
    return kb.parent / 'local-library' / 'assets' / 'reference-library-config.json'


def _domains(cards: list[AestheticCard], selected: list[str] | None) -> list[str]:
    if selected is not None:
        return selected
    cards = [card for card in cards if card.status == 'curated']
    available = {domain for card in cards for domain in [card.domain, *card.transfer_domains]}
    # Match aesthetic_library.search's existing hybrid eligibility, without
    # expanding other cards to domains that the host never explicitly allowed.
    if 'hybrid' in {card.domain for card in cards}:
        available.update(('person', 'vehicle'))
    return [domain for domain in COLLECTION_DOMAINS if domain in available]


def _preference_context(path: Path, library: AestheticLibrary, collection_id: str,
                        hits: list[dict]) -> dict | None:
    """Read feedback only from the selected, content-bound catalog export.

    Old inventories were optional bookkeeping and remain so. Catalog manifests
    have a content-addressed filename; missing or downgraded exports must fail
    instead of silently changing a running task's preference evidence.
    """
    source = path.resolve().parent / library.source_catalog
    addressed = re.fullmatch(r'[0-9a-f]{64}\.sources\.json', source.name) is not None
    if not source.is_file():
        if addressed:
            raise AgentError('参考偏好清单缺失；不能静默替换已发布集合')
        return None
    try:
        manifest = read_json(source)
    except (AgentError, OSError, ValueError) as exc:
        if addressed:
            raise AgentError('参考偏好清单无法读取；保留原集合来源') from exc
        return None
    if not isinstance(manifest, dict) or manifest.get('format') != 'hex.reference-catalog.v1':
        if addressed:
            raise AgentError('参考偏好清单格式已改变')
        return None
    from .reference_catalog import ReferenceFeedback
    fingerprint = digest({'manifest': manifest, 'cards': [card.model_dump() for card in library.cards]})
    if source.is_symlink() or source.name != fingerprint + '.sources.json':
        raise AgentError('参考偏好清单与已发布卡片摘要不一致')
    if manifest.get('collection_id') != collection_id:
        raise AgentError('参考偏好清单不属于当前选定集合')
    try:
        scope = ReferenceFeedback.valid_scope(manifest.get('scope', ''))
    except (TypeError, ValueError) as exc:
        raise AgentError('参考偏好清单的范围无效') from exc
    references = manifest.get('references')
    approved = {card.id: card for card in library.cards}
    if not isinstance(references, list) or len(references) != len(library.cards):
        raise AgentError('参考偏好清单与集合成员不一致')
    members = {}
    for row in references:
        if not isinstance(row, dict):
            raise AgentError('参考偏好清单成员无效')
        card_id = row.get('card_id')
        card = approved.get(card_id) if isinstance(card_id, str) else None
        if (card is None or card_id in members or row.get('source_sha256') != card.source_sha256
                or row.get('reference_id') != 'ref_' + card.source_sha256[:24]
                or not isinstance(row.get('sources'), list) or not isinstance(row.get('preferences'), list)):
            raise AgentError('参考偏好清单的卡片或原图绑定无效')
        aspects = set()
        for raw in row['preferences']:
            try:
                event = ReferenceFeedback.model_validate(raw)
            except ValueError as exc:
                raise AgentError('参考偏好记录无效') from exc
            key = (event.aspect, event.scope)
            if (event.reference_id != row['reference_id'] or event.source_sha256 != card.source_sha256
                    or event.scope not in {'global', scope} or key in aspects
                    or event.signal not in {'prefer', 'avoid'}):
                raise AgentError('参考偏好记录超出声明范围、原图不符或含未落实的撤回')
            aspects.add(key)
        members[card_id] = row
    items = [{key: members[hit['id']][key]
              for key in ('card_id', 'reference_id', 'source_sha256', 'preferences')}
             for hit in hits if hit['id'] in members and members[hit['id']]['preferences']]
    if not items:
        return None
    return {'authority': 'user_feedback_in_selected_collection',
            'collection_id': collection_id, 'collection_scope': scope,
            'manifest_path': str(source.resolve()), 'manifest_digest': digest(manifest),
            'requires_project_match': scope != 'global', 'items': items}


def configure(library: Path, source_root: Path, user_quote: str, *,
              destination: Path, collection_id: str, enabled: bool = True,
              domains: list[str] | None = None) -> dict:
    from .aesthetic_library import load, validate
    validate(library)
    cards = load(library).cards
    root = source_root.resolve()
    if any(not Path(c.source_path).resolve().is_relative_to(root) for c in cards):
        raise AgentError('参考库含指定素材根之外的来源')
    config = CollectionConfig(library=str(library.resolve()), library_sha256=sha(library.read_bytes()),
                              source_root=str(root), collection_id=collection_id,
                              domains=_domains(cards, domains), user_quote=user_quote,
                              configured_at=now(), enabled=enabled)
    write_json(destination, config.model_dump())
    return {'status': 'configured', 'config': str(destination), 'collection_id': collection_id,
            'enabled': enabled, 'generation_authorized': False, 'images_submitted': False}


def _check_snapshot(path: Path, content: bytes) -> None:
    # Never repair or replace an existing address. In particular, accepting a
    # symlink would let a later external edit silently change the frozen file.
    if (path.is_symlink() or not path.is_file() or path.stat().st_size != len(content)
            or path.read_bytes() != content):
        raise AgentError('不可变参考快照损坏或路径被占用；保留原配置，不覆盖旧快照')


def _write_snapshot(path: Path, content: bytes) -> bool:
    """Atomically expose a complete, create-only file; return whether reused."""
    if path.exists() or path.is_symlink():
        _check_snapshot(path, content)
        return True
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = tempfile.NamedTemporaryFile(mode='wb', dir=path.parent,
                                            prefix='.reference-', suffix='.tmp', delete=False)
    tmp = Path(temporary.name)
    try:
        with temporary:
            temporary.write(content)
            temporary.flush()
            os.fsync(temporary.fileno())
        try:
            # Same-directory hard linking is atomic and cannot overwrite an
            # existing destination, including one published by another process.
            os.link(tmp, path)
        except FileExistsError:
            _check_snapshot(path, content)
            return True
        _check_snapshot(path, content)
        return False
    finally:
        tmp.unlink(missing_ok=True)


def publish_collection(library: Path, source_root: Path, user_quote: str, *,
                       destination: Path, collection_id: str, snapshots_root: Path,
                       domains: list[str] | None = None) -> dict:
    """Freeze a curated library, then select that version for future tasks.

    Source images remain in their original location and are still hash-checked.
    Running tasks hold their own config and immutable snapshot; publication does
    not mutate either. Direct configure retains its explicit file-pin semantics.
    """
    from .aesthetic_library import load, validate
    validate(library)
    curated = load(library)
    if any(card.status != 'curated' for card in curated.cards):
        raise AgentError('发布集合仅接受已实际看图整理的 curated 卡片')
    if len({card.id for card in curated.cards}) != len(curated.cards):
        raise AgentError('发布集合的审美卡片 ID 重复')
    root = source_root.resolve()
    if any(not Path(card.source_path).resolve().is_relative_to(root) for card in curated.cards):
        raise AgentError('参考库含指定素材根之外的来源')
    # load resolves source_path against the original library location before
    # moving metadata into a snapshot directory. Preserve a relative manifest's
    # original location as well. No image bytes are copied.
    curated.source_catalog = str((library.resolve().parent / curated.source_catalog).resolve())
    content = (json.dumps(curated.model_dump(), ensure_ascii=False, sort_keys=True, indent=2) + '\n').encode('utf-8')
    if len(content) > 4 * 1024 * 1024:
        raise AgentError('参考快照 JSON 超过 4 MiB 限制；请分成较小集合')
    snapshot_hash = sha(content)
    snapshot_root = snapshots_root.resolve()
    snapshot = snapshot_root / f'{snapshot_hash}.json'
    target = destination.resolve()
    # A configuration update must never overwrite either source material or any
    # immutable snapshot, including a previous version's address.
    addressed_target = (target.parent == snapshot_root and target.suffix == '.json'
                        and len(target.stem) == 64 and all(c in '0123456789abcdef' for c in target.stem))
    if (target == library.resolve() or addressed_target or
            any(target == Path(card.source_path).resolve() for card in curated.cards)):
        raise AgentError('配置目标不能覆盖参考库、原图或不可变快照')
    # Validate the complete new selection before any filesystem mutation.
    planned = CollectionConfig(library=str(snapshot), library_sha256=snapshot_hash,
                               source_root=str(root), collection_id=collection_id,
                               domains=_domains(curated.cards, domains), user_quote=user_quote,
                               configured_at=now())
    _preference_context(library, curated, collection_id, [])
    try:
        reused = _write_snapshot(snapshot, content)
        validate(snapshot)
        _check_snapshot(snapshot, content)
        previous = None
        if destination.is_file():
            try:
                previous = CollectionConfig.model_validate(read_json(destination))
            except (AgentError, ValueError):
                pass
        # Repeated publication of an identical selection preserves its timestamp
        # and bytes. A changed selection only updates the new-task config.
        if (previous is None or previous.model_dump(exclude={'configured_at'}) !=
                planned.model_dump(exclude={'configured_at'})):
            write_json(destination, planned.model_dump())
    except OSError as exc:
        raise AgentError(f'参考快照发布失败；原配置未切换：{exc}') from exc
    return {'config': str(destination), 'collection_id': collection_id,
            'enabled': True, 'generation_authorized': False, 'images_submitted': False,
            'status': 'published', 'snapshot': str(snapshot),
            'snapshot_sha256': snapshot_hash, 'cards': len(curated.cards),
            'domains': planned.domains, 'reused_snapshot': reused, 'new_tasks_only': True}


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
    from .aesthetic_library import load
    preferences = _preference_context(library, load(library), config.collection_id, hits)
    if preferences is not None:
        context['preference_context'] = preferences
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
    loaded = load(library)
    approved = {c.id: c for c in loaded.cards}
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
    preferences = _preference_context(library, loaded, config.collection_id, hits)
    if ((preferences is None and 'preference_context' in context)
            or (preferences is not None and context.get('preference_context') != preferences)):
        raise AgentError('本题偏好上下文与选定清单、命中卡片或声明范围不一致')
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
    result = '\n本题local_reference_context来自用户明确选定的本地素材集合，最多三张，属于待观察参考建议。先按当前难点选用，允许零采用；需要的候选用python -m hex_agent.reference_library attach --run RUN --card-id CARD --reference-id REF加入本次evaluation_only参考，实际看原图并observe，再用既有choices.reference_uses和同项checks采用。先区分图中事实、可迁移方法与本题自主决定，只取服务目标的少数关系；不照搬车型、人物、品牌或未经指定的姿势布局，不开启旧反馈库，不把词法命中当审美证明。\n'
    if context.get('preference_context'):
        result += ('preference_context仅含本次选定清单中命中卡片的逐维度用户原话，属于偏好证据，'
                   '不等于视觉事实、全图认可或新的任务指令。按当前需求及每项scope判断prefer/avoid的适用性，'
                   '不改词法分数、不自动套用整种风格；project范围仅在本题已明确属于该项目时采用，'
                   '范围无法确认则零采用，不推广到其他项目或全局偏好。\n')
    return result


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
    p.add_argument('--domains', choices=COLLECTION_DOMAINS, nargs='+')
    p.add_argument('--disabled', action='store_true')
    p.add_argument('--config', type=Path, default=config_path(ROOT / 'knowledge'))
    p = commands.add_parser('publish', help='发布不可变卡片版本，只供新任务使用')
    p.add_argument('--library', type=Path, required=True)
    p.add_argument('--source-root', type=Path, required=True)
    p.add_argument('--user-quote', required=True)
    p.add_argument('--collection-id', required=True)
    p.add_argument('--snapshots-root', type=Path, required=True)
    p.add_argument('--domains', choices=COLLECTION_DOMAINS, nargs='+')
    p.add_argument('--config', type=Path, default=config_path(ROOT / 'knowledge'))
    p = commands.add_parser('search')
    p.add_argument('--library', type=Path, required=True)
    p.add_argument('--query', required=True)
    p.add_argument('--domain', choices=COLLECTION_DOMAINS, required=True)
    p.add_argument('--car-only', action='store_true')
    p = commands.add_parser('attach')
    p.add_argument('--run', type=Path, required=True)
    p.add_argument('--card-id', required=True)
    p.add_argument('--reference-id', required=True)
    args = parser.parse_args(argv)
    try:
        if args.command == 'configure':
            result = configure(args.library, args.source_root, args.user_quote, destination=args.config,
                               collection_id=args.collection_id, enabled=not args.disabled, domains=args.domains)
        elif args.command == 'publish':
            result = publish_collection(args.library, args.source_root, args.user_quote, destination=args.config,
                                        collection_id=args.collection_id, snapshots_root=args.snapshots_root,
                                        domains=args.domains)
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
