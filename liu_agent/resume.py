"""Advance verified local evidence without issuing generation requests."""
from __future__ import annotations

from pathlib import Path

from . import host
from .contracts import PairComparison
from .io import AgentError, digest, read_json, within, write_json
from .run_status import _packet_grant, inspect_run
from .workflow import read_packet


def _save_if_changed(path: Path, value) -> None:
    if path.exists():
        try:
            if read_json(path) == value:
                return
        except (OSError, ValueError):
            pass
    write_json(path, value)


def resume_run(run: Path, *, provider=None, _before_local_action=None) -> dict:
    """Prepare the next local action, leaving actual visual judgments to the host.

    Default local/host recovery issues no model request. Explicitly authorized
    API recovery may fill missing text stages while preserving original limits.
    Neither route generates new images, resets budgets, clears another worker's
    lock, marks unknown results as failed, or invents user preferences.
    """
    run = Path(run).resolve()
    if (run/'api-state.json').exists():
        from .api_recovery import resume_api_run
        return resume_api_run(run, provider=provider, _before_local_action=_before_local_action)
    report = inspect_run(run)
    response = {'format': 'liu.aesthetic.resume.v1', 'run': str(run),
                'action': 'waiting', 'status': report, 'artifacts': {},
                'requests_submitted': 0, 'visual_validation': 'not_performed_by_resume'}
    if report['stage'] == 'exported' and report['next_action'].get('action') == 'reassessment_required':
        response.update(action='reassessment_required', artifacts=report['next_action']['inputs'])
        return response
    if (_before_local_action is None and not report['blockers'] and report['stage'] in ('exported', 'needs_authorization', 'ready_for_dispatch')
            and report.get('generation_status') == 'not_attempted' and not report['candidates']):
        from .generation_authorization import read_authorization, record_authorization
        from .native_budget import has_transfer, transfer_budget
        packet = read_packet(run)
        grant = _packet_grant(packet)
        if grant and not has_transfer(run):
            authorization = (read_authorization(run, packet) if (run/'generation-authorization.json').exists()
                             else None)
            if authorization is None or (grant['source_id'] not in
                    {row['source_id'] for row in authorization['history']}
                    and grant['max_images'] > authorization['max_images']):
                # record_authorization owns this run's lock and rechecks its packet.
                # No dispatch can spend an unrecorded grant in the interim.
                record_authorization(run, grant['source_id'])
                response['action'] = 'authorization_recorded'
                report = inspect_run(run)
                response['status'] = report
                if report['blockers']:
                    return response
        link = packet.get('requirement_parent')
        if link and not has_transfer(run):
            parent = Path(link['run'])
            try:
                if (not parent.is_absolute() or parent != parent.resolve()
                        or read_packet(parent)['digest'] != link['packet_digest']):
                    raise AgentError('关联父版本路径或packet摘要不一致')
                child_auth = read_authorization(run, packet)
                parent_auth = read_authorization(parent, read_packet(parent))
                source_id = child_auth['history'][-1]['source_id']
                if parent_auth['history'][-1]['source_id'] != source_id:
                    raise AgentError('父子尚未登记同一当前新增许可')
                transfer_budget(parent, run, source_id)
                response['action'] = 'budget_transferred'
                response['status'] = inspect_run(run)
                return response
            except (AgentError, OSError, ValueError, KeyError, TypeError) as exc:
                # A competing resume may have completed the same transfer.
                report = inspect_run(run)
                response['status'] = report
                if not (report['generation_allowed']
                        and report['budget'].get('transfer_role') == 'child'):
                    response['recovery_blocker'] = str(exc)
                    return response
        if response['status']['generation_allowed']:
            response['action'] = ('authorization_recorded' if response['action'] == 'authorization_recorded'
                                  else 'ready_for_dispatch')
        return response
    actionable = ('needs_preflight', 'needs_eval', 'needs_compare',
                  'ready_to_export', 'generation_failed')
    if report['stage'] not in actionable:
        return response
    with host.dispatch_lock(run):
        if _before_local_action is not None:
            _before_local_action()
        # Recheck inside the same lock used for generation receipts/reservations.
        report = inspect_run(run, _dispatch_lock_held=True)
        response['status'] = report
        stage = report['stage']
        if stage == 'needs_preflight':
            host._preflight_locked(run, run/'solution.json')
            response['action'] = 'preflight_prepared'
        elif stage == 'needs_eval':
            candidate = next(c for c in report['candidates'] if not c['evaluated'])
            attempt = next(a for a in host.read_dispatches(run)
                           if a['request']['id'] == candidate['request_id'])
            # Restore the validated generation snapshot, never a fresh design.
            _save_if_changed(run/'solution.json', attempt['request']['solution'])
            prepared = host.prepare_eval(run, within(run, candidate['file']))
            response.update(action='eval_prepared', artifacts=prepared)
        elif stage == 'needs_compare':
            packet = read_packet(run)
            trials = read_json(run/'host-image-tests.json')
            comparison = {
                'format': 'liu.aesthetic.compare-input.v1',
                'packet_digest': packet['digest'],
                'brief': packet['brief'], 'need': packet['need'],
                'contract': read_json(run/'acceptance.json'),
                'references': packet['references'],
                'candidates': [{'label': label, 'file': trial['file'],
                                'sha256': trial['sha256'], 'review': trial['review'],
                                'solution': trial['solution']}
                               for label, trial in zip(('A', 'B'), trials)],
                'next': '实际看原始参考与两张候选，按固定标准记录收益和损失，再用 choose 登记；不自动选最后一张。',
            }
            from .host_references import review_context
            comparison.update(review_context(packet))
            comparison['digest'] = digest(comparison)
            _save_if_changed(run/'compare-input.json', comparison)
            _save_if_changed(run/'comparison.schema.json', PairComparison.model_json_schema())
            response.update(action='comparison_prepared', artifacts={
                'compare_input': str(run/'compare-input.json'),
                'comparison_schema': str(run/'comparison.schema.json')})
        elif stage in ('ready_to_export', 'generation_failed'):
            if report['next_action'].get('render_only'):
                from .exporter import export_result
                from .feedback import apply_feedback, apply_relative_preference
                result = read_json(run/'result.json')
                apply_feedback(run, result)
                apply_relative_preference(run, result)
                export_result(result, run)
            else:
                result = host.finalize(run, run/'solution.json')
            response.update(action='exported', artifacts={
                'result': str(run/'result.json'), 'html': str(run/'result.html'),
                'markdown': str(run/'result.md'), 'quality_status': result['status']})
        response['status'] = inspect_run(run, _dispatch_lock_held=True)
    return response
