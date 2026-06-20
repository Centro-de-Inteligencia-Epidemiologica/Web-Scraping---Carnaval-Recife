import asyncio
import threading

from PyQt6.QtCore import QThread, pyqtSignal

from app.config import Config
from app.llm.analyzer import LLMAnalyzer, AnalysisResult
from app.scraper.instagram_scraper import InstagramScraper, ContentItem


class LoginWorker(QThread):
    """Runs Instagram login in background."""

    progress = pyqtSignal(str)
    finished = pyqtSignal(bool, str)  # (success, message)

    def __init__(self, config: Config, headless: bool = False):
        super().__init__()
        self.config = config
        self.headless = headless

    def run(self):
        scraper = InstagramScraper(
            self.config.ig_username,
            self.config.ig_password,
            self.config.state_path,
        )

        async def _do():
            ok, msg = await scraper.login(
                headless=self.headless,
                progress_cb=lambda m: self.progress.emit(m),
            )
            self.finished.emit(ok, msg)

        asyncio.run(_do())


class ScrapeWorker(QThread):
    """Runs scraping + LLM analysis in background."""

    progress = pyqtSignal(str)
    result_ready = pyqtSignal(object)   # AnalysisResult
    raw_item = pyqtSignal(object)       # ContentItem (before analysis)
    scan_finished = pyqtSignal()
    error = pyqtSignal(str)

    def __init__(self, config: Config):
        super().__init__()
        self.config = config
        self._stop_event = threading.Event()

    def stop(self):
        self._stop_event.set()

    def run(self):
        try:
            asyncio.run(self._run_async())
        except Exception as exc:
            self.error.emit(str(exc))
        finally:
            self.scan_finished.emit()

    async def _run_async(self):
        cfg = self.config
        scraper = InstagramScraper(cfg.ig_username, cfg.ig_password, cfg.state_path)
        analyzer = LLMAnalyzer(cfg.ollama_endpoint, cfg.text_model, cfg.vision_model)

        # We collect items via callback to enable streaming results
        items_queue: list[ContentItem] = []

        def _raw_cb(item: ContentItem):
            items_queue.append(item)

        await scraper.scrape_all(
            accounts=cfg.accounts,
            max_items=cfg.max_posts_per_account,
            progress_cb=lambda m: self.progress.emit(m),
            result_cb=_raw_cb,
            stop_event=self._stop_event,
        )

        self.progress.emit(f"Analisando {len(items_queue)} itens com LLM…")

        for item in items_queue:
            if self._stop_event.is_set():
                break

            self.raw_item.emit(item)

            # Quick keyword filter (skip LLM if not analyze_all and no keyword hit)
            if not cfg.analyze_all:
                text_lower = (item.text or "").lower()
                has_kw = any(k.lower() in text_lower for k in cfg.keywords)
                has_img = item.image_bytes is not None
                if not has_kw and not has_img:
                    continue

            self.progress.emit(
                f"LLM ← {item.content_type} de @{item.author}"
            )
            result = analyzer.analyze(item, cfg.keywords)

            if result and result.probability >= cfg.min_probability:
                self.result_ready.emit(result)

        self.progress.emit("Varredura concluída.")
