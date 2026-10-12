"""Fixed local text workflows and native single-parent instruction editing."""
from __future__ import annotations

from fractions import Fraction
from math import sqrt
from pathlib import Path
from urllib.parse import urlsplit

from .generation_spec import image_facts
from .io import AgentError, digest, within


EDIT_TOOL = 'comfyui.flux-klein-edit'
QWEN_EDIT_TOOL = 'comfyui.qwen-image-edit'
QWEN_MASK_TOOL = 'comfyui.qwen-image-masked-edit'
EDIT_TOOLS = frozenset((EDIT_TOOL, QWEN_EDIT_TOOL, QWEN_MASK_TOOL))
TOOL_TARGETS = {'comfyui.flux-klein': 'flux-klein', 'comfyui.qwen-image': 'qwen-image',
                EDIT_TOOL: 'flux-klein', QWEN_EDIT_TOOL: 'qwen-image', QWEN_MASK_TOOL: 'qwen-image'}
MODELS = {
    'flux-klein': ('Flux2_Klein\\flux-2-klein-9b-fp8.safetensors',
                   'Flux2_Klein\\qwen_3_8b_fp8mixed.safetensors', 'flux2-vae.safetensors'),
    'qwen-image': ('qwen_image_2.1_int8_convrot.safetensors',
                   'qwen3vl_8b_int8_convrot.safetensors', 'qwen_image_2.1_vae_bf16.safetensors'),
}
SIZES = (512, 768, 1024)


def local_endpoint(value: str) -> str:
    if not isinstance(value, str):
        raise AgentError('ComfyUI endpoint 必须是字符串')
    if value != value.strip() or any(ord(char) < 32 or ord(char) == 127 for char in value):
        raise AgentError('ComfyUI endpoint 不允许空白或控制字符')
    try:
        url = urlsplit(value)
        port = url.port
    except (ValueError, TypeError) as exc:
        raise AgentError('ComfyUI endpoint 无效') from exc
    if (url.scheme != 'http' or url.hostname not in ('127.0.0.1', 'localhost', '::1')
            or url.username or url.password or url.query or url.fragment
            or url.path not in ('', '/') or not port or not 1 <= port <= 65535):
        raise AgentError('ComfyUI 只允许明确端口的本地回环 HTTP endpoint')
    return value.rstrip('/')


def capabilities(tool: str) -> dict:
    if tool not in TOOL_TARGETS:
        raise AgentError('未知 ComfyUI 工具')
    target = TOOL_TARGETS[tool]
    sizes = [f'{w}x{h}' for w in SIZES for h in SIZES if target == 'flux-klein' or w == h]
    body = {
        'provider_id': 'comfyui_local', 'model_id': MODELS[target][0], 'model_known': True,
        'checked_at': '2026-09-29', 'source_type': 'local_nodes_and_fixed_adapter',
        'sources': ['liu_agent/comfy_connector.py', 'ComfyUI 0.37.2 /object_info'],
        'account_access': 'verify_before_execution', 'operations': ['generation'],
        'reference_images': {'support': 'not_supported', 'max_images': 0},
        'parameters': {
            'size': {'support': 'supported', 'control': 'controlled', 'values': sizes, 'custom_size': False},
            'quality': {'support': 'supported', 'control': 'controlled', 'values': ['auto']},
            'output_format': {'support': 'supported', 'control': 'controlled', 'values': ['png']},
        },
    }
    if tool == EDIT_TOOL:
        body.update(operations=['edit'], reference_images={
            'support': 'supported', 'ordered': True, 'max_images': 1,
            'roles': ['identity', 'edit_target', 'edit_canvas'],
            'visual_adherence': 'not_verified', 'size_policy': 'preserve_input_pixels',
        })
        body['sources'] = body['sources'] + [
            'https://github.com/Comfy-Org/workflow_templates/blob/main/templates/image_flux2_klein_image_edit_9b_distilled.json']
    if tool in (QWEN_EDIT_TOOL, QWEN_MASK_TOOL):
        body.update(checked_at='2026-10-07', operations=['edit'], reference_images={
            'support': 'supported', 'ordered': True, 'max_images': 1,
            'roles': ['identity', 'edit_target', 'edit_canvas'],
            'visual_adherence': 'not_verified', 'size_policy': 'first_reference_processed_size',
            'reference_resolution': [0, 512, 768, 1024],
        })
        body['parameters']['size'].update(values=[], custom_size=True, constraints={
            'edge_multiple': 32, 'max_edge': 4096, 'max_aspect_ratio': 128,
            'min_pixels': 32 * 32, 'max_pixels': 4096 * 4096,
        })
        body['sources'].append('ComfyUI comfy_extras/nodes_qwen.py TextEncodeQwenImage21; native autogrow images.image_1')
    if tool == QWEN_MASK_TOOL:
        body['region_control']={'mask':'binary_sampling_and_original_pixel_composite','pose_condition':'not_supported',
            'size_policy':'original_aligned_canvas_no_resize','output_verification':'exact_outside_mask_rgb_pixels',
            'visual_adherence':'not_verified'}
        body['region_control']['optional_composition']={'alpha':'independent_single_weight','hard_protection':'white_keeps_parent_priority',
            'raw_decode':'saved_separately_same_sampling','required_pair':['composite_alpha','protection_mask']}
        body['sources'].append('ComfyUI native VAEEncode / SetLatentNoiseMask / ImageCompositeMasked; output pixel check')
    return {**body, 'snapshot_id': 'liu-capability-' + digest(body)}


def _node(kind: str, **inputs) -> dict:
    return {'class_type': kind, 'inputs': inputs}


def build_arguments(*, run: Path, target: str, prompt: str, images: list,
                    provider_options: dict | None, desired_aspect_ratio: str = 'auto',
                    tool: str | None = None) -> dict:
    if target not in MODELS:
        raise AgentError('本地工作流目标只支持 flux-klein 或 qwen-image')
    if tool is not None and TOOL_TARGETS.get(tool) != target:
        raise AgentError('本地工具与目标模型不一致')
    editing = tool in EDIT_TOOLS
    qwen_edit = tool in (QWEN_EDIT_TOOL, QWEN_MASK_TOOL)
    masking = tool == QWEN_MASK_TOOL
    if images and not editing:
        raise AgentError('当前本地连接器仅纯文生图，不支持参考图或编辑，不能静默丢弃输入')
    if not isinstance(prompt, str) or not prompt.strip():
        raise AgentError('本地生图需要已编译的完整提示词')
    if not isinstance(provider_options, dict):
        raise AgentError('本地生图必须提供 endpoint、width、height、seed 配置')
    options = provider_options
    allowed_options = {'endpoint', 'width', 'height', 'seed', 'steps', 'cfg'}
    if qwen_edit:
        allowed_options.add('reference_resolution')
    if masking:
        allowed_options.add('edit_mask')
        allowed_options.update(('composite_alpha','protection_mask'))
    if set(options) - allowed_options:
        raise AgentError('本地配置含未支持参数；不允许任意工作流或模型替换')
    if not {'endpoint', 'width', 'height', 'seed'} <= set(options):
        raise AgentError('本地配置缺少 endpoint、width、height 或 seed')
    endpoint = local_endpoint(options['endpoint'])
    width, height, seed = (options[k] for k in ('width', 'height', 'seed'))
    if qwen_edit and any(type(v) is not int or not 32 <= v <= 4096 or v % 32 for v in (width, height)):
        raise AgentError('Qwen 编辑输出尺寸必须为32的倍数且处于32至4096；须匹配父图实际处理尺寸')
    if not qwen_edit and any(type(v) is not int or v not in SIZES for v in (width, height)):
        raise AgentError('本地测试尺寸限 512、768 或 1024 像素')
    if target == 'qwen-image' and not qwen_edit and width != height:
        raise AgentError('当前 Qwen 2.1 最小工作流仅支持方图')
    resolution = options.get('reference_resolution')
    if qwen_edit and (type(resolution) is not int or resolution not in (0, 512, 768, 1024)):
        raise AgentError('Qwen 编辑必须显式冻结 reference_resolution=0、512、768或1024')
    if type(seed) is not int or not 0 <= seed <= 2**64 - 1:
        raise AgentError('seed 必须是 0 至 2^64-1 的整数')
    if desired_aspect_ratio != 'auto':
        try:
            ratio = Fraction(desired_aspect_ratio.replace(':', '/'))
        except (ValueError, ZeroDivisionError, AttributeError) as exc:
            raise AgentError('无效画幅比例') from exc
        if ratio != Fraction(width, height):
            raise AgentError('本地尺寸与本题画幅比例冲突，不自动改画幅')
    default_steps = 4 if target == 'flux-klein' else 25
    steps = options.get('steps', default_steps)
    allowed_steps = (4,) if target == 'flux-klein' else (25, 40, 50)
    if type(steps) is not int or steps not in allowed_steps:
        raise AgentError('Klein 蒸馏路线固定 4 步；Qwen 2.1 允许 25、40 或 50 步')
    cfg = options.get('cfg', 1.0)
    if type(cfg) not in (float, int) or cfg != 1:
        raise AgentError('当前固定工作流 CFG 必须为 1，不支持负向提示路线')
    image_input = _image_input(run, images, width, height, resolution=resolution) if editing else None
    mask_image = None
    composition = {}
    if masking:
        if resolution!=0 or (image_input['width'],image_input['height'])!=(width,height):
            raise AgentError('蒙版编辑只支持原始32对齐画布和reference_resolution=0，不缩放保护区')
        from .comfy_mask import mask_input
        mask_image=mask_input(run,options.get('edit_mask'),width,height)
        from .comfy_mask import composition_controls
        composition=composition_controls(run,options,mask_image,width,height)
    unet, clip, vae = MODELS[target]
    graph = {
        '1': _node('UNETLoader', unet_name=unet, weight_dtype='default'),
        '2': _node('CLIPLoader', clip_name=clip, type='flux2' if target == 'flux-klein' else 'qwen_image', device='default'),
        '3': _node('VAELoader', vae_name=vae),
    }
    if target == 'flux-klein':
        graph.update({
            '4': _node('CLIPTextEncode', clip=['2', 0], text=prompt),
            '5': _node('BasicGuider', model=['1', 0], conditioning=['4', 0]),
            '6': _node('EmptyFlux2LatentImage', width=width, height=height, batch_size=1),
            '7': _node('KSamplerSelect', sampler_name='euler'),
            '8': _node('SamplerCustomAdvanced', noise=['9', 0], guider=['5', 0], sampler=['7', 0], sigmas=['10', 0], latent_image=['6', 0]),
            '9': _node('RandomNoise', noise_seed=seed),
            '10': _node('Flux2Scheduler', steps=steps, width=width, height=height),
        })
        if image_input is not None:
            graph.update({
                '13': _node('LoadImage', image=image_input['subfolder'] + '/' + image_input['local_upload_name']),
                '14': _node('VAEEncode', pixels=['13', 0], vae=['3', 0]),
                '15': _node('ReferenceLatent', conditioning=['4', 0], latent=['14', 0]),
                '16': _node('ConditioningZeroOut', conditioning=['4', 0]),
                '17': _node('ReferenceLatent', conditioning=['16', 0], latent=['14', 0]),
                '5': _node('CFGGuider', model=['1', 0], positive=['15', 0], negative=['17', 0], cfg=1.0),
            })
    else:
        graph.update({
            '4': _node('TextEncodeQwenImage21', clip=['2', 0], prompt=prompt, negative_prompt='', resolution=width),
            '8': _node('KSampler', model=['1', 0], seed=seed, steps=steps, cfg=1.0,
                       sampler_name='euler', scheduler='simple', positive=['4', 0], negative=['4', 1], latent_image=['4', 2], denoise=1.0),
        })
        if qwen_edit:
            graph['13'] = _node('LoadImage', image=image_input['subfolder'] + '/' + image_input['local_upload_name'])
            graph['4']['inputs'].update(vae=['3', 0], resolution=resolution,
                                       **{'images.image_1': ['13', 0]})
    graph['11'] = _node('VAEDecode', samples=['8', 0], vae=['3', 0])
    graph['12'] = _node('SaveImage', images=['11', 0], filename_prefix='CL-HEX/' + target)
    if masking:
        graph.update({
            '20':_node('LoadImage',image=mask_image['subfolder']+'/'+mask_image['local_upload_name']),
            '21':_node('ImageToMask',image=['20',0],channel='red'),
            '22':_node('VAEEncode',pixels=['13',0],vae=['3',0]),
            '23':_node('SetLatentNoiseMask',samples=['22',0],mask=['21',0]),
            '24':_node('ImageCompositeMasked',destination=['13',0],source=['11',0],x=0,y=0,resize_source=False,mask=['21',0]),
        })
        graph['8']['inputs']['latent_image']=['23',0]
        graph['12']['inputs']['images']=['24',0]
        if composition:
            alpha,protected=composition['alpha_image'],composition['protection_image']
            graph.update({
                '25':_node('LoadImage',image=alpha['subfolder']+'/'+alpha['local_upload_name']),
                '26':_node('ImageToMask',image=['25',0],channel='red'),
                '27':_node('LoadImage',image=protected['subfolder']+'/'+protected['local_upload_name']),
                '28':_node('ImageToMask',image=['27',0],channel='red'),
                '29':_node('InvertMask',mask=['28',0]),
                '30':_node('MaskComposite',destination=['26',0],source=['29',0],x=0,y=0,operation='multiply'),
                '33':_node('SaveImage',images=['11',0],filename_prefix='CL-HEX/qwen-image-rawdecode'),
            })
            graph['24']['inputs']['mask']=['30',0]
    result = {'endpoint': endpoint, 'target': target, 'prompt': prompt, 'width': width,
              'height': height, 'seed': seed, 'steps': steps, 'cfg': float(cfg),
              'workflow': graph, 'output_node': '12', 'output_format': 'png'}
    if image_input is not None:
        result.update(input_image=image_input,
                      referenced_image_paths=[str(within(run, image_input['file']).resolve())])
    if qwen_edit:
        original_size = [image_input['width'], image_input['height']]
        result.update(reference_resolution=resolution, reference_processing={
            'node': 'TextEncodeQwenImage21', 'image_input': 'images.image_1',
            'original_size': original_size, 'processed_size': [width, height],
            'resolution': resolution, 'round_to_multiple': 32,
            'resampling': 'none' if original_size == [width, height] else 'lanczos',
            'scope': 'native reference preprocessing and output latent; no pixel or identity preservation guarantee',
        })
    if masking:
        result.update(edit_mask=dict(options['edit_mask']),mask_image=mask_image)
        result['reference_processing']['scope']='original canvas; binary sampling mask and exact outside-mask RGB verification; visual effect unverified'
        if composition:result.update(composition)
    return result


def _image_input(run: Path, images: list, width: int, height: int, *, resolution: int | None = None) -> dict:
    if not isinstance(images, list) or len(images) != 1 or not isinstance(images[0], dict):
        raise AgentError('本地编辑必须有且只有一张已登记父图')
    image = images[0]
    if (image.get('role') not in ('identity', 'edit_target', 'edit_canvas')
            or not isinstance(image.get('id'), str) or not image['id'].strip()):
        raise AgentError('本地编辑父图角色或标识无效')
    try:
        path = within(run, image['file'])
        facts = image_facts(path.read_bytes())
    except (KeyError, TypeError, OSError) as exc:
        raise AgentError('本地编辑父图文件无效') from exc
    if image.get('sha256') != facts['sha256']:
        raise AgentError('本地编辑父图在冻结后发生变化；不能提交')
    if facts['output_format'] != 'png':
        raise AgentError('本地编辑父图必须为PNG')
    if resolution is None and (facts['width'], facts['height']) != (width, height):
        raise AgentError('本地编辑父图必须为PNG且尺寸与输出完全一致；不自动缩放')
    if resolution is not None:
        w, h = facts['width'], facts['height']
        ratio = w / h
        processed = (max(32, round(sqrt(resolution ** 2 * ratio) / 32) * 32),
                     max(32, round(sqrt(resolution ** 2 / ratio) / 32) * 32)) if resolution else (
                         max(32, round(w / 32) * 32), max(32, round(h / 32) * 32))
        if processed != (width, height):
            raise AgentError('Qwen 编辑声明输出尺寸与父图原尺寸及reference_resolution的实际处理尺寸不一致')
    return {**{key: image[key] for key in ('id', 'file', 'sha256', 'role')},
            'width': facts['width'], 'height': facts['height'],
            'local_upload_name': 'hex-reference-' + facts['sha256'] + '.png',
            'subfolder': 'CL-HEX/references'}


def validate_arguments(arguments: dict, target: str, *, tool: str | None = None,
                       run: Path | None = None) -> None:
    """Rebuild only for integrity comparison, never replace frozen arguments."""
    try:
        images = [arguments['input_image']] if tool in EDIT_TOOLS else []
        keys = ('endpoint', 'width', 'height', 'seed', 'steps', 'cfg')
        if tool in (QWEN_EDIT_TOOL, QWEN_MASK_TOOL):
            keys += ('reference_resolution',)
        if tool == QWEN_MASK_TOOL:
            keys += ('edit_mask',)
            if 'composite_alpha' in arguments or 'protection_mask' in arguments:
                keys += ('composite_alpha','protection_mask')
        rebuilt = build_arguments(run=run or Path('.'), target=target, prompt=arguments['prompt'], images=images, tool=tool,
                                  provider_options={k: arguments[k] for k in keys})
    except (KeyError, TypeError) as exc:
        raise AgentError('冻结 ComfyUI 实参结构无效') from exc
    if rebuilt != arguments:
        raise AgentError('冻结 ComfyUI workflow 与已注册模型或实参不一致')


def _required_input_present(node: dict, name: str, schema) -> bool:
    if name in node['inputs']:
        return True
    # The registered Qwen node flattens its autogrow group into images.image_1.
    # A text-only workflow may omit it only when the live template permits zero.
    if node['class_type'] != 'TextEncodeQwenImage21' or name != 'images':
        return False
    try:
        if not isinstance(schema, list) or schema[0] != 'COMFY_AUTOGROW_V3':
            return False
        template = schema[1]['template']
        names, minimum = template['names'], template['min']
        required = template['input']['required']
        supplied = [key[len(name) + 1:] for key in node['inputs'] if key.startswith(name + '.')]
        return (isinstance(names, list) and all(isinstance(key, str) and key for key in names)
                and len(set(names)) == len(names) and type(minimum) is int
                and 0 <= minimum <= len(names) and set(required) == {'image'}
                and required['image'][0] == 'IMAGE'
                and len(supplied) >= minimum and all(key in names for key in supplied))
    except (KeyError, TypeError, IndexError):
        return False


def validate_live_nodes(arguments: dict, node_info: dict, *, before_upload: bool = False) -> None:
    """Read-only presence/schema check; does not load models or prove generation."""
    if not isinstance(node_info, dict):
        raise AgentError('本地节点信息必须是对象')
    if 'mask_image' in arguments:
        required={'VAEEncode':({'pixels':'IMAGE','vae':'VAE'},'LATENT'),
                  'SetLatentNoiseMask':({'samples':'LATENT','mask':'MASK'},'LATENT'),
                  'ImageToMask':({'image':'IMAGE','channel':'COMBO'},'MASK'),
                  'ImageCompositeMasked':({'destination':'IMAGE','source':'IMAGE','mask':'MASK'},'IMAGE')}
        if 'alpha_image' in arguments:
            required.update({'InvertMask':({'mask':'MASK'},'MASK'),
                'MaskComposite':({'destination':'MASK','source':'MASK','x':'INT','y':'INT','operation':'COMBO'},'MASK')})
        for name,(fields,output) in required.items():
            try:
                info=node_info[name];schema={**info['input'].get('required',{}),**info['input'].get('optional',{})}
                valid=info['output']==[output] and all(schema[k][0]==kind for k,kind in fields.items())
            except (KeyError,TypeError,IndexError):valid=False
            if not valid:raise AgentError('本地蒙版节点接口变化；上传前停止：'+name)
    if arguments.get('target') == 'qwen-image' and 'input_image' in arguments:
        try:
            info = node_info['TextEncodeQwenImage21']
            inputs = info['input']
            image_schema = inputs['required']['images']
            template = image_schema[1]['template']
            valid = (image_schema[0] == 'COMFY_AUTOGROW_V3'
                     and 'image_1' in template['names']
                     and template['input']['required']['image'][0] == 'IMAGE'
                     and inputs['optional']['vae'][0] == 'VAE'
                     and info['output'] == ['CONDITIONING', 'CONDITIONING', 'LATENT'])
        except (KeyError, TypeError, IndexError):
            valid = False
        if not valid:
            raise AgentError('本地 Qwen 2.1 单父图参考节点接口已变化；上传前停止，不降级为纯文生图')
    for node in arguments['workflow'].values():
        info = node_info.get(node['class_type'])
        if not isinstance(info, dict):
            raise AgentError('本地服务缺少工作流所需节点：' + node['class_type'])
        inputs = info.get('input', {})
        if not isinstance(inputs, dict):
            raise AgentError('本地节点输入结构无效：' + node['class_type'])
        for group in ('required', 'optional'):
            fields = inputs.get(group, {})
            if not isinstance(fields, dict):
                raise AgentError('本地节点字段结构无效：' + node['class_type'])
            if group == 'required':
                for name, schema in fields.items():
                    if not _required_input_present(node, name, schema):
                        raise AgentError('本地节点新增或缺少必填输入；尚未上传或提交：'
                                         + node['class_type'] + '.' + name)
            for name, value in node['inputs'].items():
                if before_upload and node['class_type'] == 'LoadImage' and name == 'image':
                    continue
                if name not in fields:
                    continue
                schema = fields[name]
                if not isinstance(schema, list) or not schema or not isinstance(schema[0], (str, list)):
                    raise AgentError('本地节点参数结构无效：' + node['class_type'] + '.' + name)
                if isinstance(schema[0], list) and value not in schema[0]:
                    raise AgentError('本地节点缺少冻结模型或参数：' + node['class_type'] + '.' + name)
