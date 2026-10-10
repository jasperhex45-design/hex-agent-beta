"""Small task-derived method inputs; no second plan, acceptance or budget state.

The rules select and specialize the existing production instructions. References
and user content remain data in the payload, never interpolated into system text.
"""
from __future__ import annotations

from copy import deepcopy

from .io import AgentError, digest


METHODS = {
    'understand-task': (
        '按当前原话确认用途、开放空间、锁定项及参考职责；先决定任务模式，再做视觉提案。身份图不自动锁机位，编辑范围不能由风格参考扩大。',
        'PRODUCTION_PARSE'),
    'observe-roles': (
        '逐张先确认像素与本题职责，再记录可见现象、最有表现力的关系及拉平后的损失，区分身份内容与可迁移方法。光色承担表达时看其在主体、材料和环境间怎样共同作用，不只摘姿势和色名。没有当前图像摘要匹配的观察，不把旧观察当成本次已看图；可读和可上传分别判断。',
        'PRODUCTION_OBSERVE'),
    'open-design': (
        '开放创作：从本题用途确定观看重点，机位、尺度、裁切、主体状态与光色按表达自主选择；清楚、完整或戏剧性都不是默认目标，未锁定选择可撤回。无需参考也可设计，不虚构观察，不先列风格名再找理由。',
        'PRODUCTION_DESIGN'),
    'open-design-v2': (
        '开放创作：先按 Need.visual_research 判断本题研究职责。需要研究时，实际查找并查看解决当前视觉问题的案例，比较有不同表达关系的方向，再将选定方向写入一个 Plan。参考须改变具体选择；观看位置、主体状态、尺度与光色按表达决定，不默认全身平视或一律斜角。选择是可撤回假设，不升级用户硬锁或整体目标；没有车型或身份图不构成免研究理由。用户已指定方向、范围内修改或明确禁止研究时遵从有来源的判断，不强造研究或用户问卷。',
        'PRODUCTION_DESIGN'),
    'reference-transfer': (
        '参考迁移：用当前已核对观察把“可见现象 → 本题允许借用的主导关系 → choices.action/cooperation → checks.failure”连接起来。舍弃主导关系须有本题依据、明确损失与新的表达承接，不能默认清晰自然更好；宿主提案不升级用户硬锁。按本题用途调整未锁定景别与媒介，保留用户指定的身份与复刻范围；未被参考职责或用户要求授权的身份、场景不照搬。缺少真实观察先补观察，不用标题代替。',
        'PRODUCTION_DESIGN'),
    'scoped-edit': (
        '范围内编辑：以真实画布为基准，仅让当前 change_scope、preserve、must_keep 与 allowed_operations 决定可改内容。choices 写具体变化和与保留项的关系；checks 同时检查改善与范围外退步。原画面范围外的已成立部分沿用，不改变未授权修改的场景或机位。',
        'PRODUCTION_DESIGN'),
    'model-compile': (
        '模型编译：每个目标从同一 Need/Plan 独立编写完整正文，实际 model_profiles 与 compilation_runtimes 决定语言、次序及可用控制。逐项对应 choices、锁定项和真实参考顺序；模型不支持的范围写入 limitations，不自行换模型或删需求。',
        'PRODUCTION_COMPILE'),
    'result-review': (
        '结果观察：据原话明确目标，实际缩看整体、正常大小看关系、按需放大局部并返回整体，再核对 checks；既有 evidence 只记录实际做过的观察与界限。局部全 met 不证明目标成立，自选拍法未实现也不自动等于表达失败；保留真实硬锁，无目标级修改依据则记录不确定，不强修有效画面。只提供研究观察，不代替接受或授权重生成。',
        'RECORD_ONLY_REVIEW'),
    'scoped-repair': (
        '反馈修复：核对绑定当前图的具体差距及 preserve；先定位需求、参考、设计、编译、输入或结果哪层失效，只修改有证据的一两个重点。缺乏图像或反馈依据时先核对，不以机器解释冒充用户原话，不扩大续改范围或许可。',
        'REVISE'),
    'identity-boundary': (
        '身份图只决定本题主体事实；摄影或风格案例只提供被允许的方法。多视图身份保持不等于全部机位锁定，指定单图编辑的保留项优先。',
        'PRODUCTION_DESIGN'),
    'reference-boundary': (
        '只借用参考被指定且观察支持的维度；本题目的与媒介优先。负例不转成正向样式，evaluation_only 与无上传授权素材不进入生成图片列表；文字理解不等于上传许可。',
        'PRODUCTION_OBSERVE'),
    'color-scope': (
        '仅调色与曝光：可改变色相、明度和色彩关系；保持原机位、主体形态、构图、光向、天气和阴影结构。不要用换光源或换环境实现调色。',
        'PRODUCTION_DESIGN'),
    'relight-scope': (
        '重新布光：只调整现有允许的色彩、曝光和光照；保留主体形态、构图和天气。受光与反射跟随新光照，不能借重布光重新安排主体或机位。',
        'PRODUCTION_DESIGN'),
    'scene-scope': (
        '场景替换：在明确允许的范围内替换或调整场景；保持主体身份、结构、原机位与主体比例。环境光与反射的调整仍须服从 allowed_operations，不改变锁定主体或观看关系。',
        'PRODUCTION_DESIGN'),
    'revision-boundary': (
        '续改编译必须绑定当前 revision 中同一父图、preserve 和 changes；将保留与变化分别转成完整正文。父图不等于任意风格参考，不以新首图代替已授权的续改。',
        'PRODUCTION_COMPILE'),
}

CAR_EDIT_INSTRUCTION = '''
汽车摄影候选 car-photography-v1 的范围内编辑：沿用当前车辆、画布与已确认摄影关系，只执行本题允许的变化。不要因“更有冲击”重选环境、机位、车身比例或构图；只有原话明确纳入修改范围的关系才可改变。
反射、车漆及玻璃、橡胶、金属的调整服从当前编辑范围；仅调色时不得重设光向与阴影结构。沿用既有 choices/checks 记录改善与保留，不增加第二轮设计或自动补图。结果仍需实际看图与用户判断。
'''

_ALIASES = {'strategy': 'design', 'compile': 'model_compile', 'evaluate': 'review'}
_STAGES = {'parse', 'observe', 'design', 'model_compile', 'review', 'revise', 'compare', 'research'}


def enabled(packet: dict) -> bool:
    """Only newly marked production tasks opt in; old packets are not migrated."""
    if 'method_context_version' not in packet:
        return False
    value = packet['method_context_version']
    if type(value) is not int or value != 1:
        raise AgentError('method_context_version 无效；不能静默回退方法分支')
    from .visual_decision import policy
    if policy(packet) is None:
        raise AgentError('方法上下文只用于显式生产任务；不能静默迁移 legacy')
    return True


def _task_mode(packet: dict) -> str:
    brief = packet.get('brief') or {}
    understanding = (packet.get('need') or {}).get('understanding') or {}
    mode = understanding.get('task_mode', 'open_creation')
    # The explicit editing scope must not be broadened by a stale interpretation.
    return 'local_edit' if brief.get('scope', 'create') != 'create' else mode


def _method(ident: str) -> dict:
    instruction, source_symbol = METHODS[ident]
    return {'id': ident, 'instruction': instruction,
            'source': {'file': 'liu_agent/method_context.py', 'symbol': 'METHODS', 'key': ident},
            'derived_from': {'file': 'liu_agent/instructions.py', 'symbol': source_symbol}}


def _reference_roles(packet: dict) -> list[dict]:
    from .reference_gate import generation_eligible
    analysis = packet.get('reference_analysis') or {}
    findings = {f['id']: f for f in analysis.get('findings', [])}
    observed = {r['id']: r.get('sha256') for r in analysis.get('input_images', [])}
    result = []
    for ref in packet.get('references', []):
        finding = findings.get(ref['id']) or {}
        current = bool(ref.get('sha256') and finding.get('seen')
                       and observed.get(ref['id']) == ref['sha256'])
        evaluation_only = bool(ref.get('evaluation_only') or ref['role'] == 'aesthetic_negative')
        eligible = generation_eligible(packet, ref)
        result.append({'id': ref['id'], 'declared_role': ref['role'],
                       'sha256': ref.get('sha256'),
                       'observation_state': 'current' if current else 'not_current',
                       'observed_role': finding.get('role_in_task') if current else None,
                       'visible_facts': finding.get('visible_facts') if current else None,
                       'transfer': deepcopy(finding.get('transfer', [])) if current else [],
                       'do_not_transfer': deepcopy(finding.get('do_not_transfer', [])) if current else [],
                       'evaluation_only': evaluation_only,
                       'upload_authorized': ref.get('rights') == 'user_authorized',
                       'generation_eligible': eligible})
    return result


def build_method_context(packet: dict, stage: str, *, revision: dict | None = None) -> dict | None:
    """Derive the exact method branch and its evidence without mutating the task.

    ``revision`` is the caller's existing compilation/revision context, not a new
    record. Sources identify actual local Python symbols. Availability of this
    payload does not establish host reading, semantic understanding or quality.
    """
    if not enabled(packet):
        return None
    name = stage.lower() if isinstance(stage, str) else ''
    name = _ALIASES.get(name, name)
    if name not in _STAGES:
        raise AgentError('未知方法阶段：' + str(stage))
    if name in ('compare', 'research'):
        return None
    need = packet.get('need') or {}
    brief = packet.get('brief') or {}
    understanding = need.get('understanding') or {}
    mode = _task_mode(packet)
    if mode not in ('open_creation', 'reference_transfer', 'local_edit'):
        raise AgentError('方法选择需要有效 task_mode；先核对当前需求')
    refs = _reference_roles(packet)
    design_method = {'open_creation': 'open-design', 'reference_transfer': 'reference-transfer',
                     'local_edit': 'scoped-edit'}[mode]
    from .visual_research import enabled as research_enabled
    research_versioned = research_enabled(packet)
    if research_versioned and mode == 'open_creation':
        design_method = 'open-design-v2'
    primary = {'parse': 'understand-task', 'observe': 'observe-roles', 'design': design_method,
               'model_compile': 'model-compile', 'review': 'result-review', 'revise': 'scoped-repair'}[name]
    supporting = []
    if name in ('observe', 'design', 'model_compile', 'revise'):
        if any(r['declared_role'] == 'identity' and not r['evaluation_only'] for r in refs):
            supporting.append('identity-boundary')
        if any(r['declared_role'] != 'identity' or r['evaluation_only'] for r in refs):
            supporting.append('reference-boundary')
        if name != 'observe' and mode == 'local_edit':
            scope_method = {'color_only': 'color-scope', 'relight': 'relight-scope',
                            'scene': 'scene-scope'}.get(brief.get('scope'))
            if scope_method:
                supporting.append(scope_method)
        if revision is not None:
            supporting.append('revision-boundary')
    basis = {'scope': brief.get('scope', 'create'), 'domain': need.get('domain', brief.get('domain')),
             'medium': brief.get('medium', 'still'), 'purpose': understanding.get('purpose'),
             'change_scope': understanding.get('change_scope'),
             'preserve': deepcopy(understanding.get('preserve', [])),
             'must_keep': deepcopy(need.get('must_keep', [])),
             'allowed_operations': deepcopy(packet.get('allowed_operations', [])),
             'revision': deepcopy(revision)}
    if research_versioned:
        basis['visual_research_version'] = packet['visual_research_version']
        basis['visual_research'] = deepcopy(need.get('visual_research'))
    selected = [_method(primary), *[_method(ident) for ident in supporting]]
    from .co_creation import enabled as interaction_enabled
    if interaction_enabled(packet):
        basis['interaction_policy'] = packet['interaction_policy']
        basis['direction_review'] = deepcopy(need.get('direction_review'))
    return {'format': 'cl-hex.method-context.v1', 'authority': 'derived_from_current_task',
            'stage': name, 'task_mode': mode, 'primary_method': selected[0],
            'supporting_methods': selected[1:], 'selection_basis': basis,
            'reference_roles': refs, 'sources': [method['source'] for method in selected],
            'input_digest': digest({'task_mode': mode, 'selection_basis': basis, 'reference_roles': refs}),
            'notice': '这是当前阶段提供的方法与来源，不是宿主阅读、理解、实际采用或视觉验收的证明；需求、方案、调用和反馈仍用原有记录。'}


def method_instruction(packet: dict, stage: str) -> str:
    """Return only trusted static rules, never user/reference strings."""
    context = build_method_context(packet, stage)
    if context is None:
        return ''
    methods = [context['primary_method'], *context['supporting_methods']]
    return '\n本阶段按当前任务选择的方法（沿用原输出结构）：\n' + '\n'.join(
        method['instruction'] for method in methods) + '\n'
