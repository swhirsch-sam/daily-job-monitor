# daily-job-monitor# Daily Job Monitor

Automated tool that scans six company career sites every morning, filters job listings by title and US location, deduplicates results across runs, and sends a summary email.

## Sites Monitored

| Company | Site Type |
|---------|-----------|
| Kantar | Workday |
| Ipsos | Oracle HCM |
| Burson | Generic |
| NielsenIQ | Generic |
| Heineken | Generic |
| Publicis Groupe | Generic |

## Filter Rules

**Include** job titles containing (case-insensitive): `analyst`, `associate`, `specialist`, `coordinator`, `researcher`

**Exclude** job titles containing (case-insensitive): `senior`, `sr.`, `manager`, `director`, `vp`, `lead`, `principal`, `head`

Only US-based roles with explicit location are included.

## Repository Structure

```
daily-job-monitor/
├── job_monitor.py                    # Main script
├── requirements.txt                  # Python dependencies
├── seen_jobs.json                    # Auto-generated deduplication state (committed by CI)
└── .github/
    └── workflows/
        └── daily_scan.yml            # GitHub Actions workflow (runs daily at 8 AM UTC)
```

## Setup

### 1. Fork or clone this repository

### 2. Add GitHub Actions Secrets

Go to **Settings → Secrets and variables → Actions → New repository secret** and add:

| Secret Name | Description |
|-------------|-------------|
| `RECIPIENT_EMAIL` | The email address that receives the daily report |
| `SENDER_EMAIL` | Gmail address used to send the email |
| `SENDER_PASSWORD` | Gmail App Password (not your main password) |
| `SMTP_HOST` | (Optional) Defaults to `smtp.gmail.com` |
| `SMTP_PORT` | (Optional) Defaults to `587` |

> **Gmail App Password**: Go to your Google Account → Security → 2-Step Verification → App passwords. Generate one for "Mail".

### 3. Adjust the schedule (optional)

Edit `.github/workflows/daily_scan.yml` and change the cron expression:

```yaml
- cron: "0 8 * * *"   # 8:00 AM UTC daily
```

Use [crontab.guru](https://crontab.guru) to build your preferred schedule.

### 4. Run manually to test

Go to **Actions → Daily Job Monitor → Run workflow** to trigger a test run immediately.

## How It Works

1. GitHub Actions spins up an Ubuntu runner every morning.
2. Chrome and all Python dependencies are installed.
3. `job_monitor.py` launches a headless Chrome browser via Selenium.
4. Each career site is visited, scrolled to load dynamic content, and scraped.
5. Matching jobs are filtered by title keywords and US location.
6. Results are compared against `seen_jobs.json` (committed to the repo) to detect new vs. still-open roles.
7. An email is sent only if there is at least one new role (or always if you remove that check).
8. `seen_jobs.json` is updated and committed back to the repo.

## Email Format

**Subject:** `Daily Job Scan — May 20, 2026 — 3 New Role(s) Found`

**Body includes:**
- New roles with company, title, location, link, and date posted
- Still-open roles from prior scans
- Per-site match counts
- Any scan errors

## Local Development

```bash
# Install dependencies
pip install -r requirements.txt

# Set environment variables
export SENDER_EMAIL="your@gmail.com"
export SENDER_PASSWORD="your-app-password"
export RECIPIENT_EMAIL="your@email.com"

# Run
python job_monitor.py
```

> You need Chrome installed locally. `webdriver-manager` handles ChromeDriver automatically.

## Customization

All filter keywords and site URLs are defined at the top of `job_monitor.py` in the `INCLUDE_KEYWORDS`, `EXCLUDE_KEYWORDS`, and `SITES` lists — easy to edit without touching the scraping logic.
