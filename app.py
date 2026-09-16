from pathlib import Path
from datetime import datetime
from urllib.parse import urlsplit, parse_qs, quote
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


INDONESIA_INVESTMENT_DOMAINS = (
    "idx.co.id",
    "ajaib.co.id",
    "makmur.id",
    "bareksa.com",
    "bibit.id",
    "bni-am.co.id",
    "syailendracapital.com",
    "mandiri-investasi.co.id",
    "bri-mi.co.id",
    "manulifeim.co.id",
    "sucorinvest.com",
    "batavia-am.co.id",
    "eastspring.co.id",
    "principal.co.id",
    "bahana.co.id",
    "trimegah-am.com",
    "panin-am.co.id",
    "ciptadana.com",
)

FUND_RELEVANCE_TERMS = (
    "reksa dana", "reksadana", "pasar uang", "pendapatan tetap",
    "obligasi", "campuran", "saham", "nab", "aum", "fund",
    "manajer investasi", "expense ratio", "minimum pembelian",
)

def _host_from_url(url):
    try:
        return re.sub(r"^www\.", "", urlsplit(url).netloc.lower())
    except Exception:
        return ""

def _allowed_indonesia_investment_url(url):
    host = _host_from_url(url)
    return any(host == d or host.endswith("." + d) for d in INDONESIA_INVESTMENT_DOMAINS)

def _fund_result_relevant(item):
    if not _allowed_indonesia_investment_url(item.get("url") or ""):
        return False
    text = " ".join([
        str(item.get("title") or ""),
        str(item.get("snippet") or ""),
        str(item.get("url") or ""),
    ]).lower()
    return any(term in text for term in FUND_RELEVANCE_TERMS)

async def google_web_search(query, category, max_results=5):
    """Google Search HTML, bahasa Indonesia. Hasil kemudian difilter domain investasi Indonesia."""
    url = "https://www.google.com/search?hl=id&gl=id&num=10&q=" + quote(query)
    try:
        body = await get_text(url, 10)
    except Exception:
        return []

    out, seen = [], set()
    # Google HTML layout changes often; handle the common /url?q= links and direct links.
    for href, title_html in re.findall(
        r'(?is)<a[^>]+href="(?:/url\?q=)?(https?://[^"&]+)[^"]*"[^>]*>(.*?)</a>',
        body,
    ):
        href = html.unescape(href)
        if href in seen or not _allowed_indonesia_investment_url(href):
            continue
        title = re.sub(r"<[^>]+>", " ", html.unescape(title_html))
        title = re.sub(r"\s+", " ", title).strip()
        if not title:
            continue
        item = {"category": category, "title": title[:220], "snippet": "", "url": href}
        if not _fund_result_relevant(item):
            continue
        seen.add(href)
        out.append(item)
        if len(out) >= max_results:
            break
    return out


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


async def bing_search(query, category, max_results=4):
    """Fallback search when DuckDuckGo HTML returns zero/blocked."""
    url = "https://www.bing.com/search?q=" + quote(query) + "&setlang=id"
    try:
        body = await get_text(url, 10)
    except Exception:
        return []

    blocks = re.findall(r'(?is)<li[^>]+class="[^"]*b_algo[^"]*"[^>]*>(.*?)</li>', body)
    out = []
    for block in blocks:
        m = re.search(r'(?is)<h2[^>]*>\s*<a[^>]+href="([^"]+)"[^>]*>(.*?)</a>', block)
        if not m:
            continue
        href = html.unescape(m.group(1))
        title = re.sub(r"<[^>]+>", "", html.unescape(m.group(2))).strip()
        p = re.search(r'(?is)<p[^>]*>(.*?)</p>', block)
        snippet = re.sub(r"<[^>]+>", "", html.unescape(p.group(1))).strip() if p else ""
        out.append({"category":category,"title":title,"snippet":snippet,"url":href})
        if len(out) >= max_results:
            break
    return out


async def search_any(query, category, max_results=5):
    """
    Google Indonesia menjadi sumber discovery utama.
    DDG/Bing hanya fallback dan semua hasil WAJIB lolos allowlist sumber investasi Indonesia.
    """
    chunks = await asyncio.gather(
        google_web_search(query, category, max_results),
        ddg_search(query, category, max_results),
        bing_search(query, category, max_results),
        return_exceptions=True,
    )
    out, seen = [], set()
    for chunk in chunks:
        if not isinstance(chunk, list):
            continue
        for item in chunk:
            if not _fund_result_relevant(item):
                continue
            key = item.get("url") or item.get("title")
            if not key or key in seen:
                continue
            seen.add(key)
            out.append(item)
            if len(out) >= max_results:
                return out
    return out


FUND_SEED_PRODUCTS = [
    {
        "fund_category":"money_market",
        "manager":"Syailendra Capital",
        "product_name":"Syailendra Dana Kas (SDK)",
        "url":"https://www.syailendracapital.com/public/index.php/en/product/reksa-dana-pasar-uang",
    },
    {
        "fund_category":"fixed_income",
        "manager":"Syailendra Capital",
        "product_name":"Syailendra Fixed Income Fund (SFIF)",
        "url":"https://syailendracapital.com/product/reksa-dana-pendapatan-tetap/syailendra-fixed-income-fund-sfif",
    },
    {
        "fund_category":"mixed",
        "manager":"Syailendra Capital",
        "product_name":"Syailendra Balanced Opportunity Fund (SBOF) Kelas A",
        "url":"https://www.syailendracapital.com/public/index.php/product/reksa-dana-campuran",
    },
    {
        "fund_category":"equity",
        "manager":"Syailendra Capital",
        "product_name":"Syailendra Equity Opportunity Fund (SEOF) Kelas A",
        "url":"https://www.syailendracapital.com/en/product/reksa-dana-saham/syailendra-equity-opportunity-fund-seof",
    },
    {
        "fund_category":"money_market",
        "manager":"Mandiri Manajemen Investasi",
        "product_name":"Mandiri Investa Pasar Uang 2 (MIPU2)",
        "url":"https://www.mandiri-investasi.co.id/id/produk/reksa-dana/pasar-uang/mandiri-investa-pasar-uang-2-mipu-2/",
    },
    {
        "fund_category":"money_market",
        "manager":"BNI Asset Management",
        "product_name":"BNI-AM Dana Pasar Uang Kemilau Kelas A",
        "url":"https://www.bni-am.co.id/produk.html/1/Reksa-Dana-Pasar-Uang",
    },
]


def _sentence_complete_excerpt(text, keywords=(), max_sentences=5):
    if not text:
        return []
    clean=re.sub(r"\s+"," ",text).strip()
    # Remove common navigation noise before sentence extraction.
    noise=[
        "Why Syailendra","Company Philosophy","Manajemen & Struktur Organisasi",
        "Management & Organization Structure","Goals Planner","Download Center",
        "Help Center","Contact Us","Customer Complaints","YO! Inves","Yo! Inves",
        "Beranda","Tentang Kami","Kebijakan Privasi","Info Karir","Pengumuman Lelang",
    ]
    for n in noise:
        clean=clean.replace(n," ")
    clean=re.sub(r"\s+"," ",clean).strip()
    sentences=re.split(r"(?<=[.!?])\s+",clean)
    out=[]
    for s in sentences:
        ss=s.strip()
        if len(ss)<15 or len(ss)>320:
            continue
        low=ss.lower()
        if keywords and not any(k.lower() in low for k in keywords):
            continue
        out.append(ss)
        if len(out)>=max_sentences:
            break
    return out

def _extract_fund_detail_facts(text):
    facts=[]
    clean=re.sub(r"\s+"," ",text or "").strip()
    patterns=[
        (r"(?:NAB Per Unit|NAB/unit)\s*[:\-]?\s*(?:Rp\.?\s*)?([0-9.,]+)", "NAB per unit"),
        (r"(?:1 Tahun|1Y)\s*[:\-]?\s*([+\-]?[0-9.,]+%)", "Return 1 tahun"),
        (r"(?:1 Bulan|1M)\s*[:\-]?\s*([+\-]?[0-9.,]+%)", "Return 1 bulan"),
        (r"Minimum Pembelian\s*[:\-]?\s*(Rp\.?\s*[0-9.,]+)", "Minimum pembelian"),
        (r"(?:Tingkat Risiko|Risk Level)\s*[:\-]?\s*([A-Za-z ]{3,30})", "Tingkat risiko"),
    ]
    for pat,label in patterns:
        m=re.search(pat,clean,re.I)
        if m:
            facts.append(f"{label}: {m.group(1).strip()}.")
    if not facts:
        facts.extend(_sentence_complete_excerpt(
            clean,
            keywords=("minimum pembelian","nab","1 tahun","return","imbal hasil","risiko","periode investasi"),
            max_sentences=5
        ))
    return facts[:6]


async def _fetch_seed_fund(seed):
    item = dict(seed)
    try:
        body = await get_text(seed["url"], 8)
        text = _clean_html_text(body)
        item["snippet"] = ""
        item["return_facts"] = _extract_fund_return_facts(text, 5)
        item["detail_facts"] = _extract_fund_detail_facts(text)
        item["official_hint"] = True
        item["category"] = "Reksadana • " + FUND_CATEGORY_MAP.get(seed["fund_category"], "Reksadana").title()
    except Exception:
        item["snippet"] = ""
        item["return_facts"] = []
        item["detail_facts"] = ["Data detail produk belum berhasil dimuat; gunakan tautan sumber resmi untuk verifikasi."]
        item["official_hint"] = True
        item["category"] = "Reksadana • " + FUND_CATEGORY_MAP.get(seed["fund_category"], "Reksadana").title()
    return item



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
        "https://banksaqu.co.id/products/tabungmatic-19",
        "https://banksaqu.co.id/support/219/apakah-saya-bisa-menambah-dana-ke-saku-booster",
        "https://banksaqu.co.id/products/busposito-16",
        "https://banksaqu.co.id/products/saku-booster-11",
        "https://banksaqu.co.id/support/270/what-is-the-interest-rate-on-saku-booster",
        "https://banksaqu.co.id/legal/riplay",
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
        "https://www.superbank.id/content/files/Product/CELENGAN-FAQ%20Articles%20v2.pdf",
        "https://www.superbank.id/content/files/Product/Celengan%20-%20RIPLAY.pdf",
        "https://www.superbank.id/content/produk-layanan/tabungan/celengan/Ringkasan%20Informasi%20Produk%20dan%20Layanan.pdf",
    ],
}

BANK_KNOWN_PRODUCTS = {
    "Bank Saqu": [
        {
            "product_name": "Saku Booster",
            "url": "https://banksaqu.co.id/products/saku-booster-11",
            "rate_facts": [
                {"rate": "10% p.a.", "rate_percent": 10.0, "short_context": "Bunga Saku Booster"}
            ],
            "important_facts": [
                "Saku Booster memberi bunga 10% per tahun.",
                "Saku Booster dapat menjadi tempat menabung otomatis melalui Tabungmatic: selisih pembulatan dari transaksi tertentu masuk otomatis ke Saku Booster.",
                "Saku Booster juga dapat menerima reward/cashback dari program Bank Saqu.",
                "Dana tidak ditambahkan lewat top up manual langsung seperti tabungan biasa; mekanisme masuk dana mengikuti fitur/program Bank Saqu seperti Tabungmatic dan reward/cashback.",
                "Tidak ada minimum setoran khusus seperti deposito; minimum setoran tidak berlaku.",
                "Dana dapat dipindahkan dari Saku Booster ke saku lain sesuai ketentuan yang berlaku."
            ],
            "minimum_deposit": 0,
            "minimum_deposit_label": "Tidak ada minimum setoran khusus",
            "funding_mechanism": "Tabungmatic + reward/cashback",
            "manual_topup": False,
            "simulation_eligible": True,
            "simulation_mode": "accumulated_balance",
            "simulation_note": "Simulasi menggunakan asumsi saldo sebesar dana simulasi telah terkumpul di Saku Booster melalui Tabungmatic/reward, bukan setoran langsung manual.",
            "official_hint": True,
            "source_type": "official-known-product",
        },
        {
            "product_name": "Busposito",
            "url": "https://banksaqu.co.id/products/busposito-16",
            "rate_facts": [],
            "rate_text": "Bunga dinamis sesuai jumlah peserta",
            "important_facts": [
                "Busposito adalah produk deposito Bank Saqu dengan bunga yang ditentukan oleh jumlah peserta dalam Busposito yang sama.",
                "Minimum penempatan dana Busposito adalah Rp100.000.",
                "Busposito hanya dapat diikuti ketika penawaran tersedia di aplikasi Bank Saqu dan masa tunggu belum berakhir atau kuota peserta belum penuh.",
                "Semakin banyak peserta, bunga dapat menjadi lebih tinggi; angka bunga aktif mengikuti penawaran Busposito yang sedang tersedia di aplikasi.",
                "Dana dapat ditarik sebelum jatuh tempo sesuai ketentuan dan biaya penarikan yang tercantum pada aplikasi."
            ],
            "minimum_deposit": 100000,
            "simulation_eligible": False,
            "dynamic_rate": True,
            "official_hint": True,
            "source_type": "official-known-product",
        },
        {
            "product_name": "Deposito Reguler",
            "url": "https://banksaqu.co.id/blog/update-suku-bunga-deposito-reguler-mulai-1-november-2025",
            "rate_facts": [
                {"rate":"4% p.a.","rate_percent":4.0,"short_context":"1–2 bulan, saldo < Rp500 juta","tenor":"1–2 bulan"},
                {"rate":"4,5% p.a.","rate_percent":4.5,"short_context":"3–5 bulan, saldo < Rp500 juta","tenor":"3–5 bulan"},
                {"rate":"5% p.a.","rate_percent":5.0,"short_context":"6–12 bulan, saldo < Rp500 juta","tenor":"6–12 bulan"},
                {"rate":"6% p.a.","rate_percent":6.0,"short_context":"6–12 bulan, saldo ≥ Rp500 juta","tenor":"6–12 bulan"},
                {"rate":"6,5% p.a.","rate_percent":6.5,"short_context":"6–12 bulan, saldo ≥ Rp2 miliar","tenor":"6–12 bulan"}
            ],
            "important_facts": [
                "Deposito Reguler Bank Saqu mulai dari Rp1.000.000.",
                "Untuk saldo di bawah Rp500 juta, bunga yang diumumkan Bank Saqu adalah 4,00% untuk tenor 1–2 bulan, 4,50% untuk 3–5 bulan, dan 5,00% untuk 6–12 bulan.",
                "Bunga dapat mencapai 6,50% per tahun pada tier saldo dan tenor tertentu.",
                "Promo lama yang pernah menyebut keuntungan hingga 10% bukan suku bunga Deposito Reguler saat ini; promo tersebut menggabungkan bunga deposito dan bonus dana serta memiliki periode program terbatas."
            ],
            "minimum_deposit": 1000000,
            "simulation_eligible": True,
            "official_hint": True,
            "source_type": "official-known-product",
        }
    ],
    "Krom Bank": [
        {
            "product_name": "Kantong Basic",
            "url": "https://krom.id/",
            "rate_facts": [
                {"rate":"6% p.a.","rate_percent":6.0,"short_context":"Kantong Tabungan Mode Basic"}
            ],
            "important_facts": [
                "Kantong Tabungan Mode Basic memberi bunga 6% per tahun.",
                "Saldo Kantong dapat mulai diisi dari Rp1."
            ],
            "minimum_deposit": 1,
            "simulation_eligible": True,
            "official_hint": True,
            "source_type": "official-known-product",
        },
        {
            "product_name": "Kantong Boost",
            "url": "https://krom.id/",
            "rate_facts": [
                {"rate":"6,25% p.a.","rate_percent":6.25,"short_context":"Kantong Tabungan Mode Boost, minimum periode simpanan 7 hari"}
            ],
            "important_facts": [
                "Kantong Tabungan Mode Boost memberi bunga 6,25% per tahun.",
                "Mode Boost memiliki periode simpanan minimum 7 hari.",
                "Saldo Kantong dapat mulai diisi dari Rp1."
            ],
            "minimum_deposit": 1,
            "simulation_eligible": True,
            "official_hint": True,
            "source_type": "official-known-product",
        },
        {
            "product_name": "Krom Flex",
            "url": "https://krom.id/pengumuman-penyesuaian-suku-bunga-deposito-krom-flex-dan-krom-max/",
            "rate_facts": [
                {"rate":"6,5% p.a.","rate_percent":6.5,"short_context":"Tenor 14 hari","tenor":"14 hari"},
                {"rate":"6,75% p.a.","rate_percent":6.75,"short_context":"Tenor 1 bulan","tenor":"1 bulan"},
                {"rate":"7% p.a.","rate_percent":7.0,"short_context":"Tenor 3 bulan","tenor":"3 bulan"},
                {"rate":"7% p.a.","rate_percent":7.0,"short_context":"Tenor 6 bulan","tenor":"6 bulan"},
                {"rate":"7,5% p.a.","rate_percent":7.5,"short_context":"Tenor 12 bulan","tenor":"12 bulan"}
            ],
            "important_facts": [
                "Krom Flex memberi bunga hingga 7,50% per tahun berdasarkan tenor.",
                "Krom Flex dapat dicairkan sebelum jatuh tempo tanpa penalti; bunga mengikuti ketentuan produk.",
                "Saldo awal minimum deposito Krom adalah Rp100.000."
            ],
            "minimum_deposit": 100000,
            "simulation_eligible": True,
            "official_hint": True,
            "source_type": "official-known-product",
        },
        {
            "product_name": "Krom Max",
            "url": "https://krom.id/pengumuman-penyesuaian-suku-bunga-deposito-krom-flex-dan-krom-max/",
            "rate_facts": [
                {"rate":"6,5% p.a.","rate_percent":6.5,"short_context":"Tenor 14 hari","tenor":"14 hari"},
                {"rate":"7,5% p.a.","rate_percent":7.5,"short_context":"Tenor 1 bulan","tenor":"1 bulan"},
                {"rate":"7,5% p.a.","rate_percent":7.5,"short_context":"Tenor 3 bulan","tenor":"3 bulan"},
                {"rate":"7,5% p.a.","rate_percent":7.5,"short_context":"Tenor 6 bulan","tenor":"6 bulan"},
                {"rate":"8% p.a.","rate_percent":8.0,"short_context":"Tenor 12 bulan","tenor":"12 bulan"}
            ],
            "important_facts": [
                "Krom Max memberi bunga hingga 8,00% per tahun; tenor 12 bulan menggunakan bunga 8,00% per tahun menurut pengumuman yang berlaku mulai 1 Mei 2026.",
                "Krom Max dapat dicairkan lebih awal tanpa penalti, tetapi bunga periode berjalan tidak dibayarkan sesuai ketentuan produk.",
                "Saldo awal minimum deposito Krom adalah Rp100.000."
            ],
            "minimum_deposit": 100000,
            "simulation_eligible": True,
            "official_hint": True,
            "source_type": "official-known-product",
        }
    ],
    "Superbank": [
        {
            "product_name": "Celengan by Superbank",
            "url": "https://www.superbank.id/content/files/Product/Celengan%20-%20RIPLAY.pdf",
            "rate_facts": [
                {"rate": "10% p.a.", "rate_percent": 10.0, "short_context": "Suku bunga Celengan"}
            ],
            "important_facts": [
                "Celengan by Superbank menawarkan bunga 10% per tahun.",
                "Saldo minimal dan setoran awal dapat Rp0 sesuai ringkasan produk.",
                "Tidak ada biaya administrasi bulanan."
            ],
            "minimum_deposit": 0,
            "simulation_eligible": True,
            "official_hint": True,
            "source_type": "official-known-product",
        }
    ],
}



def _important_bank_facts(text, max_facts=5):
    if not text:
        return []
    clean = re.sub(r"\s+", " ", text).strip()
    sentences = re.split(r"(?<=[.!?])\s+|\s+\|\s+", clean)
    keywords = (
        "bunga", "suku bunga", "saldo minimal", "setoran awal", "minimum",
        "biaya administrasi", "gratis", "dicairkan", "ditarik", "penarikan",
        "tenor", "pajak", "lps", "cashback", "reward"
    )
    facts = []
    seen = set()
    for s in sentences:
        ss = s.strip(" -:;|")
        low = ss.lower()
        if len(ss) < 12 or len(ss) > 240:
            continue
        if not any(k in low for k in keywords):
            continue
        # Hindari menu/navigation noise.
        if low.count("menu") >= 2 or "copyright" in low:
            continue
        key = low[:180]
        if key in seen:
            continue
        seen.add(key)
        facts.append(ss[:220])
        if len(facts) >= max_facts:
            break
    return facts


def _guess_bank_product_name(title, text, bank_name):
    joined = f"{title} {text[:400]}".lower()
    known = {
        "saku booster": "Saku Booster",
        "celengan": "Celengan by Superbank",
        "deposito reguler": "Deposito Reguler",
        "busposito": "Busposito",
        "krom flex": "Krom Flex",
        "krom max": "Krom Max",
    }
    for k, v in known.items():
        if k in joined:
            return v
    clean_title = re.sub(r"\s+", " ", title or "").strip()
    return clean_title[:90] if clean_title else bank_name



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
    item["rate_facts"] = item.get("rate_facts") or _extract_rate_facts(text)
    for fact in item["rate_facts"]:
        if "short_context" not in fact:
            ctx = re.sub(r"\\s+", " ", str(fact.get("context") or "")).strip()
            fact["short_context"] = ctx[:90]
    item["important_facts"] = item.get("important_facts") or _important_bank_facts(text)
    return item


async def fetch_official_bank_pages(bank_name):
    out = []

    # Known official product summaries guarantee clean high-value cards for products
    # whose official pages are noisy or PDF-heavy.
    for known in BANK_KNOWN_PRODUCTS.get(bank_name, []):
        item = dict(known)
        item["category"] = "Bank digital • " + bank_name
        item["title"] = item.get("product_name") or bank_name
        item["snippet"] = " ".join(item.get("important_facts") or [])
        out.append(_attach_rate_facts(item))

    async def fetch_one(url):
        try:
            body = await get_text(url, 8)
            # PDF/binary pages may not yield useful HTML text; known summaries above remain available.
            text = _clean_html_text(body)
            snippet = _extract_interest_snippet(text, 700)
            if not snippet:
                return None
            title_match = re.search(r"(?is)<title[^>]*>(.*?)</title>", body)
            title = _clean_html_text(title_match.group(1)) if title_match else f"{bank_name} official"
            product_name = _guess_bank_product_name(title, text, bank_name)
            return _attach_rate_facts({
                "category": "Bank digital • " + bank_name,
                "title": title[:180],
                "product_name": product_name,
                "snippet": snippet,
                "important_facts": _important_bank_facts(snippet),
                "url": url,
                "source_type": "official-direct",
                "official_hint": True,
            })
        except Exception:
            return None

    pages = await asyncio.gather(
        *(fetch_one(url) for url in BANK_OFFICIAL_PAGES.get(bank_name, [])),
        return_exceptions=True,
    )
    seen = {x.get("url") for x in out}
    for item in pages:
        if not isinstance(item, dict):
            continue
        if item.get("url") in seen:
            continue
        seen.add(item.get("url"))
        out.append(item)
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


def _rate_matches_selected_ranges(rate_percent, selected_ranges):
    if rate_percent is None or not selected_ranges:
        return True
    try:
        p = float(rate_percent)
    except Exception:
        return False
    for r in selected_ranges:
        m = re.match(r"\s*([0-9.]+)\s*-\s*([0-9.]+)\s*", str(r))
        if not m:
            continue
        lo, hi = float(m.group(1)), float(m.group(2))
        if lo <= p <= hi:
            return True
    return False


async def _bank_search_one(name, selected_ranges):
    domains = BANK_DOMAINS.get(name, [])
    bank_items = []
    seen = set()

    try:
        official = await asyncio.wait_for(fetch_official_bank_pages(name), timeout=8)
    except Exception:
        official = []

    structured_bank = name in BANK_KNOWN_PRODUCTS and bool(BANK_KNOWN_PRODUCTS.get(name))
    for item in official:
        # For banks with curated current products, ignore generic page parsing so a stray
        # percentage from comparison tables can never become the product rate.
        if structured_bank and item.get("source_type") != "official-known-product":
            continue
        key = item.get("url") or item.get("title")
        if key in seen:
            continue
        seen.add(key)
        bank_items.append(item)

    has_rate = any(item.get("rate_facts") for item in bank_items)
    if not has_rate:
        range_hint = " ".join(selected_ranges[:2])
        q = f'"{name}" bunga tabungan deposito terbaru {range_hint}'.strip()
        try:
            items = await asyncio.wait_for(
                multi_search(q, "Bank digital • " + name, domains=domains, max_results=3),
                timeout=8
            )
        except Exception:
            items = []
        for item in items:
            key = item.get("url") or item.get("title")
            if key in seen:
                continue
            seen.add(key)
            item.setdefault("source_type", "web-search")
            item["product_name"] = _guess_bank_product_name(item.get("title"), item.get("snippet"), name)
            item["important_facts"] = _important_bank_facts(item.get("snippet") or "")
            _attach_rate_facts(item)
            bank_items.append(item)

    cleaned = []
    seen_products = set()
    for item in bank_items:
        facts = [
            f for f in (item.get("rate_facts") or [])
            if _rate_matches_selected_ranges(f.get("rate_percent"), selected_ranges)
        ]
        # Produk dengan bunga dinamis (contoh Busposito) tetap tampil sebagai fakta produk,
        # tetapi tidak ikut simulasi sampai ada angka bunga aktif yang terverifikasi.
        if not facts and not item.get("dynamic_rate"):
            continue
        item["rate_facts"] = facts
        key = (item.get("product_name") or item.get("title") or "").strip().lower()
        if key in seen_products:
            continue
        seen_products.add(key)
        cleaned.append(item)

    cleaned.sort(key=lambda x:(
        0 if x.get("source_type")=="official-known-product" else 1,
        0 if x.get("official_hint") else 1,
    ))
    return cleaned[:8]

async def bank_search(names_text, rate_ranges_text=''):
    names=[x.strip() for x in names_text.split(',') if x.strip()][:10]
    selected_ranges=[x.strip() for x in rate_ranges_text.split(',') if x.strip()]
    chunks=await asyncio.gather(*(_bank_search_one(name,selected_ranges) for name in names),return_exceptions=True)
    result=[]
    for chunk in chunks:
        if isinstance(chunk,list): result.extend(chunk)
    return {"fetched_at":nowiso(),"search_mode":"fast-official-first+single-web-fallback","banks_requested":names,"selected_interest_ranges":selected_ranges,"items":result}


FUND_CATEGORY_MAP = {
    "money_market": "pasar uang",
    "fixed_income": "pendapatan tetap",
    "mixed": "campuran",
    "equity": "saham",
}

# Ini hanya alias untuk mengenali nama MI pada hasil pencarian, BUKAN pembatas pencarian.
# Discovery tetap memakai query internet umum sehingga MI lain tetap dapat muncul.
INVESTMENT_MANAGER_ALIASES = {
    "Syailendra Capital": ["syailendra"],
    "BNI Asset Management": ["bni asset management", "bni am"],
    "Mandiri Manajemen Investasi": ["mandiri manajemen investasi", "mandiri investasi"],
    "BRI Manajemen Investasi": ["bri manajemen investasi", "bri mi"],
    "Manulife Aset Manajemen Indonesia": ["manulife aset manajemen", "mami"],
    "Schroders Indonesia": ["schroder", "schroders"],
    "Sucor Asset Management": ["sucor asset management", "sucorinvest"],
    "Batavia Prosperindo Aset Manajemen": ["batavia prosperindo", "bpam"],
    "Eastspring Investments Indonesia": ["eastspring"],
    "Principal Asset Management": ["principal asset management"],
    "Bahana TCW Investment Management": ["bahana tcw"],
    "Trimegah Asset Management": ["trimegah asset management", "trimegah am"],
    "BNP Paribas Asset Management": ["bnp paribas asset management"],
    "Ashmore Asset Management Indonesia": ["ashmore"],
    "Danareksa Investment Management": ["danareksa investment management"],
    "Avrist Asset Management": ["avrist asset management"],
    "Majoris Asset Management": ["majoris asset management"],
    "Insight Investments Management": ["insight investments management", "insight investment"],
    "Samuel Aset Manajemen": ["samuel aset manajemen"],
    "Shinhan Asset Management Indonesia": ["shinhan asset management"],
    "Henan Putihrai Asset Management": ["henan putihrai asset management", "hpam"],
    "Maybank Asset Management": ["maybank asset management"],
    "KISI Asset Management": ["kisi asset management"],
    "Star Asset Management": ["star asset management"],
    "Ciptadana Asset Management": ["ciptadana asset management"],
    "Panin Asset Management": ["panin asset management"],
}


def _infer_fund_manager(text):
    t = (text or "").lower()
    for manager, aliases in INVESTMENT_MANAGER_ALIASES.items():
        if any(alias in t for alias in aliases):
            return manager
    return None


def _extract_fund_return_facts(text, max_facts=4):
    if not text:
        return []
    clean = re.sub(r"\s+", " ", text)
    patterns = [
        (r"(1\s*(?:tahun|thn|year))\s*[:|]?\s*([+-]?\d{1,3}(?:[.,]\d+)?)\s*%", "1 Tahun"),
        (r"(ytd)\s*[:|]?\s*([+-]?\d{1,3}(?:[.,]\d+)?)\s*%", "YTD"),
        (r"(6\s*(?:bulan|bln|month))\s*[:|]?\s*([+-]?\d{1,3}(?:[.,]\d+)?)\s*%", "6 Bulan"),
        (r"(3\s*(?:bulan|bln|month))\s*[:|]?\s*([+-]?\d{1,3}(?:[.,]\d+)?)\s*%", "3 Bulan"),
    ]
    facts = []
    seen = set()
    for pattern, label in patterns:
        for m in re.finditer(pattern, clean, re.I):
            raw = m.group(2).replace(",", ".")
            try:
                val = float(raw)
            except Exception:
                continue
            key = (label, round(val, 4))
            if key in seen:
                continue
            seen.add(key)
            facts.append({"period": label, "value": f"{val:+g}%", "percent": val})
            if len(facts) >= max_facts:
                return facts
    return facts


def _enrich_fund_item(item, category_key):
    text = " ".join([
        str(item.get("title") or ""),
        str(item.get("snippet") or ""),
        str(item.get("url") or ""),
    ])
    item["fund_category"] = category_key
    item["manager"] = _infer_fund_manager(text)
    item["product_name"] = (item.get("title") or "").split(" | ")[0].strip()[:180]
    item["return_facts"] = _extract_fund_return_facts(text)
    item["detail_facts"] = _extract_fund_detail_facts((item.get("title") or "")+" "+(item.get("snippet") or ""))
    return item


async def fund_search(categories_text=""):
    selected = [x.strip() for x in (categories_text or "").split(",") if x.strip()]
    selected = [x for x in selected if x in FUND_CATEGORY_MAP]
    if not selected:
        selected = list(FUND_CATEGORY_MAP.keys())

    trusted_sites = [
        "makmur.id",
        "ajaib.co.id",
        "bareksa.com",
        "bibit.id",
        "bni-am.co.id",
        "syailendracapital.com",
        "mandiri-investasi.co.id",
    ]

    async def search_query(category_key, query, max_results=5):
        try:
            items = await asyncio.wait_for(
                search_any(query, "Reksadana • " + FUND_CATEGORY_MAP[category_key].title(), max_results),
                timeout=10,
            )
        except Exception:
            return []
        cleaned=[]
        for item in items:
            if not _fund_result_relevant(item):
                continue
            cleaned.append(_enrich_fund_item(item, category_key))
        return cleaned

    jobs = []
    for key in selected:
        label = FUND_CATEGORY_MAP[key]
        # Search Google Indonesia per platform tepercaya agar tidak nyasar ke Gmail, film, apartemen, dll.
        for domain in trusted_sites:
            jobs.append((key, f'site:{domain} reksadana "{label}" Indonesia NAB return 1 tahun'))
        jobs.append((key, f'reksadana "{label}" Indonesia manajer investasi NAB AUM'))

    search_chunks, seed_chunks = await asyncio.gather(
        asyncio.gather(*(search_query(key, q, 5) for key, q in jobs), return_exceptions=True),
        asyncio.gather(*(
            _fetch_seed_fund(seed)
            for seed in FUND_SEED_PRODUCTS
            if seed["fund_category"] in selected
        ), return_exceptions=True),
    )

    out, seen = [], set()
    per_category_count = {key: 0 for key in selected}

    for item in seed_chunks:
        if not isinstance(item, dict):
            continue
        key = item.get("url") or item.get("product_name")
        if not key or key in seen:
            continue
        seen.add(key)
        cat = item.get("fund_category")
        per_category_count[cat] = per_category_count.get(cat, 0) + 1
        out.append(item)

    for chunk in search_chunks:
        if not isinstance(chunk, list):
            continue
        for item in chunk:
            if not _fund_result_relevant(item):
                continue
            key = item.get("url") or item.get("title")
            if not key or key in seen:
                continue
            cat = item.get("fund_category")
            if per_category_count.get(cat, 0) >= 8:
                continue
            seen.add(key)
            per_category_count[cat] = per_category_count.get(cat, 0) + 1
            out.append(item)

    out.sort(key=lambda x:(
        0 if x.get("official_hint") else 1,
        0 if x.get("manager") else 1,
        0 if x.get("return_facts") else 1,
        selected.index(x.get("fund_category")) if x.get("fund_category") in selected else 99,
    ))
    managers=sorted({x.get("manager") for x in out if x.get("manager")})
    return {
        "fetched_at":nowiso(),
        "search_mode":"google-id+trusted-indonesia-investment-sources",
        "coverage_mode":"INDONESIA_ONLY_TRUSTED_SOURCES",
        "selected_categories":selected,
        "managers_detected":managers,
        "manager_count_detected":len(managers),
        "items":out[:24],
        "note":"Hanya sumber investasi Indonesia/MI tepercaya. Hasil non-investasi otomatis dibuang.",
    }



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
        f'site:idx.co.id "{symbol}" dividen {year}',
        f'site:ajaib.co.id "{symbol}" dividen {year}',
        f'site:bareksa.com "{symbol}" dividen {year}',
        f'"{symbol}" dividen tunai {year} per saham Indonesia',
    ]
    seen=set(); results=[]
    for q in queries:
        items=await search_any(q, f'Dividen • {symbol}', 4)
        for item in items:
            if not _allowed_indonesia_investment_url(item.get("url") or ""):
                continue
            key=item.get('url') or item.get('title')
            if key in seen: continue
            seen.add(key); results.append(item)
        if results: break
    snippet=' '.join([(x.get('title','')+' '+x.get('snippet','')).strip() for x in results[:3]])
    dps=_parse_dividend_per_share(snippet)
    dy=(dps/float(price)*100.0) if dps is not None and price else None
    return {
        'dividend_per_share':dps,
        'dividend_yield_estimate':dy,
        'dividend_title':results[0].get('title') if results else None,
        'dividend_snippet':results[0].get('snippet') if results else None,
        'dividend_source_url':results[0].get('url') if results else None,
        'dividend_sources':results[:3],
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

async def combined_research(names_text, symbols_text, rate_ranges_text='', stock_lot_mode='manual', stock_lot_budget=0, stock_recommendation_count=5, fund_categories_text=''):
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
        bounded(fund_search(fund_categories_text),16,{"fetched_at":nowiso(),"items":[],"search_mode":"timeout-fallback","coverage_mode":"ALL_MI_DISCOVERY_NOT_LIMITED_TO_FIXED_LIST"}),
        bounded(get_stocks(),22,{"fetched_at":nowiso(),"items":[],"candidates":[],"mode":stock_lot_mode}),
    )
    return {"fetched_at":nowiso(),"research_mode":"parallel-bounded-v10.29-id-only","stocks":stocks,"banks":banks,"funds":funds}

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
                fund_categories = query.get(
                    "categories",
                    ["money_market,fixed_income,mixed,equity"],
                )[0]
                return await send_json(send, await fund_search(fund_categories))

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
                fund_categories = query.get(
                    "fund_categories",
                    ["money_market,fixed_income,mixed,equity"],
                )[0]
                return await send_json(
                    send,
                    await combined_research(
                        names,
                        symbols,
                        ranges,
                        stock_lot_mode,
                        stock_lot_budget,
                        stock_recommendation_count,
                        fund_categories,
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
