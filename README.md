TREND RESEARCH
==============
Finds possible online-retail product ideas, scores opportunity 1-100,
and publishes a simple dashboard/report.

DEFAULT MARKET
Florida, Volusia County, Edgewater, New Smyrna Beach, Oak Hill, Titusville.

SETUP
pip install openpyxl reportlab

RUN SAMPLE DATA
python trendscout.py

RUN LIVE SHOPPING DATA
1. Copy config.example.json to config.json
2. Paste a SerpApi key into config.json
3. Run:
   python trendscout.py live

PRESET RUNS
python trendscout.py live top_trends
python trendscout.py live most_potential

The dashboard also has a Custom Keywords option for manual ideas.

SAFER TEST RUN
PowerShell:
$env:SERPAPI_KEY="your_key_here"
$env:MAX_SEARCHES="2"
python trendscout.py live

SEARCH USE CONTROL
- max_searches limits how many SerpApi calls a live run can make.
- cache_days keeps repeated searches in serpapi_cache.json so rerunning the
  same query does not keep spending searches from this program.
- Do not upload config.json or serpapi_cache.json to the public web folder.

OUTPUT FILES
- index.html: phone-friendly dashboard
- results.json: data used by the dashboard
- report.html: easy browser report
- report.pdf: printable/downloadable report
- top5_report.txt: plain text backup
- jamify_results.xlsx: advanced table

CURRENT LIVE LIMITS
Live mode pulls Google Shopping prices, seller/result counts, ratings,
sample product titles, and sources through SerpApi. Trend growth,
review pain-point scanning, TikTok/Etsy/Amazon/Reddit discovery, and
weekly alerts are still later steps.

SCORING
Demand proxy 35% + low competition 30% + price spread 20% + quality gap 15%.
