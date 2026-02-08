import os
import math
import json
from datetime import datetime
from typing import Any, Dict, Tuple

import requests
from requests.auth import HTTPBasicAuth

TOMTOM_ROUTE_BASE = "https://api.tomtom.com/routing/1/calculateRoute"
MAILJET_SEND_URL = "https://api.mailjet.com/v3.1/send"


def tomtom_route_summary(api_key: str, origin: Tuple[float, float], dest: Tuple[float, float]) -> Dict[str, Any]:
    route_locs = f"{origin[0]},{origin[1]}:{dest[0]},{dest[1]}"
    url = f"{TOMTOM_ROUTE_BASE}/{route_locs}/json"

    params = {
        "key": api_key,
        "traffic": "true",
        "computeTravelTimeFor": "all",
        "routeRepresentation": "polyline",
    }

    r = requests.get(url, params=params, timeout=25)
    r.raise_for_status()
    data = r.json()

    routes = data.get("routes") or []
    if not routes:
        return {"ok": False, "reason": "No routes returned", "raw": data}

    summary = routes[0].get("summary") or {}
    travel = int(summary.get("travelTimeInSeconds", 0))
    no_traffic = int(summary.get("noTrafficTravelTimeInSeconds", 0))
    delay = int(summary.get("trafficDelayInSeconds", max(0, travel - (no_traffic or travel))))

    if travel <= 0:
        return {"ok": False, "reason": "Invalid travel time in response", "raw": data}
    if no_traffic <= 0:
        no_traffic = travel

    return {
        "ok": True,
        "travel_sec": travel,
        "no_traffic_sec": no_traffic,
        "delay_sec": max(0, delay),
    }


def mailjet_send(api_key: str, api_secret: str, email_from: str, email_to: str, subject: str, text: str) -> None:
    payload = {
        "Messages": [
            {
                "From": {"Email": email_from, "Name": "Commute Alert"},
                "To": [{"Email": email_to}],
                "Subject": subject,
                "TextPart": text,
            }
        ]
    }

    r = requests.post(
        MAILJET_SEND_URL,
        auth=HTTPBasicAuth(api_key, api_secret),
        headers={"Content-Type": "application/json"},
        data=json.dumps(payload),
        timeout=25,
    )
    if r.status_code >= 400:
        raise RuntimeError(f"Mailjet error {r.status_code}: {r.text}")


def already_alerted_today(state_dir: str, today_key: str) -> bool:
    os.makedirs(state_dir, exist_ok=True)
    path = os.path.join(state_dir, "last_alert_date.txt")
    if not os.path.exists(path):
        return False
    with open(path, "r", encoding="utf-8") as f:
        return f.read().strip() == today_key


def mark_alerted_today(state_dir: str, today_key: str) -> None:
    os.makedirs(state_dir, exist_ok=True)
    path = os.path.join(state_dir, "last_alert_date.txt")
    with open(path, "w", encoding="utf-8") as f:
        f.write(today_key)


def main() -> int:
    required = [
        "TOMTOM_API_KEY",
        "COMMUTE_ORIGIN_LAT", "COMMUTE_ORIGIN_LNG",
        "COMMUTE_DEST_LAT", "COMMUTE_DEST_LNG",
    ]
    missing = [k for k in required if not os.getenv(k)]
    if missing:
        print(f"Missing required env vars: {', '.join(missing)}")
        return 2

    origin = (float(os.environ["COMMUTE_ORIGIN_LAT"]), float(os.environ["COMMUTE_ORIGIN_LNG"]))
    dest = (float(os.environ["COMMUTE_DEST_LAT"]), float(os.environ["COMMUTE_DEST_LNG"]))

    delay_thresh_min = int(os.getenv("DELAY_THRESHOLD_MIN", "15"))
    delay_thresh_pct = float(os.getenv("DELAY_THRESHOLD_PCT", "30"))

    tt = tomtom_route_summary(os.environ["TOMTOM_API_KEY"], origin, dest)
    if not tt["ok"]:
        print(f"⚠️ TomTom routing failed: {tt.get('reason')}")
        return 0

    travel = tt["travel_sec"]
    no_traffic = tt["no_traffic_sec"]
    delay = tt["delay_sec"]

    travel_min = math.ceil(travel / 60)
    no_traffic_min = math.ceil(no_traffic / 60)
    delay_min = math.ceil(delay / 60) if delay > 0 else 0
    delay_pct = (delay / no_traffic) * 100.0 if no_traffic else 0.0

    print(
        f"⏱️ Commute (TomTom)\n"
        f"- ETA with traffic: {travel_min} min\n"
        f"- ETA no traffic:  {no_traffic_min} min\n"
        f"- Delay:           {delay_min} min ({delay_pct:.0f}%)"
    )

    is_bad = (delay_min >= delay_thresh_min) or (delay_pct >= delay_thresh_pct)
    if not is_bad:
        print("✅ No significant delay.")
        return 0

    print(f"🚧 ALERT: Delay exceeds threshold (>= {delay_thresh_min} min OR >= {delay_thresh_pct:.0f}%).")

    # daily dedupe (important because we run twice around DST)
    today_key = datetime.now().strftime("%Y-%m-%d")
    state_dir = os.getenv("STATE_DIR", ".state")
    if already_alerted_today(state_dir, today_key):
        print("ℹ️ Alert already sent today; skipping email to avoid duplicates.")
        return 0

    # Mailjet creds (optional until you add secrets)
    mj_pub = os.getenv("MJ_APIKEY_PUBLIC")
    mj_priv = os.getenv("MJ_APIKEY_PRIVATE")
    email_to = os.getenv("EMAIL_TO")
    email_from = os.getenv("EMAIL_FROM")

    if not (mj_pub and mj_priv and email_to and email_from):
        print("ℹ️ Mailjet secrets not set (MJ_APIKEY_PUBLIC/MJ_APIKEY_PRIVATE/EMAIL_TO/EMAIL_FROM). Not sending email yet.")
        return 0

    subject = "Commute Alert: Heavy delay detected"
    body = (
        f"Commute delay exceeded threshold.\n\n"
        f"ETA with traffic: {travel_min} min\n"
        f"ETA no traffic:  {no_traffic_min} min\n"
        f"Delay:           {delay_min} min ({delay_pct:.0f}%)\n"
    )

    mailjet_send(mj_pub, mj_priv, email_from, email_to, subject, body)
    mark_alerted_today(state_dir, today_key)
    print("📧 Mailjet email alert sent.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
