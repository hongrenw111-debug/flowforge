"""`flowforge lastframe` / `firstframe` / `mad` 的 CLI 级行为测试（离线）。

只断言外部行为：退出码、中文输出、产物文件；不窥探内部实现。
"""

from __future__ import annotations

import pytest
from typer.testing import CliRunner

from flowforge.cli import app

runner = CliRunner()


# ---------------------------------------------------------------- 命令注册


def test_help_lists_frames_commands():
    result = runner.invoke(app, ["--help"])
    assert result.exit_code == 0
    assert "lastframe" in result.output
    assert "firstframe" in result.output
    assert "mad" in result.output


# ---------------------------------------------------------------- lastframe / firstframe


def test_lastframe_extracts_white_last_frame(two_tone_video, tmp_path, read_pixels):
    out = tmp_path / "last.png"
    result = runner.invoke(app, ["lastframe", str(two_tone_video), "-o", str(out)])
    assert result.exit_code == 0, result.output
    assert "尾帧已抽取" in result.output
    assert out.is_file()
    pixels = read_pixels(out)
    assert min(pixels) > 225  # 次秒全白：全部通道接近 255
    assert max(pixels) > 240


def test_firstframe_extracts_black_first_frame(two_tone_video, tmp_path, read_pixels):
    out = tmp_path / "first.png"
    result = runner.invoke(app, ["firstframe", str(two_tone_video), "-o", str(out)])
    assert result.exit_code == 0, result.output
    assert "首帧已抽取" in result.output
    assert out.is_file()
    assert max(read_pixels(out)) < 30  # 首秒全黑：全部通道接近 0


def test_lastframe_without_output_option_chinese_error(two_tone_video):
    result = runner.invoke(app, ["lastframe", str(two_tone_video)])
    assert result.exit_code == 1
    assert "缺少输出路径" in result.output


def test_firstframe_without_output_option_chinese_error(two_tone_video):
    result = runner.invoke(app, ["firstframe", str(two_tone_video)])
    assert result.exit_code == 1
    assert "缺少输出路径" in result.output


def test_lastframe_missing_video_chinese_error(tmp_path):
    missing = tmp_path / "nope.mp4"
    result = runner.invoke(app, ["lastframe", str(missing), "-o", str(tmp_path / "o.png")])
    assert result.exit_code == 1
    assert "视频文件不存在" in result.output


def test_firstframe_missing_video_chinese_error(tmp_path):
    missing = tmp_path / "nope.mp4"
    result = runner.invoke(app, ["firstframe", str(missing), "-o", str(tmp_path / "o.png")])
    assert result.exit_code == 1
    assert "视频文件不存在" in result.output


def test_lastframe_fake_video_rejected(fake_video, tmp_path):
    """14 字节伪视频在 CLI 层同样被拒收（工单必列回归）。"""
    result = runner.invoke(app, ["lastframe", str(fake_video), "-o", str(tmp_path / "o.png")])
    assert result.exit_code == 1
    assert "文件损坏或不是有效视频" in result.output


def test_lastframe_rejects_directory_as_video(tmp_path):
    result = runner.invoke(app, ["lastframe", str(tmp_path), "-o", str(tmp_path / "o.png")])
    assert result.exit_code == 1
    assert "不是文件" in result.output


# ---------------------------------------------------------------- mad


def test_mad_prints_value(black_png, white_png):
    result = runner.invoke(app, ["mad", str(black_png), str(white_png)])
    assert result.exit_code == 0, result.output
    assert "MAD" in result.output


def test_mad_same_image_prints_zero(black_png):
    result = runner.invoke(app, ["mad", str(black_png), str(black_png)])
    assert result.exit_code == 0, result.output
    assert "MAD = 0.00" in result.output


def test_mad_known_difference_value_in_expected_range(black_png, white_png):
    result = runner.invoke(app, ["mad", str(black_png), str(white_png)])
    assert result.exit_code == 0, result.output
    # 纯黑对纯白满量程差异：解析输出数值断言落在 [250, 255]
    value = float(result.output.strip().split("MAD = ")[1].split()[0])
    assert 250.0 <= value <= 255.0


def test_mad_with_threshold_over_reports_failure(black_png, white_png):
    result = runner.invoke(
        app, ["mad", str(black_png), str(white_png), "--threshold", "25"]
    )
    assert result.exit_code == 1
    assert "超过阈值" in result.output


def test_mad_with_threshold_under_reports_pass(black_png):
    result = runner.invoke(
        app, ["mad", str(black_png), str(black_png), "--threshold", "25"]
    )
    assert result.exit_code == 0, result.output
    assert "未超过阈值" in result.output


def test_mad_missing_image_chinese_error(tmp_path):
    missing = tmp_path / "nope.png"
    result = runner.invoke(app, ["mad", str(missing), str(missing)])
    assert result.exit_code == 1
    assert "图片文件不存在" in result.output


# ---------------------------------------------------------------- ffmpeg 缺失指引


@pytest.mark.parametrize("command", ["lastframe", "firstframe", "mad"])
def test_missing_ffmpeg_shows_windows_install_guidance(
    command, tmp_path, monkeypatch, red_video, black_png
):
    """ffmpeg/ffprobe 不在 PATH：中文报错附 Windows 安装指引（gyan.dev）。"""
    monkeypatch.setenv("PATH", str(tmp_path / "no-such-bin"))
    if command == "mad":
        argv = ["mad", str(black_png), str(black_png)]
    else:
        argv = [command, str(red_video), "-o", str(tmp_path / "o.png")]
    result = runner.invoke(app, argv)
    assert result.exit_code == 1
    assert "未找到" in result.output
    assert "gyan.dev" in result.output
