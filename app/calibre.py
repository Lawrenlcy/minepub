"""Local EPUB → Kindle KF8 conversion using calibre's official CLI."""
from __future__ import annotations

import os
from pathlib import Path
import shutil
import struct
import subprocess

ROOT = Path(__file__).resolve().parent.parent


def find_converter() -> str | None:
    override = os.getenv("MINEPUB_EBOOK_CONVERT")
    if override:
        # An explicit configuration error must not silently select another binary.
        return str(Path(override).resolve()) if Path(override).is_file() else None
    candidates = [
        ROOT / "tools" / "calibre" / "Calibre Portable" / "Calibre" / "ebook-convert.exe",
        ROOT / "tools" / "calibre" / "Calibre" / "ebook-convert.exe",
        ROOT / "tools" / "calibre" / "ebook-convert.exe",
    ]
    for variable in ("ProgramFiles", "ProgramFiles(x86)"):
        if os.getenv(variable):
            candidates.append(Path(os.environ[variable]) / "Calibre2" / "ebook-convert.exe")
    for candidate in candidates:
        if candidate.is_file():
            return str(candidate)
    return shutil.which("ebook-convert")


def validate_azw3(path: Path) -> None:
    """Require a Palm database with a KF8 MOBI header, not a renamed EPUB/MOBI6."""
    with path.open("rb") as source:
        header = source.read(86)
        if len(header) < 86 or header[60:68] != b"BOOKMOBI":
            raise ValueError("转换结果不是有效的 Kindle 电子书")
        count = struct.unpack_from(">H", header, 76)[0]
        first_record = struct.unpack_from(">I", header, 78)[0]
        if count < 2 or first_record < 78 + count * 8 or first_record + 40 > path.stat().st_size:
            raise ValueError("AZW3 记录表不完整")
        source.seek(first_record)
        record = source.read(40)
        if record[16:20] != b"MOBI" or struct.unpack_from(">I", record, 36)[0] != 8:
            raise ValueError("转换结果不是 AZW3 / KF8 格式")


def convert_epub(epub: Path, output: Path) -> None:
    executable = find_converter()
    if not executable:
        raise RuntimeError("未找到 Calibre，请运行 setup-azw3.ps1 或设置 MINEPUB_EBOOK_CONVERT")
    # The suffix determines calibre's output format; keep it .azw3 until publication.
    temporary = output.with_name("converting.azw3")
    env = os.environ.copy()
    env["CALIBRE_CONFIG_DIRECTORY"] = str(ROOT / "data" / "calibre-config")
    env["CALIBRE_TEMP_DIR"] = str(output.parent)
    try:
        subprocess.run(
            [executable, str(epub.resolve()), str(temporary.resolve()),
             "--disable-font-rescaling", "--chapter", "/", "--chapter-mark", "none",
             "--page-breaks-before", "/"],
            check=True, timeout=600, env=env,
            creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
        )
        validate_azw3(temporary)
        temporary.replace(output)
    finally:
        temporary.unlink(missing_ok=True)
