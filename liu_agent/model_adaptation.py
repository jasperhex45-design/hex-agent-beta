"""Task-bound model writing choices; coverage is not semantic or visual proof."""
from __future__ import annotations

import unicodedata
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, ValidationError, model_serializer

from .io import AgentError, digest


Nonblank = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=18000)]


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", validate_assignment=True, revalidate_instances="always")


class AdaptationRule(_Strict):
    rule_index: int = Field(ge=0, strict=True)
    reason: Nonblank
    prompt_excerpt: Nonblank


class ChoiceCoverage(_Strict):
    choice_id: Nonblank
    prompt_excerpt: Nonblank


class RevisionContext(_Strict):
    model_config = ConfigDict(strict=True)
    candidate_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    candidate_role: Literal["edit_target", "edit_canvas"]
    review_digest: str = Field(pattern=r"^[a-f0-9]{64}$")
    preserve: list[str]
    changes: list[str]


class PromptAdaptation(_Strict):
    model_config = ConfigDict(json_schema_extra=lambda schema: schema.update(required=list(schema["properties"])))
    context_digest: str = Field(pattern=r"^[a-f0-9]{64}$")
    strategy: Nonblank
    rules: list[AdaptationRule] = Field(min_length=1)
    choice_coverage: list[ChoiceCoverage]
    direction_excerpt: Nonblank | None
    template_excerpt: Nonblank | None
    aesthetic_excerpt: Nonblank | None
    revision: RevisionContext | None = None
    revision_excerpt: Nonblank | None = None

    @model_serializer(mode="wrap")
    def legacy_dump(self, handler):
        result = handler(self)
        for key in ("revision", "revision_excerpt"):
            if key not in self.model_fields_set:
                result.pop(key, None)
        return result


def _plain(value):
    if hasattr(value, "model_dump"):
        return _plain(value.model_dump())
    if isinstance(value, dict):
        return {key: _plain(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_plain(item) for item in value]
    if hasattr(value, "__dict__"):
        return _plain(vars(value))
    return value


def _revision(value) -> dict | None:
    if value is None:
        return None
    try:
        return RevisionContext.model_validate(_plain(value)).model_dump()
    except ValidationError as exc:
        raise AgentError("返修适配必须绑定真实候选图、审核摘要及保留和修改项") from exc


def revision_context(candidate_sha256, role, review) -> dict:
    review = _plain(review)
    if not isinstance(review, dict):
        raise AgentError("返修适配缺少真实审核记录")
    return _revision({"candidate_sha256": candidate_sha256, "candidate_role": role,
                      "review_digest": digest(review), "preserve": review.get("preserve"),
                      "changes": review.get("changes")})


def _read_adaptations(rows):
    try:
        return [PromptAdaptation.model_validate(row.get("adaptation")) for row in rows]
    except ValidationError as exc:
        raise AgentError("每个新模型提示词都需要完整的适配依据，不能仅换标题") from exc


def require_revision(compilation, expected) -> None:
    expected = _revision(expected)
    rows = _plain(compilation)["prompts"]
    if not rows or any(_revision(row.revision) != expected for row in _read_adaptations(rows)):
        raise AgentError("提示词返修依据与当前真实候选图或审核不一致，须重新编译")


def build_context(packet, spec, design, targets=None, revision=None) -> dict:
    # Lazy imports allow contracts.py to re-export PromptAdaptation safely.
    from .creative_directions import selected_direction_input
    from .reference_gate import generation_eligible

    spec, design = _plain(spec), _plain(design)
    targets = list(packet["brief"]["targets"] if targets is None else targets)
    if not targets or len(targets) != len(set(targets)):
        raise AgentError("模型适配目标必须非空且不重复")
    if any(target not in packet["model_profiles"] for target in targets):
        raise AgentError("模型适配目标缺少已冻结的模型档案")
    references = [{key: ref.get(key) for key in ("id", "role", "sha256", "note")}
                  for ref in packet.get("references", []) if generation_eligible(packet, ref)]
    need = packet.get("need", {})
    task = {"request_summary": packet["brief"].get("request"),
            "need": _plain({key: need.get(key) for key in ("intent", "subject", "desired_feeling", "must_keep", "avoid", "understanding")}),
            "input_sources_digest": (packet.get("input_sources") or {}).get("digest"),
            "scope": packet["brief"].get("scope"), "medium": packet["brief"].get("medium", "still"),
            "task_mode": (need.get("understanding") or {}).get("task_mode"),
            "spec": spec, "selected_direction": selected_direction_input(design),
            "template_binding": design.get("template_binding"),
            "aesthetic_binding": design.get("aesthetic_binding"),
            "generation_reference_order": references}
    from .reference_roles import compilation_context
    roles = compilation_context(packet, design)
    if roles is not None:
        task['reference_roles'] = roles
    if "compilation_settings_version" in packet:
        version = packet["compilation_settings_version"]
        if type(version) is not int or version != 1:
            raise AgentError("未知输出设置编译上下文版本，不能降级复用旧正文")
        # Share the actual brief-over-design merge used by execution. Older
        # packets deliberately keep their byte-identical compilation contexts.
        from .contracts import DesignPlan
        from .workflow import generation_settings
        task["generation_settings"] = generation_settings(packet, DesignPlan.model_validate(design))
    revision = _revision(revision)
    if revision is not None:
        task["revision"] = revision
        references.append({"id": "PREVIOUS_CANDIDATE", "sha256": revision["candidate_sha256"],
                           "role": revision["candidate_role"]})
    choices = [{key: choice.get(key) for key in ("id", "action", "cooperation", "tradeoff")}
               for choice in design.get("choices", [])]
    from .visual_research import creative_context_enabled, evidence_context
    if creative_context_enabled(packet):
        from .host_references import research_references, observed_ids
        from .user_feedback_context import frozen_context

        checks = {check['id']: check for check in design.get('checks', [])}
        if len(checks) != len(design.get('checks', [])):
            raise AgentError('创作编译依据包含重复检查；先修正当前Plan')
        references_by_id = {ref['id']: ref for ref in research_references(packet)}
        observations = {row['id']: row for row in (packet.get('reference_analysis') or {}).get('findings', [])}
        observed = observed_ids(packet)
        for source_choice, choice in zip(design.get('choices', []), choices):
            criterion = source_choice.get('criterion_id')
            if criterion not in checks:
                raise AgentError('创作编译须绑定当前choice对应的检查，不能只传动作标签')
            evidence = []
            for use in source_choice.get('reference_uses', []):
                ident = use.get('reference_id')
                if ident not in references_by_id or ident not in observations or ident not in observed:
                    raise AgentError('创作编译所用参考缺少当前实际观察，不能用旧描述替代')
                ref = references_by_id[ident]
                observation = observations[ident]
                if not observation.get('seen') or not observation.get('visible_facts', '').strip():
                    raise AgentError('创作编译所用参考缺少可见事实，不能只声明采用')
                evidence.append({
                    'use': _plain(use),
                    'reference': {key: ref[key] for key in
                                  ('id', 'role', 'sha256', 'metadata_digest', 'source_url', 'credit', 'evaluation_only')
                                  if key in ref},
                    'observation': {key: observation[key] for key in
                                    ('visible_facts', 'transfer', 'do_not_transfer', 'interpretation', 'uncertainty',
                                     'source_kind', 'professional_source_url', 'professional_excerpt')
                                    if key in observation}})
            choice.update(goal=source_choice.get('goal'), criterion_id=criterion,
                          check=_plain(checks[criterion]), reference_evidence=evidence)
        task.update(creative_context_version=1,
                    camera=_plain(design.get('camera')),
                    reading_order=design.get('reading_order'),
                    expression=design.get('expression'),
                    visual_research=evidence_context(packet),
                    user_feedback_context=_plain(frozen_context(packet)))
    from .prompt_guides import selected_guides
    guides = selected_guides(packet, targets)
    result = {"version": 1, "design_digest": digest(design), "targets": {}}
    for target in targets:
        profile = _plain(packet["model_profiles"][target])
        context = {"target": target, "design_digest": result["design_digest"],
                   "profile": profile, "profile_digest": digest(profile),
                   "runtime": _plain(packet.get("compilation_runtimes", {}).get(target,
                                      {"status": "unknown_not_inferred_from_family_profile"})),
                   "task": _plain(task), "choices": _plain(choices)}
        if target in guides:
            context['prompt_guide'] = guides[target]
        result["targets"][target] = {**context, "context_digest": digest(context)}
    return result


def _fingerprint(text: str) -> str:
    return "".join(char.casefold() for char in text if not char.isspace()
                   and not unicodedata.category(char).startswith("P"))


def validate_adaptation(packet, spec, design, compilation, targets=None) -> dict:
    if "model_adaptation_version" not in packet:
        return {}
    if type(packet["model_adaptation_version"]) is not int or packet["model_adaptation_version"] != 1:
        raise AgentError("未知模型适配合同版本，不能降级或假称已完成")
    rows = _plain(compilation)["prompts"]
    adaptations = _read_adaptations(rows)
    revisions = [_revision(row.revision) for row in adaptations]
    if not revisions or any(revision != revisions[0] for revision in revisions[1:]):
        raise AgentError("所有模型提示词须绑定同一候选图与审核返修依据")
    contexts = build_context(packet, spec, design, targets, revision=revisions[0])["targets"]
    if sorted(row["target"] for row in rows) != sorted(contexts):
        raise AgentError("模型适配目标漏项、重复或多出未请求的模型")
    result = {}
    for row, adaptation in zip(rows, adaptations):
        target, text = row["target"], row["text"]
        context = contexts[target]
        if adaptation.context_digest != context["context_digest"]:
            raise AgentError("模型适配依据已失效：任务、设计、档案、执行模型或参考输入发生变化")
        if not _fingerprint(text):
            raise AgentError("模型提示词正文必须包含非空内容")
        # Adaptation is a per-target binding to real rules and task inputs.
        # Compatible targets may legitimately share the same complete body;
        # changing prose merely to pass a distinctness check adds no evidence.
        excerpts = [item.prompt_excerpt for item in adaptation.rules + adaptation.choice_coverage]
        indices = [item.rule_index for item in adaptation.rules]
        rules = context["profile"]["writing_rules"]
        if len(indices) != len(set(indices)) or any(index >= len(rules) for index in indices):
            raise AgentError("模型适配规则索引重复或不存在，不能伪造档案依据")
        ids = [item.choice_id for item in adaptation.choice_coverage]
        choices = context["choices"]
        if len(ids) != len(set(ids)) or set(ids) != {choice["id"] for choice in choices}:
            raise AgentError("模型适配须逐项覆盖当前全部设计选择，不能漏项或伪造ID")
        aesthetic = context["task"]["aesthetic_binding"]
        for name, needed in (("direction", context["task"]["selected_direction"] is not None),
                             ("template", context["task"]["template_binding"] is not None),
                             ("aesthetic", bool(aesthetic and aesthetic.get("selected_card_ids"))),
                             ("revision", adaptation.revision is not None)):
            excerpt = getattr(adaptation, name + "_excerpt")
            if needed != (excerpt is not None):
                raise AgentError(f"{name} 适配片段须与当前实际采用的设计绑定一致")
            if excerpt is not None:
                excerpts.append(excerpt)
        # A complete adapted body bypasses the legacy append path, so it must
        # still validate the declared rendering of the selected direction.
        # The model authors that rendering; code proves its current source
        # binding and presence, never translation quality or visual success.
        if context["task"]["selected_direction"] is not None and context["profile"]["language"] == "en":
            from .creative_directions import compile_direction
            rendering = compile_direction(design, row, "en")
            if rendering not in text or rendering not in (adaptation.direction_excerpt or ""):
                raise AgentError("direction 适配片段须包含当前选定方向的完整声明转译，不能用无关正文代替")
        if any(excerpt not in text for excerpt in excerpts):
            raise AgentError("模型适配引用的片段不在实际提示词正文中，不能只写说明假装采用")
        evidence = adaptation.model_dump()
        if adaptation.revision is None:
            evidence.pop("revision", None)
            evidence.pop("revision_excerpt", None)
        evidence["rules"] = [{**item.model_dump(), "rule": rules[item.rule_index]} for item in adaptation.rules]
        excerpts_by_id = {item.choice_id: item.prompt_excerpt for item in adaptation.choice_coverage}
        evidence.pop("choice_coverage")
        evidence["choices"] = [{"choice_id": choice["id"], **{key: choice[key] for key in ("action", "cooperation", "tradeoff")},
                                "prompt_excerpt": excerpts_by_id[choice["id"]]} for choice in choices]
        if context['task'].get('creative_context_version') == 1:
            for source, bound in zip(choices, evidence['choices']):
                bound.update({key: _plain(source[key]) for key in
                              ('goal', 'criterion_id', 'check', 'reference_evidence')})
            evidence['creative_context'] = {
                key: _plain(context['task'][key]) for key in
                ('creative_context_version', 'camera', 'reading_order', 'expression',
                 'visual_research', 'user_feedback_context')}
        evidence.update(profile_digest=context["profile_digest"], runtime=context["runtime"],
                        authority="agent_proposed_not_visual_validated", semantic_preservation="not_proven_by_code")
        result[target] = evidence
    return result
