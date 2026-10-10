"""Verify the installed CPU model and its actual Hex Need integration.

Authored installation examples validate integration, not creative image quality.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import subprocess
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from liu_agent import knowledge_context, workflow
from liu_agent.contracts import Need
from liu_agent.io import ROOT, now, read_json, write_json
from liu_agent.adapters.native_retriever import NativeRetriever
from liu_agent.native_runtime import knowledge_python
from liu_agent.retrieval import Retriever, load_cards


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--out', type=Path, required=True)
    args = parser.parse_args()
    out = args.out.resolve()
    if out.exists():
        parser.error('Choose a fresh evidence directory; existing tasks are preserved.')
    python = knowledge_python(ROOT)
    if not python.is_file():
        parser.error('Native embedding runtime is missing for this host; run native_install.py first.')
    out.mkdir(parents=True)
    probe_process = subprocess.run([str(python), '-I', str(Path(__file__).with_name('native_worker.py'))],
                                   input=b'{"action":"semantic_probe"}', capture_output=True,
                                   timeout=30, check=True, creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
    probe = json.loads(probe_process.stdout)
    assert probe['paraphrase_above_unrelated'] and probe['dimensions'] == 512
    cards = load_cards(ROOT / 'knowledge')
    query = Retriever(cards, backend=NativeRetriever({}, cards)).explain_search(
        '车辆烤漆表层的高光和反射', 'vehicle', 'create', kind='material', limit=6)
    assert query['backend'] == 'native' and query['degraded'] is False
    assert query['hits'][0]['card']['id'] == 'material.paint'
    write_json(out / 'semantic-probe.json', probe)
    write_json(out / 'card-query.json', query)

    brief = read_json(ROOT / 'examples/demo-brief.json')
    brief['targets'] = ['image']
    brief['title'] = '原生 CPU 知识安装验证（作者示例）'
    write_json(out / 'brief.json', brief)
    write_json(out / 'sources.json', {'format': 'liu.input-sources.v1', 'messages': [{
        'id': 'authored-demo', 'actor': 'user',
        'text': '\n'.join(['作者编写的安装验证，非真实用户创作请求，无生成许可。',
                           brief['request'], *brief['must_keep'], *brief['avoid']]),
        'origin': 'host_transcription', 'locator': 'scripts/runtime/native_verify.py', 'in_reply_to': ''}]})
    config = knowledge_context.normalize_config({'backend': 'native'})
    write_json(out / 'knowledge-config.json', config)
    run = out / 'run'
    packet = workflow.make_packet(out / 'brief.json', run, ROOT / 'knowledge', out / 'state',
                                  ROOT / 'examples/demo-need.json', sources_file=out / 'sources.json',
                                  execution_profile='knowledge-assisted-v1', knowledge_config=config)
    result = packet['retrieval']
    assert result['method'] == 'native_with_local_fallback' and result['degraded'] is False
    assert len(result['calls']) == 3 and all(c['status'] == 'succeeded' for c in result['calls'])
    assert all(c['network_call_count'] == 0 for c in result['calls'])
    assert 'material.glass' in {row['card']['id'] for row in result['hits']}
    assert workflow.design_input(packet)['retrieval'] == result
    assert workflow.read_packet(run)['retrieval'] == result
    refreshed = workflow.refresh_packet(packet, run, ROOT / 'knowledge', out / 'state',
                                         Need.model_validate(packet['need']), 'host_supplied')
    assert refreshed['retrieval'] == result
    assert len(read_json(run / 'knowledge-retrieval-usage.json')['calls']) == 3
    packages = json.loads(subprocess.check_output([str(python), '-m', 'pip', 'list', '--format=json'],
                                                 text=True, encoding='utf-8'))
    assert not {'torch', 'transformers', 'sentence-transformers'} & {p['name'].lower() for p in packages}
    runtime_bytes = sum(p.stat().st_size for p in (ROOT / '.runtime/knowledge').rglob('*') if p.is_file())
    receipt = {'format': 'hex.native-installation-verification.v1', 'created_at': now(),
               'origin': 'authored_installation_examples_not_visual_or_user_acceptance',
               'model': read_json(Path(__file__).with_name('native_model_manifest.json')),
               'actual_embedding_inference': True, 'indexed_cards': len(cards),
               'platform': sys.platform, 'python': str(python),
               'semantic_probe': probe, 'task_retrieved_ids': [h['card']['id'] for h in result['hits']],
               'task_calls': result['calls'], 'same_need_refresh_reused': True,
               'network_calls': 0, 'generation_calls': 0, 'ports': [], 'daemon_required': False,
               'runtime_bytes': runtime_bytes, 'installed_packages': packages,
               'limits': result['limits'], 'run': str(run),
               'visual_quality': 'not_tested', 'user_acceptance': 'not_recorded'}
    write_json(out / 'verification.json', receipt)
    print(json.dumps({'verified': True, 'indexed_cards': len(cards),
                      'runtime_bytes': runtime_bytes, 'network_calls': 0,
                      'task_retrieved_ids': receipt['task_retrieved_ids'],
                      'receipt': str(out / 'verification.json')}, ensure_ascii=False))


if __name__ == '__main__':
    main()
