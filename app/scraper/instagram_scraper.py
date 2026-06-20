import asyncio
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Optional, List, Callable

from playwright.async_api import async_playwright, BrowserContext, Page, TimeoutError as PWTimeout


@dataclass
class ContentItem:
    url: str
    username: str
    content_type: str  # "post" | "story" | "reel"
    text: str = ""
    location: str = ""
    image_bytes: Optional[bytes] = None


_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/124.0.0.0 Safari/537.36"
)


class InstagramScraper:
    def __init__(self, ig_username: str, ig_password: str, state_path: str = "ig_state.json"):
        self.ig_username = ig_username
        self.ig_password = ig_password
        self.state_path = state_path

    # ------------------------------------------------------------------
    # Login
    # ------------------------------------------------------------------

    async def login(
        self,
        headless: bool = False,
        progress_cb: Optional[Callable[[str], None]] = None,
        manual_timeout: int = 420,
    ) -> tuple[bool, str]:
        """Perform Instagram login and save session state. Returns (ok, message).

        Tolerant of reCAPTCHA / bot-protection / 2FA: it best-effort fills the
        login form, then polls for the ``sessionid`` cookie for up to
        ``manual_timeout`` seconds while the user solves any challenge in the
        visible browser. The cookie is the authoritative "logged in" signal.
        """

        def _log(msg: str):
            if progress_cb:
                progress_cb(msg)

        async def _has_session() -> bool:
            cookies = await context.cookies("https://www.instagram.com")
            return any(c["name"] == "sessionid" and c.get("value") for c in cookies)

        async def _dismiss_dialogs():
            for label in ("Not now", "Not Now", "Agora não", "Ignorar", "Save info", "Salvar informações"):
                try:
                    btn = page.get_by_role("button", name=label)
                    await btn.click(timeout=1_500)
                    await page.wait_for_timeout(800)
                except Exception:
                    pass

        _log("Abrindo navegador…")
        async with async_playwright() as p:
            browser = await p.chromium.launch(
                headless=headless,
                args=["--disable-blink-features=AutomationControlled"],
            )
            context = await browser.new_context(
                viewport={"width": 1280, "height": 720},
                user_agent=_UA,
                locale="pt-BR",
                extra_http_headers={"Accept-Language": "pt-BR,pt;q=0.9,en;q=0.8"},
            )
            await context.add_init_script(
                "Object.defineProperty(navigator,'webdriver',{get:()=>undefined})"
            )
            page = await context.new_page()
            page.set_default_timeout(30_000)

            try:
                _log("Navegando para o Instagram…")
                await page.goto(
                    "https://www.instagram.com/accounts/login/",
                    wait_until="domcontentloaded",
                )
                await page.wait_for_timeout(2_000)

                # Accept cookies banner if present
                try:
                    btn = page.get_by_role("button", name="Allow all cookies")
                    await btn.click(timeout=3_000)
                    await page.wait_for_timeout(1_000)
                except Exception:
                    pass

                # Best-effort auto-fill. If the form isn't present (e.g. a
                # reCAPTCHA / bot-protection page is showing first), skip it –
                # the user will complete login manually in the window.
                try:
                    await page.fill("input[name='username']", self.ig_username, timeout=8_000)
                    await page.fill("input[name='password']", self.ig_password, timeout=4_000)
                    await page.click("button[type='submit']", timeout=4_000)
                    _log("Credenciais preenchidas, enviando…")
                    await page.wait_for_timeout(4_000)
                except Exception:
                    _log("Formulário não disponível (provável captcha/proteção).")

                if await _has_session():
                    await _dismiss_dialogs()
                    _log("Login direto bem-sucedido. Salvando sessão…")
                    await context.storage_state(path=self.state_path)
                    await browser.close()
                    return True, "Login realizado com sucesso."

                if headless:
                    await browser.close()
                    return False, (
                        "Captcha/2FA solicitado e não há janela para resolver. "
                        "Use 'Login com Navegador Visível' nas Configurações."
                    )

                # --- Manual completion window -------------------------------
                _log(
                    f"⚠ Complete o captcha / 2FA / login na janela do navegador. "
                    f"Aguardando até {manual_timeout}s…"
                )
                waited = 0
                interval = 3
                last_note = 0
                while waited < manual_timeout:
                    if await _has_session():
                        await _dismiss_dialogs()
                        await page.wait_for_timeout(1_000)
                        _log("Sessão detectada! Salvando cookies…")
                        await context.storage_state(path=self.state_path)
                        await browser.close()
                        return True, "Login realizado com sucesso."
                    await page.wait_for_timeout(interval * 1000)
                    waited += interval
                    if waited - last_note >= 30:
                        last_note = waited
                        _log(f"  …aguardando login ({waited}/{manual_timeout}s)")

                await browser.close()
                return False, (
                    f"Tempo esgotado ({manual_timeout}s) sem detectar login. "
                    "Tente novamente e conclua o captcha/2FA mais rápido, ou aumente o tempo."
                )

            except Exception as exc:
                # Even on error, salvage the session if a cookie was set.
                try:
                    if await _has_session():
                        await context.storage_state(path=self.state_path)
                        await browser.close()
                        return True, "Login realizado (apesar de aviso)."
                except Exception:
                    pass
                try:
                    await browser.close()
                except Exception:
                    pass
                return False, str(exc)

    # ------------------------------------------------------------------
    # Public entry point for scraping
    # ------------------------------------------------------------------

    async def scrape_all(
        self,
        accounts,  # List[AccountConfig]
        max_items: int = 50,
        progress_cb: Optional[Callable[[str], None]] = None,
        result_cb: Optional[Callable[[ContentItem], None]] = None,
        stop_event: Optional[threading.Event] = None,
    ) -> List[ContentItem]:
        """Open one browser context and process all accounts."""

        def _log(msg: str):
            if progress_cb:
                progress_cb(msg)

        def _stopped() -> bool:
            return stop_event is not None and stop_event.is_set()

        if not Path(self.state_path).exists():
            _log("Arquivo de sessão não encontrado. Faça login primeiro.")
            return []

        all_items: List[ContentItem] = []

        async with async_playwright() as p:
            browser = await p.chromium.launch(
                headless=True,
                args=["--disable-blink-features=AutomationControlled"],
            )
            context = await browser.new_context(
                storage_state=self.state_path,
                viewport={"width": 1600, "height": 900},
                user_agent=_UA,
                locale="pt-BR",
            )
            await context.add_init_script(
                "Object.defineProperty(navigator,'webdriver',{get:()=>undefined})"
            )
            context.set_default_timeout(30_000)

            # Block heavy resources
            async def _route(route):
                if route.request.resource_type in ("font", "media"):
                    await route.abort()
                else:
                    await route.continue_()

            await context.route("**/*", _route)

            for account in accounts:
                if _stopped():
                    break

                username = account.username
                _log(f"→ Processando @{username}")

                if account.scrape_stories:
                    _log(f"  Stories de @{username}…")
                    stories = await self._scrape_stories(context, username, _log)
                    for item in stories:
                        all_items.append(item)
                        if result_cb:
                            result_cb(item)
                    if _stopped():
                        break

                if account.scrape_posts:
                    _log(f"  Posts de @{username}…")
                    posts = await self._scrape_profile_content(
                        context, username, "post", max_items, _log
                    )
                    for item in posts:
                        all_items.append(item)
                        if result_cb:
                            result_cb(item)
                    if _stopped():
                        break

                if account.scrape_reels:
                    _log(f"  Reels de @{username}…")
                    reels = await self._scrape_profile_content(
                        context, username, "reel", max_items, _log
                    )
                    for item in reels:
                        all_items.append(item)
                        if result_cb:
                            result_cb(item)

            await browser.close()

        _log(f"Coleta concluída. {len(all_items)} itens no total.")
        return all_items

    # ------------------------------------------------------------------
    # Stories
    # ------------------------------------------------------------------

    async def _scrape_stories(
        self,
        context: BrowserContext,
        username: str,
        log: Callable,
    ) -> List[ContentItem]:
        page = await context.new_page()
        items: List[ContentItem] = []

        try:
            await page.goto(
                f"https://www.instagram.com/stories/{username}/",
                wait_until="domcontentloaded",
                timeout=20_000,
            )
            await page.wait_for_timeout(2_500)

            if "/stories/" not in page.url or username not in page.url:
                log(f"  Nenhuma story ativa para @{username}")
                return items

            seen: set = set()
            for _ in range(20):
                url = page.url
                if url in seen or username not in url:
                    break
                seen.add(url)

                img_bytes = await page.screenshot(type="jpeg", quality=70)

                text = await page.evaluate(
                    """() => {
                        const els = document.querySelectorAll(
                            'span[style*="font"], div[class*="story"] span, '
                            'div[style*="text-align"] span'
                        );
                        return [...els].map(e => e.innerText.trim()).filter(Boolean).join(' ');
                    }"""
                )

                items.append(
                    ContentItem(
                        url=url,
                        username=username,
                        content_type="story",
                        text=(text or "").strip(),
                        image_bytes=img_bytes,
                    )
                )
                log(f"  Frame {len(items)} capturado")

                await page.keyboard.press("ArrowRight")
                await page.wait_for_timeout(1_500)

                if page.is_closed():
                    break

        except Exception as exc:
            log(f"  Erro em stories de @{username}: {exc}")
        finally:
            if not page.is_closed():
                await page.close()

        return items

    # ------------------------------------------------------------------
    # Posts & Reels (shared logic)
    # ------------------------------------------------------------------

    async def _scrape_profile_content(
        self,
        context: BrowserContext,
        username: str,
        content_type: str,  # "post" | "reel"
        max_items: int,
        log: Callable,
    ) -> List[ContentItem]:
        if content_type == "reel":
            profile_url = f"https://www.instagram.com/{username}/reels/"
            link_pattern = "/reel/"
        else:
            profile_url = f"https://www.instagram.com/{username}/"
            link_pattern = "/p/"

        # --- collect links ---
        grid_page = await context.new_page()
        links: set = set()
        try:
            await grid_page.goto(profile_url, wait_until="domcontentloaded", timeout=20_000)
            await grid_page.wait_for_timeout(2_000)

            scrolls = max(3, max_items // 12 + 2)
            for _ in range(scrolls):
                anchors = await grid_page.query_selector_all(
                    f"a[href*='{link_pattern}']"
                )
                for a in anchors:
                    href = await a.get_attribute("href") or ""
                    if link_pattern in href:
                        links.add(
                            "https://www.instagram.com" + href.split("?")[0]
                        )
                if len(links) >= max_items:
                    break
                await grid_page.evaluate(
                    "window.scrollTo(0, document.body.scrollHeight)"
                )
                await grid_page.wait_for_timeout(2_500)
        except Exception as exc:
            log(f"  Erro coletando links de @{username}: {exc}")
        finally:
            await grid_page.close()

        links_list = list(links)[:max_items]
        log(f"  {len(links_list)} links encontrados para @{username}")

        # --- visit each item ---
        items: List[ContentItem] = []
        detail_page = await context.new_page()
        try:
            for url in links_list:
                try:
                    item = await self._extract_item(
                        detail_page, url, username, content_type
                    )
                    if item:
                        items.append(item)
                        log(f"  OK: {url.split('instagram.com')[1]}")
                except Exception as exc:
                    log(f"  Falha: {url.split('instagram.com')[1]} – {exc}")
                await asyncio.sleep(0.8)
        finally:
            await detail_page.close()

        return items

    async def _extract_item(
        self,
        page: Page,
        url: str,
        username: str,
        content_type: str,
    ) -> Optional[ContentItem]:
        await page.goto(url, wait_until="domcontentloaded", timeout=25_000)
        await page.wait_for_timeout(1_500)

        # Caption via og:description (most reliable)
        text = await page.evaluate(
            """() => {
                const og = document.querySelector('meta[property="og:description"]');
                if (og) return og.getAttribute('content') || '';
                const h1s = document.querySelectorAll('article h1, main h1');
                for (const h of h1s) {
                    if (h.innerText.trim()) return h.innerText.trim();
                }
                return '';
            }"""
        )

        # Location link
        location = await page.evaluate(
            """() => {
                const el = document.querySelector('a[href*="/explore/locations/"]');
                return el ? el.innerText.trim() : '';
            }"""
        )

        # Image/video screenshot
        img_bytes: Optional[bytes] = None
        try:
            el = await page.query_selector("article img[alt]")
            if el is None:
                el = await page.query_selector("video")
            if el:
                img_bytes = await el.screenshot(type="jpeg", quality=70)
        except Exception:
            pass

        return ContentItem(
            url=url,
            username=username,
            content_type=content_type,
            text=(text or "").strip(),
            location=(location or "").strip(),
            image_bytes=img_bytes,
        )
