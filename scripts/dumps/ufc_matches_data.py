"""Raw UFC/MMA match dump (keyed by (athlete_a_name, year)).

Hand-dropped into the workspace as matches.py; converted verbatim to an importable
module (JSON true/false -> Python True/False). Opponent + result are DERIVED in
scripts/insert_ufc_matches.py. Do not edit by hand -- regenerate from the source dump.
"""

RAW = [
    {
        ("Charles Oliveira", 2015): {
            "winner": "Max Holloway",
            "method": "TKO (shoulder injury)",
            "events": [],
        },
        ("Dustin Poirier", 2025): {
            "winner": "Max Holloway",
            "method": "Unanimous Decision (48-47, 49-46, 49-46)",
            "events": [
                {
                    "label": "Knockdown (right hand)",
                    "type": "transition",
                    "actor": "Max Holloway",
                    "successful": True,
                },
                {"label": "Mount", "type": "control", "actor": "Max Holloway"},
                {
                    "label": "Butterfly Half Guard",
                    "type": "guard",
                    "actor": "Dustin Poirier",
                },
                {
                    "label": "Escape to Standing",
                    "type": "escape",
                    "actor": "Dustin Poirier",
                },
            ],
        },
        ("Mateusz Gamrot", 2023): {
            "winner": "Charles Oliveira",
            "method": "Submission (Rear Naked Choke)",
            "events": [
                {
                    "label": "Double Leg Takedown",
                    "type": "transition",
                    "actor": "Mateusz Gamrot",
                    "successful": True,
                },
                {
                    "label": "Omoplata / Triangle Attempt",
                    "type": "submission",
                    "actor": "Charles Oliveira",
                    "successful": False,
                },
                {
                    "label": "Sweep",
                    "type": "sweep",
                    "actor": "Charles Oliveira",
                    "successful": True,
                },
                {"label": "Back Take", "type": "control", "actor": "Charles Oliveira"},
                {
                    "label": "Body Triangle",
                    "type": "control",
                    "actor": "Charles Oliveira",
                },
                {
                    "label": "Rear Naked Choke Attempt",
                    "type": "submission",
                    "actor": "Charles Oliveira",
                    "successful": False,
                },
                {
                    "label": "Body Lock Takedown",
                    "type": "transition",
                    "actor": "Charles Oliveira",
                    "successful": True,
                },
                {"label": "Back Take", "type": "control", "actor": "Charles Oliveira"},
                {
                    "label": "Rear Naked Choke",
                    "type": "submission",
                    "actor": "Charles Oliveira",
                    "successful": True,
                },
            ],
        },
        ("Paul Craig", 2024): {
            "winner": "Caio Borralho",
            "method": "KO (punches)",
            "events": [
                {
                    "label": "Double Leg Takedown Attempt",
                    "type": "transition",
                    "actor": "Paul Craig",
                    "successful": False,
                },
                {
                    "label": "Pull Guard Attempt",
                    "type": "transition",
                    "actor": "Paul Craig",
                    "successful": False,
                },
            ],
        },
        ("Reinier de Ridder", 2025): {
            "winner": "Reinier de Ridder",
            "method": "TKO (knees)",
            "events": [
                {
                    "label": "Single Leg Takedown Attempt",
                    "type": "transition",
                    "actor": "Bo Nickal",
                    "successful": False,
                },
                {
                    "label": "Reversal",
                    "type": "sweep",
                    "actor": "Reinier de Ridder",
                    "successful": True,
                },
                {"label": "Mount", "type": "control", "actor": "Reinier de Ridder"},
                {
                    "label": "Back Take Attempt",
                    "type": "control",
                    "actor": "Reinier de Ridder",
                },
                {
                    "label": "North South Control",
                    "type": "control",
                    "actor": "Reinier de Ridder",
                },
                {"label": "Escape to Standing", "type": "escape", "actor": "Bo Nickal"},
                {
                    "label": "Body Lock Takedown Attempt",
                    "type": "transition",
                    "actor": "Reinier de Ridder",
                    "successful": False,
                },
            ],
        },
    },
    {
        ("Charles Oliveira", 2015): {
            "winner": "Max Holloway",
            "method": "TKO (shoulder injury)",
            "events": [],
        },
        ("Dustin Poirier", 2025): {
            "winner": "Max Holloway",
            "method": "Unanimous Decision (48-47, 49-46, 49-46)",
            "events": [
                {
                    "label": "Knockdown (right hand)",
                    "type": "transition",
                    "actor": "Max Holloway",
                    "successful": True,
                },
                {"label": "Mount", "type": "control", "actor": "Max Holloway"},
                {
                    "label": "Butterfly Half Guard",
                    "type": "guard",
                    "actor": "Dustin Poirier",
                },
                {
                    "label": "Escape to Standing",
                    "type": "escape",
                    "actor": "Dustin Poirier",
                },
            ],
        },
        ("Mateusz Gamrot", 2023): {
            "winner": "Charles Oliveira",
            "method": "Submission (Rear Naked Choke)",
            "events": [
                {
                    "label": "Double Leg Takedown",
                    "type": "transition",
                    "actor": "Mateusz Gamrot",
                    "successful": True,
                },
                {
                    "label": "Omoplata / Triangle Attempt",
                    "type": "submission",
                    "actor": "Charles Oliveira",
                    "successful": False,
                },
                {
                    "label": "Sweep",
                    "type": "sweep",
                    "actor": "Charles Oliveira",
                    "successful": True,
                },
                {"label": "Back Take", "type": "control", "actor": "Charles Oliveira"},
                {
                    "label": "Body Triangle",
                    "type": "control",
                    "actor": "Charles Oliveira",
                },
                {
                    "label": "Rear Naked Choke Attempt",
                    "type": "submission",
                    "actor": "Charles Oliveira",
                    "successful": False,
                },
                {
                    "label": "Body Lock Takedown",
                    "type": "transition",
                    "actor": "Charles Oliveira",
                    "successful": True,
                },
                {"label": "Back Take", "type": "control", "actor": "Charles Oliveira"},
                {
                    "label": "Rear Naked Choke",
                    "type": "submission",
                    "actor": "Charles Oliveira",
                    "successful": True,
                },
            ],
        },
        ("Paul Craig", 2024): {
            "winner": "Caio Borralho",
            "method": "KO (punches)",
            "events": [
                {
                    "label": "Double Leg Takedown Attempt",
                    "type": "transition",
                    "actor": "Paul Craig",
                    "successful": False,
                },
                {
                    "label": "Pull Guard Attempt",
                    "type": "transition",
                    "actor": "Paul Craig",
                    "successful": False,
                },
            ],
        },
        ("Reinier de Ridder", 2025): {
            "winner": "Reinier de Ridder",
            "method": "TKO (knees)",
            "events": [
                {
                    "label": "Single Leg Takedown Attempt",
                    "type": "transition",
                    "actor": "Bo Nickal",
                    "successful": False,
                },
                {
                    "label": "Reversal",
                    "type": "sweep",
                    "actor": "Reinier de Ridder",
                    "successful": True,
                },
                {"label": "Mount", "type": "control", "actor": "Reinier de Ridder"},
                {
                    "label": "Back Take Attempt",
                    "type": "control",
                    "actor": "Reinier de Ridder",
                },
                {
                    "label": "North South Control",
                    "type": "control",
                    "actor": "Reinier de Ridder",
                },
                {"label": "Escape to Standing", "type": "escape", "actor": "Bo Nickal"},
                {
                    "label": "Body Lock Takedown Attempt",
                    "type": "transition",
                    "actor": "Reinier de Ridder",
                    "successful": False,
                },
            ],
        },
    },
    {
        ("Charles Oliveira", 2015): {
            "winner": "Max Holloway",
            "method": "TKO (shoulder injury)",
            "events": [],
        },
        ("Dustin Poirier", 2025): {
            "winner": "Max Holloway",
            "method": "Unanimous Decision (48-47, 49-46, 49-46)",
            "events": [
                {
                    "label": "Knockdown (right hand)",
                    "type": "transition",
                    "actor": "Max Holloway",
                    "successful": True,
                },
                {"label": "Mount", "type": "control", "actor": "Max Holloway"},
                {
                    "label": "Butterfly Half Guard",
                    "type": "guard",
                    "actor": "Dustin Poirier",
                },
                {
                    "label": "Escape to Standing",
                    "type": "escape",
                    "actor": "Dustin Poirier",
                },
            ],
        },
        ("Mateusz Gamrot", 2023): {
            "winner": "Charles Oliveira",
            "method": "Submission (Rear Naked Choke)",
            "events": [
                {
                    "label": "Double Leg Takedown",
                    "type": "transition",
                    "actor": "Mateusz Gamrot",
                    "successful": True,
                },
                {
                    "label": "Omoplata / Triangle Attempt",
                    "type": "submission",
                    "actor": "Charles Oliveira",
                    "successful": False,
                },
                {
                    "label": "Sweep",
                    "type": "sweep",
                    "actor": "Charles Oliveira",
                    "successful": True,
                },
                {"label": "Back Take", "type": "control", "actor": "Charles Oliveira"},
                {
                    "label": "Body Triangle",
                    "type": "control",
                    "actor": "Charles Oliveira",
                },
                {
                    "label": "Rear Naked Choke Attempt",
                    "type": "submission",
                    "actor": "Charles Oliveira",
                    "successful": False,
                },
                {
                    "label": "Body Lock Takedown",
                    "type": "transition",
                    "actor": "Charles Oliveira",
                    "successful": True,
                },
                {"label": "Back Take", "type": "control", "actor": "Charles Oliveira"},
                {
                    "label": "Rear Naked Choke",
                    "type": "submission",
                    "actor": "Charles Oliveira",
                    "successful": True,
                },
            ],
        },
        ("Paul Craig", 2024): {
            "winner": "Caio Borralho",
            "method": "KO (punches)",
            "events": [
                {
                    "label": "Double Leg Takedown Attempt",
                    "type": "transition",
                    "actor": "Paul Craig",
                    "successful": False,
                },
                {
                    "label": "Pull Guard Attempt",
                    "type": "transition",
                    "actor": "Paul Craig",
                    "successful": False,
                },
            ],
        },
        ("Reinier de Ridder", 2025): {
            "winner": "Reinier de Ridder",
            "method": "TKO (knees)",
            "events": [
                {
                    "label": "Single Leg Takedown Attempt",
                    "type": "transition",
                    "actor": "Bo Nickal",
                    "successful": False,
                },
                {
                    "label": "Reversal",
                    "type": "sweep",
                    "actor": "Reinier de Ridder",
                    "successful": True,
                },
                {"label": "Mount", "type": "control", "actor": "Reinier de Ridder"},
                {
                    "label": "Back Take Attempt",
                    "type": "control",
                    "actor": "Reinier de Ridder",
                },
                {
                    "label": "North South Control",
                    "type": "control",
                    "actor": "Reinier de Ridder",
                },
                {"label": "Escape to Standing", "type": "escape", "actor": "Bo Nickal"},
                {
                    "label": "Body Lock Takedown Attempt",
                    "type": "transition",
                    "actor": "Reinier de Ridder",
                    "successful": False,
                },
            ],
        },
    },
    {
        ("Matt Serra", 2008): {
            "winner": "Georges St-Pierre",
            "method": "TKO (knees)",
            "events": [
                {
                    "label": "Double Leg Takedown",
                    "type": "transition",
                    "actor": "Georges St-Pierre",
                    "successful": True,
                },
                {"label": "Half Guard", "type": "guard", "actor": "Matt Serra"},
                {"label": "Guard Pass", "type": "pass", "actor": "Georges St-Pierre"},
                {
                    "label": "Side Control",
                    "type": "control",
                    "actor": "Georges St-Pierre",
                },
                {"label": "Back Take", "type": "control", "actor": "Georges St-Pierre"},
                {"label": "TKO (knees)", "type": "reset", "actor": "referee"},
            ],
        },
        ("Jon Fitch", 2008): {
            "winner": "Georges St-Pierre",
            "method": "Unanimous Decision (50-43, 50-44, 50-44)",
            "events": [
                {
                    "label": "Double Leg Takedown",
                    "type": "transition",
                    "actor": "Georges St-Pierre",
                    "successful": True,
                },
                {"label": "Guard", "type": "guard", "actor": "Jon Fitch"},
                {"label": "Half Guard", "type": "guard", "actor": "Jon Fitch"},
                {"label": "Guard Pass", "type": "pass", "actor": "Georges St-Pierre"},
                {
                    "label": "Side Control",
                    "type": "control",
                    "actor": "Georges St-Pierre",
                },
                {"label": "Back Take", "type": "control", "actor": "Georges St-Pierre"},
                {"label": "Hooks In", "type": "control", "actor": "Georges St-Pierre"},
                {"label": "Escape to Standing", "type": "escape", "actor": "Jon Fitch"},
                {
                    "label": "Takedown",
                    "type": "transition",
                    "actor": "Georges St-Pierre",
                    "successful": True,
                },
            ],
        },
        ("BJ Penn", 2009): {
            "winner": "Georges St-Pierre",
            "method": "TKO (doctor stoppage after round 4)",
            "events": [
                {
                    "label": "Takedown",
                    "type": "transition",
                    "actor": "Georges St-Pierre",
                    "successful": True,
                },
                {"label": "Guard", "type": "guard", "actor": "BJ Penn"},
                {"label": "Half Guard", "type": "guard", "actor": "BJ Penn"},
                {"label": "Guard Pass", "type": "pass", "actor": "Georges St-Pierre"},
                {
                    "label": "Side Control",
                    "type": "control",
                    "actor": "Georges St-Pierre",
                },
                {"label": "TKO (doctor stoppage)", "type": "reset", "actor": "referee"},
            ],
        },
        ("Thiago Alves", 2009): {
            "winner": "Georges St-Pierre",
            "method": "Unanimous Decision (50-45, 50-44, 50-45)",
            "events": [
                {
                    "label": "Double Leg Takedown",
                    "type": "transition",
                    "actor": "Georges St-Pierre",
                    "successful": True,
                },
                {
                    "label": "Escape to Standing",
                    "type": "escape",
                    "actor": "Thiago Alves",
                },
                {
                    "label": "Double Leg Takedown",
                    "type": "transition",
                    "actor": "Georges St-Pierre",
                    "successful": True,
                },
                {
                    "label": "Escape to Standing (gave up back)",
                    "type": "escape",
                    "actor": "Thiago Alves",
                },
                {"label": "Back Take", "type": "control", "actor": "Georges St-Pierre"},
                {
                    "label": "Double Leg Takedown",
                    "type": "transition",
                    "actor": "Georges St-Pierre",
                    "successful": True,
                },
            ],
        },
        ("Dan Hardy", 2010): {
            "winner": "Georges St-Pierre",
            "method": "Unanimous Decision (50-43, 50-44, 50-45)",
            "events": [
                {
                    "label": "Double Leg Takedown",
                    "type": "transition",
                    "actor": "Georges St-Pierre",
                    "successful": True,
                },
                {"label": "Half Guard", "type": "guard", "actor": "Dan Hardy"},
                {"label": "Back Take", "type": "control", "actor": "Georges St-Pierre"},
                {
                    "label": "Armbar Attempt",
                    "type": "submission",
                    "actor": "Georges St-Pierre",
                    "successful": False,
                },
                {
                    "label": "Kimura Attempt",
                    "type": "submission",
                    "actor": "Georges St-Pierre",
                    "successful": False,
                },
                {"label": "Escape to Standing", "type": "escape", "actor": "Dan Hardy"},
                {
                    "label": "Takedown",
                    "type": "transition",
                    "actor": "Georges St-Pierre",
                    "successful": True,
                },
            ],
        },
        ("Josh Koscheck", 2010): {
            "winner": "Georges St-Pierre",
            "method": "Unanimous Decision (50-45, 50-45, 50-45)",
            "events": [
                {
                    "label": "Double Leg Takedown",
                    "type": "transition",
                    "actor": "Georges St-Pierre",
                    "successful": True,
                },
                {"label": "Back Take", "type": "control", "actor": "Georges St-Pierre"},
                {
                    "label": "Escape to Standing",
                    "type": "escape",
                    "actor": "Josh Koscheck",
                },
                {
                    "label": "Takedown",
                    "type": "transition",
                    "actor": "Georges St-Pierre",
                    "successful": True,
                },
            ],
        },
        ("Jake Shields", 2011): {
            "winner": "Georges St-Pierre",
            "method": "Unanimous Decision (50-45, 48-47, 48-47)",
            "events": [
                {
                    "label": "Takedown Attempt",
                    "type": "transition",
                    "actor": "Jake Shields",
                    "successful": False,
                },
                {"label": "Sprawl", "type": "escape", "actor": "Georges St-Pierre"},
                {
                    "label": "Double Leg Takedown",
                    "type": "transition",
                    "actor": "Georges St-Pierre",
                    "successful": True,
                },
                {
                    "label": "Escape to Standing",
                    "type": "escape",
                    "actor": "Jake Shields",
                },
            ],
        },
        ("Carlos Condit", 2012): {
            "winner": "Georges St-Pierre",
            "method": "Unanimous Decision (49-46, 50-45, 50-45)",
            "events": [
                {
                    "label": "Double Leg Takedown",
                    "type": "transition",
                    "actor": "Georges St-Pierre",
                    "successful": True,
                },
                {"label": "Guard", "type": "guard", "actor": "Carlos Condit"},
                {
                    "label": "Armbar Attempt",
                    "type": "submission",
                    "actor": "Carlos Condit",
                    "successful": False,
                },
                {"label": "Half Guard", "type": "guard", "actor": "Carlos Condit"},
                {
                    "label": "Knockdown (head kick)",
                    "type": "transition",
                    "actor": "Carlos Condit",
                    "successful": True,
                },
                {"label": "Mount", "type": "control", "actor": "Carlos Condit"},
                {
                    "label": "Escape to Standing",
                    "type": "escape",
                    "actor": "Georges St-Pierre",
                },
                {
                    "label": "Double Leg Takedown",
                    "type": "transition",
                    "actor": "Georges St-Pierre",
                    "successful": True,
                },
                {"label": "Back Take", "type": "control", "actor": "Georges St-Pierre"},
                {
                    "label": "Escape to Standing",
                    "type": "escape",
                    "actor": "Carlos Condit",
                },
                {
                    "label": "Double Leg Takedown",
                    "type": "transition",
                    "actor": "Georges St-Pierre",
                    "successful": True,
                },
            ],
        },
        ("Nick Diaz", 2013): {
            "winner": "Georges St-Pierre",
            "method": "Unanimous Decision (50-45, 50-45, 50-45)",
            "events": [
                {
                    "label": "Double Leg Takedown",
                    "type": "transition",
                    "actor": "Georges St-Pierre",
                    "successful": True,
                },
                {"label": "Guard", "type": "guard", "actor": "Nick Diaz"},
                {
                    "label": "Leg Lock Attempt",
                    "type": "submission",
                    "actor": "Nick Diaz",
                    "successful": False,
                },
                {"label": "Half Guard", "type": "guard", "actor": "Nick Diaz"},
                {"label": "Guard Pass", "type": "pass", "actor": "Georges St-Pierre"},
                {
                    "label": "Side Control",
                    "type": "control",
                    "actor": "Georges St-Pierre",
                },
                {"label": "Back Take", "type": "control", "actor": "Georges St-Pierre"},
                {"label": "Escape to Standing", "type": "escape", "actor": "Nick Diaz"},
                {
                    "label": "Double Leg Takedown",
                    "type": "transition",
                    "actor": "Georges St-Pierre",
                    "successful": True,
                },
            ],
        },
        ("Johnny Hendricks", 2013): {
            "winner": "Georges St-Pierre",
            "method": "Split Decision (48-47 Hendricks, 48-47 St-Pierre, 48-47 St-Pierre)",
            "events": [
                {
                    "label": "Double Leg Takedown",
                    "type": "transition",
                    "actor": "Georges St-Pierre",
                    "successful": True,
                },
                {
                    "label": "Guillotine Attempt",
                    "type": "submission",
                    "actor": "Johnny Hendricks",
                    "successful": False,
                },
                {
                    "label": "Knockdown (punches)",
                    "type": "transition",
                    "actor": "Johnny Hendricks",
                    "successful": True,
                },
                {"label": "Mount", "type": "control", "actor": "Johnny Hendricks"},
                {
                    "label": "Escape to Standing",
                    "type": "escape",
                    "actor": "Georges St-Pierre",
                },
                {
                    "label": "Double Leg Takedown",
                    "type": "transition",
                    "actor": "Georges St-Pierre",
                    "successful": True,
                },
                {"label": "Back Take", "type": "control", "actor": "Georges St-Pierre"},
                {
                    "label": "Kimura Attempt",
                    "type": "submission",
                    "actor": "Georges St-Pierre",
                    "successful": False,
                },
            ],
        },
    },
    {
        ("Dricus du Plessis", 2025): {
            "winner": "Khamzat Chimaev",
            "method": "Unanimous Decision (50-44, 50-44, 50-44)",
            "events": [
                {
                    "label": "Double Leg Takedown",
                    "type": "transition",
                    "actor": "Khamzat Chimaev",
                    "successful": True,
                },
                {
                    "label": "Mounted Crucifix",
                    "type": "control",
                    "actor": "Khamzat Chimaev",
                },
                {
                    "label": "Arm Triangle Attempt",
                    "type": "submission",
                    "actor": "Khamzat Chimaev",
                    "successful": False,
                },
                {
                    "label": "Sweep / Reversal",
                    "type": "sweep",
                    "actor": "Dricus du Plessis",
                    "successful": True,
                },
                {
                    "label": "Guillotine Attempt",
                    "type": "submission",
                    "actor": "Dricus du Plessis",
                    "successful": False,
                },
                {
                    "label": "Double Leg Takedown",
                    "type": "transition",
                    "actor": "Khamzat Chimaev",
                    "successful": True,
                },
                {
                    "label": "Side Control",
                    "type": "control",
                    "actor": "Khamzat Chimaev",
                },
                {
                    "label": "Double Leg Takedown",
                    "type": "transition",
                    "actor": "Khamzat Chimaev",
                    "successful": True,
                },
                {
                    "label": "Side Control",
                    "type": "control",
                    "actor": "Khamzat Chimaev",
                },
                {
                    "label": "Mounted Crucifix",
                    "type": "control",
                    "actor": "Khamzat Chimaev",
                },
                {
                    "label": "Double Leg Takedown",
                    "type": "transition",
                    "actor": "Khamzat Chimaev",
                    "successful": True,
                },
                {"label": "Referee Stand Up", "type": "reset", "actor": "referee"},
                {
                    "label": "Double Leg Takedown",
                    "type": "transition",
                    "actor": "Khamzat Chimaev",
                    "successful": True,
                },
                {
                    "label": "Americana Attempt",
                    "type": "submission",
                    "actor": "Khamzat Chimaev",
                    "successful": False,
                },
                {
                    "label": "Guillotine Attempt",
                    "type": "submission",
                    "actor": "Dricus du Plessis",
                    "successful": False,
                },
                {"label": "Referee Stand Up", "type": "reset", "actor": "referee"},
                {
                    "label": "Takedown",
                    "type": "transition",
                    "actor": "Khamzat Chimaev",
                    "successful": True,
                },
                {
                    "label": "Back Take Attempt",
                    "type": "control",
                    "actor": "Dricus du Plessis",
                },
            ],
        }
    },
    {
        ("Kevin Holland", 2022): {
            "winner": "Khamzat Chimaev",
            "method": "Submission (D'Arce Choke)",
            "events": [
                {
                    "label": "Double Leg Takedown",
                    "type": "transition",
                    "actor": "Khamzat Chimaev",
                    "successful": True,
                },
                {
                    "label": "D'Arce Choke Attempt",
                    "type": "submission",
                    "actor": "Khamzat Chimaev",
                    "successful": False,
                },
                {
                    "label": "D'Arce Choke",
                    "type": "submission",
                    "actor": "Khamzat Chimaev",
                    "successful": True,
                },
            ],
        },
        ("Dricus du Plessis", 2025): {
            "winner": "Khamzat Chimaev",
            "method": "Unanimous Decision (50-44, 50-44, 50-44)",
            "events": [
                {
                    "label": "Double Leg Takedown",
                    "type": "transition",
                    "actor": "Khamzat Chimaev",
                    "successful": True,
                },
                {
                    "label": "Mounted Crucifix",
                    "type": "control",
                    "actor": "Khamzat Chimaev",
                },
                {
                    "label": "Arm Triangle Attempt",
                    "type": "submission",
                    "actor": "Khamzat Chimaev",
                    "successful": False,
                },
                {
                    "label": "Sweep / Reversal",
                    "type": "sweep",
                    "actor": "Dricus du Plessis",
                    "successful": True,
                },
                {
                    "label": "Guillotine Attempt",
                    "type": "submission",
                    "actor": "Dricus du Plessis",
                    "successful": False,
                },
                {
                    "label": "Double Leg Takedown",
                    "type": "transition",
                    "actor": "Khamzat Chimaev",
                    "successful": True,
                },
                {
                    "label": "Side Control",
                    "type": "control",
                    "actor": "Khamzat Chimaev",
                },
                {
                    "label": "Double Leg Takedown",
                    "type": "transition",
                    "actor": "Khamzat Chimaev",
                    "successful": True,
                },
                {
                    "label": "Side Control",
                    "type": "control",
                    "actor": "Khamzat Chimaev",
                },
                {
                    "label": "Mounted Crucifix",
                    "type": "control",
                    "actor": "Khamzat Chimaev",
                },
                {
                    "label": "Double Leg Takedown",
                    "type": "transition",
                    "actor": "Khamzat Chimaev",
                    "successful": True,
                },
                {"label": "Referee Stand Up", "type": "reset", "actor": "referee"},
                {
                    "label": "Double Leg Takedown",
                    "type": "transition",
                    "actor": "Khamzat Chimaev",
                    "successful": True,
                },
                {
                    "label": "Americana Attempt",
                    "type": "submission",
                    "actor": "Khamzat Chimaev",
                    "successful": False,
                },
                {
                    "label": "Guillotine Attempt",
                    "type": "submission",
                    "actor": "Dricus du Plessis",
                    "successful": False,
                },
                {"label": "Referee Stand Up", "type": "reset", "actor": "referee"},
                {
                    "label": "Takedown",
                    "type": "transition",
                    "actor": "Khamzat Chimaev",
                    "successful": True,
                },
                {
                    "label": "Back Take Attempt",
                    "type": "control",
                    "actor": "Dricus du Plessis",
                },
            ],
        },
    },
    {
        ("Anthony Hernandez", 2025): {
            "winner": "Sean Strickland",
            "method": "TKO (knee and punches)",
            "events": [
                {
                    "label": "Clinch (against fence)",
                    "type": "control",
                    "actor": "Anthony Hernandez",
                },
                {
                    "label": "Escape to Standing",
                    "type": "escape",
                    "actor": "Sean Strickland",
                },
            ],
        },
        ("Robert Whittaker", 2024): {
            "winner": "Khamzat Chimaev",
            "method": "Submission (Face Crank)",
            "events": [
                {
                    "label": "Double Leg Takedown",
                    "type": "transition",
                    "actor": "Khamzat Chimaev",
                    "successful": True,
                },
                {"label": "Back Take", "type": "control", "actor": "Khamzat Chimaev"},
                {
                    "label": "Rear Naked Choke Attempt",
                    "type": "submission",
                    "actor": "Khamzat Chimaev",
                    "successful": False,
                },
                {
                    "label": "Face Crank",
                    "type": "submission",
                    "actor": "Khamzat Chimaev",
                    "successful": True,
                },
            ],
        },
        ("Israel Adesanya", 2023): {
            "winner": "Sean Strickland",
            "method": "Unanimous Decision (49-46, 49-46, 49-46)",
            "events": [
                {
                    "label": "Takedown Attempt",
                    "type": "transition",
                    "actor": "Sean Strickland",
                    "successful": False,
                }
            ],
        },
        ("Kamaru Usman", 2023): {
            "winner": "Khamzat Chimaev",
            "method": "Majority Decision (29-27, 29-27, 28-28)",
            "events": [
                {
                    "label": "Single Leg Takedown",
                    "type": "transition",
                    "actor": "Khamzat Chimaev",
                    "successful": True,
                },
                {"label": "Back Take", "type": "control", "actor": "Khamzat Chimaev"},
                {
                    "label": "Body Triangle Attempt",
                    "type": "control",
                    "actor": "Khamzat Chimaev",
                },
                {
                    "label": "Rear Naked Choke Attempt",
                    "type": "submission",
                    "actor": "Khamzat Chimaev",
                    "successful": False,
                },
                {"label": "Slam Escape", "type": "escape", "actor": "Kamaru Usman"},
                {
                    "label": "Double Leg Takedown",
                    "type": "transition",
                    "actor": "Khamzat Chimaev",
                    "successful": True,
                },
                {"label": "Back Take", "type": "control", "actor": "Khamzat Chimaev"},
                {
                    "label": "Rear Naked Choke Attempt",
                    "type": "submission",
                    "actor": "Khamzat Chimaev",
                    "successful": False,
                },
                {
                    "label": "Escape to Standing",
                    "type": "escape",
                    "actor": "Kamaru Usman",
                },
                {
                    "label": "Double Leg Takedown",
                    "type": "transition",
                    "actor": "Khamzat Chimaev",
                    "successful": True,
                },
                {
                    "label": "Mount Attempt",
                    "type": "control",
                    "actor": "Khamzat Chimaev",
                },
            ],
        },
        ("Abus Magomedov", 2023): {
            "winner": "Sean Strickland",
            "method": "TKO (punches)",
            "events": [
                {
                    "label": "Takedown",
                    "type": "transition",
                    "actor": "Abus Magomedov",
                    "successful": True,
                },
                {
                    "label": "Escape to Standing",
                    "type": "escape",
                    "actor": "Sean Strickland",
                },
            ],
        },
    },
]


