# collector/history_test.py
import os, json, datetime
import requests

API_BASE = "https://api.ecowitt.net/api/v3/device/"

def kst_range(days=2):
    KST = datetime.timezone(datetime.timedelta(hours=9))
    now = datetime.datetime.now(KST)
    start = (now - datetime.timedelta(days=days)).strftime("%Y-%m-%d %H:%M:%S")
    end = now.strftime("%Y-%m-%d %H:%M:%S")
    return start, end

def fetch_realtime(api_key, app_key, mac):
    url = API_BASE + "real_time"
    params = {
        "application_key": app_key,
        "api_key": api_key,
        "mac": mac,
        "call_back": "all",
    }
    r = requests.get(url, params=params, timeout=30)
    r.raise_for_status()
    return r.json()

def try_history(api_key, app_key, mac, call_back, start_date, end_date):
    url = API_BASE + "history"
    params = {
        "application_key": app_key,
        "api_key": api_key,
        "mac": mac,
        "start_date": start_date,
        "end_date": end_date,
        "call_back": call_back,
    }
    r = requests.get(url, params=params, timeout=60)
    # Ecowitt는 HTTP 200이어도 code로 에러를 주므로 json을 봐야 함
    try:
        j = r.json()
    except Exception:
        j = {"not_json": r.text}
    return r.status_code, r.url, j

def main():
    os.makedirs("data", exist_ok=True)

    api_key = os.environ["ECOWITT_API_KEY"]
    app_key = os.environ["ECOWITT_APPLICATION_KEY"]
    mac = os.environ["ECOWITT_MAC"]

    start_date, end_date = kst_range(days=2)

    # 1) real_time에서 사용 가능한 sensor key를 보고, history call_back 후보를 만든다
    rt = fetch_realtime(api_key, app_key, mac)
    with open("data/raw_last.json", "w", encoding="utf-8") as f:
        json.dump(rt, f, ensure_ascii=False, indent=2)

    data = rt.get("data", {})
    sensor_keys = list(data.keys())

    # 우리가 필요한 건 2동= temp_and_humidity_ch1.temperature
    # 먼저 이 후보를 최우선으로, 그 다음 온도 관련 후보를 쭉 시도
    candidates = []
    candidates.append("temp_and_humidity_ch1.temperature")
    candidates.append("temp_and_humidity_ch1")
    candidates.append("indoor.temperature")
    candidates.append("indoor")
    # 추가 후보 자동 생성(temperature 있는 것 위주)
    for k in sensor_keys:
        if "temp" in k or "indoor" in k or "outdoor" in k:
            candidates.append(f"{k}.temperature")
            candidates.append(k)

    # 중복 제거
    seen = set()
    candidates = [c for c in candidates if not (c in seen or seen.add(c))]

    debug = {
        "start_date": start_date,
        "end_date": end_date,
        "candidates": candidates,
        "results": []
    }

    # 2) 후보를 하나씩 history에 던져보고, data가 비지 않으면 성공으로 간주
    for cb in candidates[:20]:  # 너무 많이 때리면 제한 걸릴 수 있어서 20개만
        status, url, j = try_history(api_key, app_key, mac, cb, start_date, end_date)
        code = j.get("code")
        msg = j.get("msg")
        d = j.get("data")
        ok = isinstance(d, list) and len(d) > 0
        debug["results"].append({
            "call_back": cb,
            "http_status": status,
            "url": url,
            "code": code,
            "msg": msg,
            "data_len": len(d) if isinstance(d, list) else None,
            "ok": ok
        })
        if ok:
            # 성공한 응답을 저장하고 종료
            with open("data/history_ok.json", "w", encoding="utf-8") as f:
                json.dump(j, f, ensure_ascii=False, indent=2)
            debug["first_ok"] = cb
            break

    with open("data/history_debug.json", "w", encoding="utf-8") as f:
        json.dump(debug, f, ensure_ascii=False, indent=2)

if __name__ == "__main__":
    main()
