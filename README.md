TREND RESEARCH
==============
Private product-opportunity research tool for Jamie.

WHAT IT DOES
- Jamie logs in with the same staff user/password from the store portal.
- Top Trends, Most Potential, and Custom can generate a fresh live report.
- Each run is capped at 3 searches by default and has a 5 minute cooldown.
- Current and previous reports can be viewed as HTML or PDF.

DEFAULT MARKET
Florida, Volusia County, Edgewater, New Smyrna Beach, Oak Hill, Titusville.

SETUP
pip install -r requirements.txt

Copy config.example.json to config.json and set:
- serpapi_key
- portal_users_path
- allowed_users: ["jamie"]

RUN LOCALLY
python report_server.py

Open:
http://127.0.0.1:8099/

SERVER NOTES
Nginx must proxy the Jamify site to report_server.py. Do not serve results.json,
report.html, report.pdf, top5_report.txt, or jamify_results.xlsx directly from
/var/www, because those files must stay behind Jamie's login.

ENVIRONMENT CONTROLS
- MAX_SEARCHES: paid search cap per report run. Default 3.
- RUN_COOLDOWN_SECONDS: delay between report runs. Default 300.
- PORTAL_USERS_PATH: path to the store portal users.json.
- ALLOWED_USERS: comma-separated usernames allowed into this tool. Default jamie.
- TREND_OUTPUT_DIR: where current report files are written.
- TREND_STATE_DIR: where private run history is saved.

OUTPUT FILES
- index.html: phone-friendly dashboard and login screen.
- results.json: current report data.
- report.html: printable browser report.
- report.pdf: downloadable report.
- top5_report.txt: plain text backup.
- jamify_results.xlsx: advanced table.

DATA SOURCES
Live reports use Google Shopping data through SerpApi for prices, seller/result
counts, ratings, sample product titles, and sample stores. Each product also
gets quick check links for Google Trends, Reddit, Etsy, and Amazon without
spending extra searches.
