# Codebase & Architecture Audit Report

**Target Repository:** `gamblingwebfind`  
**Audit Focus:** Dead Code, Over-Engineering, Obsolete Helper Scripts, Schema Redundancies, and Hardcoded Paths.

---

## 1. Dead Code & Obsolete Helper Scripts (Immediate Deletions)

| Priority | Tag | File / Component | Lines | Action & Rationale |
| :--- | :--- | :--- | :--- | :--- |
| **P0** | `delete` | [`project_sup/helping_code/compare_processed_domains.py.py`](file:///f:/projects/gamblingwebfind/project_sup/helping_code/compare_processed_domains.py.py) | 76 | **Delete.** Has a broken double `.py.py` extension and is an obsolete one-off sync script that duplicates database queries. |
| **P0** | `delete` | [`project_sup/helping_code/arrange_docx_report.py`](file:///f:/projects/gamblingwebfind/project_sup/helping_code/arrange_docx_report.py) | 331 | **Delete.** Obsolete standalone XML report rearranger hardcoded to an external machine path (`F:\projects\all domainsearched\...`). Superseded by native batch generator [`capture_url/docx_report_generator.py`](file:///f:/projects/gamblingwebfind/capture_url/docx_report_generator.py). |

| **P1** | `delete` | [`project_sup/helping_code/compare_dbs.py`](file:///f:/projects/gamblingwebfind/project_sup/helping_code/compare_dbs.py) | 110 | **Delete.** Legacy terminal comparison script superseded by [`compare_status_changes.py`](file:///f:/projects/gamblingwebfind/project_sup/helping_code/compare_status_changes.py) which exports clickable Excel reports. |
| **P1** | `delete` | `project_sup/mongopipeline/status_count_by_added_date` | 15 | **Delete.** Stray scratch query file with hardcoded historical date (`2026-08-14`). |

---

## 2. Over-Engineered Subsystems (Prune / Simplify)

| Priority | Tag | File / Component | Lines | Action & Rationale |
| :--- | :--- | :--- | :--- | :--- |
| **P1** | `yagni` | [`checking_url/deep_crawl.py`](file:///f:/projects/gamblingwebfind/checking_url/deep_crawl.py) | 150 | **Prune/Remove.** Complex outbound link scraper with hardcoded 60+ domain blocklist and heuristics (`DEEP_CRAWL_MIN_LINKS`, `DEEP_CRAWL_STRICT`). Injects low-confidence outbound URLs into `domain_Listed`, complicating classification and causing false positives. |
| **P1** | `shrink` | [`checking_url/fetcher.py`](file:///f:/projects/gamblingwebfind/checking_url/fetcher.py) (Stealth Tier) | ~50 | **Remove StealthyFetcher fallback.** The dual-tier architecture (`STEALTH_FALLBACK=false` by default) spins up heavy Scrapling browsers per domain on 403s. Fast tier (`curl_cffi`) for Stage 2 + Playwright for Stage 3 screenshotting is already sufficient. |
| **P1** | `shrink` | [`db/mongo_client.py`](file:///f:/projects/gamblingwebfind/db/mongo_client.py) | ~110 | **Clean unused helper methods:**<br>• `find_gambling_domains_by_date` (unused)<br>• `keywords_col` (legacy; keywords are loaded directly from `gambling_top_500_keywords.json`)<br>• `seed_discovered_domains` (tied to `deep_crawl.py`)<br>• `_TRACKING_PARAMS` & `strip_tracking_params` (unused regex overhead) |

---

## 3. Database Schema Redundancies & Fixes

1. **Strict 5-Field Clean Schema for `domain_Listed`:**
   ```json
   {
     "_id": "example.com",
     "domain": "example.com",
     "active": true,
     "processed": false,
     "added_date": "2026-08-15"
   }
   ```
   * *Rule:* Newly inserted domains MUST have `processed: false` and dynamic `added_date` (`$setOnInsert`).

2. **Strict 7-Field Clean Schema for `checked_domains`:**
   ```json
   {
     "_id": "example.com",
     "domain": "example.com",
     "url": "https://example.com",
     "status": "gambling",
     "reason": ["strong:casino", "medium:poker"],
     "screenshot_taken": true,
     "screenshot_failed_reason": null
   }
   ```
   * *Rule:* Never nest stringified JSON inside list arrays (e.g. `['["strong:play now"]']` $\rightarrow$ use clean lists `["strong:play now"]`).

---

## 4. Net Impact Summary

* **Redundant Files to Delete:** 6 files
* **Net Source Lines Removable:** **~840+ lines of dead / over-engineered code**
* **External Dependencies Removable:** `pymupdf` (FitZ)
