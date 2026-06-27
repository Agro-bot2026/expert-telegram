"""
ExpertIA Web — FastAPI
Backend limpio, sin dependencias de Telegram.
"""
import os
import sys
import uuid
import hashlib
import sqlite3
import logging
import random
import string
import smtplib
import threading
import struct
import base64
import re
import requests

from pathlib import Path
from datetime import datetime, timedelta
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from dotenv import load_dotenv

load_dotenv("/root/ExpertTelegram/.env")
os.environ["GOOGLE_APPLICATION_CREDENTIALS"] = "/root/ExpertTelegram/llave.json"
sys.path.insert(0, "/root/ExpertTelegram")

from fastapi import FastAPI, Request, UploadFile, File, Form
from slowapi import Limiter, _rate_limit_exceeded_handler
from slowapi.util import get_remote_address
from slowapi.errors import RateLimitExceeded
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from starlette.middleware.sessions import SessionMiddleware
from jinja2 import Environment, FileSystemLoader

from modules.rag import index_pdf, query_rag, get_collection
from modules.docx_handler import is_supported, get_extension
from modules.podcast import generar_podcast, get_estilos
from modules.mercadopago_handler import crear_preferencia, activar_plan_pro, get_payment_info
from modules.security import check_prompt_injection, sanitize_text, validate_file
from modules.monitor import alert_nuevo_usuario, alert_nuevo_pago, alert_injection, alert_archivo_malicioso, alert_error, alert_servidor_inicio

# ─── CONFIG ───────────────────────────────────────────────
log = logging.getLogger(__name__)
logging.basicConfig(level=logging.INFO)

DB_PATH = os.getenv("DB_PATH", "/root/ExpertTelegram/expertia.db")
UPLOADS_DIR = Path(os.getenv("UPLOADS_DIR", "/root/ExpertTelegram/uploads"))
TEMPLATES_DIR = Path("/root/ExpertTelegram/templates")
SECRET_KEY = os.getenv("SECRET_KEY", "expertia-secret-2026")
FREE_PDF_LIMIT = int(os.getenv("FREE_PDF_LIMIT", "2"))
FREE_QUESTION_LIMIT = int(os.getenv("FREE_QUESTION_LIMIT", "5"))

UPLOADS_DIR.mkdir(exist_ok=True)

app = FastAPI()
limiter = Limiter(key_func=get_remote_address)
app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)
app.add_middleware(SessionMiddleware, secret_key=SECRET_KEY, max_age=2592000)

jinja_env = Environment(loader=FileSystemLoader(str(TEMPLATES_DIR)))

def render(name: str, **ctx) -> HTMLResponse:
    t = jinja_env.get_template(name)
    return HTMLResponse(t.render(**ctx))

# ─── BASE DE DATOS ────────────────────────────────────────
def get_db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn

def init_db():
    conn = get_db()
    cur = conn.cursor()
    # Tabla usuarios web
    cur.execute('''CREATE TABLE IF NOT EXISTS web_users (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        email TEXT UNIQUE NOT NULL,
        password TEXT NOT NULL,
        nombre TEXT DEFAULT '',
        profession TEXT DEFAULT 'General',
        plan TEXT DEFAULT 'free',
        pro_until TEXT DEFAULT '',
        verificado INTEGER DEFAULT 0,
        created_at TEXT DEFAULT CURRENT_TIMESTAMP
    )''')
    # Tabla documentos
    cur.execute('''CREATE TABLE IF NOT EXISTS web_documentos (
        doc_id TEXT PRIMARY KEY,
        user_id INTEGER NOT NULL,
        filename TEXT NOT NULL,
        chunks INTEGER DEFAULT 0,
        created_at TEXT DEFAULT CURRENT_TIMESTAMP
    )''')
    # Tabla verificaciones
    cur.execute('''CREATE TABLE IF NOT EXISTS verificaciones (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        email TEXT NOT NULL,
        codigo TEXT NOT NULL,
        intentos INTEGER DEFAULT 0,
        expires_at TEXT NOT NULL,
        verificado INTEGER DEFAULT 0,
        created_at TEXT DEFAULT CURRENT_TIMESTAMP
    )''')
    conn.commit()
    conn.close()

init_db()

# ─── AUTH ─────────────────────────────────────────────────
def hash_password(password: str) -> str:
    return hashlib.sha256(password.encode()).hexdigest()

def get_user_by_email(email: str) -> dict | None:
    conn = get_db()
    cur = conn.cursor()
    cur.execute("SELECT * FROM web_users WHERE email = ?", (email,))
    row = cur.fetchone()
    conn.close()
    return dict(row) if row else None

def get_user_by_id(user_id: int) -> dict | None:
    conn = get_db()
    cur = conn.cursor()
    cur.execute("SELECT * FROM web_users WHERE id = ?", (user_id,))
    row = cur.fetchone()
    conn.close()
    return dict(row) if row else None

def current_user(request: Request) -> dict | None:
    user_id = request.session.get("user_id")
    if not user_id:
        return None
    return get_user_by_id(user_id)

# ─── DOCUMENTOS ───────────────────────────────────────────
def get_user_docs(user_id: int) -> list:
    conn = get_db()
    cur = conn.cursor()
    cur.execute("SELECT * FROM web_documentos WHERE user_id = ?", (user_id,))
    rows = cur.fetchall()
    conn.close()
    return [dict(r) for r in rows]

def add_doc_record(user_id: int, doc_id: str, filename: str, chunks: int):
    conn = get_db()
    cur = conn.cursor()
    cur.execute(
        "INSERT INTO web_documentos (doc_id, user_id, filename, chunks) VALUES (?,?,?,?)",
        (doc_id, user_id, filename, chunks)
    )
    conn.commit()
    conn.close()

def delete_doc_record(doc_id: str, user_id: int):
    conn = get_db()
    cur = conn.cursor()
    cur.execute("DELETE FROM web_documentos WHERE doc_id = ? AND user_id = ?", (doc_id, user_id))
    conn.commit()
    conn.close()

# ─── EMAIL ────────────────────────────────────────────────
def generar_codigo() -> str:
    return ''.join(random.choices(string.digits, k=6))

def send_verification_email(email: str, codigo: str) -> bool:
    try:
        smtp_host = os.getenv("SMTP_HOST")
        smtp_port = int(os.getenv("SMTP_PORT", "465"))
        smtp_user = os.getenv("SMTP_USER")
        smtp_pass = os.getenv("SMTP_PASSWORD")
        from_email = os.getenv("SMTP_FROM_EMAIL")
        from_name = os.getenv("SMTP_FROM_NAME", "ExpertIA")

        msg = MIMEMultipart("alternative")
        msg["Subject"] = "Tu código de verificación — ExpertIA"
        msg["From"] = f"{from_name} <{from_email}>"
        msg["To"] = email

        html = f"""<html><body style="font-family:Arial;background:#030608;color:#e0f0f5;padding:40px;">
        <div style="max-width:500px;margin:0 auto;background:rgba(8,13,16,0.9);border:1px solid rgba(0,242,255,0.15);border-radius:20px;padding:40px;">
        <h1 style="color:#00f2ff;text-align:center;">⚡ ExpertIA</h1>
        <p style="text-align:center;color:#5a7a85;">Verifica tu email para activar tu cuenta</p>
        <div style="background:rgba(0,242,255,0.1);border:2px solid #00f2ff;border-radius:12px;padding:20px;text-align:center;">
        <p style="color:#5a7a85;font-size:14px;">Tu código de verificación:</p>
        <p style="font-size:36px;font-weight:bold;color:#00ff9d;letter-spacing:8px;margin:20px 0;">{codigo}</p>
        <p style="color:#5a7a85;font-size:12px;">Válido por 10 minutos</p>
        </div></div></body></html>"""

        msg.attach(MIMEText(html, "html"))
        with smtplib.SMTP_SSL(smtp_host, smtp_port) as server:
            server.login(smtp_user, smtp_pass)
            server.send_message(msg)
        return True
    except Exception as e:
        log.error(f"Error email: {e}")
        return False

# ─── TTS ──────────────────────────────────────────────────
def generar_audio_tts(texto: str) -> str | None:
    try:
        import google.auth
        import google.auth.transport.requests

        texto = re.sub(r'\[.*?\.pdf\]', '', texto)
        texto = re.sub(r'Del documento.*', '', texto)
        texto = texto.strip()[:4500]

        credentials, _ = google.auth.default(
            scopes=["https://www.googleapis.com/auth/cloud-platform"]
        )
        auth_req = google.auth.transport.requests.Request()
        credentials.refresh(auth_req)
        token = credentials.token

        GCP_PROJECT = os.getenv("GCP_PROJECT_ID", "cleanbot-8f137")
        base_url = f"https://us-central1-aiplatform.googleapis.com/v1beta1/projects/{GCP_PROJECT}/locations/us-central1/publishers/google/models"
        headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}

        resp = requests.post(
            f"{base_url}/gemini-3.1-flash-tts-preview:generateContent",
            json={
                "contents": [{"role": "user", "parts": [{"text": texto}]}],
                "generationConfig": {
                    "responseModalities": ["AUDIO"],
                    "speechConfig": {"voiceConfig": {"prebuiltVoiceConfig": {"voiceName": "Kore"}}}
                }
            },
            headers=headers, timeout=120
        )

        if resp.status_code != 200:
            return None

        audio_pcm = base64.b64decode(resp.json()["candidates"][0]["content"]["parts"][0]["inlineData"]["data"])

        # PCM → WAV
        sample_rate = 24000
        channels = 1
        bits = 16
        wav_header = struct.pack('<4sI4s4sIHHIIHH4sI',
            b'RIFF', 36 + len(audio_pcm), b'WAVE',
            b'fmt ', 16, 1, channels, sample_rate,
            sample_rate * channels * bits // 8,
            channels * bits // 8, bits,
            b'data', len(audio_pcm)
        )
        wav_bytes = wav_header + audio_pcm
        return base64.b64encode(wav_bytes).decode()
    except Exception as e:
        log.error(f"Error TTS: {e}")
        return None

# ─── PODCAST JOBS ─────────────────────────────────────────
podcast_jobs = {}

def run_podcast_job(job_id: str, respuesta: str, estilo: str):
    try:
        podcast_jobs[job_id]["status"] = "processing"
        audio_b64 = generar_podcast(respuesta, estilo)
        podcast_jobs[job_id]["status"] = "done"
        podcast_jobs[job_id]["audio"] = audio_b64
    except Exception as e:
        log.error(f"Error job podcast {job_id}: {e}")
        podcast_jobs[job_id]["status"] = "error"
        podcast_jobs[job_id]["error"] = str(e)

# ─── RUTAS PÚBLICAS ───────────────────────────────────────
@app.get("/", response_class=HTMLResponse)
async def landing(request: Request):
    user = current_user(request)
    if user:
        return RedirectResponse("/dashboard")
    return render("landing.html")

@app.get("/login", response_class=HTMLResponse)
async def login_get(request: Request):
    if current_user(request):
        return RedirectResponse("/dashboard")
    return render("login.html")

@app.post("/login")
@limiter.limit("5/minute")
async def login_post(request: Request):
    data = await request.form()
    email = data.get("email", "").strip()
    password = data.get("password", "").strip()

    user = get_user_by_email(email)
    if not user or user["password"] != hash_password(password):
        return render("login.html", error="Email o contraseña incorrectos")
    if not user["verificado"]:
        return render("login.html", error="Debés verificar tu email primero")

    request.session["user_id"] = user["id"]
    return RedirectResponse("/dashboard", status_code=302)

@app.get("/register", response_class=HTMLResponse)
async def register_get(request: Request):
    if current_user(request):
        return RedirectResponse("/dashboard")
    return render("register.html")

@app.post("/register")
@limiter.limit("3/minute")
async def register_post(request: Request):
    data = await request.form()
    email = data.get("email", "").strip()
    password = data.get("password", "").strip()
    nombre = data.get("nombre", "").strip()

    if not email or not password or len(password) < 6:
        return render("register.html", error="Email o contraseña inválidos")

    if get_user_by_email(email):
        return render("register.html", error="Ese email ya está registrado")

    try:
        conn = get_db()
        cur = conn.cursor()
        # Guardar código de verificación
        codigo = generar_codigo()
        expires_at = (datetime.now() + timedelta(minutes=10)).isoformat()
        cur.execute("DELETE FROM verificaciones WHERE email = ?", (email,))
        cur.execute(
            "INSERT INTO verificaciones (email, codigo, expires_at) VALUES (?, ?, ?)",
            (email, codigo, expires_at)
        )
        conn.commit()
        conn.close()

        if send_verification_email(email, codigo):
            request.session["registro_temporal"] = {
                "email": email,
                "password": password,
                "nombre": nombre
            }
            return RedirectResponse("/verificar", status_code=303)
        else:
            return render("register.html", error="Error al enviar email. Intenta de nuevo.")
    except Exception as e:
        return render("register.html", error=str(e))

@app.get("/verificar", response_class=HTMLResponse)
async def verificar_get(request: Request):
    if "registro_temporal" not in request.session:
        return RedirectResponse("/register")
    return render("verificar.html")

@app.post("/verificar")
async def verificar_post(request: Request):
    data = await request.form()
    codigo_ingresado = data.get("codigo", "").strip()

    if "registro_temporal" not in request.session:
        return RedirectResponse("/register")

    temp = request.session["registro_temporal"]
    email = temp["email"]

    try:
        conn = get_db()
        cur = conn.cursor()
        cur.execute(
            "SELECT codigo, expires_at, intentos FROM verificaciones WHERE email = ? AND verificado = 0 ORDER BY created_at DESC LIMIT 1",
            (email,)
        )
        row = cur.fetchone()

        if not row:
            conn.close()
            return render("verificar.html", error="Código expirado. Registrate de nuevo.")

        codigo_guardado, expires_at, intentos = row

        if datetime.fromisoformat(expires_at) < datetime.now():
            conn.close()
            return render("verificar.html", error="Código expirado. Solicitá uno nuevo.")

        if intentos >= 3:
            conn.close()
            return render("verificar.html", error="Demasiados intentos. Solicitá un código nuevo.")

        if codigo_ingresado != codigo_guardado:
            cur.execute("UPDATE verificaciones SET intentos = intentos + 1 WHERE email = ?", (email,))
            conn.commit()
            conn.close()
            return render("verificar.html", error=f"Código incorrecto. {3 - (intentos + 1)} intentos restantes.")

        # Crear usuario
        cur.execute(
            "INSERT INTO web_users (email, password, nombre, verificado) VALUES (?, ?, ?, 1)",
            (email, hash_password(temp["password"]), temp["nombre"])
        )
        user_id = cur.lastrowid
        cur.execute("UPDATE verificaciones SET verificado = 1 WHERE email = ?", (email,))
        conn.commit()
        conn.close()

        del request.session["registro_temporal"]
        request.session["user_id"] = user_id
        alert_nuevo_usuario(email, temp.get("nombre", ""))
        return RedirectResponse("/dashboard", status_code=303)
    except Exception as e:
        return render("verificar.html", error=str(e))

@app.post("/reenviar-codigo")
async def reenviar_codigo(request: Request):
    if "registro_temporal" not in request.session:
        return RedirectResponse("/register")

    email = request.session["registro_temporal"]["email"]
    try:
        codigo = generar_codigo()
        expires_at = (datetime.now() + timedelta(minutes=10)).isoformat()
        conn = get_db()
        cur = conn.cursor()
        cur.execute("DELETE FROM verificaciones WHERE email = ? AND verificado = 0", (email,))
        cur.execute("INSERT INTO verificaciones (email, codigo, expires_at) VALUES (?, ?, ?)", (email, codigo, expires_at))
        conn.commit()
        conn.close()

        if send_verification_email(email, codigo):
            return render("verificar.html", success="Código reenviado. Revisá tu email.")
        else:
            return render("verificar.html", error="Error al reenviar. Intentá de nuevo.")
    except Exception as e:
        return render("verificar.html", error=str(e))

@app.get("/logout")
async def logout(request: Request):
    request.session.clear()
    return RedirectResponse("/")

@app.get("/planes", response_class=HTMLResponse)
async def planes(request: Request):
    user = current_user(request)
    return render("planes.html", user=user)

@app.get("/privacidad", response_class=HTMLResponse)
async def privacidad(request: Request):
    return render("privacidad.html", user=None)

# ─── RUTAS PRIVADAS ───────────────────────────────────────
@app.get("/dashboard", response_class=HTMLResponse)
async def dashboard(request: Request):
    user = current_user(request)
    if not user:
        return RedirectResponse("/login")
    docs = get_user_docs(user["id"])
    return render("dashboard.html", user=user, docs=docs,
                  free_pdf_limit=FREE_PDF_LIMIT,
                  is_pro=user.get("plan") == "pro")

@app.get("/chat", response_class=HTMLResponse)
async def chat_page(request: Request):
    user = current_user(request)
    if not user:
        return RedirectResponse("/login")
    docs = get_user_docs(user["id"])
    log.info(f"CHAT DEBUG: user_id={user['id']} docs={len(docs)}")
    return render("chat.html", user=user, docs=docs,
                  is_pro=user.get("plan") == "pro")

@app.post("/upload")
@limiter.limit("10/minute")
async def upload_file(request: Request, file: UploadFile = File(...)):
    user = current_user(request)
    if not user:
        return JSONResponse({"error": "No autorizado"}, status_code=401)

    docs = get_user_docs(user["id"])
    if user.get("plan") != "pro" and len(docs) >= FREE_PDF_LIMIT:
        return JSONResponse({
            "error": f"Plan gratuito: máximo {FREE_PDF_LIMIT} documentos.",
            "upgrade": True
        }, status_code=403)

    if not is_supported(file.filename):
        return JSONResponse({"error": "Solo se aceptan PDF, DOCX y DOC"}, status_code=400)

    try:
        user_dir = UPLOADS_DIR / str(user["id"])
        user_dir.mkdir(exist_ok=True)
        ext = get_extension(file.filename) or ".pdf"
        file_path = user_dir / f"{uuid.uuid4()}{ext}"
        content = await file.read()

        with open(file_path, "wb") as f:
            f.write(content)

        # Validar archivo
        es_valido, motivo = validate_file(str(file_path), file.filename, len(content))
        if not es_valido:
            os.remove(file_path)
            alert_archivo_malicioso(user.get("email", "desconocido"), file.filename, motivo)
            return JSONResponse({"error": motivo}, status_code=400)

        doc_id, chunks = index_pdf(user["id"], str(file_path), file.filename)
        add_doc_record(user["id"], doc_id, file.filename, chunks)

        return JSONResponse({
            "success": True,
            "doc_id": doc_id,
            "filename": file.filename,
            "chunks": chunks
        })
    except Exception as e:
        log.error(f"Error upload: {e}")
        return JSONResponse({"error": str(e)}, status_code=500)

@app.delete("/document/{doc_id}")
async def delete_document(doc_id: str, request: Request):
    user = current_user(request)
    if not user:
        return JSONResponse({"error": "No autorizado"}, status_code=401)
    try:
        col = get_collection(user["id"])
        results = col.get(where={"doc_id": doc_id})
        if results["ids"]:
            col.delete(ids=results["ids"])
        delete_doc_record(doc_id, user["id"])
        return JSONResponse({"success": True})
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)

@app.post("/chat/message")
@limiter.limit("30/minute")
async def chat_message(request: Request, message: str = Form(...)):
    user = current_user(request)
    if not user:
        return JSONResponse({"error": "No autorizado"}, status_code=401)
    # Sanitizar y verificar anti prompt injection
    message = sanitize_text(message)
    es_seguro, motivo = check_prompt_injection(message)
    if not es_seguro:
        alert_injection(user.get("email", "desconocido"), message)
        return JSONResponse({"error": motivo}, status_code=400)

    try:
        result = query_rag(user["id"], message, user.get("profession", "General"))
        return JSONResponse({
            "answer": result["answer"],
            "sources": result["sources"]
        })
    except Exception as e:
        log.error(f"Error chat: {e}")
        return JSONResponse({"error": str(e)}, status_code=500)

# ─── TTS ──────────────────────────────────────────────────
@app.post("/api/tts")
async def tts_endpoint(request: Request):
    user = current_user(request)
    if not user:
        return JSONResponse({"error": "No autorizado"}, status_code=401)
    try:
        data = await request.json()
        texto = data.get("texto", "")
        audio_b64 = generar_audio_tts(texto)
        if audio_b64:
            return JSONResponse({"audio": audio_b64})
        return JSONResponse({"error": "Error generando audio"}, status_code=500)
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)

# ─── PODCAST ──────────────────────────────────────────────
@app.get("/api/podcast/estilos")
async def podcast_estilos(request: Request):
    user = current_user(request)
    if not user:
        return JSONResponse({"error": "No autorizado"}, status_code=401)
    return JSONResponse({"estilos": get_estilos()})

@app.post("/api/podcast")
async def podcast_endpoint(request: Request):
    user = current_user(request)
    if not user:
        return JSONResponse({"error": "No autorizado"}, status_code=401)
    try:
        data = await request.json()
        respuesta = data.get("respuesta", "")
        estilo = data.get("estilo", "cafe")
        if not respuesta:
            return JSONResponse({"error": "Sin contenido"}, status_code=400)
        job_id = str(uuid.uuid4())
        podcast_jobs[job_id] = {"status": "pending", "audio": None, "error": None}
        t = threading.Thread(target=run_podcast_job, args=(job_id, respuesta, estilo), daemon=True)
        t.start()
        return JSONResponse({"job_id": job_id})
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)

@app.get("/api/podcast/status/{job_id}")
async def podcast_status(job_id: str, request: Request):
    user = current_user(request)
    if not user:
        return JSONResponse({"error": "No autorizado"}, status_code=401)
    job = podcast_jobs.get(job_id)
    if not job:
        return JSONResponse({"error": "Job no encontrado"}, status_code=404)
    return JSONResponse(job)

# ─── MERCADOPAGO ──────────────────────────────────────────
@app.get("/pago/iniciar")
async def pago_iniciar(request: Request):
    user = current_user(request)
    if not user:
        return RedirectResponse("/login")
    try:
        result = crear_preferencia(user["id"], user.get("email", ""), user.get("nombre", ""))
        if result["success"]:
            return RedirectResponse(result["init_point"])
        return render("planes.html", user=user, error="Error al iniciar el pago.")
    except Exception as e:
        return render("planes.html", user=user, error=str(e))

@app.get("/pago/exitoso")
async def pago_exitoso(request: Request):
    user = current_user(request)
    payment_id = request.query_params.get("payment_id")
    status = request.query_params.get("status")
    if status == "approved" and payment_id:
        info = get_payment_info(payment_id)
        if info.get("status") == "approved":
            activar_plan_pro(user["id"])
            alert_nuevo_pago(user.get("email", "desconocido"))
            if user:
                request.session["user_id"] = user["id"]
    return render("pago_exitoso.html", user=user)

@app.get("/pago/fallido")
async def pago_fallido(request: Request):
    user = current_user(request)
    return render("planes.html", user=user, error="El pago no se completó.")

@app.get("/pago/pendiente")
async def pago_pendiente(request: Request):
    user = current_user(request)
    return render("planes.html", user=user, error="Tu pago está pendiente de acreditación.")

@app.post("/mp/webhook")
async def mp_webhook(request: Request):
    try:
        data = await request.json()
        topic = data.get("type") or request.query_params.get("topic")
        if topic == "payment":
            payment_id = str(data.get("data", {}).get("id") or request.query_params.get("id"))
            info = get_payment_info(payment_id)
            if info.get("status") == "approved":
                user_id = int(info.get("external_reference", 0))
                if user_id:
                    activar_plan_pro(user_id)
        return JSONResponse({"status": "ok"})
    except Exception as e:
        log.error(f"Error webhook MP: {e}")
        return JSONResponse({"status": "error"}, status_code=500)

# ─── MAIN ─────────────────────────────────────────────────

# ─── RESET CONTRASEÑA ─────────────────────────────────────
@app.get("/reset", response_class=HTMLResponse)
async def reset_get(request: Request):
    return render("reset.html")

@app.post("/reset")
@limiter.limit("3/minute")
async def reset_post(request: Request):
    data = await request.form()
    email = data.get("email", "").strip()

    user = get_user_by_email(email)
    if not user:
        return render("reset.html", error="No existe una cuenta con ese email.")

    try:
        codigo = generar_codigo()
        expires_at = (datetime.now() + timedelta(minutes=10)).isoformat()

        conn = get_db()
        cur = conn.cursor()
        cur.execute("DELETE FROM password_resets WHERE email = ?", (email,))
        cur.execute(
            "INSERT INTO password_resets (email, codigo, expires_at) VALUES (?, ?, ?)",
            (email, codigo, expires_at)
        )
        conn.commit()
        conn.close()

        # Reusar función de email
        if send_verification_email(email, codigo):
            request.session["reset_email"] = email
            return RedirectResponse("/reset/verificar", status_code=303)
        else:
            return render("reset.html", error="Error al enviar email. Intenta de nuevo.")
    except Exception as e:
        return render("reset.html", error=str(e))

@app.get("/reset/verificar", response_class=HTMLResponse)
async def reset_verificar_get(request: Request):
    if "reset_email" not in request.session:
        return RedirectResponse("/reset")
    return render("reset_verificar.html")

@app.post("/reset/verificar")
async def reset_verificar_post(request: Request):
    if "reset_email" not in request.session:
        return RedirectResponse("/reset")

    data = await request.form()
    codigo = data.get("codigo", "").strip()
    password = data.get("password", "").strip()
    password2 = data.get("password2", "").strip()
    email = request.session["reset_email"]

    if len(password) < 6:
        return render("reset_verificar.html", error="La contraseña debe tener al menos 6 caracteres.")

    if password != password2:
        return render("reset_verificar.html", error="Las contraseñas no coinciden.")

    try:
        conn = get_db()
        cur = conn.cursor()
        cur.execute(
            "SELECT codigo, expires_at, intentos FROM password_resets WHERE email = ? AND usado = 0 ORDER BY created_at DESC LIMIT 1",
            (email,)
        )
        row = cur.fetchone()

        if not row:
            conn.close()
            return render("reset_verificar.html", error="Código expirado. Solicitá uno nuevo.")

        codigo_guardado, expires_at, intentos = row

        if datetime.fromisoformat(expires_at) < datetime.now():
            conn.close()
            return render("reset_verificar.html", error="Código expirado. Solicitá uno nuevo.")

        if intentos >= 3:
            conn.close()
            return render("reset_verificar.html", error="Demasiados intentos. Solicitá un código nuevo.")

        if codigo != codigo_guardado:
            cur.execute("UPDATE password_resets SET intentos = intentos + 1 WHERE email = ?", (email,))
            conn.commit()
            conn.close()
            return render("reset_verificar.html", error=f"Código incorrecto. {3 - (intentos + 1)} intentos restantes.")

        # Cambiar contraseña
        cur.execute("UPDATE web_users SET password = ? WHERE email = ?", (hash_password(password), email))
        cur.execute("UPDATE password_resets SET usado = 1 WHERE email = ?", (email,))
        conn.commit()
        conn.close()

        del request.session["reset_email"]
        return render("login.html", success="¡Contraseña actualizada! Podés iniciar sesión.")
    except Exception as e:
        return render("reset_verificar.html", error=str(e))

@app.post("/reset/reenviar")
async def reset_reenviar(request: Request):
    if "reset_email" not in request.session:
        return RedirectResponse("/reset")

    email = request.session["reset_email"]
    try:
        codigo = generar_codigo()
        expires_at = (datetime.now() + timedelta(minutes=10)).isoformat()

        conn = get_db()
        cur = conn.cursor()
        cur.execute("DELETE FROM password_resets WHERE email = ?", (email,))
        cur.execute(
            "INSERT INTO password_resets (email, codigo, expires_at) VALUES (?, ?, ?)",
            (email, codigo, expires_at)
        )
        conn.commit()
        conn.close()

        if send_verification_email(email, codigo):
            return render("reset_verificar.html", success="Código reenviado. Revisá tu email.")
        else:
            return render("reset_verificar.html", error="Error al reenviar. Intentá de nuevo.")
    except Exception as e:
        return render("reset_verificar.html", error=str(e))


if __name__ == "__main__":
    alert_servidor_inicio()
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)