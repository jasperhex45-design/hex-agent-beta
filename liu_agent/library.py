"""Read-only image catalog and lexical retrieval. Never guesses image contents."""
from __future__ import annotations
import math
from collections import Counter
from pathlib import Path
from PIL import Image
from .io import AgentError, read_json, write_json, sha, now, within
from .retrieval import tokens

SUPPORTED = {'.png', '.jpg', '.jpeg', '.webp'}
IMAGE_EXTENSIONS = SUPPORTED | {'.heic', '.heif', '.gif', '.tif', '.tiff', '.bmp', '.avif'}


def load_library(path: Path) -> dict:
    data = read_json(path)
    if data.get('format') != 'liu.visual-library.v1':
        raise AgentError('不是本地视觉图库格式')
    return data


def item_path(library: dict, item: dict) -> Path:
    for relative in item['paths']:
        path = within(Path(library['root']), relative)
        if path.is_file() and sha(path.read_bytes()) == item['sha256']:
            return path
    raise AgentError('素材已删除或内容改变；重新扫描图库，旧观察不可套用')


def scan(root: Path, catalog: Path) -> dict:
    root = root.resolve()
    if not root.is_dir():
        raise AgentError('素材根目录不存在')
    if catalog.resolve().is_relative_to(root):
        raise AgentError('索引须写入工程目录，不写入原素材目录')
    old = load_library(catalog) if catalog.exists() else {}
    if old and Path(old['root']).resolve() != root:
        raise AgentError('此索引属于另一素材根目录，请使用新索引文件')
    previous = {i['sha256']: i for i in old.get('items', [])}
    items, excluded, count = {}, [], 0
    for path in sorted(root.rglob('*')):
        if not path.is_file() or path.suffix.lower() not in IMAGE_EXTENSIONS:
            continue
        count += 1
        rel = path.relative_to(root).as_posix()
        try:
            if not path.resolve().is_relative_to(root):
                raise AgentError('链接指向素材目录外')
            if path.suffix.lower() not in SUPPORTED:
                raise AgentError('当前任务仅支持单帧 PNG/JPEG/WEBP；未转换原件')
            if path.stat().st_size > 16 * 1024 * 1024:
                raise AgentError('超过16MiB任务工作图上限')
            data = path.read_bytes()
            with Image.open(path) as image:
                if image.width * image.height > 32_000_000 or getattr(image, 'n_frames', 1) != 1:
                    raise AgentError('超大图或多帧图；需另备工作副本')
                width, height = image.size
                image.verify()
            checksum = sha(data)
            if checksum in items:
                items[checksum]['paths'].append(rel)
                continue
            item = {'id': 'v' + checksum[:24], 'sha256': checksum, 'paths': [rel],
                    'width': width, 'height': height, 'bytes': len(data),
                    'observation': previous.get(checksum, {}).get('observation'),
                    'rights': 'unknown'}
            items[checksum] = item
        except (AgentError, OSError, ValueError, Image.DecompressionBombError) as exc:
            excluded.append({'path': rel, 'reason': str(exc)})
    result = {'format': 'liu.visual-library.v1', 'root': str(root), 'scanned_at': now(),
              'image_file_count': count, 'items': list(items.values()), 'excluded': excluded,
              'notice': '文件名只是线索；未观察条目不代表已看图。索引不包含图片副本，也不授予上传权。'}
    write_json(catalog, result)
    return {'catalog': str(catalog.resolve()), 'image_files': count, 'unique_images': len(items),
            'duplicate_files': sum(len(i['paths']) - 1 for i in items.values()),
            'observed_images': sum(bool(i['observation']) for i in items.values()), 'excluded': len(excluded)}


def annotate(catalog: Path, ident: str, report: Path, tool_note: str) -> dict:
    from .contracts import LibraryObservation
    data = load_library(catalog)
    item = next((i for i in data['items'] if i['id'] == ident), None)
    if item is None:
        raise AgentError('图库ID不存在')
    item_path(data, item)
    observation = LibraryObservation.model_validate(read_json(report))
    if not tool_note.strip():
        raise AgentError('必须写实际看图工具/操作；文件名不能充当看图')
    item['observation'] = {**observation.model_dump(), 'image_sha256': item['sha256'],
                           'origin': 'host_reported_not_api_verified', 'tool_note': tool_note,
                           'observed_at': now()}
    write_json(catalog, data)
    return {'id': ident, 'observation': item['observation']}


def search(catalog: Path, query: str, limit: int = 6, run: Path | None = None) -> dict:
    if run is not None:
        from .workflow import read_packet
        from .visual_decision import record_only
        if record_only(read_packet(run)):
            raise AgentError('生产配置尚未晋升图库经验；不能将检索结果写入任务')
    data = load_library(catalog)
    terms = set(tokens(query))
    if not terms:
        raise AgentError('请输入实际视觉关系或题材查询')
    docs = []
    for item in data['items']:
        observed = item.get('observation') or {}
        text = ' '.join(item['paths']).replace('_', ' ').replace('/', ' ')
        # Visually observed relations are stronger evidence than filenames.
        actual = ' '.join([observed.get('title', ''), observed.get('visible_facts', ''),
                           *observed.get('tags', []), *observed.get('transfer', [])])
        docs.append(Counter(tokens(text) + tokens(actual) * 3))
    df = Counter(t for doc in docs for t in doc)
    avg = sum(sum(d.values()) for d in docs) / max(len(docs), 1)
    hits = []
    for item, doc in zip(data['items'], docs):
        matched = sorted(terms & doc.keys())
        if not matched:
            continue
        score = sum(math.log(1 + (len(docs) - df[t] + .5)/(df[t] + .5)) * doc[t] * 2.2 /
                    (doc[t] + 1.2 * (.25 + .75 * sum(doc.values())/max(avg, 1))) for t in matched)
        try:
            source = item_path(data, item)
        except AgentError:
            continue
        hits.append({'id': item['id'], 'sha256': item['sha256'], 'path': str(source),
                     'score': round(score, 5), 'matched_terms': matched,
                     'evidence_kind': 'observed_relations' if item['observation'] else 'filename_only_unseen',
                     'observation': item['observation']})
    hits.sort(key=lambda h: (-h['score'], h['id']))
    result = {'method': 'local_visual_catalog_bm25_not_image_embeddings', 'query': query,
              'catalog': str(catalog.resolve()), 'hits': hits[:max(1, min(limit, 20))],
              'notice': '排序只表示文字相关。实际打开候选、判断迁移范围，再attach和observe。'}
    if run is not None:
        from .workflow import read_packet, save_packet, ensure_references_mutable
        packet = read_packet(run)
        ensure_references_mutable(run)
        packet['visual_retrieval'] = result
        save_packet(packet, run)
    return result


def attach(catalog: Path, ident: str, run: Path, reference_id: str, note: str, rights: str,
           usage: str = 'evaluation_only') -> dict:
    from .workflow import read_packet, add_reference, save_packet
    if usage not in ('generation', 'evaluation_only'):
        raise AgentError('usage必须是generation或evaluation_only')
    if usage == 'generation' and rights != 'user_authorized':
        raise AgentError('generation用途要求rights=user_authorized；默认仅允许evaluation_only分析')
    data = load_library(catalog)
    item = next((i for i in data['items'] if i['id'] == ident), None)
    packet = read_packet(run)
    retrieval = packet.get('visual_retrieval', {})
    if (item is None or retrieval.get('catalog') != str(catalog.resolve()) or
            not any(h['id'] == ident and h['sha256'] == item['sha256'] for h in retrieval.get('hits', []))):
        raise AgentError('先对当前任务执行 library search --run，再选择实际检索到的图片')
    source = item_path(data, item)
    packet = add_reference(run, source, reference_id, 'aesthetic_positive', note, rights,
                           evaluation_only=(usage != 'generation'), max_edge=2048)
    packet['references'][-1]['library_source'] = {'id': ident, 'sha256': item['sha256'],
                                                'query': retrieval['query'],
                                                'observation': item['observation'],
                                                'usage': usage}
    return save_packet(packet, run)
