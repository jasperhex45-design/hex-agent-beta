"""Private, incremental reference ingestion; visual claims remain host-authored.

The catalog contains user material, never product source. Published collections
reuse reference_library and its evaluation-only creation path.
"""
from __future__ import annotations

import argparse
from contextlib import contextmanager
import io
import json
import os
from pathlib import Path
import re
import sqlite3
import tempfile
from typing import Literal

from PIL import Image, UnidentifiedImageError
from pydantic import Field, field_validator, model_validator

from .contracts import AestheticCard, AestheticLibrary, Strict
from .io import AgentError, MAX_EVALUATION_SOURCE_BYTES, MAX_IMAGE_PIXELS, digest, now, read_json, sha, write_json

CATALOG_FORMAT = 'hex.reference-catalog.v1'
IMAGE_SUFFIXES = {'.png', '.jpg', '.jpeg', '.webp'}
DEFAULT_ROOT = Path.home() / '.hex' / 'reference-library'


class UnsupportedReferenceImage(AgentError):
    """A decoded media type that this static-image catalog does not ingest."""


class ReferenceFeedback(Strict):
    reference_id: str = Field(pattern=r'^ref_[0-9a-f]{24}$')
    source_sha256: str = Field(pattern=r'^[0-9a-f]{64}$')
    aspect: Literal['palette', 'light', 'composition', 'material', 'expression', 'overall']
    signal: Literal['prefer', 'avoid', 'do_not_recommend', 'clear']
    scope: str = 'global'
    user_quote: str = Field(min_length=1, max_length=3000)

    @field_validator('scope')
    @classmethod
    def valid_scope(cls, value: str) -> str:
        if value != 'global' and not re.fullmatch(r'project:[\w\- .\u4e00-\u9fff]{1,100}', value):
            raise ValueError('反馈范围须为 global 或 project:项目名')
        return value

    @model_validator(mode='after')
    def whole_reference_rejection(self):
        if self.signal == 'do_not_recommend' and self.aspect != 'overall':
            raise ValueError('不再推荐作用于整张图；局部属性使用 avoid')
        return self


def _json(value) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'))


def _atomic_bytes(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(dir=path.parent, prefix='.hex-ref-', delete=False) as file:
        temporary = Path(file.name)
        file.write(data)
    try:
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def _image_info(data: bytes) -> tuple[str, int, int]:
    if len(data) > MAX_EVALUATION_SOURCE_BYTES:
        raise AgentError('参考原图超过 64 MiB 限制')
    try:
        with Image.open(io.BytesIO(data)) as image:
            suffix = {'PNG': '.png', 'JPEG': '.jpg', 'WEBP': '.webp'}.get(image.format)
            if suffix is None or getattr(image, 'n_frames', 1) != 1:
                raise UnsupportedReferenceImage('参考库仅接受单帧 PNG/JPEG/WEBP')
            if image.width * image.height > MAX_IMAGE_PIXELS:
                raise AgentError('参考图超过 3200 万像素限制')
            image.load()
            return suffix, image.width, image.height
    except (UnidentifiedImageError, OSError, Image.DecompressionBombError) as exc:
        raise AgentError('参考图无法解码') from exc


def _scope(value: str) -> str:
    # Reuse the same validation for CLI selection and feedback ingestion.
    return ReferenceFeedback.valid_scope(value)


class ReferenceCatalog:
    def __init__(self, root: Path):
        self.root = root.expanduser().resolve()
        self.database = self.root / 'catalog.sqlite3'
        if not self.database.is_file():
            raise AgentError('参考库尚未初始化；先运行 reference_catalog init')
        with self._db() as db:
            meta = dict(db.execute('SELECT key, value FROM metadata'))
        if meta.get('format') != CATALOG_FORMAT:
            raise AgentError('不支持的参考库版本')
        self.collection_id = meta['collection_id']
        self.user_quote = meta['user_quote']
        # Early v1 catalogs already contain feedback but not exported-batch
        # receipts. Preserve that evidence while adding the small receipt table.
        with self._db() as db:
            db.execute('''CREATE TABLE IF NOT EXISTS feedback_imports
                (batch_id TEXT PRIMARY KEY, content_digest TEXT NOT NULL, created_at TEXT NOT NULL)''')
            columns = {row['name'] for row in db.execute('PRAGMA table_info(feedback_imports)')}
            if 'content_digest' not in columns:
                db.execute("ALTER TABLE feedback_imports ADD COLUMN content_digest TEXT NOT NULL DEFAULT ''")

    @classmethod
    def initialize(cls, root: Path, collection_id: str, user_quote: str) -> 'ReferenceCatalog':
        if not re.fullmatch(r'[a-zA-Z0-9][a-zA-Z0-9_.-]{2,99}', collection_id):
            raise AgentError('集合名须为 3–100 位字母、数字、点、短横线或下划线')
        if not 8 <= len(user_quote.strip()) <= 3000:
            raise AgentError('保留用户选择此素材集合的原话，8–3000 字')
        root = root.expanduser().resolve()
        root.mkdir(parents=True, exist_ok=True, mode=0o700)
        database = root / 'catalog.sqlite3'
        if database.exists():
            existing = cls(root)
            if existing.collection_id != collection_id:
                raise AgentError('此目录属于另一集合，不覆盖现有参考库')
            return existing
        db = sqlite3.connect(database)
        try:
            db.executescript('''
                PRAGMA foreign_keys=ON;
                CREATE TABLE metadata (key TEXT PRIMARY KEY, value TEXT NOT NULL);
                CREATE TABLE assets (
                    id TEXT PRIMARY KEY, sha256 TEXT NOT NULL UNIQUE, path TEXT NOT NULL,
                    width INTEGER NOT NULL, height INTEGER NOT NULL, created_at TEXT NOT NULL,
                    withdrawn INTEGER NOT NULL DEFAULT 0);
                CREATE TABLE sources (
                    source_key TEXT PRIMARY KEY, asset_id TEXT NOT NULL REFERENCES assets(id),
                    kind TEXT NOT NULL, details TEXT NOT NULL, active INTEGER NOT NULL DEFAULT 1,
                    updated_at TEXT NOT NULL);
                CREATE TABLE analyses (
                    asset_id TEXT PRIMARY KEY REFERENCES assets(id), card TEXT NOT NULL,
                    updated_at TEXT NOT NULL);
                CREATE TABLE feedback (
                    seq INTEGER PRIMARY KEY AUTOINCREMENT, event_id TEXT NOT NULL UNIQUE,
                    asset_id TEXT NOT NULL REFERENCES assets(id), payload TEXT NOT NULL,
                    created_at TEXT NOT NULL);
                CREATE TABLE feedback_imports (batch_id TEXT PRIMARY KEY, content_digest TEXT NOT NULL,
                                               created_at TEXT NOT NULL);
                CREATE TABLE sync_state (source_key TEXT PRIMARY KEY, payload TEXT NOT NULL);
                CREATE TABLE exports (fingerprint TEXT PRIMARY KEY, payload TEXT NOT NULL);
            ''')
            db.executemany('INSERT INTO metadata VALUES (?,?)', [
                ('format', CATALOG_FORMAT), ('collection_id', collection_id),
                ('user_quote', user_quote), ('created_at', now())])
            db.commit()
        finally:
            db.close()
        if os.name != 'nt':
            database.chmod(0o600)
        return cls(root)

    @contextmanager
    def _db(self):
        db = sqlite3.connect(self.database, timeout=30)
        db.row_factory = sqlite3.Row
        db.execute('PRAGMA foreign_keys=ON')
        try:
            with db:
                yield db
        finally:
            db.close()

    def ingest(self, data: bytes, *, source_key: str, kind: str, details: dict) -> dict:
        if kind != 'local' or not source_key:
            raise AgentError('参考来源类型或标识无效')
        suffix, width, height = _image_info(data)
        source_hash = sha(data)
        reference_id = 'ref_' + source_hash[:24]
        relative = 'assets/' + source_hash + suffix
        target = self.root / relative
        with self._db() as db:
            previous = db.execute('SELECT * FROM sources WHERE source_key=?', (source_key,)).fetchone()
            asset = db.execute('SELECT * FROM assets WHERE id=?', (reference_id,)).fetchone()
            if asset and asset['withdrawn']:
                raise AgentError('此图片已被撤回；不会因重复扫描自动恢复')
            if target.exists() and sha(target.read_bytes()) != source_hash:
                raise AgentError('库内原图已改变，停止覆盖并保留现场')
            if not target.exists():
                _atomic_bytes(target, data)
            db.execute('INSERT OR IGNORE INTO assets VALUES (?,?,?,?,?,?,0)',
                       (reference_id, source_hash, relative, width, height, now()))
            encoded = _json(details)
            unchanged = bool(previous and previous['asset_id'] == reference_id and
                             previous['details'] == encoded and previous['active'])
            if not unchanged:
                db.execute('''INSERT INTO sources VALUES (?,?,?,?,1,?)
                    ON CONFLICT(source_key) DO UPDATE SET asset_id=excluded.asset_id,
                    kind=excluded.kind, details=excluded.details, active=1, updated_at=excluded.updated_at''',
                           (source_key, reference_id, kind, encoded, now()))
        return {'reference_id': reference_id, 'source_sha256': source_hash,
                'status': 'unchanged' if unchanged else ('duplicate' if asset else 'imported')}

    def import_local(self, source_root: Path, *, max_files: int = 500) -> dict:
        source_root = source_root.expanduser().resolve()
        if not source_root.is_dir() or not 1 <= max_files <= 10000:
            raise AgentError('请选择存在的素材目录；每批上限 1–10000 张')
        counts = {'imported': 0, 'duplicate': 0, 'unchanged': 0}
        failures, processed, limited = [], 0, False
        for source in sorted(source_root.rglob('*')):
            if not source.is_file() or source.suffix.lower() not in IMAGE_SUFFIXES:
                continue
            resolved = source.resolve()
            if not resolved.is_relative_to(source_root) or resolved.is_relative_to(self.root):
                continue
            if processed >= max_files:
                limited = True
                break
            try:
                if source.stat().st_size > MAX_EVALUATION_SOURCE_BYTES:
                    raise AgentError('原图超过 64 MiB')
                data = source.read_bytes()
                details = {'source_path': str(resolved), 'title': source.stem,
                           'relative_path': source.relative_to(source_root).as_posix(),
                           'board_ids': [], 'source_url': None, 'origin_url': None,
                           'user_quote': self.user_quote, 'generation_authorized': False}
                result = self.ingest(data, source_key='local:' + str(resolved), kind='local', details=details)
                counts[result['status']] += 1
                # Unchanged files do not consume the incremental work allowance.
                if result['status'] != 'unchanged':
                    processed += 1
            except (OSError, AgentError) as exc:
                failures.append({'source': str(source), 'reason': str(exc)})
                processed += 1
        return {'collection_id': self.collection_id, **counts, 'failures': failures,
                'limit_reached': limited, 'status': 'imported_with_errors' if failures else 'imported',
                'visual_analysis_performed': False}

    def references(self, *, scope: str = 'global', include_withdrawn: bool = False) -> list[dict]:
        scope = _scope(scope)
        with self._db() as db:
            assets = list(db.execute('SELECT a.*, n.card FROM assets a LEFT JOIN analyses n ON n.asset_id=a.id ORDER BY a.id'))
            sources = list(db.execute('SELECT * FROM sources WHERE active=1 ORDER BY source_key'))
            events = list(db.execute('SELECT asset_id,payload FROM feedback ORDER BY seq'))
        source_map: dict[str, list] = {}
        for row in sources:
            source_map.setdefault(row['asset_id'], []).append({'kind': row['kind'], **json.loads(row['details'])})
        latest: dict[tuple, dict] = {}
        for row in events:
            event = json.loads(row['payload'])
            if event['scope'] in {'global', scope}:
                latest[(row['asset_id'], event['aspect'], event['scope'])] = event
        result = []
        for asset in assets:
            if not include_withdrawn and (asset['withdrawn'] or asset['id'] not in source_map):
                continue
            provenance = source_map.get(asset['id'], [])
            card = json.loads(asset['card']) if asset['card'] else None
            preferences = [value for key, value in latest.items()
                           if key[0] == asset['id'] and value['signal'] != 'clear']
            result.append({'id': asset['id'], 'source_path': str(self.root / asset['path']),
                           'source_sha256': asset['sha256'], 'width': asset['width'], 'height': asset['height'],
                           'title': card['title'] if card else next((s.get('title') for s in provenance if s.get('title')), asset['id']),
                           'source_url': next((s.get('source_url') for s in provenance if s.get('source_url')), None),
                           'boards': sorted({b for source in provenance for b in source.get('board_ids', [])}),
                           'status': 'withdrawn' if asset['withdrawn'] else (card['status'] if card else 'candidate'),
                           'card': card, 'preferences': preferences, 'sources': provenance})
        return result

    def status(self) -> dict:
        references = self.references()
        return {'collection_id': self.collection_id, 'catalog': str(self.root),
                'references': len(references),
                'curated': sum(r['status'] == 'curated' for r in references),
                'pending': sum(r['status'] == 'candidate' for r in references),
                'unobserved': sum(r['card'] is None for r in references),
                'needs_review': sum(r['card'] is not None and r['status'] == 'candidate' for r in references),
                'generation_authorized': False}

    def prepare(self, *, limit: int = 8, reference_ids: list[str] | None = None) -> dict:
        if not 1 <= limit <= 20:
            raise AgentError('每批实际看图数量须为 1–20')
        references = self.references()
        selected = set(reference_ids or [])
        if selected - {r['id'] for r in references}:
            raise AgentError('请求分析的参考不在当前可用集合中')
        # An inconclusive observation must not monopolize every subsequent
        # batch. Revisit those cards explicitly; incremental work starts with
        # images that have never received an observation for these exact bytes.
        candidates = [r for r in references if r['id'] in selected] if selected else [r for r in references if r['card'] is None]
        items = []
        for reference in candidates[:limit]:
            self._verify_source(reference)
            items.append(reference)
        schema = AestheticCard.model_json_schema()
        managed = {'id', 'version', 'source_path', 'source_sha256'}
        for key in managed:
            schema['properties'].pop(key, None)
        schema['required'] = [key for key in schema.get('required', []) if key not in managed]
        return {'format': 'hex.reference-analysis.v1', 'collection_id': self.collection_id,
                'input_files': [r['source_path'] for r in items], 'items': items,
                'remaining': max(0, len(candidates) - len(items)), 'card_schema': schema,
                'needs_review': sum(r['card'] is not None and r['status'] == 'candidate' for r in references),
                'output_format': 'hex.reference-observations.v1',
                'instruction': ('实际打开 input_files，按每张图的 reference_id 和 source_sha256 返回 analyses。'
                                '每项包含 reference_id、source_sha256、card；一张图提取一个主导视觉决定。'
                                '把可见事实、解释和用户偏好分开，不从标题猜作者、焦距或灯具。'
                                '写清可迁移因素、适用领域、不可迁移项和可见失败信号。'
                                '只有实际看图且完成分析才写 seen_via=view_image、status=curated；'
                                '不确定时保留 candidate。收藏行为不代表喜欢所有因素，禁止生成或改写用户反馈。')}

    @staticmethod
    def _verify_source(reference: dict) -> None:
        source = Path(reference['source_path'])
        if (not source.is_file() or source.stat().st_size > MAX_EVALUATION_SOURCE_BYTES
                or sha(source.read_bytes()) != reference['source_sha256']):
            raise AgentError('参考原图缺失或已改变，不能沿用旧观察')

    def observe(self, packet: dict) -> dict:
        if not isinstance(packet, dict) or packet.get('format') != 'hex.reference-observations.v1' or packet.get('collection_id') != self.collection_id:
            raise AgentError('观察文件类型或集合不匹配')
        rows = packet.get('analyses')
        if not isinstance(rows, list) or not 1 <= len(rows) <= 20:
            raise AgentError('观察文件每批需要 1–20 张实际图片')
        available = {r['id']: r for r in self.references()}
        built, seen = [], set()
        from .aesthetic_library import build_card
        for row in rows:
            if not isinstance(row, dict):
                raise AgentError('每张实际图片需要完整的观察对象')
            reference_id = row.get('reference_id')
            if not isinstance(reference_id, str):
                raise AgentError('观察引用需要有效的 reference_id')
            reference = available.get(reference_id)
            if reference is None or reference_id in seen or row.get('source_sha256') != reference['source_sha256']:
                raise AgentError('观察引用缺失、重复或与原图摘要不一致')
            self._verify_source(reference)
            seen.add(reference_id)
            raw = row.get('card')
            if not isinstance(raw, dict):
                raise AgentError('每张实际图片需要 card 观察记录')
            raw = {**raw, 'id': 'aesthetic.' + reference_id,
                   'version': reference['card']['version'] + 1 if reference['card'] else 1}
            card = build_card(Path(reference['source_path']), raw)
            built.append((reference_id, card))
        with self._db() as db:
            for reference_id, card in built:
                db.execute('''INSERT INTO analyses VALUES (?,?,?) ON CONFLICT(asset_id)
                    DO UPDATE SET card=excluded.card,updated_at=excluded.updated_at''',
                           (reference_id, _json(card.model_dump()), now()))
        return {'status': 'observations_saved', 'cards': len(built),
                'curated': sum(card.status == 'curated' for _, card in built),
                'user_acceptance_recorded': False}

    def feedback(self, packet: dict) -> dict:
        if not isinstance(packet, dict) or packet.get('format') != 'hex.reference-feedback.v1' or packet.get('collection_id') != self.collection_id:
            raise AgentError('反馈文件类型或集合不匹配')
        events = packet.get('events')
        if not isinstance(events, list) or not 1 <= len(events) <= 1000:
            raise AgentError('反馈文件须包含 1–1000 项用户选择')
        available = {r['id']: r for r in self.references()}
        validated = []
        for raw in events:
            event = ReferenceFeedback.model_validate(raw)
            reference = available.get(event.reference_id)
            if reference is None or event.source_sha256 != reference['source_sha256']:
                raise AgentError('反馈与当前参考图不匹配')
            self._verify_source(reference)
            validated.append(event.model_dump())
        export_id = packet.get('export_id')
        if export_id is not None and (not isinstance(export_id, str) or not 1 <= len(export_id) <= 160):
            raise AgentError('反馈 export_id 必须是 1–160 字符的批次标识')
        content_digest = digest({'collection_id': self.collection_id, 'events': validated})
        batch_id = digest({'collection_id': self.collection_id, 'export_id': export_id}) if export_id else content_digest
        with self._db() as db:
            # Idempotency belongs to an exported batch, not a preference forever:
            # prefer -> avoid -> prefer must preserve the user's latest choice.
            db.execute('BEGIN IMMEDIATE')
            existing = db.execute('SELECT content_digest FROM feedback_imports WHERE batch_id=?', (batch_id,)).fetchone()
            if existing and existing['content_digest'] != content_digest:
                raise AgentError('反馈批次 export_id 已用于不同内容；新的选择须使用新 export_id')
            added = 0 if existing else len(validated)
            if not existing:
                for index, event in enumerate(validated):
                    db.execute('INSERT INTO feedback(event_id,asset_id,payload,created_at) VALUES (?,?,?,?)',
                               (digest({'batch_id': batch_id, 'index': index}), event['reference_id'], _json(event), now()))
                db.execute('INSERT INTO feedback_imports(batch_id,content_digest,created_at) VALUES (?,?,?)',
                           (batch_id, content_digest, now()))
        return {'status': 'feedback_saved', 'added': added, 'duplicates': len(validated) - added,
                'scope_preserved': True, 'collection_publication_required': True}

    def withdraw(self, reference_id: str, user_quote: str) -> dict:
        available = {r['id']: r for r in self.references()}
        reference = available.get(reference_id)
        if reference is None or not user_quote.strip():
            raise AgentError('撤回需要当前参考 ID 与用户原话')
        source = Path(reference['source_path'])
        if source.exists():
            archive = self.root / 'withdrawn' / source.name
            archive.parent.mkdir(parents=True, exist_ok=True)
            os.replace(source, archive)
        with self._db() as db:
            db.execute('UPDATE assets SET withdrawn=1 WHERE id=?', (reference_id,))
            db.execute('UPDATE sources SET active=0 WHERE asset_id=?', (reference_id,))
        return {'status': 'withdrawn', 'reference_id': reference_id,
                'user_quote': user_quote, 'previous_reference_source_unavailable': True}

    def publish(self, *, destination: Path, scope: str = 'global', domains: list[str] | None = None) -> dict:
        from . import reference_library
        scope = _scope(scope)
        destination = destination.expanduser().resolve()
        if (destination == self.database or any(destination.is_relative_to(self.root / folder)
                for folder in ['assets', 'withdrawn', 'exports', 'collections'])):
            raise AgentError('集合配置不能覆盖库内原图、数据库或不可变导出')
        references = self.references(scope=scope)
        selected = [r for r in references if r['status'] == 'curated'
                    and not any(e['signal'] == 'do_not_recommend' for e in r['preferences'])]
        if not selected:
            previous = None
            if destination.is_file():
                previous = reference_library.CollectionConfig.model_validate(read_json(destination))
            selected_here = (previous is not None and previous.collection_id == self.collection_id
                             and Path(previous.source_root).resolve() == self.root / 'assets')
            if not selected_here and not any(r['status'] == 'curated' for r in references):
                raise AgentError('没有可发布的已看图卡片；先 prepare、实际观察并 observe')
            # Excluding the last reference is a valid user choice. Preserve old
            # task snapshots, but stop new tasks acquiring this active collection.
            if selected_here and previous.enabled:
                write_json(destination, previous.model_copy(update={'enabled': False, 'configured_at': now()}).model_dump())
            return {'status': 'no_recommendable_references', 'collection_id': self.collection_id,
                    'config': str(destination), 'scope': scope, 'enabled': False, 'cards': 0,
                    'active_config_disabled': bool(selected_here), 'new_tasks_only': True,
                    'generation_authorized': False, 'images_submitted': False}
        for reference in selected:
            self._verify_source(reference)
        manifest = {'format': CATALOG_FORMAT, 'collection_id': self.collection_id, 'scope': scope,
                    'references': [{'reference_id': r['id'], 'card_id': r['card']['id'],
                                    'source_sha256': r['source_sha256'], 'sources': r['sources'],
                                    'preferences': r['preferences']} for r in selected]}
        fingerprint = digest({'manifest': manifest, 'cards': [r['card'] for r in selected]})
        exports = self.root / 'exports'
        source_catalog = exports / (fingerprint + '.sources.json')
        library_path = exports / (fingerprint + '.cards.json')
        with self._db() as db:
            row = db.execute('SELECT payload FROM exports WHERE fingerprint=?', (fingerprint,)).fetchone()
            if row:
                library = json.loads(row['payload'])
            else:
                library = AestheticLibrary(
                    format='liu.aesthetic-library.v1', version=1, generated_at=now(),
                    source_catalog=str(source_catalog), selection_policy=[
                        '只发布实际看图并完成迁移分析的 curated 卡片',
                        '图片收藏、视觉观察和用户偏好分别保存',
                        '偏好只在用户声明的范围生效；不推定生成上传权限'],
                    cards=[AestheticCard.model_validate(r['card']) for r in selected]).model_dump()
                db.execute('INSERT INTO exports VALUES (?,?)', (fingerprint, _json(library)))
        for path, content in [(source_catalog, manifest), (library_path, library)]:
            if path.exists():
                if read_json(path) != content:
                    raise AgentError('已发布内容发生改变，不覆盖原版本')
            else:
                write_json(path, content)
        result = reference_library.publish_collection(library_path, self.root / 'assets', self.user_quote,
                    destination=destination, collection_id=self.collection_id,
                    snapshots_root=self.root / 'collections', domains=domains)
        return {**result, 'scope': scope, 'catalog': str(self.root),
                'feedback_manifest': str(source_catalog),
                'preference_context': [{'card_id': r['card']['id'], 'preferences': r['preferences']}
                                       for r in selected if r['preferences']]}


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description='HEX 私人参考库：增量导入、真实观察、版本发布与带图反馈')
    parser.add_argument('--catalog', type=Path, default=DEFAULT_ROOT)
    commands = parser.add_subparsers(dest='command', required=True)
    init = commands.add_parser('init')
    init.add_argument('--collection-id', required=True)
    init.add_argument('--user-quote', required=True)
    local = commands.add_parser('import-local')
    local.add_argument('--source-root', type=Path, required=True)
    local.add_argument('--max-files', type=int, default=500)
    commands.add_parser('status')
    prepare = commands.add_parser('prepare')
    prepare.add_argument('--limit', type=int, default=8)
    prepare.add_argument('--reference-id', action='append')
    prepare.add_argument('--out', type=Path)
    for name in ['observe', 'feedback']:
        command = commands.add_parser(name)
        command.add_argument('--file', type=Path, required=True)
    review = commands.add_parser('review')
    review.add_argument('--out', type=Path, required=True)
    review.add_argument('--scope', default='global')
    publish = commands.add_parser('publish')
    publish.add_argument('--config', type=Path)
    publish.add_argument('--scope', default='global')
    publish.add_argument('--domain', action='append')
    withdraw = commands.add_parser('withdraw')
    withdraw.add_argument('--reference-id', required=True)
    withdraw.add_argument('--user-quote', required=True)
    args = parser.parse_args(argv)
    try:
        if args.command == 'init':
            result = ReferenceCatalog.initialize(args.catalog, args.collection_id, args.user_quote).status()
        else:
            catalog = ReferenceCatalog(args.catalog)
            if args.command == 'import-local':
                result = catalog.import_local(args.source_root, max_files=args.max_files)
            elif args.command == 'status':
                result = catalog.status()
            elif args.command == 'prepare':
                result = catalog.prepare(limit=args.limit, reference_ids=args.reference_id)
                if args.out:
                    write_json(args.out, result)
            elif args.command in {'observe', 'feedback'}:
                result = getattr(catalog, args.command)(read_json(args.file))
            elif args.command == 'review':
                from .reference_review import render_review
                result = render_review(args.out, catalog.references(scope=args.scope),
                                       collection_id=catalog.collection_id, project_scope=args.scope)
            elif args.command == 'publish':
                from .reference_library import config_path
                from .io import ROOT
                result = catalog.publish(destination=args.config or config_path(ROOT / 'knowledge'),
                                         scope=args.scope, domains=args.domain)
            elif args.command == 'withdraw':
                result = catalog.withdraw(args.reference_id, args.user_quote)
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 1 if result.get('status') in {'imported_with_errors'} else 0
    except (AgentError, OSError, ValueError, sqlite3.Error) as exc:
        error = {'status': 'error', 'message': str(exc)}
        print(json.dumps(error, ensure_ascii=False))
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
