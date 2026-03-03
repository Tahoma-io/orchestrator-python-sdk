from __future__ import annotations

import asyncio
import re
from playwright.async_api import Page
import yaml

CLOSE_NAME_RE = re.compile(r"I Accept|Continue|Allow All Cookies|Accept all|accept|save|no thanks|不要", re.I)
RETRY_ERRORS = ("ERR_NETWORK_CHANGED", "ERR_CONNECTION_CLOSED", "ERR_TIMED_OUT")

import time
from typing import Iterable, Optional
from playwright.async_api import Error as PWError

async def safe_goto(page: Page, url: str, timeout_ms: int = 60000, retries: int = 3) -> Optional[object]:
    for attempt in range(retries + 1):
        try:
            return await page.goto(url, wait_until="domcontentloaded", timeout=timeout_ms)
        except PWError as e:
            msg = str(e)
            if any(k in msg for k in RETRY_ERRORS) and attempt < retries:
                await asyncio.sleep(0.8 * (attempt + 1))
                continue
            raise
    return None

def _ms_since(t0: float) -> int:
    return int((time.monotonic() - t0) * 1000)

async def wait_for_layout_stable(
        page: Page,
        *,
        timeout_ms: int = 10_000,
        quiet_ms: int = 600,
        stable_frames: int = 2,
        check_spinners: bool = True,
        spinner_selectors: Optional[Iterable[str]] = None,
        allow_websockets: bool = True,
) -> None:
    DEFAULT_SPINNER_SELECTORS = [
        "[aria-busy='true']", "[role='progressbar']", "[role='status']",
        ".spinner", ".loading", ".loader", ".progress",
        "[class*='spinner']", "[class*='loading']", "[class*='loader']",
        "[id*='spinner']", "[id*='loading']", "[id*='loader']",
        ".ant-spin", ".ant-spin-spinning", ".MuiCircularProgress-root",
    ]
    selectors = list(spinner_selectors) if spinner_selectors is not None else list(DEFAULT_SPINNER_SELECTORS)

    await page.evaluate(
        """
        (cfg) => {
          if (window.__pw_ready_installed) return;
          window.__pw_ready_installed = true;
          const state = window.__pw_ready_state = {pending: 0, lastNet: Date.now(), lastMut: Date.now(), lastSnap: null, stableOk: 0};
          try {
            const mo = new MutationObserver(() => { state.lastMut = Date.now(); });
            mo.observe(document, { subtree: true, childList: true, attributes: true, characterData: true });
          } catch (e) {}
          try {
            const origFetch = window.fetch;
            if (typeof origFetch === "function") {
              window.fetch = function(...args) {
                state.pending += 1; state.lastNet = Date.now();
                return origFetch.apply(this, args).catch((e) => { throw e; }).finally(() => { state.pending = Math.max(0, state.pending - 1); state.lastNet = Date.now(); });
              };
            }
          } catch (e) {}
          try {
            const XHR = window.XMLHttpRequest;
            if (XHR && XHR.prototype && XHR.prototype.open && XHR.prototype.send) {
              const origSend = XHR.prototype.send;
              XHR.prototype.send = function(...args) {
                state.pending += 1; state.lastNet = Date.now();
                this.addEventListener("loadend", () => { state.pending = Math.max(0, state.pending - 1); state.lastNet = Date.now(); }, { once: true });
                return origSend.apply(this, args);
              };
            }
          } catch (e) {}
          const snapshot = () => {
            const de = document.documentElement;
            if (!de) return "no-de";
            const r = de.getBoundingClientRect();
            return [Math.round(r.width), Math.round(r.height), de.scrollWidth, de.scrollHeight, window.innerWidth, window.innerHeight].join(",");
          };
          const step = () => {
            const cur = snapshot();
            if (cur === state.lastSnap) state.stableOk += 1; else state.stableOk = 0;
            state.lastSnap = cur; requestAnimationFrame(step);
          };
          requestAnimationFrame(step);
        }
        """,
        {"quiet_ms": quiet_ms, "stable_frames": stable_frames, "selectors": selectors},
    )
    await page.wait_for_function(
        """
        (cfg) => {
          const st = window.__pw_ready_state;
          if (!st) return false;
          const now = Date.now();
          const quiet = cfg.quiet_ms ?? 600;
          const needStable = cfg.stable_frames ?? 2;
          const netQuiet = (st.pending === 0) && ((now - st.lastNet) >= quiet);
          const domQuiet = (now - st.lastMut) >= quiet;
          const layoutStable = st.stableOk >= needStable;
          const isVisible = (el) => {
            if (!el) return false;
            const cs = window.getComputedStyle(el);
            if (!cs) return false;
            if (cs.display === "none" || cs.visibility === "hidden") return false;
            if (parseFloat(cs.opacity || "1") <= 0.01) return false;
            const r = el.getBoundingClientRect();
            return (r.width > 1 && r.height > 1);
          };
          let spinnerGone = true;
          if (cfg.check_spinners) {
            const sels = cfg.selectors || [];
            for (const sel of sels) {
              try {
                const nodes = document.querySelectorAll(sel);
                for (const n of nodes) { if (isVisible(n)) { spinnerGone = false; break; } }
              } catch (e) {}
              if (!spinnerGone) break;
            }
          }
          return netQuiet && domQuiet && layoutStable && spinnerGone;
        }
        """,
        arg={"quiet_ms": quiet_ms, "stable_frames": stable_frames, "check_spinners": check_spinners, "selectors": selectors, "allow_websockets": allow_websockets},
        timeout=timeout_ms,
        polling=100,
    )

async def wait_for_page_stable(
    page: Page,
    *,
    timeout_ms: int = 10_000,
    domcontentloaded_budget_ms: int = 10000,
    replay: bool = False,
    network_idle_ms: int = 10000,
    layout_stable: bool = True,
) -> None:
    start_t = time.time()
    try:
        await page.wait_for_load_state("domcontentloaded", timeout=60000)
        await page.wait_for_load_state("networkidle", timeout=network_idle_ms)
        if layout_stable:
            await wait_for_layout_stable(page, timeout_ms=6000)
    except Exception:
        print('[PlayWright Err] wait_for_load_state')
        
    mid_1 = time.time()
    print('[PlayWright 1] ', mid_1 - start_t)


def start_modal_watcher(page: Page, interval: float = 0.5) -> asyncio.Task:
    async def watcher():
        while True:
            try:
                if page.is_closed():
                    return
                for frame in page.frames:
                    dialogs = frame.get_by_role("dialog")
                    count = await dialogs.count()
                    if count == 0:
                        continue

                    for i in range(min(count, 5)):
                        dlg = dialogs.nth(i)
                        if not await dlg.is_visible():
                            continue

                        close_btn = dlg.get_by_role("button", name=CLOSE_NAME_RE).first
                        if await close_btn.count() > 0 and await close_btn.is_visible():
                            await close_btn.click(timeout=500)
                            continue

            except Exception:
                pass

            await asyncio.sleep(interval)

    return asyncio.create_task(watcher())


async def _capture_new_page(page: Page, click_coro, timeout_ms: int = 10000) -> Page | None:
    ctx = page.context
    before = set(ctx.pages)

    try:
        async with ctx.expect_page(timeout=timeout_ms) as pinfo:
            await click_coro()
        new_page = await pinfo.value
        await new_page.wait_for_load_state("domcontentloaded")
        return new_page
    except Exception:
        await asyncio.sleep(1)
        after = [p for p in ctx.pages if p not in before]
        return after[-1] if after else None


async def get_snapshot(page) -> object:
    try:
        frame = page.main_frame
        raw_yaml = await frame.locator("body").aria_snapshot(timeout=60000)
        return yaml.safe_load(raw_yaml)
    except Exception as e:
        print(f"[SNAPSHOT ERROR] {e}")
        return {}
