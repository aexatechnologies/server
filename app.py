import os
import shutil
import tempfile
import threading
from flask import Flask, request, send_file, jsonify
import yt_dlp

app = Flask(__name__)

# Temporary folder (all downloads will be here)
DOWNLOAD_FOLDER = "downloads"
os.makedirs(DOWNLOAD_FOLDER, exist_ok=True)

def delete_file_later(path, delay=10):
    """
    Delete the file after `delay` seconds in a background thread
    """
    def delayed_delete():
        try:
            if os.path.exists(path):
                os.remove(path)
        except Exception as e:
            print(f"Failed to delete {path}: {e}")

    threading.Timer(delay, delayed_delete).start()


@app.route("/download", methods=["POST"])
def download_video():
    data = request.get_json()
    url = data.get("url")

    if not url:
        return jsonify({"error": "No URL provided"}), 400

    # Use a temporary folder for this download
    temp_dir = tempfile.mkdtemp(dir=DOWNLOAD_FOLDER)

    ydl_opts = {
        "outtmpl": os.path.join(temp_dir, "%(title)s.%(ext)s"),
        "quiet": True,
        "format": "bestvideo+bestaudio/best",
    }

    try:
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info_dict = ydl.extract_info(url, download=True)
            downloaded_file = ydl.prepare_filename(info_dict)
    except Exception as e:
        shutil.rmtree(temp_dir, ignore_errors=True)
        return jsonify({"error": str(e)}), 500

    if not os.path.exists(downloaded_file):
        shutil.rmtree(temp_dir, ignore_errors=True)
        return jsonify({"error": "Download failed"}), 500

    # Schedule cleanup after sending file
    delete_file_later(downloaded_file, delay=10)
    # Optionally remove temp folder later
    threading.Timer(15, lambda: shutil.rmtree(temp_dir, ignore_errors=True)).start()

    # Send file as binary (n8n-ready)
    response = send_file(
        downloaded_file,
        as_attachment=True,
        download_name=os.path.basename(downloaded_file),
        mimetype="application/octet-stream"
    )
    return response


@app.route("/", methods=["GET"])
def index():
    return jsonify({
        "message": "yt-dlp API running. POST /download with JSON {url: <video_url>}",
        "note": "Binary-ready for n8n"
    })


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=8080)
