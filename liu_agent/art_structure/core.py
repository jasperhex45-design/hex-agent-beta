"""Translate an artistic hypothesis into the existing Plan/checks/compilation.

No images are fetched, generated, scored or silently promoted to references here.
"""
from __future__ import annotations
from pathlib import Path
from ..contracts import Compilation
from ..io import AgentError, digest, read_json, write_json, within
from .models import ArtPlan

TAG = 'liu.art-structure:'
DESIGN_GUIDE = '''\n艺术结构实验：返回ArtPlan，plan沿用原Plan合同。
案例是馆方文字及本项目设计假设，不是你已看过的图片。不可宣称看过，不能作为生成基准ID。
先比较可用空间方法，再选择最多两个；不适用可返回spatial_plan=null并说明原因。
空间计划必须包含实体、关系、虚空间用途、预计观看路径、可见动作与失败条件。
每个decision绑定plan.design中真实存在的choice_id与check_id；不虚构mechanism_ids。
动作双语描述画面关系，不写画家/作品名字；不要把多视点造成的错误用于合理化身份走样。
protected_facts逐项保留need.must_keep；operations不可越过本次编辑范围。
每个decision逐项复制所选案例的avoid_when到avoid_when_considered，并用applicability_evidence说明本任务为何未触发这些边界；不能只写“适用”。
非create任务只用camera空间，不改变原有投影；非摄影创作不虚构镜头毫米数，填null。
明确可迁移关系和不可复制的题材/符号；感受与观看次序仅是待验证假设。
修订保留冻结的check id、expected、failure和空间模式，仅修改执行动作；不降标准。'''


def validate_art_plan(art: ArtPlan, context: dict, payload: dict) -> None:
    spatial = art.spatial_plan
    if spatial is None:
        if not art.not_used_reason.strip():
            raise AgentError('没有采用艺术方法时须说明原因')
        if art.plan.design.spatial_grammar is not None:
            raise AgentError('不采用艺术结构时不能同时保留spatial_grammar')
        return
    hits = {x['case']['id']: x['case'] for x in context['hits']}
    hit_links = {x['case']['id']: x.get('grammar_links', {}) for x in context['hits']}
    if spatial.protected_facts != payload['need']['must_keep']:
        raise AgentError('空间计划必须完整同序保留当前硬约束')
    if payload['brief']['scope'] != 'create' and spatial.mode != 'camera':
        raise AgentError('局部编辑不得用新空间体系改变原投影')
    if spatial.mode != 'camera' and art.plan.design.camera.focal_equivalent_mm is not None:
        raise AgentError('非摄影空间不填写虚构的焦距')
    ids = [e.id for e in spatial.entities]
    if len(ids) != len(set(ids)):
        raise AgentError('空间实体ID重复')
    for relation in spatial.relations:
        if relation.source not in ids or relation.target not in ids or relation.source == relation.target:
            raise AgentError('空间关系引用缺失实体或自指')
    choices = {c.id: c for c in art.plan.design.choices}
    checks = {c.id: c for c in art.plan.design.checks}
    used_cases, used_choices, used_checks = set(), set(), set()
    for d in spatial.decisions:
        case = hits.get(d.case_id)
        if case is None:
            raise AgentError('只能采用本任务实际检索命中的艺术案例')
        if d.case_id in used_cases or d.choice_id in used_choices or d.check_id in used_checks:
            raise AgentError('艺术决定不能重复覆盖同一案例/选择/检查')
        used_cases.add(d.case_id); used_choices.add(d.choice_id); used_checks.add(d.check_id)
        if spatial.mode not in case['modes']:
            raise AgentError('方法与空间模式不匹配')
        if (d.operation not in payload['allowed_operations'] or d.operation not in case['operations']
                or d.operation not in art.plan.spec.operations):
            raise AgentError('艺术动作越过编辑范围或方法适用范围')
        if payload['brief']['scope'] != 'create' and d.operation in ('geometry', 'layout'):
            raise AgentError('局部编辑的艺术转译不得改变几何或版式投影')
        if d.choice_id not in choices or d.check_id not in checks or choices[d.choice_id].criterion_id != d.check_id:
            raise AgentError('艺术决定必须连接真实设计选择与同一检查项')
        if d.check_id.startswith(('hard.', 'avoid.', 'template.')):
            raise AgentError('艺术方法不能覆盖硬约束或保留检查ID')
        if set(d.do_not_transfer) != set(case['do_not_transfer']):
            raise AgentError('不可迁移项必须完整保留来源案例的全部边界')
        if (len(d.avoid_when_considered) != len(set(d.avoid_when_considered))
                or set(d.avoid_when_considered) != set(case['avoid_when'])):
            raise AgentError('适用边界必须完整复制来源案例的avoid_when')
        if not d.applicability_evidence.strip():
            raise AgentError('适用边界必须写当前任务的可见依据，不能只写适用')
        for name in (case['artist'], case['title']):
            if name.casefold() in (d.action_zh + '\n' + d.action_en).casefold():
                raise AgentError('将画家/作品名称转成具体动作，不作为生成指令')
        if any(not s.strip() for s in (d.action_zh, d.action_en, d.expected, d.failure, d.counterfactual_test)):
            raise AgentError('艺术动作或可见检查为空')
    grammar = art.plan.design.spatial_grammar
    if grammar is not None:
        available = {gid for d in spatial.decisions
                     for gid in hit_links.get(d.case_id, {}).get('grammar_ids', [])}
        if grammar.id not in available:
            raise AgentError('原生空间语法必须与本次采用案例的显式映射一致')
        native_grammars = {g['id']: g for g in payload.get('design_knowledge', {}).get('cultural_grammars', [])}
        if native_grammars and grammar.source_digest != native_grammars.get(grammar.id, {}).get('source_digest'):
            raise AgentError('艺术计划中的空间语法不是本次任务的规范卡片快照')


def projection(art: ArtPlan, context: dict, payload: dict):
    """Project to native Plan. Original validators still run afterwards."""
    validate_art_plan(art, context, payload)
    plan = art.plan.model_copy(deep=True)
    plan.design.assumptions = [x for x in plan.design.assumptions if not x.startswith(TAG)]
    if art.spatial_plan is None:
        return plan, None
    s = art.spatial_plan
    # The proposed reading path is design intent, not measured eye tracking.
    plan.design.reading_order = '预计观看路径（待看图验证）：' + ' → '.join(s.reading_path)
    choices, checks = {c.id: c for c in plan.design.choices}, {c.id: c for c in plan.design.checks}
    cases = {x['case']['id']: x['case'] for x in context['hits']}
    for d in s.decisions:
        choices[d.choice_id].action = d.action_zh
        checks[d.check_id].expected = d.expected
        checks[d.check_id].failure = d.failure
    # Keep the selected graph and its source snapshots with the exact Plan.
    record = {'format': 'liu.spatial-plan-record.v1', 'spatial_plan': s.model_dump(),
              'selected_spatial_grammar': (art.plan.design.spatial_grammar.model_dump()
                                           if art.plan.design.spatial_grammar is not None else None),
              'source_cases': [cases[d.case_id] for d in s.decisions],
              'source_context_digest': digest(context), 'hypothesis_not_quality_proof': True}
    key = digest(record)
    plan.design.assumptions.append(TAG + key)
    return plan, {**record, 'digest': key}


def compile_actions(compilation: Compilation, record: dict | None, profiles: dict, *,
                    append_missing: bool = True) -> Compilation:
    result = compilation.model_copy(deep=True)
    if record is None:
        return result
    for row in result.prompts:
        profile = profiles.get(row.target)
        if profile is None or profile.get('language') not in ('zh', 'en'):
            raise AgentError('缺少目标模型的明确编译语言')
        language = profile['language']
        actions = [d['action_en' if language == 'en' else 'action_zh']
                   for d in record['spatial_plan']['decisions']]
        missing = [action for action in actions if action not in row.text]
        if missing and append_missing:
            row.text = row.text.rstrip() + '\n\n' + ('Spatial actions: ' if language == 'en' else '空间动作：') + ' '.join(missing)
        names = [name for case in record.get('source_cases', [])
                 for name in (case.get('artist', ''), case.get('title', '')) if name.strip()]
        if any(name.casefold() in row.text.casefold() for name in names):
            raise AgentError('实际提示词不能把研究案例的画家或作品名当作生成指令')
        if len(row.text) > 18000:
            raise AgentError('编译后提示词过长；应精简正文，不截掉空间动作或硬约束')
    return result


def store_record(run: Path, record: dict | None) -> None:
    if record is None:
        return
    key = record['digest']
    path = within(run, 'art-structure/plans/' + key + '.json')
    if path.exists():
        if read_json(path) != record:
            raise AgentError('不可改写已有空间计划快照')
    else:
        write_json(path, record)


def record_for_design(run: Path, design: dict) -> dict | None:
    keys = [x[len(TAG):] for x in design.get('assumptions', []) if x.startswith(TAG)]
    if not keys:
        return None
    import re
    if len(keys) != 1 or not re.fullmatch(r'[0-9a-f]{64}', keys[0]):
        raise AgentError('空间计划引用无效')
    record = read_json(within(run, 'art-structure/plans/' + keys[0] + '.json'))
    body = {k:v for k,v in record.items() if k != 'digest'}
    if record.get('digest') != keys[0] or digest(body) != keys[0]:
        raise AgentError('空间计划快照摘要不匹配')
    return record


def frozen_spatial_contract(record: dict | None) -> dict | None:
    """Freeze the whole spatial graph; only bilingual execution wording may change."""
    if record is None:
        return None
    spatial = record['spatial_plan']
    return {
        'mode': spatial['mode'], 'experience_goal': spatial['experience_goal'],
        'entities': spatial['entities'], 'relations': spatial['relations'],
        'voids': spatial['voids'], 'reading_path': spatial['reading_path'],
        'protected_facts': spatial['protected_facts'], 'uncertainty': spatial['uncertainty'],
        'source_context_digest': record.get('source_context_digest'),
        'selected_spatial_grammar': record.get('selected_spatial_grammar'),
        'source_cases': record.get('source_cases', []),
        'decisions': [{k: d[k] for k in
                       ('case_id', 'choice_id', 'check_id', 'operation', 'adoption_reason', 'expected', 'failure',
                         'counterfactual_test', 'do_not_transfer', 'avoid_when_considered',
                         'applicability_evidence')}
                      for d in spatial['decisions']],
    }
