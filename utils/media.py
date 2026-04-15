import os
import re
import logging
import aiohttp
from groq import Groq

logger = logging.getLogger("denver.utils.media")

# ─── Globals / Singletons ─────────────────────────────────────────────────────
_groq_client = None
_tts_session: aiohttp.ClientSession | None = None

# Precompiled emoji pattern
_EMOJI_PATTERN = re.compile(
    "["
    "\U0001F600-\U0001F64F"  # emoticons
    "\U0001F300-\U0001F5FF"  # symbols & pictographs
    "\U0001F680-\U0001F6FF"  # transport & map
    "\U0001F1E0-\U0001F1FF"  # flags
    "\U00002702-\U000027B0"  # dingbats
    "\U000024C2-\U0001F251"
    "\U0001f900-\U0001f9FF"  # supplemental symbols
    "\U00002600-\U000026FF"  # misc symbols
    "]+",
    flags=re.UNICODE,
)

async def get_http_session() -> aiohttp.ClientSession:
    """Get or create a persistent aiohttp ClientSession."""
    global _tts_session
    if _tts_session is None or _tts_session.closed:
        _tts_session = aiohttp.ClientSession()
    return _tts_session

async def close_http_session():
    """Close the global aiohttp ClientSession."""
    global _tts_session
    if _tts_session and not _tts_session.closed:
        await _tts_session.close()
        logger.info("[Media] HTTP Session closed.")

def get_groq_client() -> Groq:
    """Get or create a Groq client singleton."""
    global _groq_client
    if _groq_client is None:
        _groq_client = Groq(api_key=os.getenv("GROQ_API_KEY"))
    return _groq_client

def _clean_text_for_tts(text: str) -> str:
    """Remove markdown and emojis before TTS synthesis."""
    # Remove markdown formatting
    text = re.sub(r'\*\*(.+?)\*\*', r'\1', text)  # bold
    text = re.sub(r'\*(.+?)\*', r'\1', text)       # italic
    text = re.sub(r'__(.+?)__', r'\1', text)        # bold alt
    text = re.sub(r'~~(.+?)~~', r'\1', text)        # strikethrough
    text = re.sub(r'`(.+?)`', r'\1', text)          # inline code
    text = re.sub(r'^#{1,6}\s+', '', text, flags=re.MULTILINE)  # headers

    # Remove emojis using precompiled pattern
    text = _EMOJI_PATTERN.sub("", text)
    return text.strip()

def speech_to_text(file_path: str) -> str | None:
    """Transcribe audio with Groq Whisper."""
    try:
        client = get_groq_client()
        with open(file_path, "rb") as f:
            transcription = client.audio.transcriptions.create(
                file=(os.path.basename(file_path), f.read()),
                model="whisper-large-v3",
                language="es",
                response_format="json",
                temperature=0.0,
            )
        text = transcription.text
        logger.info(f"[STT] ok | {len(text)} chars")
        return text
    except Exception as e:
        logger.error(f"[STT] fallo: {e}")
        return None

async def text_to_speech(texto: str, config: dict) -> bytes | None:
    """
    Convert text to audio using a local OpenAI-compatible server.
    config keys: url, key, model, voice, speed
    """
    texto_limpio = _clean_text_for_tts(texto)
    if not texto_limpio:
        return None

    payload = {
        "model": config['model'],
        "input": texto_limpio,
        "voice": config['voice'],
        "speed": float(config['speed']),
    }
    if "response_format" in config:
        payload["response_format"] = config["response_format"]

    try:
        session = await get_http_session()
        resp = await session.post(
                f"{config['url']}/audio/speech",
                headers={"Authorization": f"Bearer {config['key']}"},
                json=payload,
                timeout=aiohttp.ClientTimeout(total=30),
        )
        if resp.status == 200:
            audio_bytes = await resp.read()
            logger.info(f"[TTS] ok | {len(audio_bytes)} bytes")
            return audio_bytes
        logger.warning(f"[TTS] Error HTTP {resp.status}")
        return None
    except Exception as e:
        logger.error(f"[TTS] fallo — fallback a texto: {e}")
        return None
