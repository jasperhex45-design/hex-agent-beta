"""Short-lived, offline ONNX worker. Input/output are bounded UTF-8 JSON on stdio.

This file intentionally uses only the isolated runtime's dependencies. No Hex
workflow imports, network clients, server, Torch, or automatic model downloads.
"""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import sys
import tempfile
import time

import numpy as np
import onnxruntime as ort
from tokenizers import Tokenizer

ROOT = Path(__file__).resolve().parents[2]
RUNTIME = ROOT / '.runtime/knowledge'
MANIFEST_PATH = Path(__file__).with_name('native_model_manifest.json')
FORMAT = 'hex.native-index.v1'


def digest(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True,
                                     separators=(',', ':')).encode('utf-8')).hexdigest()


def verify_model():
    manifest = json.loads(MANIFEST_PATH.read_text(encoding='utf-8'))
    for item in manifest['files']:
        path = RUNTIME / 'model' / item['file']
        with path.open('rb') as stream:
            actual = hashlib.file_digest(stream, 'sha256').hexdigest()
        if actual != item['sha256'] or path.stat().st_size != item['bytes']:
            raise ValueError('model_integrity')
    return manifest


class Encoder:
    def __init__(self, manifest):
        self.manifest = manifest
        self.tokenizer = Tokenizer.from_file(str(RUNTIME / 'model/tokenizer.json'))
        self.tokenizer.enable_truncation(max_length=manifest['max_tokens'])
        self.tokenizer.enable_padding(pad_id=0, pad_token='[PAD]')
        options = ort.SessionOptions()
        options.intra_op_num_threads = 2
        options.inter_op_num_threads = 1
        options.enable_cpu_mem_arena = False
        self.session = ort.InferenceSession(str(RUNTIME / 'model/model.onnx'),
                                            sess_options=options,
                                            providers=['CPUExecutionProvider'])
        if self.session.get_providers() != ['CPUExecutionProvider']:
            raise ValueError('unexpected_execution_provider')

    def encode(self, texts, *, query=False):
        vectors = []
        for offset in range(0, len(texts), 4):
            batch = texts[offset:offset + 4]
            if query:
                batch = [self.manifest['query_prefix'] + text for text in batch]
            encoded = self.tokenizer.encode_batch(batch)
            arrays = {
                'input_ids': np.asarray([row.ids for row in encoded], dtype=np.int64),
                'attention_mask': np.asarray([row.attention_mask for row in encoded], dtype=np.int64),
                'token_type_ids': np.asarray([row.type_ids for row in encoded], dtype=np.int64),
            }
            output = self.session.run(None, {item.name: arrays[item.name] for item in self.session.get_inputs()})[0]
            # BAAI's documented BGE pooling uses the final [CLS] token, not mean.
            vector = output[:, 0, :].astype(np.float32)
            norms = np.linalg.norm(vector, axis=1, keepdims=True)
            if not np.isfinite(vector).all() or (norms <= 0).any():
                raise ValueError('invalid_embedding')
            vectors.append(vector / norms)
        return np.concatenate(vectors) if vectors else np.empty((0, self.manifest['dimensions']), dtype=np.float32)


def atomic_json(path, payload):
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(mode='w', encoding='utf-8', dir=path.parent,
                                         prefix='native-index-', suffix='.tmp', delete=False) as out:
            temporary = Path(out.name)
            json.dump(payload, out, ensure_ascii=False, separators=(',', ':'))
        os.replace(temporary, path)
    finally:
        if temporary:
            temporary.unlink(missing_ok=True)


def run(request):
    started = time.monotonic()
    manifest = verify_model()
    model_digest = digest(manifest)
    encoder = Encoder(manifest)
    if request.get('action') == 'semantic_probe':
        sentences = ['孩子在雪地里堆雪人', '儿童在冬天用积雪塑造人形', '金融市场的股票行情']
        vectors = encoder.encode(sentences)
        scores = (vectors[0] @ vectors.T).tolist()
        return {'format': 'hex.native-semantic-probe.v1', 'sentences': sentences,
                'scores': scores, 'paraphrase_above_unrelated': scores[1] > scores[2],
                'dimensions': vectors.shape[1], 'norms': np.linalg.norm(vectors, axis=1).tolist(),
                'provider': 'CPUExecutionProvider', 'model_manifest_digest': model_digest,
                'elapsed_ms': round((time.monotonic() - started) * 1000, 3)}
    cards = request['cards']
    if not isinstance(cards, list) or not 1 <= len(cards) <= 2000:
        raise ValueError('invalid_catalog')
    if any(set(row) != {'card_id', 'card_version', 'card_sha256', 'text'} or
           not isinstance(row['text'], str) or len(row['text']) > 20000 for row in cards):
        raise ValueError('invalid_catalog')
    catalog_digest = digest(cards)
    identity = {'format': FORMAT, 'model_manifest_digest': model_digest,
                'catalog_digest': catalog_digest, 'dimensions': manifest['dimensions']}
    index_path = RUNTIME / 'index.json'
    index = None
    if index_path.exists():
        if index_path.stat().st_size > 32 * 1024 * 1024:
            raise ValueError('index_size')
        cached = json.loads(index_path.read_text(encoding='utf-8'))
        if all(cached.get(key) == value for key, value in identity.items()):
            if cached.get('digest') != digest({k: v for k, v in cached.items() if k != 'digest'}):
                raise ValueError('index_integrity')
            index = cached
    reused = index is not None
    if index is None:
        vectors = encoder.encode([row['text'] for row in cards])
        index = {**identity, 'vectors': vectors.tolist()}
        index['digest'] = digest(index)
        atomic_json(index_path, index)
    vectors = np.asarray(index['vectors'], dtype=np.float32)
    if vectors.shape != (len(cards), manifest['dimensions']) or not np.isfinite(vectors).all():
        raise ValueError('index_shape')
    if not np.allclose(np.linalg.norm(vectors, axis=1), 1, atol=1e-5):
        raise ValueError('index_normalization')
    query = request['query']
    if not isinstance(query, str) or not 1 <= len(query.strip()) <= 8000:
        raise ValueError('invalid_query')
    query_vector = encoder.encode([query], query=True)[0]
    scores = vectors @ query_vector
    limit = request['candidate_limit']
    if type(limit) is not int or not 1 <= limit <= 256:
        raise ValueError('invalid_limit')
    order = sorted(range(len(cards)), key=lambda i: (-float(scores[i]), cards[i]['card_id']))[:limit]
    return {'format': 'hex.native-retrieval.v1', 'catalog_digest': catalog_digest,
            'model_manifest_digest': model_digest, 'index_digest': index['digest'],
            'index_reused': reused, 'indexed_cards': len(cards),
            'dimensions': manifest['dimensions'], 'provider': 'CPUExecutionProvider',
            'candidates': [{**{k: v for k, v in cards[i].items() if k != 'text'},
                            'score': float(scores[i])} for i in order],
            'elapsed_ms': round((time.monotonic() - started) * 1000, 3)}


if __name__ == '__main__':
    try:
        data = sys.stdin.buffer.read(4 * 1024 * 1024 + 1)
        if len(data) > 4 * 1024 * 1024:
            raise ValueError('request_size')
        result = run(json.loads(data))
        sys.stdout.buffer.write(json.dumps(result, ensure_ascii=False).encode('utf-8'))
    except Exception as exc:
        # No model download, retry, network call, or raw exception disclosure.
        sys.stdout.buffer.write(json.dumps({'error': type(exc).__name__}).encode('utf-8'))
        raise SystemExit(1)
