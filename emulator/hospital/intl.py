"""병원 국가별 환자 명단 — 미국 · 일본 (한국은 profiles.py 의 기본 경로).

general.patient_country 가 US 또는 JP 이면 프로필 풀 전체(한국과 같은 수)가 그 나라 사람으로 만들어진다:
현지식 이름(일본은 한자 + 가나), 병원 인근 도시권에 몰린 거주지(동네 수준까지만 — 번지는 만들지 않는다, 한국 정책과 같음),
현지 전화번호(미국은 방송·시험용 555-01xx 대역), 현지 체격·혈액형 분포, 이민자·외국인 주민 비율(general.korean_ratio 를 '자국민 비율'로 쓴다).
address 는 한국과 같은 키(sido/sigungu/dong/label)에 country · postal · lat · lon 이 붙는다.
"""
from __future__ import annotations

import numpy as np

M, F = "M", "F"

# ------------------------------------------------------------------ 미국
US_GIVEN = {
    M: {1930: ["Robert", "James", "John", "William", "Richard", "Charles", "Donald", "George", "Joseph", "Edward", "Frank", "Harold", "Walter", "Raymond", "Eugene"],
        1950: ["James", "Robert", "John", "Michael", "David", "William", "Richard", "Thomas", "Gary", "Larry", "Ronald", "Steven", "Dennis", "Jerry", "Kenneth"],
        1970: ["Michael", "Christopher", "Jason", "David", "James", "John", "Robert", "Brian", "Matthew", "Kevin", "Jeffrey", "Scott", "Eric", "Mark", "Anthony"],
        1990: ["Michael", "Christopher", "Matthew", "Joshua", "Andrew", "Daniel", "Tyler", "Brandon", "Ryan", "Justin", "Jacob", "Kyle", "Nicholas", "Austin", "Zachary"]},
    F: {1930: ["Mary", "Betty", "Dorothy", "Helen", "Margaret", "Ruth", "Shirley", "Barbara", "Patricia", "Joan", "Virginia", "Doris", "Frances", "Evelyn", "Mildred"],
        1950: ["Mary", "Linda", "Patricia", "Susan", "Deborah", "Barbara", "Debra", "Karen", "Nancy", "Donna", "Cynthia", "Sandra", "Pamela", "Sharon", "Kathleen"],
        1970: ["Jennifer", "Amy", "Melissa", "Michelle", "Kimberly", "Lisa", "Angela", "Heather", "Stephanie", "Nicole", "Jessica", "Elizabeth", "Rebecca", "Kelly", "Julie"],
        1990: ["Jessica", "Ashley", "Emily", "Sarah", "Samantha", "Amanda", "Brittany", "Elizabeth", "Taylor", "Megan", "Hannah", "Lauren", "Rachel", "Kayla", "Alexis"]},
}
US_FAMILY = ["Smith", "Johnson", "Williams", "Brown", "Jones", "Miller", "Davis", "Wilson", "Anderson", "Thomas", "Taylor", "Moore", "Jackson", "Martin", "Thompson", "White",
             "Harris", "Clark", "Lewis", "Robinson", "Walker", "Young", "Allen", "King", "Wright", "Scott", "Hill", "Green", "Adams", "Baker", "Nelson", "Carter", "Mitchell",
             "Roberts", "Turner", "Phillips", "Campbell", "Parker", "Evans", "Edwards", "Collins", "Stewart", "Morris", "Murphy", "Cook", "Rogers", "Morgan", "Cooper",
             "Peterson", "Reed", "Bailey", "Bell", "Kelly", "Howard", "Ward", "Cox", "Richardson", "Wood", "Watson", "Brooks", "Bennett", "Gray", "James", "Hughes",
             "O'Brien", "Sullivan", "Washington", "Jefferson", "Coleman", "Jenkins", "Kowalski", "Nowak", "Schmidt", "Mueller", "Olson", "Larson", "Hansen"]
US_BLACK_GIVEN = {M: ["Jamal", "Darnell", "Marcus", "Terrence", "Andre", "DeShawn", "Tyrone", "Malik", "Reginald", "Cedric"],
                  F: ["Aaliyah", "Keisha", "Latoya", "Tamika", "Imani", "Ebony", "Jasmine", "Monique", "Shanice", "Denise"]}
# 이민자·이민 2세 (출신국 코드, 가중치, 이름, 성)
US_IMMIGRANT = [
    ("MX", 0.40, {M: ["José", "Juan", "Luis", "Carlos", "Jesús", "Miguel", "Francisco", "Alejandro"], F: ["María", "Guadalupe", "Rosa", "Juana", "Verónica", "Ana", "Lucía", "Carmen"]},
     ["García", "Hernández", "Martínez", "López", "González", "Rodríguez", "Pérez", "Sánchez", "Ramírez", "Torres", "Flores", "Rivera"]),
    ("CN", 0.12, {M: ["Wei", "Jun", "Hao", "Ming", "Jian", "Lei"], F: ["Mei", "Li", "Xiu", "Yan", "Jing", "Hui"]}, ["Wang", "Li", "Zhang", "Chen", "Liu", "Huang", "Zhou", "Wu"]),
    ("IN", 0.12, {M: ["Arjun", "Rahul", "Vikram", "Suresh", "Anil", "Rajesh"], F: ["Priya", "Anjali", "Sunita", "Deepa", "Kavya", "Meera"]}, ["Patel", "Shah", "Singh", "Kumar", "Reddy", "Rao", "Gupta"]),
    ("PH", 0.10, {M: ["Jose", "Mark", "John Paul", "Rommel", "Rodel", "Jun"], F: ["Maria", "Ana", "Grace", "Joy", "Rowena", "Marites"]}, ["Santos", "Reyes", "Cruz", "Bautista", "Garcia", "Mendoza", "Dela Cruz"]),
    ("VN", 0.08, {M: ["Minh", "Tuan", "Hung", "Duc", "Nam"], F: ["Linh", "Mai", "Huong", "Thu", "Lan"]}, ["Nguyen", "Tran", "Le", "Pham", "Hoang"]),
    ("KR", 0.08, {M: ["Min-jun", "Sung-ho", "Jae-won", "Dong-hyun", "Young-soo"], F: ["Ji-young", "Eun-ji", "Soo-jin", "Hye-won", "Mi-kyung"]}, ["Kim", "Lee", "Park", "Choi", "Jung", "Kang"]),
    ("CU", 0.05, {M: ["Yosvany", "Raúl", "Orlando", "Rolando"], F: ["Yamilé", "Marisol", "Odalys", "Niurka"]}, ["Pérez", "Díaz", "Fernández", "Álvarez", "Morales"]),
    ("NG", 0.05, {M: ["Chinedu", "Emeka", "Oluwaseun", "Tunde"], F: ["Ngozi", "Chioma", "Funmilayo", "Adaeze"]}, ["Okafor", "Adeyemi", "Nwosu", "Okonkwo", "Balogun"]),
]
# 병원 인근 대도시권(시카고)에 몰리고 일부는 다른 주: (주, 도시, 동네, ZIP, lat, lon, 가중치, 시골)
US_PLACES = [
    ("IL", "Chicago", "Lincoln Park", "60614", 41.921, -87.648, 6, False), ("IL", "Chicago", "Lakeview", "60657", 41.940, -87.654, 6, False),
    ("IL", "Chicago", "Hyde Park", "60615", 41.799, -87.590, 5, False), ("IL", "Chicago", "Pilsen", "60608", 41.857, -87.660, 5, False),
    ("IL", "Chicago", "Logan Square", "60647", 41.923, -87.708, 5, False), ("IL", "Chicago", "Uptown", "60640", 41.966, -87.656, 4, False),
    ("IL", "Chicago", "Bronzeville", "60653", 41.817, -87.616, 4, False), ("IL", "Chicago", "Chinatown", "60616", 41.852, -87.632, 3, False),
    ("IL", "Chicago", "Albany Park", "60625", 41.968, -87.720, 3, False), ("IL", "Chicago", "Austin", "60644", 41.887, -87.765, 3, False),
    ("IL", "Evanston", "Downtown Evanston", "60201", 42.047, -87.684, 3, False), ("IL", "Oak Park", "Oak Park", "60302", 41.885, -87.785, 3, False),
    ("IL", "Naperville", "Downtown Naperville", "60540", 41.773, -88.148, 3, False), ("IL", "Schaumburg", "Schaumburg", "60173", 42.034, -88.083, 3, False),
    ("IL", "Skokie", "Skokie", "60076", 42.033, -87.733, 2, False), ("IL", "Cicero", "Cicero", "60804", 41.845, -87.754, 2, False),
    ("IL", "Aurora", "Aurora", "60505", 41.760, -88.300, 2, False), ("IL", "Joliet", "Joliet", "60432", 41.525, -88.082, 2, False),
    ("IL", "Waukegan", "Waukegan", "60085", 42.363, -87.845, 1, False), ("IL", "Kankakee", "Kankakee", "60901", 41.120, -87.861, 1, True),
    ("IL", "Peoria", "Peoria", "61602", 40.694, -89.589, 1, True), ("IL", "Champaign", "Champaign", "61820", 40.116, -88.243, 1, False),
    ("IN", "Gary", "Gary", "46402", 41.593, -87.346, 1, False), ("IN", "Hammond", "Hammond", "46320", 41.583, -87.500, 1, False),
    ("WI", "Kenosha", "Kenosha", "53140", 42.585, -87.821, 1, False), ("WI", "Milwaukee", "Bay View", "53207", 42.980, -87.896, 1, False),
    ("MI", "Kalamazoo", "Kalamazoo", "49007", 42.292, -85.587, 0.5, True), ("IA", "Davenport", "Davenport", "52801", 41.524, -90.578, 0.5, True),
]
US_AREA = {"Chicago": ["312", "773", "872"], "Evanston": ["847", "224"], "Skokie": ["847", "224"], "Schaumburg": ["847", "224"], "Waukegan": ["847", "224"],
           "Oak Park": ["708"], "Cicero": ["708"], "Naperville": ["630", "331"], "Aurora": ["630", "331"], "Joliet": ["815", "779"], "Kankakee": ["815"],
           "Peoria": ["309"], "Champaign": ["217"], "Gary": ["219"], "Hammond": ["219"], "Kenosha": ["262"], "Milwaukee": ["414"], "Kalamazoo": ["269"], "Davenport": ["563"]}
US_STATE_NAME = {"IL": "Illinois", "IN": "Indiana", "WI": "Wisconsin", "MI": "Michigan", "IA": "Iowa"}

# ------------------------------------------------------------------ 일본
# (한자, 가나)
JP_FAMILY = [("佐藤", "サトウ"), ("鈴木", "スズキ"), ("高橋", "タカハシ"), ("田中", "タナカ"), ("伊藤", "イトウ"), ("渡辺", "ワタナベ"), ("山本", "ヤマモト"), ("中村", "ナカムラ"),
             ("小林", "コバヤシ"), ("加藤", "カトウ"), ("吉田", "ヨシダ"), ("山田", "ヤマダ"), ("佐々木", "ササキ"), ("山口", "ヤマグチ"), ("松本", "マツモト"), ("井上", "イノウエ"),
             ("木村", "キムラ"), ("林", "ハヤシ"), ("斎藤", "サイトウ"), ("清水", "シミズ"), ("山崎", "ヤマザキ"), ("森", "モリ"), ("池田", "イケダ"), ("橋本", "ハシモト"),
             ("阿部", "アベ"), ("石川", "イシカワ"), ("山下", "ヤマシタ"), ("中島", "ナカジマ"), ("石井", "イシイ"), ("小川", "オガワ"), ("前田", "マエダ"), ("岡田", "オカダ"),
             ("長谷川", "ハセガワ"), ("藤田", "フジタ"), ("後藤", "ゴトウ"), ("近藤", "コンドウ"), ("村上", "ムラカミ"), ("遠藤", "エンドウ"), ("青木", "アオキ"), ("坂本", "サカモト")]
JP_GIVEN = {
    M: {1930: [("茂", "シゲル"), ("清", "キヨシ"), ("勇", "イサム"), ("博", "ヒロシ"), ("実", "ミノル"), ("進", "ススム"), ("正", "タダシ"), ("武", "タケシ"), ("三郎", "サブロウ"), ("正雄", "マサオ")],
        1950: [("誠", "マコト"), ("浩", "ヒロシ"), ("隆", "タカシ"), ("修", "オサム"), ("茂", "シゲル"), ("博之", "ヒロユキ"), ("和夫", "カズオ"), ("明", "アキラ"), ("健一", "ケンイチ"), ("秀樹", "ヒデキ")],
        1970: [("誠", "マコト"), ("大輔", "ダイスケ"), ("健太", "ケンタ"), ("直樹", "ナオキ"), ("剛", "ツヨシ"), ("哲也", "テツヤ"), ("学", "マナブ"), ("達也", "タツヤ"), ("拓也", "タクヤ"), ("智之", "トモユキ")],
        1990: [("翔太", "ショウタ"), ("拓海", "タクミ"), ("大樹", "ダイキ"), ("健太", "ケンタ"), ("翔", "ショウ"), ("蓮", "レン"), ("悠斗", "ユウト"), ("颯太", "ソウタ"), ("大翔", "ヒロト"), ("陸", "リク")]},
    F: {1930: [("和子", "カズコ"), ("幸子", "サチコ"), ("節子", "セツコ"), ("久子", "ヒサコ"), ("千代", "チヨ"), ("静子", "シズコ"), ("文子", "フミコ"), ("美代子", "ミヨコ"), ("トシ", "トシ"), ("キミ", "キミ")],
        1950: [("恵子", "ケイコ"), ("洋子", "ヨウコ"), ("幸子", "サチコ"), ("京子", "キョウコ"), ("由美子", "ユミコ"), ("久美子", "クミコ"), ("明美", "アケミ"), ("順子", "ジュンコ"), ("智子", "トモコ"), ("悦子", "エツコ")],
        1970: [("陽子", "ヨウコ"), ("裕子", "ユウコ"), ("真由美", "マユミ"), ("由美", "ユミ"), ("智子", "トモコ"), ("恵", "メグミ"), ("直美", "ナオミ"), ("香織", "カオリ"), ("麻衣", "マイ"), ("美穂", "ミホ")],
        1990: [("美咲", "ミサキ"), ("彩", "アヤ"), ("陽菜", "ヒナ"), ("さくら", "サクラ"), ("葵", "アオイ"), ("結衣", "ユイ"), ("愛", "アイ"), ("七海", "ナナミ"), ("優花", "ユウカ"), ("凛", "リン")]},
}
# 외국인 주민 (출신국, 가중치, 이름) — 한자권은 한자, 나머지는 가타카나 표기
JP_FOREIGN = [
    ("CN", 0.32, {M: [("王 偉", "ワン ウェイ"), ("李 強", "リー チャン"), ("張 磊", "チャン レイ"), ("劉 洋", "リウ ヤン")], F: [("王 芳", "ワン ファン"), ("李 娜", "リー ナー"), ("陳 静", "チェン ジン"), ("劉 敏", "リウ ミン")]}),
    ("KR", 0.20, {M: [("金 民俊", "キム ミンジュン"), ("李 成浩", "イ ソンホ"), ("朴 載元", "パク ジェウォン")], F: [("金 智英", "キム ジヨン"), ("李 恩智", "イ ウンジ"), ("朴 秀珍", "パク スジン")]}),
    ("VN", 0.18, {M: [("グエン・ヴァン・ミン", "グエン ヴァン ミン"), ("チャン・ドゥック", "チャン ドゥック")], F: [("グエン・ティ・ラン", "グエン ティ ラン"), ("レ・ティ・マイ", "レ ティ マイ")]}),
    ("PH", 0.12, {M: [("サントス・ホセ", "サントス ホセ"), ("レイエス・マーク", "レイエス マーク")], F: [("サントス・マリア", "サントス マリア"), ("クルス・グレース", "クルス グレース")]}),
    ("BR", 0.10, {M: [("シルバ・ジョアン", "シルバ ジョアン"), ("サントス・ルーカス", "サントス ルーカス")], F: [("オリヴェイラ・アナ", "オリヴェイラ アナ"), ("ソウザ・ジュリアナ", "ソウザ ジュリアナ")]}),
    ("NP", 0.08, {M: [("シュレスタ・ラメシュ", "シュレスタ ラメシュ"), ("グルン・ビカシュ", "グルン ビカシュ")], F: [("タパ・シタ", "タパ シタ"), ("ライ・マヤ", "ライ マヤ")]}),
]
# 병원 인근(도쿄권) 중심: (都道府県, 市区町村, 町域, 〒, lat, lon, 가중치, 시골)
JP_PLACES = [
    ("東京都", "文京区", "本郷", "113-0033", 35.708, 139.760, 5, False), ("東京都", "新宿区", "西新宿", "160-0023", 35.690, 139.692, 5, False),
    ("東京都", "世田谷区", "三軒茶屋", "154-0024", 35.643, 139.669, 5, False), ("東京都", "江東区", "豊洲", "135-0061", 35.655, 139.796, 4, False),
    ("東京都", "練馬区", "光が丘", "179-0072", 35.759, 139.628, 4, False), ("東京都", "足立区", "千住", "120-0034", 35.749, 139.804, 4, False),
    ("東京都", "板橋区", "高島平", "175-0082", 35.789, 139.660, 3, False), ("東京都", "大田区", "蒲田", "144-0052", 35.562, 139.716, 4, False),
    ("東京都", "杉並区", "阿佐谷南", "166-0004", 35.703, 139.636, 3, False), ("東京都", "品川区", "大井", "140-0014", 35.606, 139.733, 3, False),
    ("東京都", "江戸川区", "西葛西", "134-0088", 35.664, 139.859, 3, False), ("東京都", "八王子市", "旭町", "192-0083", 35.656, 139.339, 2, False),
    ("東京都", "町田市", "原町田", "194-0013", 35.543, 139.446, 2, False), ("神奈川県", "横浜市中区", "山下町", "231-0023", 35.444, 139.648, 3, False),
    ("神奈川県", "川崎市中原区", "小杉町", "211-0063", 35.576, 139.659, 3, False), ("神奈川県", "相模原市南区", "相模大野", "252-0303", 35.532, 139.438, 2, False),
    ("埼玉県", "さいたま市浦和区", "高砂", "330-0063", 35.859, 139.656, 3, False), ("埼玉県", "川口市", "栄町", "332-0017", 35.807, 139.724, 2, False),
    ("埼玉県", "所沢市", "東町", "359-1124", 35.788, 139.470, 2, False), ("千葉県", "船橋市", "本町", "273-0005", 35.700, 139.985, 2, False),
    ("千葉県", "千葉市中央区", "富士見", "260-0015", 35.612, 140.114, 2, False), ("千葉県", "柏市", "柏", "277-0005", 35.862, 139.971, 2, False),
    ("茨城県", "つくば市", "吾妻", "305-0031", 36.083, 140.111, 1, False), ("栃木県", "宇都宮市", "馬場通り", "320-0026", 36.559, 139.883, 1, True),
    ("群馬県", "前橋市", "大手町", "371-0026", 36.391, 139.061, 1, True), ("山梨県", "甲府市", "丸の内", "400-0031", 35.667, 138.569, 0.5, True),
    ("静岡県", "静岡市葵区", "呉服町", "420-0031", 34.975, 138.383, 0.5, True), ("長野県", "長野市", "南長野", "380-0836", 36.648, 138.194, 0.5, True),
]


def _decade(tbl: dict, sex: str, birth_year: int):
    ks = sorted(tbl[sex])
    k = max([d for d in ks if d <= birth_year] or [ks[0]])
    return tbl[sex][k]


def _place(rng: np.random.Generator, places: list):
    w = np.array([p[6] for p in places], dtype=float)
    return places[int(rng.choice(len(places), p=w / w.sum()))]


def name_us(rng: np.random.Generator, sex: str, birth_year: int, native: bool) -> tuple[str, str, dict]:
    """(표시 이름 'Given Family', 국적 코드, 추가 필드)."""
    if not native:
        codes = US_IMMIGRANT
        w = np.array([c[1] for c in codes]); c = codes[int(rng.choice(len(codes), p=w / w.sum()))]
        given = c[2][sex][int(rng.integers(len(c[2][sex])))]; fam = c[3][int(rng.integers(len(c[3])))]
        return f"{given} {fam}", c[0], {"name_given": given, "name_family": fam}
    if rng.random() < 0.13:
        pool = US_BLACK_GIVEN[sex]
    else:
        pool = _decade(US_GIVEN, sex, birth_year)
    given = pool[int(rng.integers(len(pool)))]
    fam = US_FAMILY[int(rng.integers(len(US_FAMILY)))]
    mid = "" if rng.random() < 0.55 else f" {chr(65 + int(rng.integers(26)))}."
    return f"{given}{mid} {fam}", "US", {"name_given": given, "name_family": fam}


def name_jp(rng: np.random.Generator, sex: str, birth_year: int, native: bool) -> tuple[str, str, dict]:
    if not native:
        w = np.array([c[1] for c in JP_FOREIGN]); c = JP_FOREIGN[int(rng.choice(len(JP_FOREIGN), p=w / w.sum()))]
        kanji, kana = c[2][sex][int(rng.integers(len(c[2][sex])))]
        return kanji, c[0], {"name_kana": kana}
    fk, fn = JP_FAMILY[int(rng.integers(len(JP_FAMILY)))]
    pool = _decade(JP_GIVEN, sex, birth_year)
    gk, gn = pool[int(rng.integers(len(pool)))]
    return f"{fk} {gk}", "JP", {"name_kana": f"{fn} {gn}", "name_family": fk, "name_given": gk}


def address_us(rng: np.random.Generator) -> tuple[dict, str]:
    st, city, hood, zp, lat, lon, _, rural = _place(rng, US_PLACES)
    lat, lon = round(lat + float(rng.uniform(-0.012, 0.012)), 4), round(lon + float(rng.uniform(-0.012, 0.012)), 4)
    codes = US_AREA.get(city, ["312"]); area = codes[int(rng.integers(len(codes)))]
    phone = f"({area}) 555-01{int(rng.integers(0, 100)):02d}"                    # 555-01xx: 방송·시험용 예약 번호
    return {"sido": st, "sigungu": city, "dong": hood, "label": f"{hood}, {city}, {st} {zp}", "country": "US", "state_name": US_STATE_NAME[st],
            "postal": zp, "lat": lat, "lon": lon, "rural": rural}, phone


def address_jp(rng: np.random.Generator) -> tuple[dict, str]:
    pref, city, town, zp, lat, lon, _, rural = _place(rng, JP_PLACES)
    lat, lon = round(lat + float(rng.uniform(-0.008, 0.008)), 4), round(lon + float(rng.uniform(-0.008, 0.008)), 4)
    chome = int(rng.integers(1, 6))                                             # 丁目 까지만 (번지 없음)
    phone = f"0{['90', '80', '70'][int(rng.integers(3))]}-{int(rng.integers(1000, 9999))}-{int(rng.integers(1000, 9999))}"
    return {"sido": pref, "sigungu": city, "dong": f"{town}{chome}丁目", "label": f"〒{zp} {pref}{city}{town}{chome}丁目", "country": "JP",
            "postal": zp, "lat": lat, "lon": lon, "rural": rural}, phone


# 나라별 체격·혈액형 (키 평균 남/여 cm, BMI 평균·표준편차, 혈액형 분포)
BODY = {"US": {"h": (175.5, 161.5), "bmi": (28.5, 5.5)}, "JP": {"h": (170.5, 157.5), "bmi": (22.8, 3.3)}}
BLOOD = {"US": (["O+", "A+", "B+", "AB+", "O-", "A-", "B-", "AB-"], [0.374, 0.357, 0.085, 0.034, 0.066, 0.063, 0.015, 0.006]),
         "JP": (["A+", "O+", "B+", "AB+", "A-", "O-", "B-", "AB-"], [0.399, 0.299, 0.199, 0.097, 0.002, 0.002, 0.001, 0.001])}
MRN = {"US": lambda seed, i: f"{(seed % 89) * 100000 + 30000000 + i:08d}", "JP": lambda seed, i: f"{(seed % 83) * 100000 + 50000000 + i:010d}"}
NAMERS = {"US": name_us, "JP": name_jp}
ADDRESSERS = {"US": address_us, "JP": address_jp}
COUNTRIES = ("KR", "US", "JP")
COUNTRY_LABEL = {"KR": "대한민국", "US": "미국", "JP": "일본"}


# ------------------------------------------------------------------ 국가별 신원 세트 (임상 코어는 공통, 신원만 나라별)
IDENT_FIELDS = ("name", "nationality", "nationality_label", "address", "phone", "mrn", "name_kana", "name_family", "name_given", "avatar", "home_country", "overseas")
OVERSEAS_KEEP = 0.3                    # 미국·일본 병원: 해외 체류 풀 중 이만큼만 해외 체류로 남고 나머지는 그 나라 거주 MCOT 환자가 된다


# 의료진 직함 (한국어 원문은 title_code 로 남긴다 — 라우터가 코드처럼 쓸 수 있게)
STAFF_TITLE = {
    "US": {"교수": "Attending Physician (Professor)", "임상조교수": "Clinical Assistant Professor", "전임의": "Fellow", "전공의 4년차": "Resident (PGY-4)",
           "전공의 3년차": "Resident (PGY-3)", "전공의 2년차": "Resident (PGY-2)", "전공의 1년차": "Intern (PGY-1)", "수간호사": "Nurse Manager", "간호사": "Registered Nurse (RN)"},
    "JP": {"교수": "教授", "임상조교수": "臨床助教", "전임의": "フェロー", "전공의 4년차": "専攻医 4年目", "전공의 3년차": "専攻医 3年目", "전공의 2년차": "専攻医 2年目",
           "전공의 1년차": "初期研修医", "수간호사": "看護師長", "간호사": "看護師"},
}
ROLE_LABEL = {"US": {"doctor": "Physician", "nurse": "Nurse"}, "JP": {"doctor": "医師", "nurse": "看護師"}, "KR": {"doctor": "의사", "nurse": "간호사"}}


def staff_identity(country: str, seed: int, staff_id: str, sex: str) -> dict:
    """의료진(의사·간호사) 이름을 그 나라식으로 — 결정적(병원 시드·직원 번호).  출생연도는 1965~1999 에서."""
    code = {"US": 0x5553, "JP": 0x4A50}[country]
    rng = np.random.default_rng([seed, code, 0x57AF, int(staff_id[1:]) if staff_id[1:].isdigit() else 0])
    by = int(rng.integers(1965, 2000))
    native = rng.random() < 0.85
    name, nat, extra = NAMERS[country](rng, sex, by, native)
    return {"name": name, "name_kana": extra.get("name_kana"), "nationality": nat}


def make_identities(profiles: list[dict], country: str, seed: int, native_ratio: float = 0.9) -> dict:
    """프로필마다 country 나라의 신원(이름·주소·전화·MRN·국적·아바타)을 만든다.  이름은 그 환자의 성별·출생연도에 맞춘다.
    해외 체류(MCOT 전용) 프로필: 병원 나라 사람이거나 OVERSEAS_KEEP 밖이면 그 나라 거주 환자(overseas False)로 바꾸고, 나머지만 외국 거주자로
    남긴다(신원 그대로) — 미국 병원 MCOT 는 미국 거주자가 대부분이어야 한다.  결정적: 같은 seed·프로필이면 같은 결과."""
    from .names import NATION_LABEL
    from .avatars import assign as assign_avatar
    out: dict[int, dict] = {}
    code = {"US": 0x5553, "JP": 0x4A50}[country]
    for p in profiles:
        rng = np.random.default_rng([seed, code, p["id"]])
        if p.get("overseas") and p.get("nationality") != country and rng.random() < OVERSEAS_KEEP:
            continue                                                      # 외국 거주자로 남김 (기본 신원 그대로)
        birth_year = int(p["birth_date"][:4])
        native = rng.random() < native_ratio
        name, nat, extra = NAMERS[country](rng, p["sex"], birth_year, native)
        address, phone = ADDRESSERS[country](rng)
        ident = {"name": name, "nationality": nat, "nationality_label": NATION_LABEL.get(nat, nat), "address": address, "phone": phone,
                 "mrn": MRN[country](seed, p["id"] - 1), "home_country": country, "name_kana": None, "name_family": None, "name_given": None, "overseas": False, **extra}
        ident["avatar"] = assign_avatar(np.random.default_rng([seed, code, p["id"], 7]), {**p, "nationality": nat})
        out[p["id"]] = ident
    return out
