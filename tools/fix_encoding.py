"""Repair source text that was double-encoded by a Windows PowerShell read/write round trip
(UTF-8 bytes read as cp1252 and written back as UTF-8, giving the classic "a-tilde" mojibake).

Windows PowerShell 5.1 `Get-Content -Raw` assumes the ANSI code page when a file has no BOM,
so editing a UTF-8 source file with a PowerShell one-liner corrupts every non-ASCII character.
This script detects that, reverses it, and reports any non-ASCII characters left so they can be
replaced with ASCII where the file does not need them.

Usage:  python tools/fix_encoding.py <file> [<file> ...]
"""
import sys

# written as escapes so this file stays pure ASCII and cannot damage itself
SUBS = {
    "…": "...",    # horizontal ellipsis
    "•": "-",      # bullet
    "²": "^2",     # superscript two
    "³": "^3",     # superscript three
    "µ": "u",      # micro sign
    "‘": "'", "’": "'",
    "“": '"', "”": '"',
    "–": "-", "—": "-",
}


def fix(path: str, to_ascii: bool = True) -> None:
    txt = open(path, "rb").read().decode("utf-8-sig")
    try:
        # a no-op for pure ASCII; only genuine mojibake changes here
        repaired = txt.encode("cp1252", "strict").decode("utf-8", "strict")
        if repaired != txt:
            print("%s: repaired double-encoded text" % path)
            txt = repaired
    except (UnicodeEncodeError, UnicodeDecodeError):
        pass
    if to_ascii:
        for a, b in SUBS.items():
            txt = txt.replace(a, b)
    left = sorted(set(c for c in txt if ord(c) > 127))
    if left:
        print("%s: non-ASCII characters remain: %s" % (path, " ".join(left)))
    with open(path, "w", encoding="utf-8", newline="\n") as fh:
        fh.write(txt)


if __name__ == "__main__":
    for p in sys.argv[1:]:
        fix(p)
