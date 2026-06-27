"""
Módulo Podcast — Dos locutores con expresividad humana y estilos configurables.
Estilo NotebookLM: los locutores no se nombran entre sí, solo relatan.
Voces: Locutor A (Kore, femenina) y Locutor B (Puck, masculino)
"""
import os
from dotenv import load_dotenv
load_dotenv("/root/ExpertTelegram/.env")
os.environ["GOOGLE_APPLICATION_CREDENTIALS"] = "/root/ExpertTelegram/llave.json"
import base64
import struct
import logging
import requests
import google.auth
import google.auth.transport.requests

log = logging.getLogger(__name__)

GCP_PROJECT = os.getenv("GCP_PROJECT_ID", "cleanbot-8f137")
BASE_URL = f"https://us-central1-aiplatform.googleapis.com/v1beta1/projects/{GCP_PROJECT}/locations/us-central1/publishers/google/models"

ESTILOS = {
    "noticiero": {
        "nombre": "🎙️ Noticiero",
        "descripcion": "Formal y serio, como un noticiero de televisión",
        "prompt": """Los dos locutores presentan el tema como si fuera una noticia de televisión.
Hablan formal, con autoridad, usan frases como "Según los datos...", "Se confirma que...", "En exclusiva...".
Uno presenta, el otro agrega contexto y cifras. Tono serio pero dinámico."""
    },
    "debate": {
        "nombre": "⚖️ Abogado del Diablo",
        "descripcion": "Uno defiende, el otro cuestiona todo",
        "prompt": """Un locutor explica y defiende la información. El otro cuestiona, duda y hace de abogado del diablo.
Frases como "¿Pero no será que...?", "Eso suena bien pero...", "Esperá, ¿y si...?".
Generan tensión intelectual pero llegan a una conclusión."""
    },
    "standup": {
        "nombre": "😂 Stand-up",
        "descripcion": "Explican con humor y chistes relacionados al tema",
        "prompt": """Los locutores explican el tema con humor, analogías graciosas y chistes relacionados.
Usan frases como "Es como cuando...", "O sea, imaginate...", "¡No puede ser!".
Ríen entre sí, hacen chistes pero la información es correcta y completa."""
    },
    "documental": {
        "nombre": "🔬 Documental",
        "descripcion": "Estilo National Geographic, profundo y dramático",
        "prompt": """Los locutores narran como si fuera un documental de National Geographic.
Tono solemne, dramático, con pausas dramáticas antes de revelar datos.
Frases como "Lo que ocurre a continuación es extraordinario...", "La naturaleza nos enseña que...".
Usan metáforas y descripciones vívidas."""
    },
    "clase": {
        "nombre": "🧑‍🏫 Clase Magistral",
        "descripcion": "Uno enseña, el otro es el alumno que pregunta",
        "prompt": """Un locutor es el profesor experto que explica paso a paso.
El otro es el alumno curioso que hace preguntas genuinas, se sorprende y pide aclaraciones.
Frases del alumno: "¿Pero por qué?", "No entendí esa parte...", "¡Ah, claro!".
El profesor usa ejemplos simples y analogías."""
    },
    "cafe": {
        "nombre": "☕ Charla de Café",
        "descripcion": "Informal y relajado, como dos amigos hablando",
        "prompt": """Los locutores hablan como dos amigos tomando un café, de manera informal y relajada.
Usan muletillas argentinas: "A ver...", "O sea...", "¿Viste?", "La cosa es que...", "Uf...".
Se interrumpen, se ríen, cuentan anécdotas relacionadas. Muy natural y humano."""
    },
    "misterio": {
        "nombre": "🌑 Misterio y Conspiración",
        "descripcion": "Como si fuera un secreto que pocos conocen",
        "prompt": """Los locutores presentan la información como si fuera un secreto o descubrimiento misterioso.
Hablan en tono bajo, con pausas dramáticas, como si estuvieran revelando algo que "no quieren que sepas".
Frases como "Lo que te voy a contar ahora...", "Pocos saben esto pero...", "¿Y si te dijera que...?".
Dramático pero informativo."""
    }
}

SYSTEM_BASE = """Sos un guionista de podcasts en español rioplatense argentino.
Convertís información en una charla entre dos locutores (LOCUTOR_A y LOCUTOR_B).

REGLAS OBLIGATORIAS:
1. Los locutores NO se llaman por nombre entre sí. Solo hablan y relatan.
2. Usá SIEMPRE etiquetas de expresividad:
   [respiro] — antes de algo importante
   [pausa] — para generar expectativa
   [pausa dramática] — antes de revelar algo clave
   [risa leve] — momentos livianos
   [tono de sorpresa] — cuando algo es llamativo
   [con énfasis] — palabras clave importantes
   [susurrando] — tips o secretos
   [suspira] — reflexión o resignación
   [lo interrumpe] — cuando uno corta al otro
3. Entre 8 y 12 intercambios.
4. Formato EXACTO (sin markdown, sin asteriscos):
LOCUTOR_A: [texto con etiquetas]
LOCUTOR_B: [texto con etiquetas]
...
5. Terminá con una conclusión clara.
"""

def get_token() -> str:
    credentials, _ = google.auth.default(
        scopes=["https://www.googleapis.com/auth/cloud-platform"]
    )
    auth_req = google.auth.transport.requests.Request()
    credentials.refresh(auth_req)
    return credentials.token

def generar_dialogo(respuesta: str, estilo: str = "cafe") -> list[dict]:
    """Genera el diálogo entre los locutores según el estilo."""
    token = get_token()
    headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}

    estilo_config = ESTILOS.get(estilo, ESTILOS["cafe"])
    system_prompt = SYSTEM_BASE + "\n\nESTILO DEL PODCAST:\n" + estilo_config["prompt"]

    prompt = f"""Convertí esta información en un podcast:

INFORMACIÓN:
{respuesta}

Aplicá el estilo indicado con todas las etiquetas de expresividad."""

    resp = requests.post(
        f"{BASE_URL}/gemini-2.5-flash:generateContent",
        json={
            "systemInstruction": {"parts": [{"text": system_prompt}]},
            "contents": [{"role": "user", "parts": [{"text": prompt}]}],
            "generationConfig": {"temperature": 0.85, "maxOutputTokens": 2000}
        },
        headers=headers,
        timeout=60
    )

    if resp.status_code != 200:
        raise Exception(f"Error Gemini: {resp.status_code}")

    rjson = resp.json()
    if "candidates" not in rjson or not rjson["candidates"]:
        raise Exception(f"Sin candidatos: {rjson}")
    candidate = rjson["candidates"][0]
    if "content" not in candidate or "parts" not in candidate["content"]:
        raise Exception(f"Sin parts: {candidate}")
    texto = candidate["content"]["parts"][0]["text"].strip()

    lineas = []
    for line in texto.split('\n'):
        line = line.strip()
        if line.startswith('LOCUTOR_A:'):
            lineas.append({"texto": line[10:].strip(), "voz": "Kore"})
        elif line.startswith('LOCUTOR_B:'):
            lineas.append({"texto": line[10:].strip(), "voz": "Puck"})

    return lineas

def pcm_to_wav(pcm_data: bytes, sample_rate: int = 24000) -> bytes:
    """Convierte PCM raw a WAV."""
    channels = 1
    bits = 16
    header = struct.pack('<4sI4s4sIHHIIHH4sI',
        b'RIFF', 36 + len(pcm_data), b'WAVE',
        b'fmt ', 16, 1, channels, sample_rate,
        sample_rate * channels * bits // 8,
        channels * bits // 8, bits,
        b'data', len(pcm_data)
    )
    return header + pcm_data

def generar_audio_linea(texto: str, voz: str, token: str) -> bytes:
    """Genera audio PCM para una línea con la voz indicada."""
    headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}

    resp = requests.post(
        f"{BASE_URL}/gemini-3.1-flash-tts-preview:generateContent",
        json={
            "contents": [{"role": "user", "parts": [{"text": texto}]}],
            "generationConfig": {
                "responseModalities": ["AUDIO"],
                "speechConfig": {
                    "voiceConfig": {
                        "prebuiltVoiceConfig": {"voiceName": voz}
                    }
                }
            }
        },
        headers=headers,
        timeout=60
    )

    if resp.status_code != 200:
        log.error(f"Error TTS {voz}: {resp.status_code}")
        return b""

    audio_b64 = resp.json()["candidates"][0]["content"]["parts"][0]["inlineData"]["data"]
    return base64.b64decode(audio_b64)

def generar_podcast(respuesta: str, estilo: str = "cafe") -> str:
    """
    Genera el podcast completo.
    Retorna audio WAV en base64.
    """
    lineas = generar_dialogo(respuesta, estilo)
    if not lineas:
        raise Exception("No se pudo generar el diálogo")

    log.info(f"Podcast '{estilo}': {len(lineas)} líneas")

    token = get_token()
    audio_pcm_total = b""

    for i, linea in enumerate(lineas):
        log.info(f"Audio línea {i+1}/{len(lineas)} — voz {linea['voz']}")
        pcm = generar_audio_linea(linea["texto"], linea["voz"], token)
        if pcm:
            audio_pcm_total += pcm

    if not audio_pcm_total:
        raise Exception("No se pudo generar el audio")

    wav = pcm_to_wav(audio_pcm_total)
    return base64.b64encode(wav).decode()

def get_estilos() -> list[dict]:
    """Retorna la lista de estilos disponibles para el frontend."""
    return [
        {"id": k, "nombre": v["nombre"], "descripcion": v["descripcion"]}
        for k, v in ESTILOS.items()
    ]
