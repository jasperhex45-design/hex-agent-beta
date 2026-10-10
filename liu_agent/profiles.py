"""Data-only model profiles, immutable versions, explicit promotion and rollback."""
from __future__ import annotations
from datetime import date
from pathlib import Path
from .contracts import ModelProfile
from .io import AgentError, now, read_json, write_json, sha, within


def profiles(kb: Path, state: Path) -> dict[str, ModelProfile]:
    result = {p.id: p for f in sorted((kb / "models").glob("*.json")) for p in [ModelProfile.model_validate(read_json(f))]}
    for f in sorted((state / "models" / "active").glob("*.json")):
        p = ModelProfile.model_validate(read_json(f))
        result[p.id] = p
    return result


def stale(p: ModelProfile) -> bool:
    return (date.today() - date.fromisoformat(p.last_checked)).days > p.review_after_days


def install(candidate: Path, kb: Path, state: Path, expected_version: int, allow_experimental: bool) -> dict:
    p = ModelProfile.model_validate(read_json(candidate))
    current = profiles(kb, state).get(p.id)
    actual = current.version if current else 0
    if actual != expected_version:
        raise AgentError(f"版本已变化：期待 {expected_version}，实际 {actual}")
    if p.version <= actual:
        raise AgentError("新规则版本必须大于当前版本")
    history = state / "models" / "history" / p.id
    historic_versions = [int(f.stem[1:]) for f in history.glob("v*.json") if f.stem[1:].isdigit()]
    if historic_versions and p.version <= max(historic_versions):
        raise AgentError("历史版本号不可重用；回滚后请创建更高的新版本")
    if p.status == "deprecated":
        raise AgentError("不能激活 deprecated 规则")
    if p.status == "experimental" and not allow_experimental:
        raise AgentError("候选未经验证；明确添加 --allow-experimental 才可试用")
    copied_evidence = []
    for evidence in p.evaluation_files:
        ep = within(candidate.parent, evidence)
        if not ep.is_file():
            raise AgentError(f"找不到实测记录：{evidence}")
        if ep.stat().st_size > 4 * 1024 * 1024:
            raise AgentError("实测记录应是小于4MiB的JSON/文本索引，不是全部图片文件")
        raw = ep.read_bytes()
        dest = state / "models" / "evidence" / (sha(raw)[:20] + ep.suffix)
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(raw)
        copied_evidence.append(str(dest.relative_to(state)))
    if copied_evidence:
        p.evaluation_files = copied_evidence
    history = state / "models" / "history" / p.id
    history.mkdir(parents=True, exist_ok=True)
    if current:
        write_json(history / f"v{current.version}.json", current.model_dump())
    write_json(history / f"v{p.version}.json", p.model_dump())
    write_json(state / "models" / "active" / f"{p.id}.json", p.model_dump())
    record = {"at": now(), "model": p.id, "previous": actual, "active": p.version, "status": p.status}
    write_json(state / "models" / "last-change.json", record)
    return record


def rollback(ident: str, version: int, state: Path) -> dict:
    if not ident.replace("_", "").replace("-", "").isalnum():
        raise AgentError("模型 ID 不合法")
    path = state / "models" / "history" / ident / f"v{version}.json"
    if not path.is_file():
        raise AgentError("没有这个历史版本；仅能回滚已经保存过的版本")
    p = ModelProfile.model_validate(read_json(path))
    write_json(state / "models" / "active" / f"{ident}.json", p.model_dump())
    return {"model": ident, "active": version, "rolled_back": True}
