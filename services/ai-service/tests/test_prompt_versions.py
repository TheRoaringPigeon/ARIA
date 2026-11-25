import pytest

from app.prompt_versions import PROMPT_VERSIONS, prompt_fingerprints

PINNED = {
    "supervisor": (1, "87a9cc296871"),
    "grounding_gate": (1, "859bdc1a4c85"),
    "research_tool": (1, "dc93da3b3c2d"),
    "action_target": (2, "02a926e5a951"),
    "log_fields": (1, "929d99d0d73e"),
    "schedule_kind": (1, "a91d75afd53c"),
    "schedule_detail": (4, "225bd5e0f510"),
    "last_done_date": (1, "3bcd2c8f8803"),
    "fuzzy_entity_match": (1, "c1476224cfbc"),
    "personas": (1, "988135cc58d9"),
    "answer_context": (1, "3bf16161be25"),
}


def test_every_versioned_prompt_is_pinned():
    assert set(PINNED) == set(PROMPT_VERSIONS)


@pytest.mark.parametrize("name", sorted(PINNED))
def test_prompt_change_comes_with_a_version_bump(name):
    current = (PROMPT_VERSIONS[name], prompt_fingerprints()[name])
    assert current == PINNED[name], (
        f"prompt {name!r} is now {current}, pinned as {PINNED[name]}: when its text or "
        "schema changes, bump PROMPT_VERSIONS and set the new pin to the new fingerprint"
    )
