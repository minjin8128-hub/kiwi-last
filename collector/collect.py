# collector/collect.py
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

    # 1) 원본 JSON 저장(필드 확인용)
    os.makedirs("data", exist_ok=True)
    with open("data/raw_last.json", "w", encoding="utf-8") as f:
        json.dump(raw, f, ensure_ascii=False, indent=2)

    # 2) 상태 요약 JSON 저장(대시보드용)
    status = {
        "updated_utc": datetime.datetime.now(datetime.timezone.utc).isoformat(),
        "ecowitt_raw_top_keys": list(raw.keys()),
        "note": "raw_last.json에 원본을 저장했습니다. 여기서 온도/지온/토양수분 필드명을 확정한 뒤 daily.csv/GDD/칠링 계산을 붙입니다."
    }
    with open("data/status.json", "w", encoding="utf-8") as f:
        json.dump(status, f, ensure_ascii=False, indent=2)

if __name__ == "__main__":
    main()
