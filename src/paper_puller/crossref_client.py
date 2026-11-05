from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from datetime import date
from datetime import datetime
import logging
import re
import time
from typing import Callable
from typing import Any

import requests

from .fetch_policy import FetchPolicy

CROSSREF_ENDPOINT = "https://api.crossref.org/works"

ProgressCallback = Callable[[int, int, int, int], None]
PageProgressCallback = Callable[[int, int, int, int], None]


@dataclass(slots=True)
class FetchResult:
    articles: list[dict[str, Any]]
    total_records: int


@dataclass(frozen=True, slots=True)
class JournalProfile:
    key: str
    display_name: str
    query_container_title: str
    default_start_year: int
    issn_by_year: list[tuple[range, tuple[str, ...]]]
    work_type: str = "journal-article"
    required_title_substrings: tuple[str, ...] = ()
    search_query: str | None = None
    dblp_stream: str | None = None
    dblp_venue: str | None = None
    # Semantic Scholar venue names (canonical name first, then aliases).
    s2_venues: tuple[str, ...] = ()


TRO_PROFILE = JournalProfile(
    key="tro",
    display_name="IEEE Transactions on Robotics",
    query_container_title="IEEE Transactions on Robotics",
    default_start_year=1985,
    issn_by_year=[
        (range(2004, 3000), ("1552-3098", "1941-0468")),
        (range(1989, 2004), ("1042-296X",)),
        (range(1985, 1989), ("1070-9932", "1558-223X")),
    ],
)

IJRR_PROFILE = JournalProfile(
    key="ijrr",
    display_name="The International Journal of Robotics Research",
    query_container_title="The International Journal of Robotics Research",
    default_start_year=1982,
    issn_by_year=[
        (range(1982, 3000), ("0278-3649", "1741-3176")),
    ],
)

TMECH_PROFILE = JournalProfile(
    key="tmech",
    display_name="IEEE/ASME Transactions on Mechatronics",
    query_container_title="IEEE/ASME Transactions on Mechatronics",
    default_start_year=1996,
    issn_by_year=[
        (range(1996, 3000), ("1083-4435", "1941-014X")),
    ],
)

SCIROBOTICS_PROFILE = JournalProfile(
    key="scirobotics",
    display_name="Science Robotics",
    query_container_title="Science Robotics",
    default_start_year=2016,
    issn_by_year=[
        # 2470-9476 is Science Robotics (AAAS) only. It used to be OR'd here
        # with 2218-6581, which belongs to a different, unrelated journal --
        # MDPI's "Robotics" -- and that silently mislabeled MDPI Robotics
        # articles (DOI prefix 10.3390/robotics...) as Science Robotics
        # (DOI prefix 10.1126/scirobotics...) in every past sync. Do not
        # re-add 2218-6581 here.
        (range(2016, 3000), ("2470-9476",)),
    ],
)

CYBERNETICS_PROFILE = JournalProfile(
    key="cybernetics",
    display_name="IEEE Transactions on Cybernetics (lineage)",
    query_container_title="IEEE Transactions on Cybernetics",
    default_start_year=1960,
    issn_by_year=[
        (range(2013, 3000), ("2168-2267", "2168-2275")),
        (range(1996, 2013), ("1083-4419", "1941-0492")),
        (range(1971, 1996), ("0018-9472", "2168-2909")),
        (range(1968, 1971), ("0536-1540", "2168-2860", "0536-1567", "2168-2887")),
        (range(1965, 1968), ("0096-249X", "2168-2852", "0536-1567", "2168-2887")),
        (range(1963, 1965), ("0096-249X", "2168-2852")),
        (range(1960, 1963), ("0099-4561", "2168-2836")),
    ],
)

RCIM_PROFILE = JournalProfile(
    key="rcim",
    display_name="Robotics and Computer-Integrated Manufacturing",
    query_container_title="Robotics and Computer-Integrated Manufacturing",
    default_start_year=1984,
    issn_by_year=[
        (range(1984, 3000), ("0736-5845",)),
    ],
)

TASE_PROFILE = JournalProfile(
    key="tase",
    display_name="IEEE Transactions on Automation Science and Engineering (lineage)",
    query_container_title="IEEE Transactions on Automation Science and Engineering",
    default_start_year=1985,
    issn_by_year=[
        (range(2004, 3000), ("1545-5955", "1558-3783")),
        (range(1989, 2004), ("1042-296X",)),
        (range(1985, 1989), ("1070-9932", "1558-223X")),
    ],
)

RAL_PROFILE = JournalProfile(
    key="ral",
    display_name="IEEE Robotics and Automation Letters",
    query_container_title="IEEE Robotics and Automation Letters",
    default_start_year=2016,
    issn_by_year=[
        (range(2016, 3000), ("2377-3766", "2377-3774")),
    ],
)

JFR_PROFILE = JournalProfile(
    key="jfr",
    display_name="Journal of Field Robotics",
    query_container_title="Journal of Field Robotics",
    default_start_year=2006,
    issn_by_year=[
        (range(2006, 3000), ("1556-4959", "1556-4967")),
    ],
)

TFR_PROFILE = JournalProfile(
    key="tfr",
    display_name="IEEE Transactions on Field Robotics",
    query_container_title="IEEE Transactions on Field Robotics",
    default_start_year=2024,
    issn_by_year=[
        (range(2024, 3000), ("2997-1101",)),
    ],
)

ICRA_YEARLY_CONTAINER_TITLES: dict[int, str] = {
    2025: "2025 IEEE International Conference on Robotics and Automation (ICRA)",
    2024: "2024 IEEE International Conference on Robotics and Automation (ICRA)",
    2023: "2023 IEEE International Conference on Robotics and Automation (ICRA)",
    2022: "2022 International Conference on Robotics and Automation (ICRA)",
    2021: "2021 IEEE International Conference on Robotics and Automation (ICRA)",
    2020: "2020 IEEE International Conference on Robotics and Automation (ICRA)",
    2019: "2019 International Conference on Robotics and Automation (ICRA)",
    2018: "2018 IEEE International Conference on Robotics and Automation (ICRA)",
    2017: "2017 IEEE International Conference on Robotics and Automation (ICRA)",
    2016: "2016 IEEE International Conference on Robotics and Automation (ICRA)",
    2015: "2015 IEEE International Conference on Robotics and Automation (ICRA)",
    2014: "2014 IEEE International Conference on Robotics and Automation (ICRA)",
    2013: "2013 IEEE International Conference on Robotics and Automation",
    2012: "2012 IEEE International Conference on Robotics and Automation",
    2011: "2011 IEEE International Conference on Robotics and Automation",
    2010: "2010 IEEE International Conference on Robotics and Automation",
    2009: "2009 IEEE International Conference on Robotics and Automation",
    2008: "2008 IEEE International Conference on Robotics and Automation",
    2007: "Proceedings 2007 IEEE International Conference on Robotics and Automation",
    2006: "Proceedings 2006 IEEE International Conference on Robotics and Automation, 2006. ICRA 2006.",
    2005: "Proceedings of the 2005 IEEE International Conference on Robotics and Automation",
    2004: "IEEE International Conference on Robotics and Automation, 2004. Proceedings. ICRA '04. 2004",
    2003: "2003 IEEE International Conference on Robotics and Automation (Cat. No.03CH37422)",
    2002: "Proceedings 2002 IEEE International Conference on Robotics and Automation (Cat. No.02CH37292)",
    2001: "Proceedings 2001 ICRA. IEEE International Conference on Robotics and Automation (Cat. No.01CH37164)",
    2000: "Proceedings 2000 ICRA. Millennium Conference. IEEE International Conference on Robotics and Automation. Symposia Proceedings (Cat. No.00CH37065)",
    1999: "Proceedings 1999 IEEE International Conference on Robotics and Automation (Cat. No.99CH36288C)",
    1998: "Proceedings. 1998 IEEE International Conference on Robotics and Automation (Cat. No.98CH36146)",
    1997: "Proceedings of International Conference on Robotics and Automation",
    1996: "Proceedings of IEEE International Conference on Robotics and Automation",
    1995: "Proceedings of 1995 IEEE International Conference on Robotics and Automation",
    1994: "Proceedings of the 1994 IEEE International Conference on Robotics and Automation",
    1993: "[1993] Proceedings IEEE International Conference on Robotics and Automation",
    1992: "Proceedings 1992 IEEE International Conference on Robotics and Automation",
    1991: "Proceedings. 1991 IEEE International Conference on Robotics and Automation",
    1990: "Proceedings., IEEE International Conference on Robotics and Automation",
    1989: "Proceedings, 1989 International Conference on Robotics and Automation",
    1988: "Proceedings. 1988 IEEE International Conference on Robotics and Automation",
    1987: "Proceedings. 1987 IEEE International Conference on Robotics and Automation",
    1986: "Proceedings. 1986 IEEE International Conference on Robotics and Automation",
    1985: "Proceedings. 1985 IEEE International Conference on Robotics and Automation",
    1984: "Proceedings. 1984 IEEE International Conference on Robotics and Automation",
}

ICRA_PROFILE = JournalProfile(
    key="icra",
    display_name="IEEE International Conference on Robotics and Automation",
    query_container_title="IEEE International Conference on Robotics and Automation",
    default_start_year=1984,
    issn_by_year=[],
    work_type="proceedings-article",
    required_title_substrings=("conference on robotics and automation",),
    search_query="10.1109/ICRA",
    s2_venues=("IEEE International Conference on Robotics and Automation", "ICRA"),
)

IROS_YEARLY_CONTAINER_TITLES: dict[int, str] = {
    2025: "2025 IEEE/RSJ International Conference on Intelligent Robots and Systems (IROS)",
    2024: "2024 IEEE/RSJ International Conference on Intelligent Robots and Systems (IROS)",
    2023: "2023 IEEE/RSJ International Conference on Intelligent Robots and Systems (IROS)",
    2022: "2022 IEEE/RSJ International Conference on Intelligent Robots and Systems (IROS)",
    2021: "2021 IEEE/RSJ International Conference on Intelligent Robots and Systems (IROS)",
    2020: "2020 IEEE/RSJ International Conference on Intelligent Robots and Systems (IROS)",
    2019: "2019 IEEE/RSJ International Conference on Intelligent Robots and Systems (IROS)",
    2018: "2018 IEEE/RSJ International Conference on Intelligent Robots and Systems (IROS)",
    2017: "2017 IEEE/RSJ International Conference on Intelligent Robots and Systems (IROS)",
    2016: "2016 IEEE/RSJ International Conference on Intelligent Robots and Systems (IROS)",
    2015: "2015 IEEE/RSJ International Conference on Intelligent Robots and Systems (IROS)",
    2014: "2014 IEEE/RSJ International Conference on Intelligent Robots and Systems",
    2013: "2013 IEEE/RSJ International Conference on Intelligent Robots and Systems",
    2012: "2012 IEEE/RSJ International Conference on Intelligent Robots and Systems",
    2011: "2011 IEEE/RSJ International Conference on Intelligent Robots and Systems",
    2010: "2010 IEEE/RSJ International Conference on Intelligent Robots and Systems",
    2009: "2009 IEEE/RSJ International Conference on Intelligent Robots and Systems",
    2008: "2008 IEEE/RSJ International Conference on Intelligent Robots and Systems",
    2007: "2007 IEEE/RSJ International Conference on Intelligent Robots and Systems",
    2006: "2006 IEEE/RSJ International Conference on Intelligent Robots and Systems",
    2005: "2005 IEEE/RSJ International Conference on Intelligent Robots and Systems",
    2004: "2004 IEEE/RSJ International Conference on Intelligent Robots and Systems (IROS) (IEEE Cat. No.04CH37566)",
    2003: "Proceedings 2003 IEEE/RSJ International Conference on Intelligent Robots and Systems (IROS 2003) (Cat. No.03CH37453)",
    2002: "IEEE/RSJ International Conference on Intelligent Robots and Systems",
    2001: "Proceedings 2001 IEEE/RSJ International Conference on Intelligent Robots and Systems. Expanding the Societal Role of Robotics in the the Next Millennium (Cat. No.01CH37180)",
    2000: "Proceedings. 2000 IEEE/RSJ International Conference on Intelligent Robots and Systems (IROS 2000) (Cat. No.00CH37113)",
    1999: "Proceedings 1999 IEEE/RSJ International Conference on Intelligent Robots and Systems. Human and Environment Friendly Robots with High Intelligence and Emotional Quotients (Cat. No.99CH36289)",
    1998: "Proceedings. 1998 IEEE/RSJ International Conference on Intelligent Robots and Systems. Innovations in Theory, Practice and Applications (Cat. No.98CH36190)",
    1997: "Proceedings of the 1997 IEEE/RSJ International Conference on Intelligent Robot and Systems. Innovative Robotics for Real-World Applications. IROS '97",
    1996: "Proceedings of IEEE/RSJ International Conference on Intelligent Robots and Systems. IROS '96",
    1995: "Proceedings 1995 IEEE/RSJ International Conference on Intelligent Robots and Systems. Human Robot Interaction and Cooperative Robots",
    1994: "Proceedings of IEEE/RSJ International Conference on Intelligent Robots and Systems (IROS'94)",
    1993: "Proceedings of 1993 IEEE/RSJ International Conference on Intelligent Robots and Systems (IROS '93)",
    1992: "Proceedings of the IEEE/RSJ International Conference on Intelligent Robots and Systems",
    1991: "Proceedings IROS '91:IEEE/RSJ International Workshop on Intelligent Robots and Systems '91",
    1990: "IEEE International Workshop on Intelligent Robots and Systems, Towards a New Frontier of Applications",
    1989: "Proceedings. IEEE/RSJ International Workshop on Intelligent Robots and Systems '. (IROS '89) 'The Autonomous Mobile Robots and Its Applications",
    1988: "IEEE International Workshop on Intelligent Robots",
}

IROS_PROFILE = JournalProfile(
    key="iros",
    display_name="IEEE/RSJ International Conference on Intelligent Robots and Systems",
    query_container_title="IEEE/RSJ International Conference on Intelligent Robots and Systems",
    default_start_year=1988,
    issn_by_year=[],
    work_type="proceedings-article",
    required_title_substrings=("intelligent robot",),
    search_query="10.1109/IROS",
    s2_venues=("IEEE/RSJ International Conference on Intelligent Robots and Systems", "IROS"),
)

ICML_PROFILE = JournalProfile(
    key="icml",
    display_name="International Conference on Machine Learning",
    query_container_title="International Conference on Machine Learning",
    default_start_year=1980,
    issn_by_year=[],
    work_type="proceedings-article",
    dblp_stream="conf/icml",
    dblp_venue="ICML",
    s2_venues=("International Conference on Machine Learning", "ICML"),
)

AAAI_PROFILE = JournalProfile(
    key="aaai",
    display_name="AAAI Conference on Artificial Intelligence",
    query_container_title="AAAI Conference on Artificial Intelligence",
    default_start_year=1980,
    issn_by_year=[],
    work_type="proceedings-article",
    dblp_stream="conf/aaai",
    dblp_venue="AAAI",
    s2_venues=("AAAI Conference on Artificial Intelligence", "AAAI"),
)

NEURIPS_PROFILE = JournalProfile(
    key="neurips",
    display_name="Conference on Neural Information Processing Systems",
    query_container_title="Conference on Neural Information Processing Systems",
    default_start_year=1987,
    issn_by_year=[],
    work_type="proceedings-article",
    dblp_stream="conf/nips",
    dblp_venue="NeurIPS",
    s2_venues=("Neural Information Processing Systems", "NeurIPS", "NIPS"),
)

ICLR_PROFILE = JournalProfile(
    key="iclr",
    display_name="International Conference on Learning Representations",
    query_container_title="International Conference on Learning Representations",
    default_start_year=2013,
    issn_by_year=[],
    work_type="proceedings-article",
    dblp_stream="conf/iclr",
    dblp_venue="ICLR",
    s2_venues=("International Conference on Learning Representations", "ICLR"),
)

CORL_PROFILE = JournalProfile(
    key="corl",
    display_name="Conference on Robot Learning",
    query_container_title="Conference on Robot Learning",
    default_start_year=2017,
    issn_by_year=[],
    work_type="proceedings-article",
    dblp_stream="conf/corl",
    dblp_venue="CoRL",
    s2_venues=("Conference on Robot Learning", "CoRL"),
)

AAMAS_PROFILE = JournalProfile(
    key="aamas",
    display_name="International Conference on Autonomous Agents and Multiagent Systems",
    query_container_title="International Conference on Autonomous Agents and Multiagent Systems",
    default_start_year=2002,
    issn_by_year=[],
    work_type="proceedings-article",
    dblp_stream="conf/ifaamas",
    dblp_venue="AAMAS",
    s2_venues=("Adaptive Agents and Multi-Agent Systems", "AAMAS"),
)

PROFILES_BY_KEY: dict[str, JournalProfile] = {
    TRO_PROFILE.key: TRO_PROFILE,
    IJRR_PROFILE.key: IJRR_PROFILE,
    TMECH_PROFILE.key: TMECH_PROFILE,
    SCIROBOTICS_PROFILE.key: SCIROBOTICS_PROFILE,
    CYBERNETICS_PROFILE.key: CYBERNETICS_PROFILE,
    RCIM_PROFILE.key: RCIM_PROFILE,
    TASE_PROFILE.key: TASE_PROFILE,
    RAL_PROFILE.key: RAL_PROFILE,
    JFR_PROFILE.key: JFR_PROFILE,
    TFR_PROFILE.key: TFR_PROFILE,
    ICRA_PROFILE.key: ICRA_PROFILE,
    IROS_PROFILE.key: IROS_PROFILE,
    ICML_PROFILE.key: ICML_PROFILE,
    AAAI_PROFILE.key: AAAI_PROFILE,
    NEURIPS_PROFILE.key: NEURIPS_PROFILE,
    ICLR_PROFILE.key: ICLR_PROFILE,
    CORL_PROFILE.key: CORL_PROFILE,
    AAMAS_PROFILE.key: AAMAS_PROFILE,
}


class CrossrefClient:
    def __init__(
        self,
        *,
        mailto: str | None = None,
        timeout_seconds: float = 20.0,
        retry_count: int = 3,
        retry_delay_seconds: float = 2.0,
    ) -> None:
        self.mailto = (mailto or "").strip() or None
        self.timeout_seconds = timeout_seconds
        self.retry_count = retry_count
        self.retry_delay_seconds = retry_delay_seconds
        self.session = requests.Session()
        self.policy = FetchPolicy()

    def fetch_tro_history(
        self,
        *,
        profile: JournalProfile = TRO_PROFILE,
        page_size: int = 1000,
        start_year: int | None = None,
        start_date: date | None = None,
        max_pages: int | None = None,
        end_year: int | None = None,
        progress_callback: ProgressCallback | None = None,
        page_progress_callback: PageProgressCallback | None = None,
    ) -> FetchResult:
        from_year = int(start_year) if start_year is not None else profile.default_start_year
        if start_date is not None:
            from_year = start_date.year
        to_year = int(end_year) if end_year is not None else datetime.now().year
        if from_year > to_year:
            raise RuntimeError(f"Invalid year range: start_year={from_year} is after end_year={to_year}")

        total_years = (to_year - from_year) + 1
        consumed_pages = 0
        total_records = 0
        all_articles: list[dict[str, Any]] = []

        # Journal ISSNs identify the venue exactly. Combine adjacent years with
        # the same identifiers instead of making one cursor query per year.
        ranges: list[tuple[int, int]] = []
        for year in range(from_year, to_year + 1):
            if (ranges and _issn_for_year(profile, year)
                    and _issn_for_year(profile, year) == _issn_for_year(profile, ranges[-1][0])
                    and not profile.search_query and not profile.required_title_substrings):
                ranges[-1] = (ranges[-1][0], year)
            else:
                ranges.append((year, year))

        # Crossref advertises x-concurrency-limit: 3 for the polite pool (with
        # mailto) and 1 otherwise; independent year ranges run in parallel.
        workers = min(3 if self.mailto else 1, len(ranges))
        if max_pages is None and workers > 1:
            def fetch(year_range: tuple[int, int]):
                year, last_year = year_range
                return self._fetch_year(
                    profile=profile, year=year, until_year=last_year,
                    from_date=start_date if year == from_year else None,
                    page_size=page_size, max_pages=None,
                    page_progress_callback=page_progress_callback,
                )
            with ThreadPoolExecutor(max_workers=workers) as pool:
                for done, ((_, last_year), (year_articles, year_total, _pages)) in enumerate(
                        zip(ranges, pool.map(fetch, ranges)), 1):
                    all_articles.extend(year_articles)
                    total_records += year_total
                    if progress_callback is not None:
                        progress_callback(last_year - from_year + 1, total_years, last_year, len(all_articles))
            return FetchResult(articles=all_articles, total_records=total_records)

        for year, last_year in ranges:
            remaining_pages = None if max_pages is None else max_pages - consumed_pages
            if remaining_pages is not None and remaining_pages <= 0:
                break

            year_articles, year_total, year_pages = self._fetch_year(
                profile=profile,
                year=year,
                until_year=last_year,
                from_date=start_date if year == from_year else None,
                page_size=page_size,
                max_pages=remaining_pages,
                page_progress_callback=page_progress_callback,
            )
            all_articles.extend(year_articles)
            total_records += year_total
            consumed_pages += year_pages

            if progress_callback is not None:
                progress_callback(last_year - from_year + 1, total_years, last_year, len(all_articles))

        return FetchResult(articles=all_articles, total_records=total_records)

    def _fetch_year(
        self, *, profile: JournalProfile, year: int, from_date: date | None,
        page_size: int, max_pages: int | None,
        page_progress_callback: PageProgressCallback | None,
        until_year: int | None = None,
    ) -> tuple[list[dict[str, Any]], int, int]:
        params: dict[str, str | int] = {
            "filter": self._build_filter(profile=profile, year=year, from_date=from_date, until_year=until_year),
            "rows": min(page_size, 1000),
            "cursor": "*",
            "select": "DOI,title,container-title,author,published-print,published-online,created,issued,volume,issue,page,URL,abstract",
        }
        query = _search_query_for_year(profile, year)
        if query:
            params["query"] = query
        elif not _issn_for_year(profile, year):
            params["query.container-title"] = profile.query_container_title
        checkpoint = {"complete-range": dict(params)}
        if max_pages is None:
            cached = self.policy.load(checkpoint, ttl=86400)
            if cached is not None:
                return cached["articles"], cached["total"], cached["pages"]
        articles, total, pages = self._fetch_pages(
            params, profile=profile, year=year, max_pages=max_pages,
            progress=page_progress_callback,
        )
        if (profile.key in {"icra", "iros"} and not articles
                and (max_pages is None or pages < max_pages)):
            params.pop("query", None)
            params["query.container-title"] = _fallback_query_container_title_for_year(profile, year)
            if profile.key == "iros":
                # Some legacy IROS entries lack publication dates.
                params["filter"] = f"type:{profile.work_type}"
            params["cursor"] = "*"
            extra, fallback_total, fallback_pages = self._fetch_pages(
                params, profile=profile, year=year,
                max_pages=None if max_pages is None else max_pages - pages,
                progress=page_progress_callback, match_doi_year=profile.key == "iros",
            )
            articles.extend(extra)
            total = max(total, fallback_total)
            pages += fallback_pages
        if max_pages is None:
            self.policy.save(checkpoint, {"articles": articles, "total": total, "pages": pages})
        return articles, total, pages

    def _fetch_pages(
        self, params: dict[str, str | int], *, profile: JournalProfile, year: int,
        max_pages: int | None, progress: PageProgressCallback | None,
        match_doi_year: bool = False,
    ) -> tuple[list[dict[str, Any]], int, int]:
        articles: list[dict[str, Any]] = []
        raw_count = pages = total = 0
        while True:
            payload = self._request(params)
            message = payload.get("message")
            if not isinstance(message, dict):
                raise RuntimeError("Unexpected Crossref payload: missing message object")
            raw_items = message.get("items", [])
            if not isinstance(raw_items, list):
                raise RuntimeError("Unexpected Crossref payload: items is not a list")
            total = max(total, int(message.get("total-results") or 0))
            pages += 1
            raw_count += len(raw_items)
            articles.extend(item for item in raw_items
                            if isinstance(item, dict) and _matches_profile(item, profile)
                            and (not match_doi_year or _doi_year(item) == year))
            if progress:
                progress(year, pages, len(articles), total)
            if max_pages is not None and pages >= max_pages:
                break
            if raw_count >= total:
                break
            next_cursor = message.get("next-cursor")
            if len(raw_items) < int(params["rows"]) or not next_cursor or next_cursor == params["cursor"]:
                self.policy.clear()
                raise RuntimeError(f"Incomplete Crossref query for {profile.key}: {raw_count}/{total} raw records; retry later")
            params = {**params, "cursor": str(next_cursor)}
        return articles, total, pages

    def _build_filter(self, *, profile: JournalProfile, year: int, from_date: date | None = None, until_year: int | None = None) -> str:
        issn_filters = [f"issn:{issn}" for issn in _issn_for_year(profile, year)]
        start = from_date.isoformat() if from_date is not None else f"{year}-01-01"
        filters = [
            f"type:{profile.work_type}",
            f"from-pub-date:{start}",
            f"until-pub-date:{until_year or year}-12-31",
            *issn_filters,
        ]
        return ",".join(filters)

    def _request(self, params: dict[str, str | int]) -> dict[str, Any]:
        cached = self.policy.load(params)
        if cached is not None:
            return cached
        headers = {"User-Agent": "paper-puller/0.1.0"}
        if self.mailto:
            headers["User-Agent"] = f"paper-puller/0.1.0 (mailto:{self.mailto})"

        last_error: Exception | None = None
        for attempt in range(1, self.retry_count + 1):
            try:
                self.policy.wait()
                response = self.session.get(
                    CROSSREF_ENDPOINT,
                    params=params,
                    timeout=self.policy.timeout(self.timeout_seconds),
                    headers=headers,
                )
                self.policy.observe(response.headers)
                if response.status_code in {429, 500, 502, 503, 504}:
                    raise requests.HTTPError(
                        f"Crossref returned HTTP {response.status_code}",
                        response=response,
                    )
                response.raise_for_status()
                payload = response.json()
                if not isinstance(payload, dict):
                    raise RuntimeError("Unexpected Crossref payload shape")
                self.policy.save(params, payload)
                return payload
            except (requests.RequestException, ValueError, RuntimeError) as exc:
                last_error = exc
                if (isinstance(exc, requests.HTTPError) and exc.response is not None
                        and 400 <= exc.response.status_code < 500 and exc.response.status_code != 429):
                    raise RuntimeError(f"Crossref rejected the request (HTTP {exc.response.status_code})") from exc
                if attempt >= self.retry_count:
                    break
                sleep_for = self.retry_delay_seconds * attempt
                logging.warning("Crossref request failed on attempt %s/%s: %s", attempt, self.retry_count, exc)
                self.policy.sleep(sleep_for)

        raise RuntimeError(f"Crossref request failed after {self.retry_count} attempts: {last_error}")


def _issn_for_year(profile: JournalProfile, year: int) -> tuple[str, ...]:
    if not profile.issn_by_year:
        return ()
    for year_range, issns in profile.issn_by_year:
        if year in year_range:
            return issns
    return profile.issn_by_year[-1][1]


def _search_query_for_year(profile: JournalProfile, year: int) -> str | None:
    if profile.key == "icra":
        # Legacy ICRA entries are often minted under the ROBOT DOI stem.
        return "10.1109/ROBOT" if year <= 2000 else "10.1109/ICRA"
    if profile.key == "iros":
        return "10.1109/IROS"
    return profile.search_query


def _fallback_query_container_title_for_year(profile: JournalProfile, year: int) -> str:
    if profile.key == "icra":
        return ICRA_YEARLY_CONTAINER_TITLES.get(year) or profile.query_container_title
    if profile.key == "iros":
        return IROS_YEARLY_CONTAINER_TITLES.get(year) or profile.query_container_title
    return profile.query_container_title


def _matches_icra(item: dict[str, Any]) -> bool:
    title = _container_title(item)
    doi = _doi(item)
    return (
        "conference on robotics and automation" in title
        or "/icra." in doi
        or "/robot." in doi
    )


def _matches_iros(item: dict[str, Any]) -> bool:
    title = _container_title(item)
    doi = _doi(item)
    return (
        ("intelligent robot" in title and "system" in title)
        or "/iros." in doi
    )


def _doi(item: dict[str, Any]) -> str:
    doi = item.get("DOI") or item.get("doi")
    return str(doi or "").strip().lower()


def _doi_year(item: dict[str, Any]) -> int | None:
    doi = _doi(item)
    match = re.search(r"/(?:iros|icra|robot)\.(\d{4})\.", doi)
    if not match:
        return None
    return int(match.group(1))


def _matches_profile(item: dict[str, Any], profile: JournalProfile) -> bool:
    if profile.key == "icra":
        return _matches_icra(item)
    if profile.key == "iros":
        return _matches_iros(item)
    if not profile.required_title_substrings:
        return True
    title = _container_title(item)
    return all(sub in title for sub in profile.required_title_substrings)


def _container_title(item: dict[str, Any]) -> str:
    container = item.get("container-title")
    if isinstance(container, list) and container:
        return str(container[0] or "").strip().lower()
    if isinstance(container, str):
        return container.strip().lower()
    return ""
