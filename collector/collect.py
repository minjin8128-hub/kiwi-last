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

    os.makedirs("data", exist_ok=True)

    # raw 원본 저장 (이 파일이 꼭 생겨야 함)
    with open("data/raw_last.json", "w", encoding="utf-8") as f:
        json.dump(raw, f, ensure_ascii=False, indent=2)

    status = {
        "updated_utc": datetime.datetime.now(datetime.timezone.utc).isoformat(),
        "ecowitt_raw_top_keys": list(raw.keys()),
        "note": "raw_last.json 생성 확인용"
    }
    with open("data/status.json", "w", encoding="utf-8") as f:
        json.dump(status, f, ensure_ascii=False, indent=2)

if __name__ == "__main__":
    main()
