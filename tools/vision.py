"""
tools/vision.py — Image analysis tools for Denver Bot.

Uses the configured vision-capable LLM (Gemma 4 multimodal) via the
OpenAI-compatible API to analyze images from:
  - Home Assistant camera captures (via _captured_image side channel)
  - User-uploaded photos (Telegram / Web Chat, stored in the same channel)

Tools:
  - analizar_imagen           : analyze an already-captured image
"""

import os
import base64
import logging

from langchain_core.tools import tool
import tools as tools_module

logger = logging.getLogger("denver.tools.vision")


# ─── Vision LLM client ────────────────────────────────────────────────────────

def _get_vision_client():
    """
    Returns an OpenAI-compatible client for the vision model.
    Uses LLM_URL / LLM_MODEL by default;
    overridable via VISION_MODEL_URL / VISION_MODEL env vars.
    """
    from openai import OpenAI
    url   = os.getenv("VISION_MODEL_URL")
    model = os.getenv("VISION_MODEL")
    key   = os.getenv("VISION_API_KEY")
    return OpenAI(base_url=url, api_key=key), model


def _detect_mime(image_bytes: bytes) -> str:
    if image_bytes[:2] == b'\xff\xd8':
        return "image/jpeg"
    if image_bytes[:8] == b'\x89PNG\r\n\x1a\n':
        return "image/png"
    if image_bytes[:4] == b'GIF8':
        return "image/gif"
    if image_bytes[:4] == b'RIFF' and image_bytes[8:12] == b'WEBP':
        return "image/webp"
    return "image/jpeg"  # safe default for HA cameras


def _call_vision_llm(image_bytes: bytes, pregunta: str) -> str:
    """Core vision call — shared by both tools."""
    b64 = base64.b64encode(image_bytes).decode("utf-8")
    mime = _detect_mime(image_bytes)
    data_uri = f"data:{mime};base64,{b64}"

    client, model = _get_vision_client()

    system_prompt = (
        "Sos un asistente de visión artificial. Analizás imágenes con precisión y detalle. "
        "Respondé siempre en español, de forma clara y concisa. "
        "Si la imagen proviene de una cámara de seguridad, prestá atención a personas, "
        "objetos, puertas, ventanas, vehículos o cualquier elemento relevante."
    )

    response = client.chat.completions.create(
        model=model,
        messages=[
            {"role": "system", "content": system_prompt},
            {
                "role": "user",
                "content": [
                    {"type": "image_url", "image_url": {"url": data_uri}},
                    {"type": "text",      "text": pregunta},
                ],
            },
        ],
        max_tokens=1024,
        temperature=0.2,
    )

    description = response.choices[0].message.content.strip()
    logger.info(f"[vision] Vision OK — {len(description)} chars")
    return description


# ─── Tool 1: analyze an already-captured image ────────────────────────────────

@tool
def analizar_imagen(pregunta: str = "Describí detalladamente lo que ves en esta imagen.") -> str:
    """
    Analyzes the last captured or user-uploaded image using the vision LLM.
    Use ONLY when an image is already available (after capture_camera_image ran,
    or when the user sent a photo).

    pregunta: Specific question about the image in Spanish.
    Examples: "¿Hay alguien?", "¿La puerta está abierta?", "Describí lo que ves."
    Returns a detailed text description in Spanish.
    """
    image_bytes: bytes | None = tools_module._captured_image

    if not image_bytes:
        return (
            "No hay ninguna imagen disponible para analizar. "
            "Usá primero capture_camera_image o pedile al usuario que envíe una foto."
        )

    try:
        return _call_vision_llm(image_bytes, pregunta)
    except Exception as e:
        logger.error(f"[analizar_imagen] Error: {e}")
        return f"Error analizando la imagen: {e}"



