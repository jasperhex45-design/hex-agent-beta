"""Executor boundary: route frozen requests, never choose or switch a model."""
from pathlib import Path

from .io import AgentError
from .comfy_connector import TOOL_TARGETS
from .pippit_connector import TOOL_NAME


def catalog(*, evidence_runs: list[Path] | None = None) -> list[dict]:
    rows = [
        {'id': 'comfy_local', 'implemented': True, 'execution': 'managed',
         'tools': sorted(TOOL_TARGETS), 'recovery': 'query_same_request'},
        {'id': 'cloud_image', 'implemented': True, 'execution': 'managed_cli',
         'tools': [TOOL_NAME], 'recovery': 'query_same_request'},
        {'id': 'codex', 'implemented': True, 'execution': 'external_host',
         'tools': ['image_gen.imagegen'], 'recovery': 'host_receipt_only'},
        {'id': 'photoshop', 'implemented': False, 'execution': 'not_integrated',
         'tools': [], 'recovery': 'not_integrated'},
        {'id': 'comfy_agent', 'implemented': False, 'execution': 'not_integrated',
         'tools': [], 'recovery': 'not_integrated'},
    ]
    rows = [{**row, 'readiness': 'not_probed', 'owns_budget': False,
             'automatic_retries': False,
             'can_submit_via_execute': row['id'] in ('comfy_local', 'cloud_image')}
            for row in rows]
    from .generation_spec import host_capabilities, image_facts
    from .io import within
    for row in rows:
        row['capability_evidence'] = {
            'authority': 'adapter_declaration_and_explicit_saved_records',
            'location': {'comfy_local': 'local', 'cloud_image': 'cloud', 'codex': 'host_platform_unspecified'}.get(row['id'], 'not_integrated'),
            'transport': {'comfy_local': 'http', 'cloud_image': 'cli_to_cloud', 'codex': 'external_host_tool'}.get(row['id'], 'not_integrated'),
            'declared': {tool: host_capabilities(tool) for tool in row['tools']},
            'observed': [], 'unusable_evidence': [], 'current_provider_qualified': False,
            'region_preservation_qualified': False,
            'notice': '声明不是实测；旧完成记录仅支持该次条件，不验证当前账号/服务、参考遵循、区域保持或画质。'}
    if evidence_runs:
        from .harness import case_record
        from .host import read_dispatches
        for path in evidence_runs:
            run = Path(path).resolve()
            try:
                case = case_record(run)
                if case['route'] != 'host' or case['attempt']['state'] != 'completed' or not case['candidate']:
                    raise AgentError('记录未完成或不在已接入宿主路线；不能充作实测能力')
                tool = case['model']['tool']
                entry = next((row for row in rows if tool in row['tools']), None)
                if entry is None:
                    raise AgentError('此工具未在registry接入，不能借其它provider的记录补资格')
                request_id = case['attempt']['request_id']
                request = next(row['request'] for row in read_dispatches(run) if row['request']['id'] == request_id)
                facts = image_facts(within(run, case['candidate']['file']).read_bytes())
                entry['capability_evidence']['observed'].append({
                    'run': str(run), 'request_id': request_id, 'case_digest': case['digest'],
                    'scope': 'verified_saved_completed_execution_only', 'tool': tool, 'model': case['model'],
                    'actual_size': facts['size'], 'candidate_sha256': facts['sha256'],
                    'generation_reference_count': len(request.get('generation_inputs', [])),
                    'reference_order': [ref.get('sha256') for ref in request.get('generation_inputs', [])],
                    'visual_reference_adherence': 'not_verified', 'region_preservation': 'not_verified',
                    'evidence_sha256': case['evidence_sha256'], 'current_availability': 'not_probed'})
            except (AgentError, OSError, ValueError, KeyError, StopIteration) as exc:
                # Keep rejection visible without guessing the intended provider.
                for row in rows:
                    row['capability_evidence']['unusable_evidence'].append({'run': str(run), 'reason': str(exc)})
    return rows


def _adapter(tool: str):
    if tool in TOOL_TARGETS:
        from . import comfy_execution
        return comfy_execution
    if tool == TOOL_NAME:
        from . import pippit_execution
        return pippit_execution
    return None


def _attempt(run: Path, request_id: str) -> dict:
    run = Path(run)
    if any((run / name).exists() for name in ('api-state.json', 'calls.json', 'image-tests.json', 'api')):
        raise AgentError('API 与宿主执行证据冲突；停止执行或查询，不能切换路线')
    from .host import read_dispatches
    attempt = next((row for row in read_dispatches(run) if row['request']['id'] == request_id), None)
    if attempt is None:
        raise AgentError('没有冻结 dispatch；执行器不能直接提交')
    return attempt


def next_action(run: Path, request_id: str) -> dict:
    tool = _attempt(run, request_id)['request']['tool']
    adapter = _adapter(tool)
    if adapter is not None:
        return adapter.next_action(run, request_id)
    return {'command': 'record-result', 'description': '由原宿主核对同一请求的真实回执；禁止重发',
            'request_ids': [request_id]}


def _require_family(tool: str, required_executor: str | None) -> None:
    family = 'comfy_local' if tool in TOOL_TARGETS else 'cloud_image' if tool == TOOL_NAME else 'external_host'
    if required_executor is not None and required_executor != family:
        raise AgentError('请求工具与执行入口不一致；不能切换路线')


def execute(run: Path, request_id: str, *, cli_path: Path | None = None,
            required_executor: str | None = None) -> dict:
    tool = _attempt(run, request_id)['request']['tool']
    _require_family(tool, required_executor)
    adapter = _adapter(tool)
    if adapter is None:
        raise AgentError('EXECUTOR_NOT_INTEGRATED：此请求由外部宿主执行，不能切换路线')
    if tool == TOOL_NAME:
        if cli_path is None:
            raise AgentError('小云雀执行需要已核实的 cli-path')
        return adapter.execute(run, request_id, cli_path)
    if cli_path is not None:
        raise AgentError('本地 ComfyUI 请求不能传入 CLI 或切换执行方式')
    return adapter.execute(run, request_id)


def query(run: Path, request_id: str, *, required_executor: str | None = None) -> dict:
    tool = _attempt(run, request_id)['request']['tool']
    _require_family(tool, required_executor)
    adapter = _adapter(tool)
    if adapter is None:
        raise AgentError('EXECUTOR_QUERY_UNAVAILABLE：核对原宿主回执，不能重新提交')
    return adapter.query(run, request_id)


def compare_comfy_contract(run: Path, request_id: str, *, out: Path | None = None,
                           probe_file: Path | None = None) -> dict:
    """Offline comparison against public MCP interfaces, never an executor switch."""
    from . import comfy_execution
    from .harness import _input_hashes
    from .native_budget import budget_for_run
    from .io import digest, read_json, sha, write_json
    run = Path(run).resolve()
    if out is not None:
        out = Path(out).resolve()
        if out.is_relative_to(run):
            raise AgentError('Contract output must stay outside the original task')
    before = _input_hashes(run)
    attempt = comfy_execution._attempt(run, request_id)
    args = attempt['request']['arguments']
    directory = run / 'comfy' / request_id
    state = comfy_execution._read_state(directory, attempt)
    recovery = comfy_execution.next_action(run, request_id)
    binding = {'same_target':False, 'qualified':False, 'authority':'not_probed',
               'reason':'MCP execution, discovery and validation targets are not bound'}
    if probe_file:
        value = read_json(Path(probe_file))
        if value.get('format') != 'hex.comfy-target-probe.v1':
            raise AgentError('Unknown target probe format')
        targets = [value.get(k) for k in ('execution_target','discovery_target','validation_target')]
        same = all(t == args['endpoint'] for t in targets)
        binding.update(same_target=same, authority=value.get('authority','unknown'),
                       probe_sha256=sha(Path(probe_file).read_bytes()),
                       reason='Saved target note is not a live MCP qualification' if same
                       else 'Execution, discovery and validation must use the same target')
    workflow_path = str(out/'workflow.json') if out else None
    body = {'format':'hex.comfy-contract-comparison.v1', 'run':str(run), 'request_id':request_id,
            'request_digest':attempt['request_digest'], 'arguments_digest':digest(args),
            'workflow_digest':digest(args['workflow']), 'frozen_parameters':
                {k:args[k] for k in ('endpoint','width','height','seed','steps','cfg')},
            'http':{'implemented':True, 'transport':'http', 'entrypoint':'execute-comfy/query-comfy',
                    'request':{'prompt':args['workflow'],'client_id':request_id,
                               'extra_data':{'hex_request_id':request_id}}},
            'mcp':{'implemented_in_hex':False, 'transport':'stdio', 'tool':'run_workflow',
                   'arguments':{'workflow_path':workflow_path,'wait':False,'confirm_spend':False},
                   'workflow_source':'exact_existing_frozen_API_graph', 'dispatch_authorized':False,
                   'original_http_request_may_be_replayed':False,
                   'original_http_job_tracked_by_cli':'not_verified',
                   'source_commit':'e5f768d31de21ea32829381cebdda5336492a8ac',
                   'source_version':'0.10.0',
                   'license':'AGPL-3.0-or-later OR LicenseRef-Comfy-Commercial',
                   'license_disposition':'documentation_contract_only_no_copied_or_installed_implementation'},
            'target_binding':binding, 'existing_recovery':recovery,
            'original_execution_state':state['state'] if state else 'not_submitted',
            'budget':budget_for_run(run), 'new_image_authorization':False,
            'recommendation':'retain_http', 'generation_calls':0, 'writes_to_run':0,
            'network_calls':0, 'automatic_retry':False,
            'next_step':'Review saved contract and unresolved target/recovery bindings before any MCP integration',
            'validation_limits':['UI exports with no discovered nodes', 'dynamic input validation',
                                 'execution/OOM', 'remote discovery/validation target mismatch'],
            'notice':'Public tool declarations and synthetic replies do not qualify a live MCP service.'}
    body['digest'] = digest(body)
    if before != _input_hashes(run):
        raise AgentError('Original task changed during contract comparison')
    if out:
        if out.exists() and any(out.iterdir()):
            if (not (out/'contract.json').is_file() or read_json(out/'contract.json') != body
                    or read_json(out/'workflow.json') != args['workflow']):
                raise AgentError('Contract output is already occupied or changed')
        else:
            out.mkdir(parents=True, exist_ok=True)
            write_json(out/'workflow.json',args['workflow'])
            write_json(out/'contract.json',body)
    return body


def inspect_mcp_reply(reply: dict, *, expected_prompt_id: str | None = None) -> dict:
    """Parse already-saved public reply fields; this function never calls MCP."""
    import re
    from .io import digest
    if not isinstance(reply,dict):
        raise AgentError('Saved MCP reply must be an object')
    ident = reply.get('prompt_id')
    if ident is not None and (not isinstance(ident,str) or not re.fullmatch(r'[A-Za-z0-9_-]{1,100}',ident)):
        raise AgentError('Invalid original MCP job identifier')
    if expected_prompt_id and ident != expected_prompt_id:
        raise AgentError('MCP response differs from the original job identifier')
    status = reply.get('status')
    if not ident:
        state, action = 'outcome_unknown','reconcile_original_submission'
    elif reply.get('timed_out') is True:
        state, action = 'pending','job_status_same_id'
    elif isinstance(status,str) and status in ('failed','error','cancelled'):
        state, action = 'failed','retain_failure_evidence'
    elif status == 'completed':
        state, action = 'outputs_unverified','fetch_outputs_same_id'
    else:
        state, action = 'pending','job_status_same_id'
    body = {'format':'hex.saved-mcp-reply.v1','prompt_id':ident,'state':state,'next_action':action,
            'automatic_retry':False,'budget_refund':False,'generation_calls':0,'network_calls':0,
            'raw_reply_digest':digest(reply),'output_bytes_verified':False,
            'notice':'Only jobs created through the original MCP tracker may use this proposed query path.'}
    return {**body,'digest':digest(body)}


def main(argv=None):
    import argparse, json
    p=argparse.ArgumentParser(description='Inspect existing executor evidence and offline Comfy contracts')
    sub=p.add_subparsers(dest='action',required=True)
    compare=sub.add_parser('compare-comfy');compare.add_argument('--run',type=Path,required=True)
    compare.add_argument('--request-id',required=True);compare.add_argument('--out',type=Path)
    compare.add_argument('--probe',type=Path)
    reply=sub.add_parser('inspect-mcp-reply');reply.add_argument('--file',type=Path,required=True)
    reply.add_argument('--expected-prompt-id')
    sub.add_parser('catalog')
    for item in sub.choices.values():
        item.add_argument('--format',choices=['json','human'],default='json')
    args=p.parse_args(argv)
    try:
        if args.action=='catalog': result=catalog()
        elif args.action=='compare-comfy':
            result=compare_comfy_contract(args.run,args.request_id,out=args.out,probe_file=args.probe)
        else:
            from .io import read_json
            result=inspect_mcp_reply(read_json(args.file),expected_prompt_id=args.expected_prompt_id)
        if args.format == 'human' and args.action == 'compare-comfy':
            print('现有 HTTP 执行器保留；MCP 尚未接入，仅完成离线契约。')
            print('目标绑定：'+result['target_binding']['reason'])
            print('原请求下一步：'+result['existing_recovery']['description'])
            print('新生成许可：无。本轮网络调用／生图：0。')
        elif args.format == 'human' and args.action == 'inspect-mcp-reply':
            print('已保存回执状态：'+result['state']+'；下一步：'+result['next_action'])
            print('不自动重试，不退还已用预算。')
        else: print(json.dumps(result,ensure_ascii=False,indent=2))
        return 0
    except (AgentError,OSError,ValueError,KeyError) as exc:
        print(json.dumps({'error':str(exc),'generation_calls':0},ensure_ascii=False));return 2


if __name__=='__main__': raise SystemExit(main())
