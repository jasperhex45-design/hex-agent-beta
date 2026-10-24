"""Strict contracts used by local tools, the Codex host, and the Responses API.

Schemas used as model outputs deliberately have no optional/default fields:
Structured Outputs requires all object properties to be listed as required.
"""
from __future__ import annotations
from typing import Literal
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator, model_serializer
from .creative_directions import DirectionDraft, DirectionSelection, DirectionTranslation
from .generation_spec import GenerationSpec
from .model_adaptation import PromptAdaptation

# Poster is a first-class task domain. It is separate from person/vehicle
# because layout, typography and information hierarchy are the deliverable.
Domain = Literal["person", "illustration", "poster", "vehicle", "character", "prop"]
Scope = Literal["create", "edit", "color_only", "relight", "scene"]
Role = Literal["identity", "scene", "style", "lighting", "color", "pose", "wardrobe", "detail", "material", "composition", "edit_target", "aesthetic_positive", "aesthetic_negative", "acceptance_floor"]

class Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", validate_assignment=True)

class Reference(Strict):
    id: str = Field(pattern=r"^[A-Za-z][A-Za-z0-9_-]{0,31}$")
    path: str = Field(min_length=1, max_length=1024)
    role: Role
    note: str = Field(default="", max_length=1000)
    rights: Literal["user_authorized", "unknown"] = "unknown"
    source_url: str = ""
    credit: str = ""
    evaluation_only: bool = False
    required: bool = True

    @model_serializer(mode='wrap')
    def legacy_dump(self, handler):
        result = handler(self)
        if 'required' not in self.model_fields_set:
            result.pop('required', None)
        return result

class HostVisibleReference(Strict):
    """Host-attested conversation image, limited to evaluation-only research."""
    model_config = ConfigDict(json_schema_extra=lambda s: s.update(required=list(s['properties'])))
    id: str = Field(pattern=r"^[A-Za-z][A-Za-z0-9_-]{0,31}$")
    source_id: str = Field(min_length=1, max_length=160)
    locator: str = Field(min_length=1, max_length=2000)
    evidence_type: Literal['host_attested']
    observation_method: Literal['conversation_image']
    observation_limits: str = Field(min_length=1, max_length=2000)
    role: Literal['aesthetic_positive', 'aesthetic_negative', 'style', 'composition', 'material']
    note: str = Field(min_length=1, max_length=1000)
    evaluation_only: Literal[True]
    source_url: str = ''
    credit: str = ''


class GenerationOptions(Strict):
    """Output settings proposed for this task, not an aesthetic grade."""
    model_config = ConfigDict(json_schema_extra=lambda s: s.update(required=list(s['properties'])))
    desired_aspect_ratio: str = "auto"
    requested_size: str | None = None
    quality: str = Field(default="auto", min_length=1, max_length=30)
    output_format: Literal["png", "jpeg", "webp"] = "png"


class ReferencePolicy(Strict):
    version: Literal[1]
    task_mode: Literal['open_creation', 'reference_transfer', 'local_edit']
    understanding_digest: str
    source_digest: str


class Brief(Strict):
    title: str = Field(default="创意任务", min_length=1, max_length=120)
    request: str = Field(min_length=2, max_length=12000)
    domain: Literal["auto", "person", "illustration", "poster", "vehicle", "character", "prop"] = "auto"
    scope: Scope = "create"
    targets: list[str] = Field(default_factory=lambda: ["image", "nano", "midjourney", "seedream"], min_length=1, max_length=8)
    must_keep: list[str] = Field(default_factory=list, max_length=30)
    avoid: list[str] = Field(default_factory=list, max_length=30)
    references: list[Reference] = Field(default_factory=list, max_length=8)
    color_style: str | None = None
    public_search_hint: str = Field(default="", max_length=500)
    medium: Literal["still", "film_shot"] = "still"
    scene_context: str = Field(default="", max_length=5000)
    previous_shot: str = Field(default="", max_length=2000)
    next_shot: str = Field(default="", max_length=2000)
    exploration_allowed: bool = False
    generation_options: GenerationOptions | None = None
    model_comparison: bool = False
    # Explicit car-entry choice: missing identity material must never silently
    # turn a preservation request into a fictional vehicle.
    vehicle_identity: Literal['preserve', 'open'] = 'preserve'
    delivery_level: Literal['exploratory', 'formal', 'high_risk'] | None = None

    @model_serializer(mode='wrap')
    def legacy_dump(self, handler):
        result = handler(self)
        if 'delivery_level' not in self.model_fields_set:
            result.pop('delivery_level', None)
        if 'model_comparison' not in self.model_fields_set:
            result.pop('model_comparison', None)
        if 'vehicle_identity' not in self.model_fields_set:
            result.pop('vehicle_identity', None)
        if 'generation_options' not in self.model_fields_set:
            result.pop('generation_options', None)
        elif self.generation_options is not None:
            result['generation_options'] = self.generation_options.model_dump(exclude_unset=True)
        return result

    @field_validator("targets")
    @classmethod
    def unique_targets(cls, value):
        if len(set(value)) != len(value):
            raise ValueError("targets 不能重复")
        return value

    @model_validator(mode="after")
    def unique_refs(self):
        if self.model_comparison and (self.scope != "create" or len(self.targets) != 2):
            raise ValueError("模型首图比较只支持创建任务的两个明确目标")
        ids = [r.id for r in self.references]
        if len(ids) != len(set(ids)):
            raise ValueError("参考图 id 不能重复")
        if self.scope != "create" and not self.references:
            raise ValueError("编辑/调色需要真实参考图；无图请改为 create，不伪造原图")
        return self

class KnowledgeNeed(Strict):
    query: str
    kind: Literal["task", "style", "composition", "lighting", "color", "material", "constraint"]
    reason: str


class SourceRef(Strict):
    source_id: str
    quote: str


class VisualResearchLimitedExit(Strict):
    """Host-reported limits of open research, never proof of sufficient study."""
    reason: Literal['sources_unavailable', 'no_decision_gain']
    attempts: list[str] = Field(min_length=1)
    unresolved: str = Field(min_length=1)

    @field_validator('attempts', 'unresolved')
    @classmethod
    def nonblank_disclosure(cls, value):
        entries = value if isinstance(value, list) else [value]
        if any(not entry.strip() for entry in entries):
            raise ValueError('limited_exit 的尝试记录与未解决事项不能留空白')
        return value


class VisualResearchAssessment(Strict):
    """Host interpretation of this task, independent of generation references."""
    model_config = ConfigDict(json_schema_extra=lambda s: s.update(required=list(s['properties'])))
    route: Literal['study', 'specified_direction', 'bounded_edit', 'user_waived']
    trigger: Literal['open_brief', 'explicit_research', 'rejected_direction',
                     'user_specified_target', 'local_change', 'user_prohibited_study']
    reason: str = Field(min_length=1)
    visual_question: str
    source_refs: list[SourceRef]
    evidence_requirement: Literal['feedback_review', 'visual_reference', 'professional_analysis'] | None = None
    limited_exit: VisualResearchLimitedExit | None = None

    @model_serializer(mode='wrap')
    def legacy_dump(self, handler):
        result = handler(self)
        for field in ('evidence_requirement', 'limited_exit'):
            if field not in self.model_fields_set:
                result.pop(field, None)
        return result


class ConstraintEvidence(Strict):
    field: Literal['must_keep', 'avoid']
    text: str
    kind: Literal['user_explicit', 'user_confirmed', 'scope_rule']
    source_refs: list[SourceRef]
    rule_id: str


class QuestionRecord(Strict):
    model_config = ConfigDict(json_schema_extra=lambda s: s.update(required=list(s['properties'])))
    id: str
    question: str
    affected_decision: str
    decision_type: Literal['identity', 'text', 'scope', 'reference_role', 'expression', 'technical']
    status: Literal['open', 'answered', 'withdrawn']
    reason: str
    source_refs: list[SourceRef]
    options: list[DirectionDraft] = Field(default_factory=list, max_length=3)
    recommended_id: str | None = None
    selected_id: str | None = None
    selection_source: Literal['user_selected', 'user_explicit', 'user_delegated'] | None = None
    answer_options_digest: str | None = None
    delegation_scope: str = ''
    supersedes: str | None = None

    @model_serializer(mode='wrap')
    def legacy_dump(self, handler):
        result = handler(self)
        for key in ('options', 'recommended_id', 'selected_id', 'selection_source',
                    'answer_options_digest', 'delegation_scope', 'supersedes'):
            if key not in self.model_fields_set:
                result.pop(key, None)
        return result


class DirectionReview(Strict):
    route: Literal['direct', 'ask']
    basis: Literal['user_explicit', 'user_delegated', 'bounded_autonomy', 'unresolved_tradeoff']
    reason: str = Field(min_length=1)
    source_refs: list[SourceRef]
    question_id: str | None


class TaskUnderstanding(Strict):
    task_mode: Literal['open_creation', 'reference_transfer', 'local_edit']
    purpose: str
    audience: str
    change_scope: str
    preserve: list[str]
    reference_roles: list[str]


class BriefChange(Strict):
    model_config = ConfigDict(json_schema_extra=lambda s: s.update(required=list(s['properties'])))
    domain: Domain | None
    scope: Scope | None
    source_refs: list[SourceRef]
    must_keep: list[str] | None = None
    avoid: list[str] | None = None
    targets: list[str] | None = Field(default=None, min_length=1)

    @model_serializer(mode='wrap')
    def legacy_dump(self, handler):
        result = handler(self)
        if 'targets' not in self.model_fields_set:
            result.pop('targets', None)
        return result

class Need(Strict):
    model_config = ConfigDict(json_schema_extra=lambda s: s.update(required=list(s['properties'])))
    domain: Domain
    intent: str
    subject: str
    desired_feeling: list[str]
    must_keep: list[str]
    avoid: list[str]
    assumptions: list[str]
    questions: list[str]
    knowledge_needs: list[KnowledgeNeed]
    public_queries: list[str]
    constraint_evidence: list[ConstraintEvidence] = Field(default_factory=list)
    question_records: list[QuestionRecord] = Field(default_factory=list)
    understanding: TaskUnderstanding | None = None
    brief_change: BriefChange | None = None
    direction_review: DirectionReview | None = None
    visual_research: VisualResearchAssessment | None = None

    @model_serializer(mode='wrap')
    def legacy_dump(self, handler):
        result = handler(self)
        for field in ('constraint_evidence', 'question_records', 'understanding', 'brief_change', 'direction_review', 'visual_research'):
            if field not in self.model_fields_set:
                result.pop(field, None)
        return result

class EvidenceTarget(Strict):
    id: str
    criterion: str
    visible_evidence: str

class VisualSpec(Strict):
    concept: str
    feeling: str
    composition: str
    lighting: str
    color: str
    material: str
    capture: str
    locked_facts: list[str]
    changes: list[str]
    operations: list[Literal["color", "exposure", "lighting", "weather", "geometry", "layout", "content", "texture"]]
    selected_knowledge_ids: list[str]
    assumptions: list[str]
    evidence_targets: list[EvidenceTarget]

class ChoiceTranslation(Strict):
    choice_id: str
    text: str


class PromptDraft(Strict):
    # Old saved prompts may omit the new empty list; newly requested model
    # outputs must still supply every property for strict Structured Outputs.
    model_config = ConfigDict(json_schema_extra=lambda schema: schema.update(required=list(schema['properties'])))
    target: str
    text: str
    # One translation per spec.locked_facts item, in exactly the same order.
    # Keeping this outside prose makes contract checking possible for English MJ.
    translated_locks: list[str]
    translated_aesthetic_relations: list[str] = Field(default_factory=list, max_length=2)
    choice_translations: list[ChoiceTranslation] = Field(default_factory=list, max_length=8)
    direction_translation: DirectionTranslation | None = None
    adaptation: PromptAdaptation | None = None
    setup_notes: list[str]
    limitations: list[str]

    @model_serializer(mode='wrap')
    def legacy_dump(self, handler):
        result = handler(self)
        if 'choice_translations' not in self.model_fields_set:
            result.pop('choice_translations', None)
        if 'direction_translation' not in self.model_fields_set:
            result.pop('direction_translation', None)
        if 'adaptation' not in self.model_fields_set:
            result.pop('adaptation', None)
        return result

class Compilation(Strict):
    prompts: list[PromptDraft]

class IntentClaim(Strict):
    model_config = ConfigDict(json_schema_extra=lambda s: s.update(required=list(s['properties'])))
    quote: str
    interpretation: str
    inferred: bool
    source_refs: list[SourceRef] = Field(default_factory=list)

    @model_serializer(mode='wrap')
    def legacy_dump(self, handler):
        result = handler(self)
        if 'source_refs' not in self.model_fields_set:
            result.pop('source_refs', None)
        return result

class CameraChoice(Strict):
    shot_size: str
    viewpoint_and_distance: str
    focal_equivalent_mm: float | None
    framing_and_scale: str
    focus_and_depth: str
    reason: str

class ReferenceUse(Strict):
    model_config = ConfigDict(json_schema_extra=lambda s: s.update(required=list(s['properties'])))
    reference_id: str
    observed_relation: str
    disposition: Literal['adopt', 'reject'] = 'adopt'
    reason: str = ''

    @model_serializer(mode='wrap')
    def legacy_dump(self, handler):
        result = handler(self)
        for key in ('disposition', 'reason'):
            if key not in self.model_fields_set:
                result.pop(key, None)
        return result


class AestheticBinding(Strict):
    """Bridge a seen aesthetic card to this task's actual design."""
    selected_card_ids: list[str]
    dominant_decision: str = Field(min_length=8)
    supporting_mechanism: str = Field(min_length=8)
    visible_test: str = Field(min_length=12)
    failure_test: str = Field(min_length=12)
    adoption_reason: str = Field(min_length=8)
    not_selected_reason: str = Field(min_length=2)


class TemplateBinding(Strict):
    """Bind a task-conditioned template to an actual design decision."""
    template_id: str = Field(pattern=r"^template\.[a-z0-9_-]{3,80}$")
    adopted_structure: str = Field(min_length=12)
    adoption_reason: str = Field(min_length=8)
    visible_test: str = Field(min_length=12)
    pitfalls_applied: list[str] = Field(min_length=1, max_length=8)
    not_selected_reason: str = Field(min_length=2)

class DesignChoice(Strict):
    model_config = ConfigDict(json_schema_extra=lambda s: s.update(required=list(s['properties'])))
    id: str
    goal: str
    mechanism_ids: list[str]
    action: str
    cooperation: str
    tradeoff: str
    criterion_id: str
    reference_uses: list[ReferenceUse]
    basis: Literal['retrieved_mechanism', 'agent_hypothesis'] = 'retrieved_mechanism'
    core_knowledge_ids: list[str] = Field(default_factory=list, max_length=8)

    @model_serializer(mode='wrap')
    def legacy_dump(self, handler):
        result = handler(self)
        if 'basis' not in self.model_fields_set:
            result.pop('basis', None)
        if 'core_knowledge_ids' not in self.model_fields_set:
            result.pop('core_knowledge_ids', None)
        return result


class SpatialGrammar(Strict):
    """Experimental cross-cultural structure hypothesis, never a style label."""
    id: str = Field(pattern=r"^grammar\.[a-z0-9_-]{3,80}$")
    source_digest: str = Field(pattern=r"^[0-9a-f]{64}$")
    status: Literal["experimental"]
    spatial_model: str = Field(min_length=3)
    viewer_path: str = Field(min_length=3)
    negative_space_function: str = Field(min_length=3)
    dominant_vectors: list[str] = Field(min_length=1, max_length=5)
    layer_plan: str = Field(min_length=3)
    scale_relation: str = Field(min_length=3)
    time_model: str = Field(min_length=3)
    surface_scope: str = Field(min_length=3)
    cultural_reference: Literal["structural_only"]

    @model_validator(mode="before")
    @classmethod
    def default_experimental_markers(cls, value):
        if isinstance(value, dict):
            value = dict(value)
            value.setdefault('status', 'experimental')
            value.setdefault('cultural_reference', 'structural_only')
        return value

class DesignCheck(Strict):
    id: str
    dimension: Literal["expression", "composition", "light_color", "material", "relationship", "overall", "narrative"]
    question: str
    expected: str
    failure: str
    reference_ids: list[str]

class NarrativeChoice(Strict):
    scene_function: str
    shot_function: str
    point_of_view: str
    previous_link: str
    next_link: str
    information_reveal: str
    staging: str
    camera_movement: str
    rhythm: str
    continuity: str
    unknown_context: list[str]

class DesignPlan(Strict):
    model_config = ConfigDict(json_schema_extra=lambda s: s.update(required=list(s['properties'])))
    expression: str
    carrier: Literal["person", "object", "relationship", "environment", "ensemble"]
    intent_claims: list[IntentClaim]
    subject_environment_relation: str
    reading_order: str
    camera: CameraChoice
    choices: list[DesignChoice]
    spatial_grammar: SpatialGrammar | None
    checks: list[DesignCheck]
    baseline_reference_ids: list[str]
    assumptions: list[str]
    narrative: NarrativeChoice | None
    aesthetic_binding: AestheticBinding | None
    template_binding: TemplateBinding | None
    creative_directions: DirectionSelection | None = None
    direction_id: str | None = None
    generation_options: GenerationOptions | None = None
    knowledge_skip_reason: str | None = None
    decision_context_digest: str | None = None

    @model_serializer(mode='wrap')
    def legacy_dump(self, handler):
        result = handler(self)
        for field in ('creative_directions', 'direction_id', 'generation_options', 'knowledge_skip_reason', 'decision_context_digest'):
            if field not in self.model_fields_set:
                result.pop(field, None)
        return result

    @model_validator(mode="before")
    @classmethod
    def default_experimental_grammar(cls, value):
        if isinstance(value, dict) and 'spatial_grammar' not in value:
            value = dict(value)
            value['spatial_grammar'] = None
        return value

class Plan(Strict):
    spec: VisualSpec
    design: DesignPlan

class Solution(Strict):
    packet_digest: str
    spec: VisualSpec
    design: DesignPlan
    compilation: Compilation

class RefFinding(Strict):
    model_config = ConfigDict(json_schema_extra=lambda s: s.update(required=list(s['properties'])))
    id: str
    seen: bool
    useful: bool
    role_in_task: Literal["identity", "canvas", "positive", "negative", "context", "reject"]
    visible_facts: str
    transfer: list[str]
    do_not_transfer: list[str]
    interpretation: str
    uncertainty: str
    source_kind: Literal['generated_candidate', 'user_reference', 'external_work'] | None = None
    professional_source_url: str | None = None
    professional_excerpt: str | None = Field(default=None, max_length=2000)

    @model_serializer(mode='wrap')
    def legacy_dump(self, handler):
        result = handler(self)
        for field in ('source_kind', 'professional_source_url', 'professional_excerpt'):
            if field not in self.model_fields_set:
                result.pop(field, None)
        return result

class ReferenceAnalysis(Strict):
    model_config = ConfigDict(json_schema_extra=lambda s: s.update(required=list(s['properties'])))
    findings: list[RefFinding]
    # Task-bound host judgment, recorded with the observed image manifest.
    # Missing fields retain the serialized shape of frozen older observations.
    selection_state: Literal['exploring', 'acceptable', 'uncertain', 'no_suitable_reference'] | None = None
    selection_reason: str = ''

    @model_serializer(mode='wrap')
    def legacy_dump(self, handler):
        result = handler(self)
        for field in ('selection_state', 'selection_reason'):
            if field not in self.model_fields_set:
                result.pop(field, None)
        return result

class EvalItem(Strict):
    criterion_id: str
    verdict: Literal["met", "not_met", "uncertain"]
    candidate_evidence: str
    reference_evidence: str

class BaselineComparison(Strict):
    reference_id: str
    verdict: Literal["comparable", "below", "uncertain"]
    composition: str
    light_and_material: str
    expression: str
    decisive_gap: str

class RevisionTarget(Strict):
    criterion_id: str = Field(description='绑定本次已判为 not_met 的标准 ID；不得绑定 met 或 uncertain 项。')
    change: str = Field(description='逐字复制 changes 数组中同一位置的动作，不另写同义改述。')


class WholeImageJudgment(Strict):
    dimension: Literal['overall', 'visual_coherence', 'expression']
    verdict: Literal['met', 'not_met', 'uncertain']
    evidence: str = Field(min_length=5, max_length=2000)


class HolisticReview(Strict):
    image_access: Literal['viewed', 'unavailable']
    review_context: Literal['isolated', 'author_context', 'unknown']
    first_impression: str = Field(min_length=5, max_length=2000)
    judgments: list[WholeImageJudgment] = Field(min_length=3, max_length=3)


class ImageEval(Strict):
    model_config = ConfigDict(json_schema_extra=lambda s: s.update(required=list(s['properties'])))
    holistic: HolisticReview | None = None
    decision: Literal["ready_for_user", "revise", "uncertain"]
    items: list[EvalItem]
    baseline_comparisons: list[BaselineComparison]
    diagnosis: Literal["intent", "reference", "design", "compiler", "execution", "unknown"]
    diagnosis_reason: str
    preserve: list[str]
    changes: list[str]
    sequence_validation: Literal["not_applicable", "not_tested"]
    revision_targets: list[RevisionTarget] = Field(
        default_factory=list, max_length=2,
        description='修订时逐项绑定 changes：数量、顺序和 change 正文须与 changes 完全一致，每项指向已知 not_met 标准。')

    @model_serializer(mode='wrap')
    def legacy_dump(self, handler):
        result = handler(self)
        if 'revision_targets' not in self.model_fields_set:
            result.pop('revision_targets', None)
        if 'holistic' not in self.model_fields_set:
            result.pop('holistic', None)
        return result

class PairComparison(Strict):
    choice: Literal["A", "B", "uncertain"]
    evidence: str
    regressions: list[str]


class CalibrationImage(Strict):
    """A real local image bound to a personal preference record.

    The byte hash is captured at recording time and must be rechecked before
    the record is used. This is evidence of which files were compared, not a
    quality or aesthetic score.
    """
    label: Literal["A", "B"]
    path: str = Field(min_length=1, max_length=4096)
    sha256: str = Field(pattern=r"^[0-9a-f]{64}$")


class CalibrationRejection(Strict):
    image: Literal["A", "B"]
    reason: str = Field(min_length=2, max_length=1000)


class PersonalCalibration(Strict):
    """Explicit user pairwise preference; never inferred from machine eval."""
    format: Literal["liu.personal-calibration.v1"]
    version: Literal[1]
    id: str = Field(pattern=r"^calibration\.[a-z0-9_-]{3,80}$")
    recorded_at: str = Field(min_length=10)
    domain: Literal["person", "illustration", "poster", "vehicle", "character", "prop", "hybrid"]
    criterion: str = Field(min_length=3, max_length=500)
    images: list[CalibrationImage] = Field(min_length=2, max_length=2)
    choice: Literal["A", "B", "tie", "uncertain"]
    user_quote: str = Field(min_length=2, max_length=2000)
    rejected_reasons: list[CalibrationRejection] = Field(max_length=8)
    source: Literal["explicit_user_pairwise_feedback"]
    # Explicit bindings make cross-task experiments auditable. Null/empty
    # values mean this record is intentionally unbound; they are not a
    # runtime preference model.
    task_id: str | None = Field(max_length=240)
    method_ids: list[str] = Field(max_length=8)
    grammar_ids: list[str] = Field(max_length=8)

    @model_validator(mode="after")
    def validate_pair(self):
        if [x.label for x in self.images] != ["A", "B"]:
            raise ValueError("校准图片必须按 A、B 顺序各提供一张")
        if self.images[0].sha256 == self.images[1].sha256:
            raise ValueError("A/B 不能是同一份图片；个人偏好校准必须比较两个真实候选")
        if self.choice in ("A", "B"):
            rejected = {x.image for x in self.rejected_reasons}
            loser = "B" if self.choice == "A" else "A"
            if loser not in rejected:
                raise ValueError("明确选择 A/B 时必须记录被否决图片及其原始原因")
        return self


class PersonalCalibrationLedger(Strict):
    format: Literal["liu.personal-calibration-ledger.v1"]
    version: Literal[1]
    updated_at: str = Field(min_length=10)
    records: list[PersonalCalibration] = Field()
    digest: str = Field(pattern=r"^[0-9a-f]{64}$")


class PrivateStyleRule(Strict):
    """A user-specific visual tendency with explicit evidence boundaries."""
    id: str = Field(pattern=r"^personal\.[a-z0-9_.-]{3,80}$")
    domains: list[Literal["person", "illustration", "poster", "vehicle", "character", "prop", "hybrid"]] = Field(min_length=1)
    confidence: Literal["confirmed_positive", "supported_interest", "experimental"]
    direction: str = Field(min_length=3, max_length=1000)
    visible_checks: list[str] = Field(min_length=1, max_length=8)
    avoid: list[str] = Field(max_length=8)
    evidence_ids: list[str] = Field(min_length=1, max_length=30)


class PrivateAestheticProfile(Strict):
    """Private taste profile; advisory until the user accepts a result."""
    format: Literal["liu.private-aesthetic-profile.v1"]
    version: Literal[1]
    owner: str = Field(min_length=1, max_length=120)
    status: Literal["experimental_private_baseline"]
    confirmed_positive_card_ids: list[str]
    explicit_negative_count: int = Field(ge=0)
    external_observation_count: int = Field(ge=0)
    rules: list[PrivateStyleRule] = Field(min_length=1, max_length=12)
    review_policy: Literal["advisory_user_final"]
    source_record: str = Field(min_length=1, max_length=300)

class Observation(Strict):
    target_id: str
    verdict: Literal["met", "not_met", "uncertain"]
    evidence: str
    correction: str

class Review(Strict):
    decision: Literal["ready_for_user", "revise", "uncertain"]
    observations: list[Observation]
    preserve: list[str]
    revision: str

class Source(Strict):
    label: str
    url: str
    kind: Literal["user_material", "official", "editorial", "external"]
    checked_at: str

class Card(Strict):
    id: str = Field(pattern=r"^[a-z][a-z0-9_.-]{2,100}$")
    version: int = Field(ge=1)
    kind: Literal["task", "style", "composition", "lighting", "color", "material", "constraint", "failure"]
    domains: list[Domain] = Field(min_length=1)
    title: str
    tags: list[str] = Field(min_length=1)
    when: str
    avoid_when: str
    rules: list[str] = Field(min_length=1)
    failure_signals: list[str]
    scopes: list[Scope] = Field(min_length=1)
    requires_reference_role: str
    conflicts: list[str]
    status: Literal["seed", "candidate", "validated", "deprecated"]
    sources: list[Source] = Field(min_length=1)
    evidence: list[str]

    @model_validator(mode="after")
    def evidence_for_validation(self):
        if self.status == "validated" and not self.evidence:
            raise ValueError("validated 卡片必须带真实测试/人工记录的 evidence 路径")
        return self

class ModelProfile(Strict):
    id: str = Field(pattern=r"^[a-z][a-z0-9_-]{1,60}$")
    version: int = Field(ge=1)
    label: str
    language: Literal["zh", "en"]
    status: Literal["experimental", "validated", "deprecated"]
    provider_version: str
    last_checked: str
    review_after_days: int = Field(ge=1, le=365)
    writing_rules: list[str] = Field(min_length=1)
    setup_rules: list[str]
    limitations: list[str]
    sources: list[Source] = Field(min_length=1)
    evaluation_files: list[str]

    @field_validator("last_checked")
    @classmethod
    def checked_date(cls, value):
        from datetime import date
        date.fromisoformat(value)
        return value

    @model_validator(mode="after")
    def validation_requires_evidence(self):
        if self.status == "validated" and not self.evaluation_files:
            raise ValueError("没有目标模型实测记录不能标为 validated")
        return self

OUTPUT_MODELS = {c.__name__: c for c in (Need, VisualSpec, Compilation, Solution, Review, Card, ModelProfile, DesignPlan, Plan, ReferenceAnalysis, ImageEval, PairComparison, AestheticBinding, TemplateBinding, CalibrationImage, CalibrationRejection, PersonalCalibration, PersonalCalibrationLedger, PrivateStyleRule, PrivateAestheticProfile)}

class MaterialFinding(Strict):
    reference_id: str
    observed_composition: str
    observed_light: str
    observed_color: str
    observed_material: str
    useful_for: list[str]
    do_not_transfer: list[str]
    uncertainty: str

class MaterialAnalysis(Strict):
    findings: list[MaterialFinding]

OUTPUT_MODELS["MaterialAnalysis"] = MaterialAnalysis

class LibraryObservation(Strict):
    title: str = Field(min_length=2)
    tags: list[str] = Field(min_length=1, max_length=20)
    visible_facts: str = Field(min_length=10)
    transfer: list[str] = Field(min_length=1)
    do_not_transfer: list[str] = Field(min_length=1)
    uncertainty: str = Field(min_length=2)

OUTPUT_MODELS["LibraryObservation"] = LibraryObservation
OUTPUT_MODELS["HostVisibleReference"] = HostVisibleReference


class AestheticCard(Strict):
    """A seen local image translated into one transferable visual decision.

    This is deliberately separate from the seed knowledge cards: it carries
    evidence about a real user image, not a general rule or a filename guess.
    """
    # Legacy curated cards remain readable; model-generated cards must name
    # transfer_domains explicitly, including [] when no transfer is allowed.
    model_config = ConfigDict(json_schema_extra=lambda schema: schema.update(required=list(schema['properties'])))
    id: str = Field(pattern=r"^aesthetic\.[a-z0-9_-]{3,80}$")
    version: int = Field(ge=1)
    source_path: str = Field(min_length=1, max_length=2048)
    source_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    domain: Literal["person", "illustration", "poster", "vehicle", "hybrid"]
    transfer_domains: list[Literal["person", "illustration", "poster", "vehicle", "character", "prop", "hybrid"]] = Field(default_factory=list, max_length=7)
    roles: list[Literal["expression", "composition", "light", "material", "relationship", "layout", "motion"]] = Field(min_length=1)
    title: str = Field(min_length=2, max_length=160)
    visible_facts: str = Field(min_length=20)
    dominant_decision: str = Field(min_length=8, max_length=500)
    supporting_mechanisms: list[str] = Field(min_length=1, max_length=8)
    transfer_conditions: list[str] = Field(min_length=1, max_length=8)
    do_not_transfer: list[str] = Field(min_length=1, max_length=8)
    anti_default: list[str] = Field(min_length=1, max_length=8)
    visible_test: str = Field(min_length=12, max_length=500)
    failure_signals: list[str] = Field(min_length=1, max_length=8)
    tags: list[str] = Field(min_length=1, max_length=20)
    evidence: list[str] = Field(min_length=1, max_length=8)
    seen_via: Literal["view_image", "host_reported"]
    status: Literal["curated", "candidate", "deprecated"]

    @model_validator(mode="after")
    def curated_cards_need_direct_observation(self):
        if self.status == "curated" and self.seen_via != "view_image":
            raise ValueError("curated 审美卡片必须有实际看图证据")
        return self


class AestheticLibrary(Strict):
    format: Literal["liu.aesthetic-library.v1"]
    version: int = Field(ge=1)
    generated_at: str = Field(min_length=10)
    source_catalog: str = Field(min_length=1)
    selection_policy: list[str] = Field(min_length=3)
    cards: list[AestheticCard] = Field(min_length=1)


class AestheticPlan(Strict):
    format: Literal["liu.aesthetic-plan.v1"]
    version: int = Field(ge=1)
    domain: Literal["person", "illustration", "poster", "vehicle", "hybrid"]
    route: str = Field(min_length=3)
    dominant_decision: str = Field(min_length=8)
    supporting_mechanism: str = Field(min_length=8)
    withheld_information: str = Field(min_length=8)
    anti_default: list[str] = Field(min_length=1, max_length=8)
    visible_test: str = Field(min_length=12)
    failure_test: str = Field(min_length=12)
    card_ids: list[str] = Field(min_length=1, max_length=8)
    revision_rule: str = Field(min_length=12)
    status: Literal["working", "validated", "needs_review"]


OUTPUT_MODELS["AestheticCard"] = AestheticCard
OUTPUT_MODELS["AestheticLibrary"] = AestheticLibrary
OUTPUT_MODELS["AestheticPlan"] = AestheticPlan
OUTPUT_MODELS.update({c.__name__: c for c in (ReferencePolicy, GenerationOptions, GenerationSpec, DirectionDraft, DirectionSelection, DirectionTranslation)})
OUTPUT_MODELS['PromptAdaptation'] = PromptAdaptation
