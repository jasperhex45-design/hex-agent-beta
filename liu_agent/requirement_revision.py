"""Prepare an associated requirement revision without rewriting its parent run."""
from copy import deepcopy
from pathlib import Path
from shutil import copytree, rmtree
from tempfile import TemporaryDirectory
from uuid import uuid4

from .clarification import reconcile_questions
from .contracts import Need, Reference
from .io import AgentError, read_json, sha, within, write_json
from .provenance import merge_sources, resolve_refs, verified_sources
from .workflow import make_packet, read_packet, save_packet


def branch_requirements(parent_run: Path, out: Path, kb: Path, state: Path,
                         need_file: Path, sources_file: Path,
                         edit_reference_file: Path | None = None,
                         parent_request_id: str | None = None, *,
                        reuse_parent_input: bool = False) -> dict:
    """Create a new planning packet; generation and budget transfer stay separate.

    Build beside the destination and publish by rename only after the parent link
    is sealed. A failed write cannot leave a usable, unassociated child packet.
    The parent may have pending generation: preparing a changed need is read-only
    with respect to that call and does not authorize another one.
    """
    parent_run, out = Path(parent_run).resolve(), Path(out).resolve()
    if out == parent_run or out.is_relative_to(parent_run):
        raise AgentError('新需求目录不能等于或位于原任务目录内')
    if out.exists():
        raise AgentError('新需求输出目录必须不存在；不能覆盖已有目录')
    if parent_request_id is not None and edit_reference_file is None:
        raise AgentError('--parent-request-id 仅用于显式父图的关联编辑')

    if type(reuse_parent_input) is not bool:
        raise AgentError('同输入重测标记必须是布尔值')
    if reuse_parent_input and (edit_reference_file is not None or parent_request_id is not None):
        raise AgentError('同输入重测不能同时选择另一张父图')
    if reuse_parent_input:
        reusable_input_candidate(parent_run)
    parent = read_packet(parent_run)
    previous_sources = parent.get('input_sources')
    previous_ids = {row['id'] for row in verified_sources(previous_sources)}
    merged = merge_sources(previous_sources, sources_file)
    new_user_ids = {row['id'] for row in verified_sources(merged)
                    if row['actor'] == 'user' and row['id'] not in previous_ids}
    if not new_user_ids:
        raise AgentError('新需求版本需要新增用户消息；重复来源或Agent提案不能代替')
    need = Need.model_validate(read_json(need_file))
    if need.brief_change is not None:
        messages = resolve_refs(merged, need.brief_change.source_refs)
        if not any(row['actor'] == 'user' and row['id'] in new_user_ids for row in messages):
            raise AgentError('需求分支的范围或领域变化必须引用新增用户消息，不能重用旧指令')
    from .co_creation import enabled as interaction_enabled
    need = reconcile_questions(parent['need'], need, merged, selective=interaction_enabled(parent))
    if reuse_parent_input and (not need.understanding or need.understanding.task_mode != 'local_edit'
            or (need.brief_change is not None and need.brief_change.scope not in (None, 'edit', 'color_only', 'relight', 'scene'))):
        raise AgentError('同输入重测必须保留明确的局部编辑范围')

    brief = deepcopy(parent['brief'])
    candidate = None
    for reference in brief['references']:
        reference['path'] = str(within(parent_run, reference['path']))
    if edit_reference_file is not None:
        if need.brief_change is None or need.brief_change.scope not in ('edit', 'color_only', 'relight', 'scene') or not need.understanding or need.understanding.task_mode != 'local_edit':
            raise AgentError('关联编辑参考仅适用于明确的 local_edit 范围变更')
        from .feedback import verified_candidate
        if parent['brief'].get('model_comparison') and not parent_request_id:
            raise AgentError('双模型比较的关联编辑须明确指定父图request_id')
        _, _, candidate = verified_candidate(parent_run, parent_request_id)
        if parent['brief'].get('model_comparison'):
            # This child edits one real image. The parent's two-image comparison
            # and its unspent/used permissions remain frozen in the parent.
            requested_targets = need.brief_change.targets
            if requested_targets is not None and len(requested_targets) != 1:
                raise AgentError('单张关联编辑只能明确指定一个目标模型')
            brief['model_comparison'] = False
            brief['targets'] = requested_targets or [candidate['target']]
        descriptor = read_json(edit_reference_file)
        if not isinstance(descriptor, dict) or descriptor.get('rights') != 'user_authorized':
            raise AgentError('编辑画布需要明确的 user_authorized 输入权限')
        reference = Reference.model_validate(descriptor)
        if reference.role != 'identity' or reference.evaluation_only:
            raise AgentError('编辑画布必须是可用于生成的 identity 参考')
        source = (edit_reference_file.parent / reference.path).resolve()
        selected = within(parent_run, candidate['file']).resolve()
        if source != selected or not source.is_file() or sha(source.read_bytes()) != candidate['sha256']:
            raise AgentError('编辑画布必须是当前选中父图，文件或摘要不能变化')
        if any(row['id'] == reference.id for row in brief['references']):
            raise AgentError('编辑画布参考 ID 与已有参考重复')
        brief['references'].append({**reference.model_dump(), 'path': str(selected)})

    out.parent.mkdir(parents=True, exist_ok=True)
    with TemporaryDirectory(prefix='.liu-requirement-', dir=out.parent) as temporary:
        staging = Path(temporary).resolve()
        brief_input, need_input, source_input = (staging / name for name in
                                               ('brief.json', 'need.json', 'sources.json'))
        write_json(brief_input, brief)
        write_json(need_input, need.model_dump())
        write_json(source_input, {'format': merged['format'], 'messages': merged['messages']})
        staged_run = staging / 'run'
        from .visual_decision import policy
        inherited_policy = policy(parent)
        from .workflow_policy import policy as workflow_policy
        inherited_workflow = workflow_policy(parent)
        from .visual_research import validate_refresh as validate_research_refresh
        changed_brief = {**brief, **({'scope': need.brief_change.scope}
                                   if need.brief_change and need.brief_change.scope is not None else {})}
        validate_research_refresh(parent, need.model_dump(), merged, current_brief=changed_brief)
        packet = make_packet(brief_input, staged_run, kb, state, need_input,
                             sources_file=source_input, previous_need=parent['need'],
                             execution_profile=inherited_policy['profile'] if inherited_policy else 'legacy',
                             interaction_profile=parent.get('interaction_policy', {}).get('profile'),
                             knowledge_config=parent.get('knowledge_retrieval_config'),
                             workflow_profile=inherited_workflow['name'] if inherited_workflow else 'legacy',
                             deliverable_kind=(('local_edit' if brief['scope'] != 'create' else inherited_workflow['deliverable_kind'])
                                               if inherited_workflow else None),
                             host_reference_descriptors=[
                                 {k: v for k, v in row.items() if k not in ('metadata_digest', 'source_message_digest')}
                                 for row in parent.get('host_visible_references', [])])
        packet['requirement_parent'] = {
            'run': str(parent_run), 'packet_digest': parent['digest'],
        }
        if reuse_parent_input:
            packet['requirement_parent']['reuse_parent_input'] = True
        if parent_request_id is not None:
            packet['requirement_parent'].update(
                parent_request_id=candidate['request_id'],
                parent_candidate_sha256=candidate['sha256'])
        packet = save_packet(packet, staged_run)
        # Do not replace a destination created while the packet was prepared.
        if out.exists():
            raise AgentError('新需求输出目录已存在；未覆盖')
        _publish_staged_run(staged_run, out)
    return packet


def _publish_staged_run(staged_run: Path, out: Path) -> None:
    """Keep private construction private, then publish with destination inheritance.

    Moving a directory from mkdtemp's private Windows parent retains its restricted
    ACL and prevents the separate executor identity reading the published task.
    Copy only task artifacts to an ordinary sibling before the final atomic rename;
    no account, ACL or credential settings are changed.
    """
    publication = out.parent / ('.liu-publish-' + uuid4().hex)
    try:
        copytree(staged_run, publication)
        if out.exists():
            raise AgentError('新需求输出目录已存在；未覆盖')
        publication.rename(out)
    finally:
        if publication.exists():
            if publication.resolve().parent != out.parent.resolve() or not publication.name.startswith('.liu-publish-'):
                raise AgentError('发布临时目录超出预期父目录；保留现场')
            rmtree(publication)


def reusable_input_candidate(run: Path, visited: frozenset[Path] = frozenset()) -> dict:
    """Resolve only the verified input of this linked edit, never its failed output.

    This is source selection, not a grant. The normal terminal budget, new user
    source, parent-attempt validation and unknown guards still apply at dispatch.
    """
    from .feedback import verified_candidate
    from .reference_gate import validate_reference_policy
    run = Path(run).resolve()
    if run in visited:
        raise AgentError('同输入来源关联存在循环')
    packet = read_packet(run)
    if (packet.get('need', {}).get('understanding') or {}).get('task_mode') != 'local_edit':
        raise AgentError('同输入重测需要已有的局部编辑任务')
    link = packet.get('requirement_parent')
    if not isinstance(link, dict) or not isinstance(link.get('run'), str):
        raise AgentError('同输入重测缺少可核验的父图来源')
    parent = Path(link['run'])
    if not parent.is_absolute() or parent != parent.resolve():
        raise AgentError('同输入父图来源路径无效')
    pp = read_packet(parent)
    if link.get('packet_digest') != pp['digest']:
        raise AgentError('同输入父图来源摘要已变化')
    base = {'run', 'packet_digest'}
    if set(link) == base | {'reuse_parent_input'} and link['reuse_parent_input'] is True:
        candidate = reusable_input_candidate(parent, visited | {run})
    elif set(link) == base:
        _, _, candidate = verified_candidate(parent)
    elif set(link) == base | {'parent_request_id', 'parent_candidate_sha256'}:
        _, _, candidate = verified_candidate(parent, link['parent_request_id'])
        if candidate['sha256'] != link['parent_candidate_sha256']:
            raise AgentError('同输入父图身份摘要不一致')
    else:
        raise AgentError('同输入父图关联结构无效')
    targets = validate_reference_policy(packet)['edit_target_ids']
    refs = [r for r in packet['references'] if r['id'] in targets]
    if len(refs) != 1 or refs[0]['sha256'] != candidate['sha256']:
        raise AgentError('同输入重测仅支持一张已核验的原编辑画布')
    if sha(within(run, refs[0]['file']).read_bytes()) != candidate['sha256']:
        raise AgentError('同输入原画布字节已变化')
    return candidate
