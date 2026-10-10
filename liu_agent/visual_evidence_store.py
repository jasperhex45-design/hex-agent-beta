"""Small explicit indexes over existing images; pixels stay outside task context."""
from __future__ import annotations

import argparse
from copy import deepcopy
import json
from pathlib import Path

from PIL import Image

from . import harness
from .io import AgentError, digest, read_json, sha, within, write_json
from .retrieval import tokens
from .run_status import inspect_run
from .workflow import read_packet

FORMAT = "hex.visual-evidence-index.v1"


def _seal(body: dict) -> dict:
    return {**body, "digest": digest(body)}


def _load(path: Path) -> dict:
    data = read_json(path)
    if data.get("format") != FORMAT or data.get("digest") != digest({k: v for k, v in data.items() if k != "digest"}):
        raise AgentError("视觉证据索引摘要或格式无效")
    if len({row["id"] for row in data["assets"]}) != len(data["assets"]):
        raise AgentError("视觉证据索引身份重复")
    return data


def _asset(path: Path, role: str, identity: dict, *, observation=None, feedback=None, lineage=None,
           source_evidence=None, raw_original_sha256=None) -> dict:
    raw = path.read_bytes()
    with Image.open(path) as image:
        size = list(image.size)
    return {"id": "ve_" + digest({"sha256": sha(raw), "role": role, "identity": identity}),
            "source_path": str(path.resolve()), "source_sha256": sha(raw), "role": role,
            "source_size": size, "size": size, "identity": identity,
            "raw_original": {"sha256": raw_original_sha256 or sha(raw),
                             "bytes_available_here": not raw_original_sha256 or raw_original_sha256 == sha(raw)},
            "observations": observation or [], "feedback": feedback or [], "lineage": lineage,
            "source_evidence": source_evidence or [], "crop_box": None, "parent_asset_id": None,
            "crop_pixels_sha256": None}


def _proof(path: Path) -> dict:
    return {"file": str(path.resolve()), "sha256": sha(path.read_bytes())}


def build(runs: list[Path], out: Path, *, case_packs: list[Path] | None = None) -> dict:
    assets, sources = {}, []
    if any(Path(out).resolve().is_relative_to(Path(root).resolve()) for root in [*runs, *(case_packs or [])]):
        raise AgentError("索引须写在原任务和来源包之外，不能覆盖源证据")
    for run in dict.fromkeys(Path(path).resolve() for path in runs):
        status = inspect_run(run)
        if status["blockers"]:
            raise AgentError("原任务证据无效，不能建立图像索引")
        packet = read_packet(run)
        proof = [_proof(run / "packet.json")]
        feedback = []
        if (run / "user-feedback.json").exists():
            from .feedback import verified_feedback
            _, candidate, report = verified_feedback(run)
            proof.append(_proof(run / "user-feedback.json"))
            feedback = [{"candidate_sha256": candidate["sha256"], "value": report,
                         "authority": "bound_user_feedback_not_technique_preference"}]
        facts = {row["id"]: row for row in packet.get("reference_analysis", {}).get("findings", [])}
        identity = {"packet_digest": packet["digest"], "domain": packet["need"]["domain"],
                    "request": packet["need"]["intent"]}
        for reference in packet["references"]:
            row = _asset(within(run, reference["file"]), "reference", {**identity, "reference_id": reference["id"]},
                         observation=[{"value": facts[reference["id"]], "authority": "prior_host_observation"}] if reference["id"] in facts else [],
                         source_evidence=proof, raw_original_sha256=reference.get("original_sha256"))
            if row["source_sha256"] != reference["sha256"]:
                raise AgentError("参考源图与任务摘要不同")
            row["reference_role"] = reference["role"]
            row["usage"] = "evaluation_only"
            assets[row["id"]] = row
        trials_file = run / ("image-tests.json" if (run / "api-state.json").exists() else "host-image-tests.json")
        trials = read_json(trials_file) if trials_file.exists() else []
        for candidate in status["candidates"]:
            case = harness.case_record(run, candidate["request_id"])
            trial = next((row for row in trials if row.get("request_id") == candidate["request_id"]), None)
            ledger_name = "api-state.json" if case["route"] == "api" else "host-dispatches.json"
            ledger = read_json(run / ledger_name)
            rows = ledger["requests"] if case["route"] == "api" else ledger
            attempt = next(row for row in rows if (row["id"] if case["route"] == "api" else row["request"]["id"]) == candidate["request_id"])
            original_sha256 = attempt["output"].get("original_sha256")
            evidence = [*proof, _proof(run / ledger_name), *([_proof(trials_file)] if trial else [])]
            row = _asset(within(run, candidate["file"]), "candidate", {**identity, "request_id": candidate["request_id"], "case_digest": case["digest"]},
                         observation=[{"value": trial["review"], "authority": "prior_reviewer_report"}] if trial else [],
                         feedback=[item for item in feedback if item["candidate_sha256"] == candidate["sha256"]],
                         lineage=deepcopy(packet.get("requirement_parent")), source_evidence=evidence,
                         raw_original_sha256=original_sha256)
            row["usage"] = "evaluation_only"
            if original_sha256 is None:
                row["raw_original"] = {"sha256": None, "bytes_available_here": False}
            assets[row["id"]] = row
        sources.append({"run": str(run), "packet_digest": packet["digest"], "state": status["generation_status"]})
    for folder in dict.fromkeys(Path(path).resolve() for path in case_packs or []):
        manifest = read_json(folder / "case.json")
        if manifest.get("format") != "hex.car-fashion-study-case.v1" or manifest["status"] != "candidate":
            raise AgentError("仅导入明确的待验证外部案例包，不伪造原生成功")
        for entry in [*manifest["artifacts"], *manifest["reference_bindings"], *manifest["candidate_bindings"]]:
            if sha(within(folder, entry["path"]).read_bytes()) != entry["sha256"]:
                raise AgentError("外部案例来源摘要变化")
        feedback = read_json(folder / "evidence/direction-feedback.json")
        cards = read_json(folder / "cards.json")["cards"]
        reference_cards = read_json(folder / "evidence/style-materials.json")["cards"]
        proof = [_proof(folder / "case.json"), _proof(folder / "evidence/direction-feedback.json"),
                 _proof(folder / "cards.json"), _proof(folder / "evidence/style-materials.json")]
        for role, entries in [("reference", manifest["reference_bindings"]), ("candidate", manifest["candidate_bindings"])]:
            for entry in entries:
                card = next((card for card in [*cards, *reference_cards] if card["source_sha256"] == entry["sha256"]), None)
                row = _asset(within(folder, entry["path"]), role,
                             {"library_file_id": entry["library_file_id"], "file_id": entry["file_id"], "domain": "vehicle", "request": "人车时尚案例"},
                             observation=[{"value": card["visible_facts"], "authority": "prior_host_observation"}] if card else [],
                             feedback=[{"value": feedback, "scope": "batch_only_no_individual_acceptance"}] if role == "candidate" else [],
                             source_evidence=proof)
                row.update(usage="evaluation_only", execution_authority="external_case_not_native_dispatch")
                assets[row["id"]] = row
        sources.append({"case_pack": str(folder), "state": "candidate", "native_completion": False})
    body = {"format": FORMAT, "assets": sorted(assets.values(), key=lambda row: row["id"]), "sources": sources,
            "notice": "显式索引快照；按需重建，不扫描全机、不上传、不修改原图，不把观察变成成功经验。"}
    result = _seal(body)
    write_json(out, result)
    return result


def _verify(asset: dict) -> None:
    path = Path(asset["source_path"])
    if not path.is_file() or sha(path.read_bytes()) != asset["source_sha256"]:
        raise AgentError("源图缺失或变化，不能套用旧观察")
    for proof in asset["source_evidence"]:
        path = Path(proof["file"])
        if not path.is_file() or sha(path.read_bytes()) != proof["sha256"]:
            raise AgentError("源图绑定的观察/反馈证据变化；重建索引")


def retrieve(index: Path, query: str, *, roles: list[str] | None = None, limit: int = 3) -> dict:
    data = _load(index)
    terms = set(tokens(query))
    hits = []
    for asset in data["assets"]:
        if roles and asset["role"] not in roles: continue
        text = json.dumps({"role": asset["role"], "identity": asset["identity"], "observations": asset["observations"]}, ensure_ascii=False)
        matches = terms & set(tokens(text))
        if not matches: continue
        hits.append((len(matches), asset))
    hits.sort(key=lambda item: (-item[0], item[1]["id"]))
    selected = []
    for score, asset in hits:
        _verify(asset)
        selected.append({key: asset[key] for key in ["id", "role", "source_sha256", "size", "identity", "crop_box"]} | {"score": score})
        if len(selected) >= max(1, min(limit, 3)): break
    return {"format": "hex.visual-evidence-retrieval.v1", "query": query, "hits": selected,
            "method": "existing_bilingual_tokens_metadata_overlap", "pixel_payloads": 0,
            "notice": "命中只证明元数据相关；inspect后由宿主按需实际打开图像，允许零采用。"}


def inspect(index: Path, ident: str) -> dict:
    row = next((row for row in _load(index)["assets"] if row["id"] == ident), None)
    if row is None: raise AgentError("视觉证据ID不存在")
    _verify(row)
    if row["crop_box"]:
        with Image.open(row["source_path"]) as image:
            if sha(image.convert("RGB").crop(row["crop_box"]).tobytes()) != row["crop_pixels_sha256"]:
                raise AgentError("区域像素摘要不一致")
    return {**deepcopy(row), "notice": "读取描述不等于宿主看过原图；crop是原图区域坐标及像素摘要，没有新生成或原图改写。"}


def add_crop(index: Path, ident: str, box: list[int]) -> dict:
    data = _load(index)
    original = inspect(index, ident)
    if original["crop_box"] or len(box) != 4 or any(type(value) is not int for value in box):
        raise AgentError("区域须是原图上的四个整数坐标")
    width, height = original["source_size"]
    left, top, right, bottom = box
    if not 0 <= left < right <= width or not 0 <= top < bottom <= height:
        raise AgentError("区域超出原图或没有面积")
    with Image.open(original["source_path"]) as image:
        pixels = sha(image.convert("RGB").crop(box).tobytes())
    row = {key: value for key, value in original.items() if key != "notice"}
    row.update(id="ve_" + digest({"parent": ident, "crop_box": box, "pixels": pixels}), role="crop",
               parent_asset_id=ident, crop_box=box, size=[right-left, bottom-top], crop_pixels_sha256=pixels,
               observation_scope="parent_full_frame_not_new_roi_observation", feedback_scope="parent_result_not_crop_acceptance")
    if not any(item["id"] == row["id"] for item in data["assets"]): data["assets"].append(row)
    body = {key: value for key, value in data.items() if key != "digest"}
    body["assets"].sort(key=lambda item: item["id"])
    write_json(index, _seal(body))
    return row


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    build_parser = sub.add_parser("build")
    build_parser.add_argument("--run", type=Path, action="append", default=[])
    build_parser.add_argument("--case-pack", type=Path, action="append", default=[])
    build_parser.add_argument("--index", type=Path, required=True)
    for command in ["search", "inspect", "crop"]:
        item = sub.add_parser(command)
        item.add_argument("--index", type=Path, required=True)
        if command == "search":
            item.add_argument("--query", required=True)
            item.add_argument("--role", action="append", default=[])
        else: item.add_argument("--id", required=True)
        if command == "crop": item.add_argument("--box", nargs=4, type=int, required=True)
    args = parser.parse_args(argv)
    try:
        if args.command == "build":
            index = build(args.run, args.index, case_packs=args.case_pack)
            result = {"index": str(args.index), "assets": len(index["assets"]), "digest": index["digest"]}
        elif args.command == "search": result = retrieve(args.index, args.query, roles=args.role)
        elif args.command == "inspect": result = inspect(args.index, args.id)
        else: result = add_crop(args.index, args.id, args.box)
    except (AgentError, OSError, ValueError, KeyError, TypeError) as exc:
        print(str(exc), file=__import__("sys").stderr)
        return 2
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
