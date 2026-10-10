"""Local pixel diagnostics. No aesthetic ranking, registration or image rewriting."""
from __future__ import annotations

import hashlib
import io
import math
from pathlib import Path
from time import perf_counter

from PIL import Image, ImageChops

from ..io import AgentError, MAX_EVALUATION_SOURCE_BYTES, MAX_IMAGE_PIXELS


def _load(path: Path):
    path = Path(path)
    if path.stat().st_size > MAX_EVALUATION_SOURCE_BYTES:
        raise AgentError('图像超过本地图像检查大小限制')
    data = path.read_bytes()
    if len(data) > MAX_EVALUATION_SOURCE_BYTES:
        raise AgentError('图像超过本地图像检查大小限制')
    # Decode and hash the same bytes even if the path is edited concurrently.
    with Image.open(io.BytesIO(data)) as source:
        if source.width * source.height > MAX_IMAGE_PIXELS or getattr(source, 'n_frames', 1) != 1:
            raise AgentError('图像过大或包含多帧，不能按单张图检查')
        source.load()
        image = source.copy()
        metadata = {
            'format': source.format, 'size': list(source.size), 'mode': source.mode,
            'orientation': source.getexif().get(274, 1),
            'icc_sha256': hashlib.sha256(source.info.get('icc_profile', b'')).hexdigest(),
            'gamma': source.info.get('gamma'), 'srgb': source.info.get('srgb'),
            'chromaticity': source.info.get('chromaticity'), 'transparency': source.info.get('transparency'),
            'exif_color_space': source.getexif().get_ifd(34665).get(40961),
        }
    # Pillow may decode 16-bit RGB PNG to 8-bit RGB. Inspect IHDR, not mode alone.
    header = data[:26]
    metadata['encoded_bit_depth'] = header[24] if header.startswith(b'\x89PNG\r\n\x1a\n') else None
    metadata['sha256'] = hashlib.sha256(data).hexdigest()
    return image, metadata


def inspect_image(path: Path, *, expected_size=None, expected_ratio=None,
                  expected_format=None, ratio_tolerance=0.01) -> dict:
    """Check only supplied output requirements; unusual style is not a defect."""
    start = perf_counter()
    if not math.isfinite(ratio_tolerance) or ratio_tolerance < 0:
        raise AgentError('比例容差必须为非负有限数')
    checks = []
    try:
        image, metadata = _load(Path(path))
        image.close()
    except (OSError, ValueError, AgentError, Image.DecompressionBombError) as exc:
        return {'format': 'hex.image-checks.v1', 'state': 'unreadable', 'checks': [
            {'id': 'image.read', 'verdict': 'not_met', 'evidence': str(exc)}],
            'usage': {'backend': 'Pillow', 'model_calls': 0, 'elapsed_seconds': perf_counter() - start}}
    checks.append({'id': 'image.read', 'verdict': 'met', 'evidence': '原始图像可解码；未重采样或校色'})
    if expected_size is not None:
        if len(expected_size) != 2 or any(type(v) is not int or v <= 0 for v in expected_size):
            raise AgentError('输出尺寸必须是两个正整数')
        checks.append({'id': 'image.size', 'verdict': 'met' if metadata['size'] == list(expected_size) else 'not_met',
                       'expected': list(expected_size), 'actual': metadata['size']})
    if expected_ratio not in (None, 'auto'):
        try:
            ratio = [float(v) for v in str(expected_ratio).split(':')]
            if len(ratio) != 2 or any(not math.isfinite(v) or v <= 0 for v in ratio):
                raise ValueError()
        except ValueError as exc:
            raise AgentError('比例必须为正数宽:高') from exc
        actual = metadata['size'][0] / metadata['size'][1]
        relative_error = abs(actual / (ratio[0] / ratio[1]) - 1)
        checks.append({'id': 'image.ratio', 'verdict': 'met' if relative_error <= ratio_tolerance else 'not_met',
                       'expected': expected_ratio, 'actual': actual, 'relative_error': relative_error,
                       'tolerance': ratio_tolerance})
    if expected_format is not None:
        expected = str(expected_format).upper().replace('JPG', 'JPEG')
        checks.append({'id': 'image.format', 'verdict': 'met' if metadata['format'] == expected else 'not_met',
                       'expected': expected, 'actual': metadata['format']})
    return {'format': 'hex.image-checks.v1', 'state': 'completed', 'image': metadata, 'checks': checks,
            'usage': {'backend': 'Pillow', 'model_calls': 0, 'elapsed_seconds': perf_counter() - start},
            'limits': ['只检查文件与明确规格；不判断身份、语义或审美，也不替代用户接受']}


def strict_outside_mask(original: Path, candidate: Path, mask: Path, *, premises: dict) -> dict:
    """Exact native sample comparison, conditional on explicit task premises.

    White mask pixels are editable. No alignment, colorspace conversion, resize,
    EXIF transpose or implicit mask threshold is allowed. Only 8-bit PNG in
    L/RGB/RGBA is currently supported, so unsupported inputs remain uncertain.
    """
    required = ('exact_outside_required', 'lossless_pipeline', 'same_coordinate_frame',
                'same_color_space', 'same_bit_depth')
    result = {'id': 'pixels.outside_mask', 'verdict': 'uncertain',
              'premises': dict(premises), 'alignment': 'none_original_coordinates',
              'limits': ['前提声明来自调用者，检查器不证明素材历史或任务授权']}
    missing = [key for key in required if premises.get(key) is not True]
    if missing or not str(premises.get('requirement_source', '')).strip():
        return {**result, 'reason': '严格零差异前提或任务来源不足', 'missing': missing}
    images = []
    try:
        a, am = _load(original); images.append(a)
        b, bm = _load(candidate); images.append(b)
        m, mm = _load(mask); images.append(m)
        result['image_bindings'] = {'original': am['sha256'], 'candidate': bm['sha256'], 'mask': mm['sha256']}
        if (am['format'] != 'PNG' or bm['format'] != 'PNG' or
                am['encoded_bit_depth'] != 8 or bm['encoded_bit_depth'] != 8 or
                am['mode'] not in ('L', 'RGB', 'RGBA') or am['mode'] != bm['mode']):
            return {**result, 'reason': '严格检查仅支持相同原生模式的无损8位PNG'}
        if a.size != b.size or a.size != m.size or am['orientation'] != 1 or bm['orientation'] != 1 or mm['orientation'] != 1:
            return {**result, 'reason': '尺寸、原坐标或方向前提不成立'}
        if any(am[key] != bm[key] for key in ('icc_sha256', 'gamma', 'srgb', 'chromaticity', 'transparency', 'exif_color_space')):
            return {**result, 'reason': '颜色元数据不同，不能严格判零差异'}
        if m.mode != 'L' or mm['format'] != 'PNG' or mm['encoded_bit_depth'] != 8 or any(m.histogram()[1:255]):
            return {**result, 'reason': '蒙版必须是同尺寸8位L模式二值PNG；不自动阈值化'}
        outside = ImageChops.invert(m)
        protected_pixels = outside.histogram()[255]
        if protected_pixels == 0:
            return {**result, 'reason': '蒙版外没有可检查像素'}
        difference = ImageChops.difference(a, b)
        changed = Image.new('L', a.size, 0)
        for band in difference.split():
            changed = ImageChops.lighter(changed, band)
        changed = ImageChops.multiply(changed.point(lambda v: 255 if v else 0), outside)
        changed_pixels = changed.histogram()[255]
        return {**result, 'verdict': 'met' if changed_pixels == 0 else 'not_met',
                'protected_pixels': protected_pixels, 'changed_pixels': changed_pixels,
                'changed_bbox_original': list(changed.getbbox()) if changed.getbbox() else None,
                'reason': '在已声明前提下比较原始像素；含透明像素的颜色与alpha通道'}
    except (OSError, ValueError, AgentError, Image.DecompressionBombError) as exc:
        return {**result, 'reason': str(exc)}
    finally:
        for image in images:
            image.close()


def original_coordinate_geometry(*, original_size, candidate_size, landmarks: list,
                                 coordinate_frame='original', transform=None) -> dict:
    """Measure supplied landmarks on original canvases, without fitting a transform."""
    result = {'id': 'geometry.original_coordinates', 'verdict': 'uncertain',
              'coordinate_frame': coordinate_frame, 'transform': transform,
              'landmarks': [], 'limits': ['关键点由调用者观察或测量；本函数不自动证明检测或主体身份']}
    if coordinate_frame != 'original' or transform is not None:
        return {**result, 'reason': '不能用配准后的坐标作结构保持结论；请另存诊断变换'}
    if (len(original_size) != 2 or any(type(v) is not int or v <= 0 for v in original_size) or
            list(original_size) != list(candidate_size) or not landmarks):
        return {**result, 'reason': '需要同尺寸原画布和明确关键点'}
    ids = set()
    for row in landmarks:
        if not row.get('id') or row['id'] in ids:
            raise AgentError('几何关键点ID为空或重复')
        ids.add(row['id'])
        if row.get('original') is None or row.get('candidate') is None:
            result['landmarks'].append({'id': row['id'], 'verdict': 'uncertain', 'reason': '关键点不可见'})
            continue
        points = row['original'], row['candidate']
        tolerance = row.get('allowed_shift_px')
        if (not isinstance(tolerance, (float, int)) or not math.isfinite(tolerance) or tolerance < 0 or
                any(len(point) != 2 or any(not isinstance(v, (int, float)) or not math.isfinite(v) or
                        v < 0 or v >= original_size[index] for index, v in enumerate(point)) for point in points)):
            raise AgentError('关键点必须在原画布内，且容差为非负有限数')
        dx, dy = points[1][0] - points[0][0], points[1][1] - points[0][1]
        distance = math.hypot(dx, dy)
        result['landmarks'].append({'id': row['id'], 'original': list(points[0]), 'candidate': list(points[1]),
                                   'dx': dx, 'dy': dy, 'distance_px': distance, 'allowed_shift_px': tolerance,
                                   'verdict': 'met' if distance <= tolerance else 'not_met'})
    verdicts = [row['verdict'] for row in result['landmarks']]
    result['verdict'] = 'not_met' if 'not_met' in verdicts else 'uncertain' if 'uncertain' in verdicts else 'met'
    return result
