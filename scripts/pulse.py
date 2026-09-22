#!/usr/bin/env python3
"""Build profile assets from public repository records, using only the standard library."""

import argparse
import calendar
import html
import json
import os
from pathlib import Path
import re
import time
from datetime import date, datetime, timedelta, timezone
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

ROOT = Path(__file__).resolve().parents[1]
USER = "JerryChen97"
OWNERS = ("PennyLaneAI", "XanaduAI", USER)
# Also discover release repositories from every merged or reviewed public PR.
EXTRA_RELEASE_REPOS = (
    "PennyLaneAI/pennylane", "PennyLaneAI/pennylane-cirq",
    "PennyLaneAI/pennylane-qiskit", "PennyLaneAI/PennyLane-IonQ",
    "PennyLaneAI/pennylane-qulacs", "PennyLaneAI/pennylane-aqt",
    "PennyLaneAI/pennylane-lightning", "PennyLaneAI/catalyst",
)
START = "<!-- PULSE:START -->"
END = "<!-- PULSE:END -->"


class GitHub:
    def __init__(self):
        self.token = os.environ.get("GH_TOKEN") or os.environ.get("GITHUB_TOKEN")
        self.last_search = 0.0

    def get(self, path, **params):
        if not path.startswith(("/search/", "/repos/")):
            raise ValueError("Only repository and public-search endpoints are allowed")
        url = "https://api.github.com" + path
        if params:
            url += "?" + urlencode(params)
        headers = {"Accept": "application/vnd.github+json", "User-Agent": "JerryChen97-public-pulse",
                   "X-GitHub-Api-Version": "2022-11-28"}
        if self.token:
            headers["Authorization"] = "Bearer " + self.token
        for attempt in range(4):
            if path.startswith("/search/"):
                time.sleep(max(0, (2.1 if self.token else 6.2) - (time.monotonic() - self.last_search)))
                self.last_search = time.monotonic()
            try:
                with urlopen(Request(url, headers=headers), timeout=45) as response:
                    return json.load(response)
            except HTTPError as exc:
                if attempt == 3 or exc.code not in (403, 429, 500, 502, 503, 504):
                    raise RuntimeError(f"GitHub HTTP {exc.code}: {path}; keeping the previous dashboard") from None
                wait = int(exc.headers.get("Retry-After", 10 * (attempt + 1)))
                if exc.headers.get("X-RateLimit-Remaining") == "0":
                    wait = max(wait, int(exc.headers.get("X-RateLimit-Reset", 0)) - int(time.time()) + 1)
                if wait > 180:
                    raise RuntimeError("GitHub rate limit exceeded; keeping the previous dashboard") from None
                time.sleep(max(1, wait))
            except (URLError, TimeoutError):
                if attempt == 3:
                    raise RuntimeError(f"GitHub unavailable: {path}; keeping the previous dashboard") from None
                time.sleep(5 * (attempt + 1))

    def search(self, kind, query, page=1, per_page=100):
        if "is:public" not in query.split():
            raise ValueError("Every search must explicitly request public records")
        for attempt in range(3):
            result = self.get(f"/search/{kind}", q=query, sort="created" if kind == "issues" else "author-date",
                              order="asc", per_page=per_page, page=page)
            if result.get("incomplete_results") is False:
                return result
            time.sleep(2 * (attempt + 1))
        raise RuntimeError("GitHub returned incomplete search results; keeping the previous dashboard")

    def issues(self, query, first=date(2008, 1, 1), last=None):
        """Paginate; split creation-date ranges to get past the 1,000-result search cap."""
        last = last or datetime.now(timezone.utc).date()
        bounded = f"{query} created:{first.isoformat()}..{last.isoformat()}"
        result = self.search("issues", bounded)
        total = result["total_count"]
        if total > 1000:
            if first == last:
                raise RuntimeError("More than 1,000 PRs in one day; refusing a truncated dashboard")
            middle = first + (last - first) // 2
            items = self.issues(query, first, middle) + self.issues(query, middle + timedelta(days=1), last)
            if len({item["id"] for item in items}) != total:
                raise RuntimeError("Search changed while splitting date ranges; retry on the next run")
            return items
        items = result["items"]
        for page in range(2, (total + 99) // 100 + 1):
            batch = self.search("issues", bounded, page=page)
            if batch["total_count"] != total:
                raise RuntimeError("Search changed during pagination; retry on the next run")
            items.extend(batch["items"])
        unique = {item["id"]: item for item in items}
        if len(unique) != total:
            raise RuntimeError("Search pagination omitted records; keeping the previous dashboard")
        return list(unique.values())

    def releases(self, repo):
        # The local token may see private repositories. Never publish their records.
        metadata = self.get(f"/repos/{repo}")
        if metadata.get("private") is not False or metadata.get("visibility") != "public":
            raise RuntimeError(f"Refusing a non-public release source: {repo}")
        result = []
        page = 1
        while True:
            batch = self.get(f"/repos/{repo}/releases", per_page=100, page=page)
            result.extend(item for item in batch if not item["draft"] and item["published_at"]
                          and item["author"]["login"].lower() == USER.lower())
            if len(batch) < 100:
                return result
            page += 1


def query_for(owner, metric):
    scope = f"user:{owner}" if owner == USER else f"org:{owner}"
    filters = {"commits": f"author:{USER} merge:false",
               "merged": f"is:pr author:{USER} is:merged",
               "reviewed": f"is:pr reviewed-by:{USER} -author:{USER}"}
    return f"is:public {scope} {filters[metric]}"


def search_url(owner, metric):
    return "https://github.com/search?" + urlencode({"q": query_for(owner, metric),
                                                    "type": "commits" if metric == "commits" else "pullrequests"})


def simplify_pr(item, owner):
    repo = item["repository_url"].removeprefix("https://api.github.com/repos/")
    if repo.split("/")[0].lower() != owner.lower() or "pull_request" not in item:
        raise RuntimeError("Unexpected search scope or non-PR record")
    return {"id": item["id"], "number": item["number"], "repo": repo,
            "title": item["title"], "url": item["html_url"],
            "merged_at": item["pull_request"].get("merged_at")}


def collect(api, today):
    groups = []
    merged = []
    release_repos = set(EXTRA_RELEASE_REPOS)
    for owner in OWNERS:
        print(f"Reading public activity: {owner}", flush=True)
        authored = [simplify_pr(item, owner) for item in api.issues(query_for(owner, "merged"), last=today)]
        reviewed = [simplify_pr(item, owner) for item in api.issues(query_for(owner, "reviewed"), last=today)]
        if any(not item["merged_at"] for item in authored):
            raise RuntimeError("A merged PR is missing its merge date")
        commits = api.search("commits", query_for(owner, "commits"), per_page=1)["total_count"]
        groups.append({"owner": owner, "commits": commits, "merged": len(authored),
                       "reviewed": len(reviewed), "releases": 0})
        merged.extend(authored)
        release_repos.update(item["repo"] for item in authored + reviewed)
    releases = []
    for repo in sorted(release_repos, key=str.lower):
        print(f"Reading public releases: {repo}", flush=True)
        for release in api.releases(repo):
            releases.append({"repo": repo, "tag": release["tag_name"], "url": release["html_url"],
                             "published_at": release["published_at"]})
    for group in groups:
        group["releases"] = sum(r["repo"].split("/")[0].lower() == group["owner"].lower() for r in releases)
    return {"schema_version": 1, "as_of": today.isoformat(), "user": USER, "groups": groups,
            "merged_prs": sorted(merged, key=lambda x: x["merged_at"], reverse=True),
            "releases": sorted(releases, key=lambda x: x["published_at"], reverse=True),
            "release_repositories": sorted(release_repos, key=str.lower)}


def months(data):
    today = date.fromisoformat(data["as_of"])
    end = today.year * 12 + today.month - 1
    series = []
    for value in range(end - 11, end + 1):
        year, month = value // 12, value % 12 + 1
        key = f"{year:04d}-{month:02d}"
        counts = [sum(pr["repo"].split("/")[0].lower() == owner.lower()
                      and pr["merged_at"][:7] == key for pr in data["merged_prs"]) for owner in OWNERS]
        series.append({"month": key, "label": calendar.month_abbr[month], "counts": counts})
    return series


def totals(data):
    return {key: sum(group[key] for group in data["groups"])
            for key in ("commits", "merged", "reviewed", "releases")}


def svg(data, dark=False):
    p = ({"bg": "#0b1523", "card": "#132235", "ink": "#edf6fc", "muted": "#a0b5c8", "line": "#2b4054",
          "accent": "#67e8f9", "bar": "#22d3ee", "purple": "#c4b5fd", "green": "#5eead4"} if dark else
         {"bg": "#f3fbfd", "card": "#ffffff", "ink": "#123047", "muted": "#50687b", "line": "#d3e6ec",
          "accent": "#087e8b", "bar": "#0891b2", "purple": "#7c3aed", "green": "#0d9488"})
    out = ['<svg xmlns="http://www.w3.org/2000/svg" width="1000" height="674" viewBox="0 0 1000 674" role="img" aria-labelledby="title desc">',
           '<title id="title">Jerry Chen — public open-source contributions</title>',
           '<desc id="desc">All-time public repository activity across PennyLaneAI, XanaduAI and JerryChen97. '
           + html.escape(", ".join(f"{value:,} {key}" for key, value in totals(data).items()))
           + '. Monthly bars show merged pull requests. Detailed sources and definitions follow in the README.</desc>',
           f'<rect x="1" y="1" width="998" height="672" rx="22" fill="{p["bg"]}" stroke="{p["line"]}"/>',
           '<g font-family="Segoe UI, Helvetica, Arial, sans-serif">']

    def text(x, y, value, size=16, fill=None, weight=400, anchor="start"):
        out.append(f'<text x="{x}" y="{y}" font-size="{size}" font-weight="{weight}" fill="{fill or p["ink"]}" text-anchor="{anchor}">{html.escape(str(value))}</text>')

    text(32, 36, "PUBLIC WORK / ALL TIME", 12, p["accent"], 700)
    text(32, 76, "Open-source footprint", 30, weight=700)
    text(968, 36, "UPDATED " + data["as_of"] + " UTC", 11, p["muted"], anchor="end")
    text(32, 104, "PennyLaneAI  ·  XanaduAI  ·  Personal repositories", 16, p["muted"])
    metrics = (("commits", "Authored commits"), ("merged", "Merged PRs"),
               ("reviewed", "PRs reviewed"), ("releases", "Releases published"))
    sums = totals(data)
    for index, (key, label) in enumerate(metrics):
        x = 32 + 238 * index
        out.append(f'<rect x="{x}" y="130" width="222" height="108" rx="12" fill="{p["card"]}" stroke="{p["line"]}"/>')
        text(x + 18, 178, f"{sums[key]:,}", 35, p["accent"], 700)
        text(x + 18, 213, label, 16, p["muted"])
    text(32, 281, "WHERE THE WORK HAPPENS", 12, p["muted"], 700)
    for x, label in ((492, "COMMITS"), (648, "MERGED PRs"), (806, "PRs REVIEWED"), (958, "RELEASES")):
        text(x, 281, label, 11, p["muted"], 600, "end")
    colors = (p["bar"], p["purple"], p["green"])
    for index, group in enumerate(data["groups"]):
        y = 316 + index * 38
        out.append(f'<circle cx="40" cy="{y-5}" r="5" fill="{colors[index]}"/>')
        text(54, y, group["owner"] if group["owner"] != USER else "Personal / JerryChen97", 16, weight=600)
        for x, key in ((492, "commits"), (648, "merged"), (806, "reviewed"), (958, "releases")):
            text(x, y, f'{group[key]:,}', 17, weight=600, anchor="end")
    out.append(f'<path d="M32 416H968" stroke="{p["line"]}"/>')
    text(32, 447, "MERGED PULL REQUESTS", 12, p["muted"], 700)
    series = months(data)
    period = series[0]["month"] + " — " + series[-1]["month"]
    text(968, 447, period + " · current month in progress", 12, p["muted"], anchor="end")
    maximum = max(1, max(sum(month["counts"]) for month in series))
    for index, month in enumerate(series):
        x = 46 + index * 77
        total = sum(month["counts"])
        y = 584
        for count, color in zip(month["counts"], colors):
            height = 94 * count / maximum
            y -= height
            if height:
                out.append(f'<rect x="{x}" y="{y:.2f}" width="60" height="{height:.2f}" fill="{color}"/>')
        text(x + 30, round(y - 9), total, 12, p["muted"], anchor="middle")
        text(x + 30, 610, month["label"], 13, p["muted"], anchor="middle")
    text(32, 648, "Public records only · Counts overlap · Sources and metric definitions below", 13, p["muted"])
    out.extend(["</g>", "</svg>"])
    return "\n".join(out) + "\n"


def safe_text(value):
    value = " ".join(str(value).split())
    value = html.escape(value, quote=False).replace("|", "&#124;")
    return re.sub(r"([\\`*_{}\[\]])", r"\\\1", value)


def safe_url(value):
    if not value.startswith("https://github.com/"):
        raise ValueError("Only public GitHub links are allowed")
    return value.replace("(", "%28").replace(")", "%29").replace(" ", "%20")


def link(label, url):
    return f"[{safe_text(label)}]({safe_url(url)})"


def readme_section(data):
    lines = [START, '<picture>',
             '  <source media="(prefers-color-scheme: dark)" srcset="./assets/pulse-dark.svg" />',
             '  <img src="./assets/pulse-light.svg" width="100%" alt="Public contributions across PennyLaneAI, XanaduAI and JerryChen97; accessible figures and sources follow below." />',
             '</picture>', '',
             'Public work across **PennyLaneAI**, **XanaduAI**, and my personal repositories. '
             'Counts come directly from public repository records and include all available history.', '',
             '<details>', '<summary>Explore the numbers and sources</summary>', '',
             '| Repository owner | Authored commits | Merged PRs | PRs reviewed | Releases published |',
             '| :--- | ---: | ---: | ---: | ---: |']
    for group in data["groups"]:
        owner = group["owner"]
        cells = [link(owner, f"https://github.com/{owner}")]
        cells.extend(link(f'{group[key]:,}', search_url(owner, key)) for key in ("commits", "merged", "reviewed"))
        cells.append(f'[{group["releases"]:,}](./data/pulse.json)')
        lines.append("| " + " | ".join(cells) + " |")
    lines += ['', '**Monthly merged PRs** — the current month is still in progress.', '',
              '| Month (UTC) | PennyLaneAI | XanaduAI | Personal |', '| :--- | ---: | ---: | ---: |']
    for month in months(data):
        lines.append('| ' + ' | '.join([month["month"]] + [str(c) for c in month["counts"]]) + ' |')
    lines += ['', 'Commits are non-merge commits indexed on default branches. Reviews count distinct PRs reviewed, '
              'excluding my own PRs. Releases count public releases authored by my account in the tracked repositories. '
              'These metrics overlap and are not a combined contribution total. Private work is excluded.', '',
              f'Updated **{data["as_of"]} UTC** · [How this is counted](./docs/pulse.md) · [Public data](./data/pulse.json)',
              '', '</details>', '', '### Recent contributions', '',
              '| Merged (UTC) | Project | Contribution |', '| :--- | :--- | :--- |']
    for pr in data["merged_prs"][:5]:
        lines.append(f'| {pr["merged_at"][:10]} | {link(pr["repo"], "https://github.com/" + pr["repo"])} | '
                     + link(f'{pr["title"]} #{pr["number"]}', pr["url"]) + ' |')
    if not data["merged_prs"]:
        lines.append('| — | — | No public merged PRs found in this scope. |')
    lines += ['', '### Recent releases published', '', '| Published (UTC) | Project | Release |', '| :--- | :--- | :--- |']
    # Feature different projects, instead of filling this list with one package's tags.
    seen = set()
    for release in data["releases"]:
        if release["repo"] in seen:
            continue
        seen.add(release["repo"])
        lines.append(f'| {release["published_at"][:10]} | {link(release["repo"], "https://github.com/" + release["repo"])} | '
                     + link(release["tag"], release["url"]) + ' |')
        if len(seen) == 4:
            break
    if not seen:
        lines.append('| — | — | No public releases published by this account in tracked repositories. |')
    lines += ['', END]
    return "\n".join(lines)


def update_readme(original, data):
    if original.count(START) != 1 or original.count(END) != 1:
        raise ValueError("README must contain exactly one PULSE marker pair")
    first, last = original.index(START), original.index(END)
    if last < first:
        raise ValueError("README marker order is invalid")
    return original[:first] + readme_section(data) + original[last + len(END):]


def write_outputs(data):
    # Compute all assets first; failed collection/rendering must leave the old dashboard intact.
    outputs = {ROOT / "README.md": update_readme((ROOT / "README.md").read_text(encoding="utf-8"), data),
               ROOT / "assets/pulse-light.svg": svg(data), ROOT / "assets/pulse-dark.svg": svg(data, True),
               ROOT / "data/pulse.json": json.dumps(data, ensure_ascii=False, indent=2) + "\n"}
    for path, content in outputs.items():
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8", newline="\n")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--from-data", type=Path, help="Re-render an existing public snapshot without network access")
    args = parser.parse_args()
    if args.from_data:
        data = json.loads(args.from_data.read_text(encoding="utf-8"))
    else:
        data = collect(GitHub(), datetime.now(timezone.utc).date())
    write_outputs(data)
    print(json.dumps({"as_of": data["as_of"], "totals": totals(data), "tracked_release_repos": len(data["release_repositories"])}))


if __name__ == "__main__":
    main()
