"""Deterministische Schutzmechanismen – bewusst NICHT dem LLM überlassen.

* Grounding: Jedes Zitat einer Wissenseinheit wird gegen das Transkript geprüft.
* PII-Redaktion: offensichtliche Kundendaten werden vor Speicherung/LLM maskiert.
"""
from __future__ import annotations

import re
import unicodedata
from difflib import SequenceMatcher


def _norm(s: str) -> str:
    s = unicodedata.normalize("NFKC", s).lower()
    s = re.sub(r"[„“\"'‚‘’«»…]", "", s)
    s = re.sub(r"[^\w\s%§€/.,-]", " ", s)
    return re.sub(r"\s+", " ", s).strip()


def quote_match_score(quote: str, text: str) -> float:
    """1.0 = wörtlich enthalten; sonst beste Fensterähnlichkeit (robust gegen Interpunktion)."""
    q, t = _norm(quote), _norm(text)
    if not q or not t:
        return 0.0
    if q in t:
        return 1.0
    qw, tw = q.split(), t.split()
    n = len(qw)
    if n == 0:
        return 0.0
    best = 0.0
    for size in {max(1, n - 2), n, n + 2}:
        for i in range(0, max(1, len(tw) - size + 1)):
            window = " ".join(tw[i : i + size])
            r = SequenceMatcher(None, q, window).ratio()
            if r > best:
                best = r
    return best


GROUNDING_THRESHOLD = 0.82


def verify_sources(source_quotes, expert_turns: dict) -> list[dict]:
    """source_quotes: [{turn_id, quote}] → angereicherte Referenzen mit `verified`-Flag und Zeitstempeln."""
    refs = []
    for sq in source_quotes:
        tid = sq["turn_id"] if isinstance(sq, dict) else sq.turn_id
        quote = sq["quote"] if isinstance(sq, dict) else sq.quote
        turn = expert_turns.get(tid)
        if turn is None:
            # evtl. falsche ID → in allen Expertenaussagen suchen
            best = max(expert_turns.values(), key=lambda t: quote_match_score(quote, t.text), default=None)
            if best is not None and quote_match_score(quote, best.text) >= GROUNDING_THRESHOLD:
                turn = best
        score = quote_match_score(quote, turn.text) if turn else 0.0
        refs.append(
            {
                "turn_id": turn.id if turn else tid,
                "quote": quote,
                "t_start": turn.t_start if turn else None,
                "t_end": turn.t_end if turn else None,
                "verified": score >= GROUNDING_THRESHOLD,
                "match_score": round(score, 3),
            }
        )
    return refs


# --- PII -------------------------------------------------------------------
_PII_PATTERNS = [
    ("IBAN", re.compile(r"\b[A-Z]{2}\d{2}(?:\s?[A-Z0-9]{4}){3,7}(?:\s?[A-Z0-9]{1,3})?\b")),
    ("E-MAIL", re.compile(r"\b[\w.+-]+@[\w-]+\.[\w.-]+\b")),
    ("TELEFON", re.compile(r"(?<!\w)(?:\+49|0049|0)[\s/-]?\d{2,5}[\s/-]?\d{3,}(?:[\s-]?\d{2,})?(?!\w)")),
    ("VERTRAGSNR", re.compile(r"\b(?:vertrags(?:nummer|nr\.?)|bausparvertrag(?:snummer)?)\s*:?\s*[A-Z0-9][A-Z0-9-]{5,}\b", re.I)),
]


def redact_pii(text: str) -> tuple[str, int]:
    count = 0
    for label, rx in _PII_PATTERNS:
        text, n = rx.subn(f"[{label} entfernt]", text)
        count += n
    return text, count
