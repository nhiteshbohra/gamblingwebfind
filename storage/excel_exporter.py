import os
import json
import sqlite3
import pandas as pd

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
        conn = sqlite3.connect(self.db_path)
        
        # 1. Verified Sheet
        query_verified = """
        SELECT url, domain, confidence_score, classification_reasons as matched_signals, verified_at as checked_at
        FROM urls
        WHERE status = 'verified'
        ORDER BY verified_at DESC
        """
        df_verified = pd.read_sql_query(query_verified, conn)

        # 2. Rejected Sheet
        query_rejected = """
        SELECT url, domain, confidence_score, classification_reasons as matched_signals, last_checked_at as checked_at
        FROM urls
        WHERE status = 'rejected'
        ORDER BY last_checked_at DESC
        """
        df_rejected = pd.read_sql_query(query_rejected, conn)

        # 3. Dead Sheet
        query_dead = """
        SELECT url, domain, last_checked_at as checked_at
        FROM urls
        WHERE status = 'dead'
        ORDER BY last_checked_at DESC
        """
        df_dead = pd.read_sql_query(query_dead, conn)

        # 4. Blocked Sheet
        query_blocked = """
        SELECT url, domain, last_checked_at as checked_at
        FROM urls
        WHERE status = 'blocked'
        ORDER BY last_checked_at DESC
        """
        df_blocked = pd.read_sql_query(query_blocked, conn)

        conn.close()

        # Write to multi-sheet Excel workbook
        with pd.ExcelWriter(self.output_path, engine='openpyxl') as writer:
            df_verified.to_excel(writer, sheet_name='Verified', index=False)
            df_rejected.to_excel(writer, sheet_name='Rejected', index=False)
            df_dead.to_excel(writer, sheet_name='Dead', index=False)
            df_blocked.to_excel(writer, sheet_name='Blocked', index=False)

        stats = {
            'verified': len(df_verified),
            'rejected': len(df_rejected),
            'dead': len(df_dead),
            'blocked': len(df_blocked),
            'file': self.output_path
        }
        return stats
