"""나라별 합성 인물: 이름(한자·가나·아랍 문자·네덜란드 접두사 포함), 성별, 생년월일, 주소, 전화.

전화번호는 가능한 한 각국의 방송·시험용 대역을 쓴다(미국·캐나다 555-01xx, 영국 07700 900xxx, 호주 0491 570 xxx).
"""
from __future__ import annotations

import datetime as dt
import random

M, F = "M", "F"

# ------------------------------------------------------------------ 이름 풀
US_GIVEN = {M: ["James", "Robert", "Michael", "William", "David", "Richard", "Joseph", "Thomas", "Charles", "Daniel", "Anthony", "Mark", "Carlos", "Luis", "Jamal", "Kevin", "Brian", "Wei", "Arjun", "Ethan"],
            F: ["Mary", "Patricia", "Jennifer", "Linda", "Elizabeth", "Barbara", "Susan", "Jessica", "Karen", "Nancy", "Maria", "Rosa", "Aaliyah", "Ashley", "Emily", "Grace", "Mei", "Priya", "Olivia", "Donna"]}
US_FAMILY = ["Smith", "Johnson", "Williams", "Brown", "Jones", "Garcia", "Miller", "Davis", "Rodriguez", "Martinez", "Hernandez", "Lopez", "Wilson", "Anderson", "Thomas",
             "Taylor", "Moore", "Jackson", "Martin", "Lee", "Thompson", "White", "Harris", "Clark", "Lewis", "Nguyen", "Patel", "Kim", "O'Brien", "Washington"]
US_RACE = [("2106-3", "White", 0.6), ("2054-5", "Black or African American", 0.14), ("2028-9", "Asian", 0.07), ("1002-5", "American Indian or Alaska Native", 0.01),
           ("2076-8", "Native Hawaiian or Other Pacific Islander", 0.005), ("2131-1", "Other Race", 0.175)]
UK_GIVEN = {M: ["Oliver", "George", "Harry", "Jack", "Thomas", "James", "William", "David", "John", "Peter", "Mohammed", "Ravi", "Callum", "Owen", "Rhys", "Graham"],
            F: ["Olivia", "Amelia", "Isla", "Emily", "Margaret", "Susan", "Elizabeth", "Joan", "Patricia", "Aisha", "Priya", "Siobhan", "Fiona", "Bethan", "Jean", "Hannah"]}
UK_FAMILY = ["Smith", "Jones", "Taylor", "Brown", "Williams", "Wilson", "Johnson", "Davies", "Robinson", "Wright", "Thompson", "Evans", "Walker", "White", "Roberts", "Green",
             "Hall", "Wood", "Jackson", "Clarke", "Khan", "Patel", "Ahmed", "Murphy", "MacDonald", "O'Neill", "Hughes", "Edwards"]
UK_ETHNIC = [("A", "White - British", 0.75), ("B", "White - Irish", 0.02), ("C", "White - Any other White background", 0.05), ("H", "Asian or Asian British - Indian", 0.04),
             ("J", "Asian or Asian British - Pakistani", 0.04), ("N", "Black or Black British - African", 0.03), ("M", "Black or Black British - Caribbean", 0.02),
             ("R", "Other Ethnic Groups - Chinese", 0.01), ("Z", "Not stated", 0.04)]
# (한자, 가나)
JP_FAMILY = [("佐藤", "サトウ"), ("鈴木", "スズキ"), ("高橋", "タカハシ"), ("田中", "タナカ"), ("伊藤", "イトウ"), ("渡辺", "ワタナベ"), ("山本", "ヤマモト"), ("中村", "ナカムラ"),
             ("小林", "コバヤシ"), ("加藤", "カトウ"), ("吉田", "ヨシダ"), ("山田", "ヤマダ"), ("佐々木", "ササキ"), ("山口", "ヤマグチ"), ("松本", "マツモト"), ("井上", "イノウエ"),
             ("木村", "キムラ"), ("林", "ハヤシ"), ("斎藤", "サイトウ"), ("清水", "シミズ"), ("齋藤", "サイトウ"), ("髙橋", "タカハシ")]
JP_GIVEN = {M: [("太郎", "タロウ"), ("一郎", "イチロウ"), ("健一", "ケンイチ"), ("誠", "マコト"), ("浩", "ヒロシ"), ("茂", "シゲル"), ("翔太", "ショウタ"), ("大輔", "ダイスケ"),
                ("隆", "タカシ"), ("勇", "イサム"), ("修", "オサム"), ("博之", "ヒロユキ"), ("清", "キヨシ"), ("悠斗", "ユウト")],
            F: [("花子", "ハナコ"), ("幸子", "サチコ"), ("恵子", "ケイコ"), ("洋子", "ヨウコ"), ("美咲", "ミサキ"), ("由美", "ユミ"), ("和子", "カズコ"), ("陽菜", "ヒナ"),
                ("久美子", "クミコ"), ("明美", "アケミ"), ("千代", "チヨ"), ("智子", "トモコ"), ("節子", "セツコ"), ("さくら", "サクラ")]}
KR_FAMILY = ["김", "이", "박", "최", "정", "강", "조", "윤", "장", "임", "한", "오", "서", "신", "권", "황", "안", "송", "류", "홍", "전", "고", "문", "양", "손", "배", "남궁", "선우"]
KR_GIVEN = {M: ["민준", "서준", "도윤", "영수", "영호", "정훈", "성민", "상철", "동현", "재현", "준호", "광수", "병철", "현우", "지훈", "태식", "종민", "승우"],
            F: ["서연", "지우", "영희", "순자", "미영", "은정", "정숙", "혜진", "수빈", "지현", "옥순", "미경", "현정", "유진", "경희", "말순", "하은", "선영"]}
DE_GIVEN = {M: ["Hans", "Peter", "Klaus", "Jürgen", "Wolfgang", "Michael", "Thomas", "Stefan", "Andreas", "Lukas", "Mehmet", "Dieter", "Uwe", "Jonas", "Günter"],
            F: ["Ursula", "Monika", "Petra", "Sabine", "Renate", "Karin", "Anna", "Julia", "Birgit", "Gisela", "Ayşe", "Ingrid", "Katrin", "Lena", "Brigitte"]}
DE_FAMILY = ["Müller", "Schmidt", "Schneider", "Fischer", "Weber", "Meyer", "Wagner", "Becker", "Schulz", "Hoffmann", "Schäfer", "Koch", "Bauer", "Richter", "Klein",
             "Wolf", "Schröder", "Neumann", "Schwarz", "Zimmermann", "Yılmaz", "Krüger", "Hartmann", "Lange", "Weiß"]
FR_GIVEN = {M: ["Jean", "Pierre", "Michel", "André", "Philippe", "Alain", "Bernard", "Jacques", "François", "Nicolas", "Karim", "Thierry", "Hugo", "Gérard", "Mathéo"],
            F: ["Marie", "Monique", "Françoise", "Isabelle", "Catherine", "Nathalie", "Sylvie", "Christine", "Hélène", "Élodie", "Fatima", "Chantal", "Camille", "Jeanne", "Léa"]}
FR_FAMILY = ["Martin", "Bernard", "Thomas", "Petit", "Robert", "Richard", "Durand", "Dubois", "Moreau", "Laurent", "Simon", "Michel", "Lefèvre", "Leroy", "Roux",
             "David", "Bertrand", "Morel", "Fournier", "Girard", "Garnier", "Faure", "Mercier", "Benali", "Chevalier"]
NL_GIVEN = {M: ["Jan", "Pieter", "Hendrik", "Willem", "Cornelis", "Johannes", "Gerrit", "Daan", "Sem", "Bram", "Mohamed", "Joost", "Kees", "Ruud"],
            F: ["Maria", "Johanna", "Anna", "Cornelia", "Wilhelmina", "Emma", "Sanne", "Femke", "Lotte", "Ingrid", "Fatma", "Anouk", "Grietje", "Marieke"]}
# (접두사, 성) — 접두사는 humanname-own-prefix 로 따로 나간다
NL_FAMILY = [("de", "Jong"), ("", "Jansen"), ("de", "Vries"), ("van den", "Berg"), ("van", "Dijk"), ("", "Bakker"), ("", "Janssen"), ("", "Visser"), ("", "Smit"),
             ("", "Meijer"), ("de", "Boer"), ("", "Mulder"), ("de", "Groot"), ("", "Bos"), ("", "Vos"), ("", "Peters"), ("", "Hendriks"), ("van", "Leeuwen"),
             ("", "Dekker"), ("van der", "Meer"), ("", "Brouwer"), ("de", "Wit"), ("van der", "Linden")]
AU_GIVEN = {M: ["Jack", "Oliver", "William", "Noah", "Lachlan", "Cooper", "Bruce", "Graham", "Wayne", "Darren", "Minh", "Nikos", "Harrison", "Kieran"],
            F: ["Charlotte", "Olivia", "Mia", "Chloe", "Sharon", "Kylie", "Margaret", "Dianne", "Linh", "Sienna", "Jacinta", "Tahlia", "Beverley", "Georgia"]}
AU_FAMILY = ["Smith", "Jones", "Williams", "Brown", "Wilson", "Taylor", "Johnson", "White", "Martin", "Anderson", "Thompson", "Nguyen", "Thomas", "Walker", "Harris",
             "Lee", "Ryan", "Robinson", "Kelly", "King", "Papadopoulos", "Tran", "Mitchell", "O'Connor"]
AU_INDIGENOUS = [("4", "Neither Aboriginal nor Torres Strait Islander origin", 0.93), ("1", "Aboriginal but not Torres Strait Islander origin", 0.04),
                 ("2", "Torres Strait Islander but not Aboriginal origin", 0.005), ("3", "Both Aboriginal and Torres Strait Islander origin", 0.005), ("9", "Not stated/inadequately described", 0.02)]
CA_GIVEN = {M: ["Liam", "Noah", "William", "Benjamin", "Jean-François", "Marc", "Gilles", "Robert", "Harpreet", "Raj", "Daniel", "Ryan", "Pierre-Luc", "Kevin"],
            F: ["Emma", "Olivia", "Charlotte", "Geneviève", "Marie-Claude", "Chantal", "Jennifer", "Susan", "Manpreet", "Linda", "Sophie", "Amélie", "Heather", "Nicole"]}
CA_FAMILY = ["Smith", "Brown", "Tremblay", "Martin", "Roy", "Wilson", "MacDonald", "Gagnon", "Johnson", "Taylor", "Côté", "Campbell", "Anderson", "Leblanc", "Lee",
             "Singh", "Gill", "Bouchard", "Morrison", "Wong", "Pelletier", "Fraser", "Chan"]
# 싱가포르: (성, 이름) 관습이 민족별로 다르다 — 중국계는 성이 앞, 말레이계는 bin/binte, 인도계는 s/o, d/o
SG_CHINESE_FAMILY = ["Tan", "Lim", "Lee", "Ng", "Ong", "Wong", "Goh", "Chua", "Chan", "Koh", "Teo", "Ang", "Yeo", "Tay", "Ho"]
SG_CHINESE_GIVEN = {M: ["Wei Ming", "Kok Leong", "Boon Huat", "Jun Jie", "Chee Keong", "Ah Kow", "Zhi Hao"], F: ["Mei Ling", "Siew Hoon", "Hui Min", "Li Ting", "Ah Lian", "Xin Yi", "Bee Choo"]}
SG_MALAY = {M: ["Muhammad Faizal", "Ahmad", "Hafiz", "Ismail", "Rahman"], F: ["Nur Aisyah", "Siti Nurhaliza", "Farah", "Zainab", "Nurul Huda"]}
SG_MALAY_FATHER = ["Abdullah", "Hassan", "Osman", "Ibrahim", "Salleh", "Yusof"]
SG_INDIAN = {M: ["Rajesh", "Suresh", "Kumar", "Arun", "Vijay"], F: ["Lakshmi", "Priya", "Kavitha", "Deepa", "Anitha"]}
SG_INDIAN_FATHER = ["Subramaniam", "Krishnan", "Pillai", "Nair", "Ramasamy", "Govindasamy"]
BR_GIVEN = {M: ["José", "João", "Antônio", "Francisco", "Carlos", "Paulo", "Pedro", "Lucas", "Luiz", "Marcos", "Gabriel", "Rafael", "Raimundo", "Sebastião"],
            F: ["Maria", "Ana", "Francisca", "Antônia", "Adriana", "Juliana", "Márcia", "Fernanda", "Patrícia", "Aline", "Raimunda", "Conceição", "Luana", "Beatriz"]}
BR_FAMILY = ["Silva", "Santos", "Oliveira", "Souza", "Rodrigues", "Ferreira", "Alves", "Pereira", "Lima", "Gomes", "Costa", "Ribeiro", "Martins", "Carvalho",
             "Almeida", "Lopes", "Soares", "Fernandes", "Vieira", "Barbosa", "Nascimento", "Araújo"]
# (로마자, 아랍 문자)
AE_GIVEN = {M: [("Mohammed", "محمد"), ("Ahmed", "أحمد"), ("Khalid", "خالد"), ("Saeed", "سعيد"), ("Rashid", "راشد"), ("Hamdan", "حمدان"), ("Sultan", "سلطان"),
                ("Omar", "عمر"), ("Abdullah", "عبدالله"), ("Yousef", "يوسف")],
            F: [("Fatima", "فاطمة"), ("Mariam", "مريم"), ("Aisha", "عائشة"), ("Noura", "نورة"), ("Hessa", "حصة"), ("Shamma", "شمة"), ("Latifa", "لطيفة"),
                ("Maitha", "ميثاء"), ("Salama", "سلامة"), ("Amna", "آمنة")]}
AE_FAMILY = [("Al Mansouri", "المنصوري"), ("Al Nuaimi", "النعيمي"), ("Al Dhaheri", "الظاهري"), ("Al Mazrouei", "المزروعي"), ("Al Ketbi", "الكتبي"), ("Al Shamsi", "الشامسي"),
             ("Al Hammadi", "الحمادي"), ("Al Suwaidi", "السويدي"), ("Al Mheiri", "المهيري"), ("Al Qubaisi", "القبيسي")]
# UAE 의료기관 환자는 상당수가 외국 국적 — (국적 ISO3, 이름 풀)
AE_EXPAT = [("IND", ["Rajesh Kumar", "Anil Menon", "Priya Nair", "Suresh Pillai", "Deepa Thomas"]), ("PAK", ["Imran Khan", "Ayesha Siddiqui", "Bilal Ahmed"]),
            ("PHL", ["Maria Santos", "Jose Reyes", "Rowena Cruz"]), ("EGY", ["Mahmoud Hassan", "Mona Adel"]), ("GBR", ["Sarah Thompson", "James Walker"])]

# ------------------------------------------------------------------ 주소 풀 (도시, 행정구역, 우편번호 규칙)
US_CITIES = {"IL": [("Chicago", "606"), ("Evanston", "602"), ("Oak Park", "603"), ("Naperville", "605")], "AZ": [("Phoenix", "850"), ("Tempe", "852"), ("Mesa", "852"), ("Scottsdale", "852")],
             "VT": [("Burlington", "054"), ("Montpelier", "056"), ("Rutland", "057"), ("St. Albans", "054")], "CA": [("San Diego", "921"), ("Chula Vista", "919"), ("La Mesa", "919"), ("Escondido", "920")]}
US_STREETS = ["Main St", "Oak Ave", "Maple Dr", "Cedar Ln", "Elm St", "Washington Blvd", "Park Ave", "Lake Shore Dr", "2nd St", "Pine St", "Sunset Blvd", "Hillcrest Rd"]
UK_TOWNS = [("Leeds", "LS"), ("Bradford", "BD"), ("Wakefield", "WF"), ("Harrogate", "HG"), ("York", "YO"), ("Huddersfield", "HD"), ("Halifax", "HX")]
UK_STREETS = ["High Street", "Church Lane", "Station Road", "Victoria Road", "Mill Lane", "The Green", "Park Road", "Queens Road", "Kings Avenue", "Manor Close"]
JP_ADDR = {"東京都": [("文京区", "本郷", "113"), ("新宿区", "西新宿", "160"), ("世田谷区", "三軒茶屋", "154"), ("江東区", "豊洲", "135"), ("練馬区", "光が丘", "179")],
           "大阪府": [("大阪市北区", "梅田", "530"), ("大阪市中央区", "北浜", "541"), ("豊中市", "新千里東町", "560"), ("吹田市", "江坂町", "564"), ("堺市堺区", "三国ヶ丘御幸通", "590")]}
KR_ADDR = {"서울특별시": [("종로구", "혜화동", "031"), ("마포구", "공덕동", "041"), ("송파구", "잠실동", "055"), ("강서구", "화곡동", "077"), ("노원구", "상계동", "017")],
           "경기도": [("성남시 분당구", "정자동", "135"), ("고양시 일산동구", "백석동", "104"), ("수원시 영통구", "매탄동", "166")],
           "강원특별자치도": [("강릉시", "교동", "254"), ("동해시", "천곡동", "257"), ("삼척시", "남양동", "259")],
           "대전광역시": [("유성구", "봉명동", "341"), ("서구", "둔산동", "352")]}
KR_ROADS = ["대학로", "세종대로", "올림픽로", "화곡로", "동일로", "정자일로", "중앙로", "천곡로", "한밭대로", "경강로"]
DE_CITIES = [("Köln", "50"), ("Bonn", "53"), ("Leverkusen", "51"), ("Bergisch Gladbach", "51"), ("Brühl", "50"), ("Troisdorf", "53")]
DE_STREETS = ["Hauptstraße", "Schulstraße", "Gartenstraße", "Bahnhofstraße", "Kirchweg", "Rheinuferstraße", "Lindenallee", "Am Markt"]
FR_CITIES = [("Toulouse", "310"), ("Blagnac", "317"), ("Colomiers", "317"), ("Muret", "316"), ("Montauban", "820"), ("Albi", "810")]
FR_STREETS = ["rue de la République", "avenue Jean Jaurès", "rue Victor Hugo", "place du Capitole", "chemin des Vignes", "boulevard Carnot", "allée des Tilleuls"]
NL_CITIES = [("Amsterdam", "10"), ("Amstelveen", "11"), ("Haarlem", "20"), ("Zaandam", "15"), ("Hoofddorp", "21"), ("Diemen", "11")]
NL_STREETS = ["Kerkstraat", "Dorpsstraat", "Stationsweg", "Molenweg", "Prinsengracht", "Amstelveenseweg", "Schoolstraat", "Julianalaan"]
AU_SUBURBS = [("Belconnen", "ACT", "2617"), ("Tuggeranong", "ACT", "2900"), ("Gungahlin", "ACT", "2912"), ("Queanbeyan", "NSW", "2620"), ("Woden", "ACT", "2606"), ("Yass", "NSW", "2582")]
AU_STREETS = ["Northbourne Ave", "Ginninderra Dr", "Anketell St", "Crawford St", "Hibberson St", "Comur St", "Athllon Dr"]
CA_CITIES = [("Toronto", "M5V"), ("Toronto", "M4W"), ("Mississauga", "L5B"), ("Brampton", "L6T"), ("Markham", "L3R"), ("Scarborough", "M1P")]
CA_STREETS = ["Queen St W", "Yonge St", "Bloor St E", "King St", "Dundas St", "Eglinton Ave", "Bay St", "Sheppard Ave"]
SG_ESTATES = [("Tampines", "52"), ("Bedok", "46"), ("Ang Mo Kio", "56"), ("Jurong West", "64"), ("Toa Payoh", "31"), ("Marine Parade", "44"), ("Woodlands", "73")]
BR_CITIES = [("São Paulo", "SP", "01"), ("Guarulhos", "SP", "07"), ("Osasco", "SP", "06"), ("Santo André", "SP", "09"), ("Campinas", "SP", "13")]
BR_STREETS = ["Rua Augusta", "Avenida Paulista", "Rua da Consolação", "Rua Vergueiro", "Avenida Brasil", "Rua das Flores", "Rua XV de Novembro"]
AE_AREAS = [("Abu Dhabi", "Al Khalidiyah"), ("Abu Dhabi", "Al Mushrif"), ("Abu Dhabi", "Khalifa City"), ("Al Ain", "Al Jimi"), ("Abu Dhabi", "Mohammed Bin Zayed City")]


def _weighted(r: random.Random, items):
    x, acc = r.random() * sum(i[-1] for i in items), 0.0
    for it in items:
        acc += it[-1]
        if x <= acc:
            return it
    return items[-1]


def make_person(locale: str, r: random.Random, today: dt.date, age_mu: float = 64.0, sex: str | None = None, birth: dt.date | None = None) -> dict:
    """sex/birth 를 주면 그대로 쓴다(에뮬레이터 환자를 연동 EMR 의 현지 인물로 옮길 때)."""
    s0 = M if r.random() < 0.52 else F
    a0 = int(min(96, max(19, r.gauss(age_mu, 15))))
    b0 = today - dt.timedelta(days=int(a0 * 365.25 + r.randint(0, 364)))
    sex = sex or s0
    birth = birth or b0
    age = (today - birth).days // 365 if birth != b0 else a0
    p = {"sex": sex, "birth": birth.isoformat(), "age": age, "prefix": None, "given": [], "family": "", "text": ""}
    phone_tail = f"{r.randint(0, 99):02d}"
    if locale == "us":
        g = r.choice(US_GIVEN[sex]); mi = chr(65 + r.randint(0, 25))
        p.update(family=r.choice(US_FAMILY), given=[g, mi], language=("es" if r.random() < 0.12 else "en"))
        p["race"] = _weighted(r, US_RACE)[:2]
        p["ethnicity"] = ("2135-2", "Hispanic or Latino") if r.random() < 0.18 else ("2186-5", "Not Hispanic or Latino")
    elif locale == "uk":
        p.update(family=r.choice(UK_FAMILY), given=[r.choice(UK_GIVEN[sex])], prefix=("Mr" if sex == M else r.choice(["Mrs", "Ms", "Miss"])), language="en")
        if r.random() < 0.3:
            p["given"].append(r.choice(UK_GIVEN[sex]))
        p["ethnic"] = _weighted(r, UK_ETHNIC)[:2]
    elif locale == "jp":
        (fk, fr_), (gk, gr) = r.choice(JP_FAMILY), r.choice(JP_GIVEN[sex])
        p.update(family=fk, given=[gk], kana_family=fr_, kana_given=gr, text=f"{fk} {gk}", kana_text=f"{fr_} {gr}", language="ja")
    elif locale == "kr":
        fam = r.choice(KR_FAMILY); giv = r.choice(KR_GIVEN[sex])
        p.update(family=fam, given=[giv], text=fam + giv, language="ko")
    elif locale == "de":
        p.update(family=r.choice(DE_FAMILY), given=[r.choice(DE_GIVEN[sex])], language="de")
        if r.random() < 0.08:
            p["title"] = "Dr."
    elif locale == "fr":
        fam = r.choice(FR_FAMILY)
        p.update(family=fam, given=[r.choice(FR_GIVEN[sex])], language="fr")
        if sex == F and r.random() < 0.55:                       # 기혼 여성: 출생 성(nom de naissance) + 사용 성(nom d'usage)
            p["birth_family"], p["family"] = fam, r.choice(FR_FAMILY)
        else:
            p["birth_family"] = fam
    elif locale == "nl":
        pre, fam = r.choice(NL_FAMILY)
        p.update(family_prefix=pre, family_own=fam, family=(f"{pre} {fam}".strip()), given=[r.choice(NL_GIVEN[sex])], language="nl")
        p["initials"] = p["given"][0][0] + "."
    elif locale == "au":
        p.update(family=r.choice(AU_FAMILY), given=[r.choice(AU_GIVEN[sex])], prefix=("Mr" if sex == M else r.choice(["Mrs", "Ms"])), language="en")
        p["indigenous"] = _weighted(r, AU_INDIGENOUS)[:2]
    elif locale == "ca":
        p.update(family=r.choice(CA_FAMILY), given=[r.choice(CA_GIVEN[sex])], language=("fr" if r.random() < 0.15 else "en"))
    elif locale == "sg":
        eth = r.choices(["chinese", "malay", "indian"], weights=(74, 14, 12))[0]
        if eth == "chinese":
            fam, giv = r.choice(SG_CHINESE_FAMILY), r.choice(SG_CHINESE_GIVEN[sex])
            p.update(family=fam, given=[giv], text=f"{fam.upper()} {giv.upper()}")
        elif eth == "malay":
            giv = r.choice(SG_MALAY[sex]); fa = r.choice(SG_MALAY_FATHER)
            p.update(family=fa, given=[giv], text=f"{giv.upper()} {'BIN' if sex == M else 'BINTE'} {fa.upper()}")
        else:
            giv = r.choice(SG_INDIAN[sex]); fa = r.choice(SG_INDIAN_FATHER)
            p.update(family=fa, given=[giv], text=f"{giv.upper()} {'S/O' if sex == M else 'D/O'} {fa.upper()}")
        p["ethnic_sg"], p["language"] = eth, "en"
    elif locale == "br":
        fams = r.sample(BR_FAMILY, 2)
        giv = r.choice(BR_GIVEN[sex]) + ((" " + r.choice(["Aparecida", "Cristina", "de Fátima"]) if sex == F else " " + r.choice(["Carlos", "Henrique", "Eduardo"])) if r.random() < 0.4 else "")
        fam = ("da " if fams[1] == "Silva" else "") + fams[1]
        p.update(family=f"{fams[0]} {fam}", given=giv.split(" "), language="pt-BR")
        p["mother"] = r.choice(BR_GIVEN[F]) + " " + r.choice(BR_FAMILY) + " " + fams[0]
        p["race_br"] = r.choice([("01", "Branca"), ("02", "Preta"), ("03", "Parda"), ("04", "Amarela"), ("05", "Indígena")])
    elif locale == "ae":
        if r.random() < 0.55:
            (gl, ga), (fl, fa) = r.choice(AE_GIVEN[sex]), r.choice(AE_FAMILY)
            father = r.choice(AE_GIVEN[M])
            p.update(family=fl, given=[gl, father[0]], arabic_family=fa, arabic_given=[ga, father[1]], nationality="ARE", language="ar")
        else:
            nat, pool = r.choice(AE_EXPAT)
            gl, fl = r.choice(pool).rsplit(" ", 1)
            p.update(family=fl, given=[gl], nationality=nat, language="en")
    if not p["text"]:
        p["text"] = " ".join(p["given"] + [p["family"]])
    p.setdefault("phone_tail", phone_tail)
    return p


def fill_contact(p: dict, locale: str, r: random.Random, region: str | None = None) -> None:
    """주소와 전화 — 사이트 지역(region)에 맞춘다."""
    if "address" in p and "phone" in p:
        return
    if locale == "uk":
        town, pc = r.choice(UK_TOWNS)
        p["address"] = {"line": [f"{r.randint(1, 180)} {r.choice(UK_STREETS)}"], "city": town, "district": "West Yorkshire", "state": None,
                        "postal": f"{pc}{r.randint(1, 29)} {r.randint(1, 9)}{r.choice('ABDEFGHJLNPQRSTUWXY')}{r.choice('ABDEFGHJLNPQRSTUWXY')}", "country": "GB"}
        p["phone"] = f"07700 900{r.randint(0, 999):03d}"
    elif locale == "us":
        city, zip3 = r.choice(US_CITIES[region or "IL"])
        p["address"] = {"line": [f"{r.randint(100, 9899)} {r.choice(US_STREETS)}"] + ([f"Apt {r.randint(1, 40)}{r.choice('ABCD')}"] if r.random() < 0.25 else []),
                        "city": city, "district": None, "state": region, "postal": f"{zip3}{r.randint(1, 99):02d}", "country": "US"}
        area = {"IL": "312", "AZ": "602", "VT": "802", "CA": "619"}[region or "IL"]
        p["phone"] = f"({area}) 555-01{r.randint(0, 99):02d}"
    elif locale == "jp":
        pref = region or "東京都"
        city, town, zp = r.choice(JP_ADDR[pref])
        p["address"] = {"line": [f"{town}{r.randint(1, 5)}-{r.randint(1, 30)}-{r.randint(1, 20)}"], "city": city, "district": None, "state": pref,
                        "postal": f"{zp}-{r.randint(0, 9999):04d}", "country": "JP"}
        p["phone"] = f"0{r.choice(['80', '90', '70'])}-{r.randint(1000, 9999)}-{r.randint(1000, 9999)}"
    elif locale == "kr":
        sido = region or "서울특별시"
        gu, dong, zp = r.choice(KR_ADDR[sido])
        p["address"] = {"line": [f"{r.choice(KR_ROADS)} {r.randint(1, 300)}"], "city": gu, "district": dong, "state": sido, "postal": f"{zp}{r.randint(0, 99):02d}", "country": "KR"}
        p["phone"] = f"010-{r.randint(2000, 9999)}-{r.randint(1000, 9999)}"
    elif locale == "de":
        city, plz = r.choice(DE_CITIES)
        p["address"] = {"line": [f"{r.choice(DE_STREETS)} {r.randint(1, 120)}"], "city": city, "district": None, "state": "Nordrhein-Westfalen", "postal": f"{plz}{r.randint(100, 999)}", "country": "DE"}
        p["phone"] = f"+49 221 {r.randint(100000, 9999999)}"
    elif locale == "fr":
        city, cp = r.choice(FR_CITIES)
        p["address"] = {"line": [f"{r.randint(1, 90)} {r.choice(FR_STREETS)}"], "city": city, "district": None, "state": None, "postal": f"{cp}{r.randint(0, 9)}0", "country": "FR"}
        p["phone"] = f"06 {r.randint(10, 99)} {r.randint(10, 99)} {r.randint(10, 99)} {r.randint(10, 99)}"
    elif locale == "nl":
        city, pc = r.choice(NL_CITIES)
        p["address"] = {"line": [f"{r.choice(NL_STREETS)} {r.randint(1, 250)}"], "city": city, "district": None, "state": "Noord-Holland",
                        "postal": f"{pc}{r.randint(10, 99)} {r.choice('ABCDEGHJKLMNPRSTVWXZ')}{r.choice('ABCDEGHJKLMNPRSTVWXZ')}", "country": "NL"}
        p["phone"] = f"06-{r.randint(10000000, 99999999)}"
    elif locale == "au":
        sub, st, pc = r.choice(AU_SUBURBS)
        p["address"] = {"line": [f"{r.randint(1, 200)} {r.choice(AU_STREETS)}"], "city": sub, "district": None, "state": st, "postal": pc, "country": "AU"}
        p["phone"] = f"0491 570 {r.randint(0, 999):03d}"
    elif locale == "ca":
        city, fsa = r.choice(CA_CITIES)
        p["address"] = {"line": [f"{r.randint(1, 2400)} {r.choice(CA_STREETS)}"], "city": city, "district": None, "state": "ON",
                        "postal": f"{fsa} {r.randint(1, 9)}{r.choice('ABCEGHJKLMNPRSTVWXYZ')}{r.randint(0, 9)}", "country": "CA"}
        p["phone"] = f"(416) 555-01{r.randint(0, 99):02d}"
    elif locale == "sg":
        est, pc = r.choice(SG_ESTATES)
        blk = r.randint(100, 899)
        p["address"] = {"line": [f"Blk {blk} {est} Street {r.randint(11, 91)}", f"#{r.randint(2, 25):02d}-{r.randint(1, 500):03d}"], "city": "Singapore", "district": est, "state": None,
                        "postal": f"{pc}{blk:03d}{r.randint(0, 9)}"[:6], "country": "SG"}
        p["phone"] = f"+65 {r.choice('89')}{r.randint(100, 999)} {r.randint(1000, 9999)}"
    elif locale == "br":
        city, uf, cep = r.choice(BR_CITIES)
        p["address"] = {"line": [f"{r.choice(BR_STREETS)}, {r.randint(10, 3000)}"], "city": city, "district": r.choice(["Centro", "Vila Mariana", "Pinheiros", "Mooca", "Tatuapé"]),
                        "state": uf, "postal": f"{cep}{r.randint(100, 999)}-{r.randint(0, 999):03d}", "country": "BR"}
        p["phone"] = f"(11) 9{r.randint(1000, 9999)}-{r.randint(1000, 9999)}"
    elif locale == "ae":
        city, area = r.choice(AE_AREAS)
        p["address"] = {"line": [f"Villa {r.randint(1, 90)}, Street {r.randint(1, 40)}", area], "city": city, "district": area, "state": "Abu Dhabi",
                        "postal": None, "country": "AE"}
        p["phone"] = f"+971 5{r.choice('0256')} {r.randint(100, 999)} {r.randint(1000, 9999)}"
