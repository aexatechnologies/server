import os
import shutil
import tempfile
import threading
import logging
from flask import Flask, request, Response, jsonify
import yt_dlp
import mimetypes
from urllib.parse import quote

app = Flask(__name__)

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format="[%(asctime)s] [%(levelname)s] %(message)s",
)

DOWNLOAD_FOLDER = "downloads"
os.makedirs(DOWNLOAD_FOLDER, exist_ok=True)

CHUNK_SIZE = 5 * 1024 * 1024  # 5 MB


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


# === Generator for Chunked Streaming ===
def stream_file_in_chunks(file_path):
    """Yield chunks of the file with their size in headers."""
    try:
        with open(file_path, "rb") as f:
            while True:
                chunk = f.read(CHUNK_SIZE)
                if not chunk:
                    break
                yield chunk
    except Exception as e:
        logging.error(f"[STREAM ERROR] {e}")


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

        # === Metadata ===
        file_size = os.path.getsize(downloaded_file)
        mime_type, _ = mimetypes.guess_type(downloaded_file)
        mime_type = mime_type or "application/octet-stream"

        schedule_delete(downloaded_file, delay=30)
        schedule_delete(temp_dir, delay=35, is_dir=True)

        original_name = os.path.basename(downloaded_file)
        try:
            safe_filename = original_name.encode("latin-1").decode("latin-1")
        except UnicodeEncodeError:
            safe_filename = quote(original_name)

        logging.info(f"[STREAMING] Sending file in chunks: {safe_filename}")

        # === Response Streaming ===
        def generate():
            with open(downloaded_file, "rb") as f:
                while True:
                    chunk = f.read(CHUNK_SIZE)
                    if not chunk:
                        break
                    # yield as bytes
                    yield chunk

        headers = {
            "Content-Disposition": f'attachment; filename="{safe_filename}"',
            "X-Filename": safe_filename,
            "X-Size-Bytes": str(file_size),
            "X-Mime-Type": mime_type,
        }

        return Response(generate(), headers=headers, mimetype=mime_type)

    except Exception as e:
        logging.exception("[FATAL] Unhandled exception in /download")
        return jsonify({"error": str(e)}), 500


@app.route("/", methods=["GET"])
def index():
    return jsonify({
        "message": "yt-dlp API running",
        "usage": "POST /download with JSON {url: <video_url>}",
        "returns": "Binary MP4 streamed in chunks",
        "chunk_size_bytes": CHUNK_SIZE,
        "example_headers": {
            "X-Filename": "video.mp4",
            "X-Size-Bytes": "12345678",
            "X-Mime-Type": "video/mp4"
        },
    })


# === App Entrypoint ===
if __name__ == "__main__":
    app.run(host="0.0.0.0", port=8080, threaded=True)
