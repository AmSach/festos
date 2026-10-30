"""Small, dependency-free string similarity tools for entity resolution."""
import re
import unicodedata


def norm(s):
    s = unicodedata.normalize("NFKD", s or "").encode("ascii", "ignore").decode().lower()
    s = re.sub(r"[^a-z ]+", " ", s)
    return re.sub(r"\s+", " ", s).strip()


def jaro(s1, s2):
    if s1 == s2:
        return 1.0
    l1, l2 = len(s1), len(s2)
    if not l1 or not l2:
        return 0.0
    md = max(l1, l2) // 2 - 1
    m1 = [False] * l1
    m2 = [False] * l2
    matches = 0
    for i in range(l1):
        lo, hi = max(0, i - md), min(i + md + 1, l2)
        for j in range(lo, hi):
            if not m2[j] and s1[i] == s2[j]:
                m1[i] = m2[j] = True
                matches += 1
                break
    if not matches:
        return 0.0
    t = 0
    k = 0
    for i in range(l1):
        if m1[i]:
            while not m2[k]:
                k += 1
            if s1[i] != s2[k]:
                t += 1
            k += 1
    t /= 2
    return (matches / l1 + matches / l2 + (matches - t) / matches) / 3


def jaro_winkler(a, b, p=0.1):
    a, b = norm(a), norm(b)
    j = jaro(a, b)
    pre = 0
    for x, y in zip(a, b):
        if x != y or pre == 4:
            break
        pre += 1
    return j + pre * p * (1 - j)


def phonetic(s):
    """Crude phonetic key tuned for Indian names in Latin script:
    collapses doubled vowels/consonants and common transliteration variants."""
    s = norm(s)
    out = []
    for w in s.split():
        w = w.replace("aa", "a").replace("ee", "i").replace("oo", "u").replace("w", "v")
        w = w.replace("ph", "f").replace("sh", "s").replace("th", "t").replace("dh", "d").replace("kh", "k")
        w = re.sub(r"(.)\1+", r"\1", w)
        w = w[0] + re.sub(r"[aeiouy]", "", w[1:]) if w else w
        out.append(w)
    return " ".join(out)


def digits(s):
    return re.sub(r"\D", "", s or "")


def normalize_phone(s):
    d = digits(s)
    if len(d) == 12 and d.startswith("91"):
        d = d[2:]
    if len(d) == 10 and d[0] in "6789":
        return "+91-" + d
    return None


def is_placeholder_phone(s):
    d = digits(s)[-10:]
    if not d:
        return True
    if len(set(d)) <= 2:
        return True
    if d in ("9876543210", "1234567890", "0123456789"):
        return True
    return False
