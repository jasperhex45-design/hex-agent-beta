"""Task-conditioned visual templates.

Templates are reusable production structures, not aesthetic truth.  They sit
above the observed-image cards: a template says what must be designed, while a
card says which visible relation was actually seen in a reference.
"""
from __future__ import annotations

import math
from collections import Counter
from pathlib import Path

from .contracts import TemplateBinding
from .io import AgentError, read_json
from .retrieval import tokens


def load(path: Path) -> dict:
    try:
        data = read_json(path)
    except FileNotFoundError as exc:
        raise AgentError(f"模板库不存在：{path}") from exc
    if data.get("format") != "liu.template-library.v1":
        raise AgentError("模板库格式不受支持")
    templates = data.get("templates")
    if not isinstance(templates, list) or not templates:
        raise AgentError("模板库不能为空")
    ids = [x.get("id") for x in templates]
    if len(ids) != len(set(ids)):
        raise AgentError("模板 ID 重复")
    for item in templates:
        if not item.get("source_url") or not item.get("guidance") or not item.get("pitfalls"):
            raise AgentError(f"模板缺少来源、指导或避坑规则：{item.get('id')}")
        pack = item.get("style_pack", {})
        if not isinstance(pack, dict):
            raise AgentError(f"模板 style_pack 必须是对象：{item.get('id')}")
        for key in ("environment_variables", "style_fidelity_anchors", "source_content_to_avoid"):
            if key in pack and (not isinstance(pack[key], list) or any(not str(x).strip() for x in pack[key])):
                raise AgentError(f"模板 style_pack.{key} 必须是非空字符串列表：{item.get('id')}")
        adapters = pack.get("model_adapters", {})
        if not isinstance(adapters, dict):
            raise AgentError(f"模板 style_pack.model_adapters 必须是对象：{item.get('id')}")
        for model, adapter in adapters.items():
            if not isinstance(adapter, dict):
                raise AgentError(f"模板模型适配器必须是对象：{item.get('id')} / {model}")
            for key in ("prompt_rules", "setup_notes", "limitations"):
                if key in adapter and (not isinstance(adapter[key], list)
                                       or any(not str(x).strip() for x in adapter[key])):
                    raise AgentError(f"模板适配器 {key} 必须是非空字符串列表：{item.get('id')} / {model}")
    return data


def validate(path: Path) -> dict:
    data = load(path)
    return {"library": str(path.resolve()), "templates": len(data["templates"]),
            "status": "passed", "source": data.get("source_url", "")}


def _document(item: dict) -> Counter:
    values = [item.get("id", ""), item.get("title", ""), item.get("category", ""),
              *item.get("domains", []), *item.get("styles", []),
              *item.get("scenes", []), *item.get("tags", []),
              *item.get("required_slots", []), *item.get("guidance", []),
              *item.get("pitfalls", []),
              *item.get("style_pack", {}).get("style_fidelity_anchors", []),
              *item.get("style_pack", {}).get("source_content_to_avoid", [])]
    return Counter(tokens(" ".join(values)))


def search(path: Path, query: str, domain: str | None = None,
           limit: int = 4) -> dict:
    data = load(path)
    terms = set(tokens(query))
    if not terms:
        raise AgentError("请输入模板查询词")
    candidates = []
    for item in data["templates"]:
        if item.get("status") != "curated":
            continue
        if domain and domain not in item.get("domains", []) and "hybrid" not in item.get("domains", []):
            continue
        doc = _document(item)
        matched = sorted(terms & doc.keys())
        if not matched:
            continue
        score = 0.0
        for term in matched:
            idf = math.log(1 + (len(data["templates"]) - sum(term in _document(x) for x in data["templates"]) + .5) / .5)
            score += idf * doc[term]
        candidates.append({"id": item["id"], "score": round(score, 5),
                           "matched_terms": matched, "template": item})
    candidates.sort(key=lambda x: (-x["score"], x["id"]))
    return {"method": "curated_template_bm25_category_style_scene", "query": query,
            "domain": domain, "hits": candidates[:max(1, min(limit, 8))],
            "notice": "模板提供结构与避坑规则，不证明审美效果；仍需绑定到当前设计并实际验证。"}


def attach_search(run: Path, result: dict) -> dict:
    from .workflow import ensure_references_mutable, read_packet, save_packet
    packet = read_packet(run)
    from .visual_decision import record_only
    if record_only(packet):
        raise AgentError('生产配置尚未晋升模板经验；不能将检索结果写入任务')
    ensure_references_mutable(run)
    packet["template_retrieval"] = result
    return save_packet(packet, run)


def selected(result: dict, binding: TemplateBinding | None) -> dict | None:
    if binding is None:
        return None
    for hit in result.get("hits", []):
        if hit.get("id") == binding.template_id:
            return hit["template"]
    raise AgentError("template_binding 引用了本次未检索到的模板")
