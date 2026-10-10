"""Explicit offline probability adapter; never controls acceptance or generation.

Only mock and saved-response adapters are supplied. There is no HTTP client,
credential lookup, automatic retry, default evaluator replacement or image edit.
"""
from __future__ import annotations
import argparse
import base64
from copy import deepcopy
import json
import math
from pathlib import Path
from typing import Protocol
from .io import AgentError, digest, read_json, sha


def _seal(body):
    return {**body, 'digest': digest(body)}


def _verify(bundle):
    if bundle.get('digest') != digest({k:v for k,v in bundle.items() if k != 'digest'}):
        raise AgentError('Visual gate bundle digest is invalid')
    for source in bundle['source_files']:
        if sha(Path(source['file']).read_bytes()) != source['sha256']:
            raise AgentError('Visual gate source changed; rebuild the isolated input')
    if bundle.get('format') == 'hex.visual-decision-input.v1':
        from .intent_review import visual_input
        original = visual_input(Path(bundle['run']), bundle['request_id'])
        expected, _ = _content(original['input'])
        request = bundle['request']
        questions = [{'type':'predicate','name':'goal_supported','instructions':bundle['rubric']['predicate']}]
        if (original['digest'] != bundle['visual_input_digest'] or
                original['candidate_sha256'] != bundle['candidate_sha256'] or
                read_json(Path(bundle['source_files'][0]['file'])) != bundle['rubric'] or
                bundle['rubric_digest'] != digest(bundle['rubric']) or
                bundle['request_digest'] != digest(request) or request['questions'] != questions or
                request['input'] != [{'role':'user','content':expected}]):
            raise AgentError('Derived visual request binding differs from the original task/rubric/pixels')


def _content(visual_payload):
    from .generation_spec import image_facts
    payload = deepcopy(visual_payload)
    images = payload.pop('images_in_order')
    content = [{'type':'input_text','text':json.dumps(payload,ensure_ascii=False)}]
    sources = []
    for image in images:
        path = Path(image['file']); raw = path.read_bytes()
        if sha(raw) != image['sha256']:
            raise AgentError('Visual gate image changed')
        facts = image_facts(raw)
        mime = {'png':'image/png','jpeg':'image/jpeg','webp':'image/webp'}[facts['output_format']]
        content.append({'type':'input_text','text':json.dumps({k:v for k,v in image.items() if k!='file'})})
        content.append({'type':'input_image','image_url':
                        'data:'+mime+';base64,'+base64.b64encode(raw).decode('ascii')})
        sources.append({'file':str(path),'sha256':sha(raw)})
    return content, sources


def prepare(run: Path, index: Path, asset_id: str, rubric_file: Path, *, model: str,
            request_id: str | None = None) -> dict:
    from .intent_review import visual_input
    from .visual_evidence_store import inspect
    run, rubric_file = Path(run).resolve(), Path(rubric_file).resolve()
    rubric = read_json(rubric_file)
    if (set(rubric) != {'version','domain','predicate'} or
            any(not isinstance(v,str) or not v.strip() for v in rubric.values()) or not model.strip()):
        raise AgentError('Visual rubric needs explicit version, domain and predicate')
    asset = inspect(Path(index), asset_id)
    visual = visual_input(run, request_id)
    if (asset['role'] != 'candidate' or asset['source_sha256'] != visual['candidate_sha256']
            or asset['identity'].get('request_id') != visual['request_id']):
        raise AgentError('Visual evidence belongs to a different candidate')
    content, image_sources = _content(visual['input'])
    sources = [{'file':str(rubric_file),'sha256':sha(rubric_file.read_bytes())},
               {'file':str(Path(index).resolve()),'sha256':sha(Path(index).read_bytes())},
               *asset['source_evidence'], *image_sources]
    request = {'model':model, 'input':[{'role':'user','content':content}],
               'questions':[{'type':'predicate','name':'goal_supported','instructions':rubric['predicate']}]}
    bundle = _seal({'format':'hex.visual-decision-input.v1','run':str(run),
                    'request_id':visual['request_id'],'candidate_sha256':visual['candidate_sha256'],
                    'visual_input_digest':visual['digest'], 'rubric':rubric,'rubric_digest':digest(rubric),
                    'request':request,'request_digest':digest(request),'source_files':sources,
                    'enabled_by_default':False,'uploads_authorized':False,'api_calls':0})
    if len(json.dumps(bundle,ensure_ascii=False).encode('utf-8')) > 4*1024*1024:
        raise AgentError('Gate input exceeds the current 4 MiB saved-envelope limit; no upload performed')
    _verify(bundle)
    return bundle


class DecisionAdapter(Protocol):
    authority: str
    def evaluate(self, request: dict, request_digest: str) -> dict: ...


class MockDecisionAdapter:
    authority = 'synthetic_fixture'
    def __init__(self, response=None, *, error=None):
        self.response, self.error, self.calls = response, error, 0
    def evaluate(self, request, request_digest):
        self.calls += 1
        if self.error: raise self.error
        return deepcopy(self.response)


class SavedDecisionAdapter:
    authority = 'saved_response_not_live_qualified'
    def __init__(self, path: Path): self.path = Path(path)
    def evaluate(self, request, request_digest):
        saved = read_json(self.path)
        if saved.get('request_digest') != request_digest:
            raise AgentError('Saved Decisions response binding does not match this request')
        return deepcopy(saved['response'])


def _probability(value):
    if type(value) not in (int,float) or not math.isfinite(value) or not 0 <= value <= 1:
        raise AgentError('Expected a finite predicate probability in [0,1]')
    return float(value)


def calibrate(samples_file: Path) -> dict:
    """Fit an abstention band; exercise thresholds, not real-world calibration."""
    samples_file = Path(samples_file).resolve(); data = read_json(samples_file)
    rows = data['samples']
    if not rows or any(r['split'] not in ('fit','heldout') or type(r['label']) is not bool
                       or not r['source_family'] for r in rows):
        raise AgentError('Calibration needs labelled fit and heldout samples')
    for r in rows: _probability(r['probability'])
    if len({r['candidate_sha256'] for r in rows}) != len(rows):
        raise AgentError('Candidate duplicates cannot become independent calibration samples')
    fit = [r for r in rows if r['split']=='fit']; heldout = [r for r in rows if r['split']=='heldout']
    if not fit or not heldout or {r['source_family'] for r in fit} & {r['source_family'] for r in heldout}:
        raise AgentError('Source family cannot cross fit/heldout splits')
    if {r['label'] for r in fit} != {True,False}:
        raise AgentError('Fit samples need both positive and negative labels')
    negative_max = max(r['probability'] for r in fit if not r['label'])
    positive_min = min(r['probability'] for r in fit if r['label'])
    if negative_max >= positive_min:
        low, high = 0.0, 1.0
    else:
        low, high = negative_max, positive_min
    def metrics(samples):
        decided = [r for r in samples if r['probability'] <= low or r['probability'] >= high]
        return {'count':len(samples), 'decided':len(decided),
                'errors':sum((r['probability'] >= high) != r['label'] for r in decided),
                'brier':sum((r['probability']-int(r['label']))**2 for r in samples)/len(samples)}
    return _seal({'format':'hex.visual-gate-thresholds.v1','model':data['model'],
                  'rubric_digest':data['rubric_digest'],'domain':data['domain'],
                  'thresholds':{'low':low,'high':high}, 'fit':metrics(fit),'heldout':metrics(heldout),
                  'source_groups':len({r['source_family'] for r in rows}),
                  'label_authority':data['label_authority'],
                  'status':'synthetic_threshold_exercise' if data['label_authority']=='synthetic_fixture'
                            else 'unverified_labels_threshold_exercise',
                  'qualified':False, 'probability_calibrated':False,
                  'source_files':[{'file':str(samples_file),'sha256':sha(samples_file.read_bytes())}],
                  'notice':'Empirical offline thresholds do not establish reliable visual probabilities.'})


def evaluate(bundle: dict, adapter: DecisionAdapter, *, enabled: bool = False,
             calibration: dict | None = None) -> dict:
    _verify(bundle)
    result = {'format':'hex.visual-decision-result.v1', 'decision':'abstain',
              'suggested_action':'human_review','status':'disabled','probability':None,
              'calibration_status':'not_calibrated', 'rubric_digest':bundle['rubric_digest'],
              'rubric_version':bundle['rubric']['version'],'request_digest':bundle['request_digest'],
              'evidence':{k:bundle[k] for k in ('request_id','candidate_sha256','visual_input_digest')},
              'api_calls':0,'generation_calls':0,'writes_to_run':0,'automatic_retry':False,
              'automatic_acceptance':False,'automatic_repair':False,'usage':None}
    if calibration:
        _verify(calibration)
        if (calibration['model'] != bundle['request']['model'] or
                calibration['rubric_digest'] != bundle['rubric_digest'] or
                calibration['domain'] != bundle['rubric']['domain'] or calibration['qualified'] is not False):
            raise AgentError('Threshold profile belongs to another model/rubric/domain or claims unsupported qualification')
        result.update(calibration_status=calibration['status'], thresholds=calibration['thresholds'],
                      calibration_digest=calibration['digest'])
    if not enabled: return _seal(result)
    if adapter.authority not in ('synthetic_fixture','saved_response_not_live_qualified'):
        raise AgentError('Only explicit offline adapters are enabled in this experiment')
    result.update(status='review_required',adapter_authority=adapter.authority)
    try:
        raw = adapter.evaluate(deepcopy(bundle['request']), bundle['request_digest'])
    except TimeoutError:
        _verify(bundle)
        return _seal({**result,'status':'outcome_unknown','notice':'Unknown call outcome; do not retry automatically.'})
    _verify(bundle)
    if not isinstance(raw,dict): return _seal({**result,'notice':'Missing Decisions response'})
    result.update(raw_response_digest=digest(raw),usage=raw.get('usage'),model=raw.get('model'))
    answers = raw.get('answers')
    if raw.get('model') != bundle['request']['model'] or not isinstance(answers,list) or len(answers)!=1:
        return _seal({**result,'notice':'Missing or mismatched model/question answers'})
    answer = answers[0]
    if not isinstance(answer,dict) or answer.get('type')!='predicate' or answer.get('name')!='goal_supported':
        return _seal({**result,'notice':'Refusal, unsupported answer type or question mismatch'})
    try: result['probability'] = _probability(answer.get('probability'))
    except AgentError: return _seal({**result,'notice':'Invalid predicate probability'})
    return _seal({**result,'notice':'Unqualified probability; advisory evidence only, no visual acceptance.'})


def main(argv=None):
    p=argparse.ArgumentParser(description=__doc__)
    sub=p.add_subparsers(dest='action',required=True)
    prep=sub.add_parser('prepare')
    for name in ('run','index','rubric','out'): prep.add_argument('--'+name,type=Path,required=True)
    prep.add_argument('--asset-id',required=True);prep.add_argument('--model',required=True);prep.add_argument('--request-id')
    fit=sub.add_parser('calibrate');fit.add_argument('--samples',type=Path,required=True);fit.add_argument('--out',type=Path,required=True)
    read=sub.add_parser('replay');read.add_argument('--bundle',type=Path,required=True);read.add_argument('--response',type=Path,required=True)
    for item in sub.choices.values():
        item.add_argument('--format',choices=['json','human'],default='json')
    args=p.parse_args(argv)
    try:
        from .io import write_json
        if args.action=='prepare':
            data=prepare(args.run,args.index,args.asset_id,args.rubric,model=args.model,request_id=args.request_id)
        elif args.action=='calibrate': data=calibrate(args.samples)
        else:
            data=evaluate(read_json(args.bundle),SavedDecisionAdapter(args.response),enabled=True)
            if args.format=='human':
                print('视觉判断：需人工复查；概率尚未获得真实视觉校准。')
                print('状态：'+data['status']+'；来源：'+data.get('adapter_authority','unknown'))
                print('没有自动验收、改图或重试。')
            else: print(json.dumps(data,ensure_ascii=False))
            return 0
        destination=args.out.resolve()
        if ((args.action=='prepare' and destination.is_relative_to(args.run.resolve())) or
                any(destination==Path(s['file']).resolve() for s in data['source_files'])):
            raise AgentError('Derived gate input must stay outside the original task/source files')
        if destination.exists():
            if read_json(destination)!=data: raise AgentError('Derived output is occupied; choose an empty path')
        else:
            destination.parent.mkdir(parents=True,exist_ok=True)
            with destination.open('x',encoding='utf-8') as handle:
                json.dump(data,handle,ensure_ascii=False,indent=2)
        print(json.dumps({'out':str(args.out),'digest':data['digest'],'api_calls':0,'new_image_authorization':False}))
        return 0
    except (AgentError,OSError,ValueError,KeyError) as exc:
        print(str(exc));return 2


if __name__=='__main__': raise SystemExit(main())
