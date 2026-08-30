"""frames 模块级行为测试（离线；夹具由 conftest 用 ffmpeg 现场生成）。

只断言模块公开接口的外部行为：产物像素颜色、MAD 数值、中文错误信息。
"""

from __future__ import annotations

from pathlib import Path

import pytest

from styleforge.frames import (
    FramesError,
    extract_first_frame,
    extract_last_frame,
    mad,
)

# yuv420p 编解码往返误差在几个灰阶以内；颜色断言统一留出该容差。
PIXEL_TOLERANCE = 30


def assert_solid_pixels(pixels: bytes, *, bright: str) -> None:
    """断言全部像素为同一种亮/暗纯色（亮=白、暗=黑、红=红通道独高）。"""
    assert len(pixels) == 64 * 36 * 3
    reds = pixels[0::3]
    greens = pixels[1::3]
    blues = pixels[2::3]
    if bright == "white":
        assert min(reds) > 255 - PIXEL_TOLERANCE
        assert min(greens) > 255 - PIXEL_TOLERANCE
        assert min(blues) > 255 - PIXEL_TOLERANCE
    elif bright == "black":
        assert max(reds) < PIXEL_TOLERANCE
        assert max(greens) < PIXEL_TOLERANCE
        assert max(blues) < PIXEL_TOLERANCE
    elif bright == "red":
        assert min(reds) > 255 - PIXEL_TOLERANCE
        assert max(greens) < PIXEL_TOLERANCE
        assert max(blues) < PIXEL_TOLERANCE
    else:  # pragma: no cover - 防御拼写错误
        raise AssertionError(f"未知颜色断言类型：{bright}")


# ---------------------------------------------------------------- 尾帧抽取


def test_extract_last_frame_of_solid_video_has_color(red_video, tmp_path, read_pixels):
    out = tmp_path / "last.png"
    result = extract_last_frame(red_video, out)
    assert result == out
    assert_solid_pixels(read_pixels(out), bright="red")


def test_extract_first_frame_of_solid_video_has_color(red_video, tmp_path, read_pixels):
    out = tmp_path / "first.png"
    result = extract_first_frame(red_video, out)
    assert result == out
    assert_solid_pixels(read_pixels(out), bright="red")


# ---------------------------------------------------------------- 首/末帧颜色（渐变夹具）


def test_first_frame_of_two_tone_video_is_black(two_tone_video, tmp_path, read_pixels):
    out = tmp_path / "first.png"
    extract_first_frame(two_tone_video, out)
    assert_solid_pixels(read_pixels(out), bright="black")


def test_last_frame_of_two_tone_video_is_white(two_tone_video, tmp_path, read_pixels):
    out = tmp_path / "last.png"
    extract_last_frame(two_tone_video, out)
    assert_solid_pixels(read_pixels(out), bright="white")


def test_extract_creates_missing_parent_dirs(red_video, tmp_path, read_pixels):
    out = tmp_path / "deep" / "nested" / "last.png"
    extract_last_frame(red_video, out)
    assert out.is_file()
    assert_solid_pixels(read_pixels(out), bright="red")


# ---------------------------------------------------------------- 完整性校验（工单必列用例）


def test_extract_missing_video_chinese_error(tmp_path):
    missing = tmp_path / "nope.mp4"
    with pytest.raises(FramesError, match="视频文件不存在"):
        extract_last_frame(missing, tmp_path / "out.png")
    with pytest.raises(FramesError, match="视频文件不存在"):
        extract_first_frame(missing, tmp_path / "out.png")


def test_extract_directory_as_video_chinese_error(tmp_path):
    with pytest.raises(FramesError, match="不是文件"):
        extract_last_frame(tmp_path, tmp_path / "out.png")


def test_fake_14_byte_video_rejected(fake_video, tmp_path):
    """14 字节「No session fou」无 moov atom，必须被拒收（樱之诗取证实坑回归）。"""
    with pytest.raises(FramesError, match="文件损坏或不是有效视频"):
        extract_last_frame(fake_video, tmp_path / "out.png")
    with pytest.raises(FramesError, match="文件损坏或不是有效视频"):
        extract_first_frame(fake_video, tmp_path / "out.png")


def test_empty_video_rejected(empty_video, tmp_path):
    with pytest.raises(FramesError, match="文件损坏或不是有效视频"):
        extract_last_frame(empty_video, tmp_path / "out.png")


def test_too_short_video_rejected(short_video, tmp_path):
    with pytest.raises(FramesError, match="文件损坏或不是有效视频") as excinfo:
        extract_last_frame(short_video, tmp_path / "out.png")
    assert "时长" in str(excinfo.value)  # 错误信息给出探测到的时长细节


# ---------------------------------------------------------------- MAD 比对


def test_mad_same_image_is_zero(black_png):
    value = mad(black_png, black_png)
    assert isinstance(value, float)
    assert value == 0.0


def test_mad_black_vs_white_is_full_scale(black_png, white_png):
    """纯黑对纯白的 MAD 应接近满量程 255。"""
    value = mad(black_png, white_png)
    assert 250.0 <= value <= 255.0


def test_mad_black_vs_gray_in_expected_range(black_png, gray_png):
    """纯黑（0）对纯灰（约 128）的 MAD 应在 128 附近的预期区间。"""
    value = mad(black_png, gray_png)
    assert 115.0 <= value <= 140.0


def test_mad_scales_larger_images_to_64x36(black_png_large, white_png_large):
    """任意尺寸输入都缩放到 64×36 后比对；纯色大图 MAD 仍应满量程。"""
    value = mad(black_png_large, white_png_large)
    assert 250.0 <= value <= 255.0


def test_mad_missing_image_chinese_error(tmp_path):
    missing = tmp_path / "nope.png"
    with pytest.raises(FramesError, match="图片文件不存在"):
        mad(missing, missing)


def test_mad_directory_as_image_chinese_error(tmp_path):
    with pytest.raises(FramesError, match="不是文件"):
        mad(tmp_path, tmp_path)


def test_mad_undecodable_image_chinese_error(garbage_png, black_png):
    with pytest.raises(FramesError, match="无法读取图片"):
        mad(garbage_png, black_png)
