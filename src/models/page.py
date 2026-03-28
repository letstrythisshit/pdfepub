from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class TextBlock:
    """A text block extracted from a PDF page with positional and font info."""
    bbox: tuple  # (x0, y0, x1, y1) in page coordinates
    text: str
    font_name: str
    font_size: float
    is_bold: bool
    is_italic: bool
    color: tuple = (0, 0, 0)
    baseline_y: float = 0.0

    @property
    def norm_bbox(self):
        """Normalized bbox (set externally by analyzer)."""
        return getattr(self, '_norm_bbox', self.bbox)

    @norm_bbox.setter
    def norm_bbox(self, value):
        self._norm_bbox = value

    @property
    def width(self):
        return self.bbox[2] - self.bbox[0]

    @property
    def height(self):
        return self.bbox[3] - self.bbox[1]

    @property
    def center_x(self):
        return (self.bbox[0] + self.bbox[2]) / 2

    @property
    def center_y(self):
        return (self.bbox[1] + self.bbox[3]) / 2


@dataclass
class ImageRef:
    """Reference to an image extracted from a PDF page."""
    xref: int
    bbox: tuple  # (x0, y0, x1, y1)
    width: int = 0
    height: int = 0
    image_path: str = ""
    colorspace: str = ""


@dataclass
class PageData:
    """All extracted data for a single PDF page."""
    page_num: int
    width: float
    height: float
    image_path: str = ""
    text_blocks: list = field(default_factory=list)
    images: list = field(default_factory=list)
    text_quality: float = 1.0  # 0-1, <0.5 means needs OCR


@dataclass
class PDFAnalysis:
    """Complete analysis result for a PDF file."""
    file_path: str
    page_count: int
    page_width: float
    page_height: float
    metadata: dict = field(default_factory=dict)
    outlines: list = field(default_factory=list)  # PDF TOC entries: [level, title, page]
    pages: list = field(default_factory=list)  # list of PageData
    needs_ocr: bool = False
    fonts_used: set = field(default_factory=set)
