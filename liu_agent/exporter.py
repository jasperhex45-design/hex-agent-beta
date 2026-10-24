"""Safe static result files. No server, CDN, third-party fonts, or remote scripts."""
from __future__ import annotations
import base64
import html
import io
import re
from pathlib import Path
from PIL import Image
from .io import AgentError, atomic_text, write_json, within, digest, sha
from .messages import STATUS_LABELS, build_presentation

CSS = """
:root{color-scheme:dark;--bg:#101214;--panel:#181c20;--line:#2b3238;--text:#eef1ed;--muted:#a7afb1;--accent:#caf187}
.pill.neutral{background:#252a2b;color:#bdc5c7;border-color:#3c4447}.pill.warn{background:#392b23;color:#ffd2a6;border-color:#79533b}*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--text);font:15px/1.7 -apple-system,BlinkMacSystemFont,'Segoe UI','Microsoft YaHei',sans-serif}
main{max-width:1160px;margin:0 auto;padding:48px 26px 60px}.brand{font-size:12px;letter-spacing:.18em;color:var(--accent);font-weight:700}.hero{padding:22px 0 30px;border-bottom:1px solid var(--line)}h1{font-size:38px;line-height:1.3;letter-spacing:-.03em;margin:8px 0 14px}h2{font-size:20px;margin:0 0 14px}h3{font-size:16px;margin:0}p{margin:10px 0}.sub{color:var(--muted);max-width:900px}.pill{display:inline-block;background:#252d24;color:var(--accent);border:1px solid #3c4d32;padding:4px 10px;border-radius:30px;font-size:12px;margin:4px 5px 4px 0}.layout{display:grid;grid-template-columns:1fr 300px;gap:24px;padding-top:26px;align-items:start}.panel{background:var(--panel);border:1px solid var(--line);border-radius:18px;padding:24px;margin-bottom:18px}nav{display:flex;gap:8px;flex-wrap:wrap;margin-bottom:18px}button{font:inherit;cursor:pointer;border:1px solid var(--line);padding:9px 15px;background:#22292d;color:var(--text);border-radius:10px;min-height:42px}button:hover,button:focus-visible{border-color:var(--accent)}button.active,.copy{background:var(--accent);color:#172014;border-color:var(--accent);font-weight:700}.prompt{white-space:pre-wrap;overflow-wrap:anywhere;font-size:14px;line-height:1.9;background:#111517;border:1px solid var(--line);padding:20px;border-radius:12px;max-height:650px;overflow:auto}.prompt pre{white-space:pre-wrap;font:inherit;margin:0}.row{display:flex;justify-content:space-between;gap:12px;align-items:center;margin-bottom:12px}.model{display:none}.model:first-of-type{display:block}.caption{font-size:12px;color:var(--muted)}details{border-top:1px solid var(--line);padding-top:12px;margin-top:14px}summary{cursor:pointer;color:#ccd4d2}ul{padding-left:20px}a{color:var(--accent);word-break:break-word}img{max-width:100%;height:auto;border-radius:12px;display:block;margin:8px 0}.grid{display:grid;grid-template-columns:repeat(2,1fr);gap:15px}.fact{border-bottom:1px solid var(--line);padding:11px 0}.fact b{display:block;font-size:12px;color:var(--muted);margin-bottom:5px}.toast{position:fixed;bottom:25px;left:50%;transform:translateX(-50%);padding:10px 18px;background:var(--accent);color:#172014;border-radius:20px;display:none}footer{margin-top:28px;color:var(--muted);font-size:12px}.notice{border-left:3px solid var(--accent);padding-left:12px;color:#ccd4c8}.small{font-size:13px}@media(max-width:820px){main{padding:26px 16px}.layout{display:block}h1{font-size:29px}.grid{grid-template-columns:1fr}.panel{padding:18px}}@media print{body{background:white;color:black}.panel{background:white;color:black}.model{display:block!important}.prompt{color:black;background:white;max-height:none}button,nav,.toast{display:none}.layout{display:block}}
"""
JS = r"""
const tabs=[...document.querySelectorAll('[data-tab]')];
tabs.forEach(b=>b.addEventListener('click',()=>{tabs.forEach(t=>t.classList.toggle('active',t===b));document.querySelectorAll('.model').forEach(m=>m.style.display=m.id===b.dataset.tab?'block':'none')}));
function toast(t){const el=document.querySelector('.toast');el.textContent=t;el.style.display='block';setTimeout(()=>el.style.display='none',2200)}
async function copy(text){try{if(!navigator.clipboard)throw Error('fallback');await navigator.clipboard.writeText(text);toast('已复制')}catch(e){const t=document.createElement('textarea');t.value=text;t.style.position='fixed';t.style.top='0';document.body.appendChild(t);t.focus();t.select();let ok=false;try{ok=document.execCommand('copy')}catch(err){}if(ok){t.remove();toast('已复制')}else{t.style.width='80vw';t.style.height='50vh';toast('浏览器限制复制：请在选中文本后按 Ctrl+C');t.addEventListener('blur',()=>t.remove(),{once:true})}}}
document.querySelectorAll('[data-copy]').forEach(b=>b.addEventListener('click',()=>copy(document.getElementById(b.dataset.copy).textContent)));
"""

def esc(s):
    return html.escape(str(s), quote=True)


def md_escape(s):
    return re.sub(r'([\\`*_{}\[\]()#+.!|>~-])', r'\\\1', esc(s))


def bullets(values):
    return "<ul>" + "".join("<li>"+esc(v)+"</li>" for v in values) + "</ul>"


def thumbnail(path: Path) -> str:
    with Image.open(path) as image:
        image = image.convert("RGB")
        image.thumbnail((960, 960))
        buf = io.BytesIO()
        image.save(buf, format="JPEG", quality=85)
    return "data:image/jpeg;base64," + base64.b64encode(buf.getvalue()).decode("ascii")


DISPLAY_LABELS = {
    'decision': {'ready_for_user': '复查建议交由用户判断', 'revise': '复查建议修改',
                 'uncertain': '复查无法判断'},
    'verdict': {'met': '复查认为已满足', 'not_met': '复查认为未满足', 'uncertain': '复查无法判断'},
    'baseline': {'comparable': '复查认为足以相比，个人审美仍由用户判断',
                 'below': '复查认为低于参考', 'uncertain': '复查无法判断'},
    'submission': {'planned_not_submitted': '计划提交，尚未提交', 'not_submitted': '未提交',
                   'api_images_submitted': '程序记录已通过 API 提交图像',
                   'api_image_bytes': '程序记录已提交 API 图像字节',
                   'host_reported_not_api_verified': '宿主报告已提交，未由 API 核验'},
    'origin': {'host_reported_not_api_verified': '宿主报告，未经 API 独立核验',
               'api_image_bytes': '程序记录的 API 图像结果'},
    'model_test': {'not_tested': '此目标模型尚未试图',
                   'trial_recorded_not_human_accepted': '已记录此目标模型的候选，用户选择另行记录'},
    'profile': {'experimental': '实验性档案', 'validated': '档案已核验，不代表当前图像已验收',
                'deprecated': '已停用档案'},
    'coverage': {'reference_selected_not_submitted': '已选择生成参考，是否提交见输入记录',
                 'no_generation_baseline': '尚无可用于生成的视觉基准'},
    'role': {'identity': '身份', 'scene': '场景', 'style': '风格', 'lighting': '光影',
             'color': '色彩', 'pose': '姿态', 'wardrobe': '服装', 'detail': '细节',
             'material': '材质', 'composition': '构图', 'edit_target': '待编辑原图',
             'aesthetic_positive': '审美正例', 'aesthetic_negative': '审美反例',
             'acceptance_floor': '质量基准'},
    'dimension': {'expression': '表达', 'composition': '构图', 'light_color': '光色',
                  'material': '材质', 'relationship': '关系', 'overall': '整体', 'narrative': '叙事'},
    'holistic_dimension': {'overall': '整体观感', 'visual_coherence': '视觉可信', 'expression': '表达'},
    'review_context': {'isolated': '评审方声明使用隔离上下文',
                       'author_context': '作者同上下文自评', 'unknown': '上下文情况未确认'},
    'image_access': {'viewed': '评审方声明已实际看图', 'unavailable': '评审方声明未能查看图像'},
    'medium': {'still': '静态图', 'film_shot': '影片静帧'},
    'domain': {'person': '人像', 'illustration': '插画', 'poster': '海报', 'vehicle': '汽车',
               'character': '角色', 'prop': '道具', 'hybrid': '混合题材', 'auto': '题材待判断'},
}


def display_label(group: str, value) -> str:
    """Translate display enums without treating unfamiliar values as success."""
    return DISPLAY_LABELS.get(group, {}).get(value, '状态未识别，待核实')


REVIEW_FILE_BOUNDARY = '文件绑定只核对图片与记录的一致性，不能独立证明实际看图，也不证明个人审美已验收。'


HOLISTIC_REVIEW_BOUNDARY = ('看图与上下文隔离是评审方的声明，文件绑定不能独立验证。'
                            '以下判断是机器复查建议，不等于用户接受。')


def render_holistic_review(review: dict) -> tuple[list[str], str]:
    """Display a saved whole-image review without certifying its declarations."""
    whole = review.get('holistic')
    if not whole:
        return [], ''
    context = display_label('review_context', whole.get('review_context'))
    access = display_label('image_access', whole.get('image_access'))
    impression = esc(whole.get('first_impression', ''))
    markdown = ['### 整体看图复查', '审查上下文：' + context, '实际看图声明：' + access,
                HOLISTIC_REVIEW_BOUNDARY, '第一眼感受：' + impression]
    section = ('<div class="holistic-review"><h4>整体看图复查</h4>'
               '<p class="small">审查上下文：' + esc(context) + '<br>实际看图声明：'
               + esc(access) + '</p><p class="caption">' + esc(HOLISTIC_REVIEW_BOUNDARY)
               + '</p><p>第一眼感受：' + impression.replace('\n', '<br>') + '</p>')
    for item in whole.get('judgments', []):
        label = display_label('holistic_dimension', item.get('dimension'))
        verdict = display_label('verdict', item.get('verdict'))
        evidence = esc(item.get('evidence', ''))
        markdown += [f'{label} / {verdict}：{evidence}']
        section += (f'<div class="fact"><b>{esc(label)} · {esc(verdict)}</b>'
                    + evidence.replace('\n', '<br>') + '</div>')
    return markdown, section + '</div>'


def render_workflow_review(result: dict) -> tuple[list[str], str]:
    """Show the actual full-flow findings without replacing the user's choice."""
    completion = result.get('workflow_completion')
    if not completion:
        return [], ''
    rows = completion.get('candidate_receipts', [])
    if not rows:
        text = '本次交付提示词，已核对方案与编译；未调用生图，也未记录图片或视觉验收。'
        return ['## 交付前检查', text], '<section class="panel"><h2>交付前检查</h2><p>' + esc(text) + '</p></section>'
    decision = result.get('human_acceptance')
    note = ('已按你的确认保留本版；检查记录和未满足项仍保留。' if decision == 'accepted_by_user' else
            '已记录你不采用本版；原图与检查保留，不自动重画。' if decision == 'rejected_by_user' else
            '检查已记录，是否采用由你决定；技术细节可按需展开。')
    if completion.get('independent_review') is False:
        note += ' 本次为探索首稿自检，未做独立评审。'
    study = completion.get('visual_research') or {}
    if study.get('research_sufficient') is False:
        note += ' 参考研究不足：' + str((study.get('limited_exit') or {}).get('unresolved', '具体缺口见记录。'))
    markdown = ['## 交付前检查', note]
    sections = ['<section class="panel"><h2>交付前检查</h2><p>' + esc(note) + '</p>']
    differences = list(dict.fromkeys(row['candidate_evidence']
        for accepted in completion.get('accepted_deviations', []) for row in accepted['deviations']))
    if differences:
        markdown.extend(['已确认保留的差异：', *differences])
        sections.append('<p>已确认保留的差异：</p>' + bullets(differences))
    markdown.append('<details>\n<summary>查看检查详情</summary>\n')
    sections.append('<details><summary>查看检查详情</summary>')
    check_names = {'image.read': '图片读取', 'image.ratio': '画幅比例',
                   'image.size': '输出尺寸', 'image.format': '文件格式'}
    verdicts = {'met': '满足', 'not_met': '未满足', 'uncertain': '待核实'}
    for row in rows:
        if not row.get('delivered', True):
            continue
        facts = [check_names.get(item['id'], item['id']) + '：' + verdicts.get(item.get('verdict'), '未确认')
                 for item in row.get('image_checks', {}).get('checks', [])]
        ocr = row.get('ocr_checks', {})
        if ocr.get('state') == 'not_applicable':
            facts.append('画内文字核对：本题登记为不适用')
        else:
            facts.append('画内文字核对：' + ('已完成' if ocr.get('state') == 'completed' else '未完成'))
            for item in ocr.get('items', []):
                facts.append(str(item.get('exact_text', item.get('expected_text', item.get('criterion_id', ''))))
                             + '：' + verdicts.get(item.get('verdict'), '未确认'))
        markdown.extend(facts)
        sections.append(bullets(facts))
        whole_md, whole_html = render_holistic_review(row.get('review', {}))
        markdown.extend(whole_md)
        sections.append(whole_html)
        items = row.get('review', {}).get('items', [])
        if items:
            findings = [str(item['candidate_evidence']) for item in items]
            markdown.extend(['### 本题逐项观察', *findings])
            sections.append('<details><summary>本题逐项观察</summary>' + bullets(findings) + '</details>')
    derived = completion.get('derived_delivery')
    if derived:
        facts = ['首稿尺寸：未满足（原not_met保留）', '已解决范围：仅同比例缩小到冻结尺寸',
                 '最终图已重新检查与评审；原模型只生成一次，本地返修一次。']
        markdown.extend(['### 原失败与本地解决证据', *facts])
        sections.append('<h3>原失败与本地解决证据</h3>' + bullets(facts))
    markdown.append('</details>')
    return markdown, ''.join(sections) + '</details></section>'


# Only the passage from "整体表达、空间或媒介不成立才回设计；" through
# "整体已成立则只处理有依据的局部差距，" in REASSESSMENT_DESCRIPTION adapts
# zy-cinematic-realism Result Repair, Copyright (c) 2026 ZY / popopo-99;
# CC BY-NC 4.0. Source, license and Hex Agent modifications: ../THIRD_PARTY_NOTICES.md.
# This attribution does not cover this module's execution or evidence logic.
REASSESSMENT_DESCRIPTION = (
    '用户已否决，视觉目标尚未完成。先读取本图反馈原话、已有反馈来源快照及仍有效的需求与本题偏好；'
    '本次明确要求优先，保留用户已满意且未被本次变更推翻的部分，不把一次反馈提升为长期偏好。'
    '实际对照被否决候选和与争议有关的参考，区分已知事实与推断，定位最早有证据的失败层：'
    '需求理解或参考职责错误先修对应输入；整体表达、空间或媒介不成立才回设计；'
    '整体已成立则只处理有依据的局部差距，正文漏项回编译，实际执行不一致先核对调用。'
    '不因一次否决机械推翻全方案，也不靠追加风格词代替诊断。用户未说明拒绝理由时不补造原因；'
    '必要原图不可见或执行依据缺失时明确未知，先补证据，不声称已经完成视觉归因。'
    '最多提出两个待验证假设；若需改变方向或冻结标准，在旧记录之外保存改变与保留范围、新方案和生成前标准。'
    '重审本身不生成、不改旧图与旧合同、不重置预算；新记录不自动取得新增生成授权或预算。'
)


def rejection_next_action(run: Path, result: dict, packet: dict | None = None) -> dict | None:
    """Describe a verified rejection using authoritative packet references, without writes."""
    if result.get('human_acceptance') != 'rejected_by_user':
        return None
    run = run.resolve()
    if packet is None:
        from .workflow import read_packet
        packet = read_packet(run)
    sources = {'user_feedback': run/'user-feedback.json', 'packet': run/'packet.json',
               'contract': run/'acceptance.json', 'result': run/'result.json'}
    from . import feedback_sources
    if result.get('feedback_source_evidence') == feedback_sources.EVIDENCE:
        # The current feedback receipt already verifies this append-only snapshot.
        # Include its earlier replies so partial approval is not lost on resume;
        # the host still checks which image/scope and later changes each reply binds.
        sources['feedback_sources'] = run/feedback_sources.FILE
    selected = next((t for t in result.get('trials', [])
                     if t['round'] == result.get('selected_trial_round')), None)
    if selected:
        sources['candidate'] = within(run, selected['file'])
    inputs = {name: str(path) for name, path in sources.items() if path.is_file()}
    inputs['references'] = [str(within(run, ref['file'])) for ref in packet['references']
                            if within(run, ref['file']).is_file()]
    from .co_creation import copilot_enabled
    copilot = (' 进入 Copilot 反馈沟通：先使用用户已经说明的问题与偏好；若只是不好看或关键表达仍不明确，围绕本图提出一个能改变研究方向的问题，得到回答后再找参考和修订。已有明确修改则不重复追问；不套固定风格菜单、不重复抽图。'
               if copilot_enabled(packet) else '')
    return {'command': None, 'action': 'reassessment_required',
            'description': REASSESSMENT_DESCRIPTION + copilot, 'inputs': inputs}


def uncertain_next_action(result: dict) -> dict | None:
    """Expose the selected review without interpreting or changing its diagnosis."""
    from .visual_decision import record_only
    if record_only(result.get('acceptance', {})):
        return None
    if result.get('human_acceptance') in ('accepted_by_user', 'rejected_by_user'):
        return None
    selected = next((t for t in result.get('trials', [])
                     if t['round'] == result.get('selected_trial_round')), None)
    review = (selected or {}).get('review') or {}
    if review.get('decision') != 'uncertain':
        return None
    description = ['当前复查无法判断；先核对不确定原因，不自动重画。',
                   '复查原因：' + review.get('diagnosis_reason', '')]
    if review.get('preserve'):
        description.append('保留：' + '；'.join(review['preserve']))
    if review.get('changes'):
        description.append('待核实的下一步：' + '；'.join(review['changes']))
    return {'command': None, 'description': '\n'.join(description)}


def render_adaptation_explanation(result: dict) -> tuple[str, str]:
    """Explain saved decisions and compiled excerpts without inventing intent."""
    adaptations = result.get('model_adaptation')
    if not adaptations:
        return '', ''
    from .io import AgentError
    request = result['acceptance']['request']
    choices = result['design']['choices']
    by_id = {choice['id']: choice for choice in choices}
    boundary = ('以下动作与取舍是 Agent 的设计提案；模型写法是尚未通过图像效果验证的策略。'
                '正文片段来自本次编译结果，是否实际发送以调用记录为准。')
    markdown = ['# 从需求到画面：设计与模型写法', boundary, '## 你的原话', esc(request),
                '## Agent 如何转成画面']
    sections = ['<details><summary>从需求到画面：设计与模型写法</summary>',
                '<p class="caption">' + esc(boundary) + '</p>',
                '<h3>你的原话</h3><p>' + esc(request).replace('\n', '<br>') + '</p>',
                '<h3>Agent 如何转成画面</h3>']
    for choice in choices:
        markdown.extend(['### ' + esc(choice['goal']), '设计动作：' + esc(choice['action']),
                         '配合关系：' + esc(choice['cooperation']), '主动取舍：' + esc(choice['tradeoff'])])
        sections.append('<div class="fact"><b>' + esc(choice['goal']) + '</b><p>设计动作：'
                        + esc(choice['action']) + '</p><p>配合关系：' + esc(choice['cooperation'])
                        + '</p><p class="caption">主动取舍：' + esc(choice['tradeoff']) + '</p></div>')
    labels = {'image': 'Image', 'seedream': 'Seedream', 'nano': 'Nano', 'midjourney': 'Midjourney'}
    for target, proof in adaptations.items():
        label = labels.get(target, target)
        text = result.get('prompts', {}).get(target, {}).get('text', '')
        markdown.extend(['## ' + esc(label) + '：这次怎样组织提示词', esc(proof['strategy'])])
        sections.append('<details><summary>' + esc(label) + '：这次怎样组织提示词</summary><p>'
                        + esc(proof['strategy']) + '</p>')
        for rule in proof['rules']:
            excerpt = rule['prompt_excerpt']
            if not excerpt.strip() or excerpt not in text:
                raise AgentError('模型适配说明引用的片段不在当前编译正文中，先重新编译')
            markdown.extend(['### 写法：' + esc(rule['rule']), '本次采用原因：' + esc(rule['reason']),
                             '对应正文：\n\n' + '\n'.join('> ' + esc(line) for line in excerpt.splitlines())])
            sections.append('<div class="fact"><b>写法：' + esc(rule['rule']) + '</b><p>本次采用原因：'
                            + esc(rule['reason']) + '</p><p class="caption">对应正文</p><blockquote>'
                            + esc(excerpt).replace('\n', '<br>') + '</blockquote></div>')
        for item in proof['choices']:
            choice = by_id.get(item['choice_id'])
            excerpt = item['prompt_excerpt']
            if choice is None or not excerpt.strip() or excerpt not in text:
                raise AgentError('模型适配说明的设计动作或正文片段失效，先重新编译')
            markdown.extend(['### 设计如何进入正文：' + esc(choice['goal']),
                             '\n'.join('> ' + esc(line) for line in excerpt.splitlines())])
            sections.append('<div class="fact"><b>设计如何进入正文：' + esc(choice['goal'])
                            + '</b><blockquote>' + esc(excerpt).replace('\n', '<br>') + '</blockquote></div>')
        sections.append('</details>')
    sections.append('</details>')
    return '\n\n'.join(markdown) + '\n', ''.join(sections)


def _export_production(result: dict, run: Path) -> None:
    """Show the uncompressed candidate before optional author/research commentary."""
    trial = next((row for row in result['trials'] if row['round'] == result.get('selected_trial_round')), result['trials'][0])
    display = result.get('delivery_asset') or trial
    image_label = '最终交付图（本地同比缩小）' if result.get('delivery_asset') else '首张原图'
    raw = within(run, display['file']).read_bytes()
    with Image.open(io.BytesIO(raw)) as image:
        mime = Image.MIME.get(image.format, 'image/png')
    original = 'data:' + mime + ';base64,' + base64.b64encode(raw).decode('ascii')
    presentation = build_presentation(run, result=result)
    write_json(run/'presentation.json', presentation)
    adaptation_md, adaptation_html = render_adaptation_explanation(result)
    acceptance = presentation['facets']['user_decision']
    feedback = '可以采用这一版、保留部分、局部修改或换方向；直接说明想保留和改变什么。'
    positive = presentation.get('candidate_feedback')
    if positive or result.get('human_acceptance') in ('accepted_by_user', 'rejected_by_user'):
        feedback = presentation['next_action']
    confirmed = result.get('human_acceptance') == 'accepted_by_user'
    image_state = '已确认保留' if confirmed else '已拒绝，保留作对照' if result.get('human_acceptance') == 'rejected_by_user' else '等待你的判断'
    parts = [f"# {result['brief']['title']}", f"![{image_label}]({display['file']})", md_escape(acceptance),
             feedback, '选择仅适用于当前图片；以下按钮只复制回复，不会代你登记反馈。']
    feedback_sources_html = ''
    if positive:
        parts.extend('<details>\n<summary>反馈来源</summary>\n\n' + md_escape(message['locator'])
                     + '\n\n</details>' for message in positive['messages'])
        feedback_sources_html = ''.join('<details><summary>反馈来源</summary><p>'
                                      + esc(message['locator']) + '</p></details>'
                                      for message in positive['messages'])
    prompt_html = ''
    (run/'prompts').mkdir(exist_ok=True)
    for index, (target, row) in enumerate(result['prompts'].items()):
        atomic_text(run/'prompts'/f'{target}.txt', row['text']+'\n')
        prompt_html += f'<h3>{esc(target)}</h3><pre class="prompt">{esc(row["text"])}</pre>'
        parts.append(f'<details>\n<summary>{target} 提示词</summary>\n\n{row["text"]}\n</details>')
    review = trial.get('review') or {}
    review_md, review_html = render_holistic_review(review)
    for item in review.get('items', []):
        text = f'{item["criterion_id"]}：{item["candidate_evidence"]}'
        review_md.append(text)
        review_html += '<p>' + esc(text) + '</p>'
    research = '机器意见仅作研究记录，不决定本图是否合格，也不触发补图。'
    if review:
        parts.append('<details>\n<summary>机器研究记录（不参与验收）</summary>\n\n' + research + '\n\n' + '\n\n'.join(review_md) + '\n</details>')
    if adaptation_md:
        parts.append('<details>\n<summary>设计与模型写法</summary>\n\n' + adaptation_md + '\n</details>')
    accept_text = '接受这一版，候选 ' + display['sha256']
    reject_text = '拒绝这一版，候选 ' + display['sha256']
    candidate_id = display.get('asset_id') or trial.get('request_id') or (trial.get('generation_evidence') or {}).get('request_id', '')
    actions = ([] if confirmed else [('accept-reply', '采用这一版')]) + [
        ('preserve-reply', '保留部分'), ('edit-reply', '局部修改'), ('direction-reply', '换方向')]
    action_buttons = ''.join('<button data-copy="'+key+'">'+label+'</button> ' for key, label in actions)
    action_replies = ''.join('<span id="'+key+'" hidden>'+esc(text)+'</span>' for key, text in [
        ('preserve-reply', '保留这张的【认可部分】，候选 '+candidate_id+'，图片 '+display['sha256']),
        ('edit-reply', '修改这张的【改变内容】，保留【认可部分】，候选 '+candidate_id+'，图片 '+display['sha256']),
        ('direction-reply', '这张换一个创作方向：【目标与关系】，候选 '+candidate_id+'，图片 '+display['sha256'])])
    action_summary = ''
    if presentation.get('candidate_actions'):
        active = presentation['candidate_actions']['active']
        action_summary = '<p>已记录本张反馈，局部认可不表示整张接受。</p>'+''.join(
            '<p>'+esc(row['quote'])+'</p>' for row in active)
    parent_preview = ''
    parent = result.get('requirement_parent')
    if parent is None:
        from .workflow import read_packet
        parent = read_packet(run).get('requirement_parent')
    if parent and parent.get('parent_request_id'):
        from .feedback import verified_candidate
        _, _, parent_trial = verified_candidate(Path(parent['run']), parent['parent_request_id'])
        if parent_trial['sha256'] != parent['parent_candidate_sha256']:
            raise AgentError('改版交付的父图与关联版本不同')
        parent_raw = within(Path(parent['run']), parent_trial['file']).read_bytes()
        with Image.open(io.BytesIO(parent_raw)) as parent_image:
            parent_mime = Image.MIME.get(parent_image.format, 'image/png')
        parent_url = 'data:'+parent_mime+';base64,'+base64.b64encode(parent_raw).decode('ascii')
        parent_preview = '<section class="panel"><h2>前版 / 本版</h2><div class="grid"><div><p>前版</p><a href="'+parent_url+'" target="_blank"><img src="'+parent_url+'" alt="实际父图"></a></div><div><p>本版</p><a href="'+original+'" target="_blank"><img src="'+original+'" alt="本次改版"></a></div></div><p>点击图片查看局部。改善与保留是否成立待实际对照；已返回图片不等于已修好。</p></section>'
    page = f'''<!doctype html><html lang="zh-CN"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>{esc(result['brief']['title'])} · Hex Agent</title><style>{CSS}</style></head><body><main>
<h1>{esc(result['brief']['title'])}</h1><section class="panel" id="original-candidate"><img alt="{esc(image_label)}，{image_state}" src="{original}"><a href="{esc(display['file'])}" download>打开或保存{esc(image_label)}</a></section>
<section class="panel"><h2>这一版的反馈</h2><p>{esc(acceptance)}</p><p>{esc(feedback)}</p>{action_summary}{feedback_sources_html}{action_buttons}<details><summary>更多选择</summary><button data-copy="reject-reply">复制“拒绝这一版”</button></details><p class="caption">复制后发给 Hex Agent 才会登记；页面不会自动保存选择。</p><span id="accept-reply" hidden>{esc(accept_text)}</span><span id="reject-reply" hidden>{esc(reject_text)}</span>{action_replies}</section>
{parent_preview}
<details><summary>设计与实际提示词</summary><p>{esc(result['design']['expression'])}</p>{adaptation_html}{prompt_html}</details>
{'<details><summary>机器研究记录（不参与验收）</summary><p>'+research+'</p>'+review_html+'</details>' if review else ''}
<footer>图像、来源与检查记录分别保留；你的选择只适用于当前版本。</footer></main><div class="toast" role="status" aria-live="polite"></div><script>{JS}</script></body></html>'''
    if result['brief'].get('model_comparison'):
        gallery = []
        comparison_parts = [f"# {result['brief']['title']}"]
        for index, row in enumerate(result['trials'], 1):
            data = base64.b64encode(within(run, row['file']).read_bytes()).decode('ascii')
            label = f'第{index}张 · {row["target"]}'
            gallery.append(f'<section class="panel"><h2>{esc(label)}</h2><img src="data:image/png;base64,{data}" alt="{esc(label)} 原图"><a href="{esc(row["file"])}">查看原图</a><details><summary>候选追溯信息</summary><p>request_id: {esc(row["request_id"])}<br>sha256: {esc(row["sha256"])}</p></details></section>')
            comparison_parts += [f'## {label}', f'![{label}]({row["file"]})',
                                 '<details>\n<summary>候选追溯信息</summary>\n\n'
                                 f'request_id `{row["request_id"]}`；sha256 `{row["sha256"]}`\n</details>']
        relative = presentation.get('relative_preference')
        preference_html = ''
        if relative:
            preferred_index = next(i for i, row in enumerate(result['trials'], 1)
                                   if row['request_id'] == relative['binding']['preferred_request_id'])
            title = f'本轮相对偏好：第{preferred_index}张更接近（未作最终接受）'
            preference_html = ('<section class="panel"><h2>' + esc(title) + '</h2>'
                               + ''.join('<p>当时指向第' + str(next(i for i, row in enumerate(result['trials'], 1)
                                         if row['request_id'] == message['preferred_request_id']))
                                         + '张；原话：' + esc(message['quote']) + '</p>'
                                         + '<details><summary>反馈来源</summary><p class="caption">'
                                         + esc(message['locator']) + '</p></details>'
                                         for message in relative['messages']) + '</section>')
            comparison_parts += ['## ' + title]
            comparison_parts += ['当时指向第' + str(next(i for i, row in enumerate(result['trials'], 1)
                                 if row['request_id'] == message['preferred_request_id']))
                                 + '张；原话：' + md_escape(message['quote'])
                                 + '\n\n<details><summary>反馈来源</summary>\n\n'
                                 + md_escape(message['locator']) + '\n\n</details>'
                                 for message in relative['messages']]
        guidance = (presentation['next_action'] if relative else
                    '可以直接说“第一张更接近，但还要调整……”这类感受与下一步目标；比较偏好不等于接受任一张，也不会自动补图。')
        page = f'<!doctype html><html lang="zh-CN"><meta charset="utf-8"><title>Hex Agent 模型比较</title><style>{CSS}</style><main><h1>{esc(result["brief"]["title"])}</h1>{"".join(gallery)}{preference_html}<p>{esc(guidance)}</p><details><summary>设计与实际提示词</summary>{adaptation_html}{prompt_html}</details></main></html>'
        prompt_details = [f'<details>\n<summary>{target} 提示词</summary>\n\n{row["text"]}\n</details>'
                          for target, row in result['prompts'].items()]
        parts = comparison_parts + [guidance, md_escape(acceptance) if relative else acceptance, result['notice'],
                                    '## 设计与模型写法', adaptation_md, *prompt_details]
    if result.get('delivery_asset'):
        history = '<details class="panel"><summary>原首稿与处理记录</summary><p>首稿未覆盖，原尺寸问题保留记录；本地处理没有再次调用生图模型。</p><a href="' + esc(trial['file']) + '">查看原首稿</a><p><a href="bounded-repair.json">查看完整处理记录</a></p></details>'
        page = page.replace('</main>', history + '</main>', 1)
        parts.extend(['## 原首稿保留', f"[查看原首稿]({trial['file']})", '[本地变换回执](bounded-repair.json)'])
    workflow_md, workflow_html = render_workflow_review(result)
    parts.extend(workflow_md)
    if workflow_html:
        page = page.replace('</main>', workflow_html + '</main>', 1)
    atomic_text(run/'result.html', page)
    atomic_text(run/'result.md', '\n\n'.join(parts)+'\n')
    exported = ['result.html', 'result.md', *[f'prompts/{target}.txt' for target in result['prompts']]]
    if result.get('delivery_asset'):
        exported += [display['file'], trial['file'], 'bounded-repair.json']
    if adaptation_md:
        atomic_text(run/'design-explanation.md', adaptation_md)
        exported.append('design-explanation.md')
    write_json(run/'export-manifest.json', {'format': 'liu.aesthetic.export-manifest.v1',
               'result_digest': digest(result), 'files': {name: sha(within(run, name).read_bytes()) for name in exported}})


def export_result(result: dict, run: Path) -> None:
    """One result page, not a workflow UI. Escape all model/user strings."""
    adaptation_md, adaptation_html = render_adaptation_explanation(result)
    if result.get('delivery_asset'):
        from .bounded_repair import delivery_asset
        if result['delivery_asset'] != delivery_asset(run):
            raise AgentError('待导出派生图与原run的变换证据不一致')
    write_json(run / "result.json", result)
    from .visual_decision import record_only
    if record_only(result.get('acceptance', {})) and result.get('trials'):
        _export_production(result, run)
        return
    d, spec = result['design'], result['spec']
    presentation = build_presentation(run, result=result)
    write_json(run / 'presentation.json', presentation)
    label = presentation['facets']['execution']
    demo = result['producer'].startswith('authored_demo')
    human_acceptance = result.get('human_acceptance', 'not_recorded')
    badge = '安装演示 · 作者编写 · 没有生成图片' if demo else label
    tested = result['selected_trial_round']
    badge_class = 'neutral' if demo or not result['trials'] else 'warn'
    reference_usage = result.get('reference_usage', {})
    authority_text = presentation['facets']['review']
    acceptance_text = presentation['facets']['user_decision']
    summary_lines = presentation['summary']
    next_step = presentation['next_action']
    summary_html = ('<section class="panel"><h2>本轮状态</h2>'
                    + ''.join('<p>' + esc(line) + '</p>' for line in summary_lines)
                    + '<p><b>下一步：</b>' + esc(next_step) + '</p></section>')
    original_request = result['acceptance']['request'] if result['acceptance'].get('source_contract_version') == 1 else result['brief']['request']
    parts = [f"# {result['brief']['title']}", f"**{badge}**", '## 本轮状态',
             *[md_escape(line) for line in summary_lines], '下一步：' + md_escape(next_step), result['notice'],
             '## 原始需求', original_request,
             '## 审核边界', f'复查证据：{authority_text}', f'用户选择：{md_escape(acceptance_text)}', REVIEW_FILE_BOUNDARY,
             '## 本次视觉设计', d['expression'],
             '### 主体与环境', d['subject_environment_relation'],
             '### 观看顺序', d['reading_order'],
             '### 取景选择', *[f"{k}：{v}" for k,v in d['camera'].items() if v is not None]]
    grammar = d.get('spatial_grammar')
    if grammar:
        parts += ['### 实验性空间语法', f"模型：{grammar['spatial_model']}", f"观看路径：{grammar['viewer_path']}",
                  f"留白功能：{grammar['negative_space_function']}", f"动势：{'、'.join(grammar['dominant_vectors'])}",
                  f"层次：{grammar['layer_plan']}", f"尺度关系：{grammar['scale_relation']}",
                  f"时间模型：{grammar['time_model']}", f"表面范围：{grammar['surface_scope']}",
                  '状态：实验性；只记录结构操作，不代表掌握某一绘画传统或复刻名作。']
    for c in d['choices']:
        parts += [f"### {c['goal']}", c['action'], '配合：'+c['cooperation'], '代价：'+c['tradeoff']]
    if adaptation_md:
        parts.append('<details>\n<summary>从需求到画面：设计与模型写法</summary>\n\n'
                     + adaptation_md + '\n</details>')
    narrative = d.get('narrative')
    if narrative:
        parts += ['## 影片镜头意图（当前只交付静帧方案）'] + [f"{k}：{v}" for k,v in narrative.items()]
    labels = {'composition':'构图','lighting':'光影','color':'色彩','material':'材质','capture':'成像'}
    model_sections, tabs = [], []
    (run/'prompts').mkdir(exist_ok=True)
    for index,(target,p) in enumerate(result['prompts'].items()):
        atomic_text(run/'prompts'/(target+'.txt'),p['text']+'\n')
        parts += [f"## {target}", p['text'], '### 使用设置', *['- '+x for x in p['setup_notes']],
                  '### 模型验证边界', display_label('model_test', p['target_model_test']), *['- '+x for x in p['limitations']]]
        ident=f'model{index}'
        tabs.append(f'<button data-tab="{ident}" class="{"active" if index==0 else ""}">{esc(target)}</button>')
        model_sections.append(f'<section class="model" id="{ident}"><div class="row"><h3>{esc(target)} <span class="caption">档案 v{esc(p["profile_version"])} · {esc(display_label("profile", p["profile_status"]))}</span></h3><button class="copy" data-copy="text{index}">复制提示词</button></div><div class="prompt"><pre id="text{index}">{esc(p["text"])}</pre></div><details><summary>使用端设置 / 上传顺序</summary>{bullets(p["setup_notes"])}</details><details><summary>验证边界</summary><p class="caption">{esc(display_label("model_test", p["target_model_test"]))}</p>{bullets(p["limitations"])}</details></section>')
    design_html = f'<p>{esc(d["subject_environment_relation"])}</p><p class="caption">观看顺序：{esc(d["reading_order"])}</p>'
    for c in d['choices']:
        design_html += f'<div class="fact"><b>{esc(c["goal"])}</b>{esc(c["action"])}<p class="small">配合：{esc(c["cooperation"])}</p><p class="caption">取舍：{esc(c["tradeoff"])}</p></div>'
    design_html += adaptation_html
    if grammar:
        grammar_items = [
            ('空间模型', grammar['spatial_model']), ('观看路径', grammar['viewer_path']),
            ('留白功能', grammar['negative_space_function']), ('主导动势', '、'.join(grammar['dominant_vectors'])),
            ('层次计划', grammar['layer_plan']), ('尺度关系', grammar['scale_relation']),
            ('时间模型', grammar['time_model']), ('表面范围', grammar['surface_scope']),
        ]
        design_html += '<details><summary>实验性空间语法（结构操作，不是风格标签）</summary>'
        design_html += ''.join(f'<div class="fact"><b>{esc(label)}</b>{esc(value)}</div>' for label, value in grammar_items)
        design_html += '<p class="caption">实验性；不代表掌握某一绘画传统或复刻名作。</p></details>'
    cam_labels={'shot_size':'景别','viewpoint_and_distance':'观察位置与距离','focal_equivalent_mm':'等效焦距提案（mm，非测量值）','framing_and_scale':'取入范围与比例','focus_and_depth':'清晰范围','reason':'为什么这样取景'}
    design_html += '<details><summary>镜头与取景依据</summary>'+''.join(f'<div class="fact"><b>{esc(cam_labels[k])}</b>{esc(v)}</div>' for k,v in d['camera'].items() if v is not None)+'</details>'
    design_html += '<details><summary>执行用画面说明</summary>'+''.join(f'<div class="fact"><b>{label}</b>{esc(spec[key])}</div>' for key,label in labels.items())+'</details>'
    if narrative:
        nl={'scene_function':'场景功能','shot_function':'本镜头任务','point_of_view':'观看归属','previous_link':'承接','next_link':'后续','information_reveal':'信息揭示','staging':'调度','camera_movement':'运镜提案','rhythm':'节奏提案','continuity':'连续性要求','unknown_context':'未知上下文'}
        design_html += '<details open><summary>影片镜头关系（单帧不验证时间）</summary>'+''.join(f'<div class="fact"><b>{nl[k]}</b>{esc(v)}</div>' for k,v in narrative.items())+'</details>'
    design_html += '<details><summary>假设与未确认项</summary>'+bullets(d['assumptions']+spec['assumptions']+result['need']['questions'])+'</details>'
    refs_html=''
    findings={x['id']:x for x in result.get('reference_analysis',{}).get('findings',[])}
    usage_by_id={x.get('id'):x for x in (reference_usage.get('attached_references',[]) if isinstance(reference_usage,dict) else [])}
    for r in result['references']:
        f=findings.get(r['id'],{})
        url=r.get('source_url','')
        source=f'<a href="{esc(url)}" rel="noreferrer noopener">来源页面</a>' if url.startswith('https://') else ''
        usage=usage_by_id.get(r['id'],{})
        usage_text={'generation':'允许用于生成','evaluation_only':'仅分析/对照'}.get(usage.get('usage', 'evaluation_only'),'用途未记录')
        state_text={'generation_input':'已进入生成输入','reference_attached_not_submitted':'已附加，未提交'}.get(usage.get('state','reference_attached_not_submitted'),'状态未记录')
        refs_html+=f'<figure><img alt="本次参考 {esc(r["id"])}" src="{thumbnail(within(run,r["file"]))}"><figcaption class="caption">{esc(r["id"])} · {esc(display_label("role", r["role"]))} · {usage_text} · {state_text}<br>{esc(r["note"])}<br>{esc(r.get("credit",""))} {source}</figcaption><details><summary>实际观察与借用范围</summary><p class="small">{esc(f.get("visible_facts","尚无观察记录"))}</p>{bullets(f.get("transfer",[]))}<p class="caption">不确定：{esc(f.get("uncertainty","未记录"))}</p></details></figure>'
    retrieval = result.get('visual_retrieval', {})
    retrieved_ids = reference_usage.get('retrieved_card_ids', []) if isinstance(reference_usage, dict) else []
    actual_inputs = reference_usage.get('generation_inputs') if isinstance(reference_usage, dict) else None
    attached = reference_usage.get('attached_references') if isinstance(reference_usage, dict) else None
    submitted = reference_usage.get('submission_evidence', 'not_submitted') if isinstance(reference_usage, dict) else 'not_submitted'
    parts += ['## 参考输入记录', f'检索候选卡片：{len(retrieved_ids)} 张（文字检索结果，不代表已采用）',
              f'已附加参考：{len(attached) if isinstance(attached, list) else len(result.get("references", []))} 张（任务内工作副本，不代表已提交）',
              '实际生成输入：' + ('、'.join(x['id'] for x in actual_inputs) if actual_inputs else '未记录'),
              '提交证据：' + display_label('submission', submitted), '视觉影响：未由输入清单证明']
    tests_html=''
    for t in result['trials']:
        active=t['round']==tested
        review=t.get('review') or {}
        parts += [f"## 候选 {t['round']}"+('（当前选中）' if active else ''),
                  '实际模型标签：'+t.get('model_id','未知'), '来源：'+display_label('origin', t.get('origin')),
                  '复查建议：'+(display_label('decision', review.get('decision')) if review else '未复查')]
        holistic_md, holistic_html = render_holistic_review(review)
        parts += holistic_md
        rows=''
        for item in review.get('items',[]):
            rows+=f'<div class="fact"><b>{esc(item["criterion_id"])} · {esc(display_label("verdict", item["verdict"]))}</b>{esc(item["candidate_evidence"])}<p class="caption">对照：{esc(item["reference_evidence"])}</p></div>'
            parts += [f"{item['criterion_id']} / {display_label('verdict', item['verdict'])}：{item['candidate_evidence']}", '对照：'+item['reference_evidence']]
        comparison_labels = {'composition': '构图', 'light_and_material': '光色与材质',
                             'expression': '表达', 'decisive_gap': '主要差距', 'verdict': '模型判断'}
        for comparison in review.get('baseline_comparisons', []):
            for key, title in comparison_labels.items():
                value = comparison.get(key, '')
                if key == 'verdict':
                    value = display_label('baseline', value)
                parts += [f'对照 {comparison["reference_id"]} · {title}：{value}']
                rows += f'<div class="fact"><b>对照 {esc(comparison["reference_id"])} · {esc(title)}</b>{esc(value)}</div>'
        change_label = '待核实的下一步' if review.get('decision') == 'uncertain' else '优先修改'
        reason = review.get('diagnosis_reason', '')
        parts += ['复查原因：'+reason, '保留：'+'；'.join(review.get('preserve',[])),
                  change_label+'：'+'；'.join(review.get('changes',[]))]
        review_html = holistic_html + f'<p class="small">复查原因：{esc(reason)}</p>'
        if review.get('preserve'):
            review_html += '<h4>保留</h4>' + bullets(review['preserve'])
        if review.get('changes'):
            review_html += '<h4>' + change_label + '</h4>' + bullets(review['changes'])
        tests_html+=f'<section class="panel"><h3>候选 {t["round"]} {"· 当前选中" if active else "· 历史版本"}</h3><img alt="候选 {t["round"]}，用户选择见本轮状态" src="{thumbnail(within(run,t["file"]))}"><p class="caption">{esc(t.get("model_id","未知"))} · {esc(display_label("origin", t.get("origin")))} · {esc(display_label("decision", review.get("decision")) if review else "未完成复查")}</p>{review_html}<details><summary>复查报告中的图像描述</summary>{rows}</details><details><summary>此图实际使用的提示词</summary><pre class="prompt">{esc(t["prompt"])}</pre></details></section>'
    pair = result.get('comparison')
    if pair:
        reasons = {'comparison_uncertain_keep_previous': '两张图的取舍仍不确定，暂保留上一版供比较。',
                   'revision_losses_need_resolution': '新版仍有未解决的退步，暂保留上一版；这不表示旧版已通过验收。',
                   'absolute_criteria': '优先保留通过固定检查的候选，审美仍待用户判断。',
                   'pair_evidence': '依据本轮比较暂选候选，尚未代表用户接受。'}
        explanation = reasons.get(pair.get('selection_reason'), '本轮成对比较，结论仍待用户判断。')
        names = {c['id']: c['question'] for c in result['acceptance']['checks']}
        losses = list(pair.get('regressions', [])) + [names.get(ident, ident) for ident in pair.get('regressed_criterion_ids', [])]
        parts += ['## 两版取舍', explanation, pair.get('evidence', ''), '待解决的退步：']
        parts += ['- '+loss for loss in losses] if losses else ['未记录未解决退步；不代表已证明零损失。']
        tests_html += (f'<section class="panel"><h3>两版取舍</h3><p>{esc(explanation)}</p>'
                       f'<p>{esc(pair.get("evidence", ""))}</p><h4>待解决的退步</h4>'
                       + (bullets(losses) if losses else '<p class="caption">未记录未解决退步；不代表已证明零损失。</p>') + '</section>')
    questions=''.join(f'<div class="fact"><b>{esc(c["id"])} · {esc(display_label("dimension", c["dimension"]))}</b>{esc(c["question"])}<p class="caption">预期：{esc(c["expected"])}<br>不成立：{esc(c["failure"])}</p></div>' for c in result['acceptance']['checks'])
    used={m for c in d['choices'] for m in c['mechanism_ids']}
    sources=''.join(f'<p class="small">{esc(m["id"])} · {esc(m["title"])}</p>' for m in result['design_knowledge']['mechanisms'] if m['id'] in used)
    sources+='<p class="caption">理论启发不等于已在生成模型或你的任务中验证。来源与限定范围见 packet.json 的 design_knowledge。</p>'
    for s in result.get('research',{}).get('sources',[]):
        if s.get('url','').startswith('https://'):
            sources+=f'<p class="small"><a href="{esc(s["url"])}" rel="noreferrer noopener">{esc(s.get("title") or s["url"])}</a>（文字研究，不冒充看图）</p>'
    parts += ['## 本轮固定检查标准'] + [f"- {c['id']}：{c['question']} / 预期：{c['expected']} / 不成立：{c['failure']}" for c in result['acceptance']['checks']]
    parts += ['## 验证状态',label, f'复查证据：{authority_text}', f'用户选择：{acceptance_text}']
    reassessment = rejection_next_action(run, result)
    if reassessment:
        parts += ['## 下一步：重审', reassessment['description']]
    uncertainty = uncertain_next_action(result) or (result.get('next_action') if human_acceptance == 'not_recorded' else None)
    if uncertainty:
        parts += ['## 下一步：核对复查原因', uncertainty['description']]
    input_lines = [f'检索卡片：{len(retrieved_ids)} 张；文字命中不代表图像已采用',
                   f'附加参考：{len(attached) if isinstance(attached, list) else 0} 张',
                   '生成输入：' + ('、'.join(x['id'] for x in actual_inputs) if actual_inputs else '未记录'),
                   '提交证据：' + display_label('submission', submitted),
                   '输入记录不能证明风格效果或用户认可']
    evidence_html = '<section class="panel"><h2>复查与参考输入</h2><p>'+esc(authority_text)+'</p><p>'+esc(acceptance_text)+'</p><p class="caption">'+esc(REVIEW_FILE_BOUNDARY)+'</p>'+bullets(input_lines)+'</section>'
    workflow_md, workflow_html = render_workflow_review(result)
    parts.extend(workflow_md)
    atomic_text(run/'result.md','\n\n'.join(parts)+'\n')
    html_text=f'''<!doctype html><html lang="zh-CN"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>{esc(result['brief']['title'])} · Hex Agent</title><style>{CSS}</style></head><body><main><div class="brand">Hex Agent / VISUAL INTENT TO IMAGE · 0.3</div><header class="hero"><span class="pill {badge_class}">{esc(badge)}</span><h1>{esc(result['brief']['title'])}</h1><p class="sub">{esc(d['expression'])}</p><p class="caption">{esc(result['created_at'])} · {esc(display_label('medium', result['brief']['medium']))} · {esc(display_label('domain', result['need']['domain']))}</p></header>{summary_html}<div class="layout"><div><section class="panel"><h2>本次提示词</h2><nav>{''.join(tabs)}</nav>{''.join(model_sections)}</section><section class="panel"><h2>画面为什么这样设计</h2>{design_html}<details><summary>本次使用的理论依据</summary>{sources}</details></section>{tests_html}</div><aside><section class="panel"><h2>你的原始需求</h2><p>{esc(result['brief']['request'])}</p><details open><summary>必须保持</summary>{bullets(spec['locked_facts'])}</details></section><section class="panel"><h2>真实完成状态</h2><p class="notice">{esc(label)}</p><p class="small">{esc(result['notice'])}</p><p class="caption">{esc(display_label('coverage', result['visual_coverage']['mode']))}</p>{bullets(result['profile_warnings'])}</section><section class="panel"><details><summary>出图前确定的检查标准</summary>{questions}</details></section>{'<section class="panel"><h2>本次实际参考</h2>'+refs_html+'</section>' if refs_html else '<section class="panel"><h2>参考状态</h2><p class="small">此任务尚无视觉参考。理论提案只能标记为探索，不能宣称达到某参考品质。</p></section>'}</aside></div><footer>本页是离线交付文件，不含外部字体或脚本。图片工具、最终视觉质量和跨模型一致性必须分别实测。</footer></main><div class="toast" role="status" aria-live="polite"></div><script>{JS}</script></body></html>'''
    html_text = html_text.replace('<aside>', '<aside>' + evidence_html, 1)
    if workflow_html:
        html_text = html_text.replace('</main>', workflow_html + '</main>', 1)
    if result['acceptance'].get('source_contract_version') == 1:
        html_text = html_text.replace('<h2>你的原始需求</h2><p>' + esc(result['brief']['request']) + '</p>',
                                     '<h2>你的原始需求</h2><p>' + esc(original_request) + '</p>')
    if reassessment:
        guidance = '<section class="panel"><h2>下一步：重审</h2><p>' + esc(reassessment['description']) + '</p></section>'
        html_text = html_text.replace('<div class="layout">', guidance + '<div class="layout">', 1)
    if uncertainty:
        guidance = '<section class="panel"><h2>下一步：核对复查原因</h2><p>' + esc(uncertainty['description']).replace('\n', '<br>') + '</p></section>'
        html_text = html_text.replace('<div class="layout">', guidance + '<div class="layout">', 1)
    atomic_text(run/'result.html',html_text)
    exported = ['result.html', 'result.md', *[f'prompts/{target}.txt' for target in result['prompts']]]
    if adaptation_md:
        atomic_text(run/'design-explanation.md', adaptation_md)
        exported.append('design-explanation.md')
    write_json(run/'export-manifest.json', {
        'format': 'liu.aesthetic.export-manifest.v1', 'result_digest': digest(result),
        'files': {name: sha(within(run, name).read_bytes()) for name in exported},
    })
