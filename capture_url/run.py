import os
import sys
import asyncio
from tqdm import tqdm

# Add root folder to sys.path
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from capture_url.reader import read_links
from capture_url.screenshot import capture_async
from capture_url.report_builder import build_report

async def async_run(excel_path: str, output_docx_name: str, concurrency: int = 5):
    if not os.path.exists(excel_path):
        print(f"[Error] Excel input file not found: {excel_path}")
        sys.exit(1)

    print(f"[CaptureURL] Reading links from '{excel_path}'...")
    links = read_links(excel_path)
    total = len(links)
    if total == 0:
        print(f"[CaptureURL] No valid links found in '{excel_path}'.")
        return

    print(f"[CaptureURL] Found {total} link(s). Starting screenshot capture (Concurrency: {concurrency})...")

    output_dir = os.path.join("capture_url", "output")
    screenshots_dir = os.path.join(output_dir, "screenshots")
    os.makedirs(screenshots_dir, exist_ok=True)

    if not output_docx_name.endswith('.docx'):
        output_docx_name += '.docx'

    output_docx_path = os.path.join(output_dir, output_docx_name) if not os.path.isabs(output_docx_name) and not output_docx_name.startswith("capture_url") else output_docx_name

    successful_entries = []
    failed_links = []

    pbar = tqdm(total=total, desc="Capturing URLs", unit="site")
    sem = asyncio.Semaphore(concurrency)

    async def _process_one(url: str):
        async with sem:
            shot_path = await capture_async(url, screenshots_dir)
            pbar.update(1)
            if shot_path and os.path.exists(shot_path):
                return url, shot_path, True
            else:
                return url, None, False

    tasks = [_process_one(url) for url in links]
    results = await asyncio.gather(*tasks)
    pbar.close()

    for url, shot_path, success in results:
        if success:
            successful_entries.append({"url": url, "screenshot_path": shot_path})
        else:
            failed_links.append(url)

    captured_count = len(successful_entries)
    failed_count = len(failed_links)

    print(f"\n[CaptureURL] Generating Word report for {captured_count} successfully captured screenshot(s)...")
    if successful_entries:
        saved_doc = build_report(successful_entries, output_docx_path)
        print(f"[CaptureURL] Report successfully created at: {saved_doc}")
    else:
        print(f"[CaptureURL] Warning: No successful screenshots captured. Word report was not generated.")

    print("\n" + "="*60)
    print(f"[SUMMARY] {captured_count} of {total} links captured successfully, {failed_count} failed.")
    if failed_links:
        print("\nFailed Links:")
        for fl in failed_links:
            print(f"  - {fl}")
    print("="*60)

def main():
    if len(sys.argv) < 3:
        print("Usage: python capture_url/run.py <input_excel_file> <output_docx_name>")
        sys.exit(1)

    excel_input = sys.argv[1]
    docx_output = sys.argv[2]
    asyncio.run(async_run(excel_input, docx_output))

if __name__ == "__main__":
    main()
