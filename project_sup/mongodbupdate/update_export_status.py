r"""
update_export_status.py — Update export status and export date for domains listed in CSV.

Reads a CSV file containing domain records and updates their status in MongoDB
(`checked_domains` and `domain_Listed` collections):
  - exported: True
  - export_status: "successful"
  - exported_at: "2026-08-10 00:00:00 IST"
  - export_date: "10th of August 2026"
  - screenshot_taken: True
"""
import os
import sys
from pathlib import Path
import pandas as pd
from dotenv import load_dotenv
from pymongo import MongoClient, UpdateOne
from tqdm import tqdm

# Load .env (checks script dir, parent dirs, or root)
env_path = Path(__file__).resolve().parent / ".env"
if not env_path.exists():
    env_path = Path(__file__).resolve().parents[2] / ".env"
load_dotenv(dotenv_path=env_path)

DEFAULT_CSV_PATH = Path(sys.argv[1]) if len(sys.argv) > 1 else Path("")
EXPORT_DATE_STR = "10th of August 2026"
EXPORT_TIMESTAMP_IST = "2026-08-10 00:00:00 IST"
BATCH_SIZE = 1000


def get_db():
    uri = os.getenv("MONGO_URI", "mongodb://localhost:27017/")
    db_name = os.getenv("MONGO_DB_NAME", "gamblingsites")
    client = MongoClient(uri)
    return client[db_name]


def get_csv_path() -> Path:
    """Prompt user interactively for CSV file path or use CLI argument/default."""
    if len(sys.argv) > 1 and sys.argv[1].strip():
        cli_path = Path(sys.argv[1].strip().strip('"').strip("'"))
        if cli_path.exists():
            return cli_path
        print(f"[Warning] Provided path '{cli_path}' does not exist.")

    default_str = str(DEFAULT_CSV_PATH) if DEFAULT_CSV_PATH.exists() else ""

    prompt_str = "Enter the path to the CSV (.csv) file"
    if default_str:
        prompt_str += f" [Press Enter for default: {default_str}]"
    prompt_str += ": "

    user_input = input(prompt_str).strip().strip('"').strip("'")

    if not user_input:
        if DEFAULT_CSV_PATH.exists():
            return DEFAULT_CSV_PATH
        print(f"[Error] No path provided and default CSV file '{DEFAULT_CSV_PATH}' was not found.")
        sys.exit(1)

    chosen_path = Path(user_input)
    if not chosen_path.exists():
        print(f"[Error] CSV file not found at: {chosen_path}")
        sys.exit(1)

    return chosen_path


def clean_domain_and_url(raw_domain: str, raw_url: str) -> tuple[str, str]:
    dom = str(raw_domain or "").strip().lower()
    url = str(raw_url or "").strip()

    if not dom and url:
        dom = url.replace("https://", "").replace("http://", "").split("/")[0].replace("www.", "")

    if dom.startswith("www."):
        dom = dom[4:]

    if not url:
        url = f"https://{dom}"
    elif not url.startswith(("http://", "https://")):
        url = f"https://{url}"

    return dom, url


def update_export_status(csv_path: Path = None):
    if csv_path is None:
        csv_path = get_csv_path()

    if not csv_path.exists():
        print(f"[Error] CSV file not found at: {csv_path}")
        sys.exit(1)

    print(f"\nLoading CSV: {csv_path}")
    df = pd.read_csv(csv_path)
    total_rows = len(df)
    print(f"Total records in CSV: {total_rows:,}")

    db = get_db()
    source_col = db[os.getenv("MONGO_COLLECTION", "domain_Listed")]
    checked_col = db[os.getenv("CHECKED_COLLECTION", "checked_domains")]

    source_ops = []
    checked_ops = []
    processed_count = 0

    pbar = tqdm(total=total_rows, desc="Updating export status in MongoDB", unit="domains")

    for _, row in df.iterrows():
        dom, url = clean_domain_and_url(row.get("domain"), row.get("url"))
        if not dom:
            pbar.update(1)
            continue

        screenshot_flag = bool(row.get("screenshot", True))  # was: "screenhot" (typo fixed)

        # 1. Update domain_Listed
        source_ops.append(
            UpdateOne(
                {"_id": dom},
                {
                    "$set": {
                        "_id": dom,
                        "domain": dom,
                        "active": True,
                        "exported": True,
                        "export_status": "successful",
                        "exported_at": EXPORT_TIMESTAMP_IST,
                        "export_date": EXPORT_DATE_STR,
                    }
                },
                upsert=True,
            )
        )

        # 2. Update checked_domains
        is_exported = bool(screenshot_flag)
        set_fields = {
            "_id": dom,
            "domain": dom,
            "url": url,
            "exported": is_exported,
            "screenshot_taken": screenshot_flag,
            "screenshot_failed_reason": None if screenshot_flag else "timeout or blank",
        }

        update_doc = {
            "$set": set_fields,
            "$setOnInsert": {
                "status": "gambling",
                "reason": ["csv_import"],
            },
        }

        if is_exported:
            set_fields.update({
                "export_status": "successful",
                "exported_at": EXPORT_TIMESTAMP_IST,
                "export_date": EXPORT_DATE_STR,
            })
        else:
            update_doc["$unset"] = {
                "export_status": "",
                "exported_at": "",
                "export_date": "",
            }

        checked_ops.append(
            UpdateOne(
                {"_id": dom},
                update_doc,
                upsert=True,
            )
        )

        processed_count += 1
        pbar.update(1)

        if len(source_ops) >= BATCH_SIZE:
            source_col.bulk_write(source_ops, ordered=False)
            checked_col.bulk_write(checked_ops, ordered=False)
            source_ops.clear()
            checked_ops.clear()

    if source_ops:
        source_col.bulk_write(source_ops, ordered=False)
        checked_col.bulk_write(checked_ops, ordered=False)
        source_ops.clear()
        checked_ops.clear()

    pbar.close()

    # Summary
    print("\n" + "=" * 60)
    print("Export Status Update Complete!")
    print(f"  Processed CSV domains: {processed_count:,}")
    print(f"  checked_domains with export_status='successful': "
          f"{checked_col.count_documents({'export_status': 'successful'}):,}")
    print(f"  domain_Listed with export_status='successful': "
          f"{source_col.count_documents({'export_status': 'successful'}):,}")
    print("=" * 60)


if __name__ == "__main__":
    update_export_status()
