"""Gordon Ryan career match dataset — staged for batch insert.

Single source of truth for per-match METADATA is CAREER (the career table:
opponent, result, method, event, weight, stage, year). Ten matches additionally
carry a transcribed move SEQUENCE (SEQUENCES, keyed by (opponent, year)); the rest
are metadata-only (empty sequence). Metadata-only matches still drive the
chronological ELO replay (analysis.athlete_elo.score_from_match uses win_type, or
a win/loss fallback when the sequence is empty) — they just add no graph nodes/edges.

ATHLETE == "Gordon Ryan" for every row. ``MATCHES`` (built at import) is the list
the batch inserter consumes: one dict per match with register_match-shaped kwargs
plus ``result`` ("W"/"L"/"D") for reference.

Method -> win_type mapping:
  - named submission (heel hook, RNC, kimura, choke, armbar, triangle, kneebar,
    katagatame, guillotine, tarikoplata, "verbal tap", "pressure", ...) -> SUBMISSION
    (submission field = the method text)
  - "Points" / "Pts: ..."                                               -> POINTS
  - "Referee Decision"                                                  -> DECISION
  - "EBI/OT" (overtime escape-time win)                                 -> DECISION
  - "DQ"                                                                -> None (won=True)
  - result "D" (draw)                                                   -> DRAW (won=False)
    A DRAW scores S=0.5 in score_from_match (neutral ELO); its techniques still register.
"""

from typing import Any

ATHLETE = "Gordon Ryan"

# (opponent, result, method, event, weight, stage, year) — verbatim from the table.
CAREER: list[tuple[str, str, str, str, str, str, int]] = [
    # ── 2016 ────────────────────────────────────────────────────────────────
    ("Tex Johnson",        "L", "Points",            "Grappling Ind.",  "ABS",   "F",   2016),
    ("Ian Murray",         "W", "Inside heel hook",  "Sapateiro Inv.",  "ABS",   "R1",  2016),
    ("Elliott Hill",       "W", "Armlock",           "Sapateiro Inv.",  "ABS",   "4F",  2016),
    ("PJ Barch",           "W", "Kneebar",           "Sapateiro Inv.",  "ABS",   "SF",  2016),
    ("Enrico Cocco",       "W", "Inside heel hook",  "Sapateiro Inv.",  "ABS",   "F",   2016),
    ("Pat Sabatini",       "W", "Inside heel hook",  "Goodfight Pro",   "77KG",  "SF",  2016),
    ("Kevin Berbrich",     "W", "Choke",             "Goodfight Pro",   "77KG",  "F",   2016),
    ("Joshua Bacallao",    "W", "Inside heel hook",  "PTL Sunday Open", "ABS",   "SF",  2016),
    ("Nathan Orchard",     "W", "Reverse triangle",  "PTL Sunday Open", "ABS",   "SPF", 2016),
    ("James Partridge",    "W", "Inside heel hook",  "Onnit Inv. 2",    "ABS",   "SPF", 2016),
    ("Jacen Flynn",        "W", "RNC",               "EBI 6",           "ABS",   "R1",  2016),
    ("Marcello Salazar",   "W", "Kneebar",           "EBI 6",           "ABS",   "4F",  2016),
    ("Yuri Simoes",        "W", "EBI/OT",            "EBI 6",           "ABS",   "SF",  2016),
    ("Rustam Chsiev",      "W", "EBI/OT",            "EBI 6",           "ABS",   "F",   2016),
    ("Keenan Cornelius",   "W", "Inside heel hook",  "Grappling Ind.",  "ABS",   "SPF", 2016),
    ("Matt Arroyo",        "W", "Inside heel hook",  "EBI 8",           "84KG",  "R1",  2016),
    ("Mike Hillebrand",    "W", "RNC",               "EBI 8",           "84KG",  "4F",  2016),
    ("Josh Hayden",        "W", "EBI/OT",            "EBI 8",           "84KG",  "SF",  2016),
    ("Kyle Griffin",       "W", "Inside heel hook",  "EBI 8",           "84KG",  "F",   2016),
    ("Todd Mueckemheim",   "W", "Kimura",            "Sapateiro Inv.",  "ABS",   "SPF", 2016),
    ("Vagner Rocha",       "D", "---",               "Sapateiro 2",     "ABS",   "SPF", 2016),
    ("Felipe Pena",        "L", "RNC",               "Studio 540 SPF",  "ABS",   "SPF", 2016),
    # ── 2017 ────────────────────────────────────────────────────────────────
    ("Bryan Brown",        "W", "RNC",               "Sapateiro 6",     "ABS",   "R1",  2017),
    ("Antonio Carlos",     "W", "Reverse triangle",  "Sapateiro 6",     "ABS",   "4F",  2017),
    ("Jesseray Childrey",  "W", "RNC",               "Sapateiro 6",     "ABS",   "SF",  2017),
    ("Matthew Tesla",      "W", "Reverse triangle",  "Sapateiro 6",     "ABS",   "F",   2017),
    ("Joe Baize",          "W", "Reverse triangle",  "SUG 3",           "ABS",   "SPF", 2017),
    ("JP Lebosnoyani",     "W", "RNC",               "EBI 11",          "77KG",  "R1",  2017),
    ("C. MacKarski",       "W", "Arm in guillotine", "EBI 11",          "77KG",  "4F",  2017),
    ("Marcel Goncalves",   "W", "Short choke",       "EBI 11",          "77KG",  "SF",  2017),
    ("Vagner Rocha",       "W", "RNC",               "EBI 11",          "77KG",  "F",   2017),
    ("Lucas Barbosa",      "W", "Referee Decision",  "F2W 30",          "92KG",  "SPF", 2017),
    ("Leandro Lo",         "L", "Pts: 4x0",          "ADCC WC Trials",  "ABS",   "SPF", 2017),
    ("Eliot Kelly",        "W", "Triangle armbar",   "F2W 34",          "ABS",   "SPF", 2017),
    ("M. Jokmanovic",      "W", "Kimura",            "Grappling Ind.",  "ABS",   "4F",  2017),
    ("Tex Johnson",        "W", "Reverse triangle",  "Grappling Ind.",  "ABS",   "SF",  2017),
    ("D. Johnson",         "W", "RNC",               "Grappling Ind.",  "ABS",   "F",   2017),
    ("Dillon Danis",       "W", "Referee Decision",  "ADCC",            "88KG",  "E1",  2017),
    ("Romulo Barral",      "W", "RNC",               "ADCC",            "88KG",  "4F",  2017),
    ("Alexandre Ribeiro",  "W", "Referee Decision",  "ADCC",            "88KG",  "SF",  2017),
    ("Keenan Cornelius",   "W", "Mounted guillotine","ADCC",            "88KG",  "F",   2017),
    ("Roberto Abreu",      "W", "Inside heel hook",  "ADCC",            "ABS",   "E1",  2017),
    ("Craig Jones",        "W", "Katagatame",        "ADCC",            "ABS",   "4F",  2017),
    ("Mahamed Aly",        "W", "Heel hook",         "ADCC",            "ABS",   "SF",  2017),
    ("Felipe Pena",        "L", "Pts: 6x0",          "ADCC",            "ABS",   "F",   2017),
    ("Ralek Gracie",       "W", "Reverse triangle",  "Metamoris",       "ABS",   "SPF", 2017),
    ("D. Borovic",         "W", "Outside heel hook", "EBI 14",          "ABS",   "R1",  2017),
    ("P. Donabedian",      "W", "Armbar",            "EBI 14",          "ABS",   "4F",  2017),
    ("C. Hellenberg",      "W", "EBI/OT",            "EBI 14",          "ABS",   "SF",  2017),
    ("Craig Jones",        "W", "EBI/OT",            "EBI 14",          "ABS",   "F",   2017),
    ("Yuri Simoes",        "W", "RNC",               "Kasai Pro",       "ABS",   "SPF", 2017),
    # ── 2018 ────────────────────────────────────────────────────────────────
    ("Vinny Magalhaes",    "L", "Points",            "ACBJJ 13",        "O95KG", "SPF", 2018),
    ("Max Gimenis",        "W", "RNC",               "No Gi Pan Am.",   "ABS",   "SF",  2018),
    ("Kaynan Duarte",      "W", "RNC",               "No Gi Pan Am.",   "ABS",   "F",   2018),
    ("Charles McGuire",    "W", "Tarikoplata",       "No Gi Pan Am.",   "O97KG", "SF",  2018),
    ("Max Gimenis",        "W", "RNC",               "No Gi Pan Am.",   "O97KG", "F",   2018),
    ("Josh Barnett",       "W", "Triangle",          "Quintet 3",       "ABS",   "SF",  2018),
    ("Marcos Souza",       "W", "RNC",               "Quintet 3",       "ABS",   "SF",  2018),
    ("Roberto Satoshi",    "D", "---",               "Quintet 3",       "ABS",   "SF",  2018),
    ("Craig Jones",        "W", "Short choke",       "Quintet 3",       "ABS",   "F",   2018),
    ("Vitor Shaolin",      "W", "Armbar",            "Quintet 3",       "ABS",   "F",   2018),
    ("Gregor Gracie",      "D", "---",               "Quintet 3",       "ABS",   "F",   2018),
    ("Evangelous Moumtzis","W", "Armbar",            "NoGi Worlds",     "O97KG", "R1",  2018),
    ("Yuri Simoes",        "W", "Pts: 11x0",         "NoGi Worlds",     "O97KG", "SF",  2018),
    ("Roberto Abreu",      "W", "DQ",                "NoGi Worlds",     "O97KG", "F",   2018),
    ("Kalil Fadlallah",    "W", "Choke",             "NoGi Worlds",     "ABS",   "R1",  2018),
    ("Vegard Randeberg",   "W", "RNC",               "NoGi Worlds",     "ABS",   "R2",  2018),
    ("Patrick Gaudio",     "W", "Pts: 4x4, Adv",     "NoGi Worlds",     "ABS",   "4F",  2018),
    ("Jackson Sousa",      "W", "RNC",               "NoGi Worlds",     "ABS",   "SF",  2018),
    ("Yuri Simoes",        "W", "Pts: 0x0, Adv",     "NoGi Worlds",     "ABS",   "F",   2018),
    # ── 2019 ────────────────────────────────────────────────────────────────
    ("Joao Rocha",         "W", "Pts: 1x0",          "Kasai Dallas",    "120KG", "SPF", 2019),
    ("Gabriel Checco",     "W", "Kimura",            "Kinektic 1",      "ABS",   "R3",  2019),
    ("Rafael Domingos",    "W", "RNC",               "Kinektic 1",      "ABS",   "R4",  2019),
    ("G. Vasconcelos",     "W", "Arm in guillotine", "Kinektic 1",      "ABS",   "R5",  2019),
    ("Ben Hodgkinson",     "W", "RNC",               "ADCC",            "99KG",  "R1",  2019),
    ("Tim Spriggs",        "W", "RNC",               "ADCC",            "99KG",  "4F",  2019),
    ("Lucas Barbosa",      "W", "Pts: 3x0",          "ADCC",            "99KG",  "SF",  2019),
    ("Vinicius Trator",    "W", "Choke",             "ADCC",            "99KG",  "F",   2019),
    ("Pedro Marinho",      "W", "Outside heel hook", "ADCC",            "ABS",   "R1",  2019),
    ("Garry Tonon",        "W", "Choke",             "ADCC",            "ABS",   "4F",  2019),
    ("Lachlan Giles",      "W", "RNC",               "ADCC",            "ABS",   "SF",  2019),
    ("Marcus Almeida",     "W", "Pts: 0x0, Pen",     "ADCC",            "ABS",   "F",   2019),
    ("Rousimar Palhares",  "W", "Referee Decision",  "World Festival",  "ABS",   "SPF", 2019),
    ("Bo Nickal",          "W", "Triangle",          "Third Coast III", "94KG",  "SPF", 2019),
    ("Aleksei Oleinik",    "W", "Kneebar",           "Quintet Ultra",   "ABS",   "SPF", 2019),
    ("Gabriel Gonzaga",    "W", "Outside heel hook", "SUG 10",          "ABS",   "SPF", 2019),
    # ── 2020 ────────────────────────────────────────────────────────────────
    ("Tex Johnson",        "W", "Katagatame",        "Sub Stars",       "N/A",   "SPF", 2020),
    ("Pat Downey",         "W", "Verbal tap",        "BJJ Fanatics GP", "ABS",   "SPF", 2020),
    ("David Newton",       "W", "RNC",               "Grappling Ind.",  "ABS",   "SPF", 2020),
    ("Abraham Hall",       "W", "Triangle",          "Grappling Ind.",  "ABS",   "SPF", 2020),
    ("Benjamin Dixon",     "W", "RNC",               "Grappling Ind.",  "ABS",   "SPF", 2020),
    ("Chad Allen",         "W", "RNC",               "Grappling Ind.",  "ABS",   "SPF", 2020),
    ("Austin Tracy",       "W", "RNC",               "Grappling Ind.",  "ABS",   "SPF", 2020),
    ("Kyle Boehm",         "W", "Armlock",           "WNO",             "ABS",   "SPF", 2020),
    ("Matheus Diniz",      "W", "Inside heel hook",  "WNO 4",           "ABS",   "SPF", 2020),
    # ── 2021 ────────────────────────────────────────────────────────────────
    ("Roberto Jimenez",    "W", "Armbar",            "WNO 6",           "ABS",   "SPF", 2021),
    ("Vagner Rocha",       "W", "Reverse triangle",  "WNO 7",           "93KG",  "SPF", 2021),
    # ── 2022 ────────────────────────────────────────────────────────────────
    ("Jacob Couch",        "W", "Pressure",          "WNO 12",          "O92KG", "SPF", 2022),
    ("Pedro Marinho",      "W", "RNC",               "WNO 13",          "O93KG", "SPF", 2022),
    ("Felipe Pena",        "W", "Verbal tap",        "WNO 14",          "ABS",   "SPF", 2022),
    ("Heikki Jussila",     "W", "RNC",               "ADCC",            "O99KG", "R1",  2022),
    ("Victor Hugo",        "W", "Pts: 8x0",          "ADCC",            "O99KG", "4F",  2022),
    ("Roosevelt Sousa",    "W", "Outside heel hook", "ADCC",            "O99KG", "SF",  2022),
    ("Nick Rodriguez",     "W", "Outside heel hook", "ADCC",            "O99KG", "F",   2022),
    ("Andre Galvao",       "W", "RNC",               "ADCC",            "ABS",   "SPF", 2022),
    ("Nick Rodriguez",     "W", "EBI/OT",            "UFC FP Inv.",     "ABS",   "SPF", 2022),
    # ── 2023 ────────────────────────────────────────────────────────────────
    ("Patrick Gaudio",     "W", "Armbar",            "WNO 20",          "ABS",   "SPF", 2023),
    # ── 2024 ────────────────────────────────────────────────────────────────
    ("Josh Saunders",      "W", "Outside heel hook", "WNO 24",          "O94KG", "SPF", 2024),  # noqa: E501  table: "Josh Sanders"
    ("Felipe Pena",        "W", "Pts: 2x0",          "ADCC",            "ABS",   "SPF", 2024),
    ("Yuri Simoes",        "W", "Pts: 21x0",         "ADCC",            "ABS",   "SPF", 2024),
]

# ── Transcribed move sequences (the 10 detailed matches), keyed (opponent, year) ──
# actor 'you' == Gordon Ryan. successful:False = missed attempt/threat (else landed).
SEQUENCES: dict[tuple[str, int], list[dict[str, Any]]] = {
    # Gordon vs Buchecha — 2019 ADCC Absolute Final. Win = Pts 0x0 on penalty.
    ("Marcus Almeida", 2019): [
        {"label": "Butterfly Guard", "type": "guard", "actor": "you"},
        {"label": "Sumi Gaeshi", "type": "sweep", "actor": "you", "successful": True},
        {"label": "Half Guard", "type": "guard", "actor": "opponent"},
        {"label": "Single Leg Takedown","type": "transition", "actor": "you", "successful": False},
        {"label": "Sweep", "type": "sweep", "actor": "opponent", "successful": True},
        {"label": "Butterfly Guard", "type": "guard", "actor": "you"},
        {"label": "Arm Drag", "type": "transition", "actor": "you"},
        {"label": "Sumi Gaeshi", "type": "sweep", "actor": "you", "successful": False},
        {"label": "Knee Cut Pass", "type": "pass", "actor": "opponent", "successful": False},
        {"label": "Knee Cut Pass", "type": "pass", "actor": "opponent", "successful": False},
        {"label": "Closed Guard", "type": "guard", "actor": "you"},
        {"label": "Reverse Arm Lock", "type": "submission", "actor": "you", "successful": False},
    ],
    # Gordon vs Roosevelt Sousa — 2022 ADCC SF. Win = Outside heel hook (table-confirmed).
    ("Roosevelt Sousa", 2022): [
        {"label": "Leg Entry", "type": "transition", "actor": "you"},
        {"label": "Heel Hook", "type": "submission", "actor": "you"},
    ],
    # Gordon vs Victor Hugo — 2022 ADCC 4F. Win = Pts 8x0.
    ("Victor Hugo", 2022): [
        {"label": "Closed Guard", "type": "guard", "actor": "opponent"},
        {"label": "Stand Up", "type": "transition", "actor": "you"},
        {"label": "De La Riva Guard", "type": "guard", "actor": "opponent"},
        {"label": "Sweep", "type": "sweep", "actor": "opponent", "successful": False},
        {"label": "Guard Pass", "type": "pass", "actor": "you"},
        {"label": "Back Take", "type": "control", "actor": "you"},
        {"label": "Mount", "type": "control", "actor": "you"},
        {"label": "Double Underhooks", "type": "control", "actor": "you"},
        {"label": "Gift Wrap", "type": "control", "actor": "you"},
        {"label": "Half Guard", "type": "guard", "actor": "opponent"},
        {"label": "Half Guard Pass", "type": "pass", "actor": "you"},
        {"label": "Back Take", "type": "control", "actor": "you"},
        {"label": "Mount", "type": "control", "actor": "you"},
        {"label": "Double Underhooks", "type": "control", "actor": "you"},
        {"label": "S-Mount", "type": "control", "actor": "you"},
        {"label": "Triangle", "type": "submission", "actor": "you", "successful": False},
        {"label": "Guard Recovery", "type": "escape", "actor": "opponent"},
        {"label": "Backstep Pass", "type": "pass", "actor": "you"},
        {"label": "Inversion", "type": "guard", "actor": "opponent"},
        {"label": "50/50 Guard", "type": "guard", "actor": "opponent"},
        {"label": "Knee Bar", "type": "submission", "actor": "opponent", "successful": False},
        {"label": "Reverse Heel Hook", "type": "submission", "actor": "you", "successful": False},
        {"label": "Sweep", "type": "sweep", "actor": "opponent"},
        {"label": "Leg Lock", "type": "submission", "actor": "opponent", "successful": False},
    ],
    # Gordon vs Yuri Simoes — 2024 ADCC superfight. Win = Pts 21x0.
    ("Yuri Simoes", 2024): [
        {"label": "Far-Side Knee Pick", "type": "takedown", "actor": "you"},
        {"label": "Single Leg", "type": "takedown", "actor": "you"},
        {"label": "Takedown", "type": "takedown", "actor": "you"},
        {"label": "Half Guard", "type": "guard", "actor": "opponent"},
        {"label": "Knee Shield", "type": "guard", "actor": "opponent"},
        {"label": "Knee Cut", "type": "pass", "actor": "you", "successful": False},
        {"label": "Smash Half Guard", "type": "pass", "actor": "you"},
        {"label": "Deep Half Guard", "type": "guard", "actor": "opponent", "successful": False},
        {"label": "Near-Side Underhook Pass", "type": "pass", "actor": "you"},
        {"label": "Double Underhook Pass", "type": "pass", "actor": "you"},
        {"label": "Mount", "type": "control", "actor": "you"},
        {"label": "Mounted Armbar", "type": "submission", "actor": "you", "successful": False},
        {"label": "Escape", "type": "escape", "actor": "opponent"},
        {"label": "Half Guard Pass", "type": "pass", "actor": "you"},
        {"label": "Gift Wrap", "type": "control", "actor": "you"},
        {"label": "Back Take", "type": "control", "actor": "you"},
        {"label": "Crucifix", "type": "control", "actor": "you", "successful": False},
        {"label": "Escape", "type": "escape", "actor": "opponent"},
        {"label": "Head-and-Arm Pass", "type": "pass", "actor": "opponent", "successful": False},
        {"label": "Guillotine", "type": "submission", "actor": "you", "successful": False},
        {"label": "Back Take", "type": "control", "actor": "you"},
        {"label": "Kimura Trap", "type": "control", "actor": "you"},
        {"label": "Back Triangle", "type": "submission", "actor": "you", "successful": False},
        {"label": "Reverse Triangle", "type": "submission", "actor": "you", "successful": False},
        {"label": "Escape", "type": "escape", "actor": "opponent"},
        {"label": "Ankle Lock", "type": "submission", "actor": "opponent", "successful": False},
        {"label": "Counter Heel Hook", "type": "submission", "actor": "you", "successful": False},
    ],
    # Gordon vs Felipe Pena — 2024 ADCC superfight. Win = Pts 2x0.
    ("Felipe Pena", 2024): [
        {"label": "Collar Tie", "type": "control", "actor": "you"},
        {"label": "Takedown", "type": "takedown", "actor": "you"},
        {"label": "Half Guard", "type": "guard", "actor": "opponent"},
        {"label": "Bear Trap", "type": "guard", "actor": "opponent"},
        {"label": "Calf Slicer", "type": "submission", "actor": "opponent", "successful": False},
        {"label": "Sweep", "type": "sweep", "actor": "opponent", "successful": False},
        {"label": "Stand Up", "type": "transition", "actor": "you"},
        {"label": "Arm Drag", "type": "control", "actor": "you", "successful": False},
        {"label": "Back Take", "type": "control", "actor": "you", "successful": False},
        {"label": "Bear Trap", "type": "guard", "actor": "opponent"},
        {"label": "50/50 Guard", "type": "guard", "actor": "opponent"},
        {"label": "Sweep", "type": "sweep", "actor": "opponent", "successful": False},
        {"label": "Back Take", "type": "control", "actor": "opponent", "successful": False},
        {"label": "Arm Drag", "type": "control", "actor": "opponent"},
        {"label": "Back Take", "type": "control", "actor": "opponent"},
        {"label": "Reversal", "type": "sweep", "actor": "you"},
        {"label": "Double Underhooks", "type": "control", "actor": "you"},
        {"label": "Bear Trap", "type": "guard", "actor": "opponent"},
        {"label": "Sweep", "type": "sweep", "actor": "opponent", "successful": False},
        {"label": "Guard Pass", "type": "pass", "actor": "you", "successful": False},
        {"label": "Bear Trap", "type": "guard", "actor": "opponent"},
        {"label": "Ude Gatame", "type": "submission", "actor": "you", "successful": False},
    ],
    # Gordon vs Josh Saunders — WNO 24 (2024). Win = Outside heel hook.
    ("Josh Saunders", 2024): [
        {"label": "Pull Guard", "type": "transition", "actor": "you"},
        {"label": "Shin on Shin Guard","type": "guard", "actor": "you"},
        {"label": "Heel Hook", "type": "submission", "actor": "you", "successful": False},
        {"label": "Sweep", "type": "sweep", "actor": "you", "successful": True},
        {"label": "Half Guard", "type": "guard", "actor": "opponent"},
        {"label": "Half Guard Pass", "type": "pass", "actor": "you"},
        {"label": "Mount", "type": "control", "actor": "you"},
        {"label": "Triangle", "type": "submission", "actor": "you", "successful": False},
        {"label": "Double Underhooks", "type": "control", "actor": "you"},
        {"label": "Kimura", "type": "submission", "actor": "you", "successful": False},
        {"label": "Kimura", "type": "submission", "actor": "you", "successful": False},
        {"label": "Half Guard", "type": "guard", "actor": "opponent"},
        {"label": "Hip Switch Pass", "type": "pass", "actor": "you"},
        {"label": "Side Control", "type": "control", "actor": "you"},
        {"label": "Heel Hook", "type": "submission", "actor": "you", "successful": True},
    ],
    # Gordon vs Heikki Jussila ("Hy Ju") — 2022 ADCC R1. Win = RNC.
    ("Heikki Jussila", 2022): [
        {"label": "Pull Guard", "type": "transition", "actor": "you"},
        {"label": "Arm Drag", "type": "transition", "actor": "you"},
        {"label": "Back Take", "type": "control", "actor": "you"},
        {"label": "Turtle", "type": "guard", "actor": "opponent"},
        {"label": "Half Nelson", "type": "control", "actor": "you"},
        {"label": "Back Take", "type": "control", "actor": "you"},
        {"label": "Body Triangle", "type": "control", "actor": "you"},
        {"label": "Rear Naked Choke", "type": "submission", "actor": "you", "successful": True},
    ],
    # Gordon vs Bo Nickal — Third Coast III, 2019. Win = Triangle.
    ("Bo Nickal", 2019): [
        {"label": "Low Single Takedown", "type": "transition", "actor": "opponent", "successful": False},  # noqa: E501
        {"label": "Pull Guard", "type": "transition", "actor": "you"},
        {"label": "Scissor Sweep", "type": "sweep", "actor": "you", "successful": False},
        {"label": "Scramble to Standing","type": "transition", "actor": "opponent"},
        {"label": "Pull Guard", "type": "transition", "actor": "you"},
        {"label": "Scissor Sweep", "type": "sweep", "actor": "you", "successful": True},
        {"label": "Single Leg Takedown", "type": "transition", "actor": "you", "successful": False},
        {"label": "Double Leg Takedown", "type": "transition", "actor": "opponent", "successful": True},  # noqa: E501
        {"label": "Closed Guard", "type": "guard", "actor": "you"},
        {"label": "Triangle Choke", "type": "submission", "actor": "you", "successful": True},
    ],
    # Gordon vs Vinicius "Trator" Ferreira — ADCC 99KG Final, 2019. Win = Choke.
    ("Vinicius Trator", 2019): [
        {"label": "Ashi Foot Sweep", "type": "transition", "actor": "you", "successful": False},
        {"label": "Single Leg Takedown", "type": "transition", "actor": "opponent", "successful": False},  # noqa: E501
        {"label": "Body Lock Takedown", "type": "transition", "actor": "you", "successful": True},
        {"label": "Half Guard", "type": "guard", "actor": "opponent"},
        {"label": "Deep Half Guard", "type": "guard", "actor": "opponent"},
        {"label": "Half Guard Pass", "type": "pass", "actor": "you"},
        {"label": "Mount", "type": "control", "actor": "you"},
        {"label": "Kata Gatame", "type": "submission", "actor": "you", "successful": False},
        {"label": "Half Guard Recovery", "type": "escape", "actor": "opponent"},
        {"label": "Half Guard", "type": "guard", "actor": "opponent"},
        {"label": "Leg Lock", "type": "submission", "actor": "opponent", "successful": False},
        {"label": "Back Take", "type": "control", "actor": "you"},
        {"label": "Rear Naked Choke", "type": "submission", "actor": "you", "successful": True},
    ],
    # Gordon vs Andre Galvao — 2022 ADCC superfight. Win = RNC.
    ("Andre Galvao", 2022): [
        {"label": "Heel Hook", "type": "submission", "actor": "you", "successful": False},
        {"label": "Takedown", "type": "transition", "actor": "you", "successful": True},
        {"label": "Half Guard", "type": "guard", "actor": "opponent"},
        {"label": "Body Lock Pass", "type": "pass", "actor": "you", "successful": False},
        {"label": "Half Guard", "type": "guard", "actor": "opponent"},
        {"label": "Back Take", "type": "control", "actor": "you"},
        {"label": "Body Triangle", "type": "control", "actor": "you"},
        {"label": "Straight Jacket", "type": "control", "actor": "you"},
        {"label": "Rear Naked Choke", "type": "submission", "actor": "you", "successful": True},
    ],
}

# Methods that are NOT named submissions.
_POINTS = "points"
_DECISIONS = {"referee decision", "ebi/ot"}
_NO_METHOD = {"dq", "---", ""}


def _classify(method: str, result: str) -> tuple[str | None, str | None]:
    """(win_type, submission) from a Method string + W/L/D result."""
    m = method.strip()
    ml = m.lower()
    if result == "D":
        return ("DRAW", None)  # neutral S=0.5 in score_from_match
    if ml in _NO_METHOD:
        return (None, None)  # DQ / unknown — win/loss fallback drives ELO
    if ml == _POINTS or ml.startswith("pts"):
        return ("POINTS", None)
    if ml in _DECISIONS:
        return ("DECISION", None)
    return ("SUBMISSION", m)  # any named technique


def build_matches() -> list[dict[str, Any]]:
    """One register_match-shaped dict per career row (chronological)."""
    out: list[dict[str, Any]] = []
    for opponent, result, method, event, weight, stage, year in CAREER:
        win_type, submission = _classify(method, result)
        out.append({
            "athlete": ATHLETE,
            "opponent": opponent,
            "event": event,
            "year": year,
            "weight_class": None if weight in ("N/A", "") else weight,
            "win_type": win_type,
            "stage": stage or None,
            "submission": submission,
            "won": result == "W",
            "result": result,  # "W"/"L"/"D" — reference only; inserter uses `won`
            "sequence": SEQUENCES.get((opponent, year), []),
        })
    return out


MATCHES: list[dict[str, Any]] = build_matches()
