"""Explicit offline comparison preparation; no generator, judge or permission grant."""
from copy import deepcopy
from pathlib import Path

from .io import AgentError, digest, read_json, sha, write_json
from .harness import _input_hashes


def _host_request(run, request_id=None):
    from .host import read_dispatches
    rows = read_dispatches(run)
    if not rows:
        if request_id:
            raise AgentError('No frozen request matches the supplied ID')
        return None
    if request_id is None and len(rows) != 1:
        raise AgentError('Select an explicit request ID when there are multiple attempts')
    matches = [r for r in rows if request_id is None or r['request']['id'] == request_id]
    if len(matches) != 1:
        raise AgentError('Request ID must match exactly one frozen host request')
    from .harness import case_record
    case_record(run, matches[0]['request']['id'])  # reuse lineage/budget/evidence checks
    return matches[0]['request']


def semantic_trace(run: Path, dominant_choice_id: str, request_id=None) -> dict:
    """Host names the primary decision; code traces literal evidence, never meaning."""
    from .workflow import read_packet, compile_texts, validate_solution
    from .contracts import Solution
    run = Path(run).resolve()
    before = _input_hashes(run)
    packet = read_packet(run)
    request = _host_request(run, request_id)
    raw = request['solution'] if request else read_json(run / 'solution.json')
    solution = Solution.model_validate(raw)
    if solution.packet_digest != packet['digest']:
        raise AgentError('The traced solution belongs to a different packet')
    choice = next((c for c in solution.design.choices if c.id == dominant_choice_id), None)
    if choice is None:
        raise AgentError('Primary choice must be an existing Plan choice ID')
    validate_solution(packet, solution)
    compiled = compile_texts(packet, solution.spec, solution.compilation, design=solution.design)
    prompts = []
    for row in solution.compilation.prompts:
        text = compiled[row.target]['text']
        coverage = next((c for c in row.adaptation.choice_coverage if c.choice_id == choice.id), None) if row.adaptation else None
        translation = next((c for c in row.choice_translations if c.choice_id == choice.id), None)
        excerpt = coverage.prompt_excerpt if coverage else None
        if not excerpt and translation:
            excerpt = translation.text
        if not excerpt and choice.action in text:
            excerpt = choice.action
        actual = request['prompt'] if request and request['target'] == row.target else None
        prompts.append({'target': row.target, 'compiled_text': text, 'compiled_sha256': sha(text.encode()),
                        'primary_excerpt': excerpt, 'literal_excerpt_present': bool(excerpt and excerpt in text),
                        'frozen_prompt': actual, 'frozen_sha256': sha(actual.encode()) if actual else None,
                        'excerpt_in_frozen_prompt': bool(excerpt and actual and excerpt in actual),
                        'selected_direction_translation': row.direction_translation.model_dump() if row.direction_translation else None})
    result = {'format': 'hex.semantic-trace.v1', 'source_run': str(run), 'source_sha256': before,
              'packet_digest': packet['digest'], 'request_id': request['id'] if request else None,
              'original_user_messages': [m for m in packet.get('input_sources', {}).get('messages', []) if m.get('actor') == 'user'],
              'original_brief': packet['brief'], 'host_interpretation': packet.get('need'),
              'dominant_choice': {**choice.model_dump(), 'authority': 'host_selected_plan_choice_not_user_endorsement'},
              'plan_expression': solution.design.expression, 'reading_order': solution.design.reading_order,
              'selected_direction_id': raw['design'].get('direction_id'),
              'selected_direction_source': raw['design'].get('creative_directions'),
              'primary_evaluation_check': next((c.model_dump() for c in solution.design.checks if c.id == choice.criterion_id), None),
              'references': packet.get('references', []), 'primary_reference_uses': choice.model_dump()['reference_uses'],
              'actual_generation_inputs': request.get('generation_inputs', []) if request else [],
              'research': packet.get('research'), 'prompts': prompts,
              'semantic_equivalence': 'not_verified_by_code', 'new_visual_quality': 'not_tested', 'generation_calls': 0}
    if _input_hashes(run) != before:
        raise AgentError('Source evidence changed while tracing; retry from a stable task')
    return {**result, 'digest': digest(result)}


def _mask_prompt(value, prompt):
    if isinstance(value, dict):
        return {k: _mask_prompt(v, prompt) for k, v in value.items()}
    if isinstance(value, list):
        return [_mask_prompt(v, prompt) for v in value]
    return {'arm_prompt_slot': True} if isinstance(value, str) and value == prompt else value


def _save_manifest(out, body):
    result = {**body, 'digest': digest(body)}
    write_json(out / 'manifest.json', result)
    return result


def prepare(run: Path, out: Path, dominant_choice_id: str, request_id=None) -> dict:
    run, out = Path(run).resolve(), Path(out).resolve()
    if out == run or run in out.parents or (out.exists() and any(out.iterdir())):
        raise AgentError('Comparison needs an empty output directory outside the source task')
    trace = semantic_trace(run, dominant_choice_id, request_id)
    request = _host_request(run, trace['request_id'])
    if request is None:
        raise AgentError('Prepare requires an existing verified frozen request as the conditions source')
    # Materials are equal. Native receives no HEX solution or inferred Need.
    common = {'original_brief': trace['original_brief'], 'original_user_messages': trace['original_user_messages'],
              'references': trace['references'], 'provided_research_materials': trace['research'],
              'historical_generation_grants': 'evidence_only_not_new_authorization'}
    conditions = {'target': request['target'], 'tool': request['tool'],
                  'generation_spec': request.get('generation_spec'), 'capability_snapshot': request.get('capability_snapshot'),
                  'arguments_template': _mask_prompt(request['arguments'], request['prompt']),
                  'generation_inputs': request.get('generation_inputs', []),
                  'images_in_order': request.get('images_in_order', []),
                  'reference_usage': request.get('reference_usage', []),
                  'source_request_id': request['id'], 'actual_vendor_model': 'not_available'}
    shared, conditions_digest = digest(common), digest(conditions)
    inputs = {'shared_input_digest': shared, 'conditions_digest': conditions_digest, 'materials': common, 'conditions': conditions}
    native = {**inputs, 'arm': 'native', 'instruction': 'Use the same host model and available image skills to author an independent prompt. Do not copy the HEX prompt.'}
    hex_input = {**inputs, 'arm': 'hex', 'solution': request['solution'], 'semantic_trace': trace,
                 'instruction': 'Review the original intent and primary composition, then author the HEX prompt using the existing runtime. Literal coverage alone does not prove preserved meaning.'}
    if _input_hashes(run) != trace['source_sha256']:
        raise AgentError('Source changed before comparison preparation')
    out.mkdir(parents=True, exist_ok=True)
    for name, value in [('native-input.json', native), ('hex-input.json', hex_input), ('shared-input.json', common), ('conditions.json', conditions), ('semantic-trace.json', trace)]:
        write_json(out / name, value)
    body = {'format': 'hex.minimal-comparison.v1', 'state': 'prepared_no_image_authorization',
            'source_run': str(run), 'source_sha256': trace['source_sha256'],
            'shared_input_digest': shared, 'conditions_digest': conditions_digest,
            'input_sha256': {p.name: sha(p.read_bytes()) for p in out.glob('*.json')},
            'native_baseline': {'available_image_skills_allowed': True, 'normal_reasoning_allowed': True},
            'authoring_isolation': 'not_verified', 'authoring_model': 'not_yet_declared',
            'budget': {'planned_images_per_arm': 1, 'planned_total_images': 2, 'authorization': 'not_granted',
                       'automatic_retries': 0, 'estimated_cost': None},
            'generation_calls': 0, 'quality_result': 'not_tested'}
    return _save_manifest(out, body)


def check(out: Path) -> dict:
    out = Path(out).resolve()
    m = read_json(out / 'manifest.json')
    if m.get('digest') != digest({k: v for k, v in m.items() if k != 'digest'}):
        raise AgentError('Comparison manifest digest changed')
    for name, expected in m['input_sha256'].items():
        if sha((out / name).read_bytes()) != expected:
            raise AgentError('Prepared comparison input changed: ' + name)
    if digest(read_json(out / 'shared-input.json')) != m['shared_input_digest'] or digest(read_json(out / 'conditions.json')) != m['conditions_digest']:
        raise AgentError('Comparison conditions or materials changed')
    if _input_hashes(Path(m['source_run'])) != m['source_sha256']:
        raise AgentError('Source task changed after preparation')
    for arm, row in m.get('drafts', {}).items():
        if sha(Path(row['path']).read_bytes()) != row['sha256']:
            raise AgentError('Frozen draft changed: ' + arm)
    return m


def freeze(out: Path, native_draft: Path, hex_draft: Path) -> dict:
    out = Path(out).resolve()
    m = check(out)
    if m['state'] != 'prepared_no_image_authorization':
        raise AgentError('Comparison drafts are already frozen')
    rows, model = {}, None
    for arm, path in [('native', native_draft), ('hex', hex_draft)]:
        path = Path(path).resolve()
        raw = path.read_bytes()
        import json
        d = json.loads(raw)
        if d.get('arm') != arm or any(d.get(k) != m[k] for k in ['shared_input_digest', 'conditions_digest']):
            raise AgentError('Draft must bind its arm and the same materials/conditions')
        prompt = d.get('prompt')
        if not isinstance(prompt, str) or not prompt.strip() or len(prompt) > 18000:
            raise AgentError('Draft prompt must contain 1..18000 characters')
        author = d.get('authoring_model')
        if not isinstance(author, str) or not author.strip() or author in ('unknown', 'not_available'):
            raise AgentError('Declare the actual authoring model; an unknown model cannot establish equal conditions')
        if model is not None and author != model:
            raise AgentError('Both draft authors must declare the same host model')
        if d.get('origin') not in ('synthetic_fixture', 'host_transcription_not_tool_authenticated'):
            raise AgentError('Declare synthetic or host-transcribed evidence; no unauthenticated tool claim')
        model = author
        rows[arm] = {'path': str(path), 'sha256': sha(raw), 'prompt_sha256': sha(prompt.encode()), 'origin': d['origin']}
    check(out)
    for arm, row in rows.items():
        if sha(Path(row['path']).read_bytes()) != row['sha256']:
            raise AgentError('Draft changed during freezing: ' + arm)
    body = {k: deepcopy(v) for k, v in m.items() if k != 'digest'}
    body.update(state='drafts_frozen_no_image_authorization', drafts=rows, authoring_model=model,
                authoring_model_authority='draft_author_declaration_not_platform_verified')
    return _save_manifest(out, body)


def main():
    import argparse
    import json
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('action', choices=['trace', 'prepare', 'freeze', 'check'])
    p.add_argument('--run', type=Path)
    p.add_argument('--out', type=Path, required=True)
    p.add_argument('--dominant-choice')
    p.add_argument('--request-id')
    p.add_argument('--native-draft', type=Path)
    p.add_argument('--hex-draft', type=Path)
    args = p.parse_args()
    if args.action in ('trace', 'prepare') and (not args.run or not args.dominant_choice):
        p.error('trace/prepare require --run and --dominant-choice')
    if args.action == 'trace':
        result = semantic_trace(args.run, args.dominant_choice, args.request_id)
        write_json(args.out, result)
    elif args.action == 'prepare':
        result = prepare(args.run, args.out, args.dominant_choice, args.request_id)
    elif args.action == 'freeze':
        if not args.native_draft or not args.hex_draft:
            p.error('freeze requires both draft files')
        result = freeze(args.out, args.native_draft, args.hex_draft)
    else:
        result = check(args.out)
    print(json.dumps({'state': result.get('state'), 'quality_result': result.get('quality_result', result.get('new_visual_quality')),
                      'generation_calls': result['generation_calls'], 'digest': result['digest']}, ensure_ascii=False))


if __name__ == '__main__':
    main()
