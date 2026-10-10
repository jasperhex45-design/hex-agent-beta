"""Local CPU semantic candidates via a short-lived, isolated ONNX process."""
from __future__ import annotations

import json
import math
import os
import subprocess
import time

from pydantic import BaseModel, ConfigDict, Field

from ..contracts import Card
from ..io import ROOT, AgentError, digest, read_json
from ..native_runtime import knowledge_python
from .ragflow_retriever import card_fingerprint


class NativeConfig(BaseModel):
    model_config = ConfigDict(extra='forbid', frozen=True)

    enabled: bool = True
    timeout_seconds: float = Field(default=30.0, gt=0, le=60)
    max_queries: int = Field(default=3, ge=1, le=3)
    max_hits: int = Field(default=6, ge=1, le=6)
    candidate_limit: int = Field(default=64, ge=1, le=256)


class NativeRetriever:
    provider = 'native'
    network_call_count = 0

    def __init__(self, config: dict | NativeConfig, cards: list[Card]):
        self.config = config if isinstance(config, NativeConfig) else NativeConfig.model_validate(config)
        self.query_count = 0
        self.cards = [{'card_id': c.id, 'card_version': c.version,
                       'card_sha256': card_fingerprint(c),
                       'text': '\n'.join([c.title, ' '.join(c.tags), c.when, *c.rules])}
                      for c in sorted(cards, key=lambda item: item.id)]

    def retrieve(self, query: str, limit: int = 8) -> dict:
        call = {'provider': self.provider, 'status': 'not_run',
                'network_call_count': 0, 'local_process_count': 0, 'retrieval_call_count': 0,
                'elapsed_ms': None, 'request_digest': digest({'query': query, 'config': self.config.model_dump()}),
                'cost': {'amount': 0, 'currency': None, 'status': 'local_no_api_charge'}}
        result = {'backend': self.provider, 'candidates': [], 'source_evidence': [], 'excluded': [], 'call': call}
        if not self.config.enabled:
            call['reason'] = 'disabled'
            return result
        if self.query_count >= self.config.max_queries:
            call['reason'] = 'query_limit'
            return result
        if not query.strip() or len(query) > 8000:
            call['reason'] = 'invalid_query'
            return result
        python = knowledge_python(ROOT)
        worker = ROOT / 'scripts/runtime/native_worker.py'
        if not python.is_file():
            call['reason'] = 'native_runtime_missing'
            return result
        if not worker.is_file():
            call['reason'] = 'native_worker_missing'
            return result
        try:
            manifest = read_json(ROOT / 'scripts/runtime/native_model_manifest.json')
            if (not isinstance(manifest, dict) or type(manifest['dimensions']) is not int
                    or manifest['dimensions'] <= 0
                    or not isinstance(manifest['base_model'], str)
                    or not isinstance(manifest['conversion_repository'], str)
                    or not isinstance(manifest['files'][0]['sha256'], str)):
                raise ValueError('manifest_shape')
        except (OSError, ValueError, KeyError, TypeError, IndexError, AgentError):
            call['reason'] = 'native_manifest_unavailable'
            return result
        self.query_count += 1
        started = time.monotonic()
        call.update(status='failed', local_process_count=1, retrieval_call_count=1)
        try:
            request = {'cards': self.cards, 'query': query, 'candidate_limit': self.config.candidate_limit}
            process = subprocess.run([str(python), '-I', str(worker)],
                                     input=json.dumps(request, ensure_ascii=False).encode('utf-8'),
                                     capture_output=True, timeout=self.config.timeout_seconds, cwd=ROOT,
                                     env={**os.environ, 'PYTHONUTF8': '1', 'PYTHONIOENCODING': 'utf-8',
                                          'TOKENIZERS_PARALLELISM': 'false', 'HF_HUB_OFFLINE': '1'},
                                     creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
            if process.returncode != 0:
                call['reason'] = 'native_runtime_error'
                return result
            if len(process.stdout) > 2_000_000:
                raise ValueError('response_size')
            payload = json.loads(process.stdout)
            if (payload.get('format') != 'hex.native-retrieval.v1'
                    or payload.get('catalog_digest') != digest(self.cards)
                    or payload.get('model_manifest_digest') != digest(manifest)
                    or payload.get('dimensions') != manifest['dimensions']
                    or payload.get('provider') != 'CPUExecutionProvider'
                    or payload.get('indexed_cards') != len(self.cards)):
                raise ValueError('response_identity')
            mappings = {row['card_id']: row for row in self.cards}
            seen = set()
            rows = payload['candidates']
            if not isinstance(rows, list) or len(rows) > self.config.candidate_limit:
                raise ValueError('response_candidates')
            for row in rows:
                local = mappings.get(row['card_id'])
                score = row['score']
                if (local is None or row['card_id'] in seen
                        or row['card_version'] != local['card_version']
                        or row['card_sha256'] != local['card_sha256']
                        or type(score) not in (int, float) or not math.isfinite(score)
                        or not -1.0001 <= score <= 1.0001):
                    raise ValueError('response_mapping')
                seen.add(row['card_id'])
                result['candidates'].append({**row, 'source': {
                    'provider': self.provider, 'card_id': row['card_id'],
                    'card_sha256': row['card_sha256'], 'index_digest': payload['index_digest'],
                    'model': manifest['base_model'], 'model_manifest_digest': digest(manifest)}})
            call.update(status='succeeded', model=manifest['base_model'],
                        conversion_repository=manifest['conversion_repository'],
                        model_manifest_digest=digest(manifest), model_sha256=manifest['files'][0]['sha256'],
                        dimensions=payload['dimensions'], indexed_cards=payload['indexed_cards'],
                        index_digest=payload['index_digest'], index_reused=payload['index_reused'],
                        execution_provider=payload['provider'], worker_elapsed_ms=payload['elapsed_ms'])
        except subprocess.TimeoutExpired:
            call['reason'] = 'native_timeout'
        except OSError:
            call['reason'] = 'native_launch_error'
            call['local_process_count'] = 0
        except (ValueError, KeyError, TypeError, AttributeError):
            call['reason'] = 'native_invalid_response'
        finally:
            call['elapsed_ms'] = round((time.monotonic() - started) * 1000, 3)
        if call['status'] != 'succeeded':
            result['candidates'] = []
        return result


def build_backend(config: dict | NativeConfig, cards: list[Card]) -> NativeRetriever | None:
    parsed = config if isinstance(config, NativeConfig) else NativeConfig.model_validate(config)
    return NativeRetriever(parsed, cards) if parsed.enabled else None
