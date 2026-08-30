"""styleforge 命令行入口。"""

from __future__ import annotations

from pathlib import Path
from typing import Annotated

import typer

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
