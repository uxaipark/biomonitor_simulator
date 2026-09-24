"""각국 환자·의료인 식별번호 생성기 — 실제 검증 규칙(체크 디지트)을 통과하는 합성 번호.

실존 인물과 겹치지 않도록 가능한 곳은 시험용 대역을 쓴다(NHS 999xxxxxxx, 미국 전화 555-01xx 등).
각 함수는 random.Random 을 받아 결정적으로 번호를 만든다.
"""
from __future__ import annotations

import random


def luhn_digit(payload: str) -> int:
    """payload 뒤에 붙일 Luhn 체크 디지트."""
    total = 0
    for i, ch in enumerate(reversed(payload)):
        d = int(ch)
        if i % 2 == 0:                      # 체크 디지트가 붙으면 이 자리가 짝수 번째(2배) 자리
            d *= 2
            if d > 9:
                d -= 9
        total += d
    return (10 - total % 10) % 10


def luhn_ok(num: str) -> bool:
    return num.isdigit() and luhn_digit(num[:-1]) == int(num[-1])


# ------------------------------------------------------------------ 영국 NHS number (mod 11), 999 대역 = 시험용
def nhs_number(r: random.Random) -> str:
    while True:
        body = "999" + "".join(str(r.randint(0, 9)) for _ in range(6))
        s = sum(int(d) * w for d, w in zip(body, range(10, 1, -1)))
        c = 11 - s % 11
        if c == 11:
            c = 0
        if c != 10:
            return body + str(c)


def nhs_ok(n: str) -> bool:
    n = n.replace(" ", "")
    if len(n) != 10 or not n.isdigit():
        return False
    s = sum(int(d) * w for d, w in zip(n[:9], range(10, 1, -1)))
    c = 11 - s % 11
    return (0 if c == 11 else c) == int(n[9]) and c != 10


def nhs_display(n: str) -> str:
    return f"{n[:3]} {n[3:6]} {n[6:]}"


# ------------------------------------------------------------------ 미국 NPI (Luhn, 80840 접두)
def npi(r: random.Random) -> str:
    base = str(r.choice((1, 2))) + "".join(str(r.randint(0, 9)) for _ in range(8))
    return base + str(luhn_digit("80840" + base))


def npi_ok(n: str) -> bool:
    return len(n) == 10 and luhn_ok("80840" + n)


# ------------------------------------------------------------------ 네덜란드 BSN (11-proef)
def bsn(r: random.Random) -> str:
    while True:
        body = [r.randint(1, 9)] + [r.randint(0, 9) for _ in range(7)]
        s = sum(d * w for d, w in zip(body, range(9, 1, -1)))
        c = s % 11
        if c < 10:
            return "".join(map(str, body)) + str(c)


def bsn_ok(n: str) -> bool:
    if len(n) != 9 or not n.isdigit():
        return False
    s = sum(int(d) * w for d, w in zip(n[:8], range(9, 1, -1))) - int(n[8])
    return s % 11 == 0


# ------------------------------------------------------------------ 브라질 CPF / CNS
def cpf(r: random.Random) -> str:
    d = [r.randint(0, 9) for _ in range(9)]
    for n in (10, 11):
        s = sum(x * w for x, w in zip(d, range(n, 1, -1)))
        d.append((s * 10 % 11) % 10)
    s = "".join(map(str, d))
    return f"{s[:3]}.{s[3:6]}.{s[6:9]}-{s[9:]}"


def cpf_ok(v: str) -> bool:
    d = [int(c) for c in v if c.isdigit()]
    if len(d) != 11:
        return False
    for n in (9, 10):
        s = sum(x * w for x, w in zip(d[:n], range(n + 1, 1, -1)))
        if (s * 10 % 11) % 10 != d[n]:
            return False
    return True


def cns(r: random.Random) -> str:
    """Cartão Nacional de Saúde, 확정 번호(1·2로 시작) 규칙."""
    while True:
        pis = str(r.choice((1, 2))) + "".join(str(r.randint(0, 9)) for _ in range(10))
        s = sum(int(c) * (15 - i) for i, c in enumerate(pis))
        dv = 11 - s % 11
        if dv == 11:
            dv = 0
        if dv == 10:
            s += 2
            dv = 11 - s % 11
            out = pis + "001" + str(dv)
        else:
            out = pis + "000" + str(dv)
        if cns_ok(out):
            return out


def cns_ok(v: str) -> bool:
    return len(v) == 15 and v.isdigit() and sum(int(c) * (15 - i) for i, c in enumerate(v)) % 11 == 0


# ------------------------------------------------------------------ 호주 IHI (800360 + Luhn) / HPI-I (800361) / Medicare
def ihi(r: random.Random, prefix: str = "800360") -> str:
    body = prefix + "".join(str(r.randint(0, 9)) for _ in range(9))
    return body + str(luhn_digit(body))


def medicare_au(r: random.Random) -> str:
    d = [r.randint(2, 6)] + [r.randint(0, 9) for _ in range(7)]
    c = sum(x * w for x, w in zip(d, (1, 3, 7, 9, 1, 3, 7, 9))) % 10
    return "".join(map(str, d)) + str(c) + str(r.randint(1, 5))          # 8자리 + 체크 + 발급회차


# ------------------------------------------------------------------ 독일 KVNR (건강보험 번호) / LANR (의사 번호)
def kvnr(r: random.Random) -> str:
    letter = chr(ord("A") + r.randint(0, 25))
    digits = "".join(str(r.randint(0, 9)) for _ in range(8))
    seq = f"{ord(letter) - 64:02d}" + digits
    total = 0
    for i, ch in enumerate(seq):
        p = int(ch) * (1 if i % 2 == 0 else 2)
        total += p // 10 + p % 10
    return letter + digits + str(total % 10)


def lanr(r: random.Random) -> str:
    six = [r.randint(0, 9) for _ in range(6)]
    s = sum(d * (4 if i % 2 == 0 else 9) for i, d in enumerate(six))
    return "".join(map(str, six)) + str((10 - s % 10) % 10) + f"{r.randint(1, 99):02d}"


# ------------------------------------------------------------------ 프랑스 NIR (INS) — 13자리 + 키(97 - n mod 97)
def nir(r: random.Random, sex: str, birth_year: int, birth_month: int) -> str:
    dept = r.choice(["31", "75", "69", "13", "33", "59", "44", "67", "34", "06"])
    body = f"{1 if sex == 'M' else 2}{birth_year % 100:02d}{birth_month:02d}{dept}{r.randint(1, 999):03d}{r.randint(1, 999):03d}"
    return body + f"{97 - int(body) % 97:02d}"


def nir_ok(v: str) -> bool:
    return len(v) == 15 and v.isdigit() and 97 - int(v[:13]) % 97 == int(v[13:])


# ------------------------------------------------------------------ 한국 주민등록번호 (2020-10 이전 발급 규칙의 체크 디지트) — 외부로는 항상 마스킹
def rrn(r: random.Random, sex: str, birth_iso: str, foreign: bool = False) -> str:
    y, m, d = birth_iso.split("-")
    century2000 = int(y) >= 2000
    g = (7 if century2000 else 5) if foreign else (3 if century2000 else 1)
    g += 0 if sex == "M" else 1
    body = f"{y[2:]}{m}{d}{g}" + "".join(str(r.randint(0, 9)) for _ in range(5))
    s = sum(int(c) * w for c, w in zip(body, (2, 3, 4, 5, 6, 7, 8, 9, 2, 3, 4, 5)))
    return f"{body[:6]}-{body[6:]}{(11 - s % 11) % 10}"


def rrn_masked(v: str) -> str:
    return v[:8] + "******"


# ------------------------------------------------------------------ UAE Emirates ID (784-YYYY-NNNNNNN-C, Luhn)
def emirates_id(r: random.Random, birth_year: int) -> str:
    body = f"784{birth_year}" + "".join(str(r.randint(0, 9)) for _ in range(7))
    c = luhn_digit(body)
    return f"{body[:3]}-{body[3:7]}-{body[7:]}-{c}"


def emirates_ok(v: str) -> bool:
    return luhn_ok(v.replace("-", ""))


# ------------------------------------------------------------------ 싱가포르 NRIC (S/T + 7자리 + 체크 문자)
def nric(r: random.Random, birth_year: int) -> str:
    prefix = "T" if birth_year >= 2000 else "S"
    d = [r.randint(0, 9) for _ in range(7)]
    s = sum(x * w for x, w in zip(d, (2, 7, 6, 5, 4, 3, 2))) + (4 if prefix == "T" else 0)
    return prefix + "".join(map(str, d)) + "JZIHGFEDCBA"[s % 11]


def nric_ok(v: str) -> bool:
    if len(v) != 9 or v[0] not in "ST":
        return False
    d = [int(c) for c in v[1:8]]
    s = sum(x * w for x, w in zip(d, (2, 7, 6, 5, 4, 3, 2))) + (4 if v[0] == "T" else 0)
    return "JZIHGFEDCBA"[s % 11] == v[8]


# ------------------------------------------------------------------ 캐나다 온타리오 건강카드 (10자리 Luhn + 버전코드 2글자)
def ohip(r: random.Random) -> tuple[str, str]:
    body = str(r.randint(1, 9)) + "".join(str(r.randint(0, 9)) for _ in range(8))
    vc = "".join(r.choice("ABCDEFGHJKLMNPRSTVWXYZ") for _ in range(2))
    return body + str(luhn_digit(body)), vc


# ------------------------------------------------------------------ 기타 형식
def digits(r: random.Random, n: int, first_nonzero: bool = True) -> str:
    s = str(r.randint(1, 9)) if first_nonzero else str(r.randint(0, 9))
    return s + "".join(str(r.randint(0, 9)) for _ in range(n - 1))


def gmc(r: random.Random) -> str:
    return digits(r, 7)


def mcr_sg(r: random.Random) -> str:
    return "M" + digits(r, 5) + r.choice("ABCDEFGHJKLZ")
