"""Description / plot cleaning heuristics (EN/DE, no AI)."""

import re
from dataclasses import dataclass, field


# Description / Plot Cleaning Logic (EN/DE, no AI)
# ----------------------------------------------------------------------------------------------------
@dataclass
class DescriptionSignal:
    pattern: re.Pattern
    score: float
    label: str = ""

@dataclass
class PoisonSignal:
    pattern: re.Pattern
    weight: float
    label: str = ""

def _r(pattern: str, flags=re.IGNORECASE) -> re.Pattern:
    return re.compile(pattern, flags)

CLUTTER_SIGNALS = [
    DescriptionSignal(_r(r"^\s*\d{1,2}:\d{2}(:\d{2})?[\s\-\u2013\u2014]"), -10, "timestamp"),
    DescriptionSignal(_r(r"^\s*https?://\S+\s*$"), -10, "bare_url"),
    DescriptionSignal(_r(r"^\s*(#\w+[\s,]*)+$"), -10, "hashtag_line"),
    DescriptionSignal(_r(r"^\s*[-=_*~]+\s*$"), -10, "separator"),
    DescriptionSignal(_r(r"^\s*[\w\s\-]{1,30}\s*[:→\-\u2013\u2014\u2192|]\s*\S+\s*$"), -10, "label_value_only"),
    DescriptionSignal(_r(r"^\s*(?:name|inhaber|kontoinhaber|empf.{1,4}nger|kontakt|adresse|anschrift|stra.{1,2}e|plz|ort|stadt|land|telefon|fax|gesch.{1,4}ftsf.{1,4}hrer|impressum|firma|unternehmen|contact|address|phone|recipient|account\s*holder|account\s*name)\s*[:→\-\u2013\u2014\u2192|]\s*.+\s*$"), -10, "contact_label"),
    DescriptionSignal(_r(r"(?:bc1p?[q-z02-9a-km-z]{6,87}|[13][1-9A-HJ-NP-Za-km-z]{24,33}|0x[0-9a-fA-F]{40}|ltc1[q-z02-9a-km-z]{6,87}|[LM][a-km-zA-HJ-NP-Z1-9]{26,33}|r[1-9A-HJ-NP-Za-km-z]{24,34}|D[5-9A-HJ-NP-U][1-9A-HJ-NP-Za-km-z]{32}|4[0-9AB][1-9A-HJ-NP-Za-km-z]{93}|addr1[a-z0-9]{50,99}|T[1-9A-HJ-NP-Za-km-z]{33})"), -10, "crypto_address"),
    DescriptionSignal(re.compile(r"^\s*[\U0001F600-\U0001F64F\U0001F300-\U0001F5FF\U0001F680-\U0001F6FF\U0001F700-\U0001FAFF\U00002702-\U000027B0\U000024C2-\U0001F251\u2600-\u26FF\u2700-\u27BF\u25A0-\u25FF\u2022\u2023\u2043\u2013\u2014]", re.UNICODE), -10, "emoji_lead"),
    DescriptionSignal(_r(r"\b(instagram|twitter|tiktok|facebook|linkedin|reddit|threads|snapchat|telegram|whatsapp|twitch|rumble|odysee|bitchute)\b"), -6, "social_platform"),
    DescriptionSignal(_r(r"\b(folg(e|t|en)|abonnier|follow|subscribe|sub to|sub here|hit the bell|glocke|benachrichtigung|notification)\b"), -6, "follow_cta"),
    DescriptionSignal(_r(r"\b(patreon|ko-?fi|buy me a coffee|paypal\.me|paypal spenden|mitglied werden|membership|kanal.*mitglied|channel.*member)\b"), -7, "crowdfunding"),
    DescriptionSignal(_r(r"\b(merch|shop|store|t-?shirt|hoodie|fanshop|fanartikel)\b"), -7, "merch"),
    DescriptionSignal(_r(r"\b(sponsor(ed|ing)?|gesponsert|werbung\b|anzeige\b|brought to you|in kooperation|affiliate|partner.?link|rabatt.?code|promo.?code|use code|gutschein|coupon|discount code)\b"), -8, "sponsor"),
    DescriptionSignal(_r(r"\b(werbepartner|produktplatzierung|unbezahlte werbung|bezahlte werbung)\b"), -9, "de_ad_disclosure"),
    DescriptionSignal(_r(r"\b(like (and )?share|smash (the )?like|daumen hoch|like.*klick|klick.*like|teil(e|t|en)\b|don.?t forget|vergiss nicht|hinterlass(t|e)? (einen )?kommentar|leave a comment|comment below|kommentier|schreib.*unten|lass.*wissen|let me know|thanks for watching|danke f.{1,4}s? (zu)?schauen|bis zum n.{1,6}chsten|see you next|watch next|watch more|click here|tap here|link in (the )?bio|links? (in|below|above|unten|in der beschreibung)|in the description)\b"), -5, "cta"),
    DescriptionSignal(_r(r"\b(newsletter|listen on|spotify|apple podcast|google podcast|anchor\.fm|substack|buzzsprout)\b"), -4, "podcast_promo"),
    DescriptionSignal(_r(r"\b(business (mail|email|anfrage|inquiry|enquiry)|for collab(oration)?s?|kooperation anfragen|zusammenarbeit|p\.?o\.? box|postfach|mailing address|impressum)\b"), -5, "contact"),
    DescriptionSignal(_r(r"https?://\S+"), -3, "inline_url"),
    DescriptionSignal(_r(r"#\w+"), -2, "inline_hashtag"),
    DescriptionSignal(_r(r"^\s*.{1,12}\s*$"), -1, "very_short"),
]

CONTENT_SIGNALS = [
    DescriptionSignal(_r(r".{80,}"), +5, "long_line"),
    DescriptionSignal(_r(r".{120,}"), +3, "very_long_line"),
    DescriptionSignal(_r(r"[.!?\u2026]\s*$"), +3, "sentence_end"),
    DescriptionSignal(_r(r"[,;:]\s*\w"), +2, "mid_sentence_punct"),
    DescriptionSignal(_r(r"\b(und|oder|aber|denn|weil|dass|wenn|als|wie|jedoch|allerdings|au\u00dferdem|dennoch|trotzdem|and|or|but|because|that|when|as|however|although|therefore|thus|hence|while)\b"), +2, "conjunction"),
    DescriptionSignal(_r(r"\b\d{4}\b"), +1, "year"),
    DescriptionSignal(_r(r"\d+\s*(kg|km|m\b|cm|mm|gb|mb|tb|hz|mhz|ghz|fps|ms|kb|euro|eur|usd|\$|\u20ac|%|prozent)\b"), +2, "measurement"),
    DescriptionSignal(_r(r"\([^)]{5,}\)"), +2, "parenthetical"),
    DescriptionSignal(_r(r'[\u201e\u201c\u00bb\u00ab"\']{1}.{5,}[\u201d\u201c"\']{1}'), +2, "quote"),
    DescriptionSignal(_r(r"\b(teil\s*\d|part\s*\d|folge\s*\d|staffel\s*\d|season\s*\d|kapitel\s*\d|chapter\s*\d)\b"), +1, "series_ref"),
    DescriptionSignal(_r(r"\b(erkl.{1,3}r|verstehen|lernen|tutorial|anleitung|einf.{1,4}hrung|grundlagen|fortgeschritten|analyse|vergleich|experiment|studie|forschung|ergebnis|wissenschaft|technik|methode|explained?|understand|learning|beginner|advanced|analysis|comparison|experiment|study|research|result|science|technology|deep dive|breakdown|overview|guide|how to|was ist|warum|wieso|weshalb)\b"), +2, "educational_vocab"),
]

PARAGRAPH_POISON_SIGNALS = [
    PoisonSignal(_r(r"\b(sign.?up|sign up for|jetzt anmelden|registrier|create (a |an |your )?account|konto erstellen)\b"), 7.0, "signup"),
    PoisonSignal(_r(r"\b(promo.?code|promocode|rabatt.?code|gutschein.?code|coupon code|discount code|use code|code:\s*\w+|mit dem code|mit code)\b"), 7.0, "promo_code"),
    PoisonSignal(_r(r"\b(free trial|gratis.?monat|kostenlos testen|30.day(s)? free|erste[rn]? monat gratis|try.{1,10}free|jetzt kostenlos)\b"), 7.0, "free_trial"),
    PoisonSignal(_r(r"\b(affiliate|referral link|ref=|partnerlink|gesponserte[rnm]?\b|paid promotion|bezahlte werbung|produktplatzierung)\b"), 7.0, "affiliate"),
    PoisonSignal(_r(r"\b(sponsor(ed|ing)?|gesponsert|in kooperation|brought to you by|powered by|presented by|in zusammenarbeit mit)\b"), 4.0, "sponsorship"),
    PoisonSignal(_r(r"\b(check out|schau(t)? (euch|dir|mal)|besuche?t?|visit|klick(e|t)? (hier|unten)|click (here|below|the link))\b"), 2.5, "check_out_cta"),
    PoisonSignal(_r(r"\b(exklusiv|exclusive|limited( offer)?|nur für kurze zeit|limited time|zeitlich begrenzt|nur heute|only today)\b"), 3.0, "scarcity"),
    PoisonSignal(_r(r"\b(rabatt|discount|angebot|deal|offer|sale|sparen|save)\b"), 2.5, "discount"),
    PoisonSignal(_r(r"\b(link in (der )?beschreibung|link below|link above|link in bio|in the description|unten (im|in der))\b"), 2.5, "link_cta"),
    PoisonSignal(_r(r"\b(patreon|ko-?fi|buy me a coffee|paypal|mitglied|membership|merch|shop)\b"), 3.0, "monetisation"),
    PoisonSignal(_r(r"\b(newsletter|abonniere?|subscribe|folg)\b"), 2.0, "subscribe"),
    PoisonSignal(_r(r"https?://\S+"), 1.5, "url_present"),
]

EMOJI_RE = re.compile(r"[\U0001F600-\U0001F64F\U0001F300-\U0001F5FF\U0001F680-\U0001F6FF\U0001F700-\U0001FAFF\U00002702-\U000027B0\U000024C2-\U0001F251\U0000200D\U0000FE0F]+", flags=re.UNICODE)
DECO_RE = re.compile(r"[\u25b6\u25ba\u25b8\u25b7\u27a4\u2605\u2606\u2713\u2714\u2717\u2718\u2022\u00b7\u25aa\u25ab\u25e6\u2023\u2043\u25c6\u25c7\u25a0\u25a1\u25cf\u25cb\u2764]")

@dataclass
class ScoredDescriptionLine:
    original: str
    cleaned: str
    score: float
    signals: list[str] = field(default_factory=list)
    @property
    def is_blank(self) -> bool:
        return self.cleaned.strip() == ""

def _strip_description_inline(line: str) -> str:
    line = EMOJI_RE.sub("", line)
    line = DECO_RE.sub("", line)
    line = re.sub(r"https?://\S+", "", line)
    line = re.sub(r"#\w+", "", line)
    line = re.sub(r"[ \t]{2,}", " ", line)
    return line.strip()

def score_description_line(raw_line: str) -> ScoredDescriptionLine:
    cleaned = _strip_description_inline(raw_line)
    score = 0.0
    signals = []
    if not cleaned:
        return ScoredDescriptionLine(raw_line, cleaned, 0.0, ["blank"])
    emoji_count = len(EMOJI_RE.findall(raw_line))
    if emoji_count >= 3:
        score -= emoji_count * 0.5
        signals.append(f"emoji_density({emoji_count})")
    for sig in CLUTTER_SIGNALS:
        target = raw_line if sig.label == "emoji_lead" else cleaned
        if sig.pattern.search(target):
            score += sig.score
            signals.append(sig.label)
    for sig in CONTENT_SIGNALS:
        if sig.pattern.search(cleaned):
            score += sig.score
            signals.append(sig.label)
    clutter_labels = {s.label for s in CLUTTER_SIGNALS}
    if not any(s in clutter_labels for s in signals) and len(cleaned) > 60:
        score += 2
        signals.append("clean_long_bonus")
    non_space = cleaned.replace(" ", "")
    if len(non_space) > 8 and non_space.isupper():
        score -= 3
        signals.append("all_caps")
    if re.search(r"[a-z\u00e4\u00f6\u00fc\u00df]{4,}", cleaned):
        score += 1
        signals.append("lowercase_prose")
    return ScoredDescriptionLine(raw_line, cleaned, round(score, 2), signals)

@dataclass
class DescriptionParagraph:
    lines: list[ScoredDescriptionLine] = field(default_factory=list)
    poison_score: float = 0.0
    poison_hits: list[str] = field(default_factory=list)
    @property
    def is_poisoned(self) -> bool:
        return self.poison_score >= 6.0
    def analyse_poison(self) -> None:
        full_text = " ".join(sl.cleaned for sl in self.lines if not sl.is_blank)
        for sig in PARAGRAPH_POISON_SIGNALS:
            if sig.pattern.search(full_text):
                self.poison_score += sig.weight
                self.poison_hits.append(sig.label)

def clean_youtube_description(raw_description: str, threshold: float = 0.0) -> str:
    if not raw_description:
        return ""
    scored_lines = [score_description_line(line) for line in raw_description.splitlines()]
    paragraphs = [DescriptionParagraph()]
    for sl in scored_lines:
        if sl.is_blank:
            if paragraphs[-1].lines:
                paragraphs.append(DescriptionParagraph())
        else:
            paragraphs[-1].lines.append(sl)
    paragraphs = [p for p in paragraphs if p.lines]
    for p in paragraphs:
        p.analyse_poison()
    kept_parts = []
    for p in paragraphs:
        if p.is_poisoned:
            continue
        kept_lines = [sl.cleaned for sl in p.lines if sl.score > threshold and not sl.is_blank]
        if kept_lines:
            kept_parts.append("\n".join(kept_lines))
    result = "\n\n".join(kept_parts)
    return re.sub(r"\n{3,}", "\n\n", result).strip()
