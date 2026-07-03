"""Fix wrong YouTube URLs with more specific search queries."""
from dotenv import load_dotenv
load_dotenv()
from harvest.transcripts import search_video_id, get_video_title

queries = {
    "2024NCAA": "Full replay 2024 NCAA wrestling championships",
    "ADCC2022+99kg": "Relive the Entire +99kg Bracket From The 2022 ADCC",
    "ADCC2022-88kg": "Supercut The Entire -88kg ADCC Bracket From 2022",
    "ADCC2022-99kg": "Supercut Watch The Full ADCC -99kg Bracket From 2022",
    "ADCC2022-ABS": "Supercut Gear Up For ADCC With The Entire 2022 Absolute",
    "ADCC2022-Finals": "ADCC 2022 finals superfight Gordon Ryan vs Andre Galvao",
    "ADCC2022Women": "Supercut The Entire 2022 ADCC World Championships Women",
    "ADCC2024-ABS": "ADCC 2024 absolute division full bracket supercut",
    "ADCCTrials2022SouthAmericaFinals": "ADCC South America Trials 2022 finals full replay",
    "IBJJF2023-Worlds-BlackBeltFinals": "IBJJF 2023 world championship black belt finals full replay",
    "IBJJF2025top10": "IBJJF 2025 top 10 black belt finals",
    "Polaris37": "Polaris 37 full event grappling",
    "SpyderKingOfKings": "Spyder King of Kings full event grappling",
    "UFC": "UFC free fight marathon compilation grappling",
    "WNO22": "Who's Number One 22 full event WNO",
}

for name, q in sorted(queries.items()):
    print(f"[{name}] {q[:60]}...", end=" ", flush=True)
    vid = search_video_id(q)
    if vid:
        url = f"https://www.youtube.com/watch?v={vid}"
        title = get_video_title(url) or "(title fetch failed)"
        print(f"OK -> {title[:80]}")
        print(f"     {url}")
    else:
        print("NOT FOUND")
