"""
search.py — Web search via DuckDuckGo (no API key) + Wikipedia.
Uses only stdlib + requests. No third-party search SDKs.
"""

import re
import json
import urllib.parse
import urllib.request
import urllib.error
import html


# ─────────────────────────────────────────────────────────────────────────────
# Shared HTTP helper
# ─────────────────────────────────────────────────────────────────────────────

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/120.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "en-US,en;q=0.9",
}

def _get(url: str, timeout: int = 8) -> str:
    req = urllib.request.Request(url, headers=HEADERS)
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return resp.read().decode("utf-8", errors="replace")


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


def web_search(query: str) -> str:
    """Main entry point for web search. Tries instant answer first, then HTML scrape."""
    result = duckduckgo_instant(query)
    if result:
        return result

    result = duckduckgo_html_search(query)
    if result:
        return f"Search results for **\"{query}\"**:\n\n{result}"

    return f"I couldn't find anything for **\"{query}\"**. Try rephrasing your search."


# ─────────────────────────────────────────────────────────────────────────────
# Wikipedia — REST API (no key needed)
# ─────────────────────────────────────────────────────────────────────────────

WIKI_SUMMARY = "https://en.wikipedia.org/api/rest_v1/page/summary/{title}"
WIKI_SEARCH  = "https://en.wikipedia.org/w/api.php?action=query&list=search&srsearch={query}&format=json&srlimit=3"


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

    except urllib.error.HTTPError as e:
        if e.code == 404:
            return None
        return None
    except Exception:
        return None


def wikipedia_search_and_summarize(query: str) -> str:
    """Search Wikipedia for the query, then fetch the top result's summary."""
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

    except Exception as e:
        return f"Wikipedia search failed: {e}"