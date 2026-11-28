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
    ("유안타", "유안타"), ("BNK", "BNK"), ("비엔케이", "BNK"), ("LS", "LS"), ("메리츠", "메리츠"), ("다올", "다올"),
    ("부국", "부국"), ("이베스트", "이베스트"), ("하이", "하이"), ("흥국", "흥국"), ("케이프", "케이프"),
]



def log(*a):
    print(*a, file=sys.stderr)


def get(url, timeout=25, ua="Mozilla/5.0"):
    last = None
    for i in range(3):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": ua})
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
    mr = re.search(r"증거금율\s*:\s*(\d+)\s*%\s*청약\s*최고한도", body)
    band = [int(band.group(1).replace(",", "")), int(band.group(2).replace(",", ""))] if band else None
    if band and band[0] == band[1]:
        band = None
    return {"no": no, "name": name, "progress": progress, "sub": sub, "refund": dt("환불일"), "listing": dt("(?<!신규)상장일"),
            "price": int(price.group(1).replace(",", "")) if price else None, "band": band, "brokers": brokers,
            "margin": int(mr.group(1)) if mr else None}


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
                    "band": [min(prices), max(prices)] if len(set(prices)) >= 2 else None, "brokers": brokers, "report": c.get("report_nm", ""), "corp": c["corp_code"],
                    "rcept": max(x.get("rcept_no", "") for x in rows_ if x["corp_code"] == c["corp_code"])})
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
    else:
        k = norm(item["name"])
        for d in darts:   # DART 에 이름이 짧게 올라온 경우(예: 하나에어로): 청약 시작일이 같을 때만 같은 종목으로 본다
            g = norm(d["name"])
            if d["sub"] and d["sub"][0] == item["sub"][0] and len(g) >= 3 and (k.startswith(g) or g.startswith(k)):
                return d
    return None


# ---------------------------------------------------------------- 메인
MINQ_RE = re.compile(r"[【\[]\s*([^【】\[\]]*?)\s*청약(?:주식(?:수)?별)?\s*단위\s*[】\]]\s*청약주식수\s*청약\s*단위\s*(?:비\s*고\s*)?([\d,]+)\s*주\s*이상")


def fetch_doc_text(rcept):
    import io, zipfile
    url = f"{API}/document.xml?" + urllib.parse.urlencode({"crtfc_key": os.environ["DART_API_KEY"], "rcept_no": rcept})
    with urllib.request.urlopen(url, timeout=60) as r:
        z = zipfile.ZipFile(io.BytesIO(r.read()))
    txt = ""
    for n in z.namelist():
        b = z.read(n)
        try:
            txt += b.decode("utf-8")
        except Exception:
            txt += b.decode("cp949", "replace")
    return re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", txt))


def doc_text_any(m):
    """최신 접수번호에 본문이 없으면(예: [첨부추가]) 같은 회사의 이전 증권신고서 본문을 차례로 시도한다."""
    try:
        return fetch_doc_text(m["rcept"])
    except Exception:
        pass
    end = datetime.now(KST).strftime("%Y%m%d")
    bgn = (datetime.now(KST) - timedelta(days=DART_LOOKBACK)).strftime("%Y%m%d")
    r = call("list.json", corp_code=m["corp"], bgn_de=bgn, end_de=end, pblntf_ty="C", pblntf_detail_ty="C001", page_count=100)
    for x in sorted(r.get("list", []) or [], key=lambda x: x.get("rcept_no", ""), reverse=True):
        if x["rcept_no"] == m["rcept"] or "첨부추가" in x.get("report_nm", ""):
            continue
        try:
            return fetch_doc_text(x["rcept_no"])
        except Exception:
            continue
    raise RuntimeError("본문 없음")


ELIG_VER = 5   # 계좌개설·수수료 읽는 규칙을 고칠 때 올린다

ELIG_HDR = re.compile(r"[【\[]\s*([^【】\[\]]{0,30}?)\s*(?:청약|참가)\s*자격\s*[】\]]")
# 계좌개설 마감 (비대면 기준). 위에서부터 먼저 맞는 것을 쓴다.
ACCT_RULES = [
    (re.compile(r"(\d)\s*영업일\s*까지"), None),   # 신영: 청약 초일 직전 4영업일까지
    (re.compile(r"비대면[^※■]{0,60}?청약\s*당일\s*계좌\s*개설\s*후\s*청약\s*가능"), "당일"),   # NH·KB·유안타
    (re.compile(r"비대면[^※■*]{0,40}?청약일\s*종일까지"), "당일"),   # 신한
    (re.compile(r"청약일\s*현재\s*제휴은행\s*개설"), "당일"),   # 한국투자
    (re.compile(r"청약\s*개시일\s*현재\s*계좌\s*보유"), "당일"),   # 대신
    (re.compile(r"청약\s*신청일\s*전일까지\s*개설"), "전일"),   # 우리투자
    (re.compile(r"청약\s*시작일\s*전일\(영업일\s*기준\)까지\s*비대면\s*개설"), "직전 영업일"),   # IBK
    (re.compile(r"청약\s*개시일\s*직전\s*영업일까지\s*개설"), "직전 영업일"),   # BNK
    (re.compile(r"청약\s*당일\s*계좌\s*개설\s*後\s*공모주\s*온라인\s*청약만\s*가능합니다\.\s*1\)\s*비대면"), "당일"),   # 삼성
    (re.compile(r"다음의\s*계좌는\s*청약\s*당일\s*계좌\s*개설\s*후\s*공모주\s*청약이\s*가능합니다\.\s*①\s*비대면"), "당일"),   # 하나
    (re.compile(r"청약\s*기간\s*내\s*영업점\s*창구\s*개설\s*계좌\s*청약\s*불가\s*※\s*비대면"), "당일"),   # 미래에셋
    (re.compile(r"청약\s*당일\s*비대면\s*개설\s*또는\s*제휴은행\s*개설"), "당일"),   # SK
    (re.compile(r"청약\s*초일\s*전일까지\s*청약가능한\s*주식계좌\s*개설\s*고객\s*청약채널"), "전일"),   # 유진
    (re.compile(r"청약\s*개시일\s*직전일까지\s*개설"), "전일"),   # iM
]
# 청약수수료 (온라인 청약 기준, 등급 면제 전 금액). 증권사별로 표기가 달라 아는 형식만 읽는다.
FEE_RULES = [
    re.compile(r"On-?Line\s*(?:청약\s+)?((?:무료|[\d,]+원)(?:\s+(?:무료|[\d,]+원))*)\s+Off-?Line"),   # 한국투자·삼성
    re.compile(r"On-?Line\s*\([^)]*\)\s*([\d,]+)\s*원"),   # 우리투자
    re.compile(r"(?:HTS|MTS)[^\d]{0,40}청약\s*:\s*([\d,]+)\s*원"),   # 신영
    re.compile(r"온라인\s*\([^)]*\)\s*([\d,]+)\s*원"),   # 대신
    re.compile(r"HTS,?\s*MTS[^\d]{0,30}([\d,]+)\s*원"),   # BNK
    re.compile(r"온라인\s*\(HTS/MTS/홈페이지/ARS\)\s*무료\s*([\d,]+)\s*원"),   # KB
    re.compile(r"MTS,\s*HTS,\s*SMT,\s*홈페이지\s*면제\s*건당\s*([\d,]+)\s*원"),   # NH
    re.compile(r"온라인\s+면제\s+면제\s+면제\s+[\d,]+원\s+([\d,]+)\s*원"),   # 신한 (일반 등급)
    re.compile(r"온라인\s*청약\s*:\s*([\d,]+)\s*원"),   # 유안타
    re.compile(r"온라인\s*및\s*ARS\s*청약\s*수수료\s*(?:면제\s*){3}([\d,]+)\s*원"),   # 하나
    re.compile(r"온라인\s*\(HTS/MTS/WEB\)\s*([\d,]+)\s*원"),   # 유진 (N천원은 아래에서 숫자로 바꿈)
    re.compile(r"온라인\s*\(HTS,\s*WTS,\s*MTS\)\s*및\s*ARS\s*청약\s*([\d,]+)\s*원"),   # iM
    re.compile(r"온라인\s*매체\s*청약\([^)]*\)\s*\d+%\s*0원\(단,\s*[A-Za-z가-힣]+\s*등급\s*([\d,]+)\s*원\)"),   # 미래에셋 (최저 등급 기준)
    re.compile(r"150%\s*미해당\s*고객\s*[\d,]+원\s+([\d,]+)\s*원"),   # IBK (오프라인 다음 온라인)
]


def broker_sections(txt):
    hs = list(ELIG_HDR.finditer(txt))
    out = {}
    for i, m in enumerate(hs):
        nm = re.sub(r"일반\s*청약자|㈜|\(주\)|의\s*$", "", m.group(1)).strip()
        nm = re.sub(r"의$", "", nm).strip()
        b = short_broker(nm) if nm else None
        if not b:
            continue
        end = hs[i + 1].start() if i + 1 < len(hs) else m.end() + 3000
        out[b] = out.get(b, "") + " " + txt[m.end(): min(end, m.end() + 3000)]   # 같은 증권사 머리글이 연이어 나오면(안내문 + 표) 이어 붙인다
    return out


def parse_elig(txt):
    acct, fee = {}, {}
    for b, sec in broker_sections(txt).items():
        for rx, label in ACCT_RULES:
            m = rx.search(sec)
            if m:
                acct[b] = f"{m.group(1)}영업일 전" if label is None else label
                break
        sec_f = re.sub(r"(\d)\s*천\s*원", lambda x: f"{int(x.group(1)) * 1000}원", sec)
        for rx in FEE_RULES:
            m = rx.search(sec_f)
            if m:
                nums = [int(n.replace(",", "")) for n in re.findall(r"([\d,]+)\s*원", m.group(1) + "원")] if "무료" in m.group(1) else [int(m.group(1).replace(",", ""))]
                if nums and max(nums) > 0:
                    fee[b] = max(nums)
                    break
    return acct, fee


def read_doc_info(txt, brokers):
    """공시 본문 하나에서 최소 청약수량·계좌개설·수수료를 읽는다."""
    q = parse_minq(txt)
    if "_" in q:
        v = q.pop("_")
        if len(brokers) == 1:
            q[brokers[0]] = v
    acct, fee = parse_elig(txt)
    return q, acct, fee


def parse_minq(txt):
    """증권신고서 '증권사별 청약주식별 청약단위' 표의 첫 행(N주 이상) = 일반청약자 최소 청약수량. 표를 못 읽으면 비워 둔다."""
    out = {}
    for name, n in MINQ_RE.findall(txt):
        nm = re.sub(r"청약주식(?:수)?별|(?:대표주관회사|인수회사|일반청약자)\s*의?|㈜|\(주\)|의\s*$", "", name).strip()
        b = short_broker(nm) if nm else "_"    # 증권사 이름 없는 표 = 대표주관회사 한 곳만 적은 공시
        if b and b not in out:
            out[b] = int(n.replace(",", ""))
    return out


MINQ_VER = 3   # 최소수량 읽는 규칙을 고칠 때 올린다 -> 이미 시도한 지난 종목도 다시 시도


SPAC_ALIAS = [("에스케이", "SK"), ("엔에이치", "NH"), ("케이비", "KB"), ("아이비케이", "IBK"), ("디비", "DB"), ("한국투자", "한국")]


def spac_core(k):
    """스팩 이름 표기 차이를 없애는 공통 규칙(38↔DART 이름 맞춤과 영상 제목 맞춤이 같이 씀):
    에스케이=SK 같은 한글 표기 통일, '제'·'스팩'·'기업인수목적(회사)' 제거, '증권' 표기 유무 무시. 호수는 남긴다."""
    for a, b in SPAC_ALIAS:
        k = k.replace(a, b)
    k = re.sub(r"제(?=\d)", "", k)
    k = re.sub(r"기업인수목적(?:회사)?|스팩", "", k)
    return re.sub(r"투자증권|증권", "", k)


def norm2(n):
    """38 과 DART 의 회사 이름 표기 차이를 줄인 비교용 키. 스팩은 '한화플러스스팩6호' = '한화플러스제6호스팩'."""
    k = norm(n)
    if "스팩" in k or "기업인수목적" in k:
        k = spac_core(k)
        k = k.replace("호", "")
    return k


def find_group(groups, name):
    k = norm2(name)
    if k in groups:
        return groups[k]
    if "스팩" not in name and "기업인수목적" not in name and len(k) >= 4:
        for g, v in groups.items():   # DART 에는 회사 이름이 짧게(예: 하나에어로) 올라오기도 한다
            if len(g) >= 3 and (k.startswith(g) or g.startswith(k)):
                return v
    return []


def backfill(merged, today_s, limit_m=15, limit_q=14):
    """이미 지난 종목에 증거금률(38)·최소 청약수량(DART 공시)을 채운다. 한 번에 일부씩, 여러 번 돌며 끝낸다."""
    n = 0
    for it in merged.values():
        if n >= limit_m:
            break
        if it.get("margin") or it.get("mchk") or not it["id"].startswith("n"):
            continue
        no = it["id"][1:]
        try:
            d = parse_detail(text_of(get(DETAIL.format(no))), no)
        except Exception as e:
            log("  38 증거금률 읽기 실패", it["name"], e)
            continue
        n += 1
        time.sleep(0.3)
        if d and d.get("margin"):
            it["margin"] = d["margin"]
        it["mchk"] = 1
    cands = [it for it in merged.values() if it.get("elig_v") != ELIG_VER and it.get("sub") and it["sub"][1] < today_s]
    cands.sort(key=lambda x: x["sub"][1], reverse=True)
    cands = cands[:limit_q]
    if not cands:
        return
    start = datetime.fromisoformat(min(c["sub"][0] for c in cands)) - timedelta(days=45)
    end = datetime.now(KST).replace(tzinfo=None)
    groups = {}
    cur = start
    try:
        while cur < end:
            nxt = min(cur + timedelta(days=85), end)
            page = 1
            while True:
                r = call("list.json", bgn_de=cur.strftime("%Y%m%d"), end_de=nxt.strftime("%Y%m%d"), pblntf_ty="C", pblntf_detail_ty="C001", page_no=page, page_count=100)
                if r.get("status") == "013":
                    break
                if r.get("status") != "000":
                    raise RuntimeError(f"list.json: {r.get('status')} {r.get('message')}")
                for x in r.get("list", []):
                    groups.setdefault(norm2(x["corp_name"]), []).append(x)
                if page >= int(r.get("total_page", 1)):
                    break
                page += 1
            cur = nxt + timedelta(days=1)
    except Exception as e:
        log("지난 종목 DART 목록 실패(다음에 재시도):", e)
        return
    t0 = time.time()
    for it in cands:
        if time.time() - t0 > 420:   # 한 번의 수집이 너무 길어지지 않게: 남은 종목은 다음 수집에서
            log("  지난 종목 보완: 시간 제한, 나머지는 다음에")
            break
        reps = [x for x in find_group(groups, it["name"])
                if "첨부추가" not in x.get("report_nm", "") and x["rcept_no"][:8] <= it["sub"][0].replace("-", "")]
        reps.sort(key=lambda x: x["rcept_no"], reverse=True)
        failed = False
        old_ac = it.pop("acct", None)   # 읽는 규칙이 바뀌었으니 계좌개설 문구는 새로 읽는다(못 읽으면 이전 값으로 되돌린다)
        for tried, x in enumerate(reps[:6], 1):
            try:
                q, ac, fe = read_doc_info(fetch_doc_text(x["rcept_no"]), it["brokers"])
            except Exception as e:
                log("  공시 본문 실패", it["name"], e)
                failed = True
                continue
            if q and "minq" not in it:
                it["minq"], it["minq_rc"] = q, x["rcept_no"]
            for k, v in (("acct", ac), ("fee", fe)):
                if v:
                    it.setdefault(k, {})
                    for b, val in v.items():
                        it[k].setdefault(b, val)   # 더 최근 공시 값을 우선
            if "minq" in it and (it.get("acct") or it.get("fee") or tried >= 2) or tried >= 3:
                break
        if not it.get("acct") and old_ac:
            it["acct"] = old_ac
        if not failed or "minq" in it:
            it["minq_v"] = MINQ_VER
            it["elig_v"] = ELIG_VER
        log(f"  지난 종목 최소수량: {it['name']} -> {it.get('minq')}")



# ---------------------------------------------------------------- 상세창 확장(일부 종목에 먼저 적용)
EXT_VER = 6
YT_HANDLE2 = "공모주투자_0301"   # 앞으로 나올 종목 영상이 올라올 두 번째 채널
YT_CHANNEL = "UCstq1xNCuEIR43dM3CMop3g"   # 공모주투자의법칙


def num(s):
    return int(s.replace(",", ""))


def parse_ext38(t, brokers):
    """38 상세 페이지에서 수요예측 결과·배정 수량·유통가능 물량을 읽는다. 읽은 것만 돌려준다(추정 없음)."""
    e = {}
    m = re.search(r"수요예측일\s*(\d{4})\.(\d\d)\.(\d\d)\s*~\s*(\d{4})\.(\d\d)\.(\d\d)", t)
    if m:
        e["dem_dates"] = [iso(to_date(*m.groups()[:3])), iso(to_date(*m.groups()[3:]))]
    m = re.search(r"기관경쟁률\s*([\d,]+(?:\.\d+)?)(?:\s*:\s*1)?", t)
    if m:
        e["comp"] = float(m.group(1).replace(",", ""))
    m = re.search(r"구분\s*신청수량\(단위:주\)(.*?)합계\s*([\d,]+)\s*총\s*수량\s*대비\s*비율\(%\)\s*([\d.]+)\s*%", t)
    if m:
        e["lock_qty"], e["lock_pct"] = num(m.group(2)), float(m.group(3))
    m = re.search(r"합계\s*([\d,]+)\s*건\s*([\d,]+)\s*주\s*100\s*%", t)
    if m:
        e["apply_qty"] = num(m.group(2))
    m = re.search(r"기관투자자등\s*([\d,]+)\s*주\s*\(", t)
    if m:
        e["inst"] = num(m.group(1))
    m = re.search(r"주간사\s+.+?\s+주식수\s*:\s*([\d,]+)(?:\s*~\s*([\d,]+))?\s*주\s*/\s*청약한도", t)
    gen = num(m.group(1)) if m else None
    gen_hi = num(m.group(2)) if m and m.group(2) else gen
    al, alr = {}, {}
    seg = re.search(r"인수회사\s*주식수\s*청약한도\s*기타\s*(.+?)\s*주요일정", t)
    if seg:
        for nm, q, q2 in re.findall(r"([가-힣A-Za-z]*(?:증권|은행|금융투자)(?:㈜)?)\s+([\d,]+)(?:\s*~\s*([\d,]+))?\s*주", seg.group(1)):
            b = short_broker(nm)
            if b:
                lo, hi = num(q), (num(q2) if q2 else num(q))
                if lo == hi:
                    al[b] = al.get(b, 0) + lo
                else:
                    alr[b] = [alr.get(b, [0, 0])[0] + lo, alr.get(b, [0, 0])[1] + hi]
    if alr:   # 38 이 아직 범위로 적은 종목: 범위 그대로 보여 준다(인수회사 표에 적힌 값). 일부만 확정 숫자면 [값, 값]으로 둔다
        for b_, v_ in al.items():
            alr[b_] = [v_, v_]
        al = {}
    elif gen and gen == gen_hi:   # 확정된 숫자
        if not (al and not alr and sum(al.values()) == gen):
            al = {brokers[0]: gen} if len(brokers) == 1 else {}
        alr = {}
    elif gen and gen_hi and gen < gen_hi and len(brokers) == 1:
        al, alr = {}, {brokers[0]: [gen, gen_hi]}
    else:
        al, alr = {}, {}
    if al:
        e["alloc"] = al
    if alr:
        e["alloc_r"] = alr
    m = re.search(r"합\s*계\s*([\d,]+)\s*100\.00\s*%\s*([\d,]+)\s*100\.00\s*%\s*([\d,]+)\s*([\d.]+)\s*%\s*([\d,]+)\s*([\d.]+)\s*%", t)
    if m:
        e["tot"], e["free"] = num(m.group(2)), num(m.group(5))
    return e


PUT_TB = re.compile(r"일반청약자\s*환매청구권\s*부여사유\s*행사가능\s*투자자\s*부여수량\s*행사기간\s*행사가격\s*(.{0,200}?)\s*【")
PUT_NEG = re.compile(r"환매청구권(?:이|을)?\s*부여(?:되지|하지)\s*(?:않|아니)|환매청구권\)\s*제1항\s*각\s*호에\s*해당하는\s*사항이\s*존재하지\s*않")


def parse_put(t):
    """증권신고서·투자설명서의 환매청구권. 있으면 {'y':1,'mo':개월,'pct':행사가격 비율}, 없다고 밝혔으면 {}, 문서에서 못 읽으면 None."""
    m = PUT_TB.search(t)
    if m:
        row = m.group(1).strip()
        if re.fullmatch(r"[-\s]*", row):
            return {}
        r = {"y": 1}
        mm = re.search(r"일반청약자\s+[\d,]+\s+상장일[^\d]{0,12}(\d+)\s*개월", row)
        if mm:
            r["mo"] = int(mm.group(1))
        pc = re.search(r"권리\s*행사\s*가격은?\s*공모\s*가(?:격|액)?의\s*(\d+)\s*%", t)
        if pc and 50 <= int(pc.group(1)) <= 100:
            r["pct"] = int(pc.group(1))
        return r
    if PUT_NEG.search(t):
        return {}
    if re.search(r"일반청약자에게[^.]{0,80}?환매청구권을\s*부여하며", t):
        r = {"y": 1}
        mm = re.search(r"상장일(?:로)?부터\s*(\d+)\s*개월", t)
        if mm:
            r["mo"] = int(mm.group(1))
        pc = re.search(r"권리\s*행사\s*가격은?\s*공모\s*가(?:격|액)?의\s*(\d+)\s*%", t)
        if pc and 50 <= int(pc.group(1)) <= 100:
            r["pct"] = int(pc.group(1))
        return r
    return None


def read_put(e, reps):
    """투자설명서(없으면 증권신고서)에서 환매청구권을 읽어 e['put'] 에 넣는다. 못 읽으면 넣지 않는다(=화면에 안 보임)."""
    for k in ("설명서", "신고서"):
        if not reps.get(k):
            continue
        try:
            r = parse_put(fetch_doc_text(reps[k]))
        except Exception as ex:
            log("  환매청구권 읽기 실패", k, ex)
            continue
        if r is None:
            continue
        if r:
            e["put"] = r
        else:
            e.pop("put", None)
        return
    e.pop("put", None)


def parse_gen_doc(t):
    """증권신고서·투자설명서의 '정정 후' 청약대상자 유형별 표에서 확정된 일반청약자 배정 주식수(범위가 아닌 숫자 하나, '확정공모가액' 기준 문구가 뒤따르는 것)."""
    got = None
    for m in re.finditer(r"일반청약자\s+([\d,]+)\s*주\s+[\d.]+\s*%\s+[\d,]+\s*원", t):
        if "확정공모가액" in t[m.end():m.end() + 400]:
            got = num(m.group(1))
    return got


def plan_eq(t, eq):
    """균등방식으로 배정하기로 했던 물량. 공시가 '일반배정물량 N주 중 M주(X%)를 균등방식…' 또는 '총 M주를 균등배정하였습니다'로 밝힌 경우 그 값, 밝힌 게 없으면 실제 균등 배정 수량."""
    vals = {num(m) for m in re.findall(r"일반\s*배정\s*물량\s*[\d,]+\s*주\s*중\s*([\d,]+)\s*주\s*\([\d.]+\s*%\)\s*를\s*균등\s*방식", t)}
    vals |= {num(m) for m in re.findall(r"총\s*([\d,]+)\s*주를\s*균등\s*배정\s*하였습니다", t)}
    if len(vals) == 1:
        v = vals.pop()
        if v >= eq:
            return v
    return eq


def parse_report(t):
    """증권발행실적보고서: 기관 배정 중 미확약 수량, 일반청약 결과(청약 건수·수량, 균등/비례 물량)."""
    f = {}
    m = re.search(r"미확약\s+((?:[\d,]+(?:\.\d+)?\s+|-\s+){14})계\s+((?:[\d,]+(?:\.\d+)?\s+|-\s+){14})", t)
    if m:   # 7개 열(기관 유형 6 + 합계) × (수량, 비중)
        a = re.findall(r"[\d,]+(?:\.\d+)?|-", m.group(1))
        b = re.findall(r"[\d,]+(?:\.\d+)?|-", m.group(2))
        if b[13].replace(",", "") in ("100", "100.0", "100.00") and a[12] != "-" and b[12] != "-" and num(a[12]) <= num(b[12]):
            f["inst"], f["unlock"] = num(b[12]), num(a[12])
    m = re.search(r"일반투자자\s+([\d,]+)\s+[\d.]+\s+([\d,]+)\s+([\d,]+)\s+([\d,]+)\s+[\d.]+\s+([\d,]+)\s+([\d,]+)", t)
    if m:
        f["gen"], f["n"], f["req"], f["n_fin"], f["got"] = num(m.group(1)), num(m.group(2)), num(m.group(3)), num(m.group(5)), num(m.group(6))
    m = re.search(r"균등방식\s+([\d,]+)\s+[\d.]+\s+일괄청약방식\s+비례방식\s+([\d,]+)", t)
    if m:
        f["eq"], f["pr"] = num(m.group(1)), num(m.group(2))
        f["eq_p"] = plan_eq(t, f["eq"])
    # 스팩 보고서는 '청약 현황' 표의 일반투자자 청약수량이 배정수량으로 잘려 있다. 실제 청약수량은 '세부 배정 현황' 표 합계 줄에 있다.
    if f.get("n") and f.get("req") is not None:
        for m in re.finditer(r"합계\s+([\d,]+)\s+([\d,]+)\s+", t):
            if num(m.group(1)) == f["n"] and num(m.group(2)) > f["req"]:
                f["req"] = num(m.group(2))
                break
    f["rv"] = 2
    return f if "unlock" in f else {}


def parse_report_brokers(t, brokers):
    """증권발행실적보고서의 '일반투자자 세부 배정 현황[증권사]' 표마다 합계 줄을 읽어 증권사별 {n 청약건수, req 청약수량, eq 균등배정수량, pr 비례배정수량}."""
    hs = [(m.start(), m.group(1)) for m in re.finditer(r"\[([^\[\]]{2,14})\]", t) if "증권" in m.group(1)]
    out = {}
    for i, (pos, name) in enumerate(hs):
        key = next((b for b in brokers if b in name), None)
        if not key or key in out:
            continue
        seg = t[pos:hs[i + 1][0] if i + 1 < len(hs) else len(t)]
        m = re.search(r"합계\s+((?:[\d,]+(?:\.\d+)?%?\s+|-\s+){14})", seg)
        if not m:
            continue
        tk = re.findall(r"[\d,]+(?:\.\d+)?%?|-", m.group(1))
        if "-" in (tk[0], tk[1], tk[6], tk[11]):
            continue
        out[key] = {"n": num(tk[0]), "req": num(tk[1]), "eq": num(tk[6]), "pr": num(tk[11]), "eq_p": plan_eq(seg, num(tk[6]))}
    return out if len(out) == len(brokers) else {}


def parse_free_doc(t):
    """투자설명서 '상장 이후 유통물량 출회에 따른 위험' 문장에서 상장 직후 유통가능 주식수(와 상장예정주식수). 정정 전·후가 함께 있으면 마지막 것을 쓴다."""
    best = None
    for rx, order in (
        (r"상장\s*예정\s*주식수[^.]{0,60}?([\d,]{6,})\s*주\s*중\s*(?:약\s*)?([\d.]+)\s*%\s*에\s*해당하는\s*([\d,]+)\s*주는\s*상장\s*직후", "tpf"),
        (r"상장\s*예정\s*주식수\s*([\d,]{6,})\s*주(?:\([^)]*\))?\s*중\s*상장일\s*유통가능\s*물량은\s*([\d,]+)\s*주\s*\(상장일\s*기준\s*([\d.]+)\s*%\)", "tfp"),
    ):
        for m in re.finditer(rx, t):
            g = m.groups()
            tot, free, pct = (num(g[0]), num(g[2]), float(g[1])) if order == "tpf" else (num(g[0]), num(g[1]), float(g[2]))
            if tot and free and abs(free / tot * 100 - pct) < 0.1:
                best = {"tot": tot, "free": free}
        if best:
            return best
    for m in re.finditer(r"([\d,]+)\s*주\s*\(\s*([\d.]+)\s*%\s*\)\s*는\s*상장\s*직후\s*시장에서\s*유통", t):
        best = {"free": num(m.group(1))}
    return best


def find_corp(name, sub0):
    end = sub0
    bgn = sub0 - timedelta(days=75)
    k = norm(name)
    sp = is_spac(name)
    fb = None
    for pg in range(1, 12):
        r = call("list.json", bgn_de=bgn.strftime("%Y%m%d"), end_de=end.strftime("%Y%m%d"), pblntf_ty="C", pblntf_detail_ty="C001", page_no=pg, page_count=100)
        for x in r.get("list", []) or []:
            g = norm(x.get("corp_name", ""))
            if sp:   # 스팩은 이름을 짧게 줄여 맞추면 안 된다(예: 'SK증권스팩14호' 가 증권사 'SK증권' 으로 잘못 연결됨)
                if is_spac(g) and norm2(g) == norm2(k):
                    return x["corp_code"]
                if is_spac(g):   # 38 은 '신영스팩12호', DART 는 '신영해피투모로우제12호기업인수목적': 이름 앞부분과 호수가 같을 때만 보조로 쓴다
                    a_, b_ = re.fullmatch(r"(\D{2,})(\d+)", norm2(k)), re.fullmatch(r"(\D{2,})(\d+)", norm2(g))
                    if a_ and b_ and a_.group(2) == b_.group(2) and b_.group(1).startswith(a_.group(1)):
                        fb = fb or x["corp_code"]
                continue
            if g == k or (len(g) >= 3 and (k.startswith(g) or g.startswith(k))):
                return x["corp_code"]
        if r.get("status") != "000" or pg >= int(r.get("total_page", 1) or 1):
            break
    return fb


def dart_reports(corp, sub0):
    end = datetime.now(KST).strftime("%Y%m%d")
    bgn = (sub0 - timedelta(days=100)).strftime("%Y%m%d")
    r = call("list.json", corp_code=corp, bgn_de=bgn, end_de=end, page_count=100)
    out = {}
    for x in sorted(r.get("list", []) or [], key=lambda x: x.get("rcept_no", "")):
        nm = x.get("report_nm", "")
        if "발행실적보고서" in nm:
            out["실적"] = x["rcept_no"]
        elif "투자설명서" in nm:
            out["설명서"] = x["rcept_no"]
        elif "철회신고서" in nm:
            out["철회"] = x["rcept_no"]
        elif "증권신고서" in nm and not re.search(r"효력|제출요구|철회", nm):
            out["신고서"] = x["rcept_no"]
    if out.get("철회") and out.get("신고서") and out["신고서"] > out["철회"]:
        del out["철회"]   # 철회한 뒤 다시 신고서를 낸 경우
    return out


def yt_channels(key):
    """영상을 찾을 채널 목록 [{id, name}]. 두 번째 채널은 핸들로 찾는다(못 찾으면 첫 채널만)."""
    chs = [{"id": YT_CHANNEL, "name": "공모주투자의법칙"}]
    try:
        cid = name = None
        if key:
            j = json.loads(get("https://www.googleapis.com/youtube/v3/channels?" + urllib.parse.urlencode(
                {"part": "snippet", "forHandle": "@" + YT_HANDLE2, "key": key}), timeout=30))
            if j.get("items"):
                cid, name = j["items"][0]["id"], j["items"][0]["snippet"]["title"]
        if not cid:
            h = get("https://www.youtube.com/@" + urllib.parse.quote(YT_HANDLE2), timeout=30)
            m = re.search(r'"channelId":"(UC[\w-]{22})"', h) or re.search(r'channel/(UC[\w-]{22})', h)
            if m:
                cid = m.group(1)
                n = re.search(r'<meta property="og:title" content="([^"]+)"', h)
                name = n.group(1) if n else YT_HANDLE2
        if cid and cid != YT_CHANNEL:
            chs.append({"id": cid, "name": name or YT_HANDLE2})
            log(f"  YouTube 두 번째 채널: {name} ({cid})")
        elif not cid:
            log("  YouTube 두 번째 채널을 못 찾음")
    except Exception as ex:
        log("  YouTube 두 번째 채널 확인 실패:", ex)
    return chs


def yt_videos():
    """채널들의 영상 제목 목록. YT_API_KEY 가 있으면 채널당 최근 수백 개, 없으면 RSS(채널당 최근 15개)."""
    key = os.environ.get("YT_API_KEY", "").strip()
    vids = []
    for ch in yt_channels(key):
        got = []
        if key:
            try:
                tok = ""
                for _ in range(6):
                    u = "https://www.googleapis.com/youtube/v3/playlistItems?" + urllib.parse.urlencode(
                        {"part": "snippet", "maxResults": 50, "playlistId": "UU" + ch["id"][2:], "key": key, "pageToken": tok})
                    j = json.loads(get(u, timeout=30))
                    for it in j.get("items", []):
                        sn = it.get("snippet", {})
                        got.append({"id": sn.get("resourceId", {}).get("videoId"), "t": sn.get("title", ""), "p": (sn.get("publishedAt") or "")[:10], "c": ch["name"]})
                    tok = j.get("nextPageToken", "")
                    if not tok:
                        break
            except Exception as ex:
                log("  YouTube API 실패, RSS 로 대체:", ch["name"], ex)
                got = []
        if not got:
            try:
                x = get("https://www.youtube.com/feeds/videos.xml?channel_id=" + ch["id"], timeout=30)
                for en in re.findall(r"<entry>(.*?)</entry>", x, re.S):
                    i = re.search(r"<yt:videoId>([^<]+)</yt:videoId>", en)
                    t = re.search(r"<title>([^<]*)</title>", en)
                    d = re.search(r"<published>(\d{4}-\d\d-\d\d)", en)
                    if i and t:
                        got.append({"id": i.group(1), "t": re.sub(r"&amp;", "&", re.sub(r"&#39;|&apos;", "'", t.group(1))), "p": d.group(1) if d else "", "c": ch["name"]})
            except Exception as ex:
                log("  YouTube RSS 실패:", ch["name"], ex)
        log(f"  YouTube {ch['name']}: {len(got)}개")
        vids += got
    return vids


def yt_live_flags(ids, key):
    """영상이 라이브(생중계·다시보기)인지: True/False/None(모름). API 키가 있으면 liveStreamingDetails 로, 없으면 영상 페이지의 isLiveContent 로 본다."""
    out = {}
    if key:
        try:
            for i in range(0, len(ids), 50):
                j = json.loads(get("https://www.googleapis.com/youtube/v3/videos?" + urllib.parse.urlencode(
                    {"part": "liveStreamingDetails,contentDetails", "id": ",".join(ids[i:i + 50]), "key": key}), timeout=30))
                for it in j.get("items", []):
                    dur = re.match(r"PT(?:(\d+)M)?(?:(\d+)S)?$", it.get("contentDetails", {}).get("duration", ""))
                    short = bool(dur) and int(dur.group(1) or 0) == 0 and int(dur.group(2) or 0) <= 60
                    out[it["id"]] = bool(it.get("liveStreamingDetails")) or short   # 라이브·쇼츠는 '동영상' 탭이 아니다
            return out
        except Exception as ex:
            log("  YouTube videos API 실패:", ex)
    for v in ids:
        try:
            h = get("https://www.youtube.com/watch?v=" + v, timeout=30)
            m = re.search(r'"isLiveContent":(true|false)', h)
            out[v] = None if not m else (m.group(1) == "true")
        except Exception as ex:
            log("  영상 페이지 확인 실패", v, ex)
            out[v] = None
    return out


def yt_key(s):
    return re.sub(r"[\s\[\]()\-_·,.!?'\"~]", "", s or "")


def spac_key(s):
    """영상 제목·종목 이름 비교용: 38↔DART 와 같은 규칙(spac_core). '한화플러스스팩6호' = '한화플러스 6호 스팩' = '한화플러스제6호기업인수목적' -> '한화플러스6호'."""
    return spac_core(yt_key(s))


def yt_match(name, keys, title):
    tk = yt_key(title)
    if any(k and k in tk for k in keys):
        return True
    if is_spac(name) and is_spac(title):   # 증권사 이름 + 00호 + 스팩(또는 기업인수목적) 이 같으면 같은 종목
        nk = spac_key(name)
        return bool(re.search(r"\d+호$", nk)) and nk in spac_key(title)
    return False


def enrich(merged, old, alias):
    vids = None
    today = datetime.now(KST).date()
    for iid in sorted(merged):
        it = merged.get(iid)
        if not it or it.get("withdrawn") or not it.get("sub"):
            continue
        oe = (old.get(iid) or {}).get("ext") or {}
        done = it.get("listing") and it["listing"] < (today - timedelta(days=3)).isoformat()
        if oe.get("v") == EXT_VER and done and (oe.get("inst") or not oe.get("comp") or oe.get("inst_tried")) and (not oe.get("fin") or oe["fin"].get("rv") == 2) and (not is_spac(it["name"]) or (oe.get("corp_v") == 2 and oe.get("yt_v") == 2)):
            if oe.get("put_v") != 1 and oe.get("dart"):   # 환매청구권은 이 종목들도 한 번만 따로 읽는다
                oe = dict(oe)
                read_put(oe, oe["dart"])
                oe["put_v"] = 1
                oe["put_rc"] = "/".join(oe["dart"].get(k, "") for k in ("설명서", "신고서"))
            it["ext"] = oe   # 상장한 지 며칠 지난 종목은 읽는 방식이 바뀔 때까지 다시 읽지 않는다
            continue
        e = dict((old.get(iid) or {}).get("ext", {}))
        sub0 = date.fromisoformat(it["sub"][0])
        try:
            x = parse_ext38(text_of(get(DETAIL.format(iid[1:]))), it["brokers"])
            e.update(x)
            if x.get("alloc"):
                e.pop("alloc_r", None)
            elif x.get("alloc_r"):
                e.pop("alloc", None)
        except Exception as ex:
            log("  38 확장 실패", it["name"], ex)
        try:
            if not e.get("corp") or (is_spac(it["name"]) and e.get("corp_v") != 2):
                c = find_corp(it["name"], sub0)
                if is_spac(it["name"]) and e.get("corp_v") != 2:
                    for k in ("corp", "dart", "fin", "fin_rc", "fin_b", "free", "tot", "free_src"):   # 예전 이름 맞춤으로 엉뚱한 회사에 연결됐을 수 있어 처음부터 다시 읽는다
                        if k in ("free", "tot") and e.get("free_src") != "DART":
                            continue
                        e.pop(k, None)
                if c:
                    e["corp"] = c
                    e["corp_v"] = 2
            if e.get("corp"):
                reps = dart_reports(e["corp"], sub0)
                if reps:
                    e["dart"] = reps
                rc_ = "/".join(reps.get(k, "") for k in ("설명서", "신고서"))
                if rc_ and e.get("put_rc") != rc_:   # 새 공시(정정 포함)가 나오면 환매청구권을 다시 읽는다
                    read_put(e, reps)
                    e["put_rc"] = rc_
                    e["put_v"] = 1
                if e.get("alloc_r") and rc_ and e.get("gen_rc") != rc_:   # 38 이 아직 범위로만 적은 종목: 공시의 '정정 후' 표에 확정 배정이 있으면 읽는다
                    try:
                        for k_ in ("설명서", "신고서"):
                            if reps.get(k_):
                                g_ = parse_gen_doc(fetch_doc_text(reps[k_]))
                                if g_:
                                    e["gen_d"] = g_
                                    break
                        e["gen_rc"] = rc_
                    except Exception as ex3:
                        log("  공시 확정 배정 읽기 실패(다음에 재시도)", it["name"], ex3)
                if reps.get("설명서") and not e.get("free"):   # 38 에 '공모 후 유통가능 물량' 표가 없는 종목은 투자설명서에서 읽는다
                    fr = parse_free_doc(fetch_doc_text(reps["설명서"]))
                    if fr:
                        e.update(fr)
                        e["free_src"] = "DART"
                    else:
                        log("  유통가능 주식수를 못 읽음", it["name"], reps["설명서"])
                if reps.get("설명서") and not e.get("inst") and e.get("comp") and not e.get("inst_tried"):
                    # 38 이 기관 배정을 범위로만 적는 종목: 투자설명서의 '경쟁률은 우리사주 우선배정 물량을 제외한 N주를 기준으로 산출' 문장에서 읽는다
                    try:
                        dtxt = fetch_doc_text(reps["설명서"])
                    except Exception as ex2:
                        dtxt = None
                        log("  투자설명서 다시 읽기 실패(다음에 재시도)", it["name"], ex2)
                    if dtxt is not None:
                        e["inst_tried"] = 1
                        dm = re.findall(r"경쟁률은\s*우리사주[^.]{0,40}?제외한\s*([\d,]+)\s*주를\s*기준으로\s*산출", dtxt)
                        if dm and e.get("tot") and 0 < num(dm[-1]) < e["tot"]:
                            e["inst"] = num(dm[-1])
                        else:
                            log("  기관 배정 수량을 못 읽음", it["name"])
                if reps.get("실적") and (e.get("fin_rc") != reps["실적"] or e.get("v") != EXT_VER or (e.get("fin") or {}).get("rv") != 2):
                    rtxt = fetch_doc_text(reps["실적"])
                    f = parse_report(rtxt)
                    e.pop("fin_b", None)
                    if f:
                        e["fin"], e["fin_rc"] = f, reps["실적"]
                        if len(it["brokers"]) > 1:
                            fb = parse_report_brokers(rtxt, it["brokers"])
                            # 증권사별 값을 모두 더하면 보고서 전체 값과 같아야 한다. 다르면 쓰지 않는다.
                            if fb and f.get("req") and sum(v["req"] for v in fb.values()) == f["req"] and sum(v["eq"] for v in fb.values()) == f.get("eq") and sum(v["pr"] for v in fb.values()) == f.get("pr"):
                                e["fin_b"] = fb
                            else:
                                log("  증권사별 최종 경쟁률 검증 실패", it["name"])
                    else:
                        e.pop("fin", None); e.pop("fin_rc", None)
                        log("  실적보고서 표를 못 읽음", it["name"], reps["실적"])
        except Exception as ex:
            log("  DART 확장 실패", it["name"], ex)
        try:
            if vids is None:
                vids = yt_videos()
                log(f"  YouTube 영상 {len(vids)}개 읽음")
            keys = [yt_key(it["name"])] + [yt_key(a) for a in alias.get(iid, [])]
            cand = {v["id"]: v for v in vids if v.get("id") and yt_match(it["name"], keys, v["t"])}
            apikey = os.environ.get("YT_API_KEY", "").strip()
            # API 키가 없을 때만 예전에 '라이브 아님'으로 확인한 영상을 그대로 둔다. 키가 있으면 매번 다시 확인한다.
            keep = {} if apikey else {v["id"]: v for v in e.get("yt", []) if v.get("lv") is False}
            todo = [i for i in cand if i not in keep]
            flags = yt_live_flags(todo, apikey) if todo else {}
            for i in todo:
                lv = flags.get(i)
                if lv is None:   # 확인이 안 되면 제목에 라이브 표시가 있는 것만 뺀다
                    lv = bool(re.search(r"LIVE|라이브|실시간", cand[i]["t"], re.I))
                if not lv:
                    keep[i] = dict(cand[i], lv=False)
            e["yt"] = sorted(keep.values(), key=lambda v: v.get("p", ""), reverse=True)[:4]
            log(f"  영상 {it['name']}: 후보 {len(cand)}개 → 동영상 {len(keep)}개 (API {'사용' if apikey else '없음'}, 라이브/쇼츠 제외 {sum(1 for i in todo if i not in keep)}개)")
        except Exception as ex:
            log("  YouTube 실패", ex)
        e["v"] = EXT_VER
        e["yt_v"] = 2   # 스팩 영상 매칭 규칙(증권사+호수) 적용 버전
        it["ext"] = e


NV_UA = "Mozilla/5.0 (iPhone; CPU iPhone OS 16_0 like Mac OS X)"


def scrape_nw():
    """38 '신규상장' 목록: 상장일·공모가·시초가·첫날종가. {norm(이름): {'ld','price','o','d1'}} (첫날종가가 아직 없으면 d1 은 없다)"""
    out = {}
    for pg in range(1, 7):
        try:
            h = get("http://www.38.co.kr/html/fund/index.htm?o=nw" + ("" if pg == 1 else f"&page={pg}"))
        except Exception as e:
            log("38 신규상장 목록 실패", pg, e)
            break
        n0 = len(out)
        for r in re.findall(r"<tr[^>]*>(.*?)</tr>", h, re.S):
            t = re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", r).replace("&nbsp;", " ")).strip()
            m = re.match(r"^(.+?)\s+(20\d\d)/(\d\d)/(\d\d)\s+(?:[\d,]+|-)\s+(?:-?[\d.]+)?\s*%\s+([\d,]+)\s+(?:-?[\d.]+)?\s*%\s+([\d,]+)\s+(-?[\d.]+)%(?:\s+([\d,]+))?\s*$", t)
            if not m:
                continue
            price, o, pct = num(m.group(5)), num(m.group(6)), float(m.group(7))
            if not price or not o or abs((o / price - 1) * 100 - pct) > 0.02:   # 시초/공모 비율과 맞지 않으면 읽지 않는다
                continue
            e = {"ld": f"{m.group(2)}-{m.group(3)}-{m.group(4)}", "price": price, "o": o}
            if m.group(8):
                e["d1"] = num(m.group(8))
            out.setdefault(norm(m.group(1)), e)
        if len(out) == n0:
            break
    log(f"38 신규상장 {len(out)}종목")
    return out


def naver_final(t):
    """네이버 공모주 상세의 '청약결과' 증권사별 '최종 비례배정 경쟁률' -> {짧은 증권사명: 경쟁률}"""
    out = {}
    ms = list(re.finditer(r'IpoDetailSchedule_name[^>]*>\s*([^<]+?)\s*</span>', t))
    for i, m in enumerate(ms):
        seg = t[m.end():ms[i + 1].start() if i + 1 < len(ms) else m.end() + 1500]
        r = re.search(r"최종\s*비례배정\s*경쟁률</dt><dd[^>]*>\s*([\d,]+(?:\.\d+)?)\s*:\s*1", seg)
        b = short_broker(m.group(1))
        if r and b:
            v = float(r.group(1).replace(",", ""))
            if v > 0:
                out[b] = v
    return out


def naver_extra(t):
    """네이버 '청약결과' 증권사별 {짧은 증권사명: {n: 최종 청약 신청건수, c: 최종 종합 청약 경쟁률}}"""
    out = {}
    ms = list(re.finditer(r'IpoDetailSchedule_name[^>]*>\s*([^<]+?)\s*</span>', t))
    for i, m in enumerate(ms):
        seg = t[m.end():ms[i + 1].start() if i + 1 < len(ms) else m.end() + 1500]
        n = re.search(r"최종\s*청약\s*신청건수</dt><dd[^>]*>\s*([\d,]+)\s*건", seg)
        c = re.search(r"최종\s*종합\s*청약\s*경쟁률</dt><dd[^>]*>\s*([\d,]+(?:\.\d+)?)\s*:\s*1", seg)
        b = short_broker(m.group(1))
        if n and c and b:
            out[b] = {"n": num(n.group(1)), "c": float(c.group(1).replace(",", ""))}
    return out


def naver_listings(need, pxn=()):
    """38에 상장일이 없는 종목만, 네이버 증권 공모주 페이지의 상장일을 보조로 읽는다. {norm(이름): 'YYYY-MM-DD'}"""
    out, px, fin = {}, {}, {}
    fin["_x"] = {}
    try:
        h = get("https://stock.naver.com/ipo", ua=NV_UA)
        codes = set(re.findall(r"/ipo/([A-Z0-9]{7})\b", h)) | set(re.findall(r'"(?:itemCode|stockCode|code)"\s*:\s*"([A-Z0-9]{7})"', h))
        for pt in ("listing-upcoming", "subscribing", "subscribing-upcoming"):   # 첫 화면에 안 나오는 상장대기·청약 종목은 네이버 앱이 쓰는 목록 주소로 찾는다
            for pg in (1, 2, 3):
                try:
                    lst = (json.loads(get(f"https://m.stock.naver.com/front-api/ipo/progress?progressType={pt}&page={pg}", ua=NV_UA)).get("result") or {}).get("ipoList") or []
                except Exception as e:
                    log("  네이버 진행 목록 실패", pt, pg, e)
                    break
                codes |= {x["ipoCode"] for x in lst if re.fullmatch(r"[A-Z0-9]{7}", x.get("ipoCode") or "")}
                if len(lst) < 10:
                    break
        log(f"네이버 공모주 코드 {len(codes)}개")
        for c in sorted(codes):
            try:
                t = get("https://stock.naver.com/ipo/" + c, ua=NV_UA)
                nm = re.search(r"<title>\s*([^<]*?)\s*-\s*Npay", t)
                if nm:
                    fv = naver_final(t)
                    if fv:
                        fin[norm(nm.group(1))] = fv
                        fin["_x"][norm(nm.group(1))] = naver_extra(t)
                m = re.search(r"IpoDetailSchedule_text[^>]*>\s*상장\s*</span><span[^>]*>\s*(20\d{2})\.\s*(\d{1,2})\.\s*(\d{1,2})\.", t)
                if nm and m and norm(nm.group(1)) in need:
                    out[norm(nm.group(1))] = f"{int(m.group(1)):04d}-{int(m.group(2)):02d}-{int(m.group(3)):02d}"
                if nm and norm(nm.group(1)) in pxn:
                    o = re.search(r"시초가</dt><dd[^>]*>\s*([\d,]+)", t)
                    cp = re.search(r'"itemCode":"%s","currentPrice":(\d+)' % c[1:], t) or re.search(r'"itemCode":"%s"[^{}]{0,200}?"currentPrice":(\d+)' % c[1:], t)
                    st = re.search(r'"marketStatus":"(\w+)"', t)
                    at = re.search(r'"localTradedAt":"(20\d\d-\d\d-\d\dT\d\d:\d\d)', t)
                    if o and cp and st and at:
                        px[norm(nm.group(1))] = {"o": num(o.group(1)), "c": int(cp.group(1)), "st": st.group(1), "at": at.group(1)}
            except Exception as e:
                log("  네이버 상세 실패", c, e)
    except Exception as e:
        log("네이버 목록 실패(무시):", e)
    log("네이버 상장일 보조:", out, "주가:", px, "최종 비례:", fin)
    return out, px, fin


def fetch_holidays(old):
    """공공데이터포털 한국천문연구원 특일 정보(공휴일·대체공휴일·임시공휴일). 올해와 내년 전체. 실패하면 이전 값을 그대로 쓴다."""
    key = os.environ.get("HOLIDAY_API_KEY")
    if not key:
        return old
    y0 = datetime.now(KST).year
    dates = {}
    try:
        for y in (y0, y0 + 1):
            for mth in range(1, 13):
                u = "http://apis.data.go.kr/B090041/openapi/service/SpcdeInfoService/getRestDeInfo?" + urllib.parse.urlencode({"serviceKey": key, "solYear": y, "solMonth": f"{mth:02d}", "_type": "json", "numOfRows": 50})
                r = json.loads(get(u, timeout=30))
                body = (r.get("response") or {}).get("body") or {}
                if (r.get("response") or {}).get("header", {}).get("resultCode") not in ("00", None):
                    raise RuntimeError("특일 API 오류 " + str((r.get("response") or {}).get("header")))
                items = (body.get("items") or {})
                items = items.get("item") if isinstance(items, dict) else None
                if isinstance(items, dict):
                    items = [items]
                for x in items or []:
                    if x.get("isHoliday") == "Y":
                        d = str(x["locdate"])
                        dates[f"{d[:4]}-{d[4:6]}-{d[6:]}"] = x.get("dateName", "")
    except Exception as ex:
        log("특일 정보 읽기 실패(이전 값 유지):", ex)
        return old
    # 내년 1월 1일이 있으면 내년 월력요항이 확정돼 있다고 보고 내년 말까지, 없으면 올해 말까지만 믿는다
    through = f"{y0 + 1}-12-31" if f"{y0 + 1}-01-01" in dates else f"{y0}-12-31"
    return {"dates": dict(sorted(dates.items())), "through": through}


def derive_alloc(merged):
    for it_ in merged.values():   # 공시(정정 후 표)에서 확정 배정을 읽은 단일 증권사 종목은 그 숫자를 배정 수량으로 쓴다
        e_ = it_.get("ext") or {}
        if e_.get("gen_d") and not e_.get("alloc") and len(it_.get("brokers") or []) == 1:
            lo_, hi_ = (e_.get("alloc_r") or {}).get(it_["brokers"][0], [None, None])
            if lo_ is None or lo_ <= e_["gen_d"] <= hi_:
                e_["alloc"] = {it_["brokers"][0]: e_["gen_d"]}
                e_.pop("alloc_r", None)

    for it_ in merged.values():   # 청약이 끝나 실적보고서가 나왔으면, 증권사별 배정 수량은 보고서의 실제 배정(균등+비례)을 쓴다
        e_ = it_.get("ext") or {}
        f_, fb_ = e_.get("fin") or {}, e_.get("fin_b")
        if not f_.get("got") or e_.get("alloc"):   # 이미 확정 배정 물량이 있으면(미달로 실제 배정이 달랐어도) 그대로 둔다
            continue
        al_ = None
        if fb_ and len(fb_) == len(it_.get("brokers") or []):
            al_ = {b: v["eq"] + v["pr"] for b, v in fb_.items()}
        elif len(it_.get("brokers") or []) == 1:
            al_ = {it_["brokers"][0]: f_["got"]}
        if al_ and sum(al_.values()) == f_["got"]:
            e_["alloc"] = al_
            e_.pop("alloc_r", None)


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
    today_s = datetime.now(KST).date().isoformat()
    need = {norm(d["name"]) for d in scraped if not d["listing"]}
    today_names = {norm(d["name"]) for d in scraped if d["listing"] and iso(d["listing"]) == today_s}
    nv, pxs, nvfin = naver_listings(need, today_names)
    nwpx = scrape_nw()
    for d in scraped:
        item = {"id": "n" + d["no"], "name": d["name"], "brokers": list(d["brokers"]), "sub": [iso(d["sub"][0]), iso(d["sub"][1])]}
        flags = []
        if d["refund"]:
            item["refund"] = iso(d["refund"])
        if d["listing"]:
            item["listing"] = iso(d["listing"])
        elif nv.get(norm(d["name"])) and nv[norm(d["name"])] >= item["sub"][1]:
            item["listing"] = nv[norm(d["name"])]   # 38 에 아직 없을 때만 네이버 값. 38 값이 올라오면 그 값이 우선한다.
        if d["price"] is not None:
            item["price"] = d["price"]
        if d["band"]:
            item["band"] = d["band"]
        if d.get("margin"):
            item["margin"] = d["margin"]
        item["mchk"] = 1
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
                # 증권사별 최소 청약수량(공시 표). 진행·예정 종목만, 공시가 바뀌었을 때만 다시 읽는다.
                po = old.get(item["id"], {})
                if item["sub"][1] >= today_s and m.get("rcept"):
                    if po.get("minq_rc") == m["rcept"] and po.get("elig_v") == ELIG_VER and "minq" in po:
                        for k in ("minq", "minq_rc", "acct", "fee", "elig_v"):
                            if k in po:
                                item[k] = po[k]
                    else:
                        try:
                            q, ac, fe = read_doc_info(doc_text_any(m), item["brokers"])
                            if q:
                                item["minq"], item["minq_rc"] = q, m["rcept"]
                            elif "minq" in po:   # 새 공시에서 표를 못 읽어도 이전 값은 지우지 않는다
                                item["minq"], item["minq_rc"] = po["minq"], po.get("minq_rc")
                            if ac:
                                item["acct"] = ac
                            elif "acct" in po:
                                item["acct"] = po["acct"]
                            if fe:
                                item["fee"] = fe
                            elif "fee" in po:
                                item["fee"] = po["fee"]
                            item["elig_v"] = ELIG_VER
                        except Exception as e:
                            log("  공시 본문 실패", item["name"], e)
                            for k in ("minq", "minq_rc", "acct", "fee"):
                                if k in po:
                                    item[k] = po[k]
                else:
                    for k in ("minq", "minq_rc", "acct", "fee", "elig_v"):
                        if k in po:
                            item[k] = po[k]
            elif item["sub"][1] >= (datetime.now(KST) - timedelta(days=14)).date().isoformat():
                flags.append("DART 공시 못 찾음")   # 지난 종목은 DART 조회 기간 밖일 수 있어 표시하지 않는다
        if not item["brokers"]:
            flags.append("주관사 없음")
        if "listing" not in item:
            flags.append("상장일 미정")
        po0 = old.get(item["id"], {})
        for k in ("minq", "minq_rc", "minq_v", "acct", "fee", "elig_v", "nvr", "nvx", "nvr_tried"):   # DART 에서 못 찾은 날에도 이미 읽어 둔 값은 지우지 않는다
            if k not in item and k in po0:
                item[k] = po0[k]
        item["flags"] = flags
        merged[item["id"]] = item

    for it_ in merged.values():   # 네이버 증권사별 최종 비례배정 경쟁률. 목록에서 빠진 지난 종목은 DART 종목코드로 상세 페이지를 직접 읽는다(종목당 한 번)
        v_ = nvfin.get(norm(it_.get("name", "")))
        if v_:
            it_["nvr"] = v_
            x_ = (nvfin.get("_x") or {}).get(norm(it_.get("name", "")))
            if x_:
                it_["nvx"] = x_
            continue
        e_ = it_.get("ext") or {}
        if it_.get("nvr") or it_.get("nvr_tried") or not (e_.get("fin") and e_.get("corp")):
            continue
        try:
            sc = (call("company.json", corp_code=e_["corp"]).get("stock_code") or "").strip()
            if len(sc) == 6:
                v_ = naver_final(get("https://stock.naver.com/ipo/A" + sc, ua=NV_UA))
                it_["nvr_tried"] = 1
                if v_:
                    it_["nvr"] = v_
                log("  네이버 최종 비례 경쟁률", it_["name"], v_)
        except Exception as ex:
            log("  네이버 최종 비례 경쟁률 실패", it_.get("name"), ex)

    now_k = datetime.now(KST)
    for it_ in merged.values():   # 상장 첫날 시초가·종가(38 신규상장 목록) + 상장 당일 장중 현재가(네이버)
        if not it_.get("name") or not it_.get("listing"):
            continue
        n_ = norm(it_["name"])
        r_ = nwpx.get(n_)
        px = None
        if r_ and r_["ld"] == it_["listing"] and (it_.get("price") is None or r_["price"] == it_["price"]):
            px = {"o": r_["o"]}
            if r_.get("d1") and (it_["listing"] < today_s or (it_["listing"] == today_s and now_k.hour * 60 + now_k.minute >= 15 * 60 + 40)):
                px["d1"] = r_["d1"]
            elif it_["listing"] == today_s and pxs.get(n_):
                px["c"], px["st"], px["at"] = pxs[n_]["c"], pxs[n_]["st"], pxs[n_]["at"]
        if px:
            it_["px"] = px
        elif it_.get("px") and not r_:
            pass   # 이번 갱신에서 목록에 없으면 직전 값 유지
        else:
            it_.pop("px", None)
    try:
        backfill(merged, today_s)
    except Exception as e:
        log("지난 종목 보완 실패(무시):", e)

    try:
        ov0 = json.load(open(os.path.join(ROOT, "collector", "overrides.json"), encoding="utf-8"))
    except Exception:
        ov0 = {}
    try:
        enrich(merged, old, {k: v.get("alias", []) for k, v in ov0.items()})
    except Exception as e:
        log("상세 확장 실패(무시):", e)
    derive_alloc(merged)

    # 공시 표만으로는 확정할 수 없어 사람이 직접 확인한 값(collector/overrides.json)은 항상 우선한다
    try:
        ov = json.load(open(os.path.join(ROOT, "collector", "overrides.json"), encoding="utf-8"))
        for oid, o in ov.items():
            if oid in merged and o.get("minq"):
                merged[oid]["minq"] = o["minq"]
                merged[oid]["minq_v"] = MINQ_VER
    except Exception as e:
        log("overrides.json 읽기 실패(무시):", e)

    # ---- 변경·신규 알림 기록 (이전 값과 비교) ----
    now = datetime.now(KST).strftime("%Y-%m-%dT%H:%M")
    today_iso = datetime.now(KST).date().isoformat()
    tracking = bool(old)          # 첫 실행에는 알림을 만들지 않는다
    changes = []

    def md(x):
        return f"{int(x[5:7])}/{int(x[8:10])}"

    def rng(sub):
        return md(sub[0]) + ("~" + md(sub[1]) if sub[1] != sub[0] else "")

    def orig(it):
        return "철회 · 기존 일정: 청약 " + rng(it["sub"])

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
    for oid, it in list(merged.items()):   # DART 에 철회신고서가 올라온 종목(38 이 아직 반영하지 않았거나 읽히지 않을 때)
        if (it.get("ext") or {}).get("dart", {}).get("철회") and not (it.get("listing") and it["listing"] < today_iso):
            merged.pop(oid)
            log("  제외(DART 철회신고서):", it["name"])
            if tracking and oid in old:
                chg("chg", it, orig(it))
    for d in cancelled:
        it = merged.pop("n" + d["no"], None)
        if it and tracking and "n" + d["no"] in old:
            chg("chg", it, orig(it))
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
                    chg("chg", p, orig(p))

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
    hol = fetch_holidays(old_doc.get("holidays"))
    if before == json.dumps({p["id"]: p for p in ipos}, sort_keys=True, ensure_ascii=False) and old_doc.get("v") == SCHEMA and not changes and hol == old_doc.get("holidays"):
        log("변경 없음 - 파일을 건드리지 않습니다.")
        return
    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    doc = {"v": SCHEMA, "updatedAt": datetime.now(KST).strftime("%Y-%m-%dT%H:%M"), "source": "38커뮤니케이션+OpenDART", "ipos": ipos, "changes": allc}
    if hol:
        doc["holidays"] = hol
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


def probe():
    out = {}
    doc = json.load(open(OUT, encoding="utf-8"))
    names = ("엔키화이트햇", "유캐스트", "엠비디", "하나에어로다이내믹스", "영광", "브릴스", "범한메카텍", "마더스제약", "신영스팩12호", "한화플러스스팩6호", "네오사피엔스", "이노보테라퓨틱스")
    for p in doc["ipos"]:
        if p["name"] not in names:
            continue
        d = (p.get("ext") or {}).get("dart") or {}
        o = {}
        for k in ("설명서", "신고서"):
            if not d.get(k):
                continue
            try:
                t = fetch_doc_text(d[k])
                ms = [m.start() for m in re.finditer(r"환매\s*청구권", t)]
                o[k] = {"n": len(ms), "ctx": [t[max(0, a - 120):a + 330] for a in ms[:3]] + ([t[max(0, ms[-1] - 120):ms[-1] + 330]] if len(ms) > 3 else [])}
            except Exception as ex:
                o[k] = str(ex)[:120]
        out[p["name"]] = o
    doc["probe"] = out
    json.dump(doc, open(OUT, "w", encoding="utf-8"), ensure_ascii=False, indent=1, default=str)


if __name__ == "__main__":
    if os.path.exists("collector/PROBE"):
        probe()
    else:
        main()
