import os
import csv
from datetime import datetime, timezone

class CSVExporter:
    def __init__(self, db, output_path="data/presence_gambling_sites.csv"):
        self.db = db
        self.output_path = output_path
        os.makedirs(os.path.dirname(self.output_path), exist_ok=True)

    async def export(self):
        rows = await self.db.get_verified_live_rows()
        temp_path = self.output_path + ".tmp"
        with open(temp_path, 'w', newline='', encoding='utf-8') as f:
            writer = csv.writer(f)
            writer.writerow(['url', 'domain', 'confidence_score', 'first_seen_at', 'verified_at'])
            for r in rows:
                writer.writerow([r['url'], r['domain'], r['confidence_score'], r['first_seen_at'], r['verified_at']])

        if os.path.exists(self.output_path):
            os.remove(self.output_path)
        os.rename(temp_path, self.output_path)

        # Record export timestamp in meta table
        ts = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
        await self.db.set_meta('last_export_at', ts)

        return len(rows)

