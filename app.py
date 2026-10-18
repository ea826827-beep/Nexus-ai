import os
import threading
import logging
from collections import defaultdict, deque

from flask import Flask, request, jsonify
from flask_cors import CORS
from werkzeug.utils import secure_filename
from pypdf import PdfReader
from groq import Groq

app = Flask(__name__)
CORS(app)

app.config["MAX_CONTENT_LENGTH"] = 10 * 1024 * 1024

# Groq API Client Setup
groq_api_key = os.environ.get("GROQ_API_KEY")
client = Groq(api_key=groq_api_key) if groq_api_key else None

MODEL_NAME = "llama3-8b-8192"  # Groq-এর অত্যন্ত স্থিতিশীল ও সমর্থিত মডেল
UPLOAD_DIR = "uploaded_pdfs"
os.makedirs(UPLOAD_DIR, exist_ok=True)

logging.basicConfig(level=logging.INFO)

# Conversation memory
chat_memory = defaultdict(lambda: deque(maxlen=12))

# PDF memory
pdf_memory = []
pdf_lock = threading.Lock()


def split_text(text, size=900, overlap=150):
    text = " ".join(text.split())
    if not text:
        return []
    step = max(1, size - overlap)
    return [
        text[i:i + size]
        for i in range(0, len(text), step)
    ]


def search_pdf(question, limit=4):
    words = {
        word.lower().strip(".,?!:;()[]{}")
        for word in question.split()
        if len(word.strip(".,?!:;()[]{}")) >= 3
    }

    with pdf_lock:
        documents = list(pdf_memory)

    matches = []
    for document in documents:
        content = document["text"].lower()
        score = sum(1 for word in words if word in content)
        if score:
            matches.append((score, document["filename"], document["text"]))

    matches.sort(reverse=True, key=lambda item: item[0])

    return "\n\n".join(
        f"PDF: {filename}\n{text}"
        for _, filename, text in matches[:limit]
    )


def ask_nexus(question, session_id):
    if not client:
        raise ValueError("GROQ_API_KEY environment variable is missing.")

    history = list(chat_memory[session_id])
    pdf_context = search_pdf(question)

    system_message = """
You are NEXUS BRAIN, a helpful AI assistant.

Instructions:
- Answer in the user's language.
- Support Bengali, Banglish and English.
- Give clear, useful answers.
- Never invent facts when information is unavailable.
- Treat PDF content as reference material.
"""

    if pdf_context:
        system_message += f"\n\nRelevant PDF information:\n{pdf_context[:4000]}"

    messages = [{"role": "system", "content": system_message}]
    messages.extend(history)
    messages.append({"role": "user", "content": question})

    try:
        response = client.chat.completions.create(
            model=MODEL_NAME,
            messages=messages,
            temperature=0.7,
            max_tokens=1024
        )
        answer = response.choices[0].message.content.strip()
    except Exception as e:
        logging.exception("Groq API request failed")
        raise e

    chat_memory[session_id].append({"role": "user", "content": question})
    chat_memory[session_id].append({"role": "assistant", "content": answer})

    return answer


@app.route("/", methods=["GET"])
def home():
    return jsonify({
        "name": "NEXUS BRAIN",
        "status": "online",
        "model": MODEL_NAME,
        "endpoints": [
            "/",
            "/health",
            "/chat",
            "/upload-pdf",
            "/pdfs"
        ]
    })


@app.route("/health", methods=["GET"])
def health():
    with pdf_lock:
        pdf_count = len(pdf_memory)

    return jsonify({
        "status": "ok",
        "groq_ready": client is not None,
        "pdf_chunks": pdf_count
    })


@app.route("/chat", methods=["POST"])
def chat():
    data = request.get_json(silent=True) or {}
    question = str(data.get("message", "")).strip()
    session_id = str(data.get("session_id", "default"))[:80]

    if not question:
        return jsonify({"error": "Message is required"}), 400

    if len(question) > 4000:
        return jsonify({"error": "Message is too long"}), 413

    try:
        answer = ask_nexus(question, session_id)
        return jsonify({
            "answer": answer,
            "used_pdf_context": bool(search_pdf(question))
        })
    except Exception as e:
        logging.exception("AI request failed")
        return jsonify({"error": str(e)}), 500


@app.route("/upload-pdf", methods=["POST"])
def upload_pdf():
    if "file" not in request.files:
        return jsonify({"error": "Upload a PDF using the file field"}), 400

    file = request.files["file"]
    if not file.filename:
        return jsonify({"error": "No file selected"}), 400

    filename = secure_filename(file.filename)
    if not filename.lower().endswith(".pdf"):
        return jsonify({"error": "Only PDF files are accepted"}), 400

    filepath = os.path.join(UPLOAD_DIR, filename)

    try:
        file.save(filepath)
        reader = PdfReader(filepath)
        pages = []

        for number, page in enumerate(reader.pages, start=1):
            text = page.extract_text() or ""
            if text.strip():
                pages.append(f"Page {number}: {text}")

        full_text = "\n".join(pages)
        chunks = split_text(full_text)

        if not chunks:
            return jsonify({"error": "No selectable text found in PDF."}), 422

        with pdf_lock:
            pdf_memory[:] = [
                item for item in pdf_memory
                if item["filename"] != filename
            ]
            pdf_memory.extend([
                {"filename": filename, "text": chunk}
                for chunk in chunks
            ])

        return jsonify({
            "message": "PDF processed successfully",
            "filename": filename,
            "chunks": len(chunks)
        })
    except Exception:
        logging.exception("PDF processing failed")
        return jsonify({"error": "Could not process this PDF"}), 400


@app.route("/pdfs", methods=["GET"])
def list_pdfs():
    with pdf_lock:
        filenames = sorted({item["filename"] for item in pdf_memory})
    return jsonify({"pdfs": filenames})


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 10000))
    app.run(host="0.0.0.0", port=port)
