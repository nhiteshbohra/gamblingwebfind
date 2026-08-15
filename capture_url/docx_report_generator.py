"""
capture_url/docx_report_generator.py — Build A4 Word document from screenshot entries.

Logic unchanged — takes only URL + in-memory image paths, never touches the database.
"""
import os
import docx
from docx.shared import Inches, Pt, RGBColor
from docx.enum.text import WD_ALIGN_PARAGRAPH, WD_TAB_ALIGNMENT
from docx.oxml import parse_xml
from docx.oxml.ns import nsdecls


def _convert_to_pdf(docx_path: str) -> str | None:
    """Convert a .docx file to .pdf using MS Word COM with screen/web optimisation.

    Uses wdExportOptimizeForOnScreen (OptimizeFor=1) — lower DPI, smaller file.
    Tries win32com -> comtypes -> docx2pdf.
    """
    abs_docx = os.path.abspath(docx_path)
    abs_pdf = os.path.splitext(abs_docx)[0] + ".pdf"

    if not os.path.exists(abs_docx):
        return None

    # Tier 1: win32com.client
    try:
        import win32com.client as _win32
        word = _win32.Dispatch("Word.Application")
        word.Visible = False
        try:
            doc = word.Documents.Open(abs_docx)
            doc.ExportAsFixedFormat(
                OutputFileName=abs_pdf,
                ExportFormat=17,       # wdExportFormatPDF
                OpenAfterExport=False,
                OptimizeFor=1,         # wdExportOptimizeForOnScreen — smaller file
                Range=0,               # wdExportAllDocument
                Item=0,                # wdExportDocumentContent
                IncludeDocProps=True,
                KeepIRM=True,
                CreateBookmarks=0,     # wdExportCreateNoBookmarks
                DocStructureTags=True,
                BitmapMissingFonts=True,
                UseISO19005_1=False,
            )
            doc.Close(False)
        finally:
            word.Quit()

        if os.path.exists(abs_pdf):
            return abs_pdf
    except Exception:
        pass

    # Tier 2: comtypes.client (pure ctypes fallback for Python 3.12 DLL compatibility)
    try:
        import comtypes.client as _comtypes
        word = _comtypes.CreateObject("Word.Application")
        word.Visible = False
        try:
            doc = word.Documents.Open(abs_docx)
            doc.ExportAsFixedFormat(
                OutputFileName=abs_pdf,
                ExportFormat=17,
                OpenAfterExport=False,
                OptimizeFor=1,
                Range=0,
                Item=0,
                IncludeDocProps=True,
                KeepIRM=True,
                CreateBookmarks=0,
                DocStructureTags=True,
                BitmapMissingFonts=True,
                UseISO19005_1=False,
            )
            doc.Close(False)
        finally:
            word.Quit()

        if os.path.exists(abs_pdf):
            return abs_pdf
    except Exception:
        pass

    # Tier 3: docx2pdf fallback
    try:
        import docx2pdf
        docx2pdf.convert(abs_docx, abs_pdf)
        if os.path.exists(abs_pdf):
            return abs_pdf
    except Exception as e:
        print(f"[pdf] PDF conversion error for {docx_path}: {e}")

    return None


def add_clickable_hyperlink(paragraph, url: str, text: str, font_size_pt=12.0):
    """Inject an active, clickable OpenXML hyperlink run with 12pt blue underlined text."""
    full_url = url if (url.startswith("http://") or url.startswith("https://")) else f"https://{url}"
    part = paragraph.part
    r_id = part.relate_to(full_url, docx.opc.constants.RELATIONSHIP_TYPE.HYPERLINK, is_external=True)

    hyperlink = parse_xml(f'<w:hyperlink {nsdecls("w")} {nsdecls("r")} r:id="{r_id}"/>')
    run = parse_xml(f'<w:r {nsdecls("w")}/>')

    rPr = parse_xml(f'<w:rPr {nsdecls("w")}/>')
    rPr.append(parse_xml(f'<w:rFonts {nsdecls("w")} w:ascii="Arial" w:hAnsi="Arial"/>'))
    rPr.append(parse_xml(f'<w:color {nsdecls("w")} w:val="0000FF"/>'))
    rPr.append(parse_xml(f'<w:u {nsdecls("w")} w:val="single"/>'))
    rPr.append(parse_xml(f'<w:sz {nsdecls("w")} w:val="{int(font_size_pt * 2)}"/>'))
    run.append(rPr)
    run.append(parse_xml(f'<w:t {nsdecls("w")}>{text}</w:t>'))
    hyperlink.append(run)
    paragraph._p.append(hyperlink)



def build_report(entries: list[dict], output_path: str) -> str:
    """Build a compact A4 Word document.

    entries: [{"url": ..., "screenshot_path": ...}, ...]
    2 targets per page with TARGET #NNNNN header and clickable URL.
    """
    doc = docx.Document()
    for section in doc.sections:
        section.page_width = Inches(8.27)
        section.page_height = Inches(11.69)
        for attr in ('top_margin', 'bottom_margin', 'left_margin', 'right_margin'):
            setattr(section, attr, Inches(0.4))

    style = doc.styles['Normal']
    style.font.name = 'Arial'
    style.font.size = Pt(10)
    style.font.color.rgb = RGBColor(30, 40, 55)

    target_count = 0
    for entry in entries:
        url = entry.get('url', '')
        path = entry.get('screenshot_path')
        if not path or not os.path.exists(path):
            continue

        target_count += 1
        p_hdr = doc.add_paragraph()
        p_hdr.paragraph_format.space_before = Pt(4)
        p_hdr.paragraph_format.space_after = Pt(2)
        p_hdr.paragraph_format.line_spacing = 1.0
        p_hdr.paragraph_format.tab_stops.add_tab_stop(Inches(7.1), WD_TAB_ALIGNMENT.RIGHT)

        run_hdr = p_hdr.add_run(f"TARGET #{target_count:05d}")
        run_hdr.font.bold = True
        run_hdr.font.size = Pt(12.0)
        run_hdr.font.color.rgb = RGBColor(30, 40, 55)

        run_tab = p_hdr.add_run("\tURL: ")
        run_tab.font.size = Pt(12.0)
        run_tab.font.bold = True
        run_tab.font.color.rgb = RGBColor(100, 110, 120)

        add_clickable_hyperlink(p_hdr, url=url, text=url, font_size_pt=12.0)

        p_img = doc.add_paragraph()
        p_img.alignment = WD_ALIGN_PARAGRAPH.CENTER
        p_img.paragraph_format.space_before = Pt(2)
        p_img.paragraph_format.space_after = Pt(6)
        p_img.paragraph_format.line_spacing = 1.0
        p_img.add_run().add_picture(path, width=Inches(7.1))

        if target_count % 2 == 0:
            doc.add_page_break()

    os.makedirs(os.path.dirname(os.path.abspath(output_path)), exist_ok=True)
    doc.save(output_path)
    return output_path


def build_report_from_mongo(
    domain_ids: list = None,
    output_dir: str = "output",
    batch_size: int = 40,
    cleanup: bool = True,
    pdf: bool = True,
    single_file: bool = False,
    combined: bool = False,
) -> dict:
    """Build Word & PDF reports from successfully captured domains in MongoDB.

    Supports:
      - Single file mode (single_file=True or batch_size=0): 1 combined Word & PDF report.
      - Batch mode (batch_size > 0): splits into batch_001, batch_002 folders.
      - Both mode (combined=True): writes batches AND master combined Word & PDF report.
    """
    if domain_ids is not None and len(domain_ids) == 0:
        print("[report] No domains processed in this run to generate Word report.")
        return {"docx_paths": [], "pdf_paths": []}

    from db.mongo_client import checked_domains
    from capture_url.screenshot import _url_to_filename

    query = {"screenshot_taken": True}
    if domain_ids:
        query["_id"] = {"$in": domain_ids}

    captured = list(checked_domains().find(query))
    screenshots_dir = os.path.join("output", "screenshots")

    # Only include entries where the JPEG is actually on disk
    entries = []
    missing_ids = []
    for d in captured:
        jpg_path = os.path.join(screenshots_dir, _url_to_filename(d["url"]))
        if os.path.exists(jpg_path):
            entries.append({"url": d["url"], "screenshot_path": jpg_path})
        else:
            missing_ids.append(d["_id"])

    if missing_ids:
        print(f"[report] Warning: {len(missing_ids)} domains marked captured but JPEG not found on disk. Skipping.")
        checked_domains().update_many({"_id": {"$in": missing_ids}}, {"$set": {"screenshot_taken": False}})

    if not entries:
        print("[report] No valid screenshot entries found. No Word docs generated.")
        return {"docx_paths": [], "pdf_paths": []}

    docx_paths = []
    pdf_paths  = []

    # ── 1. Single Combined Master Report ──────────────────────────────────────
    if single_file or batch_size <= 0 or combined:
        master_docx = os.path.join(output_dir, "capture_report_combined.docx")
        print(f"[report] Writing single combined Word report: {len(entries)} screenshots -> {master_docx}")
        build_report(entries, master_docx)
        docx_paths.append(master_docx)

        if pdf:
            print("[pdf]    Converting combined report to PDF...")
            master_pdf = _convert_to_pdf(master_docx)
            if master_pdf:
                pdf_paths.append(master_pdf)
                print(f"[pdf]    Saved: {master_pdf}")

    # ── 2. Batched Reports ───────────────────────────────────────────────────
    if not single_file and batch_size > 0:
        batches = [entries[i:i + batch_size] for i in range(0, len(entries), batch_size)]
        total_batches = len(batches)
        print(f"[report] {len(entries)} screenshots -> {total_batches} batch(es) of up to {batch_size} each.")

        for batch_num, batch_entries in enumerate(batches, 1):
            batch_label = f"batch_{batch_num:03d}"
            batch_dir = os.path.join(output_dir, batch_label)
            os.makedirs(batch_dir, exist_ok=True)

            docx_path = os.path.join(batch_dir, f"{batch_label}_report.docx")
            print(f"[report] Writing {batch_label}: {len(batch_entries)} screenshots -> {docx_path}")
            build_report(batch_entries, docx_path)
            docx_paths.append(docx_path)

            if pdf:
                print(f"[pdf]    Converting {batch_label}_report.docx -> .pdf ...")
                pdf_path = _convert_to_pdf(docx_path)
                if pdf_path:
                    pdf_paths.append(pdf_path)
                    print(f"[pdf]    Saved: {pdf_path}")

    # Clean up all temp JPEGs only after all documents are written
    if cleanup:
        cleaned_count = 0
        for entry in entries:
            path = entry.get("screenshot_path")
            if path and os.path.exists(path):
                try:
                    os.remove(path)
                    cleaned_count += 1
                except Exception:
                    pass
        print(f"[cleanup] Auto-deleted {cleaned_count:,} temporary screenshot JPEG files from disk.")

    print(f"[report] Done. {len(docx_paths)} Word document(s) + {len(pdf_paths)} PDF(s) saved in: {output_dir}")
    return {"docx_paths": docx_paths, "pdf_paths": pdf_paths}

