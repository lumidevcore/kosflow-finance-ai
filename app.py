from pathlib import Path
from datetime import datetime
from urllib.parse import parse_qs, quote
import asyncio
import html
import json
import re

import httpx

BASE = Path(__file__).resolve().parent
PUBLIC = BASE / "public"


def nowiso():
    return datetime.now().astimezone().isoformat(timespec="seconds")


async def get_json(url, timeout=12):
    async with httpx.AsyncClient(
        timeout=timeout,
        follow_redirects=True,
        headers={"User-Agent": "Mozilla/5.0 KosFlowAI/9.1"},
    ) as client:
        response = await client.get(url)
        response.raise_for_status()
        return response.json()


async def get_text(url, timeout=12):
    async with httpx.AsyncClient(
        timeout=timeout,
        follow_redirects=True,
        headers={"User-Agent": "Mozilla/5.0"},
    ) as client:
        response = await client.get(url)
        response.raise_for_status()
        return response.text


async def fx_usd_idr():
    urls = [
        "https://open.er-api.com/v6/latest/USD",
        "https://api.frankfurter.app/latest?from=USD&to=IDR",
    ]
    for url in urls:
        try:
            data = await get_json(url, 8)
            rate = data.get("rates", {}).get("IDR")
            if rate:
                return float(rate), url
        except Exception:
            pass
    return None, None


async def crypto_snapshot():
    fx, fx_source = await fx_usd_idr()
    result = []

    for symbol, pair in [
        ("BTC", "BTCUSDT"),
        ("ETH", "ETHUSDT"),
        ("SOL", "SOLUSDT"),
    ]:
        t0 = asyncio.get_event_loop().time()
        try:
            data = await get_json(
                f"https://api.binance.com/api/v3/ticker/24hr?symbol={pair}", 8
            )
            fetch_ms = round((asyncio.get_event_loop().time() - t0) * 1000)
            usdt = float(data["lastPrice"])

            result.append(
                {
                    "symbol": symbol,
                    "pair": pair,
                    "price_usdt": usdt,
                    "price_idr": usdt * fx if fx else None,
                    "change_24h": float(data["priceChangePercent"]),
                    "source": "Binance REST snapshot",
                    "fetch_ms": fetch_ms,
                }
            )
        except Exception:
            pass

    if not result:
        try:
            url = (
                "https://api.coingecko.com/api/v3/simple/price"
                "?ids=bitcoin,ethereum,solana"
                "&vs_currencies=idr&include_24hr_change=true"
            )
            data = await get_json(url, 10)

            for key, symbol in [
                ("bitcoin", "BTC"),
                ("ethereum", "ETH"),
                ("solana", "SOL"),
            ]:
                item = data.get(key, {})
                result.append(
                    {
                        "symbol": symbol,
                        "price_idr": item.get("idr"),
                        "price_usdt": None,
                        "change_24h": item.get("idr_24h_change"),
                        "source": "CoinGecko fallback",
                        "fetch_ms": None,
                    }
                )
        except Exception:
            pass

    return {
        "fetched_at": nowiso(),
        "fx_source": fx_source,
        "items": result,
    }


async def google_finance(symbol):
    symbol = symbol.upper().replace(".JK", "")
    url = f"https://www.google.com/finance/quote/{quote(symbol)}:IDX?hl=id"
    body = await get_text(url, 10)

    price_match = re.search(r'data-last-price="([0-9.,]+)"', body)
    time_match = re.search(
        r'data-last-normal-market-timestamp="([0-9]+)"', body
    )

    if not price_match:
        raise ValueError("Google Finance price parse failed")

    price = float(price_match.group(1).replace(",", ""))
    market_timestamp = None

    if time_match:
        try:
            market_timestamp = datetime.fromtimestamp(
                int(time_match.group(1))
            ).astimezone().isoformat(timespec="seconds")
        except Exception:
            pass

    percent = 0.0
    plain = re.sub(r"<[^>]+>", " ", body)
    pct_match = re.search(r"([+\-]?[0-9]+(?:[.,][0-9]+)?)%", plain)
    if pct_match:
        try:
            percent = float(pct_match.group(1).replace(",", "."))
        except Exception:
            pass

    return {
        "symbol": symbol,
        "price": price,
        "change_percent": percent,
        "currency": "IDR",
        "source": "Google Finance",
        "url": url,
        "market_timestamp": market_timestamp,
    }


async def yahoo_finance(symbol):
    symbol = symbol.upper().replace(".JK", "")
    ticker = symbol + ".JK"
    url = (
        f"https://query1.finance.yahoo.com/v8/finance/chart/{quote(ticker)}"
        "?interval=1m&range=1d"
    )

    data = await get_json(url, 10)
    result = data["chart"]["result"][0]
    meta = result.get("meta", {})

    price = meta.get("regularMarketPrice")
    previous = meta.get("chartPreviousClose") or meta.get("previousClose")
    percent = ((price - previous) / previous * 100) if price and previous else 0

    return {
        "symbol": symbol,
        "price": price,
        "change_percent": percent,
        "currency": meta.get("currency", "IDR"),
        "source": "Yahoo Finance fallback",
        "url": url,
        "market_timestamp": None,
    }


async def stock_quotes(symbols_text):
    symbols = [x.strip() for x in symbols_text.split(",") if x.strip()][:8]
    result = []

    for symbol in symbols:
        try:
            result.append(await google_finance(symbol))
        except Exception:
            try:
                result.append(await yahoo_finance(symbol))
            except Exception:
                pass

    return {
        "fetched_at": nowiso(),
        "items": result,
        "note": (
            "Public-source quote; exchange-grade zero-delay is not guaranteed."
        ),
    }


async def ddg_search(query, category, max_results=2):
    url = "https://html.duckduckgo.com/html/?q=" + quote(query)

    try:
        body = await get_text(url, 14)
    except Exception:
        return []

    pattern = (
        r'<a rel="nofollow" class="result__a" href="([^"]+)">'
        r"([\s\S]*?)</a>[\s\S]{0,1800}?"
        r'<a class="result__snippet"[\s\S]*?>([\s\S]*?)</a>'
    )

    matches = re.findall(pattern, body, re.I)
    result = []

    for href, title, snippet in matches[:max_results]:
        result.append(
            {
                "category": category,
                "title": re.sub(
                    "<[^>]+>", "", html.unescape(title)
                ).strip(),
                "snippet": re.sub(
                    "<[^>]+>", "", html.unescape(snippet)
                ).strip(),
                "url": html.unescape(href),
            }
        )

    return result



BANK_DOMAINS = {
    "Bank Jago": ["jago.com"],
    "Bank Saqu": ["banksaqu.co.id"],
    "Bank Neo Commerce": ["bankneo.co.id", "bankneocommerce.co.id"],
    "Krom Bank": ["krom.id"],
}

async def multi_search(query, category, domains=None, max_results=4):
    """
    Internet research helper.
    Searches DuckDuckGo HTML and prefers official domains when supplied.
    """
    queries = []
    if domains:
        for domain in domains:
            queries.append(f'{query} site:{domain}')
    queries.append(query)

    seen = set()
    out = []

    for q in queries:
        for item in await ddg_search(q, category, max_results):
            url = item.get("url", "")
            key = (item.get("title", ""), url)
            if key in seen:
                continue
            seen.add(key)
            item["searched_query"] = q
            item["official_hint"] = bool(
                domains and any(d.lower() in url.lower() for d in domains)
            )
            out.append(item)
            if len(out) >= max_results:
                return out
    return out


async def bank_search(names_text):
    names = [x.strip() for x in names_text.split(",") if x.strip()][:8]
    result = []

    for name in names:
        domains = BANK_DOMAINS.get(name, [])
        queries = [
            f'"{name}" bunga tabungan terbaru',
            f'"{name}" bunga deposito terbaru',
            f'"{name}" suku bunga tabungan deposito minimum setoran',
        ]
        bank_items = []
        seen = set()

        for q in queries:
            items = await multi_search(
                q,
                "Bank digital • " + name,
                domains=domains,
                max_results=3,
            )
            for item in items:
                key = item.get("url") or item.get("title")
                if key in seen:
                    continue
                seen.add(key)
                bank_items.append(item)

        result.extend(bank_items[:5])

    return {
        "fetched_at": nowiso(),
        "search_mode": "internet-web-search",
        "items": result,
    }


async def fund_search():
    searches = [
        (
            "Reksadana pasar uang",
            [
                "reksadana pasar uang return 1 tahun terbaru Indonesia",
                "reksadana pasar uang kinerja terbaru minimum pembelian",
            ],
            ["bareksa.com", "bibit.id"],
        ),
        (
            "Reksadana pendapatan tetap",
            [
                "reksadana pendapatan tetap return 1 tahun terbaru Indonesia",
                "reksadana pendapatan tetap kinerja terbaru minimum pembelian",
            ],
            ["bareksa.com", "bibit.id"],
        ),
    ]

    out = []
    for category, queries, domains in searches:
        seen = set()
        bucket = []
        for q in queries:
            items = await multi_search(q, category, domains, max_results=4)
            for item in items:
                key = item.get("url") or item.get("title")
                if key in seen:
                    continue
                seen.add(key)
                bucket.append(item)
        out.extend(bucket[:5])

    return {
        "fetched_at": nowiso(),
        "search_mode": "internet-web-search",
        "items": out[:10],
    }


async def combined_research(names_text, symbols_text):
    """
    Search the public web each time analysis is requested.
    The local Ollama receives these fresh search results as context.
    """
    banks = await bank_search(names_text)
    funds = await fund_search()
    stocks = await stock_quotes(symbols_text)
    crypto = await crypto_snapshot()

    return {
        "fetched_at": nowiso(),
        "crypto": crypto,
        "stocks": stocks,
        "banks": banks,
        "funds": funds,
    }


async def send_json(send, data, status=200):
    body = json.dumps(data, ensure_ascii=False).encode("utf-8")
    await send(
        {
            "type": "http.response.start",
            "status": status,
            "headers": [
                [b"content-type", b"application/json; charset=utf-8"],
                [b"cache-control", b"no-store"],
            ],
        }
    )
    await send({"type": "http.response.body", "body": body})


async def send_file(send, path, content_type):
    if not path.exists() or not path.is_file():
        return await send_json(send, {"error": "not found"}, 404)

    body = path.read_bytes()
    await send(
        {
            "type": "http.response.start",
            "status": 200,
            "headers": [
                [b"content-type", content_type.encode("ascii")],
                [b"content-length", str(len(body)).encode("ascii")],
            ],
        }
    )
    await send({"type": "http.response.body", "body": body})


class KosFlowASGI:
    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            return

        method = scope.get("method", "GET").upper()
        path = scope.get("path", "/")
        query = parse_qs(
            scope.get("query_string", b"").decode("utf-8", "ignore")
        )

        if method != "GET":
            return await send_json(
                send, {"error": "method not allowed"}, 405
            )

        try:
            if path == "/api/health":
                return await send_json(
                    send,
                    {
                        "status": "ok",
                        "runtime": "pure-asgi",
                        "time": nowiso(),
                    },
                )

            if path == "/api/crypto-snapshot":
                return await send_json(send, await crypto_snapshot())

            if path == "/api/stocks":
                symbols = query.get(
                    "symbols", ["BBCA,BBRI,BMRI,TLKM"]
                )[0]
                return await send_json(
                    send, await stock_quotes(symbols)
                )

            if path == "/api/banks":
                names = query.get(
                    "names",
                    [
                        "Bank Jago,Bank Saqu,"
                        "Bank Neo Commerce,Krom Bank"
                    ],
                )[0]
                return await send_json(
                    send, await bank_search(names)
                )

            if path == "/api/funds":
                return await send_json(send, await fund_search())

            if path == "/api/research":
                names = query.get(
                    "banks",
                    ["Bank Jago,Bank Saqu,Bank Neo Commerce,Krom Bank"],
                )[0]
                symbols = query.get(
                    "stocks",
                    ["BBCA,BBRI,BMRI,TLKM"],
                )[0]
                return await send_json(
                    send,
                    await combined_research(names, symbols),
                )

            if path in ("/", "/index.html"):
                return await send_file(
                    send,
                    PUBLIC / "index.html",
                    "text/html; charset=utf-8",
                )

            # Optional static files under /public/*
            if path.startswith("/public/"):
                relative = path[len("/public/") :].replace("..", "")
                file_path = PUBLIC / relative
                suffix = file_path.suffix.lower()
                mime = {
                    ".html": "text/html; charset=utf-8",
                    ".js": "application/javascript; charset=utf-8",
                    ".css": "text/css; charset=utf-8",
                    ".json": "application/json; charset=utf-8",
                    ".svg": "image/svg+xml",
                    ".png": "image/png",
                    ".jpg": "image/jpeg",
                    ".jpeg": "image/jpeg",
                    ".webp": "image/webp",
                }.get(suffix, "application/octet-stream")

                return await send_file(send, file_path, mime)

            return await send_json(send, {"error": "not found"}, 404)

        except Exception as exc:
            return await send_json(
                send,
                {
                    "error": "internal server error",
                    "detail": str(exc),
                },
                500,
            )


app = KosFlowASGI()
