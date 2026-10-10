"""Run from the existing project root: python -m liu_agent.art_structure --help."""
from __future__ import annotations
import argparse
import json
from pathlib import Path
from pydantic import ValidationError
from ..io import ROOT, AgentError, load_env
from .catalog import DEFAULT_CATALOG, load_catalog, load_method_map, retrieve
from .models import ArtPlan
from .bridge import ArtProvider, prepare_host, adopt_host, selected_record, recall

def main(argv=None) -> int:
    p = argparse.ArgumentParser(description='中西绘画结构转译实验入口；不是新的Agent/评分器')
    subs = p.add_subparsers(dest='command', required=True)
    v = subs.add_parser('validate'); v.add_argument('--catalog', type=Path, default=DEFAULT_CATALOG)
    s = subs.add_parser('search'); s.add_argument('--query', required=True)
    s.add_argument('--catalog', type=Path, default=DEFAULT_CATALOG)
    s.add_argument('--scope', choices=['create','edit','scene','color_only','relight'], default='create')
    s.add_argument('--domain', choices=['person','illustration','vehicle','poster','character','prop','hybrid'])
    s = subs.add_parser('prepare'); s.add_argument('--run', type=Path, required=True)
    s.add_argument('--query', default=''); s.add_argument('--catalog', type=Path, default=DEFAULT_CATALOG)
    s = subs.add_parser('adopt'); s.add_argument('--run', type=Path, required=True)
    s.add_argument('--art-plan', type=Path, required=True); s.add_argument('--compilation', type=Path, required=True)
    s = subs.add_parser('inspect'); s.add_argument('--run', type=Path, required=True)
    s = subs.add_parser('recall'); s.add_argument('--runs-root', type=Path, required=True)
    s.add_argument('--domain', choices=['person','illustration','vehicle','poster','character','prop'], required=True)
    s = subs.add_parser('run'); s.add_argument('--brief', type=Path, required=True)
    s.add_argument('--sources', type=Path)
    s.add_argument('--out', type=Path, required=True); s.add_argument('--query', default='')
    s.add_argument('--allow-cloud', action='store_true'); s.add_argument('--image-tests', type=int, choices=[0,1,2], default=0)
    s.add_argument('--max-calls', type=int, default=14); s.add_argument('--feedback-root', type=Path)
    s.add_argument('--no-style-context', dest='style_context_enabled', action='store_false', default=None,
                   help='关闭本轮私人风格上下文；艺术结构检索仍按本次需求执行')
    s.add_argument('--allow-library-analysis', action='store_true',
                   help='允许将最多3张命中的图库原图提交分析；不自动作为生成参考')
    s.add_argument('--catalog', type=Path, default=DEFAULT_CATALOG)
    args = p.parse_args(argv)
    try:
        if args.command == 'validate':
            cases = load_catalog(args.catalog)
            method_map = load_method_map(cases=cases)
            result = {'catalog': str(args.catalog), 'cases': len(cases),
                      'method_map': len(method_map),
                      'mapping_validation': 'passed',
                      'output_schema': 'ArtPlan', 'schema_validation': 'passed',
                      'visual_validation': 'not_performed'}
        elif args.command == 'search':
            from ..workflow import ALLOWED_OPS
            cases = load_catalog(args.catalog)
            result = retrieve(cases, args.query, list(ALLOWED_OPS[args.scope]), scope=args.scope,
                              domain=args.domain, method_map=load_method_map(cases=cases))
        elif args.command == 'prepare':
            result = prepare_host(args.run, catalog=args.catalog, query=args.query)
        elif args.command == 'adopt':
            result = adopt_host(args.run, args.art_plan, args.compilation)
        elif args.command == 'inspect':
            result = {'selected_spatial_plan': selected_record(args.run)}
        elif args.command == 'recall':
            result = recall(args.runs_root, args.domain)
        else:
            if not args.allow_cloud:
                raise AgentError('独立API执行需要--allow-cloud；未读取.env，未调用服务')
            if not 1 <= args.max_calls <= 20:
                raise AgentError('--max-calls须为1至20')
            from ..provider import OpenAIProvider, Budget
            from ..workflow import run_cloud
            load_env(ROOT/'.env')
            delegate = OpenAIProvider(consent=True, budget=Budget(args.max_calls, args.image_tests))
            if args.image_tests and not delegate.image_model:
                raise AgentError('缺少实际图片模型ID，未调用服务')
            provider = ArtProvider(delegate, args.out, catalog=args.catalog,
                                   query=args.query, feedback_root=args.feedback_root)
            result = run_cloud(args.brief, args.out, ROOT/'knowledge', ROOT/'.state', provider,
                               image_tests=args.image_tests,
                               style_context_enabled=args.style_context_enabled,
                               allow_library_analysis=args.allow_library_analysis, sources_file=args.sources)
            result = {'run': str(args.out), 'status': result['status'],
                      'selected_spatial_plan': selected_record(args.out) if (args.out/'result.json').exists() else None}
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0
    except (AgentError, ValidationError, OSError, ValueError, KeyError) as exc:
        print(json.dumps({'status':'stopped', 'error': str(exc)}, ensure_ascii=False))
        return 2

if __name__ == '__main__':
    raise SystemExit(main())
