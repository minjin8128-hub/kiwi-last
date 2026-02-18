import os, json, datetime, csv
import requests

# ===== 조정값(나중에 바꾸면 됨) =====
TBASE_C = float(os.getenv("TBASE_C", "5"))      # GDD 기준온도(섭씨)
H_BUD = float(os.getenv("H_BUD", "80"))         # 발아10% 임계 누적GDD(임시값)
H_BLOOM = float(os.getenv("H_BLOOM", "180"))    # 만개50% 임계 누적GDD(임시값)
GDD_START_MMDD = "02-15"                        # 적산 시작일(월-일)

CHILL_SEASON_START_MMDD = "11-01"
CHILL_SEASON_END_MMDD = "02-14"
CHILL_TMIN = 2.0
CHILL_TMAX = 10.0
CHILL_TARGET_DAYS = float(os.getenv("CHILL_TARGET_DAYS", "100"))  # 목표 ChillDay(임시값)
CHILL_70 = 70.0
CHILL_90 = 90.0

def f_to_c(f):
    return (float(f) - 32.0) * 5.0 / 9.0

def parse_float(v):
    try:
        return float(v)
    except:
        return None

def get_path(d, path):
    cur = d
    for p in path:
        if not isinstance(cur, dict) or p not in cur:
            return None
        cur = cur[p]
    return cur

def ymd(date_obj):
    return date_obj.strftime("%Y-%m-%d")

def mmdd(date_obj):
    return date_obj.strftime("%m-%d")

def in_chill_season(date_obj):
    # 11/1~12/31 또는 1/1~2/14
    s = mmdd(date_obj)
    return (s >= CHILL_SEASON_START_MMDD) or (s <= CHILL_SEASON_END_MMDD)

def is_gdd_season(date_obj):
    return mmdd(date_obj) >= GDD_START_MMDD

def fetch_realtime():
    api_key = os.environ["ECOWITT_API_KEY"]
    app_key = os.environ["ECOWITT_APPLICATION_KEY"]
    mac = os.environ["ECOWITT_MAC"]

    url = (
        "https://api.ecowitt.net/api/v3/device/real_time"
        f"?application_key={app_key}&api_key={api_key}&mac={mac}&call_back=all"
    )
    r = requests.get(url, timeout=30)
    r.raise_for_status()
    return r.json()

def main():
    raw = fetch_realtime()

    os.makedirs("data", exist_ok=True)
    with open("data/raw_last.json", "w", encoding="utf-8") as f:
        json.dump(raw, f, ensure_ascii=False, indent=2)

    # ===== 센서 매핑(농장 기준) =====
    # Ecowitt JSON 구조 참고: data.indoor, data.temp_and_humidity_ch1 등 [web:2]
    # indoor = 외부(실외)
    outdoor_f = get_path(raw, ["data", "indoor", "temperature", "value"])
    # ch1 = 2동
    dong2_f = get_path(raw, ["data", "temp_and_humidity_ch1", "temperature", "value"])
    # ch2 = 3동
    dong3_f = get_path(raw, ["data", "temp_and_humidity_ch2", "temperature", "value"])

    outdoor_c = f_to_c(outdoor_f) if outdoor_f is not None else None
    dong2_c = f_to_c(dong2_f) if dong2_f is not None else None
    dong3_c = f_to_c(dong3_f) if dong3_f is not None else None

    # 토양수분 채널(soil_ch1~6)
    soils = {}
    for ch in range(1, 7):
        key = f"soil_ch{ch}"
        v = get_path(raw, ["data", key, "soilmoisture", "value"])
        if v is not None:
            soils[key] = parse_float(v)

    # ===== daily.csv (외부 기준으로 tmin/tmax 누적) =====
    today = datetime.datetime.now().date()  # 러너 시간대 영향 있음(나중에 KST로 고정 가능)
    daily_path = "data/daily.csv"
    fieldnames = ["date", "tmin_c", "tmax_c", "tmean_c", "gdd", "gdd_cum_from_0215", "chillday", "chill_cum"]

    rows = {}
    if os.path.exists(daily_path):
        with open(daily_path, "r", encoding="utf-8") as f:
            reader = csv.DictReader(f)
            for r in reader:
                rows[r["date"]] = r

    if outdoor_c is not None:
        key = ymd(today)
        if key not in rows:
            rows[key] = {
                "date": key,
                "tmin_c": str(outdoor_c),
                "tmax_c": str(outdoor_c),
                "tmean_c": "",
                "gdd": "",
                "gdd_cum_from_0215": "",
                "chillday": "",
                "chill_cum": "",
            }
        else:
            tmin = parse_float(rows[key].get("tmin_c"))
            tmax = parse_float(rows[key].get("tmax_c"))
            tmin = outdoor_c if tmin is None else min(tmin, outdoor_c)
            tmax = outdoor_c if tmax is None else max(tmax, outdoor_c)
            rows[key]["tmin_c"] = str(tmin)
            rows[key]["tmax_c"] = str(tmax)

    # 날짜순 계산(누적 GDD/칠링)
    all_dates = sorted(rows.keys())
    gdd_cum = 0.0
    chill_cum = 0.0

    for dstr in all_dates:
        d = datetime.datetime.strptime(dstr, "%Y-%m-%d").date()
        tmin = parse_float(rows[dstr].get("tmin_c"))
        tmax = parse_float(rows[dstr].get("tmax_c"))
        if tmin is None or tmax is None:
            continue

        tmean = (tmin + tmax) / 2.0
        rows[dstr]["tmean_c"] = f"{tmean:.2f}"

        # GDD: max(0, Tmean - Tbase) [web:60]
        gdd = max(0.0, tmean - TBASE_C)
        rows[dstr]["gdd"] = f"{gdd:.2f}"
        if is_gdd_season(d):
            gdd_cum += gdd
        rows[dstr]["gdd_cum_from_0215"] = f"{gdd_cum:.2f}"

        # ChillDay: 일평균 2~10C면 1점
        chillday = 0.0
        if in_chill_season(d) and (CHILL_TMIN <= tmean <= CHILL_TMAX):
            chillday = 1.0
        chill_cum += chillday
        rows[dstr]["chillday"] = f"{chillday:.0f}"
        rows[dstr]["chill_cum"] = f"{chill_cum:.0f}"

    # daily.csv 저장
    with open(daily_path, "w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=fieldnames)
        w.writeheader()
        for dstr in all_dates:
            w.writerow({k: rows[dstr].get(k, "") for k in fieldnames})

    # 예측일: 임계 누적GDD 도달 첫 날짜
    bud_date = None
    bloom_date = None
    for dstr in all_dates:
        val = parse_float(rows[dstr].get("gdd_cum_from_0215"))
        if val is None:
            continue
        if bud_date is None and val >= H_BUD:
            bud_date = dstr
        if bloom_date is None and val >= H_BLOOM:
            bloom_date = dstr

    # 칠링 % 및 70/90 날짜
    chill_pct = None
    if CHILL_TARGET_DAYS > 0:
        chill_pct = min(100.0, (chill_cum / CHILL_TARGET_DAYS) * 100.0)

    target70 = CHILL_TARGET_DAYS * (CHILL_70 / 100.0)
    target90 = CHILL_TARGET_DAYS * (CHILL_90 / 100.0)
    chill70_date = None
    chill90_date = None
    for dstr in all_dates:
        v = parse_float(rows[dstr].get("chill_cum"))
        if v is None:
            continue
        if chill70_date is None and v >= target70:
            chill70_date = dstr
        if chill90_date is None and v >= target90:
            chill90_date = dstr

    status = {
        "updated_utc": datetime.datetime.now(datetime.timezone.utc).isoformat(),
        "now": {
            "외부_c": None if outdoor_c is None else round(outdoor_c, 2),
            "2동_c": None if dong2_c is None else round(dong2_c, 2),
            "3동_c": None if dong3_c is None else round(dong3_c, 2),
            "soil": soils,
        },
        "gdd": {
            "기준온도_tbase_c": TBASE_C,
            "시작일_mmdd": GDD_START_MMDD,
            "누적_gdd_0215": None if not all_dates else rows[all_dates[-1]].get("gdd_cum_from_0215"),
            "발아10_H_BUD": H_BUD,
            "만개50_H_BLOOM": H_BLOOM,
            "발아10_예측일": bud_date,
            "만개50_예측일": bloom_date,
        },
        "chill": {
            "시즌": f"{CHILL_SEASON_START_MMDD}~{CHILL_SEASON_END_MMDD}",
            "룰": "일평균 2~10C면 1점",
            "목표점수": CHILL_TARGET_DAYS,
            "누적": int(chill_cum),
            "진행률_pct": None if chill_pct is None else round(chill_pct, 1),
            "후반70_날짜": chill70_date,
            "거의끝90_날짜": chill90_date,
        },
        "note": "현재는 실시간으로 '오늘 tmin/tmax'를 누적하는 최소버전입니다. 다음 단계에서 history API로 누락일 자동 보정 붙이면 정확도가 올라갑니다."
    }

    with open("data/status.json", "w", encoding="utf-8") as f:
        json.dump(status, f, ensure_ascii=False, indent=2)

if __name__ == "__main__":
    main()
