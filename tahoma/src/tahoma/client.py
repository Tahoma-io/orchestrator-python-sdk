import asyncio
from typing import Any, Optional
from pathlib import Path
import shortuuid
from haikunator import Haikunator
from enum import Enum
from dataclasses import dataclass
import io
from PIL import Image
from playwright.async_api import async_playwright, Browser, BrowserContext, Page as AsyncPage, Dialog

from .utils import start_modal_watcher, safe_goto, wait_for_page_stable, _capture_new_page
from .db import send_to_log
from .storage import upload_screenshot

class ActionType(str, Enum):
    CLICK = "click"
    INPUT = "input"
    SELECT = "select"
    PRESS_ENTER = "press_enter"
    GOTO = "goto"

@dataclass(frozen=False)
class ActionStep:
    action: ActionType
    index: int = 0
    role: str = ""
    name: str = ""
    nth: int = 0
    role_nth: int = 0
    text: Optional[str] = ""
    node_id: Optional[str] = ""


class Page:
    """High-level wrapper for Playwright Page."""
    
    def __init__(self, pw_page: AsyncPage, context_id: str, modal_blocker: bool = True):
        self._page = pw_page
        self._modal_blocker = modal_blocker
        self.context_id = context_id
        self.id = shortuuid.uuid()[:8]
        self._tasks = set()

    def _track_task(self, coro):
        task = asyncio.create_task(coro)
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)

    async def _init(self):
        if self._modal_blocker:
            async def handle_js_dialog(d: Dialog):
                print("js dialog:", d.type, d.message)
                await d.accept()

            self._page.on("dialog", handle_js_dialog)
            self._page._modal_watcher_task = start_modal_watcher(self._page)
            
    async def goto(self, url: str, **kwargs):
        self._track_task(send_to_log('goto', self.context_id, self.id, {'url': url}))
        return await self._page.goto(url, **kwargs)

    async def wait(self, **kwargs):
        self._track_task(send_to_log('wait', self.context_id, self.id, kwargs))
        await wait_for_page_stable(self._page, **kwargs)

    async def screenshot(self, **kwargs) -> Image.Image:
        img_bytes = await self._page.screenshot(**kwargs)
        img = Image.open(io.BytesIO(img_bytes))
        
        # Upload screenshot and log with S3 URL
        try:
            s3_url = upload_screenshot(img, prefix="page")
            self._track_task(send_to_log('screenshot', self.context_id, self.id, kwargs, s3_url=s3_url))
        except Exception as e:
            print(f"⚠️ Screenshot upload failed: {e}")
            self._track_task(send_to_log('screenshot', self.context_id, self.id, kwargs))
            
        return img
        
    async def title(self):
        return await self._page.title()

    @staticmethod
    async def apply_step(page: 'Page', step: ActionStep, replay: bool) -> 'Page':
        frame = page._page.main_frame
        if step.action == ActionType.GOTO:
            if page._page.url != step.text and step.text is not None:
                await safe_goto(page._page, step.text)
                page._track_task(send_to_log('wait', page.context_id, page.id, {'type': 'page_stable'}))
                await wait_for_page_stable(page._page, replay=replay)
            return page

        if step.action == ActionType.PRESS_ENTER:
            page._track_task(send_to_log('keypress', page.context_id, page.id, {'key': 'Enter'}))
            await page._page.keyboard.press("Enter")
            page._track_task(send_to_log('wait', page.context_id, page.id, {'type': 'page_stable'}))
            await wait_for_page_stable(page._page, replay=replay)
            return page

        loc_with_name = None
        if step.name:
            loc_with_name = frame.get_by_role(step.role, name=step.name, exact=True).nth(step.nth)

        loc_role_only = frame.get_by_role(step.role).nth(step.role_nth)

        async def _do(loc, current_page: 'Page', replay: bool, name: bool = True) -> 'Page':
            if step.action == ActionType.CLICK:
                current_page._track_task(send_to_log('click', current_page.context_id, current_page.id, {'role': step.role, 'name': step.name, 'use_name': name}))
                if name:
                    try:
                        async def _click():
                            await loc.scroll_into_view_if_needed(timeout=5000)
                            box = await loc.bounding_box()
                            if not box:
                                raise Exception("[CLICK ERR] element's bounding_box is None: ")
                            await current_page._page.mouse.click(box["x"] + box["width"] / 2, box["y"] + box["height"] / 2)

                        newp = await _capture_new_page(current_page._page, _click, timeout_ms=10000)
                        if newp is not None:
                            current_page = Page(newp, context_id=current_page.context_id, modal_blocker=current_page._modal_blocker)
                            await current_page._init()
                            current_page._track_task(send_to_log('new_page', current_page.context_id, current_page.id, {"modal_blocker": current_page._modal_blocker}))
                    except Exception as e:
                        print("[CLICK ERR] try1: ", e)
                        raise Exception("[CLICK ERR] ", e)
                else:
                    try:
                        async def _click():
                            await loc.scroll_into_view_if_needed(timeout=3000)
                            box = await loc.bounding_box()
                            if not box:
                                raise Exception("bounding_box is None")
                            await current_page._page.mouse.click(box["x"] + box["width"] / 2, box["y"] + box["height"] / 2)

                        newp = await _capture_new_page(current_page._page, _click, timeout_ms=10000)
                        if newp is not None:
                            current_page = Page(newp, context_id=current_page.context_id, modal_blocker=current_page._modal_blocker)
                            await current_page._init()
                            current_page._track_task(send_to_log('new_page', current_page.context_id, current_page.id, {"modal_blocker": current_page._modal_blocker}))
                    except Exception as e:
                        print("[CLICK ERR] try2: ", e)
                        raise Exception("[CLICK ERR] ", e)

            elif step.action == ActionType.INPUT:
                if step.role == "textbox" and step.name == "URL" and step.index==11:
                    print("no filling...")
                    return current_page
                
                await loc.focus()
                current_page._track_task(send_to_log('wait', current_page.context_id, current_page.id, {'type': 'page_stable'}))
                await wait_for_page_stable(current_page._page, replay=replay)

                current_page._track_task(send_to_log('input', current_page.context_id, current_page.id, {'action': 'clear'}))
                await current_page._page.keyboard.press('Control+A')
                await current_page._page.keyboard.press('Backspace')

                current_page._track_task(send_to_log('wait', current_page.context_id, current_page.id, {'type': 'page_stable'}))
                await wait_for_page_stable(current_page._page, replay=replay)

                if step.text is not None and step.text != "":
                    current_page._track_task(send_to_log('input', current_page.context_id, current_page.id, {'text': step.text}))
                    for char in step.text:
                        try:
                            await current_page._page.keyboard.press(char)
                            await current_page._page.wait_for_timeout(50)
                        except Exception as e:
                            print("[PLAYWRIGHT ERR] TYPE: ", char)

            elif step.action == ActionType.SELECT:
                try:
                    if step.text:
                        current_page._track_task(send_to_log('select', current_page.context_id, current_page.id, {'text': step.text}))
                        await loc.select_option(step.text, timeout=3000)
                except Exception as e:
                    raise Exception(
                        f"{loc} cannot be select as {step.text}, if the option visible, you should use click instead. Detailed failure reason: {e}.")

            else:
                raise ValueError(f"Unknown action: {step.action}")

            return current_page

        if loc_with_name is not None:
            try:
                page = await _do(loc_with_name, page, replay)
                if step.action and step.action != ActionType.INPUT:
                    page._track_task(send_to_log('wait', page.context_id, page.id, {'type': 'page_stable'}))
                    await wait_for_page_stable(page._page, replay=replay)
                return page
            except Exception as e:
                print("[EXEC LOC FALLBACK] with_name failed, fallback to role_only. ", e)
                if replay:
                    return page

        page = await _do(loc_role_only, page, replay, name=False)
        page._track_task(send_to_log('wait', page.context_id, page.id, {'type': 'page_stable'}))
        await wait_for_page_stable(page._page, replay=replay)
        return page


class Context:
    """High-level wrapper for Playwright BrowserContext."""
    
    def __init__(self, browser: Browser):
        self._browser = browser
        self._context: Optional[BrowserContext] = None
        self.id = Haikunator().haikunate()
        self._tasks = set()

    def _track_task(self, coro):
        task = asyncio.create_task(coro)
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)

    async def start(self):
        self._context = self._browser.contexts[0] if self._browser.contexts else await self._browser.new_context()
        self._track_task(send_to_log('context_start', self.id))

    async def stop(self):
        if self._context is not None:
            self._track_task(send_to_log('context_stop', self.id))
            await self._context.close()
            self._context = None
            
    def info(self):
        if self._context is None:
            return None
        return {
            "pages": len(self._context.pages),
        }

    async def new_page(self, modal_blocker: bool = True) -> Page:
        if self._context is None:
            raise RuntimeError("Context not started.")
        pw_page = await self._context.new_page()
        page = Page(pw_page, context_id=self.id, modal_blocker=modal_blocker)
        await page._init()
        self._track_task(send_to_log('new_page', self.id, page.id, {"modal_blocker": modal_blocker}))
        return page


class Tahoma:
    """
    Minimal Playwright wrapper that connects to an existing browser session:
    - connects to context
    - open a page and navigate
    """

    def __init__(self, session_url: str) -> None:
        self.session_url = session_url
        self._pw = None
        self._browser: Optional[Browser] = None
        self.context: Optional[Context] = None

    async def start(self) -> None:
        self._pw = await async_playwright().start()
        # Connect to an existing playwright session.
        self._browser = await self._pw.chromium.connect(self.session_url)
        # Typehint resolution for pyre2
        assert self._browser is not None
        self.context = Context(self._browser)
        await self.context.start()

    @property
    def pw(self):
        return self._pw

    @property
    def browser(self) -> Optional[Browser]:
        return self._browser

    async def new_page(self, modal_blocker: bool = True) -> Page:
        if self.context is None:
            raise RuntimeError("Session not started. Please call start() first.")
        return await self.context.new_page(modal_blocker=modal_blocker)

    async def close(self) -> None:
        if self.context is not None:
            await self.context.stop()
            # Drain outstanding log tasks
            if self.context._tasks:
                await asyncio.gather(*self.context._tasks, return_exceptions=True)
            self.context = None
        if self._browser is not None:
            await self._browser.close()
            self._browser = None
        if self._pw is not None:
            await self._pw.stop()
            self._pw = None
