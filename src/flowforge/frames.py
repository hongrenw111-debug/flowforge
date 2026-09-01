"""ffmpeg 帧抽取与 MAD 验证器——「无证据不报成功」红线的证据引擎。

三件能力：
- extract_last_frame：任意视频 → 最后一帧 PNG（尾帧接力模式的地基）
- extract_first_frame：任意视频 → 第一帧 PNG（验证输入帧是否被模型忠实执行）
- mad：两张图片缩放到 64×36 后的全像素平均绝对差（0-255 浮点数）

完整性校验前置：任何视频在抽帧前先经 ffprobe 时长探测——无法解析、
缺少 moov atom、时长低于下限的文件一律以中文错误拒收。樱之诗取证实坑：
下载失败响应（'No session fou' 文本）被当 .mp4 保存后混入链条，污染证据。
阈值判断留给调用方，本模块只产出数值与产物。
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

# MAD 比对的统一缩放尺寸（16:9）与满量程参考。
MAD_WIDTH = 64
MAD_HEIGHT = 36
_MAD_PIXELS = MAD_WIDTH * MAD_HEIGHT
_MAD_BYTES = _MAD_PIXELS * 3

# 视频完整性下限：Flow 产物均为 4-10 秒，低于 1 秒的文件只可能是失败响应或截断产物。
MIN_VIDEO_DURATION_SECONDS = 1.0

# 流水线默认 MAD 阈值（工单 Amendments 第 4 条）；阈值判断由调用方执行。
DEFAULT_MAD_THRESHOLD = 25.0

_SUBPROCESS_TIMEOUT = 60

_FFMPEG_INSTALL_GUIDANCE = (
    "请安装 FFmpeg 并加入 PATH：Windows 可从 https://www.gyan.dev/ffmpeg/builds/ "
    "下载 release full 构建包，解压后将 bin 目录加入 PATH 环境变量，重开终端后重试。"
)


class FramesError(Exception):
    """帧处理失败；message 为面向用户的中文错误，由 CLI 直接展示。"""


def _require_ffmpeg_tools() -> None:
    """确保 ffmpeg/ffprobe 在 PATH 中；缺失时给出 Windows 安装指引。"""
    missing = [name for name in ("ffmpeg", "ffprobe") if shutil.which(name) is None]
    if missing:
        raise FramesError(f"未找到 {'、'.join(missing)}。{_FFMPEG_INSTALL_GUIDANCE}")


def _run_tool(args: list[str], subject: str) -> subprocess.CompletedProcess:
    """执行外部工具并统一兜底转换系统级异常为中文错误。"""
    try:
        return subprocess.run(args, capture_output=True, timeout=_SUBPROCESS_TIMEOUT)
    except FileNotFoundError as exc:
        raise FramesError(f"未找到 ffmpeg/ffprobe。{_FFMPEG_INSTALL_GUIDANCE}") from exc
    except subprocess.TimeoutExpired as exc:
        raise FramesError(f"处理超时：{subject}") from exc


# ---------------------------------------------------------------- 完整性校验


def ensure_valid_video(video_path: Path) -> float:
    """视频完整性校验：存在性 + ffprobe 时长探测 + 下限检查，返回时长（秒）。

    无法解析、缺少 moov atom、时长低于下限的文件一律拒收，
    错误信息以「文件损坏或不是有效视频」开头并附带原因细节。
    """
    if not video_path.exists():
        raise FramesError(f"视频文件不存在：{video_path}")
    if not video_path.is_file():
        raise FramesError(f"视频路径不是文件：{video_path}")
    duration = _probe_duration(video_path)
    if duration < MIN_VIDEO_DURATION_SECONDS:
        raise FramesError(
            f"文件损坏或不是有效视频：{video_path}"
            f"（时长 {duration:.2f} 秒，低于 {MIN_VIDEO_DURATION_SECONDS:g} 秒完整性下限）"
        )
    return duration


def _probe_duration(video_path: Path) -> float:
    """ffprobe 时长探测；解析不出时长即视为损坏。"""
    _require_ffmpeg_tools()
    proc = _run_tool(
        [
            "ffprobe", "-v", "error",
            "-show_entries", "format=duration",
            "-of", "default=noprint_wrappers=1:nokey=1",
            str(video_path),
        ],
        video_path,
    )
    output = proc.stdout.decode(errors="replace").strip()
    if proc.returncode != 0 or not output:
        raise FramesError(
            f"文件损坏或不是有效视频：{video_path}"
            "（ffprobe 无法解析，可能缺少 moov atom 或不是视频文件）"
        )
    try:
        return float(output.splitlines()[-1])
    except ValueError as exc:
        raise FramesError(f"文件损坏或不是有效视频：{video_path}（时长探测结果无法解析）") from exc


# ---------------------------------------------------------------- 帧抽取


def extract_last_frame(video_path: Path, out_png: Path) -> Path:
    """抽取视频最后一帧（尾帧）保存为 PNG，返回输出路径。

    尾帧可被下一镜头用作首帧（链式续接的地基）。
    """
    return _extract_frame(video_path, out_png, last=True)


def extract_first_frame(video_path: Path, out_png: Path) -> Path:
    """抽取视频第一帧（首帧）保存为 PNG，返回输出路径。

    用于验证输入帧是否被模型忠实执行（MAD 首帧验证的证据来源）。
    """
    return _extract_frame(video_path, out_png, last=False)


def _extract_frame(video_path: Path, out_png: Path, *, last: bool) -> Path:
    action = "尾帧" if last else "首帧"
    ensure_valid_video(video_path)
    out_png = Path(out_png)
    out_png.parent.mkdir(parents=True, exist_ok=True)
    args = ["ffmpeg", "-y", "-v", "error"]
    if last:
        # -sseof 定位到末尾前 0.1 秒，配合 accurate seek 取最接近末尾的帧（帧级精准）。
        args += ["-sseof", "-0.1"]
    args += ["-i", str(video_path), "-frames:v", "1", str(out_png)]
    proc = _run_tool(args, video_path)
    if proc.returncode != 0 or not out_png.is_file() or out_png.stat().st_size == 0:
        raise FramesError(f"{action}抽取失败：{video_path} → {out_png}")
    return out_png


# ---------------------------------------------------------------- MAD 比对


def mad(image_a: Path, image_b: Path) -> float:
    """两张图片缩放到 64×36 后的全像素平均绝对差（0-255 浮点数）。

    同图恒为 0；阈值判断留给调用方（参考 DEFAULT_MAD_THRESHOLD）。
    """
    _require_ffmpeg_tools()
    for image_path in (image_a, image_b):
        if not image_path.exists():
            raise FramesError(f"图片文件不存在：{image_path}")
        if not image_path.is_file():
            raise FramesError(f"图片路径不是文件：{image_path}")
    data_a = _decode_scaled_rgb24(image_a)
    data_b = _decode_scaled_rgb24(image_b)
    if len(data_a) != len(data_b):
        raise FramesError(
            f"两图比对失败：解码后尺寸不一致"
            f"（{image_a}：{len(data_a)} 字节，{image_b}：{len(data_b)} 字节）"
        )
    total = sum(abs(x - y) for x, y in zip(data_a, data_b))
    return total / len(data_a)


def _decode_scaled_rgb24(image_path: Path) -> bytes:
    """图片 → 64×36 rgb24 裸像素；解码不出完整一帧即视为无效图片。"""
    proc = _run_tool(
        [
            "ffmpeg", "-v", "error",
            "-i", str(image_path),
            "-vf", f"scale={MAD_WIDTH}:{MAD_HEIGHT}",
            "-frames:v", "1",
            "-f", "rawvideo", "-pix_fmt", "rgb24", "-",
        ],
        image_path,
    )
    if proc.returncode != 0 or len(proc.stdout) < _MAD_BYTES:
        raise FramesError(f"无法读取图片：{image_path}（不是有效图片或解码失败）")
    return proc.stdout
