"""`flowforge run` 真实模式的授权闸门与驱动接线测试（离线，绝不触碰 bb-browser）。

Amendments 第 5 条：一切真实生成默认拒绝执行，需交互确认或显式授权旗标
（--yes）；未经明示授权零消耗。驱动构造与编排调用通过 monkeypatch 截获，
不发起任何 CLI 子进程。
"""

from __future__ import annotations

from pathlib import Path

import pytest
from typer.testing import CliRunner

from flowforge import cli
from flowforge.cli import app
from flowforge.fake_driver import FakeDriver
from flowforge.runner import RunReport, ShotSummary

runner = CliRunner()

SCRIPT = 'name: gate\nshots:\n  - prompt: "镜一"\n'


def write_script(tmp_path: Path) -> Path:
    path = tmp_path / "script.yaml"
    path.write_text(SCRIPT, encoding="utf-8")
    return path


def make_report(script_name: str, output_dir: Path) -> RunReport:
    return RunReport(
        script_name=script_name,
        total_shots=1,
        shots=(ShotSummary(index=1, status="done"),),
        suspects=(),
        failed=None,
        stopped_reason=None,
        output_dir=output_dir,
    )


@pytest.fixture
def intercepted(monkeypatch, tmp_path):
    """截获 BbBrowserDriver 构造与编排调用，返回记录容器。"""
    captured: dict = {}

    def fake_ctor(**kwargs):
        captured["ctor_kwargs"] = kwargs
        return FakeDriver()

    def fake_run(script, driver, *, base_dir, options):
        captured["driver"] = driver
        captured["options"] = options
        captured["base_dir"] = base_dir
        return make_report(script.name, tmp_path / "out")

    monkeypatch.setattr(cli, "BbBrowserDriver", fake_ctor)
    monkeypatch.setattr(cli, "run_script", fake_run)
    return captured


# ---------------------------------------------------------------- 非交互：必须显式旗标


def test_non_interactive_without_yes_refuses(tmp_path):
    """非交互环境无 --yes：拒绝执行，零点数消耗。"""
    path = write_script(tmp_path)
    result = runner.invoke(app, ["run", str(path)])
    assert result.exit_code == 1
    assert "Flow 点数" in result.output
    assert "--yes" in result.output
    assert "--fake" in result.output


def test_non_interactive_with_yes_proceeds_without_prompt(tmp_path, intercepted):
    """非交互环境显式 --yes：跳过交互直接执行，零自动重试。"""
    path = write_script(tmp_path)
    result = runner.invoke(app, ["run", str(path), "--yes"])
    assert result.exit_code == 0, result.output
    assert intercepted["options"].retry_simulation is False
    assert isinstance(intercepted["driver"], FakeDriver)  # 截获构造的替身
    assert "log" in intercepted["ctor_kwargs"]


# ---------------------------------------------------------------- 交互：确认对话框


def test_interactive_confirm_yes_runs(monkeypatch, tmp_path, intercepted):
    path = write_script(tmp_path)
    monkeypatch.setattr(cli, "_is_interactive", lambda: True)
    result = runner.invoke(app, ["run", str(path)], input="y\n")
    assert result.exit_code == 0, result.output
    assert intercepted["options"].retry_simulation is False


def test_interactive_confirm_declined_aborts_without_run(monkeypatch, tmp_path, intercepted):
    path = write_script(tmp_path)
    monkeypatch.setattr(cli, "_is_interactive", lambda: True)
    result = runner.invoke(app, ["run", str(path)], input="n\n")
    assert result.exit_code == 1
    assert "取消" in result.output
    assert "driver" not in intercepted  # 未发起任何运行


# ---------------------------------------------------------------- 选项透传


def test_wait_timeout_passthrough(tmp_path, intercepted):
    path = write_script(tmp_path)
    result = runner.invoke(
        app, ["run", str(path), "--yes", "--wait-timeout", "123.5"]
    )
    assert result.exit_code == 0, result.output
    assert intercepted["options"].wait_timeout == 123.5


def test_resume_passthrough_in_real_mode(tmp_path, intercepted):
    path = write_script(tmp_path)
    result = runner.invoke(app, ["run", str(path), "--yes", "--resume"])
    assert result.exit_code == 0, result.output
    assert intercepted["options"].resume is True


def test_fake_mode_still_uses_retry_simulation(tmp_path, monkeypatch):
    """--fake 行为不变：假驱动 + 重试模拟（编排逻辑测试路径）。"""
    captured: dict = {}

    def fake_run(script, driver, *, base_dir, options):
        captured["driver"] = driver
        captured["options"] = options
        return make_report(script.name, tmp_path / "out")

    monkeypatch.setattr(cli, "run_script", fake_run)
    path = write_script(tmp_path)
    result = runner.invoke(app, ["run", str(path), "--fake"])
    assert result.exit_code == 0, result.output
    assert isinstance(captured["driver"], FakeDriver)
    assert captured["options"].retry_simulation is True
