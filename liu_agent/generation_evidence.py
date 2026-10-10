"""Read-only generation provenance for review; a receipt is not visual success."""
from __future__ import annotations

from copy import deepcopy
from pathlib import Path

from .contracts import Solution
from .design import acceptance
from .io import AgentError, digest, read_json, sha, within
from .reference_gate import read_reference_policy, validate_generation_inputs

FORMAT = 'liu.generation-evidence.v1'
_INVALID = (OSError, ValueError, TypeError, KeyError, AttributeError, IndexError)


def _parameters(packet: dict, spec_raw, capabilities, parameters, *, prompt: str,
                design: dict, inputs: list[dict], image: bytes, output: dict,
                actual_arguments: dict | None = None, legacy_adapter: bool = False) -> dict:
    """Check saved facts without upgrading old receipts or recomputing intent."""
    policy = read_reference_policy(packet)
    if spec_raw is None:
        if capabilities is not None or parameters is not None:
            raise AgentError('生成规格缺失但存在参数回执，不能推断原请求')
        if policy['version'] and not legacy_adapter:
            raise AgentError('新版生成请求缺少 generation_spec，不能补造执行证据')
        return {}
    from .generation_spec import GenerationSpec, image_facts, validate_generation_spec, verify_returned_parameters
    try:
        spec = GenerationSpec.model_validate(spec_raw)
        if not isinstance(capabilities, dict) or not isinstance(parameters, dict):
            raise AgentError('生成规格缺少能力快照或三层参数回执')
        body = {k: v for k, v in capabilities.items() if k != 'snapshot_id'}
        if capabilities.get('snapshot_id') != 'liu-capability-' + digest(body):
            raise AgentError('生成能力快照摘要不一致')
        validate_generation_spec(spec, capabilities=capabilities, prompt=prompt,
                                 design_digest=digest(design),
                                 generation_reference_ids=[r['id'] for r in inputs])
        if policy['version'] and spec.task_mode != policy['task_mode']:
            raise AgentError('生成规格的任务模式与冻结参考策略不一致')
        if parameters.get('requested') != spec_raw:
            raise AgentError('实际生成参数回执与请求规格不一致')
        sent = parameters.get('actually_sent')
        if not isinstance(sent, dict):
            raise AgentError('实际发送参数缺失')
        if actual_arguments is not None:
            if sent != actual_arguments or sent.get('prompt') != prompt:
                raise AgentError('生成参数回执与实际宿主工具参数不一致')
            if parameters.get('controls') != capabilities['parameters']:
                raise AgentError('生成参数回执的可控制字段与能力快照不一致')
            returned = parameters.get('returned')
        else:
            if (parameters.get('capabilities') != capabilities or
                    sent.get('prompt_digest') != sha(prompt.encode('utf-8')) or
                    sent.get('generation_reference_ids') != [r['id'] for r in inputs] or
                    sent.get('image_sha256_in_order') != [r['sha256'] for r in inputs]):
                raise AgentError('API实际参数摘要、输入顺序或能力快照不一致')
            actual = sent.get('parameters', {})
            expected = {'model': spec.model_id, 'size': spec.requested_size,
                        'quality': spec.quality, 'output_format': spec.output_format}
            if any(actual.get(k) != v for k, v in expected.items()) or actual.get('n') not in (1, '1'):
                raise AgentError('API实际发送的尺寸、质量、格式或数量与规格不一致')
            returned = parameters.get('returned', {}).get('image')
        saved = image_facts(image)
        saved_record = parameters.get('saved', {})
        if any(saved_record.get(k) != v for k, v in saved.items()) or output.get('sha256') != saved['sha256']:
            raise AgentError('生成参数回执的保存图像事实与候选字节不一致')
        if not isinstance(returned, dict) or not all(key in returned for key in saved):
            raise AgentError('生成参数回执缺少原始返回图像事实')
        original_sha = output.get('original_sha256')
        if original_sha is None or returned['sha256'] != original_sha:
            raise AgentError('生成参数回执的原始图像摘要不一致')
        if returned['sha256'] == saved['sha256'] and returned != saved:
            raise AgentError('相同返回字节的图像事实不一致')
        if actual_arguments is not None and 'returned_verification_version' in parameters:
            version = parameters['returned_verification_version']
            if type(version) is not int or version != 1:
                raise AgentError('未知返回参数核验版本，不能降级或推断匹配')
            if parameters.get('verified') != verify_returned_parameters(spec, returned, {}, display_facts=saved):
                raise AgentError('返回参数核验与原始图像事实不一致')
        # Normalization can change encoding and orientation, so original facts
        # are checked against their original receipt, not inferred from PNG.
        return {'generation_spec': deepcopy(spec_raw),
                'capability_snapshot': deepcopy(capabilities),
                'generation_parameters': deepcopy(parameters)}
    except _INVALID as exc:
        raise AgentError('生成规格或参数证据无效，不能推断参数已生效') from exc


def verify_host_parameters(packet: dict, attempt: dict, image: bytes) -> dict:
    request = attempt['request']
    return _parameters(packet, request.get('generation_spec'), request.get('capability_snapshot'),
                       attempt.get('generation_parameters'), prompt=request['prompt'],
                       design=request['solution']['design'], inputs=request['generation_inputs'],
                       image=image, output=attempt['output'], actual_arguments=request['arguments'])


def verify_api_parameters(packet: dict, snapshot: dict, row: dict, image: bytes) -> dict:
    generation, request = snapshot['generation'], snapshot['request']
    spec = generation.get('generation_spec')
    if request.get('generation_spec') != spec:
        raise AgentError('API生成请求与生成快照的规格不一致')
    legacy_adapter = generation.get('spec_compatibility') == 'legacy_adapter_without_capabilities'
    return _parameters(packet, spec, generation.get('capability_snapshot'), row.get('generation_parameters'),
                       prompt=request['prompt'], design=snapshot['solution']['design'],
                       inputs=generation['inputs'], image=image, output=row['output'],
                       legacy_adapter=legacy_adapter)


def _context(run: Path, packet: dict, contract: dict) -> None:
    from .workflow import read_packet
    if read_packet(run) != packet:
        raise AgentError('复查任务包与已保存的原任务不一致')
    if (contract.get('digest') != digest({k: v for k, v in contract.items() if k != 'digest'})
            or read_json(run/'acceptance.json') != contract):
        raise AgentError('复查验收标准与冻结记录不一致')


def _solution(packet: dict, contract: dict, raw: dict) -> None:
    from .workflow import validate_solution
    solution = Solution.model_validate(raw)
    validate_solution(packet, solution)
    if acceptance(packet, solution.design) != contract:
        raise AgentError('生成前方案与冻结验收标准不一致')


def _bytes(run: Path, entry: dict) -> bytes:
    data = within(run, entry['file']).read_bytes()
    if sha(data) != entry['sha256']:
        raise AgentError('生成输入或候选文件与原调用摘要不一致')
    return data


def _record(route: str, packet: dict, contract: dict, image: bytes, request_id: str,
            prompt: str, solution: dict, inputs: list[dict], authority: str, **source) -> dict:
    if not request_id or not isinstance(prompt, str) or not prompt.strip():
        raise AgentError('原生成请求缺少request_id或实际提示词')
    if sha(image) in [entry['sha256'] for entry in inputs]:
        raise AgentError('输入原图不能作为新的生成候选')
    body = {'format': FORMAT, 'status': 'verified', 'route': route,
            'request_id': request_id, 'actual_prompt': prompt,
            'solution': deepcopy(solution), 'ordered_inputs': deepcopy(inputs),
            'candidate_sha256': sha(image), 'packet_digest': packet['digest'],
            'contract_digest': contract['digest'], 'authority': authority, **source,
            'notice': 'verified只表示本地调用证据一致；输入图已提交不证明风格已被采用，'
                      '提示词存在不证明模型正确执行，机器诊断不代表用户接受。'}
    return {**body, 'digest': digest(body)}


def for_host(run: Path, packet: dict, contract: dict, image: bytes) -> dict:
    """Bind one completed host receipt to its original request, without writes."""
    from .host import read_dispatches
    from .run_status import _verify_attempt
    run = Path(run)
    try:
        _context(run, packet, contract)
        attempts = read_dispatches(run)
        if len({a['request']['id'] for a in attempts}) != len(attempts):
            raise AgentError('宿主调用request_id重复，不能确定原生成请求')
        matches = [(index, attempt) for index, attempt in enumerate(attempts)
                   if attempt['status'] == 'completed'
                   and attempt.get('output', {}).get('sha256') == sha(image)]
        if len(matches) != 1:
            raise AgentError('候选图须对应唯一已完成的宿主调用')
        index, attempt = matches[0]
        _verify_attempt(packet, run, contract, attempt)
        request = attempt['request']
        inputs = request['images_in_order']
        arguments = request['arguments']
        if request['prompt'] != arguments.get('prompt'):
            raise AgentError('生成前提示词与实际工具参数不一致')
        expected_paths = [str(within(run, entry['file'])) for entry in inputs]
        from .pippit_connector import TOOL_NAME as PIPPIT_TOOL_NAME
        image_key = 'images' if request['tool'] == PIPPIT_TOOL_NAME else 'referenced_image_paths'
        other_key = 'referenced_image_paths' if image_key == 'images' else 'images'
        if other_key in arguments or arguments.get(image_key, []) != expected_paths:
            raise AgentError('实际工具图片参数与原输入顺序不一致')
        refs = {r['id']: r for r in packet['references']}
        for entry in inputs:
            if entry['id'] != 'PREVIOUS_CANDIDATE':
                if within(run, entry['file']) != within(run, refs[entry['id']]['file']):
                    raise AgentError('宿主输入文件与原参考记录不一致')
                continue
            previous = [prior for prior in attempts[:index]
                        if prior['status'] == 'completed'
                        and prior.get('output', {}).get('sha256') == entry['sha256']]
            if len(previous) != 1:
                raise AgentError('编辑画布缺少唯一的先前已完成生成调用')
            _verify_attempt(packet, run, contract, previous[0])
            if within(run, entry['file']) != within(run, previous[0]['output']['file']):
                raise AgentError('编辑画布文件与先前生成结果不一致')
        # _verify_attempt checks both the actual receipt and all input/output bytes.
        if _bytes(run, attempt['output']) != image:
            raise AgentError('候选字节与宿主已完成调用不一致')
        return _record('host', packet, contract, image, request['id'], request['prompt'],
                       request['solution'], inputs, 'host_reported_not_api_verified',
                       request_digest=attempt['request_digest'],
                       actual_arguments_digest=attempt['actual_arguments']['digest'],
                       actual_arguments_file_sha256=attempt['actual_arguments']['file_sha256'],
                       target=request['target'], tool=request['tool'],
                       **verify_host_parameters(packet, attempt, image))
    except _INVALID as exc:
        raise AgentError('宿主生成证据缺失或损坏，不能确定执行层原因') from exc


def for_api(run: Path, packet: dict, contract: dict, image: bytes,
            request_id: str | None = None) -> dict:
    """Use the durable completed API request, never the current solution file."""
    from .api_recovery import _verified, load_api_state
    run = Path(run)
    try:
        _context(run, packet, contract)
        state = load_api_state(run)
        completed = [row for row in state['requests']
                     if row['state'] == 'completed' and row['phase'] == 'generate'
                     and row['kind'] == 'image' and row['output']['type'] == 'image']
        matches = [row for row in completed if row['output']['sha256'] == sha(image)
                   and (request_id is None or row['id'] == request_id)]
        if len(matches) != 1:
            raise AgentError('候选图须对应唯一已完成的API生成请求')
        row = matches[0]
        snapshot = _verified(within(run, row['snapshot']))
        if snapshot['packet'] != packet:
            raise AgentError('API生成快照与原任务包不一致')
        _solution(packet, contract, snapshot['solution'])
        request = snapshot['request']
        inputs = snapshot['generation']['inputs']
        validate_generation_inputs(packet, contract['baseline_reference_ids'], inputs)
        if request['image_sha256'] != [entry['sha256'] for entry in inputs]:
            raise AgentError('API实际图片参数与生成前输入顺序不一致')
        if _bytes(run, row['output']) != image:
            raise AgentError('候选字节与API已完成调用不一致')
        refs = {r['id']: r for r in packet['references']}
        ordered = []
        for entry in inputs:
            if entry['id'] == 'PREVIOUS_CANDIDATE':
                previous = [prior for prior in completed if prior['round'] < row['round']
                            and prior['output']['sha256'] == entry['sha256']]
                if len(previous) != 1:
                    raise AgentError('API编辑画布缺少唯一的先前已完成生成调用')
                prior = previous[0]
                prior_snapshot = _verified(within(run, prior['snapshot']))
                if prior_snapshot['packet'] != packet:
                    raise AgentError('API编辑画布来自其它任务包')
                _solution(packet, contract, prior_snapshot['solution'])
                source = prior['output']
                item = {**entry, 'file': source['file'], 'source_request_id': prior['id']}
            else:
                source = refs[entry['id']]
                item = {**entry, 'file': source['file']}
            _bytes(run, item)
            ordered.append(item)
        return _record('api', packet, contract, image, row['id'], request['prompt'],
                       snapshot['solution'], ordered, 'api_image_bytes',
                       snapshot_digest=snapshot['digest'],
                       request_digest=digest(request), model_id=snapshot['generation']['model_id'],
                       **verify_api_parameters(packet, snapshot, row, image))
    except _INVALID as exc:
        raise AgentError('API生成证据缺失或损坏，不能确定执行层原因') from exc
