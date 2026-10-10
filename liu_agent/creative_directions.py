"""Small, revocable direction proposals; never a second Need or Plan.

Validation proves binding and provenance, not meaningful artistic difference.
The creative model must still assess whether the documented alternatives work.
"""
from __future__ import annotations

import re
import unicodedata
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_serializer, model_validator

from .io import AgentError, digest, sha


class DirectionRecord(BaseModel):
    model_config = ConfigDict(extra='forbid', validate_assignment=True,
                              json_schema_extra=lambda s: s.update(required=list(s['properties'])))


class DirectionEvidence(DirectionRecord):
    kind: Literal['reference', 'host_visible_reference', 'feedback']
    id: str = Field(min_length=1)
    evidence_digest: str = Field(pattern=r'^[0-9a-f]{64}$')
    quote: str = Field(min_length=1)


class DirectionDraft(DirectionRecord):
    id: str = Field(min_length=1)
    intent_summary: str = Field(min_length=1)
    viewer_relation: str = Field(min_length=1)
    subject_state: str = Field(min_length=1)
    spatial_organization: str = Field(min_length=1)
    dominant_decision: str = Field(min_length=1)
    supporting_decisions: list[str] = Field(default_factory=list, max_length=3)
    reference_evidence: list[DirectionEvidence] = Field(default_factory=list, max_length=5)
    expected_effect: str = Field(min_length=1)
    tradeoff: str = Field(min_length=1)
    authority: Literal['agent_hypothesis'] = 'agent_hypothesis'
    purpose: str | None = Field(default=None, min_length=1)
    first_visual_focus: str | None = Field(default=None, min_length=1)
    subject_relation: str | None = Field(default=None, min_length=1)
    proposal_kind: Literal['direction', 'same_direction_variant'] = 'direction'
    base_direction_id: str | None = Field(default=None, min_length=1)
    variation_axes: list[Literal['camera_angle', 'color_temperature', 'focal_length']] = Field(
        default_factory=list, max_length=3)

    @field_validator('id', 'intent_summary', 'viewer_relation', 'subject_state',
                     'spatial_organization', 'dominant_decision', 'expected_effect', 'tradeoff')
    @classmethod
    def nonblank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError('方向字段不能只有空白')
        return value

    @field_validator('purpose', 'first_visual_focus', 'subject_relation', 'base_direction_id')
    @classmethod
    def optional_nonblank(cls, value: str | None) -> str | None:
        if value is not None and not value.strip():
            raise ValueError('方向卡字段不能只有空白')
        return value

    @model_validator(mode='after')
    def variant_binding(self):
        if len(self.variation_axes) != len(set(self.variation_axes)):
            raise ValueError('同方向变体的变化轴不能重复')
        if self.proposal_kind == 'same_direction_variant':
            if not self.base_direction_id or self.base_direction_id == self.id or not self.variation_axes:
                raise ValueError('同方向变体须标明原方向ID及角度、色温或焦段变化')
        elif self.base_direction_id is not None or self.variation_axes:
            raise ValueError('角度、色温或焦段变体须显式标为same_direction_variant')
        return self

    @model_serializer(mode='wrap')
    def legacy_dump(self, handler):
        result = handler(self)
        for key in ('purpose', 'first_visual_focus', 'subject_relation', 'proposal_kind',
                    'base_direction_id', 'variation_axes'):
            if key not in self.model_fields_set:
                result.pop(key, None)
        return result


class DirectionSelection(DirectionRecord):
    drafts: list[DirectionDraft] = Field(min_length=1, max_length=2)
    selected_id: str = Field(min_length=1)
    selection_reason: str = Field(min_length=1)
    alternatives_reason: str = ''
    question_id: str | None = None

    @model_serializer(mode='wrap')
    def legacy_dump(self, handler):
        result = handler(self)
        if 'question_id' not in self.model_fields_set:
            result.pop('question_id', None)
        return result


class DirectionTranslation(DirectionRecord):
    direction_id: str = Field(min_length=1)
    source_digest: str = Field(pattern=r'^[0-9a-f]{64}$')
    text: str = Field(min_length=1, max_length=6000)


def _value(record, key, default=None):
    return record.get(key, default) if isinstance(record, dict) else getattr(record, key, default)


def direction_card(draft, *, task_purpose: str = '') -> dict:
    """A readable projection of a draft, never a new selection or quality verdict.

    Old drafts keep their saved representation and hashes. Their existing visible
    actions provide the display fallback; no invented purpose becomes a lock.
    """
    draft = DirectionDraft.model_validate(draft.model_dump() if isinstance(draft, DirectionDraft) else draft)
    return {
        'id': draft.id, 'source_digest': digest(draft.model_dump()),
        'purpose': draft.purpose or task_purpose or draft.intent_summary,
        'first_visual_focus': draft.first_visual_focus or draft.dominant_decision,
        'subject_state': draft.subject_state,
        'subject_relation': draft.subject_relation or '；'.join((draft.viewer_relation, draft.spatial_organization)),
        'viewer_relation': draft.viewer_relation, 'spatial_organization': draft.spatial_organization,
        'expected_effect': draft.expected_effect, 'tradeoff': draft.tradeoff,
        'proposal_kind': draft.proposal_kind, 'base_direction_id': draft.base_direction_id,
        'variation_axes': draft.variation_axes,
        'quality_status': 'not_proven_by_code',
    }


def selected_direction_input(design) -> dict | None:
    """Expose only the selected draft's visible actions to the compiler.

    The full selected draft binds the digest, while reasons and hoped-for effects
    stay out of the prompt source. No alternative is silently mixed into it.
    """
    raw = _value(design, 'creative_directions')
    if raw is None:
        if _value(design, 'direction_id'):
            raise AgentError('direction_id必须绑定已记录的方向草案')
        return None
    selection = DirectionSelection.model_validate(raw.model_dump() if isinstance(raw, DirectionSelection) else raw)
    selected = [item for item in selection.drafts if item.id == selection.selected_id]
    if len(selected) != 1 or _value(design, 'direction_id') != selection.selected_id:
        raise AgentError('编译只能使用与direction_id一致的唯一选定方向')
    draft = selected[0]
    actions = [('观看关系', draft.viewer_relation), ('主体状态', draft.subject_state),
               ('空间组织', draft.spatial_organization), ('主导选择', draft.dominant_decision)]
    if draft.first_visual_focus is not None:
        actions.append(('第一视觉重点', draft.first_visual_focus))
    if draft.subject_relation is not None:
        actions.append(('主体关系', draft.subject_relation))
    actions.extend(('支撑选择', item) for item in draft.supporting_decisions)
    if any(not value.strip() for _, value in actions):
        raise AgentError('选定方向缺少可编译的画面关系')
    return {'id': draft.id, 'source_digest': digest(draft.model_dump()),
            'prompt_source': '\n'.join(label + '：' + value.strip() for label, value in actions)}


def compile_direction(design, row, language: str) -> str:
    """Bind a prompt to the chosen draft; this cannot prove translation quality."""
    selected = selected_direction_input(design)
    if selected is None:
        return ''
    if language not in ('zh', 'en'):
        raise AgentError('方向编译只支持已声明的zh或en语言')
    if language == 'zh':
        result = selected['prompt_source']
    else:
        raw = _value(row, 'direction_translation')
        if raw is None:
            raise AgentError('英文方向编译需要direction_translation及当前方向摘要')
        try:
            translated = DirectionTranslation.model_validate(raw.model_dump() if isinstance(raw, DirectionTranslation) else raw)
        except ValueError as exc:
            raise AgentError('direction_translation格式无效，须重新编译选定方向') from exc
        if translated.direction_id != selected['id'] or translated.source_digest != selected['source_digest']:
            raise AgentError('方向或草案内容已变化，旧direction_translation失效；须重新编译')
        result = translated.text.strip()
        if not result or not re.search(r'[A-Za-z]', result) or any(unicodedata.name(char, '').startswith(
                ('CJK UNIFIED IDEOGRAPH', 'CJK COMPATIBILITY IDEOGRAPH')) for char in result):
            raise AgentError('direction_translation须为非空英文，不可省略或保留中文关系')
    if re.search(r'(?<![\w-])--[^\s-]', result):
        raise AgentError('方向关系不能混入--参数；参数请放setup_notes')
    return result


def _normal(text: str) -> str:
    return re.sub(r'\s+', '', text).casefold()


def _verify_evidence(packet: dict, evidence: DirectionEvidence, *, allow_bound_selection: bool = False) -> None:
    if not evidence.quote.strip():
        raise AgentError('方向证据需要引用当前记录的具体文字')
    if evidence.kind == 'host_visible_reference':
        from .host_references import validate_context, observed_ids
        validate_context(packet)
        ref = next((r for r in packet.get('host_visible_references', []) if r['id'] == evidence.id), None)
        finding = next((f for f in (packet.get('reference_analysis') or {}).get('findings', [])
                        if f['id'] == evidence.id), None)
        if (not ref or evidence.evidence_digest != ref['metadata_digest']
                or evidence.id not in observed_ids(packet) or not finding or not finding.get('seen')
                or not finding.get('useful') or finding.get('role_in_task') in ('reject', 'negative')):
            raise AgentError('宿主方向参考须绑定当前实际观察与元数据摘要，不能冒充像素 SHA')
        if not any(evidence.quote in text for text in
                   [finding.get('visible_facts', ''), *finding.get('transfer', [])]):
            from .co_creation import registered_selection_evidence
            if not allow_bound_selection or not registered_selection_evidence(packet, evidence):
                raise AgentError('宿主方向参考引用不在当前观察或已绑定用户选择中')
        return
    if evidence.kind == 'reference':
        ref = next((r for r in packet.get('references', []) if r['id'] == evidence.id), None)
        analysis = packet.get('reference_analysis') or {}
        finding = next((f for f in analysis.get('findings', []) if f['id'] == evidence.id), None)
        manifest = {r['id']: r['sha256'] for r in analysis.get('input_images', [])}
        if (not ref or not finding or not finding.get('seen') or not finding.get('useful')
                or finding.get('role_in_task') == 'reject'
                or evidence.evidence_digest != ref.get('sha256')
                or manifest.get(evidence.id) != ref.get('sha256')):
            raise AgentError('方向参考必须绑定当前已看且适用的真实参考及摘要')
        text = [finding.get('visible_facts', ''), *finding.get('transfer', []),
                *finding.get('do_not_transfer', [])]
        if not any(evidence.quote in item for item in text):
            from .co_creation import registered_selection_evidence
            if not allow_bound_selection or not registered_selection_evidence(packet, evidence):
                raise AgentError('方向参考引用不在当前实际观察中')
        return
    row = next((r for r in packet.get('aesthetic_feedback', {}).get('records', [])
                if r.get('record', {}).get('id') == evidence.id), None)
    if not row or digest(row['record']) != evidence.evidence_digest:
        raise AgentError('方向反馈必须来自当前任务召回的记录及摘要')
    record = row['record']
    if evidence.quote not in record.get('user_quote', ''):
        raise AgentError('方向反馈必须引用用户原话，不能伪造用户认可原因')
    if row.get('kind') == 'pairwise' or record.get('format') == 'liu.personal-calibration.v1':
        if not row.get('ledger'):
            raise AgentError('方向成对偏好缺少当前账本，不能跳过撤回核验')
        from .calibration import active_records
        latest = next((r for r in active_records(Path(row['ledger'])) if r.id == evidence.id), None)
        if latest is None or digest(latest.model_dump()) != evidence.evidence_digest:
            raise AgentError('方向反馈账本已变化，先刷新当前任务召回')
    elif row.get('kind') == 'single_image' and record.get('task_id'):
        from .feedback import verified_feedback
        _, _, latest = verified_feedback(Path(record['task_id']))
        if 'feedback.' + digest(latest)[:20] != evidence.id:
            raise AgentError('方向反馈记录已变化，先刷新当前任务召回')
    images = record.get('images', []) if row.get('kind') == 'pairwise' else [record.get('candidate', {})]
    if not images or any(not image.get('path') or not Path(image['path']).is_file()
                         or sha(Path(image['path']).read_bytes()) != image.get('sha256') for image in images):
        raise AgentError('方向反馈的绑定图片缺失或已变化')


def validate_direction_selection(packet: dict, selection: DirectionSelection | None, design) -> dict:
    """Check a single existing DesignPlan binds only the selected small draft."""
    if selection is None:
        if getattr(design, 'direction_id', None):
            raise AgentError('direction_id必须绑定已记录的方向草案')
        return {'status': 'legacy_or_not_required', 'aesthetic_quality': 'not_proven_by_code'}
    selection = DirectionSelection.model_validate(selection.model_dump() if isinstance(selection, DirectionSelection) else selection)
    ids = [d.id for d in selection.drafts]
    if len(ids) != len(set(ids)) or selection.selected_id not in ids:
        raise AgentError('方向ID重复或选定方向不存在')
    if getattr(design, 'direction_id', None) != selection.selected_id:
        raise AgentError('设计只能展开选定方向；direction_id须与selected_id一致')
    if not selection.selection_reason.strip():
        raise AgentError('选择方向需要本次目标与取舍理由')
    if len(selection.drafts) > 1:
        if not selection.alternatives_reason.strip():
            raise AgentError('两个方向须说明当前任务为何存在不同表达途径')
        axes = ('viewer_relation', 'subject_state', 'spatial_organization')
        from itertools import combinations
        for first, second in combinations(selection.drafts, 2):
            if (first.proposal_kind == second.proposal_kind == 'direction'
                    and all(_normal(getattr(first, axis)) == _normal(getattr(second, axis)) for axis in axes)):
                raise AgentError('方向须在观看关系、主体状态或空间组织上有差异，不能只有暖冷变化')
    for draft in selection.drafts:
        if any(not item.strip() for item in draft.supporting_decisions):
            raise AgentError('方向支撑选择不能只有空白')
        keys = [(item.kind, item.id) for item in draft.reference_evidence]
        if len(keys) != len(set(keys)):
            raise AgentError('方向证据不能重复引用同一记录')
        for evidence in draft.reference_evidence:
            _verify_evidence(packet, evidence, allow_bound_selection=True)
    return {'status': 'bound_to_selected_direction', 'selected_id': selection.selected_id,
            'authority': 'agent_hypothesis', 'aesthetic_quality': 'not_proven_by_code',
            'semantic_difference': 'not_proven_by_code',
            'proposal_kind': next(d.proposal_kind for d in selection.drafts if d.id == selection.selected_id)}


def relation_queries(need: dict) -> list[str]:
    """Reuse the main model's mechanism questions; do not invent a style recipe."""
    queries = []
    for item in need.get('knowledge_needs', []):
        query = item.get('query', '').strip()
        if item.get('kind') != 'task' and query and query not in queries:
            queries.append(query)
        if len(queries) == 3:
            break
    return queries
