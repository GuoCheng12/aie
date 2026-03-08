"""
src/agents/literature_structurer.py

Stage B for P4-pre literature workflow:
- Input: Stage A web-search sources only
- Output: structured candidate papers constrained to those sources

This module enforces trust boundaries:
- source_url must map to Stage A sources
- DOI may only be kept when visible in the matched source text/url
"""

from __future__ import annotations

import json
import re
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import urlparse, urlunparse


_DOI_RE = re.compile(r"\b10\.\d{4,9}/[-._;()/:A-Z0-9]+\b", re.IGNORECASE)


def _norm_str(value: Any) -> Optional[str]:
    if value is None:
        return None
    s = str(value).strip()
    return s if s else None


def _extract_first_json_object(text: str) -> Optional[str]:
    if not isinstance(text, str):
        return None
    start = text.find("{")
    if start < 0:
        return None

    depth = 0
    in_str = False
    esc = False
    for idx in range(start, len(text)):
        ch = text[idx]
        if in_str:
            if esc:
                esc = False
            elif ch == "\\":
                esc = True
            elif ch == '"':
                in_str = False
            continue

        if ch == '"':
            in_str = True
            continue
        if ch == "{":
            depth += 1
            continue
        if ch == "}":
            depth -= 1
            if depth == 0:
                return text[start : idx + 1]
            continue

    return None


def normalize_url_for_match(url: Optional[str]) -> Optional[str]:
    """
    Normalize URL for strict matching:
    - lowercase scheme/netloc
    - strip fragment
    - trim trailing slash from path
    """
    raw = _norm_str(url)
    if raw is None:
        return None
    try:
        parsed = urlparse(raw)
        scheme = parsed.scheme.lower()
        netloc = parsed.netloc.lower()
        if not scheme or not netloc:
            return None
        path = parsed.path.rstrip("/")
        normalized = urlunparse((scheme, netloc, path, "", parsed.query, ""))
        return normalized
    except Exception:
        return None


def get_url_origin(url: Optional[str]) -> Optional[str]:
    normalized = normalize_url_for_match(url)
    if normalized is None:
        return None
    parsed = urlparse(normalized)
    return f"{parsed.scheme}://{parsed.netloc}"


def normalize_title_for_dedupe(title: Optional[str]) -> Optional[str]:
    s = _norm_str(title)
    if s is None:
        return None
    s = re.sub(r"\s+", " ", s).strip().lower()
    s = re.sub(r"[^a-z0-9 ]+", "", s)
    s = re.sub(r"\s+", " ", s).strip()
    return s or None


def extract_dois(text: Optional[str]) -> List[str]:
    src = _norm_str(text)
    if src is None:
        return []
    seen: set[str] = set()
    out: List[str] = []
    for m in _DOI_RE.finditer(src):
        doi = m.group(0).rstrip(".,);")
        doi = doi.lower()
        if doi not in seen:
            seen.add(doi)
            out.append(doi)
    return out


def normalize_doi(value: Optional[str]) -> Optional[str]:
    if _norm_str(value) is None:
        return None
    s = str(value).strip()
    if s.lower().startswith("https://doi.org/"):
        s = s[len("https://doi.org/") :]
    elif s.lower().startswith("http://doi.org/"):
        s = s[len("http://doi.org/") :]
    elif s.lower().startswith("doi:"):
        s = s[4:]
    doi = s.strip().lower()
    return doi if extract_dois(doi) else None


def _source_snippet(source: Dict[str, Any]) -> str:
    return (
        _norm_str(source.get("snippet"))
        or _norm_str(source.get("description"))
        or _norm_str(source.get("text"))
        or ""
    )


def _source_text_for_doi(source: Dict[str, Any]) -> str:
    parts = [
        _norm_str(source.get("title")) or "",
        _source_snippet(source),
        _norm_str(source.get("url")) or "",
    ]
    return "\n".join(parts)


def _match_source(
    paper_source_url: Optional[str],
    source_by_norm_url: Dict[str, Dict[str, Any]],
    source_by_origin: Dict[str, List[Dict[str, Any]]],
) -> Optional[Dict[str, Any]]:
    norm = normalize_url_for_match(paper_source_url)
    if norm is None:
        return None
    matched = source_by_norm_url.get(norm)
    if matched is not None:
        return matched
    origin = get_url_origin(norm)
    if origin is None:
        return None
    candidates = source_by_origin.get(origin) or []
    return candidates[0] if candidates else None


def build_stage_b_request(
    model: str,
    inchikey: str,
    aliases: List[str],
    sources: List[Dict[str, Any]],
    max_papers: int,
    max_output_tokens: Optional[int],
    reasoning_effort: Optional[str],
) -> Dict[str, Any]:
    schema = {
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "papers": {
                "type": "array",
                "maxItems": max_papers,
                "items": {
                    "type": "object",
                    "additionalProperties": False,
                    "properties": {
                        "title": {"type": "string"},
                        "doi": {"type": ["string", "null"]},
                        "url": {"type": ["string", "null"]},
                        "pdf_url": {"type": ["string", "null"]},
                        "source_url": {"type": "string"},
                        "source_title": {"type": ["string", "null"]},
                        "why_this_matches": {"type": "string"},
                    },
                    "required": [
                        "title",
                        "doi",
                        "url",
                        "pdf_url",
                        "source_url",
                        "source_title",
                        "why_this_matches",
                    ],
                },
            }
        },
        "required": ["papers"],
    }

    aliases_str = ", ".join([a for a in aliases if _norm_str(a)]) if aliases else "(none)"
    packed_sources = []
    for idx, src in enumerate(sources, start=1):
        packed_sources.append(
            {
                "idx": idx,
                "title": _norm_str(src.get("title")) or "",
                "url": _norm_str(src.get("url")) or "",
                "snippet": _source_snippet(src),
            }
        )

    instructions = (
        "You are a literature structurer. Use ONLY the provided source list. "
        "Do not guess papers, URLs, or DOIs outside those sources. "
        "If a paper cannot be verified from provided sources, omit it. "
        "Every paper must include source_url mapped to one provided source URL."
    )
    user_input = (
        f"InChIKey: {inchikey}\n"
        f"Aliases: {aliases_str}\n"
        "Structured source list (trusted boundary):\n"
        f"{json.dumps(packed_sources, ensure_ascii=False)}"
    )

    body: Dict[str, Any] = {
        "model": model,
        "instructions": instructions,
        "input": user_input,
        "tools": [],
        "text": {
            "format": {
                "type": "json_schema",
                "name": "structured_candidate_papers",
                "strict": True,
                "schema": schema,
            }
        },
    }
    if max_output_tokens is not None:
        body["max_output_tokens"] = int(max_output_tokens)
    if reasoning_effort is not None:
        body["reasoning"] = {"effort": reasoning_effort}
    return body


def parse_stage_b_payload(resp_json: Dict[str, Any]) -> Dict[str, Any]:
    output_text_parts: List[str] = []
    for item in resp_json.get("output", []) or []:
        if not isinstance(item, dict) or item.get("type") != "message":
            continue
        for content in item.get("content", []) or []:
            if isinstance(content, dict) and content.get("type") == "output_text":
                text = content.get("text")
                if isinstance(text, str):
                    output_text_parts.append(text)
    text = "".join(output_text_parts).strip()
    if not text:
        text = _norm_str(resp_json.get("output_text")) or ""
    if not text:
        return {"papers": []}

    try:
        payload = json.loads(text)
    except json.JSONDecodeError:
        recovered = _extract_first_json_object(text)
        if recovered is None:
            return {"papers": []}
        try:
            payload = json.loads(recovered)
        except json.JSONDecodeError:
            return {"papers": []}

    if not isinstance(payload, dict):
        return {"papers": []}
    papers = payload.get("papers")
    if not isinstance(papers, list):
        return {"papers": []}
    return {"papers": papers}


def postprocess_candidate_papers(
    candidate_papers: List[Dict[str, Any]],
    sources: List[Dict[str, Any]],
    max_papers: int,
) -> Tuple[List[Dict[str, Any]], int]:
    """
    Enforce trust-boundary constraints and dedupe.
    Returns (papers, deduped_count).
    """
    source_by_norm_url: Dict[str, Dict[str, Any]] = {}
    source_by_origin: Dict[str, List[Dict[str, Any]]] = {}

    for src in sources:
        source_url = _norm_str(src.get("url"))
        source_norm = normalize_url_for_match(source_url)
        if source_norm is None:
            continue
        row = {
            "title": _norm_str(src.get("title")) or "",
            "url": source_url,
            "norm_url": source_norm,
            "snippet": _source_snippet(src),
        }
        source_by_norm_url[source_norm] = row
        origin = get_url_origin(source_norm)
        if origin is not None:
            source_by_origin.setdefault(origin, []).append(row)

    unique: List[Dict[str, Any]] = []
    seen_keys: set[str] = set()
    deduped = 0

    for raw in candidate_papers:
        if not isinstance(raw, dict):
            continue
        title = _norm_str(raw.get("title"))
        source_url_raw = _norm_str(raw.get("source_url"))
        why = _norm_str(raw.get("why_this_matches")) or "Matched from source metadata."
        if title is None or source_url_raw is None:
            continue

        source_match = _match_source(source_url_raw, source_by_norm_url, source_by_origin)
        if source_match is None:
            continue

        doi_raw = normalize_doi(_norm_str(raw.get("doi")))
        source_dois = set(extract_dois(_source_text_for_doi(source_match)))
        if doi_raw is not None and doi_raw not in source_dois:
            doi_raw = None

        normalized_title = normalize_title_for_dedupe(title)
        if doi_raw is not None:
            dedupe_key = f"doi:{doi_raw}"
        elif normalized_title is not None:
            dedupe_key = f"title:{normalized_title}"
        else:
            dedupe_key = f"title:{title.lower()}"

        if dedupe_key in seen_keys:
            deduped += 1
            continue
        seen_keys.add(dedupe_key)

        if doi_raw is not None:
            url = f"https://doi.org/{doi_raw}"
        else:
            url = _norm_str(raw.get("url")) or source_match["url"]

        pdf_url = _norm_str(raw.get("pdf_url"))

        unique.append(
            {
                "title": title,
                "doi": doi_raw,
                "url": url,
                "pdf_url": pdf_url,
                "source_url": source_match["url"],
                "source_title": source_match["title"] or None,
                "why_this_matches": why,
            }
        )
        if len(unique) >= max_papers:
            break

    return unique, deduped


def structure_candidates_from_sources(
    client: Any,
    model: str,
    inchikey: str,
    aliases: List[str],
    sources: List[Dict[str, Any]],
    max_papers: int,
    max_output_tokens: Optional[int],
    reasoning_effort: Optional[str],
) -> Tuple[List[Dict[str, Any]], int, Dict[str, Any]]:
    """
    Run Stage B (no tools) and return (papers, deduped_count, raw_response_json).
    """
    req_body = build_stage_b_request(
        model=model,
        inchikey=inchikey,
        aliases=aliases,
        sources=sources,
        max_papers=max_papers,
        max_output_tokens=max_output_tokens,
        reasoning_effort=reasoning_effort,
    )
    response = client.responses.create(**req_body)
    try:
        resp_json = response.model_dump()
    except Exception:
        try:
            resp_json = response.dict()
        except Exception:
            resp_json = json.loads(json.dumps(response, default=str))

    parsed = parse_stage_b_payload(resp_json)
    papers, deduped = postprocess_candidate_papers(
        candidate_papers=parsed.get("papers", []),
        sources=sources,
        max_papers=max_papers,
    )
    return papers, deduped, resp_json
