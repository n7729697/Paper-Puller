"""Per-year candidate extractor for the annual robotics report.

Reads the 9 BibTeX histories and, for each year 1984-2026, writes a JSON file listing
candidate papers per venue with abstract snippets so the LLM can apply the user's
quality-filter rules (editorials, CFPs, method-less reviews) without re-grepping.

Selection priority per (venue, year):
  1. Canonical-method hits (HIGH_IMPACT_PATTERNS from analyze_bib).
  2. Top up to N with entries that have substantive abstracts (>=120 chars).
  3. If still short, include everything available for that venue/year.

Output: reports/data/annual_candidates/YYYY.json, one file per year, stdlib only.
"""
from __future__ import annotations

import json
import re
import sys
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
OUT_DIR = ROOT / "reports" / "data" / "annual_candidates"
OUT_DIR.mkdir(parents=True, exist_ok=True)

VENUES = {
    "icra_history.bib": "ICRA",
    "iros_history.bib": "IROS",
    "ijrr_history.bib": "IJRR",
    "tro_history.bib": "T-RO",
    "tmech_history.bib": "T-Mech",
    "tase_history.bib": "T-ASE",
    "ral_history.bib": "RA-L",
    "rcim_history.bib": "RCIM",
    "cybernetics_history.bib": "T-Cybernetics",
}

ENTRY_RE = re.compile(r"^@article\{", re.MULTILINE)
FIELD_RES = {
    "title": re.compile(r"\n\s*title\s*=\s*\{(.*?)\},\n", re.DOTALL),
    "year": re.compile(r"\n\s*year\s*=\s*\{(\d{4})\}"),
    "abstract": re.compile(r"\n\s*abstract\s*=\s*\{(.*?)\},\n", re.DOTALL),
    "doi": re.compile(r"\n\s*doi\s*=\s*\{([^}]+)\}"),
    "author": re.compile(r"\n\s*author\s*=\s*\{(.*?)\},\n", re.DOTALL),
}

HIGH_IMPACT_PATTERNS = [
    r"orb-?slam", r"\bnerf\b", r"gaussian splatting", r"diffusion policy",
    r"\brt-?2\b", r"openvla", r"\bgpt\b", r"foundation model", r"segment anything",
    r"transformer.{0,30}robot", r"imagenet", r"\bros\b", r"\blstm\b",
    r"deep reinforcement", r"behavior cloning", r"apprenticeship learning",
    r"fastslam", r"\bicp\b", r"visual servoing", r"impedance control",
    r"dex-?net", r"qt-?opt", r"rrt\*", r"\bmpc\b", r"control barrier",
    r"model predictive", r"reinforcement learning", r"imitation learning",
    r"visual slam", r"\bvio\b", r"graph slam", r"bundle adjustment",
    r"inverse dynamics", r"sliding mode", r"backstepping", r"admittance control",
    r"adaptive control", r"neural network", r"convolutional", r"attention mechanism",
    r"vision-?language", r"humanoid", r"quadruped", r"legged",
    r"soft robot", r"continuum robot", r"origami", r"artificial muscle",
    r"\bslam\b", r"manipulat", r"\bgrasp", r"navigation", r"path planning",
    r"motion planning", r"teleoperat", r"exoskeleton",
]
HIGH_IMPACT_RE = re.compile("|".join(HIGH_IMPACT_PATTERNS), re.IGNORECASE)

LOW_QUALITY_HINTS = re.compile(
    r"\b(editorial|call for papers|guest editor|preface|introduction to the special|"
    r"corrigendum|erratum|retraction|obituary|in memoriam|book review|"
    r"table of contents|announcement|notice of|correction to)\b",
    re.IGNORECASE,
)

CANDIDATES_PER_VENUE = 12


def iter_entries(path: Path):
    with path.open("r", encoding="utf-8", errors="replace") as f:
        text = f.read()
    starts = [m.start() for m in ENTRY_RE.finditer(text)]
    starts.append(len(text))
    for i in range(len(starts) - 1):
        yield text[starts[i]:starts[i + 1]]


def extract(entry: str) -> dict:
    out = {"title": "", "year": None, "abstract": "", "doi": "", "author": ""}
    for k, rx in FIELD_RES.items():
        m = rx.search(entry)
        if m:
            val = m.group(1).strip()
            if k == "year":
                try:
                    out[k] = int(val)
                except ValueError:
                    out[k] = None
            else:
                out[k] = re.sub(r"\s+", " ", val)
    return out


def classify(rec: dict) -> tuple[bool, str]:
    """Return (is_canonical, tag). Tag is the first regex hit or an empty string."""
    hay = (rec["title"] + " " + rec["abstract"]).lower()
    m = HIGH_IMPACT_RE.search(hay)
    return (m is not None, m.group(0) if m else "")


def is_low_quality(rec: dict) -> bool:
    title = rec["title"].lower()
    abstract = rec["abstract"].lower()
    if LOW_QUALITY_HINTS.search(title):
        return True
    if not abstract and len(title) < 30:
        return True
    return False


def score(rec: dict, is_canonical: bool) -> int:
    s = 0
    if is_canonical:
        s += 1000
    s += min(len(rec["abstract"]), 2000) // 10
    if rec["doi"]:
        s += 50
    if "," in rec["author"]:
        s += 10  # multi-author paper
    return s


def main():
    by_year_venue: dict = defaultdict(lambda: defaultdict(list))
    total = 0
    kept = 0

    for fname, venue in VENUES.items():
        path = ROOT / fname
        if not path.exists():
            print(f"[skip] missing {path}", file=sys.stderr)
            continue
        for entry in iter_entries(path):
            rec = extract(entry)
            year = rec["year"]
            if year is None or year < 1984 or year > 2026:
                continue
            total += 1
            if is_low_quality(rec):
                continue
            is_canonical, tag = classify(rec)
            item = {
                "venue": venue,
                "year": year,
                "title": rec["title"][:250],
                "author": rec["author"][:200],
                "doi": rec["doi"],
                "abstract_snippet": rec["abstract"][:400],
                "canonical_tag": tag,
                "is_canonical": is_canonical,
                "score": score(rec, is_canonical),
            }
            by_year_venue[year][venue].append(item)
            kept += 1

    for year in sorted(by_year_venue):
        ranked = {}
        for venue, items in by_year_venue[year].items():
            items.sort(key=lambda x: -x["score"])
            ranked[venue] = items[:CANDIDATES_PER_VENUE]
        payload = {
            "year": year,
            "venues": ranked,
            "venue_counts": {v: len(ranked[v]) for v in ranked},
        }
        (OUT_DIR / f"{year}.json").write_text(json.dumps(payload, indent=1, ensure_ascii=False))

    print(f"Total 1984-2026 entries scanned: {total}")
    print(f"Kept after low-quality filter   : {kept}")
    print(f"Year files written              : {len(by_year_venue)}")
    print(f"Output directory                : {OUT_DIR}")


if __name__ == "__main__":
    main()
