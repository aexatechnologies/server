import os
import shutil
import tempfile
import threading
import logging
import base64
from flask import Flask, request, jsonify
import yt_dlp
import requests

app = Flask(__name__)

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format="[%(asctime)s] [%(levelname)s] %(message)s",
)

DOWNLOAD_FOLDER = "downloads"
os.makedirs(DOWNLOAD_FOLDER, exist_ok=True)

CHUNK_SIZE = 5 * 1024 * 1024  # 5 MB
N8N_WEBHOOK = "https://your-n8n-server/webhook/receive-chunk"  # <-- update this


# === Cleanup Utilities ===
def schedule_delete(path, delay=10, is_dir=False):
    """Delete a file or folder after a delay."""
    def _delete():
        try:
            if is_dir:
                shutil.rmtree(path, ignore_errors=True)
                logging.info(f"[CLEANUP] Folder deleted: {path}")
            elif os.path.exists(path):
                os.remove(path)
                logging.info(f"[CLEANUP] File deleted: {path}")
        except Exception as e:
            logging.error(f"[CLEANUP ERROR] Could not delete {path}: {e}")

    threading.Timer(delay, _delete).start()


# === Core Download Logic ===
def download_with_ytdlp(url: str, temp_dir: str) -> str:
    """Download a video using yt_dlp and return the local file path."""
    ydl_opts = {
        "outtmpl": os.path.join(temp_dir, "%(title)s.%(ext)s"),
        "quiet": True,
        "format": "bestvideo[ext=mp4]+bestaudio[ext=m4a]/best[ext=mp4]",
        "merge_output_format": "mp4",
        "retries": 3,
        "socket_timeout": 30,
        "noprogress": True,
    }

    with yt_dlp.YoutubeDL(ydl_opts) as ydl:
        info_dict = ydl.extract_info(url, download=True)
        downloaded = ydl.prepare_filename(info_dict)
        if not downloaded.endswith(".mp4"):
            downloaded = os.path.splitext(downloaded)[0] + ".mp4"

    return downloaded


# === Push file in chunks to n8n ===
def push_file_to_n8n(file_path):
    filename = os.path.basename(file_path)
    total_size = os.path.getsize(file_path)
    chunk_number = 0

    logging.info(f"[PUSH] Sending '{filename}' to n8n in chunks...")

    with open(file_path, "rb") as f:
        while True:
            chunk = f.read(CHUNK_SIZE)
            if not chunk:
                break
            chunk_number += 1
            payload = {
                "filename": filename,
                "chunk_number": chunk_number,
                "chunk_size": len(chunk),
                "total_size": total_size,
                "data": base64.b64encode(chunk).decode("utf-8")
            }
            try:
                r = requests.post(N8N_WEBHOOK, json=payload, timeout=30)
                if r.status_code != 200:
                    logging.warning(f"[PUSH] Chunk {chunk_number} returned status {r.status_code}")
            except Exception as e:
                logging.error(f"[PUSH] Failed to send chunk {chunk_number}: {e}")

    logging.info(f"[PUSH] Completed sending '{filename}' ({chunk_number} chunks).")


# === Routes ===
@app.route("/download", methods=["POST"])
def download_video():
    try:
        data = request.get_json(silent=True)
        url = data.get("url") if data else None
        if not url:
            return jsonify({"error": "Missing field 'url' in JSON"}), 400

        logging.info(f"[REQUEST] Download requested for URL: {url}")

        temp_dir = tempfile.mkdtemp(dir=DOWNLOAD_FOLDER)

        try:
            downloaded_file = download_with_ytdlp(url, temp_dir)
        except yt_dlp.utils.DownloadError as e:
            shutil.rmtree(temp_dir, ignore_errors=True)
            return jsonify({"error": f"DownloadError: {str(e)}"}), 500
        except Exception as e:
            shutil.rmtree(temp_dir, ignore_errors=True)
            return jsonify({"error": f"Unexpected error: {str(e)}"}), 500

        if not os.path.exists(downloaded_file):
            shutil.rmtree(temp_dir, ignore_errors=True)
            return jsonify({"error": "File missing after download"}), 500

        # Schedule cleanup
        schedule_delete(downloaded_file, delay=60)
        schedule_delete(temp_dir, delay=65, is_dir=True)

        # Push file in a separate thread so we can return immediately
        threading.Thread(target=push_file_to_n8n, args=(downloaded_file,)).start()

        return jsonify({
            "message": "File is being pushed to n8n in chunks",
            "filename": os.path.basename(downloaded_file),
            "total_size": os.path.getsize(downloaded_file)
        })

    except Exception as e:
        logging.exception("[FATAL] Unhandled exception in /download")
        return jsonify({"error": str(e)}), 500


@app.route("/", methods=["GET"])
def index():
    return jsonify({
        "message": "yt-dlp API running",
        "usage": "POST /download with JSON {url: <video_url>}",
        "returns": "File is pushed to n8n webhook in 5MB chunks",
        "n8n_webhook": N8N_WEBHOOK
    })


# === App Entrypoint ===
if __name__ == "__main__":
    app.run(host="0.0.0.0", port=8080, threaded=True)
