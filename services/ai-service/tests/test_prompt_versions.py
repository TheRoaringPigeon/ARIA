import pytest

from app.prompt_versions import PROMPT_VERSIONS, prompt_fingerprints

PINNED = {
    "supervisor": (1, "87a9cc296871"),
    "grounding_gate": (1, "859bdc1a4c85"),
    "research_tool": (1, "b2880fa5de8a"),
    "action_target": (2, "f4af0ec45b57"),
    "log_fields": (1, "c40f7212eea9"),
    "schedule_kind": (1, "a91d75afd53c"),
    "schedule_detail": (3, "1a1556c481ea"),
    "fuzzy_entity_match": (1, "8f2d5aaef47b"),
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
