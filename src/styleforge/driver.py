"""驱动层接口（Driver）——编排层与网页之间唯一主接缝。

编排引擎（runner）只认这里的九个方法；v1 的 bb-browser 实现（工单 04）、
测试用内存假驱动（FakeDriver）以及未来可能的 Playwright 实现都实现本合同。
任何实现都不得在本接口之外泄漏网页细节。
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from pathlib import Path


class DriverError(Exception):
    """驱动操作失败；message 为面向用户的中文错误，由编排层记录为镜头失败。"""


class DriverTimeoutError(DriverError):
    """等待生成完成超时（wait_for_completion 超过上限仍未得到产物）。"""


@dataclass(frozen=True)
class ClipInfo:
    """wait_for_completion 返回的单段产物信息；clip_id 是产物的页面内身份。"""

    clip_id: str


class Driver(ABC):
    """驱动层合同：new_project/open_project/set_first_frame/clear_first_frame/
    set_prompt/configure/generate/wait_for_completion/download_clip。

    单镜头的驱动调用序列固定为：
    set_first_frame 或 clear_first_frame → set_prompt → configure → generate
    → wait_for_completion → download_clip。
    """

    @abstractmethod
    def new_project(self, name: str) -> str:
        """新建一个以剧本名命名的 Flow 项目，返回项目 URL。"""

    @abstractmethod
    def open_project(self, url: str) -> None:
        """打开既有项目（断点续跑时使用保存的 URL，不重复建项目）。"""

    @abstractmethod
    def set_first_frame(self, image_path: Path) -> None:
        """把本地图片设为当前镜头的首帧（锚定帧 / 尾帧接力共用此入口）。"""

    @abstractmethod
    def clear_first_frame(self) -> None:
        """清空当前镜头的首帧（纯文生视频；也防上一镜的画面残留串镜）。"""

    @abstractmethod
    def set_prompt(self, text: str) -> None:
        """填写当前镜头的提示词。"""

    @abstractmethod
    def configure(self, model: str, duration: int, aspect: str, outputs: int) -> None:
        """设置当前镜头的生效参数：模型 / 时长（秒）/ 画幅 / 输出数。"""

    @abstractmethod
    def generate(self) -> None:
        """提交一次生成（真实模式下这一步开始消耗积分，绝不自动重复提交）。"""

    @abstractmethod
    def wait_for_completion(self, timeout: float) -> ClipInfo:
        """阻塞等待当前生成完成；超过 timeout（秒）抛 DriverTimeoutError。"""

    @abstractmethod
    def download_clip(self, dest_dir: Path) -> Path:
        """把当前完成的产物下载到 dest_dir，返回落地的 mp4 路径。

        产物必须先过编排层的完整性校验才允许进入链条（拒绝伪视频）。
        """
