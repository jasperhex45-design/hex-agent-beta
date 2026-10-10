"""Curated visual references and task-conditioned aesthetic plans.

The normal image catalog answers "which files might be related?".  This
module answers the narrower question "which seen image demonstrates a visual
decision I can transfer, under what conditions, and how will I test it?".
"""
from __future__ import annotations

import math
import re
from collections import Counter
from pathlib import Path

from .contracts import AestheticCard, AestheticLibrary, AestheticPlan
from .io import AgentError, now, read_json, sha, write_json
from .retrieval import tokens


def load(path: Path) -> AestheticLibrary:
    try:
        library = AestheticLibrary.model_validate(read_json(path))
        for card in library.cards:
            card.source_path = str((path.resolve().parent / card.source_path).resolve())
        return library
    except FileNotFoundError:
        raise AgentError(f"审美素材库不存在：{path}")
    except Exception as exc:
        raise AgentError(f"审美素材库校验失败：{exc}") from exc


def validate(path: Path, check_sources: bool = True) -> dict:
    library = load(path)
    checked, missing, changed = 0, [], []
    for card in library.cards:
        source = Path(card.source_path)
        if not check_sources:
            continue
        if not source.is_file():
            missing.append(card.id)
            continue
        checked += 1
        if sha(source.read_bytes()) != card.source_sha256:
            changed.append(card.id)
    if missing or changed:
        bits = []
        if missing:
            bits.append("缺失=" + ",".join(missing))
        if changed:
            bits.append("hash变化=" + ",".join(changed))
        raise AgentError("审美素材来源校验失败：" + "; ".join(bits))
    return {"library": str(path.resolve()), "cards": len(library.cards),
            "domains": sorted({c.domain for c in library.cards}),
            "sources_checked": checked, "status": "passed"}


def _positive_text(text: str) -> str:
    """Conservative clause filter, not semantic negation understanding.

    Hosts should supply affirmative visual queries. Ambiguous contrast clauses
    may produce no recall and should be rewritten by the host, never inverted.
    """
    negative = r'不要|不适用|不套|不能|不可|不需要|无需|避免|排除|禁止|并非|不是|没有|无人物|无人|\b(?:no|not|without|avoid|exclude)\b'
    return ' '.join(clause for clause in re.split(r'[，,。.;；!?！？\n]', text)
                    if not re.search(negative, clause, re.I))


def _document(card: AestheticCard, positive_only: bool = False) -> Counter:
    if positive_only:
        # Applicability, failure examples and shared instructions remain on the
        # returned card for host review; they do not count as positive evidence.
        return Counter(tokens(' '.join(_positive_text(value) for value in [
            card.title, *card.tags, card.visible_facts,
            card.dominant_decision, *card.supporting_mechanisms,
        ])))
    text = " ".join([
        card.title, card.domain, *card.roles, *card.tags,
        card.dominant_decision, *card.supporting_mechanisms,
        *card.transfer_conditions, *card.anti_default,
    ])
    return Counter(tokens(text))


def search(path: Path, query: str, domain: str | None = None,
           limit: int = 6, require_curated: bool = True, *,
           relation_queries: list[str] | None = None, positive_only: bool = False,
           car_only: bool = False) -> dict:
    library = load(path)
    relation_queries = list(dict.fromkeys(q.strip() for q in (relation_queries or []) if q.strip()))[:3]
    effective_query = _positive_text(query) if positive_only else query
    if positive_only:
        relation_queries = [_positive_text(q) for q in relation_queries if _positive_text(q).strip()]
    terms = set(tokens(' '.join([effective_query, *relation_queries])))
    # Boilerplate and domain labels alone do not establish a shared relation.
    generic = set(tokens('主体 图片 图像 画面 视觉 关系 如何 什么 形成 表达 设计 生成 人物 人像 汽车 海报 插画'))
    if positive_only:
        generic.update(tokens('汽车 车辆 人物 人像 车人 人车 摄影 照片 参考 风格 好看 漂亮 高级 纯车 car vehicle person portrait photo photograph photography image design reference style subject'))
        terms -= generic
    relation_terms = set(tokens(' '.join(relation_queries))) - generic
    if not terms and not positive_only:
        raise AgentError("请输入视觉关系、题材或任务查询")
    def domain_match(card: AestheticCard) -> bool:
        if not domain:
            return True
        # Hybrid references can inform both human and automotive routes, but
        # they are never silently included for poster or unrelated domains.
        return (card.domain == domain or domain in card.transfer_domains
                or (card.domain == "hybrid" and domain in {"person", "vehicle"}))

    cards = [c for c in library.cards if domain_match(c)
             and (not require_curated or c.status == "curated")
             and (not car_only or c.domain == 'vehicle')]
    docs = [_document(c, positive_only) for c in cards]
    df = Counter(t for d in docs for t in d)
    hits = []
    for card, doc in zip(cards, docs):
        relation_document = doc if positive_only else Counter(tokens(' '.join([
            card.visible_facts, card.dominant_decision, *card.supporting_mechanisms,
            *card.transfer_conditions, *card.failure_signals,
        ])))
        matched_relations = sorted(relation_terms & relation_document.keys())
        if relation_queries and not matched_relations:
            continue
        matched = sorted(terms & doc.keys())
        if not matched and not matched_relations:
            continue
        score = 0.0
        for term in matched:
            idf = math.log(1 + (len(cards) - df[term] + .5) / (df[term] + .5))
            score += idf * doc[term]
        # Relation coverage leads; original lexical score breaks close ties.
        score += len(matched_relations) * 2
        hits.append({"id": card.id, "score": round(score, 5),
                     "matched_terms": matched, "matched_relation_terms": matched_relations,
                     "card": card.model_dump(),
                     "match_scope": 'same_domain' if not domain or domain == card.domain else 'structural_transfer'})
    hits.sort(key=lambda h: (-h["score"], h["id"]))
    selected, skipped, seen = [], [], set()
    for hit in hits:
        source_hash = hit['card']['source_sha256']
        if positive_only and source_hash in seen:
            continue
        source = Path(hit['card']['source_path'])
        if not source.is_file() or sha(source.read_bytes()) != hit['card']['source_sha256']:
            skipped.append({'id': hit['id'], 'reason': 'source_missing_or_changed'})
            continue
        selected.append(hit)
        seen.add(source_hash)
        if len(selected) >= max(1, min(limit, 20)):
            break
    return {"method": "positive_evidence_lexical_recall_v2" if positive_only else ("relation_scoped_lexical_recall" if relation_queries else "curated_card_bm25_seen_images"),
            "query": query, "relation_queries": relation_queries, "library": str(path.resolve()),
            "domain": domain, "hits": selected, "skipped": skipped,
            "notice": "只返回已看图且已整理迁移条件的卡片；命中仍需结合当前任务复查。"}


def attach_search(run: Path, result: dict) -> dict:
    """Persist curated hits in a task packet without turning them into references."""
    from .workflow import ensure_references_mutable, read_packet, save_packet
    packet = read_packet(run)
    from .visual_decision import record_only
    if record_only(packet):
        raise AgentError('生产配置尚未晋升审美经验；不能将检索结果写入任务')
    ensure_references_mutable(run)
    packet["aesthetic_retrieval"] = result
    if result.get('library'):
        from .style_context import StyleContextConfig
        config = StyleContextConfig.model_validate(packet.get('style_context_config', {}))
        config.library = result['library']
        config.enabled = True
        packet['style_context_config'] = config.model_dump()
    return save_packet(packet, run)


def attach_reference(run: Path, card_id: str, reference_id: str, note: str,
                     rights: str, usage: str = 'evaluation_only') -> dict:
    """Turn one current curated hit into an explicit image attachment, never all hits."""
    from .workflow import read_packet, add_reference, save_packet, ensure_references_mutable
    ensure_references_mutable(run)
    packet = read_packet(run)
    if usage not in ('generation', 'evaluation_only'):
        raise AgentError('素材用途须为generation或evaluation_only')
    if usage == 'generation' and rights != 'user_authorized':
        raise AgentError('生成参考需要用户授权；已有授权可沿用')
    hit = next((h for h in packet.get('aesthetic_retrieval', {}).get('hits', []) if h['id'] == card_id), None)
    if not hit:
        raise AgentError('只可选择当前任务实际命中的审美卡片；先检索')
    card = AestheticCard.model_validate(hit['card'])
    if card.status != 'curated':
        raise AgentError('未实际整理的卡片不能作为已看图参考')
    source = Path(card.source_path)
    if not source.is_file() or sha(source.read_bytes()) != card.source_sha256:
        raise AgentError('审美卡片源图缺失或hash变化；重新检索与看图')
    packet = add_reference(run, source, reference_id, 'aesthetic_positive', note, rights,
                           evaluation_only=usage != 'generation', max_edge=2048)
    packet['references'][-1]['aesthetic_card_source'] = {
        'card_id': card.id, 'version': card.version, 'source_sha256': card.source_sha256}
    return save_packet(packet, run)


def save_plan(path: Path, raw: dict) -> dict:
    plan = AestheticPlan.model_validate(raw)
    write_json(path, plan.model_dump())
    return plan.model_dump()


def validate_plan(path: Path, library_path: Path) -> dict:
    plan = AestheticPlan.model_validate(read_json(path))
    library = load(library_path)
    ids = {c.id for c in library.cards if c.status == "curated"}
    missing = sorted(set(plan.card_ids) - ids)
    if missing:
        raise AgentError("审美方案引用了未整理的卡片：" + ",".join(missing))
    if len(plan.card_ids) != len(set(plan.card_ids)):
        raise AgentError("审美方案的 card_ids 不能重复")
    return {"plan": str(path.resolve()), "domain": plan.domain,
            "cards": plan.card_ids, "status": "passed"}


def build_card(source_path: Path, raw: dict) -> AestheticCard:
    """Fill the immutable source hash while keeping authored observations explicit."""
    if not source_path.is_file():
        raise AgentError(f"素材不存在：{source_path}")
    payload = {**raw, "source_path": str(source_path.resolve()),
               "source_sha256": sha(source_path.read_bytes())}
    return AestheticCard.model_validate(payload)


def build_library(path: Path, source_catalog: Path, cards: list[tuple[Path, dict]]) -> dict:
    built = [build_card(source, raw).model_dump() for source, raw in cards]
    if len({c["id"] for c in built}) != len(built):
        raise AgentError("审美卡片 ID 重复")
    result = AestheticLibrary(
        format="liu.aesthetic-library.v1", version=1, generated_at=now(),
        source_catalog=str(source_catalog.resolve()),
        selection_policy=[
            "只收录已实际打开并写出可见事实的图片",
            "一张图只提取一个主导决定，其他元素作为支持机制",
            "每张卡片必须写迁移条件、不可迁移项、反默认项和失败信号",
            "用户原始素材只读；库只保存路径、hash、观察和决策，不复制原图",
        ], cards=[AestheticCard.model_validate(c) for c in built],
    )
    write_json(path, result.model_dump())
    return {"library": str(path.resolve()), "cards": len(built),
            "domains": sorted({c["domain"] for c in built}), "status": "written"}
