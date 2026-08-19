PS F:\projects\gamblingwebfind> & F:/softwares/python/python.exe f:/projects/gamblingwebfind/main.py
[+] MongoDB: Connected (Database: 'gamblingsitetry')
[+] Web Dashboard: Started at http://127.0.0.1:8081

==========================================================
             GAMBLINGWEBFIND PROCESS MENU
==========================================================
1. keywordssearch        (SearXNG / Multi-Engine Search)
2. checking_url          (Fetch, AI Classify & Screenshot)
3. export_domains        (Export Reports & Divide into Batches)
0. Exit
==========================================================
Select an option (1-3, 0 to exit): 2

--- checking_url (Fetch, AI Classify & Capture Screenshots) ---

Select checking target queue:
  0. Back to main menu
  1. Check New Domains           (Fresh queue from SearXNG search or CSV import) [default]
  2. Re-check Blocked Sites      (Retry HTTP 403 / Cloudflare WAF protected sites)
  3. Re-check Unconfirmed Sites  (Re-evaluate pending sites with Ollama AI)
  4. Re-check Regular Websites   (Re-verify non-gambling sites to detect new gambling content)
  5. Re-check Dead Sites         (Re-test offline or DNS-failed sites to see if back online)

Enter choice (0-5) [default: 1]: 3
[+] Local AI: Ollama is running with model 'gambling-analyst'
[classifier] Loaded 995 keywords from gambling_top_944_keywords.json
[check] 54 unconfirmed domains to process.
Rechecking Unconfirmed: 100%|███████████████████████████████████████████████████████████████████████████████████████████████████████| 54/54 [00:38<00:00,  1.40domain/s, Left=0]

=================================================================
                 TRIPLE-LOCK AI CAPTURE SUMMARY
=================================================================
 Total Domains Processed           : 54
  * Confirmed Gambling Sites       : 0
    - Captured Screenshots         : 0
    - Evaluated by Ollama AI       : 42
    - AI Confirmed Gambling        : 0
    - Challenge Round Overrides    : 0
  * Regular Sites (Verified)       : 0
  * Unconfirmed (Ollama Down)      : 54 [Queued to re-run]
  * Blocked (403 / WAF)            : 0
  * Dead / Unreachable             : 0
=================================================================

[+] checking_url complete.

==========================================================
             GAMBLINGWEBFIND PROCESS MENU
==========================================================
1. keywordssearch        (SearXNG / Multi-Engine Search)
2. checking_url          (Fetch, AI Classify & Screenshot)
3. export_domains        (Export Reports & Divide into Batches)
0. Exit
==========================================================
Select an option (1-3, 0 to exit): 2

--- checking_url (Fetch, AI Classify & Capture Screenshots) ---

Select checking target queue:
  0. Back to main menu
  1. Check New Domains           (Fresh queue from SearXNG search or CSV import) [default]
  2. Re-check Blocked Sites      (Retry HTTP 403 / Cloudflare WAF protected sites)
  3. Re-check Unconfirmed Sites  (Re-evaluate pending sites with Ollama AI)
  4. Re-check Regular Websites   (Re-verify non-gambling sites to detect new gambling content)
  5. Re-check Dead Sites         (Re-test offline or DNS-failed sites to see if back online)

Enter choice (0-5) [default: 1]: 3
[+] Local AI: Ollama is running with model 'gambling-analyst'
[classifier] Loaded 995 keywords from gambling_top_944_keywords.json
[check] 54 unconfirmed domains to process.
Rechecking Unconfirmed:  94%|█████████████████████████████████████████████████████████████████████████████████████████████████▎     | 51/54 [00:28<00:01,  1.80domain/s, Left=3]

[+] Safely shutting down background services and releasing system RAM...
[docker] Safely stopping SearXNG Docker containers to free system memory...
[docker] SearXNG containers stopped successfully. Memory released.
Traceback (most recent call last):
  File "F:\softwares\python\Lib\asyncio\runners.py", line 118, in run
    return self._loop.run_until_complete(task)
           ^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^
  File "F:\softwares\python\Lib\asyncio\base_events.py", line 664, in run_until_complete
    return future.result()
           ^^^^^^^^^^^^^^^
  File "F:\projects\gamblingwebfind\checking_url\runner.py", line 286, in run
    try:
^^^^^^^^^
  File "F:\softwares\python\Lib\asyncio\queues.py", line 215, in join
    await self._finished.wait()
  File "F:\softwares\python\Lib\asyncio\locks.py", line 212, in wait
    await fut
asyncio.exceptions.CancelledError

During handling of the above exception, another exception occurred:

Traceback (most recent call last):
  File "f:\projects\gamblingwebfind\main.py", line 304, in <module>
    main()
  File "f:\projects\gamblingwebfind\main.py", line 298, in main
    interactive_menu()
  File "f:\projects\gamblingwebfind\main.py", line 243, in interactive_menu
    run_checking_url()
  File "f:\projects\gamblingwebfind\main.py", line 136, in run_checking_url
    summary = asyncio.run(check_run(concurrency=concurrency, limit=limit, mode=mode))
              ^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^
  File "F:\softwares\python\Lib\asyncio\runners.py", line 194, in run
    return runner.run(main)
           ^^^^^^^^^^^^^^^^
  File "F:\softwares\python\Lib\asyncio\runners.py", line 123, in run
    raise KeyboardInterrupt()
KeyboardInterrupt
PS F:\projects\gamblingwebfind> git add .
warning: in the working copy of 'checking_url/ai_classifier.py', LF will be replaced by CRLF the next time Git touches it
warning: in the working copy of 'checking_url/runner.py', LF will be replaced by CRLF the next time Git touches it
warning: in the working copy of 'main.py', LF will be replaced by CRLF the next time Git touches it
warning: in the working copy of 'tests/test_api.py', LF will be replaced by CRLF the next time Git touches it
PS F:\projects\gamblingwebfind> git commit -m "kucf ho gaya sab test karna hoga wapas"
Enumerating objects: 385, done.
Counting objects: 100% (385/385), done.
Delta compression using up to 16 threads
Compressing objects: 100% (363/363), done.
Writing objects: 100% (385/385), done.
Total 385 (delta 187), reused 0 (delta 0), pack-reused 0 (from 0)
[remaking-2 64f6704] kucf ho gaya sab test karna hoga wapas
 4 files changed, 20 insertions(+), 19 deletions(-)
PS F:\projects\gamblingwebfind> git push origin remaking-2
Enumerating objects: 15, done.
Counting objects: 100% (15/15), done.
Delta compression using up to 16 threads
Compressing objects: 100% (5/5), done.
Writing objects: 100% (8/8), 1.02 KiB | 1.02 MiB/s, done.
Total 8 (delta 7), reused 4 (delta 3), pack-reused 0 (from 0)
remote: Resolving deltas: 100% (7/7), completed with 7 local objects.
To https://github.com/nhiteshbohra/gamblingwebfind.git
   607c111..64f6704  remaking-2 -> remaking-2
PS F:\projects\gamblingwebfind> & F:/softwares/python/python.exe f:/projects/gamblingwebfind/main.py
[+] MongoDB: Connected (Database: 'gamblingsitetry')
[+] Web Dashboard: Started at http://127.0.0.1:8081

==========================================================
             GAMBLINGWEBFIND PROCESS MENU
==========================================================
1. keywordssearch        (SearXNG / Multi-Engine Search)
2. checking_url          (Fetch, AI Classify & Screenshot)
3. export_domains        (Export Reports & Divide into Batches)
0. Exit
==========================================================
Select an option (1-3, 0 to exit):
[+] Safely shutting down background services and releasing system RAM...
[docker] Safely stopping SearXNG Docker containers to free system memory...
[docker] SearXNG containers stopped successfully. Memory released.
Traceback (most recent call last):
  File "f:\projects\gamblingwebfind\main.py", line 309, in <module>
    main()
  File "f:\projects\gamblingwebfind\main.py", line 303, in main
    interactive_menu()
  File "f:\projects\gamblingwebfind\main.py", line 243, in interactive_menu
    choice = input("Select an option (1-3, 0 to exit): ").strip()
             ^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^
KeyboardInterrupt
PS F:\projects\gamblingwebfind> ollama list
NAME                         ID              SIZE      MODIFIED   
gambling-validator:latest    a5074e8ccc74    1.9 GB    2 days ago
gambling-analyst:latest      a4c60b87f22f    1.9 GB    2 days ago
llama3.1:8b                  46e0c10c039e    4.9 GB    2 days ago
qwen2.5:3b                   357c53fb659c    1.9 GB    2 days ago
PS F:\projects\gamblingwebfind> ollama list
NAME                         ID              SIZE      MODIFIED   
gambling-validator:latest    a5074e8ccc74    1.9 GB    2 days ago
gambling-analyst:latest      a4c60b87f22f    1.9 GB    2 days ago
llama3.1:8b                  46e0c10c039e    4.9 GB    2 days ago
qwen2.5:3b                   357c53fb659c    1.9 GB    2 days ago
PS F:\projects\gamblingwebfind> ollama create gambling-validator -f Modelfile.validator
gathering model components
using existing layer sha256:667b0c1932bc6ffc593ed1d03f895bf2dc8dc6df21db3042284a6f4416b06a29
using existing layer sha256:948af2743fc78a328dcb3b0f5a31b3d75f415840fdb699e8b1235978392ecf85
using existing layer sha256:0ba8f0e314b4264dfd19df045cde9d4c394a52474bf92ed6a3de22a4ca31a177
using existing layer sha256:0b0b6fc907acbe47e42d07605d0986021a4985d577c578a269e35c203ef716be
creating new layer sha256:542b044e63d2b7988f761997d900ffa6f828e33fc1e384ca1d42216dfc6f20c4
writing manifest
success
PS F:\projects\gamblingwebfind> ollama list
NAME                         ID              SIZE      MODIFIED      
gambling-validator:latest    4ab246db92f5    4.9 GB    2 seconds ago
gambling-analyst:latest      a4c60b87f22f    1.9 GB    2 days ago
llama3.1:8b                  46e0c10c039e    4.9 GB    2 days ago
qwen2.5:3b                   357c53fb659c    1.9 GB    2 days ago
PS F:\projects\gamblingwebfind> & F:/softwares/python/python.exe f:/projects/gamblingwebfind/main.py
[+] MongoDB: Connected (Database: 'gamblingsitetry')
[+] Web Dashboard: Started at http://127.0.0.1:8081

==========================================================
             GAMBLINGWEBFIND PROCESS MENU
==========================================================
1. keywordssearch        (SearXNG / Multi-Engine Search)
2. checking_url          (Fetch, AI Classify & Screenshot)
3. export_domains        (Export Reports & Divide into Batches)
0. Exit
==========================================================
Select an option (1-3, 0 to exit): 2

--- checking_url (Fetch, AI Classify & Capture Screenshots) ---

Select checking target queue:
  0. Back to main menu
  1. Check New Domains           (Fresh queue from SearXNG search or CSV import) [default]
  2. Re-check Blocked Sites      (Retry HTTP 403 / Cloudflare WAF protected sites)
  3. Re-check Unconfirmed Sites  (Re-evaluate pending sites with Ollama AI)
  4. Re-check Regular Websites   (Re-verify non-gambling sites to detect new gambling content)
  5. Re-check Dead Sites         (Re-test offline or DNS-failed sites to see if back online)

Enter choice (0-5) [default: 1]: 3
[+] Local AI: Ollama is running with model 'gambling-analyst'
[classifier] Loaded 995 keywords from gambling_top_944_keywords.json
[check] 46 unconfirmed domains to process.
Rechecking Unconfirmed:  30%|█████████████████▋                                        | 14/46 [00:39<00:57,  1.81s/domain, Left=32][validator] Validation failed for https://glassicasinos.in:
[validator] Validation failed for https://augustinecasino.com: 
Rechecking Unconfirmed:  33%|██████████████████▉                                       | 15/46 [00:58<03:12,  6.22s/domain, Left=31][validator] Validation failed for https://spill.casino:
[validator] Validation failed for https://horseplay.casino: 
[validator] Validation failed for https://slotvision.net: 
Rechecking Unconfirmed:  39%|██████████████████████▋                                   | 18/46 [01:10<02:08,  4.60s/domain, Left=28][ai_classifier] Timeout after 19.9s for https://action247.com | chars=1201 | mgr: EMA=7.8s | Timeouts=1/21 (5%) | Current window=[8.0s–45.0s]
Rechecking Unconfirmed:  41%|███████████████████████▉                                  | 19/46 [01:15<02:04,  4.61s/domain, Left=27][validator] Validation failed for https://igamingillinois.bet:
Rechecking Unconfirmed:  46%|██████████████████████████▍                               | 21/46 [01:26<02:11,  5.27s/domain, Left=25][ai_classifier] Timeout after 26.2s for https://betulator.com | chars=2556 | mgr: EMA=9.3s | Timeouts=2/22 (9%) | Current window=[8.0s–45.0s]
Rechecking Unconfirmed:  50%|█████████████████████████████                             | 23/46 [01:33<01:45,  4.60s/domain, Left=23][ai_classifier] Timeout after 33.6s for https://sportsbettingsmarts.com | chars=3610 | mgr: EMA=13.9s | Timeouts=3/24 (12%) | Current window=[8.0s–45.0s]
Rechecking Unconfirmed:  59%|██████████████████████████████████                        | 27/46 [01:40<00:46,  2.44s/domain, Left=19][validator] Validation failed for https://1xbetgiris.bet:
Rechecking Unconfirmed:  65%|█████████████████████████████████████▊                    | 30/46 [01:46<00:40,  2.53s/domain, Left=16][validator] Validation failed for https://thevirtualcasino.com:
Rechecking Unconfirmed:  83%|████████████████████████████████████████████████▋          | 38/46 [02:00<00:15,  1.98s/domain, Left=8][validator] Validation failed for https://ph365.org:
[validator] Validation failed for https://dinar33.org: 
[validator] Validation failed for https://pokergo.com: 
[validator] Validation failed for https://manekicasino.com: 
[validator] Validation failed for https://bwincasino.be: 
Rechecking Unconfirmed:  89%|████████████████████████████████████████████████████▌      | 41/46 [02:29<00:33,  6.73s/domain, Left=5][validator] Validation failed for https://nuggetcasinos.com:
[validator] Validation failed for https://parkwestcasino580.com: 
[validator] Validation failed for https://tritonpokertables.in:
Rechecking Unconfirmed: 100%|███████████████████████████████████████████████████████████| 46/46 [02:45<00:00,  3.59s/domain, Left=0]

=================================================================
                 TRIPLE-LOCK AI CAPTURE SUMMARY
=================================================================
 Total Domains Processed           : 46
  * Confirmed Gambling Sites       : 16
    - Captured Screenshots         : 14
    - Evaluated by Ollama AI       : 42
    - AI Confirmed Gambling        : 16
    - Challenge Round Overrides    : 0
  * Regular Sites (Verified)       : 23
  * Unconfirmed (Ollama Down)      : 7 [Queued to re-run]
  * Blocked (403 / WAF)            : 2
  * Dead / Unreachable             : 0
=================================================================

[+] checking_url complete.

==========================================================
             GAMBLINGWEBFIND PROCESS MENU
==========================================================
1. keywordssearch        (SearXNG / Multi-Engine Search)
2. checking_url          (Fetch, AI Classify & Screenshot)
3. export_domains        (Export Reports & Divide into Batches)
0. Exit
==========================================================
Select an option (1-3, 0 to exit): 3

--- export_domains (Export & Batch Splitting) ---
  0. Back to main menu
  1. Export from Database  (Generate Word, PDF & Excel from MongoDB)
  2. Divide into Batches   (Split existing Excel/CSV + PDF into batch folders)
Select option (0-2) [default: 1]: 1
[export] Run ID: 20260819_162840 | Verified: 17 | Failed: 0
[export] Building Word report (17 screenshots)...
[export] Converting to PDF...
[export] PDF saved: F:\projects\gamblingwebfind\output\20260819_162840\report.pdf
[export] Building Excel workbook...
[export] Excel saved: output\20260819_162840\report.xlsx

[export] Done — output/20260819_162840/
           report.pdf  : F:\projects\gamblingwebfind\output\20260819_162840\report.pdf
           report.xlsx : output\20260819_162840\report.xlsx
           Captured    : 17 | Failed: 0

[+] Export Complete:
    Run ID   : 20260819_162840
    PDF      : F:\projects\gamblingwebfind\output\20260819_162840\report.pdf
    Excel    : output\20260819_162840\report.xlsx
    Captured : 17
    Failed   : 0

[?] Would you like to divide these exported files into batches now? (y/n) [default: n]: n

==========================================================
             GAMBLINGWEBFIND PROCESS MENU
==========================================================
1. keywordssearch        (SearXNG / Multi-Engine Search)
2. checking_url          (Fetch, AI Classify & Screenshot)
3. export_domains        (Export Reports & Divide into Batches)
0. Exit
==========================================================
Select an option (1-3, 0 to exit): 2

--- checking_url (Fetch, AI Classify & Capture Screenshots) ---

Select checking target queue:
  0. Back to main menu
  1. Check New Domains           (Fresh queue from SearXNG search or CSV import) [default]
  2. Re-check Blocked Sites      (Retry HTTP 403 / Cloudflare WAF protected sites)
  3. Re-check Unconfirmed Sites  (Re-evaluate pending sites with Ollama AI)
  4. Re-check Regular Websites   (Re-verify non-gambling sites to detect new gambling content)
  5. Re-check Dead Sites         (Re-test offline or DNS-failed sites to see if back online)

Enter choice (0-5) [default: 1]: 3
[+] Local AI: Ollama is running with model 'gambling-analyst'
[classifier] Loaded 995 keywords from gambling_top_944_keywords.json
[check] 7 unconfirmed domains to process.
Rechecking Unconfirmed: 100%|█████████████████████████████████████████████████████████████| 7/7 [00:59<00:00,  8.56s/domain, Left=0]

=================================================================
                 TRIPLE-LOCK AI CAPTURE SUMMARY
=================================================================
 Total Domains Processed           : 7
  * Confirmed Gambling Sites       : 2
    - Captured Screenshots         : 2
    - Evaluated by Ollama AI       : 3
    - AI Confirmed Gambling        : 2
    - Challenge Round Overrides    : 0
  * Regular Sites (Verified)       : 1
  * Unconfirmed (Ollama Down)      : 4 [Queued to re-run]
  * Blocked (403 / WAF)            : 0
  * Dead / Unreachable             : 0
=================================================================

[+] checking_url complete.

==========================================================
             GAMBLINGWEBFIND PROCESS MENU
==========================================================
1. keywordssearch        (SearXNG / Multi-Engine Search)
2. checking_url          (Fetch, AI Classify & Screenshot)
3. export_domains        (Export Reports & Divide into Batches)
0. Exit
==========================================================
Select an option (1-3, 0 to exit):
[+] Safely shutting down background services and releasing system RAM...
[docker] Safely stopping SearXNG Docker containers to free system memory...
[docker] SearXNG containers stopped successfully. Memory released.
Traceback (most recent call last):
  File "f:\projects\gamblingwebfind\main.py", line 309, in <module>
    main()
  File "f:\projects\gamblingwebfind\main.py", line 303, in main
    interactive_menu()
  File "f:\projects\gamblingwebfind\main.py", line 243, in interactive_menu
    choice = input("Select an option (1-3, 0 to exit): ").strip()
             ^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^
KeyboardInterrupt
PS F:\projects\gamblingwebfind> & F:/softwares/python/python.exe f:/projects/gamblingwebfind/main.py
[+] MongoDB: Connected (Database: 'gamblingsitetry')
[+] Web Dashboard: Started at http://127.0.0.1:8081

==========================================================
             GAMBLINGWEBFIND PROCESS MENU
==========================================================
1. keywordssearch        (SearXNG / Multi-Engine Search)
2. checking_url          (Fetch, AI Classify & Screenshot)
3. export_domains        (Export Reports & Divide into Batches)
0. Exit
==========================================================
Select an option (1-3, 0 to exit): 2

--- checking_url (Fetch, AI Classify & Capture Screenshots) ---

Select checking target queue:
  0. Back to main menu
  1. Check New Domains           (Fresh queue from SearXNG search or CSV import) [default]
  2. Re-check Blocked Sites      (Retry HTTP 403 / Cloudflare WAF protected sites)
  3. Re-check Unconfirmed Sites  (Re-evaluate pending sites with Ollama AI)
  4. Re-check Regular Websites   (Re-verify non-gambling sites to detect new gambling content)
  5. Re-check Dead Sites         (Re-test offline or DNS-failed sites to see if back online)

Enter choice (0-5) [default: 1]: 3
[+] Local AI: Ollama is running with model 'gambling-analyst'
[classifier] Loaded 995 keywords from gambling_top_944_keywords.json
[check] 4 unconfirmed domains to process.
Rechecking Unconfirmed: 100%|█████████████████████████████████████████████████████████████| 4/4 [00:31<00:00,  7.83s/domain, Left=0]

=================================================================
                 TRIPLE-LOCK AI CAPTURE SUMMARY
=================================================================
 Total Domains Processed           : 4
  * Confirmed Gambling Sites       : 0
    - Captured Screenshots         : 0
    - Evaluated by Ollama AI       : 0
    - AI Confirmed Gambling        : 0
    - Challenge Round Overrides    : 0
  * Regular Sites (Verified)       : 0
  * Unconfirmed (Queued to re-run) : 4
  * Blocked (403 / WAF)            : 0
  * Dead / Unreachable             : 0
=================================================================

[+] checking_url complete.

==========================================================
             GAMBLINGWEBFIND PROCESS MENU
==========================================================
1. keywordssearch        (SearXNG / Multi-Engine Search)
2. checking_url          (Fetch, AI Classify & Screenshot)
3. export_domains        (Export Reports & Divide into Batches)
0. Exit
==========================================================
Select an option (1-3, 0 to exit):