#!/usr/bin/env python3
"""PDF-to-EPUB3 Converter — WCAG 2.2 AA Compliant

Usage:
    python -m src.cli input.pdf [output.epub]
    python -m src.cli --batch *.pdf
    python -m src.cli --no-llm input.pdf  # skip LLM calls
"""

import argparse
import logging
import sys
from pathlib import Path

from .pipeline import Pipeline


def setup_logging(verbose: bool = False):
    level = logging.DEBUG if verbose else logging.INFO
    logging.basicConfig(
        level=level,
        format='%(asctime)s [%(levelname)s] %(name)s: %(message)s',
        datefmt='%H:%M:%S',
    )


def main():
    parser = argparse.ArgumentParser(
        description="Convert PDF books to WCAG 2.2 AA compliant EPUB3"
    )
    parser.add_argument('input', nargs='+', help='PDF file(s) to convert')
    parser.add_argument('-o', '--output', help='Output EPUB path (single file mode)')
    parser.add_argument('--no-llm', action='store_true',
                       help='Skip LLM calls (no alt text, no validation)')
    parser.add_argument('-v', '--verbose', action='store_true',
                       help='Enable verbose logging')
    parser.add_argument('--output-dir', default='output',
                       help='Output directory for batch mode')
    parser.add_argument('--epubcheck', default=None,
                       help='Path to epubcheck.jar (or set EPUBCHECK_JAR env var)')

    args = parser.parse_args()
    setup_logging(args.verbose)

    pipeline = Pipeline(use_llm=not args.no_llm, epubcheck_path=args.epubcheck)
    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    for pdf_file in args.input:
        pdf_path = Path(pdf_file)
        if not pdf_path.exists():
            logging.error(f"File not found: {pdf_file}")
            continue

        if args.output and len(args.input) == 1:
            out_path = args.output
        else:
            out_path = str(output_dir / pdf_path.with_suffix('.epub').name)

        logging.info(f"\n{'='*60}")
        logging.info(f"Converting: {pdf_file} -> {out_path}")
        logging.info(f"{'='*60}")

        try:
            results = pipeline.run(str(pdf_path), out_path)

            if results.get('validation', {}).get('passed'):
                print(f"✓ {pdf_path.name} -> {out_path} (PASSED)")
            else:
                print(f"⚠ {pdf_path.name} -> {out_path} (validation issues)")

        except Exception as e:
            logging.error(f"Failed to convert {pdf_file}: {e}", exc_info=True)
            print(f"✗ {pdf_path.name}: {e}")

    # Print budget summary
    if not args.no_llm:
        budget = pipeline.llm.get_budget_report()
        print(f"\nLLM Budget: ${budget['total_spent']:.4f} / "
              f"${budget['budget_limit']:.2f} "
              f"({budget['calls']} calls)")


if __name__ == '__main__':
    main()
