from datetime import datetime
from xml.sax.saxutils import escape

from ..models.document import DocumentStructure


class OPFBuilder:
    """Generate content.opf with full WCAG 2.2 AA accessibility metadata."""

    def build(self, doc: DocumentStructure, book_id: str,
              content_files: list, image_manifest: list) -> str:
        lang = doc.language or "lt-LT"
        title = escape(doc.title)
        author = escape(doc.author)
        now = datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ")
        date = doc.date or datetime.utcnow().strftime("%Y-%m-%d")

        # Ensure author is not empty
        if not author:
            author = "Unknown Author"

        lines = [
            '<?xml version="1.0" encoding="utf-8"?>',
            f'<package version="3.0" unique-identifier="bookid" xml:lang="{lang}"',
            '    xmlns="http://www.idpf.org/2007/opf">',
            f'  <metadata xmlns:dc="http://purl.org/dc/elements/1.1/">',
            f'    <dc:title>{title}</dc:title>',
            f'    <dc:creator>{author}</dc:creator>',
            f'    <dc:language>{lang}</dc:language>',
            f'    <dc:identifier id="bookid">{escape(book_id)}</dc:identifier>',
            f'    <dc:date>{date}</dc:date>',
        ]

        if doc.publisher:
            lines.append(f'    <dc:publisher>{escape(doc.publisher)}</dc:publisher>')
        if doc.description:
            lines.append(f'    <dc:description>{escape(doc.description)}</dc:description>')

        lines.append(f'    <meta property="dcterms:modified">{now}</meta>')

        # WCAG 2.2 AA Accessibility metadata (following certified example)
        lines.extend([
            '    <meta property="dcterms:conformsTo">EPUB Accessibility 1.1 - WCAG 2.2 Level AA</meta>',
            '    <meta property="schema:accessibilityFeature">alternativeText</meta>',
            '    <meta property="schema:accessibilityFeature">ARIA</meta>',
            '    <meta property="schema:accessibilityFeature">displayTransformability</meta>',
            '    <meta property="schema:accessibilityFeature">highContrastDisplay</meta>',
            '    <meta property="schema:accessibilityFeature">pageBreakMarkers</meta>',
            '    <meta property="schema:accessibilityFeature">pageNavigation</meta>',
            '    <meta property="schema:accessibilityFeature">structuralNavigation</meta>',
            '    <meta property="schema:accessibilityFeature">tableOfContents</meta>',
            '    <meta property="schema:accessibilityHazard">none</meta>',
            '    <meta property="schema:accessMode">textual</meta>',
        ])

        if image_manifest:
            lines.append('    <meta property="schema:accessMode">visual</meta>')

        lines.extend([
            '    <meta property="schema:accessModeSufficient">textual</meta>',
            f'    <meta property="schema:accessibilitySummary">This publication conforms to '
            f'EPUB Accessibility 1.1 and WCAG 2.2 Level AA. It provides structural navigation, '
            f'page navigation, alternative text for images, and supports display customization.'
            f'</meta>',
            '  </metadata>',
            '',
            '  <manifest>',
        ])

        # Navigation documents
        lines.append('    <item id="ncx" href="toc.ncx" media-type="application/x-dtbncx+xml"/>')
        lines.append('    <item id="nav" href="toc.xhtml" media-type="application/xhtml+xml" properties="nav"/>')

        # CSS
        lines.append('    <item id="css" href="css/styles.css" media-type="text/css"/>')

        # Content documents
        for i, (filename, title_text, section_type) in enumerate(content_files):
            item_id = f"content_{i:03d}"
            lines.append(
                f'    <item id="{item_id}" href="{filename}" '
                f'media-type="application/xhtml+xml"/>'
            )

        # Images
        for i, (img_name, media_type) in enumerate(image_manifest):
            img_id = f"img_{i:03d}"
            lines.append(
                f'    <item id="{img_id}" href="image/{img_name}" '
                f'media-type="{media_type}"/>'
            )

        lines.extend([
            '  </manifest>',
            '',
            '  <spine toc="ncx">',
        ])

        # Spine itemrefs
        for i, (filename, title_text, section_type) in enumerate(content_files):
            item_id = f"content_{i:03d}"
            lines.append(f'    <itemref idref="{item_id}"/>')

        lines.extend([
            '  </spine>',
            '</package>',
        ])

        return '\n'.join(lines)
