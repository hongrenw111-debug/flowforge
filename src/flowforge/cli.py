"""flowforge 命令行入口。"""

from __future__ import annotations

import struct
import sys
import zlib
from pathlib import Path
from typing import Annotated

import typer

from flowforge.bb_driver import BbBrowserDriver
from flowforge.fake_driver import FakeDriver
from flowforge.frames import FramesError
from flowforge.frames import mad as compute_mad
from flowforge.frames import extract_first_frame, extract_last_frame
from flowforge.runner import RunOptions, RunReport, RunStateError
from flowforge.runner import run as run_script
from flowforge.script import (
    Defaults,
    FirstFrame,
    Script,
    ScriptInvalid,
    Shot,
    load_script,
    output_dir,
)

app = typer.Typer(
    no_args_is_help=True,
    add_completion=False,
)


def _is_interactive() -> bool:
    """当前是否为交互式终端（授权闸门据此决定走确认对话框还是要求显式旗标）。"""
    return sys.stdin.isatty() and sys.stdout.isatty()


@app.callback()
def main() -> None:
    """flowforge：校验 YAML 剧本，驱动 Google Flow 链式生成连续视频（真实生成需显式授权）。"""


@app.command()
def check(
    script_path: Annotated[Path | None, typer.Argument(help="剧本 YAML 文件路径")] = None,
) -> None:
    """校验剧本 YAML：合法输出中文 OK 摘要；非法逐条中文报错并以退出码 1 结束。"""
    if script_path is None:
        typer.echo("错误：缺少剧本文件路径。用法：flowforge check <剧本.yaml>")
        raise typer.Exit(code=1)
    try:
        script = load_script(script_path)
    except ScriptInvalid as exc:
        typer.echo(f"剧本校验未通过，共 {len(exc.errors)} 个问题：")
        for number, message in enumerate(exc.errors, start=1):
            typer.echo(f"{number}. {message}")
        raise typer.Exit(code=1) from None
    defaults = script.defaults
    typer.echo(f"剧本校验通过：{script.name}")
    typer.echo(f"镜头数：{len(script.shots)}")
    typer.echo(
        f"默认参数：模型 {defaults.model} / 时长 {defaults.duration} 秒"
        f" / 画幅 {defaults.aspect} / 输出数 {defaults.outputs}"
        f" / 下载 {defaults.download} / 重试 {defaults.retry} 次"
        f" / MAD 阈值 {defaults.mad_threshold:g}"
    )
    typer.echo(f"输出目录：{output_dir(script).as_posix()}")


@app.command()
def run(
    script_path: Annotated[Path | None, typer.Argument(help="剧本 YAML 文件路径")] = None,
    fake: Annotated[
        bool,
        typer.Option(
            "--fake",
            help="使用内存假驱动（零网页零积分：产物为 ffmpeg 生成的纯色视频，启用重试模拟）",
        ),
    ] = False,
    resume: Annotated[
        bool,
        typer.Option("--resume", help="断点续跑：加载 run-state.json，跳过已成功镜头"),
    ] = False,
    wait_timeout: Annotated[
        float,
        typer.Option("--wait-timeout", help="等待单镜生成完成的超时上限（秒）"),
    ] = 600.0,
    yes: Annotated[
        bool,
        typer.Option(
            "--yes",
            help="显式授权真实生成（将消耗 Flow 点数，零自动重试）；非交互环境必须提供本旗标",
        ),
    ] = False,
) -> None:
    """按剧本逐镜生成并归档；真实模式需显式授权（未经明示授权零消耗）。"""
    if script_path is None:
        typer.echo("错误：缺少剧本文件路径。用法：flowforge run <剧本.yaml> [--fake]")
        raise typer.Exit(code=1)
    try:
        script = load_script(script_path)
    except ScriptInvalid as exc:
        typer.echo(f"剧本校验未通过，共 {len(exc.errors)} 个问题：")
        for number, message in enumerate(exc.errors, start=1):
            typer.echo(f"{number}. {message}")
        raise typer.Exit(code=1) from None

    if fake:
        # fake 模式启用重试模拟（测试编排 retry 分支）；真实模式零自动重试。
        options = RunOptions(
            retry_simulation=True,
            resume=resume,
            wait_timeout=wait_timeout,
            log=typer.echo,
        )
        driver: FakeDriver | BbBrowserDriver = FakeDriver()
    else:
        # 授权闸门（Amendments 第 5 条）：一切真实生成默认拒绝执行，
        # 需交互确认或显式授权旗标；未经明示授权零消耗。
        if not yes:
            if _is_interactive():
                typer.echo(
                    "真实模式将通过 bb-browser 驱动你的 Chrome 操作 Google Flow，"
                    "将消耗 Flow 点数（真实生成失败零自动重试）。"
                )
                if not typer.confirm("确认执行真实生成？", default=False):
                    typer.echo("已取消：未获授权，未消耗任何点数。")
                    raise typer.Exit(code=1) from None
            else:
                typer.echo(
                    "错误：真实模式将消耗 Flow 点数（真实生成零自动重试）。"
                    "非交互环境必须显式传旗标 --yes 授权后才能执行；"
                    "如只想验证剧本与编排，请用 --fake 模式。"
                )
                raise typer.Exit(code=1) from None
        options = RunOptions(
            retry_simulation=False,
            resume=resume,
            wait_timeout=wait_timeout,
            log=typer.echo,
        )
        driver = BbBrowserDriver(log=typer.echo)

    try:
        report = run_script(
            script, driver, base_dir=script_path.parent, options=options
        )
    except RunStateError as exc:
        typer.echo(f"错误：{exc}")
        raise typer.Exit(code=1) from None
    _print_run_report(report)
    if not report.completed:
        raise typer.Exit(code=1)


def _print_run_report(report: RunReport) -> None:
    """最终报告：逐镜结果 + suspect 醒目警告 + 停止原因与续跑指引。"""
    typer.echo(f"=== 运行报告：{report.script_name} ===")
    for summary in report.shots:
        if summary.status == "done":
            if summary.mad is None:
                line = "完成（纯文生视频，无输入帧可比对 MAD）"
            elif summary.suspect:
                line = f"完成，但 MAD {summary.mad:.2f} 超标（suspect）"
            else:
                line = f"完成（MAD {summary.mad:.2f}）"
        elif summary.status == "failed":
            line = f"失败：{summary.error}"
        else:
            line = "未执行"
        typer.echo(f"镜头 {summary.index}：{line}")
    if report.suspects:
        typer.echo("警告：以下镜头 MAD 超过阈值，请人工复核后再采信：")
        for entry in report.suspects:
            typer.echo(
                f"  - 镜头 {entry.index}：MAD {entry.mad:.2f}"
                f"（阈值 {entry.threshold:g}）"
            )
    if report.stopped_reason:
        typer.echo(f"停止：{report.stopped_reason}")
        typer.echo("断点已保留在 run-state.json；修复后可加 --resume 从失败镜头续跑。")
    typer.echo(f"产物目录：{report.output_dir.as_posix()}")


@app.command()
def lastframe(
    video_path: Annotated[Path, typer.Argument(help="视频文件路径")],
    output: Annotated[Path | None, typer.Option("--output", "-o", help="尾帧 PNG 输出路径")] = None,
) -> None:
    """抽取视频最后一帧（尾帧）为 PNG，供下一镜头接力使用。"""
    if output is None:
        typer.echo("错误：缺少输出路径。用法：flowforge lastframe <视频.mp4> -o <输出.png>")
        raise typer.Exit(code=1)
    try:
        out = extract_last_frame(video_path, output)
    except FramesError as exc:
        typer.echo(f"错误：{exc}")
        raise typer.Exit(code=1) from None
    typer.echo(f"尾帧已抽取：{out}")


@app.command()
def firstframe(
    video_path: Annotated[Path, typer.Argument(help="视频文件路径")],
    output: Annotated[Path | None, typer.Option("--output", "-o", help="首帧 PNG 输出路径")] = None,
) -> None:
    """抽取视频第一帧（首帧）为 PNG，用于验证输入帧是否被模型忠实执行。"""
    if output is None:
        typer.echo("错误：缺少输出路径。用法：flowforge firstframe <视频.mp4> -o <输出.png>")
        raise typer.Exit(code=1)
    try:
        out = extract_first_frame(video_path, output)
    except FramesError as exc:
        typer.echo(f"错误：{exc}")
        raise typer.Exit(code=1) from None
    typer.echo(f"首帧已抽取：{out}")


@app.command()
def mad(
    image_a: Annotated[Path, typer.Argument(help="图片 A 路径")],
    image_b: Annotated[Path, typer.Argument(help="图片 B 路径")],
    threshold: Annotated[
        float | None,
        typer.Option("--threshold", "-t", help="MAD 阈值；提供时输出是否超阈的结论（流水线默认 25）"),
    ] = None,
) -> None:
    """计算两张图片的 MAD（64×36 全像素平均绝对差，0-255）。"""
    try:
        value = compute_mad(image_a, image_b)
    except FramesError as exc:
        typer.echo(f"错误：{exc}")
        raise typer.Exit(code=1) from None
    typer.echo(f"MAD = {value:.2f}")
    if threshold is None:
        return
    if value > threshold:
        typer.echo(f"结论：MAD {value:.2f} 超过阈值 {threshold:.2f}，判定不通过")
        raise typer.Exit(code=1)
    typer.echo(f"结论：MAD {value:.2f} 未超过阈值 {threshold:.2f}，判定通过")


def _ensure_smoke_image(target_path: Path) -> Path:
    """确保测试用首帧图就绪（若不存在则生成标准的 1280x720 纯色 PNG 图）。"""
    if target_path.is_file():
        return target_path
    target_path.parent.mkdir(parents=True, exist_ok=True)
    width, height = 1280, 720
    # 生成深蓝/靛青底色，RGB: (24, 48, 89)
    raw_row = b"\x00" + bytes((24, 48, 89)) * width
    raw_data = raw_row * height
    compressed = zlib.compress(raw_data)

    def _png_chunk(tag: bytes, data: bytes) -> bytes:
        crc = zlib.crc32(tag + data) & 0xFFFFFFFF
        return struct.pack(">I", len(data)) + tag + data + struct.pack(">I", crc)

    ihdr_data = struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0)
    png_bytes = (
        b"\x89PNG\r\n\x1a\n"
        + _png_chunk(b"IHDR", ihdr_data)
        + _png_chunk(b"IDAT", compressed)
        + _png_chunk(b"IEND", b"")
    )
    target_path.write_bytes(png_bytes)
    return target_path


@app.command()
def smoke(
    image: Annotated[
        Path | None,
        typer.Option("--image", "-i", help="首帧锚定测试图路径（若不提供则自动生成 1280x720 测试图）"),
    ] = None,
    fake: Annotated[
        bool,
        typer.Option("--fake", help="使用内存假驱动（离线测试，零网络零积分）"),
    ] = False,
    yes: Annotated[
        bool,
        typer.Option("--yes", help="显式授权真实生成（将消耗 Flow 点数，零自动重试）"),
    ] = False,
    wait_timeout: Annotated[
        float,
        typer.Option("--wait-timeout", help="等待单镜生成完成的超时上限（秒）"),
    ] = 600.0,
) -> None:
    """单镜端到端冒烟测试：开项目 → 传首帧 → 生成 8s 视频 → 下载校验 → 抽取首尾帧 → 计算 MAD。"""
    if not fake and not yes:
        if _is_interactive():
            typer.echo(
                "【点数授权确认】真实冒烟测试将通过 bb-browser 驱动 Chrome 操作 Google Flow。\n"
                "预计消耗：Omni 1.1 Flash / 8 秒 / 1 镜（参考消耗约 20 积分档，真实生成失败零自动重试）。"
            )
            if not typer.confirm("确认执行真实冒烟？", default=False):
                typer.echo("已取消：未获授权，未消耗任何点数。")
                raise typer.Exit(code=1) from None
        else:
            typer.echo(
                "错误：真实冒烟将消耗 Flow 点数（真实生成失败零自动重试）。"
                "非交互环境必须提供 --yes 授权后才能执行；"
                "如只想验证流程，请使用 --fake 模式。"
            )
            raise typer.Exit(code=1) from None

    target_dir = Path("output/smoke").resolve()
    target_dir.mkdir(parents=True, exist_ok=True)

    if image is None:
        image_path = _ensure_smoke_image(target_dir / "smoke-start.png")
    else:
        if not image.is_file():
            typer.echo(f"错误：指定的首帧图片不存在：{image}")
            raise typer.Exit(code=1)
        image_path = image.resolve()

    script = Script(
        name="smoke",
        defaults=Defaults(
            model="omni-1.1-flash",
            duration=8,
            aspect="16:9",
            outputs=1,
            download="original-720p",
            retry=0,
            mad_threshold=25.0,
        ),
        shots=[
            Shot(
                prompt="A calm cinematic camera panning across a serene landscape, highly detailed 8k",
                first_frame=FirstFrame(source="image", path=str(image_path)),
            )
        ],
    )

    options = RunOptions(
        retry_simulation=fake,
        resume=False,
        wait_timeout=wait_timeout,
        log=typer.echo,
    )
    driver: FakeDriver | BbBrowserDriver = FakeDriver() if fake else BbBrowserDriver(log=typer.echo)

    typer.echo("=== 开始执行单镜冒烟测试 ===")
    try:
        report = run_script(script, driver, base_dir=target_dir, options=options)
    except RunStateError as exc:
        typer.echo(f"冒烟失败：{exc}")
        raise typer.Exit(code=1) from None

    if not report.completed:
        typer.echo(f"冒烟未完成：{report.stopped_reason or '执行失败'}")
        raise typer.Exit(code=1)

    shot_summary = report.shots[0]
    shots_dir = report.output_dir / "shots"
    video_path = shots_dir / "shot-01.mp4"
    first_frame_path = shots_dir / "shot-01-first.png"
    last_frame_path = shots_dir / "shot-01-last.png"
    mad_file = shots_dir / "shot-01-mad.json"

    typer.echo("\n=== 冒烟测试成功证据链 ===")
    typer.echo(f"✓ 视频产物：{video_path.as_posix()}")
    typer.echo(f"✓ 首帧抽取：{first_frame_path.as_posix()}")
    typer.echo(f"✓ 尾帧抽取：{last_frame_path.as_posix()}")
    if shot_summary.mad is not None:
        typer.echo(f"✓ 首帧比对 MAD：{shot_summary.mad:.2f}（阈值 25.0）")
    typer.echo(f"✓ 质量记录：{mad_file.as_posix()}")
    typer.echo("冒烟测试全流程通过！")


@app.command("drift-test")
def drift_test(
    shots: Annotated[
        int,
        typer.Option("--shots", "-n", help="接力镜头数（默认 3）"),
    ] = 3,
    image: Annotated[
        Path | None,
        typer.Option("--image", "-i", help="第 1 镜初始锚定图片路径（若不提供则自动生成）"),
    ] = None,
    fake: Annotated[
        bool,
        typer.Option("--fake", help="使用内存假驱动（离线测试，零网络零积分）"),
    ] = False,
    yes: Annotated[
        bool,
        typer.Option("--yes", help="显式授权真实生成（将消耗 Flow 点数，零自动重试）"),
    ] = False,
    wait_timeout: Annotated[
        float,
        typer.Option("--wait-timeout", help="等待单镜生成完成的超时上限（秒）"),
    ] = 600.0,
) -> None:
    """尾帧接力漂移实验：逐跳 MAD 与累积漂移分析（实证检验接力模式一致性）。"""
    if shots < 2:
        typer.echo("错误：drift-test 至少需要 2 个镜头进行接力测试。")
        raise typer.Exit(code=1)

    if not fake and not yes:
        if _is_interactive():
            typer.echo(
                "【点数授权确认】真实 Drift 接力实验将通过 bb-browser 驱动 Chrome 操作 Google Flow。\n"
                f"预计消耗：Omni 1.1 Flash / 8 秒 / {shots} 镜（尾帧接力模式，参考消耗约 {shots * 20} 积分档，真实生成失败零自动重试）。"
            )
            if not typer.confirm("确认执行真实 Drift 实验？", default=False):
                typer.echo("已取消：未获授权，未消耗任何点数。")
                raise typer.Exit(code=1) from None
        else:
            typer.echo(
                f"错误：真实 Drift 实验将消耗约 {shots * 20} 积分（真实生成失败零自动重试）。"
                "非交互环境必须提供 --yes 授权后才能执行；"
                "如只想验证流程，请使用 --fake 模式。"
            )
            raise typer.Exit(code=1) from None

    target_dir = Path("output/drift-test").resolve()
    target_dir.mkdir(parents=True, exist_ok=True)

    if image is None:
        image_path = _ensure_smoke_image(target_dir / "drift-anchor.png")
    else:
        if not image.is_file():
            typer.echo(f"错误：指定的首帧图片不存在：{image}")
            raise typer.Exit(code=1)
        image_path = image.resolve()

    shot_list = [
        Shot(
            prompt="Cinematic shot 1, stable realistic character in scenic environment",
            first_frame=FirstFrame(source="image", path=str(image_path)),
        )
    ]
    for idx in range(2, shots + 1):
        shot_list.append(
            Shot(
                prompt=f"Cinematic continuation shot {idx}, character moving naturally",
                first_frame=FirstFrame(source="last_frame"),
            )
        )

    script = Script(
        name="drift-test",
        defaults=Defaults(
            model="omni-1.1-flash",
            duration=8,
            aspect="16:9",
            outputs=1,
            download="original-720p",
            retry=0,
            mad_threshold=25.0,
        ),
        shots=shot_list,
    )

    options = RunOptions(
        retry_simulation=fake,
        resume=False,
        wait_timeout=wait_timeout,
        log=typer.echo,
    )
    driver: FakeDriver | BbBrowserDriver = FakeDriver() if fake else BbBrowserDriver(log=typer.echo)

    typer.echo(f"=== 开始执行 {shots} 跳尾帧接力漂移实验 ===")
    try:
        report = run_script(script, driver, base_dir=target_dir, options=options)
    except RunStateError as exc:
        typer.echo(f"实验中断：{exc}")
        raise typer.Exit(code=1) from None

    if not report.completed:
        typer.echo(f"实验未完成：{report.stopped_reason or '执行失败'}")
        raise typer.Exit(code=1)

    typer.echo("\n=== 尾帧接力漂移分析报告 ===")
    typer.echo(f"初始锚定图：{image_path.as_posix()}")
    anchor_img = image_path

    for summary in report.shots:
        s_idx = summary.index
        shots_dir = report.output_dir / "shots"
        video_path = shots_dir / f"shot-{s_idx:02d}.mp4"
        first_frame = shots_dir / f"shot-{s_idx:02d}-first.png"
        last_frame = shots_dir / f"shot-{s_idx:02d}-last.png"

        step_mad = summary.mad
        step_str = f"{step_mad:.2f}" if step_mad is not None else "N/A"

        try:
            cum_mad = compute_mad(anchor_img, first_frame) if first_frame.is_file() else None
            cum_str = f"{cum_mad:.2f}" if cum_mad is not None else "N/A"
        except Exception:
            cum_mad = None
            cum_str = "计算异常"

        status_flag = "✓ 正常" if (cum_mad is not None and cum_mad <= 25.0) else "⚠️ 漂移偏高"
        typer.echo(
            f"镜头 {s_idx:02d}：单跳接力 MAD = {step_str} | 相对初始锚定累积 MAD = {cum_str} [{status_flag}]"
        )
        typer.echo(f"  - 首帧：{first_frame.as_posix()}")
        typer.echo(f"  - 尾帧：{last_frame.as_posix()}")

    typer.echo("漂移实验完成，全部产物已归档。")
