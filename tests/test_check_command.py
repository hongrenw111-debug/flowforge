"""`flowforge check` 的 CLI 级行为测试（离线，不碰网络、不碰网页）。

只断言外部行为：退出码与中文输出内容，不窥探内部实现。
"""

from __future__ import annotations

from pathlib import Path

from typer.testing import CliRunner

from flowforge.cli import app

runner = CliRunner()


def write_script(tmp_path: Path, text: str, images: tuple[str, ...] = ()) -> Path:
    """把剧本文本写入临时目录；可选创建真实存在的首帧图片文件；返回剧本路径。"""
    script_path = tmp_path / "script.yaml"
    script_path.write_text(text, encoding="utf-8")
    for rel_path in images:
        image = tmp_path / rel_path
        image.parent.mkdir(parents=True, exist_ok=True)
        image.write_bytes(b"\x89PNG\r\n\x1a\nfake-image-bytes")
    return script_path


VALID_SCRIPT = """\
name: my-story
defaults:
  model: omni-1.1-flash
  duration: 8
  aspect: "16:9"
  outputs: 1
  download: original-720p
  retry: 1
shots:
  - prompt: "开场：雨夜霓虹街头"
    first_frame: {source: image, path: frames/shot-01.png}
  - prompt: "追逐：穿过夜市"
    first_frame: {source: last_frame}
  - prompt: "结局：天台日出"
"""


# ---------------------------------------------------------------- 合法剧本


def test_help_lists_check_command():
    result = runner.invoke(app, ["--help"])
    assert result.exit_code == 0
    assert "check" in result.output


def test_check_without_path_exits_1_in_chinese():
    result = runner.invoke(app, ["check"])
    assert result.exit_code == 1
    assert "缺少剧本文件路径" in result.output


def test_missing_script_file_exits_1(tmp_path):
    result = runner.invoke(app, ["check", str(tmp_path / "nope.yaml")])
    assert result.exit_code == 1
    assert "剧本文件不存在" in result.output


def test_directory_as_script_path_exits_1(tmp_path):
    result = runner.invoke(app, ["check", str(tmp_path)])
    assert result.exit_code == 1
    assert "不是文件" in result.output


def test_valid_script_prints_ok_summary(tmp_path):
    path = write_script(tmp_path, VALID_SCRIPT, images=("frames/shot-01.png",))
    result = runner.invoke(app, ["check", str(path)])
    assert result.exit_code == 0, result.output
    assert "剧本校验通过：my-story" in result.output
    assert "镜头数：3" in result.output
    assert "模型 omni-1.1-flash" in result.output
    assert "时长 8 秒" in result.output
    assert "画幅 16:9" in result.output
    assert "输出数 1" in result.output
    assert "original-720p" in result.output
    assert "重试 1 次" in result.output
    assert "输出目录：output/my-story" in result.output


def test_defaults_omitted_uses_builtin_defaults(tmp_path):
    path = write_script(tmp_path, 'name: bare\nshots:\n  - prompt: "一镜"\n')
    result = runner.invoke(app, ["check", str(path)])
    assert result.exit_code == 0, result.output
    assert "模型 omni-1.1-flash" in result.output
    assert "时长 8 秒" in result.output
    assert "画幅 16:9" in result.output
    assert "输出数 1" in result.output
    assert "original-720p" in result.output
    assert "重试 1 次" in result.output


def test_first_frame_last_frame_and_none_sources_ok(tmp_path):
    text = (
        "name: sources\n"
        "shots:\n"
        '  - prompt: "a"\n'
        "    first_frame: {source: last_frame}\n"
        '  - prompt: "b"\n'
        "    first_frame: {source: none}\n"
        '  - prompt: "c"\n'  # 省略 first_frame，视为 none
    )
    path = write_script(tmp_path, text)
    result = runner.invoke(app, ["check", str(path)])
    assert result.exit_code == 0, result.output
    assert "镜头数：3" in result.output


def test_veo_fast_allows_10s(tmp_path):
    text = (
        "name: fast10\n"
        "defaults: {model: veo-3.1-fast, duration: 10}\n"
        'shots:\n  - prompt: "x"\n'
    )
    path = write_script(tmp_path, text)
    result = runner.invoke(app, ["check", str(path)])
    assert result.exit_code == 0, result.output


# ---------------------------------------------------------------- 非法剧本（工单必列用例）


def test_yaml_syntax_error(tmp_path):
    path = write_script(tmp_path, "name: [unclosed\n")
    result = runner.invoke(app, ["check", str(path)])
    assert result.exit_code == 1
    assert "YAML 语法错误" in result.output


def test_missing_prompt_names_shot(tmp_path):
    path = write_script(tmp_path, "name: no-prompt\nshots:\n  - first_frame: {source: none}\n")
    result = runner.invoke(app, ["check", str(path)])
    assert result.exit_code == 1
    assert "镜头 1" in result.output
    assert "缺少 prompt" in result.output


def test_invalid_first_frame_source(tmp_path):
    text = 'name: bad-source\nshots:\n  - prompt: "x"\n    first_frame: {source: video}\n'
    path = write_script(tmp_path, text)
    result = runner.invoke(app, ["check", str(path)])
    assert result.exit_code == 1
    assert "first_frame.source" in result.output
    assert "非法" in result.output
    assert "last_frame" in result.output  # 允许值列表里可查


def test_image_path_not_exist_names_shot(tmp_path):
    text = (
        "name: ghost-image\n"
        "shots:\n"
        '  - prompt: "ok"\n'
        '  - prompt: "bad"\n'
        "    first_frame: {source: image, path: frames/missing.png}\n"
    )
    path = write_script(tmp_path, text)
    result = runner.invoke(app, ["check", str(path)])
    assert result.exit_code == 1
    assert "镜头 2" in result.output
    assert "不存在" in result.output
    assert "missing.png" in result.output


def test_omni_flash_rejects_non_8s_duration(tmp_path):
    text = (
        "name: omni6\n"
        "defaults: {model: omni-1.1-flash, duration: 6}\n"
        'shots:\n  - prompt: "x"\n'
    )
    path = write_script(tmp_path, text)
    result = runner.invoke(app, ["check", str(path)])
    assert result.exit_code == 1
    assert "不支持 6 秒" in result.output
    assert "8 秒" in result.output


def test_empty_shots(tmp_path):
    path = write_script(tmp_path, "name: empty\nshots: []\n")
    result = runner.invoke(app, ["check", str(path)])
    assert result.exit_code == 1
    assert "至少需要一个镜头" in result.output


def test_missing_name(tmp_path):
    path = write_script(tmp_path, 'shots:\n  - prompt: "x"\n')
    result = runner.invoke(app, ["check", str(path)])
    assert result.exit_code == 1
    assert "name" in result.output
    assert "缺少" in result.output


# ---------------------------------------------------------------- 其余校验行为


def test_unknown_model(tmp_path):
    text = 'name: unknown\ndefaults: {model: veo-99}\nshots:\n  - prompt: "x"\n'
    path = write_script(tmp_path, text)
    result = runner.invoke(app, ["check", str(path)])
    assert result.exit_code == 1
    assert '未知模型 "veo-99"' in result.output


def test_unknown_top_level_field_reported(tmp_path):
    text = 'name: extra\ntitle: x\nshots:\n  - prompt: "x"\n'
    path = write_script(tmp_path, text)
    result = runner.invoke(app, ["check", str(path)])
    assert result.exit_code == 1
    assert '未知字段 "title"' in result.output


def test_multiple_errors_all_listed(tmp_path):
    text = (
        "name: multi\n"
        "defaults: {model: omni-1.1-flash, duration: 6}\n"
        "shots:\n"
        "  - first_frame: {source: none}\n"
    )
    path = write_script(tmp_path, text)
    result = runner.invoke(app, ["check", str(path)])
    assert result.exit_code == 1
    assert "共 2 个问题" in result.output
    assert "缺少 prompt" in result.output
    assert "不支持 6 秒" in result.output


def test_blank_prompt_rejected(tmp_path):
    path = write_script(tmp_path, 'name: blank\nshots:\n  - prompt: "   "\n')
    result = runner.invoke(app, ["check", str(path)])
    assert result.exit_code == 1
    assert "空白" in result.output


def test_invalid_aspect_and_download(tmp_path):
    text = (
        "name: bad-params\n"
        'defaults: {aspect: "4:3", download: 4k}\n'
        'shots:\n  - prompt: "x"\n'
    )
    path = write_script(tmp_path, text)
    result = runner.invoke(app, ["check", str(path)])
    assert result.exit_code == 1
    assert "画幅" in result.output
    assert "下载档位" in result.output
