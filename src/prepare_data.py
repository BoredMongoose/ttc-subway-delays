"""Clean the raw TTC subway delay logs into one tidy incident table.

Steps
  1. Read 23 monthly/yearly Excel sheets (2014-2024) and the since-2025 CSV into one table.
  2. Standardise dates, times, the line field (111 spellings -> 5 values) and directions.
  3. Map 2,000+ free-text locations to 75 canonical stations (or a between-stations segment, a yard,
     or a line-wide location), using an alias list, then fuzzy matching for typos.
  4. Attach a description and a plain-English cause group to each of the TTC's delay codes.
  5. Drop exact duplicate rows.

Outputs (data/processed/)
  incidents.csv.gz      one row per logged incident
  station_mapping.csv   every raw location string, what it was mapped to and how (audit trail)
  code_lookup.csv       code -> description -> cause group
  cleaning_log.csv      row counts at each step (checked again in sql/03_data_quality.sql)
"""
import difflib
import re
import warnings
from pathlib import Path

import pandas as pd

warnings.filterwarnings("ignore", category=UserWarning)   # openpyxl style warnings
ROOT = Path(__file__).resolve().parents[1]
RAW, OUT = ROOT / "data" / "raw", ROOT / "data" / "processed"
OUT.mkdir(parents=True, exist_ok=True)

# ---------------------------------------------------------------------------------------------
# Stations: canonical name -> (lines, aliases). Aliases are matched as whole words, longest first,
# so "FINCH WEST" wins over "FINCH" and "SHEPPARD WEST" over "SHEPPARD".
# ---------------------------------------------------------------------------------------------
STATIONS = {
    # Line 1 Yonge-University
    "Finch": ("1", ["FINCH"]),
    "North York Centre": ("1", ["NORTH YORK CENTRE", "NORTH YORK CENTER", "NORTH YORK CTR", "NORTH YORK"]),
    "Sheppard-Yonge": ("1,4", ["SHEPPARD YONGE", "YONGE SHEPPARD", "YONGE SHP", "YONGE SHEP", "SHEPPARD"]),
    "York Mills": ("1", ["YORK MILLS"]),
    "Lawrence": ("1", ["LAWRENCE"]),
    "Eglinton": ("1", ["EGLINTON"]),
    "Davisville": ("1", ["DAVISVILLE"]),
    "St Clair": ("1", ["ST CLAIR"]),
    "Summerhill": ("1", ["SUMMERHILL"]),
    "Rosedale": ("1", ["ROSEDALE"]),
    "Bloor-Yonge": ("1,2", ["BLOOR YONGE", "YONGE BLOOR", "YONGE AND BLOOR", "BLOOR AND YONGE", "YONGE BD", "BLOOR"]),
    "Wellesley": ("1", ["WELLESLEY"]),
    "College": ("1", ["COLLEGE"]),
    "TMU (Dundas)": ("1", ["TMU", "DUNDAS"]),
    "Queen": ("1", ["QUEEN"]),
    "King": ("1", ["KING"]),
    "Union": ("1", ["UNION"]),
    "St Andrew": ("1", ["ST ANDREW"]),
    "Osgoode": ("1", ["OSGOODE"]),
    "St Patrick": ("1", ["ST PATRICK"]),
    "Queen's Park": ("1", ["QUEENS PARK"]),
    "Museum": ("1", ["MUSEUM"]),
    "St George": ("1,2", ["ST GEORGE"]),
    "Spadina": ("1,2", ["SPADINA"]),
    "Dupont": ("1", ["DUPONT"]),
    "St Clair West": ("1", ["ST CLAIR WEST"]),
    "Cedarvale (Eglinton West)": ("1", ["CEDARVALE", "EGLINTON WEST"]),
    "Glencairn": ("1", ["GLENCAIRN"]),
    "Lawrence West": ("1", ["LAWRENCE WEST"]),
    "Yorkdale": ("1", ["YORKDALE"]),
    "Wilson": ("1", ["WILSON"]),
    "Sheppard West": ("1", ["SHEPPARD WEST", "DOWNSVIEW"]),          # renamed from Downsview in 2017
    "Downsview Park": ("1", ["DOWNSVIEW PARK"]),
    "Finch West": ("1", ["FINCH WEST"]),
    "York University": ("1", ["YORK UNIVERSITY", "YORK UNIV"]),
    "Pioneer Village": ("1", ["PIONEER VILLAGE"]),
    "Highway 407": ("1", ["HIGHWAY 407", "HWY 407"]),
    "Vaughan Metropolitan Centre": ("1", ["VAUGHAN METROPOLITAN CENTRE", "VAUGHAN MC", "VAUGHAN", "VMC"]),
    # Line 2 Bloor-Danforth
    "Kipling": ("2", ["KIPLING"]),
    "Islington": ("2", ["ISLINGTON"]),
    "Royal York": ("2", ["ROYAL YORK"]),
    "Old Mill": ("2", ["OLD MILL", "OLD MILLS"]),
    "Jane": ("2", ["JANE"]),
    "Runnymede": ("2", ["RUNNYMEDE"]),
    "High Park": ("2", ["HIGH PARK"]),
    "Keele": ("2", ["KEELE"]),
    "Dundas West": ("2", ["DUNDAS WEST"]),
    "Lansdowne": ("2", ["LANSDOWNE"]),
    "Dufferin": ("2", ["DUFFERIN"]),
    "Ossington": ("2", ["OSSINGTON"]),
    "Christie": ("2", ["CHRISTIE"]),
    "Bathurst": ("2", ["BATHURST"]),
    "Bay": ("2", ["BAY"]),
    "Sherbourne": ("2", ["SHERBOURNE"]),
    "Castle Frank": ("2", ["CASTLE FRANK"]),
    "Broadview": ("2", ["BROADVIEW"]),
    "Chester": ("2", ["CHESTER"]),
    "Pape": ("2", ["PAPE"]),
    "Donlands": ("2", ["DONLANDS"]),
    "Greenwood": ("2", ["GREENWOOD"]),
    "Coxwell": ("2", ["COXWELL"]),
    "Woodbine": ("2", ["WOODBINE"]),
    "Main Street": ("2", ["MAIN STREET", "MAIN"]),
    "Victoria Park": ("2", ["VICTORIA PARK"]),
    "Warden": ("2", ["WARDEN"]),
    "Kennedy": ("2,3", ["KENNEDY"]),
    # Line 3 Scarborough (closed July 2023)
    "Lawrence East": ("3", ["LAWRENCE EAST"]),
    "Ellesmere": ("3", ["ELLESMERE"]),
    "Midland": ("3", ["MIDLAND"]),
    "Scarborough Centre": ("3", ["SCARBOROUGH CENTRE", "SCARBOROUGH CENTER", "SCARBOROUGH CTR", "SCARB CTR"]),
    "McCowan": ("3", ["MCCOWAN"]),
    # Line 4 Sheppard
    "Bayview": ("4", ["BAYVIEW"]),
    "Bessarion": ("4", ["BESSARION"]),
    "Leslie": ("4", ["LESLIE"]),
    "Don Mills": ("4", ["DON MILLS"]),
}
ALIASES = sorted(((a, name) for name, (_, al) in STATIONS.items() for a in al), key=lambda t: -len(t[0]))
ALIAS_RE = [(re.compile(rf"\b{re.escape(a)}\b"), name) for a, name in ALIASES]
FUZZY_KEYS = {a: name for a, name in ALIASES if len(a) >= 6}

# Line names inside a location ("BLOOR DANFORTH LINE", "YUS/BD/SHEPPARD SUBWAY") must not be read as stations.
# Interchange spellings that include a line code are rewritten first ("YONGE BD" = Bloor-Yonge on Line 2).
INTERCHANGES = [(re.compile(r"\bYONGE (BD|B D)\b"), "BLOOR YONGE"),
                (re.compile(r"\bYONGE (SHP|SHEP|SHEPPARD)\b"), "SHEPPARD YONGE")]
LINE_WORDS = re.compile(
    r"\b(LINE \d|BLOOR ?DANFORTH( LINE| SUBWAY)?( \d)?|YONGE ?UNIVERSITY( ?SPADI\w*)?( LINE| SUBW\w*| SERVI\w*)?"
    r"|SHEPPARD (LINE|SUBW\w*)|SEPPARD SUBWAY|SCARBOROUGH RAPID TRAN\w*|SRT LINE|YUS|YU|BD|SHP|SRT|DB|SUBW\w*)\b")
FACILITY = re.compile(r"\b(YARD|CARHOUSE|HOSTLER|BUILD ?UP|SHOPS?|WYE|PORTAL|DIVISION|BUILDING|LOWER|"
                      r"TRANSIT CONTR\w*|TORONTO TRANSIT COMMIS\w*|SUB ?STATION|INTERLOCKING|TAIL TRACK)\b")
LINE_WIDE = re.compile(r"\b(LINE|LINES|LIN|SYSTEM ?WIDE|VARIOUS|ALL STATIONS|SUBW\w*|SYSTEM)\b")


def clean_text(s):
    s = str(s).upper().replace("’", "'")
    s = re.sub(r"['`]", "", s)
    s = s.replace(".", " ")
    s = re.sub(r"[-/\\,&()]+", " ", s)
    return re.sub(r"\s+", " ", s).strip()


def map_location(raw):
    """Return (station, station_to, location_type, method) for one raw location string."""
    if pd.isna(raw) or not str(raw).strip():
        return None, None, "unknown", "blank"
    text = clean_text(raw)
    for rx, repl in INTERCHANGES:
        text = rx.sub(repl, text)
    stripped = LINE_WORDS.sub(" ", text)
    stripped = re.sub(r"\b(STATION|STATIO|STATI|STATN|STN|SATION|STAION|PLATFORM|PLAT|APPROACH\w*|APPR\w*)\b", " ", stripped)
    stripped = re.sub(r"\s+", " ", stripped).strip()
    if stripped == "YONGE":                           # "YONGE STATION": Bloor-Yonge or Sheppard-Yonge, decided by line
        return "Yonge (ambiguous)", None, "station", "alias"

    hits, masked = [], stripped
    for rx, name in ALIAS_RE:                         # longest alias first; mask each hit so it can't match twice
        for m in rx.finditer(masked):
            hits.append((m.start(), name))
            masked = masked[:m.start()] + "#" * (m.end() - m.start()) + masked[m.end():]
    hits = [n for _, n in sorted(hits)]
    distinct = list(dict.fromkeys(hits))

    if FACILITY.search(text) and not re.search(r"\bSTATION\b", text):
        return (distinct[0] if distinct else None), None, "yard or facility", "rule"
    if len(distinct) >= 2:
        return distinct[0], distinct[1], "between stations", "alias"
    if len(distinct) == 1:
        return distinct[0], None, "station", "alias"
    if LINE_WIDE.search(text) or not re.search(r"[A-Z]{4,}", re.sub(r"\b(AND|TO)\b", "", stripped)):
        return None, None, "line-wide", "rule"
    # typos: "YORK UNIVERISTY", "EGLINTON WEST SATION"
    first = stripped[:22]
    best = difflib.get_close_matches(first, FUZZY_KEYS, n=1, cutoff=0.82)
    if best:
        return FUZZY_KEYS[best[0]], None, "station", "fuzzy"
    return None, None, "unknown", "unmatched"


# ---------------------------------------------------------------------------------------------
# Lines
# ---------------------------------------------------------------------------------------------
def map_line(raw):
    if pd.isna(raw):
        return None
    s = clean_text(raw)
    has1 = bool(re.search(r"\b(YU|YUS|Y|YONGE|UNIVERSITY|ONGE|LINE 1)\b", s))
    has2 = bool(re.search(r"\b(BD|B D|DB|BLOOR|DANFORTH|LINE 2)\b", s))
    has4 = bool(re.search(r"\b(SHP|SHEP|SHEPPARD|SEPPARD)\b", s)) and not re.search(r"\d+ SHEPPARD", s)
    has3 = bool(re.search(r"\b(SRT|RT|SCARBOROUGH)\b", s))
    if re.match(r"^\d", s):                           # a bus route number ("29 DUFFERIN"): not a subway line
        return None
    n = has1 + has2 + has3 + has4
    if n >= 2:
        return "multiple"
    return "1" if has1 else "2" if has2 else "3" if has3 else "4" if has4 else None


# ---------------------------------------------------------------------------------------------
# Delay codes -> cause groups. The first letter is the TTC department that owns the delay:
# E = rail cars (equipment), P = plant (track, signals, power, stations), S = security,
# T = transportation (crews, control), M = miscellaneous. The second letter is U (subway) or R (Line 3).
# ---------------------------------------------------------------------------------------------
CAUSE = {
    "Disorderly behaviour & crime": ["SUDP", "SUAP", "SUAE", "SUSA", "SUROB", "SUO", "SUPOL", "SUCOL", "SUG", "SUBT",
                                     "SUSP", "SRDP", "SRAP", "SRAE", "SRSA", "SRO", "SRBT", "SRSP", "SRCOL"],
    "Medical emergencies": ["MUI", "MUIR", "MUIRS", "MUIS", "PUMEL", "PUMST", "MRUI", "MRUIR", "PREL", "PRST"],
    "People on the tracks": ["SUUT", "SRUT", "MUPR1", "MRPR1"],
    "Alarms, doors & clean-ups": ["MUPAA", "MRPAA", "MUD", "MRD", "MUDD", "MRDD", "SUEAS", "SREAS", "MUSAN", "MRSAN"],
    "Signals & communications": ["PUSI", "PUSTC", "PUSTS", "PUSNT", "PUSO", "PUSSW", "PUSWZ", "PUSZC", "PUSAC", "PUSBE",
                                 "PUSIO", "PUSLC", "PUSIC", "PUATC", "PUCSC", "PUCSS", "PUCBI", "PUDCS", "PUTSC", "PUTTC",
                                 "PUSCA", "PUSCR", "PUSRA", "PUSEA", "PRS", "PRSA", "PRSL", "PRSO", "PRSP", "PRTST", "PRSW"],
    # cameras and screens that let a lone operator watch the doors (one-person train operation: Line 4 from 2016,
    # Line 1 from November 2021)
    "Door cameras (one-person trains)": ["PUOPO", "EUOPO", "TUOPO"],
    "Track, power & stations": ["PUSTP", "PUTTP", "PUEME", "PUEO", "PUMO", "PUTD", "PUTDN", "PUTIJ", "PUTR", "PUTS",
                                "PUTSM", "PUTCD", "PUTNT", "PUTO", "PUT0", "PUTOE", "PRW", "PRO", "MUPF"],
    "Fire & smoke": ["MUPLA", "MUPLB", "MUPLC", "MUFS", "MRFS", "MRPLA", "MRPLB", "MRPLC"],
    "Weather": ["MUWEA", "MRWEA", "MUFM", "PUTIS", "PUSIS"],
    "Planned work & closures": ["MUCP", "MUCU", "MUEC", "MUATC", "MREC", "MRSTM", "PUTWZ", "PUSWZ", "PUEWZ"],
    "Crew & operations": ["MUNOA", "MUNCA", "MUESA", "MUCSA", "MUTD", "MUWR", "MULD", "MUCL", "MUIE", "MUTO",
                          "MRESA", "MRNOA", "MRIE", "MRLD", "MRCL", "MRTO"],
}
CODE_TO_CAUSE = {code: group for group, codes in CAUSE.items() for code in codes}
PREFIX_FALLBACK = {"E": "Train equipment", "T": "Crew & operations", "S": "Disorderly behaviour & crime"}

# Signal-system failures used in the Line 1 vs Line 2 comparison (wayside and central signalling only:
# no radio, SCADA, door-monitoring cameras or weather-related signal trouble).
SIGNAL_FAILURE_CODES = ["PUSI", "PUSTC", "PUSTS", "PUSNT", "PUSO", "PUSSW", "PUSZC", "PUSAC", "PUSBE", "PUSIO",
                        "PUSLC", "PUATC", "PUCSC", "PUCSS", "PUCBI", "PUDCS", "PUTSC", "PUTTC"]


def cause_group(code):
    if pd.isna(code):
        return "Other & unclassified"
    code = str(code).strip().upper()
    if code in CODE_TO_CAUSE:
        return CODE_TO_CAUSE[code]
    return PREFIX_FALLBACK.get(code[:1], "Other & unclassified")


def fix_mojibake(s):
    """The portal's code list is UTF-8 text that was decoded as Latin-1 once (an en dash shows up as 'â\\x80\\x93')."""
    if not isinstance(s, str):
        return s
    for codec in ("latin-1", "cp1252"):
        try:
            return s.encode(codec).decode("utf-8")
        except (UnicodeEncodeError, UnicodeDecodeError):
            continue
    return s


def build_code_lookup(codes_seen):
    new = pd.read_csv(RAW / "code-descriptions.csv", encoding="utf-8")
    new = pd.DataFrame({"code": new["CODE"].str.strip(), "description": new["DESCRIPTION"].map(fix_mojibake)})
    old = pd.read_excel(RAW / "ttc-subway-delay-codes.xlsx", header=None)
    old = pd.concat([old[[2, 3]].set_axis(["code", "description"], axis=1),
                     old[[6, 7]].set_axis(["code", "description"], axis=1)]).dropna()
    old = old[~old["code"].astype(str).str.contains("CODE")]
    old["code"] = old["code"].astype(str).str.strip()
    lookup = pd.concat([new.assign(source="2025 code list"), old.assign(source="2014 code list")])
    lookup = lookup.drop_duplicates("code", keep="first")
    lookup["description"] = lookup["description"].str.strip().str.replace(r"\s+", " ", regex=True).str.capitalize()
    lookup = pd.merge(pd.DataFrame({"code": sorted(codes_seen)}), lookup, on="code", how="left")
    lookup["source"] = lookup["source"].fillna("not in published lists")
    lookup["description"] = lookup["description"].fillna("(not documented)")
    lookup["cause_group"] = lookup["code"].map(cause_group)
    lookup["is_signal_failure"] = lookup["code"].isin(SIGNAL_FAILURE_CODES)
    return lookup


# ---------------------------------------------------------------------------------------------
def read_raw():
    frames = []
    for f in sorted(RAW.glob("*.xlsx")):
        if "codes" in f.name or "readme" in f.name:
            continue
        for sheet, df in pd.read_excel(f, sheet_name=None).items():
            df["source"] = f"{f.name} [{sheet}]"
            frames.append(df)
    csv = pd.read_csv(RAW / "ttc-subway-delay-data-since-2025.csv").drop(columns="_id")
    csv["source"] = "ttc-subway-delay-data-since-2025.csv"
    frames.append(csv)
    return pd.concat(frames, ignore_index=True)


def main():
    log = []
    raw = read_raw()
    log.append(("raw rows", len(raw)))

    df = pd.DataFrame({
        "date": pd.to_datetime(raw["Date"].astype(str).str[:10], format="%Y-%m-%d"),
        "time": raw["Time"].astype(str).str[:5],
        "day_raw": raw["Day"].astype(str).str.strip(),
        "location_raw": raw["Station"].astype(str).str.strip(),
        "code": raw["Code"].astype(str).str.strip().str.upper().replace({"NAN": None}),
        "min_delay": raw["Min Delay"].astype(int),
        "min_gap": raw["Min Gap"].astype(int),
        "bound": raw["Bound"].astype(str).str.strip().str.upper().where(raw["Bound"].notna()),
        "line_raw": raw["Line"].astype(str).str.strip().where(raw["Line"].notna()),
        "vehicle": raw["Vehicle"].astype(int),
        "source": raw["source"],
    })

    # direction is N/S/E/W on the subway; a few rows hold "0", "B" or "5", which become blank. This runs before
    # de-duplication so that a copy differing only by such a value counts as a duplicate.
    df["bound"] = df["bound"].where(df["bound"].isin(["N", "S", "E", "W"]))
    before = len(df)
    df = df.drop_duplicates(subset=[c for c in df.columns if c != "source"]).reset_index(drop=True)
    log.append(("exact duplicates removed", before - len(df)))

    df["hour"] = df["time"].str[:2].astype(int)
    df["weekday"] = df["date"].dt.day_name()
    df["day_matches_date"] = df["weekday"].str.upper() == df["day_raw"].str.upper()

    # locations -> stations
    uniq = pd.Series(df["location_raw"].unique())
    mapped = pd.DataFrame(uniq.map(map_location).tolist(), columns=["station", "station_to", "location_type", "method"])
    mapped.insert(0, "location_raw", uniq)
    df = df.merge(mapped[["location_raw", "station", "station_to", "location_type"]], on="location_raw", how="left")
    counts = df["location_raw"].value_counts().rename("rows")
    mapped = mapped.merge(counts, left_on="location_raw", right_index=True).sort_values("rows", ascending=False)
    mapped.to_csv(OUT / "station_mapping.csv", index=False)

    # lines: clean the field, then fill blanks from the station when it serves only one line
    df["line"] = df["line_raw"].map(map_line)
    ambiguous = df["station"] == "Yonge (ambiguous)"
    df.loc[ambiguous, "station"] = df.loc[ambiguous, "line"].map({"4": "Sheppard-Yonge"}).fillna("Bloor-Yonge")
    station_lines = {name: lines for name, (lines, _) in STATIONS.items()}
    single = df["station"].map(lambda s: station_lines.get(s) if s and "," not in station_lines.get(s, ",") else None)
    filled = df["line"].isna() & single.notna()
    df.loc[filled, "line"] = single[filled]
    df["line"] = df["line"].fillna("unknown")
    log.append(("line filled from station", int(filled.sum())))

    # codes -> descriptions and cause groups
    lookup = build_code_lookup(df["code"].dropna().unique())
    lookup.to_csv(OUT / "code_lookup.csv", index=False)
    df = df.merge(lookup[["code", "description", "cause_group", "is_signal_failure"]], on="code", how="left")
    df["cause_group"] = df["cause_group"].fillna("Other & unclassified")
    df["is_signal_failure"] = df["is_signal_failure"].fillna(False).astype(bool)

    cols = ["date", "time", "hour", "weekday", "line", "station", "station_to", "location_type", "location_raw",
            "code", "description", "cause_group", "is_signal_failure", "min_delay", "min_gap", "bound", "vehicle",
            "line_raw", "day_matches_date", "source"]
    df = df[cols].sort_values(["date", "time"]).reset_index(drop=True)
    df.to_csv(OUT / "incidents.csv.gz", index=False, date_format="%Y-%m-%d")

    log += [("clean rows", len(df)),
            ("locations mapped to a station", int(df["location_type"].isin(["station", "between stations"]).sum())),
            ("locations unknown", int((df["location_type"] == "unknown").sum())),
            ("codes not in published lists", int(df["code"].isin(lookup.loc[lookup.source == "not in published lists", "code"]).sum())),
            ("raw location strings", len(mapped)),
            ("raw line spellings", int(df["line_raw"].nunique()))]
    pd.DataFrame(log, columns=["step", "rows"]).to_csv(OUT / "cleaning_log.csv", index=False)
    for step, n in log:
        print(f"{step:35s} {n:>9,}")


if __name__ == "__main__":
    main()
