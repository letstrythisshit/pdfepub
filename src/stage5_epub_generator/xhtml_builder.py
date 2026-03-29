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

    def build_turinys(self, doc: DocumentStructure) -> str:
        """Build a navigable Turinys (table of contents) page with links."""
        lang = doc.language or "lt-LT"
        parts = [self.XHTML_HEADER.format(lang=lang, title="Turinys")]

        lines = []
        lines.append('<section epub:type="toc" role="doc-toc">')
        lines.append('  <h1 aria-label="Turinys">Turinys</h1>')

        # Build linked TOC entries from doc.toc
        for entry in doc.toc:
            target = entry.target_file
            if entry.target_anchor:
                target += entry.target_anchor
            title = escape(entry.title)
            lines.append(f'  <p class="toc-entry"><a href="{target}">{title}</a></p>')
            for child in entry.children:
                ctarget = child.target_file
                if child.target_anchor:
                    ctarget += child.target_anchor
                ctitle = escape(child.title)
                lines.append(
                    f'  <p class="toc-entry toc-sub"><a href="{ctarget}">{ctitle}</a></p>'
                )

        lines.append('</section>')
        parts.append('\n'.join(lines))
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

        # Build image map: page_num -> list of (img_index, alt_text)
        chapter_images = {}
        if chapter.images:
            for i, img_tuple in enumerate(chapter.images):
                # Support both (path, alt) and (path, alt, page, bbox) formats
                if len(img_tuple) >= 3:
                    img_path, alt, page_num = img_tuple[0], img_tuple[1], img_tuple[2]
                else:
                    img_path, alt = img_tuple[0], img_tuple[1]
                    page_num = chapter.start_page
                chapter_images.setdefault(page_num, []).append((i, alt or "Illustration"))

        # Paragraphs with image insertion
        current_page = chapter.start_page
        inserted_image_pages = set()
        for para in chapter.paragraphs:
            # Track current page from page breaks
            if para.page_break_before > 0:
                current_page = para.page_break_before - 1  # convert back to 0-based

            # Insert images that belong to this page (before next paragraph)
            if current_page in chapter_images and current_page not in inserted_image_pages:
                inserted_image_pages.add(current_page)
                for img_idx, alt_text in chapter_images[current_page]:
                    lines.append(self._build_image_figure(
                        chapter, img_idx, alt_text, doc
                    ))

            lines.append(self._build_paragraph(para, doc))

        # Insert any remaining images that weren't placed yet
        for page_num, img_list in chapter_images.items():
            if page_num not in inserted_image_pages:
                for img_idx, alt_text in img_list:
                    lines.append(self._build_image_figure(
                        chapter, img_idx, alt_text, doc
                    ))

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

    def _build_image_figure(self, chapter: Chapter, img_idx: int,
                              alt_text: str, doc: DocumentStructure) -> str:
        """Build a figure element for an image in the chapter."""
        if img_idx >= len(chapter.images):
            return ''
        img_tuple = chapter.images[img_idx]
        img_path = img_tuple[0]

        # Find the matching image in doc.images to get the EPUB path
        from pathlib import Path
        img_filename = Path(img_path).name
        # The generator copies images to image/ directory with numbered names
        # We need to find the index in doc.images to match
        epub_img_name = None
        for di, (di_path, di_alt) in enumerate(doc.images):
            if di_path == img_path:
                ext = Path(di_path).suffix
                epub_img_name = f"image_{di + 1}{ext}"
                break

        if not epub_img_name:
            # Fallback: use the original filename
            epub_img_name = img_filename

        escaped_alt = escape(alt_text)
        return (
            f'  <figure>\n'
            f'    <img src="image/{epub_img_name}" alt="{escaped_alt}"/>\n'
            f'  </figure>'
        )

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
