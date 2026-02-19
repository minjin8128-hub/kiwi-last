# collector/history_test.py
import os, json, datetime
import requests

def main():
    os.makedirs("data", exist_ok=True)

    api_key = os.environ["ECOWITT_API_KEY"]
    app_key = os.environ["ECOWITT_APPLICATION_KEY"]
    mac = os.environ["ECOWITT_MAC"]

    KST = datetime.timezone(datetime.timedelta(hours=9))
    now = datetime.datetime.now(KST)
    start = (now - datetime.timedelta(days=2)).strftime("%Y-%m-%d %H:%M:%S")
    end = now.strftime("%Y-%m-%d %H:%M:%S")

    url = "https://api.ecowitt.net/api/v3/device/history"
    params = {
        "application_key": app_key,
        "api_key": api_key,
        "mac": mac,
        "start_date": start,
        "end_date": end,
        "call_back": "temp_and_humidity_ch1.temperature",
    }

    r = requests.get(url, params=params, timeout=60)
    try:
        j = r.json()
    except Exception:
        j = {"not_json": r.text}

    out = {
        "http_status": r.status_code,
        "url": r.url,
        "json": j
    }

    with open("data/history_ok.json", "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=2)

if __name__ == "__main__":
    main()
