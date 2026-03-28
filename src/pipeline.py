import logging
import time
from pathlib import Path

from .stage1_pdf_analyzer.analyzer import PDFAnalyzer
from .stage2_layout_detector.detector import LayoutDetector
from .stage3_content_extractor.extractor import ContentExtractor
from .stage4_structure_builder.builder import StructureBuilder
from .stage5_epub_generator.generator import EPUBGenerator
from .stage6_quality_checker.checker import QualityChecker
from .llm.client import LLMClient

logger = logging.getLogger(__name__)


class Pipeline:
    """Orchestrates the 6-stage PDF-to-EPUB3 conversion pipeline."""

    def __init__(self, use_llm: bool = True, epubcheck_path: str = None):
        self.llm = LLMClient() if use_llm else None
        self.epubcheck_path = epubcheck_path

    def run(self, pdf_path: str, output_path: str = None) -> dict:
        pdf_path = Path(pdf_path)
        if not output_path:
            output_path = pdf_path.with_suffix('.epub')
        output_path = Path(output_path)

        work_dir = output_path.parent / f".work_{pdf_path.stem}"
        work_dir.mkdir(parents=True, exist_ok=True)

        results = {'stages': {}, 'timings': {}}

        # Stage 1: PDF Analysis
        logger.info("=" * 60)
        logger.info("STAGE 1: PDF Analysis")
        t0 = time.time()
        analyzer = PDFAnalyzer(render_dpi=150)
        analysis = analyzer.analyze(str(pdf_path), str(work_dir))
        results['timings']['stage1'] = time.time() - t0
        results['stages']['pdf_analysis'] = {
            'pages': analysis.page_count,
            'outlines': len(analysis.outlines),
            'needs_ocr': analysis.needs_ocr,
            'fonts': len(analysis.fonts_used),
        }
        logger.info(f"Stage 1 complete: {analysis.page_count} pages, "
                    f"{len(analysis.outlines)} outlines, "
                    f"needs_ocr={analysis.needs_ocr}")

        # Stage 2: Layout Detection (Bombe Engine)
        logger.info("=" * 60)
        logger.info("STAGE 2: Bombe Engine - Layout Detection")
        t0 = time.time()
        detector = LayoutDetector()
        layout = detector.detect(analysis)
        results['timings']['stage2'] = time.time() - t0
        results['stages']['layout'] = {
            'repeating_elements': len(layout.repeating_elements),
            'chapter_starts': len(layout.chapter_start_pages),
            'body_font': layout.body_font_size,
            'heading_fonts': layout.heading_font_sizes,
        }
        logger.info(f"Stage 2 complete: {len(layout.repeating_elements)} repeating, "
                    f"{len(layout.chapter_start_pages)} chapters")

        # Stage 3: Content Extraction
        logger.info("=" * 60)
        logger.info("STAGE 3: Content Extraction")
        t0 = time.time()
        extractor = ContentExtractor()
        extracted = extractor.extract(analysis, layout, str(work_dir))
        results['timings']['stage3'] = time.time() - t0

        total_paras = sum(len(p['paragraphs']) for p in extracted['pages'])
        total_fn = len(extracted['footnotes'])
        results['stages']['extraction'] = {
            'paragraphs': total_paras,
            'footnotes': total_fn,
            'images': len(extracted['images']),
        }
        logger.info(f"Stage 3 complete: {total_paras} paragraphs, "
                    f"{total_fn} footnotes, {len(extracted['images'])} images")

        # Stage 4: Structure Building
        logger.info("=" * 60)
        logger.info("STAGE 4: Structure Building")
        t0 = time.time()
        builder = StructureBuilder(llm_client=self.llm)
        document = builder.build(analysis, layout, extracted)
        results['timings']['stage4'] = time.time() - t0
        results['stages']['structure'] = {
            'title': document.title,
            'author': document.author,
            'front_matter': len(document.front_matter),
            'chapters': len(document.chapters),
            'toc_entries': len(document.toc),
            'total_pages': document.total_pages,
        }
        logger.info(f"Stage 4 complete: '{document.title}' by {document.author}, "
                    f"{len(document.chapters)} chapters")

        # Stage 5: EPUB Generation
        logger.info("=" * 60)
        logger.info("STAGE 5: EPUB Generation")
        t0 = time.time()
        generator = EPUBGenerator(llm_client=self.llm)
        epub_path = generator.generate(document, str(output_path))
        results['timings']['stage5'] = time.time() - t0
        results['epub_path'] = epub_path
        logger.info(f"Stage 5 complete: {epub_path}")

        # Stage 6: Validation
        logger.info("=" * 60)
        logger.info("STAGE 6: Quality Check")
        t0 = time.time()
        checker = QualityChecker(epubcheck_path=self.epubcheck_path)
        validation = checker.check(epub_path)
        results['timings']['stage6'] = time.time() - t0
        results['validation'] = validation
        logger.info(f"Stage 6 complete: passed={validation['passed']}")

        # Budget report
        if self.llm:
            results['llm_budget'] = self.llm.get_budget_report()

        # Clean up work directory
        import shutil
        shutil.rmtree(work_dir, ignore_errors=True)

        # Summary
        total_time = sum(results['timings'].values())
        logger.info("=" * 60)
        logger.info(f"CONVERSION COMPLETE in {total_time:.1f}s")
        logger.info(f"Output: {epub_path}")
        logger.info(f"Validation: {'PASSED' if validation['passed'] else 'FAILED'}")
        if self.llm:
            budget = self.llm.get_budget_report()
            logger.info(f"LLM cost: ${budget['total_spent']:.4f} "
                       f"({budget['calls']} calls)")

        return results
