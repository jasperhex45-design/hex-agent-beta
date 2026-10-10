"""Bounded source updates and public visual-reference acquisition.

Source changes create reports, not model-quality claims. Commons is one limited
source, not a replacement for a curated commercial photography reference library.
"""
from __future__ import annotations
import json
from pathlib import Path
from urllib.parse import urlencode, urlsplit
from .contracts import Card, MaterialAnalysis
from .io import AgentError, sha, now, write_json, read_json, normalize_image, within
from .network import PublicFetcher, html_text
from .instructions import COMMON


def check_updates(profiles: dict, state: Path, fetcher: PublicFetcher) -> dict:
    urls = sorted({s.url for p in profiles.values() for s in p.sources if s.kind == "official" and s.url.startswith("https://")})
    results = []
    snapshots = state / "source_snapshots"
    for url in urls[:12]:
        key = sha(url.encode("utf-8"))[:20]
        try:
            result = fetcher.get(url)
            if result["mime"] not in ("text/html", "text/plain", "application/json"):
                raise AgentError("更新源不是可读文本")
            text = html_text(result["body"].decode("utf-8", "replace"))
            if len(text) < 80:
                raise AgentError("来源正文不足，可能需要登录/JavaScript；不当成无更新")
            current_hash = sha(text.encode("utf-8"))
            old_path = snapshots / f"{key}.json"
            old = read_json(old_path) if old_path.exists() else None
            changed = bool(old and old["content_sha256"] != current_hash)
            record = {"url": url, "checked_at": now(), "content_sha256": current_hash,
                      "previous_sha256": old["content_sha256"] if old else None,
                      "state": "changed" if changed else "unchanged" if old else "baseline_created",
                      "excerpt": text[:12000]}
            write_json(old_path, record)
            if changed:
                write_json(state / "update_candidates" / f"{key}-{current_hash[:10]}.json", record)
            results.append({k: v for k, v in record.items() if k != "excerpt"})
        except AgentError as exc:
            results.append({"url": url, "state": "unavailable", "error": str(exc)})
    report = {"checked_at": now(), "sources": results,
              "note": "网页正文变化可能来自导航或文案，不等于模型升级。仅提出候选，未覆盖任何编译规则。"}
    write_json(state / "last-update-check.json", report)
    return report


def collect_commons(query: str, out: Path, fetcher: PublicFetcher, limit: int = 3) -> dict:
    if not query.strip() or len(query) > 300:
        raise AgentError("素材查询应为 1-300 字的公开视觉关键词")
    if not 1 <= limit <= 3:
        raise AgentError("首版每次最多收集3张研究缩略图")
    params = {"action": "query", "generator": "search", "gsrsearch": query + " filetype:bitmap",
              "gsrnamespace": 6, "gsrlimit": min(limit*2, 6), "prop": "imageinfo",
              "iiprop": "url|extmetadata|mime", "iiurlwidth": 768,
              "iiextmetadatafilter": "LicenseShortName|LicenseUrl|Artist|ImageDescription|UsageTerms",
              "format": "json"}
    url = "https://commons.wikimedia.org/w/api.php?" + urlencode(params)
    # This is the documented API, not HTML crawling. Respect its HTTP limits/errors.
    data = json.loads(fetcher.get(url, respect_robots=False)["body"])
    if data.get("error"):
        raise AgentError("Commons API 返回错误；请缩短查询或稍后检查服务")
    items, failures = [], []
    out.mkdir(parents=True, exist_ok=True)
    pages = sorted(data.get("query", {}).get("pages", {}).values(), key=lambda p: p.get("index", 9999))
    for page in pages:
        if len(items) >= limit:
            break
        info = (page.get("imageinfo") or [{}])[0]
        meta = info.get("extmetadata", {})
        license_name = html_text(meta.get("LicenseShortName", {}).get("value", ""))
        if not license_name or info.get("mime") not in ("image/jpeg", "image/png", "image/webp"):
            continue
        thumb = info.get("thumburl", "")
        if urlsplit(thumb).hostname not in ("upload.wikimedia.org", "commons.wikimedia.org"):
            continue
        try:
            raw = fetcher.get(thumb, max_bytes=6_000_000, respect_robots=False)["body"]
            image = normalize_image(raw)
            ident = "REF_" + sha(image)[:12]
            if any(i["id"] == ident for i in items):
                continue
            name = ident + ".png"
            (out / name).write_bytes(image)
            items.append({"id": ident, "file": name, "sha256": sha(image), "title": page.get("title", ""),
                          "page_url": info.get("descriptionurl", ""), "image_url": thumb,
                          "license_reported": license_name, "license_url": meta.get("LicenseUrl", {}).get("value", ""),
                          "creator": html_text(meta.get("Artist", {}).get("value", "")),
                          "description": html_text(meta.get("ImageDescription", {}).get("value", ""))[:1500],
                          "visual_inspection": False, "use": "research_reference_only"})
        except AgentError as exc:
            failures.append({"title": page.get("title", ""), "error": str(exc)})
    report = {"query": query, "checked_at": now(), "items": items, "failures": failures,
              "notice": "保留来源与许可元数据；未认定可直接商用。只用于研究，不自动作为身份参考或上传至生图服务。"}
    write_json(out / "materials.json", report)
    return report


def analyze_materials(report: dict, directory: Path, provider, need: dict) -> MaterialAnalysis:
    if not report["items"]:
        return MaterialAnalysis(findings=[])
    images = []
    for item in report["items"]:
        image = within(directory, item["file"]).read_bytes()
        if sha(image) != item["sha256"]:
            raise AgentError("研究图片在收集后改变")
        images.append(image)
    result = provider.structured(COMMON + """
分析这些真实取得的参考图，只描述可见构图/光影/色彩/材质和适用条件。
不识别人物姓名，不从风格图迁移主体身份；每张 findings.reference_id 对应材料 id。
图像不合适也要指出，不因为搜索命中就认定美观。记录 uncertainty。
""", {"need": need, "materials_in_order": report["items"]}, MaterialAnalysis, images)
    expected = {item["id"] for item in report["items"]}
    actual = [item.reference_id for item in result.findings]
    if set(actual) != expected or len(actual) != len(expected):
        raise AgentError("素材分析漏图或虚构 reference_id")
    write_json(directory / "visual-analysis.json", result.model_dump())
    return result


def promote_card(candidate: Path, state: Path, evidence_file: Path, note: str) -> dict:
    card = Card.model_validate(read_json(candidate))
    if not evidence_file.is_file() or len(note.strip()) < 5:
        raise AgentError("晋升知识需要真实验收文件和至少5字的适用范围说明")
    evidence_copy = state / "knowledge" / "evidence" / (sha(evidence_file.read_bytes())[:16] + evidence_file.suffix)
    evidence_copy.parent.mkdir(parents=True, exist_ok=True)
    evidence_copy.write_bytes(evidence_file.read_bytes())
    path = state / "knowledge" / "active" / f"{card.id}.json"
    if path.exists() and read_json(path)["version"] >= card.version:
        raise AgentError("知识版本必须递增")
    raw = card.model_dump()
    raw.update(status="validated", evidence=[str(evidence_copy.relative_to(state)), "人工说明：" + note])
    write_json(path, Card.model_validate(raw).model_dump())
    return {"id": card.id, "version": card.version, "path": str(path), "scope_note": note}
