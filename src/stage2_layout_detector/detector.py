import logging
from collections import Counter, defaultdict

import numpy as np

from ..models.page import PDFAnalysis
from ..models.layout import RepeatingElement, PageFeatures, LayoutProfile

logger = logging.getLogger(__name__)

# Tolerances for position matching (normalized coordinates)
X_TOLERANCE = 0.05
Y_TOLERANCE = 0.02
SIZE_TOLERANCE = 0.5  # font size points


class LayoutDetector:
    """Stage 2: The Bombe Engine — cross-page statistical layout analysis."""

    def detect(self, analysis: PDFAnalysis) -> LayoutProfile:
        logger.info(f"Bombe Engine: analyzing {analysis.page_count} pages")

        # Step 1: Extract features per page
        features = [self._extract_features(p, analysis) for p in analysis.pages]

        # Step 2: Build font size histogram and hierarchy
        font_hierarchy, body_size, heading_sizes, footnote_size = \
            self._analyze_font_hierarchy(analysis)

        # Step 3: Detect repeating elements
        repeating = self._detect_repeating_elements(analysis, body_size)

        # Step 4: Detect footnote zones
        footnote_zone_y = self._detect_footnote_zone(analysis, body_size, footnote_size)

        # Step 5: Detect page number pattern
        pn_pattern, pn_offset = self._detect_page_numbers(analysis, repeating)

        # Step 6: Classify page types
        page_types = self._classify_pages(features, body_size, heading_sizes)

        # Step 7: Detect chapter start pages
        chapter_starts = self._detect_chapter_starts(
            analysis, features, page_types, heading_sizes
        )

        # Step 8: Compute content margins
        content_margins = self._compute_content_margins(analysis, repeating)

        profile = LayoutProfile(
            repeating_elements=repeating,
            page_types=page_types,
            font_hierarchy=font_hierarchy,
            chapter_start_pages=chapter_starts,
            body_font_size=body_size,
            heading_font_sizes=heading_sizes,
            footnote_font_size=footnote_size,
            footnote_zone_y=footnote_zone_y,
            page_number_pattern=pn_pattern,
            page_number_offset=pn_offset,
            content_margins=content_margins,
        )

        logger.info(f"Layout: {len(repeating)} repeating elements, "
                    f"{len(chapter_starts)} chapter starts, "
                    f"body font={body_size}, headings={heading_sizes}")
        return profile

    def _extract_features(self, page_data, analysis) -> PageFeatures:
        blocks = page_data.text_blocks
        pw, ph = analysis.page_width, analysis.page_height

        if not blocks:
            return PageFeatures(page_num=page_data.page_num)

        sizes = [b.font_size for b in blocks]
        char_counts = Counter()
        for b in blocks:
            char_counts[round(b.font_size, 1)] += len(b.text)

        dominant_size = char_counts.most_common(1)[0][0] if char_counts else 0
        text_area = sum(b.width * b.height for b in blocks)
        page_area = pw * ph

        # Vertical gaps between consecutive blocks
        sorted_blocks = sorted(blocks, key=lambda b: b.bbox[1])
        gaps = []
        for i in range(1, len(sorted_blocks)):
            gap = sorted_blocks[i].bbox[1] - sorted_blocks[i - 1].bbox[3]
            if gap > 5:  # significant gap
                gaps.append(gap / ph)

        return PageFeatures(
            page_num=page_data.page_num,
            text_block_count=len(blocks),
            text_coverage=text_area / page_area if page_area > 0 else 0,
            max_font_size=max(sizes) if sizes else 0,
            dominant_font_size=dominant_size,
            has_large_title=max(sizes, default=0) > dominant_size * 1.3,
            top_margin=min(b.norm_bbox[1] for b in blocks),
            bottom_margin=max(b.norm_bbox[3] for b in blocks),
            image_area_ratio=0,  # computed from image refs if needed
            small_text_count=sum(1 for b in blocks if b.font_size < dominant_size * 0.8),
            vertical_gaps=gaps,
        )

    def _analyze_font_hierarchy(self, analysis):
        """Build font size histogram weighted by character count."""
        size_chars = Counter()
        for page in analysis.pages:
            for block in page.text_blocks:
                rounded = round(block.font_size, 1)
                size_chars[rounded] += len(block.text)

        if not size_chars:
            return {}, 10.0, [], 8.0

        # Body font = most common size by character count
        body_size = size_chars.most_common(1)[0][0]

        # Heading fonts = sizes significantly larger than body
        heading_sizes = sorted(
            [s for s, c in size_chars.items() if s > body_size * 1.2],
            reverse=True
        )

        # Footnote font = most common size smaller than body
        small_sizes = [(s, c) for s, c in size_chars.items() if s < body_size * 0.85]
        footnote_size = max(
            (s for s, c in small_sizes),
            default=body_size * 0.8
        ) if small_sizes else body_size * 0.8

        # Build hierarchy mapping
        hierarchy = {body_size: "body"}
        for i, hs in enumerate(heading_sizes):
            hierarchy[hs] = f"h{min(i + 1, 6)}"
        if footnote_size != body_size:
            hierarchy[footnote_size] = "footnote"

        logger.info(f"Font hierarchy: body={body_size}, headings={heading_sizes}, "
                    f"footnote={footnote_size}")
        return hierarchy, body_size, heading_sizes, footnote_size

    def _detect_repeating_elements(self, analysis, body_size) -> list:
        """Find text spans that appear at same position across >60% of pages."""
        # Group spans by approximate normalized position and size
        position_groups = defaultdict(list)

        for page in analysis.pages:
            for block in page.text_blocks:
                # Skip body-sized text (too common, not repeating decoration)
                if abs(block.font_size - body_size) < SIZE_TOLERANCE:
                    continue

                # Create position key with tolerance
                nx = round(block.norm_bbox[0] / X_TOLERANCE) * X_TOLERANCE
                ny = round(block.norm_bbox[1] / Y_TOLERANCE) * Y_TOLERANCE
                key = (nx, ny, round(block.font_size, 1))
                position_groups[key].append((page.page_num, block))

        repeating = []
        total_pages = analysis.page_count
        min_frequency = 0.4  # appear on at least 40% of pages

        for key, occurrences in position_groups.items():
            unique_pages = len(set(p for p, _ in occurrences))
            freq = unique_pages / total_pages

            if freq >= min_frequency:
                sample_block = occurrences[0][1]
                nb = sample_block.norm_bbox

                # Classify by position
                if nb[1] < 0.08:
                    etype = "header"
                elif nb[1] > 0.92:
                    etype = "footer"
                elif nb[1] > 0.85:
                    # Check if it's a page number (short, numeric-ish)
                    avg_len = np.mean([len(b.text) for _, b in occurrences])
                    if avg_len < 5:
                        etype = "page_number"
                    else:
                        etype = "footer"
                else:
                    etype = "running_title"

                repeating.append(RepeatingElement(
                    norm_bbox=nb,
                    element_type=etype,
                    frequency=freq,
                    sample_text=sample_block.text[:50],
                ))

        # Also detect repeating elements at body size that are very frequent
        # (e.g., running headers/footers in body font)
        body_position_groups = defaultdict(list)
        for page in analysis.pages:
            for block in page.text_blocks:
                if abs(block.font_size - body_size) >= SIZE_TOLERANCE:
                    continue
                if block.norm_bbox[1] < 0.06 or block.norm_bbox[1] > 0.94:
                    nx = round(block.norm_bbox[0] / X_TOLERANCE) * X_TOLERANCE
                    ny = round(block.norm_bbox[1] / Y_TOLERANCE) * Y_TOLERANCE
                    key = (nx, ny)
                    body_position_groups[key].append((page.page_num, block))

        for key, occurrences in body_position_groups.items():
            unique_pages = len(set(p for p, _ in occurrences))
            freq = unique_pages / total_pages
            if freq >= min_frequency:
                sample_block = occurrences[0][1]
                nb = sample_block.norm_bbox
                etype = "header" if nb[1] < 0.1 else "footer"
                repeating.append(RepeatingElement(
                    norm_bbox=nb,
                    element_type=etype,
                    frequency=freq,
                    sample_text=sample_block.text[:50],
                ))

        logger.info(f"Found {len(repeating)} repeating elements")
        return repeating

    def _detect_footnote_zone(self, analysis, body_size, footnote_size) -> float:
        """Find the y-threshold where footnotes begin."""
        import re
        footnote_ys = []

        # Method 1: Look for pages where small text appears at the bottom
        for page in analysis.pages:
            small_blocks = [
                b for b in page.text_blocks
                if b.font_size < body_size * 0.85 and b.norm_bbox[1] > 0.5
            ]
            body_blocks = [
                b for b in page.text_blocks
                if abs(b.font_size - body_size) < SIZE_TOLERANCE and b.norm_bbox[1] > 0.3
            ]
            if small_blocks and body_blocks:
                max_body_y = max(b.norm_bbox[3] for b in body_blocks)
                min_fn_y = min(b.norm_bbox[1] for b in small_blocks)
                if min_fn_y > max_body_y:
                    footnote_ys.append((max_body_y + min_fn_y) / 2)

        if footnote_ys:
            threshold = np.median(footnote_ys)
            logger.info(f"Footnote zone threshold (small font): {threshold:.3f}")
            return threshold

        # Method 2: Detect same-size footnotes at page bottom (number-prefixed blocks)
        # Some books use body font size for footnotes, separated only by position
        for page in analysis.pages:
            bottom_blocks = sorted(
                [b for b in page.text_blocks if b.norm_bbox[1] > 0.7],
                key=lambda b: b.norm_bbox[1]
            )
            # Check if bottom blocks start with a number (footnote pattern)
            fn_candidates = []
            for b in bottom_blocks:
                text = b.text.strip()
                if re.match(r'^\d{1,3}\s', text) or re.match(r'^\d{1,3}$', text):
                    fn_candidates.append(b)

            if len(fn_candidates) >= 1:
                # Find the gap between the last non-footnote block and the first footnote
                fn_min_y = min(b.norm_bbox[1] for b in fn_candidates)
                upper_blocks = [
                    b for b in page.text_blocks
                    if b.norm_bbox[3] < fn_min_y and b.norm_bbox[1] < fn_min_y
                ]
                if upper_blocks:
                    max_upper_y = max(b.norm_bbox[3] for b in upper_blocks)
                    gap = fn_min_y - max_upper_y
                    if gap > 0.01:  # meaningful gap between body and footnotes
                        footnote_ys.append((max_upper_y + fn_min_y) / 2)

        if footnote_ys and len(footnote_ys) >= 3:
            threshold = np.median(footnote_ys)
            logger.info(f"Footnote zone threshold (number-prefixed): {threshold:.3f}")
            return threshold

        return 1.0  # no footnote zone detected

    def _detect_page_numbers(self, analysis, repeating) -> tuple:
        """Detect page number pattern and offset."""
        # Check if any repeating element looks like page numbers
        for elem in repeating:
            if elem.element_type == "page_number":
                return "bare_number", 0

        # Look for sequential numbers at consistent positions
        # Check bottom of pages for isolated numbers
        bottom_numbers = {}
        for page in analysis.pages:
            for block in page.text_blocks:
                if block.norm_bbox[1] > 0.9:
                    text = block.text.strip().strip('|').strip()
                    try:
                        num = int(text)
                        bottom_numbers[page.page_num] = num
                    except ValueError:
                        pass

        if len(bottom_numbers) > analysis.page_count * 0.3:
            # Check if sequential
            sorted_pages = sorted(bottom_numbers.items())
            diffs = [sorted_pages[i + 1][1] - sorted_pages[i][1]
                     for i in range(len(sorted_pages) - 1)]
            if diffs and abs(np.median(diffs) - 1) < 0.5:
                offset = sorted_pages[0][1] - sorted_pages[0][0]
                if '|' in str(analysis.pages[0].text_blocks[-1].text if analysis.pages[0].text_blocks else ''):
                    return "pipe_number", offset
                return "bare_number", offset

        return "none", 0

    def _classify_pages(self, features, body_size, heading_sizes) -> dict:
        """Classify each page by type."""
        page_types = {}
        for f in features:
            if f.text_block_count == 0:
                page_types[f.page_num] = "blank"
            elif f.text_block_count <= 3 and f.text_coverage < 0.05:
                page_types[f.page_num] = "separator"
            elif f.has_large_title and f.top_margin > 0.15:
                page_types[f.page_num] = "chapter_start"
            elif f.has_large_title and f.text_coverage < 0.15:
                page_types[f.page_num] = "title_page"
            elif f.text_coverage < 0.08 and f.text_block_count < 5:
                page_types[f.page_num] = "front_matter"
            elif f.image_area_ratio > 0.5:
                page_types[f.page_num] = "image_page"
            else:
                page_types[f.page_num] = "body"

        # First few pages are often front matter
        for i in range(min(5, len(features))):
            if page_types.get(features[i].page_num) == "body":
                if features[i].text_coverage < 0.2:
                    page_types[features[i].page_num] = "front_matter"

        return page_types

    def _detect_chapter_starts(self, analysis, features, page_types,
                               heading_sizes) -> list:
        """Identify chapter start pages."""
        # If we have PDF outlines, use those page numbers
        if analysis.outlines:
            starts = set()
            for level, title, page_num in analysis.outlines:
                starts.add(page_num - 1)  # outlines are 1-based
            return sorted(starts)

        # Otherwise use page classification + heading detection
        starts = []
        for f in features:
            if page_types.get(f.page_num) == "chapter_start":
                starts.append(f.page_num)

        # Also look for pages with heading-sized text after a gap
        if not starts and heading_sizes:
            largest_heading = heading_sizes[0]
            for page in analysis.pages:
                for block in page.text_blocks:
                    if abs(block.font_size - largest_heading) < SIZE_TOLERANCE:
                        if page.page_num not in starts:
                            starts.append(page.page_num)
                        break

        return sorted(starts)

    def _compute_content_margins(self, analysis, repeating) -> tuple:
        """Compute the content area after excluding repeating elements."""
        if not repeating:
            # Use observed margins from text blocks
            lefts, tops, rights, bottoms = [], [], [], []
            for page in analysis.pages:
                for block in page.text_blocks:
                    lefts.append(block.norm_bbox[0])
                    tops.append(block.norm_bbox[1])
                    rights.append(block.norm_bbox[2])
                    bottoms.append(block.norm_bbox[3])
            if lefts:
                return (
                    np.percentile(lefts, 5),
                    np.percentile(tops, 5),
                    np.percentile(rights, 95),
                    np.percentile(bottoms, 95),
                )
            return (0.05, 0.05, 0.95, 0.95)

        # Exclude zones occupied by repeating elements
        top_y = 0.0
        bottom_y = 1.0
        for elem in repeating:
            if elem.element_type in ("header", "running_title"):
                top_y = max(top_y, elem.norm_bbox[3] + 0.01)
            elif elem.element_type in ("footer", "page_number"):
                bottom_y = min(bottom_y, elem.norm_bbox[1] - 0.01)

        return (0.05, top_y, 0.95, bottom_y)
