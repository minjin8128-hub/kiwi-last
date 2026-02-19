# collector/history_test.py
import os, json, datetime
import requests

def main():
    api_key = os.environ["ECOWITT_API_KEY"]
    app_key = os.environ["ECOWITT_APPLICATION_KEY"]
    mac = os.environ["ECOWITT_MAC"]

    # KST 기준: 어제 00:00 ~ 오늘 23:59 (넉넉하게 2일 범위)
    KST = datetime.timezone(datetime.timedelta(hours=9))
    now = datetime.datetime.now(KST)
    start = (now - datetime.timedelta(days=2)).strftime("%Y-%m-%d %H:%M:%S")
    end = now.strftime("%Y-%m-%d %H:%M:%S")

    # 히스토리 URL 후보(일단 이 형태로 시도)
    url = "https://api.ecowitt.net/api/v3/device/history"
    params = {
        "application_key": app_key,
        "api_key": api_key,
        "mac": mac,
        "start_date": start,
        "end_date": end,
        "call_back": "all",
        # "cycle_type": "5min",  # 안 되면 지우고 재시도(다음 단계에서 확정)
    }

    r = requests.get(url, params=params, timeout=60)
    print("status", r.status_code)
    print(r.text[:3000])  # 로그에 일부 출력

    os.makedirs("data", exist_ok=True)
    with open("data/history_sample.json", "w", encoding="utf-8") as f:
        try:
            json.dump(r.json(), f, ensure_ascii=False, indent=2)
        except Exception:
            json.dump({"not_json": r.text}, f, ensure_ascii=False, indent=2)

if __name__ == "__main__":
    main()
