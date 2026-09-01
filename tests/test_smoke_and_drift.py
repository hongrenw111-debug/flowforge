"""工单 05 单元测试：smoke 与 drift-test 命令行及授权闸门测试（--fake 离线模式）。"""

from __future__ import annotations

from pathlib import Path

import pytest
from typer.testing import CliRunner

from flowforge.cli import app

runner = CliRunner()


@pytest.fixture(autouse=True)
def no_sleep(monkeypatch):
    """CLI 测试零延时。"""
    monkeypatch.setattr("time.sleep", lambda seconds: None)


@pytest.fixture
def workspace(tmp_path, monkeypatch):
    """产物 output/ 落在临时目录内。"""
    monkeypatch.chdir(tmp_path)
    return tmp_path


def test_smoke_fake_auto_generates_image_and_completes(workspace: Path):
    result = runner.invoke(app, ["smoke", "--fake"])
    assert result.exit_code == 0
    assert "=== 开始执行单镜冒烟测试 ===" in result.stdout
    assert "=== 冒烟测试成功证据链 ===" in result.stdout
    assert "✓ 视频产物：" in result.stdout
    assert "✓ 首帧抽取：" in result.stdout
    assert "✓ 尾帧抽取：" in result.stdout
    assert "冒烟测试全流程通过！" in result.stdout

    # 验证物理文件
    shots_dir = workspace / "output" / "smoke" / "shots"
    assert (shots_dir / "shot-01.mp4").is_file()
    assert (shots_dir / "shot-01-first.png").is_file()
    assert (shots_dir / "shot-01-last.png").is_file()
    assert (shots_dir / "shot-01-mad.json").is_file()


def test_smoke_non_interactive_without_yes_rejected(workspace: Path):
    result = runner.invoke(app, ["smoke"])
    assert result.exit_code == 1
    assert "非交互环境必须提供 --yes 授权" in result.stdout


def test_smoke_custom_image_nonexistent_fails(workspace: Path):
    nonexistent = workspace / "missing.png"
    result = runner.invoke(app, ["smoke", "--fake", "--image", str(nonexistent)])
    assert result.exit_code == 1
    assert "指定的首帧图片不存在" in result.stdout


def test_drift_test_fake_two_shots_reports_mad(workspace: Path):
    result = runner.invoke(app, ["drift-test", "--fake", "--shots", "2"])
    assert result.exit_code == 0
    assert "=== 开始执行 2 跳尾帧接力漂移实验 ===" in result.stdout
    assert "=== 尾帧接力漂移分析报告 ===" in result.stdout
    assert "镜头 01：" in result.stdout
    assert "镜头 02：" in result.stdout
    assert "单跳接力 MAD =" in result.stdout
    assert "相对初始锚定累积 MAD =" in result.stdout
    assert "漂移实验完成，全部产物已归档。" in result.stdout

    # 验证 2 个镜头的文件
    shots_dir = workspace / "output" / "drift-test" / "shots"
    for i in (1, 2):
        assert (shots_dir / f"shot-{i:02d}.mp4").is_file()
        assert (shots_dir / f"shot-{i:02d}-first.png").is_file()
        assert (shots_dir / f"shot-{i:02d}-last.png").is_file()


def test_drift_test_invalid_shots_count(workspace: Path):
    result = runner.invoke(app, ["drift-test", "--fake", "--shots", "1"])
    assert result.exit_code == 1
    assert "至少需要 2 个镜头" in result.stdout


def test_drift_test_non_interactive_without_yes_rejected(workspace: Path):
    result = runner.invoke(app, ["drift-test"])
    assert result.exit_code == 1
    assert "非交互环境必须提供 --yes 授权" in result.stdout
