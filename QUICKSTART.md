# Quick Start Guide

Get your Shopify Card Checker running in 15 minutes!

## Prerequisites

- 3 VPS servers (Ubuntu 22.04/24.04)
- Telegram bot token from [@BotFather](https://t.me/BotFather)
- Telegram API credentials from [my.telegram.org](https://my.telegram.org)

## Step 1: Prepare VPS Servers (All 3)

```bash
# Update system
apt update && apt upgrade -y

# Install Python and pip
apt install python3 python3-pip git -y

# Create directory
mkdir -p /root/shopify-checker
cd /root/shopify-checker
```

## Step 2: Upload Files

### VPS1 & VPS2 (API Servers)
Upload these files:
- `api.py`
- `queries.py`
- `requirements.txt`
- `shopify-api.service`

### VPS3 (Bot Server)
Upload these files:
- `bot.py`
- `requirements.txt`
- `shopify-bot.service`

You can use SCP, SFTP, or simply copy-paste the file contents.

## Step 3: Install Dependencies (All 3)

```bash
cd /root/shopify-checker
pip3 install -r requirements.txt
```

This will install:
- telethon (Telegram)
- aiohttp, curl-cffi (HTTP)
- quart, hypercorn (Web server)
- aiofiles (Async file I/O)
- python-dotenv (Environment variables)

## Step 4: Configure Environment

### VPS1 (Primary API)

Create `/root/shopify-checker/.env`:

```env
WORKERS=6
PORT=5000
HOST=0.0.0.0
```

### VPS2 (Secondary API)

Create `/root/shopify-checker/.env`:

```env
WORKERS=4
PORT=5000
HOST=0.0.0.0
```

### VPS3 (Bot)

Create `/root/shopify-checker/.env`:

```env
# Get these from https://my.telegram.org
API_ID=12345678
API_HASH=your_api_hash_here

# Get this from @BotFather
BOT_TOKEN=123456789:ABCdefGHIjklMNOpqrsTUVwxyz

# Your Telegram user IDs (first one is owner)
ADMIN_IDS=123456789,987654321

# API endpoints (replace with your VPS IPs)
API_ENDPOINTS=http://VPS1_IP:5000/shopify,http://VPS2_IP:5000/shopify

# Performance setting
MASS_CHECK_WORKERS=20
```

**How to get your Telegram User ID:**
1. Message [@userinfobot](https://t.me/userinfobot)
2. It will reply with your ID

## Step 5: Initialize Data Files (VPS3 Only)

```bash
cd /root/shopify-checker

# Create empty files
touch sites.txt proxy.txt banned.txt

# Create empty JSON files
echo '{}' > premium.json
echo '{}' > keys.json
echo '{}' > credit_keys.json

# Add some test sites
cat > sites.txt << 'EOF'
https://example-shop.myshopify.com
https://another-shop.com
EOF
```

## Step 6: Install Systemd Services

### VPS1 & VPS2 (API)

```bash
# Copy service file
cp /root/shopify-checker/shopify-api.service /etc/systemd/system/

# Reload systemd
systemctl daemon-reload

# Enable auto-start on boot
systemctl enable shopify-api

# Start the service
systemctl start shopify-api

# Check status
systemctl status shopify-api
```

### VPS3 (Bot)

```bash
# Copy service file
cp /root/shopify-checker/shopify-bot.service /etc/systemd/system/

# Reload systemd
systemctl daemon-reload

# Enable auto-start on boot
systemctl enable shopify-bot

# Start the service
systemctl start shopify-bot

# Check status
systemctl status shopify-bot
```

## Step 7: Open Firewall (VPS1 & VPS2)

```bash
# Allow API port
ufw allow 5000/tcp

# Enable firewall
ufw enable

# Check status
ufw status
```

**Note:** VPS3 (bot) doesn't need any open ports.

## Step 8: Verify Installation

### Check API Health (VPS1 & VPS2)

```bash
curl http://localhost:5000/health
```

Expected response:
```json
{"status":"healthy","workers":6}
```

### Check Bot Logs (VPS3)

```bash
journalctl -u shopify-bot -f
```

You should see:
```
🤖 Starting Shopify Card Checker Bot...
✅ Bot is running!
```

Press `Ctrl+C` to exit logs.

## Step 9: Test in Telegram

1. Open Telegram
2. Find your bot (the username you set with @BotFather)
3. Send: `/start`
4. You should see the main menu with buttons:
   - 💳 Single Check
   - 📁 Mass Check
   - 💎 Redeem Key
   - 📊 My Info
   - 📋 Plans
   - 🔒 Admin Panel (if you're an admin)

## Step 10: Generate Your First Key

1. Click "🔒 Admin Panel"
2. Click "🔑 Generate Keys"
3. Click "Generate 🎁 TRIAL"
4. Copy the key code
5. Click "🏠 Back to Main Menu"
6. Click "💎 Redeem Key"
7. Send the key code

You now have 3000 credits to test!

## Common Issues

### "Bot not responding"

**Check logs:**
```bash
journalctl -u shopify-bot -n 50
```

**Common causes:**
- Invalid BOT_TOKEN
- Wrong API_ID or API_HASH
- Missing Python packages

**Fix:**
```bash
systemctl restart shopify-bot
```

### "No API endpoints available"

**Check API health:**
```bash
# From VPS3
curl http://VPS1_IP:5000/health
curl http://VPS2_IP:5000/health
```

**Common causes:**
- API not running on VPS1/VPS2
- Firewall blocking port 5000
- Wrong IP in API_ENDPOINTS

**Fix:**
```bash
# On VPS1 and VPS2
systemctl restart shopify-api
ufw allow 5000/tcp
```

### "Cards all failing"

**Check sites.txt:**
```bash
cat /root/shopify-checker/sites.txt
```

**Test a site manually:**
```bash
curl "https://your-site.com/products.json?limit=1"
```

Should return JSON with products.

**Fix:**
- Add valid Shopify sites to sites.txt
- Add working proxies to proxy.txt

## Next Steps

### Add More Sites

```bash
echo "https://new-shop.myshopify.com" >> /root/shopify-checker/sites.txt
```

Or use the bot:
1. Click "🔒 Admin Panel"
2. Click "🌐 Site Management"
3. Click "➕ Add Site"
4. Send the URL

### Add Proxies

```bash
echo "ip:port:user:pass" >> /root/shopify-checker/proxy.txt
```

Or use the bot:
1. Click "🔒 Admin Panel"
2. Click "🔄 Proxy Management"
3. Click "➕ Add Proxy"
4. Send the proxy

### Test Sites

1. Click "🔒 Admin Panel"
2. Click "🌐 Site Management"
3. Click "🔍 Test All Sites"
4. Watch live progress
5. Dead sites will be automatically removed

### Set Price Filter

1. Click "🔒 Admin Panel"
2. Click "🌐 Site Management"
3. Click "🔄 Set Price Filter"
4. Select a filter (e.g., "💰 Under $10")
5. Cache warmup will start automatically

### Check Statistics

1. Click "🔒 Admin Panel"
2. Click "📊 Statistics"
3. See total users, credits, sites, proxies

## Useful Commands

### View Logs

```bash
# Bot logs (VPS3)
journalctl -u shopify-bot -f

# API logs (VPS1/VPS2)
journalctl -u shopify-api -f

# Last 100 lines
journalctl -u shopify-bot -n 100
```

### Restart Services

```bash
# Restart bot
systemctl restart shopify-bot

# Restart API
systemctl restart shopify-api

# Restart all services
systemctl restart shopify-bot shopify-api
```

### Check Status

```bash
# Bot status
systemctl status shopify-bot

# API status
systemctl status shopify-api

# Check if services are enabled
systemctl is-enabled shopify-bot
systemctl is-enabled shopify-api
```

### Check Worker Status

```bash
# From VPS3 or any machine
curl http://VPS1_IP:5000/workers
curl http://VPS2_IP:5000/workers
```

Response shows active/available workers:
```json
{"total": 6, "active": 2, "available": 4}
```

## Backup & Restore

### Backup

```bash
cd /root/shopify-checker
tar -czf backup-$(date +%Y%m%d).tar.gz \
  premium.json keys.json credit_keys.json \
  sites.txt proxy.txt banned.txt .env
```

### Restore

```bash
cd /root/shopify-checker
tar -xzf backup-20240115.tar.gz
systemctl restart shopify-bot
```

## Security Tips

1. **Keep .env secure** - Never share your BOT_TOKEN
2. **Use strong passwords** - For VPS SSH access
3. **Enable UFW firewall** - Block unused ports
4. **Regular backups** - Daily cron job
5. **Update regularly** - `pip3 install -r requirements.txt --upgrade`
6. **Monitor logs** - Check for suspicious activity
7. **Use good proxies** - Residential > Datacenter

## Performance Tips

1. **More sites = better** - More variety, less chance of CAPTCHA
2. **Good proxies = better** - Residential proxies work best
3. **Adjust workers** - Increase if you have resources
4. **Use filters** - "Under $5" is fastest
5. **Monitor resources** - Use `htop` to check CPU/RAM

## Getting Help

- 📖 Read [README.md](README.md) for full documentation
- 🏗️ Read [ARCHITECTURE.md](ARCHITECTURE.md) for technical details
- 🚀 Read [DEPLOYMENT.md](DEPLOYMENT.md) for advanced setup
- 📝 Check logs with `journalctl`
- 💬 Ask in your Telegram support channel

## Summary

You now have a fully functional Shopify card checker with:
- ✅ Button-driven interface
- ✅ 3-VPS architecture
- ✅ Load balancing
- ✅ Health checks
- ✅ Price filtering
- ✅ Live progress bars
- ✅ Admin panel
- ✅ Key system

**Enjoy checking cards! 🎉**
