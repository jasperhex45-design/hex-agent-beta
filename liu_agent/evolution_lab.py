"""Offline one-mechanism trials with fixed queries; recommend, never apply."""
from __future__ import annotations

import argparse
from copy import deepcopy
import json
from pathlib import Path
import time

from . import visual_evidence_store as store
from .io import AgentError, digest, read_json, sha, write_json

DOMAIN_GUARDS = frozenset({"source_identity", "required_evidence", "no_generation", "no_auto_apply"})
DIFFS = {
    "retrieval_limit": ("max(1, min(limit, 3))", "max(1, min(limit, 1))"),
    "summary_without_request": (
        'selected.append({key: asset[key] for key in ["id", "role", "source_sha256", "size", "identity", "crop_box"]} | {"score": score})',
        'summary = {key: asset[key] for key in ["id", "role", "source_sha256", "size", "identity", "crop_box"]} | {"score": score}\n        summary["identity"] = {key: value for key, value in summary["identity"].items() if key != "request"}\n        selected.append(summary)'),
}


def _sealed(body: dict) -> dict:
    return {**body, "digest": digest(body)}


def _source_groups(index: dict, projects: dict[str, str]) -> dict:
    """Conservatively join explicit projects and all recorded source relations."""
    assets = {row['id']: row for row in index['assets']}
    if not isinstance(projects, dict) or set(projects) != set(assets) or any(not isinstance(value, str) or not value.strip() for value in projects.values()):
        raise AgentError('来源隔离须为全部图像明确项目分组；未知项目不默认独立')
    parent = {ident: ident for ident in assets}

    def root(ident):
        while parent[ident] != ident:
            parent[ident] = parent[parent[ident]]
            ident = parent[ident]
        return ident

    seen = {}
    for ident, row in assets.items():
        store._verify(row)
        tokens = {('project', projects[ident].strip()), ('asset', ident)}
        for value in (row.get('source_sha256'), (row.get('raw_original') or {}).get('sha256')):
            if value: tokens.add(('image', value))
        if row.get('parent_asset_id'): tokens.add(('asset', row['parent_asset_id']))
        packet = row.get('identity', {}).get('packet_digest')
        if packet: tokens.add(('packet', packet))
        for key in ('library_file_id', 'file_id'):
            value = row.get('identity', {}).get(key)
            if value: tokens.add((key, value))
        for proof in row.get('source_evidence', []):
            tokens.add(('proof_file', str(Path(proof['file']).resolve()).casefold()))
            tokens.add(('proof_content', proof['sha256']))

        def lineage(value):
            if isinstance(value, dict):
                for key, item in value.items():
                    if isinstance(item, str) and key.endswith('packet_digest'):
                        tokens.add(('packet', item))
                    if isinstance(item, str) and key == 'parent_candidate_sha256':
                        tokens.add(('image', item))
                    if isinstance(item, str) and key in {'run', 'parent_run'}:
                        tokens.add(('source_run', str(Path(item).resolve()).casefold()))
                    lineage(item)
            elif isinstance(value, list):
                for item in value: lineage(item)

        lineage(row.get('lineage'))
        # Source runs connect parent links with their packet/reference group.
        for proof in row.get('source_evidence', []):
            if Path(proof['file']).name == 'packet.json':
                tokens.add(('source_run', str(Path(proof['file']).resolve().parent).casefold()))
        for token in tokens:
            if token in seen: parent[root(ident)] = root(seen[token])
            else: seen[token] = ident
    groups = {}
    for ident in assets:
        groups.setdefault(root(ident), []).append(ident)
    members = sorted(sorted(group) for group in groups.values())
    return {'protocol': 'declared_projects_and_recorded_sources.v1',
            'project_authority': 'host_declared_not_independently_authenticated',
            'projects': deepcopy(projects), 'connected_groups': members}


def seal_suite(index: Path, cases: list[dict], *, source_projects: dict[str, str] | None = None) -> dict:
    data = store._load(index)
    body = {"format": "hex.offline-query-suite.v1", "index_digest": data["digest"],
            "cases": deepcopy(cases), "heldout_scope": "queries_only_not_new_images_or_blind_visual_judgment"}
    if source_projects is not None:
        body.update(format='hex.offline-query-suite.v2', source_grouping=_source_groups(data, source_projects),
                    heldout_scope='source_grouped_queries_not_blind_visual_judgment')
    result = _sealed(body)
    _validate_suite(data, result)
    return result


def _validate_suite(index: dict, suite: dict) -> None:
    if (suite.get("format") not in {"hex.offline-query-suite.v1", "hex.offline-query-suite.v2"} or
            suite.get("digest") != digest({k: v for k, v in suite.items() if k != "digest"}) or
            suite["index_digest"] != index["digest"]):
        raise AgentError("固定suite/索引摘要变化；不能事后换答案")
    cases = suite["cases"]
    if not 2 <= len(cases) <= 40 or {case["split"] for case in cases} != {"legacy", "heldout"}:
        raise AgentError("suite须同时有旧任务与heldout，最多40查询")
    ids = {asset["id"] for asset in index["assets"]}
    seen = set()
    case_ids = set()
    for case in cases:
        if not case.get('id') or case['id'] in case_ids:
            raise AgentError('查询案例ID须非空且唯一')
        case_ids.add(case['id'])
        signature = digest({"query": " ".join(case["query"].casefold().split()), "roles": sorted(case["roles"])})
        if signature in seen or not case["query"].strip():
            raise AgentError("legacy/heldout查询重复或为空；不能伪装独立留出")
        seen.add(signature)
        required = case["required_asset_ids"]
        if not 1 <= len(required) <= 3 or not set(required) <= ids:
            raise AgentError("每项须有1至3个真实来源身份作为可核对答案")
    if suite['format'] == 'hex.offline-query-suite.v2':
        grouping = suite.get('source_grouping', {})
        if grouping != _source_groups(index, grouping.get('projects', {})):
            raise AgentError('来源分组或谱系已变化，须重新封存suite')
        group_for = {ident: position for position, group in enumerate(grouping['connected_groups']) for ident in group}
        split_for = {}
        for case in cases:
            for ident in case['required_asset_ids']:
                group = group_for[ident]
                if group in split_for and split_for[group] != case['split']:
                    raise AgentError('同项目、原图、参考或派生谱系跨split；不能称来源隔离')
                split_for[group] = case['split']


def _candidate(candidate: dict) -> None:
    mechanism = candidate.get("mechanism")
    if mechanism not in DIFFS or candidate.get("automatic_apply") is not False:
        raise AgentError("只支持两个离线小机制，绝不自动应用生产")
    if set(candidate.get("domain_guards", [])) != DOMAIN_GUARDS:
        raise AgentError("本项目domain guards必须明确且完整，不能默认空")
    if candidate.get("claim") not in {"process", "visual"} or not candidate.get("hypothesis", "").strip():
        raise AgentError("候选须有单一假设及process/visual声明")
    change = candidate["diff"]
    source = Path(store.__file__)
    before, after = DIFFS[mechanism]
    if (change.get("path") != "liu_agent/visual_evidence_store.py" or
            change.get("source_sha256") != sha(source.read_bytes()) or
            change.get("before") != before or change.get("after") != after or
            source.read_text(encoding="utf-8").count(before) != 1):
        raise AgentError("候选diff不是当前源码的单一已允许改动；拒绝任意patch执行")


def evaluate(index: Path, suite: dict, candidate: dict, *, history: Path) -> dict:
    data = store._load(index)
    _validate_suite(data, suite)
    _candidate(candidate)
    history = Path(history).resolve()
    if history == Path(index).resolve() or history.suffix != ".json":
        raise AgentError("历史须用独立JSON，不能覆盖输入索引")
    for source in data["sources"]:
        root = Path(source.get("run", source.get("case_pack", ""))).resolve()
        if history.is_relative_to(root):
            raise AgentError("离线历史不能写回原生产任务或来源包")
    # Verify required source bytes even on cache hits; stale history is not proof.
    for ident in {ident for case in suite["cases"] for ident in case["required_asset_ids"]}:
        store.inspect(index, ident)
    log = read_json(history) if history.exists() else _sealed({"format": "hex.offline-evolution-history.v1", "reports": []})
    if log.get("format") != "hex.offline-evolution-history.v1" or log.get("digest") != digest({k: v for k, v in log.items() if k != "digest"}):
        raise AgentError("离线历史损坏；不能重置失败或预算")
    candidate_digest = digest(candidate)
    prior = [row for row in log["reports"] if row["suite_digest"] == suite["digest"]]
    cached = next((row for row in prior if row["candidate_digest"] == candidate_digest), None)
    if cached:
        if 'source_independence' in cached:
            return cached
        # Explain the scope of an old cached result without rewriting history
        # or upgrading its evidence to a new independence claim.
        projected = {key: deepcopy(value) for key, value in cached.items() if key != 'digest'}
        projected.update(source_independence='not_verified_query_only_legacy',
                         compatibility_projection={'original_report_digest': cached['digest'],
                                                   'scope': 'historical_cached_report_not_recomputed'})
        return _sealed(projected)
    max_candidates = 1 if suite['format'] == 'hex.offline-query-suite.v2' else 2
    if len(prior) >= max_candidates:
        if max_candidates == 1:
            raise AgentError('来源隔离封存集仅检查一个预选候选；不得反复读取后调参')
        raise AgentError("此固定suite最多两个候选，离线预算已用完；保留负例，不循环改答案")
    started = time.perf_counter()
    measurements = []
    for case in suite["cases"]:
        baseline = store.retrieve(index, case["query"], roles=case["roles"], limit=3)
        proposed = store.retrieve(index, case["query"], roles=case["roles"], limit=1 if candidate["mechanism"] == "retrieval_limit" else 3)
        if candidate["mechanism"] == "summary_without_request":
            proposed = deepcopy(proposed)
            for hit in proposed["hits"]: hit["identity"].pop("request", None)
        measurements.append({"id": case["id"], "split": case["split"],
            "baseline_correct": set(case["required_asset_ids"]) <= {hit["id"] for hit in baseline["hits"]},
            "candidate_correct": set(case["required_asset_ids"]) <= {hit["id"] for hit in proposed["hits"]},
            "baseline_bytes": len(json.dumps(baseline, ensure_ascii=False, sort_keys=True).encode("utf-8")),
            "candidate_bytes": len(json.dumps(proposed, ensure_ascii=False, sort_keys=True).encode("utf-8")),
            "required_asset_ids": case["required_asset_ids"], "candidate_asset_ids": [hit["id"] for hit in proposed["hits"]]})
    baseline_bytes = sum(row["baseline_bytes"] for row in measurements)
    candidate_bytes = sum(row["candidate_bytes"] for row in measurements)
    gates = {"baseline_correctness": all(row["baseline_correct"] for row in measurements),
             "legacy_correctness": all(row["candidate_correct"] for row in measurements if row["split"] == "legacy"),
             "heldout_correctness": all(row["candidate_correct"] for row in measurements if row["split"] == "heldout"),
             "cost": candidate_bytes <= baseline_bytes,
             "claim_evidence": candidate["claim"] == "process"}
    body = {"format": "hex.offline-evolution-result.v1", "candidate_digest": candidate_digest,
            "suite_digest": suite["digest"], "index_digest": data["digest"], "hypothesis": candidate["hypothesis"],
            "candidate_diff": candidate["diff"], "measurements": measurements, "gates": gates,
            "decision": "recommend_accept_process_only" if all(gates.values()) else "reject",
            "automatic_apply": False, "patch_executed": False, "implementation": "bounded_offline_adapter_not_diff_execution",
            "visual_quality": "not_tested", "visual_claim_supported": False,
            "cost": {"baseline_metadata_bytes": baseline_bytes, "candidate_metadata_bytes": candidate_bytes,
                     "metric": "actual_utf8_metadata_bytes_not_API_tokens_or_money", "API_tokens": None, "amount": None,
                     "generation_calls": 0, "paid_calls": 0}, "elapsed_seconds": round(time.perf_counter()-started, 6),
            "heldout_scope": suite["heldout_scope"],
            "source_independence": 'declared_projects_and_recorded_sources_checked' if suite['format'] == 'hex.offline-query-suite.v2' else 'not_verified_query_only_legacy',
            "notice": "只评估本地检索输出适配器和身份完整性；建议不应用源码，不证明盲测、审美提升或生产token节省。"}
    result = _sealed(body)
    write_json(history, _sealed({"format": log["format"], "reports": [*log["reports"], result]}))
    return result


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--index", type=Path, required=True)
    parser.add_argument("--suite", type=Path, required=True)
    parser.add_argument("--candidate", type=Path, required=True)
    parser.add_argument("--history", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        if args.out.resolve() in {path.resolve() for path in [args.index, args.suite, args.candidate, args.history]}:
            raise AgentError("输出不能覆盖输入或失败历史")
        result = evaluate(args.index, read_json(args.suite), read_json(args.candidate), history=args.history)
        write_json(args.out, result)
    except (AgentError, OSError, ValueError, KeyError, TypeError) as exc:
        print(str(exc), file=__import__("sys").stderr)
        return 2
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result["decision"] == "recommend_accept_process_only" else 2


if __name__ == "__main__":
    raise SystemExit(main())
