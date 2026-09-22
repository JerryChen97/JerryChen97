#!/usr/bin/env python3
"""Build profile assets from public repository records, using only the standard library."""

import argparse
import html
import json
import os
from pathlib import Path
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
METRICS = (("commits", "Authored commits"), ("merged", "Merged PRs"),
           ("reviewed", "PRs reviewed"), ("releases", "Releases published"))


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


def totals(data):
    return {key: sum(group[key] for group in data["groups"])
            for key in ("commits", "merged", "reviewed", "releases")}


def description(data):
    sums = totals(data)
    return "All-time totals from tracked public repositories: " + "; ".join(
        f"{sums[key]:,} {label.lower()}" for key, label in METRICS) + "."


def svg(data, dark=False):
    p = ({"bg": "#0b1523", "card": "#132235", "ink": "#edf6fc", "muted": "#a0b5c8", "line": "#2b4054",
          "accent": "#67e8f9"} if dark else
         {"bg": "#f3fbfd", "card": "#ffffff", "ink": "#123047", "muted": "#50687b", "line": "#d3e6ec",
          "accent": "#087e8b"})
    out = ['<svg xmlns="http://www.w3.org/2000/svg" width="1000" height="200" viewBox="0 0 1000 200" role="img" aria-labelledby="title desc">',
           '<title id="title">GitHub Pulse — overall totals</title>',
           '<desc id="desc">' + html.escape(description(data)) + '</desc>',
           f'<rect x="1" y="1" width="998" height="198" rx="18" fill="{p["bg"]}" stroke="{p["line"]}"/>',
           '<g font-family="Segoe UI, Helvetica, Arial, sans-serif">']

    def text(x, y, value, size=16, fill=None, weight=400, anchor="start"):
        out.append(f'<text x="{x}" y="{y}" font-size="{size}" font-weight="{weight}" fill="{fill or p["ink"]}" text-anchor="{anchor}">{html.escape(str(value))}</text>')

    text(28, 32, "TRACKED PUBLIC ACTIVITY / ALL TIME", 13, p["accent"], 700)
    text(972, 32, "UPDATED " + data["as_of"] + " UTC", 12, p["muted"], anchor="end")
    sums = totals(data)
    for index, (key, label) in enumerate(METRICS):
        x = 28 + 240 * index
        out.append(f'<rect x="{x}" y="52" width="224" height="120" rx="12" fill="{p["card"]}" stroke="{p["line"]}"/>')
        text(x + 18, 111, f"{sums[key]:,}", 46, p["accent"], 700)
        text(x + 18, 147, label, 19, p["muted"])
    out.extend(["</g>", "</svg>"])
    return "\n".join(out) + "\n"


def readme_section(data):
    lines = [START, '<picture>',
             '  <source media="(prefers-color-scheme: dark)" srcset="./assets/pulse-dark.svg" />',
             '  <img src="./assets/pulse-light.svg" width="100%" alt="' + html.escape(description(data), quote=True) + '" />',
             '</picture>', END]
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
