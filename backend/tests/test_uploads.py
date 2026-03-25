import hashlib

import pytest

from app.uploads import UploadedTextDecodeError, convert_uploaded_txt, decode_uploaded_text, extract_title_author


def test_decode_uploaded_text_accepts_utf8_sig():
    raw = "\ufeff《臺灣戀曲》\n作者：作者甲\n\n歡迎來到臺灣。".encode("utf-8-sig")

    text = decode_uploaded_text(raw)

    assert "《臺灣戀曲》" in text
    assert "歡迎來到臺灣。" in text


def test_extract_title_author_falls_back_to_filename():
    title, author = extract_title_author("第一章 測試\n歡迎來到臺灣。", "my-novel.txt")

    assert title == "my-novel"
    assert author == "未知"


def test_convert_uploaded_txt_returns_simplified_text_and_metadata():
    raw = "《臺灣戀曲》\n作者：作者甲\n\n第1章 初見\n\n歡迎來到臺灣。".encode("utf-8")

    converted = convert_uploaded_txt("taiwan-love.txt", raw)

    assert converted.title_tc == "臺灣戀曲"
    assert converted.title_sc == "台湾恋曲"
    assert converted.author_tc == "作者甲"
    assert converted.author_sc == "作者甲"
    assert "欢迎来到台湾。" in converted.content_txt
    assert converted.content_txt.endswith("\n")
    assert converted.content_sha256 == hashlib.sha256(converted.content_txt.encode("utf-8")).hexdigest()


def test_convert_uploaded_txt_rejects_empty_input():
    with pytest.raises(UploadedTextDecodeError, match="empty"):
        convert_uploaded_txt("empty.txt", b"")
