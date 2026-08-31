"""编排引擎（runner）——把剧本变成逐镜生成、验证、归档的完整运行。

职责：镜头顺序执行、首帧来源三态解析（image 锚定 / last_frame 尾帧接力 /
none 纯文生视频）、逐镜 MAD 验证与产物归档、run-state.json 断点状态、
真跑零自动重试 + 连续失败熔断、fake 模式重试模拟、断点续跑、节奏注入。

红线（Amendments 第 3/7 条）：真实模式镜头失败 → 标 failed、停止剧本、
断点保留，绝不二次提交生成——用户的钱不是重试预算。
编排层只认 Driver 接口，不窥探驱动实现。
"""

from __future__ import annotations

import json
import random
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

from styleforge.driver import Driver
from styleforge.frames import (
    FramesError,
    ensure_valid_video,
    extract_first_frame,
    extract_last_frame,
    mad,
)
from styleforge.script import (
    Script,
    Shot,
    effective_shot_params,
    output_dir,
    resolve_image_path,
)

# 运行状态文件名（每剧本每次运行一个；断点续跑 / 取证的载体）。
STATE_FILENAME = "run-state.json"

_STATUS_DONE = "done"
_STATUS_FAILED = "failed"
_STATUS_PENDING = "pending"


class RunStateError(Exception):
    """断点状态问题（缺失 / 损坏 / 与剧本不一致）；message 为中文。"""


class ShotInputError(Exception):
    """镜头输入解析失败（图片缺失 / 接力链断裂）；message 为中文。"""


# ---------------------------------------------------------------- 运行选项与报告


@dataclass
class RunOptions:
    """运行参数；测试注入零延时 sleep 与固定区间实现节奏可控。"""

    # 拟人化节奏（spec 默认）：动作间随机延时、镜头间冷却，均为闭区间秒数。
    action_delay: tuple[float, float] = (3.0, 8.0)
    shot_cooldown: tuple[float, float] = (30.0, 60.0)
    # 熔断：连续失败达上限即停（默认 1：一次失败就停）。
    max_consecutive_failures: int = 1
    # fake 模式重试模拟（测试编排 retry 分支）；真实模式必须 False（零自动重试）。
    retry_simulation: bool = False
    # 断点续跑：加载已有 run-state.json，跳过 done 镜头。
    resume: bool = False
    # wait_for_completion 超时上限（秒）。
    wait_timeout: float = 600.0
    # 注入点：睡眠与日志（测试用记录函数替换；default_factory 延迟解析，
    # 让测试可以 monkeypatch time.sleep 实现零延时）。
    sleep: Callable[[float], None] = field(default_factory=lambda: time.sleep)
    log: Callable[[str], None] = field(default=print)


@dataclass(frozen=True)
class SuspectEntry:
    """MAD 超阈值的镜头（最终报告醒目列出）。"""

    index: int
    mad: float
    threshold: float


@dataclass(frozen=True)
class FailureEntry:
    """导致停止的失败镜头。"""

    index: int
    error: str
    attempts: int


@dataclass(frozen=True)
class ShotSummary:
    """单个镜头的运行结果摘要（报告逐镜展示用）。"""

    index: int
    status: str
    mad: float | None = None
    suspect: bool = False
    error: str | None = None


@dataclass(frozen=True)
class RunReport:
    """一次运行的最终结果。"""

    script_name: str
    total_shots: int
    shots: tuple[ShotSummary, ...]
    suspects: tuple[SuspectEntry, ...]
    failed: FailureEntry | None
    stopped_reason: str | None
    output_dir: Path

    @property
    def completed(self) -> bool:
        """全部镜头 done 且无失败（suspect 不算失败）。"""
        return self.failed is None and all(
            summary.status == _STATUS_DONE for summary in self.shots
        )


# ---------------------------------------------------------------- 状态文件


def _initial_state(script: Script) -> dict:
    return {
        "script": script.name,
        "project_url": None,
        "mad_threshold": float(script.defaults.mad_threshold),
        "shots": [
            {"index": index, "status": _STATUS_PENDING, "attempts": 0}
            for index in range(1, len(script.shots) + 1)
        ],
    }


def _save_state(state_path: Path, state: dict) -> None:
    state_path.write_text(
        json.dumps(state, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )


def _load_state(state_path: Path, script: Script) -> dict:
    if not state_path.is_file():
        raise RunStateError(
            f"断点状态文件不存在：{state_path}（无法续跑；去掉 --resume 可全新运行）"
        )
    try:
        state = json.loads(state_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise RunStateError(f"断点状态文件无法解析：{state_path}（{exc}）") from exc
    if state.get("script") != script.name:
        raise RunStateError(
            f'断点状态属于剧本 "{state.get("script")}"，与当前剧本 '
            f'"{script.name}" 不一致，拒绝续跑'
        )
    shots = state.get("shots")
    if not isinstance(shots, list) or len(shots) != len(script.shots):
        count = len(shots) if isinstance(shots, list) else 0
        raise RunStateError(
            f"断点状态与当前剧本镜头数不一致"
            f"（状态 {count} 个 / 剧本 {len(script.shots)} 个），拒绝续跑"
        )
    return state


# ---------------------------------------------------------------- 首帧来源解析


def _first_frame_source(shot: Shot) -> str:
    return shot.first_frame.source if shot.first_frame is not None else "none"


def _resolve_input_frame(
    shot: Shot, index: int, state: dict, base_dir: Path
) -> Path | None:
    """解析镜头的首帧输入：image → 指定图；last_frame → 上一镜尾帧产物；none → None。

    返回绝对路径：驱动上传图片需要绝对路径，不依赖进程工作目录。
    """
    source = _first_frame_source(shot)
    if source == "none":
        return None
    if source == "image":
        first_frame = shot.first_frame
        if not first_frame.path or not first_frame.path.strip():
            raise ShotInputError(
                f"镜头 {index}：first_frame.source 为 image 时缺少 path"
            )
        image_path = resolve_image_path(base_dir, first_frame.path)
        if not image_path.is_file():
            raise ShotInputError(f"镜头 {index}：首帧图片不存在：{image_path}")
        return image_path.absolute()
    # last_frame：接力链取上一镜尾帧产物
    if index == 1:
        raise ShotInputError(
            "镜头 1：首帧来源为 last_frame，但它没有上一镜头可接力"
        )
    previous = state["shots"][index - 2]
    previous_last = previous.get("last_frame")
    if previous.get("status") != _STATUS_DONE or not previous_last:
        raise ShotInputError(
            f"镜头 {index}：上一镜头 {index - 1} 未产出尾帧，无法接力"
        )
    previous_path = Path(previous_last)
    if not previous_path.is_file():
        raise ShotInputError(f"镜头 {index}：上一镜头尾帧文件丢失：{previous_last}")
    return previous_path.absolute()


# ---------------------------------------------------------------- 主流程


def run(script: Script, driver: Driver, *, base_dir: Path, options: RunOptions) -> RunReport:
    """按剧本顺序执行全部镜头；返回最终报告（CLI 据此出报告与退出码）。"""
    out_dir = output_dir(script)
    shots_dir = out_dir / "shots"
    shots_dir.mkdir(parents=True, exist_ok=True)
    state_path = out_dir / STATE_FILENAME

    state = (
        _load_state(state_path, script) if options.resume else _initial_state(script)
    )
    _save_state(state_path, state)

    # 项目只在首次创建；续跑打开保存的 URL，不重复建项目。
    project_url = state.get("project_url")
    if project_url:
        options.log(f"断点续跑：打开已有 Flow 项目 {project_url}")
        driver.open_project(project_url)
    else:
        project_url = driver.new_project(script.name)
        state["project_url"] = project_url
        _save_state(state_path, state)
        options.log(f"已创建 Flow 项目：{project_url}")

    threshold = float(script.defaults.mad_threshold)
    # 真实模式零自动重试：max_attempts 恒为 1；fake 模式按剧本 retry 模拟。
    max_attempts = 1 + (script.defaults.retry if options.retry_simulation else 0)
    if options.retry_simulation:
        options.log(
            f"fake 模式：启用重试模拟（每镜最多 {max_attempts} 次尝试）"
        )

    summaries: list[ShotSummary] = []
    suspects: list[SuspectEntry] = []
    failure: FailureEntry | None = None
    stopped_reason: str | None = None
    consecutive_failures = 0
    total = len(script.shots)

    for index, shot in enumerate(script.shots, start=1):
        shot_state = state["shots"][index - 1]
        if shot_state.get("status") == _STATUS_DONE:
            options.log(f"镜头 {index}/{total}：已完成（断点续跑，跳过）")
            summaries.append(ShotSummary(index=index, status=_STATUS_DONE))
            continue

        params = effective_shot_params(shot, script.defaults)
        source = _first_frame_source(shot)
        options.log(
            f"镜头 {index}/{total}：开始"
            f"（首帧来源 {source}，模型 {params['model']}，{params['duration']} 秒）"
        )

        try:
            input_frame = _resolve_input_frame(shot, index, state, base_dir)
        except ShotInputError as exc:
            # 输入缺失不构成一次提交：不计尝试、绝不重试；
            # 接力链已断，后续镜头也无法成立，直接停止（断点保留）。
            options.log(f"镜头 {index}：输入解析失败：{exc}")
            error_text = str(exc)
            shot_state["status"] = _STATUS_FAILED
            shot_state["error"] = error_text
            _save_state(state_path, state)
            failure = FailureEntry(
                index=index, error=error_text, attempts=int(shot_state.get("attempts", 0))
            )
            summaries.append(
                ShotSummary(index=index, status=_STATUS_FAILED, error=error_text)
            )
            if options.retry_simulation:
                stopped_reason = f"镜头 {index} 失败：{error_text}"
            else:
                stopped_reason = "真实模式镜头失败即停（真跑零自动重试）"
            break

        # 逐次尝试；真实模式 max_attempts=1（失败即停，绝不二次提交）。
        error_text: str | None = None
        artifacts: dict | None = None
        attempts_used = int(shot_state.get("attempts", 0))
        for _ in range(max_attempts):
            attempts_used += 1
            shot_state["attempts"] = attempts_used
            _save_state(state_path, state)
            options.log(f"镜头 {index}：第 {attempts_used} 次尝试")
            try:
                artifacts = _execute_attempt(
                    shot=shot,
                    index=index,
                    input_frame=input_frame,
                    params=params,
                    shots_dir=shots_dir,
                    threshold=threshold,
                    driver=driver,
                    options=options,
                )
                error_text = None
                break
            except Exception as exc:  # 驱动/帧处理失败都算本次尝试失败
                error_text = str(exc)
                artifacts = None
                options.log(f"镜头 {index}：尝试失败：{error_text}")

        if artifacts is None:
            error_text = error_text or "未知错误"
            shot_state["status"] = _STATUS_FAILED
            shot_state["error"] = error_text
            _save_state(state_path, state)
            failure = FailureEntry(
                index=index, error=error_text, attempts=attempts_used
            )
            summaries.append(
                ShotSummary(index=index, status=_STATUS_FAILED, error=error_text)
            )
            if not options.retry_simulation:
                stopped_reason = (
                    f"镜头 {index} 失败即停（真实模式零自动重试）：{error_text}"
                )
                break
            consecutive_failures += 1
            if consecutive_failures >= options.max_consecutive_failures:
                stopped_reason = (
                    f"连续失败 {consecutive_failures} 次，达到熔断上限 "
                    f"{options.max_consecutive_failures}，停止剧本"
                )
                break
            options.log(
                f"镜头 {index} 标记失败，继续执行（连续失败 "
                f"{consecutive_failures}/{options.max_consecutive_failures}）"
            )
            continue

        video, first_png, last_png, mad_value, suspect = artifacts
        shot_state.update(
            {
                "status": _STATUS_DONE,
                "video": video.as_posix(),
                "first_frame": first_png.as_posix(),
                "last_frame": last_png.as_posix(),
                "input_frame": input_frame.as_posix() if input_frame else None,
                "mad": round(mad_value, 4) if mad_value is not None else None,
                "suspect": suspect,
                "params": dict(params),
            }
        )
        _save_state(state_path, state)
        consecutive_failures = 0
        summaries.append(
            ShotSummary(
                index=index,
                status=_STATUS_DONE,
                mad=mad_value,
                suspect=suspect,
            )
        )
        if suspect:
            suspects.append(SuspectEntry(index=index, mad=mad_value, threshold=threshold))
            options.log(
                f"镜头 {index}：警告：MAD {mad_value:.2f} 超过阈值 {threshold:g}，"
                "标记 suspect"
            )
        if index < total:
            _pause(options, options.shot_cooldown)

    # 未执行到的镜头（熔断/失败停止之后）在报告里补 pending，保持逐镜齐全。
    processed = {summary.index for summary in summaries}
    for index in range(1, total + 1):
        if index not in processed:
            summaries.append(ShotSummary(index=index, status=_STATUS_PENDING))
    summaries.sort(key=lambda summary: summary.index)

    return RunReport(
        script_name=script.name,
        total_shots=total,
        shots=tuple(summaries),
        suspects=tuple(suspects),
        failed=failure,
        stopped_reason=stopped_reason,
        output_dir=out_dir,
    )


def _execute_attempt(
    *,
    shot: Shot,
    index: int,
    input_frame: Path | None,
    params: dict,
    shots_dir: Path,
    threshold: float,
    driver: Driver,
    options: RunOptions,
) -> tuple[Path, Path, Path, float | None, bool]:
    """单次尝试的完整动作序列：设帧 → 填词 → 配参 → 生成 → 等待 → 下载
    → 完整性校验 → 归档 → 抽帧 → MAD。失败抛异常，由调用方计次。"""

    if input_frame is None:
        driver.clear_first_frame()  # 防上一镜画面残留串镜
    else:
        driver.set_first_frame(input_frame)
    _pause(options, options.action_delay)
    driver.configure(
        params["model"], params["duration"], params["aspect"], params["outputs"]
    )
    _pause(options, options.action_delay)
    driver.set_prompt(shot.prompt)
    _pause(options, options.action_delay)
    driver.generate()
    clip = driver.wait_for_completion(options.wait_timeout)
    options.log(f"镜头 {index}：生成完成（{clip.clip_id}），开始下载")
    raw_video = driver.download_clip(shots_dir)

    # 完整性校验：伪视频（无 moov atom / 时长不足）不得进入链条。
    try:
        duration = ensure_valid_video(raw_video)
    except FramesError as exc:
        raw_video.unlink(missing_ok=True)
        options.log(f"镜头 {index}：伪视频已拒收并删除：{raw_video.name}")
        raise FramesError(f"镜头 {index}：{exc}") from exc
    video = shots_dir / f"shot-{index:02d}.mp4"
    raw_video.replace(video)
    options.log(f"镜头 {index}：下载完成并通过完整性校验（{duration:.1f} 秒）")

    first_png = extract_first_frame(video, shots_dir / f"shot-{index:02d}-first.png")
    last_png = extract_last_frame(video, shots_dir / f"shot-{index:02d}-last.png")

    if input_frame is not None:
        mad_value = mad(input_frame, first_png)
        suspect = mad_value > threshold
        options.log(f"镜头 {index}：MAD {mad_value:.2f}（阈值 {threshold:g}）")
    else:
        mad_value = None
        suspect = False

    _write_mad_json(
        shots_dir / f"shot-{index:02d}-mad.json",
        index=index,
        mad_value=mad_value,
        threshold=threshold,
        suspect=suspect,
        input_frame=input_frame,
        first_png=first_png,
    )
    return video, first_png, last_png, mad_value, suspect


def _write_mad_json(
    path: Path,
    *,
    index: int,
    mad_value: float | None,
    threshold: float,
    suspect: bool,
    input_frame: Path | None,
    first_png: Path,
) -> None:
    """镜头的 MAD 证据文件（无证据不报成功）。"""
    doc = {
        "shot": index,
        "mad": round(mad_value, 4) if mad_value is not None else None,
        "threshold": threshold,
        "suspect": suspect,
        "input_frame": input_frame.as_posix() if input_frame else None,
        "first_frame": first_png.as_posix(),
    }
    if mad_value is None:
        doc["note"] = "纯文生视频镜头无输入帧，跳过 MAD 比对"
    path.write_text(
        json.dumps(doc, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )


def _pause(options: RunOptions, bounds: tuple[float, float]) -> None:
    """节奏注入：在区间内随机取值后交给可注入的 sleep（测试注入记录函数可观测 0 值）。"""
    options.sleep(random.uniform(bounds[0], bounds[1]))
