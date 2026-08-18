"""
export_domains/batch_splitter.py — Size & link-bounded batch splitter for reports.
"""
import os
import json
import urllib.parse
from pathlib import Path
from typing import Any
import pymupdf
import pandas as pd
import openpyxl
from openpyxl.styles import Font

PDF_LIMIT_MB = 24.0
MAX_LINKS_PER_BATCH = 1000
SAFE_FACTOR = 0.98
HYPERLINK_FONT = Font(color="0000FF", underline="single")


def _apply_hyperlinks(workbook_path: str):
    """Make Domain and URL cells in an Excel file clickable hyperlinks."""
    try:
        wb = openpyxl.load_workbook(workbook_path)
        for ws in wb.worksheets:
            cols = {str(c.value or "").strip().lower(): idx for idx, c in enumerate(ws[1], 1)}
            target_cols = [cols[k] for k in ("domain", "domain name", "target domain", "url", "target url", "link") if k in cols]
            for col_idx in target_cols:
                for row in range(2, ws.max_row + 1):
                    cell = ws.cell(row=row, column=col_idx)
                    val = str(cell.value or "").strip()
                    if val and not val.startswith("#"):
                        target = val if val.startswith(("http://", "https://")) else f"https://{val}"
                        if urllib.parse.urlparse(target).netloc:
                            cell.hyperlink = target
                            cell.font = HYPERLINK_FONT
        wb.save(workbook_path)
        wb.close()
    except Exception:
        pass


_make_excel_urls_clickable = _apply_hyperlinks


def _load_input_table(file_path: str) -> tuple[list[str], list[list[Any]]]:
    """Load records from an Excel or CSV file via pandas."""
    ext = Path(file_path).suffix.lower()
    df = pd.read_excel(file_path) if ext in (".xlsx", ".xls") else pd.read_csv(file_path)
    df = df.fillna("")
    header = [str(c).strip() for c in df.columns]
    return header, df.values.tolist()


def create_batches(
    csv_path: str = None,
    pdf_path: str = None,
    folder: str = None,
    output_root: str = None,
    limit_mb: float = PDF_LIMIT_MB,
    max_links: int = MAX_LINKS_PER_BATCH,
    include_csv: bool = False,
) -> list[str]:
    """Split Excel/CSV + PDF into size-bounded (<= limit_mb) & link-bounded (<= max_links) batches."""
    if folder and os.path.isdir(folder):
        p = Path(folder)
        pdf_path = pdf_path or next((str(f) for f in p.glob("*.pdf")), None)
        csv_path = csv_path or next((str(f) for f in p.glob("*.xlsx")), None) or next((str(f) for f in p.glob("*.csv")), None)

    if not csv_path or not os.path.exists(csv_path) or not pdf_path or not os.path.exists(pdf_path):
        print(f"[!] Invalid files: table='{csv_path}', pdf='{pdf_path}'")
        return []

    output_root = output_root or os.path.join(os.path.dirname(os.path.abspath(pdf_path)), "Batches")
    header, rows = _load_input_table(csv_path)
    src_doc = pymupdf.open(pdf_path)
    total_pages, total_rows = len(src_doc), len(rows)

    if total_rows == 0 or total_pages == 0:
        src_doc.close()
        return []

    # Read exact row-per-page index from sidecar if present, else fallback
    sidecar_path = os.path.splitext(pdf_path)[0] + "_page_index.json"
    if os.path.exists(sidecar_path):
        with open(sidecar_path, "r", encoding="utf-8") as f:
            counts = json.load(f)
        page_row_ends, cum = [], 0
        for c in counts:
            cum += c
            page_row_ends.append(cum)
        links_per_page = None
    else:
        links_per_page = max(1, round(total_rows / total_pages))
        page_row_ends = [min((p + 1) * links_per_page, total_rows) for p in range(total_pages)]

    max_pages = max(1, max_links // (links_per_page or 2))
    keep_indices = [i for i, col in enumerate(header) if str(col).strip().lower() not in ("exported_at", "exported at")]
    excel_header = [header[i] for i in keep_indices]
    limit_bytes = int(limit_mb * 1024 * 1024 * SAFE_FACTOR)

    os.makedirs(output_root, exist_ok=True)
    batches, start_pg = [], 0

    while start_pg < total_pages:
        low, high = start_pg + 1, min(start_pg + max_pages, total_pages)
        best_end = low

        while low <= high:
            mid = (low + high) // 2
            test_pdf = pymupdf.open()
            test_pdf.insert_pdf(src_doc, from_page=start_pg, to_page=mid - 1)
            buf = test_pdf.tobytes(garbage=4, deflate=True)
            test_pdf.close()

            if len(buf) <= limit_bytes:
                best_end = mid
                low = mid + 1
            else:
                high = mid - 1

        start_row = page_row_ends[start_pg - 1] if start_pg > 0 else 0
        end_row = total_rows if best_end >= total_pages else page_row_ends[best_end - 1]
        batches.append((start_pg, best_end, start_row, end_row))
        start_pg = best_end

    created_folders = []
    for num, (sp, ep, sr, er) in enumerate(batches, 1):
        folder_path = os.path.join(output_root, f"Batch {num}")
        os.makedirs(folder_path, exist_ok=True)
        created_folders.append(folder_path)

        batch_rows = rows[sr:er]
        if include_csv:
            pd.DataFrame(batch_rows, columns=header).to_csv(os.path.join(folder_path, f"batch_{num}.csv"), index=False)

        xlsx_path = os.path.join(folder_path, f"batch_{num}.xlsx")
        pd.DataFrame([[r[i] for i in keep_indices] for r in batch_rows], columns=excel_header).to_excel(xlsx_path, index=False)
        _apply_hyperlinks(xlsx_path)

        batch_pdf = pymupdf.open()
        batch_pdf.insert_pdf(src_doc, from_page=sp, to_page=ep - 1)
        batch_pdf.save(os.path.join(folder_path, f"batch_{num}.pdf"), garbage=4, deflate=True)
        batch_pdf.close()

    src_doc.close()
    print(f"[+] Divided {total_rows} links across {len(batches)} batch(es) in '{output_root}'.")
    return created_folders


def prompt_divide_into_batches():
    """Interactive CLI wizard for dividing reports into batches."""
    print("\n--- Divide into Batches (Split Excel/CSV + PDF) ---")
    raw_table = input("[1/3] Enter path to Excel/CSV file or folder (0 to cancel): ").strip().strip('"').strip("'")
    if raw_table in ("0", "b", "back") or not raw_table:
        return

    if os.path.isdir(raw_table):
        create_batches(folder=raw_table)
        return

    if not os.path.exists(raw_table):
        print(f"[!] File not found: {raw_table}")
        return

    raw_pdf = input("[2/3] Enter path to corresponding PDF report (0 to cancel): ").strip().strip('"').strip("'")
    if raw_pdf in ("0", "b", "back") or not os.path.exists(raw_pdf):
        print(f"[!] PDF not found: {raw_pdf}")
        return

    out_folder = input("[3/3] Enter output folder [press Enter for 'Batches' next to PDF]: ").strip().strip('"').strip("'")
    create_batches(csv_path=raw_table, pdf_path=raw_pdf, output_root=out_folder or None)
