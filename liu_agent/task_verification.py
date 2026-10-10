"""Explicit task-entry adapter: run existing read-only CLIs and record their calls."""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import subprocess
import sys

from .harness import _input_hashes
from .io import AgentError, digest, read_json, sha, within, write_json


def _code_hashes() -> dict:
    root = Path(__file__).resolve().parent
    return {name: sha((root / name).read_bytes()) for name in [
        "task_verification.py", "cli.py", "harness.py", "run_status.py",
        "trajectory_evaluator.py", "visual_evidence_store.py", "generation_authorization.py"]}


def _arguments(operation: str, run: Path, out: Path, query: str, ident: str | None = None) -> list[str]:
    commands = {
        "status": ["hex_agent", "status", "--run", str(run), "--format", "json"],
        "harness": ["hex_agent", "harness", "plan", "--run", str(run)],
        "trajectory": ["hex_agent.trajectory_evaluator", "--run", str(run), "--out", str(out / "generation-trajectory.json")],
        "build": ["hex_agent.visual_evidence_store", "build", "--run", str(run), "--index", str(out / "visual-index.json")],
        "search": ["hex_agent.visual_evidence_store", "search", "--index", str(out / "visual-index.json"), "--query", query],
        "inspect": ["hex_agent.visual_evidence_store", "inspect", "--index", str(out / "visual-index.json"), "--id", ident or ""],
        "finish": ["hex_agent.trajectory_evaluator", "--run", str(run), "--finish", "image", "--out", str(out / "finish-image.json")],
    }
    return ["-B", "-m", *commands[operation]]


def verify_trace(run: Path, out: Path, trace: dict) -> dict:
    run, out = Path(run).resolve(), Path(out).resolve()
    if (trace.get("format") != "hex.task-entry-trajectory.v1" or
            trace.get("origin") != "live_local_readonly_invocations" or
            trace.get("run") != str(run) or trace.get("out") != str(out) or
            trace.get("source_before") != _input_hashes(run) or
            trace.get("source_after") != trace.get("source_before") or
            trace.get("code_sha256") != _code_hashes()):
        raise AgentError("task-entry source/code binding changed")
    events = trace["events"]
    names = [event["operation"] for event in events]
    if names[:5] != ["status", "harness", "trajectory", "build", "search"] or names[-1] != "finish":
        raise AgentError("task-entry order changed")
    if len(events) > 9 or any(name != "inspect" for name in names[5:-1]):
        raise AgentError("task-entry must inspect at most three selected assets")
    selected = []
    inspected = []
    for number, event in enumerate(events):
        if event["sequence"] != number or event["argv"] != _arguments(
                event["operation"], run, out, trace["query"], event.get("asset_id")):
            raise AgentError("task-entry actual argv/order mismatch")
        proof = event["receipt"]
        path = within(out, proof["file"])
        if sha(path.read_bytes()) != proof["sha256"]:
            raise AgentError("task-entry receipt hash changed")
        receipt = read_json(path)
        if receipt["argv"] != event["argv"] or receipt["returncode"] != event["returncode"]:
            raise AgentError("task-entry receipt argv/result mismatch")
        allowed = {0, 2} if event["operation"] in {"trajectory", "finish"} else {0}
        if receipt["returncode"] not in allowed:
            raise AgentError("task-entry command failed")
        value = json.loads(receipt["stdout"])
        if event["operation"] == "trajectory" and value.get("status") not in {"passed", "evidence_incomplete"}:
            raise AgentError("native generation evidence failed")
        if event["operation"] == "search": selected = [hit["id"] for hit in value["hits"]]
        if event["operation"] == "inspect":
            if value["id"] != event["asset_id"]: raise AgentError("inspect identity changed")
            inspected.append(value["id"])
    if inspected != selected: raise AgentError("selected evidence was not inspected in order")
    for proof in trace["outputs"]:
        if sha(within(out, proof["file"]).read_bytes()) != proof["sha256"]:
            raise AgentError("task-entry derived output changed")
    return {"status": "passed", "scope": "recorded_current_readonly_task_entry_consistency",
            "events": len(events), "tool_platform_authenticated": False,
            "original_generation_trace_proven": False, "trace_digest": digest(trace)}


def verify_task(run: Path, out: Path, query: str) -> dict:
    run, out = Path(run).resolve(), Path(out).resolve()
    if out.is_relative_to(run) or (out.exists() and any(out.iterdir())):
        raise AgentError("output must be an empty folder outside the source task")
    out.mkdir(parents=True, exist_ok=True)
    before = _input_hashes(run)
    trace = {"format": "hex.task-entry-trajectory.v1", "origin": "live_local_readonly_invocations",
             "run": str(run), "out": str(out), "query": query, "source_before": before,
             "code_sha256": _code_hashes(), "events": [], "outputs": [],
             "generation_steps": "unknown_not_invoked_or_reconstructed"}
    root = Path(__file__).resolve().parent.parent
    env = {**os.environ, "PYTHONPATH": str(root), "PYTHONIOENCODING": "utf-8"}

    def call(operation, ident=None):
        argv = _arguments(operation, run, out, query, ident)
        started = datetime.now(timezone.utc).isoformat()
        process = subprocess.run([sys.executable, *argv], cwd=root, env=env,
                                 capture_output=True, encoding="utf-8", timeout=120)
        receipt = {"argv": argv, "executable": sys.executable, "cwd": str(root),
                   "started_at_utc": started, "completed_at_utc": datetime.now(timezone.utc).isoformat(),
                   "returncode": process.returncode, "stdout": process.stdout, "stderr": process.stderr}
        name = f"receipts/{len(trace['events']):02d}-{operation}.json"
        write_json(out / name, receipt)
        event = {"sequence": len(trace["events"]), "operation": operation, "argv": argv,
                 "returncode": process.returncode, "receipt": {"file": name, "sha256": sha((out / name).read_bytes())}}
        if ident is not None: event["asset_id"] = ident
        trace["events"].append(event)
        write_json(out / "task-entry-trace.json", trace)
        allowed = {0, 2} if operation in {"trajectory", "finish"} else {0}
        if process.returncode not in allowed: raise AgentError(f"{operation}: {process.stderr or process.stdout}")
        return json.loads(process.stdout)

    status = call("status")
    plan = call("harness")
    native = call("trajectory")
    if native["status"] == "failed": raise AgentError(native.get("error", "native evidence failed"))
    call("build")
    hits = call("search")
    inspections = [call("inspect", hit["id"]) for hit in hits["hits"]]
    finish = call("finish")
    trace["source_after"] = _input_hashes(run)
    trace["outputs"] = [{"file": name, "sha256": sha((out / name).read_bytes())}
                        for name in ["generation-trajectory.json", "visual-index.json", "finish-image.json"]]
    write_json(out / "task-entry-trace.json", trace)
    verified = verify_trace(run, out, trace)
    report = {"format": "hex.task-verification.v1", "task_entry": "python -m hex_agent.task_verification",
              "run": str(run), "adapter_trajectory": verified, "generation_trajectory": native,
              "status_stage": status["stage"], "harness_plan_digest": digest(plan),
              "inspected_assets": [row["id"] for row in inspections], "finish": finish,
              "source_unchanged": trace["source_after"] == before,
              "generation_calls": 0, "paid_calls": 0, "pixel_observations": 0,
              "default_production_enabled": False,
              "unknown": ["historical ordered generation-tool calls", "actual vendor model when unexposed", "new visual quality"],
              "notice": "本轮真实只读入口轨迹可核验；不把过去终态补造成完整生成轨迹，不生成、不授权、不修改原任务。"}
    write_json(out / "verification.json", report)
    return report


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--query", required=True)
    args = parser.parse_args(argv)
    try:
        result = verify_task(args.run, args.out, args.query)
    except (AgentError, OSError, ValueError, KeyError, subprocess.SubprocessError) as exc:
        print(str(exc), file=sys.stderr)
        return 2
    print(json.dumps({"report": str(args.out / "verification.json"),
                      "adapter_trajectory": result["adapter_trajectory"],
                      "generation_trajectory": result["generation_trajectory"]["status"],
                      "finish_image_allowed": result["finish"]["allowed"]}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
