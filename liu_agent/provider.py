"""Opt-in official OpenAI HTTP adapter. Model IDs are supplied by the user.

No SDK retry defaults, no silent cloud fallback, no inference of subscription
entitlements. All attempted requests consume a local call budget.
"""
from __future__ import annotations
import base64
import json
import os
from dataclasses import dataclass, field
from typing import TypeVar
import httpx
from pydantic import BaseModel
from .io import AgentError, normalize_image, now, sha
from .instructions import RESEARCH
from .generation_spec import (GenerationSpec, image_facts, openai_capabilities,
                              validate_generation_spec, verify_returned_parameters)

T = TypeVar("T", bound=BaseModel)

@dataclass
class Budget:
    max_calls: int = 14
    max_images: int = 2
    calls: int = 0
    images: int = 0
    events: list[dict] = field(default_factory=list)
    _prepaid: dict | None = field(default=None, repr=False)
    _reservation_active: bool = field(default=False, repr=False)

    def take(self, kind: str) -> dict:
        # The durable workflow reserves before entering a provider adapter.
        # Its one underlying HTTP request consumes that same reservation.
        if self._prepaid is not None:
            event, self._prepaid = self._prepaid, None
            if event['kind'] != kind:
                raise AgentError('请求类型与持久化预留不一致，已阻止调用')
            return event
        if self._reservation_active:
            raise AgentError('同一预留只允许一个底层请求；额外调用必须单独持久化')
        if self.calls >= self.max_calls:
            raise AgentError("请求次数上限已到；不继续调用")
        if kind == "image" and self.images >= self.max_images:
            raise AgentError("图片次数上限已到；不继续生成")
        self.calls += 1
        self.images += int(kind == "image")
        event = {"at": now(), "kind": kind, "state": "attempted"}
        self.events.append(event)
        return event


def response_text(data: dict) -> str:
    if data.get("status") != "completed":
        raise AgentError("模型返回未完成状态；没有自动重试。请检查输出 token 上限或服务状态")
    parts = []
    for output in data.get("output", []):
        for content in output.get("content", []):
            if content.get("type") == "refusal":
                raise AgentError("模型拒绝了此请求；未自动改写规避")
            if content.get("type") == "output_text":
                parts.append(content.get("text", ""))
    if not parts:
        raise AgentError("模型未返回文本，不以空内容冒充成功")
    return "".join(parts)


class OpenAIProvider:
    # High-capability reasoning is reserved for decisions that can change the
    # visual direction. Routine serialization stays on the execution model.
    HIGH_REASONING_PHASES = frozenset({
        "parse", "observe", "direction", "design", "research", "evaluate", "compare", "revise",
    })

    def __init__(self, *, consent: bool, budget: Budget | None = None,
                 transport: httpx.BaseTransport | None = None):
        if not consent:
            raise AgentError("云端调用需要 --allow-cloud；本地启动不会自动上传素材")
        self.key = os.getenv("OPENAI_API_KEY", "").strip()
        self.text_model = os.getenv("LIU_TEXT_MODEL", "").strip()
        self.design_model = os.getenv("LIU_DESIGN_MODEL", "").strip() or self.text_model
        self.execution_model = os.getenv("LIU_EXECUTION_MODEL", "").strip() or self.text_model
        self.image_model = os.getenv("LIU_IMAGE_MODEL", "").strip()
        self.search_model = os.getenv("LIU_SEARCH_MODEL", "").strip() or self.text_model
        if not self.key or not self.text_model:
            raise AgentError("请在 .env 填写 OPENAI_API_KEY 和 LIU_TEXT_MODEL；用账号可用的真实模型 ID")
        try:
            self.max_output_tokens = int(os.getenv("LIU_MAX_OUTPUT_TOKENS", "10000"))
        except ValueError as exc:
            raise AgentError("LIU_MAX_OUTPUT_TOKENS 必须是整数") from exc
        if not 2000 <= self.max_output_tokens <= 20000:
            raise AgentError("LIU_MAX_OUTPUT_TOKENS 必须在2000至20000之间")
        self.budget = budget or Budget()
        self.transport = transport
        self.model_routes: list[dict] = []
        self.last_generation_evidence: dict | None = None

    def capabilities(self) -> dict:
        """Dated local adapter facts, not a remote account-permission probe."""
        return openai_capabilities(self.image_model)

    def model_for_phase(self, phase: str) -> str:
        """Return the configured text model for a governed pipeline phase."""
        if phase in self.HIGH_REASONING_PHASES:
            return self.design_model
        return self.execution_model

    def post(self, endpoint: str, kind: str, *, _generation_evidence: dict | None = None,
             _generation_arguments: dict | None = None, **kwargs) -> dict:
        event = self.budget.take(kind)
        if _generation_evidence is not None:
            # This stays on the existing attempt event, including errors and
            # unknown outcomes; it does not create another budget ledger.
            event["generation"] = _generation_evidence
        try:
            with httpx.Client(timeout=httpx.Timeout(600 if kind == "image" else 180, connect=20),
                              follow_redirects=False, trust_env=False, transport=self.transport) as client:
                if _generation_evidence is not None:
                    _generation_evidence["actually_sent"] = _generation_arguments
                response = client.post("https://api.openai.com/v1/" + endpoint,
                                       headers={"Authorization": "Bearer " + self.key}, **kwargs)
            event["http_status"] = response.status_code
            event["request_id"] = response.headers.get("x-request-id", "")
            if not 200 <= response.status_code < 300:
                # Do not print raw remote error bodies: they may echo input content.
                code = ""
                try:
                    code = str(response.json().get("error", {}).get("code", ""))[:80]
                except (ValueError, AttributeError):
                    pass
                event["state"] = "http_error"
                raise AgentError(f"OpenAI 请求失败 HTTP {response.status_code} {code}；未重试。request_id={event['request_id']}")
            data = response.json()
            event.update(state="received", response_id=data.get("id", ""), usage=data.get("usage"))
            return data
        except httpx.TimeoutException as exc:
            event["state"] = "outcome_unknown"
            raise AgentError("外部请求超时，可能已经计费；未自动重试。先核对服务记录") from exc
        except httpx.HTTPError as exc:
            event["state"] = "network_error"
            raise AgentError("无法连接 OpenAI 官方 API；检查网络。未自动重试") from exc

    def structured(self, instruction: str, payload: dict, output_type: type[T], images: list[bytes] | None = None,
                   phase: str = "execution") -> T:
        model = self.model_for_phase(phase)
        self.model_routes.append({"phase": phase, "model": model, "output_type": output_type.__name__, "at": now()})
        content = [{"type": "input_text", "text": json.dumps(payload, ensure_ascii=False)}]
        for data in images or []:
            content.append({"type": "input_image", "image_url": "data:image/png;base64," + base64.b64encode(data).decode("ascii")})
        body = {"model": model, "store": False, "instructions": instruction,
                "input": [{"role": "user", "content": content}],
                "text": {"format": {"type": "json_schema", "name": output_type.__name__, "strict": True,
                                    "schema": output_type.model_json_schema()}},
                "max_output_tokens": self.max_output_tokens}
        result = self.post("responses", "text", json=body)
        return output_type.model_validate_json(response_text(result))

    def generate(self, prompt: str, images: list[bytes], spec: GenerationSpec | dict | None = None) -> bytes:
        self.last_generation_evidence = None
        if not self.image_model:
            raise AgentError("LIU_IMAGE_MODEL 未配置；可先仅生成提示词")
        capabilities = self.capabilities()
        if spec is not None:
            spec = GenerationSpec.model_validate(spec)
            validate_generation_spec(spec, capabilities=capabilities, prompt=prompt)
            if len(spec.generation_reference_ids) != len(images):
                raise AgentError("生成规格的参考 ID 数量与实际图片数量不一致；未提交请求")
        parameters = {"model": self.image_model, "prompt": prompt, "n": 1}
        if spec is not None:
            parameters.update(size=spec.requested_size, quality=spec.quality, output_format=spec.output_format)
        endpoint = "images/edits" if images else "images/generations"
        sent_arguments = {"endpoint": endpoint, "encoding": "multipart" if images else "json",
                          "parameters": {key: value for key, value in parameters.items() if key != "prompt"},
                          "prompt_digest": sha(prompt.encode("utf-8")),
                          "generation_reference_ids": list(spec.generation_reference_ids) if spec else None,
                          "image_sha256_in_order": [sha(image) for image in images],
                          "evidence_source": "adapter_request_arguments"}
        evidence = {
            "version": "liu.generation-parameters.v1",
            "mode": "generation_spec" if spec is not None else "legacy_without_spec",
            "capabilities": capabilities,
            "requested": spec.model_dump() if spec is not None else {
                "model_id": self.image_model, "prompt_digest": sha(prompt.encode("utf-8")),
                "requested_size": None, "quality": None, "output_format": None},
            "actually_sent": None,
            "returned": None, "saved": None, "verified": None,
        }
        self.last_generation_evidence = evidence
        if images:
            files = [("image[]", (f"reference-{i+1}.png", img, "image/png")) for i, img in enumerate(images)]
            fields = {name: str(value) for name, value in parameters.items()}
            sent_arguments["parameters"] = {key: value for key, value in fields.items() if key != "prompt"}
            data = self.post(endpoint, "image", data=fields, files=files, _generation_evidence=evidence,
                             _generation_arguments=sent_arguments)
        else:
            data = self.post(endpoint, "image", json=parameters, _generation_evidence=evidence,
                             _generation_arguments=sent_arguments)
        rows = data.get("data", [])
        if spec is not None and len(rows) != 1:
            raise AgentError("请求一张图片却未返回恰好一张；不冒充生成规格已兑现")
        encoded = rows[0].get("b64_json") if rows else None
        if not encoded:
            raise AgentError("没有返回图片字节；URL-only 图像服务未接入，不冒充出图成功")
        try:
            decoded = base64.b64decode(encoded, validate=True)
        except (ValueError, TypeError) as exc:
            raise AgentError("图片返回数据无效") from exc
        facts = image_facts(decoded)
        reported = {name: data[name] for name in ("size", "quality", "output_format", "model") if name in data}
        evidence["returned"] = {"reported": reported, "image": facts}
        normalized = normalize_image(decoded)
        saved_facts = image_facts(normalized)
        verified = verify_returned_parameters(spec, facts, reported, display_facts=saved_facts)
        evidence["verified"] = verified
        if spec is not None and any(
                check["status"] in ("mismatch", "reported_mismatch") or check.get("report_matches_bytes") is False
                for check in (verified[name] for name in ('size', 'output_format', 'quality'))):
            raise AgentError("返回图片尺寸、格式或质量报告与生成规格不一致；已保留参数事实，未自动重试")
        evidence["saved"] = {**saved_facts, "normalization": "metadata_free_srgb_png"}
        return normalized

    def research(self, queries: list[str], allowed_domains: list[str] | None = None) -> dict:
        if not queries:
            return {"state": "not_needed", "sources": [], "summary": ""}
        tool = {"type": "web_search"}
        if allowed_domains:
            tool["filters"] = {"allowed_domains": allowed_domains}
        body = {"model": self.search_model, "store": False, "instructions": RESEARCH,
                "input": "公开检索查询：\n" + "\n".join(queries[:3]),
                "tools": [tool], "tool_choice": "required", "max_tool_calls": 2,
                "include": ["web_search_call.action.sources"], "max_output_tokens": 3000}
        result = self.post("responses", "search", json=body)
        summary = response_text(result)
        sources, used_search = {}, False
        for output in result.get("output", []):
            if output.get("type") == "web_search_call":
                used_search = True
                for src in output.get("action", {}).get("sources", []):
                    url = src.get("url", "")
                    if url.startswith("https://"):
                        sources[url] = {"url": url, "title": src.get("title", ""), "visual_inspection": False}
            for content in output.get("content", []):
                for ann in content.get("annotations", []):
                    if ann.get("type") == "url_citation" and ann.get("url", "").startswith("https://"):
                        sources[ann["url"]] = {"url": ann["url"], "title": ann.get("title", ""), "visual_inspection": False}
        if not used_search or not sources:
            raise AgentError("检索没有可追溯来源；不把无来源摘要写入知识库")
        return {"state": "text_evidence_only", "queries": queries[:3], "summary": summary,
                "sources": list(sources.values()), "checked_at": now(),
                "warning": "本次是有来源的文字补充，不代表已查看网页中的视觉素材，也不会自动写入核心库"}
