import os
import uuid
import logging
from pathlib import Path
from functools import wraps

from flask import Flask, request, jsonify, redirect, session
from flask_cors import CORS
from flask_sqlalchemy import SQLAlchemy
from werkzeug.security import generate_password_hash, check_password_hash
from werkzeug.utils import secure_filename
from authlib.integrations.flask_client import OAuth
from dotenv import load_dotenv
from pypdf import PdfReader
from huggingface_hub import InferenceClient

load_dotenv()

# -------------------------
# APP CONFIGURATION
# -------------------------

app = Flask(__name__)

app.config["SECRET_KEY"] = os.getenv("SECRET_KEY")

if not app.config["SECRET_KEY"]:
    raise RuntimeError("SECRET_KEY environment variable is required.")

app.config["SQLALCHEMY_DATABASE_URI"] = os.getenv(
    "DATABASE_URL",
    "sqlite:///nexus.db"
).replace("postgres://", "postgresql://", 1)

app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False
app.config["MAX_CONTENT_LENGTH"] = 10 * 1024 * 1024
app.config["SESSION_COOKIE_HTTPONLY"] = True
app.config["SESSION_COOKIE_SECURE"] = (
    os.getenv("COOKIE_SECURE", "true").lower() == "true"
)
app.config["SESSION_COOKIE_SAMESITE"] = "None"

db = SQLAlchemy(app)

FRONTEND_URL = os.getenv("FRONTEND_URL", "").rstrip("/")
GOOGLE_CLIENT_ID = os.getenv("GOOGLE_CLIENT_ID")
GOOGLE_CLIENT_SECRET = os.getenv("GOOGLE_CLIENT_SECRET")

CORS(
    app,
    supports_credentials=True,
    origins=[FRONTEND_URL] if FRONTEND_URL else []
)

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("NEXUS")

UPLOAD_DIR = Path(os.getenv("UPLOAD_DIR", "uploads"))
UPLOAD_DIR.mkdir(parents=True, exist_ok=True)

MODEL_NAME = os.getenv(
    "MODEL_NAME",
    "Qwen/Qwen2.5-0.5B-Instruct"
)
HF_TOKEN = os.getenv("HF_TOKEN")

oauth = OAuth(app)

if GOOGLE_CLIENT_ID and GOOGLE_CLIENT_SECRET:
    oauth.register(
        name="google",
        client_id=GOOGLE_CLIENT_ID,
        client_secret=GOOGLE_CLIENT_SECRET,
        server_metadata_url=(
            "https://accounts.google.com/.well-known/openid-configuration"
        ),
        client_kwargs={
            "scope": "openid email profile"
        }
    )


# -------------------------
# DATABASE
# -------------------------

class User(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    email = db.Column(db.String(255), unique=True, nullable=False)
    password_hash = db.Column(db.String(255), nullable=True)
    google_id = db.Column(db.String(255), unique=True, nullable=True)
    display_name = db.Column(db.String(120), nullable=True)


with app.app_context():
    db.create_all()


# -------------------------
# AUTH HELPERS
# -------------------------

def current_user():
    user_id = session.get("user_id")

    if not user_id:
        return None

    return db.session.get(User, user_id)


def login_required_api(function):
    @wraps(function)
    def wrapper(*args, **kwargs):
        if not current_user():
            return jsonify({"error": "Please log in first."}), 401

        return function(*args, **kwargs)

    return wrapper


def user_json(user):
    return {
        "id": user.id,
        "email": user.email,
        "name": user.display_name or user.email
    }


# -------------------------
# HEALTH
# -------------------------

@app.get("/")
def home():
    return jsonify({
        "app": "NEXUS AI",
        "status": "online"
    })


@app.get("/health")
def health():
    return jsonify({
        "status": "healthy",
        "model": MODEL_NAME,
        "ai_token_configured": bool(HF_TOKEN)
    })


# -------------------------
# SIGNUP
# -------------------------

@app.post("/auth/signup")
def signup():
    data = request.get_json(silent=True) or {}

    email = str(data.get("email", "")).strip().lower()
    password = data.get("password", "")
    name = str(data.get("name", "")).strip()[:120]

    if not email or "@" not in email:
        return jsonify({"error": "Enter a valid email."}), 400

    if not isinstance(password, str) or len(password) < 10:
        return jsonify({
            "error": "Password must be at least 10 characters."
        }), 400

    if len(email) > 255:
        return jsonify({"error": "Email is too long."}), 400

    if User.query.filter_by(email=email).first():
        return jsonify({"error": "An account already exists."}), 409

    user = User(
        email=email,
        password_hash=generate_password_hash(password),
        display_name=name or email.split("@")[0]
    )

    db.session.add(user)
    db.session.commit()

    session.clear()
    session["user_id"] = user.id

    return jsonify({
        "message": "Account created successfully.",
        "user": user_json(user)
    }), 201


# -------------------------
# LOGIN
# -------------------------

@app.post("/auth/login")
def login():
    data = request.get_json(silent=True) or {}

    email = str(data.get("email", "")).strip().lower()
    password = data.get("password", "")

    user = User.query.filter_by(email=email).first()

    if (
        not user
        or not user.password_hash
        or not isinstance(password, str)
        or not check_password_hash(user.password_hash, password)
    ):
        return jsonify({"error": "Invalid email or password."}), 401

    session.clear()
    session["user_id"] = user.id

    return jsonify({
        "message": "Login successful.",
        "user": user_json(user)
    })


# -------------------------
# CURRENT USER
# -------------------------

@app.get("/auth/me")
def me():
    user = current_user()

    if not user:
        return jsonify({"user": None}), 200

    return jsonify({"user": user_json(user)})


# -------------------------
# LOGOUT
# -------------------------

@app.post("/auth/logout")
def logout():
    session.clear()

    return jsonify({
        "message": "Logged out successfully."
    })


# -------------------------
# GOOGLE OAUTH
# -------------------------

@app.get("/auth/google")
def google_login():
    if not GOOGLE_CLIENT_ID or not GOOGLE_CLIENT_SECRET:
        return jsonify({
            "error": "Google OAuth credentials are not configured."
        }), 503

    redirect_uri = (
        request.url_root.rstrip("/")
        + "/auth/google/callback"
    )

    return oauth.google.authorize_redirect(redirect_uri)


@app.get("/auth/google/callback")
def google_callback():
    if not GOOGLE_CLIENT_ID or not GOOGLE_CLIENT_SECRET:
        return "Google OAuth is not configured.", 503

    try:
        token = oauth.google.authorize_access_token()
        profile = token.get("userinfo")

        if not profile:
            profile = oauth.google.parse_id_token(
                token,
                nonce=None
            )

        if not profile:
            return "Could not retrieve Google account.", 400

        google_id = str(profile["sub"])
        email = str(profile.get("email", "")).lower()

        if not email:
            return "Google did not provide an email.", 400

        user = User.query.filter_by(google_id=google_id).first()

        if not user:
            user = User.query.filter_by(email=email).first()

        if user:
            if user.google_id and user.google_id != google_id:
                return "Google account conflict.", 409

            if user.password_hash and not user.google_id:
                return (
                    "This email already has a password account. "
                    "Use password login instead.",
                    409
                )

            user.google_id = google_id
        else:
            user = User(
                email=email,
                google_id=google_id,
                display_name=profile.get("name", email.split("@")[0])
            )
            db.session.add(user)

        db.session.commit()

        session.clear()
        session["user_id"] = user.id

        if not FRONTEND_URL:
            return jsonify({
                "message": "Google login successful.",
                "user": user_json(user)
            })

        return redirect(FRONTEND_URL)

    except Exception:
        logger.exception("Google OAuth failed")
        return "Google authentication failed.", 400


# -------------------------
# AI MODEL
# -------------------------

def generate_ai_reply(message, history=None, files=None):
    if not HF_TOKEN:
        raise RuntimeError("HF_TOKEN is not configured.")

    client = InferenceClient(
        model=MODEL_NAME,
        token=HF_TOKEN
    )

    messages = [{
        "role": "system",
        "content": (
            "You are NEXUS, a helpful AI assistant. "
            "Answer accurately and clearly. "
            "Never claim to have read a file unless its text is available."
        )
    }]

    if isinstance(history, list):
        for item in history[-8:]:
            if not isinstance(item, dict):
                continue

            role = item.get("role")
            content = item.get("content", item.get("text", ""))

            if role in ("user", "assistant") and isinstance(content, str):
                messages.append({
                    "role": role,
                    "content": content[:3000]
                })

    document_text = []

    if isinstance(files, list):
        for file in files[:5]:
            if isinstance(file, dict):
                text_content = file.get("text", "")

                if isinstance(text_content, str) and text_content:
                    document_text.append(text_content[:5000])

    prompt = message[:6000]

    if document_text:
        prompt += (
            "\n\nUploaded document contents:\n"
            + "\n---\n".join(document_text)
        )

    messages.append({
        "role": "user",
        "content": prompt
    })

    result = client.chat_completion(
        messages=messages,
        max_tokens=700,
        temperature=0.7
    )

    return result.choices[0].message.content or ""


# -------------------------
# CHAT
# -------------------------

@app.post("/chat")
@login_required_api
def chat():
    data = request.get_json(silent=True) or {}

    message = data.get("message", "")

    if not isinstance(message, str) or not message.strip():
        return jsonify({"error": "Enter a message."}), 400

    if len(message) > 6000:
        return jsonify({"error": "Message is too long."}), 400

    try:
        reply = generate_ai_reply(
            message.strip(),
            data.get("history", []),
            data.get("files", [])
        )

        return jsonify({
            "reply": reply,
            "model": MODEL_NAME
        })

    except Exception:
        logger.exception("AI request failed")

        return jsonify({
            "error": (
                "AI request failed. Check model availability, "
                "HF_TOKEN, and server logs."
            )
        }), 502


# -------------------------
# FILE UPLOAD
# -------------------------

ALLOWED_DOCUMENTS = {
    ".txt", ".md", ".pdf", ".csv", ".json",
    ".py", ".html", ".css", ".js", ".xml", ".log"
}

ALLOWED_IMAGES = {
    ".png", ".jpg", ".jpeg", ".webp", ".gif"
}


@app.post("/upload")
@login_required_api
def upload():
    incoming = request.files.getlist("files")

    if not incoming:
        return jsonify({"error": "Choose a file first."}), 400

    if len(incoming) > 5:
        return jsonify({"error": "Maximum 5 files per request."}), 400

    uploaded = []

    for file in incoming:
        if not file.filename:
            continue

        filename = secure_filename(file.filename)
        extension = Path(filename).suffix.lower()

        is_image = extension in ALLOWED_IMAGES

        if not filename or (
            extension not in ALLOWED_DOCUMENTS and not is_image
        ):
            return jsonify({
                "error": f"Unsupported file type: {extension}"
            }), 400

        content = file.read(10 * 1024 * 1024 + 1)

        if len(content) > 10 * 1024 * 1024:
            return jsonify({"error": "File exceeds 10 MB."}), 413

        file_id = uuid.uuid4().hex
        saved_name = f"{file_id}{extension}"

        with open(UPLOAD_DIR / saved_name, "wb") as output:
            output.write(content)

        extracted = ""

        try:
            if extension == ".pdf":
                from io import BytesIO

                pdf = PdfReader(BytesIO(content))
                extracted = "\n".join(
                    page.extract_text() or ""
                    for page in pdf.pages[:20]
                )[:12000]

            elif not is_image:
                extracted = content.decode(
                    "utf-8",
                    errors="replace"
                )[:12000]

        except Exception:
            logger.exception("Document extraction failed")

        uploaded.append({
            "id": file_id,
            "name": filename,
            "type": "image" if is_image else "document",
            "size": len(content),
            "text": extracted
        })

    return jsonify(uploaded)


# -------------------------
# START SERVER
# -------------------------

if __name__ == "__main__":
    port = int(os.getenv("PORT", "5000"))

    app.run(
        host="0.0.0.0",
        port=port,
        debug=False
    )
