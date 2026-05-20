"""
search.py — Web search via DuckDuckGo (no API key) + Wikipedia.
Uses only stdlib + requests. No third-party search SDKs.
"""

import re
import json
import urllib.parse
import html
import time
import asyncio
import httpx


# ─────────────────────────────────────────────────────────────────────────────
# Shared HTTP helper
# ─────────────────────────────────────────────────────────────────────────────

HEADERS = {
    "User-Agent": (
        "ARIA/1.0 local-assistant "
        "(https://github.com/local/aria; local-only personal project)"
    ),
    "Accept-Language": "en-US,en;q=0.9",
}

def _get(url: str, timeout: int = 8) -> str:
    with httpx.Client(timeout=timeout, headers=HEADERS, follow_redirects=True) as client:
        resp = client.get(url)
        resp.raise_for_status()
        return resp.text


_CACHE: dict[str, tuple[float, str | None]] = {}
_CACHE_TTL = 300


async def _get_async(url: str, timeout: int = 8) -> str:
    async with httpx.AsyncClient(timeout=timeout, headers=HEADERS, follow_redirects=True) as client:
        resp = await client.get(url)
        resp.raise_for_status()
        return resp.text


def _cache_get(key: str) -> str | None | bool:
    item = _CACHE.get(key)
    if not item:
        return False
    ts, value = item
    if time.time() - ts > _CACHE_TTL:
        _CACHE.pop(key, None)
        return False
    return value


def _cache_set(key: str, value: str | None) -> str | None:
    _CACHE[key] = (time.time(), value)
    if len(_CACHE) > 128:
        oldest = sorted(_CACHE, key=lambda k: _CACHE[k][0])[:32]
        for k in oldest:
            _CACHE.pop(k, None)
    return value


def _run(coro):
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(coro)
    raise RuntimeError("Synchronous search API called from a running event loop")


# ─────────────────────────────────────────────────────────────────────────────
# DuckDuckGo — Instant Answer API (JSON, no key needed)
# ─────────────────────────────────────────────────────────────────────────────

DDG_INSTANT = "https://api.duckduckgo.com/?q={query}&format=json&no_html=1&skip_disambig=1"
DDG_HTML    = "https://html.duckduckgo.com/html/?q={query}"

def _clean(text: str) -> str:
    text = html.unescape(text)
    text = re.sub(r"<[^>]+>", "", text)
    text = re.sub(r"\s+", " ", text)
    return text.strip()


def duckduckgo_instant(query: str) -> str | None:
    """
    Try the DDG Instant Answer API first.
    Returns a short answer string or None.
    """
    try:
        url = DDG_INSTANT.format(query=urllib.parse.quote_plus(query))
        raw = _get(url)
        data = json.loads(raw)

        # AbstractText is the best — Wikipedia-backed summary
        if data.get("AbstractText"):
            source = data.get("AbstractSource", "")
            text = _clean(data["AbstractText"])[:600]
            return f"{text}\n\n*Source: {source}*" if source else text

        # Answer (e.g. "42", currency rates, unit conversions)
        if data.get("Answer"):
            return _clean(data["Answer"])

        # Definition
        if data.get("Definition"):
            src = data.get("DefinitionSource", "")
            return f"{_clean(data['Definition'])}\n\n*Source: {src}*" if src else _clean(data["Definition"])

        # Related topics — grab first 3 snippets
        topics = data.get("RelatedTopics", [])
        snippets = []
        for t in topics:
            if isinstance(t, dict) and t.get("Text"):
                snippets.append("• " + _clean(t["Text"])[:180])
            if len(snippets) >= 3:
                break
        if snippets:
            return "Here's what I found:\n\n" + "\n".join(snippets)

    except Exception:
        pass
    return None


async def duckduckgo_instant_async(query: str) -> str | None:
    cache_key = f"ddg-instant:{query.lower()}"
    cached = _cache_get(cache_key)
    if cached is not False:
        return cached
    try:
        url = DDG_INSTANT.format(query=urllib.parse.quote_plus(query))
        data = json.loads(await _get_async(url))
        if data.get("AbstractText"):
            source = data.get("AbstractSource", "")
            text = _clean(data["AbstractText"])[:600]
            return _cache_set(cache_key, f"{text}\n\n*Source: {source}*" if source else text)
        if data.get("Answer"):
            return _cache_set(cache_key, _clean(data["Answer"]))
        if data.get("Definition"):
            src = data.get("DefinitionSource", "")
            value = f"{_clean(data['Definition'])}\n\n*Source: {src}*" if src else _clean(data["Definition"])
            return _cache_set(cache_key, value)
    except Exception:
        pass
    return _cache_set(cache_key, None)


def duckduckgo_html_search(query: str, max_results: int = 4) -> str | None:
    """
    Scrape DuckDuckGo HTML results page for snippets.
    Fallback when Instant Answer returns nothing.
    """
    try:
        url = DDG_HTML.format(query=urllib.parse.quote_plus(query))
        raw = _get(url)

        # Extract result snippets from HTML
        snippets = re.findall(
            r'class="result__snippet"[^>]*>(.*?)</a>',
            raw, re.DOTALL
        )
        titles = re.findall(
            r'class="result__a"[^>]*>(.*?)</a>',
            raw, re.DOTALL
        )

        results = []
        for i, (title, snippet) in enumerate(zip(titles, snippets)):
            if i >= max_results:
                break
            t = _clean(title)
            s = _clean(snippet)
            if t and s:
                results.append(f"**{t}**\n{s}")

        if results:
            return "\n\n".join(results)

    except Exception:
        pass
    return None


async def duckduckgo_html_search_async(query: str, max_results: int = 4) -> str | None:
    cache_key = f"ddg-html:{query.lower()}:{max_results}"
    cached = _cache_get(cache_key)
    if cached is not False:
        return cached
    try:
        url = DDG_HTML.format(query=urllib.parse.quote_plus(query))
        raw = await _get_async(url)
        snippets = re.findall(r'class="result__snippet"[^>]*>(.*?)</a>', raw, re.DOTALL)
        titles = re.findall(r'class="result__a"[^>]*>(.*?)</a>', raw, re.DOTALL)
        results = []
        for i, (title, snippet) in enumerate(zip(titles, snippets)):
            if i >= max_results:
                break
            t = _clean(title)
            s = _clean(snippet)
            if t and s:
                results.append(f"**{t}**\n{s}")
        return _cache_set(cache_key, "\n\n".join(results) if results else None)
    except Exception:
        return _cache_set(cache_key, None)


def web_search(query: str) -> str:
    """Main entry point for web search. Tries instant answer first, then HTML scrape."""
    result = duckduckgo_instant(query)
    if result:
        return result

    result = duckduckgo_html_search(query)
    if result:
        return f"Search results for **\"{query}\"**:\n\n{result}"

    return f"I couldn't find anything for **\"{query}\"**. Try rephrasing your search."


async def web_search_async(query: str) -> str:
    result = await duckduckgo_instant_async(query)
    if result:
        return result
    result = await duckduckgo_html_search_async(query)
    if result:
        return f"Search results for **\"{query}\"**:\n\n{result}"
    return f"I couldn't find anything for **\"{query}\"**. Try rephrasing your search."


# ─────────────────────────────────────────────────────────────────────────────
# Wikipedia — REST API (no key needed)
# ─────────────────────────────────────────────────────────────────────────────

WIKI_SUMMARY = "https://en.wikipedia.org/api/rest_v1/page/summary/{title}"
WIKI_SEARCH  = "https://en.wikipedia.org/w/api.php?action=query&list=search&srsearch={query}&format=json&srlimit=3"


def _wiki_title_from_query(query: str) -> str:
    query = re.sub(r"\s+", " ", query).strip()
    return query[:1].upper() + query[1:] if query else query


def wikipedia_summary(title: str) -> str | None:
    try:
        url = WIKI_SUMMARY.format(title=urllib.parse.quote(title.replace(" ", "_")))
        raw = _get(url)
        data = json.loads(raw)

        if data.get("type") == "disambiguation":
            return f"**{title}** is a disambiguation page. Try being more specific, e.g. '{title} (person)' or '{title} (film)'."

        extract = data.get("extract", "").strip()
        if not extract:
            return None

        # Truncate to ~500 chars at sentence boundary
        if len(extract) > 500:
            cut = extract[:500]
            last_dot = cut.rfind(".")
            extract = cut[:last_dot + 1] if last_dot > 200 else cut + "..."

        page_url = data.get("content_urls", {}).get("desktop", {}).get("page", "")
        footer = f"\n\n*Wikipedia — {page_url}*" if page_url else "\n\n*Source: Wikipedia*"
        return f"**{data.get('title', title)}**\n\n{extract}{footer}"

    except httpx.HTTPStatusError as e:
        if e.response.status_code == 404:
            return None
        return None
    except Exception:
        return None


async def wikipedia_summary_async(title: str) -> str | None:
    cache_key = f"wiki-summary:{title.lower()}"
    cached = _cache_get(cache_key)
    if cached is not False:
        return cached
    try:
        url = WIKI_SUMMARY.format(title=urllib.parse.quote(title.replace(" ", "_")))
        data = json.loads(await _get_async(url))
        if data.get("type") == "disambiguation":
            return _cache_set(cache_key, f"**{title}** is a disambiguation page. Try being more specific, e.g. '{title} (person)' or '{title} (film)'.")
        extract = data.get("extract", "").strip()
        if not extract:
            return _cache_set(cache_key, None)
        if len(extract) > 500:
            cut = extract[:500]
            last_dot = cut.rfind(".")
            extract = cut[:last_dot + 1] if last_dot > 200 else cut + "..."
        page_url = data.get("content_urls", {}).get("desktop", {}).get("page", "")
        footer = f"\n\n*Wikipedia — {page_url}*" if page_url else "\n\n*Source: Wikipedia*"
        return _cache_set(cache_key, f"**{data.get('title', title)}**\n\n{extract}{footer}")
    except Exception:
        return _cache_set(cache_key, None)


def wikipedia_search_and_summarize(query: str) -> str:
    """Search Wikipedia for the query, then fetch the top result's summary."""
    direct = wikipedia_summary(_wiki_title_from_query(query))
    if direct:
        return direct

    try:
        url = WIKI_SEARCH.format(query=urllib.parse.quote_plus(query))
        raw = _get(url)
        data = json.loads(raw)
        results = data.get("query", {}).get("search", [])

        if not results:
            return f"No Wikipedia article found for **\"{query}\"**."

        # Try top 3 results until one returns a good summary
        for r in results[:3]:
            title = r.get("title", "")
            summary = wikipedia_summary(title)
            if summary:
                return summary

        return f"Found Wikipedia articles about **\"{query}\"** but couldn't extract a summary. Try: https://en.wikipedia.org/wiki/{urllib.parse.quote(results[0]['title'])}"

    except Exception:
        fallback_url = f"https://en.wikipedia.org/wiki/{urllib.parse.quote(query.replace(' ', '_'))}"
        return (
            f"I couldn't search Wikipedia right now, but you can try the direct page: "
            f"{fallback_url}"
        )


async def wikipedia_search_and_summarize_async(query: str) -> str:
    direct = await wikipedia_summary_async(_wiki_title_from_query(query))
    if direct:
        return direct

    try:
        url = WIKI_SEARCH.format(query=urllib.parse.quote_plus(query))
        data = json.loads(await _get_async(url))
        results = data.get("query", {}).get("search", [])
        if not results:
            return f"No Wikipedia article found for **\"{query}\"**."
        for r in results[:3]:
            title = r.get("title", "")
            summary = await wikipedia_summary_async(title)
            if summary:
                return summary
        return f"Found Wikipedia articles about **\"{query}\"** but couldn't extract a summary. Try: https://en.wikipedia.org/wiki/{urllib.parse.quote(results[0]['title'])}"
    except Exception:
        fallback_url = f"https://en.wikipedia.org/wiki/{urllib.parse.quote(query.replace(' ', '_'))}"
        return (
            f"I couldn't search Wikipedia right now, but you can try the direct page: "
            f"{fallback_url}"
        )
