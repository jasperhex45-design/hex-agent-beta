"""Explicit local CPU OCR runner using installed weights and the Hex adapter."""
from __future__ import annotations

import argparse
import hashlib
import importlib.metadata
import json
import os
import platform
import sys
from datetime import datetime, timezone
from pathlib import Path
from time import perf_counter

ROOT = Path(__file__).resolve().parents[2]
RUNTIME = ROOT / ".runtime" / "ocr"
sys.path.insert(0, str(ROOT))


def read_json(path):
    return json.loads(Path(path).read_text(encoding="utf-8-sig"))


def write_new_json(path, value):
    path = Path(path).resolve()
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("x", encoding="utf-8") as output:
        output.write(json.dumps(value, ensure_ascii=False, indent=2) + "\n")


def configure_local_runtime():
    # These names are supported by installed PaddleX 3.7.2. Explicit local
    # model directories below prevent inference from selecting remote weights.
    os.environ["PADDLE_PDX_CACHE_HOME"] = str(RUNTIME / "cache" / "paddlex")
    os.environ["PADDLE_PDX_DISABLE_MODEL_SOURCE_CHECK"] = "True"
    os.environ["HF_HOME"] = str(RUNTIME / "cache" / "huggingface")
    os.environ["HF_HUB_OFFLINE"] = "1"
    os.environ["MODELSCOPE_CACHE"] = str(RUNTIME / "cache" / "modelscope")
    network_attempts = []

    def local_only(event, args):
        if event in {"socket.connect", "socket.getaddrinfo"}:
            network_attempts.append(event)
            raise RuntimeError("OCR inference is local-only; outbound network denied")

    sys.addaudithook(local_only)
    return network_attempts


def verified_models():
    manifest = read_json(RUNTIME / "model-manifest.json")
    paths = {}
    for model in manifest["models"]:
        for entry in model["files"]:
            path = (RUNTIME / "models" / entry["path"]).resolve()
            if not path.is_relative_to((RUNTIME / "models").resolve()):
                raise ValueError("Model manifest path leaves the local model directory")
            if hashlib.sha256(path.read_bytes()).hexdigest() != entry["sha256"]:
                raise ValueError("Installed model changed: " + entry["path"])
        directory = (RUNTIME / "models" / model["directory"]).resolve()
        if not directory.is_relative_to((RUNTIME / "models").resolve()):
            raise ValueError("Model directory leaves local runtime")
        # Paddle's Windows C++ model reader mishandles non-ASCII absolute paths.
        # The runner sets its process cwd to ROOT; an ASCII relative path keeps
        # the verified files in place and needs no copied or patched weights.
        paths[model["name"]] = str(directory.relative_to(ROOT))
    if set(paths) != {"PP-OCRv5_mobile_det", "PP-OCRv5_mobile_rec"}:
        raise ValueError("Only the explicitly installed mobile detection/recognition pair is supported")
    return manifest, paths


def build_engine():
    manifest, paths = verified_models()
    from paddleocr import PaddleOCR
    start = perf_counter()
    engine = PaddleOCR(
        text_detection_model_name="PP-OCRv5_mobile_det",
        text_detection_model_dir=paths["PP-OCRv5_mobile_det"],
        text_recognition_model_name="PP-OCRv5_mobile_rec",
        text_recognition_model_dir=paths["PP-OCRv5_mobile_rec"],
        use_doc_orientation_classify=False,
        use_doc_unwarping=False,
        use_textline_orientation=False,
        device="cpu", cpu_threads=4, enable_mkldnn=False,
    )
    return engine, manifest, perf_counter() - start


def runtime_metadata(manifest, load_seconds, network_attempts):
    import paddle
    packages = ["paddlepaddle", "paddleocr", "paddlex", "Pillow", "numpy"]
    return {"python": platform.python_version(), "executable": sys.executable,
            "platform": platform.platform(),
            "packages": {name: importlib.metadata.version(name) for name in packages},
            "device": paddle.get_device(), "compiled_with_cuda": paddle.is_compiled_with_cuda(),
            "cpu_threads": 4, "enable_mkldnn": False,
            "model_load_seconds": load_seconds, "model_manifest": manifest,
            "network_policy": "socket connect and DNS denied in this inference process",
            "network_attempts_blocked": len(network_attempts)}


def run_review(image_path, contract, requirements, engine):
    from liu_agent.adapters.ocr_review import review_text
    return review_text(image_path, contract, requirements, engine=engine,
                       runtime_label="hex_local_paddle_cpu_3.3.1_ocr_3.7.0_mobile_v5")


def verify(output_dir, network_attempts):
    from PIL import Image, ImageDraw, ImageFont
    from liu_agent.io import digest
    output_dir = Path(output_dir).resolve()
    output_dir.mkdir(parents=True, exist_ok=False)
    image_path = output_dir / "synthetic-text.png"
    lines = ["HEX", "文字核对", "20261007"]
    font_path = Path(os.environ.get("WINDIR", "C:/Windows")) / "Fonts" / "msyh.ttc"
    font = ImageFont.truetype(str(font_path), 84)
    image = Image.new("RGB", (1200, 480), "white")
    draw = ImageDraw.Draw(image)
    for index, text in enumerate(lines):
        draw.text((70, 30 + index * 140), text, fill="black", font=font)
    image.save(image_path)
    checks = [{"id": "text.line" + str(index + 1), "question": "画面是否包含准确文字：" + text,
               "expected": text} for index, text in enumerate(lines)]
    contract = {"format": "hex.ocr-runtime-synthetic-check.v1", "checks": checks,
                "origin": "synthetic_runtime_fixture_not_a_production_task"}
    contract["digest"] = digest(contract)
    requirements = [{"criterion_id": check["id"], "text_required": True, "exact_text": text}
                    for check, text in zip(checks, lines)]
    write_new_json(output_dir / "fixture-contract.json", contract)
    write_new_json(output_dir / "fixture-requirements.json", requirements)
    engine, manifest, load_seconds = build_engine()
    review = run_review(image_path, contract, requirements, engine)
    write_new_json(output_dir / "hex-ocr-review.json", review)
    passed = (review["state"] == "completed" and review["usage"]["model_calls"] == 1 and
              all(item["verdict"] == "met" for item in review["items"]))
    report = {"format": "hex.local-ocr-runtime-verification.v1",
              "created_at": datetime.now(timezone.utc).isoformat(),
              "passed": passed, "evidence_kind": "real_cpu_ocr_on_synthetic_text_fixture",
              "fixture": {"path": str(image_path), "expected_lines": lines,
                          "font": str(font_path), "size": [1200, 480],
                          "sha256": hashlib.sha256(image_path.read_bytes()).hexdigest()},
              "runtime": runtime_metadata(manifest, load_seconds, network_attempts), "review": review,
              "limits": ["No generative model or paid API invoked", "Synthetic fixture is not production image-quality evidence",
                         "This run does not establish OCR accuracy on arbitrary artwork or user acceptance"]}
    write_new_json(output_dir / "verification.json", report)
    print(json.dumps({"passed": passed, "report": str(output_dir / "verification.json"),
                      "texts": [token["text"] for token in review.get("tokens", [])],
                      "state": review["state"]}, ensure_ascii=False))
    return 0 if passed else 1


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    check = sub.add_parser("verify", help="Run real OCR on a clearly labeled synthetic text fixture")
    check.add_argument("--output-dir", type=Path, required=True)
    review = sub.add_parser("review", help="Run the Hex OCR adapter against existing frozen checks")
    review.add_argument("--image", type=Path, required=True)
    review.add_argument("--contract", type=Path, required=True)
    review.add_argument("--requirements", type=Path, required=True)
    review.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    for name in ("output_dir", "image", "contract", "requirements", "out"):
        if hasattr(args, name):
            setattr(args, name, getattr(args, name).resolve())
    os.chdir(ROOT)
    network_attempts = configure_local_runtime()
    if args.command == "verify":
        return verify(args.output_dir, network_attempts)
    if args.out.exists():
        parser.error("Output already exists; existing evidence is not overwritten")
    contract = read_json(args.contract)
    requirements = read_json(args.requirements)
    # Check source bindings before allocating/loading models.
    from liu_agent.adapters.ocr_review import _requirements
    _requirements(contract, requirements)
    if not requirements:
        result = run_review(args.image, contract, requirements, None)
    else:
        engine, manifest, load_seconds = build_engine()
        result = run_review(args.image, contract, requirements, engine)
        result["runtime_evidence"] = runtime_metadata(manifest, load_seconds, network_attempts)
    write_new_json(args.out, result)
    print(json.dumps({"state": result["state"], "output": str(args.out.resolve())}, ensure_ascii=False))
    return 0 if result["state"] in {"completed", "not_applicable"} else 1


if __name__ == "__main__":
    raise SystemExit(main())
