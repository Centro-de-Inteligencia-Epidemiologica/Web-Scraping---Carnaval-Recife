import base64
import json
import requests
from dataclasses import dataclass, field
from typing import List, Optional

from app.scraper.instagram_scraper import ContentItem


@dataclass
class AnalysisResult:
    username: str
    city: str
    text: str
    reason: str          # max 100 chars
    probability: int     # 1-4
    content_type: str
    url: str
    matched_keywords: List[str] = field(default_factory=list)


_TEXT_SYSTEM = """\
Você é um sistema de vigilância epidemiológica analisando publicações de redes sociais.
Palavras-chave monitoradas: {keywords}

Analise o conteúdo e retorne APENAS JSON:
{{
  "city": "cidade mencionada ou string vazia",
  "is_relevant": true ou false,
  "reason": "motivo breve máx 100 caracteres",
  "probability": número de 1 a 4 onde:
    1 = sem relevância epidemiológica
    2 = possível relevância
    3 = provável relevância
    4 = alta relevância (surto, massa de doentes, contaminação clara)
}}"""

_VISION_PROMPT = """\
Você é um analista de vigilância epidemiológica.
Analise esta imagem de uma publicação do Instagram.
Palavras-chave monitoradas: {keywords}
Legenda/texto da publicação: {text}

Procure sinais de: sintomas em grupos, doença, contaminação, hospital lotado, etc.
Retorne APENAS JSON:
{{
  "city": "cidade se visível ou mencionada",
  "is_relevant": true ou false,
  "reason": "motivo breve máx 100 caracteres",
  "probability": 1 a 4
}}"""


class LLMAnalyzer:
    def __init__(self, ollama_endpoint: str, text_model: str, vision_model: str):
        self.endpoint = ollama_endpoint.rstrip("/")
        self.text_model = text_model
        self.vision_model = vision_model

    # ------------------------------------------------------------------
    # Connection test
    # ------------------------------------------------------------------

    def test_connection(self) -> tuple[bool, str]:
        try:
            r = requests.get(f"{self.endpoint}/api/tags", timeout=5)
            r.raise_for_status()
            models = [m["name"] for m in r.json().get("models", [])]
            return True, "Conectado. Modelos: " + ", ".join(models[:6])
        except Exception as exc:
            return False, str(exc)

    # ------------------------------------------------------------------
    # Main analysis entry
    # ------------------------------------------------------------------

    def analyze(
        self,
        item: ContentItem,
        keywords: List[str],
    ) -> Optional[AnalysisResult]:
        kw_str = ", ".join(keywords)

        if item.image_bytes:
            raw = self._analyze_vision(item, kw_str)
            if raw is None:
                raw = self._analyze_text(item, kw_str)
        else:
            raw = self._analyze_text(item, kw_str)

        if raw is None:
            return None

        matched = [
            k for k in keywords if k.lower() in (item.text or "").lower()
        ]

        reason = (raw.get("reason") or "")[:100]
        prob = max(1, min(4, int(raw.get("probability") or 1)))

        return AnalysisResult(
            username=item.username,
            city=raw.get("city") or item.location or "",
            text=item.text or "",
            reason=reason,
            probability=prob,
            content_type=item.content_type,
            url=item.url,
            matched_keywords=matched,
        )

    # ------------------------------------------------------------------
    # Text-only call
    # ------------------------------------------------------------------

    def _analyze_text(self, item: ContentItem, kw_str: str) -> Optional[dict]:
        system = _TEXT_SYSTEM.format(keywords=kw_str)
        user_msg = (
            f"Local: {item.location or 'desconhecido'}\n"
            f"Texto: {(item.text or '')[:2000]}"
        )
        payload = {
            "model": self.text_model,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user_msg},
            ],
            "stream": False,
            "format": "json",
            "options": {"temperature": 0.0},
        }
        return self._call(payload)

    # ------------------------------------------------------------------
    # Vision call
    # ------------------------------------------------------------------

    def _analyze_vision(self, item: ContentItem, kw_str: str) -> Optional[dict]:
        img_b64 = base64.b64encode(item.image_bytes).decode()
        ctx_text = f"{item.location or ''} {item.text or ''}"[:1000]
        prompt = _VISION_PROMPT.format(keywords=kw_str, text=ctx_text)

        payload = {
            "model": self.vision_model,
            "messages": [
                {
                    "role": "user",
                    "content": prompt,
                    "images": [img_b64],
                }
            ],
            "stream": False,
            "format": "json",
            "options": {"temperature": 0.0},
        }
        return self._call(payload, timeout=180)

    # ------------------------------------------------------------------
    # HTTP helper
    # ------------------------------------------------------------------

    def _call(self, payload: dict, timeout: int = 120) -> Optional[dict]:
        try:
            r = requests.post(
                f"{self.endpoint}/api/chat",
                json=payload,
                timeout=timeout,
            )
            r.raise_for_status()
            content = r.json()["message"]["content"]
            return json.loads(content)
        except Exception:
            return None
