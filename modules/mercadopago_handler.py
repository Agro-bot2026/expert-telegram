"""
Módulo MercadoPago — Pagos y webhooks para Plan Pro.
"""
import os
import hmac
import hashlib
import logging
import sqlite3
import mercadopago

log = logging.getLogger(__name__)

DB_PATH = os.getenv("DB_PATH", "/root/ExpertTelegram/expertia.db")
MP_ACCESS_TOKEN = os.getenv("MP_ACCESS_TOKEN", "")
MP_WEBHOOK_SECRET = os.getenv("MP_WEBHOOK_SECRET", "")
APP_URL = os.getenv("APP_URL", "https://expert-ai.vip")
PRECIO_PRO = 10000  # ARS

sdk = mercadopago.SDK(MP_ACCESS_TOKEN)

def crear_preferencia(user_id: int, email: str, nombre: str) -> dict:
    """Crea una preferencia de pago en MercadoPago."""
    preference_data = {
        "items": [
            {
                "title": "ExpertIA Plan Pro",
                "description": "PDFs ilimitados, preguntas ilimitadas, soporte prioritario",
                "quantity": 1,
                "currency_id": "ARS",
                "unit_price": float(PRECIO_PRO)
            }
        ],
        "payer": {
            "email": email,
            "name": nombre or "Usuario"
        },
        "back_urls": {
            "success": f"{APP_URL}/pago/exitoso",
            "failure": f"{APP_URL}/pago/fallido",
            "pending": f"{APP_URL}/pago/pendiente"
        },
        "auto_return": "approved",
        "notification_url": f"{APP_URL}/mp/webhook",
        "external_reference": str(user_id),
        "statement_descriptor": "ExpertIA Pro"
    }

    result = sdk.preference().create(preference_data)
    if result["status"] == 201:
        return {
            "success": True,
            "init_point": result["response"]["init_point"],
            "preference_id": result["response"]["id"]
        }
    else:
        log.error(f"Error MP: {result}")
        return {"success": False, "error": str(result)}

def verificar_webhook(payload: bytes, signature: str) -> bool:
    """Verifica la firma del webhook de MercadoPago."""
    if not MP_WEBHOOK_SECRET:
        return True
    expected = hmac.new(
        MP_WEBHOOK_SECRET.encode(),
        payload,
        hashlib.sha256
    ).hexdigest()
    return hmac.compare_digest(expected, signature)

def activar_plan_pro(user_id: int) -> bool:
    """Activa el plan Pro para un usuario."""
    try:
        from datetime import datetime, timedelta
        pro_until = (datetime.now() + timedelta(days=30)).strftime("%Y-%m-%d")
        conn = sqlite3.connect(DB_PATH)
        cur = conn.cursor()
        cur.execute(
            "UPDATE users SET plan = 'pro', pro_until = ? WHERE id = ?",
            (pro_until, user_id)
        )
        conn.commit()
        conn.close()
        log.info(f"Plan Pro activado para user_id={user_id} hasta {pro_until}")
        return True
    except Exception as e:
        log.error(f"Error activando Pro: {e}")
        return False

def get_payment_info(payment_id: str) -> dict:
    """Obtiene información de un pago."""
    result = sdk.payment().get(payment_id)
    if result["status"] == 200:
        return result["response"]
    return {}
