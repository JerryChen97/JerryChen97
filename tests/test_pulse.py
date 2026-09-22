from datetime import date
from pathlib import Path
import sys
import unittest
from unittest.mock import patch
import xml.etree.ElementTree as ET

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import pulse


def snapshot():
    return {"as_of": "2026-01-15", "groups": [
        {"owner": owner, "commits": 3, "merged": 1 if i == 0 else 0, "reviewed": 2, "releases": 0}
        for i, owner in enumerate(pulse.OWNERS)],
        "merged_prs": [{"id": 1, "number": 1, "repo": "PennyLaneAI/pennylane", "title": "Fix | <tag> [link]",
                        "url": "https://github.com/PennyLaneAI/pennylane/pull/1", "merged_at": "2026-01-01T00:00:00Z"}],
        "releases": [], "release_repositories": []}


class SearchTests(unittest.TestCase):
    def test_queries_require_public_scope_and_correct_authorship(self):
        for owner in pulse.OWNERS:
            for metric in ("commits", "merged", "reviewed"):
                self.assertIn("is:public", pulse.query_for(owner, metric))
        self.assertIn("merge:false", pulse.query_for("PennyLaneAI", "commits"))
        self.assertIn("-author:JerryChen97", pulse.query_for("PennyLaneAI", "reviewed"))
        with self.assertRaises(ValueError):
            pulse.GitHub().search("issues", "is:pr author:JerryChen97")

    def test_pagination_does_not_drop_page_two(self):
        api = pulse.GitHub()
        with patch.object(api, "search", side_effect=[
            {"total_count": 101, "items": [{"id": i} for i in range(100)]},
            {"total_count": 101, "items": [{"id": 100}]},
        ]) as search:
            self.assertEqual(len(api.issues("is:public is:pr")), 101)
            self.assertEqual(search.call_count, 2)

    def test_result_cap_splits_into_disjoint_ranges(self):
        api = pulse.GitHub()
        def response(kind, query, page=1):
            if "2026-01-01..2026-01-02" in query:
                return {"total_count": 1001, "items": []}
            ids = list(range(501)) if "2026-01-01..2026-01-01" in query else list(range(501, 1001))
            return {"total_count": len(ids), "items": [{"id": i} for i in ids[(page-1)*100:page*100]]}
        with patch.object(api, "search", side_effect=response) as search:
            result = api.issues("is:public", date(2026, 1, 1), date(2026, 1, 2))
            self.assertEqual(len(result), 1001)
            self.assertEqual(len({item["id"] for item in result}), 1001)
            queries = [call.args[1] for call in search.call_args_list]
            self.assertTrue(any("created:2026-01-01..2026-01-01" in q for q in queries))
            self.assertTrue(any("created:2026-01-02..2026-01-02" in q for q in queries))

    def test_changed_split_search_fails(self):
        api = pulse.GitHub()
        with patch.object(api, "search", side_effect=[
            {"total_count": 1001, "items": []},
            {"total_count": 1, "items": [{"id": 1}]},
            {"total_count": 1, "items": [{"id": 2}]},
        ]), self.assertRaises(RuntimeError):
            api.issues("is:public", date(2026, 1, 1), date(2026, 1, 2))

    def test_changed_or_duplicate_pages_fail(self):
        for final_total, final_id in ((102, 100), (101, 1)):
            api = pulse.GitHub()
            with patch.object(api, "search", side_effect=[
                {"total_count": 101, "items": [{"id": i} for i in range(100)]},
                {"total_count": final_total, "items": [{"id": final_id}]},
            ]), self.assertRaises(RuntimeError):
                api.issues("is:public")

    def test_incomplete_results_never_become_zero(self):
        api = pulse.GitHub()
        with patch.object(api, "get", return_value={"incomplete_results": True, "total_count": 0, "items": []}), \
             patch.object(pulse.time, "sleep"), self.assertRaises(RuntimeError):
            api.search("issues", "is:public")

    def test_private_release_source_is_rejected_before_reading(self):
        api = pulse.GitHub()
        with patch.object(api, "get", return_value={"private": True, "visibility": "private"}) as get, \
             self.assertRaises(RuntimeError):
            api.releases("PennyLaneAI/private")
        self.assertEqual(get.call_count, 1)

    def test_releases_filter_drafts_and_other_authors(self):
        api = pulse.GitHub()
        release = {"id": 1, "draft": False, "published_at": "2026-01-01T00:00:00Z", "author": {"login": pulse.USER}}
        draft = {**release, "id": 2, "draft": True}
        other = {**release, "id": 3, "author": {"login": "someone-else"}}
        with patch.object(api, "get", side_effect=[{"private": False, "visibility": "public"}, [release, draft, other]]):
            self.assertEqual(api.releases("PennyLaneAI/pennylane"), [release])

    def test_prs_from_other_owners_are_rejected(self):
        with self.assertRaises(RuntimeError):
            pulse.simplify_pr({"repository_url": "https://api.github.com/repos/other/repo", "pull_request": {}}, "PennyLaneAI")


class OutputTests(unittest.TestCase):
    def test_manual_readme_content_is_preserved(self):
        original = "Manual intro\n" + pulse.START + "\nold\n" + pulse.END + "\nManual footer\n"
        output = pulse.update_readme(original, snapshot())
        self.assertTrue(output.startswith("Manual intro\n"))
        self.assertTrue(output.endswith("\nManual footer\n"))
        self.assertEqual(output.count(pulse.START), 1)
        with self.assertRaises(ValueError):
            pulse.update_readme("no markers", snapshot())

    def test_svg_is_valid_with_empty_and_real_series(self):
        for data in (snapshot(), {**snapshot(), "merged_prs": []}):
            for dark in (False, True):
                root = ET.fromstring(pulse.svg(data, dark))
                self.assertEqual(root.attrib["viewBox"], "0 0 1000 200")
                self.assertNotIn("nan", pulse.svg(data, dark))


if __name__ == "__main__":
    unittest.main()
