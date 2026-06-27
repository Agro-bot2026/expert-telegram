"""
Módulo RAG — ChromaDB + Gemini + PDF processing.
Independiente del bot de Telegram.
"""
import os
import sys
import uuid
import sqlite3
import logging
import hashlib
import fitz
import vertexai
import chromadb

from pathlib import Path
from datetime import datetime
from dotenv import load_dotenv
from sentence_transformers import SentenceTransformer
from vertexai.generative_models import GenerativeModel, Part
from chromadb.config import Settings
from modules.docx_handler import extract_text_from_docx, get_extension

load_dotenv("/root/ExpertTelegram/.env")
os.environ["GOOGLE_APPLICATION_CREDENTIALS"] = "/root/ExpertTelegram/llave.json"

log = logging.getLogger(__name__)

DB_PATH = os.getenv("DB_PATH", "/root/ExpertTelegram/expertia.db")
CHROMA_PATH = os.getenv("CHROMA_PATH", "/root/ExpertTelegram/chroma_db")
GCP_PROJECT = os.getenv("GCP_PROJECT_ID", "cleanbot-8f137")
UPLOADS_DIR = Path(os.getenv("UPLOADS_DIR", "/root/ExpertTelegram/uploads"))

# Inicializar modelos
vertexai.init(project=GCP_PROJECT, location="us-central1")
model = GenerativeModel("gemini-2.5-flash")
embedder = SentenceTransformer("paraphrase-multilingual-MiniLM-L12-v2")
chroma_client = chromadb.PersistentClient(
    path=CHROMA_PATH,
    settings=Settings(anonymized_telemetry=False)
)

def init_db():
    """Inicializa la base de datos."""
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    cur.execute('''CREATE TABLE IF NOT EXISTS users (
        id INTEGER PRIMARY KEY,
        telegram_id INTEGER UNIQUE,
        username TEXT,
        nombre TEXT,
        profession TEXT DEFAULT 'General',
        plan TEXT DEFAULT 'free',
        pro_until TEXT DEFAULT '',
        preguntas_mes INTEGER DEFAULT 0,
        mes_actual TEXT DEFAULT '',
        created_at TEXT DEFAULT CURRENT_TIMESTAMP,
        email TEXT,
        password TEXT
    )''')
    cur.execute('''CREATE TABLE IF NOT EXISTS web_documentos (
        doc_id TEXT PRIMARY KEY,
        user_id INTEGER,
        filename TEXT,
        chunks INTEGER,
        created_at TEXT DEFAULT CURRENT_TIMESTAMP
    )''')
    conn.commit()
    conn.close()

def get_or_create_user(user_id: int) -> dict:
    """Obtiene o crea un usuario."""
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()
    cur.execute("SELECT * FROM users WHERE telegram_id = ?", (user_id,))
    row = cur.fetchone()
    if not row:
        cur.execute(
            "INSERT INTO users (telegram_id, username, nombre, plan) VALUES (?,?,?,?)",
            (user_id, str(user_id), str(user_id), "free")
        )
        conn.commit()
        cur.execute("SELECT * FROM users WHERE telegram_id = ?", (user_id,))
        row = cur.fetchone()
    conn.close()
    return dict(row)

def get_user_docs(user_id: int) -> list:
    """Obtiene los web_documentos de un usuario."""
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()
    cur.execute("SELECT * FROM web_documentos WHERE user_id = ?", (user_id,))
    rows = cur.fetchall()
    conn.close()
    return [dict(r) for r in rows]

def add_doc_record(user_id: int, doc_id: str, filename: str, chunks: int):
    """Agrega un registro de documento."""
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    cur.execute(
        "INSERT INTO web_documentos (doc_id, user_id, filename, chunks, created_at) VALUES (?,?,?,?,?)",
        (doc_id, user_id, filename, chunks, datetime.now().isoformat())
    )
    conn.commit()
    conn.close()

def delete_doc_record(doc_id: str, user_id: int):
    """Elimina un registro de documento."""
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    cur.execute("DELETE FROM web_documentos WHERE doc_id = ? AND user_id = ?", (doc_id, user_id))
    conn.commit()
    conn.close()

def check_questions(user: dict) -> bool:
    """Verifica si el usuario puede hacer más preguntas."""
    if user.get("plan") == "pro":
        return True
    return True  # Sin límite en pruebas

def increment_questions(user_id: int):
    """Incrementa el contador de preguntas."""
    pass  # Sin límite en pruebas

def get_collection(user_id: int):
    """Obtiene la colección ChromaDB del usuario."""
    return chroma_client.get_or_create_collection(
        name=f"user_{user_id}",
        metadata={"hnsw:space": "cosine"}
    )

def extract_text_from_pdf(pdf_path: str) -> tuple[str, bool]:
    """Extrae texto de un PDF, con OCR si es necesario."""
    try:
        doc = fitz.open(pdf_path)
        text = ""
        for page in doc:
            text += page.get_text()
        doc.close()

        if len(text.strip()) > 100:
            return text, False

        # OCR con Gemini Vision
        log.info("Usando OCR con Gemini Vision")
        doc = fitz.open(pdf_path)
        ocr_texts = []
        for page_num in range(min(len(doc), 10)):
            page = doc[page_num]
            pix = page.get_pixmap(dpi=150)
            img_bytes = pix.tobytes("png")
            img_part = Part.from_data(data=img_bytes, mime_type="image/png")
            response = model.generate_content([
                img_part,
                "Extraé todo el texto de esta imagen. Solo el texto, sin comentarios."
            ])
            ocr_texts.append(response.text)
        doc.close()
        return "\n\n".join(ocr_texts), True
    except Exception as e:
        log.error(f"Error extrayendo PDF: {e}")
        return "", False

def index_pdf(user_id: int, pdf_path: str, filename: str) -> tuple[str, int]:
    """Indexa un documento en ChromaDB."""
    doc_id = str(uuid.uuid4())
    ext = get_extension(pdf_path)

    if ext in ['.docx', '.doc']:
        text, used_ocr = extract_text_from_docx(pdf_path)
    else:
        text, used_ocr = extract_text_from_pdf(pdf_path)

    if not text.strip():
        raise ValueError("No se pudo extraer texto del documento.")

    # Dividir en chunks
    chunk_size = 500
    words = text.split()
    chunks = []
    for i in range(0, len(words), chunk_size):
        chunk = ' '.join(words[i:i + chunk_size])
        if chunk.strip():
            chunks.append(chunk)

    if not chunks:
        raise ValueError("El documento no tiene contenido.")

    # Indexar en ChromaDB
    col = get_collection(user_id)
    embeddings = embedder.encode(chunks).tolist()
    col.add(
        documents=chunks,
        embeddings=embeddings,
        ids=[f"{doc_id}_{i}" for i in range(len(chunks))],
        metadatas=[{"doc_id": doc_id, "filename": filename, "chunk": i} for i in range(len(chunks))]
    )

    return doc_id, len(chunks)

def query_rag(user_id: int, question: str, profession: str = "General") -> dict:
    """Responde una pregunta usando RAG."""
    col = get_collection(user_id)
    q_embedding = embedder.encode([question]).tolist()
    results = col.query(query_embeddings=q_embedding, n_results=5)

    if not results["documents"] or not results["documents"][0]:
        return {"answer": "No encontré información relevante en tus web_documentos.", "sources": []}

    context = "\n\n".join(results["documents"][0])
    sources = list(set([m["filename"] for m in results["metadatas"][0]]))

    prompt = f"""Sos un experto asistente que responde preguntas basándose EXCLUSIVAMENTE en el contenido de los web_documentos proporcionados.

DOCUMENTOS:
{context}

PREGUNTA: {question}

INSTRUCCIONES:
- Respondé SOLO con información de los web_documentos
- Si la información no está en los web_documentos, decilo claramente
- Sé claro y conciso
- No inventes información"""

    response = model.generate_content(prompt)
    answer = response.text.strip()

    return {"answer": answer, "sources": sources}
