"""runner 编排引擎的行为测试（离线：内存假驱动 + 真 ffmpeg 产物 + 真状态文件）。

只断言外部行为：产物文件、run-state.json 内容、驱动调用序列、运行报告。
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from conftest import make_solid_png
from flowforge.fake_driver import FakeDriver, FakeShotBehavior
from flowforge.frames import mad as compute_mad
from flowforge.runner import RunOptions, RunStateError, run as run_script
from flowforge.script import load_script


@pytest.fixture
def workspace(tmp_path, monkeypatch):
    """工作目录切换到临时目录：产物 output/ 落在 tmp_path 内。"""
    monkeypatch.chdir(tmp_path)
    return tmp_path


def zero_options(**overrides) -> RunOptions:
    """测试默认零延时；可按需覆盖任意选项。"""
    values = {
        "action_delay": (0.0, 0.0),
        "shot_cooldown": (0.0, 0.0),
        "retry_simulation": True,
        "sleep": lambda seconds: None,
    }
    values.update(overrides)
    return RunOptions(**values)


def write_script(tmp_path: Path, text: str) -> Path:
    script_path = tmp_path / "script.yaml"
    script_path.write_text(text, encoding="utf-8")
    return script_path


def read_state(workspace: Path, name: str) -> dict:
    state_path = workspace / "output" / name / "run-state.json"
    return json.loads(state_path.read_text(encoding="utf-8"))


def shots_dir(workspace: Path, name: str) -> Path:
    return workspace / "output" / name / "shots"


CHAIN3 = """\
name: chain3
shots:
  - prompt: "第一镜"
  - prompt: "第二镜"
    first_frame: {source: last_frame}
  - prompt: "第三镜"
    first_frame: {source: last_frame}
"""


# ---------------------------------------------------------------- 全流程归档


def test_full_fake_run_archives_everything(workspace):
    script = load_script(write_script(workspace, CHAIN3))
    driver = FakeDriver()
    report = run_script(script, driver, base_dir=workspace, options=zero_options())

    assert report.completed
    assert not report.suspects
    assert report.failed is None

    directory = shots_dir(workspace, "chain3")
    for index in (1, 2, 3):
        assert (directory / f"shot-{index:02d}.mp4").is_file()
        assert (directory / f"shot-{index:02d}-first.png").is_file()
        assert (directory / f"shot-{index:02d}-last.png").is_file()
        assert (directory / f"shot-{index:02d}-mad.json").is_file()

    state = read_state(workspace, "chain3")
    assert state["script"] == "chain3"
    assert state["project_url"].startswith("https://")
    assert state["mad_threshold"] == 25.0
    assert [s["status"] for s in state["shots"]] == ["done", "done", "done"]
    assert [s["attempts"] for s in state["shots"]] == [1, 1, 1]
    first = state["shots"][0]
    assert first["video"].endswith("shots/shot-01.mp4")
    assert first["first_frame"].endswith("shots/shot-01-first.png")
    assert first["last_frame"].endswith("shots/shot-01-last.png")
    assert first["input_frame"] is None
    assert first["mad"] is None  # 纯文生视频无输入帧
    assert first["suspect"] is False
    assert first["params"] == {
        "model": "omni-1.1-flash",
        "duration": 8,
        "aspect": "16:9",
        "outputs": 1,
    }
    # 后两镜有输入帧，MAD 是数值
    assert isinstance(state["shots"][1]["mad"], float)
    assert state["shots"][1]["input_frame"].endswith("shots/shot-01-last.png")


def test_mad_json_records_evidence(workspace):
    script = load_script(write_script(workspace, CHAIN3))
    driver = FakeDriver()
    run_script(script, driver, base_dir=workspace, options=zero_options())

    doc = json.loads(
        (shots_dir(workspace, "chain3") / "shot-02-mad.json").read_text(encoding="utf-8")
    )
    assert doc["shot"] == 2
    assert isinstance(doc["mad"], float)
    assert doc["threshold"] == 25.0
    assert doc["suspect"] is False
    assert doc["input_frame"].endswith("shots/shot-01-last.png")
    assert doc["first_frame"].endswith("shots/shot-02-first.png")


# ---------------------------------------------------------------- 双模式首帧传递链


def test_image_source_uses_specified_image_as_input(workspace):
    image = make_solid_png(workspace, "red", "shot-01.png")
    text = (
        "name: anchored\n"
        "shots:\n"
        '  - prompt: "锚定镜"\n'
        "    first_frame: {source: image, path: shot-01.png}\n"
    )
    script = load_script(write_script(workspace, text))
    driver = FakeDriver()
    report = run_script(script, driver, base_dir=workspace, options=zero_options())

    assert report.completed
    assert ("set_first_frame", str(image)) in driver.calls
    state = read_state(workspace, "anchored")
    assert state["shots"][0]["input_frame"] == image.as_posix()
    # 同色锚定：MAD 极低，不标 suspect
    assert state["shots"][0]["mad"] < 25.0
    assert state["shots"][0]["suspect"] is False


def test_last_frame_chain_passes_previous_artifact(workspace):
    script = load_script(write_script(workspace, CHAIN3))
    driver = FakeDriver()
    run_script(script, driver, base_dir=workspace, options=zero_options())

    expected_input = shots_dir(workspace, "chain3") / "shot-01-last.png"
    assert ("set_first_frame", str(expected_input)) in driver.calls
    # 内容级传递链：镜 1 产物红色 → 其尾帧红色 → 镜 2 首帧仍红色
    from flowforge.frames import extract_first_frame

    shot2_first = workspace / "shot2_first_probe.png"
    extract_first_frame(
        shots_dir(workspace, "chain3") / "shot-02.mp4", shot2_first
    )
    assert compute_mad(expected_input, shot2_first) < 25.0


def test_none_source_clears_first_frame(workspace):
    image = make_solid_png(workspace, "red", "shot-01.png")
    text = (
        "name: clears\n"
        "shots:\n"
        '  - prompt: "锚定镜"\n'
        "    first_frame: {source: image, path: shot-01.png}\n"
        '  - prompt: "空镜"\n'
        "    first_frame: {source: none}\n"
    )
    script = load_script(write_script(workspace, text))
    driver = FakeDriver()
    run_script(script, driver, base_dir=workspace, options=zero_options())

    assert ("clear_first_frame",) in driver.calls
    # 只有锚定镜调用过 set_first_frame；空镜不得残留上一镜画面
    assert sum(1 for call in driver.calls if call[0] == "set_first_frame") == 1


# ---------------------------------------------------------------- MAD 验证


def test_mad_suspect_flagged_but_script_continues(workspace):
    image = make_solid_png(workspace, "black", "shot-01.png")
    text = (
        "name: suspect\n"
        "shots:\n"
        '  - prompt: "高漂移镜"\n'
        "    first_frame: {source: image, path: shot-01.png}\n"
        '  - prompt: "接力镜"\n'
        "    first_frame: {source: last_frame}\n"
    )
    script = load_script(write_script(workspace, text))
    # 注入高差异：输入黑图，产出红视频 → MAD 飙高
    driver = FakeDriver(behaviors={"高漂移镜": FakeShotBehavior(color="red")})
    report = run_script(script, driver, base_dir=workspace, options=zero_options())

    # suspect 不中断剧本
    assert report.completed
    assert len(report.suspects) == 1
    assert report.suspects[0].index == 1
    assert report.suspects[0].mad > 25.0

    state = read_state(workspace, "suspect")
    assert state["shots"][0]["suspect"] is True
    assert state["shots"][0]["mad"] > 25.0
    assert state["shots"][1]["status"] == "done"
    doc = json.loads(
        (shots_dir(workspace, "suspect") / "shot-01-mad.json").read_text(encoding="utf-8")
    )
    assert doc["suspect"] is True


@pytest.mark.parametrize(
    ("threshold", "expect_suspect"),
    [(5.0, True), (100.0, False)],
)
def test_mad_threshold_configurable_from_script(workspace, threshold, expect_suspect):
    image = make_solid_png(workspace, "red", "shot-01.png")
    text = (
        "name: tuned\n"
        f"defaults: {{mad_threshold: {threshold}}}\n"
        "shots:\n"
        '  - prompt: "漂移镜"\n'
        "    first_frame: {source: image, path: shot-01.png}\n"
    )
    script = load_script(write_script(workspace, text))
    # 暗红（139,0,0）对红色（约 254,0,0）：MAD 约 38
    driver = FakeDriver(behaviors={"漂移镜": FakeShotBehavior(color="darkred")})
    report = run_script(script, driver, base_dir=workspace, options=zero_options())

    assert report.completed
    state = read_state(workspace, "tuned")
    assert state["mad_threshold"] == threshold
    assert state["shots"][0]["suspect"] is expect_suspect
    assert bool(report.suspects) is expect_suspect


# ---------------------------------------------------------------- 真跑零自动重试与熔断


def test_real_mode_failure_stops_script_without_resubmit(workspace):
    text = (
        "name: realmode\n"
        "defaults: {retry: 3}\n"
        "shots:\n"
        '  - prompt: "好镜"\n'
        '  - prompt: "坏镜"\n'
        '  - prompt: "后续镜"\n'
    )
    script = load_script(write_script(workspace, text))
    driver = FakeDriver(behaviors={"坏镜": FakeShotBehavior(always_fails=True)})
    # 真实模式：retry_simulation=False，即使剧本写了 retry: 3 也绝不二次提交
    report = run_script(
        script, driver, base_dir=workspace, options=zero_options(retry_simulation=False)
    )

    assert not report.completed
    assert report.failed is not None
    assert report.failed.index == 2
    assert "生成失败" in report.failed.error
    assert report.stopped_reason is not None
    # 坏镜只提交一次：generate 共 2 次（好镜 1 + 坏镜 1），绝无第二次提交
    assert driver.calls.count(("generate",)) == 2
    assert ("set_prompt", "后续镜") not in driver.calls

    state = read_state(workspace, "realmode")
    assert [s["status"] for s in state["shots"]] == ["done", "failed", "pending"]
    assert state["shots"][1]["attempts"] == 1
    assert "生成失败" in state["shots"][1]["error"]


def test_fake_mode_retry_simulation_succeeds_on_second_attempt(workspace):
    text = (
        "name: retryok\n"
        "defaults: {retry: 1}\n"
        "shots:\n"
        '  - prompt: "首试失败镜"\n'
        '  - prompt: "普通镜"\n'
    )
    script = load_script(write_script(workspace, text))
    driver = FakeDriver(
        behaviors={"首试失败镜": FakeShotBehavior(failures_before_success=1)}
    )
    report = run_script(script, driver, base_dir=workspace, options=zero_options())

    assert report.completed
    state = read_state(workspace, "retryok")
    assert state["shots"][0]["attempts"] == 2
    assert state["shots"][0]["status"] == "done"
    assert state["shots"][1]["attempts"] == 1
    # 首试失败镜提交了 2 次，普通镜 1 次
    assert driver.calls.count(("generate",)) == 3


def test_fake_mode_retry_exhausted_trips_default_breaker(workspace):
    text = (
        "name: breaker\n"
        "defaults: {retry: 1}\n"
        "shots:\n"
        '  - prompt: "坏镜"\n'
        '  - prompt: "后续镜"\n'
    )
    script = load_script(write_script(workspace, text))
    driver = FakeDriver(behaviors={"坏镜": FakeShotBehavior(always_fails=True)})
    report = run_script(script, driver, base_dir=workspace, options=zero_options())

    assert not report.completed
    assert report.failed.index == 1
    state = read_state(workspace, "breaker")
    assert [s["status"] for s in state["shots"]] == ["failed", "pending"]
    assert state["shots"][0]["attempts"] == 2  # retry: 1 → 共 2 次尝试后失败
    assert ("set_prompt", "后续镜") not in driver.calls


def test_circuit_breaker_limit_configurable(workspace):
    text = (
        "name: breaker2\n"
        "shots:\n"
        '  - prompt: "好镜"\n'
        '  - prompt: "坏镜二"\n'
        '  - prompt: "坏镜三"\n'
        '  - prompt: "坏镜四"\n'
    )
    script = load_script(write_script(workspace, text))
    driver = FakeDriver(
        behaviors={
            "坏镜二": FakeShotBehavior(always_fails=True),
            "坏镜三": FakeShotBehavior(always_fails=True),
            "坏镜四": FakeShotBehavior(always_fails=True),
        }
    )
    report = run_script(
        script,
        driver,
        base_dir=workspace,
        options=zero_options(max_consecutive_failures=2),
    )

    assert not report.completed
    state = read_state(workspace, "breaker2")
    # 镜 2 失败（连续 1 < 上限 2）继续；镜 3 失败（连续 2 达上限）熔断；镜 4 未执行
    assert [s["status"] for s in state["shots"]] == [
        "done", "failed", "failed", "pending",
    ]
    assert ("set_prompt", "坏镜四") not in driver.calls
    assert "熔断" in report.stopped_reason


# ---------------------------------------------------------------- 断点续跑


def test_resume_skips_done_shots_and_finishes(workspace):
    text = (
        "name: resume-me\n"
        "shots:\n"
        '  - prompt: "首镜"\n'
        '  - prompt: "坏镜"\n'
        '  - prompt: "末镜"\n'
    )
    script = load_script(write_script(workspace, text))
    driver1 = FakeDriver(behaviors={"坏镜": FakeShotBehavior(always_fails=True)})
    run_script(
        script, driver1, base_dir=workspace, options=zero_options(retry_simulation=False)
    )
    state_url = read_state(workspace, "resume-me")["project_url"]

    # 修复后换新驱动续跑
    driver2 = FakeDriver()
    report2 = run_script(
        script, driver2, base_dir=workspace, options=zero_options(resume=True)
    )

    assert report2.completed
    # done 镜头的驱动方法未被调用：不重建设计、不重跑首镜
    assert ("new_project", "resume-me") not in driver2.calls
    assert ("open_project", state_url) in driver2.calls
    assert ("set_prompt", "首镜") not in driver2.calls
    assert ("set_prompt", "坏镜") in driver2.calls
    assert ("set_prompt", "末镜") in driver2.calls

    state = read_state(workspace, "resume-me")
    assert [s["status"] for s in state["shots"]] == ["done", "done", "done"]
    assert [s["attempts"] for s in state["shots"]] == [1, 2, 1]  # 尝试次数累计
    assert (shots_dir(workspace, "resume-me") / "shot-02.mp4").is_file()


def test_resume_without_state_file_raises_chinese_error(workspace):
    script = load_script(write_script(workspace, CHAIN3))
    with pytest.raises(RunStateError) as excinfo:
        run_script(
            script, FakeDriver(), base_dir=workspace, options=zero_options(resume=True)
        )
    assert "断点" in str(excinfo.value)


def test_resume_rejects_different_script_name(workspace):
    script = load_script(write_script(workspace, CHAIN3))
    run_script(script, FakeDriver(), base_dir=workspace, options=zero_options())
    other_text = 'name: another\nshots:\n  - prompt: "x"\n'
    other = load_script(write_script(workspace, other_text))
    # 把 chain3 的状态文件复制到 another 的状态位置，模拟「状态与剧本不一致」
    other_state_dir = workspace / "output" / "another"
    other_state_dir.mkdir(parents=True, exist_ok=True)
    (other_state_dir / "run-state.json").write_text(
        (workspace / "output" / "chain3" / "run-state.json").read_text(encoding="utf-8"),
        encoding="utf-8",
    )
    with pytest.raises(RunStateError) as excinfo:
        run_script(
            other, FakeDriver(), base_dir=workspace, options=zero_options(resume=True)
        )
    assert "不一致" in str(excinfo.value)


def test_resume_rejects_changed_shot_count(workspace):
    script = load_script(write_script(workspace, CHAIN3))
    run_script(script, FakeDriver(), base_dir=workspace, options=zero_options())
    mutated = load_script(
        write_script(workspace, 'name: chain3\nshots:\n  - prompt: "x"\n')
    )
    with pytest.raises(RunStateError) as excinfo:
        run_script(
            mutated, FakeDriver(), base_dir=workspace, options=zero_options(resume=True)
        )
    assert "镜头数" in str(excinfo.value)


# ---------------------------------------------------------------- 节奏注入


def test_zero_delay_options_produce_zero_sleeps(workspace):
    sleeps: list[float] = []
    script = load_script(write_script(workspace, CHAIN3))
    run_script(
        script,
        FakeDriver(),
        base_dir=workspace,
        options=zero_options(sleep=sleeps.append),
    )
    assert sleeps
    assert all(seconds == 0.0 for seconds in sleeps)


def test_default_pacing_ranges_action_and_cooldown(workspace):
    sleeps: list[float] = []
    text = 'name: pacing\nshots:\n  - prompt: "a"\n  - prompt: "b"\n'
    script = load_script(write_script(workspace, text))
    run_script(
        script,
        FakeDriver(),
        base_dir=workspace,
        options=RunOptions(sleep=sleeps.append),  # 默认区间：动作 3-8s / 冷却 30-60s
    )
    assert sleeps
    action = [s for s in sleeps if 3.0 <= s <= 8.0]
    cooldown = [s for s in sleeps if 30.0 <= s <= 60.0]
    assert action, "动作延时应落在 3-8 秒区间"
    assert cooldown, "镜头冷却应落在 30-60 秒区间"
    assert all(
        (3.0 <= s <= 8.0) or (30.0 <= s <= 60.0) for s in sleeps
    ), f"存在越界延时：{sleeps}"


# ---------------------------------------------------------------- 单镜头参数覆盖


def test_shot_level_overrides_passed_to_driver(workspace):
    text = (
        "name: overrides\n"
        "defaults: {model: omni-1.1-flash, duration: 8}\n"
        "shots:\n"
        '  - prompt: "默认镜"\n'
        '  - prompt: "覆盖镜"\n'
        "    model: veo-3.1-fast\n"
        "    duration: 4\n"
        '    aspect: "9:16"\n'
        "    outputs: 2\n"
    )
    script = load_script(write_script(workspace, text))
    driver = FakeDriver()
    report = run_script(script, driver, base_dir=workspace, options=zero_options())

    assert report.completed
    assert ("configure", "omni-1.1-flash", "8", "16:9", "1") in driver.calls
    assert ("configure", "veo-3.1-fast", "4", "9:16", "2") in driver.calls
    state = read_state(workspace, "overrides")
    assert state["shots"][0]["params"] == {
        "model": "omni-1.1-flash", "duration": 8, "aspect": "16:9", "outputs": 1,
    }
    assert state["shots"][1]["params"] == {
        "model": "veo-3.1-fast", "duration": 4, "aspect": "9:16", "outputs": 2,
    }


# ---------------------------------------------------------------- 完整性与输入异常


def test_garbage_download_rejected_and_never_archived(workspace):
    text = (
        "name: garbage\n"
        "defaults: {retry: 0}\n"
        "shots:\n"
        '  - prompt: "伪视频镜"\n'
        '  - prompt: "后续镜"\n'
    )
    script = load_script(write_script(workspace, text))
    driver = FakeDriver(
        behaviors={"伪视频镜": FakeShotBehavior(download_garbage=True)}
    )
    report = run_script(script, driver, base_dir=workspace, options=zero_options())

    assert not report.completed
    assert report.failed.index == 1
    assert "损坏" in report.failed.error or "不是有效视频" in report.failed.error
    directory = shots_dir(workspace, "garbage")
    # 伪视频不得进入链条：没有 shot-01.mp4，14 字节文件也不得残留
    assert not (directory / "shot-01.mp4").exists()
    assert not any(f.stat().st_size == 14 for f in directory.glob("*.mp4"))
    state = read_state(workspace, "garbage")
    assert state["shots"][0]["status"] == "failed"
    assert state["shots"][1]["status"] == "pending"


def test_first_shot_with_last_frame_source_fails_chinese(workspace):
    text = (
        "name: orphan\n"
        "shots:\n"
        '  - prompt: "无源镜"\n'
        "    first_frame: {source: last_frame}\n"
    )
    script = load_script(write_script(workspace, text))
    report = run_script(
        script,
        FakeDriver(),
        base_dir=workspace,
        options=zero_options(retry_simulation=False),
    )
    assert not report.completed
    assert report.failed.index == 1
    assert "上一镜头" in report.failed.error
    state = read_state(workspace, "orphan")
    assert state["shots"][0]["status"] == "failed"
    assert state["shots"][0]["attempts"] == 0  # 未提交过生成，不计尝试


def test_missing_image_at_runtime_fails_shot(workspace):
    image = make_solid_png(workspace, "red", "shot-01.png")
    text = (
        "name: ghost\n"
        "shots:\n"
        '  - prompt: "锚定镜"\n'
        "    first_frame: {source: image, path: shot-01.png}\n"
    )
    script = load_script(write_script(workspace, text))
    image.unlink()  # 校验之后、运行之前图片被删
    driver = FakeDriver()
    report = run_script(
        script,
        driver,
        base_dir=workspace,
        options=zero_options(retry_simulation=False),
    )
    assert not report.completed
    assert "不存在" in report.failed.error
    assert ("generate",) not in driver.calls  # 未提交过任何生成


# ---------------------------------------------------------------- 失败在 fake 重试模式下不重试输入解析错误


def test_input_resolution_failure_not_retried_even_in_fake_mode(workspace):
    text = (
        "name: orphan-fake\n"
        "defaults: {retry: 2}\n"
        "shots:\n"
        '  - prompt: "无源镜"\n'
        "    first_frame: {source: last_frame}\n"
    )
    script = load_script(write_script(workspace, text))
    report = run_script(
        script, FakeDriver(), base_dir=workspace, options=zero_options()
    )
    assert not report.completed
    state = read_state(workspace, "orphan-fake")
    assert state["shots"][0]["attempts"] == 0  # 缺输入不构成一次提交，绝不重试
