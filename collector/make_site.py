#!/usr/bin/env python3
"""index.html(게시용 조각) -> docs/index.html (GitHub Pages 용 완전한 문서). 화면을 고칠 때마다 한 번 실행."""
import os
root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
body = open(os.path.join(root, "index.html"), encoding="utf-8").read()
head = ('<!doctype html>\n<html lang="ko">\n<head>\n<meta charset="utf-8">\n'
        '<meta name="viewport" content="width=device-width, initial-scale=1, viewport-fit=cover">\n'
        '<meta name="theme-color" content="#1F5FD6">\n'
        '<link rel="icon" type="image/png" href="icon-192.png">\n<link rel="apple-touch-icon" href="apple-touch-icon.png">\n'
        '<meta name="apple-mobile-web-app-title" content="공모주">\n'
        '<style>:root{color-scheme:light;padding-top:env(safe-area-inset-top,0px);padding-bottom:env(safe-area-inset-bottom,0px)}'
        'body{margin:0;font:14px system-ui,sans-serif}img{max-width:100%}[hidden]{display:none!important}</style>\n'
        '</head>\n<body>\n')
os.makedirs(os.path.join(root, "docs", "data"), exist_ok=True)
open(os.path.join(root, "docs", "index.html"), "w", encoding="utf-8").write(head + body + "\n</body>\n</html>\n")
print("docs/index.html 생성")
