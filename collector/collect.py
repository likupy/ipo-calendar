#!/usr/bin/env python3
"""공모주 캘린더 수집기: OpenDART 증권신고서(지분증권) -> data/ipo.json

사용:  DART_API_KEY=... python collector/collect.py
표준 라이브러리만 사용합니다 (holidays 패키지가 있으면 공휴일 계산에 활용).

확인된 사실(OpenDART 개발가이드):
  - 공시검색  GET https://opendart.fss.or.kr/api/list.json
  - 지분증권 증권신고서 GET https://opendart.fss.or.kr/api/estkRs.json
    (crtfc_key, corp_code, bgn_de, end_de). 응답 그룹: 일반사항(sbd 청약기일, pymd 납입기일),
    증권의종류(slprc 모집가액), 인수인정보(actsen 인수인구분, actnmn 인수인명).
    그룹이 'group[].title/list' 로 오든 키로 오든 rows() 가 둘 다 처리합니다.
DART 가 공개하지 않는 것: 환불일(납입기일로 대체), 상장일(추정). 아래 FLAGS 로 표시합니다.
"""
import json
import os
import re
import sys
import time
import urllib.parse
import urllib.request
import io
import zipfile
from datetime import date, datetime, timedelta, timezone

API = "https://opendart.fss.or.kr/api"
KST = timezone(timedelta(hours=9))
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT = os.path.join(ROOT, "docs", "data", "ipo.json")

LOOKBACK_DAYS = 75      # 신고서를 훑는 기간
KEEP_DAYS = 400         # 지난 종목을 json 에 남기는 기간
LISTING_GAP_BD = 4      # 상장일 추정: 환불일 + N 영업일 (샘플 8건 중 6건 4일, 나머지 3일)

# 증권사 이름 -> 캘린더 약칭
BROKER_MAP = [
    ("미래에셋", "미래"), ("한국투자", "한국"), ("NH", "NH"), ("엔에이치", "NH"), ("대신", "대신"),
    ("삼성", "삼성"), ("KB", "KB"), ("케이비", "KB"), ("신한", "신한"), ("하나", "하나"),
    ("IBK", "IBK"), ("아이비케이", "IBK"), ("유진", "유진"), ("신영", "신영"), ("키움", "키움"),
    ("한화", "한화"), ("SK", "SK"), ("에스케이", "SK"), ("DB", "DB"), ("디비", "DB"),
    ("현대차", "현대차"), ("iM", "iM"), ("아이엠", "iM"), ("상상인", "상상인"), ("교보", "교보"),
    ("유안타", "유안타"), ("BNK", "BNK"), ("LS", "LS"), ("메리츠", "메리츠"), ("다올", "다올"),
    ("부국", "부국"), ("이베스트", "이베스트"), ("하이", "하이"), ("흥국", "흥국"), ("케이프", "케이프"),
]


def log(*a):
    print(*a, file=sys.stderr)


# ---------------------------------------------------------------- HTTP
def call(path, **params):
    params["crtfc_key"] = os.environ["DART_API_KEY"]
    url = f"{API}/{path}?{urllib.parse.urlencode(params)}"
    last = None
    for i in range(3):
        try:
            with urllib.request.urlopen(url, timeout=30) as r:
                return json.loads(r.read().decode("utf-8"))
        except Exception as e:  # 네트워크 일시 오류는 재시도
            last = e
            time.sleep(2 * (i + 1))
    raise RuntimeError(f"{path} 호출 실패: {last}")


DEBUG = []
T38 = {}
DOC_BUDGET = [25]
LST_BUDGET = [40]
DEBUG_DUMP = False   # 원인 파악용(확인되면 False)   # 한 번 실행에서 본문을 내려받는 최대 횟수


def doc_text(rcept_no):
    """공시 원문(zip) -> 태그 제거한 텍스트. 실패하면 ''."""
    url = f"{API}/document.xml?" + urllib.parse.urlencode({"crtfc_key": os.environ["DART_API_KEY"], "rcept_no": rcept_no})
    try:
        with urllib.request.urlopen(url, timeout=40) as r:
            raw = r.read()
        z = zipfile.ZipFile(io.BytesIO(raw))
    except Exception as e:
        log("  원문 실패", rcept_no, e)
        return ""
    out = []
    for n in z.namelist():
        b = z.read(n)
        for enc in ("utf-8", "cp949"):
            try:
                t = b.decode(enc)
                break
            except Exception:
                t = ""
        out.append(re.sub(r"<[^>]+>", " ", t))
    return re.sub(r"\s+", " ", " ".join(out))


BAND_RE = re.compile(r"희망\s*공모(?:가|가액|가격)[^0-9]{0,60}?([\d,]{4,9})\s*원?\s*(?:~|∼|～|-|에서)\s*([\d,]{4,9})")


LST_RE = re.compile(r"상장\s*(?:예정)?\s*(?:일|기일)[^0-9]{0,40}(20\d{2})\s*[.\-년/]\s*(\d{1,2})\s*[.\-월/]\s*(\d{1,2})")


def listing_from_doc(text):
    for m in LST_RE.finditer(text):
        try:
            return date(int(m.group(1)), int(m.group(2)), int(m.group(3)))
        except ValueError:
            pass
    return None


def band_from_doc(text):
    for m in BAND_RE.finditer(text):
        lo, hi = int(m.group(1).replace(",", "")), int(m.group(2).replace(",", ""))
        if 100 <= lo <= hi:
            return [lo, hi]
    return None


# ---------------------------------------------------------------- 영업일
def _holidays():
    try:
        import holidays  # type: ignore
        h = holidays.country_holidays("KR", years=range(2025, 2030))
        out = set(h.keys())
    except Exception:
        out = set()
    # 증시 휴장일(공휴일이 아닌 것)
    for y in range(2025, 2030):
        out.add(date(y, 5, 1))
        out.add(date(y, 12, 31))
    return out


HOLIDAYS = _holidays()


def is_bd(d):
    return d.weekday() < 5 and d not in HOLIDAYS


def add_bd(d, n):
    while n > 0:
        d += timedelta(days=1)
        if is_bd(d):
            n -= 1
    return d


# ---------------------------------------------------------------- 파싱
DATE_FULL = re.compile(r"(20\d{2})\s*[.\-년/]\s*(\d{1,2})\s*[.\-월/]\s*(\d{1,2})")
MD = re.compile(r"(\d{1,2})\s*[.\-월/]\s*(\d{1,2})")


def parse_dates(text):
    """'2026년 11월 12일 ~ 2026년 11월 13일' 같은 문자열에서 날짜 목록."""
    if not text:
        return []
    found = [date(int(a), int(b), int(c)) for a, b, c in DATE_FULL.findall(text)]
    if len(found) == 1:
        rest = DATE_FULL.sub("", text, count=1)
        d = re.search(r"[~\-–]\s*(\d{1,2})\s*일", rest)
        if d and not MD.search(rest.split("~")[-1] if "~" in rest else rest):
            try:
                found.append(date(found[0].year, found[0].month, int(d.group(1))))
            except ValueError:
                pass
        m = MD.search(rest) if len(found) == 1 else None
        if m:
            try:
                found.append(date(found[0].year, int(m.group(1)), int(m.group(2))))
            except ValueError:
                pass
    return found


def parse_won(text):
    """문자열에서 100 이상인 금액만 추출 (원 단위 공모가)."""
    if not text:
        return []
    nums = [int(x.replace(",", "")) for x in re.findall(r"\d[\d,]*", text)]
    return [n for n in nums if n >= 100]


def short_broker(name):
    n = (name or "").replace("(주)", "").replace("㈜", "")
    for key, short in BROKER_MAP:
        if key.lower() in n.lower():
            return short
    n = re.sub(r"(투자증권|금융투자|증권|주식회사)", "", n).strip()
    return n or None


def iso(d):
    return d.isoformat() if d else None


# ---------------------------------------------------------------- DART 응답 정리
def rows(resp, *keys):
    """응답에서 그룹 하나를 꺼낸다. 그룹 키 이름이 다를 수 있어 여러 후보를 받는다."""
    for k in keys:
        if isinstance(resp.get(k), list):
            return resp[k]
    # 그룹 이름을 몰라도 'title' 이 있는 구조(group 배열) 대응
    for g in resp.get("group", []) or []:
        if g.get("title") in keys and isinstance(g.get("list"), list):
            return g["list"]
    return []


def latest(items):
    """접수번호가 가장 큰 (최신 정정) 행들만."""
    if not items:
        return []
    top = max(i.get("rcept_no", "") for i in items)
    return [i for i in items if i.get("rcept_no", "") == top]


def build_one(corp, flags_out):
    if "철회" in corp.get("report_nm", ""):
        return "WITHDRAWN"
    """list.json 의 한 회사 -> 캘린더 항목. 실패하면 None."""
    end = datetime.now(KST).strftime("%Y%m%d")
    bgn = (datetime.now(KST) - timedelta(days=LOOKBACK_DAYS)).strftime("%Y%m%d")
    r = call("estkRs.json", corp_code=corp["corp_code"], bgn_de=bgn, end_de=end)
    if r.get("status") != "000":
        log(f"  estkRs {corp['corp_name']}: {r.get('status')} {r.get('message')}")
        return None

    general = latest(rows(r, "general", "일반사항"))
    stock = latest(rows(r, "stock", "증권의종류"))
    under = latest(rows(r, "acqs", "인수인정보", "underwriters"))
    flags = []

    g = general[0] if general else {}
    sub = parse_dates(g.get("sbd"))
    if len(sub) == 1:
        sub = [sub[0], sub[0]]
    if not sub:
        flags.append("청약일 없음")
    pay = parse_dates(g.get("pymd"))

    # 가격: 확정(발행조건확정 정정) 이면 단일 값, 아니면 희망 밴드(두 값)
    prices = []
    for s in stock:
        prices += parse_won(s.get("slprc"))
    band = price = None
    if len(prices) >= 2:
        band = [min(prices), max(prices)]       # 값이 둘이면 밴드
    elif not prices:
        flags.append("가격 없음")

    doc_hits = []
    if band is None and DOC_BUDGET[0] > 0 and not corp.get("_have_band"):
        DOC_BUDGET[0] -= 1
        txt = doc_text(corp.get("_first") or corp["rcept_no"])
        band = band_from_doc(txt)
        if band is None and txt:
            flags.append("본문에서 희망밴드 못 찾음")

    # 확정 공모가·상장일: 38커뮤니케이션
    lst_doc = None
    i38 = info_38(T38, corp["corp_name"], iso(sub[0]) if sub else None)
    if i38["band"]:
        band = i38["band"]
    if i38["price"] is not None:
        price = i38["price"]
    if i38["listing"]:
        lst_doc = i38["listing"]

    brokers = []
    for u in under:
        if re.search(r"대표|공동|주관|주선|인수", u.get("actsen", "")):
            b = short_broker(u.get("actnmn"))
            if b and b not in brokers:
                brokers.append(b)
    if not brokers:
        flags.append("주관사 없음")

    refund = None
    if sub:
        refund = pay[0] if pay else add_bd(sub[1], 2)
        if not pay:
            flags.append("환불일=청약종료+2영업일 추정")
    listing = lst_doc
    listing_est = False

    item = {
        "id": corp["corp_code"],
        "name": re.sub(r"\(주\)|㈜", "", corp["corp_name"]).strip(),
        "brokers": brokers,
        "sub": [iso(sub[0]), iso(sub[1])] if sub else None,
        "refund": iso(refund),
        "listing": iso(listing),
        "listingEst": listing_est,
        "rcept_no": corp["rcept_no"],
        "flags": flags,
    }
    if "스팩" in item["name"] or "기업인수목적" in item["name"]:
        item["spac"] = True
        if price is None and band is None:
            price = 2000   # 스팩 공모가는 항상 2,000원
            flags[:] = [f for f in flags if not f.startswith("가격")]
    if band and band[0] == band[1]:
        band = None   # 스팩 등 밴드가 하나뿐이면 밴드로 보지 않는다
    if item.get("spac") and price is None and band is None:
        price = 2000
    if band:
        item["band"] = band
    if price is not None:
        item["price"] = price
        if band:
            item["tone"] = "up" if price >= band[1] else ("down" if price <= band[0] else None)
    return {k: v for k, v in item.items() if v is not None}


# ---------------------------------------------------------------- 후보 선정
def candidates():
    """최근 지분증권 증권신고서 중 '아직 상장 전 회사'(=IPO) 만."""
    end = datetime.now(KST).strftime("%Y%m%d")
    bgn = (datetime.now(KST) - timedelta(days=LOOKBACK_DAYS)).strftime("%Y%m%d")
    out, page = [], 1
    while True:
        r = call("list.json", bgn_de=bgn, end_de=end, pblntf_ty="C", pblntf_detail_ty="C001",
                 last_reprt_at="N", page_no=page, page_count=100)
        if r.get("status") == "013":  # 조회된 데이터 없음
            break
        if r.get("status") != "000":
            raise RuntimeError(f"list.json: {r.get('status')} {r.get('message')}")
        out += r.get("list", [])
        if page >= int(r.get("total_page", 1)):
            break
        page += 1
    by = {}
    for x in out:
        if x.get("stock_code", "").strip():
            # 이미 상장된 회사: 유상증자 등은 제외. 단, 38커뮤니케이션 공모청약 일정에 있으면 방금 상장한 공모주로 본다.
            k = name_key(x["corp_name"])
            if not re.search(r"(?<![가-힣A-Za-z0-9])" + k + r"(?:스팩|기업인수목적)?\s*20\d\d\.\d\d\.\d\d~", T38.get("sched", "")):
                continue
        c = by.setdefault(x["corp_code"], dict(x, _confirmed=False))
        if x["rcept_no"] >= c["rcept_no"]:
            c.update({k: x[k] for k in ("rcept_no", "report_nm", "rcept_dt")})
        c["_first"] = min(c.get("_first", x["rcept_no"]), x["rcept_no"])
        if "발행조건확정" in x.get("report_nm", ""):
            c["_confirmed"] = True
    return list(by.values())


# ---------------------------------------------------------------- 38커뮤니케이션 (확정공모가 · 상장일)
# DART 에는 확정 공모가(slprc 는 희망밴드 하단), 상장일이 없다. 38커뮤니케이션 공개 페이지에서 보완한다.
# 서버에서 열리는 것은 확인했고, 페이지 모양이 바뀌면 이 부분만 못 읽고 나머지는 그대로 동작한다.
URL38 = {"sched": "http://www.38.co.kr/html/fund/index.htm?o=k", "new": "http://www.38.co.kr/html/fund/index.htm?o=nw"}


def fetch_38():
    out = {}
    for k, url in URL38.items():
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
            with urllib.request.urlopen(req, timeout=20) as r:
                raw = r.read()
            try:
                body = raw.decode("utf-8")
            except Exception:
                body = raw.decode("cp949", "replace")
            body = body.replace("&nbsp;", " ")
            out[k] = re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", body))
        except Exception as e:
            log("38 실패", k, e)
            out[k] = ""
    return out


def name_key(name):
    n = re.sub(r"\(주\)|㈜", "", name)
    n = re.sub(r"(기업인수목적|스팩)$", "", n).strip()
    return re.escape(n)


def info_38(texts, name, sub_start):
    """-> {'price': int|None, 'band': [lo,hi]|None, 'listing': date|None}"""
    key = name_key(name)
    pre = r"(?<![가-힣A-Za-z0-9])"
    res = {"price": None, "band": None, "listing": None}
    m = re.search(pre + key + r"(?:스팩|기업인수목적)?\s*(20\d\d)\.(\d\d)\.(\d\d)~\d\d\.\d\d\s+(\S+)\s+(\S+)", texts.get("sched", ""))
    if m and (sub_start is None or sub_start[5:7] + sub_start[8:10] == m.group(2) + m.group(3)):
        if re.fullmatch(r"[\d,]+", m.group(4)):
            res["price"] = int(m.group(4).replace(",", ""))
        b = re.fullmatch(r"([\d,]+)~([\d,]+)", m.group(5))
        if b:
            res["band"] = [int(b.group(1).replace(",", "")), int(b.group(2).replace(",", ""))]
    m = re.search(pre + key + r"(?:스팩|기업인수목적)?\s*(20\d\d)/(\d\d)/(\d\d)", texts.get("new", ""))
    if m:
        try:
            res["listing"] = date(int(m.group(1)), int(m.group(2)), int(m.group(3)))
        except ValueError:
            pass
    return res


# ---------------------------------------------------------------- 메인
def probe_38():
    """38 페이지 구조 확인용(임시): 목록의 상세 링크와 상세 페이지 본문을 기록."""
    out = {}
    urls = {"sched": "http://www.38.co.kr/html/fund/index.htm?o=k", "sched2": "http://www.38.co.kr/html/fund/index.htm?o=k&page=2",
            "new": "http://www.38.co.kr/html/fund/index.htm?o=nw", "detail": "https://www.38.co.kr/html/fund/?o=v&no=2333&l=&page=1",
            "cal": "http://www.38.co.kr/html/fund/index.htm?o=c"}
    for k, url in urls.items():
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
            with urllib.request.urlopen(req, timeout=20) as r:
                raw = r.read()
            try:
                body = raw.decode("utf-8")
            except Exception:
                body = raw.decode("cp949", "replace")
            e = {"len": len(body)}
            e["links"] = [[h, re.sub(r"<[^>]+>|\s+", " ", t).strip()] for h, t in re.findall(r'<a[^>]+href="([^"]*o=v[^"]*)"[^>]*>(.*?)</a>', body, re.S)][:80]
            txt = re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", body.replace("&nbsp;", " ")))
            e["text"] = txt[:200] if k != "detail" else txt[:6000]
            i = body.find("상장일")
            e["html_listing"] = body[max(0, i - 600): i + 900] if i >= 0 and k == "detail" else None
            out[k] = e
        except Exception as ex:
            out[k] = {"error": str(ex)}
    return out


def main():
    if os.path.exists(os.path.join(ROOT, "collector", "PROBE")):
        d = json.load(open(OUT, encoding="utf-8")) if os.path.exists(OUT) else {"ipos": []}
        d["probe"] = probe_38()
        json.dump(d, open(OUT, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
        return
    if not os.environ.get("DART_API_KEY"):
        sys.exit("DART_API_KEY 환경변수가 없습니다.")
    old, old_doc = {}, {}
    if os.path.exists(OUT):
        try:
            old_doc = json.load(open(OUT, encoding="utf-8"))
            for p in old_doc.get("ipos", []):
                old[p["id"]] = p
        except Exception as e:
            log("기존 ipo.json 읽기 실패(무시):", e)

    if old_doc.get("v") != 2:   # 데이터 규칙을 바꿨을 때 한 번만: 이전 방식으로 들어간 상장일은 버린다
        for p in old.values():
            p.pop("listing", None)
            p.pop("listingEst", None)
        OLD_JSON = json.dumps(old, sort_keys=True, ensure_ascii=False)
    global T38
    T38 = fetch_38()
    cands = candidates()
    for c in cands:
        c['_have_band'] = bool(old.get(c['corp_code'], {}).get('band'))
        c['_have_listing'] = old.get(c['corp_code'], {}).get('listingEst') is False
    log(f"후보 {len(cands)}건")
    new, failed = {}, 0
    for c in cands:
        try:
            item = build_one(c, [])
        except Exception as e:
            log(f"  실패 {c['corp_name']}: {e}")
            item = None
        if item == "WITHDRAWN":
            old.pop(c["corp_code"], None)   # 철회된 종목은 캘린더에서 뺀다
            continue
        if item and item.get("sub"):
            o = old.get(item["id"])
            if o and o.get("band") and o["band"][0] != o["band"][1] and "band" not in item:   # 확정 공시에는 밴드가 없으니 이전 값 유지
                item["band"] = o["band"]
                if item.get("price") is not None:
                    b = item["band"]
                    item["tone"] = "up" if item["price"] >= b[1] else ("down" if item["price"] <= b[0] else None)
                    item = {k: v for k, v in item.items() if v is not None}
            new[item["id"]] = item
        elif item is None:
            failed += 1
        time.sleep(0.2)

    # 이번에 못 가져온 기존 항목은 유지 (지난 종목 + 일시 오류 대비)
    cutoff = (datetime.now(KST) - timedelta(days=KEEP_DAYS)).date().isoformat()
    merged = dict(old)
    merged.update(new)
    # 이번에 새로 못 읽은(이미 상장해 일정표에서 내려간) 종목은 상장일만 다시 확인하고, 이전 값의 오류를 바로잡는다
    for k, p in merged.items():
        if k in new:
            continue
        i = info_38(T38, p["name"], (p.get("sub") or [None])[0])
        if i["listing"]:
            p["listing"] = iso(i["listing"])
            p["listingEst"] = False
        if i["price"] is not None and p.get("price") is None:
            p["price"] = i["price"]
        if p.get("band") and p["band"][0] == p["band"][1]:
            p.pop("band")
            p.pop("tone", None)
    ipos = [p for p in merged.values() if (p.get("sub") or [p.get("listing") or "0"])[0] >= cutoff]
    ipos.sort(key=lambda p: (p.get("sub") or ["9999"])[0])

    if not ipos and failed:
        sys.exit("수집 결과가 비어 있어 기존 파일을 덮어쓰지 않습니다.")

    if old and old_doc.get("v") == 2 and "debug" not in old_doc and "probe" not in old_doc and OLD_JSON == json.dumps({p["id"]: p for p in ipos}, sort_keys=True, ensure_ascii=False):
        log("변경 없음 - 파일을 건드리지 않습니다.")
        return
    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    doc = {"v": 2, "updatedAt": datetime.now(KST).strftime("%Y-%m-%dT%H:%M"), "source": "OpenDART+38", "ipos": ipos}
    if DEBUG:
        doc["debug"] = DEBUG

    tmp = OUT + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(doc, f, ensure_ascii=False, indent=1)
    os.replace(tmp, OUT)
    flagged = [(p["name"], p["flags"]) for p in ipos if p.get("flags")]
    log(f"저장 {len(ipos)}건 -> {OUT}")
    for n, f in flagged:
        log(f"  ! {n}: {', '.join(f)}")


if __name__ == "__main__":
    main()
