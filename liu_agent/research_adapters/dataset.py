"""Derived evaluation views and ancestry-safe train/validation/holdout splits."""
from __future__ import annotations

from copy import deepcopy
from pathlib import Path

from liu_agent import brief_registry, harness
from liu_agent.io import AgentError, digest, read_json, sha, within
from liu_agent.workflow import read_packet

SPLITS = ('train', 'validation', 'holdout')
METRICS = ('constraint_loss', 'invented_constraints', 'unnecessary_questions',
           'reference_role_misuse', 'design_goal_mismatch', 'compilation_drift')

# Specifications, not ready briefs, actual user requests, or image authorizations.
_TASKS = [
    ('C1', 'automotive', '局部改漆保细节', '银色旅行车', 'edit', 'edit_target', '摄影', '锁定原机位', '保留原光线', '漆面反射与蒙版外细节'),
    ('C2', 'automotive', '开放创意 KV', '琥珀色双门概念车', 'create', 'none', '广告KV', '单一视觉重心', '自主设计', '表达强度与车身可信'),
    ('C3', 'automotive', '真实街景融合', '白色城市厢式车', 'composite', 'identity+scene', '纪实摄影', '街道路面关系', '匹配现场', '接地阴影与尺度'),
    ('C4', 'automotive', '车与人物的复合海报', '蓝色敞篷车与成年舞者', 'create', 'identity', '人物汽车海报', '人物与车共同呈现', '方向一致', '人车主次与解剖'),
    ('P1', 'portrait', '保持身份换场景', '成年陶艺师', 'edit', 'identity', '环境肖像', '保留面部结构', '适应新场景', '身份与环境关系'),
    ('P2', 'portrait', '更换服装造型', '成年男性乐手', 'edit', 'identity+wardrobe', '时尚摄影', '保留原动作', '保留原光线', '服饰与身体关系'),
    ('P3', 'portrait', '局部妆容或肤质修改', '成年短发女性', 'local_edit', 'edit_target', '近景人像', '锁定原机位', '保留原光线', '妆容边界与皮肤纹理'),
    ('P4', 'portrait', '多人关系与不同状态', '三名成年徒步者', 'create', 'none', '群像摄影', '三人关系', '阴天自然光', '人数动作与各自状态'),
    ('G1', 'graphic', '精确名称日期数字', '北岸读书会', 'create', 'none', '活动海报', '标题日期数字层级', '不适用', '精确文案不得增删'),
    ('G2', 'graphic', '多层信息版式', '独立戏剧节', 'create', 'none', '节目折页', '分组与阅读顺序', '不适用', '信息完整与可读'),
    ('G3', 'graphic', '品牌色与必要元素', '苔青茶品牌', 'create', 'brand', '包装正面', '商标和品名', '不适用', '品牌色与必要元素'),
    ('G4', 'graphic', '跨媒介参考转译', '潮汐音乐专辑', 'create', 'style', '唱片封面', '绘画转平面', '参考的明暗组织', '只迁移指定风格职责'),
    ('S1', 'storyboard', '单镜头转折事件', '车站发现遗失行李的旅客', 'create', 'none', '电影关键帧', '转折瞬间', '站台灯光', '一张图可见转折'),
    ('S2', 'storyboard', '景深遮挡与视线空间', '隔窗观察街道的侦探', 'create', 'none', '电影关键帧', '前中后景及视线', '内外光差', '遮挡不破坏动作可读'),
    ('S3', 'storyboard', '依据既有上一镜设计下一镜', '灯塔守望者', 'continuation', 'previous_shot', '电影关键帧', '延续空间与视线', '延续既有光线', '连续性而非复制画面'),
    ('S4', 'storyboard', '单个画布中的分镜页表达动作因果', '快递员追赶飘走的纸条', 'create', 'none', '单张分镜页', '格间因果', '统一环境光', '动作顺序和同一主体'),
]


def task_matrix() -> dict:
    rows = []
    for index, values in enumerate(_TASKS):
        ident, domain, request_type, subject, scope, refs, medium, composition, light, challenge = values
        rows.append({'case_id': ident, 'domain': domain, 'request_type': request_type,
                     'subject_specification': subject, 'scope': scope, 'reference_conditions': refs,
                     'medium': medium, 'composition': composition, 'lighting': light,
                     'challenge': challenge, 'split': ('train', 'train', 'validation', 'holdout')[index % 4],
                     'source_family': 'planned-independent-' + ident,
                     'source_independence': 'not_verified_until_assets_bound',
                     'arms': {'A': 'not_run', 'B': 'not_run'}, 'planned_images': 2})
    return {'format': 'hex.offline-task-matrix.v1', 'cases': rows, 'planned_images': 32,
            'generation_calls': 0, 'authorization': 'not_granted_by_matrix',
            'scope': '16 independent specifications; assets and actual A/B outputs not yet bound',
            'metrics': list(METRICS), 'visual_quality': 'not_tested', 'user_acceptance': 'not_recorded'}


def _ancestry_tokens(value) -> set[str]:
    """Consume recorded ancestry identifiers, not inferred visual resemblance."""
    tokens = set()
    if isinstance(value, dict):
        for key, child in value.items():
            if isinstance(child, str) and child:
                if key in ('packet_digest', 'parent_packet_digest', 'source_packet_digest'):
                    tokens.add('packet:' + child)
                elif key.endswith('sha256'):
                    tokens.add('sha:' + child)
                elif key in ('parent_run', 'run', 'source_run'):
                    tokens.add('run:' + str(Path(child).resolve()))
                elif key in ('request_id', 'parent_request_id'):
                    tokens.add('request-id:' + child)
            tokens |= _ancestry_tokens(child)
    elif isinstance(value, list):
        for child in value:
            tokens |= _ancestry_tokens(child)
    return tokens


def evidence_view(run: Path, request_id: str | None = None) -> dict:
    """Read canonical ledger through case_record; return a disposable derived view."""
    run = Path(run).resolve()
    case = harness.case_record(run, request_id)
    packet = read_packet(run)
    tokens = {'run:' + str(run), 'packet:' + packet['digest'],
              'request-id:' + case['attempt']['request_id'],
              'request:' + digest(packet['brief']['request'].strip())}
    tokens |= _ancestry_tokens(packet.get('requirement_parent'))
    for ref in packet.get('references', []):
        tokens |= {'sha:' + ref[k] for k in ('original_sha256', 'sha256') if ref.get(k)}
    if case['candidate']:
        path = within(run, case['candidate']['file'])
        if sha(path.read_bytes()) != case['candidate']['sha256']:
            raise AgentError('Candidate changed while deriving the evaluation view')
        tokens.add('sha:' + case['candidate']['sha256'])
        # The saved normalized PNG and the returned original may differ in bytes.
        # Keep both identifiers from the verified native ledger for grouping.
        if case['route'] == 'host':
            from liu_agent.host import read_dispatches
            attempt = next(r for r in read_dispatches(run) if r['request']['id'] == case['attempt']['request_id'])
        else:
            from liu_agent.api_recovery import load_api_state
            attempt = next(r for r in load_api_state(run)['requests'] if r['id'] == case['attempt']['request_id'])
        original_sha = (attempt.get('output') or {}).get('original_sha256')
        if original_sha:
            tokens.add('sha:' + original_sha)
    else:
        original_sha = None
    if harness._input_hashes(run) != case['evidence_sha256']:
        raise AgentError('Source evidence changed while reading; retry from a stable task')
    return {'case': case, 'group_tokens': sorted(tokens), 'recorded_original_sha256': original_sha,
            'canonical_ledger': str(run / ('api-state.json' if case['route'] == 'api' else 'host-dispatches.json')),
            'authority': 'derived_read_only_not_new_execution_fact'}


def compilation_view(run: Path, case: dict) -> dict:
    """Project an original frozen solution through the native compiler-input API."""
    from liu_agent.contracts import Solution
    from liu_agent.workflow import compilation_input
    run = Path(run).resolve()
    packet = read_packet(run)
    ident = case['attempt']['request_id']
    if case['route'] == 'host':
        from liu_agent.host import read_dispatches
        attempt = next(r for r in read_dispatches(run) if r['request']['id'] == ident)
        raw = attempt['request']['solution']
    else:
        from liu_agent.api_recovery import load_api_state, _verified
        attempt = next(r for r in load_api_state(run)['requests'] if r['id'] == ident)
        raw = _verified(within(run, attempt['snapshot']))['solution']
    solution = Solution.model_validate(raw)
    if solution.packet_digest != packet['digest']:
        raise AgentError('Frozen compiler solution belongs to a different packet')
    return compilation_input(packet, solution.spec, solution.design, targets=[case['model']['target']])


def partition(rows: list[dict], registry: Path | None = None) -> dict:
    """Split by connected recorded ancestry, including native registry groups.

    Rows are evaluation bindings only: case_id, split, source_family and either
    run/request_id or synthetic_text. Synthetic text is never image evidence.
    """
    if not rows or len({r['case_id'] for r in rows}) != len(rows):
        raise AgentError('Evaluation bindings require nonempty unique case IDs')
    native, native_groups = {}, []
    if registry:
        entries = brief_registry._load(Path(registry))['entries']
        for entry in entries:
            brief_registry._verified_files(entry)
        for group in brief_registry._groups(entries):
            group_id = digest(sorted(e['registration']['case_id'] for e in group))
            group_tokens = {'registry-group:' + group_id}
            for entry in group:
                group_tokens.update(brief_registry._tokens(entry))
                for run in entry.get('runs', []):
                    group_tokens.add('run:' + str(Path(run['run']).resolve()))
                    if run.get('initial_packet_digest'):
                        group_tokens.add('packet:' + run['initial_packet_digest'])
            native_groups.append({'tokens': group_tokens, 'splits': {e['registration']['split'] for e in group}})
            for entry in group:
                native[entry['registration']['case_id']] = (entry, group_id)
    prepared = []
    for raw in rows:
        row = deepcopy(raw)
        if any(key in row for key in ('evidence', 'task_input', 'group_tokens', 'compiler_input_scope', 'origin')):
            raise AgentError('Derived evidence, input and origin fields must come from native source projection')
        if row.get('run') and row.get('synthetic_text'):
            raise AgentError('Bind exactly one source: native run or synthetic text, never both')
        if row.get('split') not in SPLITS or not row.get('source_family'):
            raise AgentError('Every case needs an explicit split and source family')
        tokens = {'family:' + row['source_family']}
        if row.get('run'):
            view = evidence_view(Path(row['run']), row.get('request_id'))
            row['evidence'] = view
            row['origin'] = 'existing_native_evidence'
            row['task_input'] = compilation_view(Path(row['run']), view['case'])
            row['compiler_input_scope'] = 'native frozen Need/Plan projection; historical revision context is not reconstructed'
            tokens.update(view['group_tokens'])
        elif isinstance(row.get('synthetic_text'), str) and row['synthetic_text'].strip():
            row['origin'] = 'synthetic_text_not_user_or_visual_evidence'
            tokens.add('request:' + digest(row['synthetic_text'].strip()))
        else:
            raise AgentError('Bind a verified native run or explicitly synthetic text')
        if row['case_id'] in native:
            entry, group_id = native[row['case_id']]
            if (entry['registration']['split'] == 'heldout') != (row['split'] == 'holdout'):
                raise AgentError('Existing heldout membership cannot be reassigned')
            if row['source_family'] != entry['registration']['source_family']:
                raise AgentError('Source family must match the original brief registry')
            if row.get('run'):
                brief_registry._run(entry, Path(row['run']))
            tokens.update(brief_registry._tokens(entry))
            tokens.add('registry-group:' + group_id)
        # Additional source assets are verified, never trusted by a declared hash.
        for asset in row.get('source_assets', []):
            actual = sha(Path(asset['path']).read_bytes())
            if asset.get('sha256') and asset['sha256'] != actual:
                raise AgentError('Evaluation source asset hash mismatch')
            tokens.add('sha:' + actual)
            tokens.add('family:' + asset['source_family'])
        # Renaming a case ID cannot move a registered heldout source into train.
        # Close transitively over all registry groups, including registered runs.
        remaining_native = list(native_groups)
        changed = True
        while changed:
            changed = False
            for group in remaining_native[:]:
                if tokens.intersection(group['tokens']):
                    expected = {'heldout'} if row['split'] == 'holdout' else {'dev'}
                    if group['splits'] != expected:
                        raise AgentError('Registered source group cannot cross existing dev/heldout boundary under a new case ID')
                    tokens.update(group['tokens'])
                    remaining_native.remove(group)
                    changed = True
        row['group_tokens'] = sorted(tokens)
        prepared.append(row)
    remaining, groups = list(prepared), []
    while remaining:
        group = [remaining.pop(0)]
        tokens = set(group[0]['group_tokens'])
        changed = True
        while changed:
            changed = False
            for row in remaining[:]:
                if tokens.intersection(row['group_tokens']):
                    group.append(row)
                    remaining.remove(row)
                    tokens.update(row['group_tokens'])
                    changed = True
        if len({row['split'] for row in group}) != 1:
            raise AgentError('Source/lineage leakage across train, validation or holdout: ' +
                             ', '.join(row['case_id'] for row in group))
        groups.append(sorted(row['case_id'] for row in group))
    body = {'format': 'hex.offline-dataset.v1', 'rows': prepared, 'groups': groups,
            'counts': {split: sum(r['split'] == split for r in prepared) for split in SPLITS},
            'independence_scope': 'recorded hashes, source families, requests and ancestry; unknown crops require source-family annotation'}
    return {**body, 'digest': digest(body)}


def verify_dataset(dataset: dict) -> None:
    if dataset.get('digest') != digest({k: v for k, v in dataset.items() if k != 'digest'}):
        raise AgentError('Offline dataset changed')
    for row in dataset['rows']:
        if row.get('run'):
            current = evidence_view(Path(row['run']), row.get('request_id'))
            if current != row['evidence']:
                raise AgentError('Canonical task evidence changed; rebuild the derived dataset')
            if compilation_view(Path(row['run']), current['case']) != row.get('task_input'):
                raise AgentError('Native compiler input projection changed')
        for asset in row.get('source_assets', []):
            if 'sha:' + sha(Path(asset['path']).read_bytes()) not in row['group_tokens']:
                raise AgentError('Evaluation source asset changed')
