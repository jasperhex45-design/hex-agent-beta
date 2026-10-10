"""Optional standalone GEPA adapter for compilation rules, never another runtime.

Verified against gepa-ai/gepa's optimize / GEPAAdapter protocol. Dependencies
and model callbacks are supplied explicitly. This module downloads nothing.
"""
from __future__ import annotations

from dataclasses import dataclass
from copy import deepcopy
import importlib.metadata
import importlib.util
import inspect
import math
from pathlib import Path

from liu_agent.brief_registry import _lock
from liu_agent.io import AgentError, digest, read_json, sha, write_json
from liu_agent.research_adapters.dataset import METRICS, verify_dataset
from liu_agent.research_adapters.method_optimization import COMPONENT, sealed, verify, verify_method

KINDS = ('task_model', 'reflection_model', 'visual_review')
_MISSING = object()


def dependency_status() -> dict:
    installed = importlib.util.find_spec('gepa') is not None
    return {'package': 'gepa', 'installed': installed,
            'version': importlib.metadata.version('gepa') if installed else None,
            'execution': 'not_run', 'install_performed': False,
            'official_project': 'https://github.com/gepa-ai/gepa', 'code_license': 'MIT'}


def _callback_identity(callback) -> dict:
    target = callback if inspect.isfunction(callback) or inspect.ismethod(callback) else type(callback)
    path = inspect.getsourcefile(target)
    if not path or not Path(path).is_file():
        raise AgentError('GEPA callbacks require a readable source module for reproducible resume')
    return {'module': target.__module__, 'qualname': target.__qualname__,
            'source_sha256': sha(Path(path).read_bytes()),
            'callable_source_sha256': sha(inspect.getsource(target).encode('utf-8'))}


class ResearchCalls:
    """Research-only model usage journal, separate from native image budgets.

    Reservations persist before callbacks; unknown outcomes block further calls.
    A fresh object resumes the same journal. It never creates image permissions.
    """
    def __init__(self, path: Path):
        self.path = Path(path).resolve()
        self.stop_file = self.path.with_suffix('.stop')

    @classmethod
    def create(cls, path: Path, *, limits: dict, max_cost: float, currency: str,
               authorization_source: Path, quote: str) -> 'ResearchCalls':
        """Caller supplies an actual user-source file and an exact authorization quote.

        The source is provenance, not parsed language authorization. The host must
        verify scope before this explicit operation, as for existing host receipts.
        """
        if set(limits) != set(KINDS) or any(type(v) is not int or v < 0 for v in limits.values()):
            raise AgentError('Declare separate nonnegative task/reflection/visual-review call caps')
        if not math.isfinite(max_cost) or max_cost < 0 or not currency.strip():
            raise AgentError('Declare a finite total cost cap and currency')
        source = Path(authorization_source).resolve()
        raw = source.read_bytes()
        if not quote.strip() or quote not in raw.decode('utf-8-sig'):
            raise AgentError('Research authorization quote is missing from its supplied source')
        journal = cls(path)
        with _lock(journal.path):
            if journal.path.exists():
                raise AgentError('Research usage already exists; resume it, never reset consumed calls')
            journal._save({'format': 'hex.research-calls.v1', 'limits': limits,
                           'max_cost': max_cost, 'currency': currency,
                           'authorization': {'path': str(source), 'sha256': sha(raw), 'quote': quote,
                                             'authority': 'host_verified_user_source_not_platform_authenticated'},
                           'calls': [], 'generation_allowed': False})
        return journal

    def _save(self, body):
        body = {k: v for k, v in body.items() if k != 'digest'}
        write_json(self.path, sealed(body))

    def read(self) -> dict:
        body = read_json(self.path)
        verify(body)
        source = body['authorization']
        if sha(Path(source['path']).read_bytes()) != source['sha256']:
            raise AgentError('Research authorization source changed')
        return body

    def stopped(self) -> bool:
        body = self.read()
        return self.stop_file.exists() or any(c['state'] != 'completed' for c in body['calls'])

    def invoke(self, kind: str, key: str, payload, callback, *, upper_cost: float):
        if kind not in KINDS:
            raise AgentError('Research adapter cannot dispatch images; use the native generation workflow')
        if not key or not math.isfinite(upper_cost) or upper_cost < 0:
            raise AgentError('Each model invocation needs an ID and a finite per-call cost bound')
        with _lock(self.path):
            body = self.read()
            if self.stop_file.exists() or any(c['state'] != 'completed' for c in body['calls']):
                raise AgentError('Research is stopped or has an unresolved call; query/reconcile, never resend')
            prior = next((c for c in body['calls'] if c['key'] == key), None)
            if prior:
                if prior['kind'] != kind or prior['payload_digest'] != digest(payload):
                    raise AgentError('Consumed research call ID cannot be rebound to different input')
                if not prior.get('result_available'):
                    raise AgentError('Original result is missing; query/reconcile it, never resend')
                return prior['result']
            used = sum(c['kind'] == kind for c in body['calls'])
            cost = sum(c['usage']['cost'] for c in body['calls'])
            if used >= body['limits'][kind] or cost + upper_cost > body['max_cost']:
                raise AgentError('Research model-call or total cost limit reached')
            body['calls'].append({'key': key, 'kind': kind, 'payload_digest': digest(payload),
                                  'upper_cost': upper_cost, 'state': 'pending', 'usage': None})
            self._save(body)
        try:
            reply = callback(payload)
            self.reconcile(key, reply['usage'], result=reply['result'])
            return reply['result']
        except BaseException:
            # Preserve reservation even when callback succeeded but receipt save failed.
            with _lock(self.path):
                body = self.read()
                row = next(c for c in body['calls'] if c['key'] == key)
                if row['state'] != 'completed':
                    row['state'] = 'outcome_unknown'
                    self._save(body)
            raise

    def reconcile(self, key: str, usage: dict, *, result=_MISSING) -> None:
        """Save actual usage returned by the original request/query, no call occurs."""
        with _lock(self.path):
            body = self.read()
            rows = [c for c in body['calls'] if c['key'] == key]
            if len(rows) != 1:
                raise AgentError('Only the original unresolved research call can be reconciled')
            row = rows[0]
            if row['state'] == 'completed' and (row.get('result_available') or row['usage'] != usage or result is _MISSING):
                raise AgentError('Completed usage is immutable; only its missing original result may be restored')
            if (not isinstance(usage, dict) or usage.get('currency') != body['currency']
                    or type(usage.get('cost')) not in (int, float) or not math.isfinite(usage['cost'])
                    or usage['cost'] < 0
                    or any(type(usage.get(k)) is not int or usage[k] < 0 for k in ('input_tokens', 'output_tokens'))
                    or not usage.get('request_id') or not usage.get('model')):
                raise AgentError('Actual model, original request, tokens and bounded cost receipt are required')
            row.update(state='completed', usage=dict(usage))
            row['result_available'] = result is not _MISSING
            if result is not _MISSING:
                row['result_digest'] = digest(result)
                row['result'] = result
            if usage['cost'] > row['upper_cost']:
                row['cost_bound_exceeded'] = True
                self.stop_file.write_text('Actual cost exceeded the declared per-call bound. Review original receipt before resuming.', encoding='utf-8')
            self._save(body)
            if row.get('cost_bound_exceeded'):
                raise AgentError('Actual research cost exceeded its bound; recorded actual usage and stopped')


@dataclass
class OfflineBatch:
    """Dependency-free structural batch used only to test the adapter protocol."""
    outputs: list
    scores: list[float]
    trajectories: list | None = None
    objective_scores: list | None = None


class CompilationAdapter:
    propose_new_texts = None

    def __init__(self, execute, *, calls: ResearchCalls, task_upper_cost: float,
                 allowed_case_ids: set[str], case_digests: dict | None = None):
        self.execute = execute
        self.calls = calls
        self.task_upper_cost = task_upper_cost
        self.allowed_case_ids = set(allowed_case_ids)
        self.case_digests = dict(case_digests or {})

    def evaluate(self, batch, candidate, capture_traces=False):
        if set(candidate) != {COMPONENT} or not isinstance(candidate[COMPONENT], str) or not candidate[COMPONENT].strip():
            raise AgentError('GEPA may only alter the compilation_rules text component')
        outputs, scores, trajectories, objectives = [], [], [], []
        for row in batch:
            if row['case_id'] not in self.allowed_case_ids or row['split'] == 'holdout':
                raise AgentError('Holdout or unknown cases cannot enter optimization')
            if self.case_digests and self.case_digests.get(row['case_id']) != digest(row):
                raise AgentError('Frozen optimization case changed')
            payload = {'method': candidate[COMPONENT], 'case': deepcopy(row)}
            result = self.calls.invoke('task_model', 'task-' + digest(payload), payload, self.execute,
                                       upper_cost=self.task_upper_cost)
            metrics = result.get('metrics', {})
            if (set(metrics) != set(METRICS)
                    or any(type(v) not in (int, float) or not math.isfinite(v) or v < 0 for v in metrics.values())
                    or type(result.get('score')) not in (int, float) or not 0 <= result['score'] <= 1
                    or not result.get('feedback') or not result.get('scope')):
                raise AgentError('Task evaluator must return six finite failure metrics, score, feedback and scope')
            outputs.append(result['output'])
            scores.append(result['score'])
            objectives.append({key: 1 / (1 + value) for key, value in metrics.items()})
            trajectories.append({'Inputs': {'case_id': row['case_id'],
                                             'input': row.get('synthetic_text', row.get('task_input'))},
                                 'Generated Outputs': result['output'], 'Feedback': result['feedback'],
                                 'scope': result['scope'], 'metrics': metrics})
        cls = OfflineBatch
        if importlib.util.find_spec('gepa'):
            from gepa.core.adapter import EvaluationBatch
            cls = EvaluationBatch
        return cls(outputs=outputs, scores=scores, trajectories=trajectories if capture_traces else None,
                   objective_scores=objectives)

    def make_reflective_dataset(self, candidate, eval_batch, components_to_update):
        if set(candidate) != {COMPONENT} or components_to_update != [COMPONENT] or eval_batch.trajectories is None:
            raise AgentError('Reflection requires traces for the one allowed method component')
        return {COMPONENT: eval_batch.trajectories}


def optimize(method: dict, dataset: dict, out: Path, *, calls: ResearchCalls | None = None,
             execute=None, reflect=None, max_metric_calls: int = 16,
             task_upper_cost: float = 0, reflection_upper_cost: float = 0,
             execution_identity: dict | None = None) -> dict:
    """Explicit research entry point. Missing authorization/services => not_run.

    execute and reflect return {result, usage}; reflect.result is str per
    GEPA's LanguageModel protocol. No model names trigger implicit API calls.
    """
    verify_method(method)
    verify_dataset(dataset)
    if type(max_metric_calls) is not int or max_metric_calls < 1:
        raise AgentError('A positive metric-call cap is required')
    if not all(dataset['counts'][split] for split in ('train', 'validation', 'holdout')):
        raise AgentError('Separate nonempty train, validation and holdout sets are required')
    status = dependency_status()
    missing = ([] if status['installed'] else ['gepa dependency'])
    if calls is None: missing.append('explicit research call authorization and cost journal')
    if execute is None: missing.append('bounded task compiler/evaluator callback')
    if reflect is None: missing.append('bounded reflection-model callback')
    identity_fields = ('host', 'task_model', 'reflection_model', 'evaluator_version')
    if not execution_identity or not all(execution_identity.get(key) for key in identity_fields):
        missing.append('host/task/reflection model and evaluator version identity')
    if missing:
        return {'status': 'not_run', 'missing': missing, 'dependency': status,
                'generation_calls': 0, 'model_calls': 0, 'production_enabled': False}
    import gepa
    expected = {'seed_candidate', 'trainset', 'valset', 'adapter', 'reflection_lm',
                'max_metric_calls', 'run_dir', 'stop_callbacks'}
    if not expected.issubset(inspect.signature(gepa.optimize).parameters):
        raise AgentError('Installed GEPA optimize API differs from the verified adapter contract')
    out = Path(out).resolve()
    for row in dataset['rows']:
        if row.get('run') and out.is_relative_to(Path(row['run']).resolve()):
            raise AgentError('GEPA research output cannot be inside a canonical source task')
    train = [r for r in dataset['rows'] if r['split'] == 'train']
    validation = [r for r in dataset['rows'] if r['split'] == 'validation']
    manifest = sealed({'format': 'hex.gepa-run.v1', 'candidate_digest': method['digest'],
                       'dataset_digest': dataset['digest'], 'gepa_version': status['version'],
                       'calls_path': str(calls.path), 'max_metric_calls': max_metric_calls,
                       'execution_identity': execution_identity,
                       'callbacks': {'execute': _callback_identity(execute), 'reflect': _callback_identity(reflect)},
                       'task_upper_cost': task_upper_cost, 'reflection_upper_cost': reflection_upper_cost,
                       'component': COMPONENT, 'holdout_given_to_optimizer': False})
    with _lock(out / 'run-manifest.json'):
        manifest_file = out / 'run-manifest.json'
        if manifest_file.exists() and read_json(manifest_file) != manifest:
            raise AgentError('Resume requires the same method, dataset, caps, journal and GEPA version')
        if not manifest_file.exists() and any(p.name != 'run-manifest.json.lock' for p in out.iterdir()):
            raise AgentError('Cannot adopt existing GEPA state without its matching run manifest')
        if calls.stopped():
            return {'status': 'stopped', 'reason': 'stop marker or unresolved research call',
                    'production_enabled': False}
        write_json(manifest_file, manifest)
        adapter = CompilationAdapter(execute, calls=calls, task_upper_cost=task_upper_cost,
                                     allowed_case_ids={r['case_id'] for r in train + validation},
                                     case_digests={r['case_id']: digest(r) for r in train + validation})
        def reflection_lm(messages):
            result = calls.invoke('reflection_model', 'reflection-' + digest(messages), messages, reflect,
                                  upper_cost=reflection_upper_cost)
            if not isinstance(result, str) or not result.strip():
                raise AgentError('GEPA reflection LanguageModel must return a nonempty str')
            return result
        result = gepa.optimize(seed_candidate={COMPONENT: method['text']}, trainset=train,
                               valset=validation, adapter=adapter, reflection_lm=reflection_lm,
                               max_metric_calls=max_metric_calls, run_dir=str(out / 'engine'),
                               stop_callbacks=lambda _state: calls.stopped(),
                               use_wandb=False, use_mlflow=False, seed=0)
        verify_dataset(dataset)
        report = sealed({'format': 'hex.gepa-result.v1', 'status': 'completed_offline_optimization',
                         'best_candidate': result.best_candidate, 'run_manifest_digest': manifest['digest'],
                         'calls_journal_digest': calls.read()['digest'], 'production_enabled': False,
                         'visual_quality': 'not_verified', 'holdout': 'not_run'})
        write_json(out / 'result.json', report)
        return report
