#!/usr/bin/env python3
"""공모주 캘린더 수집기

주 출처  : 38커뮤니케이션 종목별 상세 페이지 (청약일·환불일·상장일·확정공모가·희망밴드·주간사)
교차확인 : OpenDART 증권신고서(지분증권) (청약일·희망밴드·인수인). 서로 다르면 항목의 flags 에 남긴다.
추정값은 쓰지 않는다. 출처에 없으면 비워 둔다.

사용:  DART_API_KEY=... python collector/collect.py
"""
import json
import os
import re
import sys
import time
import urllib.parse
import urllib.request
from datetime import date, datetime, timedelta, timezone

API = "https://opendart.fss.or.kr/api"
KST = timezone(timedelta(hours=9))
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT = os.path.join(ROOT, "docs", "data", "ipo.json")
SCHEMA = 3

WINDOW_DAYS = 70      # 청약이 이 기간보다 오래전이면 새로 읽지 않는다
KEEP_DAYS = 400       # 지난 종목을 json 에 남기는 기간
DART_LOOKBACK = 75
LIST_PAGES = ["http://www.38.co.kr/html/fund/index.htm?o=k", "http://www.38.co.kr/html/fund/index.htm?o=k&page=2",
              "http://www.38.co.kr/html/fund/index.htm?o=nw"]
DETAIL = "http://www.38.co.kr/html/fund/?o=v&no={}"

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


def get(url, timeout=25):
    last = None
    for i in range(3):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
            with urllib.request.urlopen(req, timeout=timeout) as r:
                raw = r.read()
            try:
                return raw.decode("utf-8")
            except Exception:
                return raw.decode("cp949", "replace")
        except Exception as e:
            last = e
            time.sleep(2 * (i + 1))
    raise RuntimeError(f"{url}: {last}")


def text_of(html):
    return re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", html.replace("&nbsp;", " ")))


def short_broker(name):
    n = (name or "").replace("(주)", "").replace("㈜", "")
    for key, short in BROKER_MAP:
        if key.lower() in n.lower():
            return short
    n = re.sub(r"(투자증권|금융투자|증권|주식회사)", "", n).strip()
    return n or None


def iso(d):
    return d.isoformat() if d else None


def to_date(y, m, d):
    try:
        return date(int(y), int(m), int(d))
    except ValueError:
        return None


# ---------------------------------------------------------------- 38커뮤니케이션
LINK = re.compile(r'href="(?:\./|/html/fund/(?:index\.htm)?)\?o=v&(?:amp;)?no=(\d+)')


def parse_detail(text, no):
    m = re.search(r"종목명\s+(.+?)\s+진행상황\s+(\S+)", text)
    if not m:
        return None
    name = re.sub(r"\(구[^)]*\)", "", m.group(1)).strip()
    progress = m.group(2)
    body = text[m.start():]

    def dt(label):
        x = re.search(label + r"\s*(\d{4})\.(\d\d)\.(\d\d)", body)
        return to_date(*x.groups()) if x else None

    s = re.search(r"공모청약일\s*(\d{4})\.(\d\d)\.(\d\d)\s*~\s*(\d{4})\.(\d\d)\.(\d\d)", body)
    sub = [to_date(*s.groups()[:3]), to_date(*s.groups()[3:])] if s else None
    price = re.search(r"확정공모가\s*([\d,]+)\s*원", body)
    band = re.search(r"희망공모가액\s*:?\s*([\d,]+)\s*~\s*([\d,]+)\s*원", body)
    brk = re.search(r"주간사\s+(.+?)\s+주식수", body)
    brokers = []
    for b in re.split(r"[,/·]", brk.group(1)) if brk else []:
        sb = short_broker(b.strip())
        if sb and sb not in brokers:
            brokers.append(sb)
    band = [int(band.group(1).replace(",", "")), int(band.group(2).replace(",", ""))] if band else None
    if band and band[0] == band[1]:
        band = None
    return {"no": no, "name": name, "progress": progress, "sub": sub, "refund": dt("환불일"), "listing": dt("(?<!신규)상장일"),
            "price": int(price.group(1).replace(",", "")) if price else None, "band": band, "brokers": brokers}


def scrape_38(old):
    ids = []
    for u in LIST_PAGES:
        try:
            for no in LINK.findall(get(u)):
                if no not in ids:
                    ids.append(no)
        except Exception as e:
            log("38 목록 실패", u, e)
    log(f"38 종목 번호 {len(ids)}개")
    today = datetime.now(KST).date()
    out, cancelled = [], []
    for no in ids:
        o = old.get("n" + no)
        if o and o.get("listing") and o["listing"] < (today - timedelta(days=1)).isoformat():
            continue   # 이미 상장까지 끝난 종목은 다시 읽지 않는다
        try:
            d = parse_detail(text_of(get(DETAIL.format(no))), no)
        except Exception as e:
            log("  38 상세 실패", no, e)
            continue
        time.sleep(0.3)
        if not d or not d["sub"] or None in d["sub"]:
            continue
        if re.search("철회|취소|연기|폐지", d["progress"]):
            log("  제외(진행상황):", d["name"], d["progress"])
            cancelled.append(d)
            continue
        if d["sub"][1] < today - timedelta(days=WINDOW_DAYS):
            continue
        out.append(d)
    return out, cancelled, set(ids)


# ---------------------------------------------------------------- OpenDART (교차 확인)
def call(path, **params):
    params["crtfc_key"] = os.environ["DART_API_KEY"]
    url = f"{API}/{path}?{urllib.parse.urlencode(params)}"
    return json.loads(get(url, timeout=30))


DATE_FULL = re.compile(r"(20\d{2})\s*[.\-년/]\s*(\d{1,2})\s*[.\-월/]\s*(\d{1,2})")


def norm(n):
    n = re.sub(r"\(구[^)]*\)|\(주\)|㈜|\s", "", n or "")
    return n


def is_spac(n):
    return "스팩" in n or "기업인수목적" in n


def rows(resp, *keys):
    for g in resp.get("group", []) or []:
        if g.get("title") in keys and isinstance(g.get("list"), list):
            return g["list"]
    for k in keys:
        if isinstance(resp.get(k), list):
            return resp[k]
    return []


def latest(items):
    if not items:
        return []
    top = max(i.get("rcept_no", "") for i in items)
    return [i for i in items if i.get("rcept_no", "") == top]


def dart_items(names38):
    end = datetime.now(KST).strftime("%Y%m%d")
    bgn = (datetime.now(KST) - timedelta(days=DART_LOOKBACK)).strftime("%Y%m%d")
    rows_, page = [], 1
    while True:
        r = call("list.json", bgn_de=bgn, end_de=end, pblntf_ty="C", pblntf_detail_ty="C001", last_reprt_at="N", page_no=page, page_count=100)
        if r.get("status") == "013":
            break
        if r.get("status") != "000":
            raise RuntimeError(f"list.json: {r.get('status')} {r.get('message')}")
        rows_ += r.get("list", [])
        if page >= int(r.get("total_page", 1)):
            break
        page += 1
    corps = {}
    for x in rows_:
        if x.get("stock_code", "").strip() and norm(x["corp_name"]) not in names38:
            continue   # 상장사의 유상증자 등
        corps.setdefault(x["corp_code"], x)
    out = []
    for c in corps.values():
        try:
            r = call("estkRs.json", corp_code=c["corp_code"], bgn_de=bgn, end_de=end)
        except Exception as e:
            log("  DART 실패", c["corp_name"], e)
            continue
        if r.get("status") != "000":
            continue
        g = (latest(rows(r, "일반사항", "general")) or [{}])[0]
        dates = [to_date(*m) for m in DATE_FULL.findall(g.get("sbd") or "")]
        sub = [iso(dates[0]), iso(dates[-1])] if dates else None
        pay = [to_date(*m) for m in DATE_FULL.findall(g.get("pymd") or "")]
        prices = []
        for s in latest(rows(r, "증권의종류", "stock")):
            prices += [int(n.replace(",", "")) for n in re.findall(r"\d[\d,]*", s.get("slprc") or "") if int(n.replace(",", "")) >= 100]
        brokers = []
        for u in latest(rows(r, "인수인정보", "acqs")):
            if re.search(r"대표|공동|주관|주선|인수", u.get("actsen", "")):
                b = short_broker(u.get("actnmn"))
                if b and b not in brokers:
                    brokers.append(b)
        out.append({"name": re.sub(r"\(주\)|㈜", "", c["corp_name"]).strip(), "sub": sub, "pay": iso(pay[0]) if pay else None,
                    "band": [min(prices), max(prices)] if len(set(prices)) >= 2 else None, "brokers": brokers, "report": c.get("report_nm", "")})
        time.sleep(0.2)
    return out


def match_dart(item, darts):
    for d in darts:
        if norm(d["name"]) == norm(item["name"]):
            return d
    if is_spac(item["name"]):
        for d in darts:
            if is_spac(d["name"]) and d["sub"] and d["sub"][0] == item["sub"][0]:
                return d
    return None


# ---------------------------------------------------------------- 메인
def main():
    if not os.environ.get("DART_API_KEY"):
        sys.exit("DART_API_KEY 환경변수가 없습니다.")
    old, old_doc = {}, {}
    if os.path.exists(OUT):
        try:
            old_doc = json.load(open(OUT, encoding="utf-8"))
            if old_doc.get("v") == SCHEMA:
                old = {p["id"]: p for p in old_doc.get("ipos", [])}
        except Exception as e:
            log("기존 ipo.json 읽기 실패(무시):", e)
    before = json.dumps(old, sort_keys=True, ensure_ascii=False)

    scraped, cancelled, ids_seen = scrape_38(old)
    if not scraped:
        sys.exit("38커뮤니케이션에서 읽은 종목이 0개입니다. 기존 파일을 유지합니다.")
    names38 = {norm(d["name"]) for d in scraped}
    try:
        darts = dart_items(names38)
    except Exception as e:
        log("DART 조회 실패(교차확인 생략):", e)
        darts = None
    log(f"38: {len(scraped)}종목, DART: {len(darts) if darts is not None else '실패'}건")

    merged = dict(old)
    for d in scraped:
        item = {"id": "n" + d["no"], "name": d["name"], "brokers": list(d["brokers"]), "sub": [iso(d["sub"][0]), iso(d["sub"][1])]}
        flags = []
        if d["refund"]:
            item["refund"] = iso(d["refund"])
        if d["listing"]:
            item["listing"] = iso(d["listing"])
        if d["price"] is not None:
            item["price"] = d["price"]
        if d["band"]:
            item["band"] = d["band"]
        if is_spac(d["name"]):
            item["spac"] = True
            if "price" not in item:
                item["price"] = 2000
        if darts is not None:
            m = match_dart(item, darts)
            if m:
                if m["sub"] and m["sub"] != item["sub"]:
                    flags.append(f"DART 청약일 {m['sub'][0][5:]}~{m['sub'][1][5:]} 와 다름")
                for b in m["brokers"]:
                    if b not in item["brokers"]:
                        item["brokers"].append(b)
                if "band" not in item and m["band"] and not item.get("spac"):
                    item["band"] = m["band"]
                if "refund" not in item and m["pay"]:
                    item["refund"] = m["pay"]
            elif item["sub"][1] >= (datetime.now(KST) - timedelta(days=14)).date().isoformat():
                flags.append("DART 공시 못 찾음")   # 지난 종목은 DART 조회 기간 밖일 수 있어 표시하지 않는다
        if not item["brokers"]:
            flags.append("주관사 없음")
        if "listing" not in item:
            flags.append("상장일 미정")
        item["flags"] = flags
        merged[item["id"]] = item

    # ---- 변경·신규 알림 기록 (이전 값과 비교) ----
    now = datetime.now(KST).strftime("%Y-%m-%dT%H:%M")
    today_iso = datetime.now(KST).date().isoformat()
    tracking = bool(old)          # 첫 실행에는 알림을 만들지 않는다
    changes = []

    def md(x):
        return f"{int(x[5:7])}/{int(x[8:10])}"

    def rng(sub):
        return md(sub[0]) + ("~" + md(sub[1]) if sub[1] != sub[0] else "")

    def chg(tab, it, text):
        changes.append({"t": now, "id": it["id"], "name": it["name"], "tab": tab, "text": text})

    if tracking:
        for it in merged.values():
            o = old.get(it["id"])
            if it["id"] not in {"n" + d["no"] for d in scraped}:
                continue
            if o is None:
                if it["sub"][1] >= today_iso:
                    chg("new", it, f"신규 등록 · 청약 {rng(it['sub'])}")
                continue
            if o["sub"] != it["sub"]:
                chg("chg", it, f"청약일 {rng(o['sub'])} → {rng(it['sub'])}")
            if o["sub"] == it["sub"] and o.get("refund") and it.get("refund") and o["refund"] != it["refund"]:
                chg("chg", it, f"환불일 {md(o['refund'])} → {md(it['refund'])}")
            if not o.get("listing") and it.get("listing"):
                chg("new", it, f"상장일 확정 · {md(it['listing'])}")
            elif o.get("listing") and it.get("listing") and o["listing"] != it["listing"]:
                chg("chg", it, f"상장일 {md(o['listing'])} → {md(it['listing'])}")
            elif o.get("listing") and not it.get("listing"):
                chg("chg", it, "상장일 미정으로 바뀜")
            if o.get("price") is None and it.get("price") is not None and not it.get("spac"):
                chg("new", it, f"확정 공모가 {it['price']:,}원")

    # ---- 철회·삭제 ----
    for d in cancelled:
        it = merged.pop("n" + d["no"], None)
        if it and tracking and "n" + d["no"] in old:
            chg("chg", it, f"철회·취소 (38 진행상황: {d['progress']})")
    if len(ids_seen) >= 10:       # 목록을 제대로 읽었을 때만, 3회 연속 안 보이면 사라진 것으로 본다
        for oid, p in list(merged.items()):
            if oid in {"n" + d["no"] for d in scraped} or oid[1:] in ids_seen:
                continue
            if p.get("listing") and p["listing"] < today_iso:
                continue
            p["miss"] = p.get("miss", 0) + 1
            if p["miss"] >= 3:
                merged.pop(oid)
                if tracking:
                    chg("chg", p, "38 일정에서 사라짐 (철회·취소 가능)")

    prior = old_doc.get("changes", []) if old_doc.get("v") == SCHEMA else []
    cut_t = (datetime.now(KST) - timedelta(days=30)).strftime("%Y-%m-%dT%H:%M")
    seen_k, allc = set(), []
    for c in prior + changes:
        k = (c["id"], c["text"])
        if c["t"] >= cut_t and k not in seen_k:
            seen_k.add(k)
            allc.append(c)
    allc = allc[-150:]

    cutoff = (datetime.now(KST) - timedelta(days=KEEP_DAYS)).date().isoformat()
    ipos = sorted([p for p in merged.values() if p["sub"][0] >= cutoff], key=lambda p: p["sub"][0])
    if before == json.dumps({p["id"]: p for p in ipos}, sort_keys=True, ensure_ascii=False) and old_doc.get("v") == SCHEMA and not changes:
        log("변경 없음 - 파일을 건드리지 않습니다.")
        return
    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    doc = {"v": SCHEMA, "updatedAt": datetime.now(KST).strftime("%Y-%m-%dT%H:%M"), "source": "38커뮤니케이션+OpenDART", "ipos": ipos, "changes": allc}
    tmp = OUT + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(doc, f, ensure_ascii=False, indent=1)
    os.replace(tmp, OUT)
    log(f"저장 {len(ipos)}건, 새 알림 {len(changes)}건 -> {OUT}")
    for c in changes:
        log(f"  알림[{c['tab']}] {c['name']}: {c['text']}")
    for p in ipos:
        if p.get("flags"):
            log(f"  ! {p['name']}: {', '.join(p['flags'])}")


if __name__ == "__main__":
    main()
