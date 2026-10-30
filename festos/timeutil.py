from datetime import datetime, timedelta

FMT = "%Y-%m-%d %H:%M"
NIGHT0 = datetime(2026, 10, 29)          # night index 0 = night starting 29 Oct
NIGHT_LABELS = ["Oct 29", "Oct 30", "Oct 31", "Nov 1", "Nov 2"]
N_NIGHTS = len(NIGHT_LABELS)


def p(s):
    if s is None or s == "":
        return None
    if isinstance(s, datetime):
        return s
    s = s.strip().replace("T", " ")
    for f in (FMT, "%Y-%m-%d %H:%M:%S", "%Y-%m-%d"):
        try:
            return datetime.strptime(s[:19] if f.endswith("%S") else s[:16] if f == FMT else s[:10], f)
        except ValueError:
            continue
    return None


def f(dt):
    return dt.strftime(FMT) if dt else None


def hhmm(s):
    h, m = s.split(":")
    return int(h), int(m)


def nights_mask(arr, dep, core="23:00-06:00", min_h=3):
    """Bitmask of nights for which this stay needs a bed (see assumptions)."""
    a, d = p(arr), p(dep)
    if not a or not d or d <= a:
        return 0
    cs, ce = core.split("-")
    sh, sm = hhmm(cs)
    eh, em = hhmm(ce)
    mask = 0
    for i in range(N_NIGHTS):
        day = NIGHT0 + timedelta(days=i)
        ws = day.replace(hour=sh, minute=sm)
        we = (day + timedelta(days=1)).replace(hour=eh, minute=em)
        ov = (min(d, we) - max(a, ws)).total_seconds() / 3600
        if ov >= min_h:
            mask |= 1 << i
    return mask


def mask_list(mask):
    return [i for i in range(N_NIGHTS) if mask >> i & 1]


def mask_label(mask):
    l = mask_list(mask)
    if not l:
        return "—"
    if len(l) == 1:
        return NIGHT_LABELS[l[0]]
    return f"{NIGHT_LABELS[l[0]]} → {NIGHT_LABELS[l[-1]]} ({len(l)} nights)"


def popcount(m):
    return bin(m).count("1")


def overlap(a1, a2, b1, b2):
    return a1 < b2 and b1 < a2


def minutes_between(a, b):
    return (p(b) - p(a)).total_seconds() / 60
