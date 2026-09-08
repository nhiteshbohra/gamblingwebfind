r"""
project_sup/visual_check.py — Visual Screenshot Audit & Folder Sorter
======================================================================

Visually audits all screenshot images in a user-specified folder using OCR
(Tesseract) and Vision AI (Ollama) and sorts them directly on disk into:
  • <folder>/gambling/  -- Confirmed online gambling operator sites
  • <folder>/false/     -- Regular websites, dead pages, and institutional
                           exceptions (hotels, resorts, banks, schools)

OCR text also runs through checking_url.text_signals, adding two precision-gated
gambling signals on top of the keyword/APK/vision logic:
  • marketing copy  -- min-deposit / bonus% / instant-withdrawal / e-wallet lists
                       / refer-&-earn / betting-ID funnel phrases
  • contact funnel  -- an actual WhatsApp/Telegram number in the screenshot
Each also requires a core gambling category word, so a forex/cashback screenshot
with "bonus"+"instant withdrawal" can't trip them. A lone OCR strong keyword with
no corroboration (trade press / regulator / casino resort / spam-injected blog
all quote "online casino") is routed to the vision model, not auto-sorted.

Pure filesystem organization:
- Does NOT write, update, or alter anything in MongoDB.
- Protects the live permanent archive (output/screenshots/).

Usage:
  python -m project_sup.visual_check
  python -m project_sup.visual_check --input "output/screenshots/New folder - Copy"
  python -m project_sup.visual_check --input ./batch1 --dry-run
  python project_sup/visual_check.py
"""
from __future__ import annotations

import argparse
import asyncio
import os
import re
import shutil
import sys
from pathlib import Path

# ── Ensure project root is on sys.path ───────────────────────────────────────
PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from dotenv import load_dotenv

load_dotenv(dotenv_path=PROJECT_ROOT / ".env")

from tqdm import tqdm

from export_domains.screenshot import is_valid_screenshot
from checking_url.ocr_extractor import extract_ocr_text, get_ocr_engine
from checking_url.ai_classifier import (
    classify_screenshot_for_sorting,
    close_ai_session,
    start_ollama_if_needed,
)
from checking_url.classifier import (
    load_keywords,
    is_hospitality_site,
    detect_negative_archetype,
    looks_like_editorial,
    is_gambling_domain,
    is_dead_or_error_page,
    is_parked_or_for_sale,
    STRONG_GAMBLING_SIGNALS,
    BARE_CATEGORY_SIGNALS,
    HOSPITALITY_OVERRIDE_SIGNALS,
)
from checking_url.text_signals import text_signals, merge_trailing_numbers

PROTECTED_ARCHIVE = PROJECT_ROOT / "output" / "screenshots"
IMAGE_EXTENSIONS = (".jpg", ".jpeg", ".png")
INSTITUTIONAL_ARCHETYPES = {"educational", "commercial_banking", "video_gaming_entertainment"}

# Any one of these as a whole word in the OCR text forces the screenshot to false/ --
# every hotel / restaurant / dining / lodging site goes to false, no exceptions, even if
# casino imagery or gambling words are also on the page (a resort's own gaming floor, a
# hotel bar's "jackpot night" flyer). Unambiguous lodging/dining nouns only.
HOSPITALITY_FORCE_FALSE = (
    "hotel", "hotels", "resort", "resorts", "motel", "hostel", "guest house", "guesthouse",
    "bed and breakfast", "restaurant", "restaurants", "cafe", "café", "bistro", "brasserie",
    "steakhouse", "eatery", "diner", "trattoria", "pizzeria", "banquet hall", "fine dining", "buffet",
    "book a room", "book a table", "reserve a table", "make a reservation", "room service",
    "check-in", "check-out", "guest rooms", "our menu", "food menu", "dinner menu",
    "lunch menu", "breakfast menu", "a la carte", "events & meetings", "meetings & events",
)


def clean_path_input(raw: str) -> str:
    """Clean a path typed or pasted from Windows Explorer / PowerShell."""
    cleaned = raw.strip()
    if cleaned.startswith("& "):
        cleaned = cleaned[2:].strip()
    return cleaned.strip('"').strip("'")


def _prevent_sleep():
    """Keep Windows awake for a multi-hour batch (a prior run lost 5h to standby).
    No-op off Windows or on failure."""
    if sys.platform != "win32":
        return
    try:
        import ctypes
        ctypes.windll.kernel32.SetThreadExecutionState(0x80000000 | 0x00000001)  # CONTINUOUS|SYSTEM_REQUIRED
    except Exception:
        pass


def _restore_sleep():
    if sys.platform != "win32":
        return
    try:
        import ctypes
        ctypes.windll.kernel32.SetThreadExecutionState(0x80000000)  # CONTINUOUS only -> normal sleep policy
    except Exception:
        pass


def _refuse_if_protected_archive(input_dir: Path):
    protected = PROTECTED_ARCHIVE.resolve()
    resolved = input_dir.resolve()
    live_archives = {protected, (protected / "New folder").resolve()}
    if resolved in live_archives or resolved in protected.parents:
        raise SystemExit(
            f"Refusing to run: '{resolved}' is (or contains) the protected archive "
            f"'{protected}'. Nothing may ever move a file out of the live archive -- "
            f"point this at a copy (e.g. 'New folder - Copy') or a different folder."
        )


def _guess_domain_from_filename(path: Path) -> str:
    """Best-effort label for vision model prompt from capture filename.

    Filenames are saved as  <domain-with-underscores>_<8-hex-hash>.<ext>
    e.g. energyonline_casino_8851bede.jpg -> energyonline.casino
         jellybean_bet_e3ced0f9.jpg       -> jellybean.bet
    We reconstruct the last segment before the hash as the TLD so that
    is_gambling_domain() can use it as a domain anchor signal.
    """
    stem = path.stem
    parts = stem.rsplit("_", 1)
    if len(parts) == 2 and len(parts[1]) == 8:
        label = parts[0]  # e.g. "energyonline_casino"
        # Reconstruct as a pseudo-domain: last underscore segment becomes TLD
        # e.g. "energyonline_casino" -> "energyonline.casino"
        label_parts = label.rsplit("_", 1)
        if len(label_parts) == 2:
            return f"{label_parts[0]}.{label_parts[1]}"
        return label
    return stem


def _despace(s: str) -> str:
    return re.sub(r"[^a-z0-9]", "", s.lower())


def _ocr_keyword_hits(ocr_lower: str, keywords: set) -> tuple[list[str], list[str]]:
    """Word-boundary keyword matches in OCR text, split into (strong, weak).

    The old code did ``kw in ocr_lower`` -- a bare substring test -- so weak terms
    like "win"/"bet"/"stake" matched inside "winter"/"between"/"mistake" and flagged
    ordinary sites as gambling. Word boundaries + the strong/weak split (mirroring
    checking_url.classifier) are what keep that from happening. Bare category words
    ("casino"/"poker"/"slot") are treated as weak here, same as the main classifier
    treats them -- a lone incidental "Casino" in OCR is not proof on its own.

    SECOND PASS (added 2026-09-05): RapidOCR reliably drops inter-word spaces on long,
    paragraph-width text lines -- confirmed on real captures, e.g. a whole marketing
    sentence comes back as "isa modemonlinecasinobrandcreatedforplayers..." with the
    spaces gone. That silently defeats \\b{kw}\\b matching on exactly the highest-value
    text: "online casino", "real money", "licensed by", "responsible gambling" etc. --
    which is why hundreds of unambiguous real-money operators (self-describing as
    "operates as a real-money online casino", "licensed under Curacao gaming license")
    were scoring only 1-2 weak keywords and landing in false/. Scoped to phrases (>= 2
    words): word boundaries are meaningless once spaces are gone, so a despaced
    SUBSTRING check is used, but only for multi-word phrases -- a single despaced word
    would just be an unbounded substring search with no boundary protection at all.
    Additive only (mirrors classifier._deobfuscate_text's leetspeak pass) -- never
    replaces the primary word-boundary check, only adds matches it missed.
    """
    strong, weak = [], []
    despaced_text = None
    for kw in keywords:
        hit = bool(re.search(rf"\b{re.escape(kw)}\b", ocr_lower))
        if not hit and " " in kw:
            if despaced_text is None:
                despaced_text = _despace(ocr_lower)
            hit = _despace(kw) in despaced_text
        if hit:
            if kw in STRONG_GAMBLING_SIGNALS and kw not in BARE_CATEGORY_SIGNALS:
                strong.append(kw)
            else:
                weak.append(kw)
    return strong, weak


def decide_gambling_or_false(
    ocr_text: str,
    vision_res: dict | None,
    keywords: set,
    domain_name: str = "",
) -> dict:
    """Combine domain anchor + OCR text + Vision AI result with strict override hierarchy:

    Hierarchy:
      1. Dead / Parked / Server Error Pages -> ALWAYS false (no functional gambling interface).
      2. Physical Hospitality (Hotels / Resorts / Restaurants / Dining) -> ALWAYS false,
         even if domain contains 'casino' (e.g. firekeeperscasino.com, cherokeecasino.com),
         unless there is an explicit real-money online wagering CTA.
      3. Negative Archetypes (Video Games, Board Games, Banking, Schools) -> ALWAYS false,
         shielding console/PC games, game dev blogs, and tabletop shops from weak keyword noise.
      4. Real-Money Gaming APKs & Online Operators -> Confirmed gambling via:
         - Strong gambling signals (Rummy, Teen Patti, Color Prediction, Aviator, Satta, Sportsbook, Casino)
         - Verified Real-Money APK cash game pattern (Download APK/App + cash/bonus/recharge/withdrawal)
         - Domain anchor (.bet, .casino, .poker, or domain keyword)
         - Vision AI operator confirmation
         - Weak keywords now require >= 3 hits AND a core game category word (no more win+bonus false positives).
    """
    ocr_lower = (ocr_text or "").lower()
    # Merged copy for keyword matching only ("laser 247" -> "laser247"). The
    # dead/parked/hospitality/archetype gates below keep reading the RAW text.
    ocr_merged_lower = merge_trailing_numbers(ocr_lower)
    # checking_url.text_signals: contextual marketing copy (min-deposit / bonus% /
    # instant-withdrawal / e-wallet lists / refer-&-earn / betting-ID funnel) and
    # WhatsApp/Telegram contact values.
    ts = text_signals(ocr_text or "")
    # Domain anchor is needed by both Step 2 (physical-casino check) and Step 4.
    domain_anchor, domain_signal = is_gambling_domain(domain_name) if domain_name else (False, "")

    # ── Step 1: Dead Page / Parked Domain / Server Error Check (Highest Priority)
    is_dead, dead_reason = is_dead_or_error_page(ocr_lower)
    is_parked, parked_markers = is_parked_or_for_sale(ocr_lower)
    if is_dead or is_parked:
        reason = dead_reason if is_dead else f"parked/for-sale ({parked_markers[0]})"
        return {
            "is_gambling": False,
            "is_institutional": True,
            "domain_anchor": False,
            "evidence": f"dead/error/parked page ({reason}) -> false",
        }

    # ── Step 2: Physical Hospitality / Hotel / Resort Check
    # Physical casino hotels (e.g. FireKeepers, Cherokee, Chinook Winds, Chumash)
    # have 'casino' in domain but their website is a physical resort/lodging/dining amenity.
    is_hosp, hosp_hits = is_hospitality_site(ocr_lower)
    hosp_force_matches = [w for w in HOSPITALITY_FORCE_FALSE if re.search(rf"\b{re.escape(w)}\b", ocr_lower)]
    vision_category = str((vision_res or {}).get("category", "")).lower()
    vision_is_hosp = bool((vision_res or {}).get("is_institutional")) or vision_category in {"hotel_resort"}

    # Physical land-based casino (FireKeepers, Casino Nanaimo, Paradise/Quechan, NagaWorld,
    # Grey Eagle...): a brick-and-mortar venue brochure, not an online operator. Terms
    # chosen to be things an online-only casino's site essentially never carries -- a
    # nav bar of "Dining / Entertainment / Rewards Club / Box Office", "plan your visit",
    # "getting here", tribal/community context. ANY one hit + no online real-money CTA
    # => physical venue => false. ("promotions" / "vip" / "live casino" are NOT here --
    # online sites use those too.)
    physical_casino = re.search(
        r"\b(gaming floor|casino floor|slot machines on our|"
        r"hotel (?:&|and) casino|casino (?:resort|hotel)|resort (?:&|and) casino|"
        r"stay (?:&|and) play|plan your (?:visit|stay)|getting here|things to do|"
        r"directions (?:&|and) parking|hours (?:&|and) directions|valet|"
        r"fine dining|dining|buffet|steakhouse|food court|box office|showroom|"
        r"live entertainment|weddings|banquet|meetings? (?:&|and) events|"
        r"rewards club|players club|player rewards|rv park|"
        r"tribal (?:administration|gaming|council)|community center)\b",
        ocr_lower,
    ) is not None

    if is_hosp or hosp_force_matches or vision_is_hosp or physical_casino:
        # Check if there is an explicit real-money online wagering CTA (real online operator)
        has_online_operator_cta = any(
            re.search(rf"\b{re.escape(cta)}\b", ocr_lower)
            for cta in ("upi deposit", "instant upi withdrawal", "play for real money", "win real cash", "online sportsbook", "claim free spins")
        )
        if not has_online_operator_cta:
            match_word = ("physical casino floor" if physical_casino and not (hosp_force_matches or hosp_hits)
                          else hosp_force_matches[0] if hosp_force_matches
                          else hosp_hits[0] if hosp_hits else "hotel/resort")
            return {
                "is_gambling": False,
                "is_institutional": True,
                "domain_anchor": False,
                "evidence": f"physical hotel/resort/casino venue ({match_word}) -> false",
            }

    # ── Step 3: Negative Archetypes (Video Games, Board Games, Banking, Schools) +
    #           editorial content (a blog post / news article / wiki ABOUT gambling)
    is_neg, neg_reason = detect_negative_archetype(ocr_lower)
    is_editorial, editorial_hits = looks_like_editorial(ocr_lower)
    vision_institutional = bool((vision_res or {}).get("is_institutional")) or vision_category in {
        "school_education", "bank_financial"
    }
    if is_editorial:
        neg_reason = neg_reason or f"editorial ({', '.join(editorial_hits[:2])})"
    if is_neg or is_editorial or vision_institutional:
        has_real_money_cta = any(
            re.search(rf"\b{re.escape(cta)}\b", ocr_lower)
            for cta in ("play for real money", "win real cash", "upi withdrawal", "instant withdrawal", "real money game", "real cash app")
        )
        if not has_real_money_cta:
            reason = neg_reason if (is_neg or is_editorial) else "vision institutional"
            return {
                "is_gambling": False,
                "is_institutional": True,
                "domain_anchor": False,
                "evidence": f"institutional/entertainment archetype ({reason}) -> false",
            }

    # ── Step 4: Evaluate Gambling Signals (Domain Anchor, Vision AI, OCR)
    # (domain_anchor / domain_signal already computed near the top)
    vision_gambling = bool((vision_res or {}).get("is_gambling"))
    strong_hits, weak_hits = _ocr_keyword_hits(ocr_merged_lower, keywords)

    # Real-money Gaming APK pattern:
    # An EXPLICIT app-distribution action (bare "app"/"register" are far too common to
    # count) combined with a real cash-game CATEGORY word (bare "bonus"/"cash"/"deposit"
    # alone are not it -- they appear on every e-commerce and rewards page).
    has_app_action = any(
        re.search(rf"\b{re.escape(a)}\b", ocr_lower)
        for a in ("download apk", "download app", "install apk", "install app", "apk download", ".apk")
    )
    has_cash_game_vocab = any(
        re.search(rf"\b{re.escape(g)}\b", ocr_lower)
        for g in (
            "rummy", "teen patti", "teenpatti", "color prediction", "colour prediction",
            "aviator", "daman", "wingo", "tiranga", "andar bahar", "dragon tiger",
            "refer and earn", "win cash", "real cash", "paisa", "withdrawal",
        )
    )
    is_apk_cash_game = has_app_action and has_cash_game_vocab

    # Weak keywords: require at least 3 distinct keywords AND at least one core category word
    # (eliminates video game false alarms where a review simply had 'win' and 'bonus')
    has_core_category = any(
        re.search(rf"\b{re.escape(w)}\b", ocr_lower)
        for w in ("casino", "slot", "slots", "poker", "bet", "betting", "roulette", "baccarat", "rummy", "satta", "lottery", "lotto", "jackpot")
    )
    strong_weak_evidence = len(weak_hits) >= 3 and has_core_category

    # text_signals corroboration (both precision-gated with a core gambling category word so
    # a forex broker / cashback site with "bonus" + "instant withdrawal" can't trip these;
    # real forex/banking already force-false at Step 3's trading_fintech archetype anyway):
    #   - marketing copy: >= ~3 contextual indicators + a core category word
    #   - betting-ID funnel: a WhatsApp/Telegram contact VALUE + a core category word
    ctx_score = float(ts.get("contextual_score") or 0.0)
    ctx_reasons = ts.get("contextual_reasons") or []
    strong_marketing = ctx_score >= 0.45 and has_core_category
    funnel_contact = bool(ts.get("contacts")) and has_core_category

    # Corroborated on-page proof of an OPERATOR (not a page ABOUT gambling): a real-money
    # APK cash-game pattern, gambling marketing copy, or a betting-ID contact funnel.
    corroborated = is_apk_cash_game or strong_marketing or funnel_contact

    # A lone OCR keyword hit -- one strong phrase ("online casino"/"sportsbook"/"live
    # dealer"), or 3+ weak keywords -- with NO domain TLD/keyword anchor and NO
    # corroboration is the #1 false-positive source here: iGaming trade press, the Ontario
    # regulator's own site, casino-review blogs, spam-injected WordPress all QUOTE those
    # phrases without BEING an operator. Not enough to auto-sort to gambling on its own.
    lone_ocr = (bool(strong_hits) or strong_weak_evidence) and not (domain_anchor or corroborated)

    # ponytail: lone_ocr pages are routed to the vision model (vision_would_help below),
    # which sees the newsroom / hotel / blog and confirms false. In --fast mode there is no
    # vision, so lone_ocr -> false (a screenshot sort erring toward false/ on an ambiguous
    # keyword is the right call -- the reported bug is FPs in gambling/, not misses).
    on_page_gambling = corroborated or (bool(strong_hits) and domain_anchor) or (strong_weak_evidence and domain_anchor)

    is_gambling = (
        on_page_gambling
        or (vision_gambling and (bool(strong_hits) or strong_weak_evidence or bool(weak_hits) or domain_anchor))
    )

    # Build detailed evidence
    evidence_bits = []
    if is_gambling:
        if strong_hits:
            evidence_bits.append(f"ocr strong signal: {strong_hits[0]}")
        if domain_anchor:
            evidence_bits.append(f"domain anchor: {domain_signal}")
        if is_apk_cash_game:
            evidence_bits.append("real-money gaming APK pattern")
        if strong_marketing:
            evidence_bits.append(f"marketing copy: {', '.join(ctx_reasons[:3])}")
        if funnel_contact:
            chans = ",".join(ts.get("contact_channels") or [])
            evidence_bits.append(f"betting-ID contact funnel ({chans})")
        if vision_gambling:
            ev = (vision_res or {}).get("visual_evidence", "gambling UI detected")
            evidence_bits.append(f"vision: {ev}")
        if strong_weak_evidence and not (strong_hits or vision_gambling):
            evidence_bits.append(f"ocr_keywords: {', '.join(weak_hits[:3])}")
    else:
        if lone_ocr:
            hint = strong_hits[0] if strong_hits else ", ".join(weak_hits[:3])
            evidence_bits.append(f"lone OCR keyword ({hint}), no anchor/corroboration -- routed to vision, kept false")
        elif len(weak_hits) in (1, 2):
            evidence_bits.append(f"insufficient/ambiguous OCR keywords ({', '.join(weak_hits)}) -- kept as false")
        else:
            evidence_bits.append("no gambling evidence found in domain, vision, or OCR")

    # Would a Vision AI call actually change this verdict? Only when it could push an
    # otherwise-false page TO gambling (a weak hit / domain anchor to corroborate), pull a
    # verdict BACK to false (a lone OCR keyword with no corroboration, or a
    # hospitality/archetype hint below the force-false bar), or when there's almost no OCR
    # text at all (canvas/WebGL UI only vision can read). Everywhere else the OCR verdict is
    # conclusive and the vision call is pure wasted latency -- run() skips it.
    vision_would_help = (
        lone_ocr
        or (not on_page_gambling and (bool(weak_hits) or domain_anchor))
        or (is_gambling and (is_hosp or bool(hosp_hits) or is_neg or is_editorial))
        or (not is_gambling and not weak_hits and not domain_anchor
            and len(ocr_lower.split()) < 3)
    )

    return {
        "is_gambling": is_gambling,
        "is_institutional": False,
        "domain_anchor": domain_anchor,
        "evidence": "; ".join(evidence_bits),
        "contacts": ts.get("contacts") or {},  # WhatsApp/Telegram/phone values from OCR
        "vision_would_help": bool(vision_would_help),
    }


async def run(
    input_dir: str,
    output_dir: str = None,
    recursive: bool = False,
    dry_run: bool = False,
    limit: int = 0,
    fast: bool = False,
    workers: int = 6,
) -> dict:
    in_path = Path(clean_path_input(str(input_dir)))
    if not in_path.is_dir():
        raise SystemExit(f"Input folder not found: {input_dir}")
    _refuse_if_protected_archive(in_path)

    if not fast:
        try:
            await start_ollama_if_needed()
        except Exception as e:
            print(f"[!] Warning: Local AI check failed: {e}")

    out_path = Path(clean_path_input(str(output_dir))) if output_dir else in_path
    gambling_dir = out_path / "gambling"
    false_dir = out_path / "false"

    pattern = "**/*" if recursive else "*"
    images = [
        p for p in in_path.glob(pattern)
        if p.suffix.lower() in IMAGE_EXTENSIONS
        and gambling_dir not in p.parents and false_dir not in p.parents
        and is_valid_screenshot(str(p))
    ]

    print(f"\n[visual_check] Found {len(images)} valid screenshot image(s) in {in_path}"
          f"{' (DRY RUN - files will not move)' if dry_run else ''}"
          f"{' [FAST: OCR-only, no Vision AI]' if fast else ''}.")

    if not images:
        return {"checked": 0, "gambling": 0, "false": 0, "vision_calls": 0}

    if limit > 0:
        images = images[:limit]
        print(f"[visual_check] Limiting to first {limit} screenshot(s).")

    keywords = load_keywords()
    get_ocr_engine()  # warm the shared OCR singleton once, before the worker pool races on it
    stats = {"checked": 0, "gambling": 0, "false": 0, "vision_calls": 0, "ocr_only": 0, "errors": 0}

    queue: asyncio.Queue = asyncio.Queue()
    for p in images:
        queue.put_nowait(p)
    pbar = tqdm(total=len(images), desc=("OCR sort" if fast else "Visual audit"),
                unit="img", dynamic_ncols=True)

    async def process(img_path: Path):
        if not img_path.exists():
            return
        domain_name = _guess_domain_from_filename(img_path)
        label = img_path.stem.rsplit("_", 1)[0] if "_" in img_path.stem else img_path.stem

        ocr_text = await asyncio.to_thread(extract_ocr_text, str(img_path))

        # 1) OCR + domain rules decide first (~0.1s). 2) only spend a Vision AI call
        # (5-60s) when it could actually flip THIS verdict -- see decide_gambling_or_false's
        # `vision_would_help`. In practice ~85-90% of screenshots are conclusive from OCR
        # alone (strong keyword, .casino TLD, dead/parked, clear hotel).
        result = decide_gambling_or_false(ocr_text, None, keywords, domain_name=domain_name)
        if not fast and result.get("vision_would_help"):
            vision_res = await classify_screenshot_for_sorting(str(img_path), label=label)
            if vision_res:
                result = decide_gambling_or_false(ocr_text, vision_res, keywords, domain_name=domain_name)
            stats["vision_calls"] += 1
        else:
            stats["ocr_only"] += 1

        stats["checked"] += 1
        bucket = "gambling" if result["is_gambling"] else "false"
        stats[bucket] += 1
        tag = "GAMBLING" if result["is_gambling"] else "FALSE"
        _c = result.get("contacts") or {}
        _c_note = f"  contacts={sum(len(v) for v in _c.values())}" if _c else ""
        tqdm.write(f"  [{tag}] {img_path.name} -- {result['evidence'][:90]}{_c_note}")

        if not dry_run and img_path.exists():
            dest_dir = gambling_dir if result["is_gambling"] else false_dir
            dest_dir.mkdir(parents=True, exist_ok=True)
            try:
                shutil.move(str(img_path), str(dest_dir / img_path.name))
            except FileNotFoundError:
                tqdm.write(f"  [WARN] File already moved: {img_path.name}")
            except Exception as move_err:
                tqdm.write(f"  [ERROR] Failed to move {img_path.name}: {move_err}")

    async def worker():
        while True:
            try:
                img_path = queue.get_nowait()
            except asyncio.QueueEmpty:
                return
            try:
                await process(img_path)
            except Exception as e:
                stats["errors"] += 1
                tqdm.write(f"  [ERROR] {img_path.name}: {type(e).__name__}: {e}")
            finally:
                pbar.update(1)
                queue.task_done()

    n_workers = max(1, workers)
    _prevent_sleep()
    try:
        await asyncio.gather(*(asyncio.create_task(worker()) for _ in range(n_workers)))
    finally:
        pbar.close()
        _restore_sleep()
        if not fast:
            await close_ai_session()

    print("\n" + "=" * 65)
    print("         VISUAL SCREENSHOT AUDIT SUMMARY" + (" (DRY RUN)" if dry_run else ""))
    print("=" * 65)
    print(f"  Total audited           : {stats['checked']:,}")
    print(f"  -> gambling/            : {stats['gambling']:,}  ({gambling_dir})")
    print(f"  -> false/               : {stats['false']:,}  ({false_dir})")
    print(f"  Vision AI calls         : {stats['vision_calls']:,}  (OCR-only: {stats['ocr_only']:,})")
    if stats["errors"]:
        print(f"  Errors (skipped)        : {stats['errors']:,}")
    print("=" * 65 + "\n")
    return stats


def main():
    parser = argparse.ArgumentParser(
        description="Visually audit a folder of screenshots (OCR + lazy Vision AI) and sort into gambling/ vs false/ folders",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  python -m project_sup.visual_check --input "output/screenshots/New folder - Copy"
  python -m project_sup.visual_check --input ./batch1 --fast            # OCR only, no Vision AI (10-20x faster)
  python -m project_sup.visual_check --input ./batch1 --workers 10
  python -m project_sup.visual_check --input ./batch1 --dry-run --limit 50
""",
    )
    parser.add_argument("--input", default=None, help="Folder of screenshots to visually audit (prompts if omitted)")
    parser.add_argument("--output", default=None, help="Folder to create gambling/ and false/ subfolders in (default: same as --input)")
    parser.add_argument("--recursive", action="store_true", help="Also scan subfolders of --input")
    parser.add_argument("--dry-run", action="store_true", help="Preview classification and moves without touching files")
    parser.add_argument("--limit", type=int, default=0, help="Max screenshots to audit (0 = all)")
    parser.add_argument("--fast", action="store_true", help="OCR + rules only, skip Vision AI entirely (fastest)")
    parser.add_argument("--workers", type=int, default=6, help="Parallel workers (default 6)")
    args = parser.parse_args()

    input_dir = args.input
    if not input_dir:
        default_dir = str(PROJECT_ROOT / "output" / "screenshots" / "New folder - Copy")
        print("\n--- Visual Screenshot Audit (OCR + lazy Vision AI) ---")
        typed = clean_path_input(input(f"Enter screenshots folder to audit [default: {default_dir}]: "))
        input_dir = typed or default_dir

    asyncio.run(
        run(
            input_dir=input_dir,
            output_dir=args.output,
            recursive=args.recursive,
            dry_run=args.dry_run,
            limit=args.limit,
            fast=args.fast,
            workers=args.workers,
        )
    )


def _selfcheck() -> int:
    """No images / no Ollama -- assert the FP-critical branches of decide_gambling_or_false.
    Run: python -m project_sup.visual_check --selfcheck"""
    from checking_url.classifier import load_keywords
    kw = load_keywords()
    D = lambda ocr, dom="", vis=None: decide_gambling_or_false(ocr, vis, kw, domain_name=dom)

    # lone OCR strong keyword, no anchor -> NOT gambling, but hand to vision
    r = D("The latest iGaming industry news. Online casino coverage, interviews, insights.", "igamingnews.com")
    assert not r["is_gambling"] and r["vision_would_help"], r

    # same, but on a real gambling TLD -> gambling
    assert D("Play our online casino and live dealer games. Register now.", "x.casino")["is_gambling"]

    # physical casino venue (gaming floor) on a casino-named domain -> institutional false
    r = D("Casino Nanaimo. Dine at The Well Public House. 400 slot machines on our gaming floor.", "casinonanaimo.com")
    assert not r["is_gambling"] and r["is_institutional"], r

    # marketing copy + core category -> gambling (operator)
    assert D("Best casino. Min deposit Rs 100. Bonus 100%. Instant withdrawal 24x7. Refer and earn. All bank accepted deposit.", "x.xyz")["is_gambling"]

    # APK real-money cash game -> gambling
    assert D("Download APK now. Teen patti and rummy. Refer and earn. Instant withdrawal.", "x.xyz")["is_gambling"]

    # dead / clean -> false
    assert not D("404 not found. The requested url was not found on this server.", "x.com")["is_gambling"]
    assert not D("Springfield Public Library catalog membership renewal reading room hours.", "splib.org")["is_gambling"]

    # 3+ weak keywords, no anchor (casino-review blog) -> NOT gambling on OCR alone
    r = D("Ultimate guide: casino winners and their jackpot stories. Casino jackpots blog.", "norefuge.net")
    assert not r["is_gambling"] and r["vision_would_help"], r

    # vision confirms + a weak hit -> gambling
    assert D("welcome bonus play", "x.com", vis={"is_gambling": True, "visual_evidence": "roulette wheel + bet slip"})["is_gambling"]

    print("visual_check.decide_gambling_or_false self-check: OK")
    return 0


if __name__ == "__main__":
    if "--selfcheck" in sys.argv:
        raise SystemExit(_selfcheck())
    main()
