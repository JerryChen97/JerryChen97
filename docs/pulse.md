# About GitHub Pulse

This dashboard reads public repository activity directly from GitHub's REST API.
It does not use the profile contribution graph, third-party stats services, or
private organization data. It covers repositories owned by **PennyLaneAI**,
**XanaduAI**, and **JerryChen97**, using all available history.

## What the numbers mean

- **Authored commits:** GitHub commit-search matches for `author:JerryChen97
  merge:false is:public` within each owner. GitHub indexes the default branch.
  Squashed commits and author-email attribution follow GitHub's own records.
  This is a count of search matches, not a deduplicated count of commit hashes
  across repositories. A commit appearing in multiple repositories can count
  more than once. Unmerged branch work is not included.
- **Merged PRs:** public pull requests authored by JerryChen97 and merged in
  the listed owners' repositories.
- **PRs reviewed:** distinct public pull requests matching
  `reviewed-by:JerryChen97 -author:JerryChen97`. Several reviews of one PR count
  once. Review requests and ordinary comments are not counted as reviews.
- **Releases published:** public GitHub release records whose `author.login`
  is JerryChen97. This credits publication, not sole authorship of the release's
  code. Drafts are excluded; published prereleases are included. The tracked
  repositories are the union of repositories found through merged/reviewed PRs
  and the explicit extra list in `scripts/pulse.py`. The complete scope and all
  counted releases appear in `data/pulse.json`. Release-only work in untracked
  repositories is not included.

These metrics overlap. A merged PR may produce an authored commit and later
ship in a release, so the dashboard does not add them into a single score.
Zero means no matching public records were returned for that metric and scope;
it says nothing about private work. Search indexing can lag recent activity.
API access controls still apply; this does not bypass organization policies.

The README presents only the four overall totals. The underlying public
snapshot remains in `data/pulse.json` for reproducibility.

## Refresh and maintenance

`.github/workflows/pulse.yml` runs daily at **09:17 UTC**, on relevant changes,
or from **Actions → Refresh public contribution dashboard → Run workflow**.
It uses the repository's built-in `GITHUB_TOKEN`; no personal token or
organization secret is required. The token needs `contents: write` to update
this profile repository. Public searches explicitly require `is:public`, and
release repositories are checked for public visibility before reading releases.

The collector follows all pages, splits PR searches that exceed GitHub's
1,000-result retrieval cap, and rejects incomplete or inconsistent results.
It fails the workflow instead of publishing partial counts or replacing a
failed request with zero. A failed refresh leaves the last successful snapshot
and its visible update date in place. GitHub may disable scheduled workflows in
inactive public repositories after 60 days; re-enable the workflow in Actions
if needed. Scheduled runs can also be delayed.

Run locally with Python 3.12 or newer:

```sh
python -m unittest discover -s tests -v
python scripts/pulse.py
```

For authenticated rate limits, supply `GH_TOKEN` or `GITHUB_TOKEN` through your
environment; never put credentials in a committed file. Without a token, public
queries still work but may exhaust the smaller anonymous rate limit. To render
the last snapshot offline:

```sh
python scripts/pulse.py --from-data data/pulse.json
```

Edit `OWNERS` and `EXTRA_RELEASE_REPOS` in `scripts/pulse.py` to change scope.
Manual README content belongs
outside the `PULSE:START` / `PULSE:END` markers. Generated assets are plain SVG
with light and dark variants. Image alternative text includes the overall
totals for accessibility.

## GitHub references

- [Commit search and default-branch indexing](https://docs.github.com/en/search-github/searching-on-github/searching-commits)
- [PR search and review qualifiers](https://docs.github.com/en/search-github/searching-on-github/searching-issues-and-pull-requests)
- [Search pagination, limits, and incomplete results](https://docs.github.com/en/rest/search/search)
- [Release records](https://docs.github.com/en/rest/releases/releases)
