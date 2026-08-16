from pymongo import MongoClient
import os
import dotenv
from pathlib import Path

dotenv.load_dotenv('.env')
db = MongoClient(os.getenv('MONGO_URI', 'mongodb://localhost:27017/'))[os.getenv('MONGO_DB_NAME', 'gamblingsites')]
checked = db[os.getenv('CHECKED_COLLECTION', 'checked_domains')]

stats = list(checked.aggregate([{"$group": {"_id": "$status", "count": {"$sum": 1}}}]))
print("=== Database Status (checked_domains) ===")
total = 0
for s in sorted(stats, key=lambda x: str(x['_id'])):
    print(f"  {str(s['_id']):15s}: {s['count']:,}")
    total += s['count']
print(f"  Total in DB    : {total:,}")

ss_active = len(list(Path('output/screenshots').glob('*.jpg')) + list(Path('output/screenshots').glob('*.png')))
ss_fp = len(list(Path('output/screenshots_fp_removed').glob('*.jpg')) + list(Path('output/screenshots_fp_removed').glob('*.png')))
print()
print("=== Screenshot Folders ===")
print(f"  output/screenshots/ (Confirmed Gambling) : {ss_active:,}")
print(f"  output/screenshots_fp_removed/ (Regular) : {ss_fp:,}")
