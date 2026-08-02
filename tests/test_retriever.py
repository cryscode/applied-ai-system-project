import sys
import os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from retriever import Retriever, KnowledgeEntry


def make_retriever(entries=None):
    return Retriever(entries=entries) if entries is not None else Retriever()


# ── retrieve() ────────────────────────────────────────────────────────────────

def test_retrieve_finds_relevant_entry_by_keyword():
    """A query about walking a Labrador puppy should surface the labrador walking entry."""
    retriever = make_retriever()
    results = retriever.retrieve("how often should I walk a labrador puppy")
    assert results
    assert any(e.breed == "labrador" and e.topic == "walking" for e in results)


def test_retrieve_species_boost_breaks_ties():
    """Passing species=cat should rank cat-specific facts above generic ones for the same query."""
    retriever = make_retriever()
    results = retriever.retrieve("how much exercise does my pet need", species="cat", top_k=1)
    assert results
    assert results[0].species == "cat"


def test_retrieve_returns_empty_for_unrelated_query():
    """A query with no keyword overlap against the knowledge base should return nothing, not a guess."""
    retriever = make_retriever()
    results = retriever.retrieve("quarterly tax filing deadlines")
    assert results == []


def test_retrieve_respects_top_k():
    """retrieve() should never return more than top_k entries."""
    retriever = make_retriever()
    results = retriever.retrieve("walk exercise dog", top_k=2)
    assert len(results) <= 2


# ── answer() ─────────────────────────────────────────────────────────────────

def test_answer_returns_fact_text_for_known_topic():
    """A matched query should return advisory text containing the underlying fact."""
    retriever = make_retriever()
    text = retriever.answer("how often should I walk a labrador puppy")
    assert "labrador" in text.lower() or "puppy" in text.lower()
    assert "advisory" in text.lower()


def test_answer_is_honest_about_unknown_topics():
    """An unmatched query should say so plainly instead of fabricating an answer."""
    retriever = make_retriever()
    text = retriever.answer("what is the capital of France")
    assert "don't have" in text.lower()


def test_answer_never_touches_scheduler_state():
    """Retriever has no reference to any Task/Owner/Scheduler -- calling answer() is side-effect-free
    beyond its own logging, by construction (it holds no such references at all)."""
    retriever = make_retriever()
    assert not hasattr(retriever, "tasks")
    assert not hasattr(retriever, "owner")
    retriever.answer("how often should I walk a labrador puppy")
    # No exception, no external state to check -- Retriever simply has nothing to mutate.


def test_species_mismatch_advisory_matches_validator_non_walking_species():
    """Retriever should have advisory coverage for the same non-walking species the Validator flags."""
    retriever = make_retriever()
    for species in ("cat", "fish", "bird", "hamster", "rabbit"):
        results = retriever.retrieve(f"how often should I walk my {species}", species=species)
        assert results, f"expected advisory coverage for species={species}"
