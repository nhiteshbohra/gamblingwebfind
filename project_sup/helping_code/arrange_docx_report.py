#!/usr/bin/env python3
"""
arrange_docx_report.py

A tool to match Target numbers and URLs between an Excel/CSV file and a Word document (.docx),
and rearrange the report so that exactly 2 Links (Target metadata) and 2 Images (Screenshots)
are placed neatly on each page.

Supports batching into smaller files (e.g. 1000 targets per file) so Word can open them fast!

Usage:
    # Batch output (recommended for large reports)
    python arrange_docx_report.py --batch-size 1000

    # Single output file
    python arrange_docx_report.py --single-file
"""

import argparse
import csv
from io import BytesIO
import os
import re
import sys
import time
import xml.etree.ElementTree as ET
import zipfile

try:
    if sys.stdout and hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

try:
    import docx
    from docx import Document
    from docx.shared import Inches, Pt, RGBColor
    from docx.enum.text import WD_ALIGN_PARAGRAPH
    from docx.oxml import parse_xml
    from docx.oxml.ns import nsdecls
except ImportError:
    print("Error: 'python-docx' library is missing. Install it using: pip install python-docx")
    sys.exit(1)


def add_clickable_hyperlink(paragraph, url: str, text: str, font_size_pt=11.0):
    """Inject an active, clickable OpenXML hyperlink run into Word paragraph."""
    full_url = url if (url.startswith("http://") or url.startswith("https://")) else f"https://{url}"
    part = paragraph.part
    r_id = part.relate_to(full_url, docx.opc.constants.RELATIONSHIP_TYPE.HYPERLINK, is_external=True)

    hyperlink = parse_xml(f'<w:hyperlink {nsdecls("w")} {nsdecls("r")} r:id="{r_id}"/>')
    run = parse_xml(f'<w:r {nsdecls("w")}/>')

    rPr = parse_xml(f'<w:rPr {nsdecls("w")}/>')
    rPr.append(parse_xml(f'<w:rFonts {nsdecls("w")} w:ascii="Calibri" w:hAnsi="Calibri"/>'))
    rPr.append(parse_xml(f'<w:color {nsdecls("w")} w:val="0066CC"/>'))
    rPr.append(parse_xml(f'<w:u {nsdecls("w")} w:val="single"/>'))
    rPr.append(parse_xml(f'<w:sz {nsdecls("w")} w:val="{int(font_size_pt * 2)}"/>'))
    run.append(rPr)
    run.append(parse_xml(f'<w:t {nsdecls("w")}>{text}</w:t>'))
    hyperlink.append(run)
    paragraph._p.append(hyperlink)


def clean_domain(url_or_domain: str) -> str:
    """Normalize a domain or URL for clean matching."""
    if not url_or_domain:
        return ""
    text = url_or_domain.strip().lower()
    text = re.sub(r"^https?://", "", text)
    text = text.split("/")[0].split("?")[0].split(":")[0]
    return text.strip()


def parse_csv(csv_path: str) -> list[dict]:
    """Parse the input Excel/CSV file containing Sno, domain, url."""
    rows = []
    print(f"📄 Reading CSV file: {csv_path}")
    with open(csv_path, "r", encoding="utf-8", errors="ignore") as f:
        reader = csv.DictReader(f)
        for row in reader:
            sno = row.get("Sno", "").strip()
            domain = row.get("domain", "").strip()
            url = row.get("url", "").strip()
            if not url and domain:
                url = f"https://{domain}"
            if domain or url:
                rows.append({
                    "sno": int(sno) if sno.isdigit() else len(rows) + 1,
                    "raw_sno": sno,
                    "domain": domain,
                    "url": url,
                    "clean_domain": clean_domain(domain or url)
                })
    print(f"✅ Extracted {len(rows)} targets from CSV.")
    return rows


def parse_docx_media(docx_path: str) -> tuple[zipfile.ZipFile, list[dict]]:
    """
    Directly parse docx zip archive for XML elements and images.
    Returns zip object and list of target objects with text, target #, url, and image path inside zip.
    """
    print(f"📦 Indexing DOCX archive: {docx_path}")
    if not os.path.exists(docx_path):
        raise FileNotFoundError(f"DOCX file not found: {docx_path}")

    z = zipfile.ZipFile(docx_path, "r")
    doc_xml = z.read("word/document.xml")
    rels_xml = z.read("word/_rels/document.xml.rels")

    rels_root = ET.fromstring(rels_xml)
    rel_map = {rel.attrib["Id"]: rel.attrib["Target"] for rel in rels_root}

    root = ET.fromstring(doc_xml)
    w_ns = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"
    a_ns = "{http://schemas.openxmlformats.org/drawingml/2006/main}"
    r_ns = "{http://schemas.openxmlformats.org/officeDocument/2006/relationships}"

    docx_targets = []
    current_target = None

    for p in root.iter(f"{w_ns}p"):
        text = "".join(p.itertext()).strip()
        blips = p.findall(f".//{a_ns}blip")
        img_targets = [
            rel_map.get(b.attrib.get(f"{r_ns}embed"))
            for b in blips
            if b.attrib.get(f"{r_ns}embed") in rel_map
        ]

        m = re.search(r"TARGET #(\d+)URL:\s*(.*)", text, re.IGNORECASE)
        if not m:
            m = re.search(r"TARGET #(\d+)\s*URL:\s*(.*)", text, re.IGNORECASE)

        if m:
            t_num = int(m.group(1))
            t_url = m.group(2).strip()
            current_target = {
                "docx_sno": t_num,
                "url": t_url,
                "clean_domain": clean_domain(t_url),
                "image_rel": None,
            }
            docx_targets.append(current_target)
        elif img_targets and current_target:
            img_path = "word/" + img_targets[0].lstrip("word/")
            current_target["image_rel"] = img_path

    print(f"✅ Extracted {len(docx_targets)} target blocks and images from DOCX.")
    return z, docx_targets


def match_csv_to_docx(csv_rows: list[dict], docx_targets: list[dict]) -> list[dict]:
    """
    Match Excel/CSV targets with DOCX screenshot images using domain / URL index.
    """
    print("🔍 Matching CSV target entries to DOCX images...")
    docx_by_domain = {}
    for dt in docx_targets:
        cd = dt["clean_domain"]
        if cd and cd not in docx_by_domain:
            docx_by_domain[cd] = dt

    matched_list = []
    unmatched_count = 0

    for r in csv_rows:
        cd = r["clean_domain"]
        dt = docx_by_domain.get(cd)

        matched_item = {
            "sno": r["sno"],
            "domain": r["domain"],
            "url": r["url"],
            "image_rel": dt["image_rel"] if dt else None
        }
        if not dt:
            unmatched_count += 1

        matched_list.append(matched_item)

    print(f"✅ Matching complete: {len(matched_list) - unmatched_count} matched, {unmatched_count} unmatched.")
    return matched_list


def create_single_docx_file(
    z_archive: zipfile.ZipFile,
    items: list[dict],
    output_path: str,
    items_per_page: int = 2,
    img_width_inches: float = 6.6
):
    """Generate a single Word document arranging specified items per page."""
    doc = Document()

    for section in doc.sections:
        section.top_margin = Inches(0.4)
        section.bottom_margin = Inches(0.4)
        section.left_margin = Inches(0.5)
        section.right_margin = Inches(0.5)

    total = len(items)
    for idx, item in enumerate(items):
        p_hdr = doc.add_paragraph()
        p_hdr.paragraph_format.space_before = Pt(4)
        p_hdr.paragraph_format.space_after = Pt(2)
        p_hdr.paragraph_format.line_spacing = 1.15

        run_sno = p_hdr.add_run(f"TARGET #{item['sno']:05d}  |  URL: ")
        run_sno.bold = True
        run_sno.font.size = Pt(11)
        run_sno.font.name = "Calibri"
        run_sno.font.color.rgb = RGBColor(15, 32, 67)

        url_str = item['url']
        add_clickable_hyperlink(p_hdr, url=url_str, text=url_str, font_size_pt=11.0)


        if item["image_rel"] and item["image_rel"] in z_archive.namelist():
            try:
                img_data = z_archive.read(item["image_rel"])
                p_img = doc.add_paragraph()
                p_img.alignment = WD_ALIGN_PARAGRAPH.CENTER
                p_img.paragraph_format.space_before = Pt(0)
                p_img.paragraph_format.space_after = Pt(6)
                
                run_img = p_img.add_run()
                run_img.add_picture(BytesIO(img_data), width=Inches(img_width_inches))
            except Exception as e:
                p_err = doc.add_paragraph(f"[Image Error: {e}]")
                p_err.paragraph_format.space_after = Pt(6)
        else:
            p_missing = doc.add_paragraph("[No Screenshot Available]")
            p_missing.paragraph_format.space_after = Pt(6)

        if (idx + 1) % items_per_page == 0 and (idx + 1) < total:
            doc.add_page_break()

    os.makedirs(os.path.dirname(os.path.abspath(output_path)), exist_ok=True)
    doc.save(output_path)


def main():
    parser = argparse.ArgumentParser(
        description="Arrange Word Report to 2 Links and 2 Images per page matching Excel/CSV target numbers."
    )
    parser.add_argument(
        "--csv", "-c",
        default=r"F:\projects\all domainsearched\all domains links exported 11-8\gamblingsites.checked_domains.csv",
        help="Path to input Excel/CSV file"
    )
    parser.add_argument(
        "--docx", "-d",
        default=r"F:\projects\all domainsearched\all domains links exported 11-8\capture_report.docx",
        help="Path to input Word document (.docx)"
    )
    parser.add_argument(
        "--output-dir", "-o",
        default=r"F:\projects\all domainsearched\all domains links exported 11-8",
        help="Directory to save arranged Word documents"
    )
    parser.add_argument(
        "--batch-size", "-b",
        type=int,
        default=1000,
        help="Number of targets per output file (default: 1000). Set to 0 for a single huge file."
    )
    parser.add_argument(
        "--items-per-page", "-n",
        type=int,
        default=2,
        help="Number of targets (links + images) per page (default: 2)"
    )
    parser.add_argument(
        "--limit", "-l",
        type=int,
        default=None,
        help="Limit number of total targets to process (for testing)"
    )

    args = parser.parse_args()

    print("=" * 65)
    print(" 📑 WORD REPORT REARRANGER (2 LINKS & 2 IMAGES PER PAGE)")
    print("=" * 65)

    csv_rows = parse_csv(args.csv)
    if args.limit:
        print(f"⚠️ Limiting process to first {args.limit} targets.")
        csv_rows = csv_rows[:args.limit]

    z_archive, docx_targets = parse_docx_media(args.docx)
    matched_items = match_csv_to_docx(csv_rows, docx_targets)

    total_items = len(matched_items)
    batch_size = args.batch_size if args.batch_size > 0 else total_items

    batches = [
        matched_items[i : i + batch_size]
        for i in range(0, total_items, batch_size)
    ]

    print(f"🚀 Splitting {total_items} targets into {len(batches)} Word document batch file(s)...")

    for b_idx, batch in enumerate(batches, start=1):
        start_sno = batch[0]["sno"]
        end_sno = batch[-1]["sno"]
        filename = f"arranged_report_part{b_idx}_targets_{start_sno}_to_{end_sno}.docx"
        output_file = os.path.join(args.output_dir, filename)

        print(f"📝 Writing Part {b_idx}/{len(batches)}: {filename} ({len(batch)} targets)...")
        start_t = time.time()
        create_single_docx_file(
            z_archive=z_archive,
            items=batch,
            output_path=output_file,
            items_per_page=args.items_per_page
        )
        elapsed = time.time() - start_t
        file_size_mb = os.path.getsize(output_file) / (1024 * 1024)
        print(f"   ✅ Saved {filename} ({file_size_mb:.1f} MB in {elapsed:.1f}s)")

    print("\n🎉 ALL BATCHES COMPLETED SUCCESSFULLY!")


if __name__ == "__main__":
    main()
