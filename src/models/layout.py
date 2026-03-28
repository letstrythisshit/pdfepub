from dataclasses import dataclass, field


@dataclass
class RepeatingElement:
    """An element that repeats across many pages (header, footer, page number)."""
    norm_bbox: tuple  # normalized (x0, y0, x1, y1) in 0-1 range
    element_type: str  # 'header', 'footer', 'page_number', 'running_title'
    frequency: float  # fraction of pages it appears on (0-1)
    sample_text: str = ""


@dataclass
class PageFeatures:
    """Geometric features extracted from a page for clustering."""
    page_num: int
    text_block_count: int = 0
    text_coverage: float = 0.0  # text area / page area
    max_font_size: float = 0.0
    dominant_font_size: float = 0.0
    has_large_title: bool = False
    top_margin: float = 0.0  # normalized y of first text
    bottom_margin: float = 0.0  # normalized y of last text
    image_area_ratio: float = 0.0
    small_text_count: int = 0
    vertical_gaps: list = field(default_factory=list)


@dataclass
class LayoutProfile:
    """The complete layout model for a document, built by the Bombe Engine."""
    repeating_elements: list = field(default_factory=list)  # list of RepeatingElement
    page_types: dict = field(default_factory=dict)  # page_num -> type string
    font_hierarchy: dict = field(default_factory=dict)  # font_size -> semantic role
    chapter_start_pages: list = field(default_factory=list)
    body_font_size: float = 0.0
    heading_font_sizes: list = field(default_factory=list)  # sorted descending
    footnote_font_size: float = 0.0
    footnote_zone_y: float = 1.0  # normalized y threshold (below = footnotes)
    page_number_pattern: str = "none"  # 'bare_number', 'pipe_number', 'none'
    page_number_offset: int = 0  # PDF page 0 = printed page N
    content_margins: tuple = (0.0, 0.0, 1.0, 1.0)  # (left, top, right, bottom) normalized
