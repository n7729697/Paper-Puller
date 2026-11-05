#!/usr/bin/env python3
"""Build an interactive, self-contained topical network from selected BibTeX records.

Links represent metadata similarity, not citations. Cytoscape.js is downloaded
once to .state/assets and embedded in the generated HTML for offline viewing.
"""
from __future__ import annotations

import argparse
import csv
import html
import itertools
import json
import math
import re
import shutil
import subprocess
import sys
import urllib.request
from collections import Counter
from pathlib import Path


DEFAULT_BIB = Path("interested/search/selected.bib")

CYTOSCAPE_VERSION = "3.30.2"
CYTOSCAPE_CDN_URL = f"https://unpkg.com/cytoscape@{CYTOSCAPE_VERSION}/dist/cytoscape.min.js"
ASSETS_DIR = Path(".state/assets")
CYTOSCAPE_LOCAL = ASSETS_DIR / f"cytoscape-{CYTOSCAPE_VERSION}.min.js"

FIELD_NAMES = [
    "title",
    "author",
    "journal",
    "booktitle",
    "year",
    "pages",
    "doi",
    "url",
    "abstract",
    "note",
]
NODE_COLUMNS = [
    "id",
    "label",
    "type",
    "year",
    "venue",
    "doi",
    "url",
    "title",
    "author",
    "primary_theme",
    "platform",
    "planning_style",
    "resource_model",
    "charging_model",
    "tags",
]
EDGE_COLUMNS = [
    "source",
    "target",
    "type",
    "weight",
    "similarity",
    "shared_basis",
    "rationale",
]

STOPWORDS = {
    "a",
    "an",
    "and",
    "are",
    "as",
    "at",
    "be",
    "by",
    "can",
    "for",
    "from",
    "has",
    "have",
    "in",
    "into",
    "is",
    "it",
    "its",
    "of",
    "on",
    "or",
    "our",
    "paper",
    "present",
    "presents",
    "propose",
    "proposed",
    "robot",
    "robots",
    "that",
    "the",
    "their",
    "this",
    "to",
    "under",
    "using",
    "we",
    "with",
    "without",
    "while",
    "when",
    "where",
    "which",
}

COLOR_PALETTE = [
    "#4C78A8",
    "#F58518",
    "#54A24B",
    "#B279A2",
    "#72B7B2",
    "#E45756",
    "#9D755D",
    "#EECA3B",
    "#FF9DA6",
    "#B5CF6B",
    "#A0A0A0",
]

THEME_RULES = [
    ("charging infrastructure placement", [r"charging station", r"battery swap", r"mobile charger", r"mobile recharger"]),
    ("task allocation", [r"task allocation", r"assignment", r"coalition"]),
    ("coverage planning", [r"coverage", r"covering"]),
    ("persistent surveillance/monitoring", [r"persistent", r"surveillance", r"monitoring"]),
    ("exploration and map building", [r"exploration", r"map building", r"mapping"]),
    ("charging rendezvous", [r"rendezvous", r"docking", r"dock"]),
    ("routing with charging/refueling", [r"routing", r"route planning", r"vehicle routing", r"refuel", r"refuelling"]),
    ("multi-agent coordination", [r"multi-agent", r"multiagent", r"multi-robot", r"multirobot", r"swarm"]),
    ("SLAM/localization", [r"slam", r"localization", r"localisation", r"odometry"]),
    ("perception/semantic mapping", [r"perception", r"semantic", r"segmentation", r"detection", r"recognition"]),
    ("learning/control", [r"reinforcement learning", r"learning", r"control", r"controller"]),
    ("manipulation", [r"manipulation", r"grasp", r"assembly"]),
    ("robot design/hardware", [r"design", r"mechanism", r"actuator", r"platform"]),
    ("manufacturing/automation", [r"manufacturing", r"industrial", r"factory", r"production"]),
]

PLATFORM_RULES = [
    ("AUVs", [r"\bauv\b", r"underwater"]),
    ("AGV team", [r"\bagv\b", r"automated guided vehicle"]),
    ("UAV-UGV team", [r"uav-ugv", r"\buav\b.*\bugv\b", r"\bugv\b.*\buav\b"]),
    ("aerial robots/UAVs", [r"\buav\b", r"drone", r"aerial", r"quadrotor", r"rotorcraft"]),
    ("robot swarm", [r"swarm"]),
    ("heterogeneous robot team", [r"heterogeneous"]),
    ("autonomous vehicle fleet", [r"self-driving", r"mobility-on-demand", r"vehicle fleet"]),
    ("mobile robot team", [r"mobile robot", r"multi-robot", r"multirobot", r"robot team"]),
    ("manipulator/arm", [r"manipulator", r"robotic arm"]),
    ("humanoid/legged robot", [r"humanoid", r"legged", r"biped", r"quadruped"]),
]

METHOD_RULES = [
    ("control barrier functions", [r"control barrier", r"\bcbf\b"]),
    ("SMT/SAT constraint planning", [r"\bsmt\b", r"satisfiability modulo", r"\bsat\b"]),
    ("integer optimization", [r"mixed-integer", r"integer linear", r"\bmilp\b", r"\bilp\b"]),
    ("dynamic programming", [r"dynamic programming"]),
    ("MDP/value iteration", [r"markov", r"value iteration", r"\bmdp\b"]),
    ("market/game-based scheduling", [r"market", r"game-theoretic", r"nash"]),
    ("heuristic/search planning", [r"heuristic", r"search", r"\ba\*"]),
    ("approximation algorithms", [r"approximation"]),
    ("temporal-logic planning", [r"temporal logic", r"\bltl\b", r"\btwtl\b"]),
    ("reinforcement learning", [r"reinforcement learning", r"\bmarl\b", r"\bmappo\b"]),
    ("graph/routing optimization", [r"graph", r"\btsp\b", r"traveling salesman", r"travelling salesman"]),
    ("model predictive control", [r"model predictive", r"\bmpc\b"]),
    ("planning/scheduling", [r"planning", r"scheduling", r"planner", r"schedule"]),
]

RESOURCE_RULES = [
    ("battery swap station", [r"battery swap", r"battery swapping", r"swaps a depleted battery"]),
    ("mobile charging host", [r"mobile charging host"]),
    ("mobile recharger/depot", [r"mobile recharger", r"mobile charging station", r"mobile depot", r"mobile recharging", r"mobile charging"]),
    ("service station", [r"service station"]),
    ("fixed charging station/depot", [r"charging station", r"charging point", r"recharging depot", r"base station"]),
    ("refueling", [r"refuel", r"refuelling"]),
    ("battery/energy constraint", [r"battery", r"batteries", r"energy-constrained", r"energy constrained", r"energy-aware", r"energy aware"]),
    ("power management", [r"power management", r"power-aware", r"power aware"]),
]

PHRASES = [
    "multi robot",
    "multi-robot",
    "multirobot",
    "robot team",
    "robot teams",
    "multi agent",
    "multi-agent",
    "charging station",
    "recharging station",
    "mobile charger",
    "mobile recharger",
    "mobile depot",
    "battery swap",
    "service station",
    "energy constrained",
    "energy-aware",
    "task allocation",
    "mission planning",
    "path planning",
    "route planning",
    "persistent monitoring",
    "persistent surveillance",
    "coverage planning",
    "reinforcement learning",
    "temporal logic",
]


HTML_TEMPLATE = """<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<title>__TITLE__</title>
<style>
  * { box-sizing: border-box; }
  html, body { margin: 0; padding: 0; height: 100%; font-family: -apple-system, "Segoe UI", Helvetica, Arial, sans-serif; color: #1a1a1a; }
  #app { display: flex; height: 100vh; width: 100vw; }
  #sidebar {
    width: 340px; min-width: 340px; height: 100%; overflow-y: auto;
    border-right: 1px solid #ddd; background: #fafafa; padding: 14px 16px;
  }
  #sidebar h1 { font-size: 14px; margin: 0 0 4px 0; }
  #sidebar .meta { font-size: 11px; color: #666; margin-bottom: 12px; }
  #sidebar h2 { font-size: 11px; text-transform: uppercase; letter-spacing: 0.08em; color: #444; margin: 14px 0 6px; }
  #search { width: 100%; padding: 6px 8px; font-size: 12px; border: 1px solid #ccc; border-radius: 4px; }
  .legend-item {
    display: flex; align-items: center; gap: 8px; padding: 3px 6px; cursor: pointer;
    border-radius: 4px; font-size: 12px; user-select: none;
  }
  .legend-item:hover { background: #eee; }
  .legend-item.off { opacity: 0.35; }
  .legend-swatch { width: 14px; height: 14px; border-radius: 3px; border: 1px solid #999; flex-shrink: 0; }
  .legend-label { flex: 1; }
  #legend-controls { display: flex; gap: 6px; margin-top: 6px; }
  #legend-controls button {
    font-size: 11px; padding: 3px 8px; border: 1px solid #bbb; background: white;
    border-radius: 4px; cursor: pointer;
  }
  #legend-controls button:hover { background: #eee; }
    #bibtex-controls, #export-controls { margin-top: 6px; }
    #bibtex-controls button, #export-controls button {
        font-size: 11px; padding: 3px 8px; border: 1px solid #bbb; background: white;
        border-radius: 4px; cursor: pointer;
    }
    #bibtex-controls button:hover, #export-controls button:hover { background: #eee; }
  #detail { font-size: 12px; line-height: 1.45; word-break: break-word; }
  #detail .empty { color: #888; font-style: italic; }
  #detail .title { font-weight: 600; margin-bottom: 4px; }
  #detail .row { margin: 3px 0; }
  #detail .key { color: #666; }
  #detail a { color: #1e6fcc; text-decoration: none; }
  #detail a:hover { text-decoration: underline; }
  #main { flex: 1; position: relative; }
  #cy { width: 100%; height: 100%; background: #fff; }
  #help { position: absolute; bottom: 10px; right: 12px; font-size: 11px; color: #888; background: rgba(255,255,255,0.85); padding: 4px 8px; border-radius: 4px; }
</style>
</head>
<body>
<div id="app">
  <aside id="sidebar">
    <h1>__TITLE__</h1>
    <div class="meta">__NODE_COUNT__ papers - __EDGE_COUNT__ similarity edges</div>

    <h2>Search</h2>
    <input id="search" type="search" placeholder="title, author, theme, tag..." autocomplete="off">

    <h2>Themes</h2>
    <div id="legend"></div>
    <div id="legend-controls">
      <button id="legend-all">All</button>
      <button id="legend-none">None</button>
    </div>

        <h2>BibTeX</h2>
        <div id="bibtex-controls">
            <button id="copy-visible-bibtex">Copy visible BibTeX</button>
        </div>
        <div id="copy-status" class="meta"></div>

        <h2>Export</h2>
        <div id="export-controls">
            <button id="export-visible-graph">Download visible graph PNG</button>
        </div>
        <div id="export-status" class="meta"></div>

    <h2>Selected paper</h2>
    <div id="detail"><div class="empty">Click a node to view details.</div></div>
  </aside>
  <div id="main">
    <div id="cy"></div>
    <div id="help">scroll = zoom - drag node = move - click background = reset</div>
  </div>
</div>

<script>__CYTOSCAPE_JS__</script>
<script id="graph-data" type="application/json">__GRAPH_DATA__</script>
<script>
(function () {
  const data = JSON.parse(document.getElementById("graph-data").textContent);
  const elements = [
    ...data.nodes.map(n => ({ group: "nodes", data: n.data })),
    ...data.edges.map(e => ({ group: "edges", data: e.data })),
  ];

  const cy = cytoscape({
    container: document.getElementById("cy"),
    elements: elements,
    wheelSensitivity: 0.25,
    style: [
      {
        selector: "node",
        style: {
          "background-color": "data(color)",
          "label": "data(label)",
          "font-size": 8,
          "text-wrap": "wrap",
          "text-max-width": "140px",
          "text-valign": "center",
          "text-halign": "center",
          "color": "#111",
          "border-width": 1,
          "border-color": "#333",
          "width": 36,
          "height": 36,
        }
      },
      {
        selector: "edge",
        style: {
          "width": "mapData(weight, 1, 5, 0.7, 4)",
          "line-color": "#bcbcbc",
          "opacity": 0.55,
          "curve-style": "haystack",
          "haystack-radius": 0.3,
        }
      },
      { selector: "node.faded", style: { "opacity": 0.12, "text-opacity": 0.12 } },
      { selector: "edge.faded", style: { "opacity": 0.04 } },
      { selector: "node.highlight", style: { "border-width": 3, "border-color": "#ff5722", "z-index": 10 } },
      { selector: "edge.highlight", style: { "line-color": "#ff7043", "opacity": 0.95, "width": "mapData(weight, 1, 5, 1.4, 5)" } },
      { selector: "node.hidden", style: { "display": "none" } },
      { selector: "edge.hidden", style: { "display": "none" } },
    ],
    layout: {
      name: "cose",
      animate: false,
      idealEdgeLength: 110,
      nodeRepulsion: 9000,
      nodeOverlap: 14,
      gravity: 0.4,
      numIter: 1500,
      padding: 30,
      randomize: true,
    },
  });

    cy.on("layoutstop", () => {
        cy.fit(cy.elements(), 40);
    });

    const copyStatus = document.getElementById("copy-status");
    const copyButton = document.getElementById("copy-visible-bibtex");
    const exportStatus = document.getElementById("export-status");
    const exportButton = document.getElementById("export-visible-graph");

    function visibleBibtexEntries() {
        return cy.nodes()
            .filter(node => !node.hasClass("hidden"))
            .map(node => node.data("bibtex"))
            .filter(Boolean);
    }

    function safeFilename(value) {
        return String(value || "graph")
            .trim()
            .toLowerCase()
            .replace(/[^a-z0-9]+/g, "-")
            .replace(/^-+|-+$/g, "") || "graph";
    }

    async function copyTextToClipboard(text) {
        if (navigator.clipboard && window.isSecureContext) {
            await navigator.clipboard.writeText(text);
            return;
        }

        const fallback = document.createElement("textarea");
        fallback.value = text;
        fallback.setAttribute("readonly", "true");
        fallback.style.position = "fixed";
        fallback.style.left = "-9999px";
        document.body.appendChild(fallback);
        fallback.select();
        document.execCommand("copy");
        fallback.remove();
    }

    copyButton.addEventListener("click", async () => {
        const bibtexEntries = visibleBibtexEntries();
        if (!bibtexEntries.length) {
            copyStatus.textContent = "No visible BibTeX entries to copy.";
            return;
        }
        try {
            await copyTextToClipboard(bibtexEntries.join("\\n\\n"));
            copyStatus.textContent = `Copied ${bibtexEntries.length} BibTeX entr${bibtexEntries.length === 1 ? "y" : "ies"}.`;
        } catch (error) {
            copyStatus.textContent = "Copy failed in this browser.";
        }
    });

    exportButton.addEventListener("click", () => {
        const pngDataUrl = cy.png({ full: true, scale: 2, bg: "#ffffff" });
        const downloadLink = document.createElement("a");
        downloadLink.href = pngDataUrl;
        downloadLink.download = `${safeFilename(document.title)}.png`;
        document.body.appendChild(downloadLink);
        downloadLink.click();
        downloadLink.remove();
        exportStatus.textContent = "Downloaded visible graph PNG.";
    });

  // Legend
  const legendEl = document.getElementById("legend");
  const hiddenThemes = new Set();
  data.legend.forEach(item => {
    const row = document.createElement("div");
    row.className = "legend-item";
    row.dataset.theme = item.theme;
    row.innerHTML = `<span class="legend-swatch" style="background:${item.color}"></span><span class="legend-label">${escapeHtml(item.theme)}</span>`;
    row.addEventListener("click", () => {
      if (hiddenThemes.has(item.theme)) hiddenThemes.delete(item.theme);
      else hiddenThemes.add(item.theme);
      applyThemeFilter();
    });
    legendEl.appendChild(row);
  });
  document.getElementById("legend-all").addEventListener("click", () => {
    hiddenThemes.clear();
    applyThemeFilter();
  });
  document.getElementById("legend-none").addEventListener("click", () => {
    data.legend.forEach(item => hiddenThemes.add(item.theme));
    applyThemeFilter();
  });

  function applyThemeFilter() {
    document.querySelectorAll(".legend-item").forEach(el => {
      el.classList.toggle("off", hiddenThemes.has(el.dataset.theme));
    });
    cy.batch(() => {
      cy.nodes().forEach(node => {
        const hide = hiddenThemes.has(node.data("theme"));
        node.toggleClass("hidden", hide);
      });
      cy.edges().forEach(edge => {
        const sHidden = edge.source().hasClass("hidden");
        const tHidden = edge.target().hasClass("hidden");
        edge.toggleClass("hidden", sHidden || tHidden);
      });
    });
        const visibleCount = visibleBibtexEntries().length;
        copyStatus.textContent = visibleCount
            ? `${visibleCount} BibTeX entr${visibleCount === 1 ? "y" : "ies"} visible.`
            : "No BibTeX entries visible.";
        exportStatus.textContent = visibleCount
            ? `${visibleCount} visible paper${visibleCount === 1 ? "" : "s"} ready to export.`
            : "No visible papers to export.";
  }

    applyThemeFilter();

  // Selection / highlight
  function clearHighlight() {
    cy.elements().removeClass("faded highlight");
    showDetail(null);
  }

  cy.on("tap", "node", evt => {
    const node = evt.target;
    const neighborhood = node.closedNeighborhood();
    cy.batch(() => {
      cy.elements().addClass("faded").removeClass("highlight");
      neighborhood.removeClass("faded");
      node.addClass("highlight");
      neighborhood.edges().addClass("highlight");
    });
    showDetail(node.data());
  });
  cy.on("tap", evt => {
    if (evt.target === cy) clearHighlight();
  });

  // Search
  const searchInput = document.getElementById("search");
  searchInput.addEventListener("input", () => {
    const query = searchInput.value.trim().toLowerCase();
    if (!query) { clearHighlight(); return; }
    cy.batch(() => {
      let matched = cy.collection();
      cy.nodes().forEach(node => {
        const d = node.data();
        const hay = [d.label, d.title, d.author, d.theme, d.platform, d.planning_style, d.resource_model, d.tags, d.venue]
          .filter(Boolean).join(" ").toLowerCase();
        if (hay.includes(query)) matched = matched.add(node);
      });
      cy.elements().addClass("faded").removeClass("highlight");
      matched.removeClass("faded").addClass("highlight");
      matched.connectedEdges().forEach(edge => {
        if (matched.contains(edge.source()) && matched.contains(edge.target())) {
          edge.removeClass("faded").addClass("highlight");
        }
      });
    });
  });

  // Detail panel
  function showDetail(d) {
    const el = document.getElementById("detail");
    if (!d) {
      el.innerHTML = '<div class="empty">Click a node to view details.</div>';
      return;
    }
    const link = d.doi
      ? `<a href="https://doi.org/${encodeURIComponent(d.doi)}" target="_blank" rel="noopener">${escapeHtml(d.doi)}</a>`
      : (d.url ? `<a href="${escapeAttr(d.url)}" target="_blank" rel="noopener">${escapeHtml(d.url)}</a>` : '<span class="empty">(no link)</span>');
    const tags = d.tags ? d.tags.split("|").filter(Boolean).map(escapeHtml).join(", ") : "";
    el.innerHTML = `
      <div class="title">${escapeHtml(d.title || d.label)}</div>
      <div class="row"><span class="key">Authors:</span> ${escapeHtml(d.author || "-")}</div>
      <div class="row"><span class="key">Venue:</span> ${escapeHtml(d.venue || "-")} <span class="key">- Year:</span> ${escapeHtml(d.year || "-")}</div>
      <div class="row"><span class="key">Theme:</span> ${escapeHtml(d.theme || "-")}</div>
      <div class="row"><span class="key">Platform:</span> ${escapeHtml(d.platform || "-")}</div>
      <div class="row"><span class="key">Method:</span> ${escapeHtml(d.planning_style || "-")}</div>
      <div class="row"><span class="key">Resource:</span> ${escapeHtml(d.resource_model || "-")}</div>
      <div class="row"><span class="key">Tags:</span> ${tags || "-"}</div>
      <div class="row"><span class="key">Link:</span> ${link}</div>
    `;
  }

  function escapeHtml(s) {
    return String(s == null ? "" : s)
      .replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;").replace(/'/g, "&#39;");
  }
  function escapeAttr(s) { return escapeHtml(s); }
})();
</script>
</body>
</html>
"""


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="paper-puller graph",
        description="Create graph CSVs and an interactive HTML graph from a BibTeX file."
    )
    parser.add_argument(
        "bib_file",
        nargs="?",
        type=Path,
        help=f"Input BibTeX file. Default: {DEFAULT_BIB}",
    )
    parser.add_argument(
        "--bib",
        type=Path,
        default=None,
        help="Input BibTeX file. Overrides the optional positional argument.",
    )
    parser.add_argument(
        "--output-prefix",
        type=Path,
        default=None,
        help="Output prefix. Default: input path without .bib.",
    )
    parser.add_argument(
        "--min-edge-score",
        type=float,
        default=0.30,
        help="Minimum combined similarity score for an edge. Default: 0.30.",
    )
    parser.add_argument(
        "--max-degree",
        type=int,
        default=8,
        help="Soft cap on retained edges per node after sorting strongest first. Default: 8.",
    )
    parser.add_argument(
        "--large-graph-limit",
        type=int,
        default=2000,
        help="Refuse pairwise edge scoring above this many entries unless --allow-large is set.",
    )
    parser.add_argument(
        "--allow-large",
        action="store_true",
        help="Allow pairwise edge scoring above --large-graph-limit.",
    )
    parser.add_argument(
        "--plot-formats",
        default="html",
        help=(
            "Deprecated compatibility option. The script writes the interactive "
            "HTML graph by default; use the browser export button for a PNG of "
            "the current view. Use 'none' to skip plots."
        ),
    )
    parser.add_argument(
        "--no-plot",
        action="store_true",
        help="Skip DOT and HTML rendering; still writes node and edge CSV files.",
    )
    parser.add_argument(
        "--engine",
        default="sfdp",
        choices=["sfdp", "neato", "dot"],
        help="Graphviz layout engine. Default: sfdp.",
    )
    parser.add_argument(
        "--keep-dot",
        action="store_true",
        default=True,
        help="Write the DOT source file. Enabled by default.",
    )
    return parser.parse_args(argv)


def normalize_space(value: str) -> str:
    return re.sub(r"\s+", " ", value).strip()


def clean_markup(value: str | None) -> str:
    if not value:
        return ""
    value = html.unescape(value)
    value = re.sub(r"<tex-math[^>]*>(.*?)</tex-math>", r"\1", value, flags=re.I | re.S)
    value = re.sub(r"<[^>]+>", " ", value)
    value = re.sub(r"\\[a-zA-Z]+|[{}_$^]", " ", value)
    return normalize_space(value)


def ascii_text(value: str | None) -> str:
    """Return an ASCII-safe string for DOT labels and portable CSV output."""
    value = clean_markup(value)
    replacements = {
        "\u2013": "-",
        "\u2014": "-",
        "\u2212": "-",
        "\u201c": '"',
        "\u201d": '"',
        "\u2018": "'",
        "\u2019": "'",
        "\u03b1": "alpha",
        "\u03b2": "beta",
        "\u03b3": "gamma",
        "\u2265": ">=",
        "\u2264": "<=",
        "\u00b0": " degrees ",
        "\u00d7": "x",
        "\u2217": "*",
    }
    value = "".join(replacements.get(char, char) for char in value)
    value = value.encode("ascii", "ignore").decode("ascii")
    return normalize_space(value)


def read_balanced_entries(text: str) -> list[str]:
    entries: list[str] = []
    index = 0
    while True:
        start = text.find("@", index)
        if start == -1:
            break
        brace_start = text.find("{", start)
        if brace_start == -1:
            break
        depth = 0
        end = brace_start
        while end < len(text):
            char = text[end]
            if char == "{":
                depth += 1
            elif char == "}":
                depth -= 1
                if depth == 0:
                    end += 1
                    break
            end += 1
        entries.append(text[start:end])
        index = end
    return entries


def split_entry_header(entry_text: str) -> tuple[str, str, str]:
    match = re.match(r"@(\w+)\s*\{\s*([^,]+)\s*,(.*)\}\s*$", entry_text, flags=re.S)
    if not match:
        return "misc", "unknown", ""
    return match.group(1).lower(), match.group(2).strip(), match.group(3)


def parse_bib_fields(body: str) -> dict[str, str]:
    fields: dict[str, str] = {}
    index = 0
    while index < len(body):
        match = re.search(r"([A-Za-z][A-Za-z0-9_-]*)\s*=", body[index:])
        if not match:
            break
        name = match.group(1).lower()
        value_start = index + match.end()
        while value_start < len(body) and body[value_start].isspace():
            value_start += 1
        if value_start >= len(body):
            break

        delimiter = body[value_start]
        if delimiter == "{":
            depth = 1
            pos = value_start + 1
            while pos < len(body) and depth:
                if body[pos] == "{":
                    depth += 1
                elif body[pos] == "}":
                    depth -= 1
                pos += 1
            value = body[value_start + 1 : pos - 1]
            index = pos
        elif delimiter == '"':
            pos = value_start + 1
            escaped = False
            while pos < len(body):
                if body[pos] == '"' and not escaped:
                    break
                escaped = body[pos] == "\\" and not escaped
                if body[pos] != "\\":
                    escaped = False
                pos += 1
            value = body[value_start + 1 : pos]
            index = pos + 1
        else:
            pos = value_start
            while pos < len(body) and body[pos] not in ",\n":
                pos += 1
            value = body[value_start:pos]
            index = pos

        fields[name] = normalize_space(html.unescape(value))
        comma = body.find(",", index)
        if comma == -1:
            break
        index = comma + 1
    return fields


def parse_entries(text: str) -> list[dict[str, str]]:
    entries: list[dict[str, str]] = []
    used_ids: Counter[str] = Counter()
    for entry_text in read_balanced_entries(text):
        entry_type, key, body = split_entry_header(entry_text)
        fields = parse_bib_fields(body)
        if not fields.get("title"):
            continue

        clean_key = safe_id(key)
        used_ids[clean_key] += 1
        if used_ids[clean_key] > 1:
            clean_key = f"{clean_key}-{used_ids[clean_key]}"

        entry = {"entry_type": entry_type, "key": clean_key}
        entry["bibtex"] = entry_text.strip()
        for field in FIELD_NAMES:
            if fields.get(field):
                entry[field] = fields[field]
        entries.append(entry)
    return entries


def safe_id(value: str) -> str:
    value = ascii_text(value).lower()
    value = re.sub(r"[^a-z0-9_.:-]+", "-", value).strip("-")
    return value or "entry"


def venue_label(entry: dict[str, str]) -> str:
    venue = entry.get("journal") or entry.get("booktitle") or ""
    venue_lower = venue.lower()
    if "automation letters" in venue_lower:
        return "RA-L"
    if "transactions on robotics" in venue_lower:
        return "T-RO"
    if "automation science" in venue_lower:
        return "T-ASE"
    if "mechatronics" in venue_lower:
        return "T-Mech"
    if "international journal" in venue_lower:
        return "IJRR"
    if "cybernetics" in venue_lower:
        return "Cybernetics"
    if "robotics and automation" in venue_lower:
        return "ICRA"
    if "intelligent robots and systems" in venue_lower:
        return "IROS"
    if venue_lower == "robotics":
        return "Robotics"
    return ascii_text(venue)


def first_match(label_rules: list[tuple[str, list[str]]], text: str, fallback: str) -> str:
    for label, patterns in label_rules:
        if any(re.search(pattern, text, flags=re.I) for pattern in patterns):
            return label
    return fallback


def all_matches(label_rules: list[tuple[str, list[str]]], text: str) -> list[str]:
    matches = []
    for label, patterns in label_rules:
        if any(re.search(pattern, text, flags=re.I) for pattern in patterns):
            matches.append(label)
    return matches


def classify(entry: dict[str, str]) -> tuple[str, str, str, str, str]:
    blob = ascii_text(f"{entry.get('title', '')} {entry.get('abstract', '')}").lower()
    theme = first_match(THEME_RULES, blob, "general robotics")
    platform = first_match(PLATFORM_RULES, blob, "robotic system")
    method = first_match(METHOD_RULES, blob, "method/analysis")
    resource_model = first_match(RESOURCE_RULES, blob, "not specified")

    tags = []
    tags.extend(all_matches(THEME_RULES, blob))
    tags.extend(all_matches(PLATFORM_RULES, blob))
    tags.extend(all_matches(METHOD_RULES, blob))
    tags.extend(all_matches(RESOURCE_RULES, blob))
    unique_tags = sorted(set(tags))
    return theme, platform, method, resource_model, "|".join(unique_tags)


def label_for(entry: dict[str, str]) -> str:
    author = ascii_text(entry.get("author", "")).split(" and ")[0].split()
    last_name = author[-1] if author else "Unknown"
    title = re.sub(r"^(The|A|An) ", "", ascii_text(entry.get("title", "")))
    short_title = " ".join(title.split()[:4])
    return normalize_space(f"{last_name} {entry.get('year', '')} {short_title}")


def build_nodes(entries: list[dict[str, str]]) -> list[dict[str, str]]:
    nodes: list[dict[str, str]] = []
    for entry in entries:
        theme, platform, method, resource_model, tags = classify(entry)
        nodes.append(
            {
                "id": entry["key"],
                "label": label_for(entry),
                "type": entry.get("entry_type", "paper"),
                "year": ascii_text(entry.get("year", "")),
                "venue": venue_label(entry),
                "doi": ascii_text(entry.get("doi", "")),
                "url": ascii_text(entry.get("url", "")),
                "title": ascii_text(entry.get("title", "")),
                "author": ascii_text(entry.get("author", "")),
                "primary_theme": theme,
                "platform": platform,
                "planning_style": method,
                "resource_model": resource_model,
                "charging_model": resource_model,
                "tags": tags,
                "bibtex": entry.get("bibtex", ""),
            }
        )
    return nodes


def tokenize(entry: dict[str, str]) -> list[str]:
    text = ascii_text(f"{entry.get('title', '')} {entry.get('title', '')} {entry.get('abstract', '')}").lower()
    tokens = [token.strip("-") for token in re.findall(r"[a-z][a-z0-9-]{2,}", text)]
    tokens = [token for token in tokens if token and token not in STOPWORDS]
    for phrase in PHRASES:
        if phrase in text:
            tokens.extend([phrase.replace(" ", "_").replace("-", "_")] * 4)
    return tokens


def tfidf_vectors(entries: list[dict[str, str]]) -> list[dict[str, float]]:
    tokenized = [tokenize(entry) for entry in entries]
    doc_count = len(entries)
    document_frequency: Counter[str] = Counter()
    for tokens in tokenized:
        document_frequency.update(set(tokens))

    idf = {
        token: math.log((doc_count + 1) / (count + 1)) + 1
        for token, count in document_frequency.items()
        if count >= 2
    }

    vectors: list[dict[str, float]] = []
    for tokens in tokenized:
        counts = Counter(token for token in tokens if token in idf)
        vector = {token: (1 + math.log(count)) * idf[token] for token, count in counts.items()}
        norm = math.sqrt(sum(value * value for value in vector.values())) or 1.0
        vectors.append({token: value / norm for token, value in vector.items()})
    return vectors


def dot_product(left: dict[str, float], right: dict[str, float]) -> float:
    if len(left) > len(right):
        left, right = right, left
    return sum(value * right.get(token, 0.0) for token, value in left.items())


def combined_edge_score(
    source: dict[str, str],
    target: dict[str, str],
    semantic_similarity: float,
) -> tuple[float, list[str]]:
    shared: list[str] = []
    score = semantic_similarity
    weighted_columns = [
        ("primary_theme", 0.18),
        ("platform", 0.12),
        ("planning_style", 0.10),
        ("resource_model", 0.12),
        ("venue", 0.03),
    ]
    for column, weight in weighted_columns:
        if source[column] and source[column] == target[column]:
            shared.append(column)
            score += weight

    if source["year"].isdigit() and target["year"].isdigit():
        if abs(int(source["year"]) - int(target["year"])) <= 2:
            score += 0.02
            shared.append("near_year")

    return score, shared


def edge_weight(score: float) -> int:
    if score >= 0.75:
        return 5
    if score >= 0.58:
        return 4
    if score >= 0.43:
        return 3
    if score >= 0.30:
        return 2
    return 1


def build_edges(
    entries: list[dict[str, str]],
    nodes: list[dict[str, str]],
    min_edge_score: float,
    max_degree: int,
) -> list[dict[str, str]]:
    vectors = tfidf_vectors(entries)
    edges: list[dict[str, str]] = []

    for left_index, right_index in itertools.combinations(range(len(nodes)), 2):
        semantic_similarity = dot_product(vectors[left_index], vectors[right_index])
        score, shared = combined_edge_score(
            nodes[left_index],
            nodes[right_index],
            semantic_similarity,
        )
        if score < min_edge_score:
            continue
        shared_basis = ";".join(shared + [f"semantic={semantic_similarity:.3f}"])
        rationale = (
            f"Combined semantic similarity {semantic_similarity:.3f}"
            + (f" plus shared {', '.join(shared)}." if shared else ".")
        )
        edges.append(
            {
                "source": nodes[left_index]["id"],
                "target": nodes[right_index]["id"],
                "type": "topic_similarity",
                "weight": str(edge_weight(score)),
                "similarity": f"{score:.4f}",
                "shared_basis": shared_basis,
                "rationale": rationale,
            }
        )

    edges.sort(key=lambda edge: (-float(edge["similarity"]), edge["source"], edge["target"]))

    degree = {node["id"]: 0 for node in nodes}
    kept: list[dict[str, str]] = []
    for edge in edges:
        if degree[edge["source"]] < max_degree and degree[edge["target"]] < max_degree:
            kept.append(edge)
            degree[edge["source"]] += 1
            degree[edge["target"]] += 1
    return kept


def write_csv(path: Path, rows: list[dict[str, str]], fieldnames: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as file:
        writer = csv.DictWriter(file, fieldnames=fieldnames, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def theme_colors(nodes: list[dict[str, str]]) -> dict[str, str]:
    themes = sorted({node["primary_theme"] for node in nodes})
    return {theme: COLOR_PALETTE[index % len(COLOR_PALETTE)] for index, theme in enumerate(themes)}


def dot_escape(value: str) -> str:
    return value.replace("\\", "\\\\").replace('"', '\\"')


def write_dot(path: Path, nodes: list[dict[str, str]], edges: list[dict[str, str]], engine: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    node_by_id = {node["id"]: node for node in nodes}
    colors = theme_colors(nodes)
    graph_name = safe_id(path.stem).replace("-", "_")

    lines = [
        f"graph {graph_name} {{",
        f'  graph [layout={engine}, overlap=false, splines=true, bgcolor="white", outputorder=edgesfirst];',
        '  node [shape=ellipse, style="filled", fontname="Helvetica", fontsize=9, penwidth=1.4, color="#333333"];',
        '  edge [fontname="Helvetica", fontsize=7, color="#8A8A8A", penwidth=1.0];',
        "",
    ]

    for node in nodes:
        fill = colors.get(node["primary_theme"], "#A0A0A0")
        label = f"{node['label']}\\n{node['venue']} | {node['year']}\\n{node['primary_theme']}"
        tooltip = node["doi"] or node["title"]
        lines.append(
            f'  "{dot_escape(node["id"])}" '
            f'[label="{dot_escape(label)}", fillcolor="{fill}", tooltip="{dot_escape(tooltip)}"];'
        )

    lines.append("")
    for edge in edges:
        if edge["source"] not in node_by_id or edge["target"] not in node_by_id:
            continue
        weight = int(edge["weight"])
        penwidth = 0.6 + 0.55 * weight
        color = "#666666" if weight >= 5 else "#8F8F8F" if weight >= 4 else "#B8B8B8"
        lines.append(
            f'  "{dot_escape(edge["source"])}" -- "{dot_escape(edge["target"])}" '
            f'[weight={weight}, penwidth={penwidth:.2f}, color="{color}", '
            f'label="{weight}", tooltip="{dot_escape(edge["shared_basis"])}"];'
        )

    lines.extend(
        [
            "",
            "  subgraph cluster_legend {",
            '    label="Theme legend";',
            '    color="#DDDDDD";',
            '    fontsize=14;',
            '    fontname="Helvetica";',
        ]
    )
    for index, (theme, fill) in enumerate(colors.items()):
        legend_id = f"legend_{index}"
        lines.append(
            f'    "{legend_id}" [label="{dot_escape(theme)}", fillcolor="{fill}", '
            'shape=box, style="filled,rounded", color="#777777"];'
        )
    lines.extend(["  }", "}"])
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def load_cytoscape_js() -> str:
    """Return cytoscape.js source, downloading and caching it on first use."""
    if CYTOSCAPE_LOCAL.exists():
        return CYTOSCAPE_LOCAL.read_text(encoding="utf-8")
    ASSETS_DIR.mkdir(parents=True, exist_ok=True)
    print(
        f"Fetching cytoscape.js v{CYTOSCAPE_VERSION} from {CYTOSCAPE_CDN_URL}\n"
        f"  caching to {CYTOSCAPE_LOCAL} (one-time)",
        file=sys.stderr,
    )
    try:
        with urllib.request.urlopen(CYTOSCAPE_CDN_URL, timeout=30) as response:
            content = response.read().decode("utf-8")
    except Exception as error:
        raise SystemExit(
            f"Could not download cytoscape.js from {CYTOSCAPE_CDN_URL}: {error}.\n"
            f"Place a copy of cytoscape v{CYTOSCAPE_VERSION} at {CYTOSCAPE_LOCAL} and rerun."
        )
    CYTOSCAPE_LOCAL.write_text(content, encoding="utf-8")
    return content


def write_html(path: Path, nodes: list[dict[str, str]], edges: list[dict[str, str]]) -> None:
    """Render a self-contained interactive HTML graph (cytoscape.js inlined)."""
    colors = theme_colors(nodes)
    cy_nodes = [
        {
            "data": {
                "id": node["id"],
                "label": node["label"],
                "title": node["title"],
                "author": node["author"],
                "year": node["year"],
                "venue": node["venue"],
                "doi": node["doi"],
                "url": node["url"],
                "theme": node["primary_theme"],
                "platform": node["platform"],
                "planning_style": node["planning_style"],
                "resource_model": node["resource_model"],
                "tags": node["tags"],
                "bibtex": node.get("bibtex", ""),
                "color": colors.get(node["primary_theme"], "#A0A0A0"),
            }
        }
        for node in nodes
    ]
    cy_edges = [
        {
            "data": {
                "id": f"{edge['source']}__{edge['target']}",
                "source": edge["source"],
                "target": edge["target"],
                "weight": int(edge["weight"]),
                "similarity": float(edge["similarity"]),
                "shared_basis": edge["shared_basis"],
                "rationale": edge["rationale"],
            }
        }
        for edge in edges
    ]
    legend = [{"theme": theme, "color": color} for theme, color in colors.items()]
    data_json = json.dumps(
        {"nodes": cy_nodes, "edges": cy_edges, "legend": legend},
        ensure_ascii=False,
    ).replace("</", "<\\/")

    cytoscape_js = load_cytoscape_js().replace("</script", "<\\/script")

    title = ascii_text(path.stem) or "Paper graph"
    document = (
        HTML_TEMPLATE.replace("__TITLE__", html.escape(title))
        .replace("__NODE_COUNT__", str(len(cy_nodes)))
        .replace("__EDGE_COUNT__", str(len(cy_edges)))
        .replace("__CYTOSCAPE_JS__", cytoscape_js)
        .replace("__GRAPH_DATA__", data_json)
    )
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(document, encoding="utf-8")


def render_plot(dot_path: Path, output_path: Path, engine: str, output_format: str) -> None:
    resolved_engine = shutil.which(engine)
    if not resolved_engine:
        fallback = next((shutil.which(candidate) for candidate in ["sfdp", "neato", "dot"]), None)
        if not fallback:
            raise SystemExit("Graphviz is required to render plots, but no sfdp/neato/dot binary was found.")
        resolved_engine = fallback

    output_path.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(
        [resolved_engine, f"-T{output_format}", str(dot_path), "-o", str(output_path)],
        check=True,
    )


def output_paths(output_prefix: Path) -> tuple[Path, Path, Path]:
    nodes_path = output_prefix.with_name(f"{output_prefix.name}_graph_nodes.csv")
    edges_path = output_prefix.with_name(f"{output_prefix.name}_graph_edges.csv")
    dot_path = output_prefix.with_name(f"{output_prefix.name}_graph.dot")
    return nodes_path, edges_path, dot_path


def requested_plot_formats(args: argparse.Namespace) -> list[str]:
    if args.no_plot:
        return []
    raw = args.plot_formats.strip().lower()
    if not raw or raw == "none":
        return []
    return [item.strip() for item in raw.split(",") if item.strip()]


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    bib_path = (args.bib or args.bib_file or DEFAULT_BIB).resolve()
    output_prefix = (args.output_prefix or bib_path.with_suffix("")).resolve()

    entries = parse_entries(bib_path.read_text(encoding="utf-8", errors="ignore"))
    if not entries:
        raise SystemExit(f"No BibTeX entries found in {bib_path}")
    if len(entries) > args.large_graph_limit and not args.allow_large:
        raise SystemExit(
            f"{bib_path} has {len(entries)} entries. Pairwise graph building is disabled above "
            f"{args.large_graph_limit} entries; use --allow-large or curate the BibTeX first."
        )

    nodes = build_nodes(entries)
    edges = build_edges(entries, nodes, args.min_edge_score, args.max_degree)

    nodes_path, edges_path, dot_path = output_paths(output_prefix)
    write_csv(nodes_path, nodes, NODE_COLUMNS)
    write_csv(edges_path, edges, EDGE_COLUMNS)

    if args.keep_dot:
        write_dot(dot_path, nodes, edges, args.engine)

    html_path = output_prefix.with_name(f"{output_prefix.name}_graph.html")
    if not args.no_plot:
        write_html(html_path, nodes, edges)

    print(f"Read {len(entries)} BibTeX entries from {bib_path}")
    print(f"Wrote {len(nodes)} nodes to {nodes_path}")
    print(f"Wrote {len(edges)} edges to {edges_path}")
    if args.keep_dot:
        print(f"Wrote DOT graph to {dot_path}")
    if not args.no_plot:
        print(f"Wrote interactive HTML graph to {html_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
