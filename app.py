import os
import io
import json
import uuid
import logging
from pathlib import Path

from flask import Flask, request, jsonify
from flask_cors import CORS
from werkzeug.utils import secure_filename
from pypdf import PdfReader

# Optional: Hugging Face model client
try:
    from huggingface_hub import InferenceClient
except ImportError:
    InferenceClient = None


# -----------------------------
# APP CONFIGURATION
# -----------------------------

app = Flask(__name__)

FRONTEND_ORIGIN = os.getenv("FRONTEND_ORIGIN", "*")

CORS(
    app,
    resources={r"/*": {"origins": FRONTEND_ORIGIN}}
)

app.config["MAX_CONTENT_LENGTH"] = 10 * 1024 * 1024

UPLOAD_DIR = Path(os.getenv("UPLOAD_DIR", "uploads"))
UPLOAD_DIR.mkdir(parents=True, exist_ok=True)

MODEL_NAME = os.getenv(
    "MODEL_NAME",
    "Qwen/Qwen2.5-0.5B-Instruct"
)

HF_TOKEN = os.getenv("HF_TOKEN")

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("NEXUS")


# -----------------------------
# ALLOWED FILE TYPES
# -----------------------------

ALLOWED_EXTENSIONS = {
    ".txt",
    ".md",
    ".pdf",
    ".csv",
    ".json",
    ".py",
    ".html",
    ".css",
    ".js",
    ".xml",
    ".log"
}

MAX_TEXT_CHARS = 12000


def allowed_file(filename):
    return Path(filename).suffix.lower() in ALLOWED_EXTENSIONS


def extract_text(filename, file_bytes):
    """
    Extract readable text from supported documents.
    Images are stored, but this text model cannot interpret them.
    """

    extension = Path(filename).suffix.lower()

    try:
        if extension == ".pdf":
            reader = PdfReader(io.BytesIO(file_bytes))
            pages = []

            for page in reader.pages[:20]:
                pages.append(page.extract_text() or "")

            return "\n".join(pages)[:MAX_TEXT_CHARS]

        if extension in ALLOWED_EXTENSIONS:
            return file_bytes.decode(
                "utf-8",
                errors="replace"
            )[:MAX_TEXT_CHARS]

    except Exception:
        logger.exception("Text extraction failed")

    return ""


# -----------------------------
# MODEL CONNECTION
# -----------------------------

def generate_ai_reply(message, history=None, files=None):
    """
    Uses a Hugging Face Inference API model.
    Set HF_TOKEN in your hosting environment.
    """

    if not HF_TOKEN:
        raise RuntimeError(
            "HF_TOKEN is not configured. "
            "Add your Hugging Face token in the server environment."
        )

    if InferenceClient is None:
        raise RuntimeError(
            "huggingface_hub is missing. "
            "Install it using requirements.txt."
        )

    client = InferenceClient(
        model=MODEL_NAME,
        token=HF_TOKEN
    )

    messages = [
        {
            "role": "system",
            "content": (
                "You are NEXUS, a helpful AI assistant. "
                "Answer clearly and accurately. "
                "If you do not know something, say so."
            )
        }
    ]

    # Keep only recent conversation turns.
    if isinstance(history, list):
        for item in history[-10:]:
            if not isinstance(item, dict):
                continue

            role = item.get("role")
            content = item.get("content", item.get("text", ""))

            if role in ("user", "assistant") and isinstance(content, str):
                messages.append({
                    "role": role,
                    "content": content[:4000]
                })

    # Include extracted document text when available.
    document_context = []

    if isinstance(files, list):
        for item in files[:5]:
            if not isinstance(item, dict):
                continue

            text_content = item.get("text", "")

            if isinstance(text_content, str) and text_content:
                document_context.append(
                    text_content[:MAX_TEXT_CHARS]
                )

    user_message = message[:6000]

    if document_context:
        user_message += (
            "\n\nUploaded document text:\n"
            + "\n\n--- Document ---\n".join(document_context)
        )

    messages.append({
        "role": "user",
        "content": user_message
    })

    result = client.chat_completion(
        messages=messages,
        max_tokens=700,
        temperature=0.7
    )

    return result.choices[0].message.content or ""


# -----------------------------
# HEALTH CHECK
# -----------------------------

@app.get("/")
def home():
    return jsonify({
        "app": "NEXUS AI",
        "status": "online",
        "message": "NEXUS backend is running"
    })


@app.get("/health")
def health():
    return jsonify({
        "status": "healthy",
        "model": MODEL_NAME,
        "model_token_configured": bool(HF_TOKEN)
    })


# -----------------------------
# CHAT API
# -----------------------------

@app.post("/chat")
def chat():
    data = request.get_json(silent=True) or {}

    message = data.get("message", "")
    history = data.get("history", [])
    files = data.get("files", [])

    if not isinstance(message, str) or not message.strip():
        return jsonify({
            "error": "Please enter a message."
        }), 400

    if len(message) > 6000:
        return jsonify({
            "error": "Message is too long."
        }), 400

    try:
        reply = generate_ai_reply(
            message=message.strip(),
            history=history,
            files=files
        )

        return jsonify({
            "reply": reply,
            "model": MODEL_NAME
        })

    except Exception:
        logger.exception("Chat request failed")

        return jsonify({
            "error": (
                "AI model request failed. Check your HF_TOKEN, "
                "model availability, and server logs."
            )
        }), 502


# -----------------------------
# FILE UPLOAD API
# -----------------------------

@app.post("/upload")
def upload():
    if "files" not in request.files:
        return jsonify({
            "error": "No files were uploaded."
        }), 400

    incoming_files = request.files.getlist("files")

    if not incoming_files:
        return jsonify({
            "error": "Please select a file."
        }), 400

    if len(incoming_files) > 5:
        return jsonify({
            "error": "Upload up to 5 files at a time."
        }), 400

    uploaded = []

    for file in incoming_files:
        if not file.filename:
            continue

        original_name = secure_filename(file.filename)
        extension = Path(original_name).suffix.lower()

        if not original_name:
            continue

        # Allow images as uploads, but don't claim the text model
        # can understand their visual contents.
        is_image = extension in {
            ".png", ".jpg", ".jpeg", ".webp", ".gif"
        }

        if not is_image and not allowed_file(original_name):
            return jsonify({
                "error": f"Unsupported file type: {extension}"
            }), 400

        file_bytes = file.read(10 * 1024 * 1024 + 1)

        if len(file_bytes) > 10 * 1024 * 1024:
            return jsonify({
                "error": "Each file must be 10 MB or smaller."
            }), 413

        file_id = uuid.uuid4().hex
        saved_name = f"{file_id}{extension}"
        saved_path = UPLOAD_DIR / saved_name

        try:
            with open(saved_path, "wb") as output:
                output.write(file_bytes)

            extracted_text = ""

            if not is_image:
                extracted_text = extract_text(
                    original_name,
                    file_bytes
                )

            uploaded.append({
                "id": file_id,
                "name": original_name,
                "type": "image" if is_image else "document",
                "size": len(file_bytes),
                "text": extracted_text
            })

        except Exception:
            logger.exception("File upload failed")

            return jsonify({
                "error": "Could not process the uploaded file."
            }), 500

    return jsonify(uploaded), 200


# -----------------------------
# ERROR HANDLERS
# -----------------------------

@app.errorhandler(413)
def too_large(_error):
    return jsonify({
        "error": "Request is too large. Maximum size is 10 MB."
    }), 413


@app.errorhandler(404)
def not_found(_error):
    return jsonify({
        "error": "API endpoint not found."
    }), 404


# -----------------------------
# START SERVER
# -----------------------------

if __name__ == "__main__":
    port = int(os.getenv("PORT", "5000"))

    app.run(
        host="0.0.0.0",
        port=port,
        debug=False
    )
