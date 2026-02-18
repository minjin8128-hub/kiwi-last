import os, json, datetime
import requests

def main():
    api_key = os.environ["ECOWITT_API_KEY"]
    app_key = os.environ["ECOWITT_APPLICATION_KEY"]
    mac = os.environ["ECOWITT_MAC"]

    url = (
        "https://api.ecowitt.net/api/v3/device/real_time"
        f"?application_key={app_key}&api_key={api_key}&mac={mac}&call_back=all"
    )

    r = requests.get(url, timeout=30)
    r.raise_for_status()
    raw = r.json()

    status = {
        "updated_utc": datetime.datetime.now(datetime.timezone.utc).isoformat(),
        "ecowitt_raw_top_keys": list(raw.keys()),
        "note": "연결 테스트 OK면 다음 단계에서 온도/토양수분 필드를 뽑아 daily/status 계산을 붙임"
    }

    os.makedirs("data", exist_ok=True)
    with open("data/status.json", "w", encoding="utf-8") as f:
        json.dump(status, f, ensure_ascii=False, indent=2)

if __name__ == "__main__":
    main()
