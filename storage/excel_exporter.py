import os
import pandas as pd
from storage.db import get_urls_export_df

class ExcelExporter:
    def __init__(self, db_path="data/presence.db", output_path="output.xlsx"):
        self.db_path = db_path
        self.output_path = output_path
        os.makedirs(os.path.dirname(os.path.abspath(self.output_path)), exist_ok=True)

    def export(self, tier="strict"):
        """Export categorized database results into a multi-sheet Excel file (.xlsx).
        Sheets:
          - Verified: Strict-confirmed gambling operators (URL, Domain, Score, Reasons, Verified At)
          - Rejected: Reachable but failed strict score/signal threshold
          - Dead: Unreachable / dead domains after retries
          - Blocked: Cloudflare / CAPTCHA / bot-blocked domains
        """
        df_verified = get_urls_export_df(self.db_path, 'verified')
        df_rejected = get_urls_export_df(self.db_path, 'rejected')
        df_dead = get_urls_export_df(self.db_path, 'dead')
        df_blocked = get_urls_export_df(self.db_path, 'blocked')

        with pd.ExcelWriter(self.output_path, engine='openpyxl') as writer:
            df_verified.to_excel(writer, sheet_name='Verified', index=False)
            df_rejected.to_excel(writer, sheet_name='Rejected', index=False)
            df_dead.to_excel(writer, sheet_name='Dead', index=False)
            df_blocked.to_excel(writer, sheet_name='Blocked', index=False)

        return {
            "file": self.output_path,
            "verified": len(df_verified),
            "rejected": len(df_rejected),
            "dead": len(df_dead),
            "blocked": len(df_blocked),
        }
