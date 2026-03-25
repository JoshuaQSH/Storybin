from pathlib import Path


def test_frontend_includes_upload_conversion_ui():
    html = (Path(__file__).resolve().parents[2] / "frontend" / "index.html").read_text(encoding="utf-8")

    assert "上传自带 TXT" in html
    assert 'id="uploadInput"' in html
    assert 'id="uploadButton"' in html
    assert "/convert/upload" in html
    assert "下载简体 .epub" in html
