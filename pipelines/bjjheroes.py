"""
BJJ Heroes scraper pipeline — ~400 athlete profiles (belt, team, lineage, achievements).

Port of bjagrelli/bjj_data_scrapping (concept). Only non-Kaggle pipeline.
"""

from __future__ import annotations

import asyncio
import logging
import urllib.error
import urllib.request
from pathlib import Path

import aiohttp
import nest_asyncio
import pandas as pd
from bs4 import BeautifulSoup

from pipelines.etl import RAW_DIR, Pipeline
from pipelines.registry import DATASETS

logger = logging.getLogger(__name__)

USER_AGENT = "GrapplingArcAnalytics/1.0 (research; dev@grapplingarc.app)"
CONCURRENCY = 4
BATCH_DELAY = 1.0
RETRIES = 3
LIST_URL = "https://www.bjjheroes.com/a-z-bjj-fighters-list"
BASE_URL = "https://www.bjjheroes.com"


class BJJHeroesPipeline(Pipeline):
    spec = DATASETS["bjjheroes"]

    def download(self, force: bool = False) -> Path:
        cache_dir = RAW_DIR / self.spec.key
        cache_csv = cache_dir / "bjjheroes.csv"

        if cache_csv.exists() and not force:
            logger.info("%s: cached at %s", self.spec.key, cache_csv)
            return cache_csv

        cache_dir.mkdir(parents=True, exist_ok=True)

        cached_html = list(cache_dir.glob("*.html"))

        if cached_html and not force:
            logger.info("%s: re-parsing %d cached HTML files", self.spec.key, len(cached_html))
            rows = []
            for html_path in cached_html:
                html = html_path.read_text(encoding="utf-8")
                row = self._parse_fighter_page(html)
                rows.append(row)
        else:
            self._check_robots_txt()
            logger.info("%s: scraping BJJ Heroes…", self.spec.key)
            fighter_urls = self._fetch_fighter_urls()

            # Applied lazily (not at import) so importing this module never patches the
            # global event loop — that breaks Starlette TestClient in unrelated tests.
            nest_asyncio.apply()
            pages = asyncio.run(self._fetch_pages(fighter_urls))

            rows = []
            for url, html in pages:
                slug = url.rstrip("/").split("/")[-1]
                html_path = cache_dir / f"{slug}.html"
                html_path.write_text(html, encoding="utf-8")
                row = self._parse_fighter_page(html)
                rows.append(row)

        df = pd.DataFrame(rows)
        df.to_csv(cache_csv, index=False)
        logger.info("%s: saved %d rows → %s", self.spec.key, len(df), cache_csv)
        return cache_csv

    def clean(self, df: pd.DataFrame) -> pd.DataFrame:
        df = df.dropna(subset=["fighter_name"])
        df["fighter_name"] = df["fighter_name"].astype(str).str.strip()
        for col in ["nickname", "belt", "team", "weight_class", "achievements_raw"]:
            if col in df.columns:
                df[col] = df[col].astype(str).str.strip().replace("nan", "", regex=False)
        return df

    def normalize(self, df: pd.DataFrame) -> pd.DataFrame:
        return df.rename(columns={
            "fighter_name": "fighter_name",
            "nickname": "nickname",
            "belt": "belt",
            "team": "team",
            "weight_class": "weight_class",
            "achievements_raw": "achievements_raw",
        })

    # ── Scraping helpers ──

    @staticmethod
    def _check_robots_txt() -> None:
        robots_url = "https://www.bjjheroes.com/robots.txt"
        try:
            req = urllib.request.Request(robots_url, headers={"User-Agent": USER_AGENT})
            with urllib.request.urlopen(req, timeout=10) as resp:
                body = resp.read().decode().lower()
                for line in body.splitlines():
                    line = line.strip()
                    if line.startswith("disallow:") and "/" in line:
                        path = line.split(":", 1)[1].strip()
                        if path == "/":
                            raise RuntimeError(
                                "Scraping disallowed by bjjheroes.com/robots.txt"
                            )
        except (urllib.error.URLError, OSError):
            logger.warning("Could not fetch robots.txt — proceeding anyway")

    @staticmethod
    def _fetch_fighter_urls() -> list[str]:
        req = urllib.request.Request(LIST_URL, headers={"User-Agent": USER_AGENT})
        with urllib.request.urlopen(req, timeout=30) as resp:
            html = resp.read().decode()
        soup = BeautifulSoup(html, "html.parser")
        urls: list[str] = []
        for a in soup.select("a[href*='/bjj-fighters/']"):
            href = str(a.get("href", ""))
            full_url = href if href.startswith("http") else BASE_URL + href
            if full_url not in urls:
                urls.append(full_url)
        return urls

    @staticmethod
    async def _fetch_pages(urls: list[str]) -> list[tuple[str, str]]:
        connector = aiohttp.TCPConnector(limit=CONCURRENCY)
        headers = {"User-Agent": USER_AGENT}
        async with aiohttp.ClientSession(connector=connector, headers=headers) as session:
            results: list[tuple[str, str]] = []
            for i in range(0, len(urls), CONCURRENCY):
                batch = urls[i:i + CONCURRENCY]
                tasks = [BJJHeroesPipeline._fetch_with_retry(session, url) for url in batch]
                htmls = await asyncio.gather(*tasks, return_exceptions=True)
                for url, html in zip(batch, htmls):
                    if isinstance(html, BaseException):
                        logger.warning("Failed %s: %s", url, html)
                        continue
                    results.append((url, html))
                if i + CONCURRENCY < len(urls):
                    await asyncio.sleep(BATCH_DELAY)
            return results

    @staticmethod
    async def _fetch_with_retry(session: aiohttp.ClientSession, url: str) -> str:
        for attempt in range(RETRIES):
            try:
                async with session.get(url) as resp:
                    resp.raise_for_status()
                    return await asyncio.wait_for(resp.text(), timeout=30)
            except (TimeoutError, aiohttp.ClientError) as e:
                if attempt == RETRIES - 1:
                    raise
                wait = 2**attempt
                logger.debug("Retry %s in %ds (%s)", url, wait, e)
                await asyncio.sleep(wait)
        raise RuntimeError(f"Failed after {RETRIES} retries: {url}")

    @staticmethod
    def _parse_fighter_page(html: str) -> dict[str, str]:
        soup = BeautifulSoup(html, "html.parser")

        name_el = soup.select_one(".fighter-name h1")
        name = name_el.get_text(strip=True) if name_el else ""

        info = soup.select_one(".fighter-info")
        nickname = belt = team = weight_class = ""
        if info:
            for label_span in info.find_all("span", class_="label"):
                key = label_span.get_text(strip=True).rstrip(":")
                sibling = label_span.next_sibling
                val = str(sibling).strip() if sibling else ""
                if key == "Nickname":
                    nickname = val
                elif key == "Belt":
                    belt = val
                elif key == "Team":
                    team = val
                elif key == "Weight":
                    weight_class = val

        ach_div = soup.select_one(".achievements")
        achievements = ""
        if ach_div:
            lis = ach_div.find_all("li")
            achievements = " | ".join(li.get_text(strip=True) for li in lis)

        return {
            "fighter_name": name,
            "nickname": nickname,
            "belt": belt,
            "team": team,
            "weight_class": weight_class,
            "achievements_raw": achievements,
        }
