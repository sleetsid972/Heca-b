# Shopify Card Checker - Button-Driven System

## Architecture Overview

This system consists of three components running across 3 VPS:

### VPS Configuration

1. **VPS1 (16GB RAM, 4 cores)** - Primary API Server
   - Runs `api.py` with 6 Hypercorn workers
   - Handles Shopify checkout requests
   - High concurrency with asyncio semaphore (5 per worker)

2. **VPS2 (8GB RAM, 4 cores)** - Secondary API Server
   - Runs `api.py` with 4 Hypercorn workers
   - Load-balanced with VPS1
   - Provides redundancy and increased capacity

3. **VPS3 (8GB RAM, 2 cores)** - Telegram Bot
   - Runs `bot.py` with Telethon
   - Handles all user interactions
   - Load-balances requests across VPS1 and VPS2
   - Health checks every 30 seconds

### Button-Driven Interface

The bot uses a **100% button-based interface** - users only type `/start` to access the main menu. All other actions are triggered by clicking inline buttons:

**Main Menu:**
- 💳 Single Check
- 📁 Mass Check
- 💎 Redeem Key
- 📊 My Info
- 📋 Plans
- 🔒 Admin Panel (admins only)

**Admin Panel Sub-Menus:**
- 🔑 Generate Keys (premium/credit keys)
- 💰 Manage Credits
- 🚫 Ban/Unban User
- 📢 Broadcast
- 🌐 Site Management (add/remove/test sites, set filter)
- 🔄 Proxy Management (add/remove/test proxies)
- 📊 Statistics
- ⚙️ Cache Status

### Key Features

#### 1. File Forwarding
- Any `.txt` file sent by any user is automatically forwarded to the owner
- Includes sender info (name, username, ID, filename)
- Owner's own files are not forwarded

#### 2. Price Filtering
- Filters: all, under5, under10, under15, under20, under30
- Strict enforcement: API returns `NO_PRODUCT_IN_PRICE_RANGE` if no matching products
- Price cache with 1-hour TTL
- Background warmup when admin changes filter

#### 3. Live Progress Bars
- Single check: "🔄 Checking card..." → final result
- Mass check: Updates every 2 seconds with progress bar and counts
- Site testing: Live per-site results with working/dead/error counts
- Proxy testing: Similar live updates

#### 4. Site Testing Logic
- For `/testsites`: Status=true for any response proving site is alive
- CARD_DECLINED, INSUFFICIENT_FUNDS = site working (Status: true)
- Only CAPTCHA, timeout, SSL errors = site dead (Status: false)

#### 5. Credit System
- Users must have credits to check cards
- Single check: 1 credit per card
- Mass check: 1 credit per card (deducted upfront, refunded on site errors)
- Premium keys: Provide credits + expiry time
- Credit keys: Add credits without expiry

## Installation

### Step 1: Install Python Dependencies

On all 3 VPS:

```bash
apt update && apt upgrade -y
apt install python3 python3-pip git -y
```

### Step 2: Clone Repository

On all 3 VPS:

```bash
mkdir -p /root/shopify-checker
cd /root/shopify-checker
# Upload files: bot.py, api.py, queries.py, .env
```

### Step 3: Install Python Packages

On all 3 VPS:

```bash
pip3 install -r requirements.txt
```

### Step 4: Configure Environment

**On VPS3 (Bot):**

Create `/root/shopify-checker/.env`:

```env
API_ID=your_telegram_api_id
API_HASH=your_telegram_api_hash
BOT_TOKEN=your_bot_token
ADMIN_IDS=123456789,987654321
API_ENDPOINTS=http://VPS1_IP:5000/shopify,http://VPS2_IP:5000/shopify
MASS_CHECK_WORKERS=20
```

**On VPS1 (Primary API):**

Create `/root/shopify-checker/.env`:

```env
WORKERS=6
PORT=5000
HOST=0.0.0.0
```

**On VPS2 (Secondary API):**

Create `/root/shopify-checker/.env`:

```env
WORKERS=4
PORT=5000
HOST=0.0.0.0
```

### Step 5: Create Initial Files

On VPS3 (Bot):

```bash
cd /root/shopify-checker
touch sites.txt proxy.txt premium.json keys.json credit_keys.json banned.txt
echo '{}' > premium.json
echo '{}' > keys.json
echo '{}' > credit_keys.json
```

### Step 6: Install Systemd Services

**On VPS1 & VPS2 (API):**

```bash
cp shopify-api.service /etc/systemd/system/
systemctl daemon-reload
systemctl enable shopify-api
systemctl start shopify-api
systemctl status shopify-api
```

**On VPS3 (Bot):**

```bash
cp shopify-bot.service /etc/systemd/system/
systemctl daemon-reload
systemctl enable shopify-bot
systemctl start shopify-bot
systemctl status shopify-bot
```

### Step 7: Verify Services

Check API health on VPS1 and VPS2:

```bash
curl http://localhost:5000/health
```

Expected response: `{"status":"healthy","workers":6}`

Check bot logs on VPS3:

```bash
journalctl -u shopify-bot -f
```

## Usage

### For Users

1. Start bot: `/start`
2. Click "💳 Single Check" and send card: `4532123456789012|12|26|123`
3. Click "📁 Mass Check" and upload `.txt` file with cards
4. Click "💎 Redeem Key" and send key code
5. Click "📊 My Info" to see credits and expiry

### For Admins

1. Click "🔒 Admin Panel"
2. Generate keys: "🔑 Generate Keys" → select plan
3. Add sites: "🌐 Site Management" → "➕ Add Site" → send URL
4. Set filter: "🌐 Site Management" → "🔄 Set Price Filter" → select filter
5. Test sites: "🌐 Site Management" → "🔍 Test All Sites"
6. Add proxies: "🔄 Proxy Management" → "➕ Add Proxy" → send proxy
7. View stats: "📊 Statistics"

## API Endpoints

### GET /health
Health check endpoint.

**Response:**
```json
{"status": "healthy", "workers": 6}
```

### GET /workers
Get worker status.

**Response:**
```json
{"total": 6, "active": 2, "available": 4}
```

### GET /product_price
Get cheapest product price for a site.

**Parameters:**
- `url` (required): Site URL
- `max_price` (optional): Maximum price filter
- `proxy` (optional): Proxy string

**Response:**
```json
{
  "price": 4.99,
  "product_id": 123456,
  "variant_id": 789012,
  "title": "Product Name"
}
```

Or error:
```json
{"error": "NO_PRODUCT_IN_PRICE_RANGE"}
```

### GET /products
Get all products from a site.

**Parameters:**
- `url` (required): Site URL
- `proxy` (optional): Proxy string

### GET /shopify
Main checkout endpoint.

**Parameters:**
- `url` (required): Site URL
- `cc` (required): Card number
- `mm` (required): Expiry month
- `yy` (required): Expiry year
- `cvv` (required): CVV
- `max_price` (optional): Maximum price filter
- `proxy` (optional): Proxy string
- `test` (optional): Set to 'true' for site testing

**Response:**
```json
{
  "Status": true,
  "Response": "Charged",
  "Gateway": "Shopify Payments",
  "product_price": 4.99,
  "order_total": 5.49,
  "raw_response": "ORDER_PLACED"
}
```

Or error:
```json
{
  "error": "NO_PRODUCT_IN_PRICE_RANGE",
  "Status": false
}
```

## Files Structure

```
/root/shopify-checker/
├── bot.py                 # Telegram bot
├── api.py                 # Shopify API
├── queries.py             # GraphQL queries
├── .env                   # Environment variables
├── requirements.txt       # Python dependencies
├── sites.txt              # List of Shopify sites
├── proxy.txt              # List of proxies
├── premium.json           # Premium users database
├── keys.json              # Premium keys database
├── credit_keys.json       # Credit keys database
├── banned.txt             # Banned user IDs
├── shopify-bot.service    # Systemd service for bot
└── shopify-api.service    # Systemd service for API
```

## Troubleshooting

### Bot Not Starting

```bash
journalctl -u shopify-bot -n 50
```

Check for:
- Invalid API_ID, API_HASH, or BOT_TOKEN
- Missing dependencies
- Python errors

### API Not Responding

```bash
journalctl -u shopify-api -n 50
curl http://localhost:5000/health
```

Check for:
- Port already in use
- Missing dependencies (curl-cffi)
- Python errors

### Cards Not Checking

1. Check API health: `curl http://VPS1_IP:5000/health`
2. Check bot logs: `journalctl -u shopify-bot -f`
3. Verify sites.txt has valid URLs
4. Test single site manually via API

### CAPTCHA Errors

If many sites return CAPTCHA_REQUIRED:
- Use high-quality residential proxies
- Rotate user agents (already done with curl_cffi)
- Reduce concurrent workers

## Performance Tuning

### Increase Concurrency

Edit `.env` on VPS3:
```env
MASS_CHECK_WORKERS=30  # Increase from 20
```

Edit `api.py` on VPS1/VPS2:
```python
MAX_CONCURRENT_REQUESTS = 10  # Increase from 5
```

Restart services:
```bash
systemctl restart shopify-api
systemctl restart shopify-bot
```

### Add More API Servers

1. Deploy api.py on additional VPS
2. Update API_ENDPOINTS in bot's .env
3. Restart bot

## Security Notes

- Keep `.env` file secure (contains bot token)
- Use strong admin authentication
- Regularly backup premium.json and keys.json
- Monitor for suspicious activity
- Use HTTPS for API endpoints (recommended: nginx reverse proxy)

## Support

For issues, contact the bot admins via Telegram.
