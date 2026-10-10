"""Execute a frozen local dispatch once, then recover only its ComfyUI history."""
from __future__ import annotations

from pathlib import Path
import re
import time

import httpx

from . import host
from .comfy_connector import EDIT_TOOLS, TOOL_TARGETS, validate_arguments, validate_live_nodes
from .generation_spec import image_facts
from .io import AgentError, MAX_IMAGE_BYTES, digest, now, read_json, sha, within, write_json
from .run_lock import dispatch_lock


def _attempt(run: Path, request_id: str) -> dict:
    attempt = next((a for a in host.read_dispatches(run) if a['request']['id'] == request_id), None)
    if attempt is None or attempt['request']['tool'] not in TOOL_TARGETS:
        raise AgentError('未找到对应 ComfyUI dispatch；不能直接提交')
    request = attempt['request']
    editing = request['tool'] in EDIT_TOOLS
    images = request.get('images_in_order', [])
    if (TOOL_TARGETS[request['tool']] != request['target'] or (images and not editing)
            or request['prompt'] != request['arguments'].get('prompt')):
        raise AgentError('ComfyUI dispatch 目标、提示词或输入不一致')
    validate_arguments(request['arguments'], request['target'], tool=request['tool'], run=run)
    if editing and (len(images) != 1 or any(
            images[0].get(key) != request['arguments']['input_image'][key]
            for key in ('id', 'file', 'sha256', 'role'))):
        raise AgentError('本地编辑父图与 dispatch 冻结输入不一致')
    return attempt


def _directory(run: Path, request_id: str) -> Path:
    directory = within(run, 'comfy/' + request_id)
    directory.mkdir(parents=True, exist_ok=True)
    return directory


def _save(directory: Path, state: dict) -> dict:
    state['updated_at'] = now()
    write_json(directory / 'execution.json', state)
    return state


def _attach_id(state: dict, response: dict) -> bool:
    if not isinstance(response, dict):
        return False
    ident = response.get('prompt_id')
    if not isinstance(ident, str) or not re.fullmatch(r'[A-Za-z0-9_-]{1,100}', ident):
        return False
    state.update(prompt_id=ident, state='submitted')
    return True


def _record(path: Path):
    try:
        return read_json(path)
    except (OSError, ValueError, RecursionError) as exc:
        raise AgentError('ComfyUI 保存的调用证据缺失或损坏；核对原提交，禁止重发') from exc


def _read_state(directory: Path, attempt: dict) -> dict | None:
    if not (directory / 'execution.json').exists():
        evidence = ('actual-arguments.json', 'submit-body.json', 'submit-response.json',
                    'submission-started.json', 'upload-request.json', 'upload-response.json',
                    'uploaded-image-facts.json', 'mask-upload-request.json', 'mask-upload-response.json',
                    'mask-uploaded-image-facts.json', 'alpha-upload-request.json', 'alpha-upload-response.json',
                    'protection-upload-request.json', 'protection-upload-response.json', 'decoded-original.png',
                    'alpha-uploaded-image-facts.json', 'protection-uploaded-image-facts.json',
                    'decoded-original-facts.json', 'decoded-output-info.json', 'protected-pixels-check.json',
                    'history-response.json', 'result.png', 'result-facts.json')
        if any((directory / name).exists() for name in evidence):
            raise AgentError('ComfyUI 执行状态丢失但已有调用证据；核对原提交，禁止重发')
        return None
    state = _record(directory / 'execution.json')
    if not isinstance(state, dict):
        raise AgentError('ComfyUI 执行状态必须是对象；核对原提交，禁止重发')
    phases = ('uploading', 'uploaded', 'upload_failed', 'submitting', 'submitted',
              'outcome_unknown', 'query_failed', 'pending', 'completed', 'failed')
    if state.get('format') != 'hex.comfy-execution.v1' or state.get('state') not in phases:
        raise AgentError('ComfyUI 执行状态格式或阶段无效；核对原提交，禁止重发')
    if (state.get('request_id') != attempt['request']['id']
            or state.get('request_digest') != attempt['request_digest']
            or state.get('arguments_digest') != digest(attempt['request']['arguments'])
            or _record(directory / 'actual-arguments.json') != attempt['request']['arguments']):
        raise AgentError('ComfyUI 执行记录或实参与冻结调用不一致')
    if 'decoded_output' in state:
        decoded=state['decoded_output']
        try:
            facts=image_facts((directory/'decoded-original.png').read_bytes())
            valid=(decoded.get('file')=='decoded-original.png' and decoded.get('sha256')==facts['sha256']
                and _record(directory/'decoded-original-facts.json')==facts
                and _record(directory/'decoded-output-info.json')['node']==attempt['request']['arguments']['decoded_output_node'])
        except (OSError,ValueError,KeyError,TypeError,AttributeError):valid=False
        if not valid:raise AgentError('原始解码证据缺失或被改动；禁止重发，核对原任务')
    ident = state.get('prompt_id')
    if ident is not None and (not isinstance(ident, str) or not re.fullmatch(r'[A-Za-z0-9_-]{1,100}', ident)):
        raise AgentError('ComfyUI 原任务标识无效；禁止重发')
    response_path = directory / 'submit-response.json'
    response = _record(response_path) if response_path.exists() else None
    if response_path.exists() and not isinstance(response, dict):
        raise AgentError('ComfyUI 保存的提交回执不是对象；核对原提交，禁止重发')
    recovered = {}
    has_receipt_id = _attach_id(recovered, response)
    if ident is not None and (not has_receipt_id or ident != recovered['prompt_id']):
        raise AgentError('ComfyUI 原任务标识与保存的提交回执不一致；禁止查询或重发')
    uploading = state['state'] in ('uploading', 'uploaded', 'upload_failed')
    if uploading and (attempt['request']['tool'] not in EDIT_TOOLS or ident is not None
                      or response_path.exists() or (directory / 'submission-started.json').exists()):
        raise AgentError('ComfyUI 上传状态与已有提交证据冲突；核对原提交，禁止重发')
    if ident is None and has_receipt_id:
        state.update(recovered)
    return state


def next_action(run: Path, request_id: str) -> dict:
    """Read-only recovery guidance; never create files, submit, or query HTTP."""
    run = run.resolve()
    attempt = _attempt(run, request_id)
    directory = within(run, 'comfy/' + request_id)
    state = _read_state(directory, attempt)
    result = {'command': None, 'description': '核对原提交记录；禁止重发',
              'request_ids': [request_id]}
    if attempt['status'] == 'pending' and state is None:
        return {**result, 'command': 'execute-comfy',
                'description': '执行此已预留且尚未提交的本地 ComfyUI 调用'}
    if attempt['status'] in ('pending', 'outcome_unknown') and state and state.get('prompt_id'):
        return {**result, 'command': 'query-comfy',
                'description': '按已保存的 prompt_id 查询原任务；禁止重新生成'}
    if attempt['status'] == 'pending' and state and state.get('state') in ('uploading', 'uploaded', 'upload_failed'):
        return {**result, 'command': 'execute-comfy',
                'description': '仅恢复本地父图上传核验，再执行尚未提交的冻结调用'}
    return result


def _client(timeout: int, transport=None):
    return httpx.Client(timeout=httpx.Timeout(timeout, connect=min(timeout, 10)),
                        follow_redirects=False, trust_env=False, transport=transport)


def _json(response: httpx.Response) -> dict:
    response.raise_for_status()
    return _response_json(response)


def _response_json(response: httpx.Response) -> dict:
    try:
        value = response.json()
    except (ValueError, RecursionError) as exc:
        raise AgentError('ComfyUI 返回无效 JSON') from exc
    if not isinstance(value, dict):
        raise AgentError('ComfyUI 返回非对象 JSON')
    return value


def _unknown(run: Path, request_id: str, directory: Path, state: dict, reason: str) -> dict:
    state.update(state='outcome_unknown', problem=reason)
    _save(directory, state)
    if _attempt(run, request_id)['status'] in ('pending', 'outcome_unknown'):
        host.record_result(run, request_id, 'outcome_unknown', None, reason)
    return state


def _upload_parent(run: Path, args: dict, directory: Path, client: httpx.Client, *, image=None, evidence_prefix='') -> None:
    image = args['input_image'] if image is None else image
    raw = within(run, image['file']).read_bytes()
    if sha(raw) != image['sha256']:
        raise AgentError('父图文件已变化，不能上传')
    fields = {'type': 'input', 'subfolder': image['subfolder'], 'overwrite': 'false'}
    write_json(directory / (evidence_prefix+'upload-request.json'), {
        **fields, 'name': image['local_upload_name'], 'sha256': image['sha256'],
        'byte_length': len(raw), 'operation': 'local_input_upload_not_generation'})
    response = _json(client.post(args['endpoint'] + '/upload/image', data=fields,
                                files={'image': (image['local_upload_name'], raw, 'image/png')}))
    write_json(directory / (evidence_prefix+'upload-response.json'), response)
    if any(response.get(key) != value for key, value in {
            'name': image['local_upload_name'], 'subfolder': image['subfolder'], 'type': 'input'}.items()):
        raise AgentError('本地上传回执文件名或目录不一致；不能改写冻结工作流')
    params = {'filename': image['local_upload_name'], 'subfolder': image['subfolder'], 'type': 'input'}
    with client.stream('GET', args['endpoint'] + '/view', params=params) as response:
        response.raise_for_status()
        chunks, length = [], 0
        for chunk in response.iter_bytes():
            length += len(chunk)
            if length > MAX_IMAGE_BYTES:
                raise AgentError('本地父图回读超过大小限制')
            chunks.append(chunk)
    verified = image_facts(b''.join(chunks))
    if (verified['sha256'] != image['sha256'] or verified['width'] != image['width']
            or verified['height'] != image['height']):
        raise AgentError('本地已上传父图字节与冻结图片不一致')
    write_json(directory / (evidence_prefix+'uploaded-image-facts.json'), verified)


def execute(run: Path, request_id: str, *, timeout: int = 45, transport=None) -> dict:
    """A pending dispatch permits one POST /prompt, including interrupted calls."""
    run = run.resolve()
    attempt = _attempt(run, request_id)
    directory = _directory(run, request_id)
    with dispatch_lock(directory):
        attempt = _attempt(run, request_id)
        state = _read_state(directory, attempt)
        if state is not None:
            if state['state'] == 'submitting' and not state.get('prompt_id'):
                return _unknown(run, request_id, directory, state, '先前本地提交中断；结果未知，不重发')
            if state['state'] not in ('uploading', 'uploaded', 'upload_failed'):
                return _save(directory, state)
        if attempt['status'] != 'pending':
            raise AgentError('只有未提交的 pending dispatch 可执行；未知或已结束调用不能重发')
        from .generation_authorization import require_submission_authorization, submission_guard
        from .native_budget import budget_for_run
        require_submission_authorization(run, attempt, budget_for_run(run))
        args = attempt['request']['arguments']
        editing = attempt['request']['tool'] in EDIT_TOOLS
        # No generation occurs during this read-only check. Failures can be
        # corrected before entering the durable submission boundary below.
        try:
            with _client(timeout, transport) as client:
                info = _json(client.get(args['endpoint'] + '/object_info'))
            validate_live_nodes(args, info, before_upload=editing)
        except (httpx.HTTPError, ValueError) as exc:
            raise AgentError('本地 ComfyUI 节点检查失败；尚未提交生成') from exc
        body = {'prompt': args['workflow'], 'client_id': request_id,
                'extra_data': {'hex_request_id': request_id}}
        if state is None:
            write_json(directory / 'actual-arguments.json', args)
            write_json(directory / 'submit-body.json', body)
            state = {'format': 'hex.comfy-execution.v1', 'request_id': request_id,
                     'request_digest': attempt['request_digest'], 'arguments_digest': digest(args),
                     'state': 'uploading' if editing else 'submitting', 'started_at': now()}
        if editing:
            state.update(state='uploading', input_upload_verified=False, problem=None)
            _save(directory, state)
            try:
                with _client(timeout, transport) as client:
                    _upload_parent(run, args, directory, client)
                    if 'mask_image' in args:
                        _upload_parent(run,args,directory,client,image=args['mask_image'],evidence_prefix='mask-')
                    for key,prefix in (('alpha_image','alpha-'),('protection_image','protection-')):
                        if key in args:_upload_parent(run,args,directory,client,image=args[key],evidence_prefix=prefix)
            except (httpx.HTTPError, ValueError, OSError, AgentError):
                state.update(state='upload_failed', problem='本地父图上传或回读核验失败；尚未提交生成，可恢复同图上传')
                return _save(directory, state)
            state.update(state='uploaded', input_upload_verified=True)
            _save(directory, state)
        rejected = False
        with submission_guard(run, attempt):
            state['state'] = 'submitting'
            _save(directory, state)
            # A stop registered during node checks/upload wins before this boundary.
            write_json(directory / 'submission-started.json', {
                'request_id': request_id, 'request_digest': attempt['request_digest'],
                'arguments_digest': digest(args), 'started_at': now()})
            started = time.monotonic()
            try:
                with _client(timeout, transport) as client:
                    response = client.post(args['endpoint'] + '/prompt', json=body)
                state['http_status'] = response.status_code
                payload = _response_json(response)
                write_json(directory / 'submit-response.json', payload)
                state['submission_seconds'] = round(time.monotonic() - started, 3)
                if _attach_id(state, payload):
                    return _save(directory, state)
                rejected = response.status_code == 400 and bool(payload.get('error'))
            except (httpx.HTTPError, ValueError, AgentError):
                pass
        if rejected:
            host.record_result(run, request_id, 'failed', None, 'ComfyUI 明确拒绝工作流；本次尝试占额度')
            state.update(state='failed', problem='本地服务明确拒绝工作流；未重发')
            return _save(directory, state)
        return _unknown(run, request_id, directory, state, '本地提交没有确定 prompt_id；先核对原任务，不重发')


def _pending(directory: Path, state: dict, problem: str) -> dict:
    state.update(state='query_failed', problem=problem)
    return _save(directory, state)

def _decode_evidence(directory: Path, args: dict, outputs: dict, client) -> dict:
    output=outputs.get(args['decoded_output_node'],{})
    images=output.get('images')
    if not isinstance(images,list) or len(images)!=1 or not isinstance(images[0],dict):
        raise AgentError('原任务缺少唯一未合成解码图；只查原任务，不接受缺失证据')
    image=images[0];filename=image.get('filename');subfolder=image.get('subfolder','')
    if (not isinstance(filename,str) or not filename.lower().endswith('.png')
        or any(c in filename for c in '/\\:[]') or any(ord(c)<32 or ord(c)==127 for c in filename)
        or image.get('type')!='output' or not isinstance(subfolder,str)
        or '..' in subfolder.replace('\\','/').split('/') or subfolder.startswith(('/','\\'))
        or ':' in subfolder or any(ord(c)<32 or ord(c)==127 for c in subfolder)):
        raise AgentError('未合成解码图地址无效')
    chunks=[];length=0
    with client.stream('GET',args['endpoint']+'/view',params=image) as response:
        response.raise_for_status()
        for chunk in response.iter_bytes():
            length+=len(chunk)
            if length>MAX_IMAGE_BYTES:raise AgentError('未合成解码图超过大小限制')
            chunks.append(chunk)
    raw=b''.join(chunks);facts=image_facts(raw)
    if facts['size']!=f"{args['width']}x{args['height']}" or facts['output_format']!='png':
        raise AgentError('未合成解码图尺寸或格式不符')
    (directory/'decoded-original.png').write_bytes(raw)
    write_json(directory/'decoded-original-facts.json',facts)
    write_json(directory/'decoded-output-info.json',{'image':image,'node':args['decoded_output_node'],'authority':'uncomposited_decode_not_final_candidate'})
    return {'file':'decoded-original.png','sha256':facts['sha256']}


def query(run: Path, request_id: str, *, timeout: int = 45, transport=None) -> dict:
    """Read the original task, verify its frozen graph, and retrieve one PNG."""
    run = run.resolve()
    attempt = _attempt(run, request_id)
    directory = _directory(run, request_id)
    with dispatch_lock(directory):
        attempt = _attempt(run, request_id)
        state = _read_state(directory, attempt)
        if not state or not state.get('prompt_id'):
            raise AgentError('没有原任务 prompt_id；核对原提交，不能重新生成')
        if attempt['status'] in ('completed', 'failed'):
            state.update(state=attempt['status'], output=attempt.get('output'))
            return _save(directory, state)
        args, prompt_id = attempt['request']['arguments'], state['prompt_id']
        try:
            with _client(timeout, transport) as client:
                response = _json(client.get(args['endpoint'] + '/history/' + prompt_id))
        except (httpx.HTTPError, ValueError, AgentError):
            return _pending(directory, state, '本地历史查询失败；只查询原任务')
        write_json(directory / 'history-response.json', response)
        entry = response.get(prompt_id)
        if entry is None:
            state.update(state='pending', problem='原任务尚无完成历史；可能排队或历史已被清理，不重发')
            return _save(directory, state)
        history_prompt = entry.get('prompt') if isinstance(entry, dict) else None
        if (not isinstance(history_prompt, list) or len(history_prompt) < 3
                or history_prompt[1] != prompt_id or history_prompt[2] != args['workflow']):
            return _pending(directory, state, '服务历史中的任务标识或实发 workflow 不匹配；未登记结果')
        status = entry.get('status', {})
        if not isinstance(status, dict):
            return _pending(directory, state, '原任务状态格式无效；只重查原任务')
        if status.get('status_str') == 'error':
            host.record_result(run, request_id, 'failed', None, 'ComfyUI 原任务返回 execution error；未重发')
            state.update(state='failed', problem='ComfyUI 原任务执行失败；保留历史回执')
            return _save(directory, state)
        if status.get('completed') is not True or status.get('status_str') != 'success':
            state.update(state='pending', problem=None)
            return _save(directory, state)
        outputs = entry.get('outputs')
        if not isinstance(outputs, dict):
            return _pending(directory, state, '原任务输出格式无效；只重查原任务')
        output = outputs.get(args['output_node'])
        if not isinstance(output, dict):
            return _pending(directory, state, '原任务缺少冻结输出节点；只重查原任务')
        images = output.get('images')
        if not isinstance(images, list) or len(images) != 1 or not isinstance(images[0], dict):
            return _pending(directory, state, '原任务未返回唯一图片；不推断完成')
        image = images[0]
        filename, subfolder = image.get('filename'), image.get('subfolder', '')
        if (not isinstance(filename, str) or not filename.lower().endswith('.png')
                or any(char in filename for char in '/\\:[]')
                or any(ord(char) < 32 or ord(char) == 127 for char in filename)
                or image.get('type') != 'output' or not isinstance(subfolder, str)
                or '..' in subfolder.replace('\\', '/').split('/') or subfolder.startswith(('/', '\\'))
                or ':' in subfolder or any(ord(char) < 32 or ord(char) == 127 for char in subfolder)):
            return _pending(directory, state, '返回图片地址无效；未下载')
        try:
            with _client(timeout, transport) as client:
                if 'decoded_output_node' in args:state['decoded_output']=_decode_evidence(directory,args,outputs,client)
                with client.stream('GET', args['endpoint'] + '/view', params={
                        'filename': filename, 'subfolder': subfolder, 'type': 'output'}) as response:
                    response.raise_for_status()
                    chunks, length = [], 0
                    for chunk in response.iter_bytes():
                        length += len(chunk)
                        if length > MAX_IMAGE_BYTES:
                            raise AgentError('返回图片过大')
                        chunks.append(chunk)
            raw = b''.join(chunks)
            facts = image_facts(raw)
            if (facts['size'] != f"{args['width']}x{args['height']}" or facts['output_format'] != 'png'):
                raise AgentError('返回尺寸或格式与冻结输入不一致')
            result_path = directory / 'result.png'
            result_path.write_bytes(raw)
            write_json(directory / 'result-facts.json', facts)
            if 'mask_image' in args:
                from .comfy_mask import protected_pixels
                protection=protected_pixels(run,args,raw)
                write_json(directory/'protected-pixels-check.json',protection)
                if not protection['verified']:
                    host.record_result(run,request_id,'failed',None,'Comfy已返回图，但蒙版外像素不符；保留原图证据，不重发')
                    state.update(state='failed',problem='区域保护核验失败；原图已保存，未重发')
                    return _save(directory,state)
            result = host.record_result(run, request_id, 'completed', result_path,
                                        'ComfyUI 实际提交、同任务历史和原图字节；不代表视觉验收',
                                        directory / 'actual-arguments.json')
        except (httpx.HTTPError, ValueError, OSError, AgentError):
            return _pending(directory, state, '原任务图片下载或参数核验失败；只重查原任务')
        state.update(state='completed', output=result['output'], problem=None)
        return _save(directory, state)
