
from fastapi import FastAPI, Query
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pathlib import Path
from datetime import datetime
import httpx, re, html, asyncio

BASE = Path(__file__).resolve().parent
PUBLIC = BASE / "public"
app = FastAPI(title="KosFlow AI Cloud API", version="9.0")

def nowiso():
    return datetime.now().astimezone().isoformat(timespec="seconds")

async def get_json(url, timeout=12):
    async with httpx.AsyncClient(timeout=timeout, follow_redirects=True, headers={"User-Agent":"Mozilla/5.0 KosFlowAI/9.0"}) as c:
        r = await c.get(url)
        r.raise_for_status()
        return r.json()

async def get_text(url, timeout=12):
    async with httpx.AsyncClient(timeout=timeout, follow_redirects=True, headers={"User-Agent":"Mozilla/5.0"}) as c:
        r = await c.get(url)
        r.raise_for_status()
        return r.text

async def fx_usd_idr():
    for u in [
        "https://open.er-api.com/v6/latest/USD",
        "https://api.frankfurter.app/latest?from=USD&to=IDR",
    ]:
        try:
            d = await get_json(u, 8)
            if d.get("rates", {}).get("IDR"):
                return float(d["rates"]["IDR"]), u
        except Exception:
            pass
    return None, None

@app.get("/api/crypto-snapshot")
async def crypto_snapshot():
    fx, fxsrc = await fx_usd_idr()
    out=[]
    for sym,pair in [("BTC","BTCUSDT"),("ETH","ETHUSDT"),("SOL","SOLUSDT")]:
        t0=asyncio.get_event_loop().time()
        try:
            d=await get_json(f"https://api.binance.com/api/v3/ticker/24hr?symbol={pair}",8)
            ms=round((asyncio.get_event_loop().time()-t0)*1000)
            usdt=float(d["lastPrice"])
            out.append({
                "symbol":sym,
                "pair":pair,
                "price_usdt":usdt,
                "price_idr": usdt*fx if fx else None,
                "change_24h":float(d["priceChangePercent"]),
                "source":"Binance REST snapshot",
                "fetch_ms":ms
            })
        except Exception:
            pass
    if not out:
        try:
            u="https://api.coingecko.com/api/v3/simple/price?ids=bitcoin,ethereum,solana&vs_currencies=idr&include_24hr_change=true"
            d=await get_json(u,10)
            for k,s in [("bitcoin","BTC"),("ethereum","ETH"),("solana","SOL")]:
                x=d.get(k,{})
                out.append({"symbol":s,"price_idr":x.get("idr"),"price_usdt":None,"change_24h":x.get("idr_24h_change"),"source":"CoinGecko fallback","fetch_ms":None})
        except Exception:
            pass
    return {"fetched_at":nowiso(),"fx_source":fxsrc,"items":out}

async def google_finance(symbol):
    sym=symbol.upper().replace(".JK","")
    url=f"https://www.google.com/finance/quote/{sym}:IDX?hl=id"
    body=await get_text(url,10)
    mp=re.search(r'data-last-price="([0-9.,]+)"',body)
    mt=re.search(r'data-last-normal-market-timestamp="([0-9]+)"',body)
    if not mp:
        raise ValueError("Google Finance parse failed")
    price=float(mp.group(1).replace(",",""))
    ts=None
    if mt:
        try:
            ts=datetime.fromtimestamp(int(mt.group(1))).astimezone().isoformat(timespec="seconds")
        except: pass
    pct=0.0
    plain=re.sub(r"<[^>]+>"," ",body)
    mpc=re.search(r'([+\-]?[0-9]+(?:[.,][0-9]+)?)%',plain)
    if mpc:
        try:pct=float(mpc.group(1).replace(",","."))
        except:pass
    return {"symbol":sym,"price":price,"change_percent":pct,"currency":"IDR","source":"Google Finance","url":url,"market_timestamp":ts}

async def yahoo_finance(symbol):
    sym=symbol.upper().replace(".JK","")
    ticker=sym+".JK"
    u=f"https://query1.finance.yahoo.com/v8/finance/chart/{ticker}?interval=1m&range=1d"
    d=await get_json(u,10)
    r=d["chart"]["result"][0]
    m=r.get("meta",{})
    price=m.get("regularMarketPrice")
    prev=m.get("chartPreviousClose") or m.get("previousClose")
    pct=((price-prev)/prev*100) if price and prev else 0
    return {"symbol":sym,"price":price,"change_percent":pct,"currency":m.get("currency","IDR"),"source":"Yahoo Finance fallback","url":u,"market_timestamp":None}

@app.get("/api/stocks")
async def stocks(symbols: str = Query("BBCA,BBRI,BMRI,TLKM")):
    syms=[x.strip() for x in symbols.split(",") if x.strip()][:8]
    out=[]
    for s in syms:
        try:
            out.append(await google_finance(s))
        except Exception:
            try: out.append(await yahoo_finance(s))
            except Exception: pass
    return {"fetched_at":nowiso(),"items":out,"note":"Public-source quote; exchange-grade zero-delay is not guaranteed."}

async def ddg_search(query, category, max_results=2):
    u="https://html.duckduckgo.com/html/?q="+httpx.QueryParams({"q":query})["q"]
    # safer explicit quoting:
    import urllib.parse
    u="https://html.duckduckgo.com/html/?q="+urllib.parse.quote(query)
    try:
        body=await get_text(u,14)
    except Exception:
        return []
    patt=r'<a rel="nofollow" class="result__a" href="([^"]+)">([\s\S]*?)</a>[\s\S]{0,1800}?<a class="result__snippet"[\s\S]*?>([\s\S]*?)</a>'
    matches=re.findall(patt,body,re.I)
    out=[]
    for href,title,snip in matches[:max_results]:
        out.append({
            "category":category,
            "title":re.sub("<[^>]+>","",html.unescape(title)).strip(),
            "snippet":re.sub("<[^>]+>","",html.unescape(snip)).strip(),
            "url":html.unescape(href)
        })
    return out

BANK_DOMAINS={
    "Bank Jago":"jago.com",
    "Bank Saqu":"banksaqu.co.id",
    "Bank Neo Commerce":"bankneo.co.id",
    "Krom Bank":"krom.id"
}

@app.get("/api/banks")
async def banks(names: str = Query("Bank Jago,Bank Saqu,Bank Neo Commerce,Krom Bank")):
    arr=[x.strip() for x in names.split(",") if x.strip()][:8]
    out=[]
    for name in arr:
        domain=BANK_DOMAINS.get(name)
        q=f'"{name}" bunga tabungan deposito suku bunga terbaru'
        if domain:q+=f" site:{domain}"
        out.extend(await ddg_search(q,"Bank digital • "+name,2))
    return {"fetched_at":nowiso(),"items":out}

@app.get("/api/funds")
async def funds():
    queries=[
        ("Reksadana pasar uang",'reksadana pasar uang return 1 tahun terbaru Indonesia site:bareksa.com OR site:bibit.id'),
        ("Reksadana pendapatan tetap",'reksadana pendapatan tetap return 1 tahun terbaru Indonesia site:bareksa.com OR site:bibit.id')
    ]
    out=[]
    for cat,q in queries:
        out.extend(await ddg_search(q,cat,3))
    return {"fetched_at":nowiso(),"items":out[:6]}

@app.get("/")
async def home():
    return FileResponse(PUBLIC/"index.html")

app.mount("/", StaticFiles(directory=PUBLIC, html=True), name="public")
