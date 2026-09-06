"""Hospital runtime model built on top of the layout engine.

Consumes a layout JSON (generated from one of ten templates or imported) and
derives everything the world engine needs: rooms/beds/wards, gateways (one
per room that allows BLE + corridor gateways every ~15 m), exam rooms, staff,
and geometry arrays for RSSI.  Gateway row index == runtime table row.
"""
from __future__ import annotations

import math

import numpy as np

from .names import staff_name
from . import layout as L

GW_TYPES = ["room", "corridor", "elevator", "stairs", "toilet", "shower", "nurse_station", "exam", "lobby", "er", "mobile", "support"]
GW_TYPE_ID = {t: i for i, t in enumerate(GW_TYPES)}
EXAM_ROOMS = ["ECG실", "심초음파실", "X-ray실", "CT실", "MRI실", "채혈실", "내시경실", "폐기능검사실", "재활치료실", "투석실", "심혈관조영실"]
NO_BLE_ROOMS = {"MRI실"}
DOCTOR_TITLES = ["교수", "임상조교수", "전임의", "전공의 4년차", "전공의 3년차", "전공의 2년차", "전공의 1년차"]
CORRIDOR_GW_SPACING = 15.0
_GW_KIND = {"room": "room", "corridor": "corridor", "elevator": "elevator", "stairs": "stairs", "toilet": "toilet", "shower": "shower",
            "nurse_station": "nurse_station", "exam": "exam", "recovery": "exam", "lobby": "lobby", "er": "er"}


GW_COVER_M = 8.5                     # design reach of a ceiling gateway in open space (10 m nominal minus margin)
CROWD_DENSITY = {"lobby": 0.25, "waiting": 0.30, "lounge": 0.20, "reception": 0.20}   # peak persons per m² (outpatient morning)
PATCHED_SHARE = 0.5                  # share of people in a public area who wear a monitored patch


def _poly_bbox(poly):
    xs = [p[0] for p in poly]
    ys = [p[1] for p in poly]
    return min(xs), min(ys), max(xs), max(ys)


class Hospital:
    def __init__(self, bed_capacity: int, seed: int, gw_capacity: int = 32, corridor_gateways: bool = True,
                 outpatients: int = 0, name: str | None = None, template: str = "auto", layout_data: dict | None = None, max_buildings: int = 3):
        self.rng = np.random.default_rng(seed + 11)
        self.gw_capacity = gw_capacity
        self.corridor_gateways = corridor_gateways
        self.layout = layout_data if layout_data else L.generate(bed_capacity, seed, template, name, max_buildings)
        self.name = self.layout["hospital"]["name"]
        self.template = self.layout["hospital"].get("template", "imported")
        self.buildings: list[dict] = [dict(b) for b in self.layout["buildings"]]
        self.floors: list[dict] = []
        self.wards: list[dict] = []
        self.rooms: list[dict] = []
        self.beds: list[dict] = []
        self.gateways: list[dict] = []
        self.staff: list[dict] = []
        self.room_by_id: dict[str, int] = {}
        self.exam_rooms: dict[str, int] = {}
        self.ward_rooms: dict[int, list[int]] = {}
        self._build()
        self._build_staff()
        self.n_fixed_gateways = len(self.gateways)
        for i in range(outpatients):
            self.add_mobile_gateway(i)
        self._geom()

    # ------------------------------------------------------------ derive runtime model
    def _add_gateway(self, gtype: str, bidx: int, floor: int, x: float, y: float, room_idx: int) -> int:
        gidx = len(self.gateways)
        gid = f"GW-{bidx + 1}{floor:02d}-{gidx:04d}"
        self.gateways.append({"idx": gidx, "id": gid, "base_id": gid, "gw_no": gidx + 1, "type": gtype, "type_id": GW_TYPE_ID[gtype], "building_idx": bidx,
                              "building": self.buildings[bidx]["name"], "floor": floor, "x": round(x, 2), "y": round(y, 2), "room_idx": room_idx,
                              "capacity": self.gw_capacity, "mac": f"C4:7F:{(gidx >> 8) & 0xFF:02X}:{gidx & 0xFF:02X}:{(gidx * 37) & 0xFF:02X}:{(gidx * 91) & 0xFF:02X}",
                              "ip": f"10.{20 + bidx}.{floor}.{(gidx % 250) + 2}", "fw": "gw-fw 2.4.1", "ble_channels": self.gw_capacity})
        return gidx

    def _build(self) -> None:
        for fl in self.layout["floors"]:
            bidx, floor = fl["building_idx"], fl["floor"]
            self.floors.append({"building_idx": bidx, "building": self.buildings[bidx]["name"], "floor": floor, "kind": fl["kind"], "name": fl.get("name", ""),
                                "width": fl["width"], "depth": fl["depth"]})
            ward_idx_by_id: dict[str, int] = {}
            for w in fl.get("wards", []):
                widx = len(self.wards)
                ward_idx_by_id[w["id"]] = widx
                self.wards.append({"idx": widx, "id": w["id"], "name": w["name"], "specialty": w["specialty"], "building_idx": bidx,
                                   "building": self.buildings[bidx]["name"], "floor": floor, "room_idxs": [], "bed_idxs": [], "nurse_station_room": -1,
                                   "doctor_ids": [], "nurse_ids": [], "toilet_room": -1, "shower_room": -1})
                self.ward_rooms[widx] = []
            for r in fl["rooms"]:
                ridx = len(self.rooms)
                widx = ward_idx_by_id.get(r.get("ward") or "", None)
                kind = r["kind"]
                room = {"idx": ridx, "id": r["id"], "name": r["name"], "kind": kind, "building_idx": bidx, "building": self.buildings[bidx]["name"],
                        "floor": floor, "x": r["cx"], "y": r["cy"], "poly": r["poly"], "ward_idx": widx if widx is not None else None, "bed_idxs": [], "gateway_idx": -1}
                self.rooms.append(room)
                self.room_by_id[r["id"]] = ridx
                if kind == "room":
                    for b in r.get("beds", []):
                        bidx_ = len(self.beds)
                        self.beds.append({"idx": bidx_, "id": b["id"], "room_idx": ridx, "patient_id": 0, "x": b["x"], "y": b["y"], "angle": b.get("angle", 0)})
                        room["bed_idxs"].append(bidx_)
                    if widx is not None:
                        self.wards[widx]["room_idxs"].append(ridx)
                        self.wards[widx]["bed_idxs"] += room["bed_idxs"]
                        self.ward_rooms[widx].append(ridx)
                if widx is not None:
                    if kind == "nurse_station" and self.wards[widx]["nurse_station_room"] < 0:
                        self.wards[widx]["nurse_station_room"] = ridx
                    if kind == "toilet" and self.wards[widx]["toilet_room"] < 0:
                        self.wards[widx]["toilet_room"] = ridx
                    if kind == "shower" and self.wards[widx]["shower_room"] < 0:
                        self.wards[widx]["shower_room"] = ridx
                if kind == "exam":
                    for key in EXAM_ROOMS:
                        if r["name"].startswith(key) and key not in self.exam_rooms:
                            self.exam_rooms[key] = ridx
                if r.get("gateway", True) and not any(r["name"].startswith(k) for k in NO_BLE_ROOMS):
                    gtype = _GW_KIND.get(kind, "support")
                    # enough ceiling gateways that every point of the room is within GW_COVER_M of one (no dead zone):
                    # they are spread along the room's long axis; a 26 m ER therefore gets two, a 6-bed ward room one
                    idxs = []
                    if r.get("poly"):
                        x0, y0, x1, y1 = _poly_bbox(r["poly"])
                        long_, short_ = max(x1 - x0, y1 - y0), min(x1 - x0, y1 - y0)
                        reach = 2.0 * math.sqrt(max(1.0, GW_COVER_M ** 2 - (short_ / 2) ** 2))
                        n_cover = max(1, math.ceil(long_ / reach - 1e-6))
                        # crowd rule: lobbies, waiting areas, lounges/cafes fill up at peak hours; keep the expected number of
                        # patched people per gateway under 75 % of its BLE capacity (peak density x share wearing a patch)
                        dens = CROWD_DENSITY.get(kind, 0.0)
                        expected = (x1 - x0) * (y1 - y0) * dens * PATCHED_SHARE
                        n_cap = max(1, math.ceil(expected / (0.75 * self.gw_capacity) - 1e-6))
                        n = max(n_cover, n_cap)
                        for k in range(n):
                            t = (k + 0.5) / n
                            gx = x0 + t * (x1 - x0) if (x1 - x0) >= (y1 - y0) else r["cx"]
                            gy = y0 + t * (y1 - y0) if (x1 - x0) < (y1 - y0) else r["cy"]
                            idxs.append(self._add_gateway(gtype, bidx, floor, gx, gy, ridx))
                    else:
                        idxs.append(self._add_gateway(gtype, bidx, floor, r["cx"], r["cy"], ridx))
                    room["gateway_idx"] = idxs[0]
                    room["gateway_idxs"] = idxs
            # corridor gateways (pseudo-rooms so movement logic can target them)
            if self.corridor_gateways:
                for ci, c in enumerate(fl.get("corridors", [])):
                    x0, y0, x1, y1 = _poly_bbox(c["poly"])
                    horiz = (x1 - x0) >= (y1 - y0)
                    length = (x1 - x0) if horiz else (y1 - y0)
                    n = max(1, int(round(length / CORRIDOR_GW_SPACING)))
                    for k in range(n):
                        t = (k + 0.5) / n
                        cx = x0 + t * (x1 - x0) if horiz else (x0 + x1) / 2
                        cy = (y0 + y1) / 2 if horiz else y0 + t * (y1 - y0)
                        ridx = len(self.rooms)
                        rid = f"B{bidx + 1}-{floor:02d}-복도{ci + 1}-{k + 1}"
                        self.rooms.append({"idx": ridx, "id": rid, "name": f"{c.get('name', '복도')} {k + 1}", "kind": "corridor", "building_idx": bidx,
                                           "building": self.buildings[bidx]["name"], "floor": floor, "x": round(cx, 2), "y": round(cy, 2), "poly": None,
                                           "ward_idx": None, "bed_idxs": [], "gateway_idx": -1})
                        self.room_by_id[rid] = ridx
                        self.rooms[ridx]["gateway_idx"] = self._add_gateway("corridor", bidx, floor, cx, cy, ridx)
        self.bed_capacity = len(self.beds)

    def _build_staff(self) -> None:
        sid = 1
        for w in self.wards:
            for k in range(4):
                sex = "M" if self.rng.random() < 0.6 else "F"
                self.staff.append({"id": f"D{sid:04d}", "name": staff_name(self.rng, sex), "role": "doctor", "title": DOCTOR_TITLES[min(k * 2, 6)],
                                   "sex": sex, "specialty": w["specialty"], "ward_idx": w["idx"], "ward": w["id"]})
                w["doctor_ids"].append(f"D{sid:04d}")
                sid += 1
            for k in range(14):
                sex = "F" if self.rng.random() < 0.9 else "M"
                self.staff.append({"id": f"N{sid:04d}", "name": staff_name(self.rng, sex), "role": "nurse", "title": "수간호사" if k == 0 else "간호사",
                                   "sex": sex, "specialty": w["specialty"], "ward_idx": w["idx"], "ward": w["id"], "shift": ["day", "evening", "night"][k % 3]})
                w["nurse_ids"].append(f"N{sid:04d}")
                sid += 1
        self.staff_by_id = {s["id"]: s for s in self.staff}

    def _geom(self) -> None:
        self.gw_xyz = np.array([[g["x"], g["y"], g["floor"] * 4.0 + g["building_idx"] * 1000.0] for g in self.gateways], dtype=np.float32)
        self.gw_room = np.array([g["room_idx"] for g in self.gateways], dtype=np.int32)

    def add_mobile_gateway(self, k: int) -> int:
        gidx = len(self.gateways)
        self.gateways.append({"idx": gidx, "id": f"MGW-{k:04d}", "base_id": f"MGW-{k:04d}", "gw_no": gidx + 1, "type": "mobile", "type_id": GW_TYPE_ID["mobile"], "building_idx": 99,
                              "building": "원외(MCOT)", "floor": 0, "x": 5000.0 + k * 50, "y": 5000.0, "room_idx": -1, "capacity": 1,
                              "mac": f"D8:3A:DD:{(k >> 8) & 0xFF:02X}:{k & 0xFF:02X}:01", "ip": f"100.64.{k // 250}.{k % 250 + 1}",
                              "fw": "mcot-app 1.9.0", "ble_channels": 1})
        if hasattr(self, "gw_xyz"):
            self._geom()
        return gidx

    # ------------------------------------------------------------ queries
    def ward_for(self, specialty: str, rng: np.random.Generator) -> list[int]:
        match = [w["idx"] for w in self.wards if w["specialty"] == specialty]
        others = [w["idx"] for w in self.wards if w["specialty"] != specialty]
        rng.shuffle(match)
        rng.shuffle(others)
        return match + others

    def free_bed_in_ward(self, widx: int) -> int:
        for b in self.wards[widx]["bed_idxs"]:
            if self.beds[b]["patient_id"] == 0:
                return b
        return -1

    def floor_rooms(self, bidx: int, floor: int, kind: str | None = None) -> list[dict]:
        return [r for r in self.rooms if r["building_idx"] == bidx and r["floor"] == floor and (kind is None or r["kind"] == kind)]

    def ward_room_of_kind(self, widx: int, kind: str) -> int:
        """Toilet/shower of the ward, else nearest one on the same floor."""
        w = self.wards[widx]
        key = {"toilet": "toilet_room", "shower": "shower_room"}.get(kind)
        if key and w[key] >= 0:
            return w[key]
        cands = self.floor_rooms(w["building_idx"], w["floor"], kind)
        return cands[0]["idx"] if cands else -1

    def lobby_room(self, bidx: int) -> int:
        for b in (bidx, 0):
            for r in self.rooms:
                if r["building_idx"] == b and r["kind"] == "lobby":
                    return r["idx"]
        return -1

    def rssi_from(self, room_idx: int, gw_idx: int, rng: np.random.Generator | None = None) -> float:
        r = self.rooms[room_idx]
        g = self.gateways[gw_idx]
        if g["building_idx"] != r["building_idx"]:
            return -120.0
        d = math.hypot(r["x"] - g["x"], r["y"] - g["y"])
        walls = 0 if g["room_idx"] == room_idx else (1 if g["type"] == "corridor" else 2)
        floors = abs(g["floor"] - r["floor"])
        rssi = -40.0 - 20.0 * math.log10(max(1.0, d)) - 6.0 * walls - 22.0 * floors
        if rng is not None:
            rssi += rng.normal(0, 1.5)
        return rssi

    def candidate_gateways(self, room_idx: int) -> np.ndarray:
        r = self.rooms[room_idx]
        key = r["floor"] * 4.0 + r["building_idx"] * 1000.0
        same = np.where(np.abs(self.gw_xyz[:, 2] - key) < 0.5)[0]
        if same.size == 0:
            return same
        d = np.hypot(self.gw_xyz[same, 0] - r["x"], self.gw_xyz[same, 1] - r["y"])
        walls = np.where(self.gw_room[same] == room_idx, 0, 1)
        rssi = -40.0 - 20.0 * np.log10(np.maximum(1.0, d)) - 6.0 * walls
        return same[np.argsort(-rssi)]

    # ------------------------------------------------------------ export (EMR / router)
    def describe(self) -> dict:
        h = self.layout["hospital"]
        return {"name": self.name, "template": self.template, "template_name": h.get("template_name"), "topology": h.get("topology"), "scale": h.get("scale"),
                "buildings": self.buildings, "n_floors": len(self.floors), "n_wards": len(self.wards), "n_rooms": len(self.rooms), "n_beds": len(self.beds),
                "n_gateways": len(self.gateways), "n_staff": len(self.staff), "max_beds_per_room": 8, "units": "m"}

    def export_layout(self) -> dict:
        """Full plan for the router: layout JSON + derived gateways (row, hardware number, position) + bed ids."""
        out = {k: v for k, v in self.layout.items()}
        out["gateways"] = [{"row": g["idx"], "gw_no": g["gw_no"], "id": g["id"], "mac": g["mac"], "type": g["type"], "building_idx": g["building_idx"], "floor": g["floor"],
                            "x": g["x"], "y": g["y"], "room": self.rooms[g["room_idx"]]["id"] if g["room_idx"] >= 0 else None, "capacity": g["capacity"]} for g in self.gateways]
        out["wards"] = [{"id": w["id"], "name": w["name"], "specialty": w["specialty"], "building_idx": w["building_idx"], "floor": w["floor"],
                         "rooms": [self.rooms[i]["id"] for i in w["room_idxs"]], "beds": [self.beds[b]["id"] for b in w["bed_idxs"]]} for w in self.wards]
        out["exam_rooms"] = {k: self.rooms[v]["id"] for k, v in self.exam_rooms.items()}
        return out

    def floor_map(self, bidx: int, floor: int) -> dict:
        fl = next((f for f in self.layout["floors"] if f["building_idx"] == bidx and f["floor"] == floor), None)
        if fl is None:
            return {"building_idx": bidx, "floor": floor, "rooms": [], "corridors": [], "fixtures": [], "gateways": [], "width": 10, "depth": 10}
        rooms = []
        for r in fl["rooms"]:
            ridx = self.room_by_id.get(r["id"])
            rr = dict(r)
            if ridx is not None:
                rr["idx"] = ridx
                rr["gateway_idx"] = self.rooms[ridx]["gateway_idx"]
                rr["bed_list"] = [{**self.beds[b]} for b in self.rooms[ridx]["bed_idxs"]]
            rooms.append(rr)
        gws = [{k: g[k] for k in ("idx", "id", "gw_no", "type", "x", "y", "room_idx", "capacity", "mac", "ip")} for g in self.gateways if g["building_idx"] == bidx and g["floor"] == floor]
        return {"building_idx": bidx, "building": self.buildings[bidx]["name"], "floor": floor, "kind": fl["kind"], "name": fl.get("name", ""), "width": fl["width"], "depth": fl["depth"],
                "rooms": rooms, "corridors": fl["corridors"], "fixtures": fl["fixtures"], "wards": fl.get("wards", []), "gateways": gws}
