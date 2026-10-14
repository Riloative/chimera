"""URL detection (shared vectors with the frontend), normalization, rewriters."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from chimera.web.urls import canonical_url, domain_matches, find_urls, rewrite, strip_tracking

CASES = json.loads((Path(__file__).parent / "fixtures" / "url_detection.json").read_text(encoding="utf-8"))["cases"]


@pytest.mark.parametrize("case", CASES, ids=[c["name"] for c in CASES])
def test_detection_vectors(case):
    found = find_urls(case["text"], case.get("limit", 5))
    assert found.urls == case["urls"]
    if "total" in case:
        assert found.total == case["total"]


def test_tracking_parameters_are_stripped():
    assert strip_tracking("https://a.org/x?utm_source=x&id=3&fbclid=abc&gclid=1") == "https://a.org/x?id=3"
    assert strip_tracking("https://a.org/x?UTM_Medium=y") == "https://a.org/x"


def test_canonical_url_merges_variants():
    a = canonical_url("http://www.News.example.org/article/?id=1&utm_source=feed#top")
    b = canonical_url("https://news.example.org/article?fbclid=XYZ&id=1")
    assert a == b
    assert canonical_url("https://a.org/x?b=2&a=1") == canonical_url("https://a.org/x?a=1&b=2")
    assert canonical_url("https://a.org/x") != canonical_url("https://a.org/y")


def test_domain_matching():
    assert domain_matches("news.example.org", ["example.org"])
    assert domain_matches("example.org", ["*.example.org"])
    assert not domain_matches("badexample.org", ["example.org"])
    assert not domain_matches("example.org.evil.net", ["example.org"])


def test_github_and_youtube_rewriters():
    blob = rewrite("https://github.com/owner/repo/blob/main/src/app.py")
    assert blob.candidates[0] == "https://raw.githubusercontent.com/owner/repo/main/src/app.py"
    assert blob.candidates[-1] == "https://github.com/owner/repo/blob/main/src/app.py"
    repo = rewrite("https://github.com/owner/repo")
    assert repo.candidates == ["https://raw.githubusercontent.com/owner/repo/HEAD/README.md", "https://github.com/owner/repo"]
    assert rewrite("https://github.com/owner/repo/issues/4").candidates == ["https://github.com/owner/repo/issues/4"]
    video = rewrite("https://www.youtube.com/watch?v=abc")
    assert video.kind == "video" and "transcript_unavailable" in video.notes
    assert rewrite("https://example.org/").candidates == ["https://example.org/"]
