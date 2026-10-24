#!/usr/bin/env python3
"""Run the runtime bundled with this portable HEX Skill, never another checkout."""
from __future__ import annotations

import hashlib
import importlib
import importlib.machinery
import importlib.metadata
import json
import os
from pathlib import Path, PurePosixPath
import posixpath
import re
import shutil
import sys
import tempfile
import zipfile


SKILL_ROOT = Path(__file__).resolve().parents[1]
RUNTIME_ROOT = SKILL_ROOT / "runtime"
MANIFEST = RUNTIME_ROOT / "MANIFEST.json"
REFERENCE_TRANSFORM = "domain-method-links.v1"
MUTABLE_ROOTS = {
    "inputs", "outputs", ".state", ".runtime", ".venv", "local-library",
    "evidence", "reports", ".codex",
}


def _relative_path(value: str) -> str:
    if (not isinstance(value, str) or not value or "\\" in value or ":" in value
            or PurePosixPath(value).is_absolute()
            or PurePosixPath(value).as_posix() != value
            or any(part in {"", ".", ".."} for part in value.split("/"))):
        raise ValueError("Bundle path must be a normalized relative path")
    return value


def _inside(root: Path, relative: str) -> Path:
    path = root / _relative_path(relative)
    if path.resolve() != path.absolute() or not path.resolve().is_relative_to(root.resolve()):
        raise ValueError("Bundled resource path is linked or outside this Skill")
    return path


def _digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _is_readable_reference(name: str) -> bool:
    if name in {"CONTRIBUTING.md",
                "README.md", "CHANGELOG.md", "pyproject.toml",
                "LICENSE", "THIRD_PARTY_NOTICES.md"}:
        return True
    if name.startswith("knowledge/") and name.endswith((".md", ".json")):
        return True
    return name.endswith(".md") and (
        name.startswith("docs/") or (name.startswith(".agents/skills/")
                                     and not name.startswith(".agents/skills/hex/")))


def _reference_path(source: str) -> str:
    path = PurePosixPath(source)
    if source.startswith(".agents/skills/") and path.name == "SKILL.md":
        path = path.with_name("METHOD.md")
    return "references/runtime/" + path.as_posix()


def _render_reference(source: str, data: bytes, source_files) -> bytes:
    """Only remap known method entry paths; keep runtime sources unchanged."""
    if not source.endswith(".md"):
        return data
    methods = {name: _reference_path(name) for name in source_files
               if _is_readable_reference(name) and name.endswith("/SKILL.md")}
    links = {**methods, ".agents/skills/hex/SKILL.md": "SKILL.md"}
    source_parent = posixpath.dirname(source)
    target_parent = posixpath.dirname(_reference_path(source))

    def rewrite_link(match):
        raw = match.group("path")
        angle = raw.startswith("<") and raw.endswith(">")
        value = raw[1:-1] if angle else raw
        path, separator, anchor = value.partition("#")
        if not path or ":" in path or path.startswith("/"):
            return match.group(0)
        logical = posixpath.normpath(posixpath.join(source_parent, path))
        if logical not in links:
            return match.group(0)
        rewritten = posixpath.relpath(links[logical], target_parent) + separator + anchor
        return match.group("open") + ("<" + rewritten + ">" if angle else rewritten)

    text = data.decode("utf-8")
    text = re.sub(r"(?P<open>\]\()(?P<path><[^>\n]+>|[^)\s]+)", rewrite_link, text)
    text = re.sub(r"(?m)^(?P<open>[ \t]{0,3}\[[^\]\n]+\]:[ \t]*)(?P<path><[^>\n]+>|\S+)",
                  rewrite_link, text)
    # Also preserve concrete paths in prose/code spans such as
    # RESOURCE_ROOT/.agents/skills/liu-aesthetic-agent/SKILL.md.
    for logical, target in sorted(methods.items()):
        text = text.replace(logical, target.removeprefix("references/runtime/"))
    return text.encode("utf-8")


def _manifest_files(manifest: dict) -> dict[str, str]:
    rows = manifest["files"]
    if not isinstance(rows, list) or not rows:
        raise ValueError("Manifest files must be a nonempty list")
    files = {}
    for row in rows:
        name = _relative_path(row["path"])
        expected = row["sha256"]
        if name in files or name == "MANIFEST.json":
            raise ValueError("Runtime manifest contains a duplicate or reserved path")
        if not isinstance(expected, str) or not re.fullmatch(r"[0-9a-f]{64}", expected):
            raise ValueError("Invalid runtime file digest")
        files[name] = expected
    return files


def ensure_runtime() -> dict:
    """Materialize only the declared product resource; preserve existing runtimes."""
    metadata = json.loads((SKILL_ROOT / "bundle.json").read_text(encoding="utf-8"))
    if metadata.get("format") != "hex.skill-bundle.v1":
        raise ValueError("Unsupported HEX Skill bundle format")
    archive = _inside(SKILL_ROOT, metadata["archive"])
    manifest_path = _inside(SKILL_ROOT, metadata["runtime_manifest"])
    for path, expected in (
        (archive, metadata["archive_sha256"]),
        (manifest_path, metadata["runtime_manifest_sha256"]),
    ):
        if not path.is_file():
            raise ValueError("Bundled resource path is missing")
        if _digest(path) != expected:
            raise ValueError("Bundled resource digest mismatch: " + path.name)
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    expected_files = _manifest_files(manifest)
    if (metadata["version"] != manifest["version"]
            or metadata.get("source_commit") != manifest.get("source_commit")):
        raise ValueError("Bundle version or source commit differs from the runtime manifest")
    expected_files["MANIFEST.json"] = metadata["runtime_manifest_sha256"]
    _inside(SKILL_ROOT, "runtime")
    if RUNTIME_ROOT.exists():
        if (not MANIFEST.is_file() or MANIFEST.is_symlink()
                or _digest(MANIFEST) != metadata["runtime_manifest_sha256"]):
            raise ValueError("Existing runtime differs from this bundle; it was preserved without overwrite")
        return metadata

    temporary = Path(tempfile.mkdtemp(prefix=".hex-runtime-", dir=SKILL_ROOT))
    try:
        with zipfile.ZipFile(archive) as source:
            if (len(source.namelist()) != len(expected_files)
                    or set(source.namelist()) != set(expected_files)):
                raise ValueError("Archive inventory differs from the declared runtime manifest")
            for name, expected in expected_files.items():
                destination = _inside(temporary, name)
                data = source.read(name)
                if hashlib.sha256(data).hexdigest() != expected:
                    raise ValueError("Archive member digest mismatch: " + name)
                destination.parent.mkdir(parents=True, exist_ok=True)
                destination.write_bytes(data)
        os.rename(temporary, RUNTIME_ROOT)
    finally:
        if temporary.exists():
            shutil.rmtree(temporary)
    return metadata


def _inspect_references(metadata: dict, manifest_files: dict[str, str]) -> int:
    if metadata.get("reference_transform") != REFERENCE_TRANSFORM:
        raise ValueError("Unsupported readable reference transformation")
    expected = {name: digest for name, digest in manifest_files.items()
                if _is_readable_reference(name)}
    rows = metadata["readable_resources"]
    if not isinstance(rows, list) or len(rows) != len(expected):
        raise ValueError("Readable reference inventory differs from the runtime manifest")
    seen = set()
    for row in rows:
        source = _relative_path(row["source"])
        path = _relative_path(row["path"])
        if source in seen or source not in expected or path != _reference_path(source):
            raise ValueError("Readable reference path is duplicate or differs from its source")
        seen.add(source)
        if row["source_sha256"] != expected[source]:
            raise ValueError("Readable reference source digest mismatch: " + source)
        rendered = _render_reference(source, _inside(RUNTIME_ROOT, source).read_bytes(), manifest_files)
        rendered_digest = hashlib.sha256(rendered).hexdigest()
        exported = _inside(SKILL_ROOT, path)
        if (row["sha256"] != rendered_digest or not exported.is_file()
                or _digest(exported) != rendered_digest):
            raise ValueError("Readable reference digest mismatch: " + path)
    return len(expected)


def _product_inventory(declared: set[str]) -> set[str]:
    """Seal every shipped product tree, leaving only real runtime data mutable."""
    product_roots = {name.split("/", 1)[0] for name in declared if "/" in name}
    product_dirs = {parent.as_posix() for name in declared
                    for parent in PurePosixPath(name).parents if parent != PurePosixPath(".")}
    import_suffixes = tuple(set(importlib.machinery.all_suffixes()) | {".pyd", ".so"})
    actual = set()
    for current, directories, names in os.walk(RUNTIME_ROOT, followlinks=False):
        current = Path(current)
        # Exceptions apply only to top-level data/host/environment paths. An
        # outputs/.state directory inside knowledge is still a product change.
        directories[:] = [name for name in directories
                          if name != "__pycache__" and not (
                              current == RUNTIME_ROOT and name in MUTABLE_ROOTS
                              and name not in product_roots)]
        for name in directories:
            path = current / name
            relative = path.relative_to(RUNTIME_ROOT).as_posix()
            if path.resolve() != path.absolute():
                raise ValueError("Linked runtime product directory: " + relative)
            if relative.split("/", 1)[0] in product_roots and relative not in product_dirs:
                raise ValueError("Undeclared runtime product directory: " + relative)
        for name in names:
            relative = (current / name).relative_to(RUNTIME_ROOT).as_posix()
            if (relative in declared or relative.split("/", 1)[0] in product_roots
                    or name.endswith(import_suffixes)):
                actual.add(relative)
    return actual


def inspect_bundle() -> dict:
    errors = []
    dependencies = {}
    bundle = {}
    manifest = {}
    checked = 0
    reference_checked = 0
    try:
        bundle = ensure_runtime()
    except (OSError, ValueError, KeyError, TypeError, zipfile.BadZipFile) as exc:
        errors.append(f"Bundled resource error: {exc}")
    if sys.version_info < (3, 11):
        errors.append("Python 3.11 or newer is required")
    if not errors:
        try:
            requirements = _inside(RUNTIME_ROOT, "requirements.txt")
            if not requirements.is_file():
                raise ValueError("Bundled runtime requirements.txt is missing")
            for line in requirements.read_text(encoding="utf-8").splitlines():
                line = line.strip()
                if not line or line.startswith("#"):
                    continue
                if "==" not in line:
                    errors.append("Runtime requirements must specify exact versions")
                    continue
                name, expected = line.split("==", 1)
                try:
                    actual = importlib.metadata.version(name)
                except importlib.metadata.PackageNotFoundError:
                    actual = None
                dependencies[name] = {"expected": expected, "actual": actual}
                if actual != expected:
                    errors.append(f"{name}: expected {expected}, found {actual or 'missing'}")

            manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))
            files = _manifest_files(manifest)
            for name, expected in files.items():
                path = _inside(RUNTIME_ROOT, name)
                if not path.is_file():
                    errors.append(f"Missing runtime file: {name}")
                    continue
                if _digest(path) != expected:
                    errors.append(f"Runtime digest mismatch: {name}")
                checked += 1
            declared = set(files) | {"MANIFEST.json"}
            actual = _product_inventory(declared)
            if actual != declared:
                errors.append("Runtime product file inventory does not match the manifest: " +
                              ", ".join(sorted(actual ^ declared)))
            reference_checked = _inspect_references(bundle, files)
        except (OSError, ValueError, KeyError, TypeError) as exc:
            errors.append(f"Invalid bundled runtime: {exc}")

    modules = {}
    if not errors:
        sys.path.insert(0, str(RUNTIME_ROOT))
        try:
            for name in ("hex_agent", "liu_agent", "cl_hex"):
                module = importlib.import_module(name)
                path = Path(module.__file__).resolve()
                modules[name] = str(path)
                if not path.is_relative_to(RUNTIME_ROOT.resolve()):
                    errors.append(f"Module {name} did not resolve inside the bundled runtime")
            actual_version = getattr(importlib.import_module("liu_agent"), "__version__", None)
            if actual_version != manifest.get("version"):
                errors.append("Runtime module version does not match the bundle manifest")
        except (ImportError, OSError) as exc:
            errors.append(f"Runtime import failed: {exc}")
    return {
        "format": "hex.skill-runtime.v1",
        "ready": not errors,
        "python": sys.executable,
        "python_version": sys.version.split()[0],
        "entry": str(Path(__file__).resolve()),
        "runtime_root": str(RUNTIME_ROOT),
        "runtime_archive": bundle.get("archive"),
        "runtime_archive_sha256": bundle.get("archive_sha256"),
        "references_root": str(SKILL_ROOT / "references/runtime"),
        "version": manifest.get("version"),
        "source_commit": manifest.get("source_commit"),
        "verified_runtime_files": checked,
        "verified_reference_files": reference_checked,
        "dependencies": dependencies,
        "module_paths": modules,
        "tesseract_executable": shutil.which("tesseract"),
        "host_activation": "skill_entry_only_no_automatic_hook_binding_claimed",
        "network_used": False,
        "generation_calls": 0,
        "errors": errors,
    }


def main(argv: list[str] | None = None) -> int:
    sys.dont_write_bytecode = True
    args = list(sys.argv[1:] if argv is None else argv)
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8")
    report = inspect_bundle()
    if args == ["--skill-doctor"] or not report["ready"]:
        print(json.dumps(report, ensure_ascii=False, indent=2))
        return 0 if report["ready"] else 2
    from hex_agent.__main__ import main as runtime_main
    return runtime_main(args)


if __name__ == "__main__":
    raise SystemExit(main())
