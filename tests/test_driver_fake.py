"""驱动合同（Driver）与内存假驱动（FakeDriver）的行为测试（离线）。

FakeDriver 的产物由真 ffmpeg lavfi 现场生成，可过真完整性校验、走真抽帧与 MAD；
行为（成功/失败/颜色/伪视频）按提示词注入，调用序列全程记录供测试断言。
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from flowforge.driver import ClipInfo, Driver, DriverError, DriverTimeoutError
from flowforge.fake_driver import FakeDriver, FakeShotBehavior
from flowforge.frames import FramesError, ensure_valid_video, extract_first_frame

# ---------------------------------------------------------------- 驱动合同


def test_fake_driver_implements_driver_contract():
    assert isinstance(FakeDriver(), Driver)


def test_driver_contract_is_the_nine_method_interface():
    """驱动接口定型（04 票 bb-browser 驱动按此实现）：九个方法缺一不可。"""
    assert Driver.__abstractmethods__ == frozenset(
        {
            "new_project",
            "open_project",
            "set_first_frame",
            "clear_first_frame",
            "set_prompt",
            "configure",
            "generate",
            "wait_for_completion",
            "download_clip",
        }
    )


def test_driver_timeout_error_is_driver_error():
    assert issubclass(DriverTimeoutError, DriverError)


# ---------------------------------------------------------------- 调用记录


def test_new_project_returns_url_and_records(tmp_path):
    driver = FakeDriver()
    url = driver.new_project("my-story")
    assert url.startswith("https://")
    assert ("new_project", "my-story") in driver.calls


def test_open_project_records_url():
    driver = FakeDriver()
    driver.open_project("https://flow.example/x")
    assert ("open_project", "https://flow.example/x") in driver.calls


def test_set_first_frame_records_path(tmp_path):
    image = tmp_path / "shot-01.png"
    image.write_bytes(b"png")
    driver = FakeDriver()
    driver.set_first_frame(image)
    assert ("set_first_frame", str(image)) in driver.calls


def test_clear_first_frame_records():
    driver = FakeDriver()
    driver.clear_first_frame()
    assert ("clear_first_frame",) in driver.calls


def test_full_shot_call_sequence_recorded(tmp_path):
    driver = FakeDriver()
    driver.set_prompt("开场")
    driver.configure("omni-1.1-flash", 8, "16:9", 1)
    driver.generate()
    driver.wait_for_completion(timeout=600.0)
    driver.download_clip(tmp_path)
    assert ("set_prompt", "开场") in driver.calls
    assert ("configure", "omni-1.1-flash", "8", "16:9", "1") in driver.calls
    assert ("generate",) in driver.calls
    assert ("wait_for_completion",) in driver.calls
    assert any(call[0] == "download_clip" for call in driver.calls)


# ---------------------------------------------------------------- 成功路径：真 ffmpeg 产物


def test_wait_for_completion_returns_clip_info():
    driver = FakeDriver()
    driver.set_prompt("一镜")
    driver.generate()
    clip = driver.wait_for_completion(timeout=600.0)
    assert isinstance(clip, ClipInfo)
    assert clip.clip_id


def test_download_clip_produces_valid_video(tmp_path):
    driver = FakeDriver()
    driver.set_prompt("一镜")
    driver.configure("omni-1.1-flash", 8, "16:9", 1)
    driver.generate()
    driver.wait_for_completion(timeout=600.0)
    path = driver.download_clip(tmp_path)
    assert path.is_file()
    assert path.parent == tmp_path
    duration = ensure_valid_video(path)
    assert 7.0 <= duration <= 9.0


def test_download_clip_first_frame_has_behavior_color(tmp_path):
    driver = FakeDriver(
        behaviors={"一镜": FakeShotBehavior(color="red")},
    )
    driver.set_prompt("一镜")
    driver.generate()
    driver.wait_for_completion(timeout=600.0)
    video = driver.download_clip(tmp_path)
    first_png = tmp_path / "first.png"
    extract_first_frame(video, first_png)
    reds = _decode_reds(first_png)
    assert min(reds) > 200
    assert max(_decode_greens(first_png)) < 50


def test_default_behavior_is_success_with_red_output(tmp_path):
    driver = FakeDriver()
    driver.set_prompt("没有注入行为的镜头")
    driver.generate()
    driver.wait_for_completion(timeout=600.0)
    video = driver.download_clip(tmp_path)
    first_png = tmp_path / "first.png"
    extract_first_frame(video, first_png)
    assert min(_decode_reds(first_png)) > 200


def test_aspect_maps_to_video_dimensions(tmp_path):
    driver = FakeDriver()
    driver.set_prompt("竖屏")
    driver.configure("omni-1.1-flash", 8, "9:16", 1)
    driver.generate()
    driver.wait_for_completion(timeout=600.0)
    video = driver.download_clip(tmp_path)
    assert _video_dimensions(video) == (36, 64)


def test_unknown_aspect_raises_chinese_driver_error(tmp_path):
    driver = FakeDriver()
    driver.set_prompt("x")
    driver.configure("omni-1.1-flash", 8, "4:3", 1)
    driver.generate()
    driver.wait_for_completion(timeout=600.0)
    with pytest.raises(DriverError) as excinfo:
        driver.download_clip(tmp_path)
    assert "画幅" in str(excinfo.value)


# ---------------------------------------------------------------- 失败注入


def test_failures_before_success_then_success():
    driver = FakeDriver(behaviors={"一镜": FakeShotBehavior(failures_before_success=1)})
    driver.set_prompt("一镜")
    driver.generate()
    with pytest.raises(DriverError) as excinfo:
        driver.wait_for_completion(timeout=600.0)
    assert "生成失败" in str(excinfo.value)
    driver.generate()  # 第二次尝试
    clip = driver.wait_for_completion(timeout=600.0)
    assert isinstance(clip, ClipInfo)


def test_always_fails_keeps_raising():
    driver = FakeDriver(behaviors={"一镜": FakeShotBehavior(always_fails=True)})
    driver.set_prompt("一镜")
    for _ in range(3):
        driver.generate()
        with pytest.raises(DriverError):
            driver.wait_for_completion(timeout=600.0)


def test_failures_are_keyed_by_prompt():
    driver = FakeDriver(
        behaviors={"坏镜": FakeShotBehavior(always_fails=True)},
    )
    driver.set_prompt("好镜")
    driver.generate()
    assert isinstance(driver.wait_for_completion(timeout=600.0), ClipInfo)
    driver.set_prompt("坏镜")
    driver.generate()
    with pytest.raises(DriverError):
        driver.wait_for_completion(timeout=600.0)


def test_download_garbage_behavior_writes_fake_video(tmp_path):
    """樱之诗实坑复现：14 字节下载失败响应被当 .mp4 保存，必须被完整性校验拒收。"""
    driver = FakeDriver(behaviors={"一镜": FakeShotBehavior(download_garbage=True)})
    driver.set_prompt("一镜")
    driver.generate()
    driver.wait_for_completion(timeout=600.0)
    path = driver.download_clip(tmp_path)
    assert path.stat().st_size == 14
    with pytest.raises(FramesError):
        ensure_valid_video(path)


# ---------------------------------------------------------------- 辅助


def _decode_reds(png: Path) -> list[int]:
    return _decode_channels(png)[0::3]


def _decode_greens(png: Path) -> list[int]:
    return _decode_channels(png)[1::3]


def _decode_channels(png: Path) -> bytes:
    proc = subprocess.run(
        [
            "ffmpeg", "-v", "error", "-i", str(png),
            "-f", "rawvideo", "-pix_fmt", "rgb24", "-",
        ],
        capture_output=True,
        timeout=60,
        check=True,
    )
    return proc.stdout


def _video_dimensions(video: Path) -> tuple[int, int]:
    proc = subprocess.run(
        [
            "ffprobe", "-v", "error", "-select_streams", "v:0",
            "-show_entries", "stream=width,height",
            "-of", "csv=p=0:s=x",
            str(video),
        ],
        capture_output=True,
        text=True,
        timeout=60,
        check=True,
    )
    width, height = proc.stdout.strip().split("x")
    return int(width), int(height)
