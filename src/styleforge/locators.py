"""Flow 页面定位表——全部网页元素定位的唯一出处（纯数据模块）。

维护约定（spec User Story 20）：Flow 改版时只改这一个文件。
- `LOCATORS`：底栏 Start/End 槽、swap_horiz、prompt 框、参数按钮、Create 按钮、
  Start 对话框（Uploads 标签 / Add to Prompt / Upload media）、生成卡片、
  下载入口与下载菜单档位、参数面板各选项。
- `MODEL_DISPLAY_NAMES` / `ASPECT_DISPLAY_NAMES`：剧本模型 ID → 页面显示名映射。
- eval 配方（`inject_file_js` / `upload_click_js` 与只读探测脚本）：页面细节
  同样集中在这里；驱动层只准引用本模块，零硬编码选择器散落。

标注「冒烟校准点」的条目是工单 04 无法离线验证、留给工单 05 真网页冒烟
按实测校正的定位（本票不碰真网页）。
"""

from __future__ import annotations

import json
from dataclasses import dataclass


# ---------------------------------------------------------------- 页面常量

# Flow 首页（flow.google 重定向到 labs.google/fx/tools/flow，原型实测）。
FLOW_HOME_URL = "https://flow.google"

# 项目画布 URL 标记：new_project 轮询 get url 直到出现该片段。
PROJECT_URL_MARKER = "/project/"


# ---------------------------------------------------------------- 定位条目


@dataclass(frozen=True)
class Locator:
    """单个页面元素的定位描述。

    role/name 供 bb-browser snap 快照（refs: {role, name}）匹配使用；
    css 供 eval 只读探测使用。match 决定 name 的匹配方式
    （exact 精确；contains/prefix 大小写不敏感）。
    """

    key: str
    description: str
    role: str | None
    name: str
    match: str = "contains"
    css: str | None = None


LOCATORS: dict[str, Locator] = {
    # 顶栏与底栏
    "new_project": Locator(
        key="new_project",
        description="顶栏 New project 按钮（实测可访问名为图标连字+文字：add_2 New project）",
        role="button",
        name="New project",
        match="contains",
    ),
    "start_slot": Locator(
        key="start_slot",
        description="底栏 Start 首帧槽（aria-haspopup=dialog 的 Radix 触发器；实测为 div[type=button]，快照 role=div，css 仅供 eval 探测不参与 snap 匹配）",
        role=None,
        name="Start",
        match="exact",
        css='[aria-haspopup="dialog"]',
    ),
    "end_slot": Locator(
        key="end_slot",
        description="底栏 End 尾帧槽（同 start_slot 为 div 触发器；v1 不用，留作尾帧扩展）",
        role=None,
        name="End",
        match="exact",
        css='[aria-haspopup="dialog"]',
    ),
    "swap_horiz": Locator(
        key="swap_horiz",
        description="首尾帧之间的 swap_horiz 交换按钮（v1 不用，留作扩展）",
        role=None,
        name="swap_horiz",
        match="contains",
    ),
    "prompt_box": Locator(
        key="prompt_box",
        description="提示词输入框（textbox，占位文本 What do you want to create?）",
        role="textbox",
        name="What do you want to create?",
        match="contains",
    ),
    "params_button": Locator(
        key="params_button",
        description="底栏参数按钮（显示名形如 Video · 720p · 8s crop_16_9 x1）",
        role="button",
        name="Video ·",
        match="prefix",
    ),
    "create_button": Locator(
        key="create_button",
        description="底栏 Create 生成按钮（arrow_forward 图标）",
        role="button",
        name="Create",
        match="contains",
    ),
    # Start 对话框
    "uploads_tab": Locator(
        key="uploads_tab",
        description="Start 对话框内 Uploads 标签（页面文本为图标连字+文字：drive_folder_uploadUploads）",
        role=None,
        name="Uploads",
        match="contains",
    ),
    "add_to_prompt": Locator(
        key="add_to_prompt",
        description="Start 对话框内 Add to Prompt 按钮",
        role="button",
        name="Add to Prompt",
        match="contains",
    ),
    "upload_media": Locator(
        key="upload_media",
        description="Start 对话框内 Upload media 按钮（备用：驱动走 DataTransfer 注入，不点它）",
        role="button",
        name="Upload media",
        match="contains",
    ),
    # 生成卡片与下载
    "generation_card": Locator(
        key="generation_card",
        description="画布上的生成卡片（悬停出下载图标；role/name/css 为冒烟校准点，完成判定走媒体 UUID diff 不依赖它）",
        role=None,
        name="",
        match="contains",
    ),
    "download_button": Locator(
        key="download_button",
        description="生成卡片上的下载图标（向下箭头；aria 名含 download）",
        role=None,
        name="download",
        match="contains",
    ),
    "download_original": Locator(
        key="download_original",
        description="下载菜单 Original 档（Original 720p mp4，即时下载）",
        role=None,
        name="Original",
        match="contains",
    ),
    # Start 槽维护
    "start_slot_remove": Locator(
        key="start_slot_remove",
        description="Start 槽悬停后出现的移除/清空按钮（冒烟校准点：aria 名待实测）",
        role=None,
        name="remove",
        match="contains",
    ),
    # 参数面板选项（name 运行时由显示名映射给出，实测 role 为 button 或 tab，match 改为 contains 兼容图标前缀）
    "params_model_option": Locator(
        key="params_model_option",
        description="参数面板模型选项（名称见 MODEL_DISPLAY_NAMES）",
        role=None,
        name="",
        match="contains",
    ),
    "params_duration_option": Locator(
        key="params_duration_option",
        description="参数面板时长选项（显示名形如 8s）",
        role=None,
        name="",
        match="contains",
    ),
    "params_aspect_option": Locator(
        key="params_aspect_option",
        description="参数面板画幅选项（显示名见 ASPECT_DISPLAY_NAMES）",
        role=None,
        name="",
        match="contains",
    ),
    "params_outputs_option": Locator(
        key="params_outputs_option",
        description="参数面板输出数选项（显示名形如 x1）",
        role=None,
        name="",
        match="contains",
    ),
}


# ---------------------------------------------------------------- 显示名映射

# 剧本模型 ID → 参数面板显示名。omni-1.1-flash 为用户实测选用项；
# 其余为按命名惯例的推断值，冒烟校准点（对不上时驱动报「未知显示名」）。
MODEL_DISPLAY_NAMES: dict[str, str] = {
    "omni-1.1-flash": "Omni 1.1 Flash",
    "gemini-omni-flash": "Gemini Omni Flash",
    "veo-3.1-fast": "Veo 3.1 Fast",
    "veo-3.1-quality": "Veo 3.1 Quality",
}

# 画幅 → 参数面板显示名（页面显示文案待冒烟校准）。
ASPECT_DISPLAY_NAMES: dict[str, str] = {
    "16:9": "16:9",
    "9:16": "9:16",
    "1:1": "1:1",
}


def duration_display(duration: int) -> str:
    """时长选项显示名（参数按钮文本即用 8s 形式）。"""
    return f"{duration}s"


def outputs_display(outputs: int) -> str:
    """输出数选项显示名（参数按钮文本即用 x1 形式）。"""
    return f"x{outputs}"


# ---------------------------------------------------------------- eval 配方
# 全部片段返回 JSON 字符串（JSON.stringify 包裹），驱动侧统一 json.loads。
# 严格使用标准 function() 语法，避免 => 箭头中的 > 符号在 Windows 批处理下被误判为输出重定向。


# 顶栏 New project 按钮 DOM 点击（实测 React 对快照 CDP click 无响应，DOM click 一击即中）。
NEW_PROJECT_DOM_CLICK_JS = (
    'JSON.stringify((function() {'
    ' var btns = Array.from(document.querySelectorAll("button, div[role=\\"button\\"], a"));'
    ' for (var i = 0; i < btns.length; i++) {'
    '   var b = btns[i];'
    '   var text = (b.textContent || "").trim();'
    '   var aria = b.getAttribute("aria-label") || "";'
    '   if (text.indexOf("New project") !== -1 || aria.indexOf("New project") !== -1) {'
    '     b.click();'
    '     return "clicked";'
    '   }'
    ' }'
    ' return "not-found";'
    '})())'
)


# 底栏 Start 首帧槽 DOM 点击（Radix UI 触发器，React DOM click 可靠弹出对话框）。
START_SLOT_DOM_CLICK_JS = (
    'JSON.stringify((function() {'
    ' var el = document.querySelector("[aria-haspopup=\\"dialog\\"]");'
    ' if (!el) {'
    '   var els = Array.from(document.querySelectorAll("div, button"));'
    '   for (var i = 0; i < els.length; i++) {'
    '     if ((els[i].textContent || "").trim() === "Start") { el = els[i]; break; }'
    '   }'
    ' }'
    ' if (el) { el.click(); return "clicked"; }'
    ' return "not-found";'
    '})())'
)


# Start 对话框内 Uploads 标签 DOM 点击。
UPLOADS_TAB_DOM_CLICK_JS = (
    'JSON.stringify((function() {'
    ' var dlg = document.querySelector(\'[role="dialog"]\') || document;'
    ' var tabs = Array.from(dlg.querySelectorAll(\'[role="tab"], button\'));'
    ' for (var i = 0; i < tabs.length; i++) {'
    '   var t = tabs[i];'
    '   var text = (t.textContent || "").trim();'
    '   if (text.indexOf("Uploads") !== -1) {'
    '     t.click();'
    '     return "clicked";'
    '   }'
    ' }'
    ' return "not-found";'
    '})())'
)


# 页面上全部媒体 UUID（getMediaUrlRedirect 的 name 参数）：生成前后 diff
# 出新增即为本次产物身份（媒体 UUID diff，防幽灵产物——樱之诗 watcher_v6 教训）。
MEDIA_NAMES_JS = (
    'JSON.stringify((function() {'
    ' var els = Array.from(document.querySelectorAll("[src*=getMediaUrlRedirect]"));'
    ' var res = [];'
    ' for (var i = 0; i < els.length; i++) {'
    '   try {'
    '     var name = new URL(els[i].src).searchParams.get("name");'
    '     if (name) res.push(name);'
    '   } catch (e) {}'
    ' }'
    ' return res;'
    '})())'
)

# Start 对话框 Uploads 网格内的媒体 UUID（限定 role=dialog，避免把 Start 槽
# 已挂的图算进网格基线）。素材 alt 是通用文案，只能按媒体 UUID diff 定位新上传。
UPLOADS_MEDIA_NAMES_JS = (
    'JSON.stringify((function() {'
    ' var dlg = document.querySelector(\'[role="dialog"]\');'
    ' if (!dlg) return [];'
    ' var imgs = Array.from(dlg.querySelectorAll("img[src*=\\"getMediaUrlRedirect\\"]"));'
    ' var res = [];'
    ' for (var i = 0; i < imgs.length; i++) {'
    '   try {'
    '     var name = new URL(imgs[i].src).searchParams.get("name");'
    '     if (name) res.push(name);'
    '   } catch (e) {}'
    ' }'
    ' return res;'
    '})())'
)


def upload_click_js(media_name: str) -> str:
    """在 Uploads 网格中按媒体 UUID 点选新增素材的 img。

    用 eval 点击是唯一可行路径：该元素只能靠页内 diff 定位（快照 ref 无法
    区分 alt 为通用文案的多张图），且 DOM click 会冒泡、React 正常响应；
    与樱之诗教训（React 拒绝 JS 合成 input/change 事件）不冲突。
    """
    return (
        'JSON.stringify((function() {'
        ' var dlg = document.querySelector(\'[role="dialog"]\');'
        f' var name = {json.dumps(media_name)};'
        ' if (!dlg) return "no-dialog";'
        ' var imgs = Array.from(dlg.querySelectorAll("img[src*=\\"getMediaUrlRedirect\\"]"));'
        ' for (var i = 0; i < imgs.length; i++) {'
        '   try {'
        '     if (new URL(imgs[i].src).searchParams.get("name") === name) {'
        '       imgs[i].click();'
        '       return "clicked";'
        '     }'
        '   } catch (e) {}'
        ' }'
        ' return "not-found";'
        '})())'
    )


def inject_file_js(data_base64: str, filename: str, mime: str) -> str:
    """首帧上传注入配方（原型实测结论，prototype-findings.md）。

    base64 → File → DataTransfer → 隐藏 input[type=file][accept=image/*]；
    **只派发 change 事件**——同时派发 input+change 会双重上传（原型实测坑）。
    """
    return (
        'JSON.stringify((function() {'
        f' var b64 = {json.dumps(data_base64)};'
        f' var filename = {json.dumps(filename)};'
        f' var mime = {json.dumps(mime)};'
        ' var raw = atob(b64);'
        ' var bytes = new Uint8Array(raw.length);'
        ' for (var i = 0; i < raw.length; i++) {'
        '   bytes[i] = raw.charCodeAt(i);'
        ' }'
        ' var file = new File([bytes], filename, {type: mime});'
        ' var inputs = Array.from(document.querySelectorAll("input[type=file]"));'
        ' var input = null;'
        ' for (var j = 0; j < inputs.length; j++) {'
        '   if (inputs[j].accept === "image/*") { input = inputs[j]; break; }'
        ' }'
        ' if (!input) return "no-input";'
        ' var dt = new DataTransfer(); dt.items.add(file);'
        ' input.files = dt.files;'
        ' input.dispatchEvent(new Event("change", {bubbles: true}));'
        ' return "injected";'
        '})())'
    )


def inject_file_from_var_js(var_name: str, filename: str, mime: str) -> str:
    """从页内全局变量中读取 Base64 并注入 input[type=file]（避开 Windows 命令行 32KB 上限）。"""
    return (
        'JSON.stringify((function() {'
        f' var b64 = window[{json.dumps(var_name)}] || "";'
        f' var filename = {json.dumps(filename)};'
        f' var mime = {json.dumps(mime)};'
        ' if (!b64) return "no-data";'
        ' var raw = atob(b64);'
        ' var bytes = new Uint8Array(raw.length);'
        ' for (var i = 0; i < raw.length; i++) {'
        '   bytes[i] = raw.charCodeAt(i);'
        ' }'
        ' var file = new File([bytes], filename, {type: mime});'
        ' var inputs = Array.from(document.querySelectorAll("input[type=file]"));'
        ' var input = null;'
        ' for (var j = 0; j < inputs.length; j++) {'
        '   if (inputs[j].accept === "image/*") { input = inputs[j]; break; }'
        ' }'
        ' if (!input) return "no-input";'
        ' var dt = new DataTransfer(); dt.items.add(file);'
        ' input.files = dt.files;'
        ' input.dispatchEvent(new Event("change", {bubbles: true}));'
        ' return "injected";'
        '})())'
    )


# Start 槽当前挂载的媒体 UUID（未挂图返回 null）：Add to Prompt 后的落位验证。
START_SLOT_MEDIA_JS = (
    'JSON.stringify((function() {'
    ' var dlg = document.querySelector(\'[role="dialog"]\');'
    ' var imgs = Array.from(document.querySelectorAll(\'img[src*="getMediaUrlRedirect"]\'));'
    ' for (var i = 0; i < imgs.length; i++) {'
    '   var img = imgs[i];'
    '   if (dlg && dlg.contains(img)) continue;'
    '   try {'
    '     var name = new URL(img.src).searchParams.get("name");'
    '     if (name) return name;'
    '   } catch (e) {}'
    ' }'
    ' return null;'
    '})())'
)

# Start 对话框内 Add to Prompt 按钮 DOM 点击。
ADD_TO_PROMPT_DOM_CLICK_JS = (
    'JSON.stringify((function() {'
    ' var btns = Array.from(document.querySelectorAll("button"));'
    ' for (var i = 0; i < btns.length; i++) {'
    '   var b = btns[i];'
    '   var text = (b.textContent || "").trim();'
    '   if (text.indexOf("Add to Prompt") !== -1) {'
    '     b.click();'
    '     return "clicked";'
    '   }'
    ' }'
    ' return "not-found";'
    '})())'
)


def set_slate_prompt_js(text: str) -> str:
    """针对 Slate.js React 状态树注入提示词并触发 React 更新，激活 Create 按钮。"""
    return (
        'JSON.stringify((function() {'
        ' var box = document.querySelector("[data-slate-editor=true], div[contenteditable=true]");'
        ' if (!box) return "no-box";'
        ' var fiberKey = Object.keys(box).find(function(k){ return k.indexOf("__reactFiber") === 0; });'
        ' if (!fiberKey) return "no-fiber";'
        ' var curr = box[fiberKey];'
        ' while(curr && (!curr.memoizedProps || !curr.memoizedProps.editor)) curr = curr.return;'
        ' if (!curr) return "no-editor";'
        ' var editor = curr.memoizedProps.editor;'
        f' var text = {json.dumps(text)};'
        ' editor.select({anchor: {path: [0, 0], offset: 0}, focus: {path: [0, 0], offset: 0}});'
        ' editor.insertText(text);'
        ' editor.onChange();'
        ' if (typeof curr.memoizedProps.onChange === "function") {'
        '   curr.memoizedProps.onChange(editor.children);'
        ' }'
        ' return "injected";'
        '})())'
    )


# 提交生成（Create）DOM 按钮点击配方。
CREATE_BUTTON_DOM_CLICK_JS = (
    'JSON.stringify((function() {'
    ' var btn = Array.from(document.querySelectorAll("button")).find(function(b){'
    '   return (b.textContent||"").indexOf("Create") !== -1;'
    ' });'
    ' if (!btn) return "no-btn";'
    ' var propKey = Object.keys(btn).find(function(k){ return k.indexOf("__reactProps") === 0; });'
    ' var props = btn[propKey] || {};'
    ' if (typeof props.onClick === "function") {'
    '   try {'
    '     props.onClick({ isTrusted: true, nativeEvent: { isTrusted: true }, currentTarget: btn, target: btn, preventDefault: function(){}, stopPropagation: function(){} });'
    '     return "clicked-handler";'
    '   } catch(e) {}'
    ' }'
    ' btn.click();'
    ' return "clicked-dom";'
    '})())'
)


# 关闭参数菜单 Popover（派发 Escape 键盘事件）。
CLOSE_MENU_ESCAPE_JS = (
    'JSON.stringify((function() {'
    ' document.dispatchEvent(new KeyboardEvent("keydown", {key: "Escape", code: "Escape", keyCode: 27, which: 27, bubbles: true}));'
    ' document.dispatchEvent(new KeyboardEvent("keyup", {key: "Escape", code: "Escape", keyCode: 27, which: 27, bubbles: true}));'
    ' return "closed";'
    '})())'
)
