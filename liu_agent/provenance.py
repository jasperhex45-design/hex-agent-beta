"""Append-only message provenance. Hashes bind bytes, not semantic truth."""
from pathlib import Path
from typing import Literal
from pydantic import Field, model_validator
from .contracts import Strict, Need, ConstraintEvidence, SourceRef
from .io import AgentError, read_json, digest


class GenerationGrant(Strict):
    # Host records an explicit cumulative allowance; intent delegation is not one.
    max_images: int = Field(strict=True, ge=1, le=2)


class RepairGrant(Strict):
    """Explicit permission for one local, proportional reduction, not a redraw."""
    scope: Literal['downscale_to_requested_size']
    max_repairs: int = Field(strict=True, ge=1, le=1)


class InputMessage(Strict):
    id: str = Field(min_length=1, max_length=160)
    actor: Literal['user', 'assistant']
    text: str = Field(min_length=1, max_length=24000)
    origin: Literal['host_transcription', 'host_message_record', 'historical_import']
    locator: str = Field(min_length=1, max_length=2000)
    in_reply_to: str = ''
    confirmed_proposal_ids: list[str] = Field(default_factory=list)
    generation_grant: GenerationGrant | None = None
    # Omit absent new fields so historical source/grant digests stay unchanged.
    repair_grant: RepairGrant | None = Field(default=None, exclude_if=lambda value: value is None)

    @model_validator(mode='after')
    def repair_requires_user_source(self):
        if self.repair_grant is not None and (self.actor != 'user' or self.origin not in
                ('host_transcription', 'host_message_record')):
            raise ValueError('repair_grant 必须绑定宿主记录的真实用户来源，不能使用提案或历史导入')
        return self


class InputSources(Strict):
    format: Literal['liu.input-sources.v1']
    messages: list[InputMessage] = Field(max_length=100)


def merge_sources(previous: dict | None, source_file: Path | None) -> dict:
    old = verified_sources(previous) if previous else []
    incoming = InputSources.model_validate(read_json(source_file)).messages if source_file else []
    rows = list(old)
    by_id = {r['id']: r for r in rows}
    incoming_ids = set()
    for message in incoming:
        row = message.model_dump()
        if row['id'] in incoming_ids:
            raise AgentError('消息来源ID重复')
        incoming_ids.add(row['id'])
        if row['id'] in by_id:
            if row != by_id[row['id']]:
                raise AgentError('用户原始消息只允许追加；不能改写已有来源')
            continue
        if row['in_reply_to'] and row['in_reply_to'] not in by_id:
            raise AgentError('回答所引用的来源消息不存在或顺序错误')
        rows.append(row)
        by_id[row['id']] = row
    body = {'format': 'liu.input-sources.v1', 'messages': rows}
    return {**body, 'digest': digest(body)}


def verified_sources(sources: dict | None) -> list[dict]:
    if not sources:
        return []
    body = {k: v for k, v in sources.items() if k != 'digest'}
    if sources.get('digest') != digest(body):
        raise AgentError('消息来源摘要不匹配')
    parsed = InputSources.model_validate(body)
    rows = [m.model_dump() for m in parsed.messages]
    if len({r['id'] for r in rows}) != len(rows):
        raise AgentError('消息来源ID重复')
    return rows


def validate_version(packet: dict) -> None:
    version = packet.get('source_contract_version')
    if version is not None and (type(version) is not int or version != 1):
        raise AgentError('未知需求来源合同版本')
    if packet.get('source_contract_version') == 1:
        verified_sources(packet.get('input_sources'))


def require_sources(packet: dict) -> None:
    validate_version(packet)
    if packet.get('source_contract_version') != 1 or not any(
            r['actor'] == 'user' for r in verified_sources(packet.get('input_sources'))):
        raise AgentError('需要真实用户消息来源 --sources；旧任务可查看与导出，但不能据混写摘要继续生成')


def resolve_refs(sources: dict, refs: list[SourceRef]) -> list[dict]:
    by_id = {r['id']: r for r in verified_sources(sources)}
    resolved = []
    for ref in refs:
        message = by_id.get(ref.source_id)
        if not message or not ref.quote.strip() or ref.quote not in message['text']:
            raise AgentError('来源引文不存在于对应原始消息')
        resolved.append(message)
    return resolved


def exact_user_refs(sources: dict, text: str) -> list[SourceRef]:
    return [SourceRef(source_id=r['id'], quote=text) for r in verified_sources(sources)
            if r['actor'] == 'user' and text.strip() and text in r['text']][:1]


def bind_constraints(brief, need: Need, sources: dict, scope_locks: dict) -> Need:
    keep = list(dict.fromkeys(brief.must_keep + need.must_keep))
    avoid = list(dict.fromkeys(brief.avoid + need.avoid))
    scope = scope_locks.get(brief.scope)
    if scope and scope not in keep:
        keep.append(scope)
    supplied = {(e.field, e.text): e for e in need.constraint_evidence}
    if len(supplied) != len(need.constraint_evidence):
        raise AgentError('约束来源重复')
    if set(supplied) - {('must_keep', x) for x in keep} - {('avoid', x) for x in avoid}:
        raise AgentError('约束来源引用不存在的约束')
    evidence = []
    for field, values in (('must_keep', keep), ('avoid', avoid)):
        for text in values:
            if not text.strip():
                raise AgentError('约束不能为空')
            row = supplied.get((field, text))
            if field == 'must_keep' and text == scope:
                row = ConstraintEvidence(field=field, text=text, kind='scope_rule',
                                         source_refs=[], rule_id='scope.' + brief.scope)
            elif row is None:
                refs = exact_user_refs(sources, text)
                if not refs:
                    raise AgentError('约束缺少用户来源；自主提案请放assumptions/choices：' + text)
                row = ConstraintEvidence(field=field, text=text, kind='user_explicit', source_refs=refs, rule_id='')
            if row.kind == 'scope_rule':
                if field != 'must_keep' or text != scope or row.rule_id != 'scope.' + brief.scope:
                    raise AgentError('不能用程序范围规则伪装设计提案')
            else:
                messages = resolve_refs(sources, row.source_refs)
                if row.rule_id:
                    raise AgentError('用户约束不能混入程序规则ID')
                if row.kind == 'user_explicit':
                    if not messages or any(m['actor'] != 'user' for m in messages) or not any(
                            text in r.quote for r in row.source_refs):
                        raise AgentError('明确硬约束须直接摘自用户消息；无关引文不能证明约束')
                else:
                    proposals = {m['id'] for m, ref in zip(messages, row.source_refs)
                                 if m['actor'] == 'assistant' and text in ref.quote}
                    if not proposals or not any(m['actor'] == 'user' and m['in_reply_to'] in proposals
                                               and m['in_reply_to'] in m['confirmed_proposal_ids'] for m in messages):
                        raise AgentError('确认提案须同时引用提案与对应用户确认')
            evidence.append(row.model_dump())
    raw = need.model_dump()
    raw.update(must_keep=keep, avoid=avoid, constraint_evidence=evidence)
    return Need.model_validate(raw)


def validate_claims(packet: dict, claims) -> None:
    require_sources(packet)
    for claim in claims:
        if claim.inferred:
            if claim.source_refs:
                resolve_refs(packet['input_sources'], claim.source_refs)
            continue
        messages = resolve_refs(packet['input_sources'], claim.source_refs)
        if not messages or not claim.quote.strip() or not any(
                m['actor'] == 'user' and claim.quote in ref.quote
                for m, ref in zip(messages, claim.source_refs)):
            raise AgentError('显式意图必须引用真实用户消息；Agent提案应标inferred')
        proposals = {m['id'] for m in messages if m['actor'] == 'assistant'}
        for message, ref in zip(messages, claim.source_refs):
            # Only a user quote actually supporting this claim invokes its
            # recorded confirmation link; unrelated context is not authority.
            if (message['actor'] == 'user' and claim.quote in ref.quote
                    and message['in_reply_to'] and message['confirmed_proposal_ids']):
                proposal = message['in_reply_to']
                if proposal not in message['confirmed_proposal_ids'] or proposal not in proposals:
                    raise AgentError('显式确认意图须同时引用对应助手提案与用户确认')


def validate_need(packet: dict) -> None:
    require_sources(packet)
    from .contracts import Brief
    from .workflow import SCOPE_LOCKS
    need = Need.model_validate(packet['need'])
    bound = bind_constraints(Brief.model_validate(packet['brief']), need, packet['input_sources'], SCOPE_LOCKS)
    if bound.model_dump() != need.model_dump():
        raise AgentError('需求约束缺少已绑定的来源，请refresh')
