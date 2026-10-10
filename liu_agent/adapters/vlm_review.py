"""An isolated VLM input/output boundary, not a model client or approval gate."""
from __future__ import annotations

import re

from ..contracts import ImageEval
from ..design import validate_eval
from ..io import AgentError, digest
from ..instructions import GOAL_FIRST_REVIEW
from ..quality_review import goal_first_payload, task_evaluation_card


def prepare_input(packet: dict, contract: dict, refs: list, candidate_sha: str,
                  evidence: dict | None = None) -> dict:
    if contract.get('quality_contract_version') != 1:
        raise AgentError('此隔离评审适配器仅用于已有quality_contract_version=1任务，不迁移旧合同')
    if not re.fullmatch(r'[0-9a-f]{64}', candidate_sha):
        raise AgentError('候选图必须有完整SHA256绑定')
    payload = goal_first_payload(packet, contract, refs, candidate_sha, evidence)
    payload['task_evaluation_card'] = task_evaluation_card(packet, contract, refs)
    return {'format': 'hex.vlm-review-input.v1', 'payload': payload,
            'output_schema': ImageEval.model_json_schema(), 'model_call_state': 'not_run',
            'usage': {'model_calls': 0},
            'instructions': GOAL_FIRST_REVIEW}


def validate_output(contract: dict, output: dict, *, candidate_sha: str,
                    reviewed_candidate_sha: str) -> dict:
    if contract.get('quality_contract_version') != 1:
        raise AgentError('不能为旧任务静默升级评审合同')
    if contract.get('digest') != digest({k: v for k, v in contract.items() if k != 'digest'}):
        raise AgentError('评审须绑定未变化的冻结合同')
    if not re.fullmatch(r'[0-9a-f]{64}', candidate_sha) or candidate_sha != reviewed_candidate_sha:
        raise AgentError('视觉评审绑定的候选图已变化')
    evaluation = ImageEval.model_validate(output)
    validate_eval(contract, evaluation)
    if evaluation.holistic.review_context != 'isolated':
        raise AgentError('该入口需要独立隔离上下文；作者自评应另行标注')
    judgments = [*evaluation.items, *evaluation.holistic.judgments, *evaluation.baseline_comparisons]
    if evaluation.holistic.image_access != 'viewed' and any(item.verdict != 'uncertain' for item in judgments):
        raise AgentError('未实际看图时具体项也必须uncertain，不能只把总判断改为uncertain')
    return {'format': 'hex.vlm-review-validation.v1', 'state': 'schema_and_coverage_valid',
            'candidate_sha256': candidate_sha, 'contract_digest': contract['digest'],
            'review': evaluation.model_dump(), 'user_acceptance': 'not_recorded_by_machine_review',
            'limits': ['程序验证输入绑定与输出覆盖；无法认证声明的隔离上下文和实际看图行为',
                       '本接口不调用模型；真实调用回执和消耗须沿原宿主执行记录保存']}
