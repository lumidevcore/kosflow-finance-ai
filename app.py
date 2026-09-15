from pathlib import Path
from datetime import datetime
from urllib.parse import parse_qs, quote
import asyncio
import html
import json
import re
import os

import httpx

BASE = Path(__file__).resolve().parent
PUBLIC = BASE / "public"
SUPABASE_URL = os.environ.get("SUPABASE_URL", "").rstrip("/")
SUPABASE_SERVICE_ROLE_KEY = os.environ.get("SUPABASE_SERVICE_ROLE_KEY", "")


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
    "SeaBank": ["seabank.co.id"],
    "Superbank": ["superbank.id"],
}

BANK_OFFICIAL_PAGES = {
    "Bank Jago": [
        "https://www.jago.com/id/jago/rates",
    ],
    "Bank Saqu": [
        "https://banksaqu.co.id/blog/informasi-bunga-saku-nabung",
        "https://banksaqu.co.id/products/saku-booster-11",
        "https://banksaqu.co.id/blog/deposito-saku-gajian",
        "https://banksaqu.co.id/products/deposito-reguler-10",
        "https://banksaqu.co.id/blog/update-suku-bunga-deposito-reguler-mulai-1-november-2025",
    ],
    "Bank Neo Commerce": [
        "https://www.bankneo.co.id/",
    ],
    "Krom Bank": [
        "https://krom.id/produk/",
        "https://krom.id/faq/",
        "https://krom.id/pengumuman-penyesuaian-suku-bunga-deposito-krom-flex-dan-krom-max/",
    ],
    "SeaBank": [
        "https://www.seabank.co.id/produk-layanan/konvensional",
    ],
    "Superbank": [
        "https://www.superbank.id/",
        "https://www.superbank.id/content/produk-layanan/tabungan/celengan/Ringkasan%20Informasi%20Produk%20dan%20Layanan.pdf",
    ],
}


def _clean_html_text(body):
    body = re.sub(r"(?is)<script[^>]*>.*?</script>", " ", body)
    body = re.sub(r"(?is)<style[^>]*>.*?</style>", " ", body)
    body = re.sub(r"(?is)<noscript[^>]*>.*?</noscript>", " ", body)
    text = re.sub(r"(?s)<[^>]+>", " ", body)
    text = html.unescape(text)
    text = re.sub(r"\s+", " ", text).strip()
    return text


def _extract_interest_snippet(text, max_len=500):
    if not text:
        return ""
    lowered = text.lower()
    keys = [
        "suku bunga",
        "bunga tabungan",
        "bunga deposito",
        "deposito",
        "tabungan",
        "minimum penempatan",
        "minimum setoran",
        "p.a.",
        "per annum",
    ]
    positions = [lowered.find(k) for k in keys if lowered.find(k) >= 0]
    start = max(0, (min(positions) if positions else 0) - 180)
    return text[start:start + max_len].strip()



def _extract_rate_facts(text, max_facts=4):
    """Extract percentage mentions with short surrounding context."""
    if not text:
        return []
    clean = re.sub(r"\s+", " ", text).strip()
    facts = []
    seen = set()
    # Match common Indonesian rate notation: 10%, 7.75%, 7,5% p.a.
    for m in re.finditer(r"(?<!\d)(\d{1,2}(?:[.,]\d{1,3})?)\s*%(?:\s*(?:p\.?\s*a\.?|per\s+annum|per\s+tahun))?", clean, re.I):
        raw_rate = m.group(1).replace(",", ".")
        try:
            val = float(raw_rate)
        except Exception:
            continue
        if val < 0 or val > 30:
            continue
        start = max(0, m.start()-75)
        end = min(len(clean), m.end()+95)
        context = clean[start:end].strip(" -|:;,")
        key = (round(val,4), context.lower())
        if key in seen:
            continue
        seen.add(key)
        facts.append({
            "rate": f"{val:g}% p.a.",
            "rate_percent": val,
            "context": context[:180],
        })
        if len(facts) >= max_facts:
            break
    return facts


def _attach_rate_facts(item):
    text = " ".join([
        str(item.get("title") or ""),
        str(item.get("snippet") or ""),
    ])
    item["rate_facts"] = _extract_rate_facts(text)
    return item


async def fetch_official_bank_pages(bank_name):
    out = []
    for url in BANK_OFFICIAL_PAGES.get(bank_name, []):
        try:
            body = await get_text(url, 14)
            text = _clean_html_text(body)
            snippet = _extract_interest_snippet(text)
            if not snippet:
                continue
            title_match = re.search(r"(?is)<title[^>]*>(.*?)</title>", body)
            title = (
                _clean_html_text(title_match.group(1))
                if title_match
                else f"{bank_name} official rates"
            )
            out.append(_attach_rate_facts({
                "category": "Bank digital • " + bank_name,
                "title": title[:180],
                "snippet": snippet,
                "url": url,
                "source_type": "official-direct",
                "official_hint": True,
            }))
        except Exception:
            pass
    return out


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


async def _bank_search_one(name, selected_ranges):
    domains = BANK_DOMAINS.get(name, [])
    bank_items = []
    seen = set()
    try:
        official = await asyncio.wait_for(fetch_official_bank_pages(name), timeout=8)
    except Exception:
        official = []
    for item in official:
        key = item.get("url") or item.get("title")
        if key in seen: continue
        seen.add(key); bank_items.append(item)
    has_rate = any(item.get("rate_facts") for item in bank_items)
    if not has_rate:
        range_hint = " ".join(selected_ranges[:2])
        q = f'"{name}" bunga tabungan deposito terbaru {range_hint}'.strip()
        try:
            items = await asyncio.wait_for(multi_search(q, "Bank digital • " + name, domains=domains, max_results=3), timeout=8)
        except Exception:
            items = []
        for item in items:
            key = item.get("url") or item.get("title")
            if key in seen: continue
            seen.add(key); item.setdefault("source_type","web-search"); _attach_rate_facts(item); bank_items.append(item)
    bank_items.sort(key=lambda x:(0 if x.get("official_hint") else 1,0 if x.get("rate_facts") else 1,0 if x.get("source_type")=="official-direct" else 1))
    return bank_items[:4]

async def bank_search(names_text, rate_ranges_text=''):
    names=[x.strip() for x in names_text.split(',') if x.strip()][:10]
    selected_ranges=[x.strip() for x in rate_ranges_text.split(',') if x.strip()]
    chunks=await asyncio.gather(*(_bank_search_one(name,selected_ranges) for name in names),return_exceptions=True)
    result=[]
    for chunk in chunks:
        if isinstance(chunk,list): result.extend(chunk)
    return {"fetched_at":nowiso(),"search_mode":"fast-official-first+single-web-fallback","banks_requested":names,"selected_interest_ranges":selected_ranges,"items":result}

async def fund_search():
    jobs=[("Reksadana pasar uang","reksadana pasar uang return 1 tahun terbaru Indonesia",["bareksa.com","bibit.id"]),("Reksadana pendapatan tetap","reksadana pendapatan tetap return 1 tahun terbaru Indonesia",["bareksa.com","bibit.id"])]
    async def one(category,q,domains):
        try: return await asyncio.wait_for(multi_search(q,category,domains,max_results=3),timeout=8)
        except Exception: return []
    chunks=await asyncio.gather(*(one(category,q,domains) for category,q,domains in jobs),return_exceptions=True)
    out=[]; seen=set()
    for chunk in chunks:
        if not isinstance(chunk,list): continue
        for item in chunk:
            key=item.get("url") or item.get("title")
            if key in seen: continue
            seen.add(key); out.append(item)
    return {"fetched_at":nowiso(),"search_mode":"fast-internet-web-search","items":out[:6]}


def _parse_dividend_per_share(text):
    if not text:
        return None
    patterns = [
        r"(?:dividen(?: tunai)?(?: sebesar)?|dividend)\s*(?:Rp\.?\s*)?([0-9][0-9.,]*)\s*(?:per|/)\s*(?:saham|share)",
        r"Rp\.?\s*([0-9][0-9.,]*)\s*(?:per|/)\s*(?:saham|share)",
    ]
    for p in patterns:
        m = re.search(p, text, re.I)
        if m:
            raw=m.group(1).replace('.', '').replace(',', '.')
            try: return float(raw)
            except Exception: pass
    return None


async def find_dividend_info(symbol, price=None):
    year=datetime.now().year
    queries=[
        f'"{symbol}" dividen tunai {year} per saham IDX',
        f'"{symbol}" dividen {year} per saham',
        f'"{symbol}" dividend yield Indonesia {year}',
    ]
    seen=set(); results=[]
    for q in queries:
        for item in await ddg_search(q, f'Dividen • {symbol}', 3):
            key=item.get('url') or item.get('title')
            if key in seen: continue
            seen.add(key); results.append(item)
        if results: break
    snippet=' '.join([(x.get('title','')+' '+x.get('snippet','')).strip() for x in results[:2]])
    dps=_parse_dividend_per_share(snippet)
    dy=(dps/float(price)*100.0) if dps is not None and price else None
    return {
        'dividend_per_share': dps,
        'dividend_yield_estimate': dy,
        'dividend_title': results[0].get('title') if results else None,
        'dividend_snippet': results[0].get('snippet') if results else None,
        'dividend_source_url': results[0].get('url') if results else None,
        'dividend_sources': results[:3],
    }


async def enrich_dividends(items):
    async def one(item):
        try:
            info=await asyncio.wait_for(find_dividend_info(item.get('symbol',''),item.get('price')),timeout=7)
            item.update(info)
        except Exception:
            pass
        return item
    if not items: return items
    return list(await asyncio.gather(*(one(item) for item in items)))


IDX_CANDIDATE_UNIVERSE = [
    "BBCA","BBRI","BMRI","BBNI","TLKM","ASII","ICBP","INDF","UNVR","PGAS",
    "ANTM","PTBA","ADRO","MDKA","BRIS","EXCL","ISAT","GOTO","BUKA","ACES",
    "CPIN","JPFA","MYOR","KLBF","SIDO","ERAA","MAPI","ESSA","INKP","TKIM",
    "TOWR","MTEL","SMGR","INTP","JSMR","AKRA","MEDC","HRUM","INDY","SCMA",
    "EMTK","AUTO","LSIP","AALI","TBIG","MIKA","HEAL","SRTG","WIIM","ELSA"
]

async def discover_affordable_stocks(max_lot_budget, limit=5):
    try: budget=float(max_lot_budget or 0)
    except Exception: budget=0
    limit=max(1,min(int(limit or 5),10)); sem=asyncio.Semaphore(10)
    async def quote_one(symbol):
        async with sem:
            try:
                try: item=await asyncio.wait_for(google_finance(symbol),timeout=5)
                except Exception: item=await asyncio.wait_for(yahoo_finance(symbol),timeout=5)
                price=item.get("price")
                if price is None: return None
                item["lot_cost"]=float(price)*100.0; item["lot_size"]=100; return item
            except Exception: return None
    raw=await asyncio.gather(*(quote_one(symbol) for symbol in IDX_CANDIDATE_UNIVERSE),return_exceptions=True)
    items=[x for x in raw if isinstance(x,dict) and (budget<=0 or float(x.get("lot_cost") or 0)<=budget)]
    items.sort(key=lambda x:(-(x.get("lot_cost") or 0),abs(float(x.get("change_percent") or 0))))
    picked=items[:limit]; await enrich_dividends(picked); return picked

async def combined_research(names_text, symbols_text, rate_ranges_text='', stock_lot_mode='manual', stock_lot_budget=0, stock_recommendation_count=5):
    async def get_stocks():
        if stock_lot_mode=="auto":
            candidates=await discover_affordable_stocks(stock_lot_budget,stock_recommendation_count)
            return {"fetched_at":nowiso(),"mode":"auto-search-only","lot_size":100,"max_lot_budget":float(stock_lot_budget or 0),"candidates":candidates,"items":candidates,"note":"Candidate search only. No balance is deducted."}
        stocks=await stock_quotes(symbols_text)
        for item in stocks.get("items",[]):
            if item.get("price") is not None:
                item["lot_size"]=100; item["lot_cost"]=float(item["price"])*100.0
        stocks["mode"]="manual"; await enrich_dividends(stocks.get("items",[])); return stocks
    async def bounded(coro,timeout,fallback):
        try: return await asyncio.wait_for(coro,timeout=timeout)
        except Exception as exc:
            result=dict(fallback); result["warning"]=type(exc).__name__; return result
    banks,funds,stocks=await asyncio.gather(
        bounded(bank_search(names_text,rate_ranges_text),18,{"fetched_at":nowiso(),"items":[],"search_mode":"timeout-fallback"}),
        bounded(fund_search(),12,{"fetched_at":nowiso(),"items":[],"search_mode":"timeout-fallback"}),
        bounded(get_stocks(),22,{"fetched_at":nowiso(),"items":[],"candidates":[],"mode":stock_lot_mode}),
    )
    return {"fetched_at":nowiso(),"research_mode":"parallel-bounded-v10.24","stocks":stocks,"banks":banks,"funds":funds}

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


def supabase_configured():
    return bool(SUPABASE_URL and SUPABASE_SERVICE_ROLE_KEY)


def sb_headers(prefer=None):
    h = {
        "apikey": SUPABASE_SERVICE_ROLE_KEY,
        "Authorization": f"Bearer {SUPABASE_SERVICE_ROLE_KEY}",
        "Content-Type": "application/json",
    }
    if prefer:
        h["Prefer"] = prefer
    return h


async def sb_request(method, table, params=None, body=None, prefer=None):
    if not supabase_configured():
        return None
    url = f"{SUPABASE_URL}/rest/v1/{table}"
    async with httpx.AsyncClient(timeout=20, follow_redirects=True) as c:
        r = await c.request(method, url, params=params, headers=sb_headers(prefer), json=body)
        r.raise_for_status()
        if not r.content:
            return []
        return r.json()


async def ensure_device(browser_device_id):
    rows = await sb_request("GET", "devices", {"browser_device_id": f"eq.{browser_device_id}", "select":"id,browser_device_id", "limit":"1"})
    if rows:
        return rows[0]
    rows = await sb_request("POST", "devices", body={"browser_device_id":browser_device_id}, prefer="return=representation")
    return rows[0]


async def read_json_body(receive):
    chunks=[]
    while True:
        msg=await receive()
        if msg.get("type")!="http.request":
            break
        chunks.append(msg.get("body",b""))
        if not msg.get("more_body",False):
            break
    raw=b"".join(chunks)
    return json.loads(raw.decode("utf-8")) if raw else {}


async def load_state(device_id):
    if not supabase_configured():
        return {"cloud_configured":False,"state":None}
    dev=await ensure_device(device_id)
    did=dev["id"]
    pair=await sb_request("GET","ollama_pairings",{"device_id":f"eq.{did}","select":"bridge_url,model_name,token_ciphertext,token_iv,updated_at","limit":"1"})
    fin=await sb_request("GET","finance_settings",{"device_id":f"eq.{did}","select":"*","limit":"1"})
    settings=None
    if fin:
        f=fin[0]
        settings={
            "cash":f.get("cash"),"allowance":f.get("allowance"),"income_weekly_min":f.get("income_weekly_min") or 0,"income_weekly_max":f.get("income_weekly_max") or 0,"monthly_kos":f.get("monthly_kos"),"weeks":f.get("weeks"),"buffer":f.get("buffer"),"weekly_needs":f.get("weekly_needs"),"risk":f.get("risk"),"stocks":f.get("stocks"),"stock_lot_mode":f.get("stock_lot_mode") or "auto","stock_lot_budget":f.get("stock_lot_budget") or 100000,"stock_recommendation_count":f.get("stock_recommendation_count") or 5,"banks":f.get("banks"),"bank_interest_ranges":f.get("bank_interest_ranges") or ["0.5-4","4-6"],"bank_simulation_amount":f.get("bank_simulation_amount") or 0,"bank_simulation_months":f.get("bank_simulation_months") or 12,"bank_compound_frequency":f.get("bank_compound_frequency") or 12,
                    "market_simulation_months":f.get("market_simulation_months") or 12,
                    "market_bear_growth_pct":f.get("market_bear_growth_pct") if f.get("market_bear_growth_pct") is not None else -10,
                    "market_base_growth_pct":f.get("market_base_growth_pct") if f.get("market_base_growth_pct") is not None else 8,
                    "market_bull_growth_pct":f.get("market_bull_growth_pct") if f.get("market_bull_growth_pct") is not None else 20,"notes":f.get("notes"),"kos_source":f.get("kos_source"),"kos_self_contribution":f.get("kos_self_contribution") or 0,"kos_parent_contribution":f.get("kos_parent_contribution") or 0,"kos_cycle_start":f.get("kos_cycle_start"),"kos_funding_scope":f.get("kos_funding_scope") or "current_cycle","bridge_url":pair[0].get("bridge_url") if pair else None,"model_name":pair[0].get("model_name") if pair else None,
        }
    return {"cloud_configured":True,"state":{"settings":settings,"pairing":pair[0] if pair else None}}


async def save_state(payload):
    if not supabase_configured():
        return {"cloud_saved":False,"reason":"supabase_not_configured"}
    dev=await ensure_device(payload["device_id"]); did=dev["id"]
    pairing=payload.get("pairing") or {}; settings=payload.get("settings") or {}
    pair_body={"device_id":did,"bridge_url":pairing.get("bridge_url"),"model_name":pairing.get("model_name"),"token_ciphertext":pairing.get("token_ciphertext"),"token_iv":pairing.get("token_iv")}
    await sb_request("POST","ollama_pairings",{"on_conflict":"device_id"},pair_body,"resolution=merge-duplicates,return=minimal")
    fin_body={"device_id":did,"cash":settings.get("cash"),"allowance":settings.get("allowance"),"income_weekly_min":settings.get("income_weekly_min") or 0,"income_weekly_max":settings.get("income_weekly_max") or 0,"monthly_kos":settings.get("monthly_kos"),"weeks":settings.get("weeks"),"buffer":settings.get("buffer"),"weekly_needs":settings.get("weekly_needs"),"risk":settings.get("risk"),"stocks":settings.get("stocks"),"stock_lot_mode":settings.get("stock_lot_mode") or "auto","stock_lot_budget":settings.get("stock_lot_budget") or 100000,"stock_recommendation_count":settings.get("stock_recommendation_count") or 5,"banks":settings.get("banks"),"bank_interest_ranges":settings.get("bank_interest_ranges") or ["0.5-4","4-6"],"bank_simulation_amount":settings.get("bank_simulation_amount") or 0,"bank_simulation_months":settings.get("bank_simulation_months") or 12,"bank_compound_frequency":settings.get("bank_compound_frequency") or 12,
                         "market_simulation_months":settings.get("market_simulation_months") or 12,
                         "market_bear_growth_pct":settings.get("market_bear_growth_pct") if settings.get("market_bear_growth_pct") is not None else -10,
                         "market_base_growth_pct":settings.get("market_base_growth_pct") if settings.get("market_base_growth_pct") is not None else 8,
                         "market_bull_growth_pct":settings.get("market_bull_growth_pct") if settings.get("market_bull_growth_pct") is not None else 20,"notes":settings.get("notes"),"kos_source":settings.get("kos_source"),"kos_self_contribution":settings.get("kos_self_contribution") or 0,"kos_parent_contribution":settings.get("kos_parent_contribution") or 0,"kos_cycle_start":settings.get("kos_cycle_start"),"kos_funding_scope":settings.get("kos_funding_scope") or "current_cycle"}
    await sb_request("POST","finance_settings",{"on_conflict":"device_id"},fin_body,"resolution=merge-duplicates,return=minimal")
    return {"cloud_saved":True}


async def save_analysis(payload):
    if not supabase_configured():
        return {"cloud_saved":False}
    dev=await ensure_device(payload["device_id"]); did=dev["id"]
    body={"device_id":did,"model_name":payload.get("model_name"),"input_json":payload.get("input_json"),"result_json":payload.get("result_json"),"ai_json":payload.get("ai_json"),"ai_raw":payload.get("ai_raw")}
    rows=await sb_request("POST","analyses",body=body,prefer="return=representation"); aid=rows[0]["id"]
    market=payload.get("market") or {}
    snaps=[]
    for typ,obj in [("crypto",market.get("crypto") or {}),("stock",market.get("stocks") or {})]:
        for x in obj.get("items",[]):
            snaps.append({"analysis_id":aid,"asset_type":typ,"symbol":x.get("symbol"),"price":x.get("price_idr") if typ=="crypto" else x.get("price"),"change_percent":x.get("change_24h") if typ=="crypto" else x.get("change_percent"),"source":x.get("source"),"source_url":x.get("url"),"market_timestamp":x.get("market_timestamp"),"raw":x})
    if snaps: await sb_request("POST","market_snapshots",body=snaps,prefer="return=minimal")
    research=payload.get("research") or {}; srcs=[]
    for cat,obj in [("bank",research.get("banks") or {}),("fund",research.get("funds") or {})]:
        for x in obj.get("items",[]):
            srcs.append({"analysis_id":aid,"category":cat,"entity_name":x.get("category"),"title":x.get("title"),"snippet":x.get("snippet"),"source_url":x.get("url"),"official":bool(x.get("official_hint")),"raw":x})
    if srcs: await sb_request("POST","research_sources",body=srcs,prefer="return=minimal")
    return {"cloud_saved":True,"analysis_id":aid}


async def list_analyses(device_id, limit=1):
    if not supabase_configured(): return {"cloud_configured":False,"items":[]}
    dev=await ensure_device(device_id)
    rows=await sb_request("GET","analyses",{"device_id":f"eq.{dev['id']}","select":"id,created_at,model_name,result_json,ai_json,ai_raw","order":"created_at.desc","limit":str(max(1,min(limit,20)))})
    return {"cloud_configured":True,"items":rows or []}


class KosFlowASGI:
    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            return

        method = scope.get("method", "GET").upper()
        path = scope.get("path", "/")
        query = parse_qs(
            scope.get("query_string", b"").decode("utf-8", "ignore")
        )

        try:
            if method == "GET" and path == "/api/health":
                return await send_json(
                    send,
                    {
                        "status": "ok",
                        "runtime": "pure-asgi",
                        "time": nowiso(),
                    },
                )

            if method == "GET" and path == "/api/cloud/status":
                return await send_json(send,{"configured":supabase_configured()})

            if method == "GET" and path == "/api/state":
                device_id=(query.get("device_id") or [""])[0]
                if not device_id: return await send_json(send,{"error":"device_id required"},400)
                return await send_json(send,await load_state(device_id))

            if method == "POST" and path == "/api/state":
                payload=await read_json_body(receive)
                if not payload.get("device_id"): return await send_json(send,{"error":"device_id required"},400)
                return await send_json(send,await save_state(payload))

            if method == "GET" and path == "/api/analyses":
                device_id=(query.get("device_id") or [""])[0]
                limit=int((query.get("limit") or ["1"])[0])
                if not device_id: return await send_json(send,{"error":"device_id required"},400)
                return await send_json(send,await list_analyses(device_id,limit))

            if method == "POST" and path == "/api/analyses":
                payload=await read_json_body(receive)
                if not payload.get("device_id"): return await send_json(send,{"error":"device_id required"},400)
                return await send_json(send,await save_analysis(payload))

            if method == "GET" and path == "/api/crypto-snapshot":
                return await send_json(send, await crypto_snapshot())

            if method == "GET" and path == "/api/stocks":
                symbols = query.get(
                    "symbols", ["BBCA,BBRI,BMRI,TLKM"]
                )[0]
                return await send_json(
                    send, await stock_quotes(symbols)
                )

            if method == "GET" and path == "/api/banks":
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

            if method == "GET" and path == "/api/funds":
                return await send_json(send, await fund_search())

            if method == "GET" and path == "/api/research":
                names = query.get(
                    "banks",
                    ["Bank Jago,Bank Saqu,Bank Neo Commerce,Krom Bank,SeaBank"],
                )[0]
                symbols = query.get(
                    "stocks",
                    ["BBCA,BBRI,BMRI,TLKM"],
                )[0]
                ranges = query.get("bank_rate_ranges", [""])[0]
                stock_lot_mode = query.get("stock_lot_mode", ["manual"])[0]
                stock_lot_budget = query.get("stock_lot_budget", ["0"])[0]
                stock_recommendation_count = query.get("stock_recommendation_count", ["5"])[0]
                return await send_json(
                    send,
                    await combined_research(
                        names,
                        symbols,
                        ranges,
                        stock_lot_mode,
                        stock_lot_budget,
                        stock_recommendation_count,
                    ),
                )

            if method == "GET" and path in ("/", "/index.html"):
                return await send_file(
                    send,
                    PUBLIC / "index.html",
                    "text/html; charset=utf-8",
                )

            # Optional static files under /public/*
            if method == "GET" and path.startswith("/public/"):
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
