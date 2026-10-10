"""Conversation-visible research evidence, never a substitute for image bytes.

The host attests actual visual access. Digests bind its metadata and user source,
not pixels or truth of the observation. No automatic promotion to image input.
"""
from .contracts import HostVisibleReference
from .io import AgentError, digest
from .provenance import verified_sources


def make_record(packet, descriptor):
    ref = HostVisibleReference.model_validate(descriptor).model_dump()
    if ref['id'] in ('CANDIDATE', 'PREVIOUS_CANDIDATE'):
        raise AgentError('宿主可见参考不能使用候选或生成输入的保留 ID')
    sources = {row['id']: row for row in verified_sources(packet.get('input_sources'))}
    source = sources.get(ref['source_id'])
    if not source or source['actor'] != 'user':
        raise AgentError('宿主可见参考必须绑定当前真实用户来源，不能用宿主描述冒充用户消息')
    for key in ('locator', 'observation_limits', 'note'):
        if not ref[key].strip():
            raise AgentError('宿主可见参考须写明会话位置、观察限制及研究职责')
    body = {**ref, 'source_message_digest': digest(source)}
    return {**body, 'metadata_digest': digest(body)}


def validate_context(packet):
    if 'host_visible_references' not in packet:
        if (packet.get('reference_analysis') or {}).get('input_host_visible_references'):
            raise AgentError('宿主可见参考已移除；观察记录失效')
        return
    rows = packet['host_visible_references']
    if not isinstance(rows, list) or not 1 <= len(rows) <= 8:
        raise AgentError('宿主可见参考须为一至八条独立研究证据')
    ids = {r['id'] for r in packet.get('references', [])}
    for row in rows:
        if not isinstance(row, dict):
            raise AgentError('宿主可见参考记录无效')
        try:
            expected = make_record(packet, {k: v for k, v in row.items()
                                          if k not in ('metadata_digest', 'source_message_digest')})
        except ValueError as exc:
            raise AgentError('宿主可见参考元数据无效；不得混入文件或像素 SHA') from exc
        if row != expected:
            raise AgentError('宿主可见参考元数据或用户来源摘要已改变')
        if row['id'] in ids:
            raise AgentError('参考 ID 重复，宿主可见证据不能覆盖像素参考')
        ids.add(row['id'])
    analysis = packet.get('reference_analysis')
    if analysis is not None and analysis.get('input_host_visible_references') != manifest(packet):
        raise AgentError('宿主可见参考观察已失效，须重新 observe')


def manifest(packet):
    return [{'id': r['id'], 'metadata_digest': r['metadata_digest']}
            for r in packet.get('host_visible_references', [])]


def research_references(packet):
    validate_context(packet)
    return packet.get('references', []) + packet.get('host_visible_references', [])


def observed_ids(packet):
    validate_context(packet)
    analysis = packet.get('reference_analysis') or {}
    pixels = {r['id']: r['sha256'] for r in analysis.get('input_images', [])}
    metadata = {r['id']: r['metadata_digest']
                for r in analysis.get('input_host_visible_references', [])}
    return ({r['id'] for r in packet.get('references', []) if pixels.get(r['id']) == r['sha256']}
            | {r['id'] for r in packet.get('host_visible_references', [])
               if metadata.get(r['id']) == r['metadata_digest']})


def export_context(packet):
    """Kept outside images_in_order; reviewers must not infer pixel access."""
    validate_context(packet)
    if 'host_visible_references' not in packet:
        return {}
    return {'host_visible_references': packet['host_visible_references'],
            'host_visible_reference_notice':
            '宿主见过会话图片的声明，仅供关系研究；元数据摘要不是像素 SHA。'
            '接收者未获得图片字节，不能据此声称独立看过原图或核验参考相似度。'}


def review_context(packet):
    context = export_context(packet)
    if not context:
        return {}
    fields = ('id', 'source_id', 'locator', 'evidence_type', 'observation_method',
              'observation_limits', 'role', 'evaluation_only', 'metadata_digest', 'source_message_digest')
    return {**context, 'host_visible_references': [
        {key: row[key] for key in fields} for row in context['host_visible_references']]}
