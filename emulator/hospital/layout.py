"""Hospital architectural layout engine.

Generates a JSON floor-plan model (metres) for ten hospital archetypes that
differ in nursing-unit topology and scale, or loads one from JSON so a router
can render exactly the same plan.  Sources used for the typologies and
dimensions: nursing-unit design literature (single/double corridor, racetrack,
decentralised pods), Korean facility rules (1-8 bed rooms) and imaging-suite
guidelines (scan room + control room + equipment room, MRI screening zone).

Schema (version 1)
  {"version":1, "hospital":{name, template, template_name, bed_capacity, beds},
   "buildings":[{idx,name,x,y,w,d}],
   "floors":[{building_idx, floor, kind: ward|diagnostic|lobby, width, depth, name,
              rooms:[{id,name,kind,poly:[[x,y]..],cx,cy,ward,beds:[{id,x,y,angle}],gateway:bool}],
              corridors:[{poly}], fixtures:[{type:display|nurse_desk|reception|..., subtype,x,y,room,label}],
              wards:[{id,name,specialty,rooms:[ids],nurse_station:id}]}]}
Room kinds: room, exam, nurse_station, toilet, shower, elevator, stairs, corridor, lobby, er,
            utility, staff, lounge, waiting, reception, control, equipment, prep, recovery, storage, office, isolation
"""
from __future__ import annotations

import json
import math
from dataclasses import dataclass, field

import numpy as np

CORR_W = 3.0                      # corridor effective width: >=3.0 m for medical floors >=500 m2 (피난·방화 규칙 제15조의2)
ROOM_D = 6.6                      # patient room depth (m)
ROOM_W = {1: 4.2, 2: 5.8, 3: 7.4, 4: 7.4, 5: 9.8, 6: 9.8, 8: 12.2}   # room width by bed count (>=1.5 m between beds, >=6.3 m2/bed, WC inside)
WC_W, WC_D = 1.7, 2.2              # en-suite toilet cell (door-side corner)
WARD_SPECIALTIES = ["심장내과", "순환기내과", "흉부외과", "내분비내과", "호흡기내과", "신장내과", "신경과",
                    "소화기내과", "종양내과", "정형외과", "감염내과", "일반외과"]
SPECIALTY_SHARE = {"심장내과": 0.26, "순환기내과": 0.18, "흉부외과": 0.10, "내분비내과": 0.06, "호흡기내과": 0.08, "신장내과": 0.05,
                   "신경과": 0.06, "소화기내과": 0.05, "종양내과": 0.05, "정형외과": 0.05, "감염내과": 0.03, "일반외과": 0.03}

# ------------------------------------------------------------------ templates (10 archetypes)
TEMPLATES: dict[str, dict] = {
    "community_linear":   {"name": "소규모 지역병원 · 단일 이중복도형", "scale": "S", "max_beds": 80,   "topology": "linear",    "beds_per_floor": 28, "room_mix": [1, 2, 2, 4, 4, 4, 6, 6], "floors_max": 4, "diag": "S"},
    "community_l":        {"name": "소규모 병원 · L자형 병동",          "scale": "S", "max_beds": 140,  "topology": "l",         "beds_per_floor": 34, "room_mix": [1, 2, 4, 4, 4, 6, 6, 8], "floors_max": 5, "diag": "S"},
    "general_t":          {"name": "중소형 종합병원 · T자형",           "scale": "M", "max_beds": 300,  "topology": "t",         "beds_per_floor": 40, "room_mix": [1, 1, 2, 2, 4, 4, 4, 6, 6, 8], "floors_max": 8, "diag": "M"},
    "general_racetrack":  {"name": "중형 종합병원 · 레이스트랙형",      "scale": "M", "max_beds": 420,  "topology": "racetrack", "beds_per_floor": 42, "room_mix": [1, 1, 2, 2, 4, 4, 4, 4, 6, 6], "floors_max": 10, "diag": "M"},
    "general_courtyard":  {"name": "중형 종합병원 · 중정(ㅁ자)형",       "scale": "M", "max_beds": 560,  "topology": "courtyard", "beds_per_floor": 48, "room_mix": [1, 2, 2, 4, 4, 4, 4, 6, 6, 8], "floors_max": 12, "diag": "L"},
    "tertiary_cross":     {"name": "대형 종합병원 · 십자형 4익동",       "scale": "L", "max_beds": 900,  "topology": "cross",     "beds_per_floor": 56, "room_mix": [1, 1, 2, 2, 4, 4, 4, 4, 4, 4, 6, 8], "floors_max": 16, "diag": "L"},
    "tertiary_tower":     {"name": "대형 병원 · 고층 타워형(중앙 코어)", "scale": "L", "max_beds": 1200, "topology": "tower",     "beds_per_floor": 36, "room_mix": [1, 1, 1, 2, 2, 4, 4, 4, 4],    "floors_max": 32, "diag": "L", "modern": True},
    "tertiary_pod":       {"name": "대형 병원 · 분산 간호(포드)형",      "scale": "L", "max_beds": 1600, "topology": "pod",       "beds_per_floor": 48, "room_mix": [1, 1, 2, 2, 4, 4, 4, 4, 6, 6, 8], "floors_max": 20, "diag": "XL"},
    "university_pavilion": {"name": "대학병원 · 다동(파빌리온) 레이스트랙", "scale": "XL", "max_beds": 2600, "topology": "racetrack", "beds_per_floor": 52, "room_mix": [1, 1, 2, 2, 2, 4, 4, 4, 4, 4, 4, 4], "floors_max": 14, "diag": "XL", "buildings_max": 4, "modern": True},
    "medical_center_y":   {"name": "메디컬센터 · Y자형 트리플 타워",     "scale": "XL", "max_beds": 5000, "topology": "y",         "beds_per_floor": 60, "room_mix": [1, 1, 2, 2, 4, 4, 4, 4, 4, 4, 4, 4], "floors_max": 24, "diag": "XL", "buildings_max": 3, "modern": True},
}
TEMPLATE_ORDER = list(TEMPLATES.keys())


def select_template(bed_capacity: int, requested: str = "auto") -> str:
    if requested in TEMPLATES:
        return requested
    for k in TEMPLATE_ORDER:
        if bed_capacity <= TEMPLATES[k]["max_beds"]:
            return k
    return TEMPLATE_ORDER[-1]


# ------------------------------------------------------------------ geometry helpers
def rect(x: float, y: float, w: float, h: float) -> list[list[float]]:
    return [[x, y], [x + w, y], [x + w, y + h], [x, y + h]]


def rot(points: list[list[float]], cx: float, cy: float, deg: float) -> list[list[float]]:
    a = math.radians(deg)
    c, s = math.cos(a), math.sin(a)
    return [[round(cx + (px - cx) * c - (py - cy) * s, 2), round(cy + (px - cx) * s + (py - cy) * c, 2)] for px, py in points]


def centroid(poly: list[list[float]]) -> tuple[float, float]:
    xs = [p[0] for p in poly]
    ys = [p[1] for p in poly]
    return round(sum(xs) / len(xs), 2), round(sum(ys) / len(ys), 2)


def point_in_poly(x: float, y: float, poly) -> bool:
    inside = False
    n = len(poly)
    for i in range(n):
        x1, y1 = poly[i]
        x2, y2 = poly[(i + 1) % n]
        if (y1 > y) != (y2 > y) and x < (x2 - x1) * (y - y1) / (y2 - y1) + x1:
            inside = not inside
    return inside


def _set_zones(fl, ward_id: str, polys) -> None:
    """Attach the nursing-unit zone (one or more polygons in floor metres) to a ward.  Every patient room of the ward
    lies inside its zone; the corridor crossings on the zone boundary are the unit's compartment doors."""
    for w in fl.wards:
        if w["id"] == ward_id:
            w["zones"] = [[[round(x, 2), round(y, 2)] for x, y in pl] for pl in polys]


@dataclass
class Floor:
    building_idx: int
    floor: int
    kind: str
    name: str
    width: float
    depth: float
    rooms: list[dict] = field(default_factory=list)
    corridors: list[dict] = field(default_factory=list)
    fixtures: list[dict] = field(default_factory=list)
    wards: list[dict] = field(default_factory=list)
    angle: float = 0.0          # for rotated wings, rooms already carry world polys

    def add_room(self, rid: str, name: str, kind: str, poly, ward: str | None = None, beds: int = 0, gateway: bool | None = None,
                 bed_angle: float = 0.0, door: str = "S") -> dict:
        cx, cy = centroid(poly)
        r = {"id": rid, "name": name, "kind": kind, "poly": [[round(x, 2), round(y, 2)] for x, y in poly], "cx": cx, "cy": cy, "ward": ward,
             "beds": [], "gateway": (kind not in ("corridor", "utility", "storage", "equipment", "control", "office") if gateway is None else gateway)}
        if name.startswith("MRI") or kind == "staff":   # RF-shielded MRI; staff rooms hold no monitored patients
            r["gateway"] = False
        if beds:
            r["beds"] = bed_positions(rid, poly, beds, bed_angle, door)
            r["door"] = door
            # en-suite toilet: the door-side corner is reserved in the bed layout but not drawn as a separate room
        self.rooms.append(r)
        return r

    def add_corridor(self, poly, name: str = "복도") -> dict:
        c = {"name": name, "poly": [[round(x, 2), round(y, 2)] for x, y in poly]}
        self.corridors.append(c)
        return c

    def add_fixture(self, ftype: str, x: float, y: float, subtype: str = "", room: str = "", label: str = "", angle: float = 0.0) -> None:
        self.fixtures.append({"type": ftype, "subtype": subtype, "x": round(x, 2), "y": round(y, 2), "room": room, "label": label, "angle": angle})

    def to_dict(self) -> dict:
        return {"building_idx": self.building_idx, "floor": self.floor, "kind": self.kind, "name": self.name, "width": self.width, "depth": self.depth,
                "rooms": self.rooms, "corridors": self.corridors, "fixtures": self.fixtures, "wards": self.wards}


def bed_positions(rid: str, poly, n: int, angle: float = 0.0, door: str = "S") -> list[dict]:
    """Beds head-to-wall, one row (<=2 beds) or two rows along the room's long axis with >=1.5 m clear
    between beds; the door-side corner is reserved for the en-suite WC (WC_W x WC_D).  door: side facing
    the corridor in the un-rotated frame (N/S/E/W)."""
    cx, cy = centroid(poly)
    loc = rot(poly, cx, cy, -angle) if angle else poly
    lx0, lx1 = min(p[0] for p in loc), max(p[0] for p in loc)
    ly0, ly1 = min(p[1] for p in loc), max(p[1] for p in loc)
    w, h = lx1 - lx0, ly1 - ly0
    horizontal = w >= h
    rows = 1 if n <= 2 else 2
    per_row = math.ceil(n / rows)
    out = []
    k = 0
    for r in range(rows):
        if horizontal:
            # row 0 = far wall (opposite the door), row 1 = door wall; the door wall keeps a WC at its start corner
            far_first = door == "S"
            at_far = (r == 0) if rows == 2 else True
            y = (ly0 + 1.35 if far_first else ly1 - 1.35) if at_far else (ly1 - 1.35 if far_first else ly0 + 1.35)
            head = (0 if far_first else 180) if at_far else (180 if far_first else 0)
            x_start = lx0 + (0.0 if at_far else WC_W)
            span = lx1 - x_start
            gap = (span - 0.8) / per_row
            for i in range(per_row):
                if k >= n:
                    break
                x = x_start + 0.4 + gap * (i + 0.5)
                px, py = (rot([[x, y]], cx, cy, angle)[0] if angle else (round(x, 2), round(y, 2)))
                out.append({"id": f"{rid}-{chr(65 + k)}", "x": px, "y": py, "angle": (head + angle) % 360})
                k += 1
        else:
            far_first = door == "E"
            at_far = (r == 0) if rows == 2 else True
            x = (lx0 + 1.35 if far_first else lx1 - 1.35) if at_far else (lx1 - 1.35 if far_first else lx0 + 1.35)
            head = (270 if far_first else 90) if at_far else (90 if far_first else 270)
            y_start = ly0 + (0.0 if at_far else WC_W)
            span = ly1 - y_start
            gap = (span - 0.8) / per_row
            for i in range(per_row):
                if k >= n:
                    break
                y = y_start + 0.4 + gap * (i + 0.5)
                px, py = (rot([[x, y]], cx, cy, angle)[0] if angle else (round(x, 2), round(y, 2)))
                out.append({"id": f"{rid}-{chr(65 + k)}", "x": px, "y": py, "angle": (head + angle) % 360})
                k += 1
    return out


def wc_poly(poly, door: str, angle: float = 0.0):
    """En-suite toilet cell polygon at the door-side corner of an (un-rotated) room rect."""
    cx, cy = centroid(poly)
    loc = rot(poly, cx, cy, -angle) if angle else poly
    lx0, lx1 = min(p[0] for p in loc), max(p[0] for p in loc)
    ly0, ly1 = min(p[1] for p in loc), max(p[1] for p in loc)
    if door == "S":
        r = rect(lx0, ly1 - WC_D, WC_W, WC_D)
    elif door == "N":
        r = rect(lx0, ly0, WC_W, WC_D)
    elif door == "E":
        r = rect(lx1 - WC_D, ly0, WC_D, WC_W)
    else:
        r = rect(lx0, ly0, WC_D, WC_W)
    return rot(r, cx, cy, angle) if angle else r


def ward_name(building_idx: int, floor: int, side: str, specialty: str) -> tuple[str, str]:
    """Nursing units are organised by department: '순환기내과 병동 3A'."""
    return f"W{building_idx + 1}{floor:02d}{side}", f"{specialty} 병동 {floor}{side}"


# ------------------------------------------------------------------ nursing-unit generators
def _rooms_along(fl: Floor, x0: float, y: float, length: float, mix: list[int], ward: str, prefix: str, above: bool,
                 start_no: int, angle: float = 0.0, pivot=(0.0, 0.0)) -> tuple[float, int, list[dict]]:
    """Fill a horizontal run [x0, x0+length] with patient rooms (rooms above or below the corridor line y)."""
    x = x0
    n = start_no
    made = []
    i = 0
    while True:
        beds = mix[i % len(mix)]
        w = ROOM_W[beds]
        if x + w > x0 + length + 0.01:
            # fill the leftover with a small support room if wide enough
            left = x0 + length - x
            if left >= 3.0:
                poly = rect(x, y - ROOM_D if above else y, left, ROOM_D)
                fl.add_room(f"{prefix}-U{n}", "린넨/창고", "storage", rot(poly, *pivot, angle) if angle else poly, ward, gateway=False)
            break
        poly = rect(x, y - ROOM_D if above else y, w, ROOM_D)
        poly_w = rot(poly, *pivot, angle) if angle else poly
        made.append(fl.add_room(f"{prefix}{n:02d}", f"{n:02d}", "room", poly_w, ward, beds, bed_angle=angle, door="S" if above else "N"))
        n += 1
        x += w
        i += 1
    return x, n, made


def _rooms_vertical(fl: Floor, x_edge: float, y0: float, length: float, mix: list[int], ward: str, prefix: str, west: bool, start_no: int) -> tuple[int, list[dict]]:
    """Fill a vertical run [y0, y0+length] with rooms whose doors face a vertical corridor at x_edge
    (west=True: rooms occupy x in [x_edge-ROOM_D, x_edge]; else [x_edge, x_edge+ROOM_D])."""
    y = y0
    n = start_no
    made = []
    i = 0
    x0 = x_edge - ROOM_D if west else x_edge
    while True:
        beds = mix[i % len(mix)]
        h = ROOM_W[beds]
        if y + h > y0 + length + 0.01:
            left = y0 + length - y
            if left >= 3.0:
                fl.add_room(f"{prefix}-U{n}", "린넨/창고", "storage", rect(x0, y, ROOM_D, left), ward, gateway=False)
            break
        made.append(fl.add_room(f"{prefix}{n:02d}", f"{n:02d}", "room", rect(x0, y, ROOM_D, h), ward, beds, door="E" if west else "W"))
        n += 1
        y += h
        i += 1
    return n, made


# priority order: the nurse station and the shared (accessible) toilet are mandatory on every nursing unit
# (의료법 시행규칙 별표 4: 병동마다 환자용 화장실; 장애인등편의법: 층별 장애인 화장실), the rest fills the band as space allows
CORE_ITEMS = [("간호사실", "nurse_station", 8.5, True), ("공용 화장실", "toilet", 3.6, True), ("투약실", "utility", 3.4, False), ("청결처치실", "utility", 3.4, False),
              ("오염처치실", "utility", 3.4, False), ("샤워실", "shower", 3.4, True), ("직원휴게실", "staff", 4.0, False), ("배선실", "utility", 3.2, False), ("환자휴게실", "lounge", 4.6, True)]


def _core_items(length: float, with_lounge: bool) -> list[tuple[str, str, float, bool]]:
    items = [it for it in CORE_ITEMS if with_lounge or it[0] != "환자휴게실"]
    out: list = []
    used = 0.0
    ns = CORE_ITEMS[0]
    if length >= 6.0:                              # the station always comes first, narrowed on a short band (min 6 m)
        out.append((ns[0], ns[1], min(ns[2], length), ns[3]))
        used = out[0][2]
    for it in items[1:]:
        if used + it[2] <= length + 0.01:
            out.append(it)
            used += it[2]
    wc = CORE_ITEMS[1]
    if length >= wc[2] and not any(it[1] == "toilet" for it in out):
        # make room for the toilet: drop support rooms from the end (never the station), else narrow the station (min 6 m)
        while out and out[-1][1] != "nurse_station" and used + wc[2] > length + 0.01:
            used -= out.pop()[2]
        if used + wc[2] > length + 0.01 and out and out[0][1] == "nurse_station" and length - wc[2] >= 6.0:
            out = [(out[0][0], out[0][1], length - wc[2], out[0][3])]
            used = out[0][2]
        if used + wc[2] <= length + 0.01:
            out.append(wc)
            used += wc[2]
    if out:
        name, kind, w, gw = out[-1]
        out[-1] = (name, kind, w + (length - used), gw)       # stretch the last item to fill the band
    return out


def _core_vertical(fl: Floor, x_edge: float, y0: float, length: float, ward: str, side: str, prefix: str, west: bool, with_lounge: bool = False) -> str:
    x0 = x_edge - ROOM_D if west else x_edge
    y = y0
    ns_id = ""
    for name, kind, w, gw in _core_items(length, with_lounge):
        rid = f"{prefix}-NS" if kind == "nurse_station" else f"{prefix}-{kind}-{side}-{int(y)}"
        r = fl.add_room(rid, f"{name} {side}", kind, rect(x0, y, ROOM_D, w), ward, gateway=gw)
        if kind == "nurse_station":
            ns_id = r["id"]
            dx = -1.0 if west else 1.0
            fl.add_fixture("display", r["cx"] + dx * 1.6, r["cy"] - 1.6, "central", r["id"], "중앙 모니터 1", 90)
            fl.add_fixture("display", r["cx"] + dx * 1.6, r["cy"] + 1.6, "central", r["id"], "중앙 모니터 2", 90)
            fl.add_fixture("nurse_desk", r["cx"] - dx * 1.4, r["cy"], "counter", r["id"], "간호사 카운터", 90)
        y += w
    return ns_id


def _core(fl: Floor, x: float, y: float, w: float, ward: str, side: str, prefix: str, angle: float = 0.0, pivot=(0.0, 0.0), with_lounge: bool = True) -> str:
    """Ward core: nurse station (open counter) + medication, clean/dirty utility, staff room, toilets/showers, lounge.
    Occupies a band of width w (along x) and depth ROOM_D below the corridor line y.  Returns nurse-station room id."""
    xx = x
    def R(p):
        return rot(p, *pivot, angle) if angle else p
    ns_id = ""
    for name, kind, rw, gw in _core_items(w, with_lounge):
        rid = f"{prefix}-NS" if kind == "nurse_station" else f"{prefix}-{kind}-{side}-{int(xx)}"
        r = fl.add_room(rid, f"{name} {side}", kind, R(rect(xx, y, rw, ROOM_D)), ward, gateway=gw)
        if kind == "nurse_station":
            ns_id = r["id"]
            for k, dx in enumerate((-1.6, 1.6)):           # central monitoring displays behind the counter + counter desk
                px, py = R([[xx + rw / 2 + dx, y + 1.0]])[0]
                fl.add_fixture("display", px, py, "central", r["id"], f"중앙 모니터 {k + 1}", angle)
            px, py = R([[xx + rw / 2, y + 2.4]])[0]
            fl.add_fixture("nurse_desk", px, py, "counter", r["id"], "간호사 카운터", angle)
        xx += rw
    return ns_id


def _corridor_displays(fl: Floor, x0: float, x1: float, y: float, prefix: str, angle: float = 0.0, pivot=(0.0, 0.0)) -> None:
    for k, x in enumerate(np.linspace(x0 + 6, x1 - 6, 3)):
        px, py = (rot([[x, y]], *pivot, angle)[0] if angle else (x, y))
        fl.add_fixture("display", px, py, "corridor", "", f"복도 전광판 {prefix}-{k + 1}", angle)


def _end_stairs(fl: Floor, x: float, y: float, prefix: str, w: float = 3.6, h: float = ROOM_D) -> None:
    """Second direct/escape stair at a wing end (건축법 시행령 제34조: 3층 이상 의료시설 직통계단 2개소 이상)."""
    fl.add_room(f"{prefix}-ST2-{int(x)}", "피난계단", "stairs", rect(x, y, w, h))


def _vertical_core(fl: Floor, x: float, y: float, w: float, h: float, prefix: str, angle: float = 0.0, pivot=(0.0, 0.0)) -> None:
    """Elevator bank + stairs block."""
    def R(p):
        return rot(p, *pivot, angle) if angle else p
    fl.add_room(f"{prefix}-EV", "엘리베이터 홀", "elevator", R(rect(x, y, w * 0.6, h)))
    fl.add_room(f"{prefix}-ST", "계단실", "stairs", R(rect(x + w * 0.6, y, w * 0.4, h)))


def gen_linear(fl: Floor, mix: list[int], spec_a: str, spec_b: str, bidx: int, floor: int, beds_per_floor: int) -> None:
    """Double-loaded corridor: two wards (A/B) either side of a central vertical core."""
    W = fl.width
    y_corr = fl.depth / 2 - CORR_W / 2
    fl.add_corridor(rect(1.0, y_corr, W - 2.0, CORR_W))
    core_w = 9.0
    core_x = W / 2 - core_w / 2
    _vertical_core(fl, core_x, y_corr - ROOM_D, core_w, ROOM_D, f"B{bidx + 1}F{floor}")
    _end_stairs(fl, 1.0, y_corr - ROOM_D, f"B{bidx + 1}F{floor}")
    _end_stairs(fl, W - 1.0 - 3.6, y_corr - ROOM_D, f"B{bidx + 1}F{floor}")
    half = (W - 2.0 - core_w) / 2
    for side, x0, spec in (("A", 1.0, spec_a), ("B", core_x + core_w, spec_b)):
        wid, wname = ward_name(bidx, floor, side, spec)
        prefix = f"{bidx + 1}{floor:02d}{side}"
        top_x0 = x0 + (3.6 if side == "A" else 0.0)
        top_len = half - 3.6
        _, n, rooms_top = _rooms_along(fl, top_x0, y_corr, top_len, mix, wid, prefix, True, 1)
        core_len = min(28.0, half * 0.55)
        # below corridor: core band near the centre, rooms towards the end
        if side == "A":
            _, n, rooms_bot = _rooms_along(fl, x0, y_corr + CORR_W, half - core_len, mix[::-1], wid, prefix, False, n)
            ns = _core(fl, x0 + half - core_len, y_corr + CORR_W, core_len, wid, side, prefix)
        else:
            ns = _core(fl, x0, y_corr + CORR_W, core_len, wid, side, prefix)
            _, n, rooms_bot = _rooms_along(fl, x0 + core_len, y_corr + CORR_W, half - core_len, mix[::-1], wid, prefix, False, n)
        _corridor_displays(fl, x0, x0 + half, y_corr + CORR_W / 2, side)
        fl.wards.append({"id": wid, "name": wname, "specialty": spec, "rooms": [r["id"] for r in rooms_top + rooms_bot], "nurse_station": ns})
        _set_zones(fl, wid, [rect(0.0, 0.0, W / 2, fl.depth) if side == "A" else rect(W / 2, 0.0, W / 2, fl.depth)])


def gen_l(fl: Floor, mix, spec_a, spec_b, bidx, floor, bpf) -> None:
    """L-shaped unit: wing A horizontal, wing B vertical, core at the corner."""
    W, D = fl.width, fl.depth
    y_corr = ROOM_D + 1.0
    x_corr = W - ROOM_D - CORR_W - 1.0
    fl.add_corridor(rect(1.0, y_corr, W - 2.0, CORR_W))
    fl.add_corridor(rect(x_corr, y_corr, CORR_W, D - y_corr - 1.0))
    _vertical_core(fl, x_corr - 9.5, y_corr + CORR_W, 9.0, ROOM_D, f"B{bidx + 1}F{floor}")
    # wing A (horizontal)
    wid, wname = ward_name(bidx, floor, "A", spec_a)
    prefix = f"{bidx + 1}{floor:02d}A"
    lenA = x_corr - 1.0
    _end_stairs(fl, 1.0, y_corr - ROOM_D, f"B{bidx + 1}F{floor}")
    _, n, top = _rooms_along(fl, 1.0 + 3.6, y_corr, lenA - 3.6, mix, wid, prefix, True, 1)
    core_len = min(26.0, lenA * 0.5)
    _, n, bot = _rooms_along(fl, 1.0, y_corr + CORR_W, lenA - core_len - 9.5, mix[::-1], wid, prefix, False, n)
    ns = _core(fl, 1.0 + lenA - core_len - 9.5, y_corr + CORR_W, core_len, wid, "A", prefix)
    _corridor_displays(fl, 1.0, lenA, y_corr + CORR_W / 2, "A")
    fl.wards.append({"id": wid, "name": wname, "specialty": spec_a, "rooms": [r["id"] for r in top + bot], "nurse_station": ns})
    _set_zones(fl, wid, [rect(0.0, 0.0, x_corr, y_corr + CORR_W + ROOM_D + 0.5)])
    # wing B (vertical): generate along x then rotate 90° about the corridor corner
    wid, wname = ward_name(bidx, floor, "B", spec_b)
    prefix = f"{bidx + 1}{floor:02d}B"
    pivot = (x_corr + CORR_W, y_corr + CORR_W)
    lenB = D - y_corr - CORR_W - 1.0
    _, n, right = _rooms_along(fl, pivot[0], pivot[1], lenB, mix, wid, prefix, True, 1, 90.0, pivot)
    core_len = min(22.0, lenB * 0.5)
    _, n, left = _rooms_along(fl, pivot[0] + core_len, pivot[1] + CORR_W, lenB - core_len, mix[::-1], wid, prefix, False, n, 90.0, pivot)
    # the core of wing B sits on the inner side (rotated band)
    ns = _core(fl, pivot[0], pivot[1] + CORR_W, core_len, wid, "B", prefix, 90.0, pivot, with_lounge=False)
    _corridor_displays(fl, pivot[0], pivot[0] + lenB, pivot[1] + CORR_W / 2, "B", 90.0, pivot)
    fl.wards.append({"id": wid, "name": wname, "specialty": spec_b, "rooms": [r["id"] for r in right + left], "nurse_station": ns})
    _set_zones(fl, wid, [rect(x_corr - ROOM_D - 0.5, 0.0, W - (x_corr - ROOM_D - 0.5), D)])


def gen_t(fl: Floor, mix, spec_a, spec_b, bidx, floor, bpf) -> None:
    """T-shape: horizontal bar with wards A (left) and B (right), a stem with the shared core and support."""
    W, D = fl.width, fl.depth
    y_corr = ROOM_D + 1.0
    fl.add_corridor(rect(1.0, y_corr, W - 2.0, CORR_W))
    stem_x = W / 2 - CORR_W / 2
    fl.add_corridor(rect(stem_x, y_corr, CORR_W, D - y_corr - 1.0))
    _vertical_core(fl, stem_x + CORR_W, y_corr + CORR_W, 9.0, ROOM_D, f"B{bidx + 1}F{floor}")
    _end_stairs(fl, 1.0, y_corr - ROOM_D, f"B{bidx + 1}F{floor}")
    _end_stairs(fl, W - 1.0 - 3.6, y_corr - ROOM_D, f"B{bidx + 1}F{floor}")
    half = (W - 2.0) / 2 - CORR_W / 2
    for side, x0, spec in (("A", 1.0, spec_a), ("B", stem_x + CORR_W, spec_b)):
        wid, wname = ward_name(bidx, floor, side, spec)
        prefix = f"{bidx + 1}{floor:02d}{side}"
        _, n, top = _rooms_along(fl, x0 + (3.6 if side == "A" else 0.0), y_corr, half - 3.6, mix, wid, prefix, True, 1)
        core_len = min(24.0, half * 0.5)
        if side == "A":
            _, n, bot = _rooms_along(fl, x0, y_corr + CORR_W, half - core_len, mix[::-1], wid, prefix, False, n)
            ns = _core(fl, x0 + half - core_len, y_corr + CORR_W, core_len, wid, side, prefix)
        else:
            ns = _core(fl, x0 + 9.5, y_corr + CORR_W, core_len, wid, side, prefix)
            _, n, bot = _rooms_along(fl, x0 + 9.5 + core_len, y_corr + CORR_W, half - core_len - 9.5, mix[::-1], wid, prefix, False, n)
        _corridor_displays(fl, x0, x0 + half, y_corr + CORR_W / 2, side)
        fl.wards.append({"id": wid, "name": wname, "specialty": spec, "rooms": [r["id"] for r in top + bot], "nurse_station": ns})
        zd = y_corr + CORR_W + ROOM_D + 0.5
        _set_zones(fl, wid, [rect(0.0, 0.0, W / 2, zd) if side == "A" else rect(W / 2, 0.0, W / 2, zd)])
    # stem: day room, family lounge, rehab corner on both sides of the stem corridor
    sy = y_corr + CORR_W + ROOM_D + 1.0
    depth_left = D - sy - 1.0
    if depth_left > 8:
        fl.add_room(f"B{bidx + 1}F{floor}-DAY", "주간 병실/가족 라운지", "lounge", rect(stem_x - 8.0, sy, 8.0, depth_left))
        fl.add_room(f"B{bidx + 1}F{floor}-REHAB", "병동 재활/처치 공간", "utility", rect(stem_x + CORR_W, sy, 8.0, depth_left), gateway=True)


def gen_racetrack(fl: Floor, mix, spec_a, spec_b, bidx, floor, bpf) -> None:
    """Racetrack: ring corridor around a central core; two cross corridors link the north and south corridors
    through the core so the elevator lobby, stairs and both nurse stations open onto circulation."""
    W, D = fl.width, fl.depth
    ox, oy = 1.0, 1.0
    iw, ih = W - 2.0, D - 2.0
    fl.add_corridor(rect(ox + ROOM_D, oy + ROOM_D, iw - 2 * ROOM_D, CORR_W), "북측 복도")
    fl.add_corridor(rect(ox + ROOM_D, oy + ih - ROOM_D - CORR_W, iw - 2 * ROOM_D, CORR_W), "남측 복도")
    fl.add_corridor(rect(ox + ROOM_D, oy + ROOM_D + CORR_W, CORR_W, ih - 2 * ROOM_D - 2 * CORR_W), "서측 복도")
    fl.add_corridor(rect(ox + iw - ROOM_D - CORR_W, oy + ROOM_D + CORR_W, CORR_W, ih - 2 * ROOM_D - 2 * CORR_W), "동측 복도")
    cx0, cy0 = ox + ROOM_D + CORR_W, oy + ROOM_D + CORR_W
    cw, ch = iw - 2 * (ROOM_D + CORR_W), ih - 2 * (ROOM_D + CORR_W)
    ev_w = 10.0
    xw = cx0 + cw / 2 - ev_w / 2 - CORR_W          # west cross corridor
    xe = cx0 + cw / 2 + ev_w / 2                   # east cross corridor
    fl.add_corridor(rect(xw, cy0, CORR_W, ch), "횡단 복도 서")
    fl.add_corridor(rect(xe, cy0, CORR_W, ch), "횡단 복도 동")
    wid_a, wname_a = ward_name(bidx, floor, "A", spec_a)
    wid_b, wname_b = ward_name(bidx, floor, "B", spec_b)
    pa, pb = f"{bidx + 1}{floor:02d}A", f"{bidx + 1}{floor:02d}B"
    ra, rb = [], []
    _, n, r = _rooms_along(fl, ox + ROOM_D, oy + ROOM_D, iw - 2 * ROOM_D, mix, wid_a, pa, True, 1); ra += r
    _, nb, r = _rooms_along(fl, ox + ROOM_D, oy + ih - ROOM_D, iw - 2 * ROOM_D, mix, wid_b, pb, False, 1); rb += r
    seg = ih - 2 * ROOM_D
    # side columns: upper half belongs to ward A (north unit), lower half to ward B (south unit)
    n, r = _rooms_vertical(fl, ox + ROOM_D, oy + ROOM_D, seg / 2, mix[::-1], wid_a, pa, True, n); ra += r
    nb, r = _rooms_vertical(fl, ox + ROOM_D, oy + ROOM_D + seg / 2, seg / 2, mix[::-1], wid_b, pb, True, nb); rb += r
    n, r = _rooms_vertical(fl, ox + iw - ROOM_D, oy + ROOM_D, seg / 2, mix[::-1], wid_a, pa, False, n); ra += r
    nb, r = _rooms_vertical(fl, ox + iw - ROOM_D, oy + ROOM_D + seg / 2, seg / 2, mix[::-1], wid_b, pb, False, nb); rb += r
    # elevator/stairs block between the cross corridors, opening onto both
    _vertical_core(fl, xw + CORR_W, cy0 + (ch - ROOM_D) / 2, ev_w, ROOM_D, f"B{bidx + 1}F{floor}")
    top_h = (ch - ROOM_D) / 2
    left_w = xw - cx0
    right_w = cx0 + cw - (xe + CORR_W)
    compact = left_w < 6.0 + 3.6                   # compact plate (tower): side blocks too narrow for a station + toilet
    if compact and top_h >= 4.0:
        # central core carries both nurse stations (A north / B south), each with its shared toilet, as in a tower plan
        for side, ward, prefix, y_ in (("A", wid_a, pa, cy0), ("B", wid_b, pb, cy0 + ch - top_h)):
            wc_w = 3.6
            r = fl.add_room(f"{prefix}-NS", f"간호사실 {side}", "nurse_station", rect(xw + CORR_W, y_, ev_w - wc_w, top_h), ward)
            fl.add_room(f"{prefix}-toilet-{side}", f"공용 화장실 {side}", "toilet", rect(xw + CORR_W + ev_w - wc_w, y_, wc_w, top_h), ward)
            ys = y_ + 1.0 if side == "A" else y_ + top_h - 1.0
            for k, dx in enumerate((-1.6, 1.6)):
                fl.add_fixture("display", r["cx"] + dx, ys, "central", r["id"], f"중앙 모니터 {k + 1}", 0.0)
            fl.add_fixture("nurse_desk", r["cx"], y_ + top_h / 2 + (0.7 if side == "A" else -0.7), "counter", r["id"], "간호사 카운터", 0.0)
            if side == "A":
                ns_a = r["id"]
            else:
                ns_b = r["id"]
    else:
        if top_h >= 3.0:
            fl.add_room(f"B{bidx + 1}F{floor}-CONF", "회진/상담실", "office", rect(xw + CORR_W, cy0, ev_w, top_h), gateway=False)
            fl.add_room(f"B{bidx + 1}F{floor}-DAY", "주간 병실/가족실", "lounge", rect(xw + CORR_W, cy0 + ch - top_h, ev_w, top_h))
        # ward cores: A on the west block, B on the east block (nurse station bands face the north/south corridors)
        ns_a = _core(fl, cx0, cy0, left_w, wid_a, "A", pa, with_lounge=False)
        ns_b = _core(fl, xe + CORR_W, cy0 + ch - ROOM_D, right_w, wid_b, "B", pb, with_lounge=False)
    for x_, w_, ward, side, prefix, y_ in ((cx0, left_w, wid_b, "B", pb, cy0 + ch - ROOM_D), (xe + CORR_W, right_w, wid_a, "A", pa, cy0)):
        if compact:                                # narrow side blocks: support rooms only (medication / clean utility)
            fl.add_room(f"{prefix}-utility-{side}-{int(y_)}", f"투약실 {side}", "utility", rect(x_, y_, w_, ROOM_D), ward, gateway=False)
            fl.add_room(f"{prefix}-utility2-{side}", f"청결처치실 {side}", "utility", rect(x_, cy0 if side == "A" else cy0 + ch - ROOM_D, w_, ROOM_D), ward, gateway=False)
            continue
        half = w_ / 2
        fl.add_room(f"{prefix}-lounge-{side}", f"환자휴게실 {side}", "lounge", rect(x_, y_, half, ROOM_D), ward)
        fl.add_room(f"{prefix}-staff-{side}", f"직원휴게실/회의 {side}", "staff", rect(x_ + half, y_, half, ROOM_D), ward)
    mid = ch - 2 * ROOM_D
    if mid >= 3.0:
        fl.add_room(f"B{bidx + 1}F{floor}-EQA", "장비/린넨 A", "storage", rect(cx0, cy0 + ROOM_D, left_w, mid), gateway=False)
        if right_w - 3.6 >= 2.0:
            fl.add_room(f"B{bidx + 1}F{floor}-EQB", "장비/린넨 B", "storage", rect(xe + CORR_W, cy0 + ROOM_D, right_w - 3.6, mid), gateway=False)
        fl.add_room(f"B{bidx + 1}F{floor}-ST2", "피난계단", "stairs", rect(cx0 + cw - 3.6, cy0 + ROOM_D, 3.6, mid))
    else:
        fl.add_room(f"B{bidx + 1}F{floor}-ST2", "피난계단", "stairs", rect(cx0 + cw - 3.6, cy0 + ch - ROOM_D - 0.01, 3.6, 0.01))
    _corridor_displays(fl, ox + ROOM_D, ox + iw - ROOM_D, oy + ROOM_D + CORR_W / 2, "A")
    _corridor_displays(fl, ox + ROOM_D, ox + iw - ROOM_D, oy + ih - ROOM_D - CORR_W / 2, "B")
    fl.wards.append({"id": wid_a, "name": wname_a, "specialty": spec_a, "rooms": [x["id"] for x in ra], "nurse_station": ns_a})
    fl.wards.append({"id": wid_b, "name": wname_b, "specialty": spec_b, "rooms": [x["id"] for x in rb], "nurse_station": ns_b})
    _set_zones(fl, wid_a, [rect(0.0, 0.0, W, D / 2)])
    _set_zones(fl, wid_b, [rect(0.0, D / 2, W, D / 2)])


def gen_courtyard(fl: Floor, mix, spec_a, spec_b, bidx, floor, bpf) -> None:
    """ㅁ-shaped block around an open courtyard: rooms on the outside of a ring corridor, cores on the short sides."""
    W, D = fl.width, fl.depth
    ox, oy, iw, ih = 1.0, 1.0, W - 2.0, D - 2.0
    band = ROOM_D + CORR_W
    fl.add_corridor(rect(ox + ROOM_D, oy + ROOM_D, iw - 2 * ROOM_D, CORR_W), "북측 복도")
    fl.add_corridor(rect(ox + ROOM_D, oy + ih - band, iw - 2 * ROOM_D, CORR_W), "남측 복도")
    fl.add_corridor(rect(ox + ROOM_D, oy + band, CORR_W, ih - 2 * band), "서측 복도")
    fl.add_corridor(rect(ox + iw - band, oy + band, CORR_W, ih - 2 * band), "동측 복도")
    fl.add_room(f"B{bidx + 1}F{floor}-CY", "중정 (옥외 정원)", "courtyard", rect(ox + band + 9.0, oy + band, iw - 2 * band - 9.0, ih - 2 * band - 5.5), gateway=False)
    wid_a, wname_a = ward_name(bidx, floor, "A", spec_a)
    wid_b, wname_b = ward_name(bidx, floor, "B", spec_b)
    pa, pb = f"{bidx + 1}{floor:02d}A", f"{bidx + 1}{floor:02d}B"
    ra, rb = [], []
    _, n, r = _rooms_along(fl, ox + ROOM_D, oy + ROOM_D, iw - 2 * ROOM_D, mix, wid_a, pa, True, 1); ra += r
    _, nb, r = _rooms_along(fl, ox + ROOM_D, oy + ih - ROOM_D, iw - 2 * ROOM_D, mix, wid_b, pb, False, 1); rb += r
    seg = ih - 2 * ROOM_D
    # west side: ward A rooms (upper part) + ward A core (lower part); east side mirrored for ward B
    n, r = _rooms_vertical(fl, ox + ROOM_D, oy + ROOM_D, seg * 0.5, mix[::-1], wid_a, pa, True, n); ra += r
    ns_a = _core_vertical(fl, ox + ROOM_D, oy + ROOM_D + seg * 0.5, seg * 0.5, wid_a, "A", pa, True)
    ns_b = _core_vertical(fl, ox + iw - ROOM_D, oy + ROOM_D, seg * 0.5, wid_b, "B", pb, False)
    nb, r = _rooms_vertical(fl, ox + iw - ROOM_D, oy + ROOM_D + seg * 0.5, seg * 0.5, mix[::-1], wid_b, pb, False, nb); rb += r
    # inner ring: vertical core + day rooms facing the courtyard
    _vertical_core(fl, ox + band, oy + band, 9.0, 6.0, f"B{bidx + 1}F{floor}")
    fl.add_room(f"B{bidx + 1}F{floor}-DAYA", "가족실/주간 병실", "lounge", rect(ox + band, oy + band + 6.0, 9.0, ih - 2 * band - 11.5))
    fl.add_room(f"B{bidx + 1}F{floor}-DAYB", "환자 휴게/식당", "lounge", rect(ox + band, oy + ih - band - 5.5, iw - 2 * band - 3.6, 5.5))
    fl.add_room(f"B{bidx + 1}F{floor}-ST2", "피난계단", "stairs", rect(ox + iw - band - 3.6, oy + ih - band - 5.5, 3.6, 5.5))
    _corridor_displays(fl, ox + ROOM_D, ox + iw - ROOM_D, oy + ROOM_D + CORR_W / 2, "A")
    _corridor_displays(fl, ox + ROOM_D, ox + iw - ROOM_D, oy + ih - band + CORR_W / 2, "B")
    fl.wards.append({"id": wid_a, "name": wname_a, "specialty": spec_a, "rooms": [x["id"] for x in ra], "nurse_station": ns_a})
    fl.wards.append({"id": wid_b, "name": wname_b, "specialty": spec_b, "rooms": [x["id"] for x in rb], "nurse_station": ns_b})
    yN, xW = oy + band, ox + band
    yS, xE = oy + ih - band, ox + iw - band
    xR = ox + ROOM_D                                 # inner face of the west column = start of the south row
    _set_zones(fl, wid_a, [[[0.0, 0.0], [W, 0.0], [W, yN], [xW, yN], [xW, yS], [xR, yS], [xR, D], [0.0, D]]])
    _set_zones(fl, wid_b, [[[W, yN], [W, D], [xR, D], [xR, yS], [xE, yS], [xE, yN]]])


def gen_cross(fl: Floor, mix, spec_a, spec_b, bidx, floor, bpf) -> None:
    """Cross (+): four wings around a central core; wards A (west+north) and B (east+south)."""
    W, D = fl.width, fl.depth
    cx, cy = W / 2, D / 2
    core = 16.0
    # corridors: horizontal and vertical arms through the core
    fl.add_corridor(rect(1.0, cy - CORR_W / 2, W - 2.0, CORR_W), "동서 복도")
    fl.add_corridor(rect(cx - CORR_W / 2, 1.0, CORR_W, D - 2.0), "남북 복도")
    _vertical_core(fl, cx + CORR_W / 2, cy + CORR_W / 2, core / 2, ROOM_D, f"B{bidx + 1}F{floor}")
    wid_a, wname_a = ward_name(bidx, floor, "A", spec_a)
    wid_b, wname_b = ward_name(bidx, floor, "B", spec_b)
    pa, pb = f"{bidx + 1}{floor:02d}A", f"{bidx + 1}{floor:02d}B"
    ra, rb = [], []
    armH = cx - CORR_W / 2 - core / 2 - 1.0          # west/east arm length
    armV = cy - CORR_W / 2 - core / 2 - 1.0          # north/south arm length
    _end_stairs(fl, 1.0, cy - CORR_W / 2 - ROOM_D, f"B{bidx + 1}F{floor}")
    _end_stairs(fl, W - 1.0 - 3.6, cy - CORR_W / 2 - ROOM_D, f"B{bidx + 1}F{floor}")
    # west arm (ward A): rooms both sides
    _, n, r = _rooms_along(fl, 1.0 + 3.6, cy - CORR_W / 2, armH - 3.6, mix, wid_a, pa, True, 1); ra += r
    _, n, r = _rooms_along(fl, 1.0, cy + CORR_W / 2, armH, mix[::-1], wid_a, pa, False, n); ra += r
    # north arm (ward A): rooms both sides of the vertical corridor
    n, r = _rooms_vertical(fl, cx - CORR_W / 2, 1.0, armV, mix, wid_a, pa, True, n); ra += r
    n, r = _rooms_vertical(fl, cx + CORR_W / 2, 1.0, armV, mix[::-1], wid_a, pa, False, n); ra += r
    # east arm (ward B)
    ex = cx + CORR_W / 2 + core / 2
    _, m, r = _rooms_along(fl, ex, cy - CORR_W / 2, armH - 3.6, mix, wid_b, pb, True, 1); rb += r
    _, m, r = _rooms_along(fl, ex, cy + CORR_W / 2, armH, mix[::-1], wid_b, pb, False, m); rb += r
    # south arm (ward B)
    sy = cy + CORR_W / 2 + core / 2
    m, r = _rooms_vertical(fl, cx - CORR_W / 2, sy, armV, mix, wid_b, pb, True, m); rb += r
    m, r = _rooms_vertical(fl, cx + CORR_W / 2, sy, armV, mix[::-1], wid_b, pb, False, m); rb += r
    # central core: two nurse stations facing the crossing + support
    # ward B's band is only half the core (the elevator block takes the other half), so its shared toilet sits at the
    # east end of the north band, across the crossing from station B
    ns_a = _core(fl, cx - CORR_W / 2 - core / 2, cy - CORR_W / 2 - ROOM_D, core - 3.6, wid_a, "A", pa, with_lounge=False)
    fl.add_room(f"{pb}-toilet-B", "공용 화장실 B", "toilet", rect(cx - CORR_W / 2 + core / 2 - 3.6, cy - CORR_W / 2 - ROOM_D, 3.6, ROOM_D), wid_b)
    ns_b = _core(fl, cx - CORR_W / 2 - core / 2, cy + CORR_W / 2, core / 2 - 0.2, wid_b, "B", pb, with_lounge=False)
    _corridor_displays(fl, 1.0, cx - core / 2, cy, "A")
    _corridor_displays(fl, ex, W - 1.0, cy, "B")
    fl.wards.append({"id": wid_a, "name": wname_a, "specialty": spec_a, "rooms": [x["id"] for x in ra], "nurse_station": ns_a})
    fl.wards.append({"id": wid_b, "name": wname_b, "specialty": spec_b, "rooms": [x["id"] for x in rb], "nurse_station": ns_b})
    xA, xB = cx - CORR_W / 2 - core / 2, ex
    yT, yM = cy - CORR_W / 2 - ROOM_D, cy - CORR_W / 2
    _set_zones(fl, wid_a, [[[0.0, 0.0], [W, 0.0], [W, yT], [xB, yT], [xB, yM], [xA, yM], [xA, D], [0.0, D]]])
    _set_zones(fl, wid_b, [[[W, yT], [W, D], [xA, D], [xA, yM], [xB, yM], [xB, yT]]])


def gen_tower(fl: Floor, mix, spec_a, spec_b, bidx, floor, bpf) -> None:
    """Compact tower: square plate, central core (elevators, stairs, nurse station), ring corridor, rooms on 4 sides."""
    gen_racetrack(fl, mix, spec_a, spec_b, bidx, floor, bpf)


def gen_pod(fl: Floor, mix, spec_a, spec_b, bidx, floor, bpf) -> None:
    """Decentralised nursing: a wide 'open core' corridor with sub-nurse stations (pods) every 4-6 rooms per side."""
    W, D = fl.width, fl.depth
    y_corr = fl.depth / 2 - 3.0
    wide = 6.0                                           # open-core corridor doubles as staff work zone
    fl.add_corridor(rect(1.0, y_corr, W - 2.0, wide), "오픈 코어 복도")
    _vertical_core(fl, W / 2 - 5.0, y_corr - ROOM_D, 10.0, ROOM_D, f"B{bidx + 1}F{floor}")
    _end_stairs(fl, 1.0, y_corr - ROOM_D, f"B{bidx + 1}F{floor}")
    _end_stairs(fl, W - 1.0 - 3.6, y_corr - ROOM_D, f"B{bidx + 1}F{floor}")
    half = (W - 2.0 - 10.0) / 2
    for side, x0, spec in (("A", 1.0, spec_a), ("B", W / 2 + 5.0, spec_b)):
        wid, wname = ward_name(bidx, floor, side, spec)
        prefix = f"{bidx + 1}{floor:02d}{side}"
        _, n, top = _rooms_along(fl, x0 + (3.6 if side == "A" else 0.0), y_corr, half - 3.6, mix, wid, prefix, True, 1)
        core_len = min(16.0, half * 0.35)
        if side == "A":
            _, n, bot = _rooms_along(fl, x0, y_corr + wide, half - core_len, mix[::-1], wid, prefix, False, n)
            ns = _core(fl, x0 + half - core_len, y_corr + wide, core_len, wid, side, prefix, with_lounge=False)
        else:
            ns = _core(fl, x0, y_corr + wide, core_len, wid, side, prefix, with_lounge=False)
            _, n, bot = _rooms_along(fl, x0 + core_len, y_corr + wide, half - core_len, mix[::-1], wid, prefix, False, n)
        # pods: sub nurse stations inside the open core, one per ~5 rooms, each with a display
        n_pods = max(2, round(len(top + bot) / 5))
        for k, px in enumerate(np.linspace(x0 + 4, x0 + half - 4, n_pods)):
            pod = fl.add_room(f"{prefix}-POD{k + 1}", f"포드 {side}{k + 1}", "nurse_station", rect(px - 1.6, y_corr + wide / 2 - 1.2, 3.2, 2.4), wid)
            fl.add_fixture("display", px, y_corr + wide / 2 - 0.9, "central", pod["id"], f"포드 모니터 {side}{k + 1}")
            fl.add_fixture("nurse_desk", px, y_corr + wide / 2 + 0.6, "pod", pod["id"], "분산 간호 스테이션")
        fl.wards.append({"id": wid, "name": wname, "specialty": spec, "rooms": [r["id"] for r in top + bot], "nurse_station": ns})
        _set_zones(fl, wid, [rect(0.0, 0.0, W / 2, D) if side == "A" else rect(W / 2, 0.0, W / 2, D)])


def gen_y(fl: Floor, mix, spec_a, spec_b, bidx, floor, bpf) -> None:
    """Y-shaped triple-wing floor: three wings at 120° around a central core (wards A: wing 1+half of 3, B: wing 2+half of 3)."""
    W, D = fl.width, fl.depth
    cx, cy = W / 2, D / 2 + 4.0
    core_r = 11.0
    wid_a, wname_a = ward_name(bidx, floor, "A", spec_a)
    wid_b, wname_b = ward_name(bidx, floor, "B", spec_b)
    pa, pb = f"{bidx + 1}{floor:02d}A", f"{bidx + 1}{floor:02d}B"
    ra, rb = [], []
    arm = min(W / 2, D / 2 + 4.0) - core_r - 2.0
    counters = {"A": 1, "B": 1}
    zones = {"A": [], "B": []}
    for k, ang in enumerate((-90.0, 30.0, 150.0)):
        ward, prefix, key = (wid_a, pa, "A") if k == 0 else (wid_b, pb, "B") if k == 1 else (wid_a if floor % 2 else wid_b, pa if floor % 2 else pb, "A" if floor % 2 else "B")
        pivot = (cx, cy)
        zones[key].append(rot(rect(cx + core_r - 1.0, cy - CORR_W / 2 - ROOM_D - 0.5, arm + 3.0, CORR_W + 2 * ROOM_D + 1.0), cx, cy, ang))
        # arm generated pointing east from the core edge, then rotated by ang around the core centre
        x0 = cx + core_r
        fl.add_corridor(rot(rect(x0 - 2.0, cy - CORR_W / 2, arm + 2.0, CORR_W), cx, cy, ang), f"윙{k + 1} 복도")
        _, n, r = _rooms_along(fl, x0, cy - CORR_W / 2, arm, mix, ward, prefix, True, counters[key], ang, pivot)
        _, n, r2 = _rooms_along(fl, x0, cy + CORR_W / 2, arm, mix[::-1], ward, prefix, False, n, ang, pivot)
        counters[key] = n
        (ra if key == "A" else rb).extend(r + r2)
        _corridor_displays(fl, x0, x0 + arm, cy, f"{k + 1}", ang, pivot)
    # central hall: an octagonal circulation hall where the three wing corridors meet; elevator/stairs block at its centre
    hall = [[round(cx + core_r * math.cos(math.radians(a)), 2), round(cy + core_r * math.sin(math.radians(a)), 2)] for a in range(0, 360, 45)]
    fl.add_corridor(hall, "코어 홀")
    _vertical_core(fl, cx - 5.0, cy - 2.8, 10.0, 5.6, f"B{bidx + 1}F{floor}")
    # nurse stations and support rooms sit between the wings (angles 90 / 210 / 330), doors on the hall
    def between(name, kind, ang, w, d, ward=None, gw=True):
        return fl.add_room(f"B{bidx + 1}F{floor}-{kind}-{int(ang)}-{name}", name, kind, rot(rect(cx + core_r + 0.2, cy - d / 2, w, d), cx, cy, ang), ward, gateway=gw)
    ns_a = between("간호사실 A", "nurse_station", 90.0, 8.0, 7.0, wid_a)["id"]
    ns_b = between("간호사실 B", "nurse_station", 330.0, 8.0, 7.0, wid_b)["id"]
    for rid, ang in ((ns_a, 90.0), (ns_b, 330.0)):
        room = next(r for r in fl.rooms if r["id"] == rid)
        fl.add_fixture("display", room["cx"], room["cy"], "central", rid, "중앙 모니터", ang)
        px, py = rot([[cx + core_r + 1.6, cy]], cx, cy, ang)[0]
        fl.add_fixture("nurse_desk", px, py, "counter", rid, "간호사 카운터", ang + 90)
    between("투약/처치실 A", "utility", 90.0 + 26, 5.0, 4.0, wid_a, False)
    between("화장실/샤워 A", "toilet", 90.0 - 26, 5.0, 4.0, wid_a)
    between("투약/처치실 B", "utility", 330.0 + 26, 5.0, 4.0, wid_b, False)
    between("화장실/샤워 B", "toilet", 330.0 - 26, 5.0, 4.0, wid_b)
    between("직원휴게실", "staff", 210.0, 7.0, 6.0)
    between("환자휴게실", "lounge", 210.0 + 28, 5.5, 4.5)
    between("피난계단", "stairs", 210.0 - 28, 5.5, 4.5, None, True)
    fl.wards.append({"id": wid_a, "name": wname_a, "specialty": spec_a, "rooms": [x["id"] for x in ra], "nurse_station": ns_a})
    fl.wards.append({"id": wid_b, "name": wname_b, "specialty": spec_b, "rooms": [x["id"] for x in rb], "nurse_station": ns_b})
    _set_zones(fl, wid_a, zones["A"])
    _set_zones(fl, wid_b, zones["B"])


GENERATORS = {"linear": gen_linear, "l": gen_l, "t": gen_t, "racetrack": gen_racetrack, "courtyard": gen_courtyard, "cross": gen_cross,
              "tower": gen_tower, "pod": gen_pod, "y": gen_y}
FLOOR_SIZE = {"linear": (60, 22), "l": (56, 46), "t": (70, 40), "racetrack": (60, 40), "courtyard": (64, 52), "cross": (84, 72),
              "tower": (44, 40), "pod": (78, 26), "y": (96, 88)}


# ------------------------------------------------------------------ diagnostic / ground floor
def gen_diagnostic(fl: Floor, size: str, bidx: int) -> None:
    """Ground floor laid out like a real diagnostic floor: a 4 m 'hospital street' in the middle, on each side a front
    row of rooms (8 m deep, doors on the street) and a back row (7.4 m deep) behind a 1.6 m service corridor.
    Departments: lobby/reception, cardiology diagnostics, blood draw, ultrasound, endoscopy suite, PFT, rehab, outpatient
    clinics (top); ER, imaging suite (X-ray + control, CT + control/equipment, MRI zones II-IV), cath lab, dialysis,
    pharmacy, cafe (bottom).  Room proportions are kept between ~1:1 and 1:2.2."""
    W, D = fl.width, fl.depth
    two_rows = D >= 34
    FRONT, BACK, SVC = 8.0, 7.4, 1.6
    if two_rows:
        street_y = D / 2 - 2.0
        top_front = (street_y - FRONT, FRONT)
        top_back = (top_front[0] - SVC - BACK, BACK)
        bot_front = (street_y + 4.0, FRONT)
        bot_back = (bot_front[0] + FRONT + SVC, BACK)
        fl.add_corridor(rect(1.0, top_front[0] - SVC, W - 2.0, SVC), "북측 서비스 복도")
        fl.add_corridor(rect(1.0, bot_front[0] + FRONT, W - 2.0, SVC), "남측 서비스 복도")
    else:
        street_y = D / 2 - 2.0
        top_front = (1.0, street_y - 1.0)
        bot_front = (street_y + 4.0, D - street_y - 5.0)
        top_back = bot_back = None
    fl.add_corridor(rect(1.0, street_y, W - 2.0, 4.0), "메인 스트리트")
    P = f"B{bidx + 1}-01"

    def row(y_h, items, start_x=1.0, end_x=None):
        """items: (name, kind, width, gateway) laid left->right; a tuple of two items stacks them vertically
        (used for small booths so no room becomes a thin sliver).  Returns next x."""
        y, h = y_h
        xx = start_x
        end_x = W - 1.0 if end_x is None else end_x
        for it in items:
            if isinstance(it[0], tuple):
                (n1, k1, w, g1), (n2, k2, _, g2) = it
                if xx + w > end_x + 0.01:
                    break
                fl.add_room(f"{P}-{n1}", n1, k1, rect(xx, y, w, h / 2), gateway=g1)
                fl.add_room(f"{P}-{n2}", n2, k2, rect(xx, y + h / 2, w, h / 2), gateway=g2)
                xx += w
                continue
            name, kind, w, gw = it
            if xx + w > end_x + 0.01:
                break
            fl.add_room(f"{P}-{name}", name, kind, rect(xx, y, w, h), gateway=gw)
            xx += w
        return xx

    row_tag = {}

    def fill_clinics(y_h, start_x, kind_name="외래 진료실", gw_every=1):
        y, h = y_h
        tag = row_tag.setdefault(round(y, 1), f"{'N' if y < street_y else 'S'}{len([t for t in row_tag.values() if t[0] == ('N' if y < street_y else 'S')]) + 1}")
        xx, k = start_x, 1
        while xx + 4.2 <= W - 1.0:
            if kind_name.startswith("외래") and k % 8 == 7 and xx + 4.0 + 4.2 <= W - 1.0:
                # public toilet block (남/여/장애인) every 7 clinics so no waiting area is more than ~30 m from one
                fl.add_room(f"{P}-화장실-{tag}{k}", f"외래 화장실 {tag}", "toilet", rect(xx, y, 4.0, h), gateway=True)
                xx += 4.0
            w = min(4.2, W - 1.0 - xx)
            if w < 3.0:
                break
            fl.add_room(f"{P}-{'진료실' if kind_name.startswith('외래 진') else '사무실' if kind_name.startswith('행정') else '대기'}#{k}{tag}", f"{kind_name} {k}", "exam" if kind_name.startswith("외래") else "office", rect(xx, y, w, h), gateway=(k % gw_every == 0))
            xx += w
            k += 1

    # ---------------- top front: lobby, reception, cardiology diagnostics, blood draw, ultrasound, clinics
    lob_w = {"S": 12.0, "M": 14.0, "L": 16.0, "XL": 18.0}[size]
    lobby = fl.add_room(f"{P}-로비", "로비", "lobby", rect(1.0, top_front[0], lob_w, top_front[1]))
    fl.add_fixture("reception", 1.0 + lob_w / 2, top_front[0] + 2.0, "desk", lobby["id"], "원무과/접수")
    fl.add_fixture("display", 1.0 + lob_w / 2, top_front[0] + 0.6, "queue", lobby["id"], "로비 안내 전광판")
    items = [("로비 화장실", "toilet", 4.0, True), ("ECG실", "exam", 4.6, True), ("심초음파실", "exam", 5.6, True)]
    if size != "S":
        items.append(("심초음파실2", "exam", 5.6, True))
    items += [("채혈실", "exam", 6.0, True), ("채혈 대기", "waiting", 5.0, True), ("초음파실", "exam", 5.0, True)]
    if size in ("L", "XL"):
        items.append(("초음파실2", "exam", 5.0, True))
    x = row(top_front, items, 1.0 + lob_w)
    fill_clinics(top_front, x)
    # ---------------- top back: vertical core, toilets, endoscopy, PFT, rehab
    if top_back:
        items = [("계단실", "stairs", 3.4, True), ("엘리베이터 홀", "elevator", 6.0, True), ("화장실", "toilet", 4.0, True)]
        if size != "S":
            items += [("내시경실", "exam", 6.0, True), ("내시경실2", "exam", 6.0, True), ("내시경 회복실", "recovery", 6.4, True), ("내시경 화장실", "toilet", 3.4, True), ("내시경 세척/준비", "prep", 4.0, False)]
        items += [("폐기능검사실", "exam", 5.0, True)]
        x = row(top_back, items)
        rehab_w = min(16.0, W - 1.0 - x)
        if rehab_w >= 8.0:
            fl.add_room(f"{P}-재활치료실", "재활치료실", "exam", rect(x, top_back[0], rehab_w, top_back[1]))
            x += rehab_w
        fill_clinics(top_back, x, "행정 사무실", 3)
    else:
        row(top_front, [("화장실", "toilet", 3.6, True), ("계단실", "stairs", 3.4, True), ("엘리베이터 홀", "elevator", 6.0, True)], W - 14.1)
    # ---------------- bottom front: ER, imaging front (X-ray rooms, waiting, CT), pharmacy/cafe
    er_w = {"S": 14.0, "M": 18.0, "L": 22.0, "XL": 26.0}[size]
    bays = {"S": 4, "M": 6, "L": 8, "XL": 10}[size]
    er = fl.add_room(f"{P}-응급실", "응급실", "er", rect(1.0, bot_front[0], er_w, bot_front[1]))
    bw = (er_w - 2.0) / math.ceil(bays / 2)
    er["beds"] = [{"id": f"{er['id']}-{i + 1}", "x": round(1.0 + 1.0 + bw * ((i // 2) + 0.5), 2), "y": round(bot_front[0] + (1.5 if i % 2 == 0 else bot_front[1] - 1.5), 2), "angle": 0 if i % 2 == 0 else 180} for i in range(bays)]
    fl.add_fixture("nurse_desk", 1.0 + er_w / 2, bot_front[0] + bot_front[1] / 2, "er", er["id"], "응급 간호사실")
    fl.add_fixture("display", 1.0 + er_w / 2 - 1.5, bot_front[0] + bot_front[1] / 2 - 1.0, "central", er["id"], "응급실 중앙 모니터")
    items = [("X-ray실", "exam", 5.6, True), (("X-ray 조정", "control", 3.4, False), ("탈의실", "prep", 3.4, False))]
    if size != "S":
        items += [("X-ray실2", "exam", 5.6, True), (("X-ray 조정2", "control", 3.4, False), ("탈의실2", "prep", 3.4, False))]
    items += [("영상 대기", "waiting", 6.0, True), ("영상 화장실", "toilet", 3.6, True)]
    if size != "S":
        items += [("CT실", "exam", 7.0, True), (("CT 조정실", "control", 3.6, False), ("CT 준비실", "prep", 3.6, True))]
    x = row(bot_front, items, 1.0 + er_w)
    if size != "S" and x + 10 <= W - 1.0:
        dw = min(18.0, W - 1.0 - x - 10.5)
        if dw >= 8.0:
            dial = fl.add_room(f"{P}-투석실", "투석실", "exam", rect(x, bot_front[0], dw, bot_front[1]))
            n_st = int(dw // 2.2) * 2
            dial["beds"] = [{"id": f"{dial['id']}-{i + 1}", "x": round(x + 1.1 + (i // 2) * 2.2, 2), "y": round(bot_front[0] + (1.5 if i % 2 == 0 else bot_front[1] - 1.5), 2), "angle": 0 if i % 2 == 0 else 180} for i in range(n_st)]
            fl.add_fixture("display", x + dw / 2, bot_front[0] + bot_front[1] / 2, "central", dial["id"], "투석실 중앙 모니터")
            x += dw
            if x + 3.4 + 6 <= W - 1.0:
                fl.add_room(f"{P}-투석 화장실", "투석 화장실", "toilet", rect(x, bot_front[0], 3.4, bot_front[1]), gateway=True)
                x += 3.4
    if x + 6 <= W - 1.0:
        pw = min(6.0, W - 1.0 - x - 4.5) if W - 1.0 - x > 10.5 else W - 1.0 - x
        fl.add_room(f"{P}-약제부", "약제부/외래약국", "reception", rect(x, bot_front[0], pw, bot_front[1]), gateway=True)
        x += pw
    if x + 4.5 <= W - 1.0:
        cw = min(12.0, W - 1.0 - x)
        fl.add_room(f"{P}-편의시설", "편의점/카페", "lounge", rect(x, bot_front[0], cw, bot_front[1]), gateway=True)
        x += cw
        fill_clinics(bot_front, x, "외래 진료실", 2)
    # ---------------- bottom back: ER support, CT equipment, MRI suite (zones II-IV), cath lab, offices
    if bot_back:
        items = [("소생실", "exam", 5.0, True), ("트리아지/접수", "reception", 4.0, True), ("응급 관찰실", "recovery", 6.0, True), ("응급 화장실", "toilet", 3.4, True)]
        if size != "S":
            items += [(("CT 장비실", "equipment", 3.6, False), ("영상 판독실", "office", 3.6, False))]
        if size in ("L", "XL"):
            items += [("MRI 스크리닝/대기 (Zone II)", "waiting", 5.0, True), ("MRI 조정실 (Zone III)", "control", 4.4, False),
                      ("MRI실 (Zone IV, RF 차폐)", "exam", 8.6, False), ("MRI 장비실", "equipment", 4.0, False),
                      ("심혈관조영실", "exam", 8.0, True), ("조영실 조정", "control", 3.6, False)]
        x = row(bot_back, items)
        fill_clinics(bot_back, x, "행정 사무실", 3)
    for k, sx in enumerate(np.linspace(8.0, W - 8.0, 4)):
        fl.add_fixture("display", sx, street_y + 2.0, "corridor", "", f"스트리트 전광판 {k + 1}")


# ------------------------------------------------------------------ hospital assembly
def generate(bed_capacity: int, seed: int, template: str = "auto", name: str | None = None, max_buildings: int = 3) -> dict:
    key = select_template(bed_capacity, template)
    T = TEMPLATES[key]
    rng = np.random.default_rng(seed + 101)
    topo = T["topology"]
    fw, fd = FLOOR_SIZE[topo]
    bpf = T["beds_per_floor"]
    # measure the real bed yield of one ward floor for this template, then size the hospital to the capacity
    probe = Floor(0, 3, "ward", "probe", fw, fd)
    GENERATORS[topo](probe, list(T["room_mix"]), "심장내과", "순환기내과", 0, 3, bpf)
    bpf = max(1, sum(len(r["beds"]) for r in probe.rooms if r["kind"] == "room"))
    n_ward_floors = max(1, math.ceil(bed_capacity / bpf))
    # buildings vs floors: keep one building up to ~12 ward floors, then add a wing (max 3 by default); once every wing is
    # in use the floors grow instead, so a 60-bed clinic is one 4-5 storey block and a 2500-bed centre is three ~17-storey towers
    target_floors = 8 if T["scale"] == "S" else 12
    n_buildings = max(1, math.ceil(n_ward_floors / target_floors))
    n_buildings = min(n_buildings, T.get("buildings_max", 8), max(1, max_buildings))
    per_building = math.ceil(n_ward_floors / n_buildings)
    names = ["본관", "별관", "신관", "동관", "서관", "암병동", "심장센터", "제2별관"]
    # specialties per ward: departments are grouped, i.e. one department fills consecutive units (A then B on a floor,
    # then the floor above) rather than being scattered over the building, the way real hospitals stack a department
    n_wards = n_ward_floors * 2
    spec_list: list[str] = []
    for s_, c in SPECIALTY_SHARE.items():
        spec_list += [s_] * max(0, round(c * n_wards))
    while len(spec_list) < n_wards:
        spec_list.append("심장내과")
    spec_list = spec_list[:n_wards]
    hospital_name = name or {"S": "바이오시그널 지역병원", "M": "바이오시그널 종합병원", "L": "바이오시그널 대학병원", "XL": "바이오시그널 메디컬센터"}[T["scale"]]
    out = {"version": 1, "units": "m",
           "hospital": {"name": hospital_name, "template": key, "template_name": T["name"], "scale": T["scale"], "bed_capacity": bed_capacity,
                        "seed": seed, "topology": topo, "max_beds_per_room": 8, "notes": "간호단위 = 층당 2개 병동(A/B), 각 병동: 간호사실(중앙 모니터 2대)·투약실·청결/오염 처치실·직원휴게실·화장실·샤워실·환자휴게실"},
           "buildings": [], "floors": []}
    floor_i = 0
    total_beds = 0
    for b in range(n_buildings):
        bx = b * (fw + 12.0)
        out["buildings"].append({"idx": b, "name": names[b % len(names)], "x": round(bx, 1), "y": 0.0, "w": fw, "d": fd})
        # ground floor: diagnostics in building 0, lobby/outpatient elsewhere
        g = Floor(b, 1, "diagnostic" if b == 0 else "lobby", "검사/응급/영상" if b == 0 else "로비/외래", fw if b else max(fw, {"S": 64.0, "M": 84.0, "L": 104.0, "XL": 124.0}[T["diag"]]), fd if b else max(fd, 40.0))
        if b == 0:
            gen_diagnostic(g, T["diag"], b)
        else:
            gen_diagnostic(g, "S", b)
        out["floors"].append(g.to_dict())
        # ward floors (floor 3 upward; floor 2 = surgery/ICU placeholder w/o beds monitored here)
        for f in range(3, 3 + per_building):
            if floor_i >= n_ward_floors:
                break
            fl = Floor(b, f, "ward", f"병동 {f}F", fw, fd)
            mix = list(T["room_mix"])
            rng.shuffle(mix)
            # last ward floor only takes the beds still missing, so the hospital lands on the requested capacity
            # instead of a full extra floor (1000 patients -> ~1250 beds, not 1450)
            remaining = bed_capacity - total_beds
            floor_bpf = bpf if floor_i < n_ward_floors - 1 else max(8, min(bpf, remaining))
            GENERATORS[topo](fl, mix, spec_list[floor_i * 2], spec_list[floor_i * 2 + 1], b, f, floor_bpf)
            _mark_isolation(fl)
            # corridor gateways are derived later; count beds
            total_beds += sum(len(r["beds"]) for r in fl.rooms if r["kind"] == "room")
            out["floors"].append(fl.to_dict())
            floor_i += 1
    # land on the requested capacity: the generators yield whole rooms per floor, so walk the top ward floors backwards and
    # empty rooms (they stay on the plan as "(공실)") until the bed count is within a room of the target
    excess = total_beds - bed_capacity
    if excess > 4:
        wards = [f for f in out["floors"] if f["kind"] == "ward"]
        for fl in reversed(wards):
            rooms = [r for r in fl["rooms"] if r["kind"] == "room" and r["beds"]]
            for r in reversed(rooms):
                if excess <= 4:
                    break
                if sum(len(x["beds"]) for x in fl["rooms"] if x["kind"] == "room") - len(r["beds"]) < 8:
                    break                                     # never strip a floor below 8 beds
                excess -= len(r["beds"]); total_beds -= len(r["beds"]); r["beds"] = []; r["name"] = r["name"] + " (공실)"
            if excess <= 4:
                break
    out["hospital"]["beds"] = total_beds
    out["hospital"]["n_buildings"] = n_buildings
    out["hospital"]["n_floors"] = len(out["floors"])
    return out


def _mark_isolation(fl: Floor) -> None:
    """One airborne-infection isolation single room per <=35 beds (FGI): the first 1-bed rooms of each ward."""
    for w in fl.wards:
        ids = set(w["rooms"])
        beds = sum(len(r["beds"]) for r in fl.rooms if r["id"] in ids)
        need = max(1, math.ceil(beds / 35))
        for r in fl.rooms:
            if need <= 0:
                break
            if r["id"] in ids and r["kind"] == "room" and len(r["beds"]) == 1:
                r["kind"] = "isolation"
                r["name"] = f"{r['name']} 격리(음압)"
                need -= 1


def validate(data: dict) -> list[str]:
    errs = []
    if not isinstance(data, dict) or data.get("version") != 1:
        errs.append("version must be 1")
        return errs
    for k in ("hospital", "buildings", "floors"):
        if k not in data:
            errs.append(f"missing {k}")
    for fl in data.get("floors", []):
        for r in fl.get("rooms", []):
            if "poly" not in r or len(r["poly"]) < 3:
                errs.append(f"room {r.get('id')} needs poly")
            for b in r.get("beds", []):
                if "x" not in b or "y" not in b:
                    errs.append(f"bed {b.get('id')} needs x/y")
        zones = {w["id"]: w.get("zones", []) for w in fl.get("wards", [])}
        for r in fl.get("rooms", []):
            z = zones.get(r.get("ward"))
            if z and r.get("kind") in ("room", "isolation") and not any(point_in_poly(r["cx"], r["cy"], pl) for pl in z):
                errs.append(f"room {r['id']} lies outside its ward zone {r['ward']}")
    return errs


def to_json(data: dict) -> str:
    return json.dumps(data, ensure_ascii=False)
