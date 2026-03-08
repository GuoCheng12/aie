"""
src/agents/web_search_candidate_papers.py

P4-pre two-stage orchestration:
1) Stage A: Responses API + web_search tool -> collect sources
2) Stage B: Responses API without tools -> structure papers from Stage A sources only

Output is strict JSON:
{
  "papers": [...],
  "stats": {"sources_in": N, "papers_out": M, "deduped": K}
}
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import threading
import time
from pathlib import Path
from typing import Any, Dict, List, Optional
from urllib.parse import urlparse

from openai import APIConnectionError, APITimeoutError, OpenAI

from src.agents.literature_structurer import structure_candidates_from_sources

try:
    import httpx
except Exception:  # pragma: no cover
    httpx = None


DEFAULT_BASE_URL = "http://35.220.164.252:3888/v1"
DEFAULT_MODEL = "gpt-5-mini"
DEFAULT_STAGE_A_PROVIDER = "openai_web_search"
DEFAULT_STAGE_A_MODEL = "gemini-2.5-flash"
DEFAULT_SOURCE_POLICY = "publisher_article_only_v1"

_GENERIC_ALIAS_RE = re.compile(r"^compound\s*\d+$", re.IGNORECASE)
_MD_LINK_RE = re.compile(r"\[([^\]]+)\]\((https?://[^)\s]+)\)")

_CURATED_POLICY_NAME = "journal_allowlist_v1"
_PUBLISHER_POLICY_NAME = "publisher_article_only_v1"
_WILEY_ADV_JOURNAL_IDS = ("16163028", "15214095")
_WILEY_ADV_TITLE_HINTS = ("advanced functional materials", "advanced materials")
_IOP_TITLE_HINT = "materials future"
_ACS_TITLE_HINT = "acs nano"
_NATURE_TITLE_HINT = "nature materials"
_DOI_RE = re.compile(r"\b10\.\d{4,9}/[-._;()/:A-Z0-9]+\b", re.IGNORECASE)

_PREFERRED_SOURCE_HOSTS = {
    "doi.org",
    "pubs.rsc.org",
    "onlinelibrary.wiley.com",
    "advanced.onlinelibrary.wiley.com",
    "pubs.acs.org",
    "nature.com",
    "sciencedirect.com",
    "link.springer.com",
    "springer.com",
    "pubmed.ncbi.nlm.nih.gov",
    "europepmc.org",
    "nih.gov",
    "ncbi.nlm.nih.gov",
}
_DEPRIORITIZED_SOURCE_HOSTS = {
    "en.wikipedia.org",
    "wikipedia.org",
    "chemistry.stackexchange.com",
    "stackexchange.com",
    "chemicalbook.com",
}
_BLOCKED_HOSTS_EXACT = {
    "en.wikipedia.org",
    "wikipedia.org",
    "chemistry.stackexchange.com",
    "stackexchange.com",
    "chemicalbook.com",
    "guidechem.com",
    "lookchem.com",
    "chemspider.com",
    "molview.org",
    "x-mol.com",
    "nlist.inflibnet.ac.in",
    "mindat.org",
}
_BLOCKED_HOST_SUBSTRINGS = {
    "sci-hub",
    "bocsci.com",
}
_PUBLISHER_HOST_SUFFIXES = (
    "doi.org",
    "rsc.org",
    "onlinelibrary.wiley.com",
    "wiley.com",
    "sciencedirect.com",
    "springer.com",
    "nature.com",
    "pubs.acs.org",
    "acs.org",
    "iopscience.iop.org",
    "pubmed.ncbi.nlm.nih.gov",
    "europepmc.org",
    "nih.gov",
    "ncbi.nlm.nih.gov",
    "journals.iucr.org",
)
_ARTICLE_PATH_HINTS = (
    "/doi/",
    "/article",
    "/articles/",
    "/articlelanding/",
    "/science/article/",
    "/abs/pii/",
    "/pdf",
    "/pubmed/",
    "/pmc/articles/",
    "/record/",
    "/content/",
)


def _strip_openai_v1_suffix(base_url: str) -> str:
    """
    Accept base_url as either gateway root (http://host:port) or OpenAI path (http://host:port/v1).
    Return gateway root without trailing slash.
    """
    u = base_url.rstrip("/")
    if u.endswith("/v1"):
        u = u[: -len("/v1")]
    return u.rstrip("/")


def _mask_key(key: str) -> str:
    s = key.strip()
    if len(s) <= 8:
        return "***"
    return s[:4] + "..." + s[-4:]


def _norm_str(value: Any) -> Optional[str]:
    if value is None:
        return None
    s = str(value).strip()
    return s if s else None


def _filter_aliases(aliases: List[str]) -> List[str]:
    out: List[str] = []
    for alias in (_norm_str(x) for x in aliases):
        if alias is None:
            continue
        if _GENERIC_ALIAS_RE.match(alias):
            continue
        out.append(alias)
    return out


def _normalize_host_path(url: Optional[str]) -> tuple[Optional[str], Optional[str]]:
    raw = _norm_str(url)
    if raw is None:
        return None, None
    try:
        parsed = urlparse(raw)
    except Exception:
        return None, None
    host = parsed.netloc.lower()
    if host.startswith("www."):
        host = host[4:]
    path = parsed.path.lower()
    return host or None, path or "/"


def _source_matches_curated_allowlist(source: Dict[str, Any]) -> tuple[bool, str]:
    host, path = _normalize_host_path(source.get("url"))
    if host is None or path is None:
        return False, "invalid_url"

    title = (_norm_str(source.get("title")) or "").lower()
    snippet = (_norm_str(source.get("snippet")) or "").lower()
    text_blob = f"{title} {snippet}".strip()

    # Advanced Functional Materials / Advanced Materials
    if host == "advanced.onlinelibrary.wiley.com":
        # Wiley Advanced journals live on this subdomain; journal-specific enforcement may require page fetch.
        return True, "wiley_advanced_platform"
    if host == "onlinelibrary.wiley.com":
        if any(f"/journal/{journal_id}" in path for journal_id in _WILEY_ADV_JOURNAL_IDS):
            return True, "wiley_journal_id"
        if any(hint in text_blob for hint in _WILEY_ADV_TITLE_HINTS):
            return True, "wiley_title_hint"
        # Article landing pages typically use /doi/... without journal id in the URL; keep as platform allowlist.
        return True, "wiley_platform"

    # Materials Future
    if host == "iopscience.iop.org":
        if "/journal/2752-5724" in path or _IOP_TITLE_HINT in text_blob:
            return True, "iop_materials_future"
        # Article pages may not include the journal slug in the URL; keep as platform allowlist.
        return True, "iop_platform"

    # ACS Nano
    if host == "pubs.acs.org":
        if "/journal/ancac3" in path or _ACS_TITLE_HINT in text_blob:
            return True, "acs_nano"
        # Article pages typically use /doi/... without journal id in the URL; keep as platform allowlist.
        return True, "acs_platform"

    # Nature Materials
    if host.endswith("nature.com"):
        if path.startswith("/nmat") or "/nmat/" in path or _NATURE_TITLE_HINT in text_blob:
            return True, "nature_materials"
        # Nature article URLs often use /articles/... without journal slug; keep as platform allowlist.
        return True, "nature_platform"

    return False, "host_not_allowlisted"


def _apply_curated_source_allowlist(sources: List[Dict[str, Any]]) -> tuple[List[Dict[str, Any]], int, Dict[str, int]]:
    kept: List[Dict[str, Any]] = []
    dropped = 0
    reasons: Dict[str, int] = {}
    for source in sources:
        allowed, reason = _source_matches_curated_allowlist(source)
        if allowed:
            kept.append(source)
        else:
            dropped += 1
            reasons[reason] = reasons.get(reason, 0) + 1
    return kept, dropped, reasons


def _looks_like_article_path(path: str) -> bool:
    p = (path or "").lower()
    if any(h in p for h in _ARTICLE_PATH_HINTS):
        return True
    return bool(_DOI_RE.search(p))


def _has_visible_doi(source: Dict[str, Any]) -> bool:
    blob = " ".join(
        [
            _norm_str(source.get("url")) or "",
            _norm_str(source.get("title")) or "",
            _norm_str(source.get("snippet")) or "",
        ]
    )
    return bool(_DOI_RE.search(blob))


def _is_publisher_host(host: str) -> bool:
    if not host:
        return False
    return any(host == suffix or host.endswith("." + suffix) for suffix in _PUBLISHER_HOST_SUFFIXES)


def _source_matches_publisher_article_policy(source: Dict[str, Any]) -> tuple[bool, str]:
    host, path = _normalize_host_path(source.get("url"))
    if host is None or path is None:
        return False, "invalid_url"

    if host in _BLOCKED_HOSTS_EXACT:
        return False, "host_blocked"
    if any(token in host for token in _BLOCKED_HOST_SUBSTRINGS):
        return False, "host_blocked"
    if host in _DEPRIORITIZED_SOURCE_HOSTS:
        return False, "host_deprioritized"

    if not _is_publisher_host(host):
        # Unknown hosts only pass if DOI is explicit and path looks article-like.
        if _has_visible_doi(source) and _looks_like_article_path(path):
            return True, "unknown_host_with_doi"
        return False, "host_not_publisher"

    # Publisher hosts still need article-ish evidence to avoid journal homepages/index pages.
    if _looks_like_article_path(path):
        return True, "publisher_article_path"
    if _has_visible_doi(source):
        return True, "publisher_doi_visible"
    return False, "publisher_non_article_path"


def _apply_publisher_article_policy(sources: List[Dict[str, Any]]) -> tuple[List[Dict[str, Any]], int, Dict[str, int]]:
    kept: List[Dict[str, Any]] = []
    dropped = 0
    reasons: Dict[str, int] = {}
    for source in sources:
        allowed, reason = _source_matches_publisher_article_policy(source)
        if allowed:
            kept.append(source)
        else:
            dropped += 1
            reasons[reason] = reasons.get(reason, 0) + 1
    return kept, dropped, reasons


def _build_stage_a_queries(inchikey: str, aliases: List[str], max_tool_calls: Optional[int]) -> List[str]:
    aliases_clean = _filter_aliases(aliases)
    normalized_inchikey = _norm_str(inchikey) or ""

    molecule_aliases = [a for a in aliases_clean if "aggregation-induced emission" not in a.lower()]
    topic_aliases = [a for a in aliases_clean if a not in molecule_aliases]
    primary_molecule = molecule_aliases[0] if molecule_aliases else (aliases_clean[0] if aliases_clean else "")
    primary_topic = topic_aliases[0] if topic_aliases else "aggregation-induced emission"

    queries: List[str] = []
    if primary_molecule:
        queries.append(f"\"{primary_molecule}\" \"{primary_topic}\" DOI")
        queries.append(f"\"{primary_molecule}\" photoactivatable \"{primary_topic}\" DOI")
        queries.append(f"\"{primary_molecule}\" fluorescence photoluminescence solid")
        if normalized_inchikey:
            queries.append(f"{normalized_inchikey} PubChem")
    else:
        queries.append(f"{normalized_inchikey} DOI")
        queries.append(f"{normalized_inchikey} PubChem")
        queries.append(f"{normalized_inchikey} fluorescence photoluminescence")
        queries.append(f"{normalized_inchikey} \"aggregation-induced emission\"")

    deduped: List[str] = []
    seen: set[str] = set()
    for query in queries:
        key = query.strip().lower()
        if not key or key in seen:
            continue
        seen.add(key)
        deduped.append(query.strip())

    budget = 4 if max_tool_calls is None else max(1, int(max_tool_calls))
    return deduped[:budget]


def _source_quality_score(url: Optional[str]) -> int:
    host, _ = _normalize_host_path(url)
    if host is None:
        return 0
    if host in _DEPRIORITIZED_SOURCE_HOSTS:
        return 0
    if host in _PREFERRED_SOURCE_HOSTS:
        return 2 if host != "doi.org" else 3
    # Favor obvious publisher subdomains (e.g., journals.iucr.org).
    if host.endswith(".org") or host.endswith(".edu"):
        return 1
    return 1


def _sort_sources_by_quality(sources: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    # Stable sort: prefer higher-quality hosts; tie-breaker by URL for determinism.
    def _key(src: Dict[str, Any]) -> tuple[int, str]:
        url = _norm_str(src.get("url")) or ""
        return (-_source_quality_score(url), url)

    return sorted(list(sources), key=_key)


def _start_progress_heartbeat(enabled: bool, interval_sec: float) -> tuple[Optional[threading.Event], float]:
    if not enabled:
        return None, 0.0

    stop = threading.Event()
    start = time.monotonic()

    def _run() -> None:
        while not stop.wait(interval_sec):
            elapsed = int(time.monotonic() - start)
            print(f"[progress] still running... elapsed={elapsed}s", file=sys.stderr, flush=True)

    t = threading.Thread(target=_run, daemon=True)
    t.start()
    return stop, start


def _get_gateway_error_code(err: BaseException) -> Optional[str]:
    body = getattr(err, "body", None)
    if isinstance(body, dict):
        e = body.get("error")
        if isinstance(e, dict):
            code = e.get("code")
            if isinstance(code, str) and code.strip():
                return code.strip()
    if "convert_request_failed" in str(err):
        return "convert_request_failed"
    return None


def _is_reasoning_effort_unsupported(err: BaseException) -> bool:
    text = str(err).lower()
    return "reasoning.effort" in text and ("unsupported value" in text or "unsupported_value" in text)


def _extract_gemini_grounding_sources(resp_json: Dict[str, Any]) -> tuple[List[Dict[str, Any]], List[str]]:
    """
    Extract grounding sources from Gemini native generateContent response.
    Returns (sources, web_search_queries).

    Note: Gemini grounding sources often use redirect URLs; caller should resolve to final landing URLs.
    """
    candidates = resp_json.get("candidates")
    if not isinstance(candidates, list) or not candidates:
        return [], []
    cand0 = candidates[0] if isinstance(candidates[0], dict) else None
    if not isinstance(cand0, dict):
        return [], []

    gm = cand0.get("groundingMetadata") or cand0.get("grounding_metadata")
    if not isinstance(gm, dict):
        return [], []

    queries: List[str] = []
    raw_queries = gm.get("webSearchQueries") or gm.get("web_search_queries")
    if isinstance(raw_queries, list):
        for q in raw_queries:
            if isinstance(q, str) and q.strip():
                queries.append(q.strip())

    sources: List[Dict[str, Any]] = []
    chunks = gm.get("groundingChunks") or gm.get("grounding_chunks") or []
    if not isinstance(chunks, list):
        return sources, queries
    for ch in chunks:
        if not isinstance(ch, dict):
            continue
        web = ch.get("web")
        if not isinstance(web, dict):
            continue
        uri = _norm_str(web.get("uri") or web.get("url"))
        if uri is None:
            continue
        title = _norm_str(web.get("title")) or ""
        sources.append({"title": title, "url": uri, "snippet": ""})

    # Deduplicate by raw URL to preserve deterministic ordering.
    deduped: List[Dict[str, Any]] = []
    seen: set[str] = set()
    for s in sources:
        u = _norm_str(s.get("url"))
        if u is None or u in seen:
            continue
        seen.add(u)
        deduped.append(s)
    return deduped, queries


def _resolve_final_url(http_client: "httpx.Client", url: str, timeout_sec: int) -> Optional[str]:
    """
    Resolve redirects and return the final landing URL.
    Uses streaming to avoid downloading large bodies.
    """
    raw = _norm_str(url)
    if raw is None:
        return None
    try:
        with http_client.stream("GET", raw, follow_redirects=True, timeout=float(timeout_sec)) as resp:
            final = str(resp.url)
            return final.strip() if final else None
    except Exception:
        return None


def _resolve_sources_to_final_urls(
    http_client: "httpx.Client", sources: List[Dict[str, Any]], timeout_sec: int, debug: bool
) -> tuple[List[Dict[str, Any]], int]:
    """
    Resolve sources' URLs to final landing URLs. Drops sources that cannot be resolved.
    Returns (resolved_sources, resolve_fail_count).
    """
    resolved: List[Dict[str, Any]] = []
    failed = 0
    for src in sources:
        raw_url = _norm_str(src.get("url"))
        if raw_url is None:
            failed += 1
            continue
        final = _resolve_final_url(http_client=http_client, url=raw_url, timeout_sec=timeout_sec)
        if final is None:
            failed += 1
            continue
        item = dict(src)
        item["url"] = final
        if debug:
            item["raw_url"] = raw_url
        resolved.append(item)

    # Deduplicate by final URL, keep first occurrence.
    deduped: List[Dict[str, Any]] = []
    seen: set[str] = set()
    for src in resolved:
        u = _norm_str(src.get("url"))
        if u is None or u in seen:
            continue
        seen.add(u)
        deduped.append(src)
    return deduped, failed


def _gemini_stage_a_generate_content(
    http_client: "httpx.Client",
    gateway_root: str,
    model: str,
    api_key: str,
    prompt_text: str,
    temperature: float,
    max_output_tokens: int,
    timeout_sec: int,
) -> Dict[str, Any]:
    """
    Call Gemini native generateContent endpoint with google_search tool enabled.
    Returns parsed JSON response.
    """
    base = gateway_root.rstrip("/")
    url = f"{base}/v1beta/models/{model}:generateContent"
    params = {"key": api_key.strip()}
    payload: Dict[str, Any] = {
        "contents": [{"role": "user", "parts": [{"text": prompt_text}]}],
        "tools": [{"google_search": {}}],
        "generationConfig": {
            "temperature": float(temperature),
            "maxOutputTokens": int(max_output_tokens),
        },
        # Keep permissive for debugging; policy should be enforced downstream via allowlist + trust boundary.
        "safetySettings": [
            {"category": "HARM_CATEGORY_HATE_SPEECH", "threshold": "OFF"},
            {"category": "HARM_CATEGORY_DANGEROUS_CONTENT", "threshold": "OFF"},
            {"category": "HARM_CATEGORY_SEXUALLY_EXPLICIT", "threshold": "OFF"},
            {"category": "HARM_CATEGORY_HARASSMENT", "threshold": "OFF"},
        ],
    }
    resp = http_client.post(url, params=params, json=payload, timeout=float(timeout_sec))
    if resp.status_code >= 400:
        body_preview = resp.text[:2000] if isinstance(resp.text, str) else ""
        raise RuntimeError(
            f"Gemini Stage A HTTP {resp.status_code} (model={model}, key={_mask_key(api_key)}): {body_preview}"
        )
    try:
        return resp.json()
    except Exception:
        body_preview = resp.text[:2000] if isinstance(resp.text, str) else ""
        raise RuntimeError(f"Gemini Stage A returned non-JSON (model={model}): {body_preview}") from None


def _extract_web_sources(resp_json: Dict[str, Any]) -> List[Dict[str, Any]]:
    """
    Extract web sources across common gateway response shapes.
    Deduplicate by URL.
    """
    raw_sources: List[Dict[str, Any]] = []
    output_items = resp_json.get("output", []) or []

    def _add_source_dict(item: Any) -> None:
        if not isinstance(item, dict):
            return
        url = _norm_str(item.get("url") or item.get("link"))
        if url is None:
            return
        title = _norm_str(item.get("title") or item.get("name")) or ""
        snippet = _norm_str(item.get("snippet") or item.get("description") or item.get("text")) or ""
        raw_sources.append({"title": title, "url": url, "snippet": snippet})

    def _add_source_fields(title: Any, url: Any, snippet: Any = None) -> None:
        u = _norm_str(url)
        if u is None:
            return
        raw_sources.append(
            {
                "title": _norm_str(title) or "",
                "url": u,
                "snippet": _norm_str(snippet) or "",
            }
        )

    for item in output_items:
        if not isinstance(item, dict):
            continue
        if item.get("type") != "web_search_call":
            continue
        action = item.get("action") or {}
        sources = action.get("sources")
        if isinstance(sources, list):
            for source in sources:
                _add_source_dict(source)

    for item in output_items:
        if not isinstance(item, dict):
            continue
        if item.get("type") not in {"tool_call", "tool", "web_search", "web_search_result"}:
            continue
        action = item.get("action") or {}
        for maybe_list in (action.get("sources"), item.get("sources"), item.get("results")):
            if isinstance(maybe_list, list):
                for source in maybe_list:
                    if isinstance(source, dict):
                        _add_source_dict(source)

    for item in output_items:
        if not isinstance(item, dict):
            continue
        if item.get("type") != "message":
            continue
        for content in item.get("content", []) or []:
            if not isinstance(content, dict):
                continue
            anns = content.get("annotations")
            if isinstance(anns, list):
                for annotation in anns:
                    if not isinstance(annotation, dict):
                        continue
                    if annotation.get("type") != "url_citation":
                        continue
                    _add_source_fields(
                        annotation.get("title"),
                        annotation.get("url") or annotation.get("source_url"),
                        annotation.get("snippet"),
                    )
            if content.get("type") == "output_text":
                text = content.get("text")
                if isinstance(text, str):
                    for m in _MD_LINK_RE.finditer(text):
                        _add_source_fields(m.group(1), m.group(2))

    deduped: List[Dict[str, Any]] = []
    seen: set[str] = set()
    for source in raw_sources:
        url = _norm_str(source.get("url"))
        if url is None:
            continue
        if url in seen:
            continue
        seen.add(url)
        deduped.append(source)
    return deduped


def _extract_web_search_calls(resp_json: Dict[str, Any]) -> List[Dict[str, Any]]:
    calls: List[Dict[str, Any]] = []
    for item in resp_json.get("output", []) or []:
        if not isinstance(item, dict):
            continue
        if item.get("type") != "web_search_call":
            continue
        action = item.get("action") or {}
        calls.append(
            {
                "id": item.get("id"),
                "status": item.get("status"),
                "query": action.get("query"),
            }
        )
    return calls


def _build_stage_a_request(
    model: str,
    inchikey: str,
    aliases: List[str],
    max_papers: int,
    tool_type: str,
    max_tool_calls: Optional[int],
    max_output_tokens: Optional[int],
    reasoning_effort: Optional[str],
    minimal_request: bool,
) -> Dict[str, Any]:
    aliases_clean = _filter_aliases(aliases)
    aliases_str = ", ".join(aliases_clean) if aliases_clean else "(none)"
    query_plan = _build_stage_a_queries(inchikey=inchikey, aliases=aliases_clean, max_tool_calls=max_tool_calls)
    query_plan_text = "\n".join([f"{idx + 1}. {q}" for idx, q in enumerate(query_plan)])

    instructions = (
        "You are a scientific literature scout. Use web_search to collect candidate paper sources. "
        "Stability rules: execute ONLY the provided query plan in order; keep retrieval deterministic; "
        "prefer publisher/journal pages (sciencedirect, wiley, rsc, acs, springer, nature, pubmed/europepmc). "
        "Avoid generic pages (wikipedia, stackexchange, chemicalbook, blogs, shopping, patent directories)."
    )
    user_input = (
        "Search paper candidates for this molecule using the fixed query plan.\n"
        f"InChIKey: {inchikey}\n"
        f"Aliases: {aliases_str}\n"
        f"Expected downstream cap: {max_papers} papers.\n"
        "Run exactly these queries (in order):\n"
        f"{query_plan_text}\n"
    )

    if minimal_request:
        return {
            "model": model,
            "input": instructions + "\n\n" + user_input,
            "tools": [{"type": tool_type}],
        }

    body: Dict[str, Any] = {
        "model": model,
        "instructions": instructions,
        "input": user_input,
        "tools": [{"type": tool_type}],
        "include": ["web_search_call.action.sources"],
    }
    if max_tool_calls is not None:
        body["max_tool_calls"] = int(max_tool_calls)
    if max_output_tokens is not None:
        body["max_output_tokens"] = int(max_output_tokens)
    if reasoning_effort is not None:
        body["reasoning"] = {"effort": reasoning_effort}
    return body


def _write_json(path: str, payload: Dict[str, Any]) -> None:
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    with open(target, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2, sort_keys=True)


def main() -> None:
    parser = argparse.ArgumentParser(description="P4-pre two-stage literature candidate retrieval")
    parser.add_argument("--base_url", default=DEFAULT_BASE_URL)
    parser.add_argument("--model", default=DEFAULT_MODEL, help="Stage B model (structuring; no tools).")
    parser.add_argument("--api_key", default=os.getenv("OPENAI_API_KEY", ""))
    parser.add_argument(
        "--stage_a_provider",
        default=DEFAULT_STAGE_A_PROVIDER,
        choices=["gemini_google_search", "openai_web_search"],
        help="Stage A search backend (default: gemini_google_search).",
    )
    parser.add_argument(
        "--stage_a_model",
        default=DEFAULT_STAGE_A_MODEL,
        help="Stage A Gemini model name (used when stage_a_provider=gemini_google_search).",
    )
    parser.add_argument(
        "--stage_a_temperature",
        type=float,
        default=0.2,
        help="Stage A Gemini temperature (default: 0.2).",
    )
    parser.add_argument(
        "--source_policy",
        default=DEFAULT_SOURCE_POLICY,
        choices=["allow_all", _CURATED_POLICY_NAME, _PUBLISHER_POLICY_NAME],
        help="Stage A source forwarding policy before Stage B (default: publisher_article_only_v1).",
    )
    parser.add_argument("--inchikey", required=True)
    parser.add_argument("--alias", action="append", default=[], help="Alias/common name; repeatable")
    parser.add_argument("--max_papers", type=int, default=15)
    parser.add_argument(
        "--minimal_request",
        action="store_true",
        help="Stage A fallback mode: send minimal request fields only.",
    )
    parser.add_argument(
        "--tool_type",
        default="web_search_preview",
        choices=["web_search_preview", "web_search"],
        help="Web search tool type for Stage A.",
    )
    parser.add_argument(
        "--max_tool_calls",
        type=int,
        default=4,
        help="Stage A max web_search calls (default: 4).",
    )
    parser.add_argument(
        "--max_output_tokens",
        type=int,
        default=1500,
        help="Output token cap for each stage; <=0 means omit.",
    )
    parser.add_argument(
        "--max_sources_for_stage_b",
        type=int,
        default=20,
        help="Max sources forwarded from Stage A to Stage B (default: 20).",
    )
    parser.add_argument(
        "--reasoning_effort",
        default="minimal",
        choices=["minimal", "low", "medium", "high"],
        help="Thinking strength for reasoning-capable models (default: minimal; ignored in --minimal_request fallback).",
    )
    parser.add_argument("--timeout_sec", type=int, default=120)
    parser.add_argument("--no_proxy", action="store_true")
    parser.add_argument("--progress", action="store_true")
    parser.add_argument("--progress_interval_sec", type=float, default=5.0)
    parser.add_argument("--load_raw", default=None, help="Load Stage A raw JSON and skip Stage A network call.")
    parser.add_argument("--dump_raw", default=None, help="Dump Stage A raw JSON response for debugging.")
    parser.add_argument("--out", default=None, help="Optional path to save final JSON.")
    parser.add_argument("--debug", action="store_true")
    parser.add_argument(
        "--strict_source_url_match",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="Deprecated compatibility flag; strict trust-boundary is always enforced in Stage B.",
    )
    args = parser.parse_args()

    base_url = args.base_url.rstrip("/")
    if not base_url.endswith("/v1"):
        base_url = base_url + "/v1"

    if args.debug:
        planned_queries = _build_stage_a_queries(
            inchikey=args.inchikey.strip(),
            aliases=_filter_aliases(args.alias),
            max_tool_calls=(int(args.max_tool_calls) if int(args.max_tool_calls) > 0 else None),
        )
        print(f"[debug] base_url={base_url}", file=sys.stderr)
        print(f"[debug] stage_a_provider={args.stage_a_provider}", file=sys.stderr)
        print(f"[debug] stage_a_model={args.stage_a_model}", file=sys.stderr)
        print(f"[debug] stage_a_temperature={args.stage_a_temperature}", file=sys.stderr)
        print(f"[debug] model={args.model}", file=sys.stderr)
        print(f"[debug] inchikey={args.inchikey}", file=sys.stderr)
        print(f"[debug] aliases(raw)={args.alias}", file=sys.stderr)
        print(f"[debug] aliases(filtered)={_filter_aliases(args.alias)}", file=sys.stderr)
        print(f"[debug] stage_a_query_plan={planned_queries}", file=sys.stderr)
        print(f"[debug] minimal_request={bool(args.minimal_request)}", file=sys.stderr)
        print(f"[debug] tool_type={args.tool_type}", file=sys.stderr)
        print(f"[debug] max_tool_calls={args.max_tool_calls}", file=sys.stderr)
        print(f"[debug] max_output_tokens={args.max_output_tokens}", file=sys.stderr)
        print(f"[debug] max_sources_for_stage_b={args.max_sources_for_stage_b}", file=sys.stderr)
        print(f"[debug] reasoning_effort={args.reasoning_effort}", file=sys.stderr)
        print(f"[debug] source_policy={args.source_policy}", file=sys.stderr)
        if args.load_raw:
            print(f"[debug] load_raw={args.load_raw}", file=sys.stderr)
        if args.dump_raw:
            print(f"[debug] dump_raw={args.dump_raw}", file=sys.stderr)

    if args.strict_source_url_match is False:
        print("[warn] --no-strict_source_url_match is deprecated; strict matching remains enforced", file=sys.stderr)

    gateway_root = _strip_openai_v1_suffix(base_url)

    http_client = None
    if httpx is not None:
        timeout = httpx.Timeout(float(args.timeout_sec))
        http_client = httpx.Client(timeout=timeout, trust_env=(not args.no_proxy))
    client = OpenAI(base_url=base_url, api_key=args.api_key.strip(), http_client=http_client)

    # Stage A
    stage_a_resp_json: Dict[str, Any]
    if args.load_raw:
        with open(args.load_raw, "r", encoding="utf-8") as f:
            stage_a_resp_json = json.load(f)
    else:
        if args.stage_a_provider == "gemini_google_search":
            if http_client is None:
                raise RuntimeError("Stage A provider gemini_google_search requires httpx to be installed")
            aliases_clean = _filter_aliases(args.alias)
            query_plan = _build_stage_a_queries(
                inchikey=args.inchikey.strip(),
                aliases=aliases_clean,
                max_tool_calls=(int(args.max_tool_calls) if int(args.max_tool_calls) > 0 else None),
            )
            query_plan_text = "\n".join([f"{idx + 1}. {q}" for idx, q in enumerate(query_plan)])
            aliases_str = ", ".join(aliases_clean) if aliases_clean else "(none)"
            stage_a_prompt = (
                "You are a scientific literature scout. Use google_search to collect candidate paper sources.\n"
                "Stability rules: execute ONLY the provided query plan in order; prefer paper landing pages.\n"
                "Avoid generic pages (wikipedia, stackexchange, chemicalbook, blogs, shopping, patent directories).\n"
                f"InChIKey: {args.inchikey.strip()}\n"
                f"Aliases: {aliases_str}\n"
                "Run exactly these queries (in order):\n"
                f"{query_plan_text}\n"
            )

            if args.progress:
                print("[progress] calling Stage A Gemini /v1beta (google_search)...", file=sys.stderr, flush=True)
            stop_event, start_t = _start_progress_heartbeat(args.progress, float(args.progress_interval_sec))
            try:
                stage_a_resp_json = _gemini_stage_a_generate_content(
                    http_client=http_client,
                    gateway_root=gateway_root,
                    model=str(args.stage_a_model),
                    api_key=args.api_key.strip(),
                    prompt_text=stage_a_prompt,
                    temperature=float(args.stage_a_temperature),
                    max_output_tokens=max(1, int(args.max_output_tokens)),
                    timeout_sec=int(args.timeout_sec),
                )
            finally:
                if stop_event is not None:
                    stop_event.set()
                if args.progress:
                    elapsed = int(time.monotonic() - start_t) if start_t else 0
                    print(f"[progress] Stage A ended (elapsed={elapsed}s)", file=sys.stderr, flush=True)
        else:
            stage_a_reasoning_effort: Optional[str] = args.reasoning_effort if not args.minimal_request else None
            if stage_a_reasoning_effort == "minimal":
                print(
                    "[warn] Stage A does not pass reasoning.effort=minimal with web_search tools; omitting reasoning for compatibility",
                    file=sys.stderr,
                )
                stage_a_reasoning_effort = None

            req_body = _build_stage_a_request(
                model=args.model,
                inchikey=args.inchikey.strip(),
                aliases=args.alias,
                max_papers=max(1, int(args.max_papers)),
                tool_type=str(args.tool_type),
                max_tool_calls=(int(args.max_tool_calls) if int(args.max_tool_calls) > 0 else None),
                max_output_tokens=(int(args.max_output_tokens) if int(args.max_output_tokens) > 0 else None),
                reasoning_effort=stage_a_reasoning_effort,
                minimal_request=bool(args.minimal_request),
            )

            if args.progress:
                print("[progress] calling Stage A /v1/responses (web_search)...", file=sys.stderr, flush=True)
            stop_event, start_t = _start_progress_heartbeat(args.progress, float(args.progress_interval_sec))
            try:
                try:
                    try:
                        resp = client.responses.create(**req_body)
                    except Exception as e:
                        code = _get_gateway_error_code(e)
                        if code == "convert_request_failed" and not args.minimal_request:
                            print("[warn] gateway convert_request_failed; retrying Stage A with --minimal_request", file=sys.stderr)
                            req_body_min = _build_stage_a_request(
                                model=args.model,
                                inchikey=args.inchikey.strip(),
                                aliases=args.alias,
                                max_papers=max(1, int(args.max_papers)),
                                tool_type=str(args.tool_type),
                                max_tool_calls=(int(args.max_tool_calls) if int(args.max_tool_calls) > 0 else None),
                                max_output_tokens=(int(args.max_output_tokens) if int(args.max_output_tokens) > 0 else None),
                                reasoning_effort=None,
                                minimal_request=True,
                            )
                            resp = client.responses.create(**req_body_min)
                        else:
                            raise
                except (APITimeoutError, TimeoutError) as e:
                    elapsed = int(time.monotonic() - start_t) if start_t else 0
                    raise RuntimeError(f"Stage A timeout after ~{elapsed}s") from e
                except APIConnectionError as e:
                    elapsed = int(time.monotonic() - start_t) if start_t else 0
                    raise RuntimeError(f"Stage A connection error after ~{elapsed}s") from e
            finally:
                if stop_event is not None:
                    stop_event.set()
                if args.progress:
                    elapsed = int(time.monotonic() - start_t) if start_t else 0
                    print(f"[progress] Stage A ended (elapsed={elapsed}s)", file=sys.stderr, flush=True)

            try:
                stage_a_resp_json = resp.model_dump()
            except Exception:
                try:
                    stage_a_resp_json = resp.dict()
                except Exception:
                    stage_a_resp_json = json.loads(json.dumps(resp, default=str))

    if args.dump_raw:
        _write_json(args.dump_raw, stage_a_resp_json)

    stage_a_is_gemini = "candidates" in stage_a_resp_json
    sources_raw: List[Dict[str, Any]] = []
    sources_resolved: List[Dict[str, Any]] = []
    resolve_failed = 0
    if stage_a_is_gemini:
        sources_raw, gemini_queries = _extract_gemini_grounding_sources(stage_a_resp_json)
        if args.debug:
            print(f"[debug] gemini_web_search_queries_sample={gemini_queries[:5]}", file=sys.stderr)
        if http_client is None:
            raise RuntimeError("Gemini Stage A requires httpx client for redirect resolution")
        sources_resolved, resolve_failed = _resolve_sources_to_final_urls(
            http_client=http_client,
            sources=sources_raw,
            timeout_sec=int(args.timeout_sec),
            debug=bool(args.debug),
        )
    else:
        if args.debug:
            output_items = stage_a_resp_json.get("output", []) or []
            output_item_types = [
                str(item.get("type")) if isinstance(item, dict) else type(item).__name__ for item in output_items
            ]
            print(f"[debug] output_item_types={output_item_types}", file=sys.stderr)
            for idx, item in enumerate(output_items[:5]):
                if isinstance(item, dict):
                    print(f"[debug] output_item[{idx}] type={item.get('type')} keys={list(item.keys())}", file=sys.stderr)
                else:
                    print(f"[debug] output_item[{idx}] type={type(item).__name__}", file=sys.stderr)
            web_calls = _extract_web_search_calls(stage_a_resp_json)
            print(f"[debug] web_search_call_count={len(web_calls)}", file=sys.stderr)
            if web_calls:
                statuses = [c.get("status") for c in web_calls]
                queries = [c.get("query") for c in web_calls if isinstance(c.get("query"), str)]
                print(f"[debug] web_search_call_status_sample={statuses[:5]}", file=sys.stderr)
                print(f"[debug] web_search_queries_sample={queries[:5]}", file=sys.stderr)
        sources_raw = _extract_web_sources(stage_a_resp_json)
        sources_resolved = sources_raw

    sources_resolved = _sort_sources_by_quality(sources_resolved)
    if args.source_policy == _CURATED_POLICY_NAME:
        sources, dropped_by_allowlist, dropped_reasons = _apply_curated_source_allowlist(sources_resolved)
    elif args.source_policy == _PUBLISHER_POLICY_NAME:
        sources, dropped_by_allowlist, dropped_reasons = _apply_publisher_article_policy(sources_resolved)
    else:
        sources, dropped_by_allowlist, dropped_reasons = sources_resolved, 0, {}
    if stage_a_is_gemini:
        print(
            f"[sources] policy={args.source_policy} stage_a=gemini raw_count={len(sources_raw)} resolved_count={len(sources_resolved)} resolve_failed={resolve_failed} allowed_count={len(sources)} dropped={dropped_by_allowlist}",
            file=sys.stderr,
        )
    else:
        print(
            f"[sources] policy={args.source_policy} stage_a=openai raw_count={len(sources_raw)} allowed_count={len(sources)} dropped={dropped_by_allowlist}",
            file=sys.stderr,
        )
    if dropped_by_allowlist > 0 and args.debug:
        print(f"[sources] dropped_reasons={dropped_reasons}", file=sys.stderr)
    for idx, source in enumerate(sources[:50], start=1):
        title = _norm_str(source.get("title")) or ""
        url = _norm_str(source.get("url")) or ""
        snippet = _norm_str(source.get("snippet")) or ""
        print(f"[sources] {idx:02d} title={title} url={url} snippet={snippet}", file=sys.stderr)

    # Stage B
    sources_for_stage_b = sources[: max(0, int(args.max_sources_for_stage_b))]
    papers: List[Dict[str, Any]] = []
    deduped = 0
    stage_b_reasoning_effort: Optional[str] = args.reasoning_effort if not args.minimal_request else None
    if stage_b_reasoning_effort == "minimal" and str(args.model).startswith("gpt-5.2"):
        print(
            "[warn] Stage B maps reasoning.effort=minimal -> none for gpt-5.2 compatibility",
            file=sys.stderr,
        )
        stage_b_reasoning_effort = "none"
    if sources_for_stage_b:
        try:
            papers, deduped, _ = structure_candidates_from_sources(
                client=client,
                model=args.model,
                inchikey=args.inchikey.strip(),
                aliases=_filter_aliases(args.alias),
                sources=sources_for_stage_b,
                max_papers=max(1, int(args.max_papers)),
                max_output_tokens=(int(args.max_output_tokens) if int(args.max_output_tokens) > 0 else None),
                reasoning_effort=stage_b_reasoning_effort,
            )
        except Exception as e:
            if stage_b_reasoning_effort is not None and _is_reasoning_effort_unsupported(e):
                print("[warn] Stage B reasoning.effort unsupported; retrying without reasoning", file=sys.stderr)
                try:
                    papers, deduped, _ = structure_candidates_from_sources(
                        client=client,
                        model=args.model,
                        inchikey=args.inchikey.strip(),
                        aliases=_filter_aliases(args.alias),
                        sources=sources_for_stage_b,
                        max_papers=max(1, int(args.max_papers)),
                        max_output_tokens=(int(args.max_output_tokens) if int(args.max_output_tokens) > 0 else None),
                        reasoning_effort=None,
                    )
                except Exception as retry_err:
                    papers = []
                    deduped = 0
                    if args.debug:
                        print(f"[warn] Stage B retry failed: {retry_err}", file=sys.stderr)
            else:
                papers = []
                deduped = 0
                if args.debug:
                    print(f"[warn] Stage B failed: {e}", file=sys.stderr)

    if len(sources_for_stage_b) == 0 or len(papers) == 0:
        print("[next_action] literature=empty_verified -> suggest wetlab/min_experiment_agent", file=sys.stderr)

    payload = {
        "papers": papers,
        "stats": {
            "sources_in": int(len(sources_for_stage_b)),
            "papers_out": int(len(papers)),
            "deduped": int(deduped),
        },
    }

    if args.out:
        _write_json(args.out, payload)

    print(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
