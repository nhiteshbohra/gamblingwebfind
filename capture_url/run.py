import os
import sys
import json
import time
import asyncio
import pandas as pd
from tqdm import tqdm

# Add root folder to sys.path
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from capture_url.reader import read_links
from capture_url.screenshot import BrowserPool, is_valid_screenshot
from capture_url.report_builder import build_report

async def async_run(excel_path: str, output_docx_name: str, concurrency: int = 15):
    if not os.path.exists(excel_path):
        print(f"[Error] Excel input file not found: {excel_path}")
        sys.exit(1)

    print(f"[CaptureURL] Reading links from '{excel_path}'...")
    links = read_links(excel_path)
    total = len(links)
    if total == 0:
        print(f"[CaptureURL] No valid links found in '{excel_path}'.")
        return

    output_dir = os.path.join("capture_url", "output")
    screenshots_dir = os.path.join(output_dir, "screenshots")
    os.makedirs(screenshots_dir, exist_ok=True)
    progress_file = os.path.join(output_dir, "progress.json")
    excel_status_path = os.path.join(output_dir, "final_url_status.xlsx")

    if not output_docx_name.endswith('.docx'):
        output_docx_name += '.docx'
    output_docx_path = os.path.join(output_dir, output_docx_name) if not os.path.isabs(output_docx_name) and not output_docx_name.startswith("capture_url") else output_docx_name

    progress_data = {}
    if os.path.exists(progress_file):
        try:
            with open(progress_file, 'r', encoding='utf-8') as f:
                progress_data = json.load(f)
        except Exception:
            progress_data = {}

    successful_entries = []
    failed_links = []
    already_done = 0

    for url in links:
        if url in progress_data and isinstance(progress_data[url], dict):
            info = progress_data[url]
            if info.get('success'):
                path = info.get('path')
                if path and is_valid_screenshot(path):
                    already_done += 1
                    successful_entries.append({"url": url, "screenshot_path": path})
            else:
                failed_links.append(url)

    print(f"[CaptureURL] Total links: {total:,} | Already verified screenshots: {already_done:,} | Remaining: {total - already_done:,} (Concurrency: {concurrency})")

    # Ensure docx and excel status files exist immediately on startup
    if successful_entries:
        try:
            build_report(successful_entries, output_docx_path)
        except Exception:
            pass

    pool = BrowserPool(concurrency=concurrency)
    await pool.start()

    sem = asyncio.Semaphore(concurrency)
    pbar = tqdm(
        total=total,
        initial=already_done,
        desc="Capturing Screenshots",
        unit="site",
        dynamic_ncols=True,
        leave=True
    )

    last_save_time = time.time()
    save_lock = asyncio.Lock()

    async def save_progress():
        nonlocal last_save_time
        async with save_lock:
            try:
                # 1. Update progress.json
                with open(progress_file, 'w', encoding='utf-8') as f:
                    json.dump(progress_data, f, indent=2)

                # 2. Rebuild complete list of all valid verified entries from progress_data
                all_verified_entries = [
                    {"url": u, "screenshot_path": info.get('path')}
                    for u, info in progress_data.items()
                    if isinstance(info, dict) and info.get('success') and info.get('path') and is_valid_screenshot(info.get('path'))
                ]

                # 3. Incrementally update Word report docx with ALL verified entries
                if all_verified_entries:
                    try:
                        build_report(all_verified_entries, output_docx_path)
                    except Exception:
                        pass  # Swallow transient lock errors if file is currently opened in Word

                # 4. Incrementally update multi-sheet Excel status workbook
                try:
                    ver_rows = []
                    fail_rows = []
                    for u, info in progress_data.items():
                        if isinstance(info, dict):
                            if info.get('success'):
                                ver_rows.append({
                                    "URL": u,
                                    "Status": "Verified / Captured",
                                    "Screenshot_Path": info.get('path', '')
                                })
                            else:
                                fail_rows.append({
                                    "URL": u,
                                    "Status": "Failed / Unreachable",
                                    "Error": info.get('error', 'Timeout / Navigation error')
                                })
                    df_ver = pd.DataFrame(ver_rows)
                    df_fail = pd.DataFrame(fail_rows)
                    with pd.ExcelWriter(excel_status_path, engine='openpyxl') as writer:
                        df_ver.to_excel(writer, sheet_name='Verified_Captured', index=False)
                        df_fail.to_excel(writer, sheet_name='Failed_Not_Verified', index=False)
                except Exception:
                    pass

                last_save_time = time.time()
            except Exception as e:
                print(f"[Warning] Failed to save progress: {e}")

    async def process_one(url: str):
        if url in progress_data and isinstance(progress_data[url], dict) and progress_data[url].get('success'):
            path = progress_data[url].get('path')
            if path and is_valid_screenshot(path):
                pbar.set_postfix(succ=len(successful_entries), fail=len(failed_links))
                return

        async with sem:
            shot_path = await pool.capture_url(url, screenshots_dir, retries=3)
            pbar.update(1)

            if shot_path and is_valid_screenshot(shot_path):
                progress_data[url] = {"success": True, "path": shot_path}
                successful_entries.append({"url": url, "screenshot_path": shot_path})
            else:
                progress_data[url] = {"success": False, "path": None, "error": "Unreachable or solid color"}
                if url not in failed_links:
                    failed_links.append(url)

            pbar.set_postfix(succ=len(successful_entries), fail=len(failed_links))

            if time.time() - last_save_time > 15:
                await save_progress()

    try:
        tasks = [process_one(url) for url in links]
        await asyncio.gather(*tasks)
    finally:
        await save_progress()
        await pool.close()
        pbar.close()

    captured_count = len(successful_entries)
    failed_count = len(failed_links)

    print(f"\n[CaptureURL] Finalizing Word report for {captured_count:,} successfully captured screenshot(s)...")
    if successful_entries:
        saved_doc = build_report(successful_entries, output_docx_path)
        print(f"[CaptureURL] Report successfully finalized at: {saved_doc}")
        print(f"[CaptureURL] Multi-sheet Excel status file updated at: {excel_status_path}")
    else:
        print(f"[CaptureURL] Warning: No successful screenshots captured.")

    print("\n" + "="*60)
    print(f"[SUMMARY] {captured_count:,} of {total:,} links captured successfully, {failed_count:,} failed.")
    print("="*60)

def main():
    excel_input = sys.argv[1] if len(sys.argv) > 1 else "final url.xlsx"
    docx_output = sys.argv[2] if len(sys.argv) > 2 else "final_url_report.docx"
    concurrency = int(sys.argv[3]) if len(sys.argv) > 3 else 15
    print(f"[CaptureURL] Starting terminal runner...")
    print(f"  Input Excel:   {excel_input}")
    print(f"  Output Docx:   {docx_output}")
    print(f"  Concurrency:   {concurrency}")
    print("="*60)
    asyncio.run(async_run(excel_input, docx_output, concurrency=concurrency))

if __name__ == "__main__":
    main()


