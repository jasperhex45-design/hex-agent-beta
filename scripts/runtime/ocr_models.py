"""Download the two explicitly selected official CPU OCR models.

This is an installation command, never called automatically by OCR inference.
The recorded SHA256 identifies the received archive; it is not a vendor signature.
"""
from __future__ import annotations

import hashlib
import json
import tarfile
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
RUNTIME = ROOT / ".runtime" / "ocr"
SOURCES = {
    "PP-OCRv5_mobile_det": "https://paddle-model-ecology.bj.bcebos.com/paddlex/official_inference_model/paddle3.0.0/PP-OCRv5_mobile_det_infer.tar",
    "PP-OCRv5_mobile_rec": "https://paddle-model-ecology.bj.bcebos.com/paddlex/official_inference_model/paddle3.0.0//PP-OCRv5_mobile_rec_infer.tar",
}
ARCHIVE_SHA256 = {
    "PP-OCRv5_mobile_det": "50446e5d01ac2a73d5319c89513281f6578414c888c602f9af13f93feefffc58",
    "PP-OCRv5_mobile_rec": "566b9512b34e34a9f0db54d87b51fa5a0b9ed2cf1ab7e49728cc0b8b5a64f414",
}


def main():
    model_root = RUNTIME / "models"
    archive_root = RUNTIME / "archives"
    model_root.mkdir(parents=True, exist_ok=True)
    archive_root.mkdir(parents=True, exist_ok=True)
    records = []
    for name, url in SOURCES.items():
        archive = archive_root / (name + "_infer.tar")
        if not archive.is_file():
            temporary = archive.with_suffix(".download")
            print("Downloading official model:", name, flush=True)
            with urllib.request.urlopen(url, timeout=120) as response, temporary.open("wb") as output:
                while chunk := response.read(1024 * 1024):
                    output.write(chunk)
            temporary.replace(archive)
        sha256 = hashlib.sha256(archive.read_bytes()).hexdigest()
        if sha256 != ARCHIVE_SHA256[name]:
            raise RuntimeError("Archive differs from the verified 2026-10-07 official download: " + name)
        with tarfile.open(archive) as bundle:
            # Python's data filter rejects absolute/traversal paths and devices.
            bundle.extractall(model_root, filter="data")
        candidates = sorted(model_root.glob(name + "*/inference.pdiparams"))
        if len(candidates) != 1:
            raise RuntimeError("Expected exactly one official inference directory for " + name)
        directory = candidates[0].parent
        files = [{"path": str(path.relative_to(model_root)).replace("\\", "/"),
                  "sha256": hashlib.sha256(path.read_bytes()).hexdigest(), "bytes": path.stat().st_size}
                 for path in sorted(directory.rglob("*")) if path.is_file()]
        records.append({"name": name, "source_url": url, "archive_sha256": sha256,
                        "archive_bytes": archive.stat().st_size, "directory": directory.name,
                        "license": "Apache-2.0", "license_source":
                        "https://huggingface.co/PaddlePaddle/" + name + "/raw/main/README.md",
                        "files": files})
        print("Installed:", name, sha256, flush=True)
    manifest = {"format": "hex.local-ocr-models.v1", "recorded_at": datetime.now(timezone.utc).isoformat(),
                "official_documentation": "https://www.paddleocr.ai/main/en/version3.x/pipeline_usage/OCR.html",
                "models": records}
    (RUNTIME / "model-manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
