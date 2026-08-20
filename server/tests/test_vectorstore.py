import pytest
from vectorstore import VectorStore

def test_add_and_search():
    store = VectorStore()
    store.add("doc1", "Added user authentication with JWT", {"file": "auth.ts"})
    store.add("doc2", "Fixed payment bug", {"file": "payment.ts"})
    
    results = store.search("user authentication", top_k=1)
    assert len(results) == 1
    assert results[0]["doc_id"] == "doc1"
    assert results[0]["metadata"]["file"] == "auth.ts"

def test_semantic_search():
    store = VectorStore()
    store.add_batch([{
        "id": "doc1",
        "text": "Added user authentication",
        "metadata": {"file": "auth.ts"},
        "embedding": [1.0, 0.0, 0.0]
    }, {
        "id": "doc2",
        "text": "Payment bug",
        "metadata": {"file": "payment.ts"},
        "embedding": [0.0, 1.0, 0.0]
    }])
    
    # Query with semantic embedding matching doc2
    results = store.search("unrelated", top_k=1, query_embedding=[0.0, 1.0, 0.0])
    assert len(results) == 1
    assert results[0]["doc_id"] == "doc2"

def test_remove_by_metadata():
    store = VectorStore()
    store.add("doc1", "test 1", {"commit": "WORKSPACE", "file": "test.txt"})
    store.add("doc2", "test 2", {"commit": "abc", "file": "test.txt"})
    
    removed = store.remove_by_metadata_multi({"commit": "WORKSPACE", "file": "test.txt"})
    assert removed == 1
    
    stats = store.stats()
    assert stats["documents"] == 1
    assert store.has_doc("doc2")
