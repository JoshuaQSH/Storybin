from pathlib import Path


def test_frontend_includes_upload_conversion_ui():
    html = (Path(__file__).resolve().parents[2] / "frontend" / "index.html").read_text(encoding="utf-8")

    assert "<title>半夏故事匣</title>" in html
    assert "<h1>半夏故事匣</h1>" in html
    assert '<div class="eyebrow">Storybin</div>' in html
    assert "Storybin 会自动去备用网址查找" in html
    assert "导入共享书架" not in html
    assert 'id="cacheFileInput"' not in html
    assert 'id="cacheUrlInput"' not in html
    assert 'id="cacheButton"' not in html
    assert "/contribute/cache" not in html
    assert "上传自带 TXT" in html
    assert 'id="uploadInput"' in html
    assert 'id="uploadButton"' in html
    assert "/convert/upload" in html
    assert "下载简体 .epub" in html
    assert "备用网址结果" in html
    assert "Owner:</strong> S. Qiu" in html
    assert "Minimal rights reserved" in html
