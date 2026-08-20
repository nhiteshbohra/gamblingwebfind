"""
export_domains/exporter.py — Unified single-command export pipeline.

Generates:
  1. report.pdf (Word .docx -> screen-optimized PDF via COM, intermediate .docx deleted)
  2. report.xlsx (Two sheets: 'Captured Domains' + 'Failed Domains' with clickable hyperlinks)

Pipeline flow:
  1. Query MongoDB for unexported gambling domains.
  2. Verify screenshot on disk (auto-correct DB if file missing).
  3. Copy verified screenshots -> output/<run_id>/screenshots/
  4. Build Word document -> Convert to PDF -> Delete .docx.
  5. Build 2-sheet Excel workbook.
  6. Mark exported=True in MongoDB.
  7. Delete original source screenshots from SCREENSHOT_DIR.
"""
import os
import sys
import shutil
import asyncio
import urllib.parse
from datetime import datetime, timezone, timedelta
from pathlib import Path

# Ensure project root is in sys.path
_ROOT = str(Path(__file__).resolve().parent.parent)
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from xml.sax.saxutils import escape as xml_escape

import docx
from docx.shared import Inches, Pt, RGBColor
from docx.enum.text import WD_ALIGN_PARAGRAPH, WD_TAB_ALIGNMENT
from docx.opc.constants import RELATIONSHIP_TYPE
from docx.oxml import parse_xml
from docx.oxml.ns import nsdecls
import pandas as pd
import openpyxl
from openpyxl.styles import Font
from dotenv import load_dotenv

load_dotenv(dotenv_path=Path(__file__).resolve().parent.parent / ".env")

from export_domains.screenshot import _url_to_filename, is_valid_screenshot, all_filename_candidates
from db.mongo_client import checked_domains, source_domains

IST = timezone(timedelta(hours=5, minutes=30))
HYPERLINK_FONT = Font(color="0000FF", underline="single")


def get_screenshot_dir() -> str:
    return os.getenv("SCREENSHOT_DIR", os.path.join("output", "screenshots"))


def get_output_dir() -> str:
    return os.getenv("EXPORT_OUTPUT_DIR", "output")


# ── Word (.docx) & PDF Generation ─────────────────────────────────────────────

def add_clickable_hyperlink(paragraph, url: str, text: str, font_size_pt: float = 12.0):
    """Inject an active, clickable OpenXML hyperlink run with 12pt blue underlined text."""
    full_url = url if (url.startswith("http://") or url.startswith("https://")) else f"https://{url}"
    part = paragraph.part
    r_id = part.relate_to(full_url, RELATIONSHIP_TYPE.HYPERLINK, is_external=True)

    safe_text = xml_escape(str(text or ""))
    hyperlink = parse_xml(f'<w:hyperlink {nsdecls("w")} {nsdecls("r")} r:id="{r_id}"/>')
    run = parse_xml(f'<w:r {nsdecls("w")}/>')

    rPr = parse_xml(f'<w:rPr {nsdecls("w")}/>')
    rPr.append(parse_xml(f'<w:rFonts {nsdecls("w")} w:ascii="Arial" w:hAnsi="Arial"/>'))
    rPr.append(parse_xml(f'<w:color {nsdecls("w")} w:val="0000FF"/>'))
    rPr.append(parse_xml(f'<w:u {nsdecls("w")} w:val="single"/>'))
    rPr.append(parse_xml(f'<w:sz {nsdecls("w")} w:val="{int(font_size_pt * 2)}"/>'))
    run.append(rPr)
    run.append(parse_xml(f'<w:t {nsdecls("w")} xml:space="preserve">{safe_text}</w:t>'))
    hyperlink.append(run)
    paragraph._p.append(hyperlink)


def _convert_to_pdf(docx_path: str) -> str | None:
    """Convert a .docx file to .pdf using MS Word COM or docx2pdf fallback."""
    abs_docx = os.path.abspath(docx_path)
    abs_pdf = os.path.splitext(abs_docx)[0] + ".pdf"

    if not os.path.exists(abs_docx):
        return None

    # Tier 1: win32com.client (MS Word on-screen optimized)
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

    # Tier 2: comtypes.client fallback
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


def build_report(entries: list[dict], output_path: str) -> str:
    """Build a compact A4 Word document (2 targets per page with clickable URLs)."""
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

        # ponytail: Include screenshot timestamp in report (Date only: YYYY-MM-DD)
        ss_date_raw = str(entry.get("screenshot_date") or entry.get("added_date") or datetime.now(IST).strftime("%Y-%m-%d"))
        ss_date = ss_date_raw.split(" ")[0].split("T")[0]
        p_meta = doc.add_paragraph()
        p_meta.paragraph_format.space_before = Pt(0)
        p_meta.paragraph_format.space_after = Pt(2)
        p_meta.paragraph_format.line_spacing = 1.0
        run_date_lbl = p_meta.add_run("SCREENSHOT DATE: ")
        run_date_lbl.font.size = Pt(8.5)
        run_date_lbl.font.bold = True
        run_date_lbl.font.color.rgb = RGBColor(120, 130, 140)
        run_date_val = p_meta.add_run(ss_date)
        run_date_val.font.size = Pt(8.5)
        run_date_val.font.color.rgb = RGBColor(50, 60, 70)

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


# ── Excel Workbook Generation ─────────────────────────────────────────────────

def _apply_excel_hyperlinks(ws):
    """Apply blue underlined hyperlinks to Domain and URL columns."""
    url_col = domain_col = None
    for idx, cell in enumerate(ws[1], 1):
        col = str(cell.value or "").strip().lower()
        if col == "url":
            url_col = idx
        elif col == "domain":
            domain_col = idx

    for col_idx in filter(None, [url_col, domain_col]):
        for row in range(2, ws.max_row + 1):
            cell = ws.cell(row=row, column=col_idx)
            val = str(cell.value or "").strip()
            if val and not val.startswith("#"):
                target = val if val.startswith(("http://", "https://")) else f"https://{val}"
                try:
                    if urllib.parse.urlparse(target).netloc:
                        cell.hyperlink = target
                        cell.font = HYPERLINK_FONT
                except Exception:
                    pass


def build_workbook(entries: list[dict], failed_docs: list[dict], output_path: str) -> str:
    """Build report.xlsx with 'Captured Domains' sheet (strictly Domain and URL)."""
    os.makedirs(os.path.dirname(os.path.abspath(output_path)), exist_ok=True)

    captured_rows = [
        {
            "S.No.": i,
            "Domain": e.get("domain", ""),
            "URL": e.get("url", ""),
        }
        for i, e in enumerate(entries, 1)
    ]

    with pd.ExcelWriter(output_path, engine="openpyxl") as writer:
        pd.DataFrame(captured_rows).to_excel(writer, sheet_name="Captured Domains", index=False)

    wb = openpyxl.load_workbook(output_path)
    for sheet_name in wb.sheetnames:
        _apply_excel_hyperlinks(wb[sheet_name])
    wb.save(output_path)
    wb.close()
    return output_path


# ── Unified Export Pipeline ───────────────────────────────────────────────────

def _candidates(domain: str, url: str) -> list[str]:
    return all_filename_candidates(url, domain)


def _find_screenshot(domain: str, url: str, src_dir: str) -> str | None:
    for cand in _candidates(domain, url):
        p = os.path.join(src_dir, cand)
        if os.path.exists(p) and is_valid_screenshot(p):
            return p
    return None


def run_export(domain_ids: list = None, limit: int = 0) -> dict:
    """Single-command export: PDF + Excel from all unexported gambling domains."""
    # 1. Query MongoDB
    query = {"status": "gambling", "screenshot_taken": True, "exported": {"$ne": True}}
    if domain_ids:
        query["_id"] = {"$in": domain_ids}

    docs = list(checked_domains().find(query))
    if limit:
        docs = docs[:limit]

    if not docs:
        print("[export] No unexported gambling domains found — nothing to do.")
        return {"run_id": None, "pdf": None, "xlsx": None, "captured": 0, "failed": 0}

    # 2. Verify screenshots on disk
    run_id = datetime.now(IST).strftime("%Y%m%d_%H%M%S")
    dest_dir = os.path.join(get_output_dir(), run_id)
    dest_screenshots = os.path.join(dest_dir, "screenshots")
    os.makedirs(dest_screenshots, exist_ok=True)

    src_dir = get_screenshot_dir()
    entries = []
    failed_docs = []
    missing_ids = []

    for doc in docs:
        domain = doc.get("domain") or doc.get("_id")
        url = doc.get("url") or f"https://{domain}"
        src = _find_screenshot(domain, url, src_dir)

        if src:
            dest = os.path.join(dest_screenshots, os.path.basename(src))
            ss_date = doc.get("screenshot_date") or doc.get("added_date")
            entry_item = {
                "domain": domain,
                "url": url,
                "_id": doc["_id"],
                "_src": src,
                "screenshot_date": ss_date,
                "ip": doc.get("ip", ""),
            }
            try:
                if os.path.exists(dest) and os.path.abspath(src) != os.path.abspath(dest):
                    os.remove(dest)
                shutil.move(src, dest)
                entry_item["screenshot_path"] = dest
                entries.append(entry_item)
            except Exception as e:
                print(f"  [export] Move failed for {domain}: {e} — using source path")
                entry_item["screenshot_path"] = src
                entries.append(entry_item)
        else:
            missing_ids.append(doc["_id"])
            failed_docs.append(doc)

    if missing_ids:
        print(f"  [export] {len(missing_ids)} domain(s) had screenshot missing — marking status as 'unconfirmed'.")
        checked_domains().update_many(
            {"_id": {"$in": missing_ids}},
            {"$set": {
                "status": "unconfirmed",
                "screenshot_taken": False,
                "reason": "Unconfirmed: Screenshot file missing at export time",
                "screenshot_failed_reason": "Screenshot file missing at export time",
                "ai_evaluated": False,
            }},
        )
        source_domains().update_many(
            {"_id": {"$in": missing_ids}},
            {"$set": {"processed": False, "active": True}},
        )

    print(f"[export] Run ID: {run_id} | Verified: {len(entries)} | Failed: {len(failed_docs)}")

    if not entries:
        print("[export] No valid screenshots found. Nothing to export.")
        return {"run_id": run_id, "pdf": None, "xlsx": None, "captured": 0, "failed": len(failed_docs)}

    # 3. Build Word report -> Convert to PDF -> Delete intermediate .docx
    docx_path = os.path.join(dest_dir, "report.docx")
    print(f"[export] Building Word report ({len(entries)} screenshots)...")
    build_report(entries, docx_path)

    print("[export] Converting to PDF...")
    pdf_path = _convert_to_pdf(docx_path)
    if pdf_path and os.path.exists(pdf_path):
        print(f"[export] PDF saved: {pdf_path}")
        try:
            os.remove(docx_path)
        except Exception:
            pass
    else:
        print(f"[export] WARNING: PDF conversion failed — keeping {docx_path}")
        pdf_path = None

    # 4. Build Excel workbook
    xlsx_path = os.path.join(dest_dir, "report.xlsx")
    print(f"[export] Building Excel workbook...")
    build_workbook(entries, failed_docs, xlsx_path)
    print(f"[export] Excel saved: {xlsx_path}")

    # 5. Mark exported=True in MongoDB
    exported_ids = [e["_id"] for e in entries]
    now_ist = datetime.now(IST).strftime("%Y-%m-%d")
    checked_domains().update_many(
        {"_id": {"$in": exported_ids}},
        {"$set": {"exported": True, "exported_at": now_ist}},
    )

    # ponytail: Do NOT delete original screenshots from SCREENSHOT_DIR. Preserve all files on disk.

    print(f"\n[export] Done — output/{run_id}/")
    print(f"           report.pdf  : {pdf_path or 'N/A (conversion failed)'}")
    print(f"           report.xlsx : {xlsx_path}")
    print(f"           Captured    : {len(entries)} | Failed: {len(failed_docs)}")

    return {
        "run_id": run_id,
        "pdf":  pdf_path,
        "xlsx": xlsx_path,
        "captured": len(entries),
        "failed": len(failed_docs),
    }


async def run(concurrency: int = None, limit: int = 0) -> dict:
    """Async entry point — offloads blocking COM PDF export to thread."""
    return await asyncio.to_thread(run_export, None, limit)


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="export_domains exporter")
    parser.add_argument("--limit", type=int, default=int(os.getenv("CAPTURE_LIMIT", 0)))
    args = parser.parse_args()
    asyncio.run(run(limit=args.limit))
