"""
Trend Research (Prototype v0.2)
Finds product opportunities, scores them, and publishes simple report files.

Modes:
  demo  - no API keys needed, uses built-in sample data
  live  - uses SerpApi Google Shopping for real price/seller/rating data

Usage:
  python trendscout.py
  python trendscout.py live

Output:
  results.json + report.html + report.pdf + top5_report.txt + jamify_results.xlsx
"""
import html
import json
import os
import sys
import urllib.parse
import urllib.request
from datetime import date

HERE = os.path.dirname(os.path.abspath(__file__))
CACHE_FILE = os.path.join(HERE, "serpapi_cache.json")
DEFAULT_BUYER_CHECK = "Check current reviews, shipping cost, return risk, and seller pricing before buying inventory."

DEFAULT_MARKETS = [
    "Florida",
    "Volusia County Florida",
    "Edgewater Florida",
    "New Smyrna Beach Florida",
    "Oak Hill Florida",
    "Titusville Florida",
]

SEARCH_PRESETS = {
    "top_trends": {
        "label": "Top Trends",
        "description": "Seasonal and rising coastal Florida product ideas.",
        "seed_keywords": [
            "portable shade canopy for beach",
            "cooling towels for heat",
            "mosquito repellent patio fan",
            "hurricane prep water storage bag",
            "waterproof phone pouch floating",
            "beach wagon balloon wheels",
            "solar dock lights",
            "portable power station for hurricane",
        ],
        "details": "trending seasonal useful for heat storms beach boating mosquitoes",
    },
    "most_potential": {
        "label": "Most Potential",
        "description": "Products that may be easier to ship, explain, and test without huge inventory risk.",
        "seed_keywords": [
            "boat seat organizer waterproof",
            "solar dock lights",
            "beach cart replacement wheels",
            "rv cabinet storage organizer",
            "patio umbrella sand anchor",
            "portable fan rechargeable clip on",
            "waterproof dry bag phone wallet",
            "hurricane prep water storage bag",
        ],
        "details": "easy to ship lightweight good margin useful problem solving",
    },
}

DEFAULT_SEARCH_MODE = "top_trends"
DEFAULT_SEED_KEYWORDS = SEARCH_PRESETS[DEFAULT_SEARCH_MODE]["seed_keywords"]

DEMO = [
    {"keyword": "beach wagon balloon wheels", "sellers": 13, "price_low": 38, "price_high": 145, "avg_rating": 4.2, "trend_growth": 78, "pain_points": "regular wheels sink in sand, carts rust"},
    {"keyword": "mosquito repellent patio fan", "sellers": 10, "price_low": 24, "price_high": 95, "avg_rating": 4.1, "trend_growth": 74, "pain_points": "weak airflow, refill costs"},
    {"keyword": "portable shade canopy for beach", "sellers": 28, "price_low": 36, "price_high": 180, "avg_rating": 4.0, "trend_growth": 82, "pain_points": "hard to anchor, breaks in wind"},
    {"keyword": "waterproof phone pouch floating", "sellers": 31, "price_low": 8, "price_high": 32, "avg_rating": 4.3, "trend_growth": 68, "pain_points": "leaks, touchscreen hard to use"},
    {"keyword": "solar dock lights", "sellers": 16, "price_low": 19, "price_high": 88, "avg_rating": 4.2, "trend_growth": 64, "pain_points": "not bright enough, water damage"},
    {"keyword": "cooling towels for heat", "sellers": 35, "price_low": 7, "price_high": 28, "avg_rating": 4.4, "trend_growth": 72, "pain_points": "dries out too fast, chemical smell"},
    {"keyword": "hurricane prep water storage bag", "sellers": 8, "price_low": 18, "price_high": 70, "avg_rating": 4.1, "trend_growth": 58, "pain_points": "hard to fill, plastic taste"},
    {"keyword": "boat seat organizer waterproof", "sellers": 12, "price_low": 22, "price_high": 74, "avg_rating": 4.3, "trend_growth": 61, "pain_points": "straps fail, pockets too small"},
]


def load_config():
    p = os.path.join(HERE, "config.json")
    if os.path.exists(p):
        with open(p, encoding="utf-8") as f:
            return json.load(f)
    return {}


def clean(value):
    return " ".join(str(value).strip().split())


def unique(values):
    seen = set()
    out = []
    for value in values:
        value = clean(value)
        key = value.lower()
        if value and key not in seen:
            seen.add(key)
            out.append(value)
    return out


def buyer_check(value):
    value = clean(value)
    if not value:
        return DEFAULT_BUYER_CHECK
    return value


def load_cache():
    if not os.path.exists(CACHE_FILE):
        return {}
    try:
        with open(CACHE_FILE, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def save_cache(cache):
    with open(CACHE_FILE, "w", encoding="utf-8") as f:
        json.dump(cache, f, indent=2)


def active_preset(cfg):
    mode = clean(cfg.get("search_mode", DEFAULT_SEARCH_MODE)).lower()
    return mode if mode in SEARCH_PRESETS else DEFAULT_SEARCH_MODE


def build_search_keywords(cfg):
    preset = SEARCH_PRESETS[active_preset(cfg)]
    seeds = cfg.get("seed_keywords") or preset["seed_keywords"]
    markets = cfg.get("market_locations") or DEFAULT_MARKETS
    customer_type = clean(cfg.get("customer_type", "coastal Florida shoppers"))
    details = clean(cfg.get("details", preset["details"]))
    avoid = clean(cfg.get("avoid", "large fragile expensive returns"))
    max_searches = int(cfg.get("max_searches", 18))

    terms = []
    for base in seeds:
        base = clean(base)
        if not base:
            continue
        terms.append(base)
        if customer_type:
            terms.append(f"{base} for {customer_type}")
        if details:
            terms.append(f"{base} {details}")
        for market in markets[:3]:
            terms.append(f"{base} {market}")
        if avoid:
            terms.append(f"{base} not {avoid}")

    return unique(terms)[:max_searches]


def serpapi_shopping(query, key, location, cache, cache_days=7):
    params = {
        "engine": "google_shopping",
        "q": query,
        "api_key": key,
        "num": 20,
        "gl": "us",
        "hl": "en",
        "device": "mobile",
    }
    if location:
        params["location"] = location

    cache_key = json.dumps({k: params[k] for k in sorted(params) if k != "api_key"}, sort_keys=True)
    cached = cache.get(cache_key)
    if cached:
        try:
            cached_on = date.fromisoformat(cached.get("cached_on", ""))
            if (date.today() - cached_on).days <= cache_days:
                data = cached["data"]
                cache_hit = True
            else:
                data = None
                cache_hit = False
        except Exception:
            data = None
            cache_hit = False
    else:
        data = None
        cache_hit = False

    if data is None:
        url = "https://serpapi.com/search.json?" + urllib.parse.urlencode(params)
        with urllib.request.urlopen(url, timeout=45) as r:
            data = json.load(r)
        if data.get("error"):
            raise RuntimeError(data["error"])
        cache[cache_key] = {"cached_on": str(date.today()), "data": data}
        cache_hit = False

    items = data.get("shopping_results", []) + data.get("inline_shopping_results", [])
    prices = [it.get("extracted_price") for it in items if isinstance(it.get("extracted_price"), (int, float))]
    ratings = [it.get("rating") for it in items if isinstance(it.get("rating"), (int, float))]
    titles = [clean(it.get("title", "")) for it in items if clean(it.get("title", ""))]
    sources = [clean(it.get("source", "")) for it in items if clean(it.get("source", ""))]

    return {
        "keyword": query,
        "sellers": len(items),
        "price_low": min(prices) if prices else 0,
        "price_high": max(prices) if prices else 0,
        "avg_rating": round(sum(ratings) / len(ratings), 2) if ratings else 0,
        "trend_growth": 50,
        "pain_points": DEFAULT_BUYER_CHECK,
        "source_count": len(set(sources)),
        "sample_titles": titles[:3],
        "sample_sources": unique(sources)[:4],
        "data_source": "SerpApi Google Shopping (cached)" if cache_hit else "SerpApi Google Shopping",
    }


def score(p):
    demand = p.get("trend_growth", 50)
    comp = max(0, 100 - p["sellers"] * 2.5)
    spread = min(100, (p["price_high"] - p["price_low"]) * 0.8)
    rating = p.get("avg_rating") or 4.2
    gap = max(0, (4.6 - rating) * 100)
    total = round(demand * 0.35 + comp * 0.30 + spread * 0.20 + gap * 0.15)
    return min(100, total)


def slogans_template(kw):
    n = kw.title()
    return [
        f"The {n} Everyone Will Want Next Season.",
        f"Stop Settling. Upgrade to the {n} Done Right.",
        f"{n}: Solves What the Cheap Ones Can't.",
        f"Built for Real Life - {n} That Lasts.",
        f"Get Yours Before Everyone Else Does.",
    ]


def slogans_claude(p, key):
    body = json.dumps({
        "model": "claude-sonnet-4-20250514",
        "max_tokens": 300,
        "messages": [{"role": "user", "content":
            f"Write 5 short punchy sales slogans for: {p['keyword']}. "
            f"Customer checks to consider: {buyer_check(p.get('pain_points', ''))}. "
            "Return ONLY a JSON array of 5 strings, nothing else."}]
    }).encode()
    req = urllib.request.Request(
        "https://api.anthropic.com/v1/messages",
        data=body,
        headers={
            "Content-Type": "application/json",
            "x-api-key": key,
            "anthropic-version": "2023-06-01",
        },
    )
    with urllib.request.urlopen(req, timeout=60) as r:
        out = json.load(r)
    txt = out["content"][0]["text"].strip().strip("`").replace("json", "", 1)
    try:
        return json.loads(txt)[:5]
    except Exception:
        return slogans_template(p["keyword"])


def row_summary(r):
    return {
        "score": r["score"],
        "keyword": r["keyword"],
        "sellers": r["sellers"],
        "price_low": r["price_low"],
        "price_high": r["price_high"],
        "avg_rating": r["avg_rating"],
        "trend_growth": r.get("trend_growth", ""),
        "pain_points": buyer_check(r.get("pain_points", "")),
        "slogans": r["slogans"],
        "sample_titles": r.get("sample_titles", []),
        "sample_sources": r.get("sample_sources", []),
        "data_source": r.get("data_source", "Demo sample"),
    }


def write_results_json(rows, meta):
    p = os.path.join(HERE, "results.json")
    data = {"meta": meta, "rows": [row_summary(r) for r in rows]}
    with open(p, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2)
    return p


def write_xlsx(rows):
    from openpyxl import Workbook
    wb = Workbook()
    ws = wb.active
    ws.title = "Trend Research"
    hdr = ["Score", "Product", "Sellers", "Price Low", "Price High", "Avg Rating",
           "Trend Growth", "Check Before Buying", "Source", "Slogan 1", "Slogan 2", "Slogan 3"]
    ws.append(hdr)
    for r in rows:
        ws.append([r["score"], r["keyword"], r["sellers"], r["price_low"], r["price_high"],
                   r["avg_rating"], r.get("trend_growth", ""), buyer_check(r.get("pain_points", "")),
                   r.get("data_source", ""), *r["slogans"][:3]])
    for i, w in enumerate([7, 34, 8, 10, 10, 10, 12, 24, 20, 38, 38, 38], 1):
        ws.column_dimensions[chr(64 + i)].width = w
    p = os.path.join(HERE, "jamify_results.xlsx")
    wb.save(p)
    return p


def write_text_report(rows, meta):
    top = rows[:5]
    lines = [
        f"TREND RESEARCH - TOP OPPORTUNITIES ({meta.get('generated_on', date.today())})",
        "=" * 50,
        f"Market: {', '.join(meta.get('markets', []))}",
        f"Data status: {meta.get('data_status')}",
        "",
    ]
    for r in top:
        lines += [
            f"[{r['score']}/100] {r['keyword'].upper()}",
            f"  Sellers: {r['sellers']} | Price: ${r['price_low']}-${r['price_high']} | Rating: {r['avg_rating'] or 'n/a'}",
            f"  Source: {r.get('data_source', 'n/a')}",
            f"  Check before buying: {buyer_check(r.get('pain_points', ''))}",
            "  Slogans:",
            *[f"    - {s}" for s in r["slogans"]],
            "",
        ]
    p = os.path.join(HERE, "top5_report.txt")
    with open(p, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))
    return p


def write_report_html(rows, meta):
    top = rows[:5]
    body_rows = []
    for r in top:
        sources = ", ".join(html.escape(s) for s in r.get("sample_sources", [])) or "n/a"
        body_rows.append(f"""
        <tr>
          <td class="score">{r['score']}</td>
          <td><strong>{html.escape(r['keyword'])}</strong><span>{html.escape(r['slogans'][0])}</span></td>
          <td>{r['sellers']}</td>
          <td>${r['price_low']}-${r['price_high']}</td>
          <td>{r['avg_rating'] or 'n/a'}</td>
          <td>{html.escape(buyer_check(r.get('pain_points', '')))}</td>
          <td>{sources}</td>
        </tr>""")

    p = os.path.join(HERE, "report.html")
    with open(p, "w", encoding="utf-8") as f:
        f.write(f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Trend Research Report</title>
<style>
body{{margin:0;background:#e9edf2;color:#1d2633;font-family:Arial,Helvetica,sans-serif;font-size:13px}}
.page{{max-width:980px;margin:18px auto;padding:18px;background:#fff;border:1px solid #d3d9e1;border-radius:8px}}
.topbar{{display:flex;justify-content:space-between;gap:16px;align-items:flex-start;margin-bottom:14px}}
h1{{margin:0;font-size:22px;line-height:1.2}} .meta{{margin-top:5px;color:#657285;font-size:12px;line-height:1.4}}
.actions{{display:flex;gap:8px}} .btn{{display:inline-flex;min-height:34px;align-items:center;justify-content:center;padding:7px 12px;border:1px solid #172033;border-radius:6px;background:#fff;color:#172033;font-weight:700;text-decoration:none;white-space:nowrap}} .primary{{background:#172033;color:#fff}}
table{{width:100%;border-collapse:collapse;table-layout:fixed}} th,td{{border:1px solid #9aa5b1;padding:8px;text-align:left;vertical-align:top}} th{{background:#f2f4f7;font-size:11px;text-transform:uppercase}} .score{{color:#167449;font-weight:700}} td span{{display:block;margin-top:4px;color:#364456;font-size:12px;line-height:1.35}}
.note{{margin:12px 0;padding:10px;border:1px solid #d3d9e1;border-radius:6px;background:#f8fafc;color:#364456}}
@media print{{@page{{size:letter portrait;margin:.3in}}body{{background:#fff;font-size:10px}}.page{{max-width:none;margin:0;padding:0;border:0}}.actions{{display:none}}th,td{{padding:5px}}}}
@media(max-width:760px){{.page{{margin:0;border-left:0;border-right:0;border-radius:0}}.topbar,.actions{{flex-direction:column}}.table-wrap{{overflow-x:auto}}table{{min-width:900px}}}}
</style>
</head>
<body>
<main class="page">
  <div class="topbar">
    <div>
      <h1>Trend Research Report</h1>
      <div class="meta">Generated: {html.escape(meta['generated_on'])}<br>Market: {html.escape(', '.join(meta.get('markets', [])))}<br>Data status: {html.escape(meta.get('data_status', ''))}</div>
    </div>
    <div class="actions"><a class="btn primary" href="report.pdf">Download PDF</a><button class="btn" onclick="window.print()">Print</button><a class="btn" href="index.html">Dashboard</a></div>
  </div>
  <div class="note">{html.escape(meta.get('data_note', ''))}</div>
  <div class="table-wrap">
    <table>
      <thead><tr><th style="width:56px">Score</th><th style="width:220px">Product / Hook</th><th style="width:70px">Sellers</th><th style="width:90px">Price</th><th style="width:70px">Rating</th><th style="width:170px">Check Before Buying</th><th>Sample Sources</th></tr></thead>
      <tbody>{''.join(body_rows)}</tbody>
    </table>
  </div>
</main>
</body>
</html>""")
    return p


def write_report_pdf(rows, meta):
    try:
        from reportlab.lib import colors
        from reportlab.lib.pagesizes import letter
        from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
        from reportlab.lib.units import inch
        from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle
    except Exception:
        return None

    out = os.path.join(HERE, "report.pdf")
    margin = 0.3 * inch
    usable_width = letter[0] - (2 * margin)
    styles = getSampleStyleSheet()
    styles.add(ParagraphStyle(name="Small", parent=styles["BodyText"], fontName="Helvetica", fontSize=8, leading=9.8))
    styles.add(ParagraphStyle(name="SmallBold", parent=styles["Small"], fontName="Helvetica-Bold"))
    story = [
        Paragraph("Trend Research Report", ParagraphStyle(name="TitleCompact", parent=styles["Title"], fontSize=16, leading=18, alignment=0)),
        Paragraph(f"Generated: {meta['generated_on']} | Market: {', '.join(meta.get('markets', []))} | {meta.get('data_status', '')}", styles["Small"]),
        Spacer(1, 0.12 * inch),
    ]

    table_data = [[Paragraph(h, styles["SmallBold"]) for h in ["Score", "Product / Hook", "Sellers", "Price", "Rating", "Check Before Buying"]]]
    for r in rows[:5]:
        table_data.append([
            Paragraph(str(r["score"]), styles["SmallBold"]),
            Paragraph(f"<b>{html.escape(r['keyword'])}</b><br/>{html.escape(r['slogans'][0])}", styles["Small"]),
            Paragraph(str(r["sellers"]), styles["Small"]),
            Paragraph(f"${r['price_low']}-${r['price_high']}", styles["Small"]),
            Paragraph(str(r["avg_rating"] or "n/a"), styles["Small"]),
            Paragraph(html.escape(buyer_check(r.get("pain_points", ""))), styles["Small"]),
        ])
    table = Table(table_data, colWidths=[0.48*inch, 2.15*inch, 0.55*inch, 0.78*inch, 0.58*inch, usable_width - 4.54*inch], repeatRows=1)
    table.setStyle(TableStyle([
        ("GRID", (0, 0), (-1, -1), 0.55, colors.HexColor("#9AA5B1")),
        ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#F2F4F7")),
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("LEFTPADDING", (0, 0), (-1, -1), 5),
        ("RIGHTPADDING", (0, 0), (-1, -1), 5),
        ("TOPPADDING", (0, 0), (-1, -1), 5),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
    ]))
    story.append(table)
    doc = SimpleDocTemplate(out, pagesize=letter, rightMargin=margin, leftMargin=margin, topMargin=margin, bottomMargin=margin)
    doc.build(story)
    return out


def write_outputs(rows, meta):
    return {
        "json": write_results_json(rows, meta),
        "xlsx": write_xlsx(rows),
        "txt": write_text_report(rows, meta),
        "html": write_report_html(rows, meta),
        "pdf": write_report_pdf(rows, meta),
    }


def main():
    args = sys.argv[1:]
    mode = args[0] if args and args[0] in ("demo", "live") else "demo"
    cfg = load_config()
    preset_args = args[1:] if mode in ("demo", "live") else args
    for arg in preset_args:
        key = clean(arg).lower()
        if key in SEARCH_PRESETS:
            cfg["search_mode"] = key
    if os.environ.get("SERPAPI_KEY"):
        cfg["serpapi_key"] = os.environ["SERPAPI_KEY"]
    if os.environ.get("ANTHROPIC_KEY"):
        cfg["anthropic_key"] = os.environ["ANTHROPIC_KEY"]
    if os.environ.get("MAX_SEARCHES"):
        cfg["max_searches"] = int(os.environ["MAX_SEARCHES"])
    if os.environ.get("SEARCH_MODE"):
        cfg["search_mode"] = os.environ["SEARCH_MODE"]
    preset_key = active_preset(cfg)
    preset = SEARCH_PRESETS[preset_key]
    markets = cfg.get("market_locations") or DEFAULT_MARKETS
    search_location = cfg.get("search_location", "Edgewater, Florida, United States")

    if mode == "live":
        serp = cfg.get("serpapi_key", "")
        if not serp:
            sys.exit("live mode needs serpapi_key in config.json")
        kws = build_search_keywords(cfg)
        products = []
        cache = load_cache()
        cache_days = int(cfg.get("cache_days", 7))
        for kw in kws:
            try:
                p = serpapi_shopping(kw, serp, search_location, cache, cache_days)
                products.append(p)
                print(f"  fetched: {kw} ({p['sellers']} shopping results)")
            except Exception as e:
                print(f"  skip {kw}: {e}")
        save_cache(cache)
        data_status = "Live Google Shopping data"
        data_note = f"{preset['label']} search. Prices, sellers, and ratings came from Google Shopping. Before buying inventory, compare reviews, shipping cost, and current seller listings."
    else:
        products = [dict(d, data_source="Demo sample") for d in DEMO]
        kws = [p["keyword"] for p in products]
        data_status = "Sample report only"
        data_note = f"{preset['label']} search. This is sample data only. Run live mode with a SerpApi key to use real shopping data."
        print("DEMO MODE - sample data only")

    for p in products:
        p["score"] = score(p)
        if mode == "live" and cfg.get("anthropic_key"):
            p["slogans"] = slogans_claude(p, cfg["anthropic_key"])
        else:
            p["slogans"] = slogans_template(p["keyword"])

    products.sort(key=lambda x: -x["score"])
    meta = {
        "generated_on": str(date.today()),
        "mode": mode,
        "search_mode": preset_key,
        "search_label": preset["label"],
        "search_description": preset["description"],
        "data_status": data_status,
        "data_note": data_note,
        "markets": markets,
        "search_location": search_location,
        "searched_keywords": kws,
    }
    outputs = write_outputs(products, meta)
    print(f"\nDone. {len(products)} products scored.")
    for label, path in outputs.items():
        if path:
            print(f"  {label}: {path}")
    print("\nTop 3:")
    for p in products[:3]:
        print(f"  [{p['score']}] {p['keyword']}")


if __name__ == "__main__":
    main()
