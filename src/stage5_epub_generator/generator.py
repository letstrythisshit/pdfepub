import logging
import shutil
import uuid
import zipfile
from datetime import datetime
from pathlib import Path

from ..models.document import DocumentStructure
from ..llm.client import LLMClient
from ..llm.prompts import ALT_TEXT_PROMPT
from .xhtml_builder import XHTMLBuilder
from .opf_builder import OPFBuilder
from .ncx_builder import NCXBuilder
from .nav_builder import NavBuilder
from .css_builder import CSSBuilder

logger = logging.getLogger(__name__)


class EPUBGenerator:
    """Stage 5: Assemble EPUB3 package with WCAG 2.2 AA compliance."""

    def __init__(self, llm_client: LLMClient = None):
        self.llm = llm_client
        self.xhtml = XHTMLBuilder()
        self.opf = OPFBuilder()
        self.ncx = NCXBuilder()
        self.nav = NavBuilder()
        self.css = CSSBuilder()

    def generate(self, doc: DocumentStructure, output_path: str) -> str:
        output_path = Path(output_path)
        build_dir = output_path.parent / f".epub_build_{output_path.stem}"

        # Clean and create build directory
        if build_dir.exists():
            shutil.rmtree(build_dir)
        build_dir.mkdir(parents=True)

        oebps = build_dir / "OEBPS"
        oebps.mkdir()
        (oebps / "css").mkdir()
        (oebps / "image").mkdir()
        meta_inf = build_dir / "META-INF"
        meta_inf.mkdir()

        # Generate alt text for images using LLM
        if self.llm:
            self._generate_alt_texts(doc)

        # Generate unique book ID
        book_id = doc.isbn if doc.isbn else f"urn:uuid:{uuid.uuid4()}"

        # 1. Write mimetype
        (build_dir / "mimetype").write_text("application/epub+zip", encoding="utf-8")

        # 2. Write container.xml
        (meta_inf / "container.xml").write_text(
            '<?xml version="1.0" encoding="utf-8"?>\n'
            '<container version="1.0" xmlns="urn:oasis:names:tc:opendocument:xmlns:container">\n'
            '  <rootfiles>\n'
            '    <rootfile full-path="OEBPS/content.opf" media-type="application/oebps-package+xml"/>\n'
            '  </rootfiles>\n'
            '</container>\n',
            encoding="utf-8"
        )

        # 3. Generate CSS
        css_content = self.css.build()
        (oebps / "css" / "styles.css").write_text(css_content, encoding="utf-8")

        # 4. Copy images
        image_manifest = []
        for i, (img_path, alt_text) in enumerate(doc.images):
            if Path(img_path).exists():
                ext = Path(img_path).suffix
                dest_name = f"image_{i + 1}{ext}"
                shutil.copy2(img_path, oebps / "image" / dest_name)
                image_manifest.append((dest_name, self._get_media_type(ext)))

        # 5. Generate XHTML content files
        content_files = []

        # Front matter
        for ch in doc.front_matter:
            xhtml = self.xhtml.build_chapter(ch, doc, is_front_matter=True)
            (oebps / ch.epub_filename).write_text(xhtml, encoding="utf-8")
            content_files.append((ch.epub_filename, ch.title, "frontmatter"))

        # Main chapters
        for ch in doc.chapters:
            xhtml = self.xhtml.build_chapter(ch, doc)
            (oebps / ch.epub_filename).write_text(xhtml, encoding="utf-8")
            content_files.append((ch.epub_filename, ch.title, "bodymatter"))

        # 6. Generate navigation (toc.xhtml)
        nav_xhtml = self.nav.build(doc)
        (oebps / "toc.xhtml").write_text(nav_xhtml, encoding="utf-8")

        # 7. Generate NCX (toc.ncx)
        ncx = self.ncx.build(doc, book_id)
        (oebps / "toc.ncx").write_text(ncx, encoding="utf-8")

        # 8. Generate OPF (content.opf)
        opf = self.opf.build(doc, book_id, content_files, image_manifest)
        (oebps / "content.opf").write_text(opf, encoding="utf-8")

        # 9. Package as EPUB (ZIP)
        self._package_epub(build_dir, output_path)

        # Clean up build directory
        shutil.rmtree(build_dir)

        logger.info(f"EPUB generated: {output_path}")
        return str(output_path)

    def _generate_alt_texts(self, doc: DocumentStructure):
        """Generate alt text for images using LLM vision."""
        updated_images = []
        for img_path, existing_alt in doc.images:
            if existing_alt:
                updated_images.append((img_path, existing_alt))
                continue

            if Path(img_path).exists():
                try:
                    alt = self.llm.ask_vision(ALT_TEXT_PROMPT, img_path, model_key="alt_text")
                    alt = alt.strip().strip('"').strip("'")
                    logger.info(f"Generated alt text for {img_path}: {alt[:50]}...")
                    updated_images.append((img_path, alt))
                except Exception as e:
                    logger.warning(f"Failed to generate alt text: {e}")
                    updated_images.append((img_path, "Image from the book"))
            else:
                updated_images.append((img_path, "Image from the book"))

        doc.images = updated_images

        # Also update chapter images
        for ch in doc.chapters:
            updated = []
            for img_path, alt in ch.images:
                # Find matching alt from doc.images
                for di_path, di_alt in doc.images:
                    if di_path == img_path:
                        updated.append((img_path, di_alt))
                        break
                else:
                    updated.append((img_path, alt or "Image from the book"))
            ch.images = updated

    def _get_media_type(self, ext: str) -> str:
        ext = ext.lower().lstrip('.')
        return {
            'jpg': 'image/jpeg', 'jpeg': 'image/jpeg',
            'png': 'image/png', 'gif': 'image/gif',
            'svg': 'image/svg+xml', 'webp': 'image/webp',
            'jpx': 'image/jpeg', 'jp2': 'image/jpeg',
        }.get(ext, 'image/png')

    def _package_epub(self, build_dir: Path, output_path: Path):
        """Package build directory as EPUB ZIP with correct structure."""
        with zipfile.ZipFile(str(output_path), 'w') as zf:
            # mimetype MUST be first, uncompressed, no extra field
            mimetype_path = build_dir / "mimetype"
            zf.write(str(mimetype_path), "mimetype",
                     compress_type=zipfile.ZIP_STORED)

            # All other files compressed
            for path in sorted(build_dir.rglob("*")):
                if path.is_file() and path.name != "mimetype":
                    arcname = str(path.relative_to(build_dir))
                    zf.write(str(path), arcname,
                             compress_type=zipfile.ZIP_DEFLATED)
