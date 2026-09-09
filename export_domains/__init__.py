"""
export_domains — Unified single-command export pipeline and screenshot capture utilities.
"""

def __getattr__(name):
    if name in (
        "run_export",
        "run",
        "build_report",
        "build_workbook",
        "_convert_to_pdf",
        "add_clickable_hyperlink",
        "generate_pdf_batch",
        "build_batch_workbook",
    ):
        import export_domains.exporter as _mod
        return getattr(_mod, name)
    if name in (
        "BrowserPool",
        "is_valid_screenshot",
        "delete_screenshot",
        "_url_to_filename",
        "all_filename_candidates",
        "find_screenshot_path",
    ):
        import export_domains.screenshot as _mod
        return getattr(_mod, name)
    if name in (
        "create_batches",
        "prompt_divide_into_batches",
        "_make_excel_urls_clickable",
    ):
        import export_domains.batch_splitter as _mod
        return getattr(_mod, name)
    raise AttributeError(f"module 'export_domains' has no attribute '{name}'")

__all__ = [
    "run_export",
    "run",
    "build_report",
    "build_workbook",
    "_convert_to_pdf",
    "add_clickable_hyperlink",
    "generate_pdf_batch",
    "build_batch_workbook",
    "BrowserPool",
    "is_valid_screenshot",
    "delete_screenshot",
    "_url_to_filename",
    "all_filename_candidates",
    "find_screenshot_path",
    "create_batches",
    "prompt_divide_into_batches",
    "_make_excel_urls_clickable",
]
