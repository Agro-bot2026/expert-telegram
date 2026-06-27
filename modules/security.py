"""
Módulo de seguridad — Validación de archivos y anti prompt injection.
"""
import os
import re
import logging

log = logging.getLogger(__name__)

# ─── ANTI PROMPT INJECTION ────────────────────────────────
INJECTION_PATTERNS = [
    r'ignore\s+(previous|all)\s+instructions',
    r'olvida\s+(las\s+instrucciones|todo)',
    r'act\s+as\s+if',
    r'pretend\s+you\s+are',
    r'you\s+are\s+now\s+',
    r'system\s*prompt',
    r'jailbreak',
    r'dan\s+mode',
    r'developer\s+mode',
    r'modo\s+desarrollador',
    r'bypass\s+(security|restrictions)',
    r'<\s*script',
    r'javascript\s*:',
    r'__import__\s*\(',
    r'os\s*\.\s*system\s*\(',
    r'eval\s*\(',
    r'exec\s*\(',
    r'\{\{.*\}\}',
]

def check_prompt_injection(text: str) -> tuple[bool, str]:
    """
    Verifica si el texto contiene intentos de prompt injection.
    Retorna (es_seguro, motivo)
    """
    if len(text) > 2000:
        return False, "La consulta es demasiado larga. Máximo 2000 caracteres."

    text_lower = text.lower()
    for pattern in INJECTION_PATTERNS:
        if re.search(pattern, text_lower, re.IGNORECASE):
            log.warning(f"Prompt injection detectado: {pattern}")
            return False, "Consulta bloqueada por seguridad."

    return True, ""

def sanitize_text(text: str) -> str:
    """Limpia el texto de caracteres peligrosos."""
    text = re.sub(r'[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]', '', text)
    return text[:2000].strip()

# ─── VALIDACIÓN DE ARCHIVOS ───────────────────────────────
MAX_FILE_SIZE = 50 * 1024 * 1024  # 50MB

PDF_MAGIC = b'%PDF'
DOCX_MAGIC = b'PK\x03\x04'
DOC_MAGIC = b'\xd0\xcf\x11\xe0'

ALLOWED_EXTENSIONS = ['.pdf', '.docx', '.doc']

DANGEROUS_PATTERNS = ['.exe', '.bat', '.sh', '.js', '.py', '.php', '.cmd', '.vbs']

def validate_file(file_path: str, filename: str, file_size: int) -> tuple[bool, str]:
    """
    Valida un archivo subido.
    Retorna (es_valido, motivo)
    """
    # 1. Tamaño
    if file_size > MAX_FILE_SIZE:
        return False, "Archivo demasiado grande. Máximo 50MB."

    # 2. Extensión
    ext = os.path.splitext(filename)[1].lower()
    if ext not in ALLOWED_EXTENSIONS:
        return False, "Solo se aceptan PDF, DOCX y DOC."

    # 3. Nombre sospechoso
    filename_lower = filename.lower()
    for danger in DANGEROUS_PATTERNS:
        if danger in filename_lower and not filename_lower.endswith(tuple(ALLOWED_EXTENSIONS)):
            return False, "Nombre de archivo sospechoso."

    # 4. Magic bytes — verificar que el contenido coincide con la extensión
    try:
        with open(file_path, 'rb') as f:
            header = f.read(8)

        if ext == '.pdf':
            if not header.startswith(PDF_MAGIC):
                return False, "El archivo no es un PDF válido."
        elif ext == '.docx':
            if not header.startswith(DOCX_MAGIC):
                return False, "El archivo no es un DOCX válido."
        elif ext == '.doc':
            if not header.startswith(DOC_MAGIC) and not header.startswith(DOCX_MAGIC):
                return False, "El archivo no es un DOC válido."

    except Exception as e:
        log.error(f"Error validando archivo: {e}")
        return False, "No se pudo validar el archivo."

    return True, ""
