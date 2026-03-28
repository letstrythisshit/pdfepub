ALT_TEXT_PROMPT = """Describe this image for a visually impaired reader in one or two sentences.
Be specific about what is shown. If there is text in the image, include it.
Output only the description, nothing else. Output in English."""

PUA_GLYPH_PROMPT = """This image shows a single character/glyph from a Lithuanian linguistics text.
It is likely a Lithuanian letter with a special diacritical mark (acute, tilde, macron, ogonek, etc.).
What Unicode character is this? Reply with ONLY the character itself, nothing else."""

TOC_CONFIRM_PROMPT = """I detected these chapter headings in a Lithuanian book PDF:
{headings}

Based on the heading text and hierarchy, does this look like a correct table of contents?
If any entries seem wrong (e.g., a heading that's actually body text, or a missing chapter),
please correct the list. Output a JSON array of objects with "title" and "level" (1=part, 2=chapter, 3=section).
Output ONLY the JSON array."""

FRONT_MATTER_PROMPT = """These are the first few pages of a Lithuanian book. Classify each:
{pages_text}

For each page, output one of: cover, half_title, title_page, copyright, dedication,
table_of_contents, foreword, preface, body_start, other.
Output as JSON: {{"page_1": "type", "page_2": "type", ...}}
Output ONLY the JSON."""

STRUCTURE_VALIDATION_PROMPT = """These are pages from a Lithuanian book that I believe are chapter title pages.
Confirm which ones are actual chapter starts and provide the chapter title for each.
Output as JSON array: [{{"page": N, "is_chapter": true/false, "title": "..."}}]
Output ONLY the JSON."""
