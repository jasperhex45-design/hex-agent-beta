"""Build the shared intake contract for conversation and optional forms.

The intake is deliberately small. It describes what must be understood before
design; it does not ask users to fill camera or lighting jargon. A host may
render this contract as chips/cards, or let the agent gather the same fields
through normal conversation.
"""
from __future__ import annotations

from .contracts import Brief, Need


def build_intake(brief: Brief, need: Need, need_mode: str = "offline_heuristic") -> dict:
    """Return a deterministic, host-neutral intake plan.

    ``questions`` comes only from consequential ambiguity already recorded by
    the requirement parser. The UI and conversational host must
    not invent a second questionnaire or ask for technical photography fields.
    """
    route = need.domain if brief.domain == "auto" else brief.domain
    unresolved = list(need.questions)
    understood = need_mode in {"host_supplied", "cloud_model"}
    return {
        "format": "liu.smart-intake.v1",
        "mode": "hybrid",
        "conversation_default": True,
        "optional_form": True,
        "form_status": "contract_only_not_implemented",
        "route": route,
        "status": ("needs_interpretation" if not understood else
                   "needs_input" if unresolved else "ready_for_reference_review"),
        "understanding_origin": need_mode,
        "question_policy": {
            "max_questions_per_turn": 1,
            "ask_only_if_result_changes": True,
            "blocking_topics": ["goal", "hard_constraint_conflict", "reference_role", "missing_required_asset"],
            "agent_decides_camera_light_material": True,
        },
        "fields": [
            {"id": "deliverable", "label": "这次要交付什么", "type": "choice", "required": True,
             "choices": ["single_image", "poster_or_kv", "cover_or_editorial"]},
            {"id": "use_context", "label": "用在哪里、给谁看", "type": "free_text", "required": False},
            {"id": "subject_and_relation", "label": "主体以及它和环境/他者的关系", "type": "agent_proposal", "required": False},
            {"id": "primary_expression", "label": "观众第一眼应该读到什么", "type": "agent_proposal", "required": False},
            {"id": "must_keep", "label": "必须保留", "type": "chips", "required": False},
            {"id": "avoid", "label": "明确不要", "type": "chips", "required": False},
            {"id": "references", "label": "参考图/素材及用途", "type": "reference_upload", "required": False},
        ],
        "artistic_contract": {
            "candidate_visual_genes": [
                "composition_and_scale",
                "subject_environment_relation",
                "light_color_and_material",
                "type_or_graphic_structure_when_relevant",
            ],
            "translation_rule": "每个采用的视觉基因都必须落成一个可见动作，并绑定一个生成前检查项。",
            "selection_rule": "按目标选择相关机制；不强制每张图都用完，不把三色、极简、裁切大字作为统一美感标准。",
            "priority": "本次明确约束 > 本次采用方向 > 私人偏好建议 > 通用模板。身份/文字等还原约束与创作自由分开。",
            "quality_boundary": "均衡是鲜明表达中的关系成立，不是各处平均完美。按视觉意图与实际用途判断粗粝、省略或偏斜的作用；有意取舍须在生成前checks.expected/failure写清范围。风格词、整齐程度和机器自评不能证明艺术质量。",
            "spatial_grammar": {
                "status": "experimental",
                "purpose": "把中西绘画中可迁移的空间与观看操作转成任务内假设，不把流派或名画当作风格标签。",
                "fields": ["spatial_model", "viewer_path", "negative_space_function", "dominant_vectors", "layer_plan", "scale_relation", "time_model", "surface_scope"],
                "selection_rule": "每个任务最多采用一个主导空间模型；若用户说不清，先用结构成对比较，不追问艺术史术语。",
                "boundary": "没有实际看过的作品、用户反馈或生成结果时，只能记录实验假设，不能声称掌握中国画/西方绘画史。"
            },
            "authorship_protocol": {
                "status": "experimental_method_not_personal_style",
                "applies_when": "开放创作或用户要求建立系列美学时采用；身份还原、指定复刻和局部修改先服从锁定范围。",
                "reference_distance": "分开参考的可迁移关系与识别性符号组合；不把换主体、换颜色或换媒介当成新的表达。",
                "coherence": "expression说明要充分展开的表达立场；choices的action/cooperation/tradeoff说明强烈主张如何由不等量的形、空白、密度或节奏承托。允许有作用的对抗和不完美，不把统一理解为平均、单一媒介或处处完整。",
                "variation": "系列保持观看关系与选择理由；题材、色板、符号和媒介可以变化。不强迫用户全部收藏收敛成一种固定画风。",
                "abstraction_control": {
                    "status": "experimental_task_method",
                    "applies_when": "用户要求意象化、绘画性、虚实关系，或参考图的主要表达依赖色块、光影和局部线性，而非逐物写实；精确复现、身份锁定、文字排版和局部修改不自动启用。",
                    "selection_rule": "从color_mass、line_direction、light_shape、material_detail找承担表达的关系；按用途分配强弱。多个强元素可有意对抗，要说明相互作用；不让线条与色彩平均解释所有对象。",
                    "visible_mapping": {
                        "primary_axis": "design.expression + 一个choices.action",
                        "secondary_axis": "choices.cooperation/tradeoff",
                        "edge_hierarchy": "checks中与当前用途相符的精确、可辨或省略范围，不强制三者齐全",
                        "detail_budget": "checks中的信息密度差异、视觉作用与必要功能边界"
                    },
                    "review_questions": [
                        "缩小后第一眼读到的是主导色块/光路/方向，还是每个物体的细节？",
                        "线条是否只承担必要结构，次要边缘是否允许融入色块和空气？",
                        "虚部是否承担空间、情绪或观看出口，而不是把整张图做成均匀模糊？"
                    ],
                    "boundary": "这是任务内的抽象化编排方法，不是固定画风或高级度评分；模型不能仅用意象、诗意、电影感等词代替可见取舍。"
                },
                "design_mapping": {
                    "stance": "design.expression + intent_claims(inferred=true for proposals)",
                    "transformation": "reference_analysis.transfer/do_not_transfer + design.choices",
                    "visible_review": "design.checks(expression/overall) -> acceptance -> ImageEval.items",
                },
                "review_questions": [
                    "去掉参考特有的题材或符号组合，当前画面的关系还值得看吗？",
                    "各项选择是否共同支撑主要表达，还是只有局部风格相似？",
                    "跨题材保留的是选择逻辑，还是重复配色、构图和道具？",
                ],
                "boundary": "宿主按任务把相关问题写成具体checks并实际看图；此协议不自动检测模仿、不证明原创、不推断用户已认可。",
            },
        },
        "known": {
            "request": brief.request,
            "domain": route,
            "scope": brief.scope,
            "explicit_must_keep": list(brief.must_keep),
            "explicit_avoid": list(brief.avoid),
            "reference_count": len(brief.references),
        },
        "interpretation": {
            "intent": need.intent,
            "subject": need.subject,
            "desired_feeling": list(need.desired_feeling),
            "must_keep": list(need.must_keep),
            "avoid": list(need.avoid),
            "assumptions": list(need.assumptions),
            "notice": "这是需求解析结果，不代表用户逐条确认；原话与推断由DesignPlan.intent_claims继续核对。",
        },
        "adaptive_questions": unresolved,
        "next_question": unresolved[0] if understood and unresolved else None,
        "ready_when": [
            "intent_claims引用当前用户原话或明确标记为推断",
            "carrier与主体-环境关系已确定",
            "参考图的身份用途、正向机制和不可迁移项已分开",
            "每个核心视觉决定都有可见检查项",
            "若仍有adaptive_questions，先解决再进入设计",
        ],
        "notice": "由宿主或模型进行语义理解；本模块只整理状态。页面尚未实现；需求就绪不等于参考就绪、可生成或审美通过。",
    }
