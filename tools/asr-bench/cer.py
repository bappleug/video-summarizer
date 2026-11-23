#!/usr/bin/env python3
"""字符级 CER：cer.py <参考文本> <待测文本>。归一化：NFKC、去标点空白、小写。"""
import re
import sys
import unicodedata


def norm(s):
    s = unicodedata.normalize("NFKC", s).lower()
    return re.sub(r"[^\w\u4e00-\u9fff]+", "", s)


def lev(a, b):
    if len(a) < len(b):
        a, b = b, a
    prev = list(range(len(b) + 1))
    for i, ca in enumerate(a, 1):
        cur = [i]
        for j, cb in enumerate(b, 1):
            cur.append(min(prev[j] + 1, cur[-1] + 1, prev[j - 1] + (ca != cb)))
        prev = cur
    return prev[-1]


def main():
    ref = norm(open(sys.argv[1]).read())
    hyp = norm(open(sys.argv[2]).read())
    d = lev(ref, hyp)
    print(f"CER {d / len(ref):.3%}  (编辑距离 {d} / 参考 {len(ref)} 字 / 待测 {len(hyp)} 字)")


if __name__ == "__main__":
    main()
