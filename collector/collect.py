import os, json, datetime, csv
import requests

# ===== 설정 =====
TBASE_C = float(os.getenv("TBASE_C", "5"))
H_BUD = float(os.getenv("H_BUD", "80"))
H_BLOOM = float(os.getenv("H_BLOOM", "180"))
GDD_START_MMDD = "02-15"

CHILL_SEASON_START_MMDD = "11-01"
CHILL_SEASON_END_MMDD = "02-14"
CHILL_TMIN = 2.0
CHILL_TMAX = 10.0
CHILL_TARGET_DAYS = float(os.getenv("CHILL_TARGET_DAYS", "100"))
CHILL_70 = 70.0
CHILL_90 = 90.0

KST = datetime.timezone(datetime.timedelta(hours=9))

def f_to_c(f):
    return (float(f) - 32.0) * 5.0 / 9.0

def safe_float(x):
    try:
        return float(x)
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

def fetch_history_temp_ch1(start_dt_kst, end_dt_kst):
    """2동(temp_and_humidity_ch1.temperature) 히스토리 가져오기.
       반환: list of (epoch_int, temp_c_float)
    """
    api_key = os.environ["ECOWITT_API_KEY"]
    app_key = os.environ["ECOWITT_APPLICATION_KEY"]
    mac = os.environ["ECOWITT_MAC"]

    start = start_dt_kst.strftime("%Y-%m-%d %H:%M:%S")
    end = end_dt_kst.strftime("%Y-%m-%d %H:%M:%S")

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
    r.raise_for_status()
    j = r.json()
    if j.get("code") != 0:
        return []

    # 구조: data.temp_and_humidity_ch1.temperature.unit="°F"
    #       data.temp_and_humidity_ch1.temperature.list = { "epoch": "F", ... }
    lst = get_path(j, ["data", "temp_and_humidity_ch1", "temperature", "list"])
    if not isinstance(lst, dict):
        return []

    out = []
    for k, v in lst.items():
        try:
            epoch = int(k)
            temp_f = float(v)
            out.append((epoch, f_to_c(temp_f)))
        except:
            pass
    out.sort(key=lambda x: x[0])
    return out

def upsert_daily_rows_from_history(rows_by_date, hist_points):
    """hist_points: list of (epoch, temp_c). KST 날짜로 묶어서 tmin/tmax 갱신."""
    for epoch, tc in hist_points:
        dt = datetime.datetime.fromtimestamp(epoch, tz=KST)
        dkey = ymd(dt.date())
        row = rows_by_date.get(dkey)
        if row is None:
            row = {}
            rows_by_date[dkey] = row
            row["date"] = dkey
            row["tmin_c"] = ""
            row["tmax_c"] = ""
            row["tmean_c"] = ""
            row["gdd"] = ""
            row["gdd_cum_from_0215"] = ""
            row["chillday"] = ""
            row["chill_cum"] = ""

        tmin = safe_float(row.get("tmin_c"))
        tmax = safe_float(row.get("tmax_c"))
        tmin = tc if tmin is None else min(tmin, tc)
        tmax = tc if tmax is None else max(tmax, tc)
        row["tmin_c"] = f"{tmin:.2f}"
        row["tmax_c"] = f"{tmax:.2f}"

def recompute_gdd_chill(rows_by_date):
    """daily.csv 전체를 날짜순으로 돌며 tmean/gdd/누적gdd/칠링 누적 재계산."""
    all_dates = sorted(rows_by_date.keys())
    gdd_cum = 0.0
    chill_cum = 0.0

    for dstr in all_dates:
        d = datetime.datetime.strptime(dstr, "%Y-%m-%d").date()
        r = rows_by_date[dstr]
        tmin = safe_float(r.get("tmin_c"))
        tmax = safe_float(r.get("tmax_c"))
        if tmin is None or tmax is None:
            continue

        tmean = (tmin + tmax) / 2.0
        r["tmean_c"] = f"{tmean:.2f}"

        gdd = max(0.0, tmean - TBASE_C)
        r["gdd"] = f"{gdd:.2f}"
        if is_gdd_season(d):
            gdd_cum += gdd
        r["gdd_cum_from_0215"] = f"{gdd_cum:.2f}"

        chillday = 0.0
        if in_chill_season(d) and (CHILL_TMIN <= tmean <= CHILL_TMAX):
            chillday = 1.0
        chill_cum += chillday
        r["chillday"] = f"{chillday:.0f}"
        r["chill_cum"] = f"{chill_cum:.0f}"

    return all_dates

def pick_threshold_dates(rows_by_date, all_dates):
    bud_date = None
    bloom_date = None
    for dstr in all_dates:
        val = safe_float(rows_by_date[dstr].get("gdd_cum_from_0215"))
        if val is None:
            continue
        if bud_date is None and val >= H_BUD:
            bud_date = dstr
        if bloom_date is None and val >= H_BLOOM:
            bloom_date = dstr

    # chill percent and 70/90% date
    chill_cum_last = 0.0
    if all_dates:
        last = all_dates[-1]
        chill_cum_last = safe_float(rows_by_date[last].get("chill_cum")) or 0.0

    chill_pct = None
    if CHILL_TARGET_DAYS > 0:
        chill_pct = min(100.0, (chill_cum_last / CHILL_TARGET_DAYS) * 100.0)

    target70 = CHILL_TARGET_DAYS * (CHILL_70 / 100.0)
    target90 = CHILL_TARGET_DAYS * (CHILL_90 / 100.0)

    date70 = None
    date90 = None
    for dstr in all_dates:
        c = safe_float(rows_by_date[dstr].get("chill_cum"))
        if c is None:
            continue
        if date70 is None and c >= target70:
            date70 = dstr
        if date90 is None and c >= target90:
            date90 = dstr

    return bud_date, bloom_date, chill_cum_last, chill_pct, date70, date90

def main():
    os.makedirs("data", exist_ok=True)

    # ===== 1) 실시간 가져오기 (현재값 표시용 + soil) =====
    raw = fetch_realtime()
    with open("data/raw_last.json", "w", encoding="utf-8") as f:
        json.dump(raw, f, ensure_ascii=False, indent=2)

    # 온도 매핑
    outdoor_f = get_path(raw, ["data", "indoor", "temperature", "value"])  # 외부(네가 쓰던 매핑)
    dong2_f = get_path(raw, ["data", "temp_and_humidity_ch1", "temperature", "value"])  # 2동
    dong3_f = get_path(raw, ["data", "temp_and_humidity_ch2", "temperature", "value"])  # 3동

    outdoor_c = f_to_c(outdoor_f) if outdoor_f is not None else None
    dong2_c = f_to_c(dong2_f) if dong2_f is not None else None
    dong3_c = f_to_c(dong3_f) if dong3_f is not None else None

    soils = {}
    for ch in range(1, 7):
        k = f"soil_ch{ch}"
        v = get_path(raw, ["data", k, "soilmoisture", "value"])
        if v is not None:
            soils[k] = safe_float(v)

    # ===== 2) daily.csv 로드 =====
    daily_path = "data/daily.csv"
    fieldnames = ["date", "tmin_c", "tmax_c", "tmean_c", "gdd", "gdd_cum_from_0215", "chillday", "chill_cum"]

    rows = {}
    if os.path.exists(daily_path):
        with open(daily_path, "r", encoding="utf-8") as f:
            reader = csv.DictReader(f)
            for r in reader:
                if r.get("date"):
                    rows[r["date"]] = r

    # ===== 3) 히스토리로 어제/오늘 보정(2동 기준) =====
    now_kst = datetime.datetime.now(KST)
    start_kst = (now_kst - datetime.timedelta(days=2)).replace(hour=0, minute=0, second=0, microsecond=0)
    end_kst = now_kst

    hist = fetch_history_temp_ch1(start_kst, end_kst)
    upsert_daily_rows_from_history(rows, hist)

    # 그래도 히스토리에서 아무것도 못 받았을 때를 대비해, 현재값 1점이라도 반영(2동 기준)
    today_kst = now_kst.date()
    t_for_fallback = dong2_c  # 2동 기준 확정
    if t_for_fallback is not None:
        dkey = ymd(today_kst)
        if dkey not in rows:
            rows[dkey] = {fn: "" for fn in fieldnames}
            rows[dkey]["date"] = dkey
            rows[dkey]["tmin_c"] = f"{t_for_fallback:.2f}"
            rows[dkey]["tmax_c"] = f"{t_for_fallback:.2f}"
        else:
            tmin = safe_float(rows[dkey].get("tmin_c"))
            tmax = safe_float(rows[dkey].get("tmax_c"))
            tmin = t_for_fallback if tmin is None else min(tmin, t_for_fallback)
            tmax = t_for_fallback if tmax is None else max(tmax, t_for_fallback)
            rows[dkey]["tmin_c"] = f"{tmin:.2f}"
            rows[dkey]["tmax_c"] = f"{tmax:.2f}"

    # ===== 4) GDD/칠링 전체 재계산 =====
    all_dates = recompute_gdd_chill(rows)

    # ===== 5) 저장 =====
    with open(daily_path, "w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=fieldnames)
        w.writeheader()
        for dstr in all_dates:
            w.writerow({k: rows[dstr].get(k, "") for k in fieldnames})

    bud_date, bloom_date, chill_cum_last, chill_pct, date70, date90 = pick_threshold_dates(rows, all_dates)

    # 최신 누적 gdd
    gdd_cum_last = None
    if all_dates:
        gdd_cum_last = rows[all_dates[-1]].get("gdd_cum_from_0215")

    status = {
        "updated_utc": datetime.datetime.utcnow().replace(tzinfo=datetime.timezone.utc).isoformat(),
        "now": {
            "외부_c": None if outdoor_c is None else round(outdoor_c, 2),
            "2동_c": None if dong2_c is None else round(dong2_c, 2),
            "3동_c": None if dong3_c is None else round(dong3_c, 2),
            "soil": soils,
        },
        "gdd": {
            "tbase_c": TBASE_C,
            "start_mmdd": GDD_START_MMDD,
            "cum_from_0215": gdd_cum_last,
            "H_BUD": H_BUD,
            "H_BLOOM": H_BLOOM,
            "pred_bud10_date": bud_date,
            "pred_bloom50_date": bloom_date,
            "basis": "2동(tmin/tmax) 기반, 히스토리로 어제/오늘 보정"
        },
        "chill": {
            "season": f"{CHILL_SEASON_START_MMDD}~{CHILL_SEASON_END_MMDD}",
            "rule": "ChillDay=1 if daily mean 2~10C else 0",
            "target_days": CHILL_TARGET_DAYS,
            "cum": chill_cum_last,
            "pct": None if chill_pct is None else round(chill_pct, 1),
            "date_70": date70,
            "date_90": date90,
            "basis": "2동(tmin/tmax) 기반"
        }
    }

    with open("data/status.json", "w", encoding="utf-8") as f:
        json.dump(status, f, ensure_ascii=False, indent=2)

if __name__ == "__main__":
    main()
