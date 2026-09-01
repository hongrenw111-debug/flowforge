"""剧本层工单 03 新增能力的 CLI 级行为测试（离线）。

覆盖：模型命名迁移（gemini-omni → omni-1.1-flash）、单镜头参数覆盖
（model/duration/aspect/outputs，校验规则与全局一致）、MAD 阈值可配。
只断言外部行为：退出码与中文输出内容。
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


# ---------------------------------------------------------------- 模型命名迁移


def test_legacy_model_name_rejected_with_migration_hint(tmp_path):
    text = (
        "name: legacy\n"
        "defaults: {model: gemini-omni}\n"
        'shots:\n  - prompt: "x"\n'
    )
    path = write_script(tmp_path, text)
    result = runner.invoke(app, ["check", str(path)])
    assert result.exit_code == 1
    assert "模型名已更新" in result.output
    assert "gemini-omni" in result.output
    assert "omni-1.1-flash" in result.output
    assert "请修改剧本" in result.output


def test_legacy_model_name_on_shot_rejected_with_migration_hint(tmp_path):
    text = (
        "name: legacy-shot\n"
        'shots:\n  - prompt: "x"\n    model: gemini-omni\n'
    )
    path = write_script(tmp_path, text)
    result = runner.invoke(app, ["check", str(path)])
    assert result.exit_code == 1
    assert "镜头 1" in result.output
    assert "模型名已更新" in result.output
    assert "omni-1.1-flash" in result.output


def test_omni_1_1_flash_is_default_model(tmp_path):
    path = write_script(tmp_path, 'name: bare\nshots:\n  - prompt: "一镜"\n')
    result = runner.invoke(app, ["check", str(path)])
    assert result.exit_code == 0, result.output
    assert "模型 omni-1.1-flash" in result.output
    assert "时长 8 秒" in result.output


def test_omni_1_1_flash_rejects_non_8s_duration(tmp_path):
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


def test_other_catalog_models_unchanged(tmp_path):
    text = (
        "name: catalog\n"
        'defaults: {model: veo-3.1-fast, duration: 10}\n'
        'shots:\n  - prompt: "x"\n'
    )
    path = write_script(tmp_path, text)
    result = runner.invoke(app, ["check", str(path)])
    assert result.exit_code == 0, result.output


# ---------------------------------------------------------------- 单镜头参数覆盖


def test_shot_level_overrides_accepted(tmp_path):
    text = (
        "name: overrides\n"
        "defaults: {model: omni-1.1-flash, duration: 8}\n"
        "shots:\n"
        '  - prompt: "普通镜"\n'
        '  - prompt: "竖屏快镜"\n'
        "    model: veo-3.1-fast\n"
        "    duration: 4\n"
        '    aspect: "9:16"\n'
        "    outputs: 2\n"
    )
    path = write_script(tmp_path, text)
    result = runner.invoke(app, ["check", str(path)])
    assert result.exit_code == 0, result.output
    assert "镜头数：2" in result.output


def test_shot_level_model_duration_combo_validated_like_global(tmp_path):
    text = (
        "name: bad-shot-model\n"
        'shots:\n  - prompt: "x"\n    model: omni-1.1-flash\n    duration: 4\n'
    )
    path = write_script(tmp_path, text)
    result = runner.invoke(app, ["check", str(path)])
    assert result.exit_code == 1
    assert "镜头 1" in result.output
    assert "不支持 4 秒" in result.output
    assert "8 秒" in result.output


def test_shot_level_model_unknown(tmp_path):
    text = 'name: bad-shot-unknown\nshots:\n  - prompt: "x"\n    model: veo-99\n'
    path = write_script(tmp_path, text)
    result = runner.invoke(app, ["check", str(path)])
    assert result.exit_code == 1
    assert "镜头 1" in result.output
    assert '未知模型 "veo-99"' in result.output


def test_shot_level_duration_validated_against_effective_model(tmp_path):
    """镜头只写 duration 不写 model 时，按生效模型（defaults.model）校验。"""
    text = (
        "name: effective\n"
        "defaults: {model: omni-1.1-flash}\n"
        'shots:\n  - prompt: "x"\n    duration: 10\n'
    )
    path = write_script(tmp_path, text)
    result = runner.invoke(app, ["check", str(path)])
    assert result.exit_code == 1
    assert "镜头 1" in result.output
    assert "不支持 10 秒" in result.output


def test_shot_level_aspect_invalid(tmp_path):
    text = (
        "name: bad-shot-aspect\n"
        'shots:\n  - prompt: "x"\n    aspect: "4:3"\n'
    )
    path = write_script(tmp_path, text)
    result = runner.invoke(app, ["check", str(path)])
    assert result.exit_code == 1
    assert "镜头 1" in result.output
    assert "画幅" in result.output
    assert '"4:3"' in result.output


def test_shot_level_outputs_out_of_range(tmp_path):
    text = 'name: bad-shot-outputs\nshots:\n  - prompt: "x"\n    outputs: 5\n'
    path = write_script(tmp_path, text)
    result = runner.invoke(app, ["check", str(path)])
    assert result.exit_code == 1
    assert "镜头 1" in result.output
    assert "outputs" in result.output


# ---------------------------------------------------------------- MAD 阈值可配


def test_mad_threshold_defaults_to_25(tmp_path):
    path = write_script(tmp_path, 'name: bare\nshots:\n  - prompt: "一镜"\n')
    result = runner.invoke(app, ["check", str(path)])
    assert result.exit_code == 0, result.output
    assert "MAD 阈值 25" in result.output


def test_mad_threshold_configurable(tmp_path):
    text = 'name: tuned\ndefaults: {mad_threshold: 12.5}\nshots:\n  - prompt: "x"\n'
    path = write_script(tmp_path, text)
    result = runner.invoke(app, ["check", str(path)])
    assert result.exit_code == 0, result.output
    assert "MAD 阈值 12.5" in result.output


def test_mad_threshold_negative_rejected(tmp_path):
    text = 'name: neg\ndefaults: {mad_threshold: -1}\nshots:\n  - prompt: "x"\n'
    path = write_script(tmp_path, text)
    result = runner.invoke(app, ["check", str(path)])
    assert result.exit_code == 1
    assert "mad_threshold" in result.output
