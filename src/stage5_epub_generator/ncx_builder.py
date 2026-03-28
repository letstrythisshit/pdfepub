from xml.sax.saxutils import escape

from ..models.document import DocumentStructure, TocEntry


class NCXBuilder:
    """Generate toc.ncx for EPUB2 backward compatibility."""

    def build(self, doc: DocumentStructure, book_id: str) -> str:
        lines = [
            '<?xml version="1.0" encoding="utf-8"?>',
            '<ncx xmlns="http://www.daisy.org/z3986/2005/ncx/" version="2005-1">',
            '  <head>',
            f'    <meta name="dtb:uid" content="{escape(book_id)}"/>',
            f'    <meta name="dtb:depth" content="{self._get_depth(doc.toc)}"/>',
            f'    <meta name="dtb:totalPageCount" content="{doc.total_pages}"/>',
            f'    <meta name="dtb:maxPageNumber" content="{doc.total_pages}"/>',
            '  </head>',
            f'  <docTitle><text>{escape(doc.title)}</text></docTitle>',
            f'  <docAuthor><text>{escape(doc.author)}</text></docAuthor>',
            '  <navMap>',
        ]

        # Build navPoints
        counter = [1]  # mutable counter for navPoint IDs
        for entry in doc.toc:
            lines.extend(self._build_navpoint(entry, counter))

        lines.append('  </navMap>')

        # Page list
        if doc.page_map:
            lines.append('  <pageList>')
            for page_num in sorted(doc.page_map.keys()):
                filename, anchor = doc.page_map[page_num]
                lines.append(
                    f'    <pageTarget id="pt_{page_num}" type="normal" value="{page_num}">'
                    f'<navLabel><text>{page_num}</text></navLabel>'
                    f'<content src="{filename}#{anchor}"/>'
                    f'</pageTarget>'
                )
            lines.append('  </pageList>')

        lines.append('</ncx>')
        return '\n'.join(lines)

    def _build_navpoint(self, entry: TocEntry, counter: list,
                        indent: str = "    ") -> list:
        lines = []
        np_id = f"navPoint{counter[0]}"
        counter[0] += 1

        target = entry.target_file
        if entry.target_anchor:
            target += entry.target_anchor

        lines.append(f'{indent}<navPoint id="{np_id}">')
        lines.append(f'{indent}  <navLabel><text>{escape(entry.title)}</text></navLabel>')
        lines.append(f'{indent}  <content src="{target}"/>')

        for child in entry.children:
            lines.extend(self._build_navpoint(child, counter, indent + "  "))

        lines.append(f'{indent}</navPoint>')
        return lines

    def _get_depth(self, toc: list) -> int:
        if not toc:
            return 1
        max_depth = 1
        for entry in toc:
            if entry.children:
                child_depth = 1 + self._get_depth(entry.children)
                max_depth = max(max_depth, child_depth)
        return max_depth
