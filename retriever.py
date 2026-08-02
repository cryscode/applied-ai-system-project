# Retriever: light RAG module for pet-care advisory answers.
#
# Scoped and read-only -- it never touches a Task, Owner, or Scheduler, and
# it cannot override anything the Validator decides. It exists purely to
# answer general advisory questions (e.g. "how often should I walk a
# Labrador puppy") from a small static knowledge base of breed/species care
# facts. No embeddings, no external API calls: retrieval is plain
# keyword-overlap scoring over a fixed set of KnowledgeEntry records, which
# keeps it deterministic, dependency-free, and auditable like the rest of
# PawPal+.

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from typing import List, Optional

from pawpal_logger import get_logger, log_decision

ADVISORY = "ADVISORY"

_WORD_RE = re.compile(r"[a-z0-9]+")

_STOPWORDS = frozenset({
    "a", "an", "the", "is", "are", "was", "were", "be", "been", "being",
    "of", "in", "on", "at", "to", "for", "and", "or", "but", "with",
    "my", "i", "you", "your", "it", "its", "do", "does", "did", "should",
    "how", "what", "when", "why", "this", "that", "these", "those",
})


def _tokenize(text: str) -> List[str]:
    """Lowercase and split text into alphanumeric tokens, dropping stopwords.

    Stopwords are excluded so scoring reflects actual topical overlap rather
    than incidental matches on words like "the" or "is" that appear in
    almost every fact.
    """
    return [tok for tok in _WORD_RE.findall(text.lower()) if tok not in _STOPWORDS]


@dataclass(frozen=True)
class KnowledgeEntry:
    """One static advisory fact in the knowledge base."""

    species: str
    topic: str
    fact: str
    breed: Optional[str] = None
    keywords: List[str] = field(default_factory=list)

    def searchable_text(self) -> str:
        """Return the blob of text a query is scored against."""
        parts = [self.species, self.topic, self.fact, self.breed or "", " ".join(self.keywords)]
        return " ".join(parts)


DEFAULT_KNOWLEDGE_BASE: List[KnowledgeEntry] = [
    KnowledgeEntry(
        species="dog", breed="labrador", topic="walking",
        fact="A Labrador puppy (under 1 year old) should get short, frequent walks -- "
             "roughly 5 minutes per month of age, up to 3-4 times a day -- to protect "
             "growing joints. Adult Labradors generally need 1-2 hours of exercise daily.",
        keywords=["walk", "walking", "exercise", "puppy", "labrador"],
    ),
    KnowledgeEntry(
        species="dog", topic="walking",
        fact="Most adult dogs benefit from at least one 20-30 minute walk, 1-2 times "
             "a day. Puppies need shorter, more frequent walks; senior dogs need more "
             "rest between walks.",
        keywords=["walk", "walking", "exercise", "dog"],
    ),
    KnowledgeEntry(
        species="dog", topic="puppy exercise",
        fact="Over-exercising a puppy can damage developing joints and growth plates. "
             "Cap walks at short durations and avoid jumping/repetitive-impact play "
             "until the dog is closer to full adult size.",
        keywords=["puppy", "exertion", "joints", "growth", "exercise"],
    ),
    KnowledgeEntry(
        species="dog", topic="senior care",
        fact="Senior dogs (roughly 8+ years) need more recovery time between walks "
             "and shorter, gentler activity overall -- watch for stiffness or "
             "reluctance to move as signs to scale back.",
        keywords=["senior", "old", "rest", "walk", "recovery"],
    ),
    KnowledgeEntry(
        species="dog", topic="medication",
        fact="Most oral medications for dogs are dosed on a fixed schedule (e.g. every "
             "8-12 hours). Always follow the vet/label schedule exactly -- doses given "
             "too close together can cause overdose-like effects.",
        keywords=["medication", "medicine", "dose", "pill", "insulin", "schedule"],
    ),
    KnowledgeEntry(
        species="dog", topic="feeding",
        fact="Puppies typically eat 3-4 small meals a day; adult dogs usually do well "
             "on 2 meals a day, spaced roughly 8-12 hours apart.",
        keywords=["feed", "feeding", "food", "meal", "diet"],
    ),
    KnowledgeEntry(
        species="dog", topic="grooming",
        fact="Most dogs benefit from brushing a few times a week and a bath every "
             "4-6 weeks, though coat type changes this a lot (double coats need more "
             "frequent brushing, especially when shedding).",
        keywords=["groom", "grooming", "brush", "bath", "coat"],
    ),
    KnowledgeEntry(
        species="cat", topic="exercise",
        fact="Cats aren't typically walked like dogs -- they get exercise through "
             "active play (wand toys, chasing, climbing) in short multiple sessions "
             "a day rather than a scheduled walk.",
        keywords=["walk", "exercise", "play", "cat"],
    ),
    KnowledgeEntry(
        species="cat", topic="feeding",
        fact="Adult cats generally do well with 2 measured meals a day; free-feeding "
             "dry food is common but can lead to overeating without portion control.",
        keywords=["feed", "feeding", "food", "meal", "diet"],
    ),
    KnowledgeEntry(
        species="cat", topic="grooming",
        fact="Most cats self-groom well and need only occasional brushing, though "
             "long-haired breeds need brushing several times a week to prevent "
             "matting.",
        keywords=["groom", "grooming", "brush", "coat", "matting"],
    ),
    KnowledgeEntry(
        species="bird", topic="exercise",
        fact="Birds aren't walked -- they need daily out-of-cage flight/exploration "
             "time in a safe, supervised space instead.",
        keywords=["walk", "exercise", "flight", "bird", "cage"],
    ),
    KnowledgeEntry(
        species="rabbit", topic="exercise",
        fact="Rabbits aren't walked on leash the way dogs are -- they need daily "
             "supervised free-roam time in a safe, hazard-free area to hop and "
             "explore.",
        keywords=["walk", "exercise", "hop", "rabbit", "roam"],
    ),
    KnowledgeEntry(
        species="hamster", topic="exercise",
        fact="Hamsters get their exercise from an appropriately sized wheel and "
             "supervised out-of-cage exploration time, not walks.",
        keywords=["walk", "exercise", "wheel", "hamster"],
    ),
    KnowledgeEntry(
        species="fish", topic="care",
        fact="Fish don't require exercise or walking; their key care needs are water "
             "quality (regular partial water changes) and consistent feeding amounts.",
        keywords=["walk", "exercise", "water", "fish", "tank"],
    ),
    KnowledgeEntry(
        species="dog", topic="hydration",
        fact="Dogs should have fresh water available at all times; refresh water at "
             "least once or twice a day and more often in hot weather or after "
             "exercise.",
        keywords=["water", "hydrate", "drink", "hydration"],
    ),
    KnowledgeEntry(
        species="dog", topic="vet checkups",
        fact="Healthy adult dogs typically need a vet checkup once a year; puppies, "
             "seniors, and pets with ongoing conditions usually need more frequent "
             "visits.",
        keywords=["vet", "checkup", "appointment", "clinic", "vaccination"],
    ),
]


@dataclass
class Retriever:
    """Scores a static knowledge base against a query and returns the best matches.

    This is the entire "RAG" surface: retrieval only, no generation model.
    Callers format the returned KnowledgeEntry facts into an answer; nothing
    here can create, edit, or remove a Task, nor does it consult or bypass
    the Validator -- advisory answers never affect what gets scheduled.
    """

    entries: List[KnowledgeEntry] = field(default_factory=lambda: list(DEFAULT_KNOWLEDGE_BASE))
    logger: logging.Logger = field(default_factory=get_logger, repr=False, compare=False)

    def _score(
        self,
        entry: KnowledgeEntry,
        query_tokens: List[str],
        species: Optional[str],
        breed: Optional[str],
    ):
        """Return (keyword_overlap, total_score) for one entry.

        keyword_overlap is kept separate from the species/breed boost so
        retrieve() can require real topical overlap -- a single incidental
        shared word (e.g. "year") shouldn't be enough to surface an
        unrelated fact just because a species/breed also happens to match.
        """
        entry_tokens = set(_tokenize(entry.searchable_text()))
        keyword_overlap = sum(1 for tok in query_tokens if tok in entry_tokens)
        total = keyword_overlap
        if species and entry.species.lower() == species.lower():
            total += 2
        if breed and entry.breed and entry.breed.lower() == breed.lower():
            total += 2
        return keyword_overlap, total

    def retrieve(
        self,
        query: str,
        species: Optional[str] = None,
        breed: Optional[str] = None,
        top_k: int = 3,
    ) -> List[KnowledgeEntry]:
        """Return up to top_k KnowledgeEntry records best matching the query.

        Scoring is plain keyword overlap between the query and each entry's
        text, with a small boost when species/breed match exactly. An entry
        only counts as a match if it has at least 2 overlapping keywords, or
        1 overlapping keyword plus a species/breed boost -- a single
        incidental shared word (e.g. both texts happen to mention "year")
        isn't enough on its own. Returns an empty list if nothing clears
        that bar -- never raises, and never guesses at an answer it has no
        real matching fact for.
        """
        query_tokens = _tokenize(query)
        scored = []
        for entry in self.entries:
            overlap, total = self._score(entry, query_tokens, species, breed)
            if overlap >= 2 or (overlap >= 1 and total > overlap):
                scored.append((total, entry))
        scored.sort(key=lambda pair: pair[0], reverse=True)
        return [entry for _, entry in scored[:top_k]]

    def answer(
        self,
        query: str,
        species: Optional[str] = None,
        breed: Optional[str] = None,
    ) -> str:
        """Return a human-readable advisory answer, or a clear "don't know".

        This is advisory text only -- it is informational, not a scheduling
        decision, and it never mutates any Task, Owner, or Scheduler state.
        """
        results = self.retrieve(query, species=species, breed=breed, top_k=1)

        if not results:
            log_decision(
                self.logger, ADVISORY, query,
                reason="no matching knowledge base entry found",
            )
            return (
                "I don't have general care info on that in my knowledge base yet. "
                "This is advisory only, so for anything specific to your pet, check "
                "with a vet."
            )

        entry = results[0]
        log_decision(
            self.logger, ADVISORY, query,
            reason=f"matched knowledge base entry (species={entry.species}, topic={entry.topic})",
        )
        return (
            f"{entry.fact}\n\n"
            "(General advisory info only -- not a substitute for vet guidance, "
            "and it doesn't change any scheduled tasks.)"
        )
