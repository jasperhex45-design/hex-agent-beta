"""Correct reference responsibilities in the existing task packet.

The reference list and its existing observation/design records remain the sole
authority. Cards and compilation context are projections, never a second store.
"""
from __future__ import annotations

import argparse
from copy import deepcopy
import json
from pathlib import Path

from .io import AgentError, read_json, write_json


ROLE_LABELS = {
    'identity': '身份', 'material': '材质', 'style': '画风',
    'composition': '构图', 'edit_target': '待编辑原图',
    'evaluation_only': '仅对照',
}


def _decisions_enabled(packet: dict) -> bool:
    if 'reference_decisions_version' not in packet:
        return False
    version = packet['reference_decisions_version']
    if type(version) is not int or version != 1:
        raise AgentError('未知参考取舍投影版本，不能降级复用旧编译')
    return True


def validate_context(packet: dict) -> None:
    """Validate opted-in metadata without migrating historical packets."""
    from .host_references import validate_context as validate_host_context
    validate_host_context(packet)
    _decisions_enabled(packet)
    if 'reference_roles_version' not in packet:
        return
    if type(packet['reference_roles_version']) is not int or packet['reference_roles_version'] != 1:
        raise AgentError('未知参考角色版本，不能沿用旧编译')
    from .contracts import Brief
    brief = Brief.model_validate(packet['brief'])
    refs = packet.get('references', [])
    if [ref['id'] for ref in refs] != [ref.id for ref in brief.references]:
        raise AgentError('参考角色图序与当前 brief 不一致')
    for ref, descriptor in zip(refs, brief.references):
        current = descriptor.model_dump()
        if any(ref.get(key, '' if key in ('source_url', 'credit', 'note') else False) != current.get(key)
               for key in ('role', 'note', 'rights', 'source_url', 'credit', 'evaluation_only')):
            raise AgentError('参考角色与当前 brief 不一致：' + ref['id'])
        if current['path'] != ref['file']:
            raise AgentError('参考角色文件绑定与当前 brief 不一致：' + ref['id'])
    analysis = packet.get('reference_analysis')
    if analysis is not None:
        expected = [{'id': ref['id'], 'sha256': ref['sha256']} for ref in refs]
        if analysis.get('input_images') != expected:
            raise AgentError('参考观察的图序或 SHA 已失效，须重新 observe')


def _relations(design, disposition='adopt') -> dict[str, list[dict]]:
    """Project actual decisions; a discarded relation must not read as adopted.

    Historical ReferenceUse records omit disposition and mean adopt. Preserve
    their row shape so ordinary existing compilation contexts stay unchanged.
    """
    if hasattr(design, 'model_dump'):
        design = design.model_dump()
    result = {}
    for choice in (design or {}).get('choices', []):
        for use in choice.get('reference_uses', []):
            if disposition is not None and use.get('disposition', 'adopt') != disposition:
                continue
            row = {'choice_id': choice['id'], 'observed_relation': use['observed_relation']}
            if disposition == 'reject':
                row['reason'] = use.get('reason', '')
            result.setdefault(use['reference_id'], []).append(row)
    return result


def cards(packet: dict, design=None, *, _legacy_decisions=False) -> list[dict]:
    """Bind facts, allowed transfer and actual adoption to current image order."""
    validate_context(packet)
    from .reference_gate import generation_eligible
    observations = packet.get('reference_analysis') or {}
    manifest = observations.get('input_images', [])
    expected = [{'id': ref['id'], 'sha256': ref['sha256']} for ref in packet['references']]
    findings = ({finding['id']: finding for finding in observations.get('findings', [])}
                if manifest == expected else {})
    adoptions = _relations(design, None if _legacy_decisions else 'adopt')
    rejections = {} if _legacy_decisions else _relations(design, 'reject')
    generation_order = {ref['id']: index for index, ref in enumerate(
        (ref for ref in packet['references'] if generation_eligible(packet, ref)), 1)}
    result = []
    for index, ref in enumerate(packet['references'], 1):
        finding = findings.get(ref['id'], {})
        role = 'evaluation_only' if ref.get('evaluation_only') or ref['role'] == 'aesthetic_negative' else ref['role']
        result.append({
            'reference_index': index, 'id': ref['id'], 'file': ref['file'],
            'sha256': ref['sha256'], 'source_sha256': ref.get('original_sha256', ref['sha256']),
            'role': ref['role'], 'display_role': role, 'label': ROLE_LABELS.get(role, role),
            'note': ref.get('note', ''), 'rights': ref['rights'],
            'evaluation_only': bool(ref.get('evaluation_only') or ref['role'] == 'aesthetic_negative'),
            'generation_index': generation_order.get(ref['id']),
            'generation_state': ('eligible_pending_dispatch' if ref['id'] in generation_order
                                 else 'not_submitted'),
            'observed': bool(finding.get('seen')), 'visible_facts': finding.get('visible_facts'),
            'allowed_transfer': finding.get('transfer', []),
            'not_to_inherit': finding.get('do_not_transfer', []),
            'adopted_relations': adoptions.get(ref['id'], []),
            **({'rejected_relations': rejections[ref['id']]} if ref['id'] in rejections else {}),
            'uncertainty': finding.get('uncertainty'),
        })
    return result


def compilation_context(packet: dict, design=None) -> dict | None:
    """Include every current reference, including observation-only images."""
    decisions = _decisions_enabled(packet)
    if not decisions and 'reference_roles_version' not in packet and 'host_visible_references' not in packet:
        return None
    from .host_references import export_context
    # Old compilation digests remain reproducible, including their historical
    # projection defect. New tasks and explicit role edits opt into corrected
    # decisions; merely reading or listing a frozen task must not migrate it.
    host_context = _host_projection(packet, design) if decisions else export_context(packet)
    return {**host_context, 'version': 1,
            'references_in_order': cards(packet, design, _legacy_decisions=not decisions),
            'authority': 'projection_of_packet_references_observation_and_design',
            'notice': '输入顺序与 SHA 仅证明职责绑定；画风不继承身份，身份不默认继承现场布光。'}


def _host_projection(packet: dict, design=None) -> dict:
    from .host_references import export_context, observed_ids
    context = export_context(packet)
    if not context:
        return {}
    observed = observed_ids(packet)
    findings = {row['id']: row for row in (packet.get('reference_analysis') or {}).get('findings', [])}
    adoptions, rejections = _relations(design), _relations(design, 'reject')
    rows = []
    for index, ref in enumerate(packet['host_visible_references'], 1):
        finding = findings.get(ref['id'], {})
        rows.append({**ref, 'host_reference_index': index, 'label': '仅研究（宿主看图声明）',
                     'generation_index': None, 'generation_state': 'not_submitted',
                     'observed': ref['id'] in observed and bool(finding.get('seen')),
                     'visible_facts': finding.get('visible_facts'),
                     'allowed_transfer': finding.get('transfer', []),
                     'not_to_inherit': finding.get('do_not_transfer', []),
                     'adopted_relations': adoptions.get(ref['id'], []),
                     **({'rejected_relations': rejections[ref['id']]} if ref['id'] in rejections else {}),
                     'uncertainty': finding.get('uncertainty')})
    return {**context, 'host_visible_references': rows}


def inspect(run: Path) -> dict:
    from .workflow import read_packet
    packet = read_packet(run)
    design = None
    path = run / 'solution.json'
    if path.exists():
        saved = read_json(path)
        if saved.get('packet_digest') == packet['digest']:
            design = saved.get('design')
    return {'packet_digest': packet['digest'], 'references': cards(packet, design),
            **_host_projection(packet, design),
            'authority': 'current_packet_not_a_separate_reference_store'}


def _require_mutable(run: Path) -> None:
    from .workflow import ensure_references_mutable
    ensure_references_mutable(run)
    if any((run / name).exists() for name in ('acceptance.json', 'prepared-request.json')):
        raise AgentError('任务输入已冻结；参考纠正须用 refresh --out 建立关联版本')


def _current(packet: dict, ident: str, expected_sha256: str | None,
             expected_packet_digest: str | None) -> dict:
    if expected_packet_digest is not None and expected_packet_digest != packet['digest']:
        raise AgentError('参考卡已过期，请重新 list 当前任务')
    ref = next((ref for ref in packet['references'] if ref['id'] == ident), None)
    if ref is None:
        raise AgentError('当前任务没有此参考：' + ident)
    if expected_sha256 is not None and ref['sha256'] != expected_sha256:
        raise AgentError('参考 SHA 与当前图不一致，不能纠正其他图片')
    return ref


def _save_changed(packet: dict, run: Path) -> dict:
    from .workflow import save_packet
    packet['reference_roles_version'] = 1
    packet['reference_decisions_version'] = 1
    # Correcting an explicit responsibility requires real recompilation, even
    # for an unfrozen legacy task or a reference that will never be submitted.
    packet['model_adaptation_version'] = 1
    packet.pop('reference_analysis', None)
    understanding = packet.get('need', {}).get('understanding')
    if understanding is not None:
        # This existing natural-language summary must not keep a removed image
        # alive in parse/design/compile input after the authoritative list changed.
        understanding['reference_roles'] = [
            ref['id'] + ': ' + ('evaluation_only' if ref.get('evaluation_only') else ref['role'])
            + ('；' + ref['note'] if ref.get('note') else '')
            for ref in packet['references'] + packet.get('host_visible_references', [])]
        if packet.get('reference_policy_version') == 1:
            from .reference_gate import build_reference_policy
            packet['reference_policy'] = build_reference_policy(packet)
        if packet.get('intake') is not None:
            packet['intake']['requirement_summary'] = deepcopy(understanding)
    # Options and answers remain source records. The existing co-creation
    # decision digest rejects the former Plan; a missing/changed reference must
    # be re-observed and re-assessed, never silently copied into a new Plan.
    validate_context(packet)
    packet = save_packet(packet, run)
    write_json(run / 'need.json', packet['need'])
    if 'intake' in packet:
        write_json(run / 'intake.json', packet['intake'])
    return packet


def set_role(run: Path, ident: str, role: str, *, note: str | None = None,
             expected_sha256: str | None = None, expected_packet_digest: str | None = None) -> dict:
    if role not in ROLE_LABELS:
        raise AgentError('角色须为 identity/material/style/composition/edit_target/evaluation_only')
    from .workflow import read_packet
    from .run_lock import dispatch_lock
    with dispatch_lock(run):
        _require_mutable(run)
        packet = deepcopy(read_packet(run))
        ref = _current(packet, ident, expected_sha256, expected_packet_digest)
        canonical = 'aesthetic_positive' if role == 'evaluation_only' else role
        ref.update(role=canonical, evaluation_only=role == 'evaluation_only')
        if note is not None:
            ref['note'] = note
        descriptor = next(ref for ref in packet['brief']['references'] if ref['id'] == ident)
        descriptor.update(role=ref['role'], evaluation_only=ref['evaluation_only'], note=ref['note'])
        return _save_changed(packet, run)


def remove(run: Path, ident: str, *, expected_sha256: str | None = None,
           expected_packet_digest: str | None = None,
           expected_metadata_digest: str | None = None) -> dict:
    from .workflow import read_packet
    from .run_lock import dispatch_lock
    with dispatch_lock(run):
        _require_mutable(run)
        packet = deepcopy(read_packet(run))
        if expected_packet_digest is not None and expected_packet_digest != packet['digest']:
            raise AgentError('参考卡已过期，请重新 list 当前任务')
        host = next((ref for ref in packet.get('host_visible_references', []) if ref['id'] == ident), None)
        if host is not None:
            if expected_sha256 is not None:
                raise AgentError('宿主可见参考没有像素 SHA；须核对 metadata_digest')
            if expected_metadata_digest is not None and host['metadata_digest'] != expected_metadata_digest:
                raise AgentError('宿主可见参考元数据摘要已变化；重新 list 当前任务')
            remaining = [ref for ref in packet['host_visible_references'] if ref['id'] != ident]
            if remaining:
                packet['host_visible_references'] = remaining
            else:
                packet.pop('host_visible_references')
        else:
            _current(packet, ident, expected_sha256, expected_packet_digest)
            if expected_metadata_digest is not None:
                raise AgentError('像素参考须核对 SHA，不能使用宿主元数据摘要')
            packet['references'] = [ref for ref in packet['references'] if ref['id'] != ident]
            packet['brief']['references'] = [ref for ref in packet['brief']['references'] if ref['id'] != ident]
        return _save_changed(packet, run)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description='Hex 当前参考职责；不生成、不上传')
    commands = parser.add_subparsers(dest='command', required=True)
    for name in ('list', 'set', 'remove'):
        sub = commands.add_parser(name)
        sub.add_argument('--run', type=Path, required=True)
        if name != 'list':
            sub.add_argument('--reference-id', required=True)
            sub.add_argument('--expected-sha256')
            sub.add_argument('--expected-packet-digest')
        if name == 'set':
            sub.add_argument('--role', choices=list(ROLE_LABELS), required=True)
            sub.add_argument('--note')
        elif name == 'remove':
            sub.add_argument('--expected-metadata-digest')
    args = parser.parse_args(argv)
    try:
        if args.command == 'list':
            result = inspect(args.run)
        else:
            keywords = {'expected_sha256': args.expected_sha256,
                        'expected_packet_digest': args.expected_packet_digest}
            packet = (set_role(args.run, args.reference_id, args.role, note=args.note, **keywords)
                      if args.command == 'set' else remove(args.run, args.reference_id,
                          expected_metadata_digest=args.expected_metadata_digest, **keywords))
            result = {'status': 'reobserve_and_recompile_required', 'packet_digest': packet['digest'],
                      'references': cards(packet), **_host_projection(packet)}
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0
    except (AgentError, ValueError, OSError) as exc:
        print(json.dumps({'error': str(exc)}, ensure_ascii=False))
        return 2


if __name__ == '__main__':
    raise SystemExit(main())
