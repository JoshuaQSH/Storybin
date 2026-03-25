"""Minimal EPUB generation helpers."""

from __future__ import annotations

from dataclasses import dataclass
from html import escape
from io import BytesIO
import re
import uuid
from zipfile import ZIP_DEFLATED, ZIP_STORED, ZipFile

CHAPTER_HEADING_RE = re.compile(r"^第.+?[章节卷回部篇集].*$")


@dataclass(slots=True)
class EpubSection:
    title: str
    paragraphs: list[str]


def build_epub(title: str, author: str, content_txt: str) -> bytes:
    book_id = f"urn:uuid:{uuid.uuid4()}"
    sections = split_text_into_sections(title, author, content_txt)

    buffer = BytesIO()
    with ZipFile(buffer, "w") as archive:
        archive.writestr("mimetype", "application/epub+zip", compress_type=ZIP_STORED)
        archive.writestr("META-INF/container.xml", _container_xml(), compress_type=ZIP_DEFLATED)
        archive.writestr("OEBPS/styles.css", _styles_css(), compress_type=ZIP_DEFLATED)
        archive.writestr("OEBPS/nav.xhtml", _nav_xhtml(title, sections), compress_type=ZIP_DEFLATED)

        manifest_items = [
            ('<item id="nav" href="nav.xhtml" media-type="application/xhtml+xml" properties="nav"/>'),
            ('<item id="css" href="styles.css" media-type="text/css"/>'),
        ]
        spine_items = []
        nav_points = []

        for index, section in enumerate(sections, start=1):
            chapter_filename = f"text/chapter-{index:03d}.xhtml"
            chapter_id = f"chapter-{index:03d}"
            archive.writestr(
                f"OEBPS/{chapter_filename}",
                _chapter_xhtml(title, section.title, section.paragraphs),
                compress_type=ZIP_DEFLATED,
            )
            manifest_items.append(
                f'<item id="{chapter_id}" href="{chapter_filename}" media-type="application/xhtml+xml"/>'
            )
            spine_items.append(f'<itemref idref="{chapter_id}"/>')
            nav_points.append(
                (
                    index,
                    chapter_id,
                    chapter_filename,
                    section.title,
                )
            )

        archive.writestr(
            "OEBPS/toc.ncx",
            _toc_ncx(title, book_id, nav_points),
            compress_type=ZIP_DEFLATED,
        )
        manifest_items.append('<item id="ncx" href="toc.ncx" media-type="application/x-dtbncx+xml"/>')
        archive.writestr(
            "OEBPS/content.opf",
            _content_opf(title, author, book_id, manifest_items, spine_items),
            compress_type=ZIP_DEFLATED,
        )

    return buffer.getvalue()


def split_text_into_sections(title: str, author: str, content_txt: str) -> list[EpubSection]:
    lines = [line.strip() for line in content_txt.splitlines()]
    body_lines = _strip_leading_metadata(lines, title, author)

    sections: list[EpubSection] = []
    current_title = "正文"
    current_paragraphs: list[str] = []

    for line in body_lines:
        if not line:
            continue
        if CHAPTER_HEADING_RE.match(line):
            if current_paragraphs:
                sections.append(EpubSection(title=current_title, paragraphs=current_paragraphs))
            current_title = line
            current_paragraphs = []
            continue
        current_paragraphs.append(line)

    if current_paragraphs or not sections:
        sections.append(EpubSection(title=current_title, paragraphs=current_paragraphs or [content_txt.strip()]))

    return sections


def _strip_leading_metadata(lines: list[str], title: str, author: str) -> list[str]:
    remaining = lines[:]
    while remaining and not remaining[0]:
        remaining.pop(0)

    title_candidates = {title.strip(), f"《{title.strip()}》"} if title.strip() else set()
    if remaining and remaining[0] in title_candidates:
        remaining.pop(0)

    while remaining and not remaining[0]:
        remaining.pop(0)

    author_line = f"作者：{author.strip()}" if author.strip() else ""
    if author_line and remaining and remaining[0] == author_line:
        remaining.pop(0)

    while remaining and not remaining[0]:
        remaining.pop(0)

    return remaining


def _container_xml() -> str:
    return """<?xml version="1.0" encoding="UTF-8"?>
<container version="1.0" xmlns="urn:oasis:names:tc:opendocument:xmlns:container">
  <rootfiles>
    <rootfile full-path="OEBPS/content.opf" media-type="application/oebps-package+xml"/>
  </rootfiles>
</container>
"""


def _styles_css() -> str:
    return """body { font-family: serif; line-height: 1.6; margin: 5%%; }
h1, h2 { text-align: center; }
p { text-indent: 2em; margin: 0.7em 0; }
"""


def _nav_xhtml(title: str, sections: list[EpubSection]) -> str:
    nav_items = "\n".join(
        f'        <li><a href="text/chapter-{index:03d}.xhtml">{escape(section.title)}</a></li>'
        for index, section in enumerate(sections, start=1)
    )
    return f"""<?xml version="1.0" encoding="utf-8"?>
<!DOCTYPE html>
<html xmlns="http://www.w3.org/1999/xhtml" xmlns:epub="http://www.idpf.org/2007/ops" lang="zh-CN">
  <head>
    <title>{escape(title)}</title>
    <link rel="stylesheet" type="text/css" href="styles.css" />
  </head>
  <body>
    <nav epub:type="toc" id="toc">
      <h1>{escape(title)}</h1>
      <ol>
{nav_items}
      </ol>
    </nav>
  </body>
</html>
"""


def _chapter_xhtml(book_title: str, section_title: str, paragraphs: list[str]) -> str:
    body = "\n".join(f"    <p>{escape(paragraph)}</p>" for paragraph in paragraphs)
    return f"""<?xml version="1.0" encoding="utf-8"?>
<!DOCTYPE html>
<html xmlns="http://www.w3.org/1999/xhtml" lang="zh-CN">
  <head>
    <title>{escape(section_title)}</title>
    <link rel="stylesheet" type="text/css" href="../styles.css" />
  </head>
  <body>
    <h1>{escape(book_title)}</h1>
    <h2>{escape(section_title)}</h2>
{body}
  </body>
</html>
"""


def _toc_ncx(
    title: str,
    book_id: str,
    nav_points: list[tuple[int, str, str, str]],
) -> str:
    points = "\n".join(
        f"""    <navPoint id="{chapter_id}" playOrder="{index}">
      <navLabel><text>{escape(section_title)}</text></navLabel>
      <content src="{escape(filename)}"/>
    </navPoint>"""
        for index, chapter_id, filename, section_title in nav_points
    )
    return f"""<?xml version="1.0" encoding="UTF-8"?>
<ncx xmlns="http://www.daisy.org/z3986/2005/ncx/" version="2005-1">
  <head>
    <meta name="dtb:uid" content="{escape(book_id)}"/>
    <meta name="dtb:depth" content="1"/>
    <meta name="dtb:totalPageCount" content="0"/>
    <meta name="dtb:maxPageNumber" content="0"/>
  </head>
  <docTitle><text>{escape(title)}</text></docTitle>
  <navMap>
{points}
  </navMap>
</ncx>
"""


def _content_opf(title: str, author: str, book_id: str, manifest_items: list[str], spine_items: list[str]) -> str:
    manifest = "\n    ".join(manifest_items)
    spine = "\n    ".join(spine_items)
    return f"""<?xml version="1.0" encoding="UTF-8"?>
<package xmlns="http://www.idpf.org/2007/opf" version="3.0" unique-identifier="bookid">
  <metadata xmlns:dc="http://purl.org/dc/elements/1.1/">
    <dc:identifier id="bookid">{escape(book_id)}</dc:identifier>
    <dc:title>{escape(title)}</dc:title>
    <dc:language>zh-CN</dc:language>
    <dc:creator>{escape(author or "未知")}</dc:creator>
  </metadata>
  <manifest>
    {manifest}
  </manifest>
  <spine toc="ncx">
    {spine}
  </spine>
</package>
"""
