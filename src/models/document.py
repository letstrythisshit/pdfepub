from dataclasses import dataclass, field
from typing import Optional


@dataclass
class TextSpan:
    """Inline text with formatting info."""
    text: str
    is_bold: bool = False
    is_italic: bool = False
    language: str = ""  # non-empty for foreign language spans
    is_superscript: bool = False
    footnote_ref: str = ""  # footnote ID if this is a reference


@dataclass
class Paragraph:
    """A paragraph of text with inline formatting."""
    text: str
    spans: list = field(default_factory=list)  # list of TextSpan
    is_first_in_section: bool = False  # -> class="txt-p" vs "txt"
    page_break_before: int = 0  # page number for pagebreak before this para, 0=none

    def get_plain_text(self):
        if self.spans:
            return ''.join(s.text for s in self.spans)
        return self.text


@dataclass
class Footnote:
    """A footnote with reference back to text."""
    footnote_id: str  # e.g. "fn-1-3" (chapter 1, note 3)
    marker: str  # the superscript text ("1", "*", etc.)
    text: str
    ref_id: str = ""  # ID of the reference point in text


@dataclass
class Chapter:
    """A chapter or section in the document."""
    title: str
    level: int  # 1=part, 2=chapter, 3=section
    paragraphs: list = field(default_factory=list)  # list of Paragraph
    footnotes: list = field(default_factory=list)  # list of Footnote
    sub_chapters: list = field(default_factory=list)  # nested Chapters
    start_page: int = 0
    epub_filename: str = ""  # e.g. "chapter_01.xhtml"
    images: list = field(default_factory=list)  # list of (path, alt_text)


@dataclass
class TocEntry:
    """An entry in the table of contents."""
    title: str
    level: int
    target_file: str  # e.g. "chapter_01.xhtml"
    target_anchor: str = ""  # e.g. "#section1"
    children: list = field(default_factory=list)


@dataclass
class DocumentStructure:
    """The complete structured document ready for EPUB generation."""
    title: str
    author: str
    language: str = "lt-LT"
    isbn: str = ""
    publisher: str = ""
    date: str = ""
    description: str = ""

    front_matter: list = field(default_factory=list)  # list of Chapter (cover, title, copyright)
    chapters: list = field(default_factory=list)  # list of Chapter
    back_matter: list = field(default_factory=list)  # list of Chapter

    toc: list = field(default_factory=list)  # list of TocEntry
    page_map: dict = field(default_factory=dict)  # page_num -> (filename, anchor_id)
    total_pages: int = 0

    images: list = field(default_factory=list)  # list of (path, alt_text)
    cover_image: str = ""
