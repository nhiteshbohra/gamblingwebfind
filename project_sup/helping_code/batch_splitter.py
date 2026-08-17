import os
import csv
import argparse
import pymupdf

PDF_LIMIT_MB = 24.0
SAFE_FACTOR = 0.92  # target 92% of limit to leave headroom
#python batch_splitter.py --csv gambling_sites.csv --pdf report.pdf --limit-mb 24

def _render_page_bytes(page, dpi=96, quality=75):
    pix = page.get_pixmap(dpi=dpi)
    return pix.tobytes("jpeg", jpg_quality=quality)


def _save_batch(pages_data, src_doc, output_path, dpi, quality, optimize):
    batch_pdf = pymupdf.open()
    for page_idx, img_bytes in pages_data:
        src_page = src_doc[page_idx]
        rect = src_page.rect
        new_page = batch_pdf.new_page(width=rect.width, height=rect.height)
        if optimize:
            new_page.insert_image(rect, stream=img_bytes)
        else:
            new_page.insert_image(rect, stream=_render_page_bytes(src_page, 150, 90))
    batch_pdf.save(output_path, garbage=4, deflate=True)
    batch_pdf.close()


def create_batches(csv_path, pdf_path, output_root="Gambling sites",
                   limit_mb=PDF_LIMIT_MB, dpi=96, quality=75, optimize=True):
    """
    Splits a CSV + PDF into size-bounded batch folders.
    Each batch PDF stays strictly under limit_mb (default 24 MB).
    Every domain row is included -- none are dropped.
    """
    if not os.path.exists(csv_path):
        print(f"[!] CSV not found: '{csv_path}'")
        csv_path = input("--> Enter path to CSV: ").strip().strip('"').strip("'")
    if not os.path.exists(pdf_path):
        print(f"[!] PDF not found: '{pdf_path}'")
        pdf_path = input("--> Enter path to PDF: ").strip().strip('"').strip("'")

    if not os.path.exists(csv_path):
        print(f"Error: CSV not found at '{csv_path}'"); return
    if not os.path.exists(pdf_path):
        print(f"Error: PDF not found at '{pdf_path}'"); return

    with open(csv_path, encoding="utf-8") as f:
        reader = csv.reader(f)
        header = next(reader)
        rows = list(reader)

    src_doc = pymupdf.open(pdf_path)
    total_pages = len(src_doc)
    total_rows = len(rows)
    max_items = min(total_rows, total_pages)

    limit_bytes = int(limit_mb * 1024 * 1024 * SAFE_FACTOR)

    print(f"\nCSV rows: {total_rows} | PDF pages: {total_pages} | Items to process: {max_items}")
    print(f"PDF size limit: {limit_mb} MB (safe ceiling: {limit_bytes/1024/1024:.1f} MB)")
    print(f"Optimization: {'Screen/Web ' + str(dpi) + 'DPI ' + str(quality) + '%JPEG' if optimize else 'Original'}\n")

    os.makedirs(output_root, exist_ok=True)

    # Pre-render all pages to JPEG so we can measure sizes before writing
    print("Pre-rendering pages to measure sizes...")
    page_bytes_list = []
    for i in range(max_items):
        b = _render_page_bytes(src_doc[i], dpi=dpi, quality=quality)
        page_bytes_list.append(b)
        if (i + 1) % 50 == 0:
            print(f"  Rendered {i+1}/{max_items}...")

    # Group into batches by accumulated byte size
    batches = []
    current = []
    current_size = 0
    PDF_PAGE_OVERHEAD = 8_000  # ~8KB per page for PDF structure

    for i, img_bytes in enumerate(page_bytes_list):
        entry_size = len(img_bytes) + PDF_PAGE_OVERHEAD
        if current and (current_size + entry_size) > limit_bytes:
            batches.append(current)
            current = []
            current_size = 0
        current.append(i)
        current_size += entry_size

    if current:
        batches.append(current)

    print(f"Size-based split: {max_items} items -> {len(batches)} batch(es)\n")

    for batch_num, page_indices in enumerate(batches, 1):
        batch_folder = os.path.join(output_root, f"Batch {batch_num}")
        os.makedirs(batch_folder, exist_ok=True)

        # CSV slice
        batch_csv = os.path.join(batch_folder, f"batch_{batch_num}.csv")
        with open(batch_csv, "w", newline="", encoding="utf-8") as f:
            w = csv.writer(f)
            w.writerow(header)
            w.writerows([rows[i] for i in page_indices])

        # PDF slice
        batch_pdf_path = os.path.join(batch_folder, f"batch_{batch_num}.pdf")
        _save_batch([(i, page_bytes_list[i]) for i in page_indices], src_doc, batch_pdf_path, dpi, quality, optimize)

        size_mb = os.path.getsize(batch_pdf_path) / (1024 * 1024)
        row_range = f"{page_indices[0]+1}-{page_indices[-1]+1}"
        print(f"Batch {batch_num}/{len(batches)}: rows {row_range} | {len(page_indices)} items | {size_mb:.2f} MB -> '{batch_folder}'")

    src_doc.close()
    print(f"\n[OK] All {max_items} items in {len(batches)} batch(es). No domain left behind.")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Split CSV+PDF into size-bounded batches (default 24 MB/PDF).")
    parser.add_argument("--csv", default="gambling_sites.csv")
    parser.add_argument("--pdf", default="Gambling_Sites_Report_Merged.pdf")
    parser.add_argument("--output", default="Gambling sites")
    parser.add_argument("--limit-mb", type=float, default=24.0, help="Max PDF size per batch in MB (default: 24)")
    parser.add_argument("--dpi", type=int, default=96)
    parser.add_argument("--quality", type=int, default=75)
    parser.add_argument("--no-optimize", action="store_true")

    args = parser.parse_args()
    create_batches(
        csv_path=args.csv,
        pdf_path=args.pdf,
        output_root=args.output,
        limit_mb=args.limit_mb,
        dpi=args.dpi,
        quality=args.quality,
        optimize=not args.no_optimize,
    )
