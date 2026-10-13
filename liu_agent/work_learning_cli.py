"""Small host-facing CLI for private WORK learning; no model or network calls."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

from . import work_learning as wl
from .io import AgentError


def _capture(run, file, owner, mode):
    from . import user_feedback_context as ufc
    from .run_lock import dispatch_lock

    run = run.expanduser().resolve()
    # Serialize with normal run writers: binding validation and registration
    # must see the same original packet, never a freshly selected profile.
    with dispatch_lock(run):
        packet = wl._read(run / 'packet.json')
        if 'digest' in packet:
            wl._verify_seal(packet)
        learning = packet.get('work_learning')
        if not isinstance(learning, dict) or not isinstance(learning.get('binding'), dict):
            raise wl.LearningError('Original run has no private-learning binding; legacy registration is not a retry')
        binding = learning['binding']
        wl._verify_seal(binding)
        if (str(Path(binding['file']).expanduser().resolve()), binding['owner'], binding['mode']) != (str(file), owner, mode):
            raise wl.LearningError('CLI profile/owner/mode must match the original run binding')
        knowledge = Path(learning['workspace_binding']).parent.parent / 'knowledge'
        return ufc.register_run(ufc.config_path(knowledge), run)


def main(argv=None):
    parser = argparse.ArgumentParser(description='HEX private WORK memory / isolated TEST memory')
    parser.add_argument('--profile', type=Path, required=True, help='Private JSON outside the source repository')
    parser.add_argument('--owner', required=True, help='Trusted local profile binding, not a SaaS login')
    parser.add_argument('--mode', choices=sorted(wl.MODES), required=True)
    sub = parser.add_subparsers(dest='command', required=True)
    init = sub.add_parser('init')
    for cmd in (init,):
        cmd.add_argument('--sources', type=Path, required=True)
        cmd.add_argument('--message-id', required=True)
        cmd.add_argument('--quote', required=True)
    init.add_argument('--allow-inferred', action='store_true')
    bind = sub.add_parser('bind')
    bind.add_argument('--knowledge', type=Path, required=True, help='The current runtime knowledge directory')
    bind.add_argument('--project', default='')
    bind.add_argument('--style', default='')
    bind.add_argument('--model', default='')
    bind.add_argument('--target', default='')
    bind.add_argument('--exclude', action='append', default=[])
    unbind = sub.add_parser('unbind')
    unbind.add_argument('--knowledge', type=Path, required=True)
    record = sub.add_parser('record-message')
    record.add_argument('--sources', type=Path, required=True)
    record.add_argument('--message-id', required=True)
    record.add_argument('--quote', required=True)
    record.add_argument('--task-id', required=True)
    record.add_argument('--domain', choices=sorted(wl.DOMAINS), required=True)
    record.add_argument('--project', default='')
    record.add_argument('--style', default='')
    record.add_argument('--task-scope', choices=sorted(wl.SCOPES), default='create')
    capture = sub.add_parser('capture-run')
    capture.add_argument('--run', type=Path, required=True, help='Retry feedback capture using the original run binding')
    sub.add_parser('prepare')
    apply = sub.add_parser('apply')
    apply.add_argument('--delta', type=Path, required=True)
    query = sub.add_parser('context')
    query.add_argument('--domain', choices=sorted(wl.DOMAINS), required=True)
    query.add_argument('--request', default='')
    query.add_argument('--project', default='')
    query.add_argument('--style', default='')
    query.add_argument('--model', default='')
    query.add_argument('--task-scope', choices=sorted(wl.SCOPES), default='create')
    query.add_argument('--exclude', action='append', default=[])
    sub.add_parser('status')
    args = parser.parse_args(argv)
    file = args.profile.expanduser().resolve()
    common = (file, args.owner, args.mode)
    try:
        if args.command == 'init':
            result = wl.create(*common, args.sources, args.message_id, args.quote, allow_inferred=args.allow_inferred)
        elif args.command == 'bind':
            result = wl.bind(args.knowledge, *common, project=args.project, style=args.style,
                             model=args.model, target=args.target, exclude=args.exclude)
        elif args.command == 'unbind':
            # Refuse to unbind a different active owner's workspace by accident.
            active = wl._read(wl.binding_file(args.knowledge))
            if (active['file'], active['owner'], active['mode']) != (str(file), args.owner, args.mode):
                raise wl.LearningError('Active workspace belongs to another profile')
            result = wl.unbind(args.knowledge)
        elif args.command == 'record-message':
            result = wl.record_message(*common, args.sources, args.message_id, args.quote,
                                       task_id=args.task_id, domain=args.domain, project=args.project,
                                       style=args.style, task_scope=args.task_scope)
        elif args.command == 'capture-run':
            result = _capture(args.run, *common)
        elif args.command == 'prepare':
            result = wl.prepare(*common)
        elif args.command == 'apply':
            result = wl.apply(*common, wl._read(args.delta))
        elif args.command == 'context':
            result = wl.context(*common, domain=args.domain, request=args.request, project=args.project,
                                style=args.style, model=args.model, task_scope=args.task_scope, exclude=args.exclude)
        else:
            state = wl._load(*common)
            result = {'status': 'ready', 'owner': args.owner, 'mode': args.mode,
                      'revision': state['revision'], 'active_rules': sum(r['active'] for r in state['rules'].values()),
                      'pending_events': sum(e['state'] == 'pending' for e in state['events'].values()),
                      'allow_inferred': state['allow_inferred'], 'persistent': True,
                      'background_worker': False, 'common_skill_changed': False}
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 2 if result.get('status') == 'learning_deferred' else 0
    except (AgentError, wl.LearningError, OSError, ValueError, KeyError, TypeError) as exc:
        print(json.dumps({'status': 'blocked', 'reason': str(exc), 'private_data_reset': False}, ensure_ascii=False), file=sys.stderr)
        return 2


if __name__ == '__main__':
    raise SystemExit(main())
