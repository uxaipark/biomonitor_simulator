"""진단·알레르기·바이탈 카탈로그 — 나라별 코드 체계(ICD-10-CM, ICD-10 WHO, KCD-8, ICD-10-GM, CIM-10, SNOMED CT, MEDIS 標準病名)와
바이탈 측정 코드(LOINC, IEEE 11073 MDC, 병원 로컬 코드)를 한곳에 모은다.  값 생성도 여기서 한다(결정적)."""
from __future__ import annotations

import math
import random

# key: icd10(WHO), icd10cm, snomed, names{en,ko,ja,de,fr,nl,pt,ar}, vitals profile, 병동 진료과 키, MEDIS 病名交換コード(합성 8자리)
CONDITIONS = {
    "afib": {"icd10": "I48.9", "icd10cm": "I48.91", "snomed": "49436004", "medis": "20059391", "dept": "card", "vs": {"hr": (104, 18, True)},
             "names": {"en": "Atrial fibrillation", "ko": "심방세동", "ja": "心房細動", "de": "Vorhofflimmern", "fr": "Fibrillation auriculaire", "nl": "Atriumfibrilleren", "pt": "Fibrilação atrial", "ar": "رجفان أذيني"}},
    "hf": {"icd10": "I50.9", "icd10cm": "I50.9", "snomed": "84114007", "medis": "20060720", "dept": "card", "vs": {"hr": (92, 10), "rr": (22, 3), "spo2": (93, 2)},
           "names": {"en": "Heart failure", "ko": "심부전", "ja": "心不全", "de": "Herzinsuffizienz", "fr": "Insuffisance cardiaque", "nl": "Hartfalen", "pt": "Insuficiência cardíaca", "ar": "قصور القلب"}},
    "nstemi": {"icd10": "I21.4", "icd10cm": "I21.4", "snomed": "401314000", "medis": "20064432", "dept": "card", "vs": {"hr": (88, 12), "sbp": (138, 18)},
               "names": {"en": "Non-ST elevation myocardial infarction", "ko": "비ST분절상승 심근경색", "ja": "非ST上昇型心筋梗塞", "de": "Nicht-ST-Hebungsinfarkt (NSTEMI)",
                         "fr": "Infarctus du myocarde sans sus-décalage du segment ST", "nl": "Non-ST-elevatie myocardinfarct", "pt": "Infarto agudo do miocárdio sem supradesnivelamento do ST", "ar": "احتشاء عضلة القلب دون ارتفاع ST"}},
    "stemi": {"icd10": "I21.3", "icd10cm": "I21.3", "snomed": "401303003", "medis": "20064431", "dept": "card", "vs": {"hr": (96, 14), "sbp": (126, 20)},
              "names": {"en": "ST elevation myocardial infarction", "ko": "ST분절상승 심근경색", "ja": "ST上昇型心筋梗塞", "de": "ST-Hebungsinfarkt (STEMI)",
                        "fr": "Infarctus du myocarde avec sus-décalage du segment ST", "nl": "ST-elevatie myocardinfarct", "pt": "Infarto agudo do miocárdio com supradesnivelamento do ST", "ar": "احتشاء عضلة القلب مع ارتفاع ST"}},
    "avb3": {"icd10": "I44.2", "icd10cm": "I44.2", "snomed": "27885002", "medis": "20051740", "dept": "card", "vs": {"hr": (42, 6)},
             "names": {"en": "Complete atrioventricular block", "ko": "완전 방실차단", "ja": "完全房室ブロック", "de": "AV-Block III. Grades", "fr": "Bloc auriculo-ventriculaire complet",
                       "nl": "Totaal AV-blok", "pt": "Bloqueio atrioventricular total", "ar": "إحصار أذيني بطيني كامل"}},
    "svt": {"icd10": "I47.1", "icd10cm": "I47.10", "snomed": "6456007", "medis": "20068203", "dept": "card", "vs": {"hr": (118, 22)},
            "names": {"en": "Supraventricular tachycardia", "ko": "상심실성 빈맥", "ja": "上室性頻拍", "de": "Supraventrikuläre Tachykardie", "fr": "Tachycardie supraventriculaire",
                      "nl": "Supraventriculaire tachycardie", "pt": "Taquicardia supraventricular", "ar": "تسرع القلب فوق البطيني"}},
    "syncope": {"icd10": "R55", "icd10cm": "R55", "snomed": "271594007", "medis": "20062610", "dept": "card", "vs": {"sbp": (112, 14)},
                "names": {"en": "Syncope and collapse", "ko": "실신 및 허탈", "ja": "失神", "de": "Synkope und Kollaps", "fr": "Syncope et collapsus", "nl": "Syncope", "pt": "Síncope e colapso", "ar": "إغماء وانهيار"}},
    "pneumonia": {"icd10": "J18.9", "icd10cm": "J18.9", "snomed": "233604007", "medis": "20059927", "dept": "resp", "vs": {"hr": (98, 10), "rr": (24, 4), "spo2": (92, 2.5), "temp": (38.3, 0.6)},
                  "names": {"en": "Pneumonia, unspecified organism", "ko": "상세불명 병원체의 폐렴", "ja": "肺炎", "de": "Pneumonie, nicht näher bezeichnet", "fr": "Pneumopathie, sans précision",
                            "nl": "Pneumonie", "pt": "Pneumonia não especificada", "ar": "التهاب رئوي غير محدد"}},
    "copd": {"icd10": "J44.1", "icd10cm": "J44.1", "snomed": "195951007", "medis": "20068480", "dept": "resp", "vs": {"rr": (23, 3), "spo2": (90, 2.5)},
             "names": {"en": "COPD with acute exacerbation", "ko": "급성 악화를 동반한 만성 폐쇄성 폐질환", "ja": "慢性閉塞性肺疾患の急性増悪", "de": "COPD mit akuter Exazerbation",
                       "fr": "BPCO avec exacerbation aiguë", "nl": "COPD met acute exacerbatie", "pt": "DPOC com exacerbação aguda", "ar": "مرض الانسداد الرئوي المزمن مع تفاقم حاد"}},
    "sepsis": {"icd10": "A41.9", "icd10cm": "A41.9", "snomed": "91302008", "medis": "20066180", "dept": "med", "vs": {"hr": (112, 12), "rr": (25, 4), "temp": (38.7, 0.7), "sbp": (98, 14)},
               "names": {"en": "Sepsis, unspecified organism", "ko": "상세불명의 패혈증", "ja": "敗血症", "de": "Sepsis, nicht näher bezeichnet", "fr": "Sepsis, sans précision",
                         "nl": "Sepsis", "pt": "Sepse não especificada", "ar": "إنتان غير محدد"}},
    "stroke": {"icd10": "I63.9", "icd10cm": "I63.9", "snomed": "432504007", "medis": "20054820", "dept": "neuro", "vs": {"sbp": (156, 18)},
               "names": {"en": "Cerebral infarction, unspecified", "ko": "상세불명의 뇌경색증", "ja": "脳梗塞", "de": "Hirninfarkt, nicht näher bezeichnet", "fr": "Infarctus cérébral, sans précision",
                         "nl": "Herseninfarct", "pt": "Infarto cerebral não especificado", "ar": "احتشاء دماغي غير محدد"}},
    "gibleed": {"icd10": "K92.2", "icd10cm": "K92.2", "snomed": "74474003", "medis": "20058102", "dept": "med", "vs": {"hr": (104, 12), "sbp": (104, 14)},
                "names": {"en": "Gastrointestinal haemorrhage", "ko": "상세불명의 위장출혈", "ja": "消化管出血", "de": "Gastrointestinale Blutung", "fr": "Hémorragie gastro-intestinale",
                          "nl": "Gastro-intestinale bloeding", "pt": "Hemorragia gastrointestinal", "ar": "نزيف الجهاز الهضمي"}},
    "hipfx": {"icd10": "S72.0", "icd10cm": "S72.001A", "snomed": "5913000", "medis": "20061720", "dept": "ortho", "vs": {},
              "names": {"en": "Fracture of neck of femur", "ko": "대퇴골 경부의 골절", "ja": "大腿骨頚部骨折", "de": "Schenkelhalsfraktur", "fr": "Fracture du col du fémur",
                        "nl": "Collumfractuur", "pt": "Fratura do colo do fêmur", "ar": "كسر عنق عظم الفخذ"}},
    "cabg": {"icd10": "Z95.1", "icd10cm": "Z95.1", "snomed": "399261000", "medis": "20089010", "dept": "cts", "vs": {"hr": (94, 12)},
             "names": {"en": "Presence of aortocoronary bypass graft", "ko": "대동맥관상동맥 우회로 이식의 존재", "ja": "冠動脈バイパス術後", "de": "Vorhandensein eines aortokoronaren Bypasses",
                       "fr": "Présence d'un pontage aorto-coronaire", "nl": "Status na CABG", "pt": "Presença de enxerto de ponte aortocoronária", "ar": "وجود طعم مجازة أبهرية تاجية"}},
}
# 에뮬레이터 환자 프로필(emulator/hospital/profiles.py)의 진단·동반질환을 연동 EMR 로 옮길 때 쓰는 추가 항목.
# 무작위 입원 생성(COND_WEIGHT)에는 쓰지 않는다.
_N = lambda en, ko, ja, de, fr, nl, pt, ar: {"en": en, "ko": ko, "ja": ja, "de": de, "fr": fr, "nl": nl, "pt": pt, "ar": ar}
EXTRA_CONDITIONS = {
    "cad": ("I25.1", "I25.10", "53741008", "20055010", "card", _N("Atherosclerotic heart disease", "죽상경화성 심장병", "冠動脈硬化症", "Atherosklerotische Herzkrankheit", "Cardiopathie artérioscléreuse", "Coronairlijden", "Doença aterosclerótica do coração", "مرض القلب التصلبي العصيدي")),
    "ami": ("I21.0", "I21.09", "54329005", "20064430", "card", _N("Acute transmural myocardial infarction of anterior wall", "앞벽의 급성 전층심근경색증", "急性前壁心筋梗塞", "Akuter transmuraler Myokardinfarkt der Vorderwand", "Infarctus transmural aigu du myocarde, de la paroi antérieure", "Acuut voorwandinfarct", "Infarto agudo transmural da parede anterior do miocárdio", "احتشاء عضلة القلب الحاد في الجدار الأمامي")),
    "htn_hd": ("I11.0", "I11.0", "64715009", "20058740", "card", _N("Hypertensive heart disease with heart failure", "울혈성 심부전을 동반한 고혈압성 심장병", "高血圧性心疾患(心不全を伴う)", "Hypertensive Herzkrankheit mit Herzinsuffizienz", "Cardiopathie hypertensive, avec insuffisance cardiaque", "Hypertensieve hartziekte met hartfalen", "Doença cardíaca hipertensiva com insuficiência cardíaca", "مرض القلب الناتج عن ارتفاع ضغط الدم مع قصور القلب")),
    "arrhythmia": ("I49.9", "I49.9", "698247007", "20056200", "card", _N("Cardiac arrhythmia, unspecified", "상세불명의 심장부정맥", "不整脈", "Kardiale Arrhythmie, nicht näher bezeichnet", "Arythmie cardiaque, sans précision", "Hartritmestoornis", "Arritmia cardíaca não especificada", "اضطراب نظم القلب غير محدد")),
    "sss": ("I49.5", "I49.5", "36083008", "20056160", "card", _N("Sick sinus syndrome", "동기능부전증후군", "洞不全症候群", "Sick-Sinus-Syndrom", "Maladie du sinus", "Sick-sinussyndroom", "Síndrome do nó sinusal", "متلازمة العقدة الجيبية المريضة")),
    "valve": ("I35.0", "I35.0", "60573004", "20053390", "cts", _N("Aortic (valve) stenosis", "대동맥판 협착", "大動脈弁狭窄症", "Aortenklappenstenose", "Rétrécissement aortique", "Aortaklepstenose", "Estenose da valva aórtica", "تضيق الصمام الأبهري")),
    "cmp": ("I42.0", "I42.0", "399020009", "20062020", "card", _N("Dilated cardiomyopathy", "확장성 심근병증", "拡張型心筋症", "Dilatative Kardiomyopathie", "Myocardiopathie avec dilatation", "Gedilateerde cardiomyopathie", "Cardiomiopatia dilatada", "اعتلال عضلة القلب التوسعي")),
    "arrest": ("I46.0", "I46.9", "410429000", "20056830", "card", _N("Cardiac arrest with successful resuscitation", "성공적으로 소생된 심장정지", "心停止(蘇生成功)", "Herzstillstand mit erfolgreicher Wiederbelebung", "Arrêt cardiaque réanimé avec succès", "Hartstilstand met succesvolle reanimatie", "Parada cardíaca com ressuscitação bem sucedida", "سكتة قلبية مع إنعاش ناجح")),
    "dm2": ("E11.9", "E11.9", "44054006", "20087910", "med", _N("Type 2 diabetes mellitus without complications", "합병증을 동반하지 않은 2형 당뇨병", "2型糖尿病", "Diabetes mellitus Typ 2 ohne Komplikationen", "Diabète sucré de type 2, sans complication", "Diabetes mellitus type 2", "Diabetes mellitus tipo 2 sem complicações", "داء السكري من النوع الثاني بدون مضاعفات")),
    "ckd": ("N18.5", "N18.5", "433146000", "20089620", "med", _N("Chronic kidney disease, stage 5", "만성 콩팥병(5기)", "慢性腎臓病ステージ5", "Chronische Nierenkrankheit, Stadium 5", "Maladie rénale chronique, stade 5", "Chronische nierschade stadium 5", "Doença renal crônica estágio 5", "مرض الكلى المزمن المرحلة الخامسة")),
    "cancer": ("C34.9", "C34.90", "363358000", "20074590", "med", _N("Malignant neoplasm of bronchus or lung, unspecified", "상세불명의 기관지 또는 폐의 악성 신생물", "肺癌", "Bösartige Neubildung: Bronchus oder Lunge, nicht näher bezeichnet", "Tumeur maligne des bronches ou du poumon, sans précision", "Longcarcinoom", "Neoplasia maligna dos brônquios ou pulmões, não especificado", "ورم خبيث في القصبة أو الرئة غير محدد")),
    "osa": ("G47.3", "G47.33", "78275009", "20066110", "resp", _N("Sleep apnoea", "수면무호흡", "睡眠時無呼吸症候群", "Schlafapnoe", "Apnée du sommeil", "Slaapapneu", "Apneia de sono", "انقطاع النفس أثناء النوم")),
    "hyperthyroid": ("E05.9", "E05.90", "34486009", "20054480", "med", _N("Thyrotoxicosis, unspecified", "상세불명의 갑상선중독증", "甲状腺機能亢進症", "Hyperthyreose, nicht näher bezeichnet", "Thyréotoxicose, sans précision", "Hyperthyreoïdie", "Tireotoxicose não especificada", "فرط نشاط الغدة الدرقية غير محدد")),
    "cholecystitis": ("K81.0", "K81.0", "65275009", "20055470", "med", _N("Acute cholecystitis", "급성 담낭염", "急性胆嚢炎", "Akute Cholezystitis", "Cholécystite aiguë", "Acute cholecystitis", "Colecistite aguda", "التهاب المرارة الحاد")),
    "htn": ("I10", "I10", "38341003", "20059870", "med", _N("Essential (primary) hypertension", "본태성(원발성) 고혈압", "本態性高血圧症", "Essentielle (primäre) Hypertonie", "Hypertension essentielle (primitive)", "Essentiële hypertensie", "Hipertensão essencial (primária)", "ارتفاع ضغط الدم الأساسي")),
    "dyslipidemia": ("E78.5", "E78.5", "370992007", "20059050", "med", _N("Hyperlipidaemia, unspecified", "상세불명의 고지질혈증", "脂質異常症", "Hyperlipidämie, nicht näher bezeichnet", "Hyperlipidémie, sans précision", "Hyperlipidemie", "Hiperlipidemia não especificada", "فرط شحميات الدم غير محدد")),
    "obesity": ("E66.9", "E66.9", "414916001", "20053120", "med", _N("Obesity, unspecified", "상세불명의 비만", "肥満症", "Adipositas, nicht näher bezeichnet", "Obésité, sans précision", "Obesitas", "Obesidade não especificada", "السمنة غير محددة")),
    "smoking": ("F17.2", "F17.210", "77176002", "20082200", "med", _N("Tobacco dependence", "담배 의존", "ニコチン依存症", "Tabakabhängigkeit", "Dépendance au tabac", "Tabaksverslaving", "Dependência de tabaco", "الاعتماد على التبغ")),
}
for _k, (_icd, _cm, _sct, _medis, _dept, _names) in EXTRA_CONDITIONS.items():
    CONDITIONS[_k] = {"icd10": _icd, "icd10cm": _cm, "snomed": _sct, "medis": _medis, "dept": _dept, "vs": {}, "names": _names}
CONDITIONS["cad"]["vs"] = {"hr": (80, 10)}
CONDITIONS["ami"]["vs"] = {"hr": (96, 14), "sbp": (126, 20)}
CONDITIONS["sss"]["vs"] = {"hr": (52, 9)}
CONDITIONS["cmp"]["vs"] = {"hr": (94, 12), "spo2": (94, 2)}
CONDITIONS["hyperthyroid"]["vs"] = {"hr": (112, 12)}
CONDITIONS["osa"]["vs"] = {"spo2": (93, 2.5)}
CONDITIONS["cholecystitis"]["vs"] = {"temp": (37.6, 0.5)}
# 에뮬레이터 ICD-10 → 키, 에뮬레이터 동반질환 문자열 → 키
EMU_ICD = {"I48.0": "afib", "I25.1": "cad", "I21.0": "ami", "I50.0": "hf", "I11.0": "htn_hd", "I49.9": "arrhythmia", "I49.5": "sss", "I44.2": "avb3", "I35.0": "valve",
           "I42.0": "cmp", "Z95.1": "cabg", "I46.0": "arrest", "E11.9": "dm2", "J18.9": "pneumonia", "J44.1": "copd", "N18.5": "ckd", "I63.9": "stroke", "K92.2": "gibleed",
           "C34.9": "cancer", "S72.0": "hipfx", "A41.9": "sepsis", "G47.3": "osa", "E05.9": "hyperthyroid", "K81.0": "cholecystitis"}
EMU_COMORB = {"고혈압": "htn", "이상지질혈증": "dyslipidemia", "제2형 당뇨병": "dm2", "만성 신질환": "ckd", "COPD": "copd", "비만": "obesity", "갑상선질환": "hyperthyroid", "흡연": "smoking",
              "뇌졸중 병력": "stroke"}
EMU_ALLERGY = {"없음": "nka", "페니실린": "penicillin", "조영제": "contrast", "NSAIDs": "nsaid", "아스피린": "aspirin", "갑각류": "shellfish", "땅콩": "peanut", "설파제": "sulfonamide"}
EMU_DEPT = {"심장내과": "card", "순환기내과": "card", "흉부외과": "cts", "호흡기내과": "resp", "내분비내과": "med", "신경과": "neuro", "신장내과": "med", "소화기내과": "med",
            "종양내과": "med", "정형외과": "ortho", "감염내과": "med", "일반외과": "med"}
COND_KEYS = [k for k in CONDITIONS if k not in EXTRA_CONDITIONS]
COND_WEIGHT = {"afib": 14, "hf": 14, "nstemi": 9, "stemi": 4, "avb3": 4, "svt": 5, "syncope": 6, "pneumonia": 10, "copd": 7, "sepsis": 6, "stroke": 7, "gibleed": 5, "hipfx": 5, "cabg": 4}

ALLERGIES = [  # key, snomed(substance), rxnorm, names
    ("nka", "716186003", None, {"en": "No known allergy", "ko": "알려진 알레르기 없음", "ja": "既知のアレルギーなし", "de": "Keine bekannten Allergien", "fr": "Aucune allergie connue",
                                  "nl": "Geen bekende allergieën", "pt": "Nenhuma alergia conhecida", "ar": "لا توجد حساسية معروفة"}),
    ("penicillin", "764146007", "7980", {"en": "Penicillin", "ko": "페니실린", "ja": "ペニシリン", "de": "Penicillin", "fr": "Pénicilline", "nl": "Penicilline", "pt": "Penicilina", "ar": "البنسلين"}),
    ("contrast", "385420005", None, {"en": "Iodinated contrast media", "ko": "요오드 조영제", "ja": "ヨード造影剤", "de": "Jodhaltiges Kontrastmittel", "fr": "Produit de contraste iodé",
                                     "nl": "Jodiumhoudend contrastmiddel", "pt": "Contraste iodado", "ar": "وسط التباين اليودي"}),
    ("aspirin", "387458008", "1191", {"en": "Aspirin", "ko": "아스피린", "ja": "アスピリン", "de": "Acetylsalicylsäure", "fr": "Acide acétylsalicylique", "nl": "Acetylsalicylzuur", "pt": "Ácido acetilsalicílico", "ar": "أسبرين"}),
    ("latex", "111088007", None, {"en": "Latex", "ko": "라텍스", "ja": "ラテックス", "de": "Latex", "fr": "Latex", "nl": "Latex", "pt": "Látex", "ar": "اللاتكس"}),
    ("shellfish", "227037002", None, {"en": "Shellfish", "ko": "갑각류", "ja": "甲殻類", "de": "Schalentiere", "fr": "Crustacés", "nl": "Schaaldieren", "pt": "Frutos do mar", "ar": "المحار"}),
    ("peanut", "256349002", None, {"en": "Peanut", "ko": "땅콩", "ja": "落花生", "de": "Erdnuss", "fr": "Arachide", "nl": "Pinda", "pt": "Amendoim", "ar": "الفول السوداني"}),
    ("sulfonamide", "387406002", "10180", {"en": "Sulfonamide", "ko": "설파제", "ja": "サルファ剤", "de": "Sulfonamid", "fr": "Sulfamide", "nl": "Sulfonamide", "pt": "Sulfonamida", "ar": "السلفوناميد"}),
    ("nsaid", "372665008", None, {"en": "Non-steroidal anti-inflammatory agent", "ko": "비스테로이드성 소염진통제(NSAIDs)", "ja": "NSAIDs", "de": "NSAR", "fr": "AINS", "nl": "NSAID's", "pt": "AINE", "ar": "مضادات الالتهاب غير الستيرويدية"}),
]
ALLERGY_WEIGHT = (70, 9, 6, 5, 4, 6, 0, 0, 0)

# ------------------------------------------------------------------ 바이탈 카탈로그
# kind: loinc, 표시명(en), ucum 단위, IEEE 11073 MDC (code, 참조 id), 한국 로컬 코드/단위, 일본 로컬 코드, 허용 범위
VITALS = {
    "hr": {"loinc": "8867-4", "display": "Heart rate", "ucum": "/min", "unit_text": "beats/minute", "mdc": ("147842", "MDC_ECG_HEART_RATE"),
           "kr": ("PR", "맥박", "회/분"), "jp": ("VS002", "脈拍"), "athena": "VITALS.HEARTRATE", "range": (20, 300)},
    "rr": {"loinc": "9279-1", "display": "Respiratory rate", "ucum": "/min", "unit_text": "breaths/minute", "mdc": ("151562", "MDC_RESP_RATE"),
           "kr": ("RR", "호흡", "회/분"), "jp": ("VS003", "呼吸数"), "athena": "VITALS.RESPIRATIONRATE", "range": (2, 80)},
    "spo2": {"loinc": "59408-5", "alt_loinc": ["2708-6"], "display": "Oxygen saturation in Arterial blood by Pulse oximetry", "ucum": "%", "unit_text": "%",
             "mdc": ("150456", "MDC_PULS_OXIM_SAT_O2"), "kr": ("SPO2", "산소포화도", "%"), "jp": ("VS006", "SpO2"), "athena": "VITALS.O2SATURATION", "range": (50, 100)},
    "temp": {"loinc": "8310-5", "display": "Body temperature", "ucum": "Cel", "unit_text": "C", "mdc": ("150364", "MDC_TEMP_BODY"),
             "kr": ("BT", "체온", "℃"), "jp": ("VS001", "体温"), "athena": "VITALS.TEMPERATURE", "range": (30.0, 43.5)},
    "sbp": {"loinc": "8480-6", "display": "Systolic blood pressure", "ucum": "mm[Hg]", "unit_text": "mmHg", "mdc": ("150021", "MDC_PRESS_BLD_NONINV_SYS"),
            "kr": ("SBP", "수축기혈압", "mmHg"), "jp": ("VS004", "収縮期血圧"), "athena": "VITALS.BLOODPRESSURE.SYSTOLIC", "range": (40, 300)},
    "dbp": {"loinc": "8462-4", "display": "Diastolic blood pressure", "ucum": "mm[Hg]", "unit_text": "mmHg", "mdc": ("150022", "MDC_PRESS_BLD_NONINV_DIA"),
            "kr": ("DBP", "이완기혈압", "mmHg"), "jp": ("VS005", "拡張期血圧"), "athena": "VITALS.BLOODPRESSURE.DIASTOLIC", "range": (20, 200)},
}
BP_PANEL = ("85354-9", "Blood pressure panel with all children optional")
VITALS_PANEL = ("85353-1", "Vital signs, weight, height, head circumference, oxygen saturation and BMI panel")
VITAL_ORDER = ("hr", "rr", "spo2", "temp", "sbp", "dbp")
# 역방향 조회: 코드 → kind
BY_LOINC = {v["loinc"]: k for k, v in VITALS.items()}
BY_LOINC.update({c: k for k, v in VITALS.items() for c in v.get("alt_loinc", [])})
BY_LOINC["8889-8"] = "hr"                       # Heart rate by Pulse oximetry
BY_LOINC["8310-5"] = "temp"
BY_MDC = {v["mdc"][0]: k for k, v in VITALS.items()}
BY_MDC.update({v["mdc"][1]: k for k, v in VITALS.items()})
BY_MDC.update({"149530": "hr", "MDC_PULS_OXIM_PULS_RATE": "hr", "150344": "temp", "MDC_TEMP": "temp", "151578": "rr", "MDC_TTHOR_RESP_RATE": "rr"})
BY_KR = {v["kr"][0]: k for k, v in VITALS.items()}
BY_JP = {v["jp"][0]: k for k, v in VITALS.items()}
BY_ATHENA = {v["athena"]: k for k, v in VITALS.items()}


def c_to_f(c: float) -> float:
    return round(c * 9 / 5 + 32, 1)


def f_to_c(f: float) -> float:
    return round((f - 32) * 5 / 9, 1)


def normalize_unit(kind: str, value: float, unit: str | None) -> tuple[float | None, str | None]:
    """수신 값을 표준 단위(ucum)로 맞춘다. (값, 오류문) — 단위가 틀리면 값은 None."""
    u = (unit or "").strip().lower().replace(" ", "")
    if kind == "temp":
        if u in ("cel", "c", "°c", "degc", "℃", "celsius", "268192", "mdc_dim_degc", ""):
            return value, None
        if u in ("[degf]", "degf", "f", "°f", "fahrenheit", "266560", "mdc_dim_fahr"):
            return f_to_c(value), None
        return None, f"temperature unit '{unit}' not recognised (Cel/[degF])"
    if kind in ("hr", "rr"):
        if u in ("/min", "bpm", "beats/min", "{beats}/min", "{breaths}/min", "breaths/min", "회/분", "/分", "min-1", "264864", "mdc_dim_beat_per_min", "mdc_dim_resp_per_min", "264928", "rpm", "{beat}/min", ""):
            return value, None
        return None, f"rate unit '{unit}' not recognised (/min)"
    if kind == "spo2":
        if u in ("%", "percent", "262688", "mdc_dim_percent", ""):
            return value, None
        return None, f"SpO2 unit '{unit}' not recognised (%)"
    if kind in ("sbp", "dbp"):
        if u in ("mm[hg]", "mmhg", "266016", "mdc_dim_mmhg", ""):
            return value, None
        if u in ("kpa",):
            return round(value * 7.50062), None
        return None, f"pressure unit '{unit}' not recognised (mm[Hg])"
    return value, None


def in_range(kind: str, v: float) -> bool:
    lo, hi = VITALS[kind]["range"]
    return lo <= v <= hi


# ------------------------------------------------------------------ 값 생성 (결정적)
BASE = {"hr": (78, 9), "rr": (17, 2), "spo2": (97, 1.2), "temp": (36.7, 0.3), "sbp": (128, 14), "dbp": (76, 9)}


def chart_values(cond: str, age: int, seed: str) -> dict:
    """한 번의 간호 기록(바이탈 1세트).  cond 의 편향 + 나이 + 측정 잡음."""
    r = random.Random(seed)
    prof = CONDITIONS[cond]["vs"]
    out = {}
    for k, (mu, sd) in BASE.items():
        spec = prof.get(k)
        irregular = False
        if spec:
            mu, sd = spec[0], spec[1]
            irregular = len(spec) > 2 and spec[2]
        if k == "sbp":
            mu += max(0, age - 50) * 0.35
        v = r.gauss(mu, sd * (1.4 if irregular else 1.0))
        out[k] = v
    out["dbp"] = min(out["dbp"], out["sbp"] - 22)
    out["spo2"] = min(100.0, out["spo2"])
    return {"hr": int(round(max(28, out["hr"]))), "rr": int(round(max(6, out["rr"]))), "spo2": int(round(max(78, out["spo2"]))),
            "temp": round(min(41.2, max(34.8, out["temp"])), 1), "sbp": int(round(out["sbp"])), "dbp": int(round(max(35, out["dbp"])))}


def pick_condition(r: random.Random) -> str:
    return r.choices(COND_KEYS, weights=[COND_WEIGHT[k] for k in COND_KEYS])[0]


def pick_allergy(r: random.Random) -> str:
    return r.choices([a[0] for a in ALLERGIES], weights=ALLERGY_WEIGHT)[0]


ALLERGY_BY_KEY = {a[0]: a for a in ALLERGIES}


def los_days(r: random.Random, cond: str) -> float:
    median = {"stemi": 4.0, "nstemi": 3.5, "hf": 5.0, "sepsis": 7.0, "stroke": 6.0, "hipfx": 6.5, "cabg": 7.0, "syncope": 1.8, "svt": 1.5}.get(cond, 3.2)
    return min(16.0, max(0.4, median * math.exp(r.gauss(0, 0.45))))
