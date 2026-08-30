"""剧本（Script）的加载与校验。

剧本 = 有序镜头（Shot）列表 + 全局默认参数（defaults）。
校验全部产出中文、指名道姓（镜头编号与字段）的错误信息，
供 `styleforge check` 逐条展示，实现不碰网页的零成本排错。
"""

from __future__ import annotations

from pathlib import Path

import yaml
from pydantic import BaseModel, ConfigDict, Field, ValidationError

# ---------------------------------------------------------------- 常量表

# 默认模型（调研定标：用户实际使用 Gemini Omni，8 秒档，支持首帧+尾帧）。
DEFAULT_MODEL = "gemini-omni"

# 模型目录：模型 ID → 该模型支持的单段时长（秒）。
# 新模型在此加一行即可接入；未来更多能力约束也扩到这张表。
MODEL_CATALOG: dict[str, frozenset[int]] = {
    "gemini-omni": frozenset({8}),
    "gemini-omni-flash": frozenset({8}),
    "veo-3.1-fast": frozenset({4, 6, 8, 10}),
    "veo-3.1-quality": frozenset({8}),
}

# Flow 视频偏好（Video preferences）里的画幅选项。
ASPECT_RATIOS: frozenset[str] = frozenset({"16:9", "9:16", "1:1"})

# 下载档位（v1 仅 Original 720p，即时可得；1080p 需异步等待，不做）。
DOWNLOAD_PRESETS: frozenset[str] = frozenset({"original-720p"})

# 首帧来源（First-Frame Source）三态：指定图片 / 上一镜头尾帧 / 空。
FIRST_FRAME_SOURCES: tuple[str, ...] = ("image", "last_frame", "none")

# 产出归档根目录：剧本产物落在 <output>/<剧本名>/ 下，按镜头编号归档。
OUTPUT_ROOT = Path("output")


# ---------------------------------------------------------------- pydantic 模型


class Defaults(BaseModel):
    """全局默认参数；剧本里写明的项即覆盖，未写的用内置默认。"""

    model_config = ConfigDict(extra="forbid")

    model: str = DEFAULT_MODEL
    duration: int = 8
    aspect: str = "16:9"
    outputs: int = Field(default=1, ge=1, le=4)
    download: str = "original-720p"
    retry: int = Field(default=1, ge=0)


class FirstFrame(BaseModel):
    """镜头首帧来源：{source: image, path} | {source: last_frame} | {source: none}。"""

    model_config = ConfigDict(extra="forbid")

    source: str
    path: str | None = None


class Shot(BaseModel):
    """镜头：一段提示词加一个首帧来源；省略 first_frame 视为 none（纯文生视频）。"""

    model_config = ConfigDict(extra="forbid")

    prompt: str = Field(min_length=1)
    first_frame: FirstFrame | None = None


class Script(BaseModel):
    """剧本：名称 + 全局默认参数 + 有序镜头列表。"""

    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1)
    defaults: Defaults = Field(default_factory=Defaults)
    shots: list[Shot] = Field(min_length=1)


class ScriptInvalid(Exception):
    """剧本校验失败；errors 为逐条中文错误（指名镜头编号与字段）。"""

    def __init__(self, errors: list[str]) -> None:
        self.errors = errors
        super().__init__("；".join(errors))


# ---------------------------------------------------------------- pydantic 错误翻译

# 字段标识符 → 中文名（技术标识符保留原文）。
_FIELD_NAMES: dict[str, str] = {
    "name": "剧本名称",
    "defaults": "默认参数",
    "shots": "镜头列表",
    "prompt": "提示词",
    "first_frame": "首帧来源",
    "source": "首帧来源类型",
    "path": "首帧图片路径",
    "model": "模型",
    "duration": "时长",
    "aspect": "画幅",
    "outputs": "输出数",
    "download": "下载档位",
    "retry": "重试次数",
}


def _split_loc(loc: tuple) -> tuple[str, str]:
    """把 pydantic 错误的 loc 拆成（中文上下文前缀, 字段链）。

    镜头上下文形如 ("shots", 2, ...) → 前缀 "镜头 3"，其余为顶层字段链。
    """
    if len(loc) >= 2 and loc[0] == "shots" and isinstance(loc[1], int):
        return f"镜头 {loc[1] + 1}", ".".join(str(item) for item in loc[2:])
    return "", ".".join(str(item) for item in loc)


def _field_text(chain: str, last_key: object) -> str:
    """字段链补充中文注释，如 "prompt（提示词）"。"""
    cn = _FIELD_NAMES.get(str(last_key), "")
    return f"{chain}（{cn}）" if cn else chain


def _reason_text(item: dict) -> str:
    """pydantic 错误类型 → 中文原因。"""
    etype = item["type"]
    ctx = item.get("ctx") or {}
    if etype == "string_type":
        return "必须是文本"
    if etype in {"int_type", "int_parsing"}:
        return "必须是整数"
    if etype == "string_too_short":
        return "不能为空"
    if etype in {"dict_type", "model_type"}:
        return "必须是键值对（mapping）"
    if etype == "list_type":
        return "必须是列表"
    if etype == "too_short":
        return f"至少需要 {ctx.get('limit_value', 1)} 个元素"
    if etype == "greater_than_equal":
        return f"不能小于 {ctx.get('ge')}"
    if etype == "less_than_equal":
        return f"不能大于 {ctx.get('le')}"
    if etype == "greater_than":
        return f"必须大于 {ctx.get('gt')}"
    if etype == "less_than":
        return f"必须小于 {ctx.get('lt')}"
    if etype == "bool_type":
        return "必须是布尔值"
    return f"格式不合法（{etype}）"


def _translate_validation_error(exc: ValidationError) -> list[str]:
    """把 pydantic 的错误列表翻译成逐条中文错误（指名镜头编号与字段）。"""
    errors: list[str] = []
    for item in exc.errors():
        loc = item["loc"]
        context, chain = _split_loc(loc)
        prefix = f"{context}：" if context else ""
        field = _field_text(chain, loc[-1]) if chain else ""
        if item["type"] == "missing":
            errors.append(f"{prefix}缺少 {field}" if field else f"{prefix}缺少该字段")
        elif item["type"] == "extra_forbidden":
            errors.append(f'{prefix}未知字段 "{chain}"')
        elif item["type"] == "model_attributes_type" and not chain:
            errors.append(f"{prefix}必须是键值对（mapping）")
        elif item["type"] == "too_short" and chain == "shots":
            errors.append(f"{prefix}shots（镜头列表）：至少需要一个镜头")
        elif field:
            errors.append(f"{prefix}{field}：{_reason_text(item)}")
        else:
            errors.append(f"{prefix}{_reason_text(item)}")
    return errors


# ---------------------------------------------------------------- 语义校验


def resolve_image_path(base_dir: Path, raw_path: str) -> Path:
    """解析首帧图片路径：绝对路径原样使用，相对路径基于剧本所在目录。"""
    candidate = Path(raw_path)
    return candidate if candidate.is_absolute() else base_dir / candidate


def semantic_errors(script: Script, base_dir: Path) -> list[str]:
    """结构合法之后的语义校验：模型目录、参数取值、图片存在性。"""
    errors = _name_semantic_errors(script.name)
    errors.extend(_defaults_semantic_errors(script.defaults))
    for index, shot in enumerate(script.shots, start=1):
        errors.extend(_shot_semantic_errors(shot, index, base_dir))
    return errors


def _name_semantic_errors(name: str) -> list[str]:
    errors: list[str] = []
    stripped = name.strip()
    if not stripped:
        errors.append("name（剧本名称）：不能为空白")
    elif "/" in stripped or "\\" in stripped or stripped in {".", ".."}:
        errors.append(f'name（剧本名称）：不能包含路径分隔符 "{name}"')
    return errors


def _defaults_semantic_errors(defaults: Defaults) -> list[str]:
    errors: list[str] = []
    if defaults.model not in MODEL_CATALOG:
        available = "、".join(sorted(MODEL_CATALOG))
        errors.append(
            f'defaults.model（模型）：未知模型 "{defaults.model}"（可用：{available}）'
        )
    else:
        allowed = "、".join(f"{n} 秒" for n in sorted(MODEL_CATALOG[defaults.model]))
        if defaults.duration not in MODEL_CATALOG[defaults.model]:
            errors.append(
                f"defaults.duration（时长）：模型 {defaults.model} 不支持 "
                f"{defaults.duration} 秒（允许：{allowed}）"
            )
    if defaults.aspect not in ASPECT_RATIOS:
        allowed_aspects = " / ".join(sorted(ASPECT_RATIOS))
        errors.append(
            f'defaults.aspect（画幅）：非法画幅 "{defaults.aspect}"（允许：{allowed_aspects}）'
        )
    if defaults.download not in DOWNLOAD_PRESETS:
        allowed_downloads = "、".join(sorted(DOWNLOAD_PRESETS))
        errors.append(
            f'defaults.download（下载档位）：非法档位 "{defaults.download}"'
            f"（允许：{allowed_downloads}）"
        )
    return errors


def _shot_semantic_errors(shot: Shot, index: int, base_dir: Path) -> list[str]:
    errors: list[str] = []
    if not shot.prompt.strip():
        errors.append(f"镜头 {index}：prompt（提示词）：不能为空白")
    first_frame = shot.first_frame
    if first_frame is None:
        return errors
    if first_frame.source not in FIRST_FRAME_SOURCES:
        allowed_sources = " / ".join(FIRST_FRAME_SOURCES)
        errors.append(
            f"镜头 {index}：first_frame.source（首帧来源类型）非法值 "
            f'"{first_frame.source}"（允许：{allowed_sources}）'
        )
    elif first_frame.source == "image":
        if not first_frame.path or not first_frame.path.strip():
            errors.append(
                f"镜头 {index}：first_frame.source 为 image 时必须提供 path（首帧图片路径）"
            )
        else:
            image_path = resolve_image_path(base_dir, first_frame.path)
            if not image_path.is_file():
                errors.append(
                    f"镜头 {index}：first_frame.path（首帧图片路径）指向的图片不存在："
                    f"{image_path}"
                )
    return errors


def output_dir(script: Script) -> Path:
    """剧本产物的归档目录：<output>/<剧本名>/（按镜头编号归档）。"""
    return OUTPUT_ROOT / script.name.strip()


# ---------------------------------------------------------------- 加载入口


def load_script(path: str | Path) -> Script:
    """读取并完整校验剧本；失败抛 ScriptInvalid（errors 为逐条中文错误）。"""
    script_path = Path(path)
    if not script_path.exists():
        raise ScriptInvalid([f"剧本文件不存在：{script_path}"])
    if not script_path.is_file():
        raise ScriptInvalid([f"剧本路径不是文件：{script_path}"])
    try:
        text = script_path.read_text(encoding="utf-8")
    except UnicodeDecodeError as exc:
        raise ScriptInvalid([f"剧本文件不是有效的 UTF-8 文本：{script_path}"]) from exc
    try:
        data = yaml.safe_load(text)
    except yaml.YAMLError as exc:
        raise ScriptInvalid([f"YAML 语法错误：{exc}"]) from exc
    if not isinstance(data, dict):
        raise ScriptInvalid(["剧本顶层必须是键值对（mapping）"])
    base_dir = script_path.resolve().parent
    try:
        script = Script.model_validate(data)
    except ValidationError as exc:
        raise ScriptInvalid(_collect_all_errors(exc, data, base_dir)) from exc
    errors = semantic_errors(script, base_dir)
    if errors:
        raise ScriptInvalid(errors)
    return script


def _collect_all_errors(exc: ValidationError, data: dict, base_dir: Path) -> list[str]:
    """结构错误全部保留；并对各自结构完好的段落尽力补充语义错误。

    这样一处结构问题（如某镜头缺 prompt）不会掩盖其他段落的语义问题
    （如模型×时长组合非法），check 能一次报出所有可发现的问题。
    """
    errors = _translate_validation_error(exc)
    try:
        defaults = Defaults.model_validate(data.get("defaults") or {})
    except ValidationError:
        pass  # defaults 段自身有结构错误，已在结构错误里逐条报告
    else:
        errors.extend(_defaults_semantic_errors(defaults))
    shots_data = data.get("shots")
    if isinstance(shots_data, list):
        for index, item in enumerate(shots_data, start=1):
            if not isinstance(item, dict):
                continue
            try:
                shot = Shot.model_validate(item)
            except ValidationError:
                continue  # 该镜头自身有结构错误，已在结构错误里逐条报告
            errors.extend(_shot_semantic_errors(shot, index, base_dir))
    name = data.get("name")
    if isinstance(name, str):
        errors.extend(_name_semantic_errors(name))
    return errors
