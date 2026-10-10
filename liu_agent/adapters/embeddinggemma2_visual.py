"""Opt-in local EmbeddingGemma 2 experiment; never downloads or falls back.

API verified against Transformers v5.19.0 embedding_gemma2 documentation.
Optional dependencies are imported only when this explicit backend is selected.
"""
from __future__ import annotations

import importlib.metadata
import hashlib
import math
import os
from pathlib import Path
import re

from PIL import Image

from ..io import AgentError, MAX_IMAGE_BYTES, MAX_IMAGE_PIXELS, digest, read_json, sha, within

MODEL_ID = 'google/embeddinggemma-2'
MANIFEST = 'hex-model-manifest.json'
DIMENSION = 768
DOCS = 'https://huggingface.co/docs/transformers/v5.19.0/en/model_doc/embedding_gemma2'
MODULES = [
    {'idx': 0, 'name': '0', 'path': '', 'type': 'sentence_transformers.base.modules.transformer.Transformer'},
    {'idx': 1, 'name': '1', 'path': '1_Pooling', 'type': 'sentence_transformers.sentence_transformer.modules.pooling.Pooling'},
    {'idx': 2, 'name': '2', 'path': '2_Normalize', 'type': 'sentence_transformers.base.modules.normalize.Normalize'},
]


def file_sha(path: Path) -> str:
    value = hashlib.sha256()
    with path.open('rb') as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b''):
            value.update(chunk)
    return value.hexdigest()


def checkpoint_identity(checkpoint: Path) -> dict:
    """Require content-bound local model provenance, including license material.

    The supplied revision is a declaration, not a signature by the model author.
    File hashes freeze the actual checkpoint used in a trial.
    """
    if not checkpoint.is_dir() or not (checkpoint / MANIFEST).is_file():
        raise AgentError('visual_trial_checkpoint_missing: supply a local checkpoint and hex-model-manifest.json; no download or fallback')
    manifest = read_json(checkpoint / MANIFEST)
    if (not isinstance(manifest, dict) or manifest.get('format') != 'hex.local-embedding-checkpoint.v1'
            or manifest.get('model_id') != MODEL_ID
            or manifest.get('license') != 'apache-2.0'
            or not re.fullmatch(r'[0-9a-f]{40}', str(manifest.get('revision', '')))):
        raise AgentError('visual_trial_checkpoint_identity_invalid: model, revision or license declaration')
    files = manifest.get('files')
    if not isinstance(files, dict) or not files or any(not isinstance(k, str) for k in files):
        raise AgentError('visual_trial_checkpoint_manifest_invalid: files must bind relative paths to SHA256')
    required = {'config.json', 'modules.json', 'config_sentence_transformers.json', '1_Pooling/config.json'}
    if (not required.issubset(files)
            or not any(name.endswith('.safetensors') for name in files)
            or not any(Path(name).name in ('README.md', 'LICENSE', 'LICENSE.txt') for name in files)):
        raise AgentError('visual_trial_checkpoint_manifest_incomplete: configuration, weights and license/model card required')
    # Unlisted files can affect tokenization/preprocessing or pooling. Freeze all
    # artifacts, not only weights. Symlinks outside this supplied tree are refused.
    actual = {str(path.relative_to(checkpoint)).replace('\\', '/')
              for path in checkpoint.rglob('*') if path.is_file() and path != checkpoint / MANIFEST
              and '.cache' not in path.relative_to(checkpoint).parts}
    if actual != set(files):
        raise AgentError('visual_trial_checkpoint_unbound_files: manifest must cover all local artifacts')
    if any(Path(name).suffix.lower() in ('.py', '.pyc', '.bin', '.pt', '.pth', '.pkl', '.pickle') for name in files):
        raise AgentError('visual_trial_checkpoint_executable_artifact: only safetensors weights and data artifacts accepted')
    for name, expected in files.items():
        path = within(checkpoint, name)
        if not re.fullmatch(r'[0-9a-f]{64}', str(expected)) or not path.is_file() or file_sha(path) != expected:
            raise AgentError('visual_trial_checkpoint_changed: artifact checksum mismatch')
    config = read_json(checkpoint / 'config.json')
    if (not isinstance(config, dict) or config.get('model_type') != 'embedding_gemma2'
            or not isinstance(config.get('vision_config'), dict) or not config['vision_config']
            or not isinstance(config.get('text_config'), dict) or config['text_config'].get('embedding_dim') != DIMENSION):
        raise AgentError('visual_trial_checkpoint_architecture_invalid: require vision and 768-dimensional embedding_gemma2')
    if read_json(checkpoint / 'modules.json') != MODULES:
        raise AgentError('visual_trial_checkpoint_modules_invalid: only official transformer/mean-pooling/normalize wrapper allowed')
    pool = read_json(checkpoint / '1_Pooling/config.json')
    if pool != {'embedding_dimension': DIMENSION, 'pooling_mode': 'mean', 'include_prompt': True}:
        raise AgentError('visual_trial_checkpoint_pooling_invalid: only mean pooling accepted')
    wrapper = read_json(checkpoint / 'config_sentence_transformers.json')
    if (not isinstance(wrapper, dict) or not isinstance(wrapper.get('prompts'), dict)
            or wrapper['prompts'].get('query') != 'task: search result | query: '
            or wrapper['prompts'].get('document') != 'title: none | text: '):
        raise AgentError('visual_trial_checkpoint_prompts_invalid')
    return {'model_id': MODEL_ID, 'declared_revision': manifest['revision'],
            'checkpoint_digest': digest(manifest), 'license': manifest['license'],
            'revision_authority': 'local_manifest_declaration_with_artifact_checksums'}


def _versions() -> dict:
    versions = {}
    for package, minimum in [('sentence-transformers', (6, 1, 0)), ('transformers', (5, 19, 0)), ('torch', (2, 6, 0))]:
        try:
            value = importlib.metadata.version(package)
        except importlib.metadata.PackageNotFoundError as exc:
            raise AgentError(f'visual_trial_dependency_missing: {package}; optional backend did not run') from exc
        match = re.match(r'^(\d+)\.(\d+)\.(\d+)', value)
        if not match or tuple(map(int, match.groups())) < minimum:
            raise AgentError(f'visual_trial_dependency_unsupported: {package} {value}')
        versions[package] = value
    return versions


def normalized(rows, expected_rows: int) -> list[list[float]]:
    """Reject malformed/nonfinite/zero vectors rather than producing a ranking."""
    if hasattr(rows, 'tolist'):
        rows = rows.tolist()
    if not isinstance(rows, list) or len(rows) != expected_rows:
        raise AgentError('visual_trial_vector_shape_invalid')
    result = []
    for row in rows:
        if not isinstance(row, list) or len(row) != DIMENSION:
            raise AgentError('visual_trial_vector_dimension_invalid')
        if any(type(value) not in (int, float) or not math.isfinite(value) for value in row):
            raise AgentError('visual_trial_vector_nonfinite')
        norm = math.sqrt(sum(value * value for value in row))
        if norm == 0 or not math.isfinite(norm):
            raise AgentError('visual_trial_vector_zero')
        result.append([value / norm for value in row])
    return result


class EmbeddingGemma2Visual:
    """Text queries, text-only controls and composed text+local-image documents."""

    def __init__(self, checkpoint: Path, *, use_prompts: bool = True, max_soft_tokens: int = 70):
        if type(use_prompts) is not bool or max_soft_tokens not in (70, 140, 280, 560, 1120):
            raise AgentError('visual_trial_config_invalid')
        self.identity = checkpoint_identity(checkpoint)
        self.identity.update({'runtime_versions': _versions(), 'modalities': ['text', 'image'],
                              'audio_tower': False, 'device': 'cpu', 'use_prompts': use_prompts,
                              'max_soft_tokens': max_soft_tokens, 'local_files_only': True,
                              'trust_remote_code': False, 'docs': DOCS})
        self.identity['execution_authority'] = 'local_embeddinggemma2_runtime'
        self.use_prompts = use_prompts
        self.max_soft_tokens = max_soft_tokens
        self.inference_counts = {'text_queries': 0, 'text_documents': 0, 'text_image_documents': 0}
        # This explicit backend is designed for a separate research process.
        # Force offline/telemetry-off before the lazy imports read their flags.
        os.environ['HF_HUB_OFFLINE'] = '1'
        os.environ['TRANSFORMERS_OFFLINE'] = '1'
        os.environ['HF_HUB_DISABLE_TELEMETRY'] = '1'
        try:
            from sentence_transformers import SentenceTransformer
            self.model = SentenceTransformer(str(checkpoint.resolve()), device='cpu', local_files_only=True,
                                             trust_remote_code=False, config_kwargs={'audio_config': None},
                                             model_kwargs={'use_safetensors': True})
            # SentenceTransformer may import successfully with an incompatible
            # checkpoint wrapper. Assert the loaded tower identity too.
            config = self.model[0].auto_model.config
            if config.model_type != 'embedding_gemma2' or config.vision_config is None or config.audio_config is not None:
                raise AgentError('visual_trial_loaded_architecture_invalid')
        except AgentError:
            raise
        except Exception as exc:
            raise AgentError(f'visual_trial_model_load_failed: {type(exc).__name__}; no fallback') from exc

    def _encode(self, inputs: list, prompt: str) -> list[list[float]]:
        kwargs = {'batch_size': 1, 'show_progress_bar': False, 'convert_to_numpy': True,
                  'normalize_embeddings': True,
                  'processing_kwargs': {'image': {'max_soft_tokens': self.max_soft_tokens}}}
        if self.use_prompts:
            kwargs['prompt_name'] = prompt
        else:
            # Explicit empty prompt overrides any checkpoint default prompt.
            kwargs['prompt'] = ''
        try:
            return normalized(self.model.encode(inputs, **kwargs), len(inputs))
        except AgentError:
            raise
        except Exception as exc:
            raise AgentError(f'visual_trial_inference_failed: {type(exc).__name__}; no fallback') from exc

    def query(self, text: str) -> list[float]:
        if not isinstance(text, str) or not text.strip() or len(text) > 4000:
            raise AgentError('visual_trial_query_invalid: require 1 to 4000 characters')
        result = self._encode([text], 'query')[0]
        self.inference_counts['text_queries'] += 1
        return result

    def documents(self, records: list[dict], *, images: bool = True) -> list[list[float]]:
        if not records or len(records) > 96:
            raise AgentError('visual_trial_corpus_limit: require 1 to 96 explicit records')
        payloads, opened = [], []
        try:
            for record in records:
                text = record['text']
                if not isinstance(text, str) or len(text) > 16000:
                    raise AgentError('visual_trial_document_invalid')
                if images:
                    path = Path(record['image'])
                    if not path.is_file() or path.stat().st_size > MAX_IMAGE_BYTES:
                        raise AgentError('visual_trial_image_missing_or_too_large')
                    if sha(path.read_bytes()) != record['image_sha256']:
                        raise AgentError('visual_trial_image_changed')
                    try:
                        with Image.open(path) as original:
                            if original.width * original.height > MAX_IMAGE_PIXELS:
                                raise AgentError('visual_trial_image_pixels_exceeded')
                            image = original.convert('RGB')
                            if record.get('crop_box'):
                                image = image.crop(record['crop_box'])
                            image.thumbnail((1024, 1024))
                    except Image.DecompressionBombError as exc:
                        raise AgentError('visual_trial_image_pixels_exceeded') from exc
                    opened.append(image)
                    payloads.append({'text': text, 'image': image})
                else:
                    payloads.append(text)
            result = self._encode(payloads, 'document')
            self.inference_counts['text_image_documents' if images else 'text_documents'] += len(records)
            return result
        finally:
            for image in opened:
                image.close()
