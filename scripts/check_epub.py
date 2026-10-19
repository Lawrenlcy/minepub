"""Structural EPUB smoke check; does not replace EPUBCheck or visual proofreading."""
import argparse
from pathlib import Path, PurePosixPath
from urllib.parse import unquote, urlsplit
import zipfile
import xml.etree.ElementTree as ET


def check(path: Path):
    with zipfile.ZipFile(path) as archive:
        assert archive.infolist()[0].filename == "mimetype"
        assert archive.infolist()[0].compress_type == zipfile.ZIP_STORED
        assert archive.read("mimetype") == b"application/epub+zip"
        assert archive.testzip() is None
        names = set(archive.namelist())
        container = ET.fromstring(archive.read("META-INF/container.xml"))
        rootfile = next(e for e in container.iter() if e.tag.endswith("rootfile"))
        package_path = rootfile.attrib["full-path"]
        package = ET.fromstring(archive.read(package_path))
        manifest = {e.attrib["id"]: e for e in package.iter() if e.tag.endswith("}item")}
        for item in manifest.values():
            name = str(PurePosixPath(package_path).parent / item.attrib["href"])
            assert name in names, f"Missing manifest asset: {name}"
        for item in package.iter():
            if item.tag.endswith("itemref"):
                assert item.attrib["idref"] in manifest
        documents = {n: ET.fromstring(archive.read(n)) for n in names if n.endswith(".xhtml")}
        import posixpath
        for name, document in documents.items():
            for element in document.iter():
                for attr in ("src", "href"):
                    link = element.attrib.get(attr)
                    if not link:
                        continue
                    parts = urlsplit(link)
                    if parts.scheme or parts.netloc:
                        continue
                    target = posixpath.normpath(posixpath.join(posixpath.dirname(name), unquote(parts.path))) if parts.path else name
                    assert target in names, f"Broken asset: {link} in {name}"
                    if parts.fragment and target in documents:
                        assert any(e.get("id") == unquote(parts.fragment) for e in documents[target].iter()), f"Broken anchor: {link}"
        pages = {int(e.attrib["data-page-idx"]) for doc in documents.values() for e in doc.iter() if "data-page-idx" in e.attrib}
        text = "".join("".join(doc.itertext()) for doc in documents.values())
        headings = sum(1 for doc in documents.values() for e in doc.iter() if e.tag.rsplit("}", 1)[-1] in {"h1", "h2", "h3", "h4", "h5", "h6"})
        print({"file": str(path), "bytes": path.stat().st_size, "pages_represented": len(pages), "first_page": min(pages) if pages else None,
               "last_page": max(pages) if pages else None, "text_characters": len(text), "headings": headings, "structural_check": "passed"})
        return text, pages


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("epub", type=Path)
    args = parser.parse_args()
    check(args.epub)
