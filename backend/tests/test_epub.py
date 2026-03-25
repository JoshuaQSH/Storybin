from io import BytesIO
from zipfile import ZipFile

from app.epub import build_epub, split_text_into_sections


def test_split_text_into_sections_uses_chapter_headings():
    sections = split_text_into_sections(
        "台湾恋曲",
        "作者甲",
        "《台湾恋曲》\n作者：作者甲\n\n第1章 初见\n\n欢迎来到台湾。\n\n第2章 重逢\n\n他们再次相遇。",
    )

    assert [section.title for section in sections] == ["第1章 初见", "第2章 重逢"]
    assert sections[0].paragraphs == ["欢迎来到台湾。"]


def test_build_epub_creates_valid_archive_with_content():
    epub_bytes = build_epub(
        "台湾恋曲",
        "作者甲",
        "《台湾恋曲》\n作者：作者甲\n\n第1章 初见\n\n欢迎来到台湾。\n",
    )

    with ZipFile(BytesIO(epub_bytes)) as archive:
        assert archive.read("mimetype") == b"application/epub+zip"
        names = set(archive.namelist())
        assert "META-INF/container.xml" in names
        assert "OEBPS/content.opf" in names
        assert "OEBPS/nav.xhtml" in names
        assert "OEBPS/toc.ncx" in names
        chapter = archive.read("OEBPS/text/chapter-001.xhtml").decode("utf-8")
        assert "台湾恋曲" in chapter
        assert "欢迎来到台湾。" in chapter
