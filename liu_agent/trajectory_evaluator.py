"""Read-only process checks over existing HEX evidence; never calls an executor."""
from __future__ import annotations

import argparse
from copy import deepcopy
import json
from pathlib import Path

from . import harness, host
from .generation_authorization import FORMAT, read_authorization
from .io import AgentError, digest, read_json, sha, within, write_json
from .run_status import inspect_run
from .workflow import read_packet


def _authorization_history(grant: dict) -> set[str]:
    values = {grant["digest"]}
    if grant["format"] == FORMAT:
        for index, binding in enumerate(grant["bindings"]):
            history = grant["history"][:index + 1]
            earlier = {**grant, "packet_digest": binding["packet_digest"],
                       "input_sources_digest": binding["input_sources"]["digest"],
                       "max_images": history[-1]["max_images"], "history": history,
                       "source_refs": grant["source_refs"][:index + 1],
                       "sources": binding["sources"], "bindings": grant["bindings"][:index + 1]}
            values.add(digest({k: v for k, v in earlier.items() if k != "digest"}))
    return values


def _remote_id(run: Path, ident: str) -> str | None:
    for family in ("comfy", "pippit"):
        path = within(run, f"{family}/{ident}/execution.json")
        if path.exists():
            record = read_json(path)
            return record.get("prompt_id") or record.get("run_id")
    return None


def _check_trace(run: Path, request: dict, state: str, trace: dict) -> None:
    if trace.get("format") != "hex.tool-trajectory.v1" or trace.get("origin") not in {
            "synthetic_fixture", "host_transcribed_not_tool_authenticated"}:
        raise AgentError("轨迹必须明确来源；本地声明不能冒充工具认证")
    events = trace.get("events")
    if not isinstance(events, list) or not 2 <= len(events) <= 200:
        raise AgentError("轨迹须有2至200项有序事件")
    dispatched = submitted = False
    result_state = None
    remote = _remote_id(run, request["id"])
    for event in events:
        if event["request_id"] != request["id"]:
            raise AgentError("未知请求只能查询原request-id，不能混入新请求")
        source = event["evidence"]
        if sha(within(run, source["file"]).read_bytes()) != source["sha256"]:
            raise AgentError("轨迹来源摘要不一致")
        op = event["op"]
        if op in {"dispatch", "submit"}:
            if event["tool"] != request["tool"] or event["arguments"] != request["arguments"]:
                raise AgentError("工具/模型或实际参数与冻结请求不一致")
            if op == "dispatch":
                if dispatched or submitted:
                    raise AgentError("dispatch顺序错误或重复")
                dispatched = True
            else:
                if not dispatched or submitted:
                    raise AgentError("必须先dispatch，原请求最多提交一次")
                submitted = True
        elif op == "query":
            if not submitted or event["arguments"].get("request_id") != request["id"]:
                raise AgentError("查询必须在原提交之后且使用原request-id")
            query_tool = "query-comfy" if request["tool"].startswith("comfyui.") else "query-pippit" if request["tool"].startswith("pippit.") else request["tool"]
            if event["tool"] not in {request["tool"], query_tool} or set(event["arguments"]) - {"request_id", "remote_id"}:
                raise AgentError("查询不能更换执行器、模型或携带新的生成参数")
            if remote and event.get("remote_id") != remote:
                raise AgentError("查询远端编号与原任务不同")
            if "remote_id" in event["arguments"] and event["arguments"]["remote_id"] != remote:
                raise AgentError("查询参数中的远端编号不一致")
        elif op == "result":
            if not submitted or event.get("status") not in {"completed", "failed", "outcome_unknown"}:
                raise AgentError("结果顺序或状态无效")
            if result_state in {"completed", "failed"}:
                raise AgentError("终态结果不可重新登记")
            result_state = event["status"]
        else:
            raise AgentError("未知轨迹操作")
    if not dispatched or not submitted or (state != "pending" and result_state != state):
        raise AgentError("轨迹缺少派发/提交或最终状态与原回执不一致")


def evaluate(run: Path, request_id: str | None = None, *, trace: dict | None = None) -> dict:
    run = Path(run).resolve()
    body = {"format": "hex.trajectory-evaluation.v1", "run": str(run),
            "scope": "recorded_process_consistency", "status": "failed", "checks": [],
            "visual_quality": "not_verified", "generation_calls": 0,
            "notice": "通过只证明保存的过程证据一致，不认证工具现场、不证明语义/审美/用户接受。"}
    try:
        case = harness.case_record(run, request_id)
        ident = case["attempt"]["request_id"]
        packet = read_packet(run)
        status = inspect_run(run)
        body.update(case_digest=case["digest"], request_id=ident,
                    model=case["model"], human_acceptance=case["evaluation"]["human_acceptance"],
                    budget=case["budget"], lineage=deepcopy(packet.get("requirement_parent")),
                    recovery={"original_request_id": ident, "state": case["attempt"]["state"],
                              "new_submission_allowed": status["generation_allowed"],
                              "next_action": status["next_action"].get("action", status["next_action"].get("command"))})
        body["checks"].append({"id": "native_evidence", "verdict": "passed",
                               "covers": ["packet", "tool", "model", "ordered_inputs", "actual_args", "candidate", "lineage", "budget"]})
        if case["route"] != "host":
            body.update(status="evidence_incomplete", missing=["API工具逐次轨迹和来源绑定授权不在此宿主接口暴露"])
        else:
            attempts = host.read_dispatches(run)
            row = next(item for item in attempts if item["request"]["id"] == ident)
            grant = read_authorization(run, packet)
            if row["request"].get("authorization_digest") not in _authorization_history(grant):
                raise AgentError("冻结请求授权不属于有效来源历史")
            body["checks"].append({"id": "source_authorization", "verdict": "passed",
                                   "digest": row["request"]["authorization_digest"]})
            if trace is None:
                body.update(status="evidence_incomplete", missing=["有序tool/args/query轨迹；不从终态回执补造顺序"])
            else:
                _check_trace(run, row["request"], row["status"], trace)
                body["checks"].append({"id": "tool_trajectory", "verdict": "passed", "trace_digest": digest(trace),
                                       "origin": trace["origin"]})
                body["status"] = "passed"
    except (AgentError, OSError, ValueError, KeyError, TypeError, StopIteration) as exc:
        body.update(status="failed", error=str(exc))
    return {**body, "digest": digest(body)}


def finish_check(run: Path, scope: str = "image") -> dict:
    """Check a claimed delivery scope; does not finalize or broaden user acceptance."""
    if scope not in {"prompt", "image", "user"}:
        raise AgentError("finish范围须为prompt/image/user")
    status = inspect_run(Path(run))
    packet = read_packet(Path(run))
    reasons = list(status["blockers"])
    if status["stage"] != "exported": reasons.append("当前结果尚未真实导出")
    if any(row["status"] in {"pending", "outcome_unknown"} for row in status["known_generation_attempts"]):
        reasons.append("原请求结果未确定，只查原编号")
    if scope in {"image", "user"}:
        minimum = len(packet["brief"]["targets"]) if packet["brief"].get("model_comparison") else 1
        if len(status["candidates"]) < minimum: reasons.append("所需实际候选未完成，不能称图片交付完成")
    if scope == "user" and status["human_acceptance"] != "accepted_by_user":
        reasons.append("没有当前结果的用户接受证据")
    return {"format": "hex.finish-check.v1", "scope": scope, "allowed": not reasons,
            "reasons": reasons, "human_acceptance": status["human_acceptance"],
            "visual_quality": "not_verified", "writes": 0, "generation_calls": 0}


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run", type=Path, required=True)
    parser.add_argument("--request-id")
    parser.add_argument("--trace", type=Path)
    parser.add_argument("--finish", choices=("prompt", "image", "user"))
    parser.add_argument("--out", type=Path)
    args = parser.parse_args(argv)
    if args.out and args.out.resolve().is_relative_to(args.run.resolve()):
        parser.error("检查报告须写在被检查任务之外，不改原证据")
    result = finish_check(args.run, args.finish) if args.finish else evaluate(
        args.run, args.request_id, trace=read_json(args.trace) if args.trace else None)
    if args.out: write_json(args.out, result)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result.get("allowed", result.get("status") == "passed") else 2


if __name__ == "__main__":
    raise SystemExit(main())
