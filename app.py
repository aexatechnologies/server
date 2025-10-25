import os
import shutil
import tempfile
import threading
import logging
import time
from flask import Flask, request, send_file, jsonify
import yt_dlp
import mimetypes
from urllib.parse import quote
from pytube import YouTube

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

def download_with_pytube(url: str, temp_dir: str) -> str:
    """Fallback for YouTube-only using pytube."""
    yt = YouTube(url)
    stream = yt.streams.filter(progressive=True, file_extension="mp4").order_by('resolution').desc().first()
    if not stream:
        raise ValueError("No mp4 video stream found")
    out_file = stream.download(output_path=temp_dir)
    return out_file

def robust_download(url: str, temp_dir: str, max_retries=3) -> str:
    """Try multiple methods with retries to download video."""
    last_exception = None
    # Try yt-dlp with retries
    for attempt in range(1, max_retries + 1):
        try:
            return download_with_ytdlp(url, temp_dir)
        except yt_dlp.utils.DownloadError as e:
            last_exception = e
            logging.warning(f"[yt_dlp attempt {attempt}] failed: {e}")
            time.sleep(1)
        except Exception as e:
            last_exception = e
            logging.warning(f"[yt_dlp attempt {attempt}] unexpected error: {e}")
            time.sleep(1)

    # Fallback to pytube for YouTube
    try:
        logging.info("[FALLBACK] Trying pytube...")
        return download_with_pytube(url, temp_dir)
    except Exception as e:
        last_exception = e
        logging.error(f"[FALLBACK] pytube failed: {e}")

    raise last_exception


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
            downloaded_file = robust_download(url, temp_dir, max_retries=3)
        except yt_dlp.utils.DownloadError as e:
            shutil.rmtree(temp_dir, ignore_errors=True)
            return jsonify({"error": f"DownloadError: {str(e)}"}), 500
        except Exception as e:
            shutil.rmtree(temp_dir, ignore_errors=True)
            return jsonify({"error": f"Unexpected error: {str(e)}"}), 500

        if not os.path.exists(downloaded_file):
            shutil.rmtree(temp_dir, ignore_errors=True)
            return jsonify({"error": "File missing after download"}), 500

        file_size = os.path.getsize(downloaded_file)
        mime_type, _ = mimetypes.guess_type(downloaded_file)
        mime_type = mime_type or "application/octet-stream"

        schedule_delete(downloaded_file, delay=15)
        schedule_delete(temp_dir, delay=20, is_dir=True)

        original_name = os.path.basename(downloaded_file)
        try:
            safe_filename = original_name.encode("latin-1").decode("latin-1")
        except UnicodeEncodeError:
            safe_filename = quote(original_name)

        response = send_file(
            downloaded_file,
            as_attachment=True,
            download_name=original_name,
            mimetype=mime_type,
            conditional=True,
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
    return jsonify({
        "message": "yt-dlp API running",
        "usage": "POST /download with JSON {url: <video_url>}",
        "returns": "Binary MP4 + metadata in HTTP headers",
        "example_headers": {
            "X-Filename": "video.mp4",
            "X-Size-Bytes": "12345678",
            "X-Mime-Type": "video/mp4"
        },
    })


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=8080, threaded=True)
