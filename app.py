import os
import shutil
import tempfile
import threading
import logging
from flask import Flask, request, send_file, jsonify
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
    """Download the highest-quality video using yt_dlp and return the local file path."""
    ydl_opts = {
        # Template for output filename
        "outtmpl": os.path.join(temp_dir, "%(title)s.%(ext)s"),

        # Download best available video and audio (any format)
        # Fallback to best combined if separate streams unavailable
        "format": "bestvideo+bestaudio/best",

        # Merge into a single MP4 container using ffmpeg
        "merge_output_format": "mp4",

        # Other options
        "quiet": True,
        "retries": 3,
        "socket_timeout": 30,
        "noprogress": True,
        "ignoreerrors": False,

        # Clean metadata (optional)
        "postprocessors": [
            {"key": "FFmpegMetadata"},
        ],
    }

    with yt_dlp.YoutubeDL(ydl_opts) as ydl:
        info_dict = ydl.extract_info(url, download=True)
        downloaded = ydl.prepare_filename(info_dict)

        # Force .mp4 extension if merged output isn't .mp4
        if not downloaded.endswith(".mp4"):
            downloaded = os.path.splitext(downloaded)[0] + ".mp4"

        # Log format info for visibility
        height = info_dict.get("height")
        fps = info_dict.get("fps")
        fmt_note = info_dict.get("format_note")
        ext = info_dict.get("ext")
        logging.info(f"[YTDLP] Downloaded format: {fmt_note or height}p @ {fps or '?'}fps ({ext})")

    return downloaded



# === Routes ===
@app.route("/download", methods=["POST"])
def download_video():
    try:
        # === Input Validation ===
        data = request.get_json(silent=True)
        url = data.get("url") if data else None
        if not url:
            return jsonify({"error": "Missing field 'url' in JSON"}), 400

        logging.info(f"[REQUEST] Download requested for URL: {url}")

        # === Temp workspace ===
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

        # === Cleanup scheduling ===
        schedule_delete(downloaded_file, delay=15)
        schedule_delete(temp_dir, delay=20, is_dir=True)

        # === Sanitize filename for headers ===
        original_name = os.path.basename(downloaded_file)
        try:
            safe_filename = original_name.encode("latin-1").decode("latin-1")
        except UnicodeEncodeError:
            safe_filename = quote(original_name)

        # === Build response ===
        response = send_file(
            downloaded_file,
            as_attachment=True,
            download_name=original_name,
            mimetype=mime_type,
            conditional=True,  # Supports range requests
            max_age=0,
        )

        response.headers["X-Filename"] = safe_filename
        response.headers["X-Size-Bytes"] = str(file_size)
        response.headers["X-Mime-Type"] = mime_type

        logging.info(f"[SUCCESS] Sent file: {safe_filename} ({file_size} bytes)")
        return response

    except Exception as e:
        logging.exception("[FATAL] Unhandled exception in /download")
        return jsonify({"error": str(e)}), 500


@app.route("/", methods=["GET"])
def index():
    return """
    <html>
    <head>
        <title>yt-dlp API</title>
        <style>
            body {
                font-family: "Segoe UI", Roboto, sans-serif;
                background: linear-gradient(135deg, #1f2937, #111827);
                color: #f9fafb;
                margin: 0;
                padding: 0;
                display: flex;
                align-items: center;
                justify-content: center;
                height: 100vh;
            }
            .container {
                background-color: rgba(31, 41, 55, 0.9);
                padding: 40px;
                border-radius: 16px;
                max-width: 700px;
                box-shadow: 0 4px 30px rgba(0, 0, 0, 0.4);
            }
            h1 {
                color: #60a5fa;
                font-size: 2.2em;
                margin-bottom: 0.3em;
            }
            p {
                color: #d1d5db;
                line-height: 1.5em;
                font-size: 1.05em;
            }
            code {
                background-color: #374151;
                color: #facc15;
                padding: 2px 6px;
                border-radius: 6px;
                font-family: "Courier New", monospace;
            }
            pre {
                background-color: #1e293b;
                padding: 10px;
                border-radius: 8px;
                overflow-x: auto;
                font-size: 0.9em;
                color: #f8fafc;
            }
            .footer {
                margin-top: 20px;
                font-size: 0.85em;
                color: #9ca3af;
            }
        </style>
    </head>
    <body>
        <div class="container">
            <h1>yt-dlp API Server</h1>
            <p>This server lets you download videos using <code>yt-dlp</code> and returns a ready-to-use MP4 file.</p>

            <h2>📦 Usage</h2>
            <p>Send a <code>POST</code> request to <code>/download</code> with JSON:</p>
            <pre>{
    "url": "https://www.youtube.com/watch?v=EXAMPLE"
}</pre>

            <h2>📥 Response</h2>
            <p>The server responds with the binary MP4 file. Example headers:</p>
            <pre>{
    "X-Filename": "video.mp4",
    "X-Size-Bytes": "12345678",
    "X-Mime-Type": "video/mp4"
}</pre>

            <div class="footer">Built with ❤️ Flask + yt-dlp</div>
        </div>
    </body>
    </html>
    """


# === App Entrypoint ===
if __name__ == "__main__":
    app.run(host="0.0.0.0", port=8080, threaded=True)
