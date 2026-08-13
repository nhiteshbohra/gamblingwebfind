"""
reports/docx_report_generator.py — Build A4 Word document from screenshot entries.

Moved from capture_url/report_builder.py. Logic unchanged — takes only URL +
in-memory image paths, never touches the database.
"""
import os
import docx
from docx.shared import Inches, Pt, RGBColor
from docx.enum.text import WD_ALIGN_PARAGRAPH, WD_TAB_ALIGNMENT
from docx.oxml import parse_xml
from docx.oxml.ns import nsdecls


def add_clickable_hyperlink(paragraph, url: str, text: str, font_size_pt=12.0):
    """Inject an active, clickable OpenXML hyperlink run with 12pt blue underlined text."""
    part = paragraph.part
    r_id = part.relate_to(url, docx.opc.constants.RELATIONSHIP_TYPE.HYPERLINK, is_external=True)

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


def build_report_from_mongo(domain_ids: list = None, output_path: str = "output/capture_report.docx", cleanup: bool = True) -> str:
    """Build Word report by querying Mongo for successfully captured domains.

    Only includes entries whose screenshot JPEG actually exists on disk.
    Cleans up temporary JPEGs after embedding them.
    """
    if domain_ids is not None and len(domain_ids) == 0:
        print("[report] No domains processed in this run to generate Word report.")
        return output_path

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
        print(f"[report] Warning: {len(missing_ids)} domains marked captured but JPEG not found on disk. Skipping from Word doc.")
        # Reconcile DB status
        checked_domains().update_many({"_id": {"$in": missing_ids}}, {"$set": {"screenshot_taken": False}})

    print(f"[report] Building Word document with {len(entries)} screenshots -> {output_path}")
    res = build_report(entries, output_path)

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

    return res
