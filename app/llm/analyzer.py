import base64
import json
import requests
from dataclasses import dataclass, field
from typing import List, Optional

from app.scraper.instagram_scraper import ContentItem


@dataclass
class AnalysisResult:
    author: str          # original content creator
    source_account: str  # monitored page where it was found
    city: str
    text: str
    reason: str          # max 100 chars
    probability: int     # 1-4
    content_type: str
    url: str
    matched_keywords: List[str] = field(default_factory=list)


_RUBRIC = """\
Você é um sistema de vigilância epidemiológica. Avalie SOMENTE indícios de \
doença, surto, contaminação ou emergência de saúde pública.

REGRA PRINCIPAL: na dúvida, use probabilidade 1. A maioria das publicações NÃO \
é relevante. Conteúdo sobre música, shows, futebol, política, religião, arte, \
publicidade, fofoca ou cotidiano = probabilidade 1, SEMPRE.

Escala de probabilidade (exija evidência EXPLÍCITA para subir):
  1 = sem qualquer indício de saúde/doença (padrão)
  2 = menção vaga a sintoma/doença de 1 pessoa, sem contexto coletivo
  3 = relato concreto de doença/contaminação afetando um grupo ou local
  4 = surto claro: muitos doentes, contaminação confirmada, hospital lotado, \
mortes por doença, alerta sanitário oficial

NÃO invente sinais. Só cite o que estiver realmente presente. \
Responda ESTRITAMENTE em português."""

_TEXT_SYSTEM = _RUBRIC + """

Palavras-chave monitoradas (use como referência semântica, não basta a palavra \
aparecer): {keywords}

Retorne APENAS JSON:
{{
  "city": "cidade mencionada ou string vazia",
  "is_relevant": true se probability>=2 senão false,
  "reason": "motivo breve em português, máx 100 caracteres",
  "probability": número inteiro de 1 a 4
}}"""

# The vision model only DESCRIBES the image (its strength); the well-calibrated
# text model makes the epidemiological judgment. This avoids the weaker vision
# model inventing relevance.
_VISION_DESCRIBE = """\
Descreva de forma objetiva e factual o que aparece nesta imagem, em português, \
em no máximo 40 palavras. Mencione pessoas, locais, sintomas visíveis, texto na \
imagem, hospitais ou multidões se houver. NÃO interprete nem opine. Apenas descreva."""


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

        # Stage 1: vision model only DESCRIBES the image (no judgment).
        image_desc = ""
        if item.image_bytes:
            image_desc = self._describe_image(item) or ""

        # Stage 2: the calibrated text model makes the epidemiological call,
        # using caption + location + the factual image description.
        raw = self._judge(item, kw_str, image_desc)
        if raw is None:
            return None

        matched = [
            k for k in keywords if k.lower() in (item.text or "").lower()
        ]

        reason = (raw.get("reason") or "")[:100]
        prob = max(1, min(4, int(raw.get("probability") or 1)))

        return AnalysisResult(
            author=item.author,
            source_account=item.source_account,
            city=raw.get("city") or item.location or "",
            text=item.text or "",
            reason=reason,
            probability=prob,
            content_type=item.content_type,
            url=item.url,
            matched_keywords=matched,
        )

    # ------------------------------------------------------------------
    # Stage 2: epidemiological judgment (text model)
    # ------------------------------------------------------------------

    def _judge(self, item: ContentItem, kw_str: str, image_desc: str) -> Optional[dict]:
        system = _TEXT_SYSTEM.format(keywords=kw_str)
        parts = [
            f"Local: {item.location or 'desconhecido'}",
            f"Legenda: {(item.text or '')[:2000]}",
        ]
        if image_desc:
            parts.append(f"Descrição da imagem: {image_desc[:600]}")
        payload = {
            "model": self.text_model,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": "\n".join(parts)},
            ],
            "stream": False,
            "format": "json",
            "options": {"temperature": 0.0},
        }
        return self._call(payload)

    # ------------------------------------------------------------------
    # Stage 1: factual image description (vision model)
    # ------------------------------------------------------------------

    def _describe_image(self, item: ContentItem) -> Optional[str]:
        img_b64 = base64.b64encode(item.image_bytes).decode()
        payload = {
            "model": self.vision_model,
            "messages": [
                {"role": "user", "content": _VISION_DESCRIBE, "images": [img_b64]},
            ],
            "stream": False,
            "options": {"temperature": 0.0},
        }
        return self._call_raw(payload, timeout=180)

    # ------------------------------------------------------------------
    # HTTP helper
    # ------------------------------------------------------------------

    def _call(self, payload: dict, timeout: int = 120) -> Optional[dict]:
        content = self._call_raw(payload, timeout)
        if content is None:
            return None
        try:
            return json.loads(content)
        except Exception:
            return None

    def _call_raw(self, payload: dict, timeout: int = 120) -> Optional[str]:
        try:
            r = requests.post(
                f"{self.endpoint}/api/chat",
                json=payload,
                timeout=timeout,
            )
            r.raise_for_status()
            return r.json()["message"]["content"]
        except Exception:
            return None
