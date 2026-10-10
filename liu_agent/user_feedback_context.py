"""Task-scoped user feedback, separate from unvalidated aesthetic experience.

Registration names existing local evidence; it grants no generation or upload
permission. New tasks freeze relevant records. Historical reads use that
snapshot, while new creative work rechecks withdrawals and source bytes.
"""
from __future__ import annotations

from copy import deepcopy
from pathlib import Path
import re
from typing import Literal

from pydantic import Field, ValidationError, model_validator

from . import calibration
from .contracts import Strict
from .feedback import verified_feedback
from .feedback_recall import _GENERIC_TERMS
from .io import AgentError, digest, read_json, sha, within, write_json
from .retrieval import _phrase_present, tokens


Domain = Literal['person', 'vehicle', 'hybrid', 'illustration', 'poster', 'character', 'prop']
Scope = Literal['create', 'edit', 'color_only', 'relight', 'scene']
_AUTHORITY = 'explicit_user_feedback_advisory'
_GENERIC = _GENERIC_TERMS | set(tokens(
    '好看 高级感 质感 张力 自然 摄影 照片 真实 清晰 效果 这次 测试 人物 人像 时尚 '
    '光线 用光 灯光 机位 构图 噪点 肤质 光影 风格 色彩 色调 细节 画质 对比 层次')) | {
        'photo', 'photography', 'portrait', 'person', 'quality', 'beautiful', 'premium', 'test',
        'light', 'lighting', 'composition', 'noise', 'texture', 'detail', 'color', 'colour',
        'contrast', 'camera', 'angle', 'style', '的', '与', '和', '张', '图',
    }
_GENERIC_CHINESE = re.compile('|'.join(re.escape(term) for term in sorted(_GENERIC, key=len, reverse=True)
                                      if re.fullmatch(r'[\u3400-\u9fff]+', term)))
_ERRORS = (AgentError, OSError, ValueError, KeyError, TypeError)


class FeedbackSource(Strict):
    kind: Literal['pairwise', 'run']
    path: str = Field(min_length=1, max_length=4096)
    record_id: str | None = Field(default=None, max_length=100)
    domains: list[Domain] = Field(default_factory=list, max_length=7)
    task_terms: list[str] = Field(default_factory=list, max_length=8)
    scope: Scope = 'create'

    @model_validator(mode='after')
    def narrow_scope(self):
        if self.kind == 'run' and self.record_id is not None:
            raise ValueError('单图来源绑定原任务，不指定成对记录ID')
        if len(self.domains) != len(set(self.domains)):
            raise ValueError('反馈适用领域不可重复')
        if any(not term.strip() or len(term) > 160 or not _terms(term) for term in self.task_terms):
            raise ValueError('反馈范围词须包含具体题材，不能只有好看或高级感')
        return self


class FeedbackConfig(Strict):
    version: Literal[1] = 1
    enabled: bool = True
    max_records: int = Field(default=3, ge=1, le=6)
    sources: list[FeedbackSource] = Field(default_factory=list, max_length=128)


def _terms(text):
    # Remove Chinese boilerplate before bigrams so e.g. 光线构图 cannot leave
    # the meaningless shared bigram 线构 and open an unrelated subject.
    return set(tokens(_GENERIC_CHINESE.sub(' ', text))) - _GENERIC


def config_path(kb: Path) -> Path:
    return Path(kb).parent / 'local-library' / 'user-feedback-context.json'


def _config(raw, base: Path | None = None) -> FeedbackConfig:
    try:
        config = FeedbackConfig.model_validate(raw)
    except ValidationError as exc:
        raise AgentError('用户反馈来源配置无效：' + str(exc)) from exc
    if base is not None:
        for source in config.sources:
            source.path = str((base / Path(source.path).expanduser()).resolve())
    return config


def initialize(packet: dict, kb: Path, *, enabled: bool | None = None) -> None:
    """Creation-only opt-in from the project's explicitly registered sources."""
    if enabled is not None and type(enabled) is not bool:
        raise AgentError('用户反馈开关须为布尔值')
    if 'user_feedback_context_version' in packet or enabled is False:
        return
    file = config_path(kb)
    if not file.is_file():
        return
    config = _config(read_json(file), file.parent)
    if enabled is not None:
        config.enabled = enabled
    packet['user_feedback_context_version'] = 1
    packet['user_feedback_config'] = config.model_dump()


def _version(packet):
    version = packet.get('user_feedback_context_version')
    if 'user_feedback_context_version' not in packet:
        if 'user_feedback_config' in packet or 'user_feedback_context' in packet:
            raise AgentError('用户反馈上下文缺少版本，不能注入旧任务')
        return False
    if type(version) is not int or version != 1:
        raise AgentError('未知用户反馈上下文版本')
    return True


def _source_records(source: FeedbackSource):
    path = Path(source.path)
    if source.kind == 'pairwise':
        if not path.is_file():
            raise AgentError('用户成对反馈账本不存在')
        for item in calibration.active_records(path):
            if source.record_id is not None and item.id != source.record_id:
                continue
            raw = item.model_dump()
            record = {key: value for key, value in raw.items() if key not in ('method_ids', 'grammar_ids')}
            yield record, digest(raw), item.criterion + ' ' + item.user_quote
    else:
        yield from _run_records(path)


def _run_records(path):
    from .candidate_feedback import verified_candidate_feedback
    from .feedback import verified_candidate, verified_relative_preference
    from .feedback_sources import EVIDENCE

    def base(packet, kind, evidence, quote, source, recorded_at, images):
        need = packet['need']
        return {'id': kind + '.' + digest(evidence)[:20], 'feedback_kind': kind,
                'domain': need['domain'], 'task_id': str(path), 'scope': packet['brief']['scope'],
                'intent': need['intent'], 'user_quote': quote, 'source': source,
                'recorded_at': recorded_at, 'images': images}

    def image(trial, label):
        return {'label': label, 'path': str(within(path, trial['file'])), 'sha256': trial['sha256']}

    found = False
    if (path / 'user-feedback.json').is_file():
        packet, trial, value = verified_feedback(path)
        row = base(packet, 'single_image', value, value['user_quote'], value['source'], value['recorded_at'],
                   [image(trial, 'candidate')])
        row.update(decision=value['decision'], source_evidence=value.get('source_evidence', 'host_reported'),
                   packet_digest=value['packet_digest'], contract_digest=value['contract_digest'])
        found = True
        yield row, digest(value), packet['need']['intent'] + ' ' + packet['need'].get('subject', '')
    for kind, verifier in (('positive_feedback', verified_candidate_feedback),
                           ('relative_preference', verified_relative_preference)):
        receipt = verifier(path)
        if receipt is None:
            continue
        binding = receipt['binding']
        if kind == 'positive_feedback':
            packet, _, trial = verified_candidate(path)
            images = [image(trial, 'candidate')]
        else:
            packet, _, first = verified_candidate(path, binding['preferred_request_id'], comparison_member=True)
            _, _, second = verified_candidate(path, binding['compared_request_id'], comparison_member=True)
            images = [image(first, 'preferred'), image(second, 'compared')]
        messages = {row['id']: row for row in receipt['sources']['messages']}
        # A reversed pair choice supersedes its predecessor. Positive comments
        # are additive and keep their own event/image bindings.
        events = receipt['messages'][-1:] if kind == 'relative_preference' else receipt['messages']
        for event in events:
            evidence = {'binding': binding, 'event': event, 'message': messages[event['source_id']]}
            row = base(packet, kind, evidence, event['quote'], event['source'], event['recorded_at'], images)
            row.update(decision=kind, source_evidence=EVIDENCE, binding=binding, source_id=event['source_id'])
            found = True
            yield row, digest(evidence), packet['need']['intent'] + ' ' + packet['need'].get('subject', '')
    if not found:
        raise AgentError('任务没有可核验的用户反馈，不从机器评审推断')


def _verify_images(record):
    for item in record['images']:
        file = Path(item['path'])
        if not file.is_file() or sha(file.read_bytes()) != item['sha256']:
            raise AgentError('用户反馈绑定图片丢失或已改变')


def _affirmative(phrase, text):
    # Full configured topic phrases establish scope; bigrams only rank matches.
    phrase = phrase.casefold().strip()
    pattern = re.escape(phrase)
    if re.search(r'[a-z0-9]', phrase):
        pattern = r'(?<![a-z0-9])' + pattern + r'(?![a-z0-9])'
    for clause in re.split(r'[，,。.;；!?！？\n]', text.casefold()):
        for match in re.finditer(pattern, clause):
            # Negating a camera choice earlier in the clause does not negate
            # its subject: 不要平视的人车时尚 still requests that compound topic.
            prefix = clause[:match.start()]
            if not re.search(
                    r'(?:不要|不是|不拍|不做|不需要|不想要|无需|排除|避免|拒绝|并非|没有|别(?:做|拍)?'
                    r'|不再(?:做|拍)?)(?:\s*(?:任何|这种|这类|相关|的|再))*\s*$'
                    r'|\b(?:no|not|without|avoid)\s+(?:(?:a|an|any|the|this)\s+)?$', prefix):
                return True
    return False


def _applicable(packet, source, record, evidence):
    need = packet['need']
    scope = packet.get('brief', {}).get('scope', 'create')
    linked = (source.kind == 'run' and
              packet.get('requirement_parent', {}).get('run') == source.path)
    if scope != source.scope and not linked:
        return []
    domain = need['domain']
    if source.domains and domain not in source.domains:
        return []
    task = need['intent'] + ' ' + need.get('subject', '')
    if source.task_terms and not all(
            _affirmative(term, task) and _phrase_present(term, evidence) for term in source.task_terms):
        return []
    if record['domain'] != domain and not (
            record['domain'] == 'hybrid' and domain in {'person', 'vehicle'}
            and {'hybrid', domain}.issubset(source.domains) and source.task_terms):
        return []
    # A comparison's off-topic criticism does not define its subject. Only an
    # explicitly registered phrase may use the quote to narrow applicability.
    topic = record['criterion'] if source.kind == 'pairwise' and not source.task_terms else evidence
    matched = sorted(_terms(task) & _terms(topic))
    return matched if matched else []


def refresh(packet: dict) -> None:
    """Refresh selected evidence only on the caller's normal mutable-task path."""
    if not _version(packet):
        return
    config = _config(packet.get('user_feedback_config'))
    rows, skipped, seen = [], [], set()
    if config.enabled:
        for source in config.sources:
            try:
                for record, evidence_digest, evidence in _source_records(source):
                    matched = _applicable(packet, source, record, evidence)
                    kind = record.get('feedback_kind', 'pairwise')
                    key = (kind, evidence_digest)
                    if not matched or key in seen:
                        continue
                    try:
                        _verify_images(record)
                    except _ERRORS as exc:
                        skipped.append({'source': source.path, 'record_id': record['id'],
                                        'reason': 'feedback_evidence_invalid', 'detail': str(exc)[:240]})
                        continue
                    seen.add(key)
                    rows.append({'kind': kind, 'record': record, 'source': source.model_dump(),
                                 'evidence_digest': evidence_digest, 'matched_terms': matched,
                                 'scope_note': '只证明绑定图像与题材中的用户反馈；本次要求优先，不升级为永久偏好。'})
            except _ERRORS as exc:
                skipped.append({'source': source.path, 'reason': 'feedback_evidence_invalid', 'detail': str(exc)[:240]})
    rows.sort(key=lambda row: (row['record']['recorded_at'], len(row['matched_terms'])), reverse=True)
    context = {'version': 1, 'authority': _AUTHORITY, 'need_digest': digest(packet['need']),
               'config_digest': digest(config.model_dump()), 'records': rows[:config.max_records],
               'skipped': skipped, 'images_submitted': False, 'generation_authorized': False,
               'status': ('disabled' if not config.enabled else 'recalled' if rows else 'no_relevant_feedback')}
    context['digest'] = digest(context)
    packet['user_feedback_context'] = context


def frozen_context(packet: dict) -> dict:
    """Read historical facts without touching today's files or withdrawals."""
    if not _version(packet):
        return {}
    config = _config(packet.get('user_feedback_config'))
    value = packet.get('user_feedback_context')
    if (not isinstance(value, dict) or type(value.get('version')) is not int or value['version'] != 1
            or value.get('authority') != _AUTHORITY or value.get('images_submitted') is not False
            or value.get('generation_authorized') is not False
            or value.get('need_digest') != digest(packet['need'])
            or value.get('config_digest') != digest(config.model_dump())
            or value.get('digest') != digest({k: v for k, v in value.items() if k != 'digest'})):
        raise AgentError('用户反馈快照与当前需求或来源不一致；先按当前任务状态刷新')
    rows = value.get('records')
    if not isinstance(rows, list) or len(rows) > config.max_records or (not config.enabled and rows):
        raise AgentError('用户反馈快照记录数量或开关无效')
    approved = [item.model_dump() for item in config.sources]
    seen = set()
    for row in rows:
        if not isinstance(row, dict) or row.get('source') not in approved:
            raise AgentError('用户反馈快照引用未登记来源')
        record = row.get('record', {})
        expected_source = 'pairwise' if row.get('kind') == 'pairwise' else 'run'
        if (row.get('kind') not in ('pairwise', 'single_image', 'positive_feedback', 'relative_preference')
                or row['source']['kind'] != expected_source or not isinstance(record, dict)
                or not isinstance(record.get('user_quote'), str) or not record['user_quote'].strip()
                or not isinstance(row.get('evidence_digest'), str)
                or not re.fullmatch(r'[0-9a-f]{64}', row['evidence_digest'])):
            raise AgentError('用户反馈快照缺少真实原话或绑定摘要')
        key = (row['kind'], row['evidence_digest'])
        if key in seen:
            raise AgentError('用户反馈快照含重复记录')
        seen.add(key)
    return deepcopy(value)


def require_current(packet: dict, *, stage: str = 'dispatch') -> dict:
    """Before NEW work only: reject stale evidence without rewriting history."""
    context = frozen_context(packet)
    for row in context.get('records', []):
        try:
            source = FeedbackSource.model_validate(row['source'])
            current = next((item for item in _source_records(source)
                            if item[0]['id'] == row['record']['id']), None)
            if current is None or current[1] != row['evidence_digest'] or current[0] != row['record']:
                raise AgentError('记录已撤回、修改或无法核验')
            _verify_images(current[0])
        except _ERRORS as exc:
            raise AgentError(f'用户反馈依据已变化 [{stage}]：{exc}；先刷新未冻结任务或建立关联版本，不能沿用旧编译。') from exc
    return context


def _register(file: Path, source: FeedbackSource) -> dict:
    file = Path(file).resolve()
    config = _config(read_json(file), file.parent) if file.is_file() else FeedbackConfig()
    if source.model_dump() not in [item.model_dump() for item in config.sources]:
        config = _config({**config.model_dump(), 'sources': [*(s.model_dump() for s in config.sources), source.model_dump()]})
        write_json(file, config.model_dump())
    return {'status': 'registered', 'config': str(file), 'source': source.model_dump(),
            'enabled': config.enabled, 'images_submitted': False, 'generation_authorized': False}


def register_pair(config_file: Path, ledger: Path, record_id: str | None = None, *,
                  domains: list[str] | None = None, task_terms: list[str] | None = None,
                  scope: str = 'create') -> dict:
    source = FeedbackSource(kind='pairwise', path=str(Path(ledger).expanduser().resolve()), record_id=record_id,
                            domains=domains or [], task_terms=task_terms or [], scope=scope)
    records = list(_source_records(source))
    if not records:
        raise AgentError('没有可登记的有效成对反馈记录；不把已撤回记录重新启用')
    # The existing ledger has a hybrid domain while Brief/Need do not. Infer
    # only this explicitly named compound subject, never an aesthetic taste or
    # an unrestricted hybrid -> every-person-task mapping. Other mixed subjects
    # retain strict scope until their real task context is supplied.
    if domains is None and task_terms is None and len(records) == 1 and records[0][0]['domain'] == 'hybrid':
        topic = next((term for term in ('人车', '人物与汽车', 'person and car')
                      if _affirmative(term, records[0][2])), None)
        if topic:
            source.domains = ['hybrid', 'person', 'vehicle']
            source.task_terms = [topic]
    for record, _, evidence in records:
        _verify_images(record)
        if source.task_terms and not all(_phrase_present(term, evidence) for term in source.task_terms):
            raise AgentError('反馈登记范围词不属于原始反馈；请缩小到实际记录')
    return _register(config_file, source)


def register_run(config_file: Path, run: Path, *, task_terms: list[str] | None = None) -> dict:
    path = Path(run).expanduser().resolve()
    records = list(_run_records(path))
    first = records[0][0]
    source = FeedbackSource(kind='run', path=str(path), domains=[first['domain']],
                            scope=first['scope'], task_terms=task_terms or [])
    for record, _, evidence in records:
        _verify_images(record)
        if source.task_terms and not all(_phrase_present(term, evidence) for term in source.task_terms):
            raise AgentError('反馈登记范围词不属于原始任务')
    return _register(config_file, source)
