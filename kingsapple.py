#!/usr/bin/env python3
"""
KingsApple v2.1 — Hardened Edition
===================================
Your Personal Financial AI. No cloud. No trackers. Just intelligence.
"""

import os
import sys
import json
import time
import sqlite3
import argparse
import getpass
import random
import logging
import atexit
import re
import shutil
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Optional, List, Dict, Any, Tuple, Callable
from functools import wraps
from contextlib import contextmanager

# =============================================================================
# DEPENDENCIES
# =============================================================================
try:
    import requests
except ImportError:
    raise ImportError("pip install requests")

try:
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM
    from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC
    from cryptography.hazmat.primitives import hashes
except ImportError:
    raise ImportError("pip install cryptography")

try:
    import feedparser
except ImportError:
    feedparser = None

try:
    import yfinance as yf
except ImportError:
    yf = None

try:
    import pandas as pd
    import numpy as np
except ImportError:
    pd = None
    np = None

# =============================================================================
# LOGGING
# =============================================================================
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S"
)
logger = logging.getLogger("KingsApple")

# =============================================================================
# CONFIG
# =============================================================================
VERSION = "2.1.0"
APP_NAME = "KingsApple"
DEFAULT_VAULT = os.path.expanduser("~/KingsAppleVault")
CONFIG_PATH = Path(DEFAULT_VAULT) / "config.json"

DEFAULT_CONFIG = {
    "fred_api_key": "",
    "quiver_api_key": "",
    "sec_contact_email": "user@example.com",
    "scan_interval_hours": 4,
    "max_retries": 3,
    "retry_backoff": 2.0,
    "log_level": "INFO",
}

GREETINGS = {
    "morning": [
        "Good morning. Markets are waking up. So am I.",
        "Rise and grind. Your portfolio awaits.",
        "Morning. Coffee first, then we conquer the markets.",
    ],
    "afternoon": [
        "Afternoon. Half the trading day is behind us.",
        "Hey. Midday check — any moves worth making?",
        "Still here, still watching. What do you need?",
    ],
    "evening": [
        "Evening. Markets are closed. Time to reflect.",
        "The bell rang. Let's review today's damage... or gains.",
        "Night falls. Your money sleeps, but I don't.",
    ],
}

ADVISOR_TONE = [
    "I'm seeing something here that concerns me.",
    "This could be an opportunity if you play it right.",
    "Your portfolio is telling a story. Let me translate.",
    "I've been watching this pattern. It's familiar.",
    "Not to alarm you, but we should talk about this.",
]

# =============================================================================
# UTILITIES
# =============================================================================
def now_iso() -> str:
    """Return current UTC time in ISO format."""
    return datetime.now(timezone.utc).isoformat()

def retry(max_tries: int = 3, backoff: float = 2.0, exceptions=(Exception,)):
    """Decorator for exponential backoff retry logic."""
    def decorator(func: Callable):
        @wraps(func)
        def wrapper(*args, **kwargs):
            for attempt in range(1, max_tries + 1):
                try:
                    return func(*args, **kwargs)
                except exceptions as e:
                    if attempt == max_tries:
                        raise
                    wait = backoff ** attempt
                    logger.warning(f"{func.__name__} failed (attempt {attempt}/{max_tries}): {e}. Retrying in {wait:.1f}s...")
                    time.sleep(wait)
            return None
        return wrapper
    return decorator

def validate_table_name(name: str) -> bool:
    """Prevent SQL injection via table names."""
    return bool(re.fullmatch(r"[a-zA-Z_][a-zA-Z0-9_]*", name))

# =============================================================================
# DATA SOURCES
# =============================================================================
SOURCES = {
    "coingecko": {
        "url": "https://api.coingecko.com/api/v3",
        "coins": ["bitcoin", "ethereum", "solana", "cardano", "polkadot", "chainlink"],
    },
    "treasury": {
        "url": "https://api.fiscaldata.treasury.gov/services/api/fiscal_service",
        "yield_10y": "/v2/accounting/od/avg_interest_rates?filter=security_desc:eq:Treasury%20Bonds&sort=-record_date&limit=1",
    },
    "fear_greed": {
        "url": "https://api.alternative.me/fng/?limit=1",
    },
    "news": {
        "feeds": [
            "https://feeds.bbci.co.uk/news/business/rss.xml",
            "https://www.coindesk.com/arc/outboundfeeds/rss/",
            "https://feeds.marketwatch.com/marketwatch/topstories",
            "https://feeds.reuters.com/reuters/businessnews",
        ],
    },
}

COIN_MAP = {
    "bitcoin": "BTC",
    "ethereum": "ETH",
    "solana": "SOL",
    "cardano": "ADA",
    "polkadot": "DOT",
    "chainlink": "LINK",
}

YF_MAP = {
    "SPY": "S&P 500",
    "QQQ": "Nasdaq 100",
    "IWM": "Russell 2000",
    "GLD": "Gold",
    "SLV": "Silver",
    "USO": "Oil",
    "TLT": "Treasury 20Y",
    "HYG": "High Yield Bonds",
    "LQD": "Investment Grade Bonds",
    "EEM": "Emerging Markets",
    "VEA": "Developed Markets",
    "VNQ": "Real Estate",
    "^VIX": "VIX Fear Index",
    "DX-Y.NYB": "US Dollar",
    "EURUSD=X": "EUR/USD",
    "GBPUSD=X": "GBP/USD",
    "USDJPY=X": "USD/JPY",
    "BTC-USD": "Bitcoin (YF)",
    "ETH-USD": "Ethereum (YF)",
}

SIGNAL_KEYWORDS = {
    "war": ["war", "conflict", "invasion", "missile", "airstrike", "troop", "ceasefire", "NATO"],
    "sanctions": ["sanctions", "embargo", "trade war", "tariff", "ban", "export controls"],
    "fed": ["federal reserve", "fed rate", "interest rate", "powell", "FOMC", "monetary policy", "rate cut", "rate hike"],
    "inflation": ["inflation", "cpi", "consumer price", "hyperinflation", "deflation", "purchasing power"],
    "recession": ["recession", "gdp contraction", "economic slowdown", "stagflation", "unemployment rise"],
    "crypto_regulation": ["crypto ban", "bitcoin etf", "sec", "cryptocurrency regulation", "stablecoin", "CBDC"],
    "banking_crisis": ["bank failure", "bank run", "liquidity crisis", "credit crunch", "deposit freeze"],
    "election": ["election", "vote", "campaign", "presidential", "midterm", "ballot"],
    "earnings": ["earnings", "revenue miss", "profit warning", "guidance cut", "EPS"],
    "merger": ["merger", "acquisition", "takeover", "buyout", "deal"],
    "cyberattack": ["hack", "cyberattack", "data breach", "ransomware", "security incident"],
    "energy": ["oil price", "gas price", "OPEC", "energy crisis", "blackout", "renewable"],
}

# =============================================================================
# VAULT — AES-256-GCM File Encryption
# =============================================================================
class Vault:
    SALT_SIZE, NONCE_SIZE, ITERATIONS, KEY_SIZE = 32, 12, 480_000, 32
    ALLOWED_TABLES = {
        "market_data", "news_signals", "alerts", "user_profile",
        "holdings", "transactions", "net_worth", "watchlist",
        "journal", "goals", "research_papers", "learning_log",
        "user_feedback",
    }

    def __init__(self, path: str, password: str):
        self.vault_path = Path(path)
        self.vault_path.mkdir(parents=True, exist_ok=True)
        self.key = self._derive_key(password)
        self.encrypted_db = self.vault_path / "kingsapple.db.enc"
        self._temp_db = None
        self.db_path = self._prepare_db()
        self._init_db()
        atexit.register(self._encrypt_on_exit)

    def _derive_key(self, password: str) -> bytes:
        salt_path = self.vault_path / ".salt"
        if salt_path.exists():
            salt = salt_path.read_bytes()
        else:
            salt = os.urandom(self.SALT_SIZE)
            salt_path.write_bytes(salt)
            os.chmod(salt_path, 0o600)
        kdf = PBKDF2HMAC(algorithm=hashes.SHA256(), length=self.KEY_SIZE, salt=salt, iterations=self.ITERATIONS)
        return kdf.derive(password.encode())

    def _prepare_db(self) -> Path:
        if self.encrypted_db.exists():
            encrypted = self.encrypted_db.read_bytes()
            if len(encrypted) < self.NONCE_SIZE + 16:
                raise ValueError("Encrypted database is corrupted or too small.")
            nonce = encrypted[:self.NONCE_SIZE]
            ciphertext = encrypted[self.NONCE_SIZE:]
            aesgcm = AESGCM(self.key)
            plaintext = aesgcm.decrypt(nonce, ciphertext, None)
            fd, temp_path = tempfile.mkstemp(suffix=".db", prefix="ka_")
            os.write(fd, plaintext)
            os.close(fd)
            self._temp_db = Path(temp_path)
        else:
            fd, temp_path = tempfile.mkstemp(suffix=".db", prefix="ka_")
            os.close(fd)
            self._temp_db = Path(temp_path)
        return self._temp_db

    def _encrypt_on_exit(self):
        if not self._temp_db or not self._temp_db.exists():
            return
        try:
            plaintext = self._temp_db.read_bytes()
            nonce = os.urandom(self.NONCE_SIZE)
            aesgcm = AESGCM(self.key)
            ciphertext = aesgcm.encrypt(nonce, plaintext, None)
            self.encrypted_db.write_bytes(nonce + ciphertext)
            os.chmod(self.encrypted_db, 0o600)
        except Exception as e:
            logger.error(f"Failed to encrypt database on exit: {e}")
        finally:
            if self._temp_db.exists():
                size = self._temp_db.stat().st_size
                with open(self._temp_db, "wb") as f:
                    f.write(os.urandom(size))
                self._temp_db.unlink()

    def save(self):
        self._encrypt_on_exit()
        self.db_path = self._prepare_db()
        self._init_db()

    def _init_db(self):
        conn = sqlite3.connect(str(self.db_path))
        c = conn.cursor()
        tables = [
            ("market_data", "id INTEGER PRIMARY KEY AUTOINCREMENT, timestamp TEXT, source TEXT, asset TEXT, price REAL, change_24h REAL, volume REAL, raw_data TEXT, signal_score REAL DEFAULT 0"),
            ("news_signals", "id INTEGER PRIMARY KEY AUTOINCREMENT, timestamp TEXT, source TEXT, title TEXT, summary TEXT, url TEXT, signal_type TEXT, severity INTEGER DEFAULT 1, correlated_assets TEXT"),
            ("alerts", "id INTEGER PRIMARY KEY AUTOINCREMENT, timestamp TEXT, level TEXT, message TEXT, action TEXT, dismissed INTEGER DEFAULT 0, portfolio_related INTEGER DEFAULT 0"),
            ("user_profile", "id INTEGER PRIMARY KEY, name TEXT, risk_tolerance TEXT, investment_horizon TEXT, annual_income REAL, net_worth_target REAL, currency TEXT DEFAULT 'USD', created_at TEXT, last_login TEXT"),
            ("holdings", "id INTEGER PRIMARY KEY AUTOINCREMENT, timestamp TEXT, symbol TEXT, asset_name TEXT, quantity REAL, avg_cost REAL, asset_class TEXT, notes TEXT"),
            ("transactions", "id INTEGER PRIMARY KEY AUTOINCREMENT, timestamp TEXT, symbol TEXT, action TEXT, quantity REAL, price REAL, fees REAL, notes TEXT"),
            ("net_worth", "id INTEGER PRIMARY KEY AUTOINCREMENT, timestamp TEXT, total_value REAL, cash REAL, equities REAL, crypto REAL, bonds REAL, alternatives REAL, notes TEXT"),
            ("watchlist", "id INTEGER PRIMARY KEY AUTOINCREMENT, symbol TEXT, asset_name TEXT, target_buy REAL, target_sell REAL, alert_enabled INTEGER DEFAULT 1, notes TEXT"),
            ("journal", "id INTEGER PRIMARY KEY AUTOINCREMENT, timestamp TEXT, entry TEXT, mood TEXT, tags TEXT"),
            ("goals", "id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT, target_amount REAL, current_amount REAL, deadline TEXT, priority TEXT, status TEXT DEFAULT 'active'"),
            ("research_papers", "id INTEGER PRIMARY KEY AUTOINCREMENT, timestamp TEXT, source TEXT, title TEXT, summary TEXT, published TEXT, url TEXT, authors TEXT, categories TEXT, signal_type TEXT, relevance_score INTEGER DEFAULT 0"),
            ("learning_log", "id INTEGER PRIMARY KEY AUTOINCREMENT, timestamp TEXT, period TEXT, alerts_evaluated INTEGER, correct_predictions INTEGER, accuracy REAL, notes TEXT"),
            ("user_feedback", "id INTEGER PRIMARY KEY AUTOINCREMENT, timestamp TEXT, alert_id INTEGER, was_correct INTEGER, notes TEXT"),
        ]
        for name, schema in tables:
            c.execute(f"CREATE TABLE IF NOT EXISTS {name} ({schema})")
        conn.commit()
        conn.close()

    def store(self, table: str, data: Dict):
        if not validate_table_name(table) or table not in self.ALLOWED_TABLES:
            raise ValueError(f"Invalid table: {table}")
        conn = sqlite3.connect(str(self.db_path))
        c = conn.cursor()
        cols = ", ".join(data.keys())
        ph = ", ".join(["?"] * len(data))
        c.execute(f"INSERT INTO {table} ({cols}) VALUES ({ph})", tuple(data.values()))
        conn.commit()
        conn.close()

    def update(self, table: str, data: Dict, where: str, params: Tuple):
        if not validate_table_name(table) or table not in self.ALLOWED_TABLES:
            raise ValueError(f"Invalid table: {table}")
        conn = sqlite3.connect(str(self.db_path))
        c = conn.cursor()
        sets = ", ".join(f"{k}=?" for k in data.keys())
        values = tuple(data.values()) + params
        c.execute(f"UPDATE {table} SET {sets} WHERE {where}", values)
        conn.commit()
        conn.close()

    def delete(self, table: str, where: str, params: Tuple):
        if not validate_table_name(table) or table not in self.ALLOWED_TABLES:
            raise ValueError(f"Invalid table: {table}")
        conn = sqlite3.connect(str(self.db_path))
        c = conn.cursor()
        c.execute(f"DELETE FROM {table} WHERE {where}", params)
        conn.commit()
        conn.close()

    def query(self, sql: str, params=()) -> List[Dict]:
        conn = sqlite3.connect(str(self.db_path))
        conn.row_factory = sqlite3.Row
        c = conn.cursor()
        c.execute(sql, params)
        rows = [dict(r) for r in c.fetchall()]
        conn.close()
        return rows

    def execute(self, sql: str, params=()):
        conn = sqlite3.connect(str(self.db_path))
        c = conn.cursor()
        c.execute(sql, params)
        conn.commit()
        conn.close()

    def get_latest_prices(self) -> Dict[str, Dict]:
        sql = """SELECT asset, price, change_24h, timestamp FROM market_data
                 WHERE id IN (SELECT MAX(id) FROM market_data GROUP BY asset) ORDER BY asset"""
        return {r["asset"]: r for r in self.query(sql)}

    def get_active_alerts(self, portfolio_only: bool = False) -> List[Dict]:
        sql = "SELECT * FROM alerts WHERE dismissed = 0"
        if portfolio_only:
            sql += " AND portfolio_related = 1"
        sql += " ORDER BY timestamp DESC LIMIT 20"
        return self.query(sql)

    def get_holdings(self) -> List[Dict]:
        sql = """SELECT h.* FROM holdings h
                 INNER JOIN (SELECT symbol, MAX(timestamp) as latest FROM holdings GROUP BY symbol) h2 
                 ON h.symbol = h2.symbol AND h.timestamp = h2.latest ORDER BY h.symbol"""
        return self.query(sql)

    def get_profile(self) -> Optional[Dict]:
        rows = self.query("SELECT * FROM user_profile LIMIT 1")
        return rows[0] if rows else None

    def get_watchlist(self) -> List[Dict]:
        return self.query("SELECT * FROM watchlist ORDER BY symbol")

    def get_goals(self) -> List[Dict]:
        return self.query("SELECT * FROM goals WHERE status = 'active' ORDER BY priority, deadline")

    def get_net_worth_history(self, days: int = 30) -> List[Dict]:
        return self.query("SELECT * FROM net_worth WHERE timestamp > datetime('now', '-{} days') ORDER BY timestamp".format(days))

    def dismiss_alert(self, aid: int):
        self.execute("UPDATE alerts SET dismissed = 1 WHERE id = ?", (aid,))

# =============================================================================
# CONFIG MANAGER
# =============================================================================
class ConfigManager:
    def __init__(self, vault_path: Path):
        self.path = vault_path / "config.json"
        self.data = DEFAULT_CONFIG.copy()
        self.load()

    def load(self):
        if self.path.exists():
            try:
                with open(self.path, 'r') as f:
                    self.data.update(json.load(f))
            except Exception as e:
                logger.warning(f"Config load failed: {e}")
        else:
            self.save()

    def save(self):
        with open(self.path, 'w') as f:
            json.dump(self.data, f, indent=2)
        os.chmod(self.path, 0o600)

    def get(self, key: str, default=None):
        return self.data.get(key, default)

    def set(self, key: str, value):
        self.data[key] = value
        self.save()

# =============================================================================
# PERSONALITY ENGINE
# =============================================================================
class Personality:
    def __init__(self, vault: Vault):
        self.vault = vault
        self.profile = vault.get_profile()

    def greet(self) -> str:
        hour = datetime.now(timezone.utc).hour
        if hour < 12:
            period = "morning"
        elif hour < 18:
            period = "afternoon"
        else:
            period = "evening"
        greeting = random.choice(GREETINGS[period])
        if self.profile and self.profile.get("name"):
            greeting = greeting.replace(".", ", " + self.profile["name"] + ".", 1)
        return greeting

    def speak(self, text: str, tone: str = "neutral"):
        prefix = ""
        if tone == "concern":
            prefix = random.choice(ADVISOR_TONE) + "\n"
        elif tone == "celebrate":
            prefix = "Good news — "
        elif tone == "urgent":
            prefix = "**URGENT** — "
        print(prefix + text)

    def risk_label(self, score: int) -> str:
        if score <= 20:
            return "Conservative"
        elif score <= 40:
            return "Moderate-Conservative"
        elif score <= 60:
            return "Balanced"
        elif score <= 80:
            return "Growth"
        return "Aggressive"

# =============================================================================
# PRICE LOOKUP UTILITY
# =============================================================================
class PriceLookup:
    @staticmethod
    def resolve(symbol: str, prices: Dict[str, Dict]) -> Optional[Dict]:
        sym = symbol.upper().strip()
        if sym in prices:
            return prices[sym]
        for ticker, name in YF_MAP.items():
            if sym == ticker.upper() or sym == name.upper():
                if name in prices:
                    return prices[name]
                if ticker.upper() in prices:
                    return prices[ticker.upper()]
        for coin_id, crypto_sym in COIN_MAP.items():
            if sym == crypto_sym:
                if crypto_sym in prices:
                    return prices[crypto_sym]
        for asset_name, data in prices.items():
            if asset_name.upper() == sym:
                return data
        return None

# =============================================================================
# INGESTION ENGINE
# =============================================================================
class IngestionEngine:
    def __init__(self, vault: Vault, config: ConfigManager):
        self.vault = vault
        self.config = config
        self.sess = requests.Session()
        self.sess.headers.update({"User-Agent": "KingsApple/2.1 (Personal Intelligence)"})
        self._last = {}

    def _rl(self, src: str, sec: float = 4.0):
        now = time.time()
        wait = sec - (now - self._last.get(src, 0))
        if wait > 0:
            time.sleep(wait)
        self._last[src] = time.time()

    @retry(max_tries=3, backoff=2.0)
    def fetch_crypto(self):
        self._rl("cg")
        results = []
        ids = ",".join(SOURCES["coingecko"]["coins"])
        try:
            url = f"{SOURCES['coingecko']['url']}/simple/price?ids={ids}&vs_currencies=usd&include_24hr_change=true&include_market_cap=true"
            r = self.sess.get(url, timeout=15)
            r.raise_for_status()
            data = r.json()
            for coin, info in data.items():
                sym = COIN_MAP.get(coin, coin.upper())
                results.append({
                    "timestamp": now_iso(),
                    "source": "coingecko", "asset": sym,
                    "price": info.get("usd"), "change_24h": info.get("usd_24h_change"),
                    "volume": info.get("usd_market_cap"), "raw_data": json.dumps(info), "signal_score": 0,
                })
        except Exception as e:
            logger.error(f"Crypto fetch error: {e}")
        return results

    @retry(max_tries=3, backoff=2.0)
    def fetch_treasury(self):
        try:
            url = SOURCES["treasury"]["url"] + SOURCES["treasury"]["yield_10y"]
            r = self.sess.get(url, timeout=15)
            r.raise_for_status()
            rec = r.json().get("data", [{}])[0]
            return {
                "timestamp": now_iso(), "source": "treasury",
                "asset": "10Y_TREASURY", "price": float(rec.get("avg_interest_rate_amt", 0)),
                "change_24h": None, "volume": None, "raw_data": json.dumps(rec), "signal_score": 0,
            }
        except Exception as e:
            logger.error(f"Treasury error: {e}")
            return None

    @retry(max_tries=3, backoff=2.0)
    def fetch_fear_greed(self):
        try:
            r = self.sess.get(SOURCES["fear_greed"]["url"], timeout=15)
            r.raise_for_status()
            d = r.json()["data"][0]
            return {
                "timestamp": now_iso(), "source": "fear_greed",
                "asset": "Fear_Greed_Index", "price": float(d["value"]),
                "change_24h": None, "volume": None, "raw_data": json.dumps(d), "signal_score": 0,
            }
        except Exception as e:
            logger.error(f"Fear/Greed error: {e}")
            return None

    def fetch_yahoo_batch(self):
        results = []
        if yf is None or pd is None:
            logger.warning("yfinance or pandas not installed. Skipping Yahoo batch.")
            return results
        tickers = list(YF_MAP.keys())
        try:
            data = yf.download(tickers, period="2d", interval="1d", group_by="ticker", progress=False, threads=True)
            if data is None or data.empty:
                return results
            for sym in tickers:
                name = YF_MAP[sym]
                try:
                    if len(tickers) == 1:
                        hist = data
                    else:
                        hist = data[sym] if sym in data.columns.levels[0] else None
                    if hist is None or hist.empty:
                        continue
                    latest = hist.iloc[-1]
                    prev = hist.iloc[-2] if len(hist) > 1 else hist.iloc[-1]
                    chg = ((latest["Close"] - prev["Close"]) / prev["Close"] * 100) if prev["Close"] and prev["Close"] != 0 else 0
                    results.append({
                        "timestamp": now_iso(), "source": "yfinance",
                        "asset": name, "price": round(float(latest["Close"]), 2),
                        "change_24h": round(float(chg), 2), "volume": int(latest.get("Volume", 0)) if not pd.isna(latest.get("Volume")) else 0,
                        "raw_data": json.dumps({"close": float(latest["Close"])}), "signal_score": 0,
                    })
                except Exception as e:
                    logger.warning(f"Yahoo parse error for {sym}: {e}")
        except Exception as e:
            logger.error(f"Yahoo batch error: {e}")
        return results

    def fetch_news(self, max_items: int = 30):
        if feedparser is None:
            logger.warning("pip install feedparser for news")
            return []
        results = []
        feeds = SOURCES["news"]["feeds"]
        per = max(1, max_items // len(feeds))
        for url in feeds:
            try:
                feed = feedparser.parse(url)
                for e in feed.entries[:per]:
                    title = e.get("title", "")
                    summary = e.get("summary", "")[:600]
                    sig, sev = "general", 1
                    text = (title + " " + summary).lower()
                    for st, kws in SIGNAL_KEYWORDS.items():
                        if any(re.search(r'\b' + re.escape(k) + r'\b', text) for k in kws):
                            sig = st
                            sev = min(2 + sum(1 for k in kws if re.search(r'\b' + re.escape(k) + r'\b', text)), 5)
                            break
                    results.append({
                        "timestamp": now_iso(), "source": url.split("/")[2],
                        "title": title, "summary": summary, "url": e.get("link", ""),
                        "signal_type": sig, "severity": sev,
                        "correlated_assets": self._corr(sig),
                    })
            except Exception as e:
                logger.warning(f"RSS {url}: {e}")
        return results

    def _corr(self, sig: str) -> str:
        m = {
            "war": "GLD,TLT,BTC,Oil", "sanctions": "DXY,GLD,Oil,USO",
            "fed": "TLT,SPY,QQQ,DXY,HYG", "inflation": "GLD,TLT,DXY,SLV",
            "recession": "TLT,GLD,VIX,HYG", "crypto_regulation": "BTC,ETH,SOL",
            "banking_crisis": "GLD,TLT,BTC,VIX", "election": "SPY,QQQ,VIX,GLD",
            "earnings": "SPY,QQQ,IWM", "merger": "SPY,QQQ", "cyberattack": "VIX,GLD,BTC",
            "energy": "USO,GLD,DXY,SPY",
        }
        return m.get(sig, "")

    def full_scan(self):
        sep = "=" * 64
        print("\n" + sep)
        print("  KingsApple Intelligence Scan — " + datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC"))
        print(sep)
        print("\n[1/5] Crypto markets...")
        for item in self.fetch_crypto():
            self.vault.store("market_data", item)
            print(f"      {item['asset']}: ${item['price']:,.2f} ({item['change_24h']:+.2f}%)")
        print("\n[2/5] Treasury & macro...")
        t = self.fetch_treasury()
        if t:
            self.vault.store("market_data", t)
            print(f"      10Y Treasury: {t['price']:.3f}%")
        fg = self.fetch_fear_greed()
        if fg:
            self.vault.store("market_data", fg)
            label = ["Extreme Fear", "Fear", "Neutral", "Greed", "Extreme Greed"][min(int(fg['price']) // 20, 4)]
            print(f"      Fear & Greed: {fg['price']:.0f} ({label})")
        print("\n[3/5] Equities, forex, commodities...")
        for item in self.fetch_yahoo_batch():
            self.vault.store("market_data", item)
            print(f"      {item['asset']}: ${item['price']:,.2f} ({item['change_24h']:+.2f}%)")
        print("\n[4/5] News & signal detection...")
        news = self.fetch_news()
        counts = {}
        for item in news:
            self.vault.store("news_signals", item)
            counts[item["signal_type"]] = counts.get(item["signal_type"], 0) + 1
        for st, c in counts.items():
            if st != "general":
                print(f"      {st.replace('_', ' ').title()}: {c} signals")
        print(f"      Total articles: {len(news)}")
        print("\n[5/5] Correlating with YOUR portfolio...")
        self._correlate_personal()
        print("\nScan complete. Your data is encrypted and stored.")
        print("  KingsApple just got smarter.")
        print(sep + "\n")

    def _correlate_personal(self):
        prices = self.vault.get_latest_prices()
        holdings = self.vault.get_holdings()
        watchlist = self.vault.get_watchlist()
        recent = self.vault.query("SELECT * FROM news_signals WHERE timestamp > datetime('now', '-2 hours') ORDER BY severity DESC LIMIT 15")
        held_symbols = {h["symbol"] for h in holdings}
        watched_symbols = {w["symbol"] for w in watchlist}
        for n in recent:
            if n["severity"] >= 3:
                corr = n["correlated_assets"].split(",") if n["correlated_assets"] else []
                portfolio_hit = any(c.strip() in held_symbols for c in corr)
                watch_hit = any(c.strip() in watched_symbols for c in corr)
                if portfolio_hit or watch_hit:
                    msg = f"PORTFOLIO ALERT — {n['signal_type'].replace('_', ' ').upper()}: {n['title'][:70]}..."
                    action = "Review your positions. This directly affects your holdings."
                    self.vault.store("alerts", {
                        "timestamp": now_iso(),
                        "level": "HIGH" if n["severity"] >= 4 else "MEDIUM",
                        "message": msg, "action": action, "portfolio_related": 1,
                    })
        for w in watchlist:
            if not w.get("alert_enabled"):
                continue
            asset_name = w.get("asset_name") or w.get("symbol")
            p = PriceLookup.resolve(w["symbol"], prices) or PriceLookup.resolve(asset_name, prices) if asset_name else None
            if p and p.get("price"):
                price = p["price"]
                if w.get("target_buy") and price <= w["target_buy"]:
                    self.vault.store("alerts", {
                        "timestamp": now_iso(), "level": "MEDIUM",
                        "message": f"{w['symbol']} hit your buy target at ${price:.2f}",
                        "action": "Consider opening a position.", "portfolio_related": 1,
                    })
                if w.get("target_sell") and price >= w["target_sell"]:
                    self.vault.store("alerts", {
                        "timestamp": now_iso(), "level": "MEDIUM",
                        "message": f"{w['symbol']} hit your sell target at ${price:.2f}",
                        "action": "Consider taking profits.", "portfolio_related": 1,
                    })
        vix = PriceLookup.resolve("^VIX", prices) or prices.get("VIX Fear Index", {})
        if vix and vix.get("price", 0) > 25:
            self.vault.store("alerts", {
                "timestamp": now_iso(), "level": "HIGH",
                "message": f"VIX spiked to {vix['price']:.1f}. Fear is elevated.",
                "action": "Consider reducing equity exposure. Hedge with GLD/TLT.", "portfolio_related": 1,
            })
        btc = PriceLookup.resolve("BTC", prices) or prices.get("BTC", {})
        if btc and btc.get("change_24h", 0) < -10:
            self.vault.store("alerts", {
                "timestamp": now_iso(), "level": "HIGH",
                "message": f"BTC crashed {btc['change_24h']:.1f}% in 24h.",
                "action": "Review crypto allocation. Potential DCA opportunity.", "portfolio_related": 1,
            })

# =============================================================================
# PORTFOLIO & ANALYTICS
# =============================================================================
class Portfolio:
    def __init__(self, vault: Vault):
        self.vault = vault

    def add_holding(self, symbol: str, name: str, qty: float, cost: float, asset_class: str, notes: str = ""):
        self.vault.store("holdings", {
            "timestamp": now_iso(), "symbol": symbol.upper().strip(),
            "asset_name": name, "quantity": float(qty), "avg_cost": float(cost),
            "asset_class": asset_class, "notes": notes,
        })
        print(f"  Added: {qty} shares of {symbol.upper()} @ ${cost:.2f}")

    def update_holding(self, symbol: str, qty: float = None, cost: float = None, asset_class: str = None, notes: str = None):
        data = {}
        if qty is not None:
            data["quantity"] = float(qty)
        if cost is not None:
            data["avg_cost"] = float(cost)
        if asset_class is not None:
            data["asset_class"] = asset_class
        if notes is not None:
            data["notes"] = notes
        if data:
            data["timestamp"] = now_iso()
            self.vault.update("holdings", data, "symbol=? AND id=(SELECT MAX(id) FROM holdings WHERE symbol=?)", (symbol.upper(), symbol.upper()))
            print(f"  Updated: {symbol.upper()}")

    def remove_holding(self, symbol: str):
        self.vault.delete("holdings", "symbol=?", (symbol.upper(),))
        print(f"  Removed: {symbol.upper()}")

    def add_transaction(self, symbol: str, action: str, qty: float, price: float, fees: float = 0, notes: str = ""):
        self.vault.store("transactions", {
            "timestamp": now_iso(), "symbol": symbol.upper().strip(),
            "action": action, "quantity": float(qty), "price": float(price), "fees": float(fees), "notes": notes,
        })

    def add_goal(self, name: str, target: float, current: float, deadline: str, priority: str = "medium"):
        self.vault.store("goals", {
            "name": name, "target_amount": float(target), "current_amount": float(current),
            "deadline": deadline, "priority": priority,
        })
        print(f"  Goal set: {name} — ${current:,.0f} / ${target:,.0f} by {deadline}")

    def update_goal(self, name: str, target: float = None, current: float = None, deadline: str = None, priority: str = None):
        data = {}
        if target is not None:
            data["target_amount"] = float(target)
        if current is not None:
            data["current_amount"] = float(current)
        if deadline is not None:
            data["deadline"] = deadline
        if priority is not None:
            data["priority"] = priority
        if data:
            self.vault.update("goals", data, "name=? AND status='active'", (name,))
            print(f"  Updated goal: {name}")

    def remove_goal(self, name: str):
        self.vault.update("goals", {"status": "deleted"}, "name=?", (name,))
        print(f"  Removed goal: {name}")

    def add_watchlist(self, symbol: str, name: str = "", buy: float = None, sell: float = None, notes: str = ""):
        existing = self.vault.query("SELECT id FROM watchlist WHERE symbol=?", (symbol.upper(),))
        if existing:
            data = {"asset_name": name or symbol.upper(), "notes": notes, "alert_enabled": 1}
            if buy is not None:
                data["target_buy"] = float(buy)
            if sell is not None:
                data["target_sell"] = float(sell)
            self.vault.update("watchlist", data, "symbol=?", (symbol.upper(),))
            print(f"  Updated watchlist: {symbol}")
        else:
            self.vault.store("watchlist", {
                "symbol": symbol.upper(), "asset_name": name or symbol.upper(),
                "target_buy": float(buy) if buy is not None else None,
                "target_sell": float(sell) if sell is not None else None,
                "notes": notes,
            })
            print(f"  Watching: {symbol}" + (f" | Buy below ${buy:.2f}" if buy else "") + (f" | Sell above ${sell:.2f}" if sell else ""))

    def remove_watchlist(self, symbol: str):
        self.vault.delete("watchlist", "symbol=?", (symbol.upper(),))
        print(f"  Removed from watchlist: {symbol.upper()}")

    def record_net_worth(self, notes: str = ""):
        snap = self.snapshot()
        breakdown = snap.get("breakdown", {})
        self.vault.store("net_worth", {
            "timestamp": now_iso(),
            "total_value": snap["total_value"],
            "cash": breakdown.get("Cash", 0),
            "equities": breakdown.get("Equities", 0),
            "crypto": breakdown.get("Crypto", 0),
            "bonds": breakdown.get("Bonds", 0),
            "alternatives": breakdown.get("Alternatives", 0),
            "notes": notes,
        })

    def snapshot(self) -> Dict:
        prices = self.vault.get_latest_prices()
        holdings = self.vault.get_holdings()
        total_value, total_cost, unrealized = 0.0, 0.0, 0.0
        breakdown = {"Equities": 0, "Crypto": 0, "Bonds": 0, "Forex": 0, "Commodities": 0, "Alternatives": 0, "Cash": 0}
        positions = []
        for h in holdings:
            sym = h["symbol"]
            qty = h["quantity"] or 0
            cost = h["avg_cost"] or 0
            asset_class = h.get("asset_class", "Equities")
            price_data = PriceLookup.resolve(sym, prices)
            price = price_data.get("price") if price_data else None
            if price is None:
                price = cost
            value = qty * price
            cost_basis = qty * cost
            pnl = value - cost_basis
            pnl_pct = (pnl / cost_basis * 100) if cost_basis else 0
            total_value += value
            total_cost += cost_basis
            unrealized += pnl
            breakdown[asset_class] = breakdown.get(asset_class, 0) + value
            positions.append({
                "symbol": sym, "qty": qty, "price": price, "value": value,
                "cost_basis": cost_basis, "pnl": pnl, "pnl_pct": pnl_pct,
                "asset_class": asset_class,
            })
        return {
            "total_value": total_value, "total_cost": total_cost,
            "unrealized_pnl": unrealized, "positions": positions,
            "breakdown": breakdown,
        }

    def analytics(self) -> Dict:
        hist = self.vault.get_net_worth_history(90)
        if len(hist) < 5:
            return {"error": "Need at least 5 net worth records for analytics"}
        values = [h["total_value"] for h in hist if h["total_value"]]
        if len(values) < 2:
            return {"error": "Insufficient data"}
        returns = [(values[i] - values[i-1]) / values[i-1] for i in range(1, len(values))]
        avg_return = sum(returns) / len(returns)
        variance = sum((r - avg_return) ** 2 for r in returns) / len(returns)
        volatility = variance ** 0.5
        peak = values[0]
        max_dd = 0
        for v in values:
            if v > peak:
                peak = v
            dd = (peak - v) / peak
            if dd > max_dd:
                max_dd = dd
        sharpe = (avg_return / volatility) if volatility > 0 else 0
        return {
            "avg_daily_return": avg_return,
            "volatility": volatility,
            "sharpe_like": sharpe,
            "max_drawdown": max_dd,
            "records": len(values),
        }

    def rebalance_suggestion(self, target_alloc: Dict[str, float]) -> List[str]:
        snap = self.snapshot()
        total = snap["total_value"]
        if total <= 0:
            return ["No holdings to rebalance."]
        suggestions = []
        for cls, target_pct in target_alloc.items():
            current = snap["breakdown"].get(cls, 0)
            current_pct = (current / total * 100) if total else 0
            diff = current_pct - target_pct
            if abs(diff) > 5:
                direction = "overweight" if diff > 0 else "underweight"
                suggestions.append(f"  {cls}: {current_pct:.1f}% (target: {target_pct}%) — you are {direction} by {abs(diff):.1f}%")
        if not suggestions:
            suggestions.append("  Your allocation looks balanced. Good job.")
        return suggestions

    def tax_loss_harvest(self) -> List[Dict]:
        snap = self.snapshot()
        losers = [p for p in snap["positions"] if p["pnl"] < 0]
        return sorted(losers, key=lambda x: x["pnl"])

# =============================================================================
# DASHBOARD & BRIEFING
# =============================================================================
class Dashboard:
    def __init__(self, vault: Vault):
        self.vault = vault
        self.ai = Personality(vault)
        self.port = Portfolio(vault)

    def _sep(self, w: int = 70) -> str:
        return "=" * w

    def _line(self, w: int = 66) -> str:
        return "-" * w

    def briefing(self):
        os.system("cls" if os.name == "nt" else "clear")
        print("\n" + self._sep())
        print("  " + self.ai.greet())
        print("  " + APP_NAME + " v" + VERSION + " — Your Personal Financial AI")
        print("  " + datetime.now(timezone.utc).strftime("%A, %B %d, %Y — %I:%M %p UTC"))
        print(self._sep())
        profile = self.vault.get_profile()
        if profile:
            print("\nYOUR PROFILE")
            print("  " + self._line())
            print(f"  Name:       {profile.get('name', 'Unknown')}")
            print(f"  Risk:       {profile.get('risk_tolerance', 'Not set')}")
            print(f"  Horizon:    {profile.get('investment_horizon', 'Not set')}")
            print(f"  Net Worth Target: ${profile.get('net_worth_target', 0):,.0f}")
        snap = self.port.snapshot()
        print("\nYOUR PORTFOLIO")
        print("  " + self._line())
        if snap["total_value"] > 0:
            print(f"  Total Value:     ${snap['total_value']:,.2f}")
            print(f"  Total Cost:      ${snap['total_cost']:,.2f}")
            pnl_color = "▲" if snap['unrealized_pnl'] >= 0 else "▼"
            print(f"  Unrealized P&L:  {pnl_color} ${snap['unrealized_pnl']:,.2f}")
            print("\nPositions:")
            for p in snap["positions"]:
                arrow = "▲" if p['pnl'] >= 0 else "▼"
                print(f"    {p['symbol']:<8} {p['qty']:>10.4f} @ ${p['price']:>10,.2f}  {arrow} ${p['pnl']:>10,.2f} ({p['pnl_pct']:+.1f}%)")
            print("\nAllocation:")
            for cls, val in snap["breakdown"].items():
                if val > 0:
                    pct = val / snap["total_value"] * 100
                    bar = "█" * int(pct / 2)
                    print(f"    {cls:<18} {bar:<50} {pct:.1f}%")
        else:
            print("  No holdings tracked yet. Use --setup.")
        goals = self.vault.get_goals()
        if goals:
            print("\nYOUR GOALS")
            print("  " + self._line())
            for g in goals:
                pct = (g["current_amount"] / g["target_amount"] * 100) if g["target_amount"] else 0
                bar = "█" * int(pct / 5) + "░" * (20 - int(pct / 5))
                print(f"  {g['name']:<25} [{bar}] {pct:.0f}%  ${g['current_amount']:,.0f}/${g['target_amount']:,.0f}  (by {g['deadline']})")
        print("\nMARKET SNAPSHOT")
        print("  " + self._line())
        prices = self.vault.get_latest_prices()
        for asset in ["BTC", "ETH", "SOL", "S&P 500", "Nasdaq 100", "Gold", "Oil", "Treasury 20Y", "10Y_TREASURY", "VIX Fear Index", "Fear_Greed_Index"]:
            p = prices.get(asset, {})
            if p.get("price") is not None:
                chg = p.get("change_24h")
                chg_str = f"{chg:+.2f}%" if chg is not None else "N/A"
                arrow = "▲" if chg and chg > 0 else "▼" if chg and chg < 0 else "─"
                print(f"  {asset:<22} ${p['price']:>12,.2f}  {arrow} {chg_str:>10}")
        print("\nYOUR ALERTS")
        print("  " + self._line())
        alerts = self.vault.get_active_alerts(portfolio_only=True)
        if not alerts:
            print("  No portfolio alerts. All quiet on your front.")
        for a in alerts[:5]:
            badge = "[HIGH]" if a["level"] == "HIGH" else "[MED]"
            print(f"  {badge} {a['message'][:55]}...")
            if a["action"]:
                print(f"      -> {a['action']}")
        if profile:
            target = {"Equities": 50, "Crypto": 15, "Bonds": 20, "Commodities": 5, "Alternatives": 5, "Cash": 5}
            print("\nREBALANCE CHECK")
            print("  " + self._line())
            for s in self.port.rebalance_suggestion(target):
                print(s)
        losers = self.port.tax_loss_harvest()
        if losers:
            print("\nTAX LOSS HARVEST OPPORTUNITIES")
            print("  " + self._line())
            for p in losers[:3]:
                print(f"  {p['symbol']}: Unrealized loss of ${p['pnl']:,.2f} ({p['pnl_pct']:.1f}%)")
        analytics = self.port.analytics()
        if "error" not in analytics:
            print("\nPORTFOLIO ANALYTICS (90d)")
            print("  " + self._line())
            print(f"  Sharpe-like ratio: {analytics['sharpe_like']:.2f}")
            print(f"  Max drawdown:      {analytics['max_drawdown']*100:.1f}%")
            print(f"  Volatility:        {analytics['volatility']*100:.1f}%")
        print("\n" + self._sep())
        print("  Commands: --scan | --chat | --setup | --add-trade | --export PATH")
        print(self._sep() + "\n")

    def all_alerts(self):
        alerts = self.vault.get_active_alerts()
        print("\nALL ACTIVE ALERTS (" + str(len(alerts)) + ")")
        print("  " + self._line())
        for a in alerts:
            print("\nID: " + str(a['id']) + " | " + a['level'] + " | " + a['timestamp'])
            print("  " + a['message'])
            if a["action"]:
                print("  Action: " + a['action'])

    def export_backup(self, path: str):
        self.vault.save()
        dest = Path(path) / ("kingsapple_backup_" + datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S") + ".db.enc")
        shutil.copy2(self.vault.encrypted_db, dest)
        print("\nBackup exported: " + str(dest))
        print("  Size: " + str(round(dest.stat().st_size / 1024, 1)) + " KB")
        print("  Encrypted: YES (AES-256-GCM)")

# =============================================================================
# INTERACTIVE CHAT MODE
# =============================================================================
class ChatMode:
    def __init__(self, vault: Vault):
        self.vault = vault
        self.ai = Personality(vault)
        self.port = Portfolio(vault)

    def run(self):
        os.system("cls" if os.name == "nt" else "clear")
        print("\n" + "=" * 64)
        print("  " + self.ai.greet())
        print("  Ask me anything about your money, the markets, or your goals.")
        print("  Type 'quit' to exit, 'help' for commands.")
        print("=" * 64 + "\n")
        while True:
            try:
                user = input("  You: ").strip()
            except (EOFError, KeyboardInterrupt):
                print("\nGoodbye.")
                break
            user_lower = user.lower()
            if user_lower in ("quit", "exit", "bye"):
                print("  KingsApple: Stay sharp. I'll be watching.")
                break
            elif user_lower == "help":
                self._help()
            elif user_lower in ("portfolio", "holdings", "positions"):
                self._show_portfolio()
            elif user_lower in ("alerts", "warnings"):
                self._show_alerts()
            elif user_lower in ("prices", "market", "snapshot"):
                self._show_prices()
            elif user_lower in ("goals", "targets"):
                self._show_goals()
            elif user_lower in ("scan", "refresh", "update"):
                IngestionEngine(self.vault, ConfigManager(self.vault.vault_path)).full_scan()
            elif user_lower in ("rebalance", "allocation"):
                self._rebalance()
            elif user_lower in ("tax loss", "harvest"):
                self._tax_loss()
            elif user_lower in ("net worth", "wealth"):
                self._net_worth()
            elif user_lower in ("analytics", "stats"):
                self._analytics()
            elif any(cmd in user_lower for cmd in ["buy", "sell", "should i", "think about"]):
                self._advise_trade(user)
            elif "risk" in user_lower:
                self._assess_risk()
            elif "what" in user_lower and "think" in user_lower:
                self._opinion()
            else:
                self._general_response(user)

    def _help(self):
        print("""
Commands:
portfolio / holdings  — Show your positions and P&L
alerts                — Show active alerts
prices / market       — Show latest prices
goals                 — Show financial goals progress
scan / refresh        — Run a new data scan
rebalance             — Check allocation vs targets
tax loss / harvest    — Find tax loss opportunities
net worth             — Show wealth trajectory
analytics / stats     — Portfolio analytics (Sharpe, drawdown)
buy / sell [symbol]   — Get advice on a trade
risk                  — Assess your risk exposure
what do you think     — Get my market opinion
quit / exit           — Leave
""")

    def _show_portfolio(self):
        snap = self.port.snapshot()
        if snap["total_value"] <= 0:
            print("  You haven't added any holdings yet. Use --setup.")
            return
        print(f"\nTotal Value: ${snap['total_value']:,.2f}")
        print(f"  Unrealized P&L: ${snap['unrealized_pnl']:,.2f}")
        for p in snap["positions"]:
            arrow = "▲" if p['pnl'] >= 0 else "▼"
            print(f"  {p['symbol']}: {p['qty']:.4f} @ ${p['price']:.2f} = ${p['value']:,.2f} {arrow} ${p['pnl']:,.2f}")

    def _show_alerts(self):
        alerts = self.vault.get_active_alerts(portfolio_only=True)
        if not alerts:
            print("  No alerts. Your portfolio is sleeping peacefully.")
            return
        for a in alerts[:5]:
            print(f"  [{a['level']}] {a['message'][:60]}")

    def _show_prices(self):
        prices = self.vault.get_latest_prices()
        for asset in ["BTC", "ETH", "S&P 500", "Gold", "VIX Fear Index"]:
            p = prices.get(asset, {})
            if p.get("price"):
                print(f"  {asset}: ${p['price']:,.2f}")

    def _show_goals(self):
        goals = self.vault.get_goals()
        if not goals:
            print("  No goals set. What are you building toward?")
            return
        for g in goals:
            pct = g["current_amount"] / g["target_amount"] * 100 if g["target_amount"] else 0
            print(f"  {g['name']}: {pct:.0f}% complete (${g['current_amount']:,.0f}/${g['target_amount']:,.0f})")

    def _rebalance(self):
        target = {"Equities": 50, "Crypto": 15, "Bonds": 20, "Commodities": 5, "Alternatives": 5, "Cash": 5}
        suggestions = self.port.rebalance_suggestion(target)
        print("\nRebalance Analysis:")
        for s in suggestions:
            print("  " + s)

    def _tax_loss(self):
        losers = self.port.tax_loss_harvest()
        if not losers:
            print("  No tax loss opportunities. Everything is green.")
            return
        print("\nTax Loss Candidates:")
        for p in losers[:5]:
            print(f"  {p['symbol']}: ${p['pnl']:,.2f} loss")

    def _net_worth(self):
        snap = self.port.snapshot()
        print(f"\nCurrent Portfolio Value: ${snap['total_value']:,.2f}")
        hist = self.vault.get_net_worth_history(30)
        if len(hist) >= 2:
            change = hist[-1]["total_value"] - hist[-2]["total_value"]
            print(f"  Change from last record: ${change:,.2f}")

    def _analytics(self):
        a = self.port.analytics()
        if "error" in a:
            print(f"\n{a['error']}")
            return
        print(f"\nSharpe-like Ratio: {a['sharpe_like']:.2f}")
        print(f"  Max Drawdown:      {a['max_drawdown']*100:.1f}%")
        print(f"  Volatility:        {a['volatility']*100:.1f}%")
        print(f"  Based on:          {a['records']} records")

    def _advise_trade(self, user_input: str):
        match = re.search(r'\b([A-Z]{1,5})\b', user_input.upper())
        if not match:
            print("  Which symbol? Say something like 'buy AAPL' or 'should I sell TSLA?'")
            return
        symbol = match.group(1)
        if symbol in ["BUY", "SELL", "THE", "AND", "FOR", "NOT", "BUT", "YOU", "ALL", "ANY", "CAN", "HAD", "HER", "WAS", "ONE", "OUR", "OUT", "DAY", "GET", "HAS", "HIM", "HIS", "HOW", "MAN", "NEW", "NOW", "OLD", "SEE", "TWO", "WAY", "WHO", "BOY", "DID", "ITS", "LET", "PUT", "SAY", "SHE", "TOO", "USE"]:
            matches = re.findall(r'\b([A-Z]{1,5})\b', user_input.upper())
            for m in matches:
                if m not in ["BUY", "SELL", "THE", "AND", "FOR", "NOT", "BUT", "YOU", "ALL"]:
                    symbol = m
                    break
            else:
                print("  Which symbol? Say something like 'buy AAPL' or 'should I sell TSLA?'")
                return
        prices = self.vault.get_latest_prices()
        price_data = PriceLookup.resolve(symbol, prices)
        price = price_data.get("price") if price_data else None
        if price:
            print(f"  {symbol} is at ${price:,.2f}.")
            if "buy" in user_input.lower():
                print("  Before buying: check your cash position, target allocation, and whether this fits your risk profile.")
            else:
                print("  Before selling: consider tax implications, your cost basis, and whether this is panic or strategy.")
        else:
            print(f"  I don't have a current price for {symbol}. Run --scan first.")

    def _assess_risk(self):
        snap = self.port.snapshot()
        profile = self.vault.get_profile()
        if not profile:
            print("  Set up your profile first with --setup.")
            return
        crypto_pct = (snap["breakdown"].get("Crypto", 0) / snap["total_value"] * 100) if snap["total_value"] else 0
        equity_pct = (snap["breakdown"].get("Equities", 0) / snap["total_value"] * 100) if snap["total_value"] else 0
        print(f"\nYour Risk Snapshot:")
        print(f"  Crypto exposure: {crypto_pct:.1f}%")
        print(f"  Equity exposure: {equity_pct:.1f}%")
        print(f"  Risk tolerance: {profile.get('risk_tolerance', 'Unknown')}")
        if crypto_pct > 25 and profile.get("risk_tolerance") == "Conservative":
            print("  **Warning**: Your crypto allocation exceeds your conservative risk profile.")

    def _opinion(self):
        prices = self.vault.get_latest_prices()
        vix = prices.get("VIX Fear Index", {}).get("price", 15)
        fg = prices.get("Fear_Greed_Index", {}).get("price", 50)
        btc_chg = prices.get("BTC", {}).get("change_24h", 0)
        print("\nMy Take Right Now:")
        if vix > 25:
            print("  Fear is high. This is when fortunes are made — if you have dry powder.")
        elif fg > 75:
            print("  Greed is elevated. Be careful chasing here. Consider taking some profits.")
        else:
            print("  Markets are in a balanced state. Stick to your plan.")
        if btc_chg and btc_chg < -5:
            print("  Crypto is bleeding. If you believe in the long term, this is accumulation weather.")
        elif btc_chg and btc_chg > 5:
            print("  Crypto is running hot. Don't FOMO — scale in, don't ape.")

    def _general_response(self, user_input: str):
        responses = [
            "I'm not sure I understand. Try 'help' for what I can do.",
            "Tell me more. Are you asking about your portfolio, the markets, or a specific trade?",
            "I hear you. Run --scan if you want fresh data, or ask about your holdings.",
        ]
        print("  " + random.choice(responses))

# =============================================================================
# SETUP WIZARD
# =============================================================================
def setup_wizard(vault: Vault):
    print("\n" + "=" * 64)
    print("  KingsApple Setup — Let's get to know each other.")
    print("=" * 64 + "\n")
    name = input("  Your name: ").strip()
    risk = input("  Risk tolerance (Conservative/Moderate/Balanced/Growth/Aggressive): ").strip()
    horizon = input("  Investment horizon (Short/Medium/Long): ").strip()
    while True:
        income = input("  Annual income (approx, or 0): ").strip()
        try:
            income_val = float(income) if income else 0
            break
        except ValueError:
            print("  Please enter a valid number.")
    while True:
        target = input("  Net worth target (or 0): ").strip()
        try:
            target_val = float(target) if target else 0
            break
        except ValueError:
            print("  Please enter a valid number.")
    vault.execute("DELETE FROM user_profile")
    vault.store("user_profile", {
        "id": 1, "name": name, "risk_tolerance": risk,
        "investment_horizon": horizon, "annual_income": income_val,
        "net_worth_target": target_val, "currency": "USD",
        "created_at": now_iso(),
        "last_login": now_iso(),
    })
    print("\nProfile saved. Now let's add your holdings.")
    print("  Enter your positions (symbol qty avg_cost asset_class). Blank line when done.")
    print("  Example: AAPL 50 175.50 Equities")
    print("  Asset classes: Equities, Crypto, Bonds, Forex, Commodities, Alternatives, Cash\n")
    port = Portfolio(vault)
    while True:
        line = input("  > ").strip()
        if not line:
            break
        parts = line.split()
        if len(parts) >= 3:
            try:
                sym, qty, cost = parts[0], float(parts[1]), float(parts[2])
                cls = parts[3] if len(parts) > 3 else "Equities"
                port.add_holding(sym, sym, qty, cost, cls)
            except ValueError:
                print("  Invalid format. Use: SYMBOL QTY COST [CLASS]")
        else:
            print("  Invalid format. Use: SYMBOL QTY COST [CLASS]")
    print("\nAny financial goals? (name target current deadline). Blank when done.")
    print("  Example: House Downpayment 100000 25000 2027-12-01")
    while True:
        line = input("  > ").strip()
        if not line:
            break
        parts = line.split()
        if len(parts) >= 3:
            try:
                port.add_goal(parts[0], float(parts[1]), float(parts[2]), parts[3] if len(parts) > 3 else "2030-01-01")
            except ValueError:
                print("  Invalid format. Use: NAME TARGET CURRENT [DEADLINE]")
        else:
            print("  Invalid format. Use: NAME TARGET CURRENT [DEADLINE]")
    print("\nSetup complete. Run --dashboard to see your briefing.")
    print("=" * 64 + "\n")

# =============================================================================
# MAIN
# =============================================================================
def main():
    parser = argparse.ArgumentParser(prog="kingsapple", description="KingsApple — Your Personal Financial AI")
    parser.add_argument("--vault", default=DEFAULT_VAULT, help="Vault path")
    parser.add_argument("--init", action="store_true", help="Initialize vault")
    parser.add_argument("--setup", action="store_true", help="Run setup wizard")
    parser.add_argument("--scan", action="store_true", help="Run intelligence scan")
    parser.add_argument("--dashboard", action="store_true", help="Show morning briefing")
    parser.add_argument("--chat", action="store_true", help="Interactive chat mode")
    parser.add_argument("--alerts", action="store_true", help="Show all alerts")
    parser.add_argument("--export", metavar="PATH", help="Export backup")
    parser.add_argument("--dismiss", type=int, metavar="ID", help="Dismiss alert")
    parser.add_argument("--password", help="Vault password")
    args = parser.parse_args()

    pwd = args.password
    if not pwd:
        if args.init:
            p1 = getpass.getpass("Create password: ")
            p2 = getpass.getpass("Confirm: ")
            if p1 != p2:
                print("Passwords don't match.")
                sys.exit(1)
            pwd = p1
        else:
            pwd = getpass.getpass("Vault password: ")

    vault = Vault(args.vault, pwd)
    config = ConfigManager(vault.vault_path)

    if args.init:
        print("\nVault initialized at: " + args.vault)
        print("  AES-256-GCM encryption active. Don't lose your password.")
        sys.exit(0)

    if args.setup:
        setup_wizard(vault)
        sys.exit(0)

    if args.scan:
        IngestionEngine(vault, config).full_scan()
        sys.exit(0)

    if args.chat:
        ChatMode(vault).run()
        sys.exit(0)

    if args.alerts:
        Dashboard(vault).all_alerts()
        sys.exit(0)

    if args.dismiss:
        vault.dismiss_alert(args.dismiss)
        print("  Alert " + str(args.dismiss) + " dismissed.")
        sys.exit(0)

    if args.export:
        Dashboard(vault).export_backup(args.export)
        sys.exit(0)

    Dashboard(vault).briefing()

if __name__ == "__main__":
    main()