"""定位表（locators）的测试：纯数据模块，集中 Flow 页面全部元素定位。

验收：底栏 Start/End 槽、swap_horiz、prompt 框、参数按钮、Create 按钮、
Start 对话框（Uploads 标签 / Add to Prompt）、生成卡片、下载入口全部入表；
模型显示名映射、页面 eval 配方（上传注入 / 媒体名探测）也集中在同一模块。
"""

from __future__ import annotations

import pytest

from flowforge import locators
from flowforge.locators import (
    ASPECT_DISPLAY_NAMES,
    LOCATORS,
    MODEL_DISPLAY_NAMES,
    Locator,
    inject_file_js,
    upload_click_js,
)
from flowforge.script import MODEL_CATALOG


EXPECTED_KEYS = (
    "new_project",
    "start_slot",
    "end_slot",
    "swap_horiz",
    "prompt_box",
    "params_button",
    "create_button",
    "uploads_tab",
    "add_to_prompt",
    "generation_card",
    "download_button",
    "download_original",
    "start_slot_remove",
    "upload_media",
    "params_model_option",
    "params_duration_option",
    "params_aspect_option",
    "params_outputs_option",
)


def test_all_ticket_keys_present():
    """工单要求定位的元素全部入表，缺一不可。"""
    for key in EXPECTED_KEYS:
        assert key in LOCATORS, f"定位表缺少条目：{key}"


@pytest.mark.parametrize("key", EXPECTED_KEYS)
def test_entries_have_chinese_description_and_match_mode(key):
    entry = LOCATORS[key]
    assert isinstance(entry, Locator)
    assert entry.key == key
    assert entry.description.strip(), "每个条目必须有中文说明"
    assert entry.match in {"exact", "contains", "prefix"}
    # name 可为空串（纯 css/冒烟校准点条目），但必须显式给出字段
    assert entry.name is not None


def test_start_slot_is_radix_dialog_trigger():
    """Start 槽：aria-haspopup=dialog 的触发器，css 侧选择器入表。"""
    slot = LOCATORS["start_slot"]
    assert slot.name == "Start"
    assert slot.css is not None and 'aria-haspopup="dialog"' in slot.css


def test_prompt_box_uses_flow_text():
    assert "What do you want to create?" in LOCATORS["prompt_box"].name


def test_uploads_tab_matches_material_icon_text():
    """Uploads 标签的页面文本是图标连字+文字混合（drive_folder_uploadUploads）。"""
    assert "Uploads" in LOCATORS["uploads_tab"].name


def test_create_button_matches_icon_and_text():
    assert "Create" in LOCATORS["create_button"].name
    assert "arrow_forward" in LOCATORS["create_button"].description


def test_download_entries():
    assert "download" in LOCATORS["download_button"].name.lower()
    assert "Original" in LOCATORS["download_original"].name


def test_model_display_names_covers_default_model():
    assert MODEL_DISPLAY_NAMES["omni-1.1-flash"] == "Omni 1.1 Flash"
    # 映射主键必须都在剧本模型目录内（防两表漂移）
    for model in MODEL_DISPLAY_NAMES:
        assert model in MODEL_CATALOG


def test_aspect_display_names_cover_catalog():
    from flowforge.script import ASPECT_RATIOS

    assert set(ASPECT_DISPLAY_NAMES) == set(ASPECT_RATIOS)


def test_inject_file_js_dispatches_change_only():
    """原型教训：input+change 双事件会双重上传，注入配方只允许派发 change。"""
    js = inject_file_js("QUJD", "frame.png", "image/png")
    assert "DataTransfer" in js
    assert 'accept === "image/*"' in js
    assert js.count("dispatchEvent") == 1
    assert "change" in js
    assert "new Event('input'" not in js and 'new Event("input"' not in js
    # base64 与文件名作为 JSON 字符串字面量嵌入
    assert '"QUJD"' in js
    assert '"frame.png"' in js
    assert '"image/png"' in js


def test_upload_click_js_matches_by_media_name():
    """素材 alt 是通用文案不能按名匹配，只能按媒体 UUID（name 参数）点选。"""
    js = upload_click_js("abc-123")
    assert '"abc-123"' in js
    assert "getMediaUrlRedirect" in js
    assert '[role="dialog"]' in js


def test_media_probe_scripts_reference_media_url_pattern():
    assert "getMediaUrlRedirect" in locators.MEDIA_NAMES_JS
    assert "getMediaUrlRedirect" in locators.UPLOADS_MEDIA_NAMES_JS
    assert '[role="dialog"]' in locators.UPLOADS_MEDIA_NAMES_JS
    assert "getMediaUrlRedirect" in locators.START_SLOT_MEDIA_JS
    # 探测脚本必须只读：不包含写操作 API
    for js in (
        locators.MEDIA_NAMES_JS,
        locators.UPLOADS_MEDIA_NAMES_JS,
        locators.START_SLOT_MEDIA_JS,
    ):
        assert "dispatchEvent" not in js
        assert ".click()" not in js


def test_flow_constants():
    assert locators.FLOW_HOME_URL.startswith("https://")
    assert locators.PROJECT_URL_MARKER == "/project/"
