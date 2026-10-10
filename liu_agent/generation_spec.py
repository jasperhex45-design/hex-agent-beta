"""Small generation contract and dated adapter capability snapshots.

Capabilities describe the documented API or exposed host tool, not account
entitlements. No capability lookup makes a network request or spends budget.
"""
from __future__ import annotations

import io
import math
import re
from fractions import Fraction
from typing import Literal

from PIL import Image, UnidentifiedImageError
from pydantic import BaseModel, ConfigDict, Field, field_validator

from .io import AgentError, MAX_IMAGE_BYTES, MAX_IMAGE_PIXELS, digest, sha


CAPABILITY_CHECKED_AT = "2026-09-26"
OPENAI_SOURCES = [
    "https://developers.openai.com/api/reference/resources/images/methods/generate",
    "https://developers.openai.com/api/reference/resources/images/methods/edit",
    "https://developers.openai.com/api/docs/guides/image-prompting",
]
_FIXED_MODELS = {"gpt-image-1", "gpt-image-1-mini", "gpt-image-1.5"}
_FLEXIBLE_MODELS = {"gpt-image-2", "gpt-image-2-2026-04-21"}
_EXTENDED_MODELS = {
    "gpt-image-2.5-flare", "gpt-image-2.5-flare-2026-09-08",
    "gpt-image-2.5-sunburst", "gpt-image-2.5-sunburst-2026-09-08",
}


class GenerationSpec(BaseModel):
    model_config = ConfigDict(extra="forbid", validate_assignment=True, revalidate_instances="always")

    task_mode: str = Field(min_length=1, max_length=40)
    provider_id: str = Field(min_length=1, max_length=80)
    model_id: str | None = Field(max_length=160)
    desired_aspect_ratio: str = Field(min_length=1, max_length=40)
    requested_size: str = Field(min_length=1, max_length=40)
    quality: str = Field(min_length=1, max_length=30)
    output_format: Literal["png", "jpeg", "webp"]
    generation_reference_ids: list[str] = Field(max_length=16)
    candidate_role: Literal["primary", "alternative", "revision"]
    prompt_digest: str = Field(pattern=r"^[a-f0-9]{64}$")
    design_digest: str = Field(pattern=r"^[a-f0-9]{64}$")
    capability_snapshot_id: str = Field(min_length=1, max_length=100)

    @field_validator("generation_reference_ids")
    @classmethod
    def unique_reference_ids(cls, value):
        if any(not item or len(item) > 80 for item in value) or len(value) != len(set(value)):
            raise ValueError("生成参考 ID 必须非空且不重复")
        return value


def _snapshot(body: dict) -> dict:
    return {**body, "snapshot_id": "liu-capability-" + digest(body)}


def openai_capabilities(model_id: str) -> dict:
    """Return an allowlisted, dated documentation snapshot for the exact ID."""
    known = model_id in _FIXED_MODELS | _FLEXIBLE_MODELS | _EXTENDED_MODELS
    flexible = model_id in _FLEXIBLE_MODELS | _EXTENDED_MODELS
    parameters = {
        "size": {"support": "supported" if known else "unknown", "control": "controlled",
                 "values": ["auto", "1024x1024", "1024x1536", "1536x1024"] if known else [],
                 "custom_size": flexible},
        "quality": {"support": "supported" if known else "unknown", "control": "controlled",
                    "values": (["auto", "low", "medium", "high"] +
                               (["xhigh", "max"] if model_id in _EXTENDED_MODELS else [])) if known else []},
        "output_format": {"support": "supported" if known else "unknown", "control": "controlled",
                          "values": ["png", "jpeg", "webp"] if known else []},
    }
    if flexible:
        parameters["size"]["constraints"] = {
            "edge_multiple": 16, "max_edge": 3840, "max_aspect_ratio": 3,
            "min_pixels": 655360, "max_pixels": 8294400,
            "experimental_above_pixels": 3686400,
        }
    return _snapshot({
        "provider_id": "openai", "model_id": model_id, "model_known": known,
        "checked_at": CAPABILITY_CHECKED_AT, "source_type": "official_documentation",
        "sources": OPENAI_SOURCES.copy(), "account_access": "not_verified",
        "operations": ["generation", "edit"] if known else [], "parameters": parameters,
    })


def validate_tool_target(tool_name: str, target: str) -> None:
    """Bind registered host tools to their model profiles, without guessing custom tools."""
    from .comfy_connector import TOOL_TARGETS
    known_targets = {
        'image_gen.imagegen': 'image',
        'image_gen__imagegen': 'image',
        'pippit.generate-image.seedream-5-pro': 'seedream',
        **TOOL_TARGETS,
    }
    expected = known_targets.get(tool_name)
    if expected is not None and target != expected:
        raise AgentError('已接入工具与目标模型不一致；不能静默替换模型：'
                         + tool_name + ' 需要 target=' + expected)


def host_capabilities(tool_name: str = "image_gen.imagegen") -> dict:
    """Describe only arguments exposed by this version of the native tool."""
    from .comfy_connector import TOOL_TARGETS, capabilities
    if not isinstance(tool_name, str) or not tool_name.strip():
        raise AgentError('工具名称必须是非空字符串')
    if tool_name in TOOL_TARGETS:
        return capabilities(tool_name)
    if tool_name.strip().lower().startswith('comfyui.'):
        raise AgentError('未接入的 ComfyUI 工具；不能作为通用外部工具派发或占用预算')
    if tool_name == "pippit.generate-image.seedream-5-pro":
        from .pippit_connector import MAX_REFERENCE_IMAGES, RATIOS, RATIO_LABELS, RESOLUTIONS
        return _snapshot({
            "provider_id": "pippit_cli", "model_id": None, "model_known": False,
            "service_model_name": "Seedream 5.0 Pro", "checked_at": "2026-09-27",
            "source_type": "cli_help_and_live_model_discovery",
            "sources": ["pippit-tool-cli 1.0.35 generate-image --help", "model describe Seedream 5.0 Pro --type image",
                        "evidence/hex-car-v1-20260927/seedream-model-live.json"],
            "account_access": "verify_before_execution", "operations": ["generation", "edit"],
            "reference_images": {"support": "supported", "ordered": True,
                                 "max_images": MAX_REFERENCE_IMAGES,
                                 "roles": "specified_in_prompt_not_dedicated_cli_fields",
                                 "visual_adherence": "not_verified"},
            "cli_options": {"resolution": {"required": True, "values": list(RESOLUTIONS)},
                            "ratio": {"required": False, "values": list(RATIOS),
                                      "option_labels": {str(key): value for key, value in RATIO_LABELS.items()}},
                            "effort": {"support": "not_exposed"}},
            "parameters": {name: {"support": "not_exposed", "control": "not_controlled", "values": []}
                           for name in ("size", "quality", "output_format")},
        })
    known = tool_name in ("image_gen.imagegen", "image_gen__imagegen")
    return _snapshot({
        "provider_id": "codex_imagegen" if known else "external_host", "model_id": None, "model_known": False,
        "checked_at": CAPABILITY_CHECKED_AT,
        "source_type": "exposed_tool_schema" if known else "host_not_introspected",
        "sources": (["image_gen.imagegen: prompt, referenced_image_paths, num_last_images_to_include, transparent_background"]
                    if known else [f"host_supplied_tool_name:{tool_name}"]),
        "account_access": "not_verified", "operations": ["generation", "edit"] if known else ["host_supplied"],
        "parameters": {name: {"support": "not_exposed" if known else "unknown", "control": "not_controlled", "values": []}
                       for name in ("size", "quality", "output_format")},
    })


def _ratio(value: str) -> Fraction | None:
    if value == "auto":
        return None
    if not re.fullmatch(r"[1-9][0-9]{0,5}:[1-9][0-9]{0,5}", value):
        raise AgentError("画幅比例须为 auto 或正整数比，例如 3:2")
    width, height = map(int, value.split(":"))
    return Fraction(width, height)


def _dimensions(value: str) -> tuple[int, int] | None:
    if value == "auto":
        return None
    if not re.fullmatch(r"[1-9][0-9]{0,4}x[1-9][0-9]{0,4}", value):
        raise AgentError("生成尺寸须为 auto 或 WIDTHxHEIGHT，例如 1536x1024")
    return tuple(map(int, value.split("x")))


def _validate_size(value: str, capability: dict) -> None:
    dimensions = _dimensions(value)
    if value in capability.get("values", []):
        return
    if not dimensions or not capability.get("custom_size"):
        raise AgentError(f"当前模型不支持生成尺寸 {value}；请选择支持的尺寸，不会自动改为方图")
    width, height = dimensions
    limits = capability["constraints"]
    if (width % limits["edge_multiple"] or height % limits["edge_multiple"] or
            max(width, height) > limits["max_edge"] or
            max(width, height) / min(width, height) > limits["max_aspect_ratio"] or
            not limits["min_pixels"] <= width * height <= limits["max_pixels"]):
        raise AgentError(f"生成尺寸 {value} 超出当前模型的边长、比例或像素能力；未提交请求")


def _resolve_size(aspect_ratio: str, capabilities: dict) -> str:
    ratio = _ratio(aspect_ratio)
    if ratio is None:
        return "auto"
    capability = capabilities["parameters"]["size"]
    if capability.get("control") == "not_controlled":
        # The requested ratio is kept in the contract. The host does not expose
        # a pixel-size argument, so do not manufacture a controllable size.
        return "auto"
    for size in capability.get("values", []):
        dimensions = _dimensions(size)
        if dimensions and Fraction(*dimensions) == ratio:
            return size
    if capability.get("custom_size"):
        # Find an exact ratio at about one megapixel, in documented 16px steps.
        # An unrepresentable ratio fails capability validation instead of being
        # rounded or replaced by a square.
        scale = max(1, math.ceil(math.sqrt(1048576 / (ratio.numerator * ratio.denominator)) / 16)) * 16
        size = f"{ratio.numerator * scale}x{ratio.denominator * scale}"
        _validate_size(size, capability)
        return size
    raise AgentError(f"当前模型不能精确映射画幅 {aspect_ratio}；请明确选择支持的比例或尺寸")


def build_generation_spec(*, provider_id: str, model_id: str | None, prompt: str,
                          design_digest: str, task_mode: str = "create",
                          desired_aspect_ratio: str = "auto", requested_size: str | None = None,
                          quality: str = "auto", output_format: str = "png",
                          generation_reference_ids: list[str] | None = None,
                          candidate_role: str = "primary", capabilities: dict | None = None) -> GenerationSpec:
    if capabilities is None:
        if provider_id == "openai":
            capabilities = openai_capabilities(model_id or "")
        elif provider_id == "codex_imagegen":
            capabilities = host_capabilities()
        else:
            raise AgentError("此提供方缺少已核实的能力快照")
    spec = GenerationSpec(
        task_mode=task_mode, provider_id=provider_id, model_id=model_id,
        desired_aspect_ratio=desired_aspect_ratio,
        requested_size=requested_size if requested_size is not None else _resolve_size(desired_aspect_ratio, capabilities),
        quality=quality, output_format=output_format,
        generation_reference_ids=list(generation_reference_ids or []), candidate_role=candidate_role,
        prompt_digest=sha(prompt.encode("utf-8")), design_digest=design_digest,
        capability_snapshot_id=capabilities["snapshot_id"],
    )
    validate_generation_spec(spec, capabilities=capabilities, prompt=prompt)
    return spec


def validate_output_options(*, quality: str, output_format: str, capabilities: dict) -> None:
    """Check exposed categorical options without needing a prompt or a size."""
    for name, value in (("quality", quality), ("output_format", output_format)):
        capability = capabilities["parameters"][name]
        if capability.get("control") == "not_controlled":
            continue
        if capability.get("support") != "supported":
            raise AgentError(f"当前提供方未核实 {name} 参数能力")
        if value not in capability.get("values", []):
            raise AgentError(f"当前模型不支持 {name}={value}；未提交请求")


def validate_generation_spec(spec: GenerationSpec, *, capabilities: dict,
                             prompt: str | None = None, design_digest: str | None = None,
                             generation_reference_ids: list[str] | None = None) -> None:
    """Validate execution capability and optional current-input bindings."""
    if _snapshot({key: value for key, value in capabilities.items() if key != "snapshot_id"})["snapshot_id"] != capabilities.get("snapshot_id"):
        raise AgentError("能力快照内容与摘要不一致")
    if (spec.capability_snapshot_id != capabilities.get("snapshot_id") or
            spec.provider_id != capabilities.get("provider_id") or spec.model_id != capabilities.get("model_id")):
        raise AgentError("生成规格与当前提供方、模型或能力快照不一致")
    if prompt is not None and sha(prompt.encode("utf-8")) != spec.prompt_digest:
        raise AgentError("生成规格的提示词摘要已失效；未提交请求")
    if design_digest is not None and design_digest != spec.design_digest:
        raise AgentError("生成规格的设计摘要已失效；未提交请求")
    if generation_reference_ids is not None and generation_reference_ids != spec.generation_reference_ids:
        raise AgentError("生成规格的参考 ID 或顺序与实际输入不一致")
    ratio, dimensions = _ratio(spec.desired_aspect_ratio), _dimensions(spec.requested_size)
    if ratio is not None and dimensions is not None and Fraction(*dimensions) != ratio:
        raise AgentError("指定尺寸与目标画幅比例不一致；不会静默变更比例")
    if spec.provider_id == "openai" and not capabilities.get("model_known"):
        raise AgentError("配置的真实模型 ID 尚无已核实能力快照；未猜测支持范围或账户权限")
    capability = capabilities["parameters"]["size"]
    if capability.get("control") != "not_controlled":
        if capability.get("support") != "supported":
            raise AgentError("当前提供方未核实 size 参数能力")
        if ratio is not None and dimensions is None:
            raise AgentError("明确的画幅比例需要可执行尺寸，不能以 auto 假称已控制比例")
        _validate_size(spec.requested_size, capability)
    validate_output_options(quality=spec.quality, output_format=spec.output_format, capabilities=capabilities)


def image_facts(data: bytes) -> dict:
    """Inspect original bytes without normalizing away their returned format."""
    if len(data) > MAX_IMAGE_BYTES:
        raise AgentError("返回图片超过 16 MiB 限制")
    try:
        with Image.open(io.BytesIO(data)) as image:
            if image.format not in ("PNG", "JPEG", "WEBP") or image.width * image.height > MAX_IMAGE_PIXELS:
                raise AgentError("返回图片格式或像素数超出支持范围")
            if getattr(image, "n_frames", 1) != 1:
                raise AgentError("返回图片必须是单帧图片")
            image.load()
            return {"width": image.width, "height": image.height,
                    "size": f"{image.width}x{image.height}", "output_format": image.format.lower(),
                    "sha256": sha(data), "byte_length": len(data)}
    except (UnidentifiedImageError, OSError, Image.DecompressionBombError) as exc:
        raise AgentError("返回图片无法安全解码") from exc


def verify_returned_parameters(spec: GenerationSpec | None, facts: dict, reported: dict,
                               *, display_facts: dict | None = None) -> dict:
    """Verify measurable bytes; quality is only a provider report, never taste."""
    result = {}
    requested_ratio = spec.desired_aspect_ratio if spec else None
    desired = _ratio(requested_ratio) if requested_ratio is not None else None
    ratio_facts = facts if display_facts is None else display_facts
    actual_ratio = Fraction(ratio_facts['width'], ratio_facts['height'])
    result['aspect_ratio'] = {
        'status': 'not_requested' if desired is None else ('matched' if desired == actual_ratio else 'mismatch'),
        'requested': requested_ratio,
        'actual': f'{actual_ratio.numerator}:{actual_ratio.denominator}',
        'source': 'decoded_image_bytes' if display_facts is None else 'saved_image_bytes',
    }
    for name, requested in (("size", spec.requested_size if spec else None),
                            ("output_format", spec.output_format if spec else None)):
        actual = facts[name]
        state = "not_requested" if requested in (None, "auto") else ("matched" if requested == actual else "mismatch")
        result[name] = {"status": state, "requested": requested, "actual": actual,
                        "source": "decoded_image_bytes"}
        if reported.get(name) is not None:
            result[name]["reported"] = reported[name]
            result[name]["report_matches_bytes"] = reported[name] == actual
    quality = reported.get("quality")
    requested_quality = spec.quality if spec else None
    result["quality"] = {
        "status": ("not_reported" if quality is None else
                   "provider_selected" if requested_quality in (None, "auto") else
                   "reported_match" if quality == requested_quality else "reported_mismatch"),
        "requested": requested_quality, "reported": quality,
        "source": "provider_metadata_only", "aesthetic_quality_verified": False,
    }
    return result
