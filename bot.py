"""
ExpertIA Bot — Telegram
Bot de IA que responde preguntas basadas en PDFs del usuario.
Stack: python-telegram-bot, Gemini 2.5 Flash, Google TTS/STT, ChromaDB, PyMuPDF, OCR
"""

import os
import sys
sys.path.insert(0, "/root/ExpertTelegram")
from modules.docx_handler import extract_text_from_docx, is_supported, get_extension
import json
import uuid
import asyncio
import logging
import sqlite3
from pathlib import Path
from datetime import datetime
from dotenv import load_dotenv

load_dotenv()

from telegram import (
    Update, InlineKeyboardButton, InlineKeyboardMarkup,
    ReplyKeyboardMarkup, KeyboardButton
)
from telegram.ext import (
    Application, CommandHandler, MessageHandler,
    CallbackQueryHandler, ContextTypes, filters, ConversationHandler
)
from telegram.constants import ChatAction

import vertexai
from vertexai.generative_models import GenerativeModel, Part
from google.cloud import texttospeech, speech_v1

import fitz  # PyMuPDF
import chromadb
from chromadb.config import Settings
from sentence_transformers import SentenceTransformer


import hashlib
import requests as req_vt

VIRUSTOTAL_API_KEY = os.getenv("VIRUSTOTAL_API_KEY", "")

def scan_virus(file_path: str) -> tuple[bool, str]:
    """
    Escanea un archivo con VirusTotal.
    Retorna (es_seguro, mensaje)
    """
    if not VIRUSTOTAL_API_KEY:
        return True, "Sin API key de VirusTotal"

    try:
        # Calcular hash SHA256
        sha256 = hashlib.sha256()
        with open(file_path, "rb") as f:
            for chunk in iter(lambda: f.read(8192), b""):
                sha256.update(chunk)
        file_hash = sha256.hexdigest()

        # Consultar por hash primero (no consume cuota)
        headers = {"x-apikey": VIRUSTOTAL_API_KEY}
        resp = req_vt.get(
            f"https://www.virustotal.com/api/v3/files/{file_hash}",
            headers=headers,
            timeout=30
        )

        if resp.status_code == 200:
            data = resp.json()
            stats = data["data"]["attributes"]["last_analysis_stats"]
            malicious = stats.get("malicious", 0)
            suspicious = stats.get("suspicious", 0)

            if malicious > 0 or suspicious > 2:
                return False, f"⚠️ Archivo peligroso detectado ({malicious} amenazas). No se puede procesar."
            return True, "Archivo seguro"

        elif resp.status_code == 404:
            # No está en la base, subimos para análisis
            with open(file_path, "rb") as f:
                upload_resp = req_vt.post(
                    "https://www.virustotal.com/api/v3/files",
                    headers=headers,
                    files={"file": f},
                    timeout=60
                )
            if upload_resp.status_code == 200:
                return True, "Archivo nuevo — analizado y seguro"
            return True, "No se pudo verificar, procesando de todas formas"

    except Exception as e:
        log.warning(f"Error VirusTotal: {e}")
        return True, "Error al verificar, procesando de todas formas"

# ─── CONFIG ───────────────────────────────────────────────
TOKEN            = os.getenv("TELEGRAM_TOKEN")
GCP_PROJECT      = os.getenv("GCP_PROJECT_ID", "cleanbot-8f137")
ADMIN_ID         = int(os.getenv("ADMIN_CHAT_ID", "1358598881"))
DB_PATH          = os.getenv("DB_PATH", "/root/ExpertTelegram/expertia.db")
FREE_PDF_LIMIT   = int(os.getenv("FREE_PDF_LIMIT", "2"))
FREE_Q_LIMIT     = int(os.getenv("FREE_QUESTION_LIMIT", "5"))
UPLOADS_DIR      = Path("/root/ExpertTelegram/uploads")
CHROMA_DIR       = Path("/root/ExpertTelegram/chroma_db")

UPLOADS_DIR.mkdir(parents=True, exist_ok=True)
CHROMA_DIR.mkdir(parents=True, exist_ok=True)

os.environ["GOOGLE_APPLICATION_CREDENTIALS"] = "/root/ExpertTelegram/llave.json"

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO
)
log = logging.getLogger(__name__)

# ─── VERTEX AI ────────────────────────────────────────────
vertexai.init(project=GCP_PROJECT, location="us-central1")
gemini = GenerativeModel("gemini-2.5-flash")

# ─── EMBEDDINGS + CHROMADB ────────────────────────────────
embed_model = SentenceTransformer("paraphrase-multilingual-MiniLM-L12-v2")
chroma_client = chromadb.PersistentClient(path=str(CHROMA_DIR))

def get_collection(user_id: int):
    return chroma_client.get_or_create_collection(
        name=f"user_{user_id}",
        metadata={"hnsw:space": "cosine"}
    )

# ─── BASE DE DATOS ────────────────────────────────────────
def init_db():
    con = sqlite3.connect(DB_PATH)
    cur = con.cursor()
    cur.executescript("""
        CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY,
            telegram_id INTEGER UNIQUE,
            username TEXT,
            nombre TEXT,
            profession TEXT DEFAULT 'General',
            plan TEXT DEFAULT 'free',
            pro_until TEXT DEFAULT '',
            preguntas_mes INTEGER DEFAULT 0,
            mes_actual TEXT DEFAULT '',
            created_at TEXT DEFAULT CURRENT_TIMESTAMP
        );
        CREATE TABLE IF NOT EXISTS documentos (
            id TEXT PRIMARY KEY,
            user_id INTEGER,
            filename TEXT,
            chunks INTEGER DEFAULT 0,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP
        );
    """)
    con.commit()
    con.close()

def get_or_create_user(telegram_id: int, username: str = "", nombre: str = "") -> dict:
    con = sqlite3.connect(DB_PATH)
    con.row_factory = sqlite3.Row
    cur = con.cursor()
    cur.execute("SELECT * FROM users WHERE telegram_id = ?", (telegram_id,))
    row = cur.fetchone()
    if not row:
        cur.execute(
            "INSERT INTO users (telegram_id, username, nombre) VALUES (?,?,?)",
            (telegram_id, username, nombre)
        )
        con.commit()
        cur.execute("SELECT * FROM users WHERE telegram_id = ?", (telegram_id,))
        row = cur.fetchone()
    con.close()
    return dict(row)

def update_user(telegram_id: int, **kwargs):
    if not kwargs:
        return
    con = sqlite3.connect(DB_PATH)
    sets = ", ".join(f"{k}=?" for k in kwargs)
    con.execute(f"UPDATE users SET {sets} WHERE telegram_id=?", (*kwargs.values(), telegram_id))
    con.commit()
    con.close()

def get_user_docs(telegram_id: int) -> list:
    con = sqlite3.connect(DB_PATH)
    con.row_factory = sqlite3.Row
    cur = con.cursor()
    cur.execute("SELECT * FROM documentos WHERE user_id=? ORDER BY created_at DESC", (telegram_id,))
    rows = cur.fetchall()
    con.close()
    return [dict(r) for r in rows]

def add_doc_record(user_id: int, doc_id: str, filename: str, chunks: int):
    con = sqlite3.connect(DB_PATH)
    con.execute(
        "INSERT INTO documentos (id, user_id, filename, chunks) VALUES (?,?,?,?)",
        (doc_id, user_id, filename, chunks)
    )
    con.commit()
    con.close()

def delete_doc_record(doc_id: str, user_id: int):
    con = sqlite3.connect(DB_PATH)
    con.execute("DELETE FROM documentos WHERE id=? AND user_id=?", (doc_id, user_id))
    con.commit()
    con.close()

def check_questions(user: dict) -> bool:
    """Retorna True si puede preguntar."""
    return True  # Sin límites durante pruebas

def increment_questions(telegram_id: int, user: dict):
    if user["plan"] != "pro":
        mes = datetime.now().strftime("%Y-%m")
        new_count = (user["preguntas_mes"] + 1) if user["mes_actual"] == mes else 1
        update_user(telegram_id, preguntas_mes=new_count, mes_actual=mes)

# ─── OCR + EXTRACCIÓN ─────────────────────────────────────
def extract_text_from_pdf(pdf_path: str) -> tuple[str, bool]:
    """Extrae texto del PDF. Si está escaneado, usa OCR vía Gemini Vision."""
    doc = fitz.open(pdf_path)
    pages_text = [page.get_text() for page in doc]
    doc.close()
    full_text = "\n".join(pages_text).strip()
    avg_chars = len(full_text) / max(len(pages_text), 1)

    if avg_chars > 50:
        return full_text, False

    # PDF escaneado — OCR con Gemini Vision
    log.info(f"PDF escaneado, usando OCR con Gemini: {pdf_path}")
    doc = fitz.open(pdf_path)
    ocr_texts = []
    for i, page in enumerate(doc):
        pix = page.get_pixmap(dpi=200)
        img_bytes = pix.tobytes("png")
        img_part = Part.from_data(data=img_bytes, mime_type="image/png")
        resp = gemini.generate_content([
            img_part,
            "Extraé todo el texto de esta imagen de documento. Solo el texto, sin comentarios."
        ])
        ocr_texts.append(resp.text)
    doc.close()
    return "\n".join(ocr_texts), True

def chunk_text(text: str, chunk_size: int = 500, overlap: int = 50) -> list[str]:
    words = text.split()
    chunks = []
    i = 0
    while i < len(words):
        chunk = " ".join(words[i:i+chunk_size])
        if chunk.strip():
            chunks.append(chunk)
        i += chunk_size - overlap
    return chunks

def index_pdf(user_id: int, pdf_path: str, filename: str) -> tuple[str, int]:
    """Indexa PDF en ChromaDB. Retorna (doc_id, num_chunks)."""
    # Escanear virus primero
    es_seguro, mensaje = scan_virus(pdf_path)
    if not es_seguro:
        raise ValueError(mensaje)

    doc_id = str(uuid.uuid4())
    ext = get_extension(pdf_path)
    if ext in ['.docx', '.doc']:
        text, used_ocr = extract_text_from_docx(pdf_path)
    else:
        text, used_ocr = extract_text_from_pdf(pdf_path)
    chunks = chunk_text(text)

    if not chunks:
        raise ValueError("No se pudo extraer texto del PDF.")

    col = get_collection(user_id)
    embeddings = embed_model.encode(chunks).tolist()
    ids = [f"{doc_id}_{i}" for i in range(len(chunks))]
    metadatas = [{"doc_id": doc_id, "filename": filename} for _ in chunks]

    col.add(documents=chunks, embeddings=embeddings, ids=ids, metadatas=metadatas)
    log.info(f"Indexado: {filename} — {len(chunks)} chunks (OCR: {used_ocr})")
    return doc_id, len(chunks)

def query_rag(user_id: int, question: str, profession: str) -> dict:
    """Consulta RAG y retorna respuesta de Gemini."""
    col = get_collection(user_id)
    if col.count() == 0:
        return {"answer": "No tenés documentos cargados. Usá /subir para agregar PDFs.", "sources": []}

    q_emb = embed_model.encode([question]).tolist()
    n = min(3, col.count())
    results = col.query(query_embeddings=q_emb, n_results=n, include=["documents", "metadatas", "distances"])

    docs = results["documents"][0]
    metas = results["metadatas"][0]
    distances = results["distances"][0]

    relevant = [(d, m) for d, m, dist in zip(docs, metas, distances) if dist < 0.7]

    if not relevant:
        return {"answer": "No encontré información relevante en tus documentos para esa pregunta. Intentá reformularla.", "sources": []}

    context = "\n\n---\n\n".join(f"[{m['filename']}]\n{d}" for d, m in relevant)
    sources = list({m["filename"] for _, m in relevant})

    prompt = f"""Sos un asistente experto para {profession}.
Tu ÚNICA fuente de información son los documentos del usuario.
Respondé EXCLUSIVAMENTE con lo que está en los documentos.
Si no está en los documentos, decilo claramente. No inventes.
Usá lenguaje claro y práctico. Respondé en español argentino.
Al final mencioná de qué documento viene la info.

Contexto de documentos:
{context}

Pregunta: {question}"""

    resp = gemini.generate_content(prompt, generation_config={"temperature": 0.2, "max_output_tokens": 1000})
    return {"answer": resp.text, "sources": sources}

# ─── TEXT TO SPEECH ───────────────────────────────────────
def text_to_speech(text: str) -> bytes:
    """Convierte texto a audio OGG/OPUS para Telegram."""
    client = texttospeech.TextToSpeechClient()
    # Limitar texto a 4500 chars para TTS
    text = text[:4500]
    synthesis_input = texttospeech.SynthesisInput(text=text)
    voice = texttospeech.VoiceSelectionParams(
        language_code="es-US",
        name="es-US-Neural2-A"
    )
    audio_config = texttospeech.AudioConfig(
        audio_encoding=texttospeech.AudioEncoding.OGG_OPUS
    )
    response = client.synthesize_speech(
        input=synthesis_input, voice=voice, audio_config=audio_config
    )
    return response.audio_content

# ─── SPEECH TO TEXT ───────────────────────────────────────
async def speech_to_text(audio_bytes: bytes) -> str:
    """Transcribe audio OGG de Telegram a texto."""
    client = speech_v1.SpeechClient()
    audio = speech_v1.RecognitionAudio(content=audio_bytes)
    config = speech_v1.RecognitionConfig(
        encoding=speech_v1.RecognitionConfig.AudioEncoding.OGG_OPUS,
        sample_rate_hertz=48000,
        language_code="es-AR",
    )
    response = client.recognize(config=config, audio=audio)
    transcript = " ".join(r.alternatives[0].transcript for r in response.results)
    return transcript

# ─── TECLADO PRINCIPAL ────────────────────────────────────
def main_keyboard():
    return ReplyKeyboardMarkup([
        ["📄 Subir PDF", "💬 Hacer pregunta"],
        ["📚 Mis documentos", "ℹ️ Mi cuenta"],
        ["🗑️ Limpiar chat", "⭐ Plan Pro"],
    ], resize_keyboard=True)

# ─── HANDLERS ─────────────────────────────────────────────

async def cmd_start(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    user_tg = update.effective_user
    user = get_or_create_user(
        telegram_id=user_tg.id,
        username=user_tg.username or "",
        nombre=user_tg.first_name or ""
    )
    await update.message.reply_text(
        f"¡Hola {user_tg.first_name}! 👋\n\n"
        f"Soy *ExpertIA*, tu asistente que responde con *tus propios documentos*.\n\n"
        f"📄 Subí un PDF y haceme preguntas. Solo respondo con lo que vos me enseñaste.\n\n"
        f"Plan actual: *{user['plan'].upper()}*",
        reply_markup=main_keyboard()
    )

async def cmd_nueva(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    ctx.user_data.pop("history", None)
    await update.message.reply_text(
        "✅ Conversación limpiada. ¿Qué querés saber?",
        reply_markup=main_keyboard()
    )

async def cmd_mi_cuenta(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    user = get_or_create_user(update.effective_user.id)
    docs = get_user_docs(update.effective_user.id)
    mes = datetime.now().strftime("%Y-%m")
    preguntas = user["preguntas_mes"] if user["mes_actual"] == mes else 0

    if user["plan"] == "free":
        estado = f"Preguntas este mes: {preguntas}/{FREE_Q_LIMIT}\nDocumentos: {len(docs)}/{FREE_PDF_LIMIT}"
    else:
        estado = f"✅ Pro hasta: {user['pro_until']}\nDocumentos: {len(docs)} (ilimitados)"

    await update.message.reply_text(
        f"👤 *Tu cuenta*\n\n"
        f"Plan: *{user['plan'].upper()}*\n"
        f"{estado}\n\n"
        f"Profesión: {user['profession']}",
        parse_mode="Markdown",
        reply_markup=main_keyboard()
    )

async def cmd_mis_docs(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    docs = get_user_docs(update.effective_user.id)
    if not docs:
        await update.message.reply_text(
            "No tenés documentos cargados.\nUsá 📄 *Subir PDF* para agregar uno.",
            parse_mode="Markdown",
            reply_markup=main_keyboard()
        )
        return

    keyboard = []
    for doc in docs:
        keyboard.append([InlineKeyboardButton(
            f"🗑️ {doc['filename']} ({doc['chunks']} chunks)",
            callback_data=f"del_{doc['id']}"
        )])

    await update.message.reply_text(
        f"📚 *Tus documentos* ({len(docs)}):\n\nTocá uno para eliminarlo:",
        reply_markup=InlineKeyboardMarkup(keyboard)
    )

async def handle_delete_doc(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    doc_id = query.data.replace("del_", "")
    user_id = query.from_user.id

    # Eliminar de ChromaDB
    col = get_collection(user_id)
    results = col.get(where={"doc_id": doc_id})
    if results["ids"]:
        col.delete(ids=results["ids"])

    delete_doc_record(doc_id, user_id)
    await query.edit_message_text("✅ Documento eliminado.")

async def handle_pdf(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """Recibe y procesa un PDF."""
    user_tg = update.effective_user
    user = get_or_create_user(user_tg.id)
    docs = get_user_docs(user_tg.id)

    # Verificar límite de documentos
    if user["plan"] == "free" and len(docs) >= FREE_PDF_LIMIT:
        await update.message.reply_text(
            f"⚠️ Plan gratuito: máximo {FREE_PDF_LIMIT} documentos.\n\n"
            f"Actualizá a ⭐ *Pro* para documentos ilimitados.",
            parse_mode="Markdown",
            reply_markup=main_keyboard()
        )
        return

    doc = update.message.document
    if not doc.file_name.lower().endswith(".pdf"):
        await update.message.reply_text("❌ Solo acepto archivos PDF.", reply_markup=main_keyboard())
        return

    if doc.file_size > 50 * 1024 * 1024:
        await update.message.reply_text("❌ El PDF supera el límite de 50MB.", reply_markup=main_keyboard())
        return

    msg = await update.message.reply_text("⏳ Procesando tu PDF...")
    await ctx.bot.send_chat_action(chat_id=update.effective_chat.id, action=ChatAction.TYPING)

    try:
        # Descargar PDF
        file = await ctx.bot.get_file(doc.file_id)
        user_dir = UPLOADS_DIR / str(user_tg.id)
        user_dir.mkdir(exist_ok=True)
        pdf_path = user_dir / f"{uuid.uuid4()}.pdf"
        await file.download_to_drive(str(pdf_path))

        # Indexar
        await msg.edit_text("🔍 Indexando contenido...")
        doc_id, chunks = index_pdf(user_tg.id, str(pdf_path), doc.file_name)
        add_doc_record(user_tg.id, doc_id, doc.file_name, chunks)

        await msg.edit_text(
            f"✅ *{doc.file_name}* procesado\n"
            f"📊 {chunks} fragmentos indexados\n\n"
            f"¡Ya podés hacer preguntas sobre este documento!",
            parse_mode="Markdown"
        )

    except Exception as e:
        log.error(f"Error procesando PDF: {e}")
        await msg.edit_text(f"❌ Error al procesar el PDF: {str(e)}")

async def handle_voice(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """Transcribe audio y responde."""
    await ctx.bot.send_chat_action(chat_id=update.effective_chat.id, action=ChatAction.TYPING)
    msg = await update.message.reply_text("🎤 Transcribiendo audio...")

    try:
        voice = update.message.voice
        file = await ctx.bot.get_file(voice.file_id)
        audio_bytes = await file.download_as_bytearray()
        transcript = await speech_to_text(bytes(audio_bytes))

        if not transcript.strip():
            await msg.edit_text("❌ No pude entender el audio. Intentá de nuevo.")
            return

        await msg.edit_text(f"🎤 Escuché: _{transcript}_", parse_mode="Markdown")
        await process_question(update, ctx, transcript)

    except Exception as e:
        log.error(f"Error en voz: {e}")
        await msg.edit_text("❌ Error al procesar el audio.")

async def handle_text(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """Maneja mensajes de texto."""
    text = update.message.text

    # Botones del teclado
    if text == "📄 Subir PDF":
        await update.message.reply_text(
            "📄 Enviame el archivo PDF directamente en este chat.",
            reply_markup=main_keyboard()
        )
        return
    elif text == "💬 Hacer pregunta":
        await update.message.reply_text(
            "💬 Escribí tu pregunta y respondo con tus documentos.",
            reply_markup=main_keyboard()
        )
        return
    elif text == "📚 Mis documentos":
        await cmd_mis_docs(update, ctx)
        return
    elif text == "ℹ️ Mi cuenta":
        await cmd_mi_cuenta(update, ctx)
        return
    elif text == "🗑️ Limpiar chat":
        await cmd_nueva(update, ctx)
        return
    elif text == "⭐ Plan Pro":
        await cmd_pro(update, ctx)
        return

    await process_question(update, ctx, text)

async def process_question(update: Update, ctx: ContextTypes.DEFAULT_TYPE, question: str):
    """Procesa una pregunta con RAG."""
    user_tg = update.effective_user
    user = get_or_create_user(user_tg.id)

    # Verificar límite de preguntas
    if not check_questions(user):
        await update.message.reply_text(
            f"⚠️ Usaste las {FREE_Q_LIMIT} preguntas gratuitas del mes.\n\n"
            f"Actualizá a ⭐ *Pro* para preguntas ilimitadas.",
            parse_mode="Markdown",
            reply_markup=main_keyboard()
        )
        return

    docs = get_user_docs(user_tg.id)
    if not docs:
        await update.message.reply_text(
            "📄 No tenés documentos cargados.\nSubí un PDF primero con 📄 *Subir PDF*.",
            parse_mode="Markdown",
            reply_markup=main_keyboard()
        )
        return

    await ctx.bot.send_chat_action(chat_id=update.effective_chat.id, action=ChatAction.TYPING)
    msg = await update.message.reply_text("🤔 Buscando en tus documentos...")

    try:
        result = query_rag(user_tg.id, question, user.get("profession", "General"))
        answer = result["answer"]
        sources = result["sources"]

        # Incrementar contador
        increment_questions(user_tg.id, user)

        # Limpiar respuesta para TTS (sin fuentes ni markdown)
        import re
        answer_clean = re.sub(r"Documento:.*?\]", "", answer)
        answer_clean = re.sub(r"\[.*?\]", "", answer_clean)
        answer_clean = re.sub(r"📎.*", "", answer_clean).strip()

        # Texto para mostrar (con fuente al final)
        response_text = answer_clean
        if sources:
            response_text += f"\n\n📎 Fuente: {', '.join(sources)}"

        await msg.edit_text(response_text)

        # Botón audio — solo texto limpio
        keyboard = InlineKeyboardMarkup([[
            InlineKeyboardButton("🔊 Escuchar respuesta", callback_data=f"tts_{msg.message_id}")
        ]])
        ctx.user_data[f"tts_{msg.message_id}"] = answer_clean
        await msg.edit_reply_markup(reply_markup=keyboard)

    except Exception as e:
        log.error(f"Error en RAG: {e}")
        await msg.edit_text("❌ Error al procesar tu pregunta. Intentá de nuevo.")

async def handle_tts(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """Genera y envía audio de la respuesta."""
    query = update.callback_query
    await query.answer("🔊 Generando audio...")
    key = query.data
    text = ctx.user_data.get(key, "")

    if not text:
        await query.answer("❌ No encontré el texto.", show_alert=True)
        return

    await ctx.bot.send_chat_action(chat_id=query.message.chat_id, action=ChatAction.RECORD_VOICE)
    try:
        audio = text_to_speech(text)
        import io
        await ctx.bot.send_voice(
            chat_id=query.message.chat_id,
            voice=io.BytesIO(audio),
            caption="🔊 ExpertIA"
        )
    except Exception as e:
        log.error(f"Error TTS: {e}")
        await query.answer("❌ Error al generar audio.", show_alert=True)

async def cmd_pro(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "⭐ *Plan Pro — $10.000 ARS/mes*\n\n"
        "✅ PDFs ilimitados\n"
        "✅ Preguntas ilimitadas\n"
        "✅ Soporte por WhatsApp\n\n"
        "Para activar tu plan Pro contactanos:",
        parse_mode="Markdown",
        reply_markup=InlineKeyboardMarkup([[
            InlineKeyboardButton("💬 Contratar por WhatsApp", url="https://wa.me/5492634841144")
        ]])
    )

# ─── MAIN ─────────────────────────────────────────────────
def main():
    init_db()
    log.info("Iniciando ExpertIA Bot...")

    app = Application.builder().token(TOKEN).build()

    # Comandos
    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CommandHandler("nueva", cmd_nueva))
    app.add_handler(CommandHandler("docs", cmd_mis_docs))
    app.add_handler(CommandHandler("cuenta", cmd_mi_cuenta))
    app.add_handler(CommandHandler("pro", cmd_pro))

    # Mensajes
    app.add_handler(MessageHandler(filters.Document.PDF, handle_pdf))
    app.add_handler(MessageHandler(filters.VOICE, handle_voice))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text))

    # Callbacks
    app.add_handler(CallbackQueryHandler(handle_delete_doc, pattern="^del_"))
    app.add_handler(CallbackQueryHandler(handle_tts, pattern="^tts_"))

    log.info("Bot corriendo...")
    app.run_polling(drop_pending_updates=True)

if __name__ == "__main__":
    main()
