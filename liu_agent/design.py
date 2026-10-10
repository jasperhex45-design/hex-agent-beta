"""Small visual-design core. Checks completeness and provenance, never 'beauty scores'.

Purpose -> selected visual mechanisms -> coordinated choices -> fixed questions.
Still images and film-shot proposals share this code; a still cannot validate time.
"""
from __future__ import annotations
from pathlib import Path
from .contracts import DesignPlan, VisualSpec, ReferenceAnalysis, ImageEval
from .io import AgentError, read_json, digest, sha, within
from .retrieval import tokens
from .reference_gate import generation_eligible
from .host_references import research_references, observed_ids


def load_design(kb: Path) -> dict:
    root = kb / 'design'
    data = {name: read_json(root / f'{name}.json') for name in ('mechanisms', 'relations', 'sources')}
    cultural_sources_path = root / 'cultural_sources.json'
    data['cultural_sources'] = read_json(cultural_sources_path) if cultural_sources_path.exists() else []
    grammar_path = root / 'cultural_grammars.json'
    data['cultural_grammars'] = read_json(grammar_path) if grammar_path.exists() else []
    for grammar in data['cultural_grammars']:
        body = {k: v for k, v in grammar.items() if k != 'source_digest'}
        grammar['source_digest'] = digest(body)
    mids = [m['id'] for m in data['mechanisms']]
    sids = {s['id'] for s in data['sources']} | {s['id'] for s in data['cultural_sources']}
    if len(mids) != len(set(mids)):
        raise AgentError('设计机制ID重复')
    for m in data['mechanisms']:
        if not set(m['source_ids']).issubset(sids):
            raise AgentError('设计机制存在未知来源：' + m['id'])
        if not set(m.get('depends_on', [])).issubset(mids):
            raise AgentError('设计机制存在未知依赖：' + m['id'])
    for r in data['relations']:
        if not set(r['inputs']).issubset(mids):
            raise AgentError('设计组合关系存在未知机制：' + r['id'])
    gids = [g['id'] for g in data['cultural_grammars']]
    if len(gids) != len(set(gids)):
        raise AgentError('跨文化结构语法ID重复')
    for grammar in data['cultural_grammars']:
        if not set(grammar.get('source_ids', [])).issubset(sids):
            raise AgentError('跨文化结构语法存在未知来源：' + grammar['id'])
    return data


def _grammar_supports_domain(grammar: dict, domain: str | None) -> bool:
    """Return whether a grammar is usable for the task's declared domain.

    ``auto`` (and an omitted domain) preserves exploratory retrieval.  Hybrid
    tasks are supported by an explicit ``hybrid`` card or by a grammar that
    declares both person and vehicle, the two domains currently used by the
    product's hybrid route.
    """
    if not domain or domain == 'auto':
        return True
    domains = set(grammar.get('domains', []))
    if domain == 'hybrid':
        return 'hybrid' in domains or {'person', 'vehicle'}.issubset(domains)
    return domain in domains


def retrieve_design(kb: Path, packet: dict, limit: int = 14) -> dict:
    data = load_design(kb)
    need, brief = packet['need'], packet['brief']
    query = ' '.join([need['intent'], *need['desired_feeling'], *[n['query'] for n in need['knowledge_needs']]])
    if brief.get('medium') == 'film_shot':
        query += ' 叙事 剧情 镜头 前后 ' + brief.get('scene_context', '')
    q = set(tokens(query))
    available = [m for m in data['mechanisms'] if brief.get('medium', 'still') == 'film_shot' or m['family'] != 'narrative']
    # Intent/coherence are reasoning anchors, not prescribed styles.
    fixed = ['intent.meaning', 'integration.coherence', 'camera.viewpoint']
    if brief['scope'] == 'color_only':
        fixed.append('color.scope')
    if brief.get('medium') == 'film_shot':
        fixed.extend(['narrative.shot_task', 'narrative.context'])
    scored = []
    for m in available:
        terms = set(tokens(' '.join([m['title'], m['problem'], m['application_rule'], *m.get('actions', [])])))
        score = len(q & terms) / max(len(terms), 1)**0.5
        if score or m['id'] in fixed:
            scored.append((score, m))
    ordered = [m for _, m in sorted(scored, key=lambda row: (-row[0], row[1]['id']))]
    by_id = {m['id']: m for m in available}
    chosen = []
    def add(ident):
        if ident in by_id and ident not in chosen and len(chosen) < limit:
            chosen.append(ident)
    for ident in fixed:
        add(ident)
    # Bounded dependency expansion; record missing dependency rather than overfill context.
    for m in ordered:
        add(m['id'])
        for dep in m.get('depends_on', []):
            add(dep)
    selected = [by_id[x] for x in chosen]
    sources = {s for m in selected for s in m['source_ids']}
    grammar_terms = set(tokens(query))
    task_domain = brief.get('domain', 'auto')
    grammar_scored = []
    for grammar in data.get('cultural_grammars', []):
        if not _grammar_supports_domain(grammar, task_domain):
            continue
        terms = set(tokens(' '.join([grammar['title'], grammar['operation'], grammar['application_rule'],
                                     *grammar.get('actions', []), *grammar.get('domains', [])])))
        score = len(grammar_terms & terms) / max(len(terms), 1) ** 0.5
        if score:
            grammar_scored.append((score, grammar))
    cultural_grammars = [g for _, g in sorted(grammar_scored, key=lambda row: (-row[0], row[1]['id']))[:2]]
    grammar_sources = {s for g in cultural_grammars for s in g.get('source_ids', [])}
    all_sources = data['sources'] + data.get('cultural_sources', [])
    return {'method': 'bilingual_lexical_recall_plus_dependencies_not_vector_search',
            'mechanisms': selected,
            'relations': [r for r in data['relations'] if len(set(r['inputs']) & set(chosen)) >= 2],
            'cultural_grammars': cultural_grammars,
            'sources': [s for s in all_sources if s['id'] in sources | grammar_sources],
            'missing_dependencies': sorted({d for m in selected for d in m.get('depends_on', []) if d not in chosen}),
            'notice': '理论启发的设计选择，不是已实测的审美保证；由有看图能力的Agent作语义取舍。'}


def validate_reference_analysis(packet: dict, analysis: ReferenceAnalysis) -> None:
    refs = {r['id']: r for r in research_references(packet)}
    actual = [f.id for f in analysis.findings]
    if set(actual) != set(refs) or len(actual) != len(refs):
        raise AgentError('参考观察必须逐一覆盖实际图像，不得漏图/虚构图像ID')
    for f in analysis.findings:
        if not f.visible_facts.strip() or not f.uncertainty.strip():
            raise AgentError('参考观察须写可见事实及不确定性，不能仅写已看过')
        if f.id in {r['id'] for r in packet.get('host_visible_references', [])} and f.role_in_task in ('identity', 'canvas'):
            raise AgentError('宿主可见参考仅供研究，不能证明身份或编辑画布')
        if f.useful and not f.seen:
            raise AgentError('未看图不能标记可用')
        if f.role_in_task == 'positive' and refs[f.id]['role'] == 'aesthetic_negative':
            raise AgentError('不能将明确的反例自动转成正向审美参考')
        if f.role_in_task == 'negative' and refs[f.id]['role'] in ('identity', 'detail'):
            raise AgentError('主体身份素材不能因不美观而被当作应消除的反例')


def ref_coverage(packet: dict) -> dict:
    analysis = packet.get('reference_analysis') or {}
    findings = analysis.get('findings', [])
    refs = {r['id']: r for r in research_references(packet)}
    observed = observed_ids(packet)
    positives = [f['id'] for f in findings if f['seen'] and f['useful'] and f['role_in_task'] == 'positive'
                 and f['id'] in refs and f['id'] in observed]
    generation_positives = [ident for ident in positives if generation_eligible(packet, refs[ident])]
    needs_research = not bool(generation_positives)
    needs_input_reference = False
    mode = 'reference_selected_not_submitted' if generation_positives else 'no_generation_baseline'
    if packet.get('reference_policy') is not None:
        from .reference_gate import read_reference_policy, validate_reference_policy
        policy = read_reference_policy(packet)
        try:
            validate_reference_policy(packet, baseline_ids=generation_positives)
        except AgentError:
            needs_research = True
            needs_input_reference = True
        else:
            needs_research = False
            if not generation_positives:
                mode = policy['task_mode'] + '_without_generation_baseline'
    from .visual_research import status as research_status
    study = research_status(packet)
    if study is not None:
        # A valid image-input policy says nothing about aesthetic research.
        needs_research = needs_input_reference or study['state'] in (
            'needs_assessment', 'needs_observation', 'needs_selection_assessment',
            'needs_reference_expansion', 'needs_reference_selection')
    return {'positive_ids': generation_positives, 'evaluation_positive_ids': positives,
            'unseen_ids': [r for r in refs if r not in {f['id'] for f in findings if f['seen']}],
            'needs_visual_research': needs_research,
            'mode': mode,
            **({'visual_research': study, 'needs_input_reference': needs_input_reference} if study is not None else {}),
            'note': '仅供评估的图不能作为生成基准。已选参考尚不证明实际提交、视觉效果或用户认可。'}


def validate_design(packet: dict, spec: VisualSpec, design: DesignPlan) -> dict:
    """No voting, weighted scores, or three-agent review. This is a local preflight."""
    from .co_creation import validate_plan
    validate_plan(packet, design)
    from .creative_directions import validate_direction_selection
    validate_direction_selection(packet, getattr(design, 'creative_directions', None), design)
    from .quality_review import validate_adoption
    validate_adoption(packet, spec, design)
    if packet.get('need_mode') == 'offline_heuristic':
        raise AgentError('离线关键词草稿不能直接进入设计；先由宿主或真实模型完成需求理解并refresh')
    if packet['need'].get('questions'):
        raise AgentError('需求有决定性未解问题，先更新需求，不直接出图')
    if (packet['brief'].get('medium', 'still') == 'film_shot') != (design.narrative is not None):
        raise AgentError('静态图不附加剧情表；film_shot则必须有镜头任务与上下文说明')
    required_texts = [design.expression, design.subject_environment_relation, design.reading_order,
                      design.camera.shot_size, design.camera.viewpoint_and_distance,
                      design.camera.framing_and_scale, design.camera.focus_and_depth, design.camera.reason]
    if any(not t.strip() for t in required_texts):
        raise AgentError('设计缺少表达、关系或取景理由')
    focal = design.camera.focal_equivalent_mm
    if focal is not None and not 1 <= focal <= 2000:
        raise AgentError('等效焦距超出合理记录范围；未知请用null，不编造测量值')
    if not design.intent_claims:
        raise AgentError('须保留当前需求的解释依据')
    if packet.get('source_contract_version') == 1:
        from .provenance import validate_need, validate_claims
        validate_need(packet)
        validate_claims(packet, design.intent_claims)
    for c in design.intent_claims:
        if not c.interpretation.strip() or (packet.get('source_contract_version') != 1 and not c.inferred and (not c.quote or c.quote not in packet['brief']['request'])):
            raise AgentError('显式意图必须引用用户原话；自行选定的方案要标inferred')
    if not 1 <= len(design.choices) <= 8:
        raise AgentError('用1至8个必要设计选择，不把整本知识库塞进每张图')
    if not 2 <= len(design.checks) <= 12:
        raise AgentError('需要2至12条本任务可见标准，至少包括表达与整体配合')
    dimensions = {c.dimension for c in design.checks}
    if not {'expression', 'overall'}.issubset(dimensions):
        raise AgentError('检查标准不能只有物体存在；必须覆盖表达与整体关系')
    check_ids = [c.id for c in design.checks]
    if len(check_ids) != len(set(check_ids)) or any(not s or s.startswith(('hard.', 'avoid.')) for s in check_ids):
        raise AgentError('设计检查ID重复/为空/占用了硬约束ID')
    known = {m['id'] for m in packet['design_knowledge']['mechanisms']}
    choices = [c.id for c in design.choices]
    if len(choices) != len(set(choices)):
        raise AgentError('设计选择ID重复')
    for c in design.choices:
        if not set(c.mechanism_ids).issubset(known):
            raise AgentError('设计选择不能引用未检索到的机制或伪造来源')
        if not c.mechanism_ids and getattr(c, 'basis', 'retrieved_mechanism') != 'agent_hypothesis':
            raise AgentError('无机制来源的设计选择须明确标为agent_hypothesis')
        if c.criterion_id not in check_ids:
            raise AgentError('设计选择没有对应结果检查项')
        if any(not s.strip() for s in (c.goal, c.action, c.cooperation, c.tradeoff)):
            raise AgentError('须说明具体选择、元素如何配合及代价')
    if design.spatial_grammar is not None:
        available = {g['id']: g for g in packet['design_knowledge'].get('cultural_grammars', [])}
        if design.spatial_grammar.id not in available:
            raise AgentError('spatial_grammar只能采用本次检索命中的实验结构语法')
        if design.spatial_grammar.status != 'experimental' or design.spatial_grammar.cultural_reference != 'structural_only':
            raise AgentError('跨文化结构语法必须保持experimental/structural_only状态')
        if design.spatial_grammar.source_digest != available[design.spatial_grammar.id].get('source_digest'):
            raise AgentError('spatial_grammar内容没有绑定本次检索到的规范卡片快照')
    if 'aesthetic_retrieval' in packet:
        hits = packet.get('aesthetic_retrieval', {}).get('hits', [])
        # An empty, domain-safe search is valid: it records that this task had
        # no applicable curated card and must not manufacture a binding.
        if hits:
            available_cards = {h.get('id') for h in hits}
            binding = design.aesthetic_binding
            if binding is None and packet['aesthetic_retrieval'].get('adoption_policy') != 'optional_with_observed_source':
                raise AgentError('已有审美卡片检索结果，设计必须写 aesthetic_binding；不能只把检索放在packet里')
            selected = binding.selected_card_ids if binding is not None else []
            if len(selected) != len(set(selected)):
                raise AgentError('aesthetic_binding.selected_card_ids 不能重复')
            if not set(selected).issubset(available_cards):
                raise AgentError('aesthetic_binding 只能采用本次审美检索命中的卡片')
            if binding is not None and not selected and not binding.not_selected_reason.strip():
                raise AgentError('没有采用审美卡片时必须说明原因')
            if selected and not binding.adoption_reason.strip():
                raise AgentError('采用审美卡片时必须说明迁移理由')
        elif design.aesthetic_binding and design.aesthetic_binding.selected_card_ids:
            raise AgentError('没有命中审美卡片时不能选择审美卡片')
    if 'template_retrieval' in packet:
        hits = packet.get('template_retrieval', {}).get('hits', [])
        binding = design.template_binding
        if hits:
            # Retrieval is advisory. A template may be intentionally withheld
            # when its structure would over-constrain the current composition.
            if binding is not None:
                available = {h.get('id') for h in hits}
                if binding.template_id not in available:
                    raise AgentError('template_binding 只能采用本次模板检索命中的模板')
                if not binding.adopted_structure.strip() or not binding.adoption_reason.strip():
                    raise AgentError('template_binding 必须写清采用结构和理由')
                if not binding.pitfalls_applied:
                    raise AgentError('template_binding 必须写已采用的避坑项')
                if len(binding.pitfalls_applied) > 2:
                    raise AgentError('template_binding 最多采用两个直接相关的避坑项，避免模板规则压过本次表达')
                selected = next(h.get('template', {}) for h in hits if h.get('id') == binding.template_id)
                allowed_pitfalls = set(selected.get('pitfalls', []))
                if (len(binding.pitfalls_applied) != len(set(binding.pitfalls_applied))
                        or not all(p.strip() and p in allowed_pitfalls for p in binding.pitfalls_applied)):
                    raise AgentError('template_binding.pitfalls_applied 必须逐项来自所选模板的避坑规则，且不能重复')
        elif binding is not None:
            raise AgentError('没有命中任务模板时不能选择模板')
    refs = {r['id']: r for r in research_references(packet)}
    analysis = packet.get('reference_analysis') or {}
    if refs:
        manifest = {r['id']: r['sha256'] for r in analysis.get('input_images', [])}
        if manifest != {r['id']: r['sha256'] for r in packet['references']} or not set(refs).issubset(observed_ids(packet)):
            raise AgentError('参考尚未实际观察，或观察记录对应旧图片；先inspect/observe')
    available_baselines = set(ref_coverage(packet)['positive_ids'])
    if len(set(design.baseline_reference_ids)) != len(design.baseline_reference_ids):
        raise AgentError('基准参考ID不能重复')
    if not set(design.baseline_reference_ids).issubset(available_baselines):
        raise AgentError('基准必须是已看过、与当前任务相关且允许进入生成的正向参考；evaluation_only不能作生成基准')
    findings = {f['id']: f for f in analysis.get('findings', [])}
    adopted = set()
    for choice in design.choices:
        used = set()
        check = next(c for c in design.checks if c.id == choice.criterion_id)
        for use in choice.reference_uses:
            finding = findings.get(use.reference_id)
            reject = use.disposition == 'reject'
            if (use.reference_id in used or not finding or not finding['seen']
                    or (not reject and (not finding['useful'] or finding['role_in_task'] in ('reject', 'negative')))):
                raise AgentError('设计采用必须指向已看且适用的参考，不能重复或采用反例')
            if reject and (not use.reason.strip() or refs[use.reference_id]['role'] in ('identity', 'detail')
                           or finding['role_in_task'] in ('identity', 'canvas')):
                raise AgentError('研究舍弃必须说明为何改变选择；身份或编辑画布不能当审美反例舍弃')
            evidence = [finding['visible_facts'], *finding['transfer']]
            if reject:
                evidence += finding.get('do_not_transfer', [])
            if not use.observed_relation.strip() or not any(use.observed_relation in s for s in evidence):
                raise AgentError('采用依据须引用实际观察中的可见关系，不能凭空写入')
            if use.reference_id not in check.reference_ids:
                raise AgentError('采用参考的设计选择必须连接到同参考的结果检查项')
            used.add(use.reference_id)
            if not reject:
                adopted.add(use.reference_id)
    if not set(design.baseline_reference_ids).issubset(adopted):
        raise AgentError('每张基准图都须进入具体设计选择 reference_uses；仅列ID不算采用')
    binding = design.aesthetic_binding
    if binding and binding.selected_card_ids:
        hits = {h['id']: h.get('card', {}) for h in packet.get('aesthetic_retrieval', {}).get('hits', [])}
        for card_id in binding.selected_card_ids:
            card = hits.get(card_id)
            if not card or not card.get('source_sha256'):
                raise AgentError('采用审美卡片前须附加其原图并实际观察；当前命中缺少原图证据')
            # A source can have been supplied directly in the brief. Its
            # original hash, not a filename or tag, proves the correspondence.
            corresponding = {r['id'] for r in packet['references']
                             if r.get('original_sha256') == card['source_sha256']}
            if not corresponding.intersection(adopted):
                raise AgentError('采用审美卡片前须附加其原图并实际观察，再将观察关系写入 reference_uses 与检查项：' + card_id)
    for c in design.checks:
        if any(not s.strip() for s in (c.question, c.expected, c.failure)):
            raise AgentError('标准缺少具体预期或失败条件')
        if not set(c.reference_ids).issubset(refs):
            raise AgentError('检查标准引用了不存在的参考图')
    if design.baseline_reference_ids and not any(set(c.reference_ids) & set(design.baseline_reference_ids) for c in design.checks):
        raise AgentError('基准图必须真正参与至少一项检查，不能只列ID')
    from .reference_gate import validate_reference_policy
    policy = validate_reference_policy(packet, baseline_ids=design.baseline_reference_ids)
    from .visual_research import validate_design as validate_study
    study = validate_study(packet, design)
    if design.narrative:
        if 'narrative' not in dimensions:
            raise AgentError('影片镜头须有一条可由静帧检查的叙事关系标准；时间连续性另列未测试')
        for field in ('scene_function', 'shot_function', 'point_of_view', 'previous_link', 'next_link',
                      'information_reveal', 'staging', 'camera_movement', 'rhythm', 'continuity'):
            if not getattr(design.narrative, field).strip():
                raise AgentError('影片镜头字段缺失：' + field + '；未知须说明假设，不虚构前后戏')
    reference_mode = 'reference_selected_not_submitted' if design.baseline_reference_ids else 'exploratory_no_visual_baseline'
    if policy['version'] == 1 and not design.baseline_reference_ids:
        reference_mode = policy['task_mode'] + '_without_generation_baseline'
    return {'structural_preflight': 'passed', 'aesthetic_quality': 'not_proven_by_code',
            'reference_mode': reference_mode,
            **({'visual_research': study} if study is not None else {})}


def validate_acceptance_wording(packet: dict) -> str | None:
    """Reject unsupported wording before any model request or contract rewrite."""
    wording = packet.get('acceptance_wording')
    if wording not in (None, 'need_recorded_v1'):
        raise AgentError('未知验收文案版本；不能改写已有冻结标准')
    return wording


def acceptance(packet: dict, design: DesignPlan) -> dict:
    """Fixed evaluation questions are established before generation, never post-hoc."""
    wording = validate_acceptance_wording(packet)
    # Existing packets have no marker. Keep their exact text and digest so
    # previously frozen requests/results remain verifiable after this upgrade.
    avoid_expected = '不出现本轮需求记录的排除项' if wording else '不出现此用户明确排除项'
    avoid_failure = '出现本轮需求记录的排除项' if wording else '出现被明确排除的结果'
    from .quality_review import version
    quality_version = version(packet)
    # Source-bound excerpts may already contain a negation, e.g. "无文字".
    # Do not reverse their meaning by wrapping them in another prohibition.
    avoid_prefix = '不得出现：'
    if quality_version == 1:
        avoid_prefix = '排除要求原话：'
        avoid_expected = '遵守这条原话的排除要求，保留原句的否定含义'
        avoid_failure = '画面违反这条原话的排除要求'
    checks = []
    for i, lock in enumerate(packet['need']['must_keep']):
        checks.append({'id': f'hard.{i+1}', 'dimension': 'hard', 'question': lock,
                       'expected': '严格保持当前要求', 'failure': '出现与该要求冲突的可见变化', 'reference_ids': []})
    for i, avoid in enumerate(packet['need']['avoid']):
        checks.append({'id': f'avoid.{i+1}', 'dimension': 'hard', 'question': avoid_prefix + avoid,
                       'expected': avoid_expected, 'failure': avoid_failure, 'reference_ids': []})
    checks.extend(c.model_dump() for c in design.checks)
    if design.template_binding:
        binding = design.template_binding
        checks.extend([
            {'id': 'template.visible', 'dimension': 'overall',
             'question': '模板结构的可见验收是否成立？',
             'expected': binding.visible_test,
             'failure': '模板结构未达到上述可见验收。', 'reference_ids': []},
            {'id': 'template.failure', 'dimension': 'overall',
             'question': '模板已采用的避坑项是否都被遵守？',
             'expected': '不出现：' + '；'.join(binding.pitfalls_applied),
             'failure': '出现模板已明确排除的表达：' + '；'.join(binding.pitfalls_applied),
             'reference_ids': []},
        ])
    data = {'format': 'liu.acceptance.v3', 'request': packet['brief']['request'],
            'expression': design.expression, 'intent_claims': [c.model_dump() for c in design.intent_claims],
            'carrier': design.carrier, 'medium': packet['brief'].get('medium', 'still'),
            'aesthetic_binding': design.aesthetic_binding.model_dump() if design.aesthetic_binding else None,
            'private_style_profile': packet.get('private_style_profile'),
            'template_binding': binding.model_dump() if design.template_binding else None,
            'checks': checks, 'baseline_reference_ids': design.baseline_reference_ids,
            'reference_images': [{'id': r['id'], 'sha256': r['sha256']} for r in packet['references']],
            'scene_context': packet['brief'].get('scene_context', ''),
            'previous_shot': packet['brief'].get('previous_shot', ''),
            'next_shot': packet['brief'].get('next_shot', ''),
            'narrative_intent': ({k: getattr(design.narrative, k) for k in
                                 ('scene_function', 'shot_function', 'point_of_view', 'information_reveal')}
                                if design.narrative else None)}
    if 'host_visible_references' in packet:
        data['host_visible_references'] = packet['host_visible_references']
    creative_protocol = packet.get('intake', {}).get('artistic_contract', {}).get('authorship_protocol')
    if packet.get('reference_policy') is not None:
        from .reference_gate import read_reference_policy
        read_reference_policy(packet)
        data['reference_policy'] = packet['reference_policy']
    if creative_protocol:
        # Freeze the method that informed this plan, not an originality verdict.
        # Task-specific visible questions still belong to design.checks.
        data['creative_protocol'] = creative_protocol
    if packet.get('source_contract_version') == 1:
        from .provenance import verified_sources
        data['request_summary'] = data['request']
        data['request'] = '\n'.join(m['text'] for m in verified_sources(packet['input_sources']) if m['actor'] == 'user')
        data.update(source_contract_version=1, input_sources_digest=packet['input_sources']['digest'],
                    constraint_evidence=packet['need']['constraint_evidence'])
    from .quality_review import version
    if version(packet) == 1:
        data['quality_contract_version'] = 1
    from .visual_decision import policy
    if policy(packet) is not None:
        data['visual_decision_policy'] = dict(policy(packet))
    from .workflow_policy import policy as workflow_policy
    if workflow_policy(packet) is not None:
        data['workflow_policy'] = dict(workflow_policy(packet))
    from .co_creation import enabled, selected_meaning, decision_digest
    if enabled(packet):
        data['interaction_policy'] = packet['interaction_policy']
        data['selected_decision'] = selected_meaning(packet)
        data['decision_context_digest'] = decision_digest(packet)
    return {**data, 'digest': digest(data)}


def validate_eval(contract: dict, evaluation: ImageEval) -> None:
    from .quality_review import validate_holistic, holistic_failures
    validate_holistic(contract, evaluation)
    baselines = contract['baseline_reference_ids']
    comparisons = evaluation.baseline_comparisons
    if sorted(c.reference_id for c in comparisons) != sorted(baselines):
        raise AgentError('审美对照须逐张覆盖原基准，不能用硬约束通过替代参考比较')
    if any(not getattr(c, field).strip() for c in comparisons for field in
           ('composition', 'light_and_material', 'expression', 'decisive_gap')):
        raise AgentError('审美对照须写候选与参考在构图、光色材质、表达和主要差距上的可见证据')
    if evaluation.decision == 'ready_for_user' and any(c.verdict != 'comparable' for c in comparisons):
        raise AgentError('低于参考或无法判断时不能建议视觉就绪')
    expected = {c['id'] for c in contract['checks']}
    actual = [c.criterion_id for c in evaluation.items]
    if set(actual) != expected or len(actual) != len(expected):
        raise AgentError('Eval必须覆盖原需求硬约束和全部设计标准；不能漏掉难通过的项')
    if any(not x.candidate_evidence.strip() or not x.reference_evidence.strip() for x in evaluation.items):
        raise AgentError('Eval须写候选图证据和参考对照/无参考说明')
    # Code checks coverage and structured verdicts, not natural-language meaning.
    # The visual reviewer must judge evidence against the frozen expected/failure
    # criteria, including any permitted crop or loss of noncritical detail.
    whole_failed = holistic_failures(evaluation) if contract.get('quality_contract_version') == 1 else set()
    unmet = any(x.verdict == 'not_met' for x in evaluation.items) or bool(whole_failed)
    uncertain = any(x.verdict == 'uncertain' for x in evaluation.items)
    if evaluation.decision == 'ready_for_user' and (unmet or uncertain):
        raise AgentError('存在未满足/看不清的项目，不能建议视觉就绪')
    if evaluation.decision == 'revise' and not evaluation.changes:
        raise AgentError('需要修改时必须指出可执行改动')
    if contract.get('source_contract_version') == 1 and evaluation.decision == 'revise':
        if not unmet or not evaluation.preserve or any(not text.strip() for text in evaluation.preserve):
            raise AgentError('修订须有已知失败与具体保留关系；仅不确定性不能作为重画依据')
        targets = evaluation.revision_targets
        failed = {item.criterion_id for item in evaluation.items if item.verdict == 'not_met'} | whole_failed
        if ([target.change for target in targets] != evaluation.changes or
                not all(target.criterion_id in failed for target in targets)):
            raise AgentError('每个修订动作须逐一绑定not_met标准及其画面证据')
    if len(evaluation.changes) > 2:
        raise AgentError('每轮只处理最多两个主要问题，不整幅推倒重抽')
    if contract['medium'] == 'film_shot' and evaluation.sequence_validation != 'not_tested':
        raise AgentError('单张镜头图不能证明动作、运镜、剪辑连续与节奏已经实现')
    if contract['medium'] == 'still' and evaluation.sequence_validation != 'not_applicable':
        raise AgentError('静态任务不检查不存在的时间连续性')


def validate_diagnosis(evaluation: ImageEval, evidence: dict | None) -> None:
    if evaluation.diagnosis not in ('compiler', 'execution'):
        return
    if (not evidence or evidence.get('status') != 'verified' or
            evidence.get('format') != 'liu.generation-evidence.v1' or
            evidence.get('digest') != digest({k: v for k, v in evidence.items() if k != 'digest'})):
        raise AgentError('缺少验证过的生成实参和设计快照，不能确定归因为编译或模型执行；应标unknown并说明缺证')


def quality_status(contract: dict, review: dict | None, has_trial: bool) -> str:
    from .visual_decision import record_only
    if record_only(contract):
        return 'image_generated_waiting_user' if has_trial else 'prompt_only_not_tested'
    if contract.get('reference_policy') is not None:
        from .contracts import ReferencePolicy
        try:
            ReferencePolicy.model_validate(contract['reference_policy'])
        except ValueError as exc:
            raise AgentError('验收合同包含未知或无效的reference_policy') from exc
    if not has_trial:
        return 'prompt_only_not_tested'
    if not review:
        return 'review_incomplete'
    evaluation = ImageEval.model_validate(review)
    validate_eval(contract, evaluation)
    if evaluation.decision == 'revise':
        return 'needs_revision'
    if evaluation.decision == 'uncertain':
        return 'evidence_insufficient'
    if not contract['baseline_reference_ids'] and not contract.get('reference_policy'):
        return 'exploratory_candidate_unvalidated'
    if contract['medium'] == 'film_shot':
        return 'storyboard_frame_for_user_review'
    return 'candidate_for_user_review'
