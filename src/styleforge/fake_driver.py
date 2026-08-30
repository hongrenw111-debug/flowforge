"""内存假驱动（FakeDriver）——编排逻辑全自动测试的替身，全程零网页零积分。

产物是真的：download_clip 用 ffmpeg lavfi 现场生成纯色 mp4（每镜颜色可注入），
因此真 ffmpeg 抽帧、真 MAD 比对、真完整性校验全部走真实代码路径。
行为是假的：按提示词注入「失败几次后成功 / 永远失败 / 产物颜色 / 伪视频」。
注入「产出颜色与输入首帧不同」即可让 MAD 飙高，覆盖 suspect 报警路径。
"""

from __future__ import annotations

import subprocess
from dataclasses import dataclass
from pathlib import Path

from styleforge.driver import ClipInfo, Driver, DriverError
from styleforge.frames import MIN_VIDEO_DURATION_SECONDS

_FFMPEG_TIMEOUT = 60

# 画幅 → 假产物像素尺寸（保持对应长宽比，够小、生成快）。
_ASPECT_SIZES: dict[str, str] = {
    "16:9": "64x36",
    "9:16": "36x64",
    "1:1": "48x48",
}

# 樱之诗取证实坑：14 字节下载失败响应文本（'No session found'）被当 .mp4 保存。
_GARBAGE_BYTES = b"No session fou"


@dataclass
class FakeShotBehavior:
    """单个镜头（按提示词区分）的行为剧本。

    - failures_before_success：前 N 次 wait_for_completion 抛 DriverError，之后成功
    - always_fails：永远失败（覆盖熔断 / 真跑停止路径）
    - color：假产物纯色（ffmpeg 颜色名）；与输入首帧不同色即可触发 MAD suspect
    - download_garbage：落地 14 字节伪视频（验证完整性校验拒收路径）
    """

    failures_before_success: int = 0
    always_fails: bool = False
    color: str = "red"
    download_garbage: bool = False


class FakeDriver(Driver):
    """按提示词注入行为剧本的内存假驱动；全部方法调用记录在 calls 里。

    calls 的元素形如 ("set_first_frame", "C:/…/shot-01.png")、("generate",)，
    测试据此断言文件传递链与「续跑不再调用 done 镜头的驱动方法」。
    """

    def __init__(
        self,
        behaviors: dict[str, FakeShotBehavior] | None = None,
        default_behavior: FakeShotBehavior | None = None,
    ) -> None:
        self.calls: list[tuple[str, ...]] = []
        self.behaviors = dict(behaviors or {})
        self.default_behavior = default_behavior or FakeShotBehavior()
        self._project_seq = 0
        self._current_url: str | None = None
        self._current_prompt: str | None = None
        self._attempts: dict[str, int] = {}
        self._download_seq = 0
        self._clip_seq = 0
        self._model = "omni-1.1-flash"
        self._duration = 8
        self._aspect = "16:9"
        self._outputs = 1

    # ---------------------------------------------------------------- 合同实现

    def new_project(self, name: str) -> str:
        self.calls.append(("new_project", name))
        self._project_seq += 1
        self._current_url = f"https://flow.example/fake/{self._project_seq}"
        return self._current_url

    def open_project(self, url: str) -> None:
        self.calls.append(("open_project", url))
        self._current_url = url

    def set_first_frame(self, image_path: Path) -> None:
        self.calls.append(("set_first_frame", str(image_path)))

    def clear_first_frame(self) -> None:
        self.calls.append(("clear_first_frame",))

    def set_prompt(self, text: str) -> None:
        self.calls.append(("set_prompt", text))
        self._current_prompt = text

    def configure(self, model: str, duration: int, aspect: str, outputs: int) -> None:
        self.calls.append(("configure", model, str(duration), aspect, str(outputs)))
        self._model = model
        self._duration = duration
        self._aspect = aspect
        self._outputs = outputs

    def generate(self) -> None:
        self.calls.append(("generate",))
        if self._current_prompt is None:
            raise DriverError("假驱动：generate 之前必须先 set_prompt")
        self._attempts[self._current_prompt] = (
            self._attempts.get(self._current_prompt, 0) + 1
        )

    def wait_for_completion(self, timeout: float) -> ClipInfo:
        self.calls.append(("wait_for_completion",))
        behavior = self._current_behavior()
        prompt = self._current_prompt or ""
        attempt = self._attempts.get(prompt, 0)
        if behavior.always_fails or attempt <= behavior.failures_before_success:
            raise DriverError(f"假驱动：镜头生成失败（模拟，提示词：{prompt}）")
        self._clip_seq += 1
        return ClipInfo(clip_id=f"fake-clip-{self._clip_seq:03d}")

    def download_clip(self, dest_dir: Path) -> None:
        dest_dir = Path(dest_dir)
        dest_dir.mkdir(parents=True, exist_ok=True)
        self._download_seq += 1
        out = dest_dir / f"clip-{self._download_seq:02d}.mp4"
        behavior = self._current_behavior()
        if behavior.download_garbage:
            out.write_bytes(_GARBAGE_BYTES)
            self.calls.append(("download_clip", str(out)))
            return out
        size = _ASPECT_SIZES.get(self._aspect)
        if size is None:
            raise DriverError(f"假驱动：不支持的画幅：{self._aspect}")
        # 真 ffmpeg 生成纯色 mp4：真 moov atom、真时长，能过完整性校验与抽帧。
        proc = subprocess.run(
            [
                "ffmpeg", "-y", "-v", "error",
                "-f", "lavfi",
                "-i",
                f"color=c={behavior.color}:s={size}:d={max(self._duration, MIN_VIDEO_DURATION_SECONDS)}",
                "-pix_fmt", "yuv420p", str(out),
            ],
            capture_output=True,
            timeout=_FFMPEG_TIMEOUT,
        )
        if proc.returncode != 0:
            raise DriverError(
                f"假驱动：生成视频失败：{proc.stderr.decode(errors='replace')}"
            )
        self.calls.append(("download_clip", str(out)))
        return out

    # ---------------------------------------------------------------- 内部

    def _current_behavior(self) -> FakeShotBehavior:
        assert self._current_prompt is not None
        return self.behaviors.get(self._current_prompt, self.default_behavior)
