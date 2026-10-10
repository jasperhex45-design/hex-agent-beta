"""Durable API reservations and evidence-led recovery, with no implicit retries."""
from __future__ import annotations
from .co_creation import blockers as interaction_blockers, early_blockers, needs_assessment, initial_assessment_required

import copy
from time import monotonic
from pathlib import Path
from uuid import uuid4

from pydantic import BaseModel

from .contracts import Compilation, ImageEval, Need, PairComparison, Plan, ReferenceAnalysis, Solution
from .design import acceptance, validate_eval
from .io import AgentError, digest, now, read_json, sha, within, write_json
from .reference_gate import needs_reference

FORMAT = 'liu.aesthetic.api-state.v1'
_ERRORS = (AgentError, OSError, ValueError, TypeError, KeyError, AttributeError)


def _seal(body: dict) -> dict:
    return {**body, 'digest': digest(body)}


def _verified(path: Path) -> dict:
    data = read_json(path)
    if not isinstance(data, dict) or data.get('digest') != digest({k: v for k, v in data.items() if k != 'digest'}):
        raise AgentError(f'{path.name} 摘要不一致，不能安全恢复')
    return data


def load_api_state(run: Path) -> dict:
    """Validate all reservations, snapshots and completed outputs without writes."""
    run = Path(run)
    from .native_budget import guard_api
    guard_api(run)
    if not (run/'api-state.json').is_file():
        raise AgentError('API旧记录缺少请求前预留及原始预算上限；不能安全恢复或重发')
    state = _verified(run/'api-state.json')
    if state['format'] != FORMAT:
        raise AgentError('API恢复记录版本不支持')
    limits = state['limits']
    if any(type(limits.get(k)) is not int or limits[k] < 0 for k in ('max_calls', 'max_images', 'image_tests')):
        raise AgentError('API原始预算上限无效')
    if limits['image_tests'] > 2:
        raise AgentError('API原始试图上限超过两张')
    rows = state['requests']
    if not isinstance(rows, list) or len({r['id'] for r in rows}) != len(rows):
        raise AgentError('API请求预留重复或损坏')
    if (len(rows) > limits['max_calls'] or
            sum(r['kind'] == 'image' for r in rows) > min(limits['max_images'], limits['image_tests'])):
        raise AgentError('API请求超过原始预算')
    known = {within(run, r['snapshot']) for r in rows}
    if any(path.resolve() not in known for path in (run/'api/requests').glob('*.json')):
        raise AgentError('API存在未进入预留账本的孤立请求快照；不能确定末次预留，禁止重置预算或重发')
    for row in rows:
        if row['state'] not in ('pending', 'completed', 'failed', 'outcome_unknown'):
            raise AgentError('API请求状态未知')
        snapshot = _verified(within(run, row['snapshot']))
        from .visual_decision import policy
        policy(snapshot.get('packet') or {})
        if snapshot['digest'] != row['snapshot_digest'] or snapshot['id'] != row['id']:
            raise AgentError('API请求与快照绑定不一致')
        if (snapshot['phase'], snapshot['round'], snapshot['kind']) != (row['phase'], row['round'], row['kind']):
            raise AgentError('API请求阶段与快照不一致')
        if row['event']['kind'] != row['kind']:
            raise AgentError('API预算事件类型不一致')
        if row['state'] == 'completed':
            output = row['output']
            path = within(run, output['file'])
            if sha(path.read_bytes()) != output['sha256']:
                raise AgentError('API已接收结果文件缺失或改变')
            if output['type'] == 'json':
                read_json(path)
            elif output['type'] != 'image':
                raise AgentError('API结果类型无效')
    return state


def _budget(state: dict) -> dict:
    rows, limits = state['requests'], state['limits']
    calls, images = len(rows), sum(r['kind'] == 'image' for r in rows)
    return {'attempted_calls': calls, 'attempted_images': images,
            'max_calls': limits['max_calls'], 'max_images': limits['max_images'],
            'remaining_calls': max(0, limits['max_calls']-calls),
            'remaining_images': max(0, min(limits['image_tests'], limits['max_images'])-images),
            'recovery_safe': True, 'counts_failed_and_unknown': True,
            'cost': None, 'cost_status': 'not_available', 'notice': '次数不是金额；预留、失败及未知结果均占预算。'}


class DurableProvider:
    """One reservation per provider operation, shared with its Budget.take call."""

    def __init__(self, run: Path, delegate, state: dict):
        self.run, self.delegate, self.state = Path(run), delegate, state
        if state.get('provider_adapter') == 'liu_agent.art_structure.bridge.ArtProvider':
            if type(delegate).__module__+'.'+type(delegate).__name__ != state['provider_adapter']:
                raise AgentError('艺术结构API任务必须恢复原ArtProvider，不能静默移除适配器')
            if not state.get('adapter_config') or delegate.recovery_config() != state['adapter_config']:
                raise AgentError('艺术结构恢复配置或证据指纹已改变；停止调用并核对原配置')
        self.context = {'packet': None, 'plan': None, 'texts': None, 'solution': None, 'round': 0}
        self.budget = delegate.budget
        if not hasattr(self.budget, '_prepaid'):
            raise AgentError('API适配器必须使用可预留的原生Budget')
        self.budget.max_calls = state['limits']['max_calls']
        self.budget.max_images = state['limits']['max_images']
        self.budget.events = [copy.deepcopy(r['event']) for r in state['requests']]
        self.budget.calls = len(state['requests'])
        self.budget.images = sum(r['kind'] == 'image' for r in state['requests'])

    @classmethod
    def create(cls, run: Path, delegate, image_tests: int, *, settings=None):
        from .native_budget import guard_api
        guard_api(run)
        if (run/'api-state.json').exists() or delegate.budget.calls or delegate.budget.images:
            raise AgentError('新API任务必须有独立预算；已有请求请使用resume')
        state = {'format': FORMAT, 'created_at': now(),
                 'limits': {'max_calls': delegate.budget.max_calls,
                            'max_images': delegate.budget.max_images, 'image_tests': image_tests},
                 'requests': [], 'settings': settings or {},
                 'provider_adapter': type(delegate).__module__+'.'+type(delegate).__name__,
                 'adapter_config': delegate.recovery_config() if callable(getattr(delegate, 'recovery_config', None)) else None}
        value = cls(run, delegate, state)
        value.persist()
        return value

    def __getattr__(self, name):
        return getattr(self.delegate, name)

    def checkpoint(self, **context):
        self.context.update({k: v.model_dump() if isinstance(v, BaseModel) else copy.deepcopy(v)
                             for k, v in context.items()})

    def persist(self):
        from .native_budget import guard_api
        guard_api(self.run)
        write_json(self.run/'api-state.json', _seal({k: v for k, v in self.state.items() if k != 'digest'}))
        write_json(self.run/'calls.json', {**_budget(self.state), 'events': self.budget.events,
                   'model_routes': getattr(self.delegate, 'model_routes', []),
                   'reservation_ledger': 'api-state.json'})

    def _call(self, phase, kind, request, invoke):
        from .native_budget import guard_api
        guard_api(self.run)
        from .visual_decision import policy, record_only
        from .workflow import read_packet
        context_packet = self.context.get('packet') or {}
        packet = read_packet(self.run) if (self.run/'packet.json').exists() else {}
        if policy(packet) != policy(context_packet):
            raise AgentError('API当前策略与请求快照不同；不能切换策略绕过冻结流程')
        from .workflow_policy import policy as workflow_policy
        if workflow_policy(packet) != workflow_policy(context_packet):
            raise AgentError('API当前完整流程与请求快照不同；不能移除交付要求再提交')
        if kind == 'image':
            from .generation_authorization import require_not_stopped
            require_not_stopped(self.run)
            from .generation_permission import require_image_permission
            if (self.run/'host-dispatches.json').exists():
                raise AgentError('已有宿主生成记录；不能通过API另起预算')
            reviews = [r for r in self.state['requests'] if r['phase'] == 'evaluate' and r['state'] == 'completed']
            prior_review = read_json(within(self.run, reviews[-1]['output']['file'])) if reviews else None
            completed_images = [r for r in self.state['requests'] if r['kind'] == 'image' and r['state'] == 'completed']
            if completed_images and record_only(packet):
                raise AgentError('首图已返回，生产策略等待用户判断；机器评审不能触发补图')
            if completed_images and not any(r['round'] == completed_images[-1]['round'] for r in reviews):
                raise AgentError('先实际复查上一候选，不跳过Eval继续生成')
            decision = None
            if (self.run/'user-feedback.json').exists():
                from .feedback import verified_feedback
                decision = verified_feedback(self.run)[2]['decision']
            require_image_permission(max_images=min(self.state['limits']['image_tests'], self.state['limits']['max_images']),
                                     attempted_images=sum(r['kind'] == 'image' for r in self.state['requests']),
                                     review=prior_review, user_decision=decision)
        unresolved = [r for r in self.state['requests'] if r['state'] in ('pending', 'outcome_unknown')]
        if unresolved:
            raise AgentError('API存在pending/outcome_unknown请求；先核对真实结果，禁止重发')
        for prior in self.state['requests']:
            if prior['phase'] == phase and prior['round'] == self.context['round']:
                snapshot = _verified(within(self.run, prior['snapshot']))
                if snapshot['request'] == request:
                    raise AgentError('相同阶段请求已有预留或结果；必须复用证据，禁止重复调用')
        if kind == 'image' and self.budget.images >= self.state['limits']['image_tests']:
            raise AgentError('已达到本任务原始试图次数；恢复不能增加预算')
        if self.budget.calls >= self.budget.max_calls or (kind == 'image' and self.budget.images >= self.budget.max_images):
            raise AgentError('请求次数上限已到；不继续调用')
        # Write the full snapshot before reserving, then persist the reservation
        # before entering an adapter or HTTP client. A crash leaves a charged slot.
        ident = uuid4().hex
        body = {**copy.deepcopy(self.context), 'id': ident, 'phase': phase, 'kind': kind,
                'request': request, 'created_at': now()}
        snapshot = _seal(body)
        relative = f'api/requests/{ident}.json'
        write_json(within(self.run, relative), snapshot)
        event = self.budget.take(kind)
        event.update(reservation_id=ident, phase=phase, round=body['round'], elapsed_seconds=None)
        model_for_phase = getattr(self.delegate, 'model_for_phase', None)
        event['model'] = (getattr(self.delegate, 'image_model', None) if kind == 'image' else
                          getattr(self.delegate, 'search_model', None) if kind == 'search' else
                          model_for_phase(phase) if callable(model_for_phase) else None)
        row = {'id': ident, 'phase': phase, 'round': body['round'], 'kind': kind,
               'state': 'pending', 'snapshot': relative, 'snapshot_digest': snapshot['digest'],
               'event': event, 'output': None}
        self.state['requests'].append(row)
        self.persist()
        self.budget._prepaid = event
        self.budget._reservation_active = True
        started = monotonic()
        try:
            result = invoke()
            if isinstance(result, bytes):
                filename = f'api/outputs/{ident}.png'
                target = within(self.run, filename)
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(result)
                output_type = 'image'
            else:
                filename = f'api/outputs/{ident}.json'
                target = within(self.run, filename)
                write_json(target, result.model_dump() if isinstance(result, BaseModel) else result)
                output_type = 'json'
            row.update(state='completed', output={'file': filename, 'sha256': sha(target.read_bytes()), 'type': output_type})
            if kind == 'image' and request.get('generation_spec') is not None:
                parameters = getattr(self.delegate, 'last_generation_evidence', None)
                if not parameters:
                    raise AgentError('图片已返回但缺少实发/返回参数记录；保留请求，不自动重试')
                row['generation_parameters'] = copy.deepcopy(parameters)
                row['output']['original_sha256'] = parameters['returned']['image']['sha256']
            event['elapsed_seconds'] = monotonic()-started
            self.persist()
            return result
        except BaseException as exc:
            if kind == 'image' and request.get('generation_spec') is not None:
                parameters = getattr(self.delegate, 'last_generation_evidence', None)
                if parameters:
                    row['generation_parameters'] = copy.deepcopy(parameters)
            row['state'] = 'failed' if event.get('state') in ('http_error', 'received') else 'outcome_unknown'
            row['error_type'] = type(exc).__name__
            event['elapsed_seconds'] = monotonic()-started
            self.persist()
            raise
        finally:
            self.budget._prepaid = None
            self.budget._reservation_active = False

    def structured(self, instruction, payload, output_type, images=None, phase='execution'):
        images = images or []
        return self._call(phase, 'text', {'instruction': instruction, 'payload': payload,
                          'output_type': output_type.__name__, 'image_sha256': [sha(x) for x in images]},
                          lambda: self.delegate.structured(instruction, payload, output_type, images, phase=phase))

    def generate(self, prompt, images, spec=None):
        from .workflow_policy import require_image_deliverable
        require_image_deliverable(self.context.get('packet') or {})
        request = {'prompt': prompt, 'image_sha256': [sha(x) for x in images]}
        if spec is not None:
            from .generation_spec import validate_generation_spec
            generation = self.context['generation']
            validate_generation_spec(spec, capabilities=generation['capability_snapshot'], prompt=prompt,
                                     design_digest=digest(self.context['solution']['design']),
                                     generation_reference_ids=[x['id'] for x in generation['inputs']])
            request['generation_spec'] = spec.model_dump()
        return self._call('generate', 'image', request,
                          lambda: self.delegate.generate(prompt, images, spec=spec) if spec is not None
                          else self.delegate.generate(prompt, images))

    def research(self, queries, *args, **kwargs):
        return self._call('research', 'search', {'queries': queries, 'options': kwargs},
                          lambda: self.delegate.research(queries, *args, **kwargs))


def _completed(state, phase, round_number):
    rows = [r for r in state['requests'] if r['phase'] == phase and r['round'] == round_number and r['state'] == 'completed']
    if len(rows) > 1:
        raise AgentError('同阶段同轮存在多个完成结果，不能自动选取')
    return rows[0] if rows else None


def restore_provider(run: Path, base_provider):
    """Rebuild the saved adapter around an explicitly authorized base provider."""
    run = Path(run).resolve()
    state = load_api_state(run)
    original = state.get('provider_adapter')
    if original != 'liu_agent.art_structure.bridge.ArtProvider':
        if original not in ('liu_agent.provider.OpenAIProvider', type(base_provider).__module__+'.'+type(base_provider).__name__):
            raise AgentError('未知API适配器；不能用默认provider替换原调用路线')
        return base_provider
    config = state.get('adapter_config')
    if not config or config.get('kind') != 'art_structure':
        raise AgentError('艺术结构旧记录缺少原适配器完整配置；不能静默回退默认模型')
    from .art_structure.bridge import ArtProvider
    adapter = ArtProvider(base_provider, run, catalog=Path(config['catalog']), query=config['query'],
                          feedback_root=Path(config['feedback_root']) if config['feedback_root'] else None)
    if adapter.recovery_config() != config:
        raise AgentError('艺术结构目录、指导或反馈证据已改变；不能按默认配置继续收费请求')
    plans = [r for r in state['requests'] if r['phase'] in ('design', 'revise') and r['state'] == 'completed']
    if plans:
        from .art_structure.core import frozen_spatial_contract, record_for_design
        plan = Plan.model_validate(read_json(within(run, plans[-1]['output']['file'])))
        adapter.current = record_for_design(run, plan.design.model_dump())
        adapter.first_checks = frozen_spatial_contract(adapter.current)
        context_path = run/'art-structure/context.json'
        if context_path.exists():
            adapter.context = read_json(context_path)
    return adapter


def _saved_response(run, state, phase, request):
    matches = []
    for row in state['requests']:
        if row['phase'] != phase:
            continue
        snap = _verified(within(run, row['snapshot']))
        if snap['request'] == request:
            matches.append(row)
        elif ({k: v for k, v in snap['request'].items() if k != 'instruction'} ==
              {k: v for k, v in request.items() if k != 'instruction'}):
            raise AgentError('阶段指令版本已变化；保留原请求结果，不能静默重调并增加费用')
    if not matches:
        return None
    if len(matches) != 1 or matches[0]['state'] != 'completed':
        raise AgentError('相同API请求已预留、失败或结果未知；恢复不会重发')
    return read_json(within(run, matches[0]['output']['file']))


class _MissingStage(Exception):
    def __init__(self, stage):
        self.stage = stage


def _observed(packet):
    analysis = packet.get('reference_analysis') or {}
    expected = [{'id': r['id'], 'sha256': r['sha256']} for r in packet['references']]
    seen = {f['id'] for f in analysis.get('findings', []) if f.get('seen')}
    return not expected or (analysis.get('input_images') == expected and seen == {r['id'] for r in expected})


def _resume_design(run, state, provider):
    """Continue only absent text stages; cached responses remain task-bound."""
    from . import instructions as ins
    from .workflow import (attach_observation, compile_texts, compilation_input, bind_compilation_runtime, design_input, finish,
                           prepare_cloud_style_references, read_packet, reference_bytes,
                           refresh_packet, save_packet, validate_solution, validate_spec)
    from .design import validate_design
    packet = read_packet(run)
    settings = state.get('settings', {})
    durable = DurableProvider(run, provider, state) if provider is not None else None
    if durable and state.get('provider_adapter') == 'liu_agent.art_structure.bridge.ArtProvider':
        if type(provider).__module__+'.'+type(provider).__name__ != state['provider_adapter']:
            raise AgentError('此任务使用艺术结构适配器；恢复缺失阶段须提供同一ArtProvider授权接口')

    def structured(phase, instruction, payload, model, images=()):
        request = {'instruction': instruction, 'payload': payload, 'output_type': model.__name__,
                   'image_sha256': [sha(x) for x in images]}
        saved = _saved_response(run, state, phase, request)
        if saved is not None:
            return model.model_validate(saved)
        write_json(run/f'api/resume-{phase}-input.json', request)
        if durable is None:
            raise _MissingStage({'parse': 'needs_parse', 'observe': 'needs_observation',
                                 'direction': 'needs_direction', 'design': 'needs_design', 'compile': 'needs_compile'}[phase])
        return durable.structured(instruction, payload, model, list(images), phase=phase)

    try:
        refs = reference_bytes(packet, run, cloud=True)
        if durable:
            durable.checkpoint(packet=packet)
        if packet['need_mode'] == 'offline_heuristic':
            need = structured('parse', ins.stage_instruction('PARSE', packet), {'brief': packet['brief'], 'input_sources': packet.get('input_sources'),
                                                 'references_in_order': packet['references']}, Need, refs)
            if not settings.get('kb') or not settings.get('state'):
                raise AgentError('早期API记录缺少检索恢复路径；需明确refresh当前需求后再恢复')
            packet = refresh_packet(packet, run, Path(settings['kb']), Path(settings['state']), need,
                                    'cloud_model', aesthetic_query=settings.get('aesthetic_query') or None)
        from .co_creation import early_blockers, blockers, needs_assessment, validate_assessment, initial_assessment_required
        if early_blockers(packet):
            return 'needs_input'
        if initial_assessment_required(packet):
            need = structured('direction', ins.stage_instruction('DIRECTION', packet), design_input(packet), Need, refs)
            validate_assessment(packet, need)
            packet = refresh_packet(packet, run, Path(settings['kb']), Path(settings['state']), need, 'cloud_model')
            if durable:
                durable.checkpoint(packet=packet)
            if early_blockers(packet):
                return 'needs_input'
            if initial_assessment_required(packet):
                return 'needs_intent_assessment'
        if settings.get('template_library_path') and not packet.get('template_retrieval'):
            from . import template_library
            template_library.attach_search(run, template_library.search(
                Path(settings['template_library_path']), settings.get('template_query') or packet['need']['intent'], packet['need']['domain']))
            packet = read_packet(run)
        packet, _ = prepare_cloud_style_references(run, packet, settings.get('allow_library_analysis', False))
        refs = reference_bytes(packet, run, cloud=True)
        if durable:
            durable.checkpoint(packet=packet)
        if refs and not _observed(packet):
            analysis = structured('observe', ins.stage_instruction('OBSERVE', packet),
                                  {'brief': packet['brief'], 'need': packet['need'], 'references_in_order': packet['references']}, ReferenceAnalysis, refs)
            packet = attach_observation(packet, run, analysis, 'api_images_submitted', '图像字节已通过API提交；判断仍需人工校准')
        if needs_assessment(packet):
            need = structured('direction', ins.stage_instruction('DIRECTION', packet), design_input(packet), Need, refs)
            validate_assessment(packet, need)
            packet = refresh_packet(packet, run, Path(settings['kb']), Path(settings['state']), need, 'cloud_model')
            if durable:
                durable.checkpoint(packet=packet)
        if blockers(packet):
            return 'needs_input'
        from .visual_research import status as research_status
        study = research_status(packet)
        if study and study['version'] == 2 and study['state'] in (
                'needs_assessment', 'needs_selection_assessment', 'needs_reference_selection'):
            return 'needs_visual_research' if study['state'] == 'needs_assessment' else study['state']
        gap = packet['retrieval']['needs_research'] or packet['visual_coverage']['needs_visual_research']
        if settings.get('research') and gap and packet['research'].get('state') == 'not_requested':
            queries = [packet['brief']['public_search_hint']] if packet['brief']['public_search_hint'] else packet['need']['public_queries']
            if queries:
                saved = _saved_response(run, state, 'research', {'queries': queries, 'options': {}})
                if saved is None:
                    if durable is None:
                        return 'needs_research'
                    durable.checkpoint(packet=packet)
                    saved = durable.research(queries)
                packet['research'] = saved
            else:
                packet['research'] = {'state': 'no_safe_query', 'sources': []}
            packet = save_packet(packet, run)
        if needs_reference(packet):
            return 'needs_reference'
        study = research_status(packet)
        if study and study['version'] == 2 and study['state'] in (
                'needs_assessment', 'needs_observation', 'needs_selection_assessment',
                'needs_reference_expansion', 'needs_reference_selection'):
            return ('needs_visual_research' if study['state'] in ('needs_assessment', 'needs_observation')
                    else study['state'])
        if provider is not None:
            updated = bind_compilation_runtime(packet, provider)
            prior = packet.get('compilation_runtimes')
            if prior and updated.get('compilation_runtimes') != prior:
                raise AgentError('恢复时模型能力与编译快照不同；不能用新模型执行旧编译')
            packet = save_packet(updated, run)
        payload = design_input(packet)
        if durable:
            durable.checkpoint(packet=packet)
        plan = structured('design', ins.stage_instruction('DESIGN', packet), payload, Plan, refs)
        validate_spec(packet, plan.spec)
        validate_design(packet, plan.spec, plan.design)
        if durable:
            durable.checkpoint(plan=plan)
            # A cached projected Plan must also restore the optional adapter's
            # spatial state before a missing compile phase uses that adapter.
            if state.get('provider_adapter') == 'liu_agent.art_structure.bridge.ArtProvider':
                from .art_structure.core import frozen_spatial_contract, record_for_design
                provider.current = record_for_design(run, plan.design.model_dump())
                provider.first_checks = frozen_spatial_contract(provider.current)
                context_file = run/'art-structure/context.json'
                if context_file.exists():
                    provider.context = read_json(context_file)
        targets = list(dict.fromkeys(packet['brief']['targets']+(['image'] if state['limits']['image_tests'] else [])))
        compile_payload = compilation_input(packet, plan.spec, plan.design, targets)
        write_json(run/'compilation-input.json', compile_payload)
        comp = structured('compile', ins.stage_instruction('MODEL_COMPILE' if 'model_adaptation' in compile_payload else 'COMPILE', packet), compile_payload, Compilation)
        if packet.get('model_adaptation_version') == 1:
            from .model_adaptation import require_revision
            require_revision(comp, None)
        texts = compile_texts(packet, plan.spec, comp, targets, plan.design)
        solution = Solution(packet_digest=packet['digest'], spec=plan.spec, design=plan.design,
                            compilation=Compilation(prompts=[p for p in comp.prompts if p.target in packet['brief']['targets']]))
        validate_solution(packet, solution)
        if durable:
            durable.checkpoint(solution=solution, texts=texts)
        from .workflow import freeze_contract
        from .workflow_policy import inspect_completion
        if packet.get('workflow_policy'):
            write_json(run/'solution.json', solution.model_dump())
            freeze_contract(run, packet, solution)
            completion = inspect_completion(run, packet)
            if not completion['ready']:
                return completion['state']
        finish(run, solution, 'openai_api')
        return 'exported'
    except _MissingStage as missing:
        return missing.stage


def _evidence(run: Path, state: dict):
    from .workflow import read_packet, validate_solution, validate_trial
    packet = read_packet(run)
    from .visual_decision import record_only
    production = record_only(packet)
    contract = read_json(run/'acceptance.json') if (run/'acceptance.json').exists() else None
    trials = read_json(run/'image-tests.json') if (run/'image-tests.json').exists() else []
    if not isinstance(trials, list) or len(trials) > 2 or len({t['round'] for t in trials}) != len(trials):
        raise AgentError('API候选记录无效或重复')
    generated = [r for r in state['requests'] if r['phase'] == 'generate' and r['state'] == 'completed']
    for row in generated:
        snap = _verified(within(run, row['snapshot']))
        solution = Solution.model_validate(snap['solution'])
        validate_solution(packet, solution)
        if snap['packet']['digest'] != packet['digest'] or acceptance(packet, solution.design) != contract:
            raise AgentError('API生成快照与当前任务/冻结标准不一致')
        image = within(run, row['output']['file']).read_bytes()
        from .generation_evidence import for_api
        generation_evidence = for_api(run, packet, contract, image, request_id=row['id'])
        if sha(image) in snap['request']['image_sha256']:
            raise AgentError('服务返回输入原图，不能恢复为新候选')
        generation = snap['generation']
        from .reference_gate import validate_generation_inputs
        ledger = validate_generation_inputs(packet, contract['baseline_reference_ids'], generation['inputs'])
        if snap['request']['image_sha256'] != [r['sha256'] for r in generation['inputs']]:
            raise AgentError('API生成快照输入图片清单不一致')
        trial = next((t for t in trials if t['round'] == row['round']), None)
        if trial is None:
            trial = {'round': row['round'], 'file': row['output']['file'], 'sha256': sha(image),
                     'prompt': snap['request']['prompt'], 'model_id': generation['model_id'],
                     'target': 'image', 'origin': 'api_image_bytes', 'solution': snap['solution'],
                     'spec': solution.spec.model_dump(), 'contract_digest': contract['digest'],
                     'generation_inputs': generation['inputs'],
                     'reference_usage': {**ledger, 'submission_evidence': 'api_images_submitted'},
                     'review_authority': 'not_performed', 'review': None, 'eval_input_images': []}
            trials.append(trial)
        if (trial['sha256'] != sha(image) or trial['solution'] != snap['solution'] or
                trial['prompt'] != snap['request']['prompt'] or trial['generation_inputs'] != generation['inputs']):
            raise AgentError('API候选与生成前方案/返回图片不一致')
        if production:
            if 'generation_evidence' in trial and trial['generation_evidence'] != generation_evidence:
                raise AgentError('生产候选生成证据与原始调用不一致')
            trial['generation_evidence'] = generation_evidence
        review = _completed(state, 'evaluate', row['round'])
        if review:
            if (packet.get('source_contract_version') == 1 and trial['review'] is not None and
                    trial.get('generation_evidence') != generation_evidence):
                raise AgentError('已保存API候选的生成证据被改变')
            evaluation = ImageEval.model_validate(read_json(within(run, review['output']['file'])))
            validate_eval(contract, evaluation)
            eval_snap = _verified(within(run, review['snapshot']))
            payload = eval_snap['request']['payload']
            if contract.get('quality_contract_version') == 1:
                from .workflow import eval_input
                if payload != eval_input(packet, contract, image, generation_evidence=generation_evidence):
                    raise AgentError('API首次复查输入与冻结白名单及原调用绑定不一致')
            elif packet.get('source_contract_version') == 1 and payload.get('generation_evidence') != generation_evidence:
                raise AgentError('API复查缺少与原生成请求一致的证据')
            from .design import validate_diagnosis
            if packet.get('source_contract_version') == 1:
                validate_diagnosis(evaluation, generation_evidence)
            expected = [r['sha256'] for r in packet['references']] + [trial['sha256']]
            if (eval_snap['request']['image_sha256'] != expected or
                    (contract.get('quality_contract_version') != 1 and payload['contract'] != contract)):
                raise AgentError('API复查返回未绑定当前候选和标准')
            if trial['review'] is not None and trial['review'] != evaluation.model_dump():
                raise AgentError('API复查记录与已接收结果不一致')
            trial.update(review=evaluation.model_dump(), review_authority='machine_reviewed_untrusted',
                         eval_input_images=[{'id': r['id'], 'sha256': r['sha256']}
                                            for r in eval_snap['request']['payload']['images_in_order']])
            if packet.get('source_contract_version') == 1:
                trial['generation_evidence'] = generation_evidence
        elif trial['review'] is not None:
            raise AgentError('API复查缺少持久化请求和返回证据')
        validate_trial(packet, run, contract, trial)
    if len(trials) != len(generated):
        raise AgentError('API候选缺少对应已完成生成预留')
    if production and len(trials) > 1:
        raise AgentError('生产首图策略不能恢复成机器筛选多张候选')
    trials.sort(key=lambda t: t['round'])
    selection, comparison = (0 if trials else None), None
    if not production and len(trials) == 2 and all(t['review'] for t in trials):
        compare = _completed(state, 'compare', trials[-1]['round'])
        if compare:
            pair = PairComparison.model_validate(read_json(within(run, compare['output']['file'])))
            snap = _verified(within(run, compare['snapshot']))
            if (snap['request']['image_sha256'] != [r['sha256'] for r in packet['references']] + [t['sha256'] for t in trials]
                    or snap['request']['payload']['contract'] != contract):
                raise AgentError('API成对比较未绑定当前两图和固定标准')
            from .revision import select_pair
            selection, comparison = select_pair(pair, trials)
    return packet, contract, trials, selection, comparison


def _research_review_append(run: Path, trials: list[dict]) -> list[dict] | None:
    """Recognize only the first verified research review of an exported image.

    The caller has already reconstructed ``trials`` from durable API receipts.
    It must still verify the previous export and manifest before advancing it.
    """
    if len(trials) != 1 or not (run/'image-tests.json').exists():
        return None
    previous = read_json(run/'image-tests.json')
    if not isinstance(previous, list) or len(previous) != 1 or not isinstance(previous[0], dict):
        return None
    before, after = previous[0], trials[0]
    review_fields = {'review', 'review_authority', 'eval_input_images'}
    if (before.get('review') is not None or not after.get('review')
            or before.get('review_authority') != 'not_performed' or before.get('eval_input_images') != []
            or {k: v for k, v in before.items() if k not in review_fields}
            != {k: v for k, v in after.items() if k not in review_fields}):
        return None
    return previous


def inspect_api_run(run: Path, *, _dispatch_lock_held: bool = False) -> dict:
    from .run_status import _apply_generation_control
    from .workflow_policy import apply_to_status
    return apply_to_status(run, _apply_generation_control(
        run, _inspect_api_run(run, _dispatch_lock_held=_dispatch_lock_held)))


def _inspect_api_run(run: Path, *, _dispatch_lock_held: bool = False) -> dict:
    run = Path(run).resolve()
    report = {'format': 'liu.aesthetic.run-status.v1', 'run': str(run), 'route': 'api',
              'stage': 'blocked', 'blockers': [], 'generation_allowed': False, 'candidates': [],
              'human_acceptance': 'not_verified', 'visual_validation': 'not_performed_by_status',
              'next_action': {'command': None, 'description': '核对API证据；不重发未知请求'},
              'budget': {'recovery_safe': False, 'attempted_calls': None, 'attempted_images': None}}
    try:
        state = load_api_state(run)
        report['budget'] = _budget(state)
        from .execution_state import execution_facts
        report.update(execution_facts(run, api_state=state))
        report['requests'] = [{k: r[k] for k in ('id', 'phase', 'round', 'kind', 'state')} for r in state['requests']]
        if (run/'host-dispatches.json').exists() or (run/'dispatch.json').exists():
            raise AgentError('此目录混有API与宿主调用记录；不能分别计数或另起预算，请核对原调用路线')
        if (run/'.dispatch.lock').exists() and not _dispatch_lock_held:
            raise AgentError('存在dispatch写入锁；先核对活动进程，不删除锁或重复提交')
        packet, contract, trials, selection, comparison = _evidence(run, state)
        from .visual_decision import record_only
        production = record_only(packet)
        report['candidates'] = [{'round': t['round'], 'file': t['file'], 'sha256': t['sha256'], 'evaluated': bool(t['review'])} for t in trials]
        unresolved = [r['id'] for r in state['requests'] if r['state'] in ('pending', 'outcome_unknown')]
        if unresolved:
            report.update(stage='awaiting_result', next_action={'command': None, 'request_ids': unresolved,
                          'description': '核对原请求真实结果；不会自动重发'})
            return report
        failed = [r for r in state['requests'] if r['state'] == 'failed']
        missing_reviews = [t for t in trials if not t['review']]
        if missing_reviews and not production:
            if any(r['phase'] == 'evaluate' and r['round'] in {t['round'] for t in missing_reviews} for r in failed):
                raise AgentError('已有复查请求失败；恢复不自动重试或增加预算')
            stage, description = 'needs_eval', '恢复已有候选并准备实际看图复查；不生成'
        elif len(trials) == 2 and not comparison and not production:
            if any(r['phase'] == 'compare' for r in failed):
                raise AgentError('已有比较请求失败；恢复不自动重试或增加预算')
            stage, description = 'needs_compare', '按固定标准比较已有两图；不生成'
        elif (run/'solution.json').exists():
            from .workflow import validate_solution
            solution = Solution.model_validate(read_json(run/'solution.json'))
            validate_solution(packet, solution)
            if acceptance(packet, solution.design) != contract:
                raise AgentError('API当前方案与固定标准不一致')
            stage, description = 'ready_to_export', '导出已有方案、候选及复查证据'
            if (run/'result.json').exists():
                from .run_status import _export_files_valid, _verify_export
                chosen = {'selected_index': selection, 'comparison': comparison} if trials else None
                previous = _research_review_append(run, trials) if production else None
                verified_trials = previous if previous is not None else trials
                report['human_acceptance'], feedback_pending = _verify_export(
                    run, packet, contract, solution, verified_trials, [], chosen)
                exported_result = read_json(run/'result.json')
                for field in ('candidate_feedback', 'relative_preference'):
                    if exported_result.get(field):
                        report[field] = exported_result[field]
                rendered = _export_files_valid(run, verified_relative=bool(
                    exported_result.get('candidate_feedback') or exported_result.get('relative_preference')))
                if previous is not None:
                    stage, description = 'ready_to_export', '追加已核验的研究评审记录；原图和用户选择保持，不新增请求'
                elif feedback_pending:
                    report['render_only'] = True
                    stage, description = 'ready_to_export', '用户反馈来源已核验；恢复导出，不重发生成请求'
                elif rendered:
                    stage, description = 'exported', '交付证据一致；视觉验收仍单独进行'
                else:
                    report['render_only'] = True
        else:
            if packet['need_mode'] == 'offline_heuristic':
                stage = 'needs_parse'
            elif early_blockers(packet):
                stage = 'needs_input'
            elif initial_assessment_required(packet):
                stage = 'needs_intent_assessment'
            elif packet['references'] and not _observed(packet):
                stage = 'needs_observation'
            elif needs_assessment(packet):
                stage = 'needs_direction'
            elif interaction_blockers(packet):
                stage = 'needs_input'
            elif needs_reference(packet):
                stage = 'needs_reference'
            else:
                from .workflow import design_input
                from . import instructions as ins
                request = {'instruction': ins.stage_instruction('DESIGN', packet), 'payload': design_input(packet), 'output_type': 'Plan',
                           'image_sha256': [r['sha256'] for r in packet['references']]}
                stage = 'needs_compile' if _saved_response(run, state, 'design', request) else 'needs_design'
            description = '先复用完整阶段快照；缺失文本阶段需要明确API授权，恢复不会生图'
        report.update(stage=stage, next_action={'command': 'resume' if stage != 'exported' else None, 'description': description})
        if stage == 'exported' and report['human_acceptance'] == 'rejected_by_user':
            from .exporter import rejection_next_action
            report['next_action'] = rejection_next_action(run, read_json(run/'result.json'), packet)
        elif stage == 'exported':
            from .exporter import uncertain_next_action
            result = read_json(run/'result.json')
            report['next_action'] = uncertain_next_action(result) or (
                result.get('next_action') if report['human_acceptance'] == 'not_recorded' else None) or report['next_action']
            if report.get('candidate_feedback') and report['human_acceptance'] == 'not_recorded':
                report['next_action'] = {'command': None, 'description':
                    '已记录当前候选的正向评价与原话；沿这版继续，修改前明确改变与保留范围。'}
    except _ERRORS as exc:
        report['blockers'].append(str(exc) if isinstance(exc, AgentError) else 'API证据文件缺失、损坏或字段无效')
        report['stage'] = 'blocked'
    return report


def _resume_reassessment(report):
    if report['stage'] == 'exported' and report['next_action'].get('action') == 'reassessment_required':
        return {**report, 'action': 'reassessment_required',
                'artifacts': report['next_action']['inputs'], 'requests_submitted': 0}
    return report


def resume_api_run(run: Path, provider=None, *, _before_local_action=None) -> dict:
    """Recover local evidence; only an explicit provider may evaluate or compare."""
    from .host import dispatch_lock
    run = Path(run).resolve()
    report = inspect_api_run(run)
    if report['stage'] in ('blocked', 'awaiting_result', 'exported', 'needs_input', 'needs_reference'):
        return _resume_reassessment(report)
    with dispatch_lock(run):
        if _before_local_action is not None:
            _before_local_action()
        # The outer inspection is only a hint. Another worker may have finished
        # or failed a paid stage before this lock became ours.
        current = inspect_api_run(run, _dispatch_lock_held=True)
        if current['stage'] in ('blocked', 'awaiting_result', 'exported', 'needs_input', 'needs_reference'):
            return _resume_reassessment(current)
        state = load_api_state(run)
        if any(r['state'] in ('pending', 'outcome_unknown') for r in state['requests']):
            raise AgentError('API证据在取得锁前出现未知请求；停止恢复，禁止重发')
        if current.get('render_only'):
            from .exporter import export_result
            from .feedback import apply_feedback, apply_relative_preference
            result = read_json(run/'result.json')
            apply_feedback(run, result)
            apply_relative_preference(run, result)
            export_result(result, run)
            resumed_stage, early = 'exported', True
        elif not (run/'solution.json').exists() and not any(r['kind'] == 'image' for r in state['requests']):
            resumed_stage = _resume_design(run, state, provider)
            # Release before read-only inspection, which respects active locks.
            early = True
        else:
            early = False
        if early:
            report = {'stage': resumed_stage}
        else:
            _resume_candidates(run, state, provider)
    result = inspect_api_run(run)
    if early and report['stage'] not in ('exported',):
        result['stage'] = report['stage']
    result['resumed'] = True
    return _resume_reassessment(result)


def _resume_candidates(run, state, provider):
    from . import instructions as ins
    from .workflow import eval_input, finish, reference_bytes
    packet, contract, trials, selection, comparison = _evidence(run, state)
    from .visual_decision import record_only
    if record_only(packet):
        write_json(run/'image-tests.json', trials)
        from .workflow_policy import inspect_completion
        completion = inspect_completion(run, packet)
        if completion['applicable'] and not completion['ready']:
            return
        solution = Solution.model_validate(trials[0]['solution'] if trials else read_json(run/'solution.json'))
        finish(run, solution, 'openai_api', trials)
        return
    durable = DurableProvider(run, provider, state) if provider is not None else None
    refs = reference_bytes(packet, run, cloud=True) if durable else None
    for trial in trials:
        if trial['review'] is not None:
            continue
        image = within(run, trial['file']).read_bytes()
        from .generation_evidence import for_api
        evidence = for_api(run, packet, contract, image)
        payload = eval_input(packet, contract, image, generation_evidence=evidence)
        write_json(run/f"tests/eval-input-{trial['round']}.json", payload)
        if durable:
            generation = _completed(state, 'generate', trial['round'])
            snapshot = _verified(within(run, generation['snapshot']))
            durable.checkpoint(**{k: snapshot[k] for k in ('packet', 'plan', 'texts', 'solution', 'round')})
            evaluation = durable.structured(ins.EVALUATE, payload, ImageEval, refs+[image], phase='evaluate')
            validate_eval(contract, evaluation)
            from .design import validate_diagnosis
            validate_diagnosis(evaluation, evidence)
            trial.update(review=evaluation.model_dump(), review_authority='machine_reviewed_untrusted',
                         generation_evidence=evidence,
                         eval_input_images=[{'id': r['id'], 'sha256': r['sha256']} for r in payload['images_in_order']])
    write_json(run/'image-tests.json', trials)
    if len(trials) == 2 and all(t['review'] for t in trials) and not comparison:
        payload = {'brief': packet['brief'], 'contract': contract,
                   'revision_focus': {'changes': trials[0]['review']['changes'], 'preserve': trials[0]['review']['preserve']},
                   'images_in_order': [{'id': r['id'], 'role': r['role']} for r in packet['references']]+[{'id': 'A'}, {'id': 'B'}]}
        write_json(run/'tests/compare-input.json', payload)
        if durable:
            generation = _completed(state, 'generate', trials[-1]['round'])
            snapshot = _verified(within(run, generation['snapshot']))
            durable.checkpoint(**{k: snapshot[k] for k in ('packet', 'plan', 'texts', 'solution', 'round')})
            pair = durable.structured(ins.COMPARE, payload, PairComparison,
                                      refs+[within(run, t['file']).read_bytes() for t in trials], phase='compare')
            from .revision import select_pair
            selection, comparison = select_pair(pair, trials)
            write_json(run/'comparison.json', comparison)
    if all(t['review'] for t in trials) and (len(trials) < 2 or comparison):
        from .workflow_policy import inspect_completion
        completion = inspect_completion(run, packet)
        if completion['applicable'] and not completion['ready']:
            return
        solution = Solution.model_validate(trials[selection]['solution'] if trials else read_json(run/'solution.json'))
        finish(run, solution, 'openai_api', trials, selection, comparison)
