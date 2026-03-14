from app.converter import to_simplified


def test_basic_conversion():
    assert to_simplified("歡迎") == "欢迎"


def test_taiwan():
    assert to_simplified("臺灣") == "台湾"


def test_software():
    assert to_simplified("軟體") in {"软件", "软体"}


def test_already_simplified_is_unchanged():
    text = "你好世界"
    assert to_simplified(text) == text


def test_empty_string():
    assert to_simplified("") == ""


def test_mixed_text():
    result = to_simplified("Chapter 1: 歡迎來到臺灣")
    assert "欢迎" in result
    assert "台湾" in result
