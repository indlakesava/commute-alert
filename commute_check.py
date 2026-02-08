import os
import math
import requests
from typing import Any, Dict, Tuple

TOMTOM_ROUTE_BASE = "https://api.tomtom.com/routing/1/calculateRoute"


def tomtom_route_summary(api_key: str, origin: Tuple[float, float], dest: Tuple[float, float]) -> Dict[str, Any]:
    route_locs = f"{origin[0]},{origin[1]}:{dest[0]},{dest[1]}"
    url = f"{TOMTOM_ROUTE_BASE}/{route_locs}/json"

    params = {
        "key": api_key,
        "traffic": "true",
        "computeTravelTimeFor": "all",  # enables the richer travel time fields
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
    # Some responses provide trafficDelayInSeconds; otherwise compute it.
    delay = int(summary.get("trafficDelayInSeconds", max(0, travel - (no_traffic or travel))))

    if travel <= 0:
        return {"ok": False, "reason": "Invalid travel time in response", "raw": data}

    if no_traffic <= 0:
        no_traffic = travel  # fallback

    return {
        "ok": True,
        "travel_sec": travel,
        "no_traffic_sec": no_traffic,
        "delay_sec": max(0, delay),
        "raw_summary": summary,
    }


def main() -> int:
    api_key = os.environ["TOMTOM_API_KEY"]
    origin = (float(os.environ["COMMUTE_ORIGIN_LAT"]), float(os.environ["COMMUTE_ORIGIN_LNG"]))
    dest = (float(os.environ["COMMUTE_DEST_LAT"]), float(os.environ["COMMUTE_DEST_LNG"]))

    delay_thresh_min = int(os.getenv("DELAY_THRESHOLD_MIN", "15"))
    delay_thresh_pct = float(os.getenv("DELAY_THRESHOLD_PCT", "30"))

    tt = tomtom_route_summary(api_key, origin, dest)
    if not tt["ok"]:
        print(f"⚠️ TomTom routing failed: {tt.get('reason')}")
        return 0

    travel = tt["travel_sec"]
    no_traffic = tt["no_traffic_sec"]
    delay = tt["delay_sec"]

    travel_min = math.ceil(travel / 60)
    no_traffic_min = math.ceil(no_traffic / 60)
    delay_min = math.ceil(delay / 60)

    delay_pct = (delay / no_traffic) * 100.0 if no_traffic else 0.0

    print(
        f"⏱️ Commute (TomTom)\n"
        f"- ETA with traffic: {travel_min} min\n"
        f"- ETA no traffic:  {no_traffic_min} min\n"
        f"- Delay:           {delay_min} min ({delay_pct:.0f}%)"
    )

    is_bad = (delay_min >= delay_thresh_min) or (delay_pct >= delay_thresh_pct)

    if is_bad:
        print(f"🚧 ALERT: Delay exceeds threshold (>= {delay_thresh_min} min OR >= {delay_thresh_pct:.0f}%).")
    else:
        print("✅ No significant delay.")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
