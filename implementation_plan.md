# Implementation Plan — Compact Clickable URL Word Report Generator

Modify and enhance the URL Screenshot to Word Report Generator to produce compact, professional, highly-dense Word (`.docx`) documents with active hyperlinks, optimized JPEG screenshots, and strict A4 2-target-per-page layouts capable of processing up to 10,000+ URLs efficiently.

## Proposed Requirements Compliance Summary

| Requirement | Proposed Specification |
| :--- | :--- |
| **Page Layout** | A4 Portrait, Margins: `0.4 in` (Top, Bottom, Left, Right) |
| **Density** | Exactly **2 targets per page**, page break every 2 targets |
| **Typography** | Font: Arial / Aptos; Target Header: `10–11 pt bold`; URL Label: `8–9 pt` |
| **Spacing** | Target Para: space_before=0, space_after=1-2 pt; URL Para: space_before=0, space_after=2-3 pt |
| **Hyperlinks** | OpenXML `<w:hyperlink>` helper for full URL, clickable (Ctrl+Click), retained on PDF export |
| **Screenshot Capture** | Playwright headless browser at `1280 × 720` resolution (16:9 ratio) |
| **Screenshot Compression** | Pillow JPEG compression at **Quality 65–70**, in-memory `io.BytesIO` buffer |
| **Screenshot Display Size** | Width: `7.0 - 7.3 in` (auto-fit within printable 7.47 in width), Height: `~3.94 - 4.1 in` |
| **Storage & Memory** | Zero permanent screenshot files on disk (temporary buffer / cleanup); batch streaming |
| **Reliability & Resume** | Exception handling per URL (failed URL doesn't crash batch); state tracking for resume |

---

## Technical Architecture & Proposed Implementation

### 1. Requirements Update (`requirements.txt`)
Add missing Python dependencies for document synthesis and webpage screenshot capture:
- `python-docx` for document structure and OpenXML manipulation
- `playwright` for headless browser page rendering at 1280x720
- `Pillow` (PIL) for image compression and JPEG conversion

### 2. High-Performance Screenshot Capturer (`core/screenshot_capturer.py`)
- Async screenshot generator utilizing Playwright `async_api`.
- Sets viewport size strictly to `1280 x 720`.
- Captures page screenshot into memory bytes.
- Passes raw image bytes to Pillow:
  - Converts image to `RGB` mode.
  - Compresses to `JPEG` with `quality=68`, `optimize=True`.
  - Maintains strict 16:9 aspect ratio without distortion.
  - Returns `io.BytesIO` buffer for direct insertion into `python-docx` (with temp file fallback if needed).
- Includes retry & timeout mechanics so failed/blocked URLs produce a clean placeholder error banner or skipped target without crashing the execution loop.

### 3. Word Report Generator Engine (`storage/docx_report_generator.py`)
- Initializes `docx.Document()`.
- **Page Setup**:
  - Page width = 8.27 in (A4), Height = 11.69 in (A4).
  - Top, Bottom, Left, Right margins = `0.4 in`.
  - Printable width = `7.47 in`, Printable height = `10.89 in`.
- **Hyperlink Helper**:
  - Injects OpenXML `<w:hyperlink>` element with relationship ID bound to the target URL.
  - Configures run style (`w:rPr`): Arial, `8.5 pt`, `w:color="0000FF"`, `w:u="single"`.
  - Displays full URL with natural line wrapping.
- **Target Layout Builder**:
  - **Header Paragraph**: `TARGET #{index:05d}` in Arial 10.5pt Bold. `space_before=0`, `space_after=2pt`.
  - **URL Paragraph**: `URL: ` (plain text) + Clickable Hyperlink (`w:hyperlink`). `space_before=0`, `space_after=3pt`.
  - **Image Paragraph**: Insets screenshot with `width=Inches(7.1)` (~3.99 in height at 16:9). `space_before=0`, `space_after=6pt`.
  - **Page Break Logic**: Adds `doc.add_page_break()` after every 2 targets (or sets `page_break_before` on odd targets past #1).
- **Streaming & Memory Efficiency**:
  - Flushes memory continuously without keeping raw screenshot objects in RAM.
  - Supports incremental output saving (`doc.save()`) every N targets to protect against process interrupts.

### 4. CLI & Pipeline Integration (`cli/main.py`)
- Add CLI command `generate-docx-report`:
  - Flags: `--input` (CSV or DB query), `--output` (path to .docx report), `--batch-size`, `--resume`.
  - Supports batch processing 10,000 URLs with resume capability via state tracking file (`docx_progress.json`).

---

## Proposed File Changes

### [MODIFY] [requirements.txt](file:///f:/projects/gamblingwebfindtest1/requirements.txt)
- Add `python-docx>=1.1.0`, `playwright>=1.40.0`, `Pillow>=10.0.0`.

### [NEW] [screenshot_capturer.py](file:///f:/projects/gamblingwebfindtest1/core/screenshot_capturer.py)
- Module to handle Playwright viewport capture (`1280x720`) and Pillow JPEG compression (`quality=68`).

### [NEW] [docx_report_generator.py](file:///f:/projects/gamblingwebfindtest1/storage/docx_report_generator.py)
- Module containing A4 layout definition, OpenXML hyperlink generator, 2-target-per-page formatter, and stream writer.

### [MODIFY] [main.py](file:///f:/projects/gamblingwebfindtest1/cli/main.py)
- Add `generate-docx-report` command line entry point connecting database/CSV source, Playwright capturer, and Word generator.

---

## Verification Plan

### Automated Tests
1. **Hyperlink XML Verification**:
   - Run a test script to generate a sample 4-target report and inspect the underlying `word/document.xml` and `word/_rels/document.xml.rels` to verify `<w:hyperlink>` tags and external relationship targets.
2. **Page Count & Target Density Test**:
   - Generate document for N=10 targets and verify page count is exactly 5 pages.
3. **Image Compression & Resolution Test**:
   - Verify generated JPEG buffers are derived from 1280x720 capture and have quality ~68 (resulting in compact image size ~40-70 KB per screenshot).
4. **Memory / RAM Usage Benchmark**:
   - Run test with 50 URLs and monitor Python process RAM to ensure memory footprint remains flat.

### Manual Verification
1. Open generated `.docx` in Microsoft Word:
   - Confirm 2 targets fit comfortably on each A4 page without spilling into extra pages.
   - Confirm Ctrl + Click on URLs opens the web page.
2. Export / Save as PDF from Word:
   - Verify URLs remain clickable in the generated PDF document.
