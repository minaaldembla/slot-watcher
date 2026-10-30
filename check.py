import os
import smtplib
from datetime import datetime, timezone
from email.message import EmailMessage

import requests
from playwright.sync_api import sync_playwright

URL = "https://broneering.mfa.ee/en/"
KEYWORD = os.getenv("KEYWORD", "").strip() or "Long-stay visa"


def env(name, default=""):
    return "".join((os.getenv(name) or default).split())


def ntfy(msg, title, priority, tags):
    topic = env("NTFY_TOPIC")
    if not topic:
        return
    requests.post(
        f"https://ntfy.sh/{topic}",
        data=msg.encode("utf-8"),
        headers={"Title": title, "Priority": priority,
                 "Tags": tags, "Click": URL},
        timeout=15,
    )


def send_ntfy(msg):
    ntfy(msg, "D-visa slot OPEN", "urgent", "rotating_light")


def send_email(msg):
    user, pwd = env("SMTP_USER"), env("SMTP_PASS")
    if not (user and pwd):
        return
    m = EmailMessage()
    m["From"], m["To"] = user, env("EMAIL_TO", user)
    m["Subject"] = "D-visa slot OPEN - Estonia Embassy New Delhi"
    m.set_content(msg)
    with smtplib.SMTP_SSL("smtp.gmail.com", 465, timeout=30) as s:
        s.login(user, pwd)
        s.send_message(m)


def send_whatsapp(msg):
    sid, token, to = env("TWILIO_SID"), env("TWILIO_TOKEN"), env("WA_TO")
    if not (sid and token and to):
        return
    r = requests.post(
        f"https://api.twilio.com/2010-04-01/Accounts/{sid}/Messages.json",
        auth=(sid, token),
        data={"From": env("TWILIO_WA_FROM", "whatsapp:+14155238886"),
              "To": to, "Body": msg},
        timeout=20,
    )
    if not r.ok:
        raise RuntimeError(f"{r.status_code} {r.text[:300]}")


def alert(msg):
    for fn in (send_ntfy, send_email, send_whatsapp):
        try:
            fn(msg)
            print(f"{fn.__name__}: done")
        except Exception as e:
            print(f"{fn.__name__} FAILED: {e}")


def check():
    """Returns (found, detail). Raises an error if something is really broken."""
    with sync_playwright() as p:
        browser = p.chromium.launch(channel="chrome")
        page = browser.new_page()

        # Site down / maintenance: not our fault, report it but don't fail
        try:
            resp = page.goto(URL, timeout=45000)
        except Exception as e:
            return False, f"Site unreachable: {str(e)[:120]}"
        status = resp.status if resp else 0
        try:
            body = page.inner_text("body", timeout=5000).lower()
        except Exception:
            body = ""
        if status >= 500 or "down for maintenance" in body or "hooldust" in body:
            return False, f"Site in maintenance/unavailable (HTTP {status})"

        first = page.locator("select").first
        first.wait_for(timeout=20000)
        target = next((o for o in first.locator("option").all_inner_texts()
                       if "New Delhi" in o), None)
        if not target:
            raise RuntimeError("New Delhi option not found - site layout changed?")
        first.select_option(label=target)

        found = False
        seen = {}
        for _ in range(12):
            page.wait_for_timeout(1000)
            selects = page.locator("select")
            for i in range(1, selects.count()):
                texts = selects.nth(i).locator("option").all_inner_texts()
                seen[i] = texts
                if any(KEYWORD.lower() in t.lower() for t in texts):
                    found = True
            if found:
                break
        browser.close()
        return found, f"Site up. Dropdown 2 options: {seen}"


if env("TEST_ALERT") == "true":
    alert(f"TEST alert from your Estonia slot watcher. Booking site: {URL}")
    raise SystemExit(0)

try:
    found, detail = check()
except Exception as e:
    msg = f"Watcher ERROR: {str(e)[:300]}"
    print(msg)
    # tell the phone about errors about once an hour (not every 5 minutes)
    if datetime.now(timezone.utc).minute < 5:
        try:
            ntfy(msg, "Watcher ERROR", "high", "warning")
        except Exception:
            pass
    raise SystemExit(1)

print(detail)
print("SLOT OPEN" if found else "no slot")
if found:
    alert(f"Estonia Embassy New Delhi: '{KEYWORD}' is selectable NOW! Book: {URL}")
if env("HEARTBEAT") == "true":
    try:
        ntfy(f"Watcher alive. {detail}. Slot: {'YES' if found else 'no'}",
             "Watcher OK", "low", "white_check_mark")
    except Exception as e:
        print(f"heartbeat FAILED: {e}")
