import os
import shutil
import tempfile
import threading
from flask import Flask, request, send_file, jsonify, Response
import yt_dlp
import mimetypes

app = Flask(__name__)

DOWNLOAD_FOLDER = "downloads"
os.makedirs(DOWNLOAD_FOLDER, exist_ok=True)

def delete_file_later(path, delay=10):
    threading.Timer(delay, lambda: os.remove(path) if os.path.exists(path) else None).start()

def delete_folder_later(path, delay=15):
    threading.Timer(delay, lambda: shutil.rmtree(path, ignore_errors=True)).start()

@app.route("/download", methods=["POST"])
def download_video():
    data = request.get_json()
    url = data.get("url")
    if not url:
        return jsonify({"error": "No URL provided"}), 400

    temp_dir = tempfile.mkdtemp(dir=DOWNLOAD_FOLDER)

    ydl_opts = {
        "outtmpl": os.path.join(temp_dir, "%(title)s.%(ext)s"),
        "quiet": True,
        "format": "bestvideo[ext=mp4]+bestaudio[ext=m4a]/best[ext=mp4]",
        "merge_output_format": "mp4",
    }

    try:
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info_dict = ydl.extract_info(url, download=True)
            downloaded_file = ydl.prepare_filename(info_dict)
            if not downloaded_file.endswith(".mp4"):
                downloaded_file = os.path.splitext(downloaded_file)[0] + ".mp4"
    except Exception as e:
        shutil.rmtree(temp_dir, ignore_errors=True)
        return jsonify({"error": str(e)}), 500

    if not os.path.exists(downloaded_file):
        shutil.rmtree(temp_dir, ignore_errors=True)
        return jsonify({"error": "Download failed"}), 500

    file_size = os.path.getsize(downloaded_file)
    mime_type, _ = mimetypes.guess_type(downloaded_file)
    mime_type = mime_type or "application/octet-stream"

    delete_file_later(downloaded_file, delay=10)
    delete_folder_later(temp_dir, delay=15)

    # Send file with metadata in headers
    response = send_file(
        downloaded_file,
        as_attachment=True,
        download_name=os.path.basename(downloaded_file),
        mimetype=mime_type
    )
    response.headers["X-Filename"] = os.path.basename(downloaded_file)
    response.headers["X-Size-Bytes"] = str(file_size)
    response.headers["X-Mime-Type"] = mime_type
    return response

@app.route("/", methods=["GET"])
def index():
    return jsonify({
        "message": "yt-dlp API running. POST /download with JSON {url: <video_url>}",
        "note": "Binary file returned with metadata in HTTP headers"
    })

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=8080)
