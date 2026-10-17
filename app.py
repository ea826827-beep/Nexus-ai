import os
from flask import Flask, request, jsonify
from flask_cors import CORS
from groq import Groq

app = Flask(__name__)
CORS(app)

# Environment Variable থেকে API Key নিবে
client = Groq(api_key=os.environ.get("GROQ_API_KEY"))

@app.route("/chat", methods=["POST"])
def chat():
    data = request.get_json(silent=True) or {}
    question = str(data.get("message", "")).strip()

    if not question:
        return jsonify({"error": "Message is required"}), 400

    try:
        completion = client.chat.completions.create(
            model="qwen-2.5-32b",  # অথবা llama-3.1-8b-instant
            messages=[
                {"role": "system", "content": "You are NEXUS BRAIN, a helpful AI assistant. Answer in English, Bengali, or Banglish."},
                {"role": "user", "content": question}
            ]
        )
        answer = completion.choices[0].message.content
        return jsonify({"answer": answer})

    except Exception as e:
        return jsonify({"error": str(e)}), 500
