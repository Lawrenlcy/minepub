"""Isolated MinerU worker: a failed inference never takes down the web server."""
from __future__ import annotations

import argparse
import io
import json
from pathlib import Path
import zipfile


def set_stage(folder: Path, value: str) -> None:
    temp = folder / "stage.tmp"
    temp.write_text(value, encoding="utf-8")
    temp.replace(folder / "stage.txt")


def convert(folder: Path, tier: str, mode: str) -> None:
    from mineru.parser import parse
    from mineru.render import EpubRenderOptions, RenderFormat, render

    set_stage(folder, "parsing")
    result = parse(str(folder / "input.pdf"), tier=tier, ocr_mode=mode)
    set_stage(folder, "rendering")
    metadata_file = folder / "metadata.json"
    metadata = json.loads(metadata_file.read_text(encoding="utf-8")) if metadata_file.exists() else {}
    epub = render(result.middle_json, RenderFormat.EPUB,
                  options=EpubRenderOptions(title=metadata.get("title")))
    if not isinstance(epub, bytes):
        raise TypeError("MinerU EPUB renderer did not return bytes")
    with zipfile.ZipFile(io.BytesIO(epub)) as archive:
        if archive.read("mimetype") != b"application/epub+zip":
            raise ValueError("Invalid EPUB mimetype")
        if "META-INF/container.xml" not in archive.namelist():
            raise ValueError("EPUB container missing")
        if archive.testzip() is not None:
            raise ValueError("EPUB archive is corrupt")
    target = folder / "output.tmp"
    target.write_bytes(epub)
    target.replace(folder / "output.epub")


def run(folder: Path, tier: str, mode: str, azw3: bool = False, azw3_only: bool = False) -> None:
    if not azw3_only:
        convert(folder, tier, mode)
    if azw3 or azw3_only:
        from app.calibre import convert_epub
        set_stage(folder, "converting_azw3")
        convert_epub(folder / "output.epub", folder / "output.azw3")
    set_stage(folder, "completed")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("folder", type=Path)
    parser.add_argument("tier", choices=["flash", "basic", "standard", "advanced"])
    parser.add_argument("mode", choices=["auto", "ocr", "txt"])
    parser.add_argument("--azw3", action="store_true")
    parser.add_argument("--azw3-only", action="store_true")
    args = parser.parse_args()
    run(args.folder, args.tier, args.mode, args.azw3, args.azw3_only)

