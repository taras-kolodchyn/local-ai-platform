from local_ai_retrieval.chunker import chunk_text


def test_chunker_is_deterministic_and_tracks_symbols() -> None:
    source = "pub fn secure_handler() {\n" + "\n".join(f"let x{i} = {i};" for i in range(12)) + "\n}"
    first = chunk_text(source, max_lines=8, overlap_lines=2)
    second = chunk_text(source, max_lines=8, overlap_lines=2)

    assert first == second
    assert first[0].symbol_name == "secure_handler"
    assert first[0].start_line == 1
    assert first[-1].end_line == len(source.splitlines())
    assert len({chunk.content_hash for chunk in first}) == len(first)


def test_chunker_rejects_invalid_overlap() -> None:
    try:
        chunk_text("hello", max_lines=10, overlap_lines=10)
    except ValueError as exc:
        assert "greater" in str(exc)
    else:
        raise AssertionError("expected ValueError")
