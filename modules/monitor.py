"""
Módulo de monitoreo — Alertas a Telegram en tiempo real.
"""
import os
import logging
import requests
from datetime import datetime
from dotenv import load_dotenv

load_dotenv("/root/ExpertTelegram/.env")

log = logging.getLogger(__name__)

ALERT_BOT_TOKEN = os.getenv("ALERT_BOT_TOKEN", "")
ALERT_CHAT_ID = os.getenv("ALERT_CHAT_ID", "")
APP_URL = os.getenv("APP_URL", "https://expert-ai.vip")

def send_alert(mensaje: str, nivel: str = "info"):
    """Envía alerta a Telegram."""
    if not ALERT_BOT_TOKEN or not ALERT_CHAT_ID:
        return

    iconos = {
        "info": "ℹ️",
        "warning": "⚠️",
        "error": "🔴",
        "success": "✅",
        "security": "🛡️"
    }

    icono = iconos.get(nivel, "📌")
    hora = datetime.now().strftime("%H:%M:%S")
    texto = f"{icono} *ExpertIA Alert*\n`{hora}`\n\n{mensaje}"

    try:
        requests.post(
            f"https://api.telegram.org/bot{ALERT_BOT_TOKEN}/sendMessage",
            json={
                "chat_id": ALERT_CHAT_ID,
                "text": texto,
                "parse_mode": "Markdown"
            },
            timeout=10
        )
    except Exception as e:
        log.error(f"Error enviando alerta: {e}")

def alert_nuevo_usuario(email: str, nombre: str):
    send_alert(f"👤 Nuevo usuario registrado\nEmail: `{email}`\nNombre: {nombre}", "success")

def alert_nuevo_pago(email: str, monto: str = "$10.000"):
    send_alert(f"💰 Nuevo pago Pro\nEmail: `{email}`\nMonto: {monto}", "success")

def alert_injection(email: str, mensaje: str):
    send_alert(f"Intento de prompt injection detectado\nUsuario: `{email}`\nMensaje: `{mensaje[:100]}`", "security")

def alert_archivo_malicioso(email: str, filename: str, motivo: str):
    send_alert(f"Archivo malicioso bloqueado\nUsuario: `{email}`\nArchivo: `{filename}`\nMotivo: {motivo}", "security")

def alert_rate_limit(ip: str, ruta: str):
    send_alert(f"Rate limit activado\nIP: `{ip}`\nRuta: `{ruta}`", "warning")

def alert_error(error: str, ruta: str = ""):
    send_alert(f"Error en servidor\nRuta: `{ruta}`\nError: `{error[:200]}`", "error")

def alert_servidor_inicio():
    send_alert(f"Servidor iniciado correctamente\nURL: {APP_URL}", "success")
