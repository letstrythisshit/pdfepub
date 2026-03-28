# pdfepub

Convert PDF books into WCAG 2.2 AA compliant EPUB3 files that pass [epubcheck](https://www.w3.org/publishing/epubcheck/) and [ACE by DAISY](https://daisy.org/activities/software/ace/) validation.

## Features

- 6-stage conversion pipeline: PDF analysis, layout detection, content extraction, structure building, EPUB generation, validation
- Cross-page statistical layout analysis ("Bombe Engine") — detects and removes headers, footers, page numbers automatically
- Bidirectional footnote linking (`noteref` <-> `doc-backlink`)
- Same-font-size footnote detection for books where footnotes use body font
- Automatic chapter detection from PDF outlines or heading font heuristics
- Title/author extraction from title page when PDF metadata is missing
- WCAG 2.2 AA accessibility metadata, ARIA roles, `epub:type` semantic markup
- Dual navigation: EPUB3 `nav` + EPUB2 `ncx` for maximum reader compatibility
- LLM-powered image alt-text generation in Lithuanian (via OpenRouter, optional)
- Budget-capped LLM usage (max $4)

## Requirements

- Python 3.9+
- Java (for epubcheck validation)
- [epubcheck](https://www.w3.org/publishing/epubcheck/) (optional but recommended)

## Installation

```bash
git clone https://github.com/letstrythisshit/pdfepub.git
cd pdfepub
pip install .
```

Or for development:

```bash
pip install -e .
```

## Setup

### epubcheck (recommended)

Download and extract [epubcheck](https://github.com/w3c/epubcheck/releases):

```bash
wget https://github.com/w3c/epubcheck/releases/download/v5.2.1/epubcheck-5.2.1.zip
unzip epubcheck-5.2.1.zip -d /opt/epubcheck
export EPUBCHECK_JAR=/opt/epubcheck/epubcheck-5.2.1/epubcheck.jar
```

Or pass the path directly: `pdfepub --epubcheck /path/to/epubcheck.jar input.pdf`

If epubcheck is not found, the pipeline still runs but skips validation.

### LLM alt-text generation (optional)

To generate image alt-text in Lithuanian using OpenRouter:

```bash
# Create .env file or export directly
export OPENROUTER_API_KEY=your-api-key-here
```

Uses `openai/gpt-5-mini` via OpenRouter. Without the key, images get placeholder alt-text.

## Usage

### Single file

```bash
pdfepub input.pdf
pdfepub input.pdf -o output.epub
```

### Batch conversion

```bash
pdfepub *.pdf --output-dir converted/
```

### Without LLM (faster, no API key needed)

```bash
pdfepub --no-llm input.pdf
```

### Module invocation

```bash
python -m src input.pdf
python -m src --no-llm --output-dir output/ *.pdf
```

### All options

```
usage: pdfepub [-h] [-o OUTPUT] [--no-llm] [-v] [--output-dir OUTPUT_DIR]
               [--epubcheck EPUBCHECK]
               input [input ...]

Convert PDF books to WCAG 2.2 AA compliant EPUB3

positional arguments:
  input                 PDF file(s) to convert

options:
  -h, --help            show this help message and exit
  -o OUTPUT, --output OUTPUT
                        Output EPUB path (single file mode)
  --no-llm              Skip LLM calls (no alt text, no validation)
  -v, --verbose         Enable verbose logging
  --output-dir OUTPUT_DIR
                        Output directory for batch mode (default: output)
  --epubcheck EPUBCHECK
                        Path to epubcheck.jar (or set EPUBCHECK_JAR env var)
```

## Pipeline Stages

| Stage | Module | Description |
|-------|--------|-------------|
| 1 | `stage1_pdf_analyzer` | Extract text blocks, images, metadata using PyMuPDF |
| 2 | `stage2_layout_detector` | Cross-page statistical analysis — detect repeating elements, footnote zones, font hierarchy |
| 3 | `stage3_content_extractor` | Filter decoration, separate footnotes, detect superscripts, build paragraphs |
| 4 | `stage4_structure_builder` | Build chapters from outlines or heuristics, assign filenames, build TOC |
| 5 | `stage5_epub_generator` | Generate XHTML, OPF, NCX, nav, CSS, package as EPUB |
| 6 | `stage6_quality_checker` | Validate with epubcheck and ACE by DAISY |

## EPUB Output Structure

```
book.epub
├── mimetype
├── META-INF/
│   └── container.xml
└── OEBPS/
    ├── content.opf          # Package document with WCAG 2.2 AA metadata
    ├── toc.ncx              # EPUB2 navigation
    ├── toc.xhtml            # EPUB3 navigation (toc, page-list, landmarks)
    ├── css/styles.css        # Stylesheet
    ├── cover.xhtml          # Front matter
    ├── chapter_01_*.xhtml   # Chapter files with semantic markup
    └── image/               # Extracted images
```

## Project Status

**Working** — tested on 4 Lithuanian PDF books:

| Book | Pages | Paragraphs | Footnotes | Chapters | epubcheck |
|------|-------|-----------|-----------|----------|-----------|
| Daukantas — Istorija Žemaitiška | 362 | 1,488 | 317 | 34 | 0 errors |
| Sruoga — Dievų miškas | 221 | 1,897 | 8 | 63 | 0 errors |
| Bučienė — Lietuvių kalbos sintaksė | 136 | 930 | 4 | 13 | 0 errors |
| Vaišnienė — Istorinės fonetikos apžvalga | 98 | 575 | 3 | 1 | 0 errors |

### Known limitations

- OCR not implemented — PDFs must have extractable text (not scanned images)
- Vaisnienė PDF detected as single chapter (no outlines, heading heuristic found only 1 chapter start)
- Some footnote references may not be linked if the superscript was not extracted as a separate text span by PyMuPDF
- Complex table layouts are not preserved (converted to plain text paragraphs)

## License

MIT
