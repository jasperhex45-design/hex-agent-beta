"""Binary edit-region inputs and exact protected-pixel checks, never pose judging."""
import io
from pathlib import Path
from PIL import Image, ImageChops, ImageOps
from .generation_spec import image_facts
from .io import AgentError, within

def mask_input(run: Path, descriptor: dict, width: int, height: int, *, binary=True,
               ident='EDIT_MASK', role='sampling_and_composite_mask') -> dict:
    if not isinstance(descriptor,dict) or set(descriptor)!={'file','sha256'}:
        raise AgentError('编辑蒙版须明确绑定本任务内PNG文件与SHA256')
    try:
        raw=within(run,descriptor['file']).read_bytes(); facts=image_facts(raw)
        with Image.open(io.BytesIO(raw)) as image:
            if image.mode not in ('RGB','L') or image.size!=(width,height):
                raise AgentError('蒙版必须为不透明RGB或灰度PNG，与原图同画布，不缩放')
            channels=image.convert('RGB').split()
            if any(ImageChops.difference(channels[0],other).getbbox() is not None for other in channels[1:]):
                raise AgentError('蒙版三个颜色通道必须一致')
            values=set(channels[0].tobytes())
            if binary and values!={0,255}:
                raise AgentError('蒙版须有黑色保护区和白色编辑区，只支持二值边界')
            if not binary and (not {0,255}<=values or not values-{0,255}):
                raise AgentError('独立合成alpha须有透明、完整核心及软过渡，不允许全空或全画布')
    except (OSError,ValueError,TypeError,KeyError) as exc:
        raise AgentError('蒙版文件无效或超出任务路径') from exc
    if facts['output_format']!='png' or facts['sha256']!=descriptor['sha256']:
        raise AgentError('蒙版格式或冻结SHA256不一致')
    return {'id':ident,'role':role,**descriptor,'width':width,'height':height,
        'local_upload_name':'hex-mask-'+facts['sha256']+'.png','subfolder':'CL-HEX/controls'}

def composition_controls(run: Path, options: dict, sampling: dict, width: int, height: int) -> dict:
    pair={'composite_alpha','protection_mask'}
    if not pair.intersection(options):return {}
    if not pair<=options.keys():raise AgentError('软合成alpha和独立硬保护蒙版必须共同冻结')
    alpha=mask_input(run,options['composite_alpha'],width,height,binary=False,ident='COMPOSITE_ALPHA',role='single_final_composition_alpha')
    protected=mask_input(run,options['protection_mask'],width,height,ident='PROTECTION_MASK',role='hard_protection_white_keeps_parent')
    with Image.open(within(run,alpha['file'])) as src:a=src.convert('L')
    with Image.open(within(run,protected['file'])) as src:p=src.convert('L')
    with Image.open(within(run,sampling['file'])) as src:s=src.convert('L')
    effective=ImageChops.multiply(a,ImageOps.invert(p))
    if effective.getbbox() is None or ImageChops.multiply(effective,ImageOps.invert(s)).getbbox() is not None:
        raise AgentError('硬保护优先后的合成alpha为空或超出冻结采样范围')
    return {'composite_alpha':dict(options['composite_alpha']),'protection_mask':dict(options['protection_mask']),
        'alpha_image':alpha,'protection_image':protected,'decoded_output_node':'33'}

def protected_pixels(run: Path, arguments: dict, output: bytes) -> dict:
    with Image.open(within(run,arguments['input_image']['file'])) as source:original=source.convert('RGB')
    with Image.open(within(run,arguments['mask_image']['file'])) as source:mask=source.convert('L')
    hard=None
    if 'alpha_image' in arguments:
        with Image.open(within(run,arguments['alpha_image']['file'])) as source:alpha=source.convert('L')
        with Image.open(within(run,arguments['protection_image']['file'])) as source:hard=source.convert('L')
        effective=ImageChops.multiply(alpha,ImageOps.invert(hard))
        mask=effective.point(lambda value:255 if value else 0)
    with Image.open(io.BytesIO(output)) as source:actual=source.convert('RGB')
    if original.size!=actual.size or mask.size!=actual.size:raise AgentError('区域保护核验尺寸不一致')
    difference=ImageChops.multiply(ImageChops.difference(original,actual),ImageOps.invert(mask).convert('RGB'))
    raw=difference.tobytes(); changed=sum(raw[i:i+3]!=b'\0\0\0' for i in range(0,len(raw),3))
    result={'format':'hex.protected-pixels.v1','verified':changed==0,'outside_mask_changed_pixels':changed,
        'editable_pixels':mask.histogram()[255],'canvas':list(actual.size),
        'parent_sha256':arguments['input_image']['sha256'],'mask_sha256':arguments['mask_image']['sha256'],
        'authority':'exact_rgb_pixel_comparison','visual_quality':'not_verified','pose_control':'not_supported'}
    if hard is not None:
        raw_hard=ImageChops.multiply(ImageChops.difference(original,actual),hard.convert('RGB')).tobytes()
        result.update(hard_protected_changed_pixels=sum(raw_hard[i:i+3]!=b'\0\0\0' for i in range(0,len(raw_hard),3)),
            alpha_sha256=arguments['alpha_image']['sha256'],protection_sha256=arguments['protection_image']['sha256'],
            protection_priority=True,composition_alpha_applications=1)
    return result
