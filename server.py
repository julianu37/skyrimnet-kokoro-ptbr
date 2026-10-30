"""
SkyrimNet <-> Kokoro (pt-BR) adapter.

SkyrimNet's XTTS engine speaks a tiny HTTP contract:
  GET  /health
  POST /create_and_store_latents   (multipart: wav_file, speaker_name, language)
  POST /tts_to_audio/              (JSON: text, speaker_wav, language, ...)

This server answers that contract with Kokoro. The reference sample uploaded by
SkyrimNet is optional and ignored by default: Kokoro has fixed voices, so the
adapter maps each SkyrimNet speaker key (voice type / voice id) to a stable
Kokoro voice instead.
"""

from __future__ import annotations

import io
import json
import logging
import re
import threading
from contextlib import asynccontextmanager
import time
import zlib
from collections import OrderedDict
from logging.handlers import RotatingFileHandler
from pathlib import Path
from typing import Any, Optional

import numpy as np
import soundfile as sf
import uvicorn
import yaml
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, Response

ROOT = Path(__file__).resolve().parent
MODEL_PATH = ROOT / "models" / "kokoro-v1.0.onnx"
VOICES_PATH = ROOT / "models" / "voices-v1.0.bin"
CONFIG_PATH = ROOT / "config.yaml"
LOG_DIR = ROOT / "logs"
LOG_DIR.mkdir(exist_ok=True)

# --------------------------------------------------------------------------- #
# Config
# --------------------------------------------------------------------------- #

DEFAULT_CONFIG: dict[str, Any] = {
    "server": {"host": "127.0.0.1", "port": 8020},
    "tts": {
        "language": "pt-br",
        "force_language": True,
        "speed": 1.0,
        "speed_jitter": 0.02,
        "default_voice": "pf_dora",
        "narrator_voice": "pf_dora",
        "player_voice": "pm_alex",
        "female_voices": ["pf_dora"],
        "male_voices": ["pm_alex", "pm_santa"],
        "cache_size": 512,
        "save_reference_audio": False,
    },
    "overrides": {},
}


def deep_merge(base: dict, extra: dict) -> dict:
    out = dict(base)
    for key, value in (extra or {}).items():
        if isinstance(value, dict) and isinstance(out.get(key), dict):
            out[key] = deep_merge(out[key], value)
        else:
            out[key] = value
    return out


def load_config() -> dict[str, Any]:
    if not CONFIG_PATH.exists():
        CONFIG_PATH.write_text(
            yaml.safe_dump(DEFAULT_CONFIG, sort_keys=False, allow_unicode=True),
            encoding="utf-8",
        )
        return DEFAULT_CONFIG
    with CONFIG_PATH.open(encoding="utf-8") as handle:
        user = yaml.safe_load(handle) or {}
    return deep_merge(DEFAULT_CONFIG, user)


CONFIG = load_config()
TTS_CFG = CONFIG["tts"]
SERVER_CFG = CONFIG["server"]

# --------------------------------------------------------------------------- #
# Logging
# --------------------------------------------------------------------------- #

logger = logging.getLogger("kokoro-adapter")
logger.setLevel(logging.INFO)
if not logger.handlers:
    formatter = logging.Formatter("%(asctime)s %(levelname)s %(message)s")
    stream = logging.StreamHandler()
    stream.setFormatter(formatter)
    logger.addHandler(stream)
    file_handler = RotatingFileHandler(
        LOG_DIR / "adapter.log", maxBytes=2_000_000, backupCount=3, encoding="utf-8"
    )
    file_handler.setFormatter(formatter)
    logger.addHandler(file_handler)

# --------------------------------------------------------------------------- #
# Model (loaded once, guarded by a lock: Kokoro is CPU bound)
# --------------------------------------------------------------------------- #

_kokoro = None
_kokoro_lock = threading.Lock()
_ready = threading.Event()

# Audio tags that SkyrimNet may put in the text for expressive engines.
TAG_RE = re.compile(
    r"\[(?:emotion|style|prosody|sfx|pause|whisper|shout|sing|sigh|laugh|cough|"
    r"breath|excited|angry|sad|happy|trembl|soft|loud)[^\]]*\]",
    re.IGNORECASE,
)
SPACE_RE = re.compile(r"\s+")


def clean_text(text: str) -> str:
    text = TAG_RE.sub(" ", text or "")
    text = text.replace("\u2019", "'").replace("\u201c", '"').replace("\u201d", '"')
    text = SPACE_RE.sub(" ", text).strip()
    return text


def normalize_key(value: Optional[str]) -> str:
    raw = (value or "").strip().replace("\\", "/").split("/")[-1]
    raw = Path(raw).stem if raw.lower().endswith(".wav") else raw
    return re.sub(r"[^a-z0-9]+", "_", raw.lower()).strip("_")


def decode_json(raw: bytes) -> dict[str, Any]:
    """Decode a JSON body sent by clients that may not declare UTF-8."""
    for encoding in ("utf-8", "utf-8-sig", "cp1252", "latin-1"):
        try:
            value = json.loads(raw.decode(encoding))
            return value if isinstance(value, dict) else {}
        except (UnicodeDecodeError, json.JSONDecodeError):
            continue
    return {}


def normalize_lang(value: Optional[str]) -> str:
    lang = (value or TTS_CFG["language"] or "pt-br").lower().replace("_", "-")
    if lang.startswith("pt"):
        return "pt-br"
    return lang


class KokoroEngine:
    def __init__(self) -> None:
        from kokoro_onnx import Kokoro

        if not MODEL_PATH.exists() or not VOICES_PATH.exists():
            raise FileNotFoundError(
                f"model files missing under {ROOT / 'models'} - run setup.bat"
            )
        self.kokoro = Kokoro(str(MODEL_PATH), str(VOICES_PATH))
        self.voices = set(self.kokoro.get_voices())
        self.sample_rate = int(getattr(self.kokoro, "sample_rate", 24000) or 24000)

        needed = {"pf_dora"} | set(TTS_CFG["male_voices"])
        missing = needed - self.voices
        if missing:
            raise RuntimeError(f"voices missing from voices-v1.0.bin: {sorted(missing)}")

    def synth(self, text: str, voice: str, speed: float, lang: str) -> bytes:
        with _kokoro_lock:
            samples, sample_rate = self.kokoro.create(
                text, voice=voice, speed=speed, lang=lang
            )
        samples = np.asarray(samples, dtype=np.float32)
        peak = float(np.max(np.abs(samples))) if samples.size else 0.0
        if peak > 1.0:
            samples = samples / peak
        buffer = io.BytesIO()
        sf.write(buffer, samples, int(sample_rate), format="WAV", subtype="PCM_16")
        return buffer.getvalue()


def silence_wav(seconds: float = 0.35) -> bytes:
    samples = np.zeros(int(24000 * seconds), dtype=np.float32)
    buffer = io.BytesIO()
    sf.write(buffer, samples, 24000, format="WAV", subtype="PCM_16")
    return buffer.getvalue()


# --------------------------------------------------------------------------- #
# Voice mapping
# --------------------------------------------------------------------------- #

_cache_lock = threading.Lock()
_cache: "OrderedDict[str, bytes]" = OrderedDict()


def cache_get(key: str) -> Optional[bytes]:
    with _cache_lock:
        value = _cache.get(key)
        if value is not None:
            _cache.move_to_end(key)
        return value


def cache_put(key: str, value: bytes) -> None:
    limit = int(TTS_CFG.get("cache_size", 512))
    if limit <= 0:
        return
    with _cache_lock:
        _cache[key] = value
        _cache.move_to_end(key)
        while len(_cache) > limit:
            _cache.popitem(last=False)


def pick_voice(speaker: str) -> str:
    key = normalize_key(speaker)
    overrides = {normalize_key(k): v for k, v in (CONFIG.get("overrides") or {}).items()}
    if key in overrides:
        return overrides[key]

    if "narrator" in key or key in {"narration", "narratorh9"}:
        return TTS_CFG["narrator_voice"]
    if key in {"player", "player_special", "prisoner"}:
        return TTS_CFG["player_voice"]

    if "female" in key or key.startswith("fem"):
        pool = TTS_CFG["female_voices"]
    elif "male" in key:
        pool = TTS_CFG["male_voices"]
    else:
        # Unknown speaker key (for example an NPC name): use the neutral
        # default instead of guessing a gender.
        return TTS_CFG["default_voice"]
    if not pool:
        return TTS_CFG["default_voice"]
    if len(pool) == 1:
        return pool[0]
    return pool[zlib.crc32(key.encode("utf-8")) % len(pool)]


def pick_speed(speaker: str) -> float:
    base = float(TTS_CFG.get("speed", 1.0))
    jitter = float(TTS_CFG.get("speed_jitter", 0.0) or 0.0)
    if not jitter:
        return base
    key = normalize_key(speaker)
    # deterministic +/- jitter, so the same NPC keeps the same cadence
    offset = (zlib.crc32(key.encode("utf-8")) % 2001 - 1000) / 1000.0 * jitter
    return round(max(0.6, min(1.6, base + offset)), 3)


# --------------------------------------------------------------------------- #
# App
# --------------------------------------------------------------------------- #

@asynccontextmanager
async def lifespan(_: FastAPI):
    global _kokoro
    started = time.perf_counter()
    logger.info("loading Kokoro model: %s", MODEL_PATH)
    _kokoro = KokoroEngine()
    logger.info(
        "model ready in %.2fs (%d voices, %d Hz)",
        time.perf_counter() - started,
        len(_kokoro.voices),
        _kokoro.sample_rate,
    )
    _ready.set()

    def warmup() -> None:
        try:
            began = time.perf_counter()
            _kokoro.synth("Olá, viajante.", TTS_CFG["default_voice"], 1.0, "pt-br")
            logger.info("warm-up ok in %.2fs", time.perf_counter() - began)
        except Exception:  # pragma: no cover - diagnostics only
            logger.exception("warm-up failed")

    threading.Thread(target=warmup, daemon=True).start()
    yield


app = FastAPI(title="SkyrimNet Kokoro PT-BR adapter", version="1.0.0", lifespan=lifespan)


@app.get("/")
def index() -> dict[str, Any]:
    return {
        "name": "SkyrimNet Kokoro PT-BR adapter",
        "ready": _ready.is_set(),
        "endpoints": ["/health", "/create_and_store_latents", "/tts_to_audio/"],
    }


@app.get("/health")
def health() -> Response:
    if not _ready.is_set() or _kokoro is None:
        return JSONResponse({"status": "loading"}, status_code=503)
    return JSONResponse(
        {
            "status": "ok",
            "backend": "kokoro-onnx",
            "language": TTS_CFG["language"],
            "sample_rate": _kokoro.sample_rate,
            "voices": sorted(_kokoro.voices),
            "voice_map": {
                "female": TTS_CFG["female_voices"],
                "male": TTS_CFG["male_voices"],
                "narrator": TTS_CFG["narrator_voice"],
                "player": TTS_CFG["player_voice"],
            },
        }
    )


@app.post("/create_and_store_latents")
@app.post("/create_and_store_latents/")
async def create_and_store_latents(request: Request) -> JSONResponse:
    """SkyrimNet uploads a reference sample before speaking.

    Kokoro cannot clone, so the sample is accepted (and optionally archived) but
    the voice actually used comes from pick_voice(speaker_name).
    """
    fields: dict[str, Any] = {}
    upload = None
    content_type = request.headers.get("content-type", "")
    if "multipart/form-data" in content_type or "x-www-form-urlencoded" in content_type:
        form = await request.form()
        for key, value in form.multi_items():
            if hasattr(value, "filename"):
                upload = (key, value)
                fields[key] = getattr(value, "filename", None)
            else:
                fields[key] = value
    elif "application/json" in content_type:
        fields = decode_json(await request.body())

    speaker = str(
        fields.get("speaker_name")
        or fields.get("speaker")
        or fields.get("speaker_wav")
        or ""
    )
    language = str(fields.get("language") or TTS_CFG["language"])
    voice = pick_voice(speaker)

    if upload and TTS_CFG.get("save_reference_audio"):
        try:
            _, file = upload
            target = ROOT / "voices" / f"{normalize_key(speaker) or 'unknown'}.wav"
            target.parent.mkdir(exist_ok=True)
            target.write_bytes(await file.read())
            logger.info("saved reference sample: %s", target)
        except Exception:
            logger.exception("could not save reference sample")

    logger.info(
        "latents: speaker=%r voice=%s language=%s sample=%s",
        speaker,
        voice,
        language,
        bool(upload),
    )
    return JSONResponse(
        {
            "success": True,
            "backend": "kokoro-onnx",
            "speaker_name": speaker,
            "voice": voice,
            "language": language,
            "message": "Kokoro uses fixed voices; reference sample acknowledged.",
        }
    )


@app.post("/tts_to_audio")
@app.post("/tts_to_audio/")
async def tts_to_audio(request: Request) -> Response:
    content_type = request.headers.get("content-type", "")
    if "application/json" in content_type:
        payload = decode_json(await request.body())
    else:
        form = await request.form()
        payload = {key: value for key, value in form.items()}

    text = clean_text(str(payload.get("text") or ""))
    speaker = str(payload.get("speaker_wav") or payload.get("speaker") or "")
    requested_lang = payload.get("language")
    lang = (
        normalize_lang(requested_lang)
        if not TTS_CFG.get("force_language")
        else normalize_lang(TTS_CFG["language"])
    )
    voice = str(payload.get("voice") or pick_voice(speaker))
    speed = float(payload.get("speed") or pick_speed(speaker))

    if not text or not re.search(r"[0-9a-zA-ZÀ-ÿ]", text):
        logger.info("empty text for speaker=%r, returning silence", speaker)
        return Response(content=silence_wav(), media_type="audio/wav")

    key = f"{text}|{voice}|{speed}|{lang}"
    cached = cache_get(key)
    if cached is not None:
        logger.info(
            "tts cache hit: speaker=%r voice=%s chars=%d", speaker, voice, len(text)
        )
        return Response(content=cached, media_type="audio/wav")

    began = time.perf_counter()
    try:
        assert _kokoro is not None
        audio = _kokoro.synth(text, voice, speed, lang)
    except Exception:
        logger.exception("synthesis failed (speaker=%r voice=%s)", speaker, voice)
        return Response(content=silence_wav(), media_type="audio/wav")

    elapsed = time.perf_counter() - began
    duration = max(0.001, (len(audio) - 44) / 2 / 24000)
    logger.info(
        "tts: speaker=%r voice=%s lang=%s chars=%d synth=%.2fs audio=%.2fs rtf=%.2f",
        speaker,
        voice,
        lang,
        len(text),
        elapsed,
        duration,
        elapsed / duration,
    )
    cache_put(key, audio)
    return Response(content=audio, media_type="audio/wav")


def main() -> None:
    uvicorn.run(
        app,
        host=str(SERVER_CFG.get("host", "127.0.0.1")),
        port=int(SERVER_CFG.get("port", 8020)),
        log_level="warning",
    )


if __name__ == "__main__":
    main()
