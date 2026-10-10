"""Seedream CLI argument adapter; no upload, subprocess, or retry occurs here.

The host admission gate owns reference permissions and dispatch accounting.
This adapter preserves its admitted image order and does not infer a provider
model ID or add creative defaults. The CLI resolves the exact service name.
"""
from fractions import Fraction
from pathlib import Path

from .io import AgentError, within


TOOL_NAME = "pippit.generate-image.seedream-5-pro"
MODEL_NAME = "Seedream 5.0 Pro"
# Live `model describe` configuration checked on 2026-09-27. These are CLI
# service capabilities, not the official API's model identifier or contract.
MAX_REFERENCE_IMAGES = 10
RESOLUTIONS = ("1K", "1.5K", "2K", "4K")
RATIOS = (0, 2, 3, 4, 5, 6, 13)
RATIO_LABELS = {0: "adaptive", 2: "16:9", 3: "9:16", 4: "4:3", 5: "3:4", 6: "1:1", 13: "21:9"}


def _options(options: dict | None) -> dict:
    if options is None:
        return {}
    if not isinstance(options, dict) or set(options) - {"resolution", "ratio"}:
        raise AgentError("小云雀连接器仅接受已核实的 resolution 和 ratio 选项")
    normalized = {}
    if "resolution" in options:
        value = options["resolution"]
        if not isinstance(value, str) or value.upper() not in RESOLUTIONS:
            raise AgentError("当前 Seedream 分辨率须为 1K、1.5K、2K 或 4K")
        normalized["resolution"] = value.upper()
    if "ratio" in options:
        value = options["ratio"]
        if type(value) is not int or value not in RATIOS:
            raise AgentError("当前 Seedream 比例必须使用已核实的整数枚举")
        normalized["ratio"] = value
    return normalized


def build_arguments(*, run: Path, target: str, prompt: str, images: list[dict],
                    provider_options: dict | None = None, desired_aspect_ratio: str = "auto") -> dict:
    """Map already-admitted images to the CLI, preserving roles via the prompt."""
    if target != "seedream":
        raise AgentError("小云雀 Seedream 连接器的目标必须为 seedream")
    if len(images) > MAX_REFERENCE_IMAGES:
        raise AgentError("当前 Seedream CLI 最多接受 10 张参考图；不能静默丢弃输入")
    arguments = {"prompt": prompt, "model": MODEL_NAME,
                 "generate_image_count": 1, "source": "codex"}
    if images:
        arguments["images"] = [str(within(run, row["file"]).resolve()) for row in images]
    options = _options(provider_options)
    if desired_aspect_ratio != "auto":
        try:
            desired = Fraction(desired_aspect_ratio.replace(":", "/"))
        except (AttributeError, ValueError, ZeroDivisionError) as exc:
            raise AgentError("任务画幅比例无效，不能提交小云雀调用") from exc
        if desired <= 0:
            raise AgentError("任务画幅比例无效，不能提交小云雀调用")
        supported = {Fraction(label.replace(":", "/")): code
                     for code, label in RATIO_LABELS.items() if code != 0}
        if desired not in supported:
            raise AgentError("当前 Seedream CLI 不支持任务目标画幅比例（unsupported）；不能静默改用自适应")
        required_ratio = supported[desired]
        if "ratio" in options and options["ratio"] != required_ratio:
            raise AgentError("小云雀 ratio 与任务目标画幅比例不一致；不能静默改变构图")
        options["ratio"] = required_ratio
    arguments.update(options)
    # Admission must validate the same frozen parameters the executor consumes.
    # This is pure argv construction: it does not invoke the CLI or reserve budget.
    build_cli_arguments("pippit", arguments)
    return arguments


def build_cli_arguments(cli_path: str | Path, arguments: dict) -> list[str]:
    """Build a shell-free argv from frozen dispatch arguments without mutation.

The executor must check the dispatch receipt and input hashes immediately
before use. Missing resolution is rejected here: old text-only dispatches
remain readable, but cannot silently acquire new submission parameters.
"""
    allowed = {"prompt", "model", "generate_image_count", "source", "images", "resolution", "ratio"}
    if not isinstance(arguments, dict) or set(arguments) - allowed:
        raise AgentError("小云雀调用包含未知参数；不能静默忽略")
    if arguments.get("model") != MODEL_NAME:
        raise AgentError("实际小云雀模型名称与 Seedream 连接器不一致")
    if type(arguments.get("generate_image_count")) is not int or arguments["generate_image_count"] != 1:
        raise AgentError("首版小云雀连接器每次只提交一张图片")
    prompt = arguments.get("prompt")
    if not isinstance(prompt, str) or not prompt.strip():
        raise AgentError("小云雀调用缺少非空提示词")
    if arguments.get("source") != "codex":
        raise AgentError("小云雀调用来源须与当前 Codex 宿主一致")
    if "resolution" not in arguments:
        raise AgentError("当前 Seedream CLI 需要 resolution；请在 dispatch 前明确分辨率，不能事后补参数")
    options = _options({key: arguments[key] for key in ("resolution", "ratio") if key in arguments})
    # Dispatch already normalizes options. Reject alternative representations
    # so the actual argument receipt remains an exact record of what was sent.
    if any(arguments[key] != value for key, value in options.items()):
        raise AgentError("小云雀 dispatch 参数尚未规范化；不能在提交时改写")
    images = arguments.get("images", [])
    if not isinstance(images, list) or len(images) > MAX_REFERENCE_IMAGES:
        raise AgentError("小云雀图片参数须为最多 10 张的有序列表")
    for image in images:
        if not isinstance(image, str) or not Path(image).is_absolute() or not Path(image).is_file():
            raise AgentError("小云雀参考图必须为存在的本地绝对路径")
        if Path(image).stat().st_size >= 500000000:
            raise AgentError("小云雀单张参考图必须小于 500 MB")
    argv = [str(cli_path), "generate-image", "--prompt", prompt, "--model", MODEL_NAME,
            "--generate-image-count", "1", "--source", "codex", "--resolution", options["resolution"]]
    if "ratio" in options:
        argv.extend(["--ratio", str(options["ratio"])])
    for image in images:
        argv.extend(["--image", image])
    return argv
