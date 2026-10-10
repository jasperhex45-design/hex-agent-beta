"""Filesystem primitives. No user asset is modified in place."""
from __future__ import annotations
import hashlib
import io
import json
import os
import tempfile
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from PIL import Image, ImageOps, ImageCms, UnidentifiedImageError

ROOT = Path(__file__).resolve().parents[1]
MAX_IMAGE_BYTES = 16 * 1024 * 1024
MAX_EVALUATION_SOURCE_BYTES = 64 * 1024 * 1024
MAX_IMAGE_PIXELS = 32_000_000

class AgentError(RuntimeError):
    """An actionable error suitable for displaying without a credential traceback."""


def now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def read_json(path: Path):
    if path.stat().st_size > 4 * 1024 * 1024:
        raise AgentError("JSON 文件超过 4 MiB 限制")
    return json.loads(path.read_text(encoding="utf-8-sig"))


def write_json(path: Path, value) -> None:
    atomic_text(path, json.dumps(value, ensure_ascii=False, indent=2) + "\n")


def _atomic_parent(path: Path) -> Path:
    parent = path.parent
    if os.name != "nt":
        return parent
    absolute = os.path.abspath(parent)
    # Reserve room for the sibling temporary name and Windows' directory
    # limit. UTF-16 code units also account for non-BMP characters in paths.
    if absolute.startswith(("\\\\?\\", "\\\\.\\")) or len(absolute.encode("utf-16-le", "surrogatepass")) // 2 < 240:
        return parent
    extended = "\\\\?\\UNC\\" + absolute[2:] if absolute.startswith("\\\\") else "\\\\?\\" + absolute
    return Path(extended)


def atomic_text(path: Path, text: str, *, newline: str | None = None) -> None:
    parent = _atomic_parent(path)
    parent.mkdir(parents=True, exist_ok=True)
    temporary = tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", newline=newline, dir=parent,
                                            prefix=".liu-", suffix=".tmp", delete=False)
    tmp = Path(temporary.name)
    failure = None
    try:
        with temporary:
            temporary.write(text)
        for attempt in range(3):
            try:
                os.replace(tmp, path)
                break
            except PermissionError as exc:
                # Only Windows sharing/lock violations identify a retryable
                # local conflict. Access denied (5) has no such guarantee.
                if getattr(exc, "winerror", None) not in (32, 33) or attempt == 2:
                    raise
                time.sleep(0.05 * (attempt + 1))
    except BaseException as exc:
        failure = exc
        raise
    finally:
        try:
            tmp.unlink(missing_ok=True)
        except OSError as cleanup_error:
            if failure is None:
                raise
            failure.add_note(f"Temporary file cleanup failed for {tmp}: {cleanup_error}")


def sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def digest(obj) -> str:
    return sha(json.dumps(obj, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8"))


def within(root: Path, relative: str) -> Path:
    """Resolve references inside a task packet; refuse traversal and symlink escapes."""
    root = root.resolve()
    result = (root / relative).resolve()
    if not result.is_relative_to(root) or result == root:
        raise AgentError("文件路径超出任务目录")
    return result


def new_run(root: Path) -> Path:
    root.mkdir(parents=True, exist_ok=True)
    run = root / (datetime.now().strftime("%Y%m%d-%H%M%S") + "-" + uuid.uuid4().hex[:8])
    run.mkdir()
    return run


def normalize_image(data: bytes, max_edge: int | None = None) -> bytes:
    """Produce a metadata-free PNG copy, including explicit ICC->sRGB conversion."""
    return _normalize_image(data, max_edge, MAX_IMAGE_BYTES)


def normalize_evaluation_image(data: bytes, max_edge: int) -> bytes:
    """Bounded local analysis copy; normal output and pixel limits still apply."""
    if not 256 <= max_edge <= 4096:
        raise AgentError('评价工作副本最长边须在256至4096之间')
    return _normalize_image(data, max_edge, MAX_EVALUATION_SOURCE_BYTES)


def _normalize_image(data: bytes, max_edge: int | None, source_byte_limit: int) -> bytes:
    if len(data) > source_byte_limit:
        raise AgentError(f"参考图超过 {source_byte_limit // (1024 * 1024)} MiB；请另存一份较小的测试副本")
    try:
        with Image.open(io.BytesIO(data)) as original:
            if original.format not in ("PNG", "JPEG", "WEBP"):
                raise AgentError("只接受 PNG/JPEG/WEBP 图片，不接受 SVG 或伪图片")
            if original.width * original.height > MAX_IMAGE_PIXELS:
                raise AgentError("图片超过 3200 万像素测试限制")
            if getattr(original, "n_frames", 1) != 1:
                raise AgentError("请使用单帧图片")
            if original.mode not in ("RGB", "RGBA", "L", "LA"):
                raise AgentError("请先另存为 8 位 RGB/RGBA 图片；不静默降位深")
            original.load()
            image = ImageOps.exif_transpose(original)
            alpha = image.getchannel("A") if "A" in image.getbands() else None
            image = image.convert("RGB")
            profile = original.info.get("icc_profile")
            if profile:
                try:
                    image = ImageCms.profileToProfile(image, ImageCms.ImageCmsProfile(io.BytesIO(profile)), ImageCms.createProfile("sRGB"), outputMode="RGB")
                except Exception as exc:
                    raise AgentError("无法读取 ICC 色彩描述文件，请先导出 sRGB 副本") from exc
            if alpha is not None:
                image.putalpha(alpha)
            if max_edge is not None:
                if not 256 <= max_edge <= 4096:
                    raise AgentError('工作副本最长边须在256至4096之间')
                image.thumbnail((max_edge, max_edge), Image.Resampling.LANCZOS)
            image.info.clear()
            output = io.BytesIO()
            image.save(output, format="PNG")
            result = output.getvalue()
            if len(result) > MAX_IMAGE_BYTES:
                raise AgentError("规范化 PNG 超过 16 MiB，请缩小测试副本")
            return result
    except (UnidentifiedImageError, OSError, Image.DecompressionBombError) as exc:
        raise AgentError("图片无法安全解码") from exc


def load_env(path: Path) -> None:
    """Small, non-executing .env loader. Existing process variables take precedence."""
    if not path.exists():
        return
    for number, line in enumerate(path.read_text(encoding="utf-8-sig").splitlines(), 1):
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if "=" not in line:
            raise AgentError(f".env 第 {number} 行缺少等号")
        key, value = line.split("=", 1)
        key, value = key.strip(), value.strip()
        if not key.replace("_", "").isalnum():
            raise AgentError(".env 变量名不合法")
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
            value = value[1:-1]
        os.environ.setdefault(key, value)
