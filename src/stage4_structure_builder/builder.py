import json
import logging
import re
from pathlib import Path

from ..models.page import PDFAnalysis
from ..models.layout import LayoutProfile
from ..models.document import (
    Chapter, Paragraph, Footnote, TocEntry, DocumentStructure, TextSpan
)
from ..llm.client import LLMClient
from ..llm.prompts import TOC_CONFIRM_PROMPT, FRONT_MATTER_PROMPT

logger = logging.getLogger(__name__)


def _sanitize_filename(title: str) -> str:
    """Create safe ASCII filename from chapter title."""
    import unicodedata
    # Normalize unicode, strip accents
    nfkd = unicodedata.normalize('NFKD', title)
    ascii_text = nfkd.encode('ascii', 'ignore').decode('ascii')
    safe = re.sub(r'[^a-zA-Z0-9\s-]', '', ascii_text.lower())
    safe = re.sub(r'[\s]+', '_', safe.strip())
    return safe[:40] if safe else "untitled"


class StructureBuilder:
    """Stage 4: Build document hierarchy from extracted content."""

    def __init__(self, llm_client: LLMClient = None):
        self.llm = llm_client

    def build(self, analysis: PDFAnalysis, layout: LayoutProfile,
              extracted: dict) -> DocumentStructure:
        pages = extracted['pages']
        footnotes = extracted['footnotes']
        images = extracted['images']

        # Build metadata
        meta = analysis.metadata
        title = (meta.get('title', '') or '').strip()
        author = (meta.get('author', '') or '').strip()

        # Detect if title looks like a filename (InDesign artifact)
        if not title or '.indd' in title.lower() or title.startswith('0') or not any(c.isalpha() for c in title):
            title = ''

        # Extract title/author from first page if metadata is missing
        if not title or not author:
            extracted_title, extracted_author = self._extract_metadata_from_title_page(analysis)
            if not title and extracted_title:
                title = extracted_title
            if not author and extracted_author:
                author = extracted_author

        if not title:
            title = Path(analysis.file_path).stem.replace('_', ' ')
        if not author:
            author = "Unknown Author"

        # Detect chapters: Path A (PDF outlines), Path B (Turinys page), Path C (heuristics)
        toc_page_nums = []  # pages containing printed TOC
        toc_entries_raw = None
        if analysis.outlines:
            chapters = self._build_from_outlines(
                analysis, layout, pages, footnotes
            )
            # Still parse TOC for creating a navigable Turinys chapter
            toc_entries_raw, toc_page_nums = self._parse_toc_page(analysis)
        else:
            toc_entries_raw, toc_page_nums = self._parse_toc_page(analysis)
            if toc_entries_raw:
                chapters = self._build_from_toc_entries(
                    toc_entries_raw, analysis, layout, pages, footnotes
                )
            else:
                chapters = self._build_from_heuristics(
                    analysis, layout, pages, footnotes
                )

        # Separate front matter (pass TOC page nums to exclude them)
        front_matter, main_chapters = self._separate_front_matter(
            chapters, layout, pages, toc_page_nums
        )

        # Assign EPUB filenames
        self._assign_filenames(front_matter, main_chapters)

        # Build TOC
        toc = self._build_toc(front_matter, main_chapters)

        # Build page map
        page_map = self._build_page_map(front_matter + main_chapters, analysis)

        # Convert images from (path, page_num, bbox) to (path, alt_text)
        doc_images = [(path, "") for path, page_num, bbox in images]

        # Attach images to chapters
        self._attach_images(main_chapters, images, analysis)

        doc = DocumentStructure(
            title=title,
            author=author,
            language="lt-LT",
            isbn=meta.get('isbn', ''),
            publisher=meta.get('producer', ''),
            front_matter=front_matter,
            chapters=main_chapters,
            toc=toc,
            page_map=page_map,
            total_pages=analysis.page_count,
            images=doc_images,
            toc_entries_raw=toc_entries_raw or [],
        )

        logger.info(f"Structure: {len(front_matter)} front matter, "
                    f"{len(main_chapters)} chapters, {len(toc)} TOC entries")
        return doc

    def _build_from_outlines(self, analysis, layout, pages, footnotes) -> list:
        """Build chapters from PDF outline/bookmarks."""
        outlines = analysis.outlines
        if not outlines:
            return []

        chapters = []
        for i, (level, title, page_num) in enumerate(outlines):
            # Page numbers in outlines are 1-based
            start_page = page_num - 1
            end_page = (outlines[i + 1][2] - 1 if i + 1 < len(outlines)
                       else analysis.page_count)

            # Collect paragraphs from pages in range
            paras = []
            chap_footnotes = []
            for page_data in pages:
                pn = page_data['page_num']
                if start_page <= pn < end_page:
                    # Add page break before first paragraph of each new page
                    page_paras = page_data['paragraphs']
                    if page_paras and pn > start_page:
                        page_paras[0].page_break_before = pn + 1  # 1-based display

                    paras.extend(page_paras)
                    chap_footnotes.extend(page_data.get('footnotes', []))

            # Mark first paragraph
            if paras:
                paras[0].is_first_in_section = True
                # First para of chapter gets page break
                if start_page >= 0:
                    paras[0].page_break_before = start_page + 1

            # Remove chapter title text from first paragraph if it matches
            if paras and title:
                first_text = paras[0].text.strip()
                if first_text.lower().startswith(title.lower()[:20]):
                    # Title is embedded in first paragraph, remove it
                    remaining = first_text[len(title):].strip()
                    if remaining:
                        paras[0].text = remaining
                        paras[0].spans = [TextSpan(text=remaining)]
                    else:
                        paras.pop(0)
                        if paras:
                            paras[0].is_first_in_section = True

            chapters.append(Chapter(
                title=title.strip(),
                level=level,
                paragraphs=paras,
                footnotes=chap_footnotes,
                start_page=start_page,
            ))

        return chapters

    def _build_from_heuristics(self, analysis, layout, pages, footnotes) -> list:
        """Build chapters without PDF outlines using layout analysis."""
        chapter_starts = layout.chapter_start_pages

        if not chapter_starts:
            # Try to detect from heading font sizes
            if layout.heading_font_sizes:
                largest = layout.heading_font_sizes[0]
                for page in analysis.pages:
                    for block in page.text_blocks:
                        if abs(block.font_size - largest) < 0.5:
                            if page.page_num not in chapter_starts:
                                chapter_starts.append(page.page_num)
                            break

        if not chapter_starts:
            # Fallback: treat entire document as one chapter
            all_paras = []
            all_fn = []
            for pd in pages:
                all_paras.extend(pd['paragraphs'])
                all_fn.extend(pd.get('footnotes', []))
            return [Chapter(
                title=Path(analysis.file_path).stem.replace('_', ' '),
                level=2,
                paragraphs=all_paras,
                footnotes=all_fn,
                start_page=0,
            )]

        chapter_starts = sorted(chapter_starts)

        # Build chapters from detected starts
        chapters = []
        for i, start in enumerate(chapter_starts):
            end = chapter_starts[i + 1] if i + 1 < len(chapter_starts) else analysis.page_count

            # Extract chapter title from first heading-sized text on start page
            title = self._extract_title_from_page(analysis.pages[start], layout)

            paras = []
            chap_fn = []
            for pd in pages:
                if start <= pd['page_num'] < end:
                    page_paras = pd['paragraphs']
                    if page_paras and pd['page_num'] > start:
                        page_paras[0].page_break_before = pd['page_num'] + 1
                    paras.extend(page_paras)
                    chap_fn.extend(pd.get('footnotes', []))

            if paras:
                paras[0].is_first_in_section = True
                paras[0].page_break_before = start + 1

            # Remove title from first paragraph if embedded
            if paras and title:
                first_text = paras[0].text.strip()
                if first_text.lower().startswith(title.lower()[:15]):
                    remaining = first_text[len(title):].strip()
                    if remaining:
                        paras[0].text = remaining
                        paras[0].spans = [TextSpan(text=remaining)]
                    else:
                        paras.pop(0)
                        if paras:
                            paras[0].is_first_in_section = True

            chapters.append(Chapter(
                title=title or f"Chapter {i + 1}",
                level=2,
                paragraphs=paras,
                footnotes=chap_fn,
                start_page=start,
            ))

        return chapters

    def _parse_toc_page(self, analysis) -> tuple:
        """Parse printed Turinys (TOC) page for chapter structure.

        Returns (entries, toc_page_numbers) where entries is list of
        (title, page_num_0based) or (None, []) if no TOC found.
        toc_page_numbers is the list of 0-based page numbers that contain the TOC.
        """
        for page_data in analysis.pages[:10]:
            # Check if page contains "Turinys" heading
            has_turinys = False
            for block in page_data.text_blocks:
                if block.text.strip().lower() == 'turinys':
                    has_turinys = True
                    break
            if not has_turinys:
                continue

            # Parse TOC entries from this page and subsequent continuation pages
            entries = []
            toc_pages = [page_data]
            toc_page_nums = [page_data.page_num]
            pn = page_data.page_num

            # Check up to 5 continuation pages (large TOCs can span many pages)
            for offset in range(1, 6):
                next_pn = pn + offset
                if next_pn >= len(analysis.pages):
                    break
                next_page = analysis.pages[next_pn]
                # Continuation page: has leader dots/numbers pattern in blocks
                has_toc_content = False
                for block in next_page.text_blocks:
                    for line in block.text.split('\n'):
                        cleaned = re.sub(r'[.·•\u00b7\u2002]+', ' ', line)
                        cleaned = re.sub(r'\s{2,}', ' ', cleaned).strip()
                        if re.match(r'^.{3,}\s+\d{1,3}\s*$', cleaned):
                            has_toc_content = True
                            break
                    if has_toc_content:
                        break
                if has_toc_content:
                    toc_pages.append(next_page)
                    toc_page_nums.append(next_pn)
                else:
                    break

            for tp in toc_pages:
                for block in tp.text_blocks:
                    # Split block text by newlines (TOC entries can be in one block)
                    raw_lines = block.text.split('\n')
                    for line in raw_lines:
                        line = line.strip()
                        if not line:
                            continue
                        # Remove leader dots, FFFD, control chars, and excessive whitespace
                        line = re.sub(r'[.·•\ufffd\u00b7\x08\u2002]+', ' ', line)
                        line = re.sub(r'\s{2,}', ' ', line).strip()
                        # Match "TITLE NUMBER" pattern
                        m = re.match(r'^(.+?)\s+(\d{1,3})\s*$', line)
                        if m:
                            entry_title = m.group(1).strip()
                            page_num = int(m.group(2)) - 1  # convert to 0-based
                            # Skip the "Turinys" entry itself and page-header artifacts
                            if entry_title.lower() in ('turinys', ''):
                                continue
                            if page_num >= 0 and len(entry_title) > 1:
                                entries.append((entry_title, page_num))

            if len(entries) >= 3:
                logger.info(f"Parsed Turinys page: {len(entries)} entries "
                           f"(pages {toc_page_nums})")
                return entries, toc_page_nums

        return None, []

    def _build_from_toc_entries(self, toc_entries, analysis, layout,
                                pages, footnotes) -> list:
        """Build chapters from parsed Turinys entries.

        Filter to main chapters only (skip subsections) by detecting
        uppercase vs mixed-case titles.
        """
        # Determine which entries are main chapters vs subsections
        # Main chapters tend to be uppercase or significantly larger
        uppercase_entries = [(t, p) for t, p in toc_entries if t.isupper() or t[0].isupper()]

        # If most entries are uppercase, filter to only uppercase
        upper_count = sum(1 for t, _ in toc_entries if t.isupper())
        if upper_count >= 3 and upper_count < len(toc_entries):
            main_entries = [(t, p) for t, p in toc_entries if t.isupper()]
        else:
            main_entries = toc_entries

        # Deduplicate by page number (keep first entry per page)
        seen_pages = set()
        deduped = []
        for title, page_num in main_entries:
            if page_num not in seen_pages:
                seen_pages.add(page_num)
                deduped.append((title, page_num))
        main_entries = deduped

        if not main_entries:
            main_entries = toc_entries

        chapters = []
        for i, (title, start_page) in enumerate(main_entries):
            end_page = (main_entries[i + 1][1] if i + 1 < len(main_entries)
                       else analysis.page_count)

            paras = []
            chap_fn = []
            for pd in pages:
                pn = pd['page_num']
                if start_page <= pn < end_page:
                    page_paras = pd['paragraphs']
                    if page_paras and pn > start_page:
                        page_paras[0].page_break_before = pn + 1
                    paras.extend(page_paras)
                    chap_fn.extend(pd.get('footnotes', []))

            if paras:
                paras[0].is_first_in_section = True
                paras[0].page_break_before = start_page + 1

            # Remove chapter title from first paragraph if embedded
            if paras and title:
                first_text = paras[0].text.strip()
                if first_text.lower().startswith(title.lower()[:15]):
                    remaining = first_text[len(title):].strip()
                    if remaining:
                        paras[0].text = remaining
                        paras[0].spans = [TextSpan(text=remaining)]
                    else:
                        paras.pop(0)
                        if paras:
                            paras[0].is_first_in_section = True

            chapters.append(Chapter(
                title=title.strip(),
                level=2,
                paragraphs=paras,
                footnotes=chap_fn,
                start_page=start_page,
            ))

        logger.info(f"Built {len(chapters)} chapters from Turinys entries")
        return chapters

    def _extract_title_from_page(self, page_data, layout) -> str:
        """Extract the chapter title from a page.

        Searches through heading sizes from largest to smallest until
        matching text is found on the page.
        """
        if not layout.heading_font_sizes:
            return ""

        for heading_size in layout.heading_font_sizes:
            title_blocks = []
            for block in page_data.text_blocks:
                if abs(block.font_size - heading_size) < 1.0:
                    text = block.text.strip()
                    if text and len(text) > 1:
                        title_blocks.append(block)

            if title_blocks:
                title_blocks.sort(key=lambda b: (b.bbox[1], b.bbox[0]))
                title = ' '.join(b.text.strip() for b in title_blocks)
                title = re.sub(r'\s+', ' ', title).strip()
                return title

        return ""

    def _separate_front_matter(self, chapters, layout, pages,
                               toc_page_nums=None) -> tuple:
        """Separate front matter chapters from main content."""
        if not chapters:
            return [], []

        toc_pages_set = set(toc_page_nums or [])

        front = []
        main = []

        for ch in chapters:
            page_type = layout.page_types.get(ch.start_page, "body")
            if page_type in ("front_matter", "title_page") and not main:
                ch.level = 1  # front matter
                front.append(ch)
            else:
                main.append(ch)

        # If no front matter detected but first chapter starts late, create it
        if not front and chapters and chapters[0].start_page > 2:
            # Pages before first chapter are front matter
            # Filter out TOC pages using both known TOC page nums and heuristic
            fm_paras = []
            for pd in pages:
                if pd['page_num'] < chapters[0].start_page:
                    if pd['page_num'] in toc_pages_set:
                        continue  # skip known TOC pages
                    if self._is_toc_page(pd):
                        continue  # skip heuristically detected TOC pages
                    fm_paras.extend(pd['paragraphs'])

            if fm_paras:
                front.append(Chapter(
                    title="Title Page",
                    level=1,
                    paragraphs=fm_paras,
                    start_page=0,
                ))

        return front, main

    def _is_toc_page(self, page_data) -> bool:
        """Detect if a page contains a printed table of contents."""
        paras = page_data.get('paragraphs', [])
        if not paras:
            return False

        # Check for "Turinys" heading
        for para in paras:
            if para.text.strip().lower() in ('turinys', 'turinys.'):
                return True

        # Check for TOC-like pattern: many lines ending with numbers
        number_lines = 0
        total_lines = 0
        for para in paras:
            text = para.text.strip()
            if not text:
                continue
            total_lines += 1
            # TOC entry pattern: text followed by page number
            if re.match(r'.+\s+\d{1,3}\s*$', text):
                number_lines += 1

        # If majority of lines have trailing numbers, it's a TOC page
        return total_lines >= 5 and number_lines >= total_lines * 0.5

    def _assign_filenames(self, front_matter, chapters):
        """Assign EPUB filenames to chapters."""
        # Front matter
        fm_names = ["cover", "half_title", "title_page", "copyright"]
        for i, ch in enumerate(front_matter):
            name = fm_names[i] if i < len(fm_names) else f"front_{i}"
            ch.epub_filename = f"{name}.xhtml"

        # Main chapters
        for i, ch in enumerate(chapters):
            safe_name = _sanitize_filename(ch.title)
            ch.epub_filename = f"chapter_{i + 1:02d}_{safe_name}.xhtml"

        # Handle sub-chapters
        for ch in chapters:
            for j, sub in enumerate(ch.sub_chapters):
                safe_name = _sanitize_filename(sub.title)
                sub.epub_filename = f"section_{j + 1:02d}_{safe_name}.xhtml"

    def _build_toc(self, front_matter, chapters) -> list:
        """Build table of contents entries."""
        toc = []

        for ch in chapters:
            entry = TocEntry(
                title=ch.title,
                level=ch.level,
                target_file=ch.epub_filename,
            )
            # Add sub-chapters
            for sub in ch.sub_chapters:
                entry.children.append(TocEntry(
                    title=sub.title,
                    level=sub.level,
                    target_file=sub.epub_filename,
                ))
            toc.append(entry)

        return toc

    def _build_page_map(self, chapters, analysis) -> dict:
        """Map page numbers to (filename, anchor_id)."""
        page_map = {}

        for ch in chapters:
            # Map start page
            if ch.start_page >= 0:
                page_map[ch.start_page + 1] = (ch.epub_filename, f"page{ch.start_page + 1}")

            # Map pages from paragraph page breaks
            for para in ch.paragraphs:
                if para.page_break_before > 0:
                    pn = para.page_break_before
                    page_map[pn] = (ch.epub_filename, f"page{pn}")

        return page_map

    def _extract_metadata_from_title_page(self, analysis) -> tuple:
        """Extract title and author from the first page using font size heuristics."""
        if not analysis.pages:
            return "", ""

        # Use only page 0 (the title page)
        page_data = analysis.pages[0]
        blocks = page_data.text_blocks
        if not blocks:
            return "", ""

        # Collect all text blocks with size and position
        sized_blocks = [(b.font_size, b.text.strip(), b.bbox[1]) for b in blocks
                        if b.text.strip() and len(b.text.strip()) > 1]
        if not sized_blocks:
            return "", ""

        sized_blocks.sort(key=lambda x: -x[0])
        max_size = sized_blocks[0][0]

        # Step 1: Find author — name-like block (2-3 capitalized words, mixed case)
        # Sort by vertical position to check from top of page down
        by_position = sorted(sized_blocks, key=lambda x: x[2])
        author = ""
        for size, text, y in by_position:
            if size < 10:
                continue
            words = text.split()
            if 2 <= len(words) <= 3:
                is_name = all(
                    w[0].isupper() and (len(w) == 1 or not w.isupper())
                    for w in words if w.isalpha()
                )
                if is_name:
                    author = text
                    break

        # Step 2: Title — largest font blocks, excluding the author block
        title_parts = []
        for size, text, y in sized_blocks:
            if text == author:
                continue
            if size >= max_size * 0.8 and size > 14:
                title_parts.append((y, text))

        # Sort by vertical position and join, normalizing whitespace
        title_parts.sort(key=lambda x: x[0])
        title = ' '.join(t for _, t in title_parts) if title_parts else ""
        title = re.sub(r'\s+', ' ', title).strip()

        return title, author

    def _attach_images(self, chapters, images, analysis):
        """Attach extracted images to their chapters by page range."""
        if not images or not chapters:
            return

        for img_path, page_num, bbox in images:
            # Find which chapter contains this page
            best_ch = None
            for i, ch in enumerate(chapters):
                end_page = (chapters[i + 1].start_page if i + 1 < len(chapters)
                           else analysis.page_count)
                if ch.start_page <= page_num < end_page:
                    best_ch = ch
                    break
            if best_ch is None and chapters:
                best_ch = chapters[-1]
            if best_ch:
                best_ch.images.append((img_path, "", page_num, bbox))
