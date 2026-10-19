"""Rule arm for the affected-object scenario: token matching of the candidate region names against the announcement.
Developed on the development provider only (Packet, 36 items). A candidate scores one point per distinctive token
(three letters or more, not a generic word) of its name that occurs in the text, with a bonus for the code in
parentheses ("(CH)") or a region code ("DE-FRA1") matched as a whole word; the best-scoring candidate wins, ties go to
the first in the candidate list, no match gives None. No provider-specific alias table.
"""
import re

GENERIC = {'network', 'connections', 'region', 'regions', 'data', 'center', 'centre', 'services', 'service', 'managed', 'object', 'storage', 'and', 'the', 'pops',
           'elements', 'north', 'south', 'east', 'west', 'central', 'new', 'city', 'metal', 'equinix'}


def tokens(name):
    return [t for t in re.split(r'[\s(),:/\-]+', name.lower()) if len(t) >= 3 and t not in GENERIC]


def codes(name):
    return [c.lower() for c in re.findall(r'\(([A-Za-z]{2,4})\)', name)] + [c.lower() for c in re.findall(r'\b([A-Z]{2}-[A-Z]{3}\d?)\b', name)]


def pick(text, candidates):
    t = text.lower(); best = None; best_score = 0
    for c in candidates:
        score = sum(1 for tok in tokens(c) if re.search(r'\b' + re.escape(tok) + r'\b', t))
        score += sum(2 for code in codes(c) if re.search(r'\b' + re.escape(code) + r'\d*\b', t))   # 'dc10', 'sy4' name the facility by code + number
        if score > best_score:
            best, best_score = c, score
    return best
