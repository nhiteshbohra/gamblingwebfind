import os
import pandas as pd

def read_links(excel_path: str, column_name: str = None) -> list[str]:
    """Read a list of link strings from an Excel (.xlsx) file.
    
    If column_name is provided, reads that specific column.
    Otherwise, auto-detects the first column containing URL-like values.
    Returns a clean list of link strings, skipping blank cells.
    """
    if not os.path.exists(excel_path):
        raise FileNotFoundError(f"Excel file not found: {excel_path}")

    # Read all sheets or first sheet
    df = pd.read_excel(excel_path, sheet_name=0)
    if df.empty:
        return []

    target_col = None
    if column_name and column_name in df.columns:
        target_col = column_name
    else:
        # Auto-detect column containing URLs
        for col in df.columns:
            sample_vals = df[col].dropna().astype(str).str.strip().tolist()[:20]
            if any(v.lower().startswith(('http://', 'https://')) or '.' in v for v in sample_vals):
                target_col = col
                break
        if target_col is None:
            target_col = df.columns[0]

    links = []
    for val in df[target_col].dropna():
        u_str = str(val).strip()
        if u_str and not u_str.startswith('#'):
            if not u_str.lower().startswith(('http://', 'https://')):
                u_str = 'https://' + u_str
            links.append(u_str)

    return links
