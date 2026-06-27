# ExpertIA — Telegram + Web

Plataforma de IA documental: subís documentos y la IA responde preguntas sobre ellos (RAG), genera informes en PDF y podcasts. Tiene dos caras que comparten la misma base de datos y credenciales:

- **Web** (`main.py`) — aplicación FastAPI con registro de usuarios, verificación por email, planes de pago vía MercadoPago y chat documental.
- **Bot de Telegram** (`bot.py`) — acceso a las mismas funciones desde Telegram, con análisis de archivos vía VirusTotal.

**Tecnología:** Python + FastAPI (web) + python-telegram-bot. Usa Vertex AI (Gemini) para IA, ChromaDB para RAG, MercadoPago para pagos, SMTP para emails y SQLite como base de datos.

---

## Requisitos del servidor

- **Sistema:** Ubuntu / Debian (probado en VPS)
- **Python:** versión 3.10 o superior
- **pm2:** para mantener los procesos corriendo
- Una **cuenta de servicio de Google Cloud** con Vertex AI (`llave.json`)
- Cuentas/credenciales de: **Telegram** (BotFather), **MercadoPago**, **VirusTotal**, y un **servidor SMTP** para enviar emails

---

## Instalación paso a paso

### 1. Clonar el repositorio

```bash
cd ~
git clone https://github.com/Agro-bot2026/expert-telegram.git ExpertTelegram
cd ExpertTelegram
```

### 2. Instalar Python, entorno virtual y pm2 (si el VPS es nuevo)

```bash
sudo apt update
sudo apt install -y python3 python3-venv python3-pip nodejs npm
sudo npm install -g pm2
```

### 3. Crear el entorno virtual e instalar dependencias

```bash
python3 -m venv venv
source venv/bin/activate
pip install --upgrade pip
pip install -r requirements.txt
```

> Nota: en el servidor original, `venv` era un enlace al venv del BotContador. Acá creamos uno propio, que es lo más limpio.

### 4. Colocar la credencial de Google (`llave.json`)

No viene en el repo. Copiala desde tu backup a la carpeta del proyecto:

```bash
# debe quedar en: ~/ExpertTelegram/llave.json
```

### 5. Configurar el archivo `.env` (¡el paso clave!)

El proyecto lee TODAS sus credenciales del `.env`, que no viene en el repo. Creá el archivo:

```bash
nano .env
```

Y completá con tus valores reales (estas son todas las variables que usa el proyecto):

```
# --- Telegram ---
TELEGRAM_TOKEN=tu_token_de_telegram

# --- Google Cloud / Vertex AI ---
GCP_PROJECT_ID=tu_proyecto_gcp

# --- MercadoPago (pagos) ---
MP_ACCESS_TOKEN=tu_access_token_de_mercadopago
MP_WEBHOOK_SECRET=tu_secreto_de_webhook

# --- VirusTotal (análisis de archivos) ---
VIRUSTOTAL_API_KEY=tu_api_key_de_virustotal

# --- Email / SMTP ---
SMTP_HOST=smtp.tuservidor.com
SMTP_PORT=587
SMTP_USER=tu_usuario_smtp
SMTP_PASSWORD=tu_password_smtp
SMTP_FROM_EMAIL=noreply@tudominio.com
SMTP_FROM_NAME=ExpertIA

# --- Seguridad web ---
SECRET_KEY=una_clave_larga_y_aleatoria_propia

# --- Alertas (bot de Telegram que avisa eventos) ---
ALERT_BOT_TOKEN=token_del_bot_de_alertas
ALERT_CHAT_ID=id_del_chat_de_alertas
ADMIN_CHAT_ID=tu_chat_id_de_admin

# --- Rutas y configuración ---
APP_URL=https://tudominio.com
DB_PATH=expertia.db
CHROMA_PATH=chroma_db
UPLOADS_DIR=uploads
FREE_QUESTION_LIMIT=10
FREE_PDF_LIMIT=3
```

> Guardá con `Ctrl+O`, Enter, y salí con `Ctrl+X`.
> Los límites (`FREE_QUESTION_LIMIT`, `FREE_PDF_LIMIT`) controlan cuántas consultas/PDFs gratis tiene un usuario antes del plan Pro. Ajustalos a gusto.
>
> ---

## Arrancar los servicios

Son dos procesos: la web y el bot. Se levantan con pm2.

### La web (FastAPI + uvicorn, puerto 8000)

```bash
pm2 start main.py --name expertia-web --interpreter ~/ExpertTelegram/venv/bin/python3
```

### El bot de Telegram

```bash
pm2 start bot.py --name expertia-bot --interpreter ~/ExpertTelegram/venv/bin/python3
```

Guardá la configuración para que levanten solos al reiniciar el VPS:

```bash
pm2 save
```

> La web queda escuchando en el puerto 8000. En producción conviene poner un nginx por delante como reverse proxy (con el dominio y el SSL).

---

## Comandos útiles de pm2

| Acción | Comando |
|---|---|
| Ver procesos | `pm2 list` |
| Logs de la web | `pm2 logs expertia-web` |
| Logs del bot | `pm2 logs expertia-bot` |
| Reiniciar la web | `pm2 restart expertia-web` |
| Reiniciar el bot | `pm2 restart expertia-bot` |

---

## Estructura del proyecto

```
ExpertTelegram/
├── main.py                       # Web FastAPI (registro, pagos, chat)
├── bot.py                        # Bot de Telegram
├── modules/
│   ├── mercadopago_handler.py    # Pagos y webhooks
│   ├── rag.py                    # Búsqueda sobre documentos (RAG)
│   ├── podcast.py                # Generación de podcasts
│   ├── docx_handler.py           # Manejo de documentos Word
│   ├── security.py               # Funciones de seguridad
│   └── monitor.py                # Monitoreo
├── templates/                    # Páginas HTML de la web
├── static/                       # CSS, imágenes
├── requirements.txt              # Dependencias de Python
├── limpiar_backups.sh            # Script de mantenimiento de backups
├── .env                          # ⚠️ NO incluido - TODAS las credenciales
├── llave.json                    # ⚠️ NO incluido - credencial de Google Cloud
├── expertia.db                   # ⚠️ NO incluido - base de datos (usuarios y pagos)
├── uploads/                      # ⚠️ NO incluido - archivos de usuarios
├── backups/                      # ⚠️ NO incluido - respaldos
└── chroma_db/                    # ⚠️ NO incluido - base vectorial
```

---

## Archivos que NO vienen en el repo

Por seguridad y privacidad, estos quedan fuera de GitHub:

- **`.env`** y todos sus backups — TODAS las credenciales (Telegram, MercadoPago, SMTP, VirusTotal)
- **`llave.json`** — credencial de Google Cloud / Vertex AI
- **`expertia.db`** — base de datos con usuarios, contraseñas (hasheadas) y pagos
- **`uploads/`** — documentos subidos por los usuarios
- **`backups/`** — respaldos (contienen `.env` y bases viejas)
- **`chroma_db/`** — base vectorial (se regenera al usar el sistema)
- **`venv/`** — entorno virtual (se regenera con `pip install -r requirements.txt`)

---

## Nota de seguridad

Este proyecto maneja **pagos reales y datos de clientes**. Al reinstalar:
- Nunca subas el `.env` ni la base de datos a ningún repositorio.
- Generá un `SECRET_KEY` nuevo y propio (no reutilices ejemplos).
- Si las credenciales de MercadoPago o SMTP se expusieron alguna vez, rotálas desde sus paneles.
