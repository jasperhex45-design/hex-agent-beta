"""Keep clarification history; source binding does not prove answer semantics."""
from __future__ import annotations

from .contracts import Need, QuestionRecord
from .io import AgentError, sha
from .provenance import resolve_refs, verified_sources


def _legacy_question(text: str) -> QuestionRecord:
    if not text.strip():
        raise AgentError('澄清问题不能为空')
    return QuestionRecord(
        id='q-' + sha(text.encode('utf-8'))[:16], question=text,
        affected_decision=text, decision_type='scope', status='open',
        reason='历史字符串问题未记录分类；保守保留为待确认项', source_refs=[],
    )


def _validate_record(record: QuestionRecord, sources: dict) -> None:
    if any(not value.strip() for value in (record.id, record.question, record.affected_decision)):
        raise AgentError('澄清记录须有ID、问题原文与受影响决定')
    messages = resolve_refs(sources, record.source_refs) if record.source_refs else []
    if record.status == 'answered':
        if not messages or any(message['actor'] != 'user' for message in messages):
            raise AgentError('已回答问题必须绑定当前任务的用户消息引文')
    elif record.status == 'withdrawn':
        if not record.reason.strip():
            raise AgentError('撤回澄清问题必须说明理由')
        if record.decision_type not in ('technical', 'expression') and (
                not messages or any(message['actor'] != 'user' for message in messages)):
            raise AgentError('身份、文字、范围或参考用途问题的撤回必须有用户消息依据')


def _indexed(records: list[QuestionRecord], sources: dict) -> dict[str, QuestionRecord]:
    by_id = {}
    questions = set()
    for record in records:
        if record.id in by_id or record.question in questions:
            raise AgentError('澄清记录ID或问题原文重复')
        _validate_record(record, sources)
        by_id[record.id] = record
        questions.add(record.question)
    return by_id


def reconcile_questions(previous_need: dict | None, need: Need, sources: dict, *, selective: bool = False) -> Need:
    """Merge recorded decisions without letting omitted questions erase blockers.

    Existing user messages may resolve an overlooked question; no new message ID
    is required. Quote validation proves who supplied text, not that the text
    correctly answers the question. That judgment remains with the host.
    """
    verified_sources(sources)
    previous_need = previous_need or {}
    previous = [QuestionRecord.model_validate(row) for row in previous_need.get('question_records', [])]
    known = _indexed(previous, sources)
    question_ids = {row.question: row.id for row in previous}
    for text in previous_need.get('questions', []):
        if text not in question_ids:
            record = _legacy_question(text)
            if record.id in known:
                raise AgentError('澄清问题ID冲突')
            known[record.id] = record
            question_ids[text] = record.id

    incoming = _indexed(need.question_records, sources)
    for ident, record in incoming.items():
        from .co_creation import validate_question
        validate_question(record, sources, selective=selective)
        old = known.get(ident)
        if old is None:
            if record.status != 'open':
                raise AgentError('未知澄清问题不能直接登记为已解决')
            if record.question in question_ids:
                raise AgentError('同一澄清问题不能换ID重提或替换历史')
        else:
            if old.options and (old.options != record.options or old.recommended_id != record.recommended_id):
                raise AgentError('已展示选项内容不可改写；用新问题与用户来源替代')
            if any(getattr(old, field) != getattr(record, field) for field in
                   ('question', 'affected_decision', 'decision_type')):
                raise AgentError('已有澄清问题的原文、影响决定和类型不能改写')
            if old.status != 'open' and old.model_dump() != record.model_dump():
                raise AgentError('已解决澄清记录不可改写或重新打开；新问题须另行记录')
        known[ident] = record
        question_ids[record.question] = ident

    for text in need.questions:
        if text not in question_ids:
            record = _legacy_question(text)
            if record.id in known:
                raise AgentError('澄清问题ID冲突')
            known[record.id] = record
            question_ids[text] = record.id

    rows = list(known.values())
    for row in rows:
        from .co_creation import validate_question
        validate_question(row, sources, selective=selective)
    raw = need.model_dump()
    raw.update(question_records=[row.model_dump() for row in rows],
               questions=[row.question for row in rows if row.status == 'open'])
    return Need.model_validate(raw)
