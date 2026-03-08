"""
tests/test_literature_structurer.py

Unit tests for Stage B post-processing trust boundary.
"""

import json
from pathlib import Path

from src.agents.literature_structurer import postprocess_candidate_papers


def _load_sources_fixture():
    fixture = Path("tests/fixtures/literature_sources_fixture.json")
    with open(fixture, "r", encoding="utf-8") as f:
        return json.load(f)


def test_doi_visible_kept_and_url_prefers_doi_org():
    sources = _load_sources_fixture()
    candidates = [
        {
            "title": "Photoactivatable aggregation-induced emission of triphenylmethanol",
            "doi": "10.1039/C7CC04693F",
            "url": "https://pubs.rsc.org/en/content/articlelanding/2017/cc/c7cc04693f",
            "pdf_url": None,
            "source_url": "https://pubs.rsc.org/en/content/articlelanding/2017/cc/c7cc04693f",
            "source_title": None,
            "why_this_matches": "Title and DOI are in source.",
        }
    ]

    papers, deduped = postprocess_candidate_papers(candidates, sources, max_papers=10)

    assert deduped == 0
    assert len(papers) == 1
    assert papers[0]["doi"] == "10.1039/c7cc04693f"
    assert papers[0]["url"] == "https://doi.org/10.1039/c7cc04693f"


def test_doi_not_visible_forced_null():
    sources = _load_sources_fixture()
    candidates = [
        {
            "title": "Triphenylmethanol fluorescence overview",
            "doi": "10.1000/not-in-source",
            "url": None,
            "pdf_url": None,
            "source_url": "https://example.org/papers/triphenylmethanol-overview",
            "source_title": None,
            "why_this_matches": "Overview page.",
        }
    ]

    papers, _ = postprocess_candidate_papers(candidates, sources, max_papers=10)

    assert len(papers) == 1
    assert papers[0]["doi"] is None
    assert papers[0]["source_url"] == "https://example.org/papers/triphenylmethanol-overview"


def test_source_url_must_come_from_sources_else_drop():
    sources = _load_sources_fixture()
    candidates = [
        {
            "title": "Untrusted source candidate",
            "doi": None,
            "url": "https://malicious.example.net/fake",
            "pdf_url": None,
            "source_url": "https://malicious.example.net/fake",
            "source_title": None,
            "why_this_matches": "Not trusted.",
        },
        {
            "title": "Trusted source candidate",
            "doi": None,
            "url": None,
            "pdf_url": None,
            "source_url": "https://example.org/papers/triphenylmethanol-overview",
            "source_title": None,
            "why_this_matches": "Trusted.",
        },
    ]

    papers, _ = postprocess_candidate_papers(candidates, sources, max_papers=10)
    source_urls = {s["url"] for s in sources}

    assert len(papers) == 1
    assert papers[0]["source_url"] in source_urls


def test_dedupe_prefers_doi_then_normalized_title():
    sources = _load_sources_fixture()
    candidates = [
        {
            "title": "Photoactivatable aggregation-induced emission of triphenylmethanol",
            "doi": "10.1039/C7CC04693F",
            "url": "https://pubs.rsc.org/en/content/articlelanding/2017/cc/c7cc04693f",
            "pdf_url": None,
            "source_url": "https://pubs.rsc.org/en/content/articlelanding/2017/cc/c7cc04693f",
            "source_title": None,
            "why_this_matches": "same DOI duplicate 1",
        },
        {
            "title": "Photoactivatable aggregation-induced emission of triphenylmethanol (duplicate)",
            "doi": "10.1039/c7cc04693f",
            "url": "https://pubs.rsc.org/en/content/articlelanding/2017/cc/c7cc04693f",
            "pdf_url": None,
            "source_url": "https://pubs.rsc.org/en/content/articlelanding/2017/cc/c7cc04693f",
            "source_title": None,
            "why_this_matches": "same DOI duplicate 2",
        },
        {
            "title": "Triphenylmethanol fluorescence overview",
            "doi": None,
            "url": None,
            "pdf_url": None,
            "source_url": "https://example.org/papers/triphenylmethanol-overview",
            "source_title": None,
            "why_this_matches": "title duplicate 1",
        },
        {
            "title": "Triphenylmethanol   fluorescence overview",
            "doi": None,
            "url": None,
            "pdf_url": None,
            "source_url": "https://example.org/papers/triphenylmethanol-overview",
            "source_title": None,
            "why_this_matches": "title duplicate 2",
        },
    ]

    papers, deduped = postprocess_candidate_papers(candidates, sources, max_papers=10)

    assert len(papers) == 2
    assert deduped == 2


def test_same_origin_source_url_mapping_allowed():
    sources = _load_sources_fixture()
    candidates = [
        {
            "title": "Same-origin candidate",
            "doi": None,
            "url": "https://example.org/some/other/path",
            "pdf_url": None,
            "source_url": "https://example.org/some/other/path",
            "source_title": None,
            "why_this_matches": "Same origin as provided sources.",
        }
    ]

    papers, _ = postprocess_candidate_papers(candidates, sources, max_papers=10)
    assert len(papers) == 1
    assert papers[0]["source_url"].startswith("https://example.org/")
