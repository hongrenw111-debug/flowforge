"""frames 工单的共享测试夹具：全部由 ffmpeg 现场生成，离线、不预置任何二进制。

视频/图片颜色选取像素级可预测的纯色，便于对抽出帧做全像素颜色断言；
黑→白拼接视频是「首末帧颜色断言」的最简渐变夹具（首秒全黑、次秒全白）。
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

FFMPEG_TIMEOUT = 60


def _run_ffmpeg(*args: str) -> None:
    """调用 ffmpeg 生成测试夹具；失败直接报错（夹具是测试的前置条件）。"""
    proc = subprocess.run(
        ["ffmpeg", "-y", "-v", "error", *args],
        capture_output=True,
        text=True,
        timeout=FFMPEG_TIMEOUT,
    )
    if proc.returncode != 0:
        raise RuntimeError(f"测试夹具生成失败：ffmpeg {' '.join(args)}\n{proc.stderr}")


def make_solid_video(
    tmp_path: Path, color: str, name: str = "solid.mp4", duration: float = 2.0
) -> Path:
    """纯色视频（64×36，yuv420p）。"""
    video = tmp_path / name
    _run_ffmpeg(
        "-f", "lavfi", "-i", f"color=c={color}:s=64x36:d={duration}",
        "-pix_fmt", "yuv420p", str(video),
    )
    return video


def make_two_tone_video(tmp_path: Path, name: str = "two_tone.mp4") -> Path:
    """首秒全黑、次秒全白的 2 秒视频（64×36）。"""
    video = tmp_path / name
    _run_ffmpeg(
        "-f", "lavfi", "-i", "color=c=black:s=64x36:d=1",
        "-f", "lavfi", "-i", "color=c=white:s=64x36:d=1",
        "-filter_complex", "[0:v][1:v]concat=n=2:v=1:a=0",
        "-pix_fmt", "yuv420p", str(video),
    )
    return video


def make_solid_png(tmp_path: Path, color: str, name: str, size: str = "64x36") -> Path:
    """纯色 PNG 图片。"""
    image = tmp_path / name
    _run_ffmpeg(
        "-f", "lavfi", "-i", f"color=c={color}:s={size}",
        "-frames:v", "1", str(image),
    )
    return image


def _read_png_pixels(png_path: Path) -> bytes:
    """把 PNG 解码为 rgb24 裸像素（64×36 → 6912 字节），供测试直接断言颜色。"""
    proc = subprocess.run(
        ["ffmpeg", "-v", "error", "-i", str(png_path), "-f", "rawvideo", "-pix_fmt", "rgb24", "-"],
        capture_output=True,
        timeout=FFMPEG_TIMEOUT,
    )
    if proc.returncode != 0:
        raise RuntimeError(f"测试夹具解码失败：{png_path}\n{proc.stderr.decode(errors='replace')}")
    return proc.stdout


@pytest.fixture
def read_pixels():
    """返回「PNG 路径 → rgb24 裸像素 bytes」的解码函数。"""
    return _read_png_pixels


@pytest.fixture
def red_video(tmp_path):
    return make_solid_video(tmp_path, "red")


@pytest.fixture
def two_tone_video(tmp_path):
    return make_two_tone_video(tmp_path)


@pytest.fixture
def short_video(tmp_path):
    """时长 0.5 秒的视频：低于 1 秒完整性下限。"""
    return make_solid_video(tmp_path, "red", name="short.mp4", duration=0.5)


@pytest.fixture
def fake_video(tmp_path):
    """14 字节伪视频：下载失败响应文本被当 .mp4 保存（樱之诗取证实坑）。"""
    video = tmp_path / "fake.mp4"
    video.write_bytes(b"No session fou")
    return video


@pytest.fixture
def empty_video(tmp_path):
    video = tmp_path / "empty.mp4"
    video.write_bytes(b"")
    return video


@pytest.fixture
def black_png(tmp_path):
    return make_solid_png(tmp_path, "black", "black.png")


@pytest.fixture
def white_png(tmp_path):
    return make_solid_png(tmp_path, "white", "white.png")


@pytest.fixture
def gray_png(tmp_path):
    return make_solid_png(tmp_path, "gray", "gray.png")


@pytest.fixture
def black_png_large(tmp_path):
    """128×72 的纯黑图：验证 MAD 比对前会缩放到 64×36。"""
    return make_solid_png(tmp_path, "black", "black_large.png", size="128x72")


@pytest.fixture
def white_png_large(tmp_path):
    return make_solid_png(tmp_path, "white", "white_large.png", size="128x72")


@pytest.fixture
def garbage_png(tmp_path):
    """文本内容被当 .png 保存的假图片。"""
    image = tmp_path / "garbage.png"
    image.write_bytes(b"No session fou")
    return image
