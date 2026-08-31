"""BbBrowserDriver 的测试（工单 04）——全程 stub，绝不真实调用 bb-browser 二进制。

本文件覆盖 `_run_cli` 收敛 seam：命令拼装、JSON 解析、非零退出、超时、
daemon 未启动自恢复、输出脱敏。页面方法测试同文件后半部分。
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

from styleforge.bb_driver import BbBrowserDriver
from styleforge.driver import DriverError


class Always:
    """标记：该项在脚本队列里反复出现（轮询场景每次调用返回同一响应）。"""

    def __init__(self, item) -> None:
        self.item = item


class FakeCli:
    """可编排响应序列的 bb-browser 命令替身。

    script 里每项按调用顺序弹出：
    - (code, stdout, stderr) 元组 → 直接返回
    - Exception 实例 → 抛出（模拟 subprocess 系统级失败）
    - Always 包装 → 返回其内容且不出队（轮询场景）
    - 可调用对象 → 接收 args 返回上述项
    calls 记录每次收到的 args 列表，供命令拼装断言。
    """

    def __init__(self, *script) -> None:
        self.script = list(script)
        self.calls: list[list[str]] = []

    def __call__(self, args, timeout):
        self.calls.append([str(a) for a in args])
        if not self.script:
            raise AssertionError("多余的 CLI 调用：" + " ".join(self.calls[-1]))
        item = self.script.pop(0)
        if callable(item):
            item = item(args)
        if isinstance(item, Always):
            self.script.insert(0, item)
            item = item.item
        if isinstance(item, Exception):
            raise item
        return item


class FakeClock:
    """可手动推进的时钟；与 sleep 联动实现确定性轮询测试。"""

    def __init__(self) -> None:
        self.now = 0.0

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


def result_json(**fields) -> str:
    return json.dumps({"result": fields})


def eval_json(value) -> str:
    """bb-browser eval 的响应：result.result 为 JS JSON.stringify 的字符串。"""
    return json.dumps({"result": {"result": json.dumps(value)}})


def ref_info(role: str = "button", name: str = "") -> dict:
    return {"role": role, "name": name}


def snap_response(**refs) -> tuple:
    payload = {"result": {"snapshotData": {"snapshot": "", "refs": refs}}}
    return (0, json.dumps(payload), "")


def make_driver(cli: FakeCli | None = None, **kwargs) -> BbBrowserDriver:
    """零等待、时钟确定性的驱动实例；sleep 推进注入的 FakeClock（driver._clock）。"""
    clock = kwargs.pop("clock", None) or FakeClock()
    return BbBrowserDriver(
        cli_runner=cli,
        clock=clock,
        sleep=clock.advance,
        rng=lambda low, high: (low + high) / 2,
        **kwargs,
    )


# ---------------------------------------------------------------- 命令拼装


def test_default_runner_assembles_command_with_binary(monkeypatch):
    """默认执行器把 binary 放在 argv 首位，全部参数字符串化。"""
    captured: dict = {}

    def fake_run(argv, **kwargs):
        captured["argv"] = list(argv)
        return subprocess.CompletedProcess(argv, 0, stdout='{"result": {}}', stderr="")

    monkeypatch.setattr("styleforge.bb_driver.subprocess.run", fake_run)
    driver = BbBrowserDriver(binary="bb-browser")
    payload = driver._run_cli("get", "url", "--tab", "a020", "--json")
    assert payload == {}
    assert captured["argv"] == ["bb-browser", "get", "url", "--tab", "a020", "--json"]


def test_run_cli_passes_timeout_to_runner():
    seen_timeouts: list[float] = []

    def runner(args, timeout):
        seen_timeouts.append(timeout)
        return 0, result_json(tab="a020"), ""

    driver = BbBrowserDriver(cli_runner=runner, cli_timeout=12.5)
    driver._run_cli("snap", "-i", "--tab", "a020", "--json")
    assert seen_timeouts == [12.5]


# ---------------------------------------------------------------- JSON 解析与错误


def test_run_cli_parses_result_envelope():
    cli = FakeCli((0, '{"result": {"tab": "a020", "url": "https://x"}}', ""))
    driver = make_driver(cli)
    payload = driver._run_cli("open", "https://flow.google", "--json")
    assert payload == {"tab": "a020", "url": "https://x"}
    assert cli.calls == [["open", "https://flow.google", "--json"]]


def test_run_cli_result_missing_returns_empty_dict():
    cli = FakeCli((0, '{"result": null}', ""))
    driver = make_driver(cli)
    assert driver._run_cli("close", "--tab", "a020", "--json") == {}


def test_run_cli_nonzero_exit_raises_chinese_error():
    cli = FakeCli((3, "", "boom: element detached"))
    driver = make_driver(cli)
    with pytest.raises(DriverError) as excinfo:
        driver._run_cli("click", "@r1", "--tab", "a020", "--json")
    message = str(excinfo.value)
    assert "click" in message
    assert "退出码 3" in message
    assert "boom" in message


def test_run_cli_rejects_invalid_json():
    cli = FakeCli((0, "not json at all", ""))
    driver = make_driver(cli)
    with pytest.raises(DriverError) as excinfo:
        driver._run_cli("snap", "-i", "--json")
    assert "JSON" in str(excinfo.value)


def test_run_cli_rejects_empty_output():
    cli = FakeCli((0, "", ""))
    driver = make_driver(cli)
    with pytest.raises(DriverError) as excinfo:
        driver._run_cli("snap", "-i", "--json")
    assert "无输出" in str(excinfo.value)


def test_run_cli_error_envelope_raises_with_message_and_hint():
    cli = FakeCli((0, '{"error": {"message": "Element not found", "hint": "run snap first"}}', ""))
    driver = make_driver(cli)
    with pytest.raises(DriverError) as excinfo:
        driver._run_cli("click", "@r9", "--tab", "a020", "--json")
    message = str(excinfo.value)
    assert "Element not found" in message
    assert "run snap first" in message


def test_run_cli_timeout_raises_chinese_error():
    cli = FakeCli(subprocess.TimeoutExpired(cmd="bb-browser snap", timeout=30))
    driver = make_driver(cli)
    with pytest.raises(DriverError) as excinfo:
        driver._run_cli("snap", "-i", "--tab", "a020", "--json")
    assert "超时" in str(excinfo.value)


def test_run_cli_missing_binary_gives_install_guidance():
    cli = FakeCli(FileNotFoundError(2, "No such file or directory"))
    driver = make_driver(cli)
    with pytest.raises(DriverError) as excinfo:
        driver._run_cli("open", "https://flow.google", "--json")
    message = str(excinfo.value)
    assert "未找到 bb-browser" in message
    assert "npm install -g bb-browser" in message


# ---------------------------------------------------------------- 脱敏（零凭据落盘）


def test_run_cli_sanitizes_secrets_in_error_output():
    cli = FakeCli(
        (
            1,
            "",
            "Authorization: Bearer ya29.super-secret cookie: SID=xyz; user@a.com got error",
        )
    )
    driver = make_driver(cli)
    with pytest.raises(DriverError) as excinfo:
        driver._run_cli("open", "https://flow.google", "--json")
    message = str(excinfo.value)
    assert "ya29.super-secret" not in message
    assert "SID=xyz" not in message
    assert "user@a.com" not in message
    assert "[已脱敏]" in message
    assert "[已脱敏邮箱]" in message


def test_run_cli_sanitizes_secrets_in_error_envelope():
    cli = FakeCli((0, '{"error": {"message": "cookie: SID=leaked value", "hint": ""}}', ""))
    driver = make_driver(cli)
    with pytest.raises(DriverError) as excinfo:
        driver._run_cli("snap", "-i", "--json")
    assert "SID=leaked" not in str(excinfo.value)


def test_sanitize_truncates_long_output():
    driver = make_driver(FakeCli())
    text = "x" * 5000
    cleaned = driver._sanitize(text)
    assert len(cleaned) < 300
    assert "已截断" in cleaned


# ---------------------------------------------------------------- daemon 自恢复


def test_daemon_down_then_start_then_retry_succeeds():
    cli = FakeCli(
        (1, "", "Error: connect ECONNREFUSED 127.0.0.1:19824 - daemon not running"),
        (0, result_json(), ""),  # daemon start
        (0, result_json(tab="a020"), ""),  # 原命令重试
    )
    driver = make_driver(cli)
    payload = driver._run_cli("open", "https://flow.google", "--json")
    assert payload == {"tab": "a020"}
    assert cli.calls == [
        ["open", "https://flow.google", "--json"],
        ["daemon", "start", "--json"],
        ["open", "https://flow.google", "--json"],
    ]


def test_daemon_start_failure_raises():
    cli = FakeCli(
        (1, "", "daemon not running"),
        (1, "", "cannot start daemon: port busy"),
    )
    driver = make_driver(cli)
    with pytest.raises(DriverError) as excinfo:
        driver._run_cli("open", "https://flow.google", "--json")
    assert "daemon" in str(excinfo.value)


def test_daemon_recovery_only_once_then_raises():
    cli = FakeCli(
        (1, "", "daemon not running"),
        (0, result_json(), ""),
        (1, "", "daemon not running again"),  # 重试仍失败 → 直接报错，不再循环恢复
    )
    driver = make_driver(cli)
    with pytest.raises(DriverError):
        driver._run_cli("open", "https://flow.google", "--json")
    assert len(cli.calls) == 3


def test_nonzero_exit_without_daemon_signature_does_not_restart_daemon():
    cli = FakeCli((1, "", "element not found"),)
    driver = make_driver(cli)
    with pytest.raises(DriverError):
        driver._run_cli("click", "@r1", "--tab", "a020", "--json")
    assert cli.calls == [["click", "@r1", "--tab", "a020", "--json"]]


# ---------------------------------------------------------------- 页面方法：new_project / open_project

FLOW_HOME = "https://flow.google"


def test_new_project_command_sequence_and_returns_project_url():
    cli = FakeCli(
        (0, result_json(tab="a020", url="https://labs.google/fx/tools/flow"), ""),
        snap_response(r1=ref_info("button", "New project")),
        (0, result_json(), ""),  # click New project
        (0, result_json(url="https://labs.google/fx/tools/flow"), ""),  # url 未进画布
        (0, result_json(url="https://labs.google/fx/tools/flow/project/abc-123"), ""),
    )
    driver = make_driver(cli)
    url = driver.new_project("我的剧本")
    assert url == "https://labs.google/fx/tools/flow/project/abc-123"
    assert driver._tab == "a020"
    assert cli.calls[0] == ["open", FLOW_HOME, "--json"]
    assert cli.calls[1] == ["snap", "-i", "--tab", "a020", "--json"]
    assert cli.calls[2] == ["click", "@r1", "--tab", "a020", "--json"]
    assert cli.calls[3] == ["get", "url", "--tab", "a020", "--json"]


def test_new_project_open_without_tab_id_raises():
    cli = FakeCli((0, result_json(url=FLOW_HOME), ""))
    driver = make_driver(cli)
    with pytest.raises(DriverError) as excinfo:
        driver.new_project("我的剧本")
    assert "标签页" in str(excinfo.value)


def test_new_project_click_not_found_times_out():
    cli = FakeCli(
        (0, result_json(tab="a020"), ""),
        Always(snap_response(r9=ref_info("button", "无关按钮"))),
    )
    driver = make_driver(cli, page_ready_timeout=3.0)
    with pytest.raises(Exception) as excinfo:
        driver.new_project("我的剧本")
    assert "超时" in str(excinfo.value)


def test_open_project_without_tab_opens_new_tab():
    cli = FakeCli((0, result_json(tab="b021"), ""))
    driver = make_driver(cli)
    driver.open_project("https://labs.google/fx/tools/flow/project/abc")
    assert driver._tab == "b021"
    assert cli.calls == [
        ["open", "https://labs.google/fx/tools/flow/project/abc", "--json"]
    ]


def test_open_project_reuses_existing_tab_with_goto():
    cli = FakeCli((0, result_json(), ""))
    driver = make_driver(cli)
    driver._tab = "a020"
    driver.open_project("https://labs.google/fx/tools/flow/project/abc")
    assert cli.calls == [
        ["goto", "https://labs.google/fx/tools/flow/project/abc", "--tab", "a020", "--json"]
    ]


# ---------------------------------------------------------------- 页面方法：set_prompt


def test_set_prompt_uses_native_fill():
    cli = FakeCli(
        snap_response(r7=ref_info("textbox", "What do you want to create?")),
        (0, result_json(), ""),
    )
    driver = make_driver(cli)
    driver._tab = "a020"
    driver.set_prompt("夜色中的站台")
    assert cli.calls == [
        ["snap", "-i", "--tab", "a020", "--json"],
        ["fill", "@r7", "夜色中的站台", "--tab", "a020", "--json"],
    ]


def test_set_prompt_missing_box_names_locator_key():
    cli = FakeCli(Always(snap_response(r1=ref_info("button", "Create"))))
    driver = make_driver(cli)
    driver._tab = "a020"
    with pytest.raises(DriverError) as excinfo:
        driver.set_prompt("夜色中的站台")
    assert "prompt_box" in str(excinfo.value)


# ---------------------------------------------------------------- 页面方法：configure


def test_configure_clicks_all_options_then_escape():
    ok = (0, result_json(), "")
    cli = FakeCli(
        snap_response(r1=ref_info("button", "Video · 720p · 8s crop_16_9 x1")),
        ok,
        snap_response(r2=ref_info("menuitemradio", "Omni 1.1 Flash")),
        ok,
        snap_response(r3=ref_info("menuitemradio", "8s")),
        ok,
        snap_response(r4=ref_info("menuitemradio", "16:9")),
        ok,
        snap_response(r5=ref_info("menuitemradio", "x1")),
        ok,
        (0, result_json(), ""),  # Escape
    )
    driver = make_driver(cli)
    driver._tab = "a020"
    driver.configure("omni-1.1-flash", 8, "16:9", 1)
    assert cli.calls == [
        ["snap", "-i", "--tab", "a020", "--json"],
        ["click", "@r1", "--tab", "a020", "--json"],
        ["snap", "-i", "--tab", "a020", "--json"],
        ["click", "@r2", "--tab", "a020", "--json"],
        ["snap", "-i", "--tab", "a020", "--json"],
        ["click", "@r3", "--tab", "a020", "--json"],
        ["snap", "-i", "--tab", "a020", "--json"],
        ["click", "@r4", "--tab", "a020", "--json"],
        ["snap", "-i", "--tab", "a020", "--json"],
        ["click", "@r5", "--tab", "a020", "--json"],
        ["press", "Escape", "--tab", "a020", "--json"],
    ]


def test_configure_unknown_model_display_name_raises_before_any_click():
    cli = FakeCli()
    driver = make_driver(cli)
    driver._tab = "a020"
    with pytest.raises(DriverError) as excinfo:
        driver.configure("mystery-model", 8, "16:9", 1)
    assert "mystery-model" in str(excinfo.value)
    assert cli.calls == []


def test_configure_unknown_aspect_raises_before_any_click():
    cli = FakeCli()
    driver = make_driver(cli)
    driver._tab = "a020"
    with pytest.raises(DriverError) as excinfo:
        driver.configure("omni-1.1-flash", 8, "4:5", 1)
    assert "4:5" in str(excinfo.value)
    assert cli.calls == []


def test_configure_missing_option_names_locator_key():
    cli = FakeCli(
        snap_response(r1=ref_info("button", "Video · 720p · 8s crop_16_9 x1")),
        Always(snap_response(r9=ref_info("menuitemradio", "别的选项"))),
    )
    driver = make_driver(cli, cli_timeout=5.0)
    driver._tab = "a020"
    with pytest.raises(DriverError) as excinfo:
        driver.configure("omni-1.1-flash", 8, "16:9", 1)
    assert "params_model_option" in str(excinfo.value)


# ---------------------------------------------------------------- 页面方法：generate / clear_first_frame


def test_generate_records_media_baseline_then_clicks_create():
    cli = FakeCli(
        (0, eval_json(["m1", "m2"]), ""),
        snap_response(r3=ref_info("button", "Create")),
        (0, result_json(), ""),
    )
    driver = make_driver(cli)
    driver._tab = "a020"
    driver.generate()
    assert driver._media_baseline == {"m1", "m2"}
    assert cli.calls[0][0] == "eval"
    assert cli.calls[1] == ["snap", "-i", "--tab", "a020", "--json"]
    assert cli.calls[2] == ["click", "@r3", "--tab", "a020", "--json"]


def test_generate_without_create_button_raises():
    cli = FakeCli((0, eval_json([]), ""), Always(snap_response(r1=ref_info("textbox", "x"))))
    driver = make_driver(cli)
    driver._tab = "a020"
    with pytest.raises(DriverError) as excinfo:
        driver.generate()
    assert "create_button" in str(excinfo.value)


def test_clear_first_frame_clicks_remove_and_verifies_empty():
    cli = FakeCli(
        snap_response(r2=ref_info("button", "Remove start frame")),
        (0, result_json(), ""),  # click remove
        (0, eval_json(None), ""),  # 槽已空
    )
    driver = make_driver(cli)
    driver._tab = "a020"
    driver.clear_first_frame()
    assert cli.calls[1] == ["click", "@r2", "--tab", "a020", "--json"]


def test_clear_first_frame_missing_remove_button_raises():
    cli = FakeCli(Always(snap_response(r1=ref_info("button", "Start"))))
    driver = make_driver(cli)
    driver._tab = "a020"
    with pytest.raises(DriverError) as excinfo:
        driver.clear_first_frame()
    assert "start_slot_remove" in str(excinfo.value)


# ---------------------------------------------------------------- set_first_frame（原型上传配方）

from conftest import make_solid_video  # noqa: E402


def uploads_snap(slot_ref="r1", uploads_ref="r2", add_ref="r3"):
    """Start 槽 / Uploads 标签 / Add to Prompt 同时可见的快照响应。"""
    return snap_response(
        **{
            slot_ref: ref_info("button", "Start"),
            uploads_ref: ref_info("tab", "drive_folder_uploadUploads"),
            add_ref: ref_info("button", "Add to Prompt"),
        }
    )


def test_set_first_frame_full_recipe(tmp_path):
    """注入前记网格基线 → 只发 change 注入 → diff 新素材 → 点选 → Add to Prompt → 验证落位。"""
    image = tmp_path / "frame.png"
    image.write_bytes(b"\x89PNG fake-bytes")

    cli = FakeCli(
        uploads_snap(),  # 1. snap：Start 槽
        (0, result_json(), ""),  # 2. click Start 槽（对话框弹出）
        uploads_snap(),  # 3. snap：Uploads 标签
        (0, result_json(), ""),  # 4. click Uploads 标签
        (0, eval_json(["m1"]), ""),  # 5. eval：网格基线
        (0, eval_json("injected"), ""),  # 6. eval：DataTransfer 注入
        (0, eval_json(["m1", "m2"]), ""),  # 7. eval：diff 出新素材 m2
        (0, eval_json("clicked"), ""),  # 8. eval：点选 m2
        uploads_snap(),  # 9. snap：Add to Prompt
        (0, result_json(), ""),  # 10. click Add to Prompt
        (0, eval_json("m2"), ""),  # 11. eval：Start 槽落位验证
    )
    driver = make_driver(cli)
    driver._tab = "a020"
    driver.set_first_frame(image)

    kinds = [call[0] for call in cli.calls]
    assert kinds == [
        "snap", "click", "snap", "click", "eval", "eval", "eval", "eval",
        "snap", "click", "eval",
    ]
    # 注入 eval：含 base64 与文件名，且只派发 change（原型教训）
    inject_call = cli.calls[5]
    assert "frame.png" in inject_call[1]
    assert inject_call[1].count("dispatchEvent") == 1
    # 点选 eval：按媒体 UUID 定位新素材
    assert '"m2"' in cli.calls[7][1]
    # 最后一步是 Start 槽验证
    assert "Start" in cli.calls[-1][1]


def test_set_first_frame_missing_image_raises_before_cli():
    cli = FakeCli()
    driver = make_driver(cli)
    driver._tab = "a020"
    with pytest.raises(DriverError) as excinfo:
        driver.set_first_frame(tmp_path_or_absolute())
    assert "不存在" in str(excinfo.value)
    assert cli.calls == []


def tmp_path_or_absolute() -> str:
    return "Z:/nonexistent/frame.png"


def test_set_first_frame_inject_reports_no_input(tmp_path):
    image = tmp_path / "frame.png"
    image.write_bytes(b"x")
    cli = FakeCli(
        uploads_snap(),
        (0, result_json(), ""),
        uploads_snap(),
        (0, result_json(), ""),
        (0, eval_json([]), ""),
        (0, eval_json("no-input"), ""),
    )
    driver = make_driver(cli)
    driver._tab = "a020"
    with pytest.raises(DriverError) as excinfo:
        driver.set_first_frame(image)
    assert "input" in str(excinfo.value)


def test_set_first_frame_upload_timeout(tmp_path):
    image = tmp_path / "frame.png"
    image.write_bytes(b"x")
    cli = FakeCli(
        uploads_snap(),
        (0, result_json(), ""),
        uploads_snap(),
        (0, result_json(), ""),
        (0, eval_json(["m1"]), ""),
        (0, eval_json("injected"), ""),
        Always((0, eval_json(["m1"]), "")),  # 新素材迟迟不出现
    )
    driver = make_driver(cli, upload_wait_timeout=3.0)
    driver._tab = "a020"
    with pytest.raises(Exception) as excinfo:
        driver.set_first_frame(image)
    assert "超时" in str(excinfo.value)


def test_set_first_frame_add_to_prompt_then_slot_missing_media(tmp_path):
    """Add to Prompt 后 Start 槽验证超时也要报错（不静默放行）。"""
    image = tmp_path / "frame.png"
    image.write_bytes(b"x")
    cli = FakeCli(
        uploads_snap(),
        (0, result_json(), ""),
        uploads_snap(),
        (0, result_json(), ""),
        (0, eval_json([]), ""),
        (0, eval_json("injected"), ""),
        (0, eval_json(["m9"]), ""),
        (0, eval_json("clicked"), ""),
        uploads_snap(),
        (0, result_json(), ""),
        Always((0, eval_json(None), "")),  # 槽内一直没有图
    )
    driver = make_driver(cli, upload_wait_timeout=3.0)
    driver._tab = "a020"
    with pytest.raises(Exception) as excinfo:
        driver.set_first_frame(image)
    assert "Start" in str(excinfo.value)


# ---------------------------------------------------------------- wait_for_completion（媒体 UUID diff）


def test_wait_for_completion_diffs_new_media_uuid():
    cli = FakeCli((0, eval_json(["old-1", "old-2"]), ""))
    driver = make_driver(cli)
    driver._tab = "a020"
    driver._media_baseline = {"old-1"}
    clip = driver.wait_for_completion(60.0)
    assert clip.clip_id == "old-2"


def test_wait_for_completion_takes_first_new_variant():
    cli = FakeCli((0, eval_json(["old-1", "new-a", "new-b"]), ""))
    driver = make_driver(cli)
    driver._tab = "a020"
    driver._media_baseline = {"old-1"}
    clip = driver.wait_for_completion(60.0)
    assert clip.clip_id == "new-a"  # 首个成功变体（DOM 顺序第一个新增）


def test_wait_for_completion_timeout_raises_driver_timeout():
    from styleforge.driver import DriverTimeoutError

    cli = FakeCli(Always((0, eval_json(["old-1"]), "")))
    driver = make_driver(cli, poll_interval=(0.5, 1.0))
    driver._tab = "a020"
    driver._media_baseline = {"old-1"}
    with pytest.raises(DriverTimeoutError) as excinfo:
        driver.wait_for_completion(2.0)
    assert "超时" in str(excinfo.value)


def test_wait_for_completion_without_baseline_records_defensively():
    """未经 generate 直接等待（异常使用）也不崩溃：先记基线再 diff。"""
    cli = FakeCli(
        (0, eval_json(["m1"]), ""),  # 防御性基线
        (0, eval_json(["m1", "m2"]), ""),  # 轮询
    )
    driver = make_driver(cli)
    driver._tab = "a020"
    clip = driver.wait_for_completion(60.0)
    assert clip.clip_id == "m2"


# ---------------------------------------------------------------- download_clip（落稳 + 完整性校验）


def test_download_clip_happy_path(tmp_path):
    download_dir = tmp_path / "downloads"
    download_dir.mkdir()
    (download_dir / "old.mp4").write_bytes(b"old")  # 历史文件必须被忽略
    dest_dir = tmp_path / "shots"
    dest_dir.mkdir()

    def snap_with_new_video(args):
        make_solid_video(download_dir, "red", name="flow-video.mp4")
        return snap_response(r2=ref_info("menuitem", "Original 720p"))

    cli = FakeCli(
        snap_response(r1=ref_info("button", "file_download")),
        (0, result_json(), ""),  # click 下载图标
        snap_with_new_video,  # snap：下载菜单（写新文件模拟下载开始）
        (0, result_json(), ""),  # click Original
    )
    driver = make_driver(
        cli, download_dir=download_dir, download_stable_interval=0.0, min_clip_bytes=1024
    )
    driver._tab = "a020"
    out = driver.download_clip(dest_dir)
    assert out.name == "clip-01.mp4"
    assert out.is_file()
    assert out.read_bytes() != b"old"
    assert not (download_dir / "flow-video.mp4").exists()  # 已移走
    kinds = [call[0] for call in cli.calls]
    assert kinds == ["snap", "click", "snap", "click"]


def test_download_clip_rejects_tiny_pseudo_video(tmp_path):
    """14 字节 'No session found' 伪视频在体积下限即拒收（樱之诗取证实坑）。"""
    download_dir = tmp_path / "downloads"
    download_dir.mkdir()

    def snap_then_dump(args):
        (download_dir / "flow-video.mp4").write_bytes(b"No session fou")
        return snap_response(r2=ref_info("menuitem", "Original 720p"))

    cli = FakeCli(
        snap_response(r1=ref_info("button", "file_download")),
        (0, result_json(), ""),
        snap_then_dump,
        (0, result_json(), ""),
    )
    driver = make_driver(cli, download_dir=download_dir, download_stable_interval=0.0)
    driver._tab = "a020"
    with pytest.raises(DriverError) as excinfo:
        driver.download_clip(tmp_path / "shots")
    message = str(excinfo.value)
    assert "伪视频" in message or "下限" in message
    assert not (download_dir / "flow-video.mp4").exists()  # 拒收后删除，零残留


def test_download_clip_rejects_large_garbage_via_ffprobe(tmp_path):
    """体积过下限但 ffprobe 解析不出的大文件同样拒收并删除。"""
    download_dir = tmp_path / "downloads"
    download_dir.mkdir()

    def snap_then_dump(args):
        (download_dir / "flow-video.mp4").write_bytes(b"No session found. " * 20000)
        return snap_response(r2=ref_info("menuitem", "Original 720p"))

    cli = FakeCli(
        snap_response(r1=ref_info("button", "file_download")),
        (0, result_json(), ""),
        snap_then_dump,
        (0, result_json(), ""),
    )
    driver = make_driver(cli, download_dir=download_dir, download_stable_interval=0.0)
    driver._tab = "a020"
    with pytest.raises(DriverError) as excinfo:
        driver.download_clip(tmp_path / "shots")
    assert "伪视频" in str(excinfo.value)
    assert not (download_dir / "flow-video.mp4").exists()


def test_download_clip_timeout_when_no_file_lands(tmp_path):
    download_dir = tmp_path / "downloads"
    download_dir.mkdir()
    cli = FakeCli(
        snap_response(r1=ref_info("button", "file_download")),
        (0, result_json(), ""),
        snap_response(r2=ref_info("menuitem", "Original 720p")),
        (0, result_json(), ""),
    )
    driver = make_driver(cli, download_dir=download_dir, download_timeout=2.0)
    driver._tab = "a020"
    with pytest.raises(Exception) as excinfo:
        driver.download_clip(tmp_path / "shots")
    assert "超时" in str(excinfo.value)


def test_download_clip_ignores_partial_crdownload(tmp_path):
    """Chrome 下载中的 .crdownload 文件不算落稳，继续等待。"""
    download_dir = tmp_path / "downloads"
    download_dir.mkdir()
    (download_dir / "flow-video.crdownload").write_bytes(b"partial")
    cli = FakeCli(
        snap_response(r1=ref_info("button", "file_download")),
        (0, result_json(), ""),
        snap_response(r2=ref_info("menuitem", "Original 720p")),
        (0, result_json(), ""),
    )
    driver = make_driver(cli, download_dir=download_dir, download_timeout=2.0)
    driver._tab = "a020"
    with pytest.raises(Exception) as excinfo:
        driver.download_clip(tmp_path / "shots")
    assert "超时" in str(excinfo.value)


def test_download_clip_missing_download_dir_raises(tmp_path):
    cli = FakeCli()
    driver = make_driver(cli, download_dir=tmp_path / "no-such-dir")
    driver._tab = "a020"
    with pytest.raises(DriverError) as excinfo:
        driver.download_clip(tmp_path / "shots")
    assert "下载目录" in str(excinfo.value)
    assert cli.calls == []


def test_download_dir_env_var_override(tmp_path, monkeypatch):
    """STYLEFORGE_DOWNLOAD_DIR 覆盖默认下载目录（构造参数优先级最高）。"""
    monkeypatch.setenv("STYLEFORGE_DOWNLOAD_DIR", str(tmp_path / "env-dl"))
    driver = BbBrowserDriver(cli_runner=FakeCli())
    assert driver._download_dir == tmp_path / "env-dl"
    explicit = BbBrowserDriver(cli_runner=FakeCli(), download_dir=tmp_path / "explicit")
    assert explicit._download_dir == tmp_path / "explicit"
    monkeypatch.delenv("STYLEFORGE_DOWNLOAD_DIR")
    driver = BbBrowserDriver(cli_runner=FakeCli())
    assert driver._download_dir == Path.home() / "Downloads"
