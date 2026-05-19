# Shopify Card Checker - Button-Driven System

A modern, high-performance Shopify card checker with a 100% button-driven Telegram interface.

## 🎯 Key Features

### 🖱️ Button-Only Interface
- Users only type `/start` - everything else is buttons
- Clean, modern UI with emojis
- Intuitive navigation with inline buttons
- No visible commands in chat

### ⚡ High Performance
- 3-VPS architecture for optimal load distribution
- Async/await throughout for maximum concurrency
- curl_cffi with Chrome 120 impersonation
- Health-aware load balancing
- Configurable worker pools

### 💰 Price Filtering System
- Strict price filtering with cache warmup
- Filters: all, under5, under10, under15, under20, under30
- Automatic refund when no products match price
- 1-hour cache TTL per site

### 📊 Live Progress Tracking
- Real-time progress bars for all operations
- Mass check: Updates every 2 seconds
- Site testing: Live per-site results
- Proxy testing: Real-time status updates

### 🔒 Complete Admin Panel
- Key generation (premium & credit keys)
- Credit management
- User ban/unban
- Broadcast messages
- Site management (add/remove/test)
- Proxy management (add/remove/test)
- Statistics dashboard
- Cache status monitoring

### 📁 Automatic File Forwarding
- All .txt files auto-forward to owner
- Includes sender metadata
- Owner's files not forwarded

### 🎯 Smart Site Testing
- Any card response = site working
- Only CAPTCHA/timeout/SSL = site dead
- Automatic dead site removal
- Batch testing with progress

## 📋 Requirements

- Python 3.11+
- Ubuntu 22.04/24.04 (or similar Linux)
- 3 VPS:
  - VPS1: 16GB RAM, 4 cores (Primary API)
  - VPS2: 8GB RAM, 4 cores (Secondary API)
  - VPS3: 8GB RAM, 2 cores (Bot)

## 🚀 Quick Start

### 1. Install Dependencies

On all 3 VPS:

```bash
apt update && apt upgrade -y
apt install python3 python3-pip git -y
```

### 2. Clone & Setup

On all 3 VPS:

```bash
mkdir -p /root/shopify-checker
cd /root/shopify-checker
```

Upload these files to all VPS:
- `bot.py` (VPS3 only)
- `api.py` (VPS1 & VPS2 only)
- `queries.py` (VPS1 & VPS2 only)
- `requirements.txt`
- `.env` (configure per VPS)

### 3. Install Python Packages

On all 3 VPS:

```bash
pip3 install -r requirements.txt
```

### 4. Configure Environment

**VPS3 (Bot) - Create `.env`:**

```env
API_ID=12345678
API_HASH=abcdef1234567890
BOT_TOKEN=123456789:ABCdefGHIjklMNOpqrsTUVwxyz
ADMIN_IDS=123456789,987654321
API_ENDPOINTS=http://VPS1_IP:5000/shopify,http://VPS2_IP:5000/shopify
MASS_CHECK_WORKERS=20
```

**VPS1 (Primary API) - Create `.env`:**

```env
WORKERS=6
PORT=5000
HOST=0.0.0.0
```

**VPS2 (Secondary API) - Create `.env`:**

```env
WORKERS=4
PORT=5000
HOST=0.0.0.0
```

### 5. Initialize Data Files

On VPS3:

```bash
cd /root/shopify-checker
touch sites.txt proxy.txt banned.txt
echo '{}' > premium.json
echo '{}' > keys.json
echo '{}' > credit_keys.json
```

Add some sites to `sites.txt`:

```bash
echo "https://example-shop.myshopify.com" >> sites.txt
echo "https://another-shop.com" >> sites.txt
```

### 6. Install Systemd Services

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

### 7. Verify Installation

Test API health:

```bash
# On VPS1
curl http://localhost:5000/health

# On VPS2
curl http://localhost:5000/health
```

Expected: `{"status":"healthy","workers":6}`

View bot logs:

```bash
# On VPS3
journalctl -u shopify-bot -f
```

## 📱 User Guide

### Getting Started

1. Open Telegram and find your bot
2. Send `/start`
3. Main menu appears with buttons

### Checking Cards

**Single Check:**
1. Click "💳 Single Check"
2. Send card: `4532123456789012|12|26|123`
3. Wait for result with BIN info, price, gateway

**Mass Check:**
1. Click "📁 Mass Check"
2. Upload `.txt` file with cards (one per line)
3. Watch live progress bar
4. Receive results file with all details

### Redeeming Keys

1. Click "💎 Redeem Key"
2. Send key code
3. Credits and expiry time added

### Checking Info

Click "📊 My Info" to see:
- User ID
- Credits balance
- Expiry date
- Active filter

### Viewing Plans

Click "📋 Plans" to see available subscription plans with prices and credits.

## 🔒 Admin Guide

### Accessing Admin Panel

1. Click "🔒 Admin Panel" (only visible to admins)
2. Choose an action

### Generating Keys

1. Click "🔑 Generate Keys"
2. Select plan type (Trial, Bronze, Silver, Gold, Platinum)
3. Or click "💳 Generate Credit Key" for custom credits
4. Copy and share key with user

### Managing Sites

**Add Site:**
1. Click "🌐 Site Management"
2. Click "➕ Add Site"
3. Send site URL

**Remove Site:**
1. Click "🌐 Site Management"
2. Click "➖ Remove Site"
3. Send site URL

**Test Sites:**
1. Click "🌐 Site Management"
2. Click "🔍 Test All Sites"
3. Watch live progress
4. Dead sites automatically removed

**Set Filter:**
1. Click "🌐 Site Management"
2. Click "🔄 Set Price Filter"
3. Select filter (all, under5, under10, etc.)
4. Cache warmup starts automatically

### Managing Proxies

**Add Proxy:**
1. Click "🔄 Proxy Management"
2. Click "➕ Add Proxy"
3. Send proxy: `ip:port:user:pass` or `ip:port`

**Test Proxies:**
1. Click "🔄 Proxy Management"
2. Click "🔍 Test All Proxies"
3. Dead proxies automatically removed

### Viewing Statistics

Click "📊 Statistics" to see:
- Total users
- Total credits in system
- Number of sites
- Number of proxies
- Cache size
- Active filter

### Checking Cache

Click "⚙️ Cache Status" to see:
- Cached sites count
- Warmup progress (if active)
- Active filter

## 🏗️ Architecture

### Three-Tier System

```
┌─────────────────────────────────────────────────┐
│          VPS3 (8GB/2c) - Bot Layer              │
│  ┌───────────────────────────────────────────┐  │
│  │  Telegram Bot (Telethon)                  │  │
│  │  - Button interface                       │  │
│  │  - User sessions                          │  │
│  │  - Health-aware load balancer             │  │
│  │  - Price cache (1h TTL)                   │  │
│  │  - Product cache (30min TTL)              │  │
│  └───────────────────────────────────────────┘  │
└─────────────────────────────────────────────────┘
                       │
            ┌──────────┴──────────┐
            │                     │
            ▼                     ▼
┌─────────────────────┐ ┌─────────────────────┐
│ VPS1 (16GB/4c)      │ │ VPS2 (8GB/4c)       │
│ Primary API         │ │ Secondary API       │
│ ┌─────────────────┐ │ │ ┌─────────────────┐ │
│ │ Quart + Hypercorn│ │ │ Quart + Hypercorn│ │
│ │ 6 workers       │ │ │ 4 workers       │ │
│ │ 5 concurrent/wkr│ │ │ 5 concurrent/wkr│ │
│ │ curl_cffi       │ │ │ curl_cffi       │ │
│ │ Chrome 120      │ │ │ Chrome 120      │ │
│ └─────────────────┘ │ │ └─────────────────┘ │
└─────────────────────┘ └─────────────────────┘
```

### Request Flow

1. **User clicks button** → Bot receives callback
2. **Bot checks credits** → Deducts if available
3. **Bot selects site** → Filters by cached price
4. **Bot calls API** → Load-balanced across VPS1/VPS2
5. **API fetches products** → Filters by max_price
6. **API performs checkout** → GraphQL flow
7. **API returns result** → Status, Response, Gateway, Price
8. **Bot formats result** → BIN info, emojis, formatting
9. **User sees result** → Clean, professional output

### Health Check System

- Bot checks API health every 30 seconds
- Only healthy endpoints receive requests
- Automatic failover if endpoint goes down
- Round-robin load balancing

### Price Cache System

- 1-hour TTL per site
- Background warmup on filter change
- Cache key: site URL
- Value: product price + expiry timestamp

### Concurrency Controls

- **Bot:** MASS_CHECK_WORKERS concurrent cards (default: 20)
- **API:** 5 concurrent requests per worker
- **VPS1:** 6 workers × 5 = 30 total concurrent
- **VPS2:** 4 workers × 5 = 20 total concurrent
- **Total capacity:** 50 concurrent checkouts

## 🔧 Configuration

### Environment Variables

See `.env.example` for all available options.

**Critical settings:**

- `API_ENDPOINTS`: Comma-separated API URLs (must end with `/shopify`)
- `MASS_CHECK_WORKERS`: Concurrent cards in mass check (20-50 recommended)
- `WORKERS`: Hypercorn workers per API server (4-8 recommended)

### Performance Tuning

**For higher throughput:**

1. Increase `MASS_CHECK_WORKERS` on bot
2. Increase `MAX_CONCURRENT_REQUESTS` in `api.py`
3. Add more Hypercorn `WORKERS`
4. Add more API VPS and update `API_ENDPOINTS`

**For lower resource usage:**

1. Decrease `MASS_CHECK_WORKERS` to 10-15
2. Decrease `MAX_CONCURRENT_REQUESTS` to 3
3. Reduce Hypercorn `WORKERS` to 2-4

### File Locations

All files on VPS3:

```
/root/shopify-checker/
├── bot.py                 # Main bot code
├── sites.txt              # One URL per line
├── proxy.txt              # One proxy per line (ip:port:user:pass)
├── premium.json           # {user_id: {credits, expiry}}
├── keys.json              # {key_code: {plan}}
├── credit_keys.json       # {key_code: credits}
├── banned.txt             # One user_id per line
└── .env                   # Configuration
```

All files on VPS1 & VPS2:

```
/root/shopify-checker/
├── api.py                 # Main API code
├── queries.py             # GraphQL queries
└── .env                   # Configuration
```

## 🐛 Troubleshooting

### Bot Not Responding

```bash
# Check status
systemctl status shopify-bot

# View logs
journalctl -u shopify-bot -f

# Restart
systemctl restart shopify-bot
```

**Common issues:**
- Invalid BOT_TOKEN
- Wrong API_ENDPOINTS (must end with `/shopify`)
- Missing dependencies

### API Not Working

```bash
# Check status
systemctl status shopify-api

# View logs
journalctl -u shopify-api -f

# Test health
curl http://localhost:5000/health

# Restart
systemctl restart shopify-api
```

**Common issues:**
- Port 5000 already in use
- Missing curl-cffi (reinstall: `pip3 install curl-cffi --upgrade`)
- Firewall blocking port 5000

### Cards Failing

**All cards dead:**
- Check if sites.txt has valid URLs
- Test site manually: `curl https://site.com/products.json`
- Check proxies are working

**CAPTCHA errors:**
- Use residential proxies
- Reduce concurrent workers
- Add more sites to pool

**NO_PRODUCT_IN_PRICE_RANGE:**
- Normal if filter is too restrictive
- Credit is automatically refunded
- Try "all" filter or higher price limit

### High Memory Usage

**Bot:**
- Reduce MASS_CHECK_WORKERS
- Clear cache: restart bot

**API:**
- Reduce WORKERS count
- Reduce MAX_CONCURRENT_REQUESTS
- Check for memory leaks (restart periodically)

## 📊 Monitoring

### View Active Workers

```bash
curl http://VPS1_IP:5000/workers
```

Response:
```json
{
  "total": 6,
  "active": 3,
  "available": 3
}
```

### View Bot Status

In bot, admin clicks:
1. "🔒 Admin Panel"
2. "📊 Statistics"

Shows:
- Total users
- Total credits
- Site count
- Proxy count
- Cache size

### System Logs

```bash
# Bot logs
journalctl -u shopify-bot -f

# API logs (VPS1)
journalctl -u shopify-api -f

# API logs (VPS2)
journalctl -u shopify-api -f
```

## 🔐 Security

### Best Practices

1. **Keep .env secure** - Contains sensitive tokens
2. **Use strong admin IDs** - Only trust known admins
3. **Regular backups** - Backup JSON files daily
4. **Monitor logs** - Watch for suspicious activity
5. **Use HTTPS** - Put nginx reverse proxy in front of APIs
6. **Rotate proxies** - Use fresh proxy pool regularly
7. **Update dependencies** - Run `pip3 install -r requirements.txt --upgrade` monthly

### Firewall Rules

**VPS1 & VPS2:**
```bash
ufw allow 5000/tcp  # API port
ufw enable
```

**VPS3:**
```bash
# Bot doesn't need open ports (outbound only)
```

### Backup Script

```bash
#!/bin/bash
# backup.sh - Run daily via cron

DATE=$(date +%Y%m%d)
BACKUP_DIR="/root/backups"
mkdir -p $BACKUP_DIR

cd /root/shopify-checker
tar -czf $BACKUP_DIR/shopify-$DATE.tar.gz \
  premium.json keys.json credit_keys.json \
  sites.txt proxy.txt banned.txt

# Keep last 7 days
find $BACKUP_DIR -name "shopify-*.tar.gz" -mtime +7 -delete
```

## 📄 License

This project is provided as-is for educational purposes.

## 🤝 Support

For issues or questions:
1. Check DEPLOYMENT.md for detailed setup
2. Review troubleshooting section above
3. Check system logs
4. Contact bot admin via Telegram

---

**Built with:**
- Python 3.11+
- Telethon (Telegram)
- Quart + Hypercorn (Async web)
- curl_cffi (HTTP client)
- Shopify GraphQL API