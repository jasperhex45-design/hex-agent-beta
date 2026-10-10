"""Frozen, opt-in visual decision strategies; never evidence of visual quality."""
from __future__ import annotations

from .io import AgentError

DEFAULT_PROFILE = 'production-baseline'
KNOWLEDGE_PROFILE = 'knowledge-assisted-v1'
CAR_PROFILE = 'car-photography-v1'
CAR_KNOWLEDGE_PROFILE = 'car-photography-knowledge-v1'
CAR_PROFILES = frozenset((CAR_PROFILE, CAR_KNOWLEDGE_PROFILE))
KNOWLEDGE_PROFILES = frozenset((KNOWLEDGE_PROFILE, CAR_KNOWLEDGE_PROFILE))
PROFILES = (DEFAULT_PROFILE, KNOWLEDGE_PROFILE, 'visual-relations-v1',
            CAR_PROFILE, CAR_KNOWLEDGE_PROFILE, 'legacy')


def build_policy(profile: str) -> dict | None:
    """Build a new task's policy. Legacy is represented by an absent marker."""
    if not isinstance(profile, str) or profile not in PROFILES:
        raise AgentError('未知 execution_profile；请选择 ' + '、'.join(PROFILES))
    if profile == 'legacy':
        return None
    if profile in KNOWLEDGE_PROFILES:
        return {
            'version': 2, 'profile': profile,
            'strategy_version': CAR_PROFILE if profile == CAR_KNOWLEDGE_PROFILE else None,
            'machine_review': 'record_only', 'experience_retrieval': 'disabled',
            'knowledge_retrieval': 'bounded_local_cards_v1',
        }
    return {
        'version': 1,
        'profile': profile,
        'strategy_version': profile if profile in ('visual-relations-v1', 'car-photography-v1') else None,
        'machine_review': 'record_only',
        'experience_retrieval': 'disabled',
    }


def policy(packet: dict) -> dict | None:
    """Read without migration; reject malformed or unsupported policy markers."""
    if 'visual_decision_policy' not in packet:
        return None
    value = packet['visual_decision_policy']
    if not isinstance(value, dict) or type(value.get('version')) is not int or value.get('version') not in (1, 2):
        raise AgentError('visual_decision_policy 版本无效；不能静默回退或迁移任务')
    expected = build_policy(value.get('profile'))
    if expected is None or value != expected:
        raise AgentError('visual_decision_policy 配置无效；须保持冻结的策略、只记录评审和禁用经验检索设置')
    return dict(expected)


def record_only(packet: dict) -> bool:
    return policy(packet) is not None


def validate_context(packet: dict) -> None:
    """Reject experience payloads in production, including direct helper writes."""
    from .co_creation import validate_state
    validate_state(packet)
    from .method_context import enabled
    enabled(packet)
    from .reference_library import validate_context as validate_local_references
    validate_local_references(packet)
    selected = policy(packet)
    if selected is None:
        if packet.get('knowledge_retrieval_config') is not None:
            raise AgentError('legacy 任务不允许静默增加知识检索配置')
        return
    if selected['profile'] in CAR_PROFILES:
        if (selected['profile'] == CAR_KNOWLEDGE_PROFILE
                and packet.get('request_input_version') != 1):
            raise AgentError('新汽车知识任务须保留完整输入冻结版本；不能降为历史任务')
        brief = packet.get('brief', {})
        if (packet.get('need', {}).get('domain') != 'vehicle'
                or brief.get('medium', 'still') != 'still'
                or not isinstance(brief.get('targets'), list)
                or len(brief['targets']) != 1
                or brief['targets'][0] not in ('seedream', 'image')
                or brief.get('model_comparison', False)):
            raise AgentError('汽车候选仅用于车辆静态单图；其他任务使用原入口，不自动迁移')
        from .car_photography import validate_vehicle_identity
        validate_vehicle_identity(brief, packet.get('references', []), packet.get('need'))
        identities = [r for r in packet.get('references', [])
                      if r.get('role') == 'identity' and not r.get('evaluation_only', False)]
        if identities and packet.get('reference_analysis') is not None:
            from .reference_gate import generation_eligible
            if not any(generation_eligible(packet, ref) for ref in identities):
                raise AgentError('车辆 identity 未被当前观察采纳；不能静默转成无车辆参考的生成')
    config = packet.get('style_context_config', {})
    if not isinstance(config, dict) or config.get('enabled', False) is not False:
        raise AgentError('生产配置禁止启用 style_context 经验召回')
    retrieval = packet.get('retrieval', {})
    knowledge = packet.get('design_knowledge', {})
    if selected['profile'] in KNOWLEDGE_PROFILES:
        from .knowledge_context import validate_context as validate_knowledge
        validate_knowledge(packet)
    elif packet.get('knowledge_retrieval_config') is not None:
        raise AgentError('旧任务不允许新增知识检索配置；须显式建立新任务')
    if (not isinstance(retrieval, dict) or
            (retrieval.get('hits') and selected['profile'] not in KNOWLEDGE_PROFILES) or
            not isinstance(knowledge, dict) or any(knowledge.get(key) for key in
                                                  ('mechanisms', 'relations', 'sources', 'cultural_grammars'))):
        raise AgentError('生产配置禁止注入未经晋升的核心知识或设计机制')
    for key in ('private_style_profile', 'aesthetic_feedback', 'template_retrieval',
                'aesthetic_retrieval', 'visual_retrieval'):
        if packet.get(key):
            raise AgentError('生产配置禁止注入未经晋升的经验上下文：' + key)
    context = packet.get('style_context', {})
    if not isinstance(context, dict) or (context and context != {'status': 'disabled'}):
        raise AgentError('生产配置仅允许关闭状态的 style_context')


def strategy_instruction(packet: dict) -> str:
    selected = policy(packet)
    if selected is None or selected['strategy_version'] is None:
        return ''
    if selected['profile'] in CAR_PROFILES:
        from .method_context import CAR_EDIT_INSTRUCTION, build_method_context
        context = build_method_context(packet, 'design')
        if context is not None and context['task_mode'] == 'local_edit':
            return CAR_EDIT_INSTRUCTION
        from .car_photography import DESIGN_INSTRUCTION
        return DESIGN_INSTRUCTION
    return '''
实验候选 visual-relations-v1：尚未证明首图收益，不是已验证经验或用户长期偏好。
先从用户目标反推哪一种可见关系主要承担该感受或用途，而不是先选风格：观众首先注意什么，主体之间或主体与环境怎样作用。把承担首要作用的关系放在既有 choices 第一项，其余选择明确承托它或形成有意对抗，并交代为此放弃什么。
生成前在当前推理中做一次反事实检查：若移去首要关系，预期感受或用途仍不变，就重选承担作用的关系；增加风格形容词不算策略执行。这是设计假设自检，不是看图结果，不追加模型调用、生成、候选方向或表单。选择数量随任务需要，不强迫凑三条。
将决定写成可见的 action/cooperation/tradeoff，绑定当前目标与 criterion_id；它仍是 agent_hypothesis，不能提升为用户硬限制。用已有 checks 描述预期作用与失败界限，后续图片与人工选择才能检验此实验假设。
'''
