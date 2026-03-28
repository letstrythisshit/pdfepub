class CSSBuilder:
    """Generate EPUB CSS stylesheet following certified example patterns."""

    def build(self) -> str:
        return '''/* PDF-to-EPUB3 Generated Styles */
/* Following WCAG 2.2 AA certified example patterns */

@page {
  margin: 0;
}

body {
  font-family: "Georgia", "Times New Roman", serif;
  font-size: 1em;
  line-height: 1.4;
  margin: 1em;
  -epub-hyphens: auto;
  hyphens: auto;
  orphans: 2;
  widows: 2;
}

/* Chapter headings */
h1 {
  font-size: 2em;
  font-weight: bold;
  text-align: center;
  margin: 2em 0 1em 0;
  line-height: 1.2;
  page-break-before: always;
}

h2 {
  font-size: 1.8em;
  font-weight: bold;
  text-align: center;
  margin: 1.5em 0 1em 0;
  line-height: 1.2;
  page-break-before: always;
}

h3 {
  font-size: 1.4em;
  font-weight: bold;
  text-align: left;
  margin: 1.2em 0 0.8em 0;
  line-height: 1.2;
}

h4 {
  font-size: 1.2em;
  font-weight: bold;
  text-align: left;
  margin: 1em 0 0.6em 0;
  line-height: 1.2;
}

/* First paragraph after heading - no indent */
p.txt-p {
  font-size: 1em;
  line-height: 1.4;
  text-align: left;
  text-indent: 0;
  margin: 0.2em 0;
}

/* Normal paragraph - indented */
p.txt {
  font-size: 1em;
  line-height: 1.4;
  text-align: left;
  text-indent: 1em;
  margin: 0;
}

/* Page break markers - invisible */
span[epub\\:type="pagebreak"] {
  visibility: hidden;
  height: 0;
  overflow: hidden;
  display: block;
}

[role="doc-pagebreak"] {
  visibility: hidden;
  height: 0;
  overflow: hidden;
  display: block;
}

/* Footnote references */
a[epub\\:type="noteref"] {
  text-decoration: none;
  vertical-align: super;
  font-size: 0.75em;
  line-height: 0;
}

a[role="doc-noteref"] sup {
  font-size: inherit;
  vertical-align: inherit;
}

/* Footnote section */
aside[epub\\:type="footnotes"] {
  margin-top: 2em;
  border-top: 1px solid #ccc;
  padding-top: 0.5em;
  font-size: 0.85em;
}

aside[epub\\:type="footnote"] {
  margin: 0.5em 0;
}

aside[epub\\:type="footnote"] p {
  text-indent: 0;
  margin: 0.2em 0;
}

/* Backlink from footnote */
a[role="doc-backlink"] {
  text-decoration: none;
  font-weight: bold;
}

/* Cover image */
figure[epub\\:type="cover"] {
  text-align: center;
  margin: 0;
  padding: 0;
}

figure[epub\\:type="cover"] img {
  max-width: 100%;
  max-height: 95vh;
  object-fit: contain;
}

/* Section styling */
section[epub\\:type="chapter"] {
  margin: 0;
  padding: 0;
}

section[epub\\:type="titlepage"],
section[epub\\:type="halftitlepage"] {
  text-align: center;
  margin-top: 3em;
}

section[epub\\:type="copyright-page"] {
  font-size: 0.85em;
  margin-top: 2em;
}

/* Images */
img {
  max-width: 100%;
  height: auto;
}

figure {
  margin: 1em 0;
  text-align: center;
}

figcaption {
  font-size: 0.9em;
  font-style: italic;
  margin-top: 0.5em;
}

/* Navigation (toc.xhtml) */
nav[epub\\:type="toc"] ol {
  list-style-type: none;
  padding-left: 0;
}

nav[epub\\:type="toc"] ol ol {
  padding-left: 1.5em;
}

nav[epub\\:type="toc"] a {
  text-decoration: none;
  color: inherit;
}

nav[epub\\:type="toc"] li {
  margin: 0.3em 0;
}
'''
