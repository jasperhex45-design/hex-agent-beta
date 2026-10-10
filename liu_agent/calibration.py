"""Append-only personal aesthetic calibration from explicit image pairs.

This module deliberately does not score images, call a model, or infer a
lasting style from a single decision. It records which real local files the
user compared, the user's verbatim explanation, and any explicit rejection
reason. Consumers must reverify the file hashes before using a record.
"""
from __future__ import annotations

from collections import Counter
from pathlib import Path
import uuid

from .contracts import (
    CalibrationImage,
    CalibrationRejection,
    PersonalCalibration,
    PersonalCalibrationLedger,
)
from .io import AgentError, digest, normalize_image, now, read_json, sha, write_json


def _image(path: Path, label: str) -> CalibrationImage:
    resolved = path.expanduser().resolve()
    if not resolved.is_file():
        raise AgentError(f"校准图片不存在：{resolved}")
    try:
        raw = resolved.read_bytes()
        # Decode and apply the same safety limits used for references. The
        # original bytes are hashed so later edits cannot silently pass.
        normalize_image(raw)
    except AgentError:
        raise
    except OSError as exc:
        raise AgentError(f"无法读取校准图片：{resolved}") from exc
    return CalibrationImage(label=label, path=str(resolved), sha256=sha(raw))


def _next_id(records: list[PersonalCalibration]) -> str:
    # UUID avoids silently merging two decisions made in the same second.
    return "calibration." + uuid.uuid4().hex[:16]


def _ledger(records: list[PersonalCalibration], updated_at: str | None = None) -> PersonalCalibrationLedger:
    raw = [record.model_dump() for record in records]
    return PersonalCalibrationLedger(
        format="liu.personal-calibration-ledger.v1",
        version=1,
        updated_at=updated_at or now(),
        records=records,
        digest=digest(raw),
    )


def load_ledger(path: Path) -> PersonalCalibrationLedger:
    if not path.exists():
        return _ledger([])
    data = read_json(path)
    # Migrate pre-binding v1 records only after verifying their old digest.
    # Missing binding fields are treated as explicitly unbound, never inferred.
    records = data.get("records", []) if isinstance(data, dict) else []
    if isinstance(records, list) and any(
        not all(key in row for key in ("task_id", "method_ids", "grammar_ids"))
        for row in records if isinstance(row, dict)
    ):
        if data.get("digest") != digest(records):
            raise AgentError("个人审美校准账本摘要不匹配，不能使用已篡改记录")
        migrated = []
        for row in records:
            item = dict(row)
            item.setdefault("task_id", None)
            item.setdefault("method_ids", [])
            item.setdefault("grammar_ids", [])
            migrated.append(item)
        data = dict(data)
        data["records"] = migrated
        data["digest"] = digest(migrated)
    ledger = PersonalCalibrationLedger.model_validate(data)
    expected = digest([record.model_dump() for record in ledger.records])
    if ledger.digest != expected:
        raise AgentError("个人审美校准账本摘要不匹配，不能使用已篡改记录")
    return ledger


def _withdrawal_path(path: Path) -> Path:
    return path.with_name(path.stem + '.withdrawals.json')


def _withdrawals(path: Path, ledger: PersonalCalibrationLedger) -> dict:
    sidecar = _withdrawal_path(path)
    if not sidecar.exists():
        body = {'format': 'hex.preference-withdrawals.v1', 'events': []}
        return {**body, 'digest': digest(body)}
    data = read_json(sidecar)
    if (not isinstance(data, dict) or data.get('format') != 'hex.preference-withdrawals.v1' or
            data.get('digest') != digest({k: v for k, v in data.items() if k != 'digest'})):
        raise AgentError('偏好撤回账本摘要无效；不再采用旧偏好')
    if not isinstance(data.get('events'), list) or any(not isinstance(row, dict) for row in data['events']):
        raise AgentError('偏好撤回事件结构无效；不再采用旧偏好')
    records = {row.id: row for row in ledger.records}
    seen = set()
    for event in data['events']:
        record_id = event.get('record_id')
        if not isinstance(record_id, str):
            raise AgentError('撤回事件缺少原偏好ID')
        if (record_id in seen or record_id not in records or
                event.get('record_digest') != digest(records[record_id].model_dump()) or
                event.get('origin') != 'explicit_user_preference_withdrawal' or
                any(not isinstance(event.get(key), str) or not event[key].strip()
                    for key in ('user_quote', 'source', 'recorded_at'))):
            raise AgentError('撤回事件须唯一绑定原偏好记录与用户原话/来源')
        seen.add(record_id)
    return data


def active_records(path: Path) -> list[PersonalCalibration]:
    """Recheck withdrawal state on every use, including saved recall snapshots."""
    ledger = load_ledger(path)
    withdrawn = {event['record_id'] for event in _withdrawals(path, ledger)['events']}
    return [row for row in ledger.records if row.id not in withdrawn]


def withdraw(path: Path, record_id: str, user_quote: str, source: str) -> dict:
    """Append an explicit withdrawal; never erase original images or feedback."""
    if not user_quote.strip() or not source.strip():
        raise AgentError('撤回须有用户明确原话和来源；不能从新评分推断撤回')
    ledger = load_ledger(path)
    item = next((row for row in ledger.records if row.id == record_id), None)
    if item is None:
        raise AgentError('撤回ID不存在，不猜测或批量撤回其他偏好')
    data = _withdrawals(path, ledger)
    existing = next((event for event in data['events'] if event['record_id'] == record_id), None)
    if existing is None:
        existing = {'record_id': record_id, 'record_digest': digest(item.model_dump()),
                    'recorded_at': now(), 'user_quote': user_quote.strip(), 'source': source.strip(),
                    'origin': 'explicit_user_preference_withdrawal'}
        body = {'format': data['format'], 'events': [*data['events'], existing]}
        write_json(_withdrawal_path(path), {**body, 'digest': digest(body)})
    return {'status': 'withdrawn', 'id': record_id, 'event': existing,
            'withdrawals': str(_withdrawal_path(path).resolve()),
            'original_record_preserved': True, 'production_recall_enabled': False}


def record(
    path: Path,
    *,
    domain: str,
    criterion: str,
    image_a: Path,
    image_b: Path,
    choice: str,
    user_quote: str,
    rejected_reasons: list[CalibrationRejection],
    task_id: str | None = None,
    method_ids: list[str] | None = None,
    grammar_ids: list[str] | None = None,
) -> dict:
    """Record one explicit A/B preference and return its binding evidence."""
    if choice not in {"A", "B", "tie", "uncertain"}:
        raise AgentError("choice 必须是 A、B、tie 或 uncertain")
    first, second = _image(image_a, "A"), _image(image_b, "B")
    item = PersonalCalibration(
        format="liu.personal-calibration.v1",
        version=1,
        id=_next_id([]),
        recorded_at=now(),
        domain=domain,
        criterion=criterion.strip(),
        images=[first, second],
        choice=choice,
        user_quote=user_quote.strip(),
        rejected_reasons=rejected_reasons,
        source="explicit_user_pairwise_feedback",
        task_id=task_id.strip() if task_id and task_id.strip() else None,
        method_ids=[x.strip() for x in (method_ids or []) if x.strip()],
        grammar_ids=[x.strip() for x in (grammar_ids or []) if x.strip()],
    )
    ledger = load_ledger(path)
    ledger = _ledger([*ledger.records, item])
    write_json(path, ledger.model_dump())
    return {
        "status": "recorded",
        "id": item.id,
        "ledger": str(path.resolve()),
        "domain": item.domain,
        "choice": item.choice,
        "task_id": item.task_id,
        "method_ids": item.method_ids,
        "grammar_ids": item.grammar_ids,
        "image_sha256": {"A": first.sha256, "B": second.sha256},
        "notice": "仅保存用户明确的这一次成对选择；没有从机器复查推断长期偏好。",
    }


def verify(path: Path) -> dict:
    """Verify ledger integrity and current bytes for every referenced image."""
    ledger = load_ledger(path)
    withdrawals = _withdrawals(path, ledger)
    checked, missing, changed = 0, [], []
    for record in ledger.records:
        for image in record.images:
            checked += 1
            file = Path(image.path)
            if not file.is_file():
                missing.append(image.path)
                continue
            current = sha(file.read_bytes())
            if current != image.sha256:
                changed.append({"path": image.path, "expected": image.sha256, "actual": current})
    status = "passed" if not missing and not changed else "stopped_not_verified"
    return {"status": status, "records": len(ledger.records), "withdrawn_records": len(withdrawals['events']), "images_checked": checked,
            "missing": missing, "changed": changed, "ledger": str(path.resolve())}


def summary(path: Path) -> dict:
    """Return transparent counts only; no score or inferred taste profile."""
    ledger = load_ledger(path)
    active = active_records(path)
    by_domain = Counter(record.domain for record in ledger.records)
    by_choice = Counter(record.choice for record in ledger.records)
    return {
        "status": "observed_records_only",
        "records": len(ledger.records),
        "active_records": len(active),
        "withdrawn_records": len(ledger.records) - len(active),
        "counts_scope": "historical_records_including_withdrawn",
        "by_domain": dict(sorted(by_domain.items())),
        "by_choice": dict(sorted(by_choice.items())),
        "bound_records": sum(bool(record.task_id or record.method_ids or record.grammar_ids) for record in ledger.records),
        "notice": "统计描述记录覆盖，不代表审美评分、模型真值或自动生成偏好。",
    }


def parse_rejection(values: list[str]) -> list[CalibrationRejection]:
    """Parse repeated CLI values of the form A=reason or B=reason."""
    parsed = []
    for value in values:
        if "=" not in value:
            raise AgentError("--rejected-reason 格式应为 A=原因 或 B=原因")
        image, reason = value.split("=", 1)
        image, reason = image.strip(), reason.strip()
        try:
            parsed.append(CalibrationRejection(image=image, reason=reason))
        except Exception as exc:
            raise AgentError("--rejected-reason 只能指定 A/B，并填写非空原因") from exc
    return parsed
