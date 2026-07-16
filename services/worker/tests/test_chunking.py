from app.logic.chunking import chunk_pages


def test_single_short_page_produces_one_chunk():
    chunks = chunk_pages(["First paragraph.\n\nSecond paragraph."])
    assert len(chunks) == 1
    assert chunks[0].page_number == 1
    assert chunks[0].chunk_index == 0
    assert "First paragraph." in chunks[0].text
    assert "Second paragraph." in chunks[0].text


def test_all_caps_header_detected_and_carries_forward():
    page = "INSTALLATION\n\nStep one: remove the cover.\n\nStep two: attach the bracket."
    chunks = chunk_pages([page])
    assert len(chunks) == 1
    assert chunks[0].section_header == "INSTALLATION"
    # The header text is still buffered into chunk.text (not excluded) so
    # it stays searchable via the embedded text, not just via metadata.
    assert "INSTALLATION" in chunks[0].text


def test_title_case_header_detected():
    page = "Installation Instructions\n\nFollow these steps carefully."
    chunks = chunk_pages([page])
    assert chunks[0].section_header == "Installation Instructions"


def test_sentence_with_terminal_punctuation_is_not_a_header():
    page = "This is a short sentence.\n\nMore content follows here."
    chunks = chunk_pages([page])
    assert chunks[0].section_header is None
    assert "This is a short sentence." in chunks[0].text


def test_new_header_replaces_old_one():
    page = (
        "SAFETY\n\nDo not touch the terminals.\n\n"
        "MAINTENANCE\n\nClean the filter monthly."
    )
    chunks = chunk_pages([page])
    # Both paragraphs fit under the flush threshold, so they land in one
    # chunk — whose section_header reflects the most recent header seen.
    assert len(chunks) == 1
    assert chunks[0].section_header == "MAINTENANCE"


def test_flushes_new_chunk_once_buffer_exceeds_threshold():
    long_paragraph_a = "A" * 600
    long_paragraph_b = "B" * 600
    page = f"{long_paragraph_a}\n\n{long_paragraph_b}"
    chunks = chunk_pages([page])
    assert len(chunks) == 2
    assert long_paragraph_a in chunks[0].text
    assert long_paragraph_b in chunks[1].text
    assert chunks[0].chunk_index == 0
    assert chunks[1].chunk_index == 1


def test_page_boundary_always_flushes_even_under_threshold():
    chunks = chunk_pages(["Short page one text.", "Short page two text."])
    assert len(chunks) == 2
    assert chunks[0].page_number == 1
    assert chunks[1].page_number == 2


def test_chunk_index_sequential_across_whole_document():
    long_paragraph = "X" * 600
    pages = [f"{long_paragraph}\n\n{long_paragraph}", f"{long_paragraph}\n\n{long_paragraph}"]
    chunks = chunk_pages(pages)
    assert [c.chunk_index for c in chunks] == list(range(len(chunks)))
    assert len(chunks) == 4


def test_no_overlap_between_chunks():
    long_paragraph_a = "A" * 600
    long_paragraph_b = "B" * 600
    page = f"{long_paragraph_a}\n\n{long_paragraph_b}"
    chunks = chunk_pages([page])
    assert long_paragraph_a not in chunks[1].text
    assert long_paragraph_b not in chunks[0].text


def test_blank_pages_produce_no_chunks():
    assert chunk_pages(["", "   \n\n  "]) == []


def test_empty_input_produces_no_chunks():
    assert chunk_pages([]) == []
