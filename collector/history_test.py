# collector/history_test.py
import os, json, datetime
import requests

def main():
    os.makedirs("data", exist_ok=True)

    # 실행됐다는 증거 파일(무조건 생성)
    with open("data/history_debug.txt", "w", encoding="utf-8") as f:
        f.write("history_test.py started\n")

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
        "call_back": "all",
    }

    r = requests.get(url, params=params, timeout=60)

    # 응답 저장(무조건)
    with open("data/history_debug.txt", "a", encoding="utf-8") as f:
        f.write(f"url={r.url}\n")
        f.write(f"status={r.status_code}\n")
        f.write(r.text[:5000] + "\n")

    # JSON이면 json으로도 저장
    out = {"status_code": r.status_code, "text": r.text[:10000]}
    try:
        out["json"] = r.json()
    except Exception:
        out["json"] = None

    with open("data/history_sample.json", "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=2)

if __name__ == "__main__":
    main()
