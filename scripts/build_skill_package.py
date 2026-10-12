"""Build a portable HEX Skill from the same allowlisted public product sources."""
from __future__ import annotations

import argparse
import hashlib
import importlib.util
import io
import json
import os
from pathlib import Path
import tempfile
import zipfile


ROOT = Path(__file__).resolve().parents[1]
_release_spec = importlib.util.spec_from_file_location(
    "hex_skill_release", Path(__file__).with_name("release_local.py"))
release = importlib.util.module_from_spec(_release_spec)
_release_spec.loader.exec_module(release)
_reference_spec = importlib.util.spec_from_file_location(
    "hex_skill_reference_rules", Path(__file__).with_name("skill_bundle_entry.py"))
reference_rules = importlib.util.module_from_spec(_reference_spec)
_reference_spec.loader.exec_module(reference_rules)


def _json_bytes(value: dict) -> bytes:
    return (json.dumps(value, ensure_ascii=False, indent=2) + "\n").encode("utf-8")


def _digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _zip_bytes(entries: dict[str, bytes]) -> bytes:
    """Use stable archive metadata; source bytes and manifests are authoritative."""
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w", zipfile.ZIP_DEFLATED, compresslevel=9) as archive:
        for name, data in sorted(entries.items()):
            info = zipfile.ZipInfo(name, date_time=(1980, 1, 1, 0, 0, 0))
            info.compress_type = zipfile.ZIP_DEFLATED
            info.create_system = 3
            info.external_attr = 0o100644 << 16
            archive.writestr(info, data, compresslevel=9)
    return output.getvalue()


def _read_public_release(root: Path, source_commit: str | None) -> tuple[dict, dict[str, bytes]]:
    # Reuse the release builder, including its safe source selection, required
    # files, canonical outer-repository Skill fallback and commit validation.
    with tempfile.TemporaryDirectory(prefix="hex-skill-source-") as temporary:
        result = release.build_release(root, Path(temporary), source_commit=source_commit)
        with zipfile.ZipFile(result["zip"]) as archive:
            manifests = [name for name in archive.namelist()
                         if name.count("/") == 1 and name.endswith("/MANIFEST.json")]
            if len(manifests) != 1:
                raise ValueError("Public release must contain exactly one root manifest")
            manifest_name = manifests[0]
            prefix = manifest_name[:-len("MANIFEST.json")]
            manifest_data = archive.read(manifest_name)
            manifest = json.loads(manifest_data)
            entries = {row["path"]: archive.read(prefix + row["path"])
                       for row in manifest["files"]}
            entries["MANIFEST.json"] = manifest_data
            if (len(archive.namelist()) != len(entries)
                    or set(archive.namelist()) != {prefix + name for name in entries}):
                raise ValueError("Public release inventory differs from its manifest")
            for row in manifest["files"]:
                if _digest(entries[row["path"]]) != row["sha256"]:
                    raise ValueError("Public release digest mismatch: " + row["path"])
    return manifest, entries


def build_skill_package(root: Path = ROOT, output_dir: Path | None = None, *,
                        source_commit: str | None = None) -> dict:
    """Return a small receipt for a standard ``hex/`` Skill ZIP and checksum.

    ``root`` is a public product source tree, including a standalone extracted
    release, or the repository's ``hex-agent`` directory. No personal install,
    workspace scan, task runtime, network connection or host registration is used.
    """
    root = Path(root).resolve()
    manifest, runtime_entries = _read_public_release(root, source_commit)
    required = {".agents/skills/hex/SKILL.md", "scripts/skill_bundle_entry.py"}
    missing = required - runtime_entries.keys()
    if missing:
        raise ValueError("Missing public Skill product files: " + ", ".join(sorted(missing)))

    runtime_archive = _zip_bytes(runtime_entries)
    runtime_manifest = runtime_entries["MANIFEST.json"]
    skill_entries = {
        "SKILL.md": runtime_entries[".agents/skills/hex/SKILL.md"],
        "scripts/hex.py": runtime_entries["scripts/skill_bundle_entry.py"],
        "assets/runtime.zip": runtime_archive,
        "assets/runtime-manifest.json": runtime_manifest,
        "LICENSE": runtime_entries["LICENSE"],
        "THIRD_PARTY_NOTICES.md": runtime_entries["THIRD_PARTY_NOTICES.md"],
    }
    agent = runtime_entries.get(".agents/skills/hex/agents/openai.yaml")
    if agent is not None:
        skill_entries["agents/openai.yaml"] = agent

    readable = []
    for relative, data in sorted(runtime_entries.items()):
        # Only domain entry filenames/links are transformed so hosts discover
        # one Skill. Keep source and exported digests explicit and verifiable.
        if not reference_rules._is_readable_reference(relative):
            continue
        target = reference_rules._reference_path(relative)
        exported = reference_rules._render_reference(relative, data, runtime_entries)
        skill_entries[target] = exported
        readable.append({"path": target, "source": relative,
                         "source_sha256": _digest(data), "sha256": _digest(exported)})

    metadata = {
        "format": "hex.skill-bundle.v1",
        "name": "hex",
        "version": manifest["version"],
        "source_commit": manifest.get("source_commit"),
        "scope": manifest["scope"],
        "channel": manifest["channel"],
        "entry": "scripts/hex.py",
        "archive": "assets/runtime.zip",
        "archive_sha256": _digest(runtime_archive),
        "runtime_manifest": "assets/runtime-manifest.json",
        "runtime_manifest_sha256": _digest(runtime_manifest),
        "references_root": "references/runtime",
        "reference_transform": reference_rules.REFERENCE_TRANSFORM,
        "readable_resources": readable,
        "notice": (
            "Generated from the public product source and the canonical HEX Skill. "
            "Readable references retain source bytes except for declared method "
            "entry filename/link transformations. Hashes verify bundle "
            "consistency, not publisher identity, host support or image quality."
        ),
    }
    skill_entries["bundle.json"] = _json_bytes(metadata)
    data = _zip_bytes({"hex/" + name: content for name, content in skill_entries.items()})
    destination = Path(output_dir).resolve() if output_dir is not None else root.parent / "deliverables"
    destination.mkdir(parents=True, exist_ok=True)
    output = destination / f"HEX-Skill-v{manifest['version']}.zip"
    staged = None
    try:
        with tempfile.NamedTemporaryFile(prefix=".hex-skill-", suffix=".tmp",
                                         dir=destination, delete=False) as temporary:
            staged = Path(temporary.name)
            temporary.write(data)
        os.replace(staged, output)
    finally:
        if staged is not None and staged.exists():
            staged.unlink()
    result = {
        "zip": str(output), "sha256": _digest(data), "files": len(skill_entries),
        "bytes": len(data), "version": manifest["version"],
        "source_commit": manifest.get("source_commit"),
        "runtime_sha256": metadata["archive_sha256"],
        "runtime_files": len(runtime_entries), "reference_files": len(readable),
    }
    output.with_suffix(".sha256.txt").write_text(
        result["sha256"] + "  " + output.name + "\n", encoding="utf-8")
    return result


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", type=Path)
    parser.add_argument("--source-commit", help="Exact source commit used for this bundle")
    args = parser.parse_args()
    print(json.dumps(build_skill_package(output_dir=args.output_dir,
                                         source_commit=args.source_commit),
                     ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
