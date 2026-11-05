"""Bibliometric analysis for the 9 robotics BibTeX histories.

Stdlib only. Streams each .bib file, extracts (venue, year, title, abstract, doi),
applies a curated keyword vocabulary, and writes CSVs into reports/data/.
"""
from __future__ import annotations

import csv
import os
import re
import sys
from collections import Counter, defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = ROOT / "reports" / "data"
DATA_DIR.mkdir(parents=True, exist_ok=True)

VENUES = {
    "cybernetics_history.bib": "T-Cybernetics",
    "icra_history.bib": "ICRA",
    "ijrr_history.bib": "IJRR",
    "iros_history.bib": "IROS",
    "ral_history.bib": "RA-L",
    "rcim_history.bib": "RCIM",
    "tase_history.bib": "T-ASE",
    "tmech_history.bib": "T-Mech",
    "tro_history.bib": "T-RO",
}

ENTRY_RE = re.compile(r"^@article\{", re.MULTILINE)
FIELD_RES = {
    "title": re.compile(r"\n\s*title\s*=\s*\{(.*?)\},\n", re.DOTALL),
    "year": re.compile(r"\n\s*year\s*=\s*\{(\d{4})\}"),
    "abstract": re.compile(r"\n\s*abstract\s*=\s*\{(.*?)\},\n", re.DOTALL),
    "doi": re.compile(r"\n\s*doi\s*=\s*\{([^}]+)\}"),
    "author": re.compile(r"\n\s*author\s*=\s*\{(.*?)\},\n", re.DOTALL),
}

VOCAB = {
    "slam": [r"\bslam\b", r"orb-?slam", r"\bvio\b", r"lio-?sam", r"visual slam",
             r"lidar slam", r"monocular slam", r"nerf-?slam", r"gaussian.{0,10}splatting"],
    "manipulation": [r"manipulat", r"\bgrasp", r"pick-?and-?place", r"dexterous",
                     r"in-?hand", r"assembly", r"bin picking", r"pushing"],
    "navigation": [r"navigat", r"path planning", r"motion planning", r"obstacle avoidance",
                   r"occupancy grid", r"a\*", r"rrt", r"social navigation"],
    "learning_rl": [r"reinforcement learning", r"\brl\b", r"deep rl", r"q-?learning",
                    r"policy gradient", r"actor-?critic", r"ppo", r"sac", r"\btd3\b"],
    "learning_imitation": [r"imitation learning", r"behavior cloning", r"learning from demonstration",
                           r"diffusion policy", r"action chunking"],
    "deep_learning": [r"deep learning", r"neural network", r"convolutional", r"\bcnn\b",
                      r"\blstm\b", r"transformer", r"attention mechanism"],
    "foundation": [r"foundation model", r"\bvla\b", r"vision-?language", r"\brt-?1\b",
                   r"\brt-?2\b", r"openvla", r"multimodal.{0,20}model", r"large language"],
    "control_classical": [r"pid", r"sliding mode", r"backstepping", r"lyapunov",
                          r"adaptive control", r"robust control", r"passivity"],
    "control_modern": [r"model predictive", r"\bmpc\b", r"control barrier", r"\bcbf\b",
                       r"koopman", r"lqr", r"linear quadratic"],
    "perception": [r"object detection", r"semantic segmentation", r"point cloud",
                   r"rgb-?d", r"depth estimat", r"6d pose", r"scene understanding"],
    "multi_robot": [r"multi-?robot", r"multi-?agent", r"\bswarm\b", r"\bmarl\b",
                    r"heterogeneous team", r"robot team"],
    "centralized": [r"centraliz"],
    "distributed": [r"distribut", r"decentraliz"],
    "cooperation": [r"cooperat", r"collaborat", r"consensus", r"formation control",
                    r"coverage", r"coordinat"],
    "symbiosis": [r"symbios"],
    "tasks_warehouse": [r"warehous", r"logistic", r"\bamr\b", r"fulfillment",
                        r"\bagv\b", r"order picking", r"material handling"],
    "soft_bio": [r"soft robot", r"bio-?inspired", r"continuum robot", r"compliant",
                 r"pneumatic", r"origami", r"artificial muscle", r"tendon-?driven"],
    "humanoid": [r"humanoid", r"biped", r"legged", r"quadruped", r"locomotion"],
    "safety": [r"safe reinforcement", r"control barrier", r"hamilton-?jacobi",
               r"formal verification", r"safe rl"],
    "hri": [r"human-?robot", r"teleoperat", r"shared autonomy", r"exoskeleton",
            r"wearable robot", r"prosthesi"],
    "sim2real": [r"sim-?to-?real", r"domain randomization", r"sim2real"],
    "tactile": [r"tactile", r"\bforce sensor", r"skin sensor"],
    "event_camera": [r"event camera", r"neuromorphic vision", r"\bdvs\b"],
}

REWARD_TERMS = {
    "individual_reward": [r"individual reward", r"per-?agent reward", r"local reward"],
    "global_reward": [r"global reward", r"team reward", r"shared reward",
                      r"joint reward"],
    "hybrid_reward": [r"hybrid reward", r"mixed reward", r"shaped reward"],
    "credit_assignment": [r"credit assignment", r"difference reward", r"counterfactual reward"],
}

HIGH_IMPACT_PATTERNS = [
    r"orb-?slam", r"\bnerf\b", r"gaussian splatting", r"diffusion policy",
    r"\brt-?2\b", r"openvla", r"\bgpt\b", r"foundation model", r"segment anything",
    r"transformer.{0,30}robot", r"imagenet", r"\bros\b", r"\blstm\b",
    r"deep reinforcement", r"behavior cloning", r"apprenticeship learning",
    r"fastslam", r"icp", r"visual servoing", r"impedance control",
    r"dex-?net", r"qt-?opt", r"rrt\*", r"\bmpc\b",
]


def compile_vocab(vocab):
    return {k: re.compile("|".join(v), re.IGNORECASE) for k, v in vocab.items()}


VOCAB_RE = compile_vocab(VOCAB)
REWARD_RE = compile_vocab(REWARD_TERMS)
HIGH_IMPACT_RE = re.compile("|".join(HIGH_IMPACT_PATTERNS), re.IGNORECASE)


def iter_entries(path: Path):
    """Yield each @article{...} block as a raw string."""
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


def main():
    per_year_venue: Counter = Counter()                 # (venue, year) -> count
    topic_era: dict = defaultdict(Counter)              # era -> topic -> count
    topic_year_venue: dict = defaultdict(Counter)       # (venue, year) -> topic -> count
    reward_year: dict = defaultdict(Counter)            # year -> term -> count
    cent_dist_era: dict = defaultdict(Counter)          # era -> {centralized|distributed} -> count
    task_year: dict = defaultdict(Counter)              # year -> task -> count
    top_impact: list = []                               # high-impact candidates
    symbios_hits: list = []                             # every symbios* match
    total = 0
    out_of_window = 0

    def era_of(y: int) -> str:
        if y is None:
            return "unknown"
        base = (y // 4) * 4
        return f"{base}-{base + 3}"

    for fname, venue in VENUES.items():
        path = ROOT / fname
        if not path.exists():
            print(f"[skip] missing {path}", file=sys.stderr)
            continue
        for entry in iter_entries(path):
            rec = extract(entry)
            year = rec["year"]
            if year is None:
                continue
            total += 1
            if year < 1984 or year > 2026:
                out_of_window += 1
                # still count cybernetics pre-1984 into a special bucket for the appendix
                if venue == "T-Cybernetics" and 1960 <= year <= 1983:
                    per_year_venue[("T-Cybernetics-pre1984", year)] += 1
                continue

            per_year_venue[(venue, year)] += 1

            hay = (rec["title"] + " " + rec["abstract"]).lower()

            # topic flags
            hit_topics = set()
            for topic, rx in VOCAB_RE.items():
                if rx.search(hay):
                    hit_topics.add(topic)
                    topic_era[era_of(year)][topic] += 1
                    topic_year_venue[(venue, year)][topic] += 1

            # reward term counts
            for term, rx in REWARD_RE.items():
                if rx.search(hay):
                    reward_year[year][term] += 1

            # centralized vs distributed (only when multi-robot context is plausible)
            if "multi_robot" in hit_topics or "cooperation" in hit_topics \
                    or "swarm" in hay or "multi-agent" in hay:
                if "centralized" in hit_topics:
                    cent_dist_era[era_of(year)]["centralized"] += 1
                if "distributed" in hit_topics:
                    cent_dist_era[era_of(year)]["distributed"] += 1

            # task buckets (only count if the flag fired)
            for task in ("manipulation", "navigation", "tasks_warehouse"):
                if task in hit_topics:
                    task_year[year][task] += 1

            # high-impact candidates
            if HIGH_IMPACT_RE.search(hay):
                top_impact.append({
                    "venue": venue,
                    "year": year,
                    "title": rec["title"][:200],
                    "doi": rec["doi"],
                    "author": rec["author"][:120],
                })

            # symbiosis literal collection
            if "symbiosis" in hit_topics:
                symbios_hits.append({
                    "venue": venue,
                    "year": year,
                    "title": rec["title"][:200],
                    "doi": rec["doi"],
                })

    # --- write per_year_per_venue.csv ---
    with (DATA_DIR / "per_year_per_venue.csv").open("w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["venue", "year", "count"])
        for (v, y), c in sorted(per_year_venue.items()):
            w.writerow([v, y, c])

    # --- keyword_era_counts.csv ---
    with (DATA_DIR / "keyword_era_counts.csv").open("w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["era", "topic", "count"])
        for era in sorted(topic_era):
            for topic, c in sorted(topic_era[era].items()):
                w.writerow([era, topic, c])

    # --- topic_flags_by_year_venue.csv ---
    with (DATA_DIR / "topic_flags_by_year_venue.csv").open("w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["venue", "year", "topic", "count"])
        for (v, y), tc in sorted(topic_year_venue.items()):
            for topic, c in sorted(tc.items()):
                w.writerow([v, y, topic, c])

    # --- centralized_vs_distributed.csv ---
    with (DATA_DIR / "centralized_vs_distributed.csv").open("w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["era", "centralized", "distributed"])
        for era in sorted(cent_dist_era):
            w.writerow([era, cent_dist_era[era]["centralized"], cent_dist_era[era]["distributed"]])

    # --- reward_design.csv ---
    with (DATA_DIR / "reward_design.csv").open("w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["year", "individual_reward", "global_reward", "hybrid_reward", "credit_assignment"])
        for y in sorted(reward_year):
            r = reward_year[y]
            w.writerow([y, r["individual_reward"], r["global_reward"], r["hybrid_reward"], r["credit_assignment"]])

    # --- task_distribution.csv ---
    with (DATA_DIR / "task_distribution.csv").open("w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["year", "manipulation", "navigation", "warehousing"])
        for y in sorted(task_year):
            t = task_year[y]
            w.writerow([y, t["manipulation"], t["navigation"], t["tasks_warehouse"]])

    # --- top_impact_candidates.csv ---
    with (DATA_DIR / "top_impact_candidates.csv").open("w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=["venue", "year", "title", "doi", "author"])
        w.writeheader()
        for row in sorted(top_impact, key=lambda r: (r["venue"], r["year"])):
            w.writerow(row)

    # --- symbiosis_hits.csv ---
    with (DATA_DIR / "symbiosis_hits.csv").open("w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=["venue", "year", "title", "doi"])
        w.writeheader()
        for row in sorted(symbios_hits, key=lambda r: (r["venue"], r["year"])):
            w.writerow(row)

    # --- console summary ---
    print(f"Total entries parsed: {total}")
    print(f"In 1984–2026 window : {total - out_of_window}")
    print(f"Out-of-window (incl. cybernetics pre-1984): {out_of_window}")
    print(f"'symbios*' literal hits: {len(symbios_hits)}")
    print(f"High-impact candidates : {len(top_impact)}")
    print(f"Artefacts written to   : {DATA_DIR}")
    for p in sorted(DATA_DIR.glob("*.csv")):
        print(f"  - {p.name} ({os.path.getsize(p)} bytes)")


if __name__ == "__main__":
    main()
