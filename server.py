import asyncio
import re
from pathlib import Path

from mcp.server.fastmcp import FastMCP
from playwright.async_api import Error as PlaywrightError
from playwright.async_api import async_playwright


mcp = FastMCP("mioria-easy1")
_playwright = None
_context = None
_page = None


async def _reset_browser() -> None:
    global _playwright, _context, _page

    _page = None
    if _context is not None:
        try:
            await _context.close()
        except PlaywrightError:
            pass
    _context = None
    if _playwright is not None:
        try:
            await _playwright.stop()
        except PlaywrightError:
            pass
    _playwright = None


async def _launch_browser() -> None:
    global _playwright, _context

    _playwright = await async_playwright().start()
    profile_path = Path(__file__).parent / ".mioria-browser-profile"
    _context = await _playwright.chromium.launch_persistent_context(
        str(profile_path),
        channel="msedge",
        headless=False,
    )


def is_bilibili_topic(question: str) -> bool:
    markers = ("B站", "哔哩哔哩", "Bilibili", "UP主", "弹幕", "番剧", "BV")
    normalized_question = question.casefold()

    return any(
        marker.casefold() in normalized_question
        for marker in markers
    )


async def _message_box_is_ready(page, timeout_ms: int = 2_000) -> bool:
    """确认页面真的可以输入，而不是仅仅 URL 看起来正确。"""
    try:
        if page.is_closed():
            return False
        message_box = page.get_by_placeholder("在此输入您的消息...")
        await message_box.wait_for(state="visible", timeout=timeout_ms)
        return await message_box.is_enabled()
    except PlaywrightError:
        return False


async def _open_or_refresh_chat_page(page):
    """恢复旧页面；中断请求后页面可能停在不可输入的旧状态。"""
    try:
        if page.is_closed():
            return None
        if await _message_box_is_ready(page):
            return True

        await page.goto(
            "https://chat.mioria.ai/",
            wait_until="domcontentloaded",
            timeout=30_000,
        )
        return await _message_box_is_ready(page, timeout_ms=5_000)
    except PlaywrightError:
        return False


async def _get_chat_page():
    global _playwright, _context, _page

    candidates = []
    if _page is not None and not _page.is_closed():
        candidates.append(_page)

    if _context is not None:
        try:
            candidates.extend(
                page for page in _context.pages
                if page not in candidates and "chat.mioria.ai" in page.url
            )
        except PlaywrightError:
            await _reset_browser()
            candidates = []

    for candidate in candidates:
        ready_page = await _open_or_refresh_chat_page(candidate)
        if ready_page:
            _page = candidate
            return _page

    if _playwright is None or _context is None:
        await _launch_browser()

    try:
        page = await _context.new_page()
    except PlaywrightError:
        await _reset_browser()
        await _launch_browser()
        page = await _context.new_page()

    try:
        await page.goto(
            "https://chat.mioria.ai/",
            wait_until="domcontentloaded",
            timeout=30_000,
        )
    except PlaywrightError:
        if not page.url.startswith("https://chat.mioria.ai"):
            raise
    _page = page
    return _page


def _question_variants(question: str) -> tuple[str, ...]:
    """返回页面可能显示的几种问题文本形式。"""
    unescaped = question.replace(r"\&", "&")
    rendered_markdown = re.sub(
        r"\[([^\]]+)\]\([^)]*\)",
        r"\1",
        unescaped,
    )
    variants = []
    for value in (question, unescaped, rendered_markdown):
        value = re.sub(r"\s+", " ", value).strip()
        if value and value not in variants:
            variants.append(value)

    title_match = re.search(r"【[^】]+】", unescaped)
    url_match = re.search(r"https?://\S+", unescaped)
    if title_match:
        title = title_match.group(0)
        if url_match:
            title_and_url = f"{title} {url_match.group(0).rstrip(')]')}"
            if title_and_url not in variants:
                variants.append(title_and_url)
        if title not in variants:
            variants.append(title)
    return tuple(variants)


def _find_question_position(visible_text: str, question: str) -> tuple[int, int]:
    """兼容 Markdown、换行和 Mioria 页面拆分显示问题文本的情况。"""
    for variant in _question_variants(question):
        position = visible_text.rfind(variant)
        if position >= 0:
            return position, len(variant)
        whitespace_pattern = re.escape(variant).replace(r"\ ", r"\s+")
        matches = list(re.finditer(whitespace_pattern, visible_text))
        if matches:
            match = matches[-1]
            return match.start(), match.end() - match.start()
    return -1, 0


def _clean_answer_prefix(answer: str) -> str:
    """去掉只在页面渲染中出现在问题后面的标题链接或视频 URL。"""
    answer = answer.strip()
    while True:
        cleaned = re.sub(
            r"^(?:https?://\S+|\[[^\]]+\]\([^)]*\))\s*",
            "",
            answer,
        )
        if cleaned == answer:
            return answer
        answer = cleaned


def _extract_latest_answer(visible_text: str, question: str) -> str:
    question_position, question_length = _find_question_position(
        visible_text,
        question,
    )
    if question_position < 0:
        return ""

    answer = visible_text[question_position + question_length:].strip()
    for end_marker in ("大语言模型可能会犯错。请核实重要信息。",):
        answer = answer.split(end_marker, 1)[0].strip()
    for status_line in ("已使用", "读取视频信息", "读取字幕"):
        answer = answer.replace(status_line, "")
    return _clean_answer_prefix(answer)


def _extract_answer_before_tool_status(visible_text: str, question: str) -> str:
    question_position, question_length = _find_question_position(
        visible_text,
        question,
    )
    if question_position < 0:
        return ""

    answer = visible_text[question_position + question_length:].strip()
    for status_line in (
        "已使用",
        "读取视频信息",
        "读取字幕",
        "转写视频音频（无字幕视频较慢，请耐心等待）",
        "转写视频音频",
    ):
        answer = answer.replace(status_line, "")
    answer = answer.replace("大语言模型可能会犯错。请核实重要信息。", "")
    return _clean_answer_prefix(answer)


async def _get_message_buttons(page):
    message_box = page.get_by_placeholder("在此输入您的消息...")
    button_candidates = (
        message_box.locator("xpath=ancestor::form[1]//button"),
        message_box.locator("xpath=ancestor::div[1]//button"),
        message_box.locator("xpath=ancestor::div[2]//button"),
        message_box.locator("xpath=ancestor::div[3]//button"),
    )
    for candidate in button_candidates:
        if await candidate.count():
            visible_buttons = []
            for index in range(await candidate.count()):
                button = candidate.nth(index)
                if await button.is_visible():
                    visible_buttons.append(button)
            if visible_buttons:
                return visible_buttons
    return []


async def _get_message_button(page):
    visible_buttons = await _get_message_buttons(page)
    if not visible_buttons:
        return None
    return visible_buttons[-1]


async def _get_button_signature(page):
    button = await _get_message_button(page)
    if button is None:
        return None
    return await button.evaluate(
        """(element) => ({
            className: typeof element.className === 'string' ? element.className : '',
            disabled: element.disabled,
            text: element.innerText || '',
            ariaLabel: element.getAttribute('aria-label') || '',
            title: element.getAttribute('title') || '',
            dataTooltip: element.getAttribute('data-tooltip') || '',
            html: element.innerHTML
        })"""
    )


def _has_substantive_answer(visible_text: str, question: str, answer: str) -> bool:
    question_position, question_length = _find_question_position(
        visible_text,
        question,
    )
    if question_position < 0:
        return False

    latest_segment = visible_text[question_position + question_length:]
    processing_markers = (
        "查询中",
        "正在查询",
        "查询视频",
        "读取视频",
        "读取字幕",
        "转写视频音频",
        "正在转写",
        "请耐心等待",
        "正在思考",
        "思考中",
        "生成中",
        "处理中",
    )
    if any(marker in latest_segment for marker in processing_markers):
        return False

    normalized_answer = answer.replace("\n", "").replace(" ", "")
    if not normalized_answer or normalized_answer.startswith("http"):
        return False
    if re.fullmatch(r"https?://\S+", normalized_answer):
        return False
    return len(normalized_answer) >= 40


async def _read_stable_visible_text(page, timeout_seconds: float = 120) -> str:
    """等待页面文本停止变化，覆盖 Mioria 的流式输出过程。"""
    deadline = asyncio.get_running_loop().time() + timeout_seconds
    previous_text = ""
    stable_rounds = 0

    while asyncio.get_running_loop().time() < deadline:
        current_text = (await page.locator("body").inner_text(timeout=30_000)).strip()
        if current_text and current_text == previous_text:
            stable_rounds += 1
            if stable_rounds >= 3:
                return current_text
        else:
            stable_rounds = 0
            previous_text = current_text
        await asyncio.sleep(2)

    return previous_text


async def _wait_for_message_box(page, timeout_seconds: float = 30):
    message_box = page.get_by_placeholder("在此输入您的消息...")
    try:
        await message_box.wait_for(state="visible", timeout=timeout_seconds * 1000)
    except PlaywrightError:
        return None
    return message_box


async def _wait_for_answer(
    page,
    question: str,
    initial_button_signature,
) -> str:
    """按发送按钮的箭头→方形→箭头状态等待完整回答。"""
    deadline = asyncio.get_running_loop().time() + 300
    generation_signature = None
    candidate_signature = None
    candidate_rounds = 0

    # 第一阶段：等待发送按钮从“箭头”变成“方形停止按钮”。
    while asyncio.get_running_loop().time() < deadline:
        current_signature = await _get_button_signature(page)
        if (
            current_signature is not None
            and current_signature != initial_button_signature
        ):
            if current_signature == candidate_signature:
                candidate_rounds += 1
            else:
                candidate_signature = current_signature
                candidate_rounds = 1
            if candidate_rounds >= 2:
                generation_signature = current_signature
                break
        else:
            candidate_signature = None
            candidate_rounds = 0
        await asyncio.sleep(1)

    if generation_signature is None:
        return "问题可能已经发送，但没有检测到停止输出按钮。"

    # 第二阶段：等待停止输出按钮变回发送箭头。
    candidate_signature = None
    candidate_rounds = 0
    while asyncio.get_running_loop().time() < deadline:
        current_signature = await _get_button_signature(page)
        if current_signature == initial_button_signature:
            if current_signature == candidate_signature:
                candidate_rounds += 1
            else:
                candidate_signature = current_signature
                candidate_rounds = 1

            if candidate_rounds >= 2:
                # 按钮已经确认回到发送状态，再读取当前问题之后的正文。
                for _ in range(10):
                    visible_text = (
                        await page.locator("body").inner_text(timeout=5_000)
                    ).strip()
                    answer = _extract_answer_before_tool_status(
                        visible_text,
                        question,
                    )
                    if not answer:
                        answer = _extract_latest_answer(visible_text, question)
                    if answer and not re.fullmatch(r"https?://\S+", answer):
                        return answer
                    await asyncio.sleep(0.5)
                return "检测到回答已结束，但没有提取到当前问题的正文。"
        else:
            candidate_signature = None
            candidate_rounds = 0
        await asyncio.sleep(1)

    return "已检测到问题发送，但等待停止输出按钮恢复发送状态超时。"


async def _submit_question(page, message_box, question: str):
    await message_box.fill(question)
    initial_button_signature = await _get_button_signature(page)
    button_candidates = (
        message_box.locator("xpath=ancestor::form[1]//button"),
        message_box.locator("xpath=ancestor::div[1]//button"),
        message_box.locator("xpath=ancestor::div[2]//button"),
        message_box.locator("xpath=ancestor::div[3]//button"),
    )

    for candidate in button_candidates:
        for index in range(await candidate.count() - 1, -1, -1):
            button = candidate.nth(index)
            if not await button.is_visible():
                continue
            try:
                await button.wait_for(state="visible", timeout=2_000)
                await button.click(timeout=5_000)
                break
            except PlaywrightError:
                continue
        else:
            continue
        break

    else:
        await message_box.press("Enter")

    for _ in range(10):
        await page.wait_for_timeout(500)
        body_text = await page.locator("body").inner_text(timeout=5_000)
        if question in body_text:
            return initial_button_signature
        try:
            if not (await message_box.input_value()).strip():
                return initial_button_signature
        except PlaywrightError:
            return initial_button_signature
    return initial_button_signature


@mcp.tool()
async def ask_mioria_for_bilibili(
    question: str,
    attachment_text: str = "",
) -> str:
    """把 B 站问题和可选附件文本发送到已登录的 Mioria.ai 浏览器页面。"""
    if not question.strip():
        return "问题不能为空。"

    full_question = question.strip()
    if attachment_text.strip():
        full_question += f"\n\n附件内容：\n{attachment_text.strip()}"

    if not is_bilibili_topic(full_question):
        return "这个工具只处理 B 站相关问题。"

    try:
        page = await _get_chat_page()
        message_box = await _wait_for_message_box(page)
        if message_box is None:
            return "等待登录超时。请确认已登录 Mioria.ai 后重试。"

        initial_button_signature = await _submit_question(
            page,
            message_box,
            full_question,
        )
        return await _wait_for_answer(page, full_question, initial_button_signature)
    except PlaywrightError as error:
        return f"浏览器操作失败：{error}"






if __name__ == "__main__":
    mcp.run(transport="stdio")

