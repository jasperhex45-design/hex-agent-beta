"""A derived, image-first feedback card; never a completion or repair gate."""
from __future__ import annotations

import base64
import io
from pathlib import Path

from PIL import Image

from .exporter import CSS, bullets, display_label, esc
from .io import AgentError, atomic_text, digest, sha, within


def _binding(candidate, reviewed, checked):
    return {'request_id': candidate['request_id'], 'candidate_sha256': candidate['sha256'],
            'candidate_file_sha256': candidate['candidate_file_sha256'],
            'contract_digest': reviewed['contract_digest'], 'review_digest': reviewed['digest'],
            'check_digest': checked['digest'] if checked is not None else None}


def _path(run, candidate, reviewed, checked):
    return within(run, 'workflow/feedback/' + digest(_binding(candidate, reviewed, checked)) + '.html')


def saved_card(run, candidate, reviewed, checked=None):
    """Locate the preview for these exact reports, without changing task state."""
    path = _path(run, candidate, reviewed, checked)
    if not path.is_file():
        return None
    return {'file': str(path), 'sha256': sha(path.read_bytes()),
            **_binding(candidate, reviewed, checked), 'root_cause': 'unknown',
            'authority': 'derived_from_registered_observations_not_causal_proof',
            'generation_calls': 0, 'completion_granted': False}


def export_card(run, candidate, contract, reviewed, checked=None):
    """Render already validated stage records, including failed/unknown reviews.

    Called under the existing workflow lock. The image and actual prompt are
    re-bound to their original generation; no source record is rewritten and
    no machine diagnosis is upgraded to an established cause.
    """
    from .task_review import _candidate
    from .workflow import read_packet
    from .workflow_policy import _check_failures, _requirements

    run = Path(run).resolve()
    image_path = within(run, candidate['file'])
    raw = image_path.read_bytes()
    packet = read_packet(run)
    requirements = _requirements(run, packet, contract)
    candidate_sha, generation, source_sha = _candidate(run, packet, contract, image_path)
    if (candidate_sha != candidate['sha256'] or source_sha != sha(raw)
            or source_sha != candidate['candidate_file_sha256']
            or generation['digest'] != candidate['evidence_digest']):
        raise AgentError('反馈卡须绑定当前候选的原图与实际生成记录')
    for stage in (reviewed, checked):
        if stage is not None and any(stage.get(key) != expected for key, expected in (
                ('request_id', candidate['request_id']), ('candidate_sha256', candidate_sha),
                ('contract_digest', contract['digest']), ('packet_digest', packet['digest']),
                ('requirements_digest', requirements['digest']))):
            raise AgentError('反馈卡的观察或检查属于另一请求、图片或合同')
    with Image.open(io.BytesIO(raw)) as image:
        mime = Image.MIME.get(image.format)
    if mime is None:
        raise AgentError('反馈卡无法识别候选原图格式')
    original = 'data:' + mime + ';base64,' + base64.b64encode(raw).decode('ascii')
    review = reviewed['report']['review']
    whole = review.get('holistic') or {}
    criteria = {row['id']: row for row in contract['checks']}
    observations = []
    for row in whole.get('judgments', []):
        observations.append('<div class="fact"><b>'
                            + esc(display_label('holistic_dimension', row['dimension'])) + ' · '
                            + esc(display_label('verdict', row['verdict'])) + '</b>'
                            + esc(row['evidence']) + '</div>')
    item_rows = []
    for item in review['items']:
        criterion = criteria[item['criterion_id']]
        item_rows.append('<tr><td>' + esc(criterion.get('question', item['criterion_id']))
                         + '<p class="caption">目标：' + esc(criterion.get('expected', ''))
                         + '</p></td><td>' + esc(display_label('verdict', item['verdict']))
                         + '</td><td>' + esc(item['candidate_evidence'])
                         + '<p class="caption">对照：' + esc(item['reference_evidence']) + '</p></td></tr>')
    checks = ['当前文件与文字检查未核验，仍需完成本题适用检查']
    check_summary = '当前文件与文字检查未核验'
    if checked is not None:
        report = checked['report']
        labels = {'image.read': '图片读取', 'image.ratio': '画幅比例',
                  'image.size': '输出尺寸', 'image.format': '文件格式'}
        verdicts = {'met': '满足', 'not_met': '未满足', 'uncertain': '待核实'}
        checks = [labels.get(row['id'], row['id']) + '：' + verdicts.get(row['verdict'], '待核实')
                  for row in report.get('image_checks', {}).get('checks', [])]
        ocr = report.get('ocr_checks', {})
        if ocr.get('state') == 'not_applicable':
            checks.append('画内文字核对：本题不适用')
        else:
            checks.append('画内文字核对：' + ('已运行' if ocr.get('state') == 'completed' else '未完成'))
            checks.extend(str(row.get('exact_text', row.get('criterion_id', '文字')))
                          + '：' + verdicts.get(row.get('verdict'), '待核实') for row in ocr.get('items', []))
        # Reuse the existing gate's actual subsystem/coverage checks; the
        # adapter's summary alone cannot establish successful OCR or pixels.
        incomplete = report.get('state') != 'completed' or bool(_check_failures(report, requirements))
        check_summary = ('文件或文字仍有未满足／未完成项' if incomplete else '文件与文字检查未发现失败项')
    hypotheses = {'intent': '需求理解', 'reference': '参考作用', 'design': '设计选择',
                  'compiler': '提示词转译', 'execution': '生成执行'}
    hypothesis = ''
    if review.get('diagnosis') in hypotheses:
        hypothesis = '<p>首评提出的待验证假设：' + esc(hypotheses[review['diagnosis']]) + '。</p>'
    if review.get('diagnosis_reason'):
        hypothesis += '<p class="small">待核对的描述：' + esc(review['diagnosis_reason']) + '</p>'
    suggestions = ''
    if review.get('preserve'):
        suggestions += '<h3>本次观察建议保留</h3>' + bullets(review['preserve'])
    if review.get('changes'):
        suggestions += '<h3>待验证的改动建议</h3>' + bullets(review['changes'])
    inputs = [row['id'] + ' · ' + ('上一候选编辑画布' if row['role'] == 'edit_canvas'
                                   else display_label('role', row['role']))
              for row in generation['ordered_inputs']]
    title = packet['brief']['title']
    page = f'''<!doctype html><html lang="zh-CN"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1"><title>{esc(title)} · 看图反馈</title>
<style>{CSS}
table{{width:100%;border-collapse:collapse}}td,th{{text-align:left;vertical-align:top;padding:12px;border-bottom:1px solid var(--line)}}td:first-child{{width:34%}}.table-scroll{{overflow:auto}}h3{{margin-top:18px}}
</style></head><body><main><div class="brand">HEX · 看图反馈</div><h1>{esc(title)}</h1>
<p class="pill neutral">{esc(display_label('decision', review['decision']))}</p>
<p class="pill neutral">{esc(check_summary)}</p>
<section class="panel"><img id="candidate-image" src="{original}" alt="本次候选原图">
<p class="caption">本次候选原图。下面是本轮复查记录，是否采用仍由你决定。</p></section>
<section class="panel"><h2>这张图要实现什么</h2><p>{esc(contract['request'])}</p></section>
<section class="panel"><h2>实际画面观察</h2><p>第一眼：{esc(whole.get('first_impression', '尚未记录'))}</p>
{''.join(observations)}<details><summary>目标与逐项观察</summary><div class="table-scroll"><table>
<thead><tr><th>本次要求</th><th>复查判断</th><th>画面证据</th></tr></thead><tbody>{''.join(item_rows)}</tbody></table></div></details></section>
<section class="panel"><h2>原因尚未定位</h2><p>画面差距与原因定位分别记录。本卡没有核实上游原因，保留未知。</p>
{hypothesis}{suggestions}<p class="caption">这些建议不会自动修改图片、追加生成或改变完成状态。</p></section>
<details><summary>文件与文字检查</summary>{bullets(checks)}</details>
<details><summary>这张图实际使用的提示词与参考</summary><pre class="prompt">{esc(generation['actual_prompt'])}</pre>
{bullets(inputs) if inputs else '<p>本次没有提交参考图片。</p>'}
<p class="caption">请求：{esc(candidate['request_id'])}<br>原图：{esc(source_sha)}<br>观察记录：{esc(reviewed['digest'])}</p></details>
<footer>视觉观察来自宿主复查。文件绑定核对记录一致性，不独立证明看图行为、审美结论或用户接受。</footer></main></body></html>'''
    if sha(image_path.read_bytes()) != source_sha:
        raise AgentError('导出反馈卡期间候选原图已变化')
    path = _path(run, candidate, reviewed, checked)
    path.parent.mkdir(parents=True, exist_ok=True)
    if not path.is_file() or path.read_text(encoding='utf-8') != page:
        atomic_text(path, page)
    return saved_card(run, candidate, reviewed, checked)
