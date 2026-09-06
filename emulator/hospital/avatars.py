"""Avatar parameter assignment (python-avatars) matched to sex / age / nationality."""
from __future__ import annotations

import numpy as np

try:
    import python_avatars as pa
    HAVE_PA = True
except Exception:  # pragma: no cover
    HAVE_PA = False

HAIR_M_YOUNG = ["SHORT_FLAT", "SHORT_ROUND", "SHORT_WAVED", "CAESAR", "CAESAR_SIDE_PART", "QUIFF", "POMPADOUR", "BUZZCUT", "SHORT_CURLY"]
HAIR_M_OLD = ["SHORT_FLAT", "SHORT_ROUND", "CAESAR", "SIDES", "NONE", "SHORT_WAVED", "SIDES"]
HAIR_F_YOUNG = ["BOB", "BUN", "LONG_NOT_TOO_LONG", "STRAIGHT_1", "STRAIGHT_2", "CURVY", "MIA_WALLACE", "PIXIE", "LOOSE_HAIR", "STRAIGHT_STRAND"]
HAIR_F_OLD = ["BOB", "BUN", "SHORT_ROUND", "SHORT_CURLY", "CURLY", "FRIZZLE", "PIXIE"]
SKIN_BY_NAT = {"KR": ["LIGHT", "PALE", "YELLOW"], "CN": ["LIGHT", "YELLOW", "PALE"], "JP": ["LIGHT", "PALE"], "VN": ["TANNED", "LIGHT", "BROWN"],
               "TH": ["TANNED", "BROWN"], "PH": ["TANNED", "BROWN"], "ID": ["BROWN", "TANNED"], "NP": ["BROWN", "TANNED", "DARK_BROWN"],
               "UZ": ["LIGHT", "TANNED"], "RU": ["PALE", "LIGHT"], "US": ["PALE", "LIGHT", "TANNED", "BROWN", "DARK_BROWN", "BLACK"]}
CLOTHES = ["SHIRT_CREW_NECK", "SHIRT_V_NECK", "SHIRT_SCOOP_NECK", "HOODIE", "COLLAR_SWEATER", "BLAZER_SHIRT"]
CLOTH_COLORS = ["PASTEL_BLUE", "PASTEL_GREEN", "HEATHER", "GRAY_01", "BLUE_02", "WHITE", "PASTEL_YELLOW", "PINK"]


def assign(rng: np.random.Generator, p: dict) -> dict:
    age, sex, nat = p["age"], p["sex"], p["nationality"]
    old = age >= 62
    if sex == "M":
        hair = (HAIR_M_OLD if old else HAIR_M_YOUNG)[int(rng.integers(len(HAIR_M_OLD if old else HAIR_M_YOUNG)))]
        facial = "NONE" if rng.random() < (0.85 if nat in ("KR", "JP", "CN") else 0.55) else ["BEARD_LIGHT", "BEARD_MEDIUM", "MOUSTACHE_FANCY"][int(rng.integers(3))]
    else:
        hair = (HAIR_F_OLD if old else HAIR_F_YOUNG)[int(rng.integers(len(HAIR_F_OLD if old else HAIR_F_YOUNG)))]
        facial = "NONE"
    if age >= 75:
        hair_color = "SILVER_GRAY" if rng.random() < 0.8 else "PLATINUM"
    elif age >= 58:
        hair_color = ["SILVER_GRAY", "BROWN_DARK", "BLACK"][int(rng.integers(3))]
    else:
        hair_color = ("BLACK" if rng.random() < 0.7 else "BROWN_DARK") if nat in ("KR", "CN", "JP", "VN", "TH", "PH", "ID", "NP") else ["BROWN", "BLONDE", "BLACK", "AUBURN", "BROWN_DARK"][int(rng.integers(5))]
    skin = SKIN_BY_NAT.get(nat, ["LIGHT"])
    acc = "NONE"
    if rng.random() < (0.45 if old else 0.25):
        acc = ["PRESCRIPTION_1", "PRESCRIPTION_2", "ROUND"][int(rng.integers(3))]
    return {"top": hair, "hair_color": hair_color, "facial_hair": facial,
            "skin_color": skin[int(rng.integers(len(skin)))], "accessory": acc,
            "eyebrows": ["DEFAULT", "DEFAULT_NATURAL", "FLAT_NATURAL", "RAISED_EXCITED_NATURAL", "SAD_CONCERNED_NATURAL"][int(rng.integers(5))],
            "eyes": ["DEFAULT", "DEFAULT", "HAPPY", "SIDE", "SQUINT"][int(rng.integers(5))],
            "mouth": ["DEFAULT", "SMILE", "SERIOUS", "TWINKLE", "CONCERNED"][int(rng.integers(5))],
            "clothing": CLOTHES[int(rng.integers(len(CLOTHES)))], "clothing_color": CLOTH_COLORS[int(rng.integers(len(CLOTH_COLORS)))],
            "background": ["#dfe8f5", "#e8f0e2", "#f5e8df", "#f0e2ea", "#e2eef0"][int(rng.integers(5))]}


def render_svg(av: dict | None, name: str = "?") -> str:
    if not av or not HAVE_PA:
        initial = name[:1]
        return (f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 264 280"><circle cx="132" cy="140" r="130" fill="#cbd5e1"/>'
                f'<text x="132" y="165" font-size="96" text-anchor="middle" fill="#334155" font-family="sans-serif">{initial}</text></svg>')
    avatar = pa.Avatar(style=pa.AvatarStyle.CIRCLE, background_color=av["background"],
                       top=getattr(pa.HairType, av["top"]), hair_color=getattr(pa.HairColor, av["hair_color"]),
                       facial_hair=getattr(pa.FacialHairType, av["facial_hair"]), facial_hair_color=getattr(pa.HairColor, av["hair_color"]),
                       skin_color=getattr(pa.SkinColor, av["skin_color"]), accessory=getattr(pa.AccessoryType, av["accessory"]),
                       eyebrows=getattr(pa.EyebrowType, av["eyebrows"]), eyes=getattr(pa.EyeType, av["eyes"]),
                       mouth=getattr(pa.MouthType, av["mouth"]), clothing=getattr(pa.ClothingType, av["clothing"]),
                       clothing_color=getattr(pa.ClothingColor, av["clothing_color"]))
    return avatar.render()
