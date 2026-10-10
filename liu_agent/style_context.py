"""Small, task-scoped case retrieval shared by host and API execution.

The configuration selects local sources, not an aesthetic truth or a model.
Recalled choices remain explicit, image-bound user evidence; they never alter
the current brief, its locks, or the private preference profile.
"""
from __future__ import annotations

from pathlib import Path
from typing import Literal

from pydantic import Field

from .contracts import Strict
from .io import AgentError, read_json, sha, digest
from .retrieval import tokens


class FeedbackSource(Strict):
    path: str
    domains: list[str] = Field(default_factory=list)
    query_terms: list[str] = Field(default_factory=list)
    scope_note: str = ''


class StyleContextConfig(Strict):
    version: Literal[1] = 1
    enabled: bool = True
    library: str | None = None
    max_cards: int = Field(default=3, ge=1, le=3)
    max_feedback: int = Field(default=2, ge=1, le=2)
    feedback_sources: list[FeedbackSource] = Field(default_factory=list, max_length=8)
    single_feedback_roots: list[str] = Field(default_factory=list, max_length=4)


def config_for(packet: dict, kb: Path, *, library: Path | None = None,
               enabled: bool | None = None) -> StyleContextConfig:
    saved = packet.get('style_context_config')
    if saved is not None:
        config = StyleContextConfig.model_validate(saved)
    else:
        file = kb.parent / 'local-library' / 'style-context.json'
        config = StyleContextConfig.model_validate(read_json(file)) if file.is_file() else StyleContextConfig()
        if config.library:
            config.library = str((file.parent / config.library).resolve())
        for source in config.feedback_sources:
            source.path = str((file.parent / source.path).resolve())
        config.single_feedback_roots = [str((file.parent / path).resolve()) for path in config.single_feedback_roots]
    if library is not None:
        config.library = str(library.resolve())
        config.enabled = True
    if enabled is not None:
        config.enabled = enabled
    return config


def recall(config: StyleContextConfig, need: dict) -> dict:
    from .calibration import active_records

    query = ' '.join([need['intent'], *need.get('desired_feeling', []),
                      *(n['query'] for n in need.get('knowledge_needs', []) if n['kind'] != 'task')])
    query_tokens = set(tokens(query))
    matches, skipped, seen = [], [], set()
    for source in config.feedback_sources:
        path = Path(source.path)
        if not path.is_file():
            skipped.append({'source': str(path), 'reason': 'missing_ledger'})
            continue
        if source.domains and need['domain'] not in source.domains:
            continue
        try:
            records = active_records(path)
        except (AgentError, ValueError) as exc:
            skipped.append({'source': str(path), 'reason': 'invalid_ledger', 'detail': str(exc)})
            continue
        for record in records:
            if record.domain != need['domain'] or record.id in seen:
                continue
            # Configured terms declare a narrow task context, not an inferred
            # taste. Unscoped ledgers must match the actual comparison text.
            context = ' '.join(source.query_terms) if source.query_terms else ' '.join(
                [record.criterion, record.user_quote, *record.method_ids, *record.grammar_ids])
            matched = sorted(query_tokens & set(tokens(context)))
            if not matched:
                continue
            if any(not Path(img.path).is_file() or sha(Path(img.path).read_bytes()) != img.sha256
                   for img in record.images):
                skipped.append({'id': record.id, 'reason': 'image_missing_or_changed'})
                continue
            seen.add(record.id)
            matches.append({'kind': 'pairwise', 'record': record.model_dump(), 'matched_terms': matched,
                            'scope_note': source.scope_note, 'ledger': str(path)})
    from .feedback_recall import recall_runs
    single = recall_runs(config.single_feedback_roots, need, limit=config.max_feedback)
    matches.extend(single['records'])
    skipped.extend(single['skipped'])
    # Filter for context and verify evidence BEFORE truncating the result.
    matches.sort(key=lambda row: (len(row['matched_terms']), row['record']['recorded_at']), reverse=True)
    for row in matches:
        row['evidence_digest'] = digest(row['record'])
    return {'authority': 'explicit_user_feedback_advisory', 'records': matches[:config.max_feedback],
            'skipped': skipped, 'images_submitted': False,
            'single_feedback_scanned': single['scanned_runs'], 'scan_truncated': single['scan_truncated'],
            'notice': '成对选择与单图验收分开标记；只证明对应图像的用户反馈，未看原图不能重述视觉原因，不能泛化为永久偏好。'}


def effective_feedback(packet: dict) -> dict:
    """Project a saved recall into current inputs without rewriting history."""
    from copy import deepcopy
    from .calibration import active_records
    result = deepcopy(packet.get('aesthetic_feedback', {}))
    if not result:
        return result
    records, skipped, ledgers = [], [], {}
    for row in result.get('records', []):
        item = row.get('record', {})
        is_pair = row.get('kind') == 'pairwise' or item.get('format') == 'liu.personal-calibration.v1'
        if not is_pair:
            records.append(row)
            continue
        ledger_path = row.get('ledger')
        if not ledger_path:
            skipped.append({'id': item.get('id'), 'reason': 'missing_current_ledger'})
            continue
        if ledger_path not in ledgers:
            try:
                ledgers[ledger_path] = {record.id: digest(record.model_dump()) for record in active_records(Path(ledger_path))}
            except (AgentError, OSError, ValueError, KeyError, TypeError):
                ledgers[ledger_path] = {}
        if ledgers[ledger_path].get(item.get('id')) != digest(item):
            skipped.append({'id': item.get('id'), 'reason': 'withdrawn_changed_or_unverifiable_record'})
        else:
            records.append(row)
    result['records'] = records
    if skipped:
        result['skipped'] = [*result.get('skipped', []), *skipped]
    return result


def refresh(packet: dict, kb: Path, *, library: Path | None = None,
            query: str | None = None, enabled: bool | None = None) -> None:
    from . import aesthetic_library
    from .creative_directions import relation_queries

    config = config_for(packet, kb, library=library, enabled=enabled)
    packet['style_context_config'] = config.model_dump()
    packet.pop('aesthetic_retrieval', None)
    packet.pop('aesthetic_feedback', None)
    if not config.enabled:
        packet['style_context'] = {'status': 'disabled'}
        return
    need = packet['need']
    actual_query = query or ' '.join([need['intent'], *need.get('desired_feeling', []),
                                    *(n['query'] for n in need.get('knowledge_needs', []) if n['kind'] != 'task')])
    if config.library:
        queries = relation_queries(need) if not query else []
        result = aesthetic_library.search(Path(config.library), actual_query, need['domain'], config.max_cards,
                                          relation_queries=queries)
        result['adoption_policy'] = 'optional_with_observed_source'
        packet['aesthetic_retrieval'] = result
    packet['aesthetic_feedback'] = recall(config, need)
    hits = packet.get('aesthetic_retrieval', {}).get('hits', [])
    packet['style_context'] = {
        'status': 'candidates_need_observation' if hits else 'no_relevant_cases',
        'relation_queries': relation_queries(need) if not query else [],
        'skill': '.agents/skills/liu-aesthetic-judgment/SKILL.md',
        'source_images': [{'card_id': h['id'], 'path': h['card']['source_path'],
                           'sha256': h['card']['source_sha256'], 'match_scope': h['match_scope']}
                          for h in hits],
        'notice': '命中不等于采用。选中后 attach-curated 并实际看图 observe；反馈不替代当前原话。',
    }
