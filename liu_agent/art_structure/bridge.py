"""Explicit adapter around the native pipeline; not a second agent or monkey patch.

The opt-in CLI supplies this provider to the existing workflow.run_cloud.
Host mode projects ArtPlan into the native Solution and calls host.preflight.
"""
from __future__ import annotations
from pathlib import Path
from ..contracts import Compilation, Plan, Solution
from ..io import AgentError, read_json, write_json, digest, sha, within
from .models import ArtPlan
from .catalog import load_catalog, context_for, DEFAULT_CATALOG
from .core import (DESIGN_GUIDE, projection, compile_actions, store_record,
                   record_for_design, frozen_spatial_contract)

class ArtProvider:
    def __init__(self, delegate, run: Path, *, catalog: Path = DEFAULT_CATALOG,
                 query: str = '', feedback_root: Path | None = None):
        self.delegate, self.run, self.query = delegate, Path(run), query
        self.catalog = Path(catalog).expanduser().resolve()
        self.cases = load_catalog(self.catalog)
        self.context, self.current, self.first_checks = None, None, None
        self._authorized_generation = None
        self.feedback_root = Path(feedback_root).expanduser().resolve() if feedback_root is not None else None
        if feedback_root is not None and not Path(feedback_root).expanduser().is_dir():
            raise AgentError('反馈根目录不存在；在任何模型调用前停止')

    def recovery_config(self) -> dict:
        """Preserve explicitly chosen adapter inputs for a later process."""
        from .catalog import DEFAULT_METHOD_MAP, DEFAULT_GRAMMAR_CATALOG
        fingerprints = {}
        for path in (self.catalog, DEFAULT_METHOD_MAP, DEFAULT_GRAMMAR_CATALOG):
            fingerprints[str(path.resolve())] = sha(path.read_bytes())
        feedback = []
        if self.feedback_root is not None:
            roots = sorted((p for p in self.feedback_root.iterdir() if p.is_dir() and not p.is_symlink()),
                           key=lambda p: p.name, reverse=True)[:100]
            for root in roots:
                if root.resolve() == self.run.resolve():
                    continue
                for name in ('user-feedback.json', 'packet.json', 'result.json', 'acceptance.json',
                             'image-tests.json', 'host-image-tests.json', 'host-dispatches.json', 'selection.json'):
                    path = root/name
                    if path.is_file():
                        feedback.append({'file': str(path.resolve()), 'sha256': sha(path.read_bytes())})
        return {'kind': 'art_structure', 'catalog': str(self.catalog), 'query': self.query,
                'feedback_root': str(self.feedback_root) if self.feedback_root is not None else None,
                'source_fingerprints': fingerprints, 'design_guide_digest': digest(DESIGN_GUIDE),
                'feedback_evidence_digest': digest(feedback)}

    def __getattr__(self, name):
        # Native budget, model IDs, route logging and research implementation
        # remain owned by the original provider.
        return getattr(self.delegate, name)

    def structured(self, instruction, payload, output_type, images=None, phase='execution'):
        from ..native_budget import guard_api
        guard_api(self.run)
        if phase in ('design', 'revise') and output_type is Plan:
            if self.context is None:
                self.context = context_for(payload, self.cases, self.query)
            evidence = []
            if self.feedback_root is not None:
                method_ids = {h['case']['method_id'] for h in self.context['hits']}
                evidence = [r for r in recall(self.feedback_root, payload['need']['domain'])['records']
                            if method_ids.intersection(r['method_ids'])]
            enriched = {**payload, 'art_structure_context': self.context,
                        'previous_spatial_plan': self.current,
                        'related_user_method_evidence': evidence}
            art = self.delegate.structured(instruction + DESIGN_GUIDE, enriched,
                                           ArtPlan, images, phase=phase)
            plan, record = projection(art, self.context, payload)
            fingerprint = frozen_spatial_contract(record)
            if phase == 'revise' and fingerprint != self.first_checks:
                raise AgentError('艺术修订不得换方法/空间模式或降低冻结检查；新方向另建任务')
            if phase == 'design':
                self.first_checks = fingerprint
            self.current = record
            store_record(self.run, record)
            write_json(within(self.run, 'art-structure/context.json'), self.context)
            write_json(within(self.run, 'art-structure/adoption.json'), {
                'status': 'adopted' if record else 'not_adopted',
                'reason': art.not_used_reason, 'plan_digest': record['digest'] if record else None})
            return plan
        if phase == 'compile' and output_type is Compilation:
            enriched = {**payload, 'spatial_actions': self.current}
            guide = '\n按当前空间动作编译；不自动把艺术家/作品名作为风格词。保留锁定项。'
            result = self.delegate.structured(instruction + guide, enriched,
                                              output_type, images, phase=phase)
            return compile_actions(result, self.current, payload['model_profiles'],
                                   append_missing='model_adaptation' not in payload)
        if phase in ('evaluate', 'compare'):
            instruction += ('\n按已冻结检查观察空间关系，不以像不像名画作结论。'
                            '平面/多视点创作不因缺摄影景深而自动失败；身份与编辑硬约束仍优先。'
                            '艺术方法实现与用户是否喜欢分开；不声称原创性或实测视线路径。')
        return self.delegate.structured(instruction, payload, output_type, images, phase=phase)

    def authorize_generation(self, packet: dict, contract: dict, prompt: str,
                             images: list[bytes], generation_inputs: list[dict]) -> None:
        """Bind the adapter to run_cloud's native preflight and input ledger."""
        if self.current and packet.get('model_adaptation_version') != 1:
            # New native compilation verifies bound choice excerpts. Requiring
            # the original action verbatim would reject model-specific prose.
            decisions = self.current['spatial_plan']['decisions']
            if not all(d['action_zh'] in prompt or d['action_en'] in prompt for d in decisions):
                raise AgentError('实际生成提示词丢失空间动作，停止而非事后补记录')
        if len(images) != len(generation_inputs) or [sha(x) for x in images] != [r['sha256'] for r in generation_inputs]:
            raise AgentError('艺术结构适配器收到的图片与原生generation_inputs不一致')
        signature = digest({
            'packet_digest': packet.get('digest'), 'contract_digest': contract.get('digest'),
            'prompt': prompt, 'generation_inputs': generation_inputs,
        })
        self._authorized_generation = {
            'signature': signature, 'prompt': prompt,
            'image_hashes': [sha(x) for x in images],
        }

    def generate(self, prompt: str, images: list[bytes], spec=None) -> bytes:
        from ..native_budget import guard_api
        guard_api(self.run)
        token = self._authorized_generation
        if token is None:
            raise AgentError('艺术结构适配器只能在原生run_cloud完成输入清单和验收门后生图')
        if token['prompt'] != prompt or token['image_hashes'] != [sha(x) for x in images]:
            self._authorized_generation = None
            raise AgentError('艺术结构适配器的实际生成调用与原生授权快照不一致')
        # The token is single-use; the surrounding workflow records the actual
        # request and remains responsible for budgets, rights and receipts.
        self._authorized_generation = None
        return self.delegate.generate(prompt, images, spec=spec) if spec is not None else self.delegate.generate(prompt, images)

    def research(self, *args, **kwargs):
        from ..native_budget import guard_api
        guard_api(self.run)
        return self.delegate.research(*args, **kwargs)


def prepare_host(run: Path, *, catalog: Path = DEFAULT_CATALOG, query: str = '') -> dict:
    from ..workflow import read_packet
    packet = read_packet(run)
    context = context_for(packet, load_catalog(catalog), query)
    data = {'packet_digest': packet['digest'], 'context': context}
    write_json(within(run, 'art-structure/host-context.json'), {**data, 'digest': digest(data)})
    write_json(within(run, 'art-structure/art-plan.schema.json'), ArtPlan.model_json_schema())
    return {'hits': len(context['hits']), 'packet_digest': packet['digest'],
            'next': '由同一个宿主完成ArtPlan及Compilation；执行art_structure adopt后走原dispatch/Eval流程。'}


def adopt_host(run: Path, art_file: Path, compilation_file: Path) -> dict:
    from ..workflow import read_packet
    from ..host import preflight
    packet = read_packet(run)
    envelope = read_json(within(run, 'art-structure/host-context.json'))
    body = {k:v for k,v in envelope.items() if k != 'digest'}
    if envelope.get('digest') != digest(body) or envelope['packet_digest'] != packet['digest']:
        raise AgentError('艺术检索上下文对应旧任务或已改变；重新prepare')
    art = ArtPlan.model_validate(read_json(art_file))
    plan, record = projection(art, envelope['context'], packet)
    if (run/'acceptance.json').exists() and (run/'solution.json').exists():
        previous = record_for_design(run, read_json(run/'solution.json')['design'])
        if frozen_spatial_contract(previous) != frozen_spatial_contract(record):
            raise AgentError('宿主艺术修订不得换方法/空间模式或冻结检查；新方向另建任务')
    compiled = compile_actions(Compilation.model_validate(read_json(compilation_file)),
                               record, packet['model_profiles'],
                               append_missing=packet.get('model_adaptation_version') != 1)
    solution = Solution(packet_digest=packet['digest'], spec=plan.spec,
                        design=plan.design, compilation=compiled)
    store_record(run, record)
    candidate = within(run, 'art-structure/proposed-solution.json')
    write_json(candidate, solution.model_dump())
    # Uses native scope/reference/budget/frozen-contract gates, without bypass.
    result = preflight(run, candidate)
    return {**result, 'spatial_plan_digest': record['digest'] if record else None,
            'next': '沿用原dispatch→record-result→prepare-eval→record-test→finalize。'}


def selected_record(run: Path) -> dict | None:
    result = read_json(within(run, 'result.json'))
    trial = next((x for x in result.get('trials', [])
                  if x['round'] == result.get('selected_trial_round')), None)
    design = trial['solution']['design'] if trial else result['design']
    return record_for_design(run, design)


def recall(root: Path, domain: str, limit: int = 3) -> dict:
    """Read bounded, explicit feedback evidence; no implicit permanent tastes.

    Only immediate task folders under a caller-specified root are examined.
    Edited, missing, stale, or wrong-domain records are excluded with reasons.
    """
    if type(limit) is not int or not 1 <= limit <= 3:
        raise AgentError('反馈回查最多3条')
    root = root.expanduser().resolve()
    if not root.is_dir():
        raise AgentError('反馈根目录不存在')
    records, skipped = [], []
    candidates = sorted((p for p in root.iterdir() if p.is_dir() and not p.is_symlink()),
                        key=lambda p: p.name, reverse=True)[:100]
    for run in candidates:
        if len(records) >= limit:
            break
        if not (run/'user-feedback.json').is_file() or not (run/'result.json').is_file():
            continue
        try:
            result = read_json(within(run, 'result.json'))
            packet = read_json(within(run, 'packet.json'))
            if result['need'] != packet['need']:
                raise AgentError('结果需求与任务包不一致，拒绝跨题材反馈污染')
            if result['need']['domain'] != domain:
                continue
            receipt = read_json(within(run, 'user-feedback.json'))
            feedback = receipt['feedback']
            if receipt['digest'] != digest(feedback):
                raise AgentError('反馈摘要不匹配')
            trial = next((t for t in result.get('trials', [])
                          if t['round'] == result.get('selected_trial_round')), None)
            if not trial or feedback['candidate_sha256'] != trial['sha256']:
                raise AgentError('反馈不属于当前选中候选')
            if (feedback['packet_digest'] != result['packet_digest'] or
                    feedback['contract_digest'] != result['acceptance']['digest']):
                raise AgentError('反馈对应旧任务或标准')
            if (feedback['decision'] not in ('accepted_by_user', 'rejected_by_user')
                    or not feedback['user_quote'].strip() or not feedback.get('source', '').strip()
                    or feedback.get('origin') != 'explicit_user_feedback_host_reported'):
                raise AgentError('缺少用户明确反馈')
            packet_body = {k:v for k,v in packet.items() if k != 'digest'}
            if packet.get('digest') != result['packet_digest'] or digest(packet_body) != packet['digest']:
                raise AgentError('反馈任务包已改变')
            contract = read_json(within(run, 'acceptance.json'))
            contract_body = {k:v for k,v in contract.items() if k != 'digest'}
            if contract != result['acceptance'] or digest(contract_body) != contract['digest']:
                raise AgentError('反馈标准已改变')
            authoritative = next((path for path in (run/'image-tests.json', run/'host-image-tests.json')
                                  if path.is_file()), None)
            if result.get('format') == 'liu.aesthetic.result.v3':
                if authoritative is None:
                    raise AgentError('正式结果缺少authoritative image-tests/host-image-tests记录')
                if read_json(authoritative) != result.get('trials', []):
                    raise AgentError('result试图与authoritative试图记录不一致')
                from ..workflow import validate_trial
                validate_trial(packet, run, contract, trial)
            if sha(within(run, trial['file']).read_bytes()) != trial['sha256']:
                raise AgentError('候选图片已改变')
            record = record_for_design(run, trial['solution']['design'])
            if record is None:
                continue
            records.append({'domain': domain, 'source_run': run.name,
                            'candidate_sha256': trial['sha256'], 'decision': feedback['decision'],
                            'user_quote': feedback['user_quote'][:1000],
                            'method_ids': [c['method_id'] for c in record['source_cases']],
                            'actions': [d['action_zh'] for d in record['spatial_plan']['decisions']],
                            'machine_diagnosis': (trial.get('review') or {}).get('diagnosis', 'unknown'),
                            'scope': 'one_task_evidence_not_permanent_preference'})
        except (AgentError, OSError, KeyError, ValueError) as exc:
            skipped.append({'run': run.name, 'reason': str(exc)[:200]})
    return {'records': records, 'skipped': skipped, 'scan_limit': 100,
            'notice': '同领域的明确反馈仅作可回查证据；不证明方法优劣，不自动更新长期偏好。'}
