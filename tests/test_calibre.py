import struct
import subprocess

import pytest

from app import calibre, worker


def kf8(version=8):
    data = bytearray(134)
    data[60:68] = b'BOOKMOBI'
    struct.pack_into('>H', data, 76, 2)
    struct.pack_into('>I', data, 78, 94)
    data[110:114] = b'MOBI'
    struct.pack_into('>I', data, 130, version)
    return bytes(data)


@pytest.mark.parametrize('data', [b'PK fake EPUB', kf8(6), kf8()[:100]])
def test_reject_non_kf8(tmp_path, data):
    output = tmp_path / 'book.azw3'
    output.write_bytes(data)
    with pytest.raises(ValueError):
        calibre.validate_azw3(output)


def test_conversion_failure_keeps_epub_and_cleans_partial(tmp_path, monkeypatch):
    epub = tmp_path / 'book.epub'
    epub.write_bytes(b'original EPUB')
    monkeypatch.setattr(calibre, 'find_converter', lambda: 'ebook-convert')
    def fail(command, **kwargs):
        (tmp_path / 'converting.azw3').write_bytes(b'partial')
        raise subprocess.CalledProcessError(1, command)
    monkeypatch.setattr(calibre.subprocess, 'run', fail)
    with pytest.raises(subprocess.CalledProcessError):
        calibre.convert_epub(epub, tmp_path / 'output.azw3')
    assert epub.read_bytes() == b'original EPUB'
    assert not (tmp_path / 'converting.azw3').exists()
    assert not (tmp_path / 'output.azw3').exists()


def test_azw3_only_does_not_reparse_pdf(tmp_path, monkeypatch):
    (tmp_path / 'output.epub').write_bytes(b'original EPUB')
    def unexpected_parse(*args):
        pytest.fail('Existing EPUB must not trigger PDF parsing')
    monkeypatch.setattr(worker, 'convert', unexpected_parse)
    monkeypatch.setattr(calibre, 'find_converter', lambda: 'ebook-convert')
    def convert(command, **kwargs):
        from pathlib import Path
        assert Path(command[1]).read_bytes() == b'original EPUB'
        Path(command[2]).write_bytes(kf8())
    monkeypatch.setattr(calibre.subprocess, 'run', convert)
    worker.run(tmp_path, 'standard', 'auto', azw3_only=True)
    calibre.validate_azw3(tmp_path / 'output.azw3')
    assert (tmp_path / 'stage.txt').read_text() == 'completed'
