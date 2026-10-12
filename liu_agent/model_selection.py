"""Explicit task targets; available profiles/tools never select for the user."""
from .io import AgentError


MODEL_DECISION = 'brief.targets'
INSTRUCTION = '''
先核对当前任务原话、已有回答和明确委托中的目标生成模型。已有明确选型沿用，不重复提问；只委托构图、风格或参考不等于委托选模型。用户未指定且未委托模型选择时，先在对话中问“这次提示词准备给哪个模型使用？”，不能凭工具可用、已加载档案、默认值或模型自信代选。型号或版本会实质改变写法但仍不明时，再问具体型号；不要求用户选择内部模板。
首次问模型就使用宿主实际可用的选择组件，给真实路线或明确选型委托的可点击选项及自由填写；先遵守组件适用范围与参数规则，不等用户追问选择栏。确无适用组件才说明并用文字选项；普通按钮标签不写成内部方向草案。
创建新任务前先解决目标模型，按真实来源显式填写 brief.targets。已建任务中出现未决的选型问题时，沿现有 question_records 登记 technical、affected_decision=brief.targets 和稳定问题 ID，保持 open；用户回答或已有明确选择/委托有可靠来源后才解决，不能由宿主自行撤回。该问题不属于可自主补齐的技术参数；不得把默认模型写成用户已经指定。
'''


def require_explicit_targets(raw: dict) -> None:
    """Guard new inputs without changing how historical Brief data is read."""
    if not isinstance(raw, dict):
        raise AgentError('brief 必须是 JSON 对象')
    if 'targets' not in raw or raw['targets'] is None or raw['targets'] == []:
        raise AgentError('缺少已确认的目标生成模型 targets；先询问用户使用哪个模型，'
                         '或沿用当前任务已有的明确选择/模型选择委托，再显式填写；不能自动代选')


def resolve_task_target(packet: dict, target: str | None, *, action: str = '编译') -> str:
    """A sole selected target may be reused; a profile is not a selection."""
    targets = packet.get('brief', {}).get('targets')
    if (not isinstance(targets, list) or not targets
            or any(not isinstance(value, str) or not value.strip() for value in targets)
            or len(targets) != len(set(targets))):
        raise AgentError('当前任务缺少有效目标模型；先核对用户选择，不按工具或档案代选')
    if target is None:
        if len(targets) != 1:
            raise AgentError(f'当前任务包含多个已选模型；请显式传 --target 指定本次{action}目标，不能默认选择 image')
        return targets[0]
    if target not in targets:
        raise AgentError(f'{action}目标不在当前 brief.targets 中；可用模型档案或工具不等于用户选择：' + str(target))
    return target
