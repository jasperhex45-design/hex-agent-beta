"""Typed, bounded artistic decisions; no aesthetic score or image-reading claim."""
from __future__ import annotations
from typing import Literal
from pydantic import BaseModel, ConfigDict, Field
from ..contracts import Plan

SpaceMode = Literal['camera', 'layered', 'planar', 'multiple_viewpoints', 'sequential']
Operation = Literal['color', 'exposure', 'lighting', 'weather', 'geometry', 'layout', 'content', 'texture']

class Strict(BaseModel):
    model_config = ConfigDict(extra='forbid')

class ArtCase(Strict):
    id: str = Field(pattern=r'^art\.[a-z0-9_.-]+$')
    method_id: str = Field(pattern=r'^space\.[a-z0-9_]+$')
    title: str
    artist: str
    tradition: str
    source_url: str
    source_checked_on: str
    source_reported_structure: str
    interpretation: str
    transfer_hypothesis: str
    keywords: list[str] = Field(min_length=1, max_length=20)
    modes: list[SpaceMode] = Field(min_length=1)
    operations: list[Operation] = Field(min_length=1)
    do_not_transfer: list[str] = Field(min_length=1)
    avoid_when: list[str] = Field(min_length=1, max_length=4)
    controlled_test: str
    epistemic_status: Literal['source_informed_design_hypothesis']
    image_status: Literal['not_bundled_not_inspected_in_this_release']

class Entity(Strict):
    id: str = Field(pattern=r'^[a-z][a-z0-9_-]{0,31}$')
    role: str = Field(min_length=2, max_length=120)
    placement: str = Field(min_length=2, max_length=160)

class Relation(Strict):
    source: str
    target: str
    relation: str = Field(min_length=2, max_length=160)

class Void(Strict):
    region: str = Field(min_length=2, max_length=160)
    purpose: str = Field(min_length=2, max_length=160)

class SpatialDecision(Strict):
    case_id: str
    choice_id: str
    check_id: str
    operation: Operation
    adoption_reason: str = Field(min_length=8, max_length=400)
    action_zh: str = Field(min_length=8, max_length=240)
    action_en: str = Field(min_length=8, max_length=400)
    expected: str = Field(min_length=8, max_length=300)
    failure: str = Field(min_length=8, max_length=300)
    counterfactual_test: str = Field(min_length=8, max_length=300)
    do_not_transfer: list[str] = Field(min_length=1, max_length=4)
    # Copy the case applicability boundary into the task decision so a model
    # cannot silently ignore a stated "avoid when" condition.
    avoid_when_considered: list[str] = Field(min_length=1, max_length=4)
    applicability_evidence: str = Field(min_length=8, max_length=500)

class SpatialPlan(Strict):
    mode: SpaceMode
    experience_goal: str = Field(min_length=4, max_length=240)
    entities: list[Entity] = Field(min_length=1, max_length=8)
    relations: list[Relation] = Field(max_length=12)
    voids: list[Void] = Field(max_length=3)
    reading_path: list[str] = Field(min_length=1, max_length=6)
    decisions: list[SpatialDecision] = Field(min_length=1, max_length=2)
    protected_facts: list[str] = Field(max_length=40)
    uncertainty: str = Field(min_length=4, max_length=400)

class ArtPlan(Strict):
    """The existing Plan remains intact. Nullable means intentional non-adoption."""
    plan: Plan
    spatial_plan: SpatialPlan | None
    not_used_reason: str
