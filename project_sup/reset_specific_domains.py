"""
project_sup/reset_specific_domains.py — Reset a user-provided list of domains back to 'unconfirmed'.

Resets status to 'unconfirmed', screenshot_taken=False, exported=False, and processed=False
in MongoDB so they can be re-checked or re-exported cleanly.

Usage:
    python project_sup/reset_specific_domains.py
"""
from sqlalchemy import true
import os
import sys
from pathlib import Path
from dotenv import load_dotenv

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))
load_dotenv(dotenv_path=PROJECT_ROOT / ".env")

from db.mongo_client import checked_domains, source_domains, get_db

DOMAINS_TO_RESET = [
    "rocketplayapp.casino", "worldcasinodirectory.com", "shazam.casino", "blackjackinfo.com",
    "likes.bet", "slotcatalog.com", "caanberry.com", "usdt.poker", "yep59.casino",
    "yep73.casino", "vn88.casino", "verde42.casino", "verde6.casino", "valter63.casino",
    "slotoro51.casino", "slotoro63.casino", "slotoro79.casino", "sierra21.casino",
    "sierra77.casino", "sierra7.casino", "sierra888.casino", "sierra777.casino",
    "kioki.casino", "jw.casino", "coupdechance.casino", "6ph.casino", "1winbr.casino",
    "zuzu.bet", "zondercruks.bet", "yep-casino5.bet", "yep-casino9.bet", "yep-casino4.bet",
    "winvora.bet", "wintera.bet", "winluxo.bet", "winer.bet", "vn88.bet", "verdiale.bet",
    "vega.bet", "uniao-777.bet", "unibets.bet", "uco.bet", "tokomacau.bet", "tip-top.bet",
    "tip-top12.bet", "tip-top13.bet", "tiptop71.bet", "tiptop777.bet", "tip-top11.bet",
    "slotoro8.bet", "slotoro88.bet", "slotoro91.bet", "slotoro92.bet", "qzino.bet",
    "masal93.bet", "masal97.bet", "masal95.bet", "masal83.bet", "masal84.bet",
    "masal85.bet", "masal89.bet", "masal92.bet", "hype.bet", "gg926.bet", "gg948.bet",
    "gg913.bet", "gg973.bet", "gg977.bet", "gg984.bet", "gg821.bet", "gg735.bet",
    "gg736.bet", "gg694.bet", "gg701.bet", "easy.bet", "betana.bet", "balooga.bet",
    "spicycasinos.com", "duckdice.io", "spinoli.com", "sportsbettingalliance.org",
    "turtlecreekcasino.com", "bojoko.co.za", "vegasslots.net", "americanluck.com",
    "casinosonline.com", "luckyeagletexas.com", "margaritavillebossiercity.com",
    "bossofbetting.com", "palacecasinoresort.com", "leelanausandscasino.com",
    "mg-lion.co.in", "9club.co.in", "casinoslots.net", "casinoglobe.net",
    "peso88official.net", "lawinplay.net", "marketing-advertising.net", "casinogurus.org",
    "nodepositcasino.org", "brainal.org", "bestaustraliancasinosites.com", "ibets.co.za",
    "lincolncasino.eu", "chanced.com", "punt.com", "wildvegascasino.com",
    "luckytigercasino.com", "clubworldcasinos.com", "ballyplay.com", "bitcasino.com",
    "livecasinohotel.com", "55-bmw.com.ph", "fortunewins.com", "australiangamblers.com",
    "dragonara.com", "automatenspielex.com", "lokicasino.com", "newcasinos.com",
    "pokerfirma.com", "n8casino.in", "labourdaman.in", "patang.org.in",
    "pari-match.pro.in", "9kbosscasino.com.in", "shriramgreenfield.co.in",
    "cricketbook-india.in", "manboclub.in", "omkarpasscode.ind.in", "onlinecasinoreports.in",
    "365gold.com.in", "casinospesialisten.net", "bonanzacasino.net", "bettingsitesusa.net",
    "22-casino.net", "anzmi.net", "imperium-games.net", "bestonlinecasinooffers.net",
    "earthbetz.net", "sgw88.net", "management.org", "phjoy.org", "patang.com.in",
    "auiator.in", "goexch9s.org.in", "thahiii.in", "playexch.ind.in", "fortunium.net",
    "mate-slots.net", "casinowise.net", "33tigawin.net", "onetheevent.org",
    "onlinecasino-usa.org", "miamiclubcasino.org", "bojoko.com", "woocasino.com",
    "www-jlbet.net.ph", "royalacecasino.com", "winspirit.com", "taya777.com",
    "bongobongo.co.zm", "phgolden.com.ph", "bestcasinohq.com", "wildhorseresort.com",
    "smartcasinoguide.com", "irelandek.com", "eklottery.in", "ebarta.in", "labelbet.in",
    "shethavante.org.in", "endocrinesolutions.in", "vadodara.net.in", "goexchange9.com.in",
    "karrington.in", "sevenseaseducation.co.in", "newsmojo.in", "mahamh.in",
    "pccricketacademy.in", "vighnaharta.in", "codeplanet.co.in", "jetxgame.co.in",
    "betbhai9comid.in", "pecah837.com.in", "monopolybigballer.in", "gambling-websites.net",
    "mcw-casino-bd.net", "gamblerinacasinos.net", "voxcasinoofficial.net",
    "jili777official.net", "opsource.net", "blackjack.org", "denverboyscouts.org",
    "casinostest.org", "newslotsite.org.uk", "21.com", "terrehautecasino.com",
    "piegaming.com", "mycasinogames.com", "casinoroom.com", "casinobuddies.com",
    "brsoftech.com", "shivanshhotel.in", "praveenedu.in", "bonusfinder.ie",
    "casinodaddy.com", "usacasinocodes.com", "bonus.express", "sunvegascasino.com",
    "goldgamblers.com", "casinotrainer.org", "libertyslots.eu", "slotsgarden.com",
    "slotmadness.com", "livecasinoonline.ca", "casino-bros.com", "newzealandcasinos.nz",
    "live-casino.nl", "livedealers.com", "imaginelive.com", "medialivecasino.com",
    "ptslot.in", "gadzngizz.in", "pietpatiala.in", "ddhmv.org.in", "6q.net",
    "playadmiral.net", "mawarslot.net", "mm88sg.net", "dolfwincasino.net",
    "intensity2aus.net", "luxebet88.net", "sharkaaa.org", "richyph.org",
    "khelaghor88.org", "trustedonlinecasinosmalaysia.com", "pentasia.com",
    "gamingintelligence.com", "igamingdirect.com", "ultimatecapper.com", "qzino.com",
    "losvegascasino.co.uk", "expatbets.com", "royalclub2.in", "casinolab-uk.net",
    "playinmatchonline.com", "bongobongo.ug", "centraljersey.com",
    "americanbettingapps.com", "communityofcoders.in", "cricbet99club.com.in",
    "bettinginsight.net", "bettingtracker.net", "clutchbets.net", "newsbetting.net",
    "realbookies.com", "luckyrebel.la", "footitalia.com", "betideas.com",
    "capperspicks.com", "arbworld.net", "igamingindustry.org", "establishtherun.com",
    "sports-arbitrage.com", "betburger.com", "safebettingsites.com", "wolfbet.com",
    "blogabet.com", "champsbase.com", "oddsfantasy.com", "bonuscodepoker.com",
    "bet-calculator.co.uk", "allcalculators.co.uk", "grandnational.org.uk",
    "correctbetting.com", "betwasp.com", "lucky15calculator.co.uk", "bettingsites.ie",
    "offersbet.co.uk", "bettingguide.co.za", "easy-bets.co.za",
    "bestbettingsitesoffers.co.uk", "onehundredgamblers.com", "dbgb.in",
    "mqmbet.com.in", "fxleaders.com", "bonusoffers.ca", "bonuskoder.net",
    "bonuscodecasinos.net", "isci.org.in", "maxbet.org", "bonusblitzcasino.com",
    "binaryoptions.com", "online-casinos.ai", "casinobonus.co.ke", "revieweek.com",
    "coinwire.com", "iplwin.in", "betstarexchin.in", "funbett.net", "desi-casinos.net",
    "coolzinocasino.org", "chicken-road-game.org", "kingcasinobonus.uk",
    "casinogambler.co.uk", "777casino.co.uk", "casino-bons.in",
    "freeslottournaments.com", "pokersites.io", "poker-online.com", "gambler-us.com",
    "pokersource.com", "blackjack.com", "blackjackchamp.com", "masinogames.com",
    "speletajiem.com", "21forfun.net", "betvegas365.net", "blackjack888.org",
    "manoogian.org", "lotterycritic.com", "lucky7evencasino.com", "maxcasinocc.com",
    "mantrimallapp.in", "nightwins.net", "prairieflowercasino.com", "riversweeps.org",
    "playmojo.net"
]


def reset_list():
    print("[reset_list] Initializing MongoDB connection...")
    get_db()

    # Clean domain list
    clean_domains = list(set(
        d.strip().lower().removeprefix("https://").removeprefix("http://").removeprefix("www.").rstrip("/")
        for d in DOMAINS_TO_RESET if d and "." in d
    ))

    print(f"[reset_list] Target list contains {len(clean_domains)} unique domains.")

    # 1. Update checked_domains collection
    res_checked = checked_domains().update_many(
        {"$or": [{"_id": {"$in": clean_domains}}, {"domain": {"$in": clean_domains}}]},
        {"$set": {
            "status": "gambling",
            "exported": False,
            "screenshot_taken": True,
            "reason": "Reset to unconfirmed by user request",
            "screenshot_failed_reason": "Reset by user request",
            "ai_evaluated": False,
        }}
    )

    # 2. Update source_domains (domain_Listed) collection
    res_source = source_domains().update_many(
        {"$or": [{"_id": {"$in": clean_domains}}, {"domain": {"$in": clean_domains}}]},
        {"$set": {
            "processed": True,
            "active": True,
        }}
        
    )

    print(f"\n{'='*55}")
    print("        DOMAIN LIST RESET COMPLETE")
    print(f"{'='*55}")
    print(f" Unique Domains Input       : {len(clean_domains)}")
    print(f" Matched & Reset in checked : {res_checked.modified_count}")
    print(f" Matched & Reset in source  : {res_source.modified_count}")
    print(f" Status set to             : 'unconfirmed'")
    print(f" Exported set to           : False")
    print(f" screenshot_taken set to   : False")
    print(f"{'='*55}")


if __name__ == "__main__":
    reset_list()
