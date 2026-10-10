"""Local blind display of two verified candidates; no generator or acceptance gate."""
from __future__ import annotations

import base64
from copy import deepcopy
from html import escape
from pathlib import Path
import secrets

from .io import AgentError, atomic_text, digest, now, read_json, sha, within, write_json

FORMAT = 'hex.blind-comparison.v1'
SOURCE_FORMAT = 'hex.blind-comparison-sources.v1'
CHOICES = ('left', 'right', 'tie', 'neither')


def _seal(body):
    return {**body, 'digest': digest(body)}


def _candidate(run, request_id=None):
    from .feedback import verified_candidate
    from .harness import case_record
    run = Path(run).resolve()
    packet, result, trial = verified_candidate(run, request_id)
    case = case_record(run, trial['request_id'])
    generation = trial.get('generation_evidence')
    if not generation or generation.get('status') != 'verified':
        raise AgentError('比较候选缺少已核验生成规格与实发证据')
    # Read the same original frozen request used by the native evidence checker.
    if case['route'] == 'host':
        from .host import read_dispatches
        row = next(r for r in read_dispatches(run) if r['request']['id'] == trial['request_id'])
        request = row['request']
        arguments = request['arguments']
        spec, capabilities = request.get('generation_spec'), request.get('capability_snapshot')
        prompt = request['prompt']
    else:
        from .api_recovery import load_api_state, _verified
        row = next(r for r in load_api_state(run)['requests'] if r['id'] == trial['request_id'])
        snapshot = _verified(within(run, row['snapshot']))
        spec, capabilities = snapshot['generation'].get('generation_spec'), snapshot['generation'].get('capability_snapshot')
        arguments = row.get('generation_parameters', {}).get('actually_sent')
        prompt = generation['actual_prompt']
    if (not isinstance(spec, dict) or not isinstance(arguments, dict) or not isinstance(capabilities, dict)
            or capabilities.get('source_type') == 'host_not_introspected'):
        raise AgentError('生成器或实发参数未知；不能假定比较条件相同')
    budget = case['budget']
    if (any(type(budget.get(k)) is not int for k in ('max_images', 'attempted_images'))
            or not budget.get('recovery_safe', True)):
        raise AgentError('比较预算缺少可核验的许可或调用计数')
    path_by_input = {str(within(run, r['file'])): {'input_sha256': r['sha256']}
                     for r in generation['ordered_inputs']}

    def controlled(value):
        if isinstance(value, dict):
            return {k: controlled(v) for k, v in value.items() if k not in ('prompt_digest', 'design_digest')}
        if isinstance(value, list):
            return [controlled(v) for v in value]
        if isinstance(value, str):
            if value == prompt:
                return {'independent_prompt_slot': True}
            if value in path_by_input:
                return path_by_input[value]
        return value

    brief = deepcopy(packet['brief'])
    brief.pop('references', None)
    brief.pop('targets', None)
    goal = {k: v for k, v in brief.items() if k != 'settings'}
    materials = [{k: r.get(k) for k in ('id', 'role', 'sha256', 'note', 'evaluation_only')}
                 for r in packet['references']]
    materials.extend({k: r[k] for k in ('id', 'role', 'evidence_type', 'metadata_digest', 'evaluation_only')}
                     for r in packet.get('host_visible_references', []))
    conditions = {
        'generator': {'route': case['route'], 'tool': case['model']['tool'],
                      'provider_id': spec.get('provider_id'), 'model_id': spec.get('model_id'),
                      'capability_snapshot_id': capabilities.get('snapshot_id')},
        'generation_spec': controlled(spec), 'actually_sent': controlled(arguments),
        'ordered_inputs': [{k: r.get(k) for k in ('id', 'role', 'sha256')}
                           for r in generation['ordered_inputs']],
        'settings': brief.get('settings'),
        'budget': {k: budget.get(k) for k in ('max_images', 'attempted_images', 'remaining_images')},
        'attempt_ordinal': case['attempt']['ordinal'],
    }
    return {
        'run': str(run), 'request_id': trial['request_id'], 'candidate_sha256': trial['sha256'],
        'file': trial['file'], 'packet_digest': packet['digest'],
        'contract_digest': result['acceptance']['digest'], 'parent': packet.get('requirement_parent'),
        'case_digest': case['digest'], 'goal': goal, 'materials': materials, 'conditions': conditions,
        'origin': trial['origin'], 'image_bytes': within(run, trial['file']).read_bytes(),
    }


def _stored(row):
    return {k: v for k, v in row.items() if k != 'image_bytes'}


def prepare(native_run: Path, hex_run: Path, out: Path, *, native_request_id=None, hex_request_id=None):
    """Export only when local control evidence agrees; never infer missing fairness."""
    out = Path(out).resolve()
    runs = [Path(native_run).resolve(), Path(hex_run).resolve()]
    if any(out == run or run in out.parents or out in run.parents for run in runs):
        raise AgentError('盲评输出须放在两个来源任务之外')
    if out.exists() and any(out.iterdir()):
        raise AgentError('盲评输出目录非空；已保存左右映射不能重新随机')
    blockers, rows = [], []
    if runs[0] == runs[1]:
        blockers.append('需两个独立候选任务，不能把同一来源当作独立对照')
    for run, request_id in zip(runs, (native_request_id, hex_request_id)):
        try:
            rows.append(_candidate(run, request_id))
        except (AgentError, OSError, ValueError, KeyError) as exc:
            blockers.append(str(exc))
    if len(rows) == 2:
        for key, label in (('goal', '原目标'), ('materials', '参考素材与职责'), ('conditions', '生成器、实发参数或预算')):
            if rows[0][key] != rows[1][key]:
                blockers.append(label + '不同；本对照不满足相同条件')
    out.mkdir(parents=True, exist_ok=True)
    if blockers:
        result = _seal({'format': FORMAT, 'state': 'blocked', 'blockers': blockers,
                        'generation_calls': 0, 'human_acceptance': 'not_recorded'})
        write_json(out / 'manifest.json', result)
        return result
    # Assignment persists once. Public files contain no arm, prompt or run locator.
    arms = [('native', rows[0]), ('hex', rows[1])]
    secrets.SystemRandom().shuffle(arms)
    mapping = _seal({'format': FORMAT, 'created_at': now(),
                     'sides': {side: {'arm': arm, **_stored(row)}
                               for side, (arm, row) in zip(('left', 'right'), arms)}})
    write_json(out / 'private' / 'mapping.json', mapping)
    pictures = []
    for side, (_, row) in zip(('left', 'right'), arms):
        raw = row['image_bytes']
        (out / (side + '.png')).write_bytes(raw)
        pictures.append(f'<figure><figcaption>{"左图" if side == "left" else "右图"}</figcaption>'
                        f'<img alt="{side}" src="data:image/png;base64,{base64.b64encode(raw).decode()}"></figure>')
    constraints = ''.join('<p>' + label + escape('；'.join(rows[0]['goal'].get(key, []))) + '</p>'
                          for key, label in (('must_keep', '必须保留：'), ('avoid', '避免：'))
                          if rows[0]['goal'].get(key))
    html = ('<!doctype html><html lang="zh-CN"><meta charset="utf-8"><title>图片比较</title>'
            '<style>body{margin:32px;font:18px system-ui;background:#f6f4ef;color:#252525}'
            'main{max-width:1400px;margin:auto}.images{display:flex;gap:24px}figure{flex:1;margin:0}'
            'img{display:block;width:100%;height:70vh;object-fit:contain}figcaption{padding:12px 0}'
            '@media(max-width:700px){.images{display:block}img{height:auto}}</style><main><h1>哪张更接近目标？</h1>'
            '<p>' + escape(rows[0]['goal']['request']) + '</p>' + constraints + '<section class="images">'
            + ''.join(pictures) + '</section><p>可回答：左图 / 右图 / 两张一样接近 / 两张都不接近。</p>'
            '<p>比较仅记录当前两张图的相对判断。</p></main></html>')
    atomic_text(out / 'compare.html', html)
    body = {'format': FORMAT, 'state': 'prepared_awaiting_user_comparison',
            'mapping_digest': mapping['digest'], 'display_sha256': sha(html.encode()),
            'images': {side: mapping['sides'][side]['candidate_sha256'] for side in ('left', 'right')},
            'local_conditions': 'verified_equal', 'actual_vendor_model': 'not_available',
            'fairness_scope': 'saved_local_controls_only', 'authoring_isolation': 'not_verified',
            'choices': list(CHOICES), 'human_acceptance': 'not_recorded', 'generation_calls': 0}
    result = _seal(body)
    write_json(out / 'manifest.json', result)
    # Detect source changes during export before making the comparison reviewable.
    check(out)
    return result


def check(out: Path):
    out = Path(out).resolve()
    manifest = read_json(out / 'manifest.json')
    if manifest.get('digest') != digest({k: v for k, v in manifest.items() if k != 'digest'}):
        raise AgentError('盲评清单摘要已变化')
    if manifest['state'] == 'blocked':
        return manifest
    mapping = read_json(out / 'private' / 'mapping.json')
    if (mapping.get('digest') != digest({k: v for k, v in mapping.items() if k != 'digest'})
            or mapping['digest'] != manifest['mapping_digest']):
        raise AgentError('盲评左右映射已变化；旧回答失效')
    if (set(mapping['sides']) != {'left', 'right'}
            or {row.get('arm') for row in mapping['sides'].values()} != {'native', 'hex'}):
        raise AgentError('盲评映射须含两个不同来源及固定左右位置')
    for side, row in mapping['sides'].items():
        current = _stored(_candidate(Path(row['run']), row['request_id']))
        if current != {k: v for k, v in row.items() if k != 'arm'}:
            raise AgentError('盲评来源候选、父版本或执行条件已变化')
        if sha((out / (side + '.png')).read_bytes()) != manifest['images'][side]:
            raise AgentError('盲评展示图片已变化')
    if sha((out / 'compare.html').read_bytes()) != manifest['display_sha256']:
        raise AgentError('盲评展示文件已变化')
    return manifest


def binding(out: Path):
    manifest = check(out)
    if manifest['state'] == 'blocked':
        raise AgentError('比较条件尚未成立，不能登记相对偏好')
    mapping = read_json(Path(out) / 'private' / 'mapping.json')
    return {'mapping_digest': manifest['mapping_digest'],
            'left_sha256': manifest['images']['left'], 'right_sha256': manifest['images']['right'],
            'parent_digest': digest({side: {k: row[k] for k in ('run', 'request_id', 'packet_digest', 'contract_digest', 'parent')}
                                     for side, row in mapping['sides'].items()})}


def record(out: Path, choice: str, quote: str, sources_file: Path, source_id: str):
    """Append a pair-only decision; never write to either original task."""
    from .host import dispatch_lock
    from .provenance import InputSources, verified_sources
    out = Path(out).resolve()
    if choice not in CHOICES or not isinstance(quote, str) or not quote.strip():
        raise AgentError('盲评需要left/right/tie/neither与真实用户原话')
    with dispatch_lock(out):
        pair = binding(out)
        raw = read_json(sources_file)
        if (not isinstance(raw, dict) or set(raw) != {'format', 'sources', 'decisions'}
                or raw['format'] != SOURCE_FORMAT or not isinstance(raw['decisions'], list)
                or len(raw['decisions']) != 1 or not isinstance(raw['decisions'][0], dict)):
            raise AgentError('盲评回答须使用hex.blind-comparison-sources.v1来源声明')
        messages = [m.model_dump() for m in InputSources.model_validate(raw['sources']).messages]
        if len({m['id'] for m in messages}) != len(messages):
            raise AgentError('盲评消息ID重复')
        declarations = [d for d in raw['decisions'] if d.get('source_id') == source_id]
        expected = {'source_id': source_id, 'quote': quote, 'choice': choice, 'scope': 'blind_comparison_pair', **pair}
        message = next((m for m in messages if m['id'] == source_id), None)
        if (len(declarations) != 1 or declarations[0] != expected or not message
                or message['actor'] != 'user' or message['origin'] == 'historical_import'
                or quote not in message['text']):
            raise AgentError('回答缺少用户原话、左右映射、候选SHA或父版本绑定')
        path = out / 'comparison-feedback.json'
        previous = read_json(path) if path.exists() else None
        if previous:
            if (previous.get('format') != 'hex.blind-comparison-feedback.v1'
                    or previous.get('digest') != digest({k: v for k, v in previous.items() if k != 'digest'})
                    or not isinstance(previous.get('decisions'), list)):
                raise AgentError('盲评反馈历史摘要已变化')
            old_messages = verified_sources(previous['sources'])
            history = previous['decisions']
            old_by_id = {m['id']: m for m in old_messages}
            positions = {m['id']: index for index, m in enumerate(old_messages)}
            prior_position = -1
            for declaration in history:
                if not isinstance(declaration, dict):
                    raise AgentError('盲评历史声明结构无效')
                sid = declaration.get('source_id')
                old = old_by_id.get(sid)
                if (not old or old['actor'] != 'user' or old['origin'] == 'historical_import'
                        or declaration.get('choice') not in CHOICES
                        or declaration.get('scope') != 'blind_comparison_pair'
                        or not isinstance(declaration.get('quote'), str) or not declaration['quote'].strip()
                        or declaration['quote'] not in old['text']
                        or any(declaration.get(k) != v for k, v in pair.items())
                        or positions[sid] <= prior_position):
                    raise AgentError('盲评历史来源或比较绑定无效；不能追认整图接受')
                prior_position = positions[sid]
        else:
            old_messages, history = [], []
        by_id = {m['id']: m for m in old_messages}
        if any(d['source_id'] == source_id for d in history):
            raise AgentError('回答已登记；改选须追加新用户消息')
        if source_id in by_id:
            raise AgentError('改选须使用新增用户消息，不能重放旧来源')
        for m in messages:
            if m['id'] in by_id:
                if by_id[m['id']] != m:
                    raise AgentError('用户消息只允许追加，不能重写原文')
                continue
            if m['in_reply_to'] and m['in_reply_to'] not in by_id:
                raise AgentError('盲评回答引用的原消息不存在')
            old_messages.append(m)
            by_id[m['id']] = m
        binding(out)
        if len(old_messages) > 100 or len(history) >= 100:
            raise AgentError('盲评消息已达上限；请保留本比较并新建明确范围')
        snapshot = _seal({'format': 'liu.input-sources.v1', 'messages': old_messages})
        receipt = _seal({'format': 'hex.blind-comparison-feedback.v1', 'sources': snapshot,
                         'decisions': [*history, {**expected, 'recorded_at': now()}],
                         'human_acceptance': 'not_recorded', 'task_acceptance': 'not_recorded',
                         'source_authority': 'host_recorded_not_platform_authenticated'})
        write_json(path, receipt)
        return {'choice': choice, 'scope': 'blind_comparison_pair', **pair,
                'human_acceptance': 'not_recorded', 'task_acceptance': 'not_recorded',
                'generation_calls': 0, 'feedback_digest': receipt['digest']}


def main(argv=None):
    import argparse
    import json
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest='action', required=True)
    prepare_parser = sub.add_parser('prepare')
    prepare_parser.add_argument('--native-run', type=Path, required=True)
    prepare_parser.add_argument('--hex-run', type=Path, required=True)
    prepare_parser.add_argument('--out', type=Path, required=True)
    prepare_parser.add_argument('--native-request-id')
    prepare_parser.add_argument('--hex-request-id')
    inspect = sub.add_parser('check')
    inspect.add_argument('--out', type=Path, required=True)
    feedback = sub.add_parser('record')
    feedback.add_argument('--out', type=Path, required=True)
    feedback.add_argument('--choice', choices=CHOICES, required=True)
    feedback.add_argument('--quote', required=True)
    feedback.add_argument('--sources', type=Path, required=True)
    feedback.add_argument('--source-id', required=True)
    args = parser.parse_args(argv)
    try:
        if args.action == 'prepare':
            result = prepare(args.native_run, args.hex_run, args.out,
                             native_request_id=args.native_request_id, hex_request_id=args.hex_request_id)
        elif args.action == 'check':
            result = check(args.out)
        else:
            result = record(args.out, args.choice, args.quote, args.sources, args.source_id)
        print(json.dumps(result, ensure_ascii=False))
        return 0
    except (AgentError, OSError, ValueError) as exc:
        parser.exit(2, str(exc) + '\n')


if __name__ == '__main__':
    raise SystemExit(main())
