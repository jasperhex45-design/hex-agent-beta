"""Isolated visual retrieval experiment. Production defaults are unchanged."""
from __future__ import annotations

import argparse
import json
import math
from pathlib import Path
import time

from . import visual_experience as experience
from .adapters.embeddinggemma2_visual import EmbeddingGemma2Visual
from .io import AgentError, digest, read_json, sha, within
from .retrieval import tokens

FORMAT = 'hex.visual-retrieval-trial.v1'
NOTICE = ('Similarity is retrieval evidence, not aesthetic quality or user acceptance. '
          'Open the actual image and record what to borrow, what to avoid, and which design decision changes. '
          'Zero adoption is allowed; saved hits must pass visual_experience.validate_recall before use.')


def _eligible(library: Path, *, domain: str | None, task_relation: str | None,
              problem_type: str | None, mode: str) -> tuple[list[dict], dict]:
    if mode not in ('creation', 'diagnostic'):
        raise AgentError('visual_trial_mode_invalid')
    data = experience._load(library)
    withdrawn = {row['card_id'] for row in data['withdrawals']}
    records, counts = [], {'withdrawn': 0, 'heldout': 0, 'outcome': 0, 'scope': 0}
    groups = experience._groups(data['cards'])
    for card in data['cards']:
        spec, context = card['spec'], card['context']
        reason = ('withdrawn' if card['id'] in withdrawn else 'heldout' if spec['split'] != 'development'
                  else 'outcome' if mode == 'creation' and context['outcome'] not in ('user_accepted', 'positive_only')
                  else 'scope' if ((domain is not None and context['domain'] != domain)
                                   or (task_relation is not None and spec['task_relation'] != task_relation)
                                   or (problem_type is not None and spec['problem_type'] != problem_type)) else None)
        if reason:
            counts[reason] += 1
            continue
        # Reuse existing image, feedback and contract checks before encoding;
        # stale evidence aborts this trial rather than silently disappearing.
        experience.inspect(library, card['id'], require_active=True)
        asset = card['result_image']['asset']
        records.append({'id': card['id'], 'card': card, 'source_group': groups[card['id']],
                        'image': asset['source_path'], 'image_sha256': asset['source_sha256'],
                        'text': json.dumps({'spec': spec, 'domain': context['domain'],
                                            'feedback': context['feedback']}, ensure_ascii=False)})
    return records, counts


def _rank(records: list[dict], scores: list[float], limit: int) -> list[tuple[dict, float]]:
    if len(records) != len(scores) or any(not math.isfinite(value) for value in scores):
        raise AgentError('visual_trial_scores_invalid')
    ranked, groups = [], set()
    for record, score in sorted(zip(records, scores), key=lambda row: (-row[1], row[0]['id'])):
        if record['source_group'] in groups:
            continue
        groups.add(record['source_group'])
        ranked.append((record, score))
        if len(ranked) == limit:
            break
    return ranked


def search(library: Path, query: str, backend: EmbeddingGemma2Visual, *, domain: str | None = None,
           task_relation: str | None = None, problem_type: str | None = None,
           mode: str = 'creation', limit: int = 3) -> dict:
    if type(limit) is not int or not 1 <= limit <= 3:
        raise AgentError('visual_trial_limit_invalid: 1 to 3')
    records, excluded = _eligible(library, domain=domain, task_relation=task_relation,
                                  problem_type=problem_type, mode=mode)
    if len(records) > 96:
        raise AgentError('visual_trial_corpus_limit: select a narrower explicit scope')
    started = time.perf_counter()
    vector = backend.query(query)  # Even an empty library must not claim model success without inference.
    embeddings = backend.documents(records) if records else []
    ranked = _rank(records, [sum(a * b for a, b in zip(vector, row)) for row in embeddings], limit)
    hits = []
    for record, score in ranked:
        card = record['card']
        hit = {'id': card['id'], 'card_digest': card['digest'], 'cosine_similarity': score,
               'source_family': card['spec']['source_family'], 'outcome': card['context']['outcome'],
               'accepted_template': card['context']['accepted_template'], 'purpose': card['spec']['purpose'],
               'applicability': card['spec']['applicability'], 'preference_scope': card['spec']['preference_scope'],
               'result_asset_id': card['result_image']['asset_id']}
        experience.validate_recall(library, hit)  # Withdrawal or feedback changes during inference also block use.
        hits.append(hit)
    return {'format': FORMAT, 'query': query, 'mode': mode, 'hits': hits,
            'method': 'opt_in_embeddinggemma2_text_image_source_diverse',
            'backend': backend.identity, 'excluded': excluded, 'candidate_count': len(records),
            'elapsed_seconds': time.perf_counter() - started, 'production_recall_enabled': False,
            'fallback_used': False, 'notice': NOTICE}


def load_benchmark(path: Path) -> dict:
    data = read_json(path)
    if (not isinstance(data, dict) or data.get('format') != 'hex.visual-retrieval-benchmark.v1'
            or data.get('digest') != digest({key: value for key, value in data.items() if key != 'digest'})
            or data.get('authority') != 'authored_synthetic_retrieval_smoke_not_visual_quality'
            or not isinstance(data.get('records'), list) or not 1 <= len(data['records']) <= 96
            or not isinstance(data.get('queries'), list) or not data['queries']):
        raise AgentError('visual_trial_benchmark_invalid_or_changed')
    records = {}
    image_groups = {}
    for row in data['records']:
        if row['id'] in records or row['split'] != 'development' or not row['source_group']:
            raise AgentError('visual_trial_benchmark_corpus_identity_invalid')
        image = within(path.parent, row['image'])
        if not image.is_file() or sha(image.read_bytes()) != row['image_sha256']:
            raise AgentError('visual_trial_benchmark_image_missing_or_changed')
        prior = image_groups.setdefault(row['image_sha256'], row['source_group'])
        if prior != row['source_group']:
            raise AgentError('visual_trial_benchmark_source_group_leakage')
        records[row['id']] = row
    query_ids = set()
    groups = {row['source_group'] for row in records.values()}
    for row in data['queries']:
        if row['id'] in query_ids or row['split'] != 'heldout' or row['source_group'] in groups:
            raise AgentError('visual_trial_benchmark_query_source_leakage')
        query_ids.add(row['id'])
        if (not row['text'].strip() or not row['relevance']
                or any(ident not in records or type(grade) is not int or grade not in (0, 1, 2)
                       for ident, grade in row['relevance'].items())
                or not any(grade > 0 for grade in row['relevance'].values())):
            raise AgentError('visual_trial_benchmark_labels_invalid')
    return data


def _metrics(ranked: list[str], relevance: dict, k: int = 3) -> dict:
    positives = {ident for ident, grade in relevance.items() if grade > 0}
    retrieved = ranked[:k]
    recall = len(positives & set(retrieved)) / len(positives)
    dcg = sum((2 ** relevance.get(ident, 0) - 1) / math.log2(position + 2)
              for position, ident in enumerate(retrieved))
    ideal = sum((2 ** grade - 1) / math.log2(position + 2)
                for position, grade in enumerate(sorted(relevance.values(), reverse=True)[:k]))
    reciprocal = next((1 / (position + 1) for position, ident in enumerate(retrieved) if ident in positives), 0)
    return {'recall_at_3': recall, 'ndcg_at_3': dcg / ideal, 'mrr_at_3': reciprocal}


def evaluate(benchmark: Path, backend: EmbeddingGemma2Visual | None = None) -> dict:
    data = load_benchmark(benchmark)
    records = [{**row, 'image': str(within(benchmark.parent, row['image']))} for row in data['records']]
    routes = [('metadata_overlap', None)]
    if backend is not None:
        routes += [('embeddinggemma2_text_only', False), ('embeddinggemma2_text_image', True)]
    reports = {}
    before = dict(backend.inference_counts) if isinstance(backend, EmbeddingGemma2Visual) else None
    for route, images in routes:
        started = time.perf_counter()
        embeddings = backend.documents(records, images=images) if images is not None else None
        rows = []
        for query in data['queries']:
            if embeddings is None:
                terms = set(tokens(query['text']))
                scores = [len(terms & set(tokens(record['text']))) for record in records]
                # Match the existing lexical baseline: no positive overlap means zero hits.
                active = [(record, score) for record, score in zip(records, scores) if score > 0]
                ranked = _rank([row[0] for row in active], [row[1] for row in active], 3)
            else:
                vector = backend.query(query['text'])
                ranked = _rank(records, [sum(a * b for a, b in zip(vector, row)) for row in embeddings], 3)
            ids = [record['id'] for record, _ in ranked]
            rows.append({'query_id': query['id'], 'ranked_ids': ids, 'metrics': _metrics(ids, query['relevance'])})
        reports[route] = {'queries': rows, 'mean_metrics': {
            name: sum(row['metrics'][name] for row in rows) / len(rows)
            for name in ('recall_at_3', 'ndcg_at_3', 'mrr_at_3')},
            'elapsed_seconds': time.perf_counter() - started}
    counts = ({key: backend.inference_counts[key] - value for key, value in before.items()}
              if before is not None else None)
    actual_model = (isinstance(backend, EmbeddingGemma2Visual)
                    and backend.identity.get('execution_authority') == 'local_embeddinggemma2_runtime'
                    and counts['text_queries'] == len(data['queries']) * 2
                    and counts['text_documents'] == len(records)
                    and counts['text_image_documents'] == len(records))
    return {'format': 'hex.visual-retrieval-evaluation.v1', 'benchmark_digest': data['digest'],
            'benchmark_authority': data['authority'], 'routes': reports,
            'true_model_executed': actual_model, 'backend': backend.identity if backend else None,
            'completed_inference_counts': counts,
            'status': ('synthetic_retrieval_comparison_only' if actual_model else
                       'synthetic_test_double' if backend else 'baseline_only_model_not_executed'),
            'production_recall_enabled': False, 'aesthetic_gain_verified': False,
            'notice': NOTICE + ' Synthetic labels and scenes only test retrieval mechanics; real-case validation is pending.'}


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest='command', required=True)
    probe = commands.add_parser('probe')
    probe.add_argument('--checkpoint', type=Path, required=True)
    find = commands.add_parser('search')
    find.add_argument('--checkpoint', type=Path, required=True)
    find.add_argument('--library', type=Path, required=True)
    find.add_argument('--query', required=True)
    find.add_argument('--domain')
    find.add_argument('--task-relation')
    find.add_argument('--problem-type')
    find.add_argument('--mode', choices=['creation', 'diagnostic'], default='creation')
    find.add_argument('--limit', type=int, default=3)
    evaluation = commands.add_parser('evaluate')
    evaluation.add_argument('--benchmark', type=Path, required=True)
    evaluation.add_argument('--checkpoint', type=Path)
    evaluation.add_argument('--baseline-only', action='store_true')
    for command in (probe, find, evaluation):
        command.add_argument('--without-prompts', action='store_true')
    args = parser.parse_args(argv)
    backend = None
    try:
        if args.command == 'evaluate':
            if bool(args.checkpoint) == bool(args.baseline_only):
                raise AgentError('visual_trial_evaluate_select_route: require checkpoint OR --baseline-only')
            backend = EmbeddingGemma2Visual(args.checkpoint, use_prompts=not args.without_prompts) if args.checkpoint else None
            result = evaluate(args.benchmark, backend)
        else:
            backend = EmbeddingGemma2Visual(args.checkpoint, use_prompts=not args.without_prompts)
            if args.command == 'search':
                result = search(args.library, args.query, backend, domain=args.domain,
                                task_relation=args.task_relation, problem_type=args.problem_type,
                                mode=args.mode, limit=args.limit)
            else:
                # Exercise both image and text paths on one authored local image.
                from tempfile import TemporaryDirectory
                from PIL import Image
                with TemporaryDirectory(prefix='hex-visual-probe-') as folder:
                    image = Path(folder) / 'synthetic.png'
                    Image.new('RGB', (32, 32), (240, 230, 120)).save(image)
                    backend.documents([{'text': 'SYNTHETIC yellow square', 'image': str(image),
                                        'image_sha256': sha(image.read_bytes())}])
                    backend.query('yellow square')
                result = {'format': FORMAT, 'status': 'text_image_runtime_verified', 'backend': backend.identity,
                          'completed_inference_counts': backend.inference_counts,
                          'aesthetic_gain_verified': False, 'production_recall_enabled': False}
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0
    except (AgentError, ValueError, OSError, KeyError, TypeError) as exc:
        print(json.dumps({'format': FORMAT, 'status': 'blocked', 'error': str(exc),
                          'true_model_executed': None if backend is not None else False,
                          'true_model_completed': False, 'fallback_used': False,
                          'completed_inference_counts': getattr(backend, 'inference_counts', None),
                          'production_recall_enabled': False}, ensure_ascii=False, indent=2))
        return 2


if __name__ == '__main__':
    raise SystemExit(main())
