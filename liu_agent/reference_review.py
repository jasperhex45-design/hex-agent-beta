"""Build a portable, evidence-bound review sheet for a private reference catalog.

The sheet is deliberately offline. A click creates a pending user choice; only
an explicitly downloaded JSON package can subsequently be imported by HEX.
Cached ``thumbnail_path`` values are not trusted: previews are regenerated from
the exact source bytes verified against ``source_sha256``.
"""
from __future__ import annotations

import base64
import hashlib
import html
import io
import json
import re
import warnings
from collections import Counter
from pathlib import Path
from urllib.parse import urlsplit

from PIL import Image, ImageOps, UnidentifiedImageError

from .io import AgentError


THUMB_MAX_EDGE = 640
THUMB_MAX_BYTES = 160_000
SOURCE_MAX_BYTES = 64 * 1024 * 1024
SOURCE_MAX_PIXELS = 48_000_000

_DOMAINS = {
    "person": "人像", "illustration": "插画", "poster": "平面设计",
    "vehicle": "汽车", "hybrid": "混合题材", "character": "角色",
    "prop": "道具", "unknown": "尚未分类",
}
_ASPECTS = {
    "palette": "配色", "light": "光影", "composition": "构图",
    "material": "质感", "expression": "表演", "overall": "整体",
}
_SIGNALS = {"prefer": "喜欢", "avoid": "避开", "do_not_recommend": "不再推荐此图"}
_IMAGE_MESSAGES = {
    "missing": "原图未读取：文件不存在，请重新导入。",
    "changed": "原图已变更：与入库版本不一致，请重新导入后再反馈。",
    "invalid_hash": "原图未核验：缺少有效的来源摘要，请重新导入。",
    "unreadable": "原图未读取：当前文件无法解码为图片。",
    "oversize": "原图未读取：超出整理页的安全读取范围，请提供较小副本。",
}


def _text(value: object, default: str = "") -> str:
    return value if isinstance(value, str) else default


def _strings(value: object) -> list[str]:
    return [x for x in value if isinstance(x, str) and x.strip()] if isinstance(value, list) else []


def _esc(value: object) -> str:
    return html.escape(_text(value), quote=True)


def _safe_json(value: object) -> str:
    # A JSON script tag is still parsed by the HTML tokenizer. Escaping '<'
    # prevents </script>, including mixed-case variants, from ending the tag.
    return (json.dumps(value, ensure_ascii=False, separators=(",", ":"))
            .replace("&", "\\u0026").replace("<", "\\u003c").replace(">", "\\u003e")
            .replace("\u2028", "\\u2028").replace("\u2029", "\\u2029"))


def _source_url(value: object) -> str:
    candidate = _text(value).strip()
    if any(ord(char) < 32 for char in candidate):
        return ""
    try:
        parsed = urlsplit(candidate)
        if parsed.scheme.lower() in {"https", "http"} and parsed.netloc and not parsed.username and not parsed.password:
            return candidate
    except ValueError:
        pass
    return ""


def _preview(reference: dict) -> tuple[str, str]:
    expected = _text(reference.get("source_sha256"))
    if not re.fullmatch(r"[0-9a-f]{64}", expected):
        return "invalid_hash", ""
    source_value = _text(reference.get("source_path"))
    if not source_value:
        return "missing", ""
    try:
        source = Path(source_value)
        if not source.is_file():
            return "missing", ""
        if source.stat().st_size > SOURCE_MAX_BYTES:
            return "oversize", ""
        with source.open("rb") as handle:
            raw = handle.read(SOURCE_MAX_BYTES + 1)
        if len(raw) > SOURCE_MAX_BYTES:
            return "oversize", ""
    except (OSError, ValueError):
        return "missing", ""
    if hashlib.sha256(raw).hexdigest() != expected:
        return "changed", ""
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("error", Image.DecompressionBombWarning)
            with Image.open(io.BytesIO(raw)) as original:
                if original.width * original.height > SOURCE_MAX_PIXELS:
                    return "oversize", ""
                original.seek(0)
                original.draft("RGB", (THUMB_MAX_EDGE, THUMB_MAX_EDGE))
                oriented = ImageOps.exif_transpose(original)
                oriented.thumbnail((THUMB_MAX_EDGE, THUMB_MAX_EDGE), Image.Resampling.LANCZOS)
                rgba = oriented.convert("RGBA")
                canvas = Image.new("RGB", rgba.size, (245, 244, 240))
                canvas.paste(rgba, mask=rgba.getchannel("A"))
                # Every preview has bounded dimensions and a bounded payload.
                # Re-encoding also removes source metadata and embedded profiles.
                while True:
                    for quality in (85, 72, 58, 42):
                        buffer = io.BytesIO()
                        canvas.save(buffer, format="JPEG", quality=quality, optimize=True)
                        encoded = buffer.getvalue()
                        if len(encoded) <= THUMB_MAX_BYTES:
                            return "ready", "data:image/jpeg;base64," + base64.b64encode(encoded).decode("ascii")
                    if max(canvas.size) <= 96:
                        return "oversize", ""
                    canvas.thumbnail((max(1, int(canvas.width * .75)), max(1, int(canvas.height * .75))),
                                     Image.Resampling.LANCZOS)
    except (Image.DecompressionBombWarning, Image.DecompressionBombError):
        return "oversize", ""
    except (UnidentifiedImageError, OSError, ValueError, SyntaxError):
        return "unreadable", ""


def _list_section(title: str, values: object) -> str:
    rows = _strings(values)
    if not rows:
        return ""
    return f'<div class="analysis-group"><h4>{_esc(title)}</h4><ul>' + "".join(f"<li>{_esc(x)}</li>" for x in rows) + "</ul></div>"


def _card_analysis(card: dict | None, image_state: str) -> str:
    if not card:
        return '<div class="analysis-empty">尚无看图分析。HEX 实际读取原图后，再整理可见事实和可借用的因素。</div>'
    warning = ('<p class="inline-warning">以下是既有分析；当前原图未通过核验，需重新读取后复核。</p>'
               if image_state != "ready" else "")
    facts, decision = _text(card.get("visible_facts")), _text(card.get("dominant_decision"))
    body = warning
    if facts:
        body += f'<div class="analysis-group"><h4>可见事实</h4><p>{_esc(facts)}</p></div>'
    if decision:
        body += f'<div class="analysis-group"><h4>可借用的决定</h4><p class="decision">{_esc(decision)}</p></div>'
    body += _list_section("支持这个决定的因素", card.get("supporting_mechanisms"))
    body += _list_section("适用条件", card.get("transfer_conditions"))
    body += _list_section("不要迁移", card.get("do_not_transfer"))
    if _text(card.get("visible_test")):
        body += f'<div class="analysis-group"><h4>怎样检查</h4><p>{_esc(card["visible_test"])}</p></div>'
    body += _list_section("失败信号", card.get("failure_signals"))
    return f'<details class="analysis"><summary>查看风格卡 <span>事实 / 借用 / 边界</span></summary><div class="analysis-body">{body}</div></details>'


def _saved_preferences(values: object, project_scope: str) -> str:
    if not isinstance(values, list):
        return ""
    rows = []
    for item in values:
        if not isinstance(item, dict):
            continue
        signal = _SIGNALS.get(item.get("signal"))
        aspect = _ASPECTS.get(item.get("aspect"))
        if not signal or not aspect:
            continue
        scope = _text(item.get("scope"), "未知范围")
        label = "长期偏好" if scope == "global" else ("仅此项目" if scope == project_scope else scope)
        quote = _text(item.get("user_quote"))
        withdrawn = ' <span class="muted">已撤回</span>' if item.get("withdrawn") or item.get("status") == "withdrawn" else ""
        rows.append(f'<li><strong>{_esc(signal)} · {_esc(aspect)}</strong><span>{_esc(label)}{withdrawn}</span>'
                    + (f'<p>“{_esc(quote)}”</p>' if quote else "") + "</li>")
    if not rows:
        return ""
    return f'<details class="saved"><summary>已有反馈 · {len(rows)} <span>不重复导出</span></summary><ul>{"".join(rows)}</ul></details>'


def _scope_options(project_scope: str) -> str:
    if project_scope != "global":
        return f'<option value="{_esc(project_scope)}">仅此项目</option><option value="global">长期偏好</option>'
    return '<option value="global">长期偏好</option>'


def _reference_html(reference: dict, index: int, image_state: str, data_url: str,
                    project_scope: str) -> str:
    card = reference.get("card") if isinstance(reference.get("card"), dict) else None
    title = _text(reference.get("title")) or _text((card or {}).get("title")) or "未命名参考"
    domain = _text((card or {}).get("domain"), "unknown")
    if domain not in _DOMAINS:
        domain = "unknown"
    status = "curated" if reference.get("status") == "curated" else "candidate"
    status_label = "已整理" if status == "curated" else "待整理"
    boards = _strings(reference.get("boards"))
    search = " ".join([title, *boards, *_strings((card or {}).get("tags")),
                       _text((card or {}).get("visible_facts")), _text((card or {}).get("dominant_decision"))])
    url = _source_url(reference.get("source_url"))
    link = f'<a class="source-link" href="{_esc(url)}" target="_blank" rel="noopener noreferrer" referrerpolicy="no-referrer">原始来源 ↗</a>' if url else ""
    if image_state == "ready":
        media = f'<img src="{data_url}" alt="{_esc(title)}" loading="lazy" decoding="async">'
    else:
        media = f'<div class="image-unavailable"><span aria-hidden="true">◇</span><p>{_esc(_IMAGE_MESSAGES[image_state])}</p></div>'
    aspect_options = '<option value="">选择视觉因素</option>' + "".join(f'<option value="{k}">{v}</option>' for k, v in _ASPECTS.items())
    signal_options = '<option value="">选择你的判断</option>' + "".join(f'<option value="{k}">{v}</option>' for k, v in _SIGNALS.items())
    unavailable = ' disabled' if image_state != "ready" else ""
    board_html = f'<p class="boards">{_esc(" / ".join(boards))}</p>' if boards else '<p class="boards muted">未指定集合</p>'
    return f'''<article class="reference-card" data-index="{index}" data-status="{status}" data-domain="{domain}" data-search="{_esc(search)}">
  <figure class="preview" data-image-state="{image_state}">{media}<span class="sequence">{index + 1:03d}</span></figure>
  <div class="card-body"><div class="card-meta"><span class="badge {status}">{status_label}</span><span>{_DOMAINS[domain]}</span>{link}</div>
    <h3>{_esc(title)}</h3>{board_html}
    {_card_analysis(card, image_state)}
    {_saved_preferences(reference.get("preferences"), project_scope)}
    <fieldset class="feedback-form" aria-label="为这张参考添加反馈"{unavailable}>
      <legend>留下你的判断</legend>
      <div class="form-row"><label>视觉因素<select class="aspect">{aspect_options}</select></label><label>你的判断<select class="signal">{signal_options}</select></label></div>
      <label>应用范围<select class="scope">{_scope_options(project_scope)}</select></label>
      <label>补充一句 <span class="optional">选填，保留你的原话</span><textarea class="quote" rows="2" maxlength="2000" placeholder="例如：喜欢这个光影，但肤色不要偏橙。"></textarea></label>
      <button class="add-feedback" type="button" disabled>先选择因素和判断</button>
    </fieldset>
    <ul class="pending-list" aria-label="本图待导出反馈"></ul>
  </div>
</article>'''


_CSS = """
:root{color-scheme:light;--paper:#f5f4f0;--card:#fffefa;--ink:#202823;--muted:#777b72;--line:#dddeda;--accent:#345c44;--soft:#e9efe6;--orange:#986a3d}
*{box-sizing:border-box}body{margin:0;background:var(--paper);color:var(--ink);font-family:Inter,"Segoe UI","PingFang SC","Microsoft YaHei",sans-serif;line-height:1.65}button,select,textarea,input{font:inherit;color:inherit}button,a,select{touch-action:manipulation}button{cursor:pointer}button:disabled{cursor:not-allowed;opacity:.5}button:focus-visible,a:focus-visible,select:focus-visible,textarea:focus-visible,input:focus-visible,summary:focus-visible{outline:3px solid #7ba88c;outline-offset:3px}[hidden]{display:none!important}
.shell{max-width:1540px;margin:auto;padding:32px 38px 150px}.brand{display:flex;align-items:center;gap:14px;letter-spacing:.16em;font-size:11px;font-weight:700}.brand-mark{background:var(--ink);color:var(--paper);padding:8px 12px;border-radius:6px;font-size:15px;letter-spacing:.1em}.brand span:last-child{color:var(--muted)}.hero{display:flex;gap:36px;align-items:flex-end;justify-content:space-between;margin:38px 0 30px}.eyebrow{color:var(--accent);font-size:12px;font-weight:650;letter-spacing:.16em;margin:0 0 10px}h1{font-size:clamp(28px,3.2vw,44px);letter-spacing:-.04em;line-height:1.25;font-weight:650;margin:0 0 17px}.lead{max-width:640px;color:#60675e;font-size:14px;margin:0}.collection-label{font-size:12px;margin-top:13px;color:var(--muted);overflow-wrap:anywhere}.collection-label strong{color:var(--ink);font-weight:500}.stats{display:flex;gap:26px;flex-shrink:0;padding-bottom:4px}.stat strong{display:block;font-size:31px;font-weight:500;line-height:1.2;font-variant-numeric:tabular-nums}.stat span{font-size:12px;color:var(--muted)}.toolbar{display:flex;align-items:center;gap:12px;flex-wrap:wrap;border-top:1px solid var(--line);padding:21px 0}.search{flex:1;min-width:220px;position:relative}.search input{width:100%;border:1px solid var(--line);border-radius:7px;padding:11px 14px;background:#fffefa}.filter{display:flex;align-items:center;gap:8px;font-size:12px;color:var(--muted)}.filter select{width:auto;min-width:112px}.result-count{font-size:12px;color:var(--muted);white-space:nowrap}.grid{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:22px;align-items:start}.reference-card{min-width:0;background:var(--card);border:1px solid var(--line);border-radius:12px;overflow:hidden;box-shadow:0 3px 12px #20282304}.preview{position:relative;aspect-ratio:4/3;background:#e9e9e3;margin:0;display:flex;align-items:center;justify-content:center;overflow:hidden}.preview img{width:100%;height:100%;object-fit:contain;display:block}.sequence{position:absolute;left:12px;top:12px;padding:3px 8px;border-radius:4px;background:#fffdf8e8;color:#596154;font-size:10px;letter-spacing:.1em;font-variant-numeric:tabular-nums}.image-unavailable{display:flex;align-items:center;justify-content:center;flex-direction:column;padding:26px;color:#70786b;max-width:300px;text-align:center}.image-unavailable>span{font-size:42px;color:#8e9786;line-height:1}.image-unavailable p{font-size:12px;margin:15px 0 0}.card-body{padding:20px}.card-meta{display:flex;gap:9px;align-items:center;font-size:11px;color:var(--muted);flex-wrap:wrap}.badge{padding:2px 7px;border-radius:4px;font-size:10px}.curated{background:#e8eee4;color:#47613e}.candidate{background:#f1ece1;color:#876b44}.source-link{margin-left:auto;color:var(--muted);text-decoration:none}.source-link:hover{text-decoration:underline;color:var(--accent)}h3{font-size:17px;line-height:1.4;font-weight:620;margin:12px 0 6px;overflow-wrap:anywhere}.boards{font-size:11px;min-height:18px;margin:0 0 15px;color:var(--muted);overflow-wrap:anywhere}.muted{color:var(--muted)}.analysis{border-top:1px solid var(--line);border-bottom:1px solid var(--line)}summary{cursor:pointer;font-size:12px;padding:12px 0;font-weight:550}summary span{font-size:10px;color:var(--muted);font-weight:400;margin-left:8px}.analysis-body{padding-bottom:8px}.analysis-group{margin-bottom:13px}.analysis-group h4{font-size:10px;letter-spacing:.04em;color:var(--muted);font-weight:550;margin:0 0 5px}.analysis-group p,.analysis-group ul{font-size:12px;line-height:1.8;margin:0;overflow-wrap:anywhere}.analysis-group ul{padding-left:16px}.decision{font-weight:550}.inline-warning{font-size:11px;color:var(--orange);padding:8px 10px;background:#f5ede2;border-radius:5px}.analysis-empty{padding:13px 0;border-block:1px solid var(--line);color:var(--muted);font-size:11px}.saved{border-bottom:1px solid var(--line)}.saved ul{list-style:none;padding:0;margin:0 0 12px}.saved li{padding:6px 0;font-size:11px;overflow-wrap:anywhere}.saved li>strong{font-weight:500;margin-right:8px}.saved li>span{color:var(--muted)}.saved li p{margin:4px 0;font-size:11px;color:var(--muted)}fieldset{padding:0;border:0;min-width:0;margin:19px 0 0}legend{font-size:12px;font-weight:600;margin:0 0 10px;padding:0}.form-row{display:grid;grid-template-columns:1fr 1fr;gap:10px}label{display:block;font-size:10px;color:#6d7469;margin-bottom:10px}select,textarea{display:block;width:100%;border:1px solid var(--line);border-radius:5px;background:#fffefa;padding:8px 9px;margin-top:4px;font-size:11px;min-height:36px}textarea{resize:vertical;min-height:65px;line-height:1.6}textarea::placeholder{color:#a0a297}.optional{font-weight:400;color:#94978d;margin-left:6px}.add-feedback{width:100%;background:var(--soft);color:var(--accent);border:1px solid #d5dfce;border-radius:5px;padding:9px 10px;font-size:11px;font-weight:550}.add-feedback:not(:disabled):hover{background:#dde7d8}.pending-list{list-style:none;padding:0;margin:0}.pending-list li{display:flex;gap:8px;align-items:flex-start;background:#f0f3eb;border-left:2px solid #759361;padding:9px 10px;margin-top:10px;font-size:11px;line-height:1.65;border-radius:0 4px 4px 0}.pending-copy{flex:1;min-width:0;overflow-wrap:anywhere}.pending-copy strong{display:block;font-weight:550}.pending-copy p{margin:3px 0 0;color:#6a7263}.undo{background:none;border:0;font-size:10px;color:#6a7263;padding:0;white-space:nowrap}.undo:hover{text-decoration:underline}.empty-state{text-align:center;padding:75px 24px;border:1px dashed #cdd2c8;border-radius:12px;background:#fafbf6}.empty-state .empty-mark{font-size:36px;color:#82937a}.empty-state h2{font-size:21px;font-weight:500;margin:9px 0}.empty-state p{font-size:13px;color:var(--muted);margin:0}.bottom-bar{position:fixed;left:0;right:0;bottom:0;background:#fffef8f5;backdrop-filter:blur(12px);border-top:1px solid var(--line);z-index:5;padding:16px 38px}.bottom-inner{max-width:1464px;display:flex;gap:22px;justify-content:space-between;align-items:center;margin:auto}.pending-total{font-size:13px;font-weight:550}.pending-total strong{font-size:20px;font-weight:600;line-height:1;margin-right:5px}.export-notice{font-size:11px;color:var(--muted);margin:3px 0 0}.bottom-actions{display:flex;gap:12px;align-items:center;flex-shrink:0}.clear{background:none;border:0;color:var(--muted);font-size:12px;padding:10px}.export{background:var(--ink);border:1px solid var(--ink);border-radius:7px;color:#fffef8;font-size:12px;font-weight:550;padding:12px 22px}.export:not(:disabled):hover{background:var(--accent)}.live-status{font-size:12px;margin:12px 0 0;color:var(--accent);min-height:20px}.noscript{border:1px solid #d2ae7c;border-radius:6px;background:#fbf0df;padding:12px;font-size:13px}.scope-note{font-size:11px;color:var(--muted);margin:0 0 19px}.footer{font-size:11px;color:var(--muted);margin-top:26px;display:flex;justify-content:space-between;gap:16px}
@media(min-width:1450px){.grid{grid-template-columns:repeat(4,minmax(0,1fr))}.card-body{padding:17px}.form-row{gap:8px}}
@media(max-width:1080px){.grid{grid-template-columns:repeat(2,minmax(0,1fr))}.hero{gap:20px}.stats{gap:18px}}
@media(max-width:670px){.shell{padding:23px 18px 185px}.hero{display:block;margin:28px 0 23px}.stats{margin-top:23px;gap:29px}.stat strong{font-size:25px}.grid{grid-template-columns:1fr;gap:19px}.toolbar{gap:9px;padding:17px 0}.search{flex-basis:100%}.filter{flex:1}.filter select{flex:1;min-width:0}.result-count{flex-basis:100%}.bottom-bar{padding:13px 18px}.bottom-inner{display:block}.bottom-actions{margin-top:9px;justify-content:flex-end}.export{padding:10px 18px}.footer{display:block}.lead{font-size:13px}.brand{gap:10px;letter-spacing:.09em}}
@media(prefers-reduced-motion:reduce){*{scroll-behavior:auto!important}}
"""


_SCRIPT = r"""
(() => {
  'use strict';
  const payload = JSON.parse(document.getElementById('reference-data').textContent);
  const pending = [];
  const articles = Array.from(document.querySelectorAll('.reference-card'));
  const aspectLabels = {palette:'配色',light:'光影',composition:'构图',material:'质感',expression:'表演',overall:'整体'};
  const signalLabels = {prefer:'喜欢',avoid:'避开',do_not_recommend:'不再推荐此图'};
  const live = document.getElementById('live-status');
  const exportButton = document.getElementById('export-feedback');
  const clearButton = document.getElementById('clear-feedback');
  const notice = document.getElementById('export-notice');
  let exported = false;
  let exportId = null;
  let exportSequence = 0;
  function newExportId() {
    if (globalThis.crypto && typeof globalThis.crypto.randomUUID === 'function') return globalThis.crypto.randomUUID();
    if (globalThis.crypto && typeof globalThis.crypto.getRandomValues === 'function') {
      const bytes = globalThis.crypto.getRandomValues(new Uint8Array(16));
      bytes[6] = (bytes[6] & 15) | 64; bytes[8] = (bytes[8] & 63) | 128;
      const hex = Array.from(bytes, byte => byte.toString(16).padStart(2, '0')).join('');
      return `${hex.slice(0,8)}-${hex.slice(8,12)}-${hex.slice(12,16)}-${hex.slice(16,20)}-${hex.slice(20)}`;
    }
    // Older local-file browsers may not expose Web Crypto. The monotonic
    // sequence keeps distinct batches unique even if clock/random are stubbed.
    exportSequence++;
    return `hex-${Date.now().toString(36)}-${exportSequence.toString(36)}-${Math.random().toString(36).slice(2)}-${Math.random().toString(36).slice(2)}`;
  }
  function contentChanged() { exported = false; exportId = null; }
  const scopeLabel = value => value === 'global' ? '长期偏好' : '仅此项目';
  function announce(message) { live.textContent = message; }
  function refreshPending() {
    document.getElementById('pending-count').textContent = String(pending.length);
    exportButton.disabled = pending.length === 0;
    clearButton.disabled = pending.length === 0;
    notice.textContent = exported ? '已发起下载；请把反馈 JSON 交给 HEX 导入。此页未写入风格库。' : '导出反馈后由 HEX 导入。当前选择仅保留在此页面，关闭或刷新会丢失。';
    articles.forEach((article, index) => {
      const list = article.querySelector('.pending-list');
      list.replaceChildren();
      pending.forEach((event, eventIndex) => {
        if (event.reference_id !== payload.references[index].id) return;
        const item = document.createElement('li');
        const copy = document.createElement('div'); copy.className = 'pending-copy';
        const label = document.createElement('strong');
        label.textContent = `${signalLabels[event.signal]}${event.signal === 'do_not_recommend' ? '' : ' · ' + aspectLabels[event.aspect]} · ${scopeLabel(event.scope)}`;
        const quote = document.createElement('p'); quote.textContent = event.user_quote;
        copy.append(label, quote);
        const remove = document.createElement('button'); remove.type = 'button'; remove.className = 'undo'; remove.textContent = '撤销';
        remove.setAttribute('aria-label', '撤销这项待导出反馈');
        remove.addEventListener('click', () => {
          pending.splice(eventIndex, 1); contentChanged(); refreshPending();
          announce('已撤销当前页中的这一项选择；已下载的反馈文件不会被改写。');
        });
        item.append(copy, remove); list.append(item);
      });
    });
  }
  articles.forEach((article, index) => {
    const source = payload.references[index];
    const aspect = article.querySelector('.aspect');
    const signal = article.querySelector('.signal');
    const scope = article.querySelector('.scope');
    const quote = article.querySelector('.quote');
    const add = article.querySelector('.add-feedback');
    function updateAction() {
      if (signal.value === 'do_not_recommend') { aspect.value = 'overall'; aspect.disabled = true; }
      else { aspect.disabled = !source.reviewable; }
      const valid = source.reviewable && Object.hasOwn(aspectLabels, aspect.value) && Object.hasOwn(signalLabels, signal.value);
      add.disabled = !valid;
      add.textContent = valid ? (signal.value === 'do_not_recommend' ? '记录：不再推荐此图' : `记录：${signalLabels[signal.value]}${aspectLabels[aspect.value]}`) : '先选择因素和判断';
    }
    aspect.addEventListener('change', updateAction);
    signal.addEventListener('change', updateAction);
    add.addEventListener('click', () => {
      if (!source.reviewable || !Object.hasOwn(aspectLabels, aspect.value) || !Object.hasOwn(signalLabels, signal.value)) return;
      if (scope.value !== 'global' && scope.value !== payload.project_scope) return;
      const event = {reference_id:source.id,source_sha256:source.source_sha256,aspect:aspect.value,signal:signal.value,scope:scope.value,user_quote:quote.value.trim() || add.textContent};
      if (pending.some(previous => JSON.stringify(previous) === JSON.stringify(event))) { announce('这项相同的反馈已经在待导出列表中。'); return; }
      pending.push(event); contentChanged();
      signal.value = ''; aspect.value = ''; quote.value = ''; updateAction(); refreshPending();
      announce(`已加入 1 项待导出反馈。共 ${pending.length} 项；还未写入风格库。`);
    });
    updateAction();
  });
  function filterCards() {
    const query = document.getElementById('search-references').value.trim().toLocaleLowerCase();
    const status = document.getElementById('filter-status').value;
    const domain = document.getElementById('filter-domain').value;
    let count = 0;
    articles.forEach(article => {
      const visible = (!status || article.dataset.status === status) && (!domain || article.dataset.domain === domain) && (!query || article.dataset.search.toLocaleLowerCase().includes(query));
      article.hidden = !visible; if (visible) count++;
    });
    document.getElementById('result-count').textContent = `显示 ${count} / ${articles.length} 张`;
    document.getElementById('no-results').hidden = count > 0 || articles.length === 0;
  }
  document.getElementById('search-references').addEventListener('input', filterCards);
  document.getElementById('filter-status').addEventListener('change', filterCards);
  document.getElementById('filter-domain').addEventListener('change', filterCards);
  clearButton.addEventListener('click', () => {
    pending.splice(0); contentChanged(); refreshPending();
    announce('已清空当前页选择。已有风格库记录和已下载文件未改变。');
  });
  exportButton.addEventListener('click', () => {
    if (!pending.length) return;
    if (exportId === null) exportId = newExportId();
    const bundle = {format:'hex.reference-feedback.v1',collection_id:payload.collection_id,export_id:exportId,events:pending.map(event => ({...event}))};
    const blob = new Blob([JSON.stringify(bundle, null, 2) + '\n'], {type:'application/json;charset=utf-8'});
    const url = URL.createObjectURL(blob);
    const link = document.createElement('a');
    link.href = url; link.download = 'hex-reference-feedback-' + new Date().toISOString().replace(/[:.]/g, '-') + '.json';
    document.body.append(link); link.click(); link.remove();
    setTimeout(() => URL.revokeObjectURL(url), 1000);
    exported = true; refreshPending();
    announce(`已发起 ${pending.length} 项反馈的文件下载。把 JSON 交给 HEX 导入后，才会更新风格库。`);
  });
  refreshPending(); filterCards();
})();
"""


def render_review(output: Path, references: list[dict], *, collection_id: str,
                  project_scope: str = "global") -> dict:
    """Render verified local previews and a user-operated feedback JSON exporter.

    ``references`` need ``id``, ``source_path`` and ``source_sha256``. Optional
    display fields are ``title``, ``source_url``, ``boards``, ``status``, a
    dictionary ``card`` and a list of prior ``preferences``. Prior preferences
    are displayed as records and never copied into new pending feedback.

    Missing, changed and unreadable images remain visible as honest placeholders
    but cannot receive new feedback. The caller still validates imported events
    against its current catalog; an offline page is not an authorization token.
    """
    if not isinstance(collection_id, str) or not collection_id.strip():
        raise AgentError("整理页需要 collection_id")
    if not isinstance(project_scope, str) or not project_scope.strip():
        raise AgentError("整理页需要有效的项目范围或 global")
    if not isinstance(references, list):
        raise AgentError("整理页的 references 必须为列表")
    output = Path(output).expanduser().resolve()
    seen: set[str] = set()
    for reference in references:
        ident = _text(reference.get("id")) if isinstance(reference, dict) else ""
        if not ident.strip() or ident in seen:
            raise AgentError("整理页的每张参考必须有唯一、非空的 id")
        seen.add(ident)
        for field in ("source_path", "thumbnail_path"):
            value = _text(reference.get(field))
            if value:
                try:
                    if Path(value).expanduser().resolve() == output:
                        raise AgentError("整理页输出不能覆盖参考原图或缩略图")
                except (OSError, ValueError):
                    pass
    cards, bindings = [], []
    image_states: Counter = Counter()
    domains: set[str] = set()
    for index, reference in enumerate(references):
        state, preview = _preview(reference)
        image_states[state] += 1
        cards.append(_reference_html(reference, index, state, preview, project_scope))
        bindings.append({"id": reference["id"], "source_sha256": _text(reference.get("source_sha256")), "reviewable": state == "ready"})
        card = reference.get("card") if isinstance(reference.get("card"), dict) else {}
        domain = _text(card.get("domain"), "unknown")
        domains.add(domain if domain in _DOMAINS else "unknown")
    curated = sum(reference.get("status") == "curated" for reference in references)
    domain_options = '<option value="">全部类别</option>' + "".join(f'<option value="{key}">{label}</option>' for key, label in _DOMAINS.items() if key in domains)
    scope_note = (f'当前项目：<strong>{_esc(project_scope)}</strong>。新增反馈默认仅用于此项目；选择“长期偏好”才会跨项目使用。'
                  if project_scope != "global" else '当前未指定项目，反馈范围为“长期偏好”。如果只想用于本次创作，请让 HEX 按该项目重新生成整理页。')
    empty = ('<section class="empty-state"><span class="empty-mark" aria-hidden="true">◇</span><h2>还没有导入参考图</h2><p>先导入可用的本地图片或已获准的来源，再在这里查看风格卡、留下反馈。</p></section>'
             if not references else "")
    payload = _safe_json({"collection_id": collection_id, "project_scope": project_scope, "references": bindings})
    # The CSP permits this standalone sheet's inline application code, data-image
    # previews and blob download; arbitrary remote embeds and network calls fail.
    page = f'''<!doctype html>
<html lang="zh-CN"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<meta http-equiv="Content-Security-Policy" content="default-src 'none'; img-src data:; style-src 'unsafe-inline'; script-src 'unsafe-inline'; connect-src 'none'; base-uri 'none'; form-action 'none'">
<meta name="referrer" content="no-referrer"><title>HEX · 私人视觉参考库</title><style>{_CSS}</style></head>
<body><main class="shell"><div class="brand"><span class="brand-mark">HEX</span><span>REFERENCE LIBRARY / 私人视觉参考</span></div>
<header class="hero"><div><p class="eyebrow">把收藏变成可用的视觉经验</p><h1>把喜欢的理由留下来。</h1><p class="lead">看原图，选出想保留或避开的视觉因素。每一项反馈都保留适用范围，由你的判断决定它如何帮助下一次创作。</p><p class="collection-label">当前集合 <strong>{_esc(collection_id)}</strong></p></div>
<div class="stats"><div class="stat"><strong>{len(references)}</strong><span>张参考</span></div><div class="stat"><strong>{image_states['ready']}</strong><span>张可查看</span></div><div class="stat"><strong>{curated}</strong><span>张已整理</span></div></div></header>
<noscript><p class="noscript">图片与风格卡可直接查看。选择、筛选和导出反馈需要浏览器允许此本地 HTML 运行 JavaScript；目前没有反馈写入风格库。</p></noscript>
<section class="toolbar" aria-label="筛选参考"><label class="search"><span class="sr-only" hidden>搜索参考</span><input id="search-references" type="search" placeholder="搜索标题、画板或视觉因素" aria-label="搜索标题、画板或视觉因素"></label>
<label class="filter">状态<select id="filter-status"><option value="">全部状态</option><option value="curated">已整理</option><option value="candidate">待整理</option></select></label>
<label class="filter">类别<select id="filter-domain">{domain_options}</select></label><span id="result-count" class="result-count">显示 {len(references)} / {len(references)} 张</span></section>
<p class="scope-note">{scope_note}</p>{empty}<section class="grid" aria-label="参考图与风格卡">{"".join(cards)}</section>
<section class="empty-state" id="no-results" hidden><h2>没有匹配的参考</h2><p>调整关键词或筛选条件；已选反馈会保留。</p></section>
<p class="live-status" id="live-status" role="status" aria-live="polite"></p><footer class="footer"><span>预览保留完整构图；来源变更或图片未读取时，会暂停本图反馈。</span><span>由你选择 · 由 HEX 记录</span></footer></main>
<aside class="bottom-bar" aria-label="导出反馈"><div class="bottom-inner"><div><div class="pending-total"><strong id="pending-count">0</strong> 项待导出反馈</div><p class="export-notice" id="export-notice">导出反馈后由 HEX 导入。当前选择仅保留在此页面，关闭或刷新会丢失。</p></div><div class="bottom-actions"><button id="clear-feedback" class="clear" type="button" disabled>清空本页选择</button><button id="export-feedback" class="export" type="button" disabled>导出反馈 JSON ↗</button></div></div></aside>
<script type="application/json" id="reference-data">{payload}</script><script>{_SCRIPT}</script></body></html>'''
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(page, encoding="utf-8")
    return {"output": str(output), "references": len(references), "previews_ready": image_states["ready"],
            "image_failures": {key: count for key, count in sorted(image_states.items()) if key != "ready" and count},
            "pending_feedback": 0, "status": "review_ready"}
