print("hello world")#!/usr/bin/env python3
"""
Daily Job Monitor
Scans multiple company career sites and emails new job postings.
Designed to run via GitHub Actions every morning.

Setup:
  1. Set GitHub Actions secrets: SENDER_EMAIL, SENDER_PASSWORD, RECIPIENT_EMAIL
  2. The workflow runs daily at 8 AM UTC (adjust cron in .github/workflows/daily_scan.yml)
  3. seen_jobs.json is committed back to the repo after each run for deduplication.
"""

import json
import os
import re
import smtplib
import time
from datetime import datetime, date
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from pathlib import Path

from bs4 import BeautifulSoup
from selenium import webdriver
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.common.by import By
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.support.ui import WebDriverWait
from webdriver_manager.chrome import ChromeDriverManager

# ---------------------------------------------------------------------------
# Configuration  (override via environment variables / GitHub Actions secrets)
# ---------------------------------------------------------------------------
RECIPIENT_EMAIL = os.environ.get("RECIPIENT_EMAIL", "YOUR_EMAIL@example.com")
SENDER_EMAIL    = os.environ.get("SENDER_EMAIL", "")
SENDER_PASSWORD = os.environ.get("SENDER_PASSWORD", "")
SMTP_HOST       = os.environ.get("SMTP_HOST", "smtp.gmail.com")
SMTP_PORT       = int(os.environ.get("SMTP_PORT", "587"))

SEEN_JOBS_FILE = Path("seen_jobs.json")

INCLUDE_KEYWORDS = ["analyst", "associate", "specialist", "coordinator", "researcher"]
EXCLUDE_KEYWORDS = ["senior", "sr.", "manager", "director", "vp", "lead", "principal", "head"]

SITES = [
    {
        "name": "Kantar",
        "url": "https://kantar.wd3.myworkdayjobs.com/en-US/KANTAR?Country=bc33aa3152ec42d4995f4791a106ed09",
        "type": "workday",
        "base_url": "https://kantar.wd3.myworkdayjobs.com",
    },
    {
        "name": "Ipsos",
        "url": "https://ecqf.fa.em2.oraclecloud.com/hcmUI/CandidateExperience/en/sites/IpsosCareers/jobs?location=United+States&locationId=300000000345520&locationLevel=country&mode=job-location",
        "type": "oracle",
        "base_url": "https://ecqf.fa.em2.oraclecloud.com",
    },
    {
        "name": "Burson",
        "url": "https://www.bursonglobal.com/careers#openpositions",
        "type": "generic",
        "base_url": "https://www.bursonglobal.com",
    },
    {
        "name": "NielsenIQ",
        "url": "https://nielseniq.com/?s=&market=global&language=en&orderby=&order=&post_type=career_job&job_locations=united-states",
        "type": "generic",
        "base_url": "https://nielseniq.com",
    },
    {
        "name": "Heineken",
        "url": "https://careers.theheinekencompany.com/Job-Listing?field_location_country_code_1[0]=US&page=0%2C0%2C1",
        "type": "generic",
        "base_url": "https://careers.theheinekencompany.com",
    },
    {
        "name": "Publicis",
        "url": "https://careers.publicisgroupe.com/jobs?location=United%20States&woe=12&regionCode=US&stretchUnit=MILES&stretch=10&page=1",
        "type": "publicis",
        "base_url": "https://careers.publicisgroupe.com",
    },
]

# ---------------------------------------------------------------------------
# Filter helpers
# ---------------------------------------------------------------------------

def title_passes_filter(title: str) -> bool:
    """Return True if title matches include rules AND does not match exclude rules."""
    t = title.lower()
    has_include = any(kw in t for kw in INCLUDE_KEYWORDS)
    has_exclude = any(kw in t for kw in EXCLUDE_KEYWORDS)
    return has_include and not has_exclude


def is_us_location(location: str) -> bool:
    """Return True only for explicitly US-listed locations."""
    if not location or location.strip() == "":
        return False
    loc = location.lower()

    non_us = [
        "global", "worldwide", "international", "remote - global",
        " uk", "united kingdom", "london", " canada", "toronto",
        "india", "bangalore", "australia", "sydney", "germany",
        "france", "paris", "singapore", "netherlands", "amsterdam",
        "brazil", "mexico", "japan", "china", "korea",
    ]
    if any(s in loc for s in non_us):
        return False

    us_signals = [
        "united states", " us ", ", us", "u.s.",
        ", ny", ", ca", ", tx", ", il", ", wa", ", fl", ", ga",
        ", ma", ", nj", ", co", ", nc", ", oh", ", pa", ", az",
        ", mn", ", mi", ", ct", ", or", ", tn", ", mo", ", va",
        ", md", ", wi", ", dc", ", nv", ", ut", ", ok", ", sc",
        "new york", "chicago", "los angeles", "san francisco",
        "boston", "seattle", "austin", "dallas", "atlanta",
        "miami", "denver", "minneapolis", "detroit", "portland",
        "charlotte", "philadelphia", "st. louis", "kansas city",
        "salt lake city", "cincinnati", "nashville", "pittsburgh",
        "baltimore", "washington d.c", "raleigh", "san diego",
        "san jose", "columbus", "jacksonville", "indianapolis",
        "memphis", "louisville", "richmond", "hartford", "new haven",
    ]
    return any(s in loc for s in us_signals)


# ---------------------------------------------------------------------------
# Persistence
# ---------------------------------------------------------------------------

def load_seen_jobs() -> dict:
    if SEEN_JOBS_FILE.exists():
        with open(SEEN_JOBS_FILE, "r") as f:
            return json.load(f)
    return {}


def save_seen_jobs(seen: dict) -> None:
    with open(SEEN_JOBS_FILE, "w") as f:
        json.dump(seen, f, indent=2)


# ---------------------------------------------------------------------------
# WebDriver
# ---------------------------------------------------------------------------

def make_driver() -> webdriver.Chrome:
    options = Options()
    options.add_argument("--headless")
    options.add_argument("--no-sandbox")
    options.add_argument("--disable-dev-shm-usage")
    options.add_argument("--disable-gpu")
    options.add_argument("--window-size=1920,1080")
    options.add_argument(
        "user-agent=Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    )
    service = Service(ChromeDriverManager().install())
    return webdriver.Chrome(service=service, options=options)


def slow_scroll(driver, pause: float = 1.5, max_scrolls: int = 20) -> None:
    """Incrementally scroll to trigger lazy-loaded content."""
    last_h = driver.execute_script("return document.body.scrollHeight")
    for _ in range(max_scrolls):
        driver.execute_script("window.scrollTo(0, document.body.scrollHeight);")
        time.sleep(pause)
        new_h = driver.execute_script("return document.body.scrollHeight")
        if new_h == last_h:
            break
        last_h = new_h


def wait_for_jobs(driver, selectors: list[str], timeout: int = 15) -> None:
    for sel in selectors:
        try:
            WebDriverWait(driver, timeout).until(
                EC.presence_of_element_located((By.CSS_SELECTOR, sel))
            )
            return
        except Exception:
            continue
    time.sleep(5)  # fallback


# ---------------------------------------------------------------------------
# Location / date extraction helpers
# ---------------------------------------------------------------------------

def _find_location_near(tag) -> str:
    parent = tag.find_parent(["li", "article", "div", "tr", "section"])
    if not parent:
        return ""
    text = parent.get_text(" ", strip=True)
    match = re.search(
        r"([A-Z][a-zA-Z .]+,\s*[A-Z]{2}(?:\s+\d{5})?|United States|U\.S\.)",
        text,
    )
    return match.group(0) if match else ""


def _find_date_near(tag) -> str:
    parent = tag.find_parent(["li", "article", "div", "tr", "section"])
    if not parent:
        return ""
    text = parent.get_text(" ", strip=True)
    match = re.search(
        r"(\d{1,2}/\d{1,2}/\d{2,4}"
        r"|\d{4}-\d{2}-\d{2}"
        r"|(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)"
        r"[a-z]*\.?\s+\d{1,2},?\s*\d{4}"
        r"|\d+\s+days?\s+ago"
        r"|today|yesterday)",
        text, re.I,
    )
    return match.group(0) if match else ""


# ---------------------------------------------------------------------------
# Workday scraper
# ---------------------------------------------------------------------------

def scrape_workday(driver, site: dict) -> list[dict]:
    jobs: list[dict] = []
    driver.get(site["url"])
    wait_for_jobs(driver, ["a[data-automation-id='jobTitle']", "li[class*='css-']"])
    slow_scroll(driver)

    soup = BeautifulSoup(driver.page_source, "html.parser")
    job_links = soup.select("a[data-automation-id='jobTitle']")

    for link in job_links:
        title = link.get_text(strip=True)
        href = link.get("href", "")
        if href and not href.startswith("http"):
            href = site["base_url"] + href

        card = link.find_parent("li") or link.find_parent("div")
        location = date_posted = ""
        if card:
            loc_el = card.find(attrs={"data-automation-id": "subtitle"})
            location = loc_el.get_text(strip=True) if loc_el else _find_location_near(link)
            date_el = card.find(attrs={"data-automation-id": "postedOn"})
            date_posted = date_el.get_text(strip=True) if date_el else ""

        if title and title_passes_filter(title) and is_us_location(location):
            jobs.append(_job(site, title, href, location, date_posted))
    return jobs


# ---------------------------------------------------------------------------
# Oracle HCM scraper
# ---------------------------------------------------------------------------

def scrape_oracle(driver, site: dict) -> list[dict]:
    jobs: list[dict] = []
    driver.get(site["url"])
    wait_for_jobs(driver, [
        "a.job-list-item--link",
        ".jobs-list .job-item",
        "[class*='jobTitle']",
        "[class*='job-card']",
    ])
    slow_scroll(driver)

    soup = BeautifulSoup(driver.page_source, "html.parser")
    for sel in [
        "a.job-list-item--link",
        ".jobs-list a",
        "li.job-item a",
        "[class*='jobTitle'] a",
        "a[class*='jobTitle']",
        "[class*='job-card'] a",
    ]:
        links = soup.select(sel)
        if links:
            break

    for link in links:
        title = link.get_text(strip=True)
        href = link.get("href", "")
        if href and not href.startswith("http"):
            href = site["base_url"] + href
        card = link.find_parent("li") or link.find_parent("div")
        location = ""
        if card:
            matches = card.find_all(string=re.compile(r",\s*[A-Z]{2}|United States", re.I))
            location = str(matches[0]).strip() if matches else _find_location_near(link)
        if title and title_passes_filter(title) and is_us_location(location):
            jobs.append(_job(site, title, href, location, _find_date_near(link)))
    return jobs


# ---------------------------------------------------------------------------
# Generic scraper with pagination
# ---------------------------------------------------------------------------

def _paginate_url(base_url: str, page_num: int, site_name: str) -> str:
    if site_name == "Publicis":
        return re.sub(r"page=\d+", f"page={page_num}", base_url)
    if site_name == "Heineken":
        return re.sub(r"page=[^&]+", f"page=0%2C{page_num - 1}%2C1", base_url)
    if site_name == "NielsenIQ":
        return base_url + f"&paged={page_num}"
    return base_url


def _extract_jobs_from_soup(soup: BeautifulSoup, site: dict) -> list[dict]:
    job_links = []

    priority_selectors = [
        "a.job-listing", "a.career-item", "a.position-item", "a.job-item",
        ".job-card a[href]", ".career-card a[href]", ".position a[href]",
        "article.job a[href]", "li.job a[href]", "li.opening a[href]",
        ".jobs-list a[href]", ".careers-list a[href]", ".openings a[href]",
        "[class*='job-title'] a[href]", "[class*='position-title'] a[href]",
        "[class*='job_title'] a[href]", "[class*='jobTitle'] a[href]",
        ".job-result a[href]", ".job-posting a[href]",
    ]
    for sel in priority_selectors:
        found = soup.select(sel)
        if len(found) > 1:
            job_links = found
            break

    if not job_links:
        from urllib.parse import urlparse
        domain = urlparse(site["url"]).netloc
        job_links = [
            a for a in soup.find_all("a", href=True)
            if any(kw in a.get_text(strip=True).lower() for kw in INCLUDE_KEYWORDS)
            and len(a.get_text(strip=True).split()) >= 2
            and domain in (a.get("href", "") or "")
        ]

    seen_titles: set[str] = set()
    jobs: list[dict] = []
    for link in job_links:
        title = link.get_text(strip=True)
        if not title or title in seen_titles:
            continue
        seen_titles.add(title)
        href = link.get("href", "")
        if href and not href.startswith("http"):
            from urllib.parse import urljoin
            href = urljoin(site["url"], href)
        location = _find_location_near(link)
        if title_passes_filter(title) and is_us_location(location):
            jobs.append(_job(site, title, href, location, _find_date_near(link)))
    return jobs


def scrape_generic(driver, site: dict) -> list[dict]:
    jobs: list[dict] = []
    page_num = 1
    while page_num <= 20:
        url = site["url"] if page_num == 1 else _paginate_url(site["url"], page_num, site["name"])
        driver.get(url)
        try:
            WebDriverWait(driver, 15).until(EC.presence_of_element_located((By.TAG_NAME, "body")))
        except Exception:
            pass
        time.sleep(3)
        slow_scroll(driver, pause=1.0, max_scrolls=10)

        soup = BeautifulSoup(driver.page_source, "html.parser")
        page_jobs = _extract_jobs_from_soup(soup, site)
        if not page_jobs:
            break
        jobs.extend(page_jobs)

        next_btn = soup.find("a", string=re.compile(r"^\s*(next|>|›)\s*$", re.I))
        if not next_btn:
            break
        page_num += 1
    return jobs


# ---------------------------------------------------------------------------
# Utility
# ---------------------------------------------------------------------------

def _job(site: dict, title: str, url: str, location: str, date_posted: str) -> dict:
    return {
        "company": site["name"],
        "title": title,
        "url": url,
        "location": location,
        "date_posted": date_posted,
    }


# ---------------------------------------------------------------------------
# Orchestration
# ---------------------------------------------------------------------------

def scan_all_sites() -> tuple[list[dict], dict]:
    driver = make_driver()
    all_jobs: list[dict] = []
    per_site_counts: dict = {}
    errors: dict = {}

    try:
        for site in SITES:
            print(f"[+] Scanning {site['name']} ...")
            try:
                if site["type"] == "workday":
                    jobs = scrape_workday(driver, site)
                elif site["type"] == "oracle":
                    jobs = scrape_oracle(driver, site)
                else:
                    jobs = scrape_generic(driver, site)
                per_site_counts[site["name"]] = len(jobs)
                all_jobs.extend(jobs)
                print(f"    -> {len(jobs)} matching job(s)")
            except Exception as exc:
                msg = str(exc)
                print(f"    ERROR: {msg}")
                per_site_counts[site["name"]] = 0
                errors[site["name"]] = msg
    finally:
        driver.quit()

    return all_jobs, per_site_counts, errors


def categorize_jobs(all_jobs: list[dict], seen: dict) -> tuple[list[dict], list[dict]]:
    new_jobs, still_open = [], []
    for job in all_jobs:
        key = job["url"] or f"{job['company']}|{job['title']}"
        if key not in seen:
            new_jobs.append(job)
        else:
            still_open.append(job)
    return new_jobs, still_open


# ---------------------------------------------------------------------------
# Email
# ---------------------------------------------------------------------------

def build_email_body(
    new_jobs: list[dict],
    still_open: list[dict],
    per_site_counts: dict,
    errors: dict,
    scan_ts: str,
) -> str:
    today = date.today().strftime("%B %d, %Y")
    lines: list[str] = []

    lines += [f"JOB SCAN SUMMARY — {today}", ""]

    lines += [f"NEW ROLES ({len(new_jobs)})", "-" * 34]
    if new_jobs:
        for j in new_jobs:
            lines += [
                j["company"],
                f"Title:    {j['title']}",
                f"Location: {j['location']}",
                f"Link:     {j['url']}",
            ]
            if j.get("date_posted"):
                lines.append(f"Posted:   {j['date_posted']}")
            lines.append("")
    else:
        lines += ["No new roles found today.", ""]

    lines += [f"STILL OPEN FROM PRIOR SCANS ({len(still_open)})", "-" * 34]
    if still_open:
        for j in still_open:
            lines.append(f"{j['company']} — {j['title']} — {j['url']}")
        lines.append("")
    else:
        lines += ["None.", ""]

    lines += ["SITES SCANNED", "-" * 34]
    for site in SITES:
        count = per_site_counts.get(site["name"], 0)
        err = errors.get(site["name"], "")
        suffix = f" (ERROR: {err})" if err else f" — {count} match(es)"
        lines.append(f"{site['name']}{suffix}")
    lines.append("")

    if errors:
        lines += ["SCAN ERRORS", "-" * 34]
        for site_name, err_msg in errors.items():
            lines.append(f"{site_name}: {err_msg}")
        lines.append("")

    lines += [
        f"Total new roles today: {len(new_jobs)}",
        f"Scan completed: {scan_ts}",
    ]
    return "\n".join(lines)


def send_email(subject: str, body: str) -> None:
    if not SENDER_EMAIL or not SENDER_PASSWORD:
        print("[!] Email credentials not configured. Printing to stdout.")
        print(f"SUBJECT: {subject}")
        print(body)
        return

    msg = MIMEMultipart("alternative")
    msg["Subject"] = subject
    msg["From"] = SENDER_EMAIL
    msg["To"] = RECIPIENT_EMAIL
    msg.attach(MIMEText(body, "plain"))

    with smtplib.SMTP(SMTP_HOST, SMTP_PORT) as server:
        server.ehlo()
        server.starttls()
        server.login(SENDER_EMAIL, SENDER_PASSWORD)
        server.sendmail(SENDER_EMAIL, RECIPIENT_EMAIL, msg.as_string())

    print(f"[OK] Email sent to {RECIPIENT_EMAIL}")


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main() -> None:
    scan_ts = datetime.utcnow().strftime("%Y-%m-%d %H:%M UTC")
    today_str = date.today().strftime("%B %d, %Y")

    print("=" * 60)
    print(f"Daily Job Monitor — {scan_ts}")
    print("=" * 60)

    seen = load_seen_jobs()
    all_jobs, per_site_counts, errors = scan_all_sites()
    new_jobs, still_open = categorize_jobs(all_jobs, seen)

    for job in new_jobs:
        key = job["url"] or f"{job['company']}|{job['title']}"
        seen[key] = {
            "first_seen": today_str,
            "title": job["title"],
            "company": job["company"],
        }
    save_seen_jobs(seen)

    if new_jobs:
        subject = f"Daily Job Scan — {today_str} — {len(new_jobs)} New Role(s) Found"
    else:
        subject = f"Daily Job Scan — {today_str} — No New Roles"

    body = build_email_body(new_jobs, still_open, per_site_counts, errors, scan_ts)
    send_email(subject, body)
    print("Done.")


if __name__ == "__main__":
    main()
