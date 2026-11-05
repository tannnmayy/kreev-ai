"""The ears and the mouth: which listening and speaking services this run uses.

Everything is chosen by which keys exist, so a machine with none still starts: the customer types and reads captions. LiveKit's
fallback adapters handle a provider that fails mid-call. The order can be changed with ALA_STT and ALA_TTS after the bake-off.
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Sequence

from livekit.agents import llm, stt, tts

from ..shop.catalog import Catalog

log = logging.getLogger("ala.speech")

# Voices per model and language. Picked by ear in the bake-off; ALA_VOICE_TE and ALA_VOICE_EN override.
VOICES: Dict[str, Dict[str, str]] = {
    "bulbul:v3": {"te": "kavitha", "en": "kavitha"},
    "bulbul:v4-flash": {"te": "kavitha_te_conversation", "en": "ishita_en_customer"},
}
LANGUAGE_CODE = {"te": "te-IN", "en": "en-IN"}


class NoLLM(llm.LLM):
    """AgentSession wants an LLM before it runs a pipeline. Ala's llm_node replaces the call, so this is never used."""

    def chat(self, **kwargs: Any):
        raise NotImplementedError("Ala's brain replaces this call")


def vocabulary(catalog: Catalog, limit: int = 100) -> List[str]:
    """The words a recogniser most needs help with: Telugu and English names of what the shop sells, fish first."""
    fish = [p for p in catalog.products if p.cat in ("fish", "dry-fish")]
    rest = [p for p in catalog.products if p.cat not in ("fish", "dry-fish")]
    words: List[str] = []
    for p in fish + rest:
        for w in (p.te, p.en):
            if w not in words:
                words.append(w)
    return words[:limit]


def _stt_prompt(catalog: Catalog) -> str:
    return "A customer buying fish and groceries from a coastal Andhra market, in Telugu and English: " + \
        ", ".join(vocabulary(catalog, 40))


def build_stt(catalog: Catalog) -> Optional[stt.STT]:
    """Every listening service with a key, in order, behind a fallback adapter."""
    if os.environ.get("ALA_STANDIN"):
        from .standin import StandInSTT
        return StandInSTT()
    order = [x.strip() for x in os.environ.get("ALA_STT", "sarvam,gladia").split(",") if x.strip()]
    found: List[stt.STT] = []
    for name in order:
        try:
            if name == "sarvam" and os.environ.get("SARVAM_API_KEY"):
                from livekit.plugins import sarvam
                found.append(sarvam.STTRealtime(language=os.environ.get("ALA_STT_LANGUAGE", "auto"), stream_type="balanced",
                                                mode="codemix", prompt=_stt_prompt(catalog)))
            elif name == "gladia" and os.environ.get("GLADIA_API_KEY"):
                from livekit.plugins import gladia
                found.append(gladia.STT(languages=["te", "en"], code_switching=True, custom_vocabulary=vocabulary(catalog, 100)))
        except Exception as exc:
            log.warning("could not set up %s for listening: %s", name, str(exc)[:160])
    if not found:
        return None
    return found[0] if len(found) == 1 else stt.FallbackAdapter(found)


@dataclass
class Voice:
    """The speaking service, with the one thing that changes per reply: the language and pace."""
    engine: Optional[tts.TTS]
    model: str = ""

    def use(self, lang: str, pace: float = 1.0) -> None:
        if self.engine is None or not hasattr(self.engine, "update_options"):
            return
        choices = VOICES.get(self.model, {})
        speaker = os.environ.get("ALA_VOICE_" + lang.upper()) or choices.get(lang) or None
        try:
            kwargs: Dict[str, Any] = {"target_language_code": LANGUAGE_CODE.get(lang, "en-IN"), "pace": pace}
            if speaker:
                kwargs["speaker"] = speaker
            self.engine.update_options(**kwargs)
        except Exception as exc:     # a voice setting must never stop the reply
            log.warning("could not switch the voice: %s", str(exc)[:160])


def build_voice() -> Voice:
    if os.environ.get("ALA_STANDIN"):
        from .standin import StandInTTS
        return Voice(StandInTTS(), "stand-in")
    if os.environ.get("SARVAM_API_KEY"):
        try:
            from livekit.plugins import sarvam
            model = os.environ.get("ALA_TTS_MODEL", "bulbul:v3")
            voice = os.environ.get("ALA_VOICE_TE") or VOICES.get(model, {}).get("te") or None
            kwargs: Dict[str, Any] = {"target_language_code": "te-IN", "model": model, "speech_sample_rate": 24000}
            if voice:
                kwargs["speaker"] = voice
            return Voice(sarvam.TTS(**kwargs), model)
        except Exception as exc:
            log.warning("could not set up Sarvam's voice: %s", str(exc)[:160])
    return Voice(None)
