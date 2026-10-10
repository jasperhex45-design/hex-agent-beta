"""Offline structural retrieval. Museum descriptions are not visual observations."""
from __future__ import annotations
import re
from pathlib import Path
from urllib.parse import urlsplit
from ..io import AgentError, read_json, digest
from .models import ArtCase

DEFAULT_CATALOG = Path(__file__).resolve().parents[2] / 'knowledge' / 'art_structure' / 'cases.json'
DEFAULT_METHOD_MAP = Path(__file__).resolve().parents[2] / 'knowledge' / 'design' / 'cultural_grammar_method_map.json'
DEFAULT_GRAMMAR_CATALOG = Path(__file__).resolve().parents[2] / 'knowledge' / 'design' / 'cultural_grammars.json'
SOURCE_HOSTS = {'www.metmuseum.org', 'www.moma.org', 'theme.npm.edu.tw',
                'artsandculture.google.com', 'www.museivaticani.va', 'www.nationalgallery.org.uk'}

def load_catalog(path: Path = DEFAULT_CATALOG) -> list[ArtCase]:
    data = read_json(path)
    if data.get('format') != 'liu.art-structure-cases.v1':
        raise AgentError('未知艺术案例格式')
    cases = [ArtCase.model_validate(x) for x in data['cases']]
    if len(cases) > 1000 or len({c.id for c in cases}) != len(cases):
        raise AgentError('案例过多或案例ID重复')
    for case in cases:
        url = urlsplit(case.source_url)
        if url.scheme != 'https' or url.hostname not in SOURCE_HOSTS or url.username or url.password:
            raise AgentError('案例来源须为已核验馆方HTTPS页面')
    return cases


def load_method_map(path: Path = DEFAULT_METHOD_MAP, grammar_path: Path = DEFAULT_GRAMMAR_CATALOG,
                    cases: list[ArtCase] | None = None) -> dict[str, dict]:
    """Load the explicit case-to-grammar seam; never infer it from artist names."""
    data = read_json(path)
    if data.get('format') != 'liu.art-structure-method-map.v1':
        raise AgentError('未知艺术案例-结构语法映射格式')
    grammars = read_json(grammar_path)
    grammar_ids = {x['id'] for x in grammars}
    grammar_domains = {
        x['id']: list(dict.fromkeys(x.get('domains', [])))
        for x in grammars
    }
    method_ids = {x.method_id for x in (cases or load_catalog())}
    result = {}
    for row in data.get('mappings', []):
        method_id = row.get('method_id')
        linked = row.get('grammar_ids', [])
        if method_id not in method_ids or not linked or not set(linked).issubset(grammar_ids):
            raise AgentError('艺术案例-结构语法映射引用不存在的ID')
        if method_id in result:
            raise AgentError('艺术案例-结构语法映射重复')
        if row.get('confidence') not in ('high', 'medium', 'low'):
            raise AgentError('艺术案例-结构语法映射缺少置信度')
        result[method_id] = {
            'method_id': method_id,
            'grammar_ids': list(linked),
            # Keep the domain information next to the explicit seam so
            # retrieval can apply the same task boundary as grammar search.
            'grammar_domains': {gid: grammar_domains[gid] for gid in linked},
            'domains': sorted({domain for gid in linked for domain in grammar_domains[gid]}),
            'confidence': row['confidence'],
            'rationale': row.get('rationale', ''),
            'gap': row.get('gap', ''),
        }
    missing = method_ids - set(result)
    if missing:
        raise AgentError('艺术案例-结构语法映射缺项：' + ','.join(sorted(missing)))
    return result

def _terms(text: str) -> set[str]:
    words = set(re.findall(r'[a-z0-9_]+', text.lower()))
    for part in re.findall(r'[\u4e00-\u9fff]+', text):
        words.update(part[i:i+2] for i in range(len(part)-1))
        if len(part) == 1:
            words.add(part)
    return words

def retrieve(cases: list[ArtCase], query: str, allowed_operations: list[str],
             *, scope: str = 'create', limit: int = 4,
             method_map: dict[str, dict] | None = None,
             domain: str | None = None) -> dict:
    if type(limit) is not int or not 1 <= limit <= 4:
        raise AgentError('每次只允许1至4个艺术案例')
    q, allowed = _terms(query), set(allowed_operations)
    scored = []
    for case in cases:
        if not allowed.intersection(case.operations):
            continue
        if scope != 'create' and 'camera' not in case.modes:
            continue
        # A method is domain-safe when at least one of its linked grammar
        # cards explicitly supports the requested task domain.  ``auto`` is
        # the brief's unresolved domain and intentionally keeps the old
        # unfiltered behavior.  If a caller supplies no map, retain backward
        # compatibility: there is no trustworthy domain metadata to filter.
        if domain and domain != 'auto' and method_map is not None:
            supported = set(method_map.get(case.method_id, {}).get('domains', []))
            compatible = ('hybrid' in supported or {'person', 'vehicle'}.issubset(supported)
                          if domain == 'hybrid' else domain in supported)
            if not compatible:
                continue
        # Deliberately exclude artist/title/tradition: retrieve a structural
        # problem, not the fame of a name or an East/West stereotype.
        terms = _terms(' '.join(case.keywords))
        overlap = q.intersection(terms)
        if overlap:
            scored.append((len(overlap) / max(len(terms), 1)**0.5, case, sorted(overlap)))
    scored.sort(key=lambda row: (-row[0], row[1].id))
    hits = []
    for _, c, terms in scored[:limit]:
        hit = {'case': c.model_dump(), 'matched_terms': terms}
        if method_map is not None:
            hit['grammar_links'] = method_map.get(c.method_id, {'grammar_ids': [], 'confidence': 'low',
                                                                 'rationale': '', 'gap': '未提供映射'})
        hits.append(hit)
    return {'format': 'liu.art-structure-context.v1', 'query': query,
            'method': 'bounded_bilingual_structural_lexical_retrieval',
            'domain': domain or 'auto',
            'hits': hits, 'catalog_digest': digest([c.model_dump() for c in cases]),
            'notice': '这是有来源的设计假设，不是已看图参考；零命中不伪造，不会自动加入生成输入。'}

def context_for(payload: dict, cases: list[ArtCase], query: str = '', limit: int = 4,
                *, domain: str | None = None) -> dict:
    need = payload['need']
    text = query.strip() or ' '.join([need['intent'], *need.get('desired_feeling', []),
                                     *[x['query'] for x in need.get('knowledge_needs', [])]])
    method_map = load_method_map(cases=cases)
    task_domain = domain if domain is not None else payload.get('brief', {}).get('domain')
    if task_domain in (None, ''):
        task_domain = need.get('domain')
    return retrieve(cases, text, payload['allowed_operations'],
                    scope=payload['brief']['scope'], limit=limit, method_map=method_map,
                    domain=task_domain)
