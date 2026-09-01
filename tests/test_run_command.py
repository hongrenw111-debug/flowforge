"""`flowforge run` 的 CLI 级行为测试（离线：--fake 假驱动 + 真 ffmpeg）。

只断言外部行为：退出码、中文输出、产物文件落地。
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from typer.testing import CliRunner

from conftest import make_solid_png
from flowforge.cli import app

runner = CliRunner()


@pytest.fixture(autouse=True)
def no_sleep(monkeypatch):
    """CLI 测试零延时：RunOptions 的 sleep 默认值在实例化时解析 time.sleep。"""
    monkeypatch.setattr("time.sleep", lambda seconds: None)


@pytest.fixture
def workspace(tmp_path, monkeypatch):
    """产物 output/ 落在临时目录内。"""
    monkeypatch.chdir(tmp_path)
    return tmp_path


def write_script(tmp_path: Path, text: str) -> Path:
    script_path = tmp_path / "script.yaml"
    script_path.write_text(text, encoding="utf-8")
    return script_path


CHAIN3 = """\
name: cli-run
shots:
  - prompt: "第一镜"
  - prompt: "第二镜"
    first_frame: {source: last_frame}
  - prompt: "第三镜"
    first_frame: {source: last_frame}
"""


def test_help_lists_run_command():
    result = runner.invoke(app, ["--help"])
    assert result.exit_code == 0
    assert "run" in result.output


def test_run_without_path_exits_1_in_chinese():
    result = runner.invoke(app, ["run"])
    assert result.exit_code == 1
    assert "缺少剧本文件路径" in result.output


def test_run_without_fake_flag_refuses_politely(workspace):
    """授权闸门：真实生成默认拒绝；bb-browser 驱动属 04 工单。"""
    path = write_script(workspace, CHAIN3)
    result = runner.invoke(app, ["run", str(path)])
    assert result.exit_code == 1
    assert "真实" in result.output
    assert "--fake" in result.output


def test_run_invalid_script_lists_chinese_errors(workspace):
    path = write_script(workspace, "name: bad\nshots: []\n")
    result = runner.invoke(app, ["run", str(path), "--fake"])
    assert result.exit_code == 1
    assert "剧本校验未通过" in result.output
    assert "至少需要一个镜头" in result.output


def test_run_fake_full_pipeline_archives_and_reports(workspace):
    path = write_script(workspace, CHAIN3)
    result = runner.invoke(app, ["run", str(path), "--fake"])
    assert result.exit_code == 0, result.output
    assert "=== 运行报告：cli-run ===" in result.output
    assert "镜头 1" in result.output
    assert "完成" in result.output
    shots_dir = workspace / "output" / "cli-run" / "shots"
    for index in (1, 2, 3):
        assert (shots_dir / f"shot-{index:02d}.mp4").is_file()
        assert (shots_dir / f"shot-{index:02d}-first.png").is_file()
        assert (shots_dir / f"shot-{index:02d}-last.png").is_file()
        assert (shots_dir / f"shot-{index:02d}-mad.json").is_file()
    assert (workspace / "output" / "cli-run" / "run-state.json").is_file()


def test_run_fake_failure_reports_and_exits_1(workspace):
    text = (
        "name: cli-fail\n"
        "shots:\n"
        '  - prompt: "无源镜"\n'
        "    first_frame: {source: last_frame}\n"
    )
    path = write_script(workspace, text)
    result = runner.invoke(app, ["run", str(path), "--fake"])
    assert result.exit_code == 1
    assert "失败" in result.output
    assert "上一镜头" in result.output
    assert "--resume" in result.output  # 断点续跑指引


def test_run_fake_suspect_warns_but_exits_0(workspace):
    make_solid_png(workspace, "black", "frame.png")
    text = (
        "name: cli-suspect\n"
        "shots:\n"
        '  - prompt: "漂移镜"\n'
        "    first_frame: {source: image, path: frame.png}\n"
    )
    path = write_script(workspace, text)
    result = runner.invoke(app, ["run", str(path), "--fake"])
    assert result.exit_code == 0, result.output
    assert "警告" in result.output
    assert "suspect" in result.output
    state = json.loads(
        (workspace / "output" / "cli-suspect" / "run-state.json").read_text(
            encoding="utf-8"
        )
    )
    assert state["shots"][0]["suspect"] is True
    assert state["shots"][0]["mad"] > 25.0


def test_run_resume_without_state_exits_1(workspace):
    path = write_script(workspace, CHAIN3)
    result = runner.invoke(app, ["run", str(path), "--fake", "--resume"])
    assert result.exit_code == 1
    assert "断点" in result.output
