import logging
import unicodedata
from collections import Counter
from pathlib import Path

import fitz  # PyMuPDF

from ..models.page import TextBlock, ImageRef, PageData, PDFAnalysis

logger = logging.getLogger(__name__)


class PDFAnalyzer:
    """Stage 1: Parse PDF structure, extract text blocks, render page images."""

    def __init__(self, render_dpi: int = 150):
        self.render_dpi = render_dpi

    def analyze(self, pdf_path: str, output_dir: str) -> PDFAnalysis:
        pdf_path = str(pdf_path)
        output_dir = Path(output_dir)
        images_dir = output_dir / "page_images"
        images_dir.mkdir(parents=True, exist_ok=True)

        doc = fitz.open(pdf_path)
        logger.info(f"Analyzing PDF: {pdf_path}, {len(doc)} pages")

        # Basic metadata
        meta = doc.metadata or {}
        page0 = doc[0]
        page_width = page0.rect.width
        page_height = page0.rect.height

        # Extract TOC/outlines
        outlines = doc.get_toc()
        logger.info(f"TOC entries: {len(outlines)}")

        # Process each page
        pages = []
        all_fonts = set()
        total_chars = 0
        problem_chars = 0

        for page_num in range(len(doc)):
            page = doc[page_num]
            page_data = self._process_page(
                page, page_num, page_width, page_height, images_dir
            )
            pages.append(page_data)

            # Track fonts
            for block in page_data.text_blocks:
                all_fonts.add(block.font_name)

            # Track text quality
            for block in page_data.text_blocks:
                for ch in block.text:
                    total_chars += 1
                    cat = unicodedata.category(ch)
                    if cat.startswith('C') and ch not in '\n\t\r':
                        problem_chars += 1
                    elif '\ue000' <= ch <= '\uf8ff':  # PUA
                        problem_chars += 1

        # Determine if OCR is needed globally
        quality = 1.0 - (problem_chars / max(total_chars, 1))
        needs_ocr = quality < 0.9
        logger.info(f"Text quality: {quality:.3f}, needs OCR: {needs_ocr}, "
                    f"problem chars: {problem_chars}/{total_chars}")

        doc.close()

        return PDFAnalysis(
            file_path=pdf_path,
            page_count=len(pages),
            page_width=page_width,
            page_height=page_height,
            metadata=meta,
            outlines=outlines,
            pages=pages,
            needs_ocr=needs_ocr,
            fonts_used=all_fonts,
        )

    def _process_page(self, page, page_num: int, page_width: float,
                      page_height: float, images_dir: Path) -> PageData:
        # Render page image
        pix = page.get_pixmap(dpi=self.render_dpi)
        img_path = images_dir / f"page_{page_num:04d}.png"
        pix.save(str(img_path))

        # Extract text blocks with full detail
        text_blocks = self._extract_text_blocks(page, page_width, page_height)

        # Extract images
        image_refs = self._extract_images(page, page_num)

        # Calculate text quality for this page
        total_c = sum(len(b.text) for b in text_blocks)
        bad_c = 0
        for b in text_blocks:
            for ch in b.text:
                cat = unicodedata.category(ch)
                if (cat.startswith('C') and ch not in '\n\t\r') or \
                   ('\ue000' <= ch <= '\uf8ff'):
                    bad_c += 1

        quality = 1.0 - (bad_c / max(total_c, 1))

        return PageData(
            page_num=page_num,
            width=page_width,
            height=page_height,
            image_path=str(img_path),
            text_blocks=text_blocks,
            images=image_refs,
            text_quality=quality,
        )

    def _extract_text_blocks(self, page, page_width: float,
                             page_height: float) -> list:
        """Extract text as paragraph-level TextBlock objects with font metadata.

        Uses PyMuPDF's block structure for paragraph grouping, but preserves
        span-level font info by picking the dominant font per block.
        """
        blocks = []
        page_dict = page.get_text("dict", flags=fitz.TEXT_PRESERVE_WHITESPACE)

        for block in page_dict.get("blocks", []):
            if block.get("type") != 0:
                continue

            block_bbox = block.get("bbox", (0, 0, 0, 0))

            # Collect all spans in this block
            spans_in_block = []
            for line in block.get("lines", []):
                for span in line.get("spans", []):
                    text = span.get("text", "")
                    if text.strip():
                        spans_in_block.append(span)

            if not spans_in_block:
                continue

            # Build the full block text by joining lines
            lines_text = []
            for line in block.get("lines", []):
                line_parts = []
                for span in line.get("spans", []):
                    t = span.get("text", "")
                    if t:
                        line_parts.append(t)
                line_text = ''.join(line_parts).rstrip()
                if line_text:
                    lines_text.append(line_text)
            full_text = '\n'.join(lines_text)

            if not full_text.strip():
                continue

            # Get dominant font info (most chars)
            font_chars = {}
            for span in spans_in_block:
                key = (span.get("font", ""), round(span.get("size", 0), 1),
                       span.get("flags", 0))
                font_chars[key] = font_chars.get(key, 0) + len(span.get("text", ""))

            dom_font, dom_size, dom_flags = max(font_chars, key=font_chars.get)

            # Note: bold/italic flags will be recalibrated in content extraction
            # relative to the book's body font (some PDFs use "Bold" as body font)
            is_bold = bool(dom_flags & (1 << 4))
            is_italic = bool(dom_flags & (1 << 1))

            color_int = spans_in_block[0].get("color", 0)
            r = (color_int >> 16) & 0xFF
            g = (color_int >> 8) & 0xFF
            b_val = color_int & 0xFF

            origin = spans_in_block[0].get("origin", (0, 0))

            tb = TextBlock(
                bbox=tuple(block_bbox),
                text=full_text.strip(),
                font_name=dom_font,
                font_size=dom_size,
                is_bold=is_bold,
                is_italic=is_italic,
                color=(r, g, b_val),
                baseline_y=origin[1] if origin else block_bbox[3],
            )
            tb.norm_bbox = (
                block_bbox[0] / page_width,
                block_bbox[1] / page_height,
                block_bbox[2] / page_width,
                block_bbox[3] / page_height,
            )
            blocks.append(tb)

            # Also add individual small spans that might be superscripts
            # (different font size within the same block)
            for span in spans_in_block:
                span_size = round(span.get("size", 0), 1)
                if span_size < dom_size * 0.75 and span.get("text", "").strip():
                    span_bbox = span.get("bbox", (0, 0, 0, 0))
                    stb = TextBlock(
                        bbox=tuple(span_bbox),
                        text=span.get("text", "").strip(),
                        font_name=span.get("font", ""),
                        font_size=span_size,
                        is_bold=False,
                        is_italic=False,
                        baseline_y=span.get("origin", (0, 0))[1],
                    )
                    stb.norm_bbox = (
                        span_bbox[0] / page_width,
                        span_bbox[1] / page_height,
                        span_bbox[2] / page_width,
                        span_bbox[3] / page_height,
                    )
                    blocks.append(stb)

        return blocks

    def _extract_images(self, page, page_num: int) -> list:
        """Extract image references from page."""
        refs = []
        for img_info in page.get_images(full=True):
            xref = img_info[0]
            refs.append(ImageRef(
                xref=xref,
                bbox=(0, 0, 0, 0),  # will be resolved during content extraction
                width=img_info[2] if len(img_info) > 2 else 0,
                height=img_info[3] if len(img_info) > 3 else 0,
                colorspace=img_info[5] if len(img_info) > 5 else "",
            ))
        return refs
