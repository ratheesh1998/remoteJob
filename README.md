# RemoteApply

Find **remote jobs** that match your CV and apply to many of them **by email, from your own mailbox**, with a personalised message and your CV attached. You review every email before anything is sent.

RemoteApply is a **single-user app that runs on your own computer**. There is no login, no signup and no cloud service. Your CV, your jobs and your email password stay on your machine.

| Review every email | Watch sending live |
|---|---|
| ![Review emails](docs/screenshots/review-emails.png) | ![Sending progress](docs/screenshots/sending-progress.png) |

## Contents

1. [What you need](#1-what-you-need)
2. [Install and start](#2-install-and-start)
3. [Set up your email (SMTP)](#3-set-up-your-email-smtp)
4. [Optional: turn on AI](#4-optional-turn-on-ai)
5. [Use it, step by step](#5-use-it-step-by-step)
6. [Configuration reference](#6-configuration-reference)
7. [Run without Docker](#7-run-without-docker)
8. [Troubleshooting](#8-troubleshooting)
9. [How it works and security](#9-how-it-works-and-security)
10. [Development and tests](#10-development-and-tests)
11. [Responsible use](#11-responsible-use)

## 1. What you need

- **Docker Desktop** (or Docker Engine with Compose v2). Get it at <https://www.docker.com/products/docker-desktop/>.
- **Git**, to download the project.
- An **email account** you can send from (Gmail, Outlook, or any SMTP provider).
- A **CV** as a text-based PDF or a DOCX (a scanned image will not work).
- *Optional:* an Anthropic API key for AI features (see [section 4](#4-optional-turn-on-ai)).

## 2. Install and start

```bash
git clone https://github.com/ratheesh1998/remoteJob.git
cd remoteJob
cp .env.example .env        # optional; you can edit it later
docker compose up --build
```

When the logs settle, open **<http://127.0.0.1:8000>**.

> **Port 8000 already in use?** Start on another port: `WEB_PORT=8001 docker compose up --build` and open `http://127.0.0.1:8001`.

The first start builds the image (a few minutes). To run in the background use `docker compose up --build -d`; stop with `docker compose down`. Your data lives in a Docker volume and survives restarts. `docker compose down -v` deletes it.

What starts: `web` (the site), `worker` (searches and sending), `beat` (watchdogs), `redis`, and `mailhog` (a fake inbox for testing, at <http://127.0.0.1:8025>).

## 3. Set up your email (SMTP)

Nothing else in the app works until your email is saved **and** a test email succeeds. Open **Email settings**, fill the form, then click **Save and send test email**. The test goes to your own From address.

### Gmail (recommended)

Gmail needs an **App Password**, not your normal password.

1. Go to your Google Account, then **Security**, and turn on **2-Step Verification**.
2. Open <https://myaccount.google.com/apppasswords>.
3. Name it "RemoteApply" and click **Create**.
4. Copy the 16-character password (spaces don't matter).
5. In RemoteApply click **Fill Gmail settings**, then enter:
   - Username: your full Gmail address
   - Password: the App Password
   - From email: the same Gmail address
   - From name: your name

| Setting | Value |
|---|---|
| Host | `smtp.gmail.com` |
| Port / security | `465` / SSL/TLS (or `587` / STARTTLS) |

Can't see the App Passwords page? 2-Step Verification is off, or your account is a managed Workspace account that blocks it.

### Outlook / Hotmail / Microsoft 365

| Setting | Value |
|---|---|
| Host | `smtp-mail.outlook.com` (Microsoft 365: `smtp.office365.com`) |
| Port / security | `587` / STARTTLS |
| Username | your full email address |

If you use two-step verification, create an app password in your Microsoft account security settings. Some Microsoft 365 organisations disable SMTP sign-in; ask your admin if you get an authentication error.

### Any other provider

Use the host, port and security type from your provider's "SMTP settings" page. Port 465 is normally SSL/TLS, and 587 is normally STARTTLS.

### Daily send limit

Default is 40 emails a day. Keep it low: providers may suspend accounts that send many similar messages quickly.

### Trying it without a real mailbox

Use MailHog: host `mailhog`, port `1025`, security **None (local testing only)**, any username and password, any From address. Sent emails appear at <http://127.0.0.1:8025>. "None" is refused for any host except localhost/mailhog.

### What if the test fails?

The page shows the server's exact error and a hint. The common ones are in [Troubleshooting](#8-troubleshooting).

![Email settings](docs/screenshots/email-settings.png)

## 4. Optional: turn on AI

Without an API key RemoteApply still works: it reads your CV with basic rules and uses a fixed email template. With a key, an LLM reads your CV more accurately and writes one personalised paragraph per application.

1. Create an API key at <https://console.anthropic.com>.
2. Put it in `.env`: `ANTHROPIC_API_KEY=your-key`
3. Restart: `docker compose up -d` (or `docker compose restart web worker`).

The CV text and each job description are sent to Anthropic only when a key is set. The default model is `claude-opus-5-5`; change `ANTHROPIC_MODEL` to use another.

The AI is never trusted blindly: email, phone and skills it extracts must literally appear in your CV, and in emails it may only cite skills that are in both your CV and the job text, with no invented numbers. If a check fails the fixed template is used instead.

## 5. Use it, step by step

The bar at the top shows five steps; finished steps get a check and later steps are locked.

1. **Email**: set up SMTP and send the test email ([section 3](#3-set-up-your-email-smtp)).
2. **CV**: upload your PDF or DOCX (max 5 MB). Anything the app is unsure about is left blank, never guessed.
3. **Confirm**: check your designation, alternate titles, skills and location, then click **Confirm and find jobs**. The search can't run before this. Uploading a new CV resets it.
4. **Find jobs**: click **Find remote jobs**. Sources run in parallel (a LinkedIn search takes about a minute; LinkedIn posts appear if a search API key is set); one failing source never breaks the search and you see each one's status (for example "Remotive: 23, RemoteOK: failed"). Filter by source, date, minimum score and "has email"; sort; tick rows. *Select all* selects the page, *Select all with email* selects every emailable job on every page.
5. **Review and send**: click **Review N selected applications**. Edit any subject or body, remove jobs, then **Confirm and queue**. Nothing is sent before that.

Then watch the **live progress** page. Emails go out one at a time with a random 20-60 second pause, stop at your daily limit, and are never sent twice to the same recipient for the same job. Temporary SMTP errors retry at most 2 times. The **Applications** page keeps history, the exact email sent, a Retry button for failures, and a CSV export.

### Why do some jobs say "Apply on site"?

Most employers collect applications through their own forms. A job can be emailed only if its description contains a plausible application address, so many postings, perhaps most, are "Apply on site" (opens the posting) and can't be bulk-emailed.

### Delete everything

Footer link **Delete all my data** (type `DELETE`) wipes your CV and its file, jobs, applications and SMTP settings.

## 5b. LinkedIn hiring posts with an HR email

Many recruiters post "send your CV to ..." on LinkedIn instead of using a job board. RemoteApply can list those posts next to the normal jobs.

**How posts are found (and what is never done).** RemoteApply never logs in to LinkedIn, never uses cookies, a browser or a proxy, and never fetches linkedin.com for this feature. It asks an **official search API** (Brave Search API, or Google Programmable Search) for public `linkedin.com/posts` results from the last 30 days and reads only the title, URL and snippet the API returns.

1. Get a key at <https://brave.com/search/api/> and put it in `.env`: `BRAVE_API_KEY=...` (then `docker compose up -d`). Google's JSON API (`GOOGLE_CSE_KEY` + `GOOGLE_CSE_ID`) is **closed to new customers and ends 2027-01-01**, so Brave is recommended.
2. Open **Search settings** to choose the provider and the maximum queries per search (default 6; each query uses one request from your quota). The page shows whether a key is set (never the key) and your remaining quota. Results are cached for 24 hours, so repeating a search costs nothing.
3. Click **Find remote jobs** as usual. Posts appear as **LinkedIn post** with a **View post** link and flow through the same preview, queue, send and history steps.

**Email extraction is strict.** An address is kept only if it passes all of these: valid syntax; not no-reply / donotreply / privacy / legal style mailboxes; not a linkedin.com address; not an image-like name such as `logo@2x.png`; not cut off by an ellipsis in the snippet (a truncated address may be wrong); and its domain has an **MX record** (a DNS timeout keeps the address, only a definite "no mail server" drops it). Posts without a usable email are not listed. Because search snippets often cut posts short, only a fraction of results contain an email.

**Add from a LinkedIn post (reliable fallback).** On the Find jobs page, click *add a post you found yourself*. Paste the text of a post (several posts separated by a line with only `---`), check the parsed email, title, company and skills, and save. Pasted jobs are marked **LinkedIn post (pasted)** and are kept across new searches. Title and company are `null`/blank when the text doesn't say; nothing is invented.

**Safety and respect for recipients**

- **Do not contact** (top menu): any address or whole domain on the list is never queued or sent, and the row is disabled in the results. The history page has a one-click **Do not contact** button that also cancels anything still queued for that address.
- **Contact cap:** the same address is never emailed more than once in 30 days (`CONTACT_CAP_DAYS`), across all jobs. Held-back rows say so.
- **Honest emails:** emails for posts say "I saw your post on LinkedIn about the {title} role", never claim a personal connection or invented experience, and end with "If this isn't the right contact, tell me and I won't write again."
- **Privacy:** only the email, post URL and snippet are stored. **Delete all my data** also wipes post jobs, the search cache, the do-not-contact list and the search settings.
- These emails come from public hiring posts. Use them only to apply for the advertised role.

## 6. Configuration reference

Set these in `.env` (copied from `.env.example`). All are optional.

| Variable | Default | Meaning |
|---|---|---|
| `ANTHROPIC_API_KEY` | empty | Enables AI CV reading and email personalisation |
| `ANTHROPIC_MODEL` | `claude-opus-5-5` | Model used for both |
| `FERNET_KEY` | auto-generated | Key that encrypts your SMTP password. Auto-created in the data folder on first run |
| `DJANGO_SECRET_KEY` | auto-generated | Django secret, auto-created on first run |
| `ALLOWED_HOSTS` | `127.0.0.1,localhost` | Keep local. Other hosts show a "no login" warning banner |
| `DATABASE_URL` | SQLite | PostgreSQL: `postgres://user:pass@host:5432/db` |
| `REDIS_URL` | `redis://127.0.0.1:6379/0` | Celery broker (Docker sets this for you) |
| `TIME_ZONE` | `UTC` | Defines "today" for the daily limit |
| `SEND_DELAY_MIN` / `SEND_DELAY_MAX` | `20` / `60` | Random seconds between emails |
| `MIN_MATCH_SCORE_TO_KEEP` | `20` | Jobs scoring lower are hidden |
| `ADZUNA_APP_ID`, `ADZUNA_APP_KEY`, `ADZUNA_COUNTRY` | empty / `us` | Turns on the Adzuna source |
| `JSEARCH_API_KEY` | empty | Turns on the JSearch (RapidAPI) source |
| `SEARCH_PROVIDER` | `brave` | `brave` or `google`: which official search API finds LinkedIn posts |
| `BRAVE_API_KEY` | empty | Turns on LinkedIn post search (Brave Search API) |
| `GOOGLE_CSE_KEY`, `GOOGLE_CSE_ID` | empty | Alternative provider (closed to new customers, ends 2027-01-01) |
| `SEARCH_MAX_QUERIES` | `6` | Default max queries per search (editable in Search settings) |
| `CONTACT_CAP_DAYS` | `30` | Never email the same address more than once in this many days |
| `LINKEDIN_ENABLED` | `true` | Include LinkedIn public remote job listings |
| `LINKEDIN_REQUEST_DELAY` | `2.5` | Seconds between LinkedIn requests (raise it if you get throttled) |
| `WEB_PORT` / `MAILHOG_PORT` | `8000` / `8025` | Host ports used by Docker Compose |

After editing `.env`: `docker compose up -d`.

## 7. Run without Docker

Needs Python 3.11+ and a Redis server.

```bash
python3.11 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
python manage.py migrate
redis-server &                                   # any Redis on localhost:6379
celery -A remoteapply worker -l info &           # searches and sending
celery -A remoteapply beat -l info &             # optional watchdogs
python manage.py runserver 127.0.0.1:8000
```

## 8. Troubleshooting

| Problem | Fix |
|---|---|
| `port is already allocated` on 8000 | `WEB_PORT=8001 docker compose up` |
| Test email: `535 Username and Password not accepted` | Gmail: use an **App Password** and make sure 2-Step Verification is on. Check the username is your full address |
| Test email: TLS / SSL error | Port 465 needs **SSL/TLS**, port 587 needs **STARTTLS** |
| Test email: timeout or connection refused | Check host and port, and that your network allows outgoing SMTP |
| "No readable text" on CV upload | The PDF is a scan. Export a text-based PDF or use DOCX |
| Search stays on "Searching…" | The worker isn't running: `docker compose logs worker`. After 5 minutes unfinished sources are marked failed |
| No LinkedIn posts found | Normal: search snippets often cut posts short, so few contain an email. Check Search settings shows "key set", or use *add a post you found yourself* |
| "Brave rate limit or monthly quota reached" | You used your API quota. Lower the max queries, wait for the reset, or use the paste page |
| LinkedIn shows few jobs, or "throttled" | LinkedIn rate-limits automated requests. Wait a few minutes and search again, raise `LINKEDIN_REQUEST_DELAY`, or set `LINKEDIN_ENABLED=false` |
| A source shows "failed" | That site was down or rate-limited. The others still work; search again later |
| Almost every job says "Apply on site" | Normal. Few postings publish an email address |
| "Could not decrypt" the SMTP password | The encryption key changed. Re-enter the password on Email settings |
| Red "no login" banner | You opened the app from a non-local address. Use `127.0.0.1` and do not expose the app to the internet |

## 9. How it works and security

- **Local only.** Docker publishes every port on `127.0.0.1`. There is no login, so never expose the app to the internet.
- **Your SMTP password** is encrypted at rest with Fernet and is write-only: never shown in a page or written to logs.
- **Job descriptions** are reduced to plain text when fetched and rendered escaped.
- **Sources:** Remotive, RemoteOK (credit link shown as its terms require), Himalayas, We Work Remotely (RSS), **LinkedIn public remote job listings**, and optional Adzuna and JSearch. Indeed and Naukri are not scraped.
- **LinkedIn:** the adapter reads LinkedIn's public job pages (no login, remote filter on). LinkedIn's terms prohibit automated access and it throttles heavily, so it makes about 25 paced requests per search, may return fewer jobs or no descriptions when throttled, and can be switched off with `LINKEDIN_ENABLED=false`. Only jobs whose description contains an application email can be bulk-emailed. LinkedIn *posts* are found separately and never by fetching linkedin.com: see section 5b.
- **Dedupe:** jobs are the same if they share a normalised URL or the same company and title. **One application per recipient per job** is a database unique constraint.
- **Daily limit** counts emails sent since local midnight. Extra emails are marked "Not sent (daily limit)" and can be retried later.

Stack: Django, Celery + Redis, SQLite (PostgreSQL optional), HTMX, Tailwind (CDN), pdfplumber, python-docx.

## 10. Development and tests

```bash
pip install -r requirements-dev.txt
python manage.py test tests      # no Redis needed
```

Layout: `core/` (SMTP setup, encryption, gate), `cv/` (parsing, extraction), `jobs/` (source adapters, scoring, dedupe, search tasks), `applications/` (email building, sending, history), `templates/`, `tests/`.

## 11. Responsible use

Send only applications that are relevant to you and the role. You are responsible for every email sent from your account and for following the email and anti-spam laws that apply to you and to the recipient (for example CAN-SPAM, GDPR/PECR, CASL). Keep the daily limit low. LinkedIn's terms prohibit automated access to its site; enabling the LinkedIn source is your choice and your risk (turn it off with `LINKEDIN_ENABLED=false`). This tool is not affiliated with any job board it reads from.
