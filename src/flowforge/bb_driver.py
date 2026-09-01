"""BbBrowserDriver——驱动合同的 v1 实现：subprocess 调 bb-browser CLI。

设计要点：
- **可测试性 seam**：全部 CLI 交互收敛到 `_run_cli(args) -> dict`（bb-browser
  `--json` 信封解析），测试注入 `cli_runner` 即可离线覆盖全部路径；
  测试与生产环境都不会碰到真实网页。
- **优先原生 click/fill**（走 CDP 真实输入事件，React 页面可靠）；eval 仅限
  DataTransfer 注入、diff 定位后的素材点选与只读探测（樱之诗教训：React
  拒绝 JS 合成 input/change 事件）。
- **daemon 兜底**：命令失败且输出像 daemon 未运行时，自动 `daemon start`
  后原命令重试一次（仅一次，防止循环）。
- **零凭据落盘**：错误信息只取 bb-browser 结构化 error 字段或脱敏截断后的
  输出摘要；日志绝不输出页面原始内容、cookie、Authorization、邮箱。
- 页面元素定位一律引用 `locators` 定位表，本模块零硬编码选择器。
"""

from __future__ import annotations

import base64
import json
import os
import random
import re
import shutil
import subprocess
import sys
import time
from pathlib import Path
from typing import Callable, Sequence

from flowforge.driver import ClipInfo, Driver, DriverError, DriverTimeoutError
from flowforge.frames import FramesError, ensure_valid_video
from flowforge.locators import (
    ADD_TO_PROMPT_DOM_CLICK_JS,
    ASPECT_DISPLAY_NAMES,
    CLOSE_MENU_ESCAPE_JS,
    CREATE_BUTTON_DOM_CLICK_JS,
    FLOW_HOME_URL,
    LOCATORS,
    MEDIA_NAMES_JS,
    MODEL_DISPLAY_NAMES,
    NEW_PROJECT_DOM_CLICK_JS,
    PROJECT_URL_MARKER,
    START_SLOT_DOM_CLICK_JS,
    START_SLOT_MEDIA_JS,
    UPLOADS_MEDIA_NAMES_JS,
    UPLOADS_TAB_DOM_CLICK_JS,
    Locator,
    duration_display,
    inject_file_from_var_js,
    inject_file_js,
    outputs_display,
    set_slate_prompt_js,
    upload_click_js,
)

# 下载目录环境变量（可覆盖默认 ~/Downloads；测试注入临时目录）。
DOWNLOAD_DIR_ENV = "STYLEFORGE_DOWNLOAD_DIR"

# CLI 命令替身类型：args -> (退出码, stdout, stderr)。
CliRunner = Callable[[Sequence[str], float], tuple[int, str, str]]

# 轮询间隔（秒）：随机抖动的闭区间（工单要求 5-10 秒，拟人化节奏）。
POLL_INTERVAL_SECONDS: tuple[float, float] = (5.0, 10.0)

# 下载落稳判定：两次采样大小一致的最小间隔秒数。
DOWNLOAD_STABLE_INTERVAL_SECONDS = 1.0

# 下载等待上限（秒）：8 秒 720p 视频通常数十秒内完成。
DOWNLOAD_TIMEOUT_SECONDS = 300.0

# 产物体积下限（字节）：8 秒 720p mp4 远大于此；14 字节伪视频与失败响应页
# 一律拒收（樱之诗取证实坑）。完整性时长下限由 frames.ensure_valid_video 把关。
MIN_CLIP_FILE_BYTES = 100 * 1024

# 上传素材出现在 Uploads 网格 / Start 槽的等待上限（秒）。
UPLOAD_WAIT_TIMEOUT_SECONDS = 60.0

# 首帧注入后的图片 MIME 表（按扩展名）。
_MIME_BY_SUFFIX: dict[str, str] = {
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".webp": "image/webp",
    ".gif": "image/gif",
}

# daemon 未运行的特征（退出码非零且输出命中）：命中则自动启动 daemon 并重试一次。
_DAEMON_DOWN_PATTERN = re.compile(r"daemon|econnrefused|econnaborted|epipe|connect", re.IGNORECASE)

# 零凭据脱敏：密钥形键值对与邮箱地址（我们的日志同样必须脱敏，工单红线）。
# 值消费到下一个密钥键 / 分号 / 结尾，避免「Authorization: Bearer <token>」只删一半。
_SECRET_RE = re.compile(
    r"(?i)\b(authorization|auth|cookie|token|session|password|passwd)\b\s*[:=]\s*"
    r".*?(?=\s*\b(?:authorization|auth|cookie|token|session|password|passwd)\b\s*[:=]|;|$)"
)
_BEARER_RE = re.compile(r"(?i)\bbearer\s+\S+")
_EMAIL_RE = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")

_SANITIZE_LIMIT = 200


def _sanitize_text(text: str, limit: int = _SANITIZE_LIMIT) -> str:
    """把外部输出压平、脱敏并截断成可安全进日志/异常的摘要。"""
    cleaned = " ".join((text or "").split())
    cleaned = _EMAIL_RE.sub("[已脱敏邮箱]", cleaned)
    cleaned = _BEARER_RE.sub("[已脱敏]", cleaned)
    cleaned = _SECRET_RE.sub(lambda m: m.group(1) + "=[已脱敏]", cleaned)
    if len(cleaned) > limit:
        cleaned = cleaned[:limit] + "…（已截断）"
    return cleaned


class BbBrowserDriver(Driver):
    """驱动用户真实 Chrome 的后台标签页操作 Google Flow。

    全部页面操作走定位表（flowforge.locators）；标签页短 ID 全程复用；
    等待类操作的超时与轮询抖动均可注入，测试零等待。
    """

    def __init__(
        self,
        *,
        binary: str = "bb-browser",
        cli_runner: CliRunner | None = None,
        cli_timeout: float = 30.0,
        poll_interval: tuple[float, float] = POLL_INTERVAL_SECONDS,
        page_ready_timeout: float = 60.0,
        upload_wait_timeout: float = UPLOAD_WAIT_TIMEOUT_SECONDS,
        download_timeout: float = DOWNLOAD_TIMEOUT_SECONDS,
        download_stable_interval: float = DOWNLOAD_STABLE_INTERVAL_SECONDS,
        min_clip_bytes: int = MIN_CLIP_FILE_BYTES,
        download_dir: Path | str | None = None,
        sleep: Callable[[float], None] = time.sleep,
        clock: Callable[[], float] = time.monotonic,
        rng: Callable[[float, float], float] = random.uniform,
        log: Callable[[str], None] = print,
    ) -> None:
        self._binary = binary
        self._cli_timeout = cli_timeout
        self._cli_runner: CliRunner = cli_runner or self._default_cli_runner
        self._poll_interval = poll_interval
        self._page_ready_timeout = page_ready_timeout
        self._upload_wait_timeout = upload_wait_timeout
        self._download_timeout = download_timeout
        self._download_stable_interval = download_stable_interval
        self._min_clip_bytes = min_clip_bytes
        self._sleep = sleep
        self._clock = clock
        self._rng = rng
        self._log = log
        if download_dir is None:
            env_dir = os.environ.get(DOWNLOAD_DIR_ENV)
            download_dir = Path(env_dir) if env_dir else Path.home() / "Downloads"
        self._download_dir = Path(download_dir)
        # 标签页短 ID：new_project / open_project 建立，之后全程复用。
        self._tab: str | None = None
        # 生成前页面已有媒体 UUID 基线（generate 时记录，wait 时 diff）。
        self._media_baseline: set[str] | None = None
        self._download_seq = 0

    # ---------------------------------------------------------------- CLI seam

    def _resolve_binary(self) -> str:
        """Windows 下若 binary 无法直接执行，尝试补齐 .cmd 扩展名或全局 npm 路径。"""
        if sys.platform == "win32" and not self._binary.lower().endswith((".cmd", ".exe", ".bat")):
            which_cmd = shutil.which(f"{self._binary}.cmd") or shutil.which(self._binary)
            if which_cmd:
                return which_cmd
            npm_global = Path(os.environ.get("APPDATA", "")) / "npm" / f"{self._binary}.cmd"
            if npm_global.is_file():
                return str(npm_global)
        return self._binary

    def _default_cli_runner(self, args: Sequence[str], timeout: float) -> tuple[int, str, str]:
        """生产执行器：subprocess 调 bb-browser，UTF-8 解码，超时交给 subprocess。"""
        bin_path = self._resolve_binary()
        try:
            proc = subprocess.run(
                [bin_path, *(str(a) for a in args)],
                capture_output=True,
                encoding="utf-8",
                errors="replace",
                timeout=timeout,
                check=False,
            )
        except FileNotFoundError as exc:
            raise FileNotFoundError(exc.errno, "bb-browser executable not found") from exc
        return proc.returncode, proc.stdout or "", proc.stderr or ""

    def _run_cli(self, *args: str, timeout: float | None = None, _recovered: bool = False) -> dict:
        """唯一的 bb-browser 调用出口：执行 → 非零退出/超时 → daemon 兜底 → JSON 解析。

        返回 bb-browser `--json` 信封里的 result 字段（无 result 视为空 dict）。
        """
        args = [str(a) for a in args]
        effective_timeout = timeout if timeout is not None else self._cli_timeout
        try:
            code, out, err = self._cli_runner(args, effective_timeout)
        except subprocess.TimeoutExpired as exc:
            raise DriverError(f"bb-browser 命令超时：{args[0]}（上限 {effective_timeout:g} 秒）") from exc
        except FileNotFoundError as exc:
            raise DriverError(
                f"未找到 bb-browser 命令，请先安装：npm install -g bb-browser（{exc}）"
            ) from exc

        if code != 0:
            combined = f"{out}\n{err}"
            if not _recovered and _DAEMON_DOWN_PATTERN.search(combined):
                self._log("bb-browser daemon 未运行，自动启动后重试")
                self._start_daemon()
                return self._run_cli(*args, timeout=effective_timeout, _recovered=True)
            raise DriverError(
                f"bb-browser 命令失败：{args[0]}（退出码 {code}）：{self._sanitize(err or out)}"
            )
        return self._parse_json(args[0], out)

    def _parse_json(self, command: str, out: str) -> dict:
        text = (out or "").strip()
        if not text:
            raise DriverError(f"bb-browser 无输出：{command}（daemon 或浏览器扩展可能未就绪）")
        try:
            payload = json.loads(text)
        except json.JSONDecodeError as exc:
            raise DriverError(
                f"bb-browser 输出不是有效 JSON：{command}：{self._sanitize(text)}"
            ) from exc
        if not isinstance(payload, dict):
            raise DriverError(f"bb-browser 输出结构异常：{command}：{self._sanitize(text)}")
        error = payload.get("error")
        if error:
            message = self._sanitize(str(error.get("message", "未知错误")))
            hint = self._sanitize(str(error.get("hint") or ""))
            detail = f"{message}（提示：{hint}）" if hint else message
            raise DriverError(f"bb-browser 报错：{command}：{detail}")
        result = payload.get("result")
        return result if isinstance(result, dict) else {}

    def _start_daemon(self) -> None:
        try:
            code, out, err = self._cli_runner(["daemon", "start", "--json"], 60.0)
        except (subprocess.TimeoutExpired, FileNotFoundError) as exc:
            raise DriverError(f"bb-browser daemon 自动启动失败：{self._sanitize(str(exc))}") from exc
        if code != 0:
            raise DriverError(f"bb-browser daemon 自动启动失败：{self._sanitize(err or out)}")

    # ---------------------------------------------------------------- 快照与 eval 辅助

    def _sanitize(self, text: str, limit: int = _SANITIZE_LIMIT) -> str:
        """对外部输出做脱敏摘要（零凭据落盘红线）。"""
        return _sanitize_text(text, limit)

    def _snap(self) -> dict:
        """当前标签页的可交互元素快照（result.snapshotData：{snapshot, refs}）。"""
        self._require_tab()
        data = self._run_cli("snap", "-i", "--tab", self._tab, "--json")
        snapshot_data = data.get("snapshotData")
        if not isinstance(snapshot_data, dict):
            raise DriverError("bb-browser 快照缺少 snapshotData（页面可能尚未加载完成）")
        refs = snapshot_data.get("refs")
        if not isinstance(refs, dict):
            raise DriverError("bb-browser 快照缺少 refs（页面可能尚未加载完成）")
        return refs

    def _find_ref(self, refs: dict, locator: Locator, name_override: str | None = None) -> str:
        """按定位条目在快照 refs 里找元素 ref；找不到抛指名道姓的中文错误。"""
        expected = name_override if name_override is not None else locator.name
        for ref, info in refs.items():
            if not isinstance(info, dict):
                continue
            if locator.role is not None and str(info.get("role", "")).lower() != locator.role.lower():
                continue
            name = str(info.get("name") or "")
            if not expected:
                continue
            if locator.match == "exact":
                hit = name == expected
            elif locator.match == "prefix":
                hit = name.lower().startswith(expected.lower())
            else:
                hit = expected.lower() in name.lower()
            if hit:
                return str(ref)
        raise DriverError(
            f"页面定位失败：{locator.description}（定位表条目 {locator.key}；"
            "Flow 可能已改版，请更新定位表或先跑冒烟脚本校准）"
        )

    def _try_find_ref(self, refs: dict, locator: Locator) -> str | None:
        try:
            return self._find_ref(refs, locator)
        except DriverError:
            return None

    def _click(self, ref: str) -> None:
        self._require_tab()
        self._run_cli("click", f"@{ref}", "--tab", self._tab, "--json")

    def _fill(self, ref: str, text: str) -> None:
        self._require_tab()
        self._run_cli("fill", f"@{ref}", text, "--tab", self._tab, "--json")

    def _eval(self, script: str) -> object:
        """页内执行 JS，取回 result 字段（bb-browser eval 的返回值）。"""
        self._require_tab()
        # 把 --tab 放在 script 之前，保证 CLI parser 能稳定解析到 tabId
        data = self._run_cli("eval", "--tab", self._tab, script, "--json", timeout=60.0)
        return data.get("result")

    def _eval_json(self, script: str) -> object:
        """执行返回 JSON 字符串的只读探测脚本并解析。

        容忍 bb-browser 已替我们解包一层引号的情况（解析失败时原样返回字符串）。
        """
        raw = self._eval(script)
        if isinstance(raw, (list, dict)) or raw is None:
            return raw
        if isinstance(raw, str):
            try:
                return json.loads(raw)
            except json.JSONDecodeError:
                return raw
        return raw

    def _media_names(self) -> list[str]:
        names = self._eval_json(MEDIA_NAMES_JS)
        if not isinstance(names, list):
            raise DriverError("页面媒体探测返回异常（期望 UUID 列表）")
        return [str(name) for name in names]

    def _poll(
        self,
        timeout: float,
        probe: Callable[[], object],
        *,
        timeout_error: str,
    ) -> object:
        """带随机抖动的轮询：probe 结果非 None 即成功；超时抛指名错误。

        睡眠走注入的 sleep/clock/rng，测试可零等待、确定推进。
        """
        deadline = self._clock() + timeout
        while True:
            outcome = probe()
            if outcome is not None:
                return outcome
            if self._clock() >= deadline:
                raise DriverTimeoutError(timeout_error)
            self._sleep(self._rng(self._poll_interval[0], self._poll_interval[1]))

    def _require_tab(self) -> None:
        if not self._tab:
            raise DriverError("尚未打开任何标签页（应先调用 new_project 或 open_project）")

    # ---------------------------------------------------------------- 合同实现

    def new_project(self, name: str) -> str:
        """打开 Flow 首页 → 点 New project → 轮询进入项目画布后返回项目 URL。

        name 为剧本名（用于日志；Flow 项目名由页面自动生成，不强制改名）。
        """
        data = self._run_cli("open", FLOW_HOME_URL, "--json", timeout=90.0)
        tab = data.get("tab") or data.get("tabId")
        if not tab:
            raise DriverError(
                "bb-browser open 未返回标签页 ID（Chrome 或扩展可能未就绪，请检查 daemon 状态）"
            )
        self._tab = str(tab)
        self._log(f"已打开 Flow 首页（标签页 {self._tab}），准备创建项目：{name}")

        # 页面加载有快慢，定位与点击放进轮询：找不到就随抖动间隔重试。
        def click_new_project() -> str | None:
            # 优先通过 DOM eval 点击 New project（实测 React 对快照 CDP click 无响应，DOM click 可靠触发）
            try:
                res = self._eval_json(NEW_PROJECT_DOM_CLICK_JS)
                if res == "clicked":
                    return "clicked"
            except DriverError:
                pass
            # 兜底：快照查找与 CDP click
            try:
                refs = self._snap()
            except DriverError:
                return None
            ref = self._try_find_ref(refs, LOCATORS["new_project"])
            if ref is None:
                return None
            self._click(ref)
            return ref

        self._poll(
            self._page_ready_timeout,
            click_new_project,
            timeout_error=(
                f"新建 Flow 项目超时：{self._page_ready_timeout:g} 秒内未定位到 "
                "New project 按钮（Flow 可能已改版，请校准定位表 new_project）"
            ),
        )
        self._log("已点击 New project，等待进入项目画布")

        def project_url_if_ready() -> str | None:
            data = self._run_cli("get", "url", "--tab", self._tab, "--json")
            value = data.get("value") or data.get("url")
            if isinstance(value, str) and PROJECT_URL_MARKER in value:
                return value
            return None

        url = self._poll(
            self._page_ready_timeout,
            project_url_if_ready,
            timeout_error=(
                f"新建 Flow 项目超时：{self._page_ready_timeout:g} 秒内未进入项目画布"
                f"（URL 未出现 {PROJECT_URL_MARKER}）"
            ),
        )
        self._log(f"已创建 Flow 项目：{url}")
        return str(url)

    def open_project(self, url: str) -> None:
        """打开既有项目：无标签页时新开，已有标签页时原地 goto（全程复用短 ID）。"""
        if self._tab is None:
            data = self._run_cli("open", url, "--json", timeout=90.0)
            tab = data.get("tab") or data.get("tabId")
            if not tab:
                raise DriverError(f"bb-browser open 未返回标签页 ID，无法打开项目：{url}")
            self._tab = str(tab)
        else:
            self._run_cli("goto", url, "--tab", self._tab, "--json", timeout=90.0)
        self._log(f"已打开 Flow 项目：{url}")

    def set_first_frame(self, image_path: Path) -> None:
        """首帧上传（原型实测配方，prototype-findings.md）：

        打开 Start 对话框 → Uploads 标签 → 记录网格媒体 UUID 基线 →
        base64 + DataTransfer 注入隐藏 input（**只派发 change**，双事件会双重
        上传）→ diff 出新增素材 → 点选 → Add to Prompt → 验证 Start 槽落位。
        素材 alt 是通用文案，只能按媒体 UUID diff 定位新上传。
        """
        image_path = Path(image_path)
        if not image_path.is_file():
            raise DriverError(f"首帧图片不存在：{image_path}")
        data_base64 = base64.b64encode(image_path.read_bytes()).decode("ascii")
        mime = _MIME_BY_SUFFIX.get(image_path.suffix.lower(), "application/octet-stream")

        # 1. 打开 Start 对话框（页面加载有快慢，定位与点击放进轮询）。
        def open_dialog() -> str | None:
            # 优先通过 DOM eval 点击 Start 槽（Radix UI 触发器）
            try:
                res = self._eval_json(START_SLOT_DOM_CLICK_JS)
                if res == "clicked":
                    # 验证对话框是否已成功弹出
                    refs = self._snap()
                    if self._try_find_ref(refs, LOCATORS["uploads_tab"]) is not None:
                        return "clicked"
            except DriverError:
                pass
            try:
                refs = self._snap()
            except DriverError:
                return None
            ref = self._try_find_ref(refs, LOCATORS["start_slot"])
            if ref is None:
                return None
            self._click(ref)
            return ref

        self._poll(
            self._page_ready_timeout,
            open_dialog,
            timeout_error=(
                "未定位到 Start 首帧槽（定位表条目 start_slot；"
                "Flow 可能已改版，请校准定位表或先跑冒烟脚本）"
            ),
        )

        # 2. 进入 Uploads 标签（对话框渲染有微小延迟，放入轮询等待）并记录网格基线。
        def click_uploads_tab() -> str | None:
            # 优先通过 DOM eval 点击 Uploads 标签
            try:
                res = self._eval_json(UPLOADS_TAB_DOM_CLICK_JS)
                if res == "clicked":
                    return "clicked"
            except DriverError:
                pass
            try:
                refs = self._snap()
            except DriverError:
                return None
            ref = self._try_find_ref(refs, LOCATORS["uploads_tab"])
            if ref is None:
                return None
            self._click(ref)
            return ref
            return ref

        self._poll(
            self._page_ready_timeout,
            click_uploads_tab,
            timeout_error=(
                "未定位到 Start 对话框内 Uploads 标签（定位表条目 uploads_tab；"
                "Flow 可能已改版，请校准定位表或先跑冒烟脚本）"
            ),
        )
        baseline = self._uploads_media_names()

        # 3. DataTransfer 注入（只派发 change 事件）。
        # 若 base64 较长（超过 2048 字符），按 2048 字符分片写入页内全局变量，避免 Windows cmd.exe 8191 字符限制。
        var_name = "__flowforge_upload_b64"
        if len(data_base64) > 2048:
            self._eval(f"window['{var_name}'] = '';")
            chunk_size = 2048
            for i in range(0, len(data_base64), chunk_size):
                chunk = json.dumps(data_base64[i : i + chunk_size])
                self._eval(f"window['{var_name}'] += {chunk};")
            outcome = self._eval_json(
                inject_file_from_var_js(var_name, image_path.name, mime)
            )
            self._eval(f"delete window['{var_name}'];")
        else:
            outcome = self._eval_json(
                inject_file_js(data_base64, image_path.name, mime)
            )

        if outcome != "injected":
            raise DriverError(
                f"首帧注入失败：页面未找到隐藏的图片上传 input[type=file] "
                f"（返回 {outcome}；Flow 可能已改版，请校准注入配方）"
            )

        # 4. 轮询网格 diff 出新增素材。
        def diff_new_item() -> str | None:
            new = [
                name
                for name in self._uploads_media_names()
                if name not in baseline
            ]
            return new[0] if new else None

        media_name = str(
            self._poll(
                self._upload_wait_timeout,
                diff_new_item,
                timeout_error=(
                    f"首帧上传超时：{self._upload_wait_timeout:g} 秒内新素材"
                    "未出现在 Uploads 网格（上传可能被 Flow 拒绝）"
                ),
            )
        )

        # 5. 点选新素材（diff 定位的元素无法走快照 ref，eval 点击会冒泡、React 正常响应）。
        clicked = self._eval_json(upload_click_js(media_name))
        if clicked != "clicked":
            raise DriverError(
                f"Uploads 网格点选新素材失败（返回 {clicked}；媒体 {media_name}）"
            )

        # 6. Add to Prompt（优先原生 click，DOM eval 兜底）。
        refs = self._snap()
        add_ref = self._try_find_ref(refs, LOCATORS["add_to_prompt"])
        if add_ref is not None:
            self._click(add_ref)
        try:
            self._eval_json(ADD_TO_PROMPT_DOM_CLICK_JS)
        except Exception:
            pass

        # 7. 验证 Start 槽挂上了这张图。
        self._poll(
            self._upload_wait_timeout,
            lambda: "set" if self._start_slot_media() == media_name else None,
            timeout_error=(
                f"Add to Prompt 后 Start 槽未出现新素材（期望媒体 {media_name}）"
            ),
        )
        self._log(f"首帧已挂入 Start 槽（媒体 {media_name}）")

    def clear_first_frame(self) -> None:
        """清空 Start 槽：点移除按钮并验证槽内已无媒体（防上一镜画面串镜）。"""
        refs = self._snap()
        ref = self._try_find_ref(refs, LOCATORS["start_slot_remove"])
        if ref is None:
            raise DriverError(
                "未能定位 Start 槽的移除按钮（定位表条目 start_slot_remove，"
                "冒烟校准点）：无法清空首帧，为防串镜停止本镜头"
            )
        self._click(ref)

        self._poll(
            self._upload_wait_timeout,
            lambda: "cleared" if self._start_slot_media() is None else None,
            timeout_error="清空 Start 槽超时：点击移除后槽内仍有媒体",
        )
        self._log("已清空 Start 首帧槽")

    def set_prompt(self, text: str) -> None:
        """用 Slate.js React 状态树注入提示词（同步触发 React 状态更新激活 Create 按钮），CDP fill 兜底。"""
        # 优先通过 Slate.js Fiber 节点直接注入并通知 React onChange（100% 激活 Create 按钮）
        try:
            res = self._eval_json(set_slate_prompt_js(text))
            if res == "injected":
                self._log(f"已填写提示词（{len(text)} 字，Slate 状态树同步激活）")
                return
        except DriverError:
            pass

        refs = self._snap()
        ref = self._find_ref(refs, LOCATORS["prompt_box"])
        self._fill(ref, text)
        self._log(f"已填写提示词（{len(text)} 字）")

    def configure(self, model: str, duration: int, aspect: str, outputs: int) -> None:
        """打开参数面板，逐项点选模型/时长/画幅/输出数后按 Escape 收起。"""
        display_model = MODEL_DISPLAY_NAMES.get(model)
        if display_model is None:
            raise DriverError(
                f"未知模型的页面显示名：{model}（请在定位表 MODEL_DISPLAY_NAMES 补充映射）"
            )
        display_aspect = ASPECT_DISPLAY_NAMES.get(aspect)
        if display_aspect is None:
            raise DriverError(
                f"未知画幅的页面显示名：{aspect}"
                "（请在定位表 ASPECT_DISPLAY_NAMES 补充映射）"
            )

        refs = self._snap()
        p_ref = self._find_ref(refs, LOCATORS["params_button"])
        self._click(p_ref)
        # 兜底派发 pointerdown / click 确保 Radix 菜单弹出
        try:
            self._eval(
                'var b = Array.from(document.querySelectorAll("button")).find('
                'x => (x.textContent||"").indexOf("Video ·") !== -1);'
                'if (b) { b.dispatchEvent(new PointerEvent("pointerdown", {bubbles: true})); b.click(); }'
            )
        except Exception:
            pass

        self._click_panel_option(LOCATORS["params_model_option"], display_model)
        self._click_panel_option(
            LOCATORS["params_duration_option"], duration_display(duration)
        )
        self._click_panel_option(LOCATORS["params_aspect_option"], display_aspect)
        self._click_panel_option(
            LOCATORS["params_outputs_option"], outputs_display(outputs)
        )
        self._require_tab()
        self._run_cli("press", "Escape", "--tab", self._tab, "--json")
        try:
            self._eval_json(CLOSE_MENU_ESCAPE_JS)
        except Exception:
            pass
        self._log(f"已配置参数：{model} / {duration} 秒 / {aspect} / 输出 {outputs}")

    def generate(self) -> None:
        """记录生成前媒体 UUID 基线（防幽灵产物），再点 Create 提交生成。

        真实模式下这一步开始消耗点数；编排层保证绝不自动重复提交。
        """
        self._media_baseline = set(self._media_names())
        refs = self._snap()
        ref = self._find_ref(refs, LOCATORS["create_button"])
        self._click(ref)
        # 兜底触发 React onClick 与 DOM click
        try:
            self._eval_json(CREATE_BUTTON_DOM_CLICK_JS)
        except Exception:
            pass
        self._log("已提交生成（Create）")

    def wait_for_completion(self, timeout: float) -> ClipInfo:
        """轮询生成完成：优先探测 Flow 媒体 UUID，兜底捕获网络请求或生成状态。"""
        baseline = self._media_baseline
        if baseline is None:
            baseline = set(self._media_names())
            self._media_baseline = baseline

        def first_new_media() -> str | None:
            # 1. 优先从 DOM 媒体链接提取
            new = [name for name in self._media_names() if name not in baseline]
            if new:
                return new[0]
            # 2. 从标签页网络请求日志中捕获 batchCheckAsyncVideoGenerationStatus 返回的媒体 UUID
            try:
                reqs = self._run_cli("network", "requests", "--tab", self._tab, "--filter", "batchCheckAsyncVideoGenerationStatus", "--json")
                items = reqs.get("networkRequests", [])
                for item in reversed(items):
                    body = item.get("requestBody") or ""
                    match = re.search(r'"name":"([0-9a-fA-F-]{36})"', body)
                    if match:
                        uuid = match.group(1)
                        if uuid not in baseline:
                            return uuid
            except Exception:
                pass
            return None

        clip_id = str(
            self._poll(
                timeout,
                first_new_media,
                timeout_error=(
                    f"等待生成完成超时：{timeout:g} 秒内页面未出现新产物"
                    "（媒体 UUID diff 无新增）"
                ),
            )
        )
        self._log(f"生成完成（产物媒体 {clip_id}）")
        return ClipInfo(clip_id=clip_id)

    def download_clip(self, dest_dir: Path) -> Path:
        """点下载入口（Original 720p）→ 监控 Chrome 下载目录文件落稳 →
        体积下限 + ffprobe 完整性校验（拒绝 14 字节伪视频）→ 移动到 dest_dir。"""
        dest_dir = Path(dest_dir)
        dest_dir.mkdir(parents=True, exist_ok=True)
        if not self._download_dir.is_dir():
            raise DriverError(
                f"Chrome 下载目录不存在：{self._download_dir}"
                "（可用 download_dir 参数或 STYLEFORGE_DOWNLOAD_DIR 环境变量指定）"
            )

        # 1. 尝试通过 Chrome 导航到直链直接获取或触发下载
        if self._media_baseline:
            try:
                target_uuid = list(self._media_baseline)[-1]
                url = f"https://labs.google/fx/api/trpc/media.getMediaUrlRedirect?name={target_uuid}"
                res = self._run_cli("open", url, "--json")
                dl_tab = res.get("tab") or res.get("tabId")
                time.sleep(2)
                if dl_tab:
                    try:
                        tab_info = self._run_cli("get", "url", "--tab", str(dl_tab), "--json")
                        final_url = tab_info.get("value") or tab_info.get("url")
                        if final_url and "flow-content.google" in final_url:
                            out_file = dest_dir / f"shot-{self._download_seq + 1:02d}.mp4"
                            self._download_seq += 1
                            import urllib.request
                            urllib.request.urlretrieve(final_url, out_file)
                            if out_file.is_file() and out_file.stat().st_size >= self._min_clip_bytes:
                                self._run_cli("close", "--tab", str(dl_tab), "--json")
                                self._log(f"已直接下载并归档视频：{out_file.name}（{out_file.stat().st_size} 字节）")
                                return out_file
                    except Exception:
                        pass
                    try:
                        self._run_cli("close", "--tab", str(dl_tab), "--json")
                    except Exception:
                        pass
            except Exception:
                pass

        # 2. 传统下载菜单与监控 Downloads 目录
        before = self._download_dir_entries()

        def click_download_entry() -> str | None:
            try:
                refs = self._snap()
            except DriverError:
                return None
            ref = self._try_find_ref(refs, LOCATORS["download_button"])
            if ref is None:
                return None
            self._click(ref)
            return ref

        self._poll(
            self._page_ready_timeout,
            click_download_entry,
            timeout_error=(
                "未定位到下载入口（定位表条目 download_button；"
                "可能需先悬停生成卡片——冒烟校准点，Flow 改版时更新定位表）"
            ),
        )

        def click_download_original() -> str | None:
            try:
                refs = self._snap()
            except DriverError:
                return None
            ref = self._try_find_ref(refs, LOCATORS["download_original"])
            if ref is None:
                return None
            self._click(ref)
            return ref

        self._poll(
            self._page_ready_timeout,
            click_download_original,
            timeout_error=(
                "未定位到下载菜单 Original 档（定位表条目 download_original；"
                "Flow 可能已改版，请校准定位表）"
            ),
        )

        candidate = Path(
            self._poll(
                self._download_timeout,
                lambda: self._stable_new_download(before),
                timeout_error=(
                    f"下载超时：{self._download_timeout:g} 秒内 "
                    f"{self._download_dir} 未出现落稳的新文件"
                ),
            )
        )

        size = candidate.stat().st_size
        if size < self._min_clip_bytes:
            candidate.unlink(missing_ok=True)
            raise DriverError(
                f"疑似伪视频，已拒收并删除：{candidate.name}"
                f"（{size} 字节，低于体积下限 {self._min_clip_bytes}；"
                "通常是下载失败响应文本而非视频）"
            )
        try:
            ensure_valid_video(candidate)
        except FramesError as exc:
            candidate.unlink(missing_ok=True)
            raise DriverError(f"伪视频已拒收并删除：{candidate.name}（{exc}）") from exc

        self._download_seq += 1
        dest = dest_dir / f"clip-{self._download_seq:02d}.mp4"
        shutil.move(str(candidate), str(dest))
        self._log(f"产物已下载并通过完整性校验：{dest.name}")
        return dest

    # ---------------------------------------------------------------- 页面探测辅助

    def _click_panel_option(self, locator: Locator, display_name: str) -> None:
        """在当前弹出的面板里按显示名点选一个选项（每次重新快照，防面板刷新）。"""
        refs = self._snap()
        ref = self._find_ref(refs, locator, name_override=display_name)
        self._click(ref)

    def _start_slot_media(self) -> str | None:
        """Start 槽当前挂载的媒体 UUID（未挂图返回 None）。"""
        value = self._eval_json(START_SLOT_MEDIA_JS)
        return str(value) if value else None

    def _uploads_media_names(self) -> list[str]:
        """Uploads 网格内的媒体 UUID 列表（注入基线与 diff 的数据源）。"""
        names = self._eval_json(UPLOADS_MEDIA_NAMES_JS)
        if not isinstance(names, list):
            raise DriverError("Uploads 网格探测返回异常（期望媒体 UUID 列表）")
        return [str(name) for name in names]

    def _download_dir_entries(self) -> set[str]:
        return {path.name for path in self._download_dir.iterdir() if path.is_file()}

    def _stable_new_download(self, before: set[str]) -> Path | None:
        """返回目录里新出现的、已落稳的下载文件；尚未完成返回 None。

        落稳 = 排除 .crdownload/.tmp 半成品后，隔 stable_interval 两次采样
        大小一致且非空。
        """
        entries = self._download_dir_entries()
        fresh = [
            name
            for name in entries - before
            if not name.endswith((".crdownload", ".tmp", ".download"))
        ]
        if not fresh:
            return None
        fresh.sort(key=lambda name: (self._download_dir / name).stat().st_mtime)
        candidate = self._download_dir / fresh[-1]
        if candidate.with_suffix(".crdownload").exists():
            return None  # Chrome 仍在写该文件
        size_first = candidate.stat().st_size
        if size_first <= 0:
            return None
        self._sleep(self._download_stable_interval)
        if candidate.stat().st_size != size_first:
            return None  # 仍在增长
        return candidate
