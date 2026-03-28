from xml.sax.saxutils import escape

from ..models.document import DocumentStructure, TocEntry


class NavBuilder:
    """Generate toc.xhtml with toc, page-list, and landmarks navigation."""

    def build(self, doc: DocumentStructure) -> str:
        lang = doc.language or "lt-LT"

        lines = [
            '<?xml version="1.0" encoding="utf-8"?>',
            '<!DOCTYPE html>',
            f'<html xmlns="http://www.w3.org/1999/xhtml" lang="{lang}" xml:lang="{lang}"',
            '      xmlns:epub="http://www.idpf.org/2007/ops">',
            '<head>',
            '  <meta charset="utf-8"/>',
            f'  <title>Turinys</title>',
            '  <link rel="stylesheet" type="text/css" href="css/styles.css"/>',
            '</head>',
            '<body>',
            '',
        ]

        # TOC nav
        lines.append('<nav epub:type="toc" id="toc" role="doc-toc">')
        lines.append('  <h1>Turinys</h1>')
        lines.append('  <ol>')
        for entry in doc.toc:
            lines.extend(self._build_toc_entry(entry, "    "))
        lines.append('  </ol>')
        lines.append('</nav>')
        lines.append('')

        # Page list nav
        if doc.page_map:
            lines.append('<nav epub:type="page-list" id="page-list" role="doc-pagelist" hidden="">')
            lines.append('  <h2>Page List</h2>')
            lines.append('  <ol>')
            for page_num in sorted(doc.page_map.keys()):
                filename, anchor = doc.page_map[page_num]
                lines.append(
                    f'    <li><a href="{filename}#{anchor}">{page_num}</a></li>'
                )
            lines.append('  </ol>')
            lines.append('</nav>')
            lines.append('')

        # Landmarks nav
        lines.append('<nav epub:type="landmarks" hidden="">')
        lines.append('  <h2>Landmarks</h2>')
        lines.append('  <ol>')

        # Add landmark entries based on what we have
        if doc.front_matter:
            for ch in doc.front_matter:
                fname = ch.epub_filename.lower()
                if "cover" in fname:
                    lines.append(
                        f'    <li><a href="{ch.epub_filename}" epub:type="cover">Cover</a></li>'
                    )
                elif "title" in fname:
                    lines.append(
                        f'    <li><a href="{ch.epub_filename}" epub:type="titlepage">Title Page</a></li>'
                    )
                elif "copyright" in fname:
                    lines.append(
                        f'    <li><a href="{ch.epub_filename}" epub:type="copyright-page">Copyright</a></li>'
                    )

        if doc.chapters:
            lines.append(
                f'    <li><a href="{doc.chapters[0].epub_filename}" '
                f'epub:type="bodymatter">Start of Content</a></li>'
            )

        lines.append('  </ol>')
        lines.append('</nav>')
        lines.append('')

        lines.extend([
            '</body>',
            '</html>',
        ])

        return '\n'.join(lines)

    def _build_toc_entry(self, entry: TocEntry, indent: str) -> list:
        lines = []
        target = entry.target_file
        if entry.target_anchor:
            target += entry.target_anchor

        if entry.children:
            lines.append(f'{indent}<li>')
            lines.append(f'{indent}  <a href="{target}">{escape(entry.title)}</a>')
            lines.append(f'{indent}  <ol>')
            for child in entry.children:
                lines.extend(self._build_toc_entry(child, indent + "    "))
            lines.append(f'{indent}  </ol>')
            lines.append(f'{indent}</li>')
        else:
            lines.append(
                f'{indent}<li><a href="{target}">{escape(entry.title)}</a></li>'
            )

        return lines
