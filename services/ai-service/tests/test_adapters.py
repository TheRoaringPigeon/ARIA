import pytest

import app.adapters as adapters_module
from app.adapters import get_adapter
from app.adapters.qwen import QwenAdapter
from app.config import settings


@pytest.fixture(autouse=True)
def reset_adapter_singleton():
    yield
    adapters_module._adapter = None


def test_qwen_adapter_strips_think_block():
    adapter = QwenAdapter()
    raw = "<think>\nreasoning about the answer\n</think>\n\nHello, ARIA!"
    assert adapter.normalize_response(raw) == "Hello, ARIA!"


def test_qwen_adapter_passes_through_content_without_think_block():
    adapter = QwenAdapter()
    assert adapter.normalize_response("just a plain reply") == "just a plain reply"


def test_qwen_adapter_falls_back_to_raw_content_if_stripping_leaves_nothing():
    adapter = QwenAdapter()
    raw = "<think>\ncut off mid-thought, no closing tag or reply followed"
    assert adapter.normalize_response(raw) == raw.strip()


def test_get_adapter_returns_qwen_by_default():
    assert isinstance(get_adapter(), QwenAdapter)


def test_get_adapter_raises_on_unknown_adapter_name(monkeypatch):
    monkeypatch.setattr(settings, "model_adapter", "not-a-real-adapter")
    with pytest.raises(ValueError, match="Unknown AI_SERVICE_MODEL_ADAPTER"):
        get_adapter()


def test_stream_filter_splits_think_and_answer_across_arbitrary_boundaries():
    filt = QwenAdapter().create_stream_filter()

    segments = []
    for delta in ["<thi", "nk>reas", "oning</th", "ink>hello"]:
        segments += filt.feed(delta)
    segments += filt.flush()

    kinds = [kind for kind, _ in segments]
    thinking = "".join(text for kind, text in segments if kind == "thinking")
    answer = "".join(text for kind, text in segments if kind == "answer")
    assert thinking == "reasoning"
    assert answer == "hello"
    # every "thinking" segment must precede every "answer" segment
    assert max(i for i, k in enumerate(kinds) if k == "thinking") < kinds.index("answer")


def test_stream_filter_emits_thinking_and_answer_from_same_feed_call():
    filt = QwenAdapter().create_stream_filter()

    filt.feed("<think>")
    segments = filt.feed("done thinking</think>hello")

    assert segments == [("thinking", "done thinking"), ("answer", "hello")]


def test_stream_filter_suppresses_leading_whitespace_arriving_as_a_separate_chunk():
    # Matches real Ollama behavior observed against the live stack: "</think>"
    # and the blank line after it arrive as separate deltas, one token each,
    # not bundled together — a one-shot lstrip on the close-tag delta alone
    # missed this in an earlier version of this filter.
    filt = QwenAdapter().create_stream_filter()

    segments = []
    for delta in ["<think>reasoning</think>", "\n\n", "The", " answer"]:
        segments += filt.feed(delta)

    assert segments == [
        ("thinking", "reasoning"),
        ("answer", "The"),
        ("answer", " answer"),
    ]


def test_stream_filter_passes_through_immediately_when_no_think_block():
    filt = QwenAdapter().create_stream_filter()

    assert filt.feed("Hello there") == [("answer", "Hello there")]
    assert filt.feed(", ARIA!") == [("answer", ", ARIA!")]


def test_stream_filter_streams_unterminated_reasoning_live_and_flush_is_empty():
    # Nothing in this text is ambiguous with "</think>", so it should all
    # stream out as "thinking" immediately rather than waiting for flush().
    filt = QwenAdapter().create_stream_filter()

    segments = filt.feed("<think>cut off, no closing tag")

    assert segments == [("thinking", "cut off, no closing tag")]
    assert filt.flush() == []


def test_stream_filter_flushes_ambiguous_tail_as_thinking():
    # Ends in "<", which is a valid (if ultimately unrealized) prefix of
    # "</think>" — held back during feed(), resolved as "thinking" by flush().
    filt = QwenAdapter().create_stream_filter()

    segments = filt.feed("<think>partial reasoning cut off with <")

    assert segments == [("thinking", "partial reasoning cut off with ")]
    assert filt.flush() == [("thinking", "<")]


def test_stream_filter_flushes_unconfirmed_open_tag_prefix_as_answer():
    # The reply never got far enough to confirm it opened with "<think>" at
    # all, so whatever arrived is treated as answer content, not reasoning.
    filt = QwenAdapter().create_stream_filter()

    assert filt.feed("<thi") == []
    assert filt.flush() == [("answer", "<thi")]


def test_stream_filter_flush_without_feeding_returns_empty():
    assert QwenAdapter().create_stream_filter().flush() == []


def test_create_stream_filter_returns_independent_instances():
    adapter = QwenAdapter()
    filt1 = adapter.create_stream_filter()
    filt2 = adapter.create_stream_filter()

    filt1.feed("<think>")

    assert filt2.feed("hello") == [("answer", "hello")]
