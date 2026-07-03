"""Verify found YouTube URLs by fetching their titles."""
from dotenv import load_dotenv
load_dotenv()
from harvest.transcripts import get_video_title

urls = {
    "2024NCAA": "https://www.youtube.com/watch?v=IihhnVez38I",
    "2025NCAA": "https://www.youtube.com/watch?v=epXN_WwO6cY",
    "2026NCAA": "https://www.youtube.com/watch?v=MlancZWswSk",
    "ADCC2022+99kg": "https://www.youtube.com/watch?v=fLmrLjgDbJI",
    "ADCC2022-88kg": "https://www.youtube.com/watch?v=AnlqZsD6g6U",
    "ADCC2022-99kg": "https://www.youtube.com/watch?v=MyUe7tIEWHc",
    "ADCC2022-ABS": "https://www.youtube.com/watch?v=JLRp1Rz-pnI",
    "ADCC2022-Finals": "https://www.youtube.com/watch?v=KnMX2tm8I7g",
    "ADCC2022Women": "https://www.youtube.com/watch?v=ZeLn9aMFFVI",
    "ADCC2024+99kg": "https://www.youtube.com/watch?v=hF7HSiZveoA",
    "ADCC2024-ABS": "https://www.youtube.com/watch?v=oJQ5efUF4oo",
    "ADCCTrials2022SouthAmericaFinals": "https://www.youtube.com/watch?v=2BNHJfxRUeQ",
    "ADCCTrials2023EastCoastFinals": "https://www.youtube.com/watch?v=loMqIK8s9a4",
    "ADCCTrials2023EastCoastSemifinals": "https://www.youtube.com/watch?v=WCz5jauTn4M",
    "ADCCTrials2024WestCoastFinals": "https://www.youtube.com/watch?v=_MfGk9NMy0U",
    "CJI": "https://www.youtube.com/watch?v=fvVcmgepLaM",
    "IBJJF2023-Worlds-BlackBeltFinals": "https://www.youtube.com/watch?v=tpGIb1DF9j8",
    "IBJJF2025top10": "https://www.youtube.com/watch?v=gEwAeyf0yNY",
    "Polaris37": "https://www.youtube.com/watch?v=v8eHYSkY7l4",
    "SpyderKingOfKings": "https://www.youtube.com/watch?v=EcEAwDXaXyo",
    "UFC": "https://www.youtube.com/watch?v=ganRmKAjTfs",
    "UFC325": "https://www.youtube.com/watch?v=PqTBCISwu7k",
    "WNO20": "https://www.youtube.com/watch?v=7AyoRIBXvUc",
    "WNO22": "https://www.youtube.com/watch?v=ALz8qbGLwio",
    "WNO24": "https://www.youtube.com/watch?v=MNknlkBbQpI",
    "WNO31": "https://www.youtube.com/watch?v=q7o5aoyy5HM",
    "khabib": "https://www.youtube.com/watch?v=A4i6pLRF9iY",
}

for name, url in sorted(urls.items()):
    title = get_video_title(url)
    if title:
        ok = "✓" if any(kw.lower() in title.lower() for kw in name.lower().replace("-","").replace("+","").split()) else "?"
        print(f"{ok} {name}: {title[:90]}")
    else:
        print(f"✗ {name}: (title fetch failed)")
