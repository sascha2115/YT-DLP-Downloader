#!/usr/bin/env python3
"""
Advanced heuristic line scorer for YouTube video descriptions.
Works for English and German. No AI API required.

Each line receives a score:
  > 0   → likely real content   (higher = more confident)
  = 0   → neutral / ambiguous
  < 0   → likely clutter        (lower = more confident it's spam)

Lines below --threshold are dropped (default: 0).
Lines are also grouped into coherent paragraphs before output.
"""

import json
import re
import subprocess
import sys
from dataclasses import dataclass, field


# ─────────────────────────────────────────────────────────────────────────────
# Signal definitions
# Each Signal has a regex, a score delta, and a label for debugging.
# Positive score = evidence of real content.
# Negative score = evidence of clutter.
# ─────────────────────────────────────────────────────────────────────────────

@dataclass
class Signal:
    pattern: re.Pattern
    score:   float
    label:   str = ""


def _r(pattern: str, flags=re.IGNORECASE) -> re.Pattern:
    return re.compile(pattern, flags)


# ── Clutter signals (negative) ────────────────────────────────────────────────

CLUTTER_SIGNALS: list[Signal] = [

    # Hard kills (very confident clutter)
    Signal(_r(r"^\s*\d{1,2}:\d{2}(:\d{2})?[\s\-\u2013\u2014]"),  -10, "timestamp"),
    Signal(_r(r"^\s*https?://\S+\s*$"),                            -10, "bare_url"),
    Signal(_r(r"^\s*(#\w+[\s,]*)+$"),                             -10, "hashtag_line"),
    Signal(_r(r"^\s*[-=_*~]+\s*$"),                               -10, "separator"),

    # "Label: single-token" lines  e.g.  "PayPal: user@example.com"
    # "Mail → someone@domain.de"  "Instagram: @handle"  "Discord: xyz#1234"
    # Pattern: optional leading word(s) + separator + one single token, nothing else.
    Signal(_r(
        r"^\s*"
        r"[\w\s\-]{1,30}"         # label  (1–30 chars, letters/digits/spaces/hyphens)
        r"\s*[:→\-\u2013\u2014\u2192|]\s*"   # separator  : → - – — |
        r"\S+"                     # exactly one token (email, handle, URL, code…)
        r"\s*$"                    # nothing after it
    ),                                                             -10, "label_value_only"),

    # Known contact/identity labels that may have multi-word values.
    # e.g. "NAME: Jasmin Dantas Kosubek"  "Inhaber: Max Mustermann"
    # The single-token pattern above misses these because the value has spaces.
    Signal(_r(
        r"^\s*"
        r"(?:name|inhaber|kontoinhaber|empf.{1,4}nger|kontakt|adresse|anschrift|"
        r"stra.{1,2}e|plz|ort|stadt|land|telefon|fax|"
        r"gesch.{1,4}ftsf.{1,4}hrer|impressum|firma|unternehmen|"
        r"contact|address|phone|recipient|account\s*holder|account\s*name)"
        r"\s*[:→\-\u2013\u2014\u2192|]\s*"
        r".+"                      # any value (possibly multi-word)
        r"\s*$"
    ),                                                             -10, "contact_label"),

    # Crypto wallet addresses — matched anywhere in a line (label optional).
    # Each format is well-defined by the protocol spec:
    #
    #   BTC  Legacy      : 1[1-9A-HJ-NP-Za-km-z]{24,33}         P2PKH, starts with 1
    #   BTC  P2SH        : 3[1-9A-HJ-NP-Za-km-z]{24,33}         starts with 3
    #   BTC  Bech32      : bc1[q-z02-9]{6,87}                    starts with bc1
    #   BTC  Bech32m     : bc1p[q-z02-9]{6,87}                   starts with bc1p (Taproot)
    #   ETH / ERC-20     : 0x[0-9a-fA-F]{40}                     exact 42 chars
    #   LTC  Legacy      : [LM][a-km-zA-HJ-NP-Z1-9]{26,33}
    #   LTC  Bech32      : ltc1[q-z02-9]{6,87}
    #   XRP              : r[1-9A-HJ-NP-Za-km-z]{24,34}
    #   DOGE             : D[5-9A-HJ-NP-U][1-9A-HJ-NP-Za-km-z]{32}
    #   Monero (XMR)     : 4[0-9AB][1-9A-HJ-NP-Za-km-z]{93}     always 95 chars
    #   Solana (SOL)     : [1-9A-HJ-NP-Za-km-z]{32,44}           base58, hardest to isolate
    #   Cardano (ADA)    : addr1[a-z0-9]{50,99}                   Shelley bech32
    #   Tron (TRX)       : T[1-9A-HJ-NP-Za-km-z]{33}
    Signal(_r(
        r"(?:"
        r"bc1p?[q-z02-9a-km-z]{6,87}"          # BTC Bech32 / Bech32m
        r"|[13][1-9A-HJ-NP-Za-km-z]{24,33}"    # BTC Legacy / P2SH
        r"|0x[0-9a-fA-F]{40}"                   # ETH / ERC-20
        r"|ltc1[q-z02-9a-km-z]{6,87}"          # LTC Bech32
        r"|[LM][a-km-zA-HJ-NP-Z1-9]{26,33}"   # LTC Legacy
        r"|r[1-9A-HJ-NP-Za-km-z]{24,34}"       # XRP
        r"|D[5-9A-HJ-NP-U][1-9A-HJ-NP-Za-km-z]{32}"  # DOGE
        r"|4[0-9AB][1-9A-HJ-NP-Za-km-z]{93}"  # XMR
        r"|addr1[a-z0-9]{50,99}"               # ADA
        r"|T[1-9A-HJ-NP-Za-km-z]{33}"         # TRX
        r")"
    ),                                                             -10, "crypto_address"),
    Signal(re.compile(                                             # plain string — \U escapes must be interpreted
        "^\\s*["
        "\U0001F600-\U0001F64F"   # emoticons
        "\U0001F300-\U0001F5FF"   # symbols & pictographs
        "\U0001F680-\U0001F6FF"   # transport & map
        "\U0001F700-\U0001FAFF"   # alchemical → symbols extended-a
        "\U00002702-\U000027B0"   # dingbats
        "\U000024C2-\U0001F251"   # enclosed chars
        "\u2600-\u26FF"           # misc symbols (☀ ☁ ⚡ ✅ ❌ …)
        "\u2700-\u27BF"           # dingbats block (✂ ✈ ✉ …)
        "\u25A0-\u25FF"           # geometric shapes (▶ ◆ ● …)
        "\u2022\u2023\u2043"      # bullet, triangular bullet, hyphen bullet
        "\u2013\u2014"            # en-dash, em-dash (often used as lead deco)
        "]",
        re.UNICODE,
    ),                                                             -10, "emoji_lead"),

    # Social media platforms
    Signal(_r(r"\b(instagram|twitter|tiktok|facebook|linkedin|"
              r"reddit|threads|snapchat|telegram|whatsapp|twitch|"
              r"rumble|odysee|bitchute)\b"),                        -6, "social_platform"),

    # Follow / subscribe CTAs (EN + DE)
    Signal(_r(r"\b(folg(e|t|en)|abonnier|follow|subscribe|sub to|sub here|"
              r"hit the bell|glocke|benachrichtigung|notification)\b"), -6, "follow_cta"),

    # Monetisation
    Signal(_r(r"\b(patreon|ko-?fi|buy me a coffee|paypal\.me|"
              r"paypal spenden|mitglied werden|membership|"
              r"kanal.*mitglied|channel.*member)\b"),               -7, "crowdfunding"),
    Signal(_r(r"\b(merch|shop|store|t-?shirt|hoodie|fanshop|fanartikel)\b"), -7, "merch"),
    Signal(_r(r"\b(sponsor(ed|ing)?|gesponsert|werbung\b|anzeige\b|"
              r"brought to you|in kooperation|affiliate|"
              r"partner.?link|rabatt.?code|promo.?code|"
              r"use code|gutschein|coupon|discount code)\b"),       -8, "sponsor"),
    Signal(_r(r"\b(werbepartner|produktplatzierung|"
              r"unbezahlte werbung|bezahlte werbung)\b"),           -9, "de_ad_disclosure"),

    # Generic calls to action (EN + DE)
    Signal(_r(r"\b(like (and )?share|smash (the )?like|daumen hoch|"
              r"like.*klick|klick.*like|teil(e|t|en)\b|don.?t forget|"
              r"vergiss nicht|hinterlass(t|e)? (einen )?kommentar|"
              r"leave a comment|comment below|kommentier|"
              r"schreib.*unten|lass.*wissen|let me know|"
              r"thanks for watching|danke f.{1,4}s? (zu)?schauen|"
              r"bis zum n.{1,6}chsten|see you next|"
              r"watch next|watch more|click here|tap here|"
              r"link in (the )?bio|links? (in|below|above|unten|"
              r"in der beschreibung)|in the description)\b"),       -5, "cta"),

    # Newsletter / podcast cross-promo
    Signal(_r(r"\b(newsletter|listen on|spotify|apple podcast|"
              r"google podcast|anchor\.fm|substack|buzzsprout)\b"), -4, "podcast_promo"),

    # Contact / collab
    Signal(_r(r"\b(business (mail|email|anfrage|inquiry|enquiry)|"
              r"for collab(oration)?s?|kooperation anfragen|"
              r"zusammenarbeit|p\.?o\.? box|postfach|"
              r"mailing address|impressum)\b"),                     -5, "contact"),

    # Inline URL anywhere in line
    Signal(_r(r"https?://\S+"),                                    -3, "inline_url"),

    # Inline hashtag
    Signal(_r(r"#\w+"),                                            -2, "inline_hashtag"),

    # Very short lines are usually labels, not prose
    Signal(_r(r"^\s*.{1,12}\s*$"),                                 -1, "very_short"),
]


# ── Content signals (positive) ────────────────────────────────────────────────

CONTENT_SIGNALS: list[Signal] = [

    # Long lines are almost always real content
    Signal(_r(r".{80,}"),                                          +5, "long_line"),
    Signal(_r(r".{120,}"),                                         +3, "very_long_line"),

    # Sentence-ending punctuation → prose
    Signal(_r(r"[.!?\u2026]\s*$"),                                 +3, "sentence_end"),
    Signal(_r(r"[,;:]\s*\w"),                                      +2, "mid_sentence_punct"),

    # Conjunctions → prose rhythm (EN + DE)
    Signal(_r(r"\b(und|oder|aber|denn|weil|dass|wenn|als|wie|"
              r"jedoch|allerdings|au\u00dferdem|dennoch|trotzdem|"
              r"and|or|but|because|that|when|as|however|"
              r"although|therefore|thus|hence|while)\b"),           +2, "conjunction"),

    # Numbers in context (statistics, dates, versions, measurements)
    Signal(_r(r"\b\d{4}\b"),                                       +1, "year"),
    Signal(_r(r"\d+\s*(kg|km|m\b|cm|mm|gb|mb|tb|hz|mhz|ghz|"
              r"fps|ms|kb|euro|eur|usd|\$|\u20ac|%|prozent)\b"),   +2, "measurement"),

    # Parenthetical remarks → clarification prose
    Signal(_r(r"\([^)]{5,}\)"),                                    +2, "parenthetical"),

    # Quoted material
    Signal(_r(r'[\u201e\u201c\u00bb\u00ab"\']{1}.{5,}[\u201d\u201c"\']{1}'), +2, "quote"),

    # Episode / series context (descriptive, not promo)
    Signal(_r(r"\b(teil\s*\d|part\s*\d|folge\s*\d|"
              r"staffel\s*\d|season\s*\d|kapitel\s*\d|chapter\s*\d)\b"), +1, "series_ref"),

    # Educational / analytical vocabulary (EN + DE)
    Signal(_r(r"\b(erkl.{1,3}r|verstehen|lernen|tutorial|anleitung|"
              r"einf.{1,4}hrung|grundlagen|fortgeschritten|"
              r"analyse|vergleich|experiment|studie|forschung|"
              r"ergebnis|wissenschaft|technik|methode|"
              r"explained?|understand|learning|beginner|advanced|"
              r"analysis|comparison|experiment|study|research|"
              r"result|science|technology|deep dive|breakdown|"
              r"overview|guide|how to|was ist|warum|wieso|weshalb)\b"), +2, "educational_vocab"),
]


# ─────────────────────────────────────────────────────────────────────────────
# Emoji / decorator stripping
# ─────────────────────────────────────────────────────────────────────────────

EMOJI_RE = re.compile(
    "["
    "\U0001F600-\U0001F64F"
    "\U0001F300-\U0001F5FF"
    "\U0001F680-\U0001F6FF"
    "\U0001F700-\U0001FAFF"
    "\U00002702-\U000027B0"
    "\U000024C2-\U0001F251"
    "\U0000200D\U0000FE0F"
    "]+",
    flags=re.UNICODE,
)

DECO_RE = re.compile(r"[\u25b6\u25ba\u25b8\u25b7\u27a4\u2605\u2606"
                     r"\u2713\u2714\u2717\u2718\u2022\u00b7\u25aa"
                     r"\u25ab\u25e6\u2023\u2043\u25c6\u25c7\u25a0"
                     r"\u25a1\u25cf\u25cb\u2764]")


def _strip_inline(line: str) -> str:
    line = EMOJI_RE.sub("", line)
    line = DECO_RE.sub("", line)
    line = re.sub(r"https?://\S+", "", line)
    line = re.sub(r"#\w+", "", line)
    line = re.sub(r"[ \t]{2,}", " ", line)
    return line.strip()


# ─────────────────────────────────────────────────────────────────────────────
# Scorer
# ─────────────────────────────────────────────────────────────────────────────

@dataclass
class ScoredLine:
    original: str
    cleaned:  str
    score:    float
    signals:  list[str] = field(default_factory=list)

    @property
    def is_blank(self) -> bool:
        return self.cleaned.strip() == ""


def score_line(raw_line: str) -> ScoredLine:
    cleaned = _strip_inline(raw_line)
    score   = 0.0
    signals: list[str] = []

    if not cleaned:
        return ScoredLine(raw_line, cleaned, 0.0, ["blank"])

    # Emoji density penalty (on raw, before stripping)
    emoji_count = len(EMOJI_RE.findall(raw_line))
    if emoji_count >= 3:
        score -= emoji_count * 0.5
        signals.append(f"emoji_density({emoji_count})")

    # Apply clutter signals.
    # emoji_lead must run against the original line because _strip_inline()
    # already removed leading emojis from `cleaned` before we get here.
    for sig in CLUTTER_SIGNALS:
        target = raw_line if sig.label == "emoji_lead" else cleaned
        if sig.pattern.search(target):
            score += sig.score
            signals.append(sig.label)

    # Apply content signals
    for sig in CONTENT_SIGNALS:
        if sig.pattern.search(cleaned):
            score += sig.score
            signals.append(sig.label)

    # Bonus: no clutter signals at all AND line is long
    clutter_labels = {s.label for s in CLUTTER_SIGNALS}
    if not any(s in clutter_labels for s in signals) and len(cleaned) > 60:
        score += 2
        signals.append("clean_long_bonus")

    # Penalty: all-caps shouting
    non_space = cleaned.replace(" ", "")
    if len(non_space) > 8 and non_space.isupper():
        score -= 3
        signals.append("all_caps")

    # Bonus: contains lowercase prose (not all-caps / all-numbers)
    if re.search(r"[a-z\u00e4\u00f6\u00fc\u00df]{4,}", cleaned):
        score += 1
        signals.append("lowercase_prose")

    return ScoredLine(raw_line, cleaned, round(score, 2), signals)


# ─────────────────────────────────────────────────────────────────────────────
# Paragraph-level ad/promo poison detection
# ─────────────────────────────────────────────────────────────────────────────
#
# Strategy: even if individual lines score OK on their own, a paragraph that
# contains any "poison" keyword is almost certainly a sponsor/promo block.
# We scan the *full concatenated paragraph text* and veto the whole thing.
#
# Each poison pattern carries a weight. We sum the weights for all matches
# across the paragraph text; if the total exceeds PARAGRAPH_POISON_THRESHOLD
# the entire paragraph is dropped.  This avoids false positives from single
# incidental keyword hits (e.g. a sentence mentioning "sign" in a different
# context).
# ─────────────────────────────────────────────────────────────────────────────

@dataclass
class PoisonSignal:
    pattern: re.Pattern
    weight:  float
    label:   str = ""


PARAGRAPH_POISON_THRESHOLD = 6.0   # total weight needed to veto a paragraph

PARAGRAPH_POISON_SIGNALS: list[PoisonSignal] = [

    # Strong single-hit poisons (weight >= threshold alone → instant veto)
    PoisonSignal(_r(r"\b(sign.?up|sign up for|jetzt anmelden|registrier|"
                    r"create (a |an |your )?account|konto erstellen)\b"),        7.0, "signup"),
    PoisonSignal(_r(r"\b(promo.?code|promocode|rabatt.?code|gutschein.?code|"
                    r"coupon code|discount code|use code|code:\s*\w+|"
                    r"mit dem code|mit code)\b"),                                7.0, "promo_code"),
    PoisonSignal(_r(r"\b(free trial|gratis.?monat|kostenlos testen|"
                    r"30.day(s)? free|erste[rn]? monat gratis|"
                    r"try.{1,10}free|jetzt kostenlos)\b"),                       7.0, "free_trial"),
    PoisonSignal(_r(r"\b(affiliate|referral link|ref=|partnerlink|"
                    r"gesponserte[rnm]?\b|paid promotion|bezahlte werbung|"
                    r"produktplatzierung)\b"),                                   7.0, "affiliate"),

    # Medium signals — two or more together cross the threshold
    PoisonSignal(_r(r"\b(sponsor(ed|ing)?|gesponsert|in kooperation|"
                    r"brought to you by|powered by|presented by|"
                    r"in zusammenarbeit mit)\b"),                                4.0, "sponsorship"),
    PoisonSignal(_r(r"\b(check out|schau(t)? (euch|dir|mal)|besuche?t?|visit|"
                    r"klick(e|t)? (hier|unten)|click (here|below|the link))\b"), 2.5, "check_out_cta"),
    PoisonSignal(_r(r"\b(exklusiv|exclusive|limited( offer)?|nur für kurze zeit|"
                    r"limited time|zeitlich begrenzt|nur heute|only today)\b"),  3.0, "scarcity"),
    PoisonSignal(_r(r"\b(rabatt|discount|angebot|deal|offer|sale|sparen|save)\b"), 2.5, "discount"),
    PoisonSignal(_r(r"\b(link in (der )?beschreibung|link below|link above|"
                    r"link in bio|in the description|unten (im|in der))\b"),     2.5, "link_cta"),
    PoisonSignal(_r(r"\b(patreon|ko-?fi|buy me a coffee|paypal|"
                    r"mitglied|membership|merch|shop)\b"),                       3.0, "monetisation"),
    PoisonSignal(_r(r"\b(newsletter|abonniere?|subscribe|folg)\b"),             2.0, "subscribe"),

    # Structural tell: a URL in the paragraph is mildly suspicious on its own
    PoisonSignal(_r(r"https?://\S+"),                                           1.5, "url_present"),
]


@dataclass
class Paragraph:
    lines:        list[ScoredLine] = field(default_factory=list)
    poison_score: float            = 0.0
    poison_hits:  list[str]        = field(default_factory=list)

    @property
    def is_poisoned(self) -> bool:
        return self.poison_score >= PARAGRAPH_POISON_THRESHOLD

    def analyse_poison(self) -> None:
        """Scan the full paragraph text for poison signals and accumulate weight."""
        full_text = " ".join(sl.cleaned for sl in self.lines if not sl.is_blank)
        for sig in PARAGRAPH_POISON_SIGNALS:
            if sig.pattern.search(full_text):
                self.poison_score += sig.weight
                self.poison_hits.append(sig.label)


def _group_paragraphs(scored_lines: list[ScoredLine]) -> list[Paragraph]:
    """Split scored lines into Paragraph objects on blank lines."""
    paragraphs: list[Paragraph] = [Paragraph()]
    for sl in scored_lines:
        if sl.is_blank:
            if paragraphs[-1].lines:
                paragraphs.append(Paragraph())
        else:
            paragraphs[-1].lines.append(sl)
    return [p for p in paragraphs if p.lines]


def assemble(
    scored_lines: list[ScoredLine],
    threshold: float = 0.0,
    debug: bool = False,
) -> str:
    """
    Two-pass assembly:
      Pass 1 — group lines into paragraphs, run poison detection on each.
      Pass 2 — drop poisoned paragraphs entirely; within surviving paragraphs
                drop individual lines that score <= threshold.
    """
    paragraphs = _group_paragraphs(scored_lines)
    for p in paragraphs:
        p.analyse_poison()

    if debug:
        print("\n─── Paragraph poison scores ────────────────────────────")
        for i, p in enumerate(paragraphs):
            veto    = "VETOED" if p.is_poisoned else "ok"
            preview = p.lines[0].cleaned[:55] if p.lines else ""
            hits    = ", ".join(p.poison_hits) if p.poison_hits else "—"
            print(f"  [{veto}] para {i+1:02d}  poison={p.poison_score:.1f}"
                  f"  {preview!r}…")
            print(f"           hits: {hits}")
        print("────────────────────────────────────────────────────────\n")

    kept_parts: list[str] = []
    for p in paragraphs:
        if p.is_poisoned:
            continue
        kept_lines = [
            sl.cleaned for sl in p.lines
            if sl.score > threshold and not sl.is_blank
        ]
        if kept_lines:
            kept_parts.append("\n".join(kept_lines))

    result = "\n\n".join(kept_parts)
    return re.sub(r"\n{3,}", "\n\n", result).strip()


# ─────────────────────────────────────────────────────────────────────────────
# yt-dlp fetch
# ─────────────────────────────────────────────────────────────────────────────

def fetch_metadata(url: str) -> dict:
    cmd = ["yt-dlp", "--dump-json", "--no-playlist", "--skip-download", url]
    result = subprocess.run(cmd, capture_output=True, text=True, check=True)
    data   = json.loads(result.stdout)
    return {
        "title":       data.get("title", ""),
        "uploader":    data.get("uploader", ""),
        "description": data.get("description", ""),
    }


# ─────────────────────────────────────────────────────────────────────────────
# CLI
# ─────────────────────────────────────────────────────────────────────────────

def main():
    import argparse

    parser = argparse.ArgumentParser(
        description="Score and clean a YouTube description (EN/DE, no AI)."
    )
    parser.add_argument("url", help="YouTube URL")
    parser.add_argument(
        "--threshold", type=float, default=0.0,
        help="Drop lines with score <= this value (default: 0).",
    )
    parser.add_argument(
        "--debug", action="store_true",
        help="Show per-line scores and triggered signals.",
    )
    args = parser.parse_args()

    print("Fetching metadata …\n")
    try:
        meta = fetch_metadata(args.url)
    except subprocess.CalledProcessError as e:
        print(f"yt-dlp error:\n{e.stderr}")
        sys.exit(1)

    print(f"Title   : {meta['title']}")
    print(f"Channel : {meta['uploader']}")

    raw    = meta["description"]
    scored = [score_line(line) for line in raw.splitlines()]

    if args.debug:
        print("\n─── Line scores ────────────────────────────────────────")
        for sl in scored:
            keep    = "\u2713" if sl.score > args.threshold else "\u2717"
            tag     = ", ".join(sl.signals) if sl.signals else "\u2014"
            preview = sl.cleaned[:68].replace("\n", " ")
            print(f"  {keep} [{sl.score:+.1f}]  {preview!r}")
            print(f"           {tag}")
        print("────────────────────────────────────────────────────────\n")

    cleaned = assemble(scored, threshold=args.threshold, debug=args.debug)

    print(f"\n─── Raw ({len(raw)} chars) ──────────────────────────────────")
    print(raw[:600] + ("\u2026" if len(raw) > 600 else ""))
    print(f"\n─── Cleaned ({len(cleaned)} chars) ──────────────────────────")
    print(cleaned)


if __name__ == "__main__":
    main()