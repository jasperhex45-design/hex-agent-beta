"""Load and verify the user's private aesthetic profile.

The profile is a small, local, advisory layer. It narrows which visible
relationships to try and review; it never creates a score or replaces the
user's final acceptance.
"""
from __future__ import annotations

from copy import deepcopy
from pathlib import Path

from .contracts import PrivateAestheticProfile
from .io import AgentError, read_json, digest


def load(path: Path) -> PrivateAestheticProfile:
    try:
        return PrivateAestheticProfile.model_validate(read_json(path))
    except FileNotFoundError as exc:
        raise AgentError(f"私人审美风格模块不存在：{path}") from exc
    except Exception as exc:
        raise AgentError(f"私人审美风格模块校验失败：{exc}") from exc


def _reference_links(packet: dict) -> dict:
    findings = {row['id']: row for row in packet.get('reference_analysis', {}).get('findings', [])}
    hits = packet.get('aesthetic_retrieval', {}).get('hits', [])
    evidence_refs = {}
    for reference in packet.get('references', []):
        finding = findings.get(reference.get('id'), {})
        if (reference.get('role') in ('aesthetic_negative', 'negative')
                or finding.get('role_in_task') in ('negative', 'reject')):
            continue
        if not reference.get('file') or not reference.get('sha256'):
            continue  # A title or URL alone is not an attached image.
        identifiers = {value for value in (reference.get('id'), reference.get('source_url')) if value}
        original_sha = reference.get('original_sha256')
        if original_sha:
            for hit in hits:
                card = hit.get('card') or {}
                if (hit.get('id') and hit['id'] == card.get('id')
                        and card.get('source_sha256') == original_sha):
                    identifiers.add(hit['id'])
        for identifier in identifiers:
            evidence_refs.setdefault(identifier, []).append({
                'reference_id': reference['id'], 'sha256': reference['sha256'],
                'role': reference.get('role'),
                'evaluation_only': bool(reference.get('evaluation_only', False)),
            })
    return evidence_refs


def task_context(profile: PrivateAestheticProfile | dict, packet: dict) -> dict:
    """Select evidence-linked candidates, never infer taste from task keywords.

    The caller supplies a verified packet and profile. This pure function neither
    opens image files nor claims a current visual observation. Positive references
    marked evaluation_only can inform local judgment without gaining upload rights.
    """
    if not isinstance(profile, PrivateAestheticProfile):
        profile = PrivateAestheticProfile.model_validate(profile)
    raw = profile.model_dump()
    need = packet.get('need', {})
    domain = need.get('domain', packet.get('brief', {}).get('domain'))
    scope = packet.get('brief', {}).get('scope')
    local_edit = scope != 'create' or (need.get('understanding') or {}).get('task_mode') == 'local_edit'
    evidence_refs = _reference_links(packet)

    rules, selection, not_selected = [], [], []
    for rule in profile.rules:
        if local_edit:
            reason = 'no_scope_applicability_evidence'
        elif domain not in rule.domains:
            reason = 'domain_not_applicable'
        else:
            matched = [ident for ident in rule.evidence_ids if ident in evidence_refs]
            if matched:
                linked = {reference['reference_id']: reference
                          for ident in matched for reference in evidence_refs[ident]}
                rules.append(rule.model_dump())
                selection.append({
                    'rule_id': rule.id,
                    'matched_evidence_ids': matched,
                    'references': list(linked.values()),
                    'basis': 'current_reference_evidence_link',
                    'status': 'candidate_not_adopted',
                })
                continue
            reason = 'no_current_reference_evidence_link'
        not_selected.append({'rule_id': rule.id, 'reason': reason})

    return {
        'format': 'liu.private-aesthetic-context.v1',
        'source_profile_digest': digest(raw),
        'status': 'candidates_require_task_judgment' if rules else 'no_applicable_rules',
        'review_policy': profile.review_policy,
        'rules': rules, 'selection': selection, 'not_selected': not_selected,
        'evidence_status': {
            'confirmed_positive_card_ids': list(profile.confirmed_positive_card_ids),
            'pinterest_observation_count': profile.pinterest_observation_count,
            'source_record': profile.source_record,
            'saved_collection_approval': 'preserved',
            'notice': '用户已认可收藏的正向参考地位保持不变；规则解释、喜欢原因及本次适用性仍待验证。',
        },
        'images_submitted': False,
        'notice': ('只提供与本轮实际参考有证据关联的候选，允许零采用；题材相同或关键词出现不构成采用理由。'
                   '局部编辑缺少范围适用证据时不注入规则。关联不证明本轮已看图；采用前仍须实际观察。'
                   '评价专用参考保持evaluation_only，不获得生成或上传许可；用户当前要求和最终反馈优先。'),
    }


def prune_context(packet: dict) -> dict | None:
    """Recheck existing task candidates after observation; never add candidates.

    Legacy profiles remain exactly as recorded. Only the scoped context format
    is narrowed, and the separate record of collection approval is preserved.
    """
    original = packet.get('private_style_profile')
    if original is None:
        return None
    context = deepcopy(original)
    if context.get('format') != 'liu.private-aesthetic-context.v1':
        return context
    links = _reference_links(packet)
    need = packet.get('need', {})
    domain = need.get('domain', packet.get('brief', {}).get('domain'))
    local_edit = (packet.get('brief', {}).get('scope') != 'create'
                  or (need.get('understanding') or {}).get('task_mode') == 'local_edit')
    selections = {row['rule_id']: row for row in context.get('selection', [])}
    analysis = packet.get('reference_analysis') or {}
    findings = {row['id']: row for row in analysis.get('findings', [])}
    observed = {row['id']: row['sha256'] for row in analysis.get('input_images', [])}
    rules, selection = [], []
    not_selected = {row['rule_id']: row for row in context.get('not_selected', [])}
    for rule in context.get('rules', []):
        previous = selections.get(rule['id'], {})
        old_refs = {row['reference_id']: row for row in previous.get('references', [])}
        matched, retained = [], {}
        if not local_edit and domain in rule.get('domains', []):
            for ident in previous.get('matched_evidence_ids', []):
                if ident not in rule.get('evidence_ids', []):
                    continue
                for current in links.get(ident, []):
                    old = old_refs.get(current['reference_id'])
                    if (not old or old.get('sha256') != current['sha256']
                            or old.get('role', current['role']) != current['role']):
                        continue
                    if ident not in matched:
                        matched.append(ident)
                    finding = findings.get(current['reference_id'], {})
                    current = dict(current)
                    current['observation'] = ('observed_candidate'
                                              if finding.get('seen') is True and
                                              observed.get(current['reference_id']) == current['sha256']
                                              else 'not_observed')
                    retained[current['reference_id']] = current
        if retained:
            rules.append(rule)
            selection.append({**previous, 'matched_evidence_ids': matched,
                              'references': list(retained.values()), 'status': 'candidate_not_adopted'})
            not_selected.pop(rule['id'], None)
        else:
            reason = ('no_scope_applicability_evidence' if local_edit else
                      'domain_not_applicable' if domain not in rule.get('domains', []) else
                      'current_reference_link_rejected_or_changed')
            not_selected[rule['id']] = {'rule_id': rule['id'], 'reason': reason}
    context.update(rules=rules, selection=selection, not_selected=list(not_selected.values()),
                   status='candidates_require_task_judgment' if rules else 'no_applicable_rules')
    return context


def verify(path: Path, aesthetic_library: Path | None = None) -> dict:
    profile = load(path)
    cards = None
    if aesthetic_library:
        from .aesthetic_library import load as load_library
        library = load_library(aesthetic_library)
        cards = {card.id for card in library.cards}
        missing = sorted(set(profile.confirmed_positive_card_ids) - cards)
        referenced = {e for rule in profile.rules for e in rule.evidence_ids}
        # Rule evidence can refer to user screenshot IDs and Pinterest records,
        # so only validate card IDs when they use the curated-card namespace.
        missing_rule_cards = sorted(
            e for e in referenced
            if e.startswith("aesthetic.") and e not in cards
        )
        if missing or missing_rule_cards:
            raise AgentError("私人审美模块引用了不存在的审美卡片：" + ",".join(sorted(set(missing + missing_rule_cards))))
    return {
        "status": "passed",
        "profile": str(path.resolve()),
        "owner": profile.owner,
        "rules": len(profile.rules),
        "confirmed_positive_cards": len(profile.confirmed_positive_card_ids),
        "explicit_negative_count": profile.explicit_negative_count,
        "pinterest_observation_count": profile.pinterest_observation_count,
        "review_policy": profile.review_policy,
        "aesthetic_library_checked": cards is not None,
        "notice": "私人模块只提供可追溯的审美方向与可见复查点；用户最终反馈覆盖模型建议。",
    }


def summary(path: Path) -> dict:
    profile = load(path)
    return {
        "status": "advisory_private_baseline",
        "owner": profile.owner,
        "rules": [
            {"id": rule.id, "domains": rule.domains, "confidence": rule.confidence,
             "visible_checks": rule.visible_checks}
            for rule in profile.rules
        ],
        "notice": "用户已认可收藏的正向地位保持不变；喜欢原因与规则解释仍待验证，不从未标记图片推断负例，不自动评分。",
    }
