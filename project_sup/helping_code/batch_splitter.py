import os
import csv
import argparse
import pymupdf


def create_batches(csv_path, pdf_path, output_root="Gambling sites", batch_size=20, dpi=96, quality=75, optimize=True):
    """
    Splits a CSV file and a matching PDF file into batch folders of `batch_size`.
    Each batch folder gets a CSV slice with header and a PDF slice with corresponding pages.
    """
    # Interactive fallback if files do not exist
    if not os.path.exists(csv_path):
        print(f"[!] CSV file not found at default: '{csv_path}'")
        user_csv = input("--> Enter path to source CSV file: ").strip().strip('"').strip("'")
        if user_csv:
            csv_path = user_csv

    if not os.path.exists(pdf_path):
        print(f"[!] PDF file not found at default: '{pdf_path}'")
        user_pdf = input("--> Enter path to source PDF file: ").strip().strip('"').strip("'")
        if user_pdf:
            pdf_path = user_pdf

    if not os.path.exists(csv_path):
        print(f"Error: CSV file not found at '{csv_path}'")
        return
    if not os.path.exists(pdf_path):
        print(f"Error: PDF file not found at '{pdf_path}'")
        return

    # Read CSV rows
    with open(csv_path, mode="r", encoding="utf-8") as f:
        reader = csv.reader(f)
        header = next(reader)
        rows = list(reader)

    # Open source PDF
    src_doc = pymupdf.open(pdf_path)
    total_pages = len(src_doc)
    total_rows = len(rows)

    print(f"\nCSV Total Rows: {total_rows}")
    print(f"PDF Total Pages: {total_pages}")

    max_items = min(total_rows, total_pages)
    total_batches = (max_items + batch_size - 1) // batch_size

    print(f"Processing {max_items} items into {total_batches} batches of up to {batch_size} items each...")
    print(f"Optimization mode: {'Screen/Web Quality (' + str(dpi) + ' DPI, ' + str(quality) + '% JPEG Quality)' if optimize else 'Original Quality'}\n")

    os.makedirs(output_root, exist_ok=True)

    for batch_num in range(1, total_batches + 1):
        start_idx = (batch_num - 1) * batch_size
        end_idx = min(start_idx + batch_size, max_items)

        batch_folder = os.path.join(output_root, f"Batch {batch_num}")
        os.makedirs(batch_folder, exist_ok=True)

        batch_rows = rows[start_idx:end_idx]

        # 1. Save CSV batch
        batch_csv_path = os.path.join(batch_folder, f"batch_{batch_num}.csv")
        with open(batch_csv_path, mode="w", newline="", encoding="utf-8") as f_out:
            writer = csv.writer(f_out)
            writer.writerow(header)
            writer.writerows(batch_rows)

        # 2. Save PDF batch (Screen/Web quality optimized)
        batch_pdf = pymupdf.open()

        for page_idx in range(start_idx, end_idx):
            src_page = src_doc[page_idx]

            if optimize:
                pix = src_page.get_pixmap(dpi=dpi)
                img_bytes = pix.tobytes("jpeg", jpg_quality=quality)
                rect = src_page.rect
                new_page = batch_pdf.new_page(width=rect.width, height=rect.height)
                new_page.insert_image(rect, stream=img_bytes)
            else:
                batch_pdf.insert_pdf(src_doc, from_page=page_idx, to_page=page_idx)

        batch_pdf_path = os.path.join(batch_folder, f"batch_{batch_num}.pdf")
        batch_pdf.save(batch_pdf_path, garbage=4, deflate=True)
        batch_pdf.close()

        size_mb = os.path.getsize(batch_pdf_path) / (1024 * 1024)
        print(f"Created Batch {batch_num}/{total_batches}: Rows {start_idx+1} to {end_idx} in '{batch_folder}' ({size_mb:.2f} MB)")

    src_doc.close()
    print("\n[OK] All batches created successfully!")



if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Split CSV and PDF into batches with screen/web quality optimization.")
    parser.add_argument("--csv", default="gambling_sites.csv", help="Path to input CSV file")
    parser.add_argument("--pdf", default="Gambling_Sites_Report_Merged.pdf", help="Path to input PDF file")
    parser.add_argument("--output", default="Gambling sites", help="Root folder for output batches")
    parser.add_argument("--size", type=int, default=20, help="Batch size (default: 20)")
    parser.add_argument("--dpi", type=int, default=96, help="Screen DPI resolution (default: 96 for wdExportOptimizeForOnScreen)")
    parser.add_argument("--quality", type=int, default=75, help="JPEG compression quality 1-100 (default: 75)")
    parser.add_argument("--no-optimize", action="store_true", help="Disable screen optimization and keep original quality")

    args = parser.parse_args()
    create_batches(
        csv_path=args.csv,
        pdf_path=args.pdf,
        output_root=args.output,
        batch_size=args.size,
        dpi=args.dpi,
        quality=args.quality,
        optimize=not args.no_optimize
    )

