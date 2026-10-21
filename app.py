import os
import io
import re
import uuid
import time
import secrets
import logging
import threading
from pathlib import Path
from datetime import datetime, timezone
from collections import defaultdict, deque

from flask import Flask, request, jsonify, session
from flask_cors import CORS
from werkzeug.security import generate_password_hash, check_password_hash
from werkzeug.utils import secure_filename
from pypdf import PdfReader

# =========================================================
# NEXUS AI — PREMIUM BACKEND
# =========================================================

APP_NAME = "NEXUS AI"
APP_VERSION = "2.0.0"

BASE_DIR = Path(__file__).resolve().parent
UPLOAD_DIR = BASE_DIR / "uploaded_files"
UPLOAD_DIR.mkdir(parents=True, exist_ok=True)

DATABASE_PATH = os.getenv(
    "DATABASE_PATH",
    str(BASE_DIR / "nexus_users.db")
)

MODEL_NAME = os.getenv(
    "MODEL_NAME",
    "Qwen/Qwen2.5-0.5B-Instruct"
)

MAX_CONTENT_LENGTH = 10 * 1024 * 1024
MAX_MESSAGE_LENGTH = 12000
MAX_FILES = 5
MAX_HISTORY = 12

ALLOWED_EXTENSIONS = {"txt", "pdf"}

# =========================================================
# LOGGING
# =========================================================

logging.basicConfig(level=logging.INFO)

logger = logging.getLogger("nexus-ai")

# =========================================================
# FLASK APPLICATION
# =========================================================

app = Flask(__name__)

app.config["MAX_CONTENT_LENGTH"] = MAX_CONTENT_LENGTH

app.config["SECRET_KEY"] = os.getenv("SECRET_KEY")

# Production-এ SECRET_KEY অবশ্যই Render Environment
# Variables-এ সেট করতে হবে।
if not app.config["SECRET_KEY"]:
    logger.warning(
        "SECRET_KEY is missing. Authentication endpoints "
        "will not be available until it is configured."
    )

# Frontend origins comma-separated environment variable.
# উদাহরণ: https://your-frontend.example
frontend_origins = [
    origin.strip()
    for origin in os.getenv("FRONTEND_ORIGINS", "").split(",")
    if origin.strip()
]

CORS(
    app,
    resources={
        r"/*": {
            "origins": frontend_origins or "*"
        }
    },
    supports_credentials=bool(frontend_origins)
)

# =========================================================
# DATABASE
# =========================================================

import sqlite3


def get_db():
    connection = sqlite3.connect(
        DATABASE_PATH,
        timeout=20
    )

    connection.row_factory = sqlite3.Row

    return connection


def init_database():
    with get_db() as db:
        db.execute("""
            CREATE TABLE IF NOT EXISTS users (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                email TEXT UNIQUE NOT NULL,
                password_hash TEXT NOT NULL,
                created_at TEXT NOT NULL
            )
        """)

        db.commit()


init_database()

# =========================================================
# AI MODEL
# =========================================================

model = None
tokenizer = None

model_loading = False
model_error = None

model_lock = threading.Lock()


def load_ai_model():
    """
    Hugging Face Transformers দিয়ে AI model load করে।

    প্রয়োজনীয় packages:
    flask
    flask-cors
    gunicorn
    transformers
    torch
    pypdf
    """

    global model, tokenizer, model_loading, model_error

    if model is not None:
        return True

    with model_lock:
        if model is not None:
            return True

        if model_loading:
            return False

        model_loading = True

    try:
        logger.info("Loading model: %s", MODEL_NAME)

        import torch
        from transformers import AutoTokenizer, AutoModelForCausalLM

        tokenizer = AutoTokenizer.from_pretrained(
            MODEL_NAME
        )

        model = AutoModelForCausalLM.from_pretrained(
            MODEL_NAME,
            torch_dtype=torch.float32
        )

        model.eval()

        model_error = None

        logger.info("AI model loaded successfully.")

        return True

    except Exception as exc:
        model_error = str(exc)

        logger.exception("Could not load AI model.")

        return False

    finally:
        model_loading = False


def generate_ai_response(message, history=None, file_context=""):
    """
    Model load না হলে পরিষ্কার error দেয়।
    Model-এর পরিবর্তে ভুয়া AI response তৈরি করে না।
    """

    if not load_ai_model():
        raise RuntimeError(
            "AI model is not ready. Check Render logs, "
            "dependencies, model availability and RAM."
        )

    import torch

    messages = [
        {
            "role": "system",
            "content": (
                "You are NEXUS AI, a helpful AI assistant. "
                "Give clear, accurate, useful answers. "
                "If information is uncertain, say so. "
                "Do not claim to have performed actions "
                "that you have not performed."
            )
        }
    ]

    for item in (history or [])[-MAX_HISTORY:]:
        role = item.get("role")

        content = item.get("content", "")

        if role in {"user", "assistant"} and content:
            messages.append({
                "role": role,
                "content": str(content)[:MAX_MESSAGE_LENGTH]
            })

    user_message = message

    if file_context:
        user_message += (
            "\n\nUploaded document content:\n"
            + file_context[:12000]
        )

    messages.append({
        "role": "user",
        "content": user_message
    })

    # Qwen chat template
    input_text = tokenizer.apply_chat_template(
        messages,
        tokenize=False,
        add_generation_prompt=True
    )

    inputs = tokenizer(
        input_text,
        return_tensors="pt",
        truncation=True,
        max_length=2048
    )

    with torch.no_grad():
        outputs = model.generate(
            **inputs,
            max_new_tokens=400,
            do_sample=True,
            temperature=0.7,
            top_p=0.9,
            repetition_penalty=1.1,
            pad_token_id=tokenizer.eos_token_id
        )

    generated_tokens = outputs[
        0
    ][inputs["input_ids"].shape[-1]:]

    answer = tokenizer.decode(
        generated_tokens,
        skip_special_tokens=True
    ).strip()

    if not answer:
        answer = "দুঃখিত, এবার উত্তর তৈরি করা যায়নি। আবার চেষ্টা করো।"

    return answer


# =========================================================
# IN-MEMORY CHAT CONTEXT
# =========================================================

chat_memory = defaultdict(
    lambda: deque(maxlen=MAX_HISTORY * 2)
)

chat_memory_lock = threading.Lock()


def get_conversation_id():
    """
    Frontend conversationId পাঠাতে পারে।
    না পাঠালে নতুন ID তৈরি হয়।
    """

    conversation_id = request.headers.get(
        "X-Conversation-ID"
    )

    if not conversation_id:
        conversation_id = request.json.get(
            "conversationId"
        ) if request.is_json else None

    if not conversation_id:
        conversation_id = str(uuid.uuid4())

    # ID length সীমাবদ্ধ রাখি।
    return str(conversation_id)[:100]


# =========================================================
# GENERAL HELPERS
# =========================================================

def error_response(message, status=400):
    return jsonify({
        "success": False,
        "error": message,
        "app": APP_NAME
    }), status


def allowed_file(filename):
    return (
        "." in filename
        and filename.rsplit(".", 1)[1].lower()
        in ALLOWED_EXTENSIONS
    )


def utc_now():
    return datetime.now(
        timezone.utc
    ).isoformat()


# =========================================================
# HEALTH CHECK
# =========================================================

@app.route("/", methods=["GET"])
def home():
    return jsonify({
        "success": True,
        "app": APP_NAME,
        "version": APP_VERSION,
        "status": "online",
        "message": "NEXUS AI backend is running."
    })


@app.route("/health", methods=["GET"])
def health():
    return jsonify({
        "success": True,
        "app": APP_NAME,
        "version": APP_VERSION,
        "status": "online",
        "model": MODEL_NAME,
        "model_loaded": model is not None,
        "model_loading": model_loading,
        "model_error": model_error
    })


# =========================================================
# CHAT API
# =========================================================

@app.route("/chat", methods=["POST"])
def chat():
    data = request.get_json(silent=True)

    if not isinstance(data, dict):
        return error_response(
            "Valid JSON request body required."
        )

    message = data.get("message", "")

    if not isinstance(message, str):
        return error_response(
            "Message must be text."
        )

    message = message.strip()

    if not message:
        return error_response(
            "Message cannot be empty."
        )

    if len(message) > MAX_MESSAGE_LENGTH:
        return error_response(
            "Message is too long."
        )

    # Frontend থেকে conversation history আসতে পারে।
    supplied_history = data.get("history", [])

    if not isinstance(supplied_history, list):
        supplied_history = []

    conversation_id = get_conversation_id()

    # Server-side conversation history.
    with chat_memory_lock:
        previous_history = list(
            chat_memory[conversation_id]
        )

    # Frontend history না থাকলে server history ব্যবহার।
    history = supplied_history or previous_history

    # Frontend-এ file upload-এর পরে extracted text
    # পাঠালে এটি ব্যবহার করা যাবে।
    file_context = data.get("file_context", "")

    if not isinstance(file_context, str):
        file_context = ""

    file_context = file_context[:12000]

    try:
        answer = generate_ai_response(
            message=message,
            history=history,
            file_context=file_context
        )

        with chat_memory_lock:
            chat_memory[conversation_id].append({
                "role": "user",
                "content": message
            })

            chat_memory[conversation_id].append({
                "role": "assistant",
                "content": answer
            })

        return jsonify({
            "success": True,
            "app": APP_NAME,
            "conversationId": conversation_id,
            "reply": answer,
            "response": answer,
            "model": MODEL_NAME,
            "timestamp": utc_now()
        })

    except Exception:
        logger.exception("Chat request failed.")

        return error_response(
            "AI response তৈরি করা যায়নি। Render logs "
            "এ model/dependency/RAM error পরীক্ষা করো।",
            503
        )


# =========================================================
# FILE UPLOAD API
# =========================================================

@app.route("/upload", methods=["POST"])
def upload():
    if "files" not in request.files and "file" not in request.files:
        return error_response(
            "Upload-এর জন্য files অথবা file field প্রয়োজন।"
        )

    files = request.files.getlist("files")

    if not files:
        files = [request.files.get("file")]

    files = [
        item for item in files
        if item and item.filename
    ]

    if not files:
        return error_response(
            "কোনো ফাইল নির্বাচন করা হয়নি।"
        )

    if len(files) > MAX_FILES:
        return error_response(
            f"একসঙ্গে সর্বোচ্চ {MAX_FILES}টি ফাইল পাঠানো যাবে।"
        )

    results = []

    for uploaded in files:
        original_name = secure_filename(
            uploaded.filename
        )

        if not original_name:
            results.append({
                "success": False,
                "error": "Invalid filename."
            })
            continue

        if not allowed_file(original_name):
            results.append({
                "success": False,
                "filename": original_name,
                "error": "শুধু PDF ও TXT ফাইল গ্রহণ করা হয়।"
            })
            continue

        try:
            raw_bytes = uploaded.read(
                MAX_CONTENT_LENGTH + 1
            )

            if len(raw_bytes) > MAX_CONTENT_LENGTH:
                results.append({
                    "success": False,
                    "filename": original_name,
                    "error": "ফাইলটি 10 MB-এর বেশি।"
                })
                continue

            extracted_text = ""

            extension = original_name.rsplit(
                ".", 1
            )[1].lower()

            if extension == "txt":
                extracted_text = raw_bytes.decode(
                    "utf-8",
                    errors="replace"
                )

            elif extension == "pdf":
                pdf = PdfReader(
                    io.BytesIO(raw_bytes)
                )

                pages = []

                for page in pdf.pages[:30]:
                    pages.append(
                        page.extract_text() or ""
                    )

                extracted_text = "\n".join(pages)

            # ফাইলের নাম থেকে unique storage name।
            stored_name = (
                secrets.token_hex(12)
                + "_"
                + original_name
            )

            file_path = UPLOAD_DIR / stored_name

            # Original filename ব্যবহার করে path তৈরি নয়;
            # secure filename ও random prefix ব্যবহৃত হচ্ছে।
            file_path.write_bytes(raw_bytes)

            results.append({
                "success": True,
                "filename": original_name,
                "size": len(raw_bytes),
                "text": extracted_text[:12000],
                "charactersExtracted": len(
                    extracted_text
                )
            })

        except Exception:
            logger.exception("File processing failed.")

            results.append({
                "success": False,
                "filename": original_name,
                "error": "ফাইল পড়া যায়নি। ফাইলটি পরীক্ষা করো।"
            })

    return jsonify({
        "success": any(
            item.get("success") for item in results
        ),
        "files": results,
        "count": len(results)
    })


# =========================================================
# AUTHENTICATION
# =========================================================

def auth_is_configured():
    return bool(app.config.get("SECRET_KEY"))


def require_auth_config():
    if not auth_is_configured():
        return error_response(
            "Authentication চালু করতে Render-এ SECRET_KEY সেট করো।",
            503
        )

    return None


@app.route("/api/signup", methods=["POST"])
def signup():
    config_error = require_auth_config()

    if config_error:
        return config_error

    data = request.get_json(silent=True) or {}

    email = str(
        data.get("email", "")
    ).strip().lower()

    password = str(
        data.get("password", "")
    )

    if not email or not password:
        return error_response(
            "Email ও password প্রয়োজন।"
        )

    if len(email) > 254 or not re.match(
        r"^[^@\s]+@[^@\s]+\.[^@\s]+$",
        email
    ):
        return error_response(
            "সঠিক email address দাও।"
        )

    if len(password) < 10:
        return error_response(
            "Password কমপক্ষে 10 characters হতে হবে।"
        )

    if len(password) > 256:
        return error_response(
            "Password খুব বড়।"
        )

    password_hash = generate_password_hash(
        password
    )

    try:
        with get_db() as db:
            cursor = db.execute(
                """
                INSERT INTO users
                (email, password_hash, created_at)
                VALUES (?, ?, ?)
                """,
                (
                    email,
                    password_hash,
                    utc_now()
                )
            )

            user_id = cursor.lastrowid

            db.commit()

        session["user_id"] = user_id

        return jsonify({
            "success": True,
            "message": "Account তৈরি হয়েছে।",
            "user": {
                "id": user_id,
                "email": email
            }
        }), 201

    except sqlite3.IntegrityError:
        return error_response(
            "এই email দিয়ে আগে থেকেই account রয়েছে।",
            409
        )

    except Exception:
        logger.exception("Signup failed.")

        return error_response(
            "Account তৈরি করা যায়নি।",
            500
        )


@app.route("/api/login", methods=["POST"])
def login():
    config_error = require_auth_config()

    if config_error:
        return config_error

    data = request.get_json(silent=True) or {}

    email = str(
        data.get("email", "")
    ).strip().lower()

    password = str(
        data.get("password", "")
    )

    if not email or not password:
        return error_response(
            "Email ও password দাও।"
        )

    with get_db() as db:
        user = db.execute(
            "SELECT * FROM users WHERE email = ?",
            (email,)
        ).fetchone()

    if not user or not check_password_hash(
        user["password_hash"],
        password
    ):
        return error_response(
            "Email অথবা password সঠিক নয়।",
            401
        )

    session.clear()
    session["user_id"] = user["id"]

    return jsonify({
        "success": True,
        "message": "Login সফল হয়েছে।",
        "user": {
            "id": user["id"],
            "email": user["email"]
        }
    })


@app.route("/api/me", methods=["GET"])
def me():
    config_error = require_auth_config()

    if config_error:
        return config_error

    user_id = session.get("user_id")

    if not user_id:
        return jsonify({
            "success": True,
            "authenticated": False,
            "user": None
        })

    with get_db() as db:
        user = db.execute(
            "SELECT id, email, created_at FROM users WHERE id = ?",
            (user_id,)
        ).fetchone()

    if not user:
        session.clear()

        return jsonify({
            "success": True,
            "authenticated": False,
            "user": None
        })

    return jsonify({
        "success": True,
        "authenticated": True,
        "user": {
            "id": user["id"],
            "email": user["email"],
            "createdAt": user["created_at"]
        }
    })


@app.route("/api/logout", methods=["POST"])
def logout():
    session.clear()

    return jsonify({
        "success": True,
        "message": "Logout সফল হয়েছে।"
    })


# =========================================================
# IMAGE GENERATION API
# =========================================================

@app.route("/generate-image", methods=["POST"])
def generate_image():
    """
    এটি endpoint-এর placeholder।
    Image provider connect না করা পর্যন্ত ছবি তৈরি করবে না।
    """

    return jsonify({
        "success": False,
        "error": (
            "Image generation provider এখনো configure করা হয়নি। "
            "এই feature চালাতে একটি image-generation provider "
            "ও তার API credentials যুক্ত করতে হবে।"
        )
    }), 501


# =========================================================
# ERROR HANDLERS
# =========================================================

@app.errorhandler(413)
def file_too_large(error):
    return error_response(
        "Request 10 MB limit অতিক্রম করেছে।",
        413
    )


@app.errorhandler(404)
def not_found(error):
    return error_response(
        "API endpoint পাওয়া যায়নি।",
        404
    )


@app.errorhandler(500)
def internal_error(error):
    logger.exception("Internal server error.")

    return error_response(
        "Server-এর ভিতরে সমস্যা হয়েছে।",
        500
    )


# =========================================================
# LOCAL DEVELOPMENT
# =========================================================

if __name__ == "__main__":
    app.run(
        host="0.0.0.0",
        port=int(os.getenv("PORT", "5000")),
        debug=False
    )
