"""styleforge 命令行入口。"""

from __future__ import annotations

from pathlib import Path
from typing import Annotated

import typer

from styleforge.fake_driver import FakeDriver
from styleforge.frames import FramesError
from styleforge.frames import mad as compute_mad
from styleforge.frames import extract_first_frame, extract_last_frame
from styleforge.runner import RunOptions, RunReport, RunStateError
from styleforge.runner import run as run_script
from styleforge.script import ScriptInvalid, load_script, output_dir

app = typer.Typer(
    no_args_is_help=True,
    add_completion=False,
)


@app.callback()
def main() -> None:
    """styleforge：校验 YAML 剧本，并（后续工单）驱动 Google Flow 链式生成连续视频。"""


@app.command()
def check(
    script_path: Annotated[Path | None, typer.Argument(help="剧本 YAML 文件路径")] = None,
) -> None:
    """校验剧本 YAML：合法输出中文 OK 摘要；非法逐条中文报错并以退出码 1 结束。"""
    if script_path is None:
        typer.echo("错误：缺少剧本文件路径。用法：styleforge check <剧本.yaml>")
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
) -> None:
    """按剧本逐镜生成并归档；当前仅 --fake 模式可用（真实驱动属后续工单）。"""
    if script_path is None:
        typer.echo("错误：缺少剧本文件路径。用法：styleforge run <剧本.yaml> --fake")
        raise typer.Exit(code=1)
    try:
        script = load_script(script_path)
    except ScriptInvalid as exc:
        typer.echo(f"剧本校验未通过，共 {len(exc.errors)} 个问题：")
        for number, message in enumerate(exc.errors, start=1):
            typer.echo(f"{number}. {message}")
        raise typer.Exit(code=1) from None
    if not fake:
        typer.echo(
            "错误：真实驱动模式尚未接入（bb-browser 驱动属后续工单），"
            "且真实生成默认拒绝执行。请先用 --fake 模式验证剧本与编排。"
        )
        raise typer.Exit(code=1)
    # fake 模式启用重试模拟（测试编排 retry 分支）；真实模式将是零自动重试。
    options = RunOptions(retry_simulation=True, resume=resume, log=typer.echo)
    try:
        report = run_script(
            script, FakeDriver(), base_dir=script_path.parent, options=options
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
        typer.echo("错误：缺少输出路径。用法：styleforge lastframe <视频.mp4> -o <输出.png>")
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
        typer.echo("错误：缺少输出路径。用法：styleforge firstframe <视频.mp4> -o <输出.png>")
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
