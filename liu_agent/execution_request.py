"""One deterministic input builder shared by preview and dispatch; no reservation."""
from pathlib import Path
from .io import AgentError, sha, within
from .workflow import compile_texts, generation_references
from .revision import revision_prompt


def build_input(run: Path, packet: dict, solution, target: str, tool: str, *,
                provider_options: dict | None = None, prior=None, review=None) -> dict:
    adaptive = packet.get('model_adaptation_version') == 1
    completed = prior is not None
    from .comfy_connector import TOOL_TARGETS
    from .generation_spec import validate_tool_target
    validate_tool_target(tool, target)
    if provider_options is not None and tool not in {'pippit.generate-image.seedream-5-pro', *TOOL_TARGETS}:
        raise AgentError('provider_options 仅用于已接入的小云雀或本地 ComfyUI 连接器')
    compiled = compile_texts(packet, solution.spec, solution.compilation, design=solution.design)
    if target not in compiled:
        raise AgentError('目标未包含在此方案编译中')
    refs, _ = generation_references(packet, run)
    from .host_references import research_references
    for ref in research_references(packet):
        finding = next((f for f in packet.get('reference_analysis', {}).get('findings', []) if f['id'] == ref['id']), None)
        if not finding or not finding['seen']:
            raise AgentError('还有参考未实际看图；不可开始生成')
    if any(r['rights'] != 'user_authorized' for r in refs):
        raise AgentError('生成输入图尚未获得上传授权')
    images = [{'id': r['id'], 'file': r['file'], 'sha256': r['sha256'], 'role': r['role'],
               'note': r['note']} for r in refs]
    # The shared gate binds the design's baseline ids to the actual files
    # that will be submitted.  It also rejects evaluation-only references.
    prompt = compiled[target]['text']
    if completed:
        images.append({'id': 'PREVIOUS_CANDIDATE', 'file': prior['output']['file'],
                       'sha256': prior['output']['sha256'], 'role': 'edit_target',
                       'note': '上一轮候选，仅作局部编辑目标，不新增身份事实'})
        if not adaptive:
            prompt = revision_prompt(prompt, review)
    if images and not adaptive:
        prompt += '\n输入图片分工：' + '；'.join(
            f"图{i + 1}={image['id']}（{image['role']}）：{image['note']}"
            for i, image in enumerate(images)
        )
    from .reference_gate import validate_generation_inputs
    baseline_ids = list(solution.design.baseline_reference_ids)
    reference_usage = validate_generation_inputs(packet, baseline_ids, images)
    for image in images:
        if sha(within(run,image['file']).read_bytes()) != image['sha256']:
            raise AgentError('生成输入文件已改变，不能使用旧调用记录')
    arguments = {'prompt': prompt}
    if tool in {'image_gen.imagegen', 'image_gen__imagegen'}:
        # Freeze the supported tool default before calling it. Receipts must
        # remain exact; do not normalize extra arguments after execution.
        arguments['transparent_background'] = False
    if images:
        arguments['referenced_image_paths'] = [str(within(run, image['file']).resolve()) for image in images]
    if tool == 'pippit.generate-image.seedream-5-pro':
        from .pippit_connector import build_arguments
        from .workflow import generation_settings
        arguments = build_arguments(run=run, target=target, prompt=prompt,
                                    images=images, provider_options=provider_options,
                                    desired_aspect_ratio=generation_settings(packet, solution.design)['desired_aspect_ratio'])
    elif tool in TOOL_TARGETS:
        from .comfy_connector import build_arguments
        from .workflow import generation_settings
        settings = generation_settings(packet, solution.design)
        arguments = build_arguments(run=run, target=target, prompt=prompt,
                                    images=images, provider_options=provider_options,
                                    desired_aspect_ratio=settings['desired_aspect_ratio'], tool=tool)
        if settings['requested_size'] not in (None, 'auto', f"{arguments['width']}x{arguments['height']}"):
            raise AgentError('本地工作流尺寸与已选方案不一致；不能静默改变尺寸')
        if settings['quality'] != 'auto' or settings['output_format'] != 'png':
            raise AgentError('当前本地工作流只支持 quality=auto 和 PNG；不能静默忽略设置')
    result = {'prompt': prompt, 'arguments': arguments, 'images_in_order': images,
              'reference_usage': reference_usage,
              'generation_inputs': [{'id': x['id'], 'role': x['role'], 'sha256': x['sha256']} for x in images]}
    if packet.get('reference_policy') is not None:
        from .generation_spec import host_capabilities
        from .workflow import prepare_generation_spec
        capabilities = host_capabilities(tool)
        spec = prepare_generation_spec(packet, solution, prompt, images, capabilities,
                                       'revision' if completed else 'primary', target=target,
                                       resolved_size=(f"{arguments['width']}x{arguments['height']}"
                                                      if tool in TOOL_TARGETS else None))
        result.update(generation_spec=spec.model_dump(), capability_snapshot=capabilities)
    # An already frozen car input is an immutable contract. Retain an omitted
    # default only when its complete, digest-verified input otherwise matches.
    # New freezes keep the explicit default; no receipt is normalized here.
    from .visual_decision import policy
    from .car_photography import FROZEN_INPUT, PROFILE
    if (tool in {'image_gen.imagegen', 'image_gen__imagegen'} and
            (policy(packet) or {}).get('profile') == PROFILE and
            (run / FROZEN_INPUT).exists()):
        from copy import deepcopy
        from .io import read_json, digest
        frozen = read_json(run / FROZEN_INPUT)
        legacy = deepcopy(result)
        legacy['arguments'].pop('transparent_background')
        expected = {'packet_digest': packet['digest'],
                    'solution_digest': digest(solution.model_dump()),
                    'target': target, 'tool': tool, 'input': legacy}
        if (isinstance(frozen, dict) and
                frozen.get('format') == 'cl-hex.prepared-request.v1' and
                frozen.get('digest') == digest({k: v for k, v in frozen.items() if k != 'digest'}) and
                all(frozen.get(k) == v for k, v in expected.items())):
            return legacy
    return result
