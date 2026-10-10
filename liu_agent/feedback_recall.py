"""Bounded local recall of ordinary user acceptance, without inventing preferences."""
from pathlib import Path

from .feedback import verified_feedback
from .io import AgentError, read_json, digest, within
from .retrieval import tokens


# Request boilerplate alone says nothing about a shared subject or expression.
_GENERIC_TERMS = set(tokens('一张 一幅 一个 一只 一些 帮我 为我 做一 生成 制作 设计 图片 图像 画面 作品 主体 产品 道具 视觉 希望 想要 需要 可以 用于 展示')) | {
    'a', 'an', 'the', 'of', 'for', 'to', 'and', 'with', 'image', 'picture', 'make', 'create', 'generate', 'product', 'design',
}


def _context_terms(need: dict) -> set[str]:
    context = ' '.join([need['intent'], *need.get('desired_feeling', []),
                        *(n['query'] for n in need.get('knowledge_needs', []) if n['kind'] != 'task')])
    return set(tokens(context)) - _GENERIC_TERMS


def recall_runs(roots: list[str], need: dict, *, limit: int = 2) -> dict:
    query_tokens = _context_terms(need)
    matches, skipped, seen, scanned = [], [], set(), 0
    for directory in roots:
        root = Path(directory).resolve()
        if not root.is_dir():
            skipped.append({'source': str(root), 'reason': 'feedback_root_unavailable'})
            continue
        # This reads only configured project roots, never a machine-wide history.
        # Limit the scan as well as the returned context; disclose partial scans.
        candidates = root.rglob('user-feedback.json')
        for receipt in candidates:
            run = receipt.parent.resolve()
            if not run.is_relative_to(root) or str(run) in seen:
                continue
            seen.add(str(run))
            scanned += 1
            if scanned > 200:
                return _result(matches, skipped, limit, scanned-1, True)
            try:
                summary = read_json(run/'result.json')
                previous = summary['need']
                if previous['domain'] != need['domain']:
                    continue
                # Match the earlier task, not a generic “looks good” verdict.
                matched = sorted(query_tokens & _context_terms(previous))
                if not matched:
                    continue
                packet, trial, feedback = verified_feedback(run)
                # The result's relevance fields must belong to its verified packet.
                if previous != packet['need']:
                    raise AgentError('反馈结果中的需求与原任务包不一致')
                design = trial['solution']['design']
                binding = design.get('aesthetic_binding') or {}
                record = {
                    'id': 'feedback.' + digest(feedback)[:20], 'domain': previous['domain'],
                    'task_id': str(run), 'intent': previous['intent'], 'scope': packet['brief']['scope'],
                    'decision': feedback['decision'], 'user_quote': feedback['user_quote'],
                    'source': feedback['source'], 'recorded_at': feedback['recorded_at'],
                    'candidate': {'path': str(within(run, trial['file'])), 'sha256': trial['sha256']},
                    'packet_digest': feedback['packet_digest'], 'contract_digest': feedback['contract_digest'],
                    'card_ids': binding.get('selected_card_ids', []),
                    'interpretation_boundary': '整图接受或否决，不证明每项技法的因果，不自动升级长期偏好',
                }
                matches.append({'kind': 'single_image', 'record': record, 'matched_terms': matched,
                                'scope_note': '只供相关同题材任务参考；用户原话优先，视觉原因未确认。'})
            except (AgentError, OSError, ValueError, KeyError, TypeError) as exc:
                skipped.append({'source': str(run), 'reason': 'feedback_evidence_invalid', 'detail': str(exc)[:200]})
    return _result(matches, skipped, limit, scanned, False)


def _result(matches, skipped, limit, scanned, truncated):
    matches.sort(key=lambda row: (len(row['matched_terms']), row['record']['recorded_at']), reverse=True)
    return {'records': matches[:limit], 'skipped': skipped, 'scanned_runs': scanned,
            'scan_truncated': truncated, 'images_submitted': False}
