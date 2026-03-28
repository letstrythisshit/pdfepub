import logging
import re
import unicodedata
from collections import defaultdict
from pathlib import Path

import fitz

from ..models.page import PDFAnalysis, TextBlock, PageData
from ..models.layout import LayoutProfile
from ..models.document import TextSpan, Paragraph, Footnote

logger = logging.getLogger(__name__)


class ContentExtractor:
    """Stage 3: Extract clean content using the layout model to filter decoration."""

    def extract(self, analysis: PDFAnalysis, layout: LayoutProfile,
                output_dir: str) -> dict:
        """
        Returns dict with:
            'pages': list of per-page content dicts
            'footnotes': dict of footnote_id -> Footnote
            'images': list of (image_path, page_num, bbox)
        """
        output_dir = Path(output_dir)
        img_dir = output_dir / "images"
        img_dir.mkdir(parents=True, exist_ok=True)

        # Recalibrate bold/italic: if the most common font is "bold",
        # then bold is actually normal body text
        body_font = self._detect_body_font(analysis)

        all_pages = []
        all_footnotes = {}
        all_images = []
        global_fn_counter = 0

        for page_data in analysis.pages:
            # Filter out repeating elements
            content_blocks = self._filter_decoration(
                page_data.text_blocks, layout
            )

            # Separate body from footnotes
            body_blocks, fn_blocks = self._separate_footnotes(
                content_blocks, layout, page_data
            )

            # Detect superscript references in body
            body_blocks, refs = self._detect_superscripts(
                body_blocks, layout.body_font_size
            )

            # Build paragraphs from body blocks
            paragraphs = self._blocks_to_paragraphs(
                body_blocks, layout, page_data.page_num, body_font
            )

            # Parse footnotes with globally unique counter
            page_footnotes = self._parse_footnotes(
                fn_blocks, page_data.page_num, global_fn_counter
            )
            global_fn_counter += len(page_footnotes)

            for fn in page_footnotes:
                all_footnotes[fn.footnote_id] = fn

            # Link superscript refs to footnotes
            self._link_refs_to_footnotes(paragraphs, refs, page_footnotes)

            all_pages.append({
                'page_num': page_data.page_num,
                'paragraphs': paragraphs,
                'footnotes': page_footnotes,
                'body_blocks': body_blocks,
            })

        # Extract images from PDF
        all_images = self._extract_images(analysis, img_dir)

        return {
            'pages': all_pages,
            'footnotes': all_footnotes,
            'images': all_images,
        }

    def _detect_body_font(self, analysis: PDFAnalysis) -> dict:
        """Detect the dominant body font to recalibrate bold/italic."""
        from collections import Counter
        font_chars = Counter()
        for page in analysis.pages:
            for block in page.text_blocks:
                font_chars[block.font_name] += len(block.text)
        if not font_chars:
            return {'name': '', 'is_bold': False, 'is_italic': False}
        body_font_name = font_chars.most_common(1)[0][0]
        return {
            'name': body_font_name,
            'is_bold': 'bold' in body_font_name.lower(),
            'is_italic': 'italic' in body_font_name.lower() or 'oblique' in body_font_name.lower(),
        }

    def _filter_decoration(self, blocks: list, layout: LayoutProfile) -> list:
        """Remove blocks that overlap with repeating elements."""
        if not layout.repeating_elements:
            return list(blocks)

        filtered = []
        for block in blocks:
            is_decoration = False
            for rep in layout.repeating_elements:
                if self._bbox_overlap(block.norm_bbox, rep.norm_bbox) > 0.3:
                    is_decoration = True
                    break
            if not is_decoration:
                filtered.append(block)

        return filtered

    def _bbox_overlap(self, a, b) -> float:
        """Calculate IoU-like overlap between two normalized bboxes."""
        x1 = max(a[0], b[0])
        y1 = max(a[1], b[1])
        x2 = min(a[2], b[2])
        y2 = min(a[3], b[3])

        if x2 <= x1 or y2 <= y1:
            return 0.0

        intersection = (x2 - x1) * (y2 - y1)
        area_a = (a[2] - a[0]) * (a[3] - a[1])
        if area_a == 0:
            return 0.0
        return intersection / area_a

    def _separate_footnotes(self, blocks: list, layout: LayoutProfile,
                            page_data: PageData) -> tuple:
        """Split blocks into body and footnote zones."""
        if layout.footnote_zone_y >= 1.0:
            return blocks, []

        body = []
        footnotes = []
        for block in blocks:
            if block.norm_bbox[1] >= layout.footnote_zone_y:
                # Small-font blocks in footnote zone are footnotes
                if block.font_size <= layout.body_font_size * 0.9:
                    footnotes.append(block)
                # Same-size blocks starting with a number are also footnotes
                elif re.match(r'^\d{1,3}\s', block.text.strip()) or \
                     re.match(r'^\d{1,3}$', block.text.strip()):
                    footnotes.append(block)
                else:
                    body.append(block)
            else:
                body.append(block)

        return body, footnotes

    def _detect_superscripts(self, blocks: list, body_size: float) -> tuple:
        """Detect superscript reference markers in body text."""
        refs = []
        result_blocks = []

        for block in blocks:
            # Superscript: smaller font + numeric + within body context
            if (block.font_size < body_size * 0.75 and
                    block.text.strip().isdigit() and
                    len(block.text.strip()) <= 3):
                refs.append({
                    'text': block.text.strip(),
                    'bbox': block.bbox,
                    'page_y': block.norm_bbox[1],
                })
            else:
                result_blocks.append(block)

        return result_blocks, refs

    def _blocks_to_paragraphs(self, blocks: list, layout: LayoutProfile,
                              page_num: int, body_font: dict = None) -> list:
        """Convert text blocks to Paragraph objects.

        Each TextBlock from the analyzer is already a paragraph-level unit
        (based on PyMuPDF's block grouping). We just need to clean and wrap.
        """
        if not blocks:
            return []
        if body_font is None:
            body_font = {'name': '', 'is_bold': False, 'is_italic': False}

        sorted_blocks = sorted(blocks, key=lambda b: (b.bbox[1], b.bbox[0]))

        paragraphs = []
        for block in sorted_blocks:
            text = self._clean_text(block.text)
            if not text:
                continue

            text = text.replace('\n', ' ')
            text = re.sub(r' +', ' ', text).strip()
            if not text:
                continue

            # Recalibrate bold/italic relative to body font
            is_bold = block.is_bold
            is_italic = block.is_italic
            if body_font['is_bold']:
                # Body font is "bold" by name, so bold flag = normal
                is_bold = False
                # Only truly bold if it's a DIFFERENT bold font or extra-bold
                if block.font_name != body_font['name']:
                    if 'bold' in block.font_name.lower():
                        is_bold = block.font_size > layout.body_font_size * 1.1
            if body_font['is_italic']:
                is_italic = False

            span = TextSpan(
                text=text,
                is_bold=is_bold,
                is_italic=is_italic,
            )

            # Store normalized Y position for footnote reference proximity matching
            source_y = block.norm_bbox[1] if hasattr(block, 'norm_bbox') else 0.0

            paragraphs.append(Paragraph(
                text=text,
                spans=[span],
                page_break_before=0,
                _source_y=source_y,
            ))

        return paragraphs

    def _parse_footnotes(self, fn_blocks: list, page_num: int,
                         counter_start: int) -> list:
        """Parse footnote blocks into Footnote objects."""
        if not fn_blocks:
            return []

        sorted_blocks = sorted(fn_blocks, key=lambda b: (b.bbox[1], b.bbox[0]))
        footnotes = []
        current_marker = None
        current_text_parts = []
        local_idx = 0

        for block in sorted_blocks:
            text = self._clean_text(block.text)
            if not text:
                continue

            match = re.match(r'^(\d{1,3})\s*(.*)', text)
            if match:
                if current_marker is not None:
                    fn_text = ' '.join(current_text_parts)
                    fn_id = f"fn-{counter_start + local_idx}"
                    footnotes.append(Footnote(
                        footnote_id=fn_id,
                        marker=str(current_marker),
                        text=fn_text,
                    ))
                    local_idx += 1
                current_marker = int(match.group(1))
                current_text_parts = [match.group(2)] if match.group(2) else []
            elif current_marker is not None:
                current_text_parts.append(text)

        if current_marker is not None:
            fn_text = ' '.join(current_text_parts)
            fn_id = f"fn-{counter_start + local_idx}"
            footnotes.append(Footnote(
                footnote_id=fn_id,
                marker=str(current_marker),
                text=fn_text,
            ))
            local_idx += 1

        if not footnotes and sorted_blocks:
            all_text = ' '.join(self._clean_text(b.text) for b in sorted_blocks)
            if all_text.strip():
                footnotes.append(Footnote(
                    footnote_id=f"fn-{counter_start + local_idx}",
                    marker="*",
                    text=all_text.strip(),
                ))

        return footnotes

    def _link_refs_to_footnotes(self, paragraphs, refs, footnotes):
        """Link superscript references in body to footnotes."""
        fn_by_marker = {fn.marker: fn for fn in footnotes}

        for ref in refs:
            marker = ref['text']
            if marker in fn_by_marker:
                fn = fn_by_marker[marker]
                ref_id = f"fnref-{fn.footnote_id}"
                fn.ref_id = ref_id

                # Find the nearest paragraph by Y position
                ref_y = ref['page_y']
                closest_para = None
                min_dist = float('inf')
                for para in paragraphs:
                    if para.spans:
                        dist = abs(ref_y - para._source_y)
                        if dist < min_dist:
                            min_dist = dist
                            closest_para = para

                if closest_para:
                    closest_para.spans.append(TextSpan(
                        text=marker,
                        is_superscript=True,
                        footnote_ref=fn.footnote_id,
                    ))

    def _extract_images(self, analysis: PDFAnalysis, img_dir: Path) -> list:
        """Extract all images from the PDF."""
        images = []
        doc = fitz.open(analysis.file_path)

        seen_xrefs = set()
        for page_data in analysis.pages:
            for img_ref in page_data.images:
                if img_ref.xref in seen_xrefs:
                    continue
                seen_xrefs.add(img_ref.xref)

                try:
                    base_image = doc.extract_image(img_ref.xref)
                    if base_image:
                        ext = base_image.get("ext", "png")
                        img_bytes = base_image.get("image", b"")
                        if len(img_bytes) < 500:
                            continue

                        # Normalize problematic formats to PNG
                        if ext in ("jpx", "jp2", "jbig2", "bmp"):
                            from PIL import Image
                            import io
                            try:
                                img = Image.open(io.BytesIO(img_bytes))
                                buf = io.BytesIO()
                                img.save(buf, format="PNG")
                                img_bytes = buf.getvalue()
                                ext = "png"
                            except Exception:
                                continue  # skip unreadable images

                        img_path = img_dir / f"image_{img_ref.xref}.{ext}"
                        img_path.write_bytes(img_bytes)
                        images.append((
                            str(img_path),
                            page_data.page_num,
                            img_ref.bbox,
                        ))
                except Exception as e:
                    logger.warning(f"Failed to extract image xref={img_ref.xref}: {e}")

        doc.close()
        return images

    def _clean_text(self, text: str) -> str:
        """Clean text: remove control chars, normalize whitespace."""
        # Remove control characters except newline/tab
        cleaned = []
        for ch in text:
            cat = unicodedata.category(ch)
            if cat.startswith('C') and ch not in '\n\t ':
                continue
            # Strip PUA chars (decorative glyphs like leader dots)
            if '\ue000' <= ch <= '\uf8ff':
                continue
            # Strip replacement character (U+FFFD) — often from unresolved PUA/encoding
            if ch == '\ufffd':
                continue
            cleaned.append(ch)

        result = ''.join(cleaned)
        # Normalize whitespace
        result = re.sub(r'[ \t]+', ' ', result)
        result = result.strip()
        return result
