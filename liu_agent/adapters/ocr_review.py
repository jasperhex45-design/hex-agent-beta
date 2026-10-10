"""Optional PaddleOCR 3.x adapter, run only for explicit frozen text checks.

The caller supplies an already configured local engine. This adapter never
installs Paddle, initializes models, downloads weights, or chooses a service.
"""
from __future__ import annotations

import math
from pathlib import Path
from time import perf_counter

from ..io import AgentError, digest
from .image_checks import inspect_image


def _requirements(contract: dict, requirements: list) -> list:
    if contract.get('digest') != digest({k: v for k, v in contract.items() if k != 'digest'}):
        raise AgentError('OCR需要未变化的冻结合同')
    checks = {row['id']: row for row in contract['checks']}
    seen = set()
    for row in requirements:
        ident = row.get('criterion_id')
        check = checks.get(ident)
        text = row.get('exact_text')
        if (not check or ident in seen or not isinstance(text, str) or not text.strip() or
                text not in (check['question'] + '\n' + check['expected'])):
            raise AgentError('OCR文字必须逐字绑定冻结检查，且每项只登记一次')
        seen.add(ident)
        # A mechanical binding cannot distinguish negation or title semantics.
        if row.get('text_required') is not True:
            raise AgentError('须明确声明该冻结项要求出现文字；不能把禁项自动送OCR')
        region = row.get('region')
        if region is not None and (len(region) != 4 or any(not isinstance(v, (int, float)) or
                not math.isfinite(v) or not 0 <= v <= 1 for v in region) or
                region[0] >= region[2] or region[1] >= region[3] or not row.get('region_source')):
            raise AgentError('文字位置须有任务来源，并使用原画布归一化矩形')
    return requirements


def _tokens(results, size):
    records = list(results)
    if len(records) != 1:
        raise AgentError('单图OCR须返回一个页面结果')
    raw = records[0]
    if hasattr(raw, 'json'):
        raw = raw.json
    if not isinstance(raw, dict):
        raise AgentError('仅支持PaddleOCR 3.x结果字典或result.json')
    raw = raw.get('res', raw)
    for name in ('rec_texts', 'rec_scores', 'rec_polys'):
        if name not in raw:
            raise AgentError('PaddleOCR结果缺少' + name)
    texts, scores, polys = raw['rec_texts'], raw['rec_scores'], raw['rec_polys']
    if not (len(texts) == len(scores) == len(polys)):
        raise AgentError('OCR文本、分数和原坐标多边形数量不一致')
    tokens = []
    for text, score, poly in zip(texts, scores, polys):
        score = float(score)
        points = [[float(v) for v in point] for point in poly]
        if (not isinstance(text, str) or not math.isfinite(score) or not 0 <= score <= 1 or len(points) < 3 or
                any(len(point) != 2 or any(not math.isfinite(v) or v < 0 or v > size[i]
                    for i, v in enumerate(point)) for point in points)):
            raise AgentError('OCR结果包含无效分数或原坐标')
        bbox = [min(p[0] for p in points), min(p[1] for p in points),
                max(p[0] for p in points), max(p[1] for p in points)]
        tokens.append({'text': text, 'confidence': score, 'polygon_original': points, 'bbox_original': bbox})
    return tokens


def review_text(image_path: Path, contract: dict, requirements: list, *, engine=None,
                confidence_threshold=0.8, runtime_label='caller_supplied_local_PaddleOCR3') -> dict:
    """Call ``engine.predict(input=...)`` once; absence is uncertain, not proof."""
    start = perf_counter()
    requirements = _requirements(contract, requirements)
    usage = {'backend': 'PaddleOCR3', 'runtime': runtime_label, 'model_calls': 0,
             'count_unit': 'one_predict_request_internal_submodel_invocations_not_measured',
             'runtime_source': 'caller_declared_local_engine',
             'external_calls_by_adapter': 0, 'elapsed_seconds': 0.0}
    result = {'format': 'hex.ocr-review.v1', 'contract_digest': contract['digest'], 'items': [],
              'usage': usage, 'limits': ['OCR可能漏字或误读；不证明语义、排版质量或用户接受',
                                        '文字与区域的任务含义由宿主负责；代码只核对来源绑定']}
    if not requirements:
        return {**result, 'state': 'not_applicable', 'reason': '没有明确画内文字检查，不启动OCR'}
    if not math.isfinite(confidence_threshold) or not 0 <= confidence_threshold <= 1:
        raise AgentError('OCR置信度阈值应为0到1')
    for row in requirements:
        result['items'].append({'criterion_id': row['criterion_id'], 'expected_text': row['exact_text'],
                                'verdict': 'uncertain', 'evidence': '尚未运行本地OCR'})
    if engine is None:
        return {**result, 'state': 'unavailable', 'reason': '可选本地OCR引擎未配置；未安装或下载任何模型'}
    inspection = inspect_image(image_path)
    if inspection['state'] != 'completed' or inspection['image']['orientation'] != 1:
        return {**result, 'state': 'unavailable', 'reason': '图像不可读或不是原坐标方向'}
    result['candidate_sha256'] = inspection['image']['sha256']
    try:
        usage['model_calls'] = 1
        # Disable document rectification and orientation transformations. Token
        # polygons must refer to the original image, never an unwarped canvas.
        records = engine.predict(input=str(Path(image_path).resolve()), use_doc_orientation_classify=False,
                                 use_doc_unwarping=False, use_textline_orientation=False)
        tokens = _tokens(records, inspection['image']['size'])
        # Some engines yield lazy results; check after fully consuming them.
        # A changed candidate must never inherit OCR observations bound to the
        # prior hash. The already attempted call remains in usage.
        after = inspect_image(image_path)
        if after.get('image', {}).get('sha256') != result['candidate_sha256']:
            result.update(state='failed', reason='OCR期间候选文件发生变化或不可读；识别结果未绑定')
            return result
        result['tokens'] = tokens
        width, height = inspection['image']['size']
        for row, item in zip(requirements, result['items']):
            matches = [token for token in tokens if token['text'] == row['exact_text'] and
                       token['confidence'] >= confidence_threshold]
            if not matches:
                item['evidence'] = '未找到高置信度逐字匹配；可能缺字、分行或识别失败，需看原图'
                continue
            item.update(verdict='met', evidence='本地OCR观察到逐字匹配；需结合原图复核', matches=matches)
            if row.get('region'):
                left, top, right, bottom = row['region']
                contained = [token for token in matches if token['bbox_original'][0] >= left * width and
                             token['bbox_original'][1] >= top * height and token['bbox_original'][2] <= right * width and
                             token['bbox_original'][3] <= bottom * height]
                item.update(verdict='met' if contained else 'not_met',
                            evidence='匹配文本位于要求区域内' if contained else '检测到的匹配文字均超出要求区域，需原图复核',
                            region=row['region'], region_source=row['region_source'])
        result['state'] = 'completed'
    except Exception as exc:
        # Keep attempted model consumption on failed inference. Do not silently
        # retry, select another backend, or serialize provider error secrets.
        result.update(state='failed', reason='本地OCR调用或结果验证失败：' + type(exc).__name__)
    finally:
        usage['elapsed_seconds'] = perf_counter() - start
    return result
