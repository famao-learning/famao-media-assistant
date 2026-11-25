"""将桌面内置浏览器无法解码的视频转成预览副本，发布始终使用原文件。"""
import atexit
import os
from pathlib import Path
import re
import shutil
import subprocess
import threading
import time
import uuid

from . import store

DIRECTORY = store.DATA / "previews"
_jobs = {}
_lock = threading.RLock()


def ffmpeg_path():
    executable = shutil.which("ffmpeg")
    if executable:
        return executable
    try:
        import imageio_ffmpeg
        return imageio_ffmpeg.get_ffmpeg_exe()
    except (ImportError, RuntimeError):
        raise ValueError("缺少视频预览组件，请重新安装完整应用后重试")


def _remove(job):
    process = job.get("process")
    if process and process.poll() is None:
        process.kill()
        process.wait()
    folder = job["folder"].resolve()
    if folder.parent == DIRECTORY.resolve() and re.fullmatch(r"[a-f0-9]{32}", folder.name):
        def remove_files():
            # Windows 的视频读取句柄可能比页面取消稍晚释放。
            for _ in range(25):
                try:
                    shutil.rmtree(folder)
                    return
                except FileNotFoundError:
                    return
                except OSError:
                    time.sleep(.2)
        worker = threading.Thread(target=remove_files, daemon=True)
        worker.start()
        return worker


def cancel(key):
    with _lock:
        job = _jobs.pop(key, None)
        if job:
            job["cancelled"] = True
            return _remove(job)


def cleanup():
    workers = []
    for key in list(_jobs):
        worker = cancel(key)
        if worker:
            workers.append(worker)
    for worker in workers:
        worker.join(timeout=5)


atexit.register(cleanup)


def start(file):
    executable = ffmpeg_path()
    with _lock:
        for key, job in list(_jobs.items()):
            if time.monotonic() - job["updated"] > 3600:
                cancel(key)
        if sum(job["status"] == "processing" for job in _jobs.values()) >= 2:
            raise ValueError("正在生成视频预览，请稍后重试")
        key = uuid.uuid4().hex
        folder = DIRECTORY / key
        folder.mkdir(parents=True)
        source = folder / ("source" + Path(file.filename).suffix.lower())
        output = folder / "preview.webm"
        job = {"folder": folder, "output": output, "status": "processing", "updated": time.monotonic(), "cancelled": False}
        try:
            file.save(source)
        except Exception:
            _remove(job)
            raise
        _jobs[key] = job

    def convert():
        try:
            command = [executable, "-nostdin", "-y", "-v", "error", "-i", str(source),
                       "-map", "0:v:0", "-map", "0:a:0?", "-vf",
                       "scale='min(640,iw)':'min(640,ih)':force_original_aspect_ratio=decrease:force_divisible_by=2,fps=24",
                       "-c:v", "libvpx", "-deadline", "realtime", "-cpu-used", "8", "-b:v", "1000k",
                       "-c:a", "libopus", "-b:a", "96k", str(output)]
            with _lock:
                if job["cancelled"]:
                    return
                job["process"] = subprocess.Popen(command, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE,
                    creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0)
            _, error = job["process"].communicate(timeout=900)
            with _lock:
                if job["cancelled"]:
                    return
                if job["process"].returncode or not output.exists():
                    job.update(status="error", error="无法解码此视频，请检查原文件是否损坏或使用 MP4 格式")
                else:
                    job["status"] = "ready"
                job["updated"] = time.monotonic()
        except Exception:
            with _lock:
                if not job["cancelled"]:
                    job.update(status="error", error="视频预览生成失败，请重新选择文件重试")
                    process = job.get("process")
                    if process and process.poll() is None:
                        process.kill()
                        process.wait()
        finally:
            try:
                source.unlink(missing_ok=True)
            except OSError:
                pass

    threading.Thread(target=convert, daemon=True).start()
    return key


def get(key):
    with _lock:
        job = _jobs.get(key)
        if not job:
            return None
        job["updated"] = time.monotonic()
        return {"status": job["status"], "error": job.get("error", "")}


def video_path(key):
    with _lock:
        job = _jobs.get(key)
        return job["output"] if job and job["status"] == "ready" else None
