from local_ai_retrieval.db import vector_literal


def test_vector_literal_is_stable() -> None:
    assert vector_literal([1, 0.25, -2.5]) == "[1,0.25,-2.5]"
