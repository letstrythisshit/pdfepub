import re
from xml.sax.saxutils import escape

from ..models.document import Chapter, DocumentStructure, Paragraph, TextSpan


class XHTMLBuilder:
    """Generate WCAG-compliant XHTML chapter files following certified example."""

    XHTML_HEADER = '''<?xml version="1.0" encoding="utf-8"?>
<!DOCTYPE html>
<html xmlns="http://www.w3.org/1999/xhtml" lang="{lang}" xml:lang="{lang}"
      xmlns:epub="http://www.idpf.org/2007/ops">
<head>
  <meta charset="utf-8"/>
  <title>{title}</title>
  <link rel="stylesheet" type="text/css" href="css/styles.css"/>
</head>
<body>
'''

    XHTML_FOOTER = '''</body>
</html>
'''

    def build_chapter(self, chapter: Chapter, doc: DocumentStructure,
                      is_front_matter: bool = False) -> str:
        lang = doc.language or "lt-LT"
        title = escape(chapter.title)

        parts = [self.XHTML_HEADER.format(lang=lang, title=title)]

        if is_front_matter:
            parts.append(self._build_front_matter_section(chapter, doc))
        else:
            parts.append(self._build_chapter_section(chapter, doc))

        parts.append(self.XHTML_FOOTER)
        return ''.join(parts)

    def _build_chapter_section(self, chapter: Chapter, doc: DocumentStructure) -> str:
        lines = []
        h_tag = f"h{min(chapter.level + 1, 6)}"  # level 1->h2, level 2->h2, level 3->h3

        # Track emitted page IDs and noteref anchors
        self._emitted_page_ids = set()
        self._emitted_noteref_ids = set()

        # Chapter section with semantic attributes
        lines.append(f'<section epub:type="chapter" role="doc-chapter">')

        # Page break at chapter start
        if chapter.start_page >= 0:
            pn = chapter.start_page + 1
            self._emitted_page_ids.add(pn)
            lines.append(
                f'  <span id="page{pn}" role="doc-pagebreak" '
                f'aria-label="{pn}" epub:type="pagebreak"></span>'
            )

        # Chapter heading
        escaped_title = escape(chapter.title)
        lines.append(
            f'  <{h_tag} aria-label="{escaped_title}">{escaped_title}</{h_tag}>'
        )

        # Paragraphs
        for para in chapter.paragraphs:
            lines.append(self._build_paragraph(para, doc))

        lines.append('</section>')

        # Footnotes section
        if chapter.footnotes:
            lines.append('')
            lines.append('<aside epub:type="footnotes">')
            for fn in chapter.footnotes:
                fn_id = fn.footnote_id
                escaped_text = escape(fn.text)
                marker = escape(fn.marker)
                lines.append(
                    f'  <aside id="{fn_id}" epub:type="footnote" role="doc-footnote">'
                )
                # Only add backlink if the noteref anchor exists in this chapter
                if fn.ref_id and fn.ref_id in self._emitted_noteref_ids:
                    lines.append(
                        f'    <p><a href="#{fn.ref_id}" role="doc-backlink">{marker}</a> '
                        f'{escaped_text}</p>'
                    )
                else:
                    lines.append(f'    <p>{marker}. {escaped_text}</p>')
                lines.append('  </aside>')
            lines.append('</aside>')

        return '\n'.join(lines)

    def _build_front_matter_section(self, chapter: Chapter,
                                    doc: DocumentStructure) -> str:
        self._emitted_page_ids = set()
        lines = []
        epub_type = "titlepage"
        role = "doc-titlepage"

        # Detect specific front matter types and valid ARIA roles
        fname = chapter.epub_filename.lower()
        if "cover" in fname:
            epub_type = "cover"
            role = None  # no valid section role for cover
        elif "copyright" in fname:
            epub_type = "copyright-page"
            role = "doc-colophon"
        elif "half_title" in fname:
            epub_type = "halftitlepage"
            role = "doc-prologue"

        if role:
            lines.append(f'<section epub:type="{epub_type}" role="{role}">')
        else:
            lines.append(f'<section epub:type="{epub_type}">')

        # Page break
        if chapter.start_page >= 0:
            pn = chapter.start_page + 1
            lines.append(
                f'  <span id="page{pn}" role="doc-pagebreak" '
                f'aria-label="{pn}" epub:type="pagebreak"></span>'
            )

        # Heading
        escaped_title = escape(chapter.title)
        lines.append(f'  <h1 aria-label="{escaped_title}">{escaped_title}</h1>')

        for para in chapter.paragraphs:
            lines.append(self._build_paragraph(para, doc))

        # Cover image
        if "cover" in fname and doc.cover_image:
            lines.append(
                f'  <figure epub:type="cover">'
                f'<img src="image/{doc.cover_image}" alt="{escape(doc.title)} cover" '
                f'epub:type="cover-image" role="doc-cover"/></figure>'
            )

        lines.append('</section>')
        return '\n'.join(lines)

    def _build_paragraph(self, para: Paragraph, doc: DocumentStructure) -> str:
        parts = []

        # Page break before paragraph (skip if already emitted)
        if para.page_break_before > 0:
            pn = para.page_break_before
            if not hasattr(self, '_emitted_page_ids'):
                self._emitted_page_ids = set()
            if pn not in self._emitted_page_ids:
                self._emitted_page_ids.add(pn)
                parts.append(
                    f'  <span id="page{pn}" role="doc-pagebreak" '
                    f'aria-label="{pn}" epub:type="pagebreak"></span>'
                )

        # Paragraph class
        css_class = "txt-p" if para.is_first_in_section else "txt"

        # Build paragraph content with inline formatting
        content = self._build_inline_content(para)

        parts.append(f'  <p class="{css_class}">{content}</p>')
        return '\n'.join(parts)

    def _build_inline_content(self, para: Paragraph) -> str:
        """Build paragraph content with inline spans for formatting."""
        if not para.spans:
            return escape(para.text)

        parts = []
        for span in para.spans:
            text = escape(span.text)

            if span.is_superscript and span.footnote_ref:
                # Footnote reference
                fn_id = span.footnote_ref
                ref_id = f"fnref-{fn_id}"
                if hasattr(self, '_emitted_noteref_ids'):
                    self._emitted_noteref_ids.add(ref_id)
                parts.append(
                    f'<a id="{ref_id}" href="#{fn_id}" '
                    f'epub:type="noteref" role="doc-noteref">'
                    f'<sup>{text}</sup></a>'
                )
            elif span.is_bold and span.is_italic:
                parts.append(f'<strong><em>{text}</em></strong>')
            elif span.is_bold:
                parts.append(f'<strong>{text}</strong>')
            elif span.is_italic:
                if span.language:
                    parts.append(
                        f'<i lang="{span.language}" xml:lang="{span.language}">'
                        f'{text}</i>'
                    )
                else:
                    parts.append(f'<em>{text}</em>')
            elif span.language:
                parts.append(
                    f'<span lang="{span.language}" xml:lang="{span.language}">'
                    f'{text}</span>'
                )
            else:
                parts.append(text)

        return ''.join(parts)
