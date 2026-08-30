"""styleforge 命令行入口。"""

from __future__ import annotations

from pathlib import Path
from typing import Annotated

import typer

from styleforge.frames import FramesError
from styleforge.frames import mad as compute_mad
from styleforge.frames import extract_first_frame, extract_last_frame
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
    )
    typer.echo(f"输出目录：{output_dir(script).as_posix()}")


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
