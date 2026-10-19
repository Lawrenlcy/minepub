from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from importlib.metadata import PackageNotFoundError, version
import logging
import json
import os
from pathlib import Path
import re
import shutil
import sqlite3
import subprocess
import sys
import threading
import time
import uuid

from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pypdf import PdfReader
from app.calibre import find_converter

ROOT = Path(__file__).resolve().parent.parent
ACTIVE = ("queued", "starting", "parsing", "rendering", "converting_azw3")
TIERS = {"flash", "basic", "standard", "advanced"}
MODES = {"auto", "ocr", "txt"}
logger = logging.getLogger("minepub")


class Jobs:
    def __init__(self, root: Path):
        self.root = root.resolve()
        self.root.mkdir(parents=True, exist_ok=True)
        self.lock = threading.RLock()
        self.stopping = threading.Event()
        self.process: subprocess.Popen | None = None
        self.current: str | None = None
        self.thread: threading.Thread | None = None
        with self.db() as db:
            db.execute("""CREATE TABLE IF NOT EXISTS jobs (
                id TEXT PRIMARY KEY, filename TEXT, tier TEXT, mode TEXT,
                status TEXT, pages INTEGER, size INTEGER, created REAL,
                updated REAL, error TEXT DEFAULT '', elapsed REAL DEFAULT 0)""")
            columns = {row["name"] for row in db.execute("PRAGMA table_info(jobs)")}
            if "output_format" not in columns:
                db.execute("ALTER TABLE jobs ADD COLUMN output_format TEXT NOT NULL DEFAULT 'epub'")
            if "kind" not in columns:
                db.execute("ALTER TABLE jobs ADD COLUMN kind TEXT NOT NULL DEFAULT 'parse'")

    def db(self):
        db = sqlite3.connect(self.root / "jobs.sqlite3", timeout=20)
        db.row_factory = sqlite3.Row
        return db

    def get(self, job_id: str):
        if not re.fullmatch(r"[0-9a-f]{32}", job_id):
            raise HTTPException(404, "任务不存在或已清理")
        with self.db() as db:
            row = db.execute("SELECT * FROM jobs WHERE id=?", (job_id,)).fetchone()
        if row is None:
            raise HTTPException(404, "任务不存在或已清理")
        return dict(row)

    def public(self, job_id: str):
        job = self.get(job_id)
        job["downloads"] = {
            fmt: f"/api/jobs/{job_id}/download?format={fmt}"
            for fmt in ("epub", "azw3") if (self.root / job_id / f"output.{fmt}").is_file()
        }
        job["download_url"] = job["downloads"].get("epub")
        return job

    def assert_capacity(self):
        with self.db() as db:
            count = db.execute("SELECT COUNT(*) FROM jobs WHERE status IN (?,?,?,?,?)", ACTIVE).fetchone()[0]
        if count >= 10:
            raise HTTPException(429, "队列已满，请等待现有任务完成")

    def update(self, job_id, **values):
        values["updated"] = time.time()
        with self.db() as db:
            db.execute("UPDATE jobs SET " + ",".join(f"{k}=?" for k in values) + " WHERE id=?",
                       (*values.values(), job_id))

    def start(self):
        # Single local server process; interrupted inference is never reported as success.
        with self.db() as db:
            db.execute("UPDATE jobs SET status='failed', error=?, updated=? WHERE status IN ('starting','parsing','rendering','converting_azw3')",
                       ("服务已重启，转换中断。已生成的 EPUB 仍可下载或重试转 AZW3；否则请重新上传。", time.time()))
        self.thread = threading.Thread(target=self.loop, daemon=True, name="mineru-queue")
        self.thread.start()

    def stop_process(self):
        if self.process and self.process.poll() is None:
            if os.name == "nt":
                subprocess.run(["taskkill", "/PID", str(self.process.pid), "/T", "/F"],
                               stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                               creationflags=subprocess.CREATE_NO_WINDOW)
            else:
                import signal
                os.killpg(self.process.pid, signal.SIGTERM)
            try:
                self.process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                self.process.kill()
                self.process.wait()

    def stop(self):
        self.stopping.set()
        with self.lock:
            self.stop_process()
        if self.thread:
            self.thread.join(timeout=15)

    def cleanup(self):
        cutoff = time.time() - 86400
        with self.lock, self.db() as db:
            rows = db.execute("SELECT id FROM jobs WHERE updated<? AND status IN ('completed','failed','cancelled')", (cutoff,)).fetchall()
            for row in rows:
                shutil.rmtree(self.root / row["id"], ignore_errors=True)
                db.execute("DELETE FROM jobs WHERE id=?", (row["id"],))

    def loop(self):
        while not self.stopping.is_set():
            try:
                self.cleanup()
                with self.lock, self.db() as db:
                    row = db.execute("SELECT * FROM jobs WHERE status='queued' ORDER BY created LIMIT 1").fetchone()
                    if row:
                        self.update(row["id"], status="starting")
                if row:
                    self.run(dict(row))
            except Exception:
                logger.exception("Queue error")
            self.stopping.wait(1)

    def run(self, job):
        job_id = job["id"]
        folder = self.root / job_id
        started = time.monotonic()
        try:
            with (folder / "worker.log").open("ab" if job["kind"] == "azw3" else "wb") as log:
                with self.lock:
                    if self.get(job_id)["status"] == "cancelled" or self.stopping.is_set():
                        return
                    self.current = job_id
                    env = os.environ.copy()
                    env.setdefault("PYTHONUTF8", "1")
                    env.setdefault("MINERU_MODEL_SOURCE", "modelscope")
                    command = [sys.executable, "-u", "-m", "app.worker", str(folder), job["tier"], job["mode"]]
                    if job["kind"] == "azw3":
                        command.append("--azw3-only")
                    elif job["output_format"] == "both":
                        command.append("--azw3")
                    (folder / "stage.txt").unlink(missing_ok=True)
                    self.process = subprocess.Popen(
                        command,
                        cwd=ROOT, env=env, stdout=log, stderr=subprocess.STDOUT,
                        creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
                        start_new_session=os.name != "nt")
                while self.process.poll() is None:
                    with self.lock:
                        status = self.get(job_id)["status"]
                        if status == "cancelled" or self.stopping.is_set():
                            self.stop_process()
                            break
                        if time.monotonic() - started > int(os.getenv("MINEPUB_TIMEOUT_SECONDS", "7200")):
                            self.stop_process()
                            self.update(job_id, status="failed", error="转换超过时限，请拆分 PDF 或调整 MINEPUB_TIMEOUT_SECONDS。")
                            break
                        stage_file = folder / "stage.txt"
                        if stage_file.exists():
                            stage = stage_file.read_text(encoding="utf-8")
                            if stage in {"parsing", "rendering", "converting_azw3"} and stage != status:
                                self.update(job_id, status=stage)
                    self.stopping.wait(0.5)
                with self.lock:
                    status = self.get(job_id)["status"]
                    if status not in {"cancelled", "failed"}:
                        has_epub = (folder / "output.epub").is_file()
                        has_requested_output = has_epub and (job["output_format"] != "both" or (folder / "output.azw3").is_file())
                        if self.process.returncode == 0 and has_requested_output:
                            self.update(job_id, status="completed", elapsed=time.monotonic() - started)
                        else:
                            message = ("AZW3 转换失败，EPUB 已保留，可下载或重试生成 AZW3。请查看日志并检查 Calibre 配置。"
                                       if has_epub and job["output_format"] == "both" else
                                       "MinerU 解析失败，请查看任务日志。首次运行可能需要下载模型；检查网络、磁盘空间和模型配置。")
                            self.update(job_id, status="failed", error=message, elapsed=time.monotonic() - started)
        except Exception:
            logger.exception("Conversion failed: %s", job_id)
            self.update(job_id, status="failed", error="转换进程无法运行，请检查服务日志。")
        finally:
            with self.lock:
                self.process = None
                self.current = None


def engine_info():
    try:
        installed = version("mineru")
        supported = installed.split(".")[0] == "4"
    except PackageNotFoundError:
        installed, supported = None, False
    return {"ready": supported, "version": installed,
            "message": "MinerU 4.x 已安装，模型将在首次使用时加载" if supported else "请安装 MinerU 4.x 后再开始转换"}


def validate_pdf(path: Path):
    try:
        with path.open("rb") as source:
            if b"%PDF-" not in source.read(1024):
                raise ValueError("不是有效的 PDF 文件")
        with PdfReader(path) as reader:
            if reader.is_encrypted:
                raise ValueError("PDF 已加密，请先解除密码保护后上传")
            count = len(reader.pages)
            if count == 0:
                raise ValueError("PDF 没有可转换的页面")
            if count > 2000:
                raise ValueError("单份 PDF 最多支持 2000 页，请拆分后上传")
            return count
    except ValueError:
        raise
    except Exception as exc:
        raise ValueError("无法读取这份 PDF，请检查文件是否完整") from exc


def create_app(data_dir: Path | None = None, run_worker: bool = True):
    jobs = Jobs(data_dir or Path(os.getenv("MINEPUB_DATA_DIR", str(ROOT / "data"))))
    upload_lock = asyncio.Lock()
    limit = int(os.getenv("MINEPUB_MAX_UPLOAD_MB", "200")) * 1024 * 1024

    @asynccontextmanager
    async def lifespan(app):
        if run_worker:
            jobs.start()
        yield
        if run_worker:
            await asyncio.to_thread(jobs.stop)

    app = FastAPI(title="Minepub · PDF to EPUB / AZW3", lifespan=lifespan)
    app.state.jobs = jobs

    @app.middleware("http")
    async def local_only(request, call_next):
        # Prevent websites and DNS rebinding from driving this unauthenticated local app.
        if request.url.hostname not in {"127.0.0.1", "localhost", "::1"}:
            return JSONResponse({"detail": "本应用只接受本机访问"}, status_code=403)
        origin = request.headers.get("origin")
        if origin and origin != str(request.base_url).rstrip("/"):
            return JSONResponse({"detail": "不允许跨站请求"}, status_code=403)
        if request.headers.get("sec-fetch-site") == "cross-site":
            return JSONResponse({"detail": "不允许跨站请求"}, status_code=403)
        response = await call_next(request)
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Referrer-Policy"] = "no-referrer"
        response.headers["Content-Security-Policy"] = "default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; connect-src 'self'; object-src 'none'; frame-ancestors 'none'; base-uri 'self'"
        if request.url.path.startswith("/api/"):
            response.headers["Cache-Control"] = "no-store"
        return response

    @app.get("/api/health")
    def health():
        return {"engine": engine_info(), "azw3": {"ready": find_converter() is not None},
                "max_upload_mb": limit // (1024 * 1024), "retention_hours": 24}

    @app.post("/api/jobs", status_code=202)
    async def upload(request: Request, filename: str = Query(min_length=1, max_length=240), tier: str = "standard", mode: str = "auto", output_format: str = "epub"):
        if tier not in TIERS or mode not in MODES or output_format not in {"epub", "both"}:
            raise HTTPException(422, "不支持的解析选项")
        if output_format == "both" and not find_converter():
            raise HTTPException(503, "AZW3 需要 Calibre，请先运行 setup-azw3.ps1；也可先选择仅 EPUB")
        if not engine_info()["ready"]:
            raise HTTPException(503, "请先安装 MinerU 4.x")
        filename = filename.replace("\\", "/").split("/")[-1]
        filename = "".join(c for c in filename if ord(c) >= 32)
        if not filename.lower().endswith(".pdf"):
            raise HTTPException(415, "请上传 .pdf 文件")
        if request.headers.get("content-type", "").split(";")[0] != "application/pdf":
            raise HTTPException(415, "请以 application/pdf 上传文件")
        length = request.headers.get("content-length")
        if length:
            try:
                if int(length) > limit:
                    raise HTTPException(413, "PDF 超过上传大小限制")
            except ValueError:
                raise HTTPException(400, "无效的 Content-Length")
        async with upload_lock:
            jobs.assert_capacity()
            job_id = uuid.uuid4().hex
            folder = jobs.root / job_id
            folder.mkdir()
            try:
                size = 0
                with (folder / "input.pdf").open("wb") as target:
                    async for chunk in request.stream():
                        size += len(chunk)
                        if size > limit:
                            raise HTTPException(413, "PDF 超过上传大小限制")
                        target.write(chunk)
                try:
                    pages = await asyncio.to_thread(validate_pdf, folder / "input.pdf")
                except ValueError as exc:
                    raise HTTPException(422, str(exc)) from exc
                (folder / "metadata.json").write_text(json.dumps({"title": Path(filename).stem}, ensure_ascii=False), encoding="utf-8")
                with jobs.lock, jobs.db() as db:
                    jobs.assert_capacity()
                    now = time.time()
                    db.execute("INSERT INTO jobs (id,filename,tier,mode,status,pages,size,created,updated,output_format) VALUES (?,?,?,?,?,?,?,?,?,?)",
                               (job_id, filename, tier, mode, "queued", pages, size, now, now, output_format))
                return jobs.public(job_id)
            except BaseException:
                shutil.rmtree(folder, ignore_errors=True)
                raise

    @app.get("/api/jobs/{job_id}")
    def status(job_id: str):
        return jobs.public(job_id)

    @app.post("/api/jobs/{job_id}/azw3", status_code=202)
    def add_azw3(job_id: str):
        with jobs.lock:
            job = jobs.get(job_id)
            folder = jobs.root / job_id
            if job["status"] in ACTIVE or jobs.current == job_id:
                raise HTTPException(409, "任务仍在处理中，请等待完成后再生成 AZW3")
            if not (folder / "output.epub").is_file():
                raise HTTPException(409, "请先完成 EPUB 转换")
            if (folder / "output.azw3").is_file():
                return jobs.public(job_id)
            if not find_converter():
                raise HTTPException(503, "未找到 Calibre，请先运行 setup-azw3.ps1")
            jobs.assert_capacity()
            jobs.update(job_id, status="queued", kind="azw3", output_format="both", error="", elapsed=0)
        return jobs.public(job_id)

    @app.post("/api/jobs/{job_id}/cancel")
    def cancel(job_id: str):
        with jobs.lock:
            job = jobs.get(job_id)
            if job["status"] not in ACTIVE:
                raise HTTPException(409, "任务已结束")
            jobs.update(job_id, status="cancelled")
            if jobs.current == job_id:
                jobs.stop_process()
        return jobs.public(job_id)

    @app.delete("/api/jobs/{job_id}", status_code=204)
    def delete(job_id: str):
        with jobs.lock:
            job = jobs.get(job_id)
            if job["status"] in ACTIVE or jobs.current == job_id:
                raise HTTPException(409, "请先取消任务，等待进程结束后再删除")
            shutil.rmtree(jobs.root / job_id, ignore_errors=True)
            with jobs.db() as db:
                db.execute("DELETE FROM jobs WHERE id=?", (job_id,))

    @app.get("/api/jobs/{job_id}/download")
    def download(job_id: str, format: str = "epub"):
        job = jobs.get(job_id)
        if format not in {"epub", "azw3"}:
            raise HTTPException(422, "只支持 EPUB 或 AZW3 下载")
        path = jobs.root / job_id / f"output.{format}"
        if not path.is_file():
            raise HTTPException(409, f"{format.upper()} 尚未生成或已清理")
        media_type = "application/epub+zip" if format == "epub" else "application/vnd.amazon.mobi8-ebook"
        return FileResponse(path, media_type=media_type, filename=Path(job["filename"]).stem + f".{format}")

    @app.get("/api/jobs/{job_id}/log")
    def log(job_id: str):
        jobs.get(job_id)
        path = jobs.root / job_id / "worker.log"
        if not path.is_file():
            return {"text": "任务尚未开始，暂无日志。"}
        with path.open("rb") as source:
            source.seek(max(0, path.stat().st_size - 16000))
            text = source.read().decode("utf-8", errors="replace")
            text = re.sub(r"\x1b\[[0-?]*[ -/]*[@-~]", "", text).replace("\r", "")
            return {"text": text}

    app.mount("/", StaticFiles(directory=ROOT / "static", html=True), name="static")
    return app


app = create_app()
