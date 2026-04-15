"""
voice_server.py — Capa de voz para Atom Echo → Denver.

MODO STREAMING:
  El Atom envía el PCM con Transfer-Encoding: chunked mientras graba.
  El servidor lee con request.stream() — uvicorn decodifica los chunks
  automáticamente. Al terminar la grabación el Atom envía "0\r\n\r\n"
  (chunk final) y el servidor comienza el pipeline STT → Denver → TTS.

  El Atom ya NO necesita el buffer estático de 128KB. El servidor
  acumula en su propia memoria (sin restricciones).

Iniciar (timeout extendido para grabaciones largas):
  uvicorn voice_server:app --host 0.0.0.0 --port 8001 --timeout-keep-alive 120 --h11-max-incomplete-event-size 2097152
"""

import asyncio
import logging
import os
import tempfile
import wave
import aiohttp
import struct
from contextlib import asynccontextmanager

from dotenv import load_dotenv
from fastapi import FastAPI, Request, HTTPException
from fastapi.responses import StreamingResponse

load_dotenv()

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
    force=True,
)
logger = logging.getLogger("denver.voice_server")
logger.setLevel(logging.INFO)

# ── Config ────────────────────────────────────────────────────────────────────
SAMPLE_RATE_IN    = 16000
ATOM_ECHO_CHAT_ID = "atom_echo"
STREAM_CHUNK_SIZE = 4096

TTS_URL       = os.getenv("TTS_URL",       "http://localhost:5050/v1")
TTS_VOICE     = os.getenv("TTS_VOICE",     "es-AR-TomasNeural")
TTS_MODEL     = os.getenv("TTS_MODEL",     "tts-1")
TTS_SPEED     = os.getenv("TTS_SPEED",     "1.3")
LOCAL_TTS_KEY = os.getenv("LOCAL_TTS_KEY", "")

# ── Funciones de Denver (Ahora en core y utils) ───────────────────────────────
from core import invoke_graph, extract_response
from utils.media import speech_to_text, _clean_text_for_tts, get_http_session, close_http_session

agent_app = None

@asynccontextmanager
async def lifespan(app: FastAPI):
    global agent_app

    from db.database import init_db
    from graph import build_graph
    from metrics import tracker # ensure tables

    logger.info("[VoiceServer] Inicializando DB...")
    await init_db()

    logger.info("[VoiceServer] Compilando grafo Denver...")
    agent_app = await build_graph()
    
    # Asegurarse de que la sesión se cree
    await get_http_session()

    logger.info("[VoiceServer] Listo en :8001/voice")
    try:
        yield
    finally:
        await close_http_session()
        if agent_app and hasattr(agent_app.checkpointer, "conn"):
            await agent_app.checkpointer.conn.close()


# ── TTS ───────────────────────────────────────────────────────────────────────
async def tts_wav(text: str) -> bytes | None:
    """Llama al servidor TTS pidiendo WAV directamente enviando el texto limpio."""
    clean = _clean_text_for_tts(text)
    if not clean:
        return None
    try:
        session = await get_http_session()
        async with (session.post(
            f"{TTS_URL}/audio/speech",
            headers={"Authorization": f"Bearer {LOCAL_TTS_KEY}"},
            json={
                "model": TTS_MODEL,
                "input": clean,
                "voice": TTS_VOICE,
                "speed": float(TTS_SPEED),
                "response_format": "wav",
            },
            timeout=aiohttp.ClientTimeout(total=30),
        )) as resp:
            if resp.status == 200:
                data = await resp.read()
                header = data[:12]
                logger.info(
                    f"[VoiceServer] TTS: {len(data)} bytes | "
                    f"header={header.hex()} | "
                    f"RIFF={'yes' if data[:4]==b'RIFF' else 'NO'}"
                )
                return data
            logger.warning(f"[VoiceServer] TTS HTTP {resp.status}")
            return None
    except Exception as e:
        logger.error(f"[VoiceServer] TTS error: {e}")
        return None


app = FastAPI(lifespan=lifespan)


# ── Helpers ───────────────────────────────────────────────────────────────────
def pcm_to_wav_file(pcm: bytes, rate: int = SAMPLE_RATE_IN) -> str:
    """Escribe PCM crudo a un WAV temporal y devuelve la ruta."""
    fd, path = tempfile.mkstemp(suffix=".wav", prefix="atom_")
    with wave.open(path, "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(rate)
        wf.writeframes(pcm)
    os.close(fd)
    return path


def convert_to_wav(audio_bytes: bytes, target_rate: int = 16000) -> bytes:
    """
    Convierte cualquier formato de audio a WAV PCM 16-bit mono.
    Si el audio ya es un WAV con el formato correcto, lo devuelve sin procesar.
    Prueba: check header → miniaudio → stdlib → ffmpeg.
    """
    import io, array

    # 1. Check if already matches target (16kHz, mono, PCM 16-bit)
    if len(audio_bytes) >= 44 and audio_bytes.startswith(b'RIFF'):
        try:
            # WAV Header positions (Little Endian):
            # 20: AudioFormat (2 bytes, 1=PCM)
            # 22: NumChannels (2 bytes, 1=Mono)
            # 24: SampleRate (4 bytes)
            # 34: BitsPerSample (2 bytes, 16)
            fmt, channels, rate, bits = struct.unpack_from('<HHII', audio_bytes, 20)
            # BitsPerSample is at 34, so we need to unpack differently or adjust offsets
            # Let's be more precise
            fmt      = struct.unpack_from('<H', audio_bytes, 20)[0]
            channels = struct.unpack_from('<H', audio_bytes, 22)[0]
            rate     = struct.unpack_from('<I', audio_bytes, 24)[0]
            bits     = struct.unpack_from('<H', audio_bytes, 34)[0]
            
            if fmt == 1 and channels == 1 and rate == target_rate and bits == 16:
                logger.info(f"[VoiceServer] WAV ya está en formato correcto ({rate}Hz, mono). Bypassing.")
                return audio_bytes
        except Exception as e:
            logger.warning(f"[VoiceServer] Error analizando header WAV: {e}")

    try:
        import miniaudio
        decoded = miniaudio.decode(
            audio_bytes, nchannels=1,
            sample_rate=target_rate,
            output_format=miniaudio.SampleFormat.SIGNED16,
        )
        raw = bytes(decoded.samples)
        buf = io.BytesIO()
        with wave.open(buf, 'wb') as wf:
            wf.setnchannels(1); wf.setsampwidth(2)
            wf.setframerate(target_rate); wf.writeframes(raw)
        result = buf.getvalue()
        logger.info(f"[VoiceServer] miniaudio→WAV: {len(result)} bytes @ {target_rate}Hz")
        return result
    except ImportError:
        logger.warning("[VoiceServer] miniaudio no instalado — pip install miniaudio")
    except Exception as e:
        logger.warning(f"[VoiceServer] miniaudio error: {e}")

    try:
        with wave.open(io.BytesIO(audio_bytes), 'rb') as wf:
            src_rate = wf.getframerate()
            src_ch   = wf.getnchannels()
            n_frames = wf.getnframes()
            raw      = wf.readframes(n_frames)
        if src_ch == 2:
            s = array.array('h', raw)
            raw = array.array('h', [(s[i] + s[i+1]) // 2 for i in range(0, len(s), 2)]).tobytes()
        if src_rate != target_rate:
            s = array.array('h', raw)
            ratio = src_rate / target_rate
            out = array.array('h', [
                max(-32768, min(32767, int(
                    s[int(i * ratio)] * (1 - (i * ratio - int(i * ratio))) +
                    s[min(int(i * ratio) + 1, len(s) - 1)] * (i * ratio - int(i * ratio))
                )))
                for i in range(int(len(s) / ratio))
            ])
            raw = out.tobytes()
        buf = io.BytesIO()
        with wave.open(buf, 'wb') as wf:
            wf.setnchannels(1); wf.setsampwidth(2)
            wf.setframerate(target_rate); wf.writeframes(raw)
        result = buf.getvalue()
        logger.info(f"[VoiceServer] stdlib WAV: {len(result)} bytes @ {target_rate}Hz")
        return result
    except Exception as e:
        logger.warning(f"[VoiceServer] stdlib wav error: {e}")

    import subprocess, shutil
    if shutil.which("ffmpeg"):
        try:
            proc = subprocess.run(
                ["ffmpeg", "-y", "-i", "pipe:0",
                 "-ar", str(target_rate), "-ac", "1", "-sample_fmt", "s16", "-f", "wav", "pipe:1"],
                input=audio_bytes, capture_output=True, timeout=20,
            )
            if proc.returncode == 0:
                logger.info(f"[VoiceServer] ffmpeg WAV: {len(proc.stdout)} bytes")
                return proc.stdout
        except Exception as e:
            logger.warning(f"[VoiceServer] ffmpeg error: {e}")

    logger.error("[VoiceServer] No se pudo convertir audio — pip install miniaudio")
    return audio_bytes


def audio_quality_log(pcm: bytes, sample_rate: int) -> None:
    """Log de calidad del audio recibido."""
    n = min(len(pcm) // 2, 8000)
    if not n:
        return
    samples = [struct.unpack_from('<h', pcm, i * 2)[0] for i in range(n)]
    rms  = int((sum(s * s for s in samples) / len(samples)) ** 0.5)
    peak = max(abs(s) for s in samples)
    status = "OK" if rms > 300 else "MUY BAJO (posible silencio o mic mal calibrado)"
    duration = len(pcm) / (sample_rate * 2)
    logger.info(
        f"[VoiceServer] Audio: {duration:.1f}s | {len(pcm)} bytes | "
        f"RMS={rms} peak={peak} — {status}"
    )


# ── Endpoint ──────────────────────────────────────────────────────────────────
@app.post("/voice")
async def voice(request: Request):
    """
    Recibe PCM 16kHz del Atom Echo en streaming (sin Content-Length).
    El Atom cierra el write con shutdown(SHUT_WR) → EOF → fin de grabación.

    Pipeline: PCM stream → acumular → WAV temp → STT → Denver → TTS → WAV stream
    """
    wav_path = None

    try:
        # ── 1. Leer PCM completo desde el stream ──────────────────────────────
        # El Atom envía chunks pequeños (512B) durante la grabación.
        # Acumulamos aquí; el Atom ya no necesita el buffer de 128KB.
        sample_rate = int(request.headers.get("x-sample-rate", SAMPLE_RATE_IN))

        logger.info(f"[VoiceServer] Recibiendo PCM | rate={sample_rate}Hz")

        chunks: list[bytes] = []

        async for chunk in request.stream():
            if chunk:
                chunks.append(chunk)

        pcm = b"".join(chunks)

        if not pcm:
            raise HTTPException(status_code=400, detail="Body vacío")

        audio_quality_log(pcm, sample_rate)

        # ── 2. PCM → WAV temporal → STT ───────────────────────────────────────
        wav_path    = pcm_to_wav_file(pcm, rate=sample_rate)
        transcript  = await asyncio.to_thread(speech_to_text, wav_path)

        if not transcript:
            logger.warning("[VoiceServer] STT sin resultado")

            async def fallback():
                audio = await tts_wav("No te escuché bien, repetí por favor.")
                if audio:
                    wav = convert_to_wav(audio, target_rate=16000)
                    for i in range(0, len(wav), STREAM_CHUNK_SIZE):
                        yield wav[i:i + STREAM_CHUNK_SIZE]
                        await asyncio.sleep(0)

            return StreamingResponse(fallback(), media_type="audio/wav")

        logger.info(f"[VoiceServer] STT: {transcript!r}")

        # ── 3. Denver graph ───────────────────────────────────────────────────
        result        = await invoke_graph(agent_app, transcript, ATOM_ECHO_CHAT_ID, is_voice=True, interface="voice")
        response_text = extract_response(result)
        logger.info(f"[VoiceServer] Denver: {response_text[:120]!r}")

        # ── 4. TTS → WAV → StreamingResponse ─────────────────────────────────
        async def audio_stream():
            audio = await tts_wav(response_text or "No pude procesar tu pedido.")
            if not audio:
                logger.error("[VoiceServer] TTS falló")
                return

            wav = convert_to_wav(audio, target_rate=16000)
            logger.info(f"[VoiceServer] WAV {len(wav)} bytes → chunks {STREAM_CHUNK_SIZE}B")

            # Log header WAV
            if len(wav) >= 44:
                audio_fmt   = struct.unpack_from('<H', wav, 20)[0]
                channels    = struct.unpack_from('<H', wav, 22)[0]
                wav_sr      = struct.unpack_from('<I', wav, 24)[0]
                bps         = struct.unpack_from('<H', wav, 34)[0]
                data_size   = struct.unpack_from('<I', wav, 40)[0]
                logger.info(
                    f"[VoiceServer] WAV header: fmt={audio_fmt}(1=PCM) "
                    f"ch={channels} sr={wav_sr}Hz bps={bps} data={data_size}B"
                )

            for i in range(0, len(wav), STREAM_CHUNK_SIZE):
                yield wav[i:i + STREAM_CHUNK_SIZE]
                await asyncio.sleep(0)

        return StreamingResponse(audio_stream(), media_type="audio/wav")

    finally:
        if wav_path and os.path.exists(wav_path):
            os.remove(wav_path)


@app.get("/health")
async def health():
    return {"status": "ok", "chat_id": ATOM_ECHO_CHAT_ID}
