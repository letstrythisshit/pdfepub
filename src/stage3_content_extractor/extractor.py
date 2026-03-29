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

        # Pre-detect diagram regions so we can exclude their text from body
        doc = fitz.open(analysis.file_path)
        diagram_regions = self._detect_diagram_regions(doc, analysis)
        doc.close()
        # Map page_num -> list of diagram bboxes
        diagram_map = {}
        for pn, bbox in diagram_regions:
            diagram_map.setdefault(pn, []).append(bbox)

        all_pages = []
        all_footnotes = {}
        all_images = []
        global_fn_counter = 0

        # Two-pass approach: first collect all footnote markers, then strip
        # This handles cross-page references (marker on page X, footnote on page Y)
        page_results = []
        all_fn_markers = set()

        # Pass 1: separate content, parse footnotes, detect superscripts
        for page_data in analysis.pages:
            content_blocks = self._filter_decoration(
                page_data.text_blocks, layout
            )

            # Filter out text blocks inside diagram regions
            if page_data.page_num in diagram_map:
                content_blocks = self._filter_diagram_text(
                    content_blocks, diagram_map[page_data.page_num],
                    analysis.pages[page_data.page_num]
                )

            body_blocks, fn_blocks = self._separate_footnotes(
                content_blocks, layout, page_data
            )

            page_footnotes = self._parse_footnotes(
                fn_blocks, page_data.page_num, global_fn_counter
            )
            global_fn_counter += len(page_footnotes)

            for fn in page_footnotes:
                all_footnotes[fn.footnote_id] = fn
                all_fn_markers.add(fn.marker)

            body_blocks, refs = self._detect_superscripts(
                body_blocks, layout.body_font_size
            )

            page_results.append({
                'page_data': page_data,
                'body_blocks': body_blocks,
                'refs': refs,
                'page_footnotes': page_footnotes,
            })

        # Pass 2: strip markers using global footnote marker set, build paragraphs
        for pr in page_results:
            body_blocks = pr['body_blocks']
            refs = pr['refs']
            page_footnotes = pr['page_footnotes']
            page_data = pr['page_data']

            # Strip using both detected refs AND all known footnote markers
            self._strip_superscript_markers(body_blocks, refs, all_fn_markers)

            paragraphs = self._blocks_to_paragraphs(
                body_blocks, layout, page_data.page_num, body_font
            )

            self._link_refs_to_footnotes(paragraphs, refs, page_footnotes)

            all_pages.append({
                'page_num': page_data.page_num,
                'paragraphs': paragraphs,
                'footnotes': page_footnotes,
                'body_blocks': body_blocks,
            })

        # Final cleanup: strip any remaining leaked superscript numbers
        # from paragraph text (catches same-font-size embedded references
        # that couldn't be detected from font size alone)
        if all_fn_markers:
            self._final_marker_cleanup(all_pages, all_fn_markers)

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

    def _filter_diagram_text(self, blocks: list, diagram_bboxes: list,
                             page_data) -> list:
        """Remove text blocks that fall within diagram regions."""
        filtered = []
        for block in blocks:
            in_diagram = False
            bx0, by0, bx1, by1 = block.bbox
            for dx0, dy0, dx1, dy1 in diagram_bboxes:
                # Check if block center is within the diagram bbox (with margin)
                cx = (bx0 + bx1) / 2
                cy = (by0 + by1) / 2
                if dx0 - 5 <= cx <= dx1 + 5 and dy0 - 5 <= cy <= dy1 + 5:
                    in_diagram = True
                    break
            if not in_diagram:
                filtered.append(block)
        return filtered

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

    def _strip_superscript_markers(self, body_blocks: list, refs: list,
                                    fn_markers: set = None):
        """Strip superscript marker text embedded in parent block text.

        PyMuPDF includes small-font spans in the parent block's text string,
        so after removing the superscript block, the marker (e.g. "162") remains
        in the body block text (e.g. "apsiėjimus162."). Strip it.

        Uses both detected superscript refs AND known footnote markers from
        the page, since some superscripts are embedded spans that aren't
        detected as separate blocks.
        """
        markers = {ref['text'] for ref in refs} if refs else set()
        if fn_markers:
            markers |= fn_markers
        if not markers:
            return

        for block in body_blocks:
            text = block.text
            for marker in sorted(markers, key=len, reverse=True):
                # Pattern 1: marker stuck to a letter: "apsiėjimus162" → "apsiėjimus"
                p1 = r'(?<=[a-zA-ZąčęėįšųūžĄČĘĖĮŠŲŪŽ.,;:)])' + re.escape(marker) + r'(?=[\s.,;:!?"\')]|$)'
                text = re.sub(p1, '', text)
                # Pattern 2: marker after punctuation+space: 'proto". 4' → 'proto".'
                p2 = r'([.,"\'!?)\]"])\s+' + re.escape(marker) + r'(?=[.\s,;:!?"\')]|$)'
                text = re.sub(p2, r'\1', text)
            block.text = text

    def _final_marker_cleanup(self, all_pages: list, all_fn_markers: set):
        """Final pass: strip leaked footnote numbers from paragraph text.

        Catches same-font-size references that weren't detected as superscripts
        and whose marker wasn't available during per-page processing.
        Handles two patterns:
          1. Numbers stuck to letters: "word162" → "word"
          2. Numbers after punctuation+space: 'proto". 4.' → 'proto".'
        """
        if not all_fn_markers:
            return

        max_fn = max((int(m) for m in all_fn_markers if m.isdigit()), default=0)
        if max_fn == 0:
            return

        def _in_range(num_str):
            try:
                return 1 <= int(num_str) <= max_fn + 5
            except ValueError:
                return False

        for page_data in all_pages:
            for para in page_data['paragraphs']:
                text = para.text

                # Pattern 1: numbers stuck to letters (no space)
                def _strip_after_letter(m):
                    return '' if _in_range(m.group(1)) else m.group(1)
                text = re.sub(
                    r'(?<=[a-zA-ZąčęėįšųūžĄČĘĖĮŠŲŪŽ])(\d{1,3})(?=[\s.,;:!?"\')]|$)',
                    _strip_after_letter, text
                )

                # Pattern 2: standalone numbers after punctuation+space
                # e.g. 'proto". 4.' → 'proto".' or 'word. 4.' → 'word.'
                def _strip_after_punct(m):
                    num = m.group(2)
                    if _in_range(num):
                        return m.group(1)  # keep the punctuation, drop space+number
                    return m.group(0)
                text = re.sub(
                    r'([.,"\'!?)\]"])\s+(\d{1,3})(?=[.\s,;:!?"\')]|$)',
                    _strip_after_punct, text
                )

                # Clean up double spaces left by stripping
                text = re.sub(r'  +', ' ', text)

                if text != para.text:
                    para.text = text
                    # Update non-superscript spans that contain body text
                    new_spans = []
                    for s in para.spans:
                        if s.is_superscript:
                            new_spans.append(s)
                        elif s.text != text:
                            # Apply the same stripping to the span text
                            stext = s.text
                            stext = re.sub(
                                r'(?<=[a-zA-ZąčęėįšųūžĄČĘĖĮŠŲŪŽ])(\d{1,3})(?=[\s.,;:!?"\')]|$)',
                                _strip_after_letter, stext
                            )
                            stext = re.sub(
                                r'([.,"\'!?)\]"])\s+(\d{1,3})(?=[.\s,;:!?"\')]|$)',
                                _strip_after_punct, stext
                            )
                            new_spans.append(TextSpan(
                                text=stext,
                                is_bold=s.is_bold,
                                is_italic=s.is_italic,
                            ))
                        else:
                            new_spans.append(s)
                    para.spans = new_spans

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
        """Extract all images from the PDF, including vector diagrams."""
        images = []
        doc = fitz.open(analysis.file_path)

        # 1. Extract embedded raster images
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
                                continue

                        img_path = img_dir / f"image_{img_ref.xref}.{ext}"
                        img_path.write_bytes(img_bytes)
                        images.append((
                            str(img_path),
                            page_data.page_num,
                            img_ref.bbox,
                        ))
                except Exception as e:
                    logger.warning(f"Failed to extract image xref={img_ref.xref}: {e}")

        # 2. Detect and render vector diagrams (boxes, lines, arrows)
        diagram_regions = self._detect_diagram_regions(doc, analysis)
        for page_num, bbox in diagram_regions:
            try:
                page = doc[page_num]
                # Add padding around the diagram region
                pad = 5
                clip = fitz.Rect(
                    max(0, bbox[0] - pad),
                    max(0, bbox[1] - pad),
                    min(page.rect.width, bbox[2] + pad),
                    min(page.rect.height, bbox[3] + pad),
                )
                # Render the clipped region at high DPI
                mat = fitz.Matrix(2.0, 2.0)  # 2x zoom for clarity
                pix = page.get_pixmap(matrix=mat, clip=clip)
                img_path = img_dir / f"diagram_p{page_num}.png"
                pix.save(str(img_path))
                images.append((str(img_path), page_num, tuple(clip)))
                logger.info(f"Rendered vector diagram on page {page_num}")
            except Exception as e:
                logger.warning(f"Failed to render diagram on page {page_num}: {e}")

        doc.close()
        return images

    def _detect_diagram_regions(self, doc, analysis) -> list:
        """Detect pages with vector diagrams (boxes, lines, arrows).

        Returns list of (page_num, bbox) for significant drawing regions.
        """
        regions = []
        for page_data in analysis.pages:
            page = doc[page_data.page_num]
            try:
                drawings = page.get_drawings()
            except Exception:
                continue

            if len(drawings) < 3:
                continue

            # Filter out single-line decorations (separators, underlines)
            significant = []
            for d in drawings:
                rect = d.get('rect')
                if not rect:
                    continue
                w = rect[2] - rect[0]
                h = rect[3] - rect[1]
                items = d.get('items', [])
                # Significant: has multiple path items OR is a rectangle
                if len(items) >= 2 or (w > 20 and h > 20):
                    significant.append(rect)

            if len(significant) < 2:
                continue

            # Compute bounding box of all significant drawings
            x0 = min(r[0] for r in significant)
            y0 = min(r[1] for r in significant)
            x1 = max(r[2] for r in significant)
            y1 = max(r[3] for r in significant)

            # Only count as diagram if the area is substantial
            area = (x1 - x0) * (y1 - y0)
            page_area = page.rect.width * page.rect.height
            if area > page_area * 0.05 and (x1 - x0) > 50 and (y1 - y0) > 30:
                regions.append((page_data.page_num, (x0, y0, x1, y1)))

        return regions

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
