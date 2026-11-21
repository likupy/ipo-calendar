#!/usr/bin/env python3
"""진행·예정 종목 중 최소 증거금을 못 채운 종목을 GitHub 이슈(라벨: 최소증거금)로 알린다.
목록이 있으면 이슈를 만들고/갱신하고(새 종목이 늘면 댓글), 모두 채워지면 이슈를 닫는다."""
import json, os, subprocess
from datetime import datetime, timezone, timedelta

LABEL = "최소증거금"
KST = timezone(timedelta(hours=9))
root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
d = json.load(open(os.path.join(root, "docs", "data", "ipo.json"), encoding="utf-8"))
today = datetime.now(KST).date().isoformat()


def gh(*a):
    return subprocess.run(["gh", *a], capture_output=True, text=True)


rows = []
for p in d.get("ipos", []):
    if not p.get("sub") or p["sub"][1] < today:
        continue
    why = []
    if not p.get("margin"):
        why.append("증거금률 없음(38)")
    if not p.get("minq"):
        why.append("최소 청약수량 못 읽음(DART 공시 표)")
    if why:
        rows.append((p["name"], p["sub"][0][5:] + "~" + p["sub"][1][5:], ", ".join(why)))

gh("label", "create", LABEL, "--color", "FBCA04")
cur = gh("issue", "list", "--state", "open", "--label", LABEL, "--json", "number,body").stdout
cur = json.loads(cur or "[]")
num = cur[0]["number"] if cur else None
body_old = cur[0]["body"] if cur else ""

if not rows:
    if num:
        gh("issue", "close", str(num), "--comment", "모든 진행·예정 종목의 최소 증거금이 채워졌어요.")
    print("미확인 종목 없음")
    raise SystemExit

body = ("아래 진행·예정 종목은 최소 증거금을 표시하지 못하고 있어요. Claude에게 종목 이름을 알려주면 공시 표 형식을 확인해 읽는 규칙을 고쳐요.\n\n"
        + "\n".join(f"- **{n}** (청약 {s}): {w}" for n, s, w in rows)
        + f"\n\n(마지막 확인 {datetime.now(KST).strftime('%m/%d %H:%M')})")
title = f"최소 증거금 못 채운 종목 {len(rows)}개"
if not num:
    gh("issue", "create", "--label", LABEL, "--title", title, "--body", body)
else:
    new = [n for n, _, _ in rows if f"**{n}**" not in body_old]
    gh("issue", "edit", str(num), "--title", title, "--body", body)
    if new:
        gh("issue", "comment", str(num), "--body", "새로 추가된 종목: " + ", ".join(new))
print("미확인", len(rows), "개:", [r[0] for r in rows])
