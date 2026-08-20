from rag_pipeline import _chunk_diff

def test_chunk_diff():
    commit_info = {
        "hash": "1234567890",
        "message": "Test commit",
        "author": "Alice",
        "date": "2023-01-01T00:00:00Z"
    }
    parsed_files = [
        {
            "file": "test.txt",
            "hunks": [
                {
                    "context_label": "function test()",
                    "removed": ["- old code"],
                    "added": ["+ new code"]
                }
            ]
        }
    ]
    
    chunks = _chunk_diff(commit_info, parsed_files, max_lines=10)
    assert len(chunks) == 1
    assert "Test commit" in chunks[0]["text"]
    assert "+ new code" in chunks[0]["text"]
    assert chunks[0]["metadata"]["file"] == "test.txt"
    assert chunks[0]["metadata"]["commit"] == "1234567890"

