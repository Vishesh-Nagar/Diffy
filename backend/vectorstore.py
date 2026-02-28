"""
Diffy — In-House TF-IDF Vector Store
Custom tokenizer, TF-IDF vectorizer, and cosine similarity search.
No external dependencies — pure Python stdlib.
"""

import math
import re
import json
import os
import hashlib
from collections import Counter, defaultdict


# ---------------------------------------------------------------------------
# Code-Aware Tokenizer
# ---------------------------------------------------------------------------

# Split camelCase and PascalCase
_CAMEL_RE = re.compile(r"(?<=[a-z])(?=[A-Z])|(?<=[A-Z])(?=[A-Z][a-z])")

# Common programming stop-words to filter out
_STOP_WORDS = frozenset({
    "the", "a", "an", "is", "are", "was", "were", "be", "been", "being",
    "in", "on", "at", "to", "for", "of", "with", "by", "from", "as",
    "and", "or", "not", "no", "but", "if", "then", "else", "this",
    "that", "it", "its", "do", "does", "did", "will", "would",
    "can", "could", "should", "shall", "may", "might",
    "has", "have", "had", "get", "set", "let", "var", "const",
    # Very common short tokens in diffs
    "diff", "git", "index", "file", "mode",
})

# Minimum token length
_MIN_TOKEN_LEN = 2


class Tokenizer:
    """Code-aware tokenizer that handles camelCase, snake_case, and programming constructs."""

    @staticmethod
    def tokenize(text):
        """
        Tokenize text into a list of normalized tokens.
        Handles: camelCase, snake_case, path separators, operators.
        """
        if not text:
            return []

        # Replace path separators and common delimiters with spaces
        text = re.sub(r"[/\\\.,:;=<>!&|{}()\[\]\"'`~@#$%^*+?]", " ", text)

        # Split on whitespace and underscores
        raw_tokens = re.split(r"[\s_\-]+", text)

        tokens = []
        for raw in raw_tokens:
            if not raw:
                continue
            # Split camelCase
            parts = _CAMEL_RE.split(raw)
            for part in parts:
                lower = part.lower()
                if (len(lower) >= _MIN_TOKEN_LEN
                        and lower not in _STOP_WORDS
                        and not lower.isdigit()):
                    tokens.append(lower)

        return tokens


# ---------------------------------------------------------------------------
# TF-IDF Vectorizer
# ---------------------------------------------------------------------------

class TFIDFVectorizer:
    """
    In-house TF-IDF vectorizer.
    Builds vocabulary and IDF from a corpus, transforms documents into
    sparse TF-IDF vectors (stored as {term_index: weight} dicts).
    """

    def __init__(self):
        self.vocabulary = {}        # term -> index
        self.idf = {}               # index -> idf weight
        self.doc_count = 0
        self._tokenizer = Tokenizer()

    def fit(self, documents):
        """Build vocabulary and compute IDF from a list of text documents."""
        doc_freq = Counter()  # term -> number of documents containing it
        self.doc_count = len(documents)

        all_terms = set()
        for doc in documents:
            tokens = self._tokenizer.tokenize(doc)
            unique_terms = set(tokens)
            for term in unique_terms:
                doc_freq[term] += 1
            all_terms.update(unique_terms)

        # Build vocabulary: assign index to each term
        self.vocabulary = {term: idx for idx, term in enumerate(sorted(all_terms))}

        # Compute IDF: log(N / df) with smoothing
        self.idf = {}
        for term, idx in self.vocabulary.items():
            df = doc_freq.get(term, 0)
            # Smooth IDF to avoid division by zero and dampen common terms
            self.idf[idx] = math.log((self.doc_count + 1) / (df + 1)) + 1.0

        return self

    def transform(self, documents):
        """Convert documents to sparse TF-IDF vectors."""
        vectors = []
        for doc in documents:
            tokens = self._tokenizer.tokenize(doc)
            tf = Counter(tokens)
            total = len(tokens) if tokens else 1

            vec = {}
            for term, count in tf.items():
                if term in self.vocabulary:
                    idx = self.vocabulary[term]
                    tf_val = count / total  # normalized term frequency
                    idf_val = self.idf.get(idx, 1.0)
                    vec[idx] = tf_val * idf_val

            vectors.append(vec)
        return vectors

    def fit_transform(self, documents):
        """Fit and transform in one step."""
        self.fit(documents)
        return self.transform(documents)

    def transform_query(self, query_text):
        """Transform a single query string into a sparse TF-IDF vector."""
        return self.transform([query_text])[0]

    def to_dict(self):
        """Serialize to dict for JSON storage."""
        return {
            "vocabulary": self.vocabulary,
            "idf": {str(k): v for k, v in self.idf.items()},
            "doc_count": self.doc_count,
        }

    @classmethod
    def from_dict(cls, data):
        """Deserialize from dict."""
        v = cls()
        v.vocabulary = data["vocabulary"]
        v.idf = {int(k): val for k, val in data["idf"].items()}
        v.doc_count = data["doc_count"]
        return v


# ---------------------------------------------------------------------------
# Cosine Similarity
# ---------------------------------------------------------------------------

def _magnitude(vec):
    """Compute the magnitude (L2 norm) of a sparse vector."""
    return math.sqrt(sum(v * v for v in vec.values())) if vec else 0.0


def cosine_similarity(vec_a, vec_b):
    """Compute cosine similarity between two sparse vectors (dicts)."""
    if not vec_a or not vec_b:
        return 0.0

    # Dot product over shared keys
    shared_keys = set(vec_a.keys()) & set(vec_b.keys())
    if not shared_keys:
        return 0.0

    dot = sum(vec_a[k] * vec_b[k] for k in shared_keys)
    mag_a = _magnitude(vec_a)
    mag_b = _magnitude(vec_b)

    if mag_a == 0.0 or mag_b == 0.0:
        return 0.0

    return dot / (mag_a * mag_b)


# ---------------------------------------------------------------------------
# Vector Store
# ---------------------------------------------------------------------------

class VectorStore:
    """
    In-house vector store with TF-IDF indexing and cosine similarity search.
    Documents are stored with metadata for retrieval.
    """

    def __init__(self):
        self._documents = {}          # doc_id -> {text, metadata}
        self._vectors = {}            # doc_id -> sparse vector
        self._vectorizer = TFIDFVectorizer()
        self._is_fitted = False

    def add(self, doc_id, text, metadata=None):
        """Add a document. After adding, call rebuild() to re-fit TF-IDF."""
        self._documents[doc_id] = {
            "text": text,
            "metadata": metadata or {},
        }
        self._is_fitted = False

    def add_batch(self, items):
        """
        Add multiple documents at once.
        items: [{id, text, metadata}]
        """
        for item in items:
            self._documents[item["id"]] = {
                "text": item["text"],
                "metadata": item.get("metadata", {}),
            }
        self._is_fitted = False

    def rebuild(self):
        """Re-fit the TF-IDF vectorizer on all documents and compute vectors."""
        if not self._documents:
            return

        doc_ids = list(self._documents.keys())
        texts = [self._documents[did]["text"] for did in doc_ids]

        self._vectorizer.fit(texts)
        vectors = self._vectorizer.transform(texts)

        self._vectors = {}
        for did, vec in zip(doc_ids, vectors):
            self._vectors[did] = vec

        self._is_fitted = True

    def search(self, query, top_k=5):
        """
        Search for documents most similar to the query.
        Returns: [{doc_id, score, text, metadata}]
        """
        if not self._is_fitted or not self._vectors:
            return []

        query_vec = self._vectorizer.transform_query(query)
        if not query_vec:
            return []

        scores = []
        for doc_id, doc_vec in self._vectors.items():
            score = cosine_similarity(query_vec, doc_vec)
            if score > 0.0:
                scores.append((doc_id, score))

        # Sort by score descending
        scores.sort(key=lambda x: x[1], reverse=True)

        results = []
        for doc_id, score in scores[:top_k]:
            doc = self._documents[doc_id]
            results.append({
                "doc_id": doc_id,
                "score": round(score, 4),
                "text": doc["text"],
                "metadata": doc["metadata"],
            })
        return results

    def remove(self, doc_id):
        """Remove a document."""
        self._documents.pop(doc_id, None)
        self._vectors.pop(doc_id, None)

    def clear(self):
        """Clear all documents."""
        self._documents.clear()
        self._vectors.clear()
        self._is_fitted = False
        self._vectorizer = TFIDFVectorizer()

    def stats(self):
        """Return store statistics."""
        return {
            "documents": len(self._documents),
            "vocabulary_size": len(self._vectorizer.vocabulary),
            "is_fitted": self._is_fitted,
        }

    def has_doc(self, doc_id):
        """Check if a document exists."""
        return doc_id in self._documents

    # ----- Persistence -----

    def save(self, path):
        """Save the vector store to a JSON file."""
        data = {
            "documents": {},
            "vectorizer": self._vectorizer.to_dict() if self._is_fitted else None,
        }

        for doc_id, doc in self._documents.items():
            data["documents"][doc_id] = {
                "text": doc["text"],
                "metadata": doc["metadata"],
            }

        os.makedirs(os.path.dirname(path) if os.path.dirname(path) else ".", exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False)

    def load(self, path):
        """Load the vector store from a JSON file."""
        if not os.path.exists(path):
            return False

        try:
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
        except (json.JSONDecodeError, OSError):
            return False

        self._documents = {}
        for doc_id, doc in data.get("documents", {}).items():
            self._documents[doc_id] = {
                "text": doc["text"],
                "metadata": doc.get("metadata", {}),
            }

        vdata = data.get("vectorizer")
        if vdata:
            self._vectorizer = TFIDFVectorizer.from_dict(vdata)
            # Recompute vectors from the loaded vectorizer
            doc_ids = list(self._documents.keys())
            texts = [self._documents[did]["text"] for did in doc_ids]
            vectors = self._vectorizer.transform(texts)
            self._vectors = dict(zip(doc_ids, vectors))
            self._is_fitted = True
        else:
            self._is_fitted = False

        return True


# ---------------------------------------------------------------------------
# Utility: generate a document ID from content
# ---------------------------------------------------------------------------

def make_doc_id(repo_name, commit_hash, file_path, hunk_index=0):
    """Generate a deterministic document ID."""
    raw = f"{repo_name}:{commit_hash}:{file_path}:{hunk_index}"
    return hashlib.sha256(raw.encode()).hexdigest()[:16]


# ---------------------------------------------------------------------------
# Self-test
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    print("=== Tokenizer Test ===")
    t = Tokenizer()
    samples = [
        "def calculateTotalPrice(items):",
        "function get_user_name(user_id) {",
        "import React from 'react';",
        "git diff --staged HEAD~1",
    ]
    for s in samples:
        print(f"  {s!r}")
        print(f"    → {t.tokenize(s)}")

    print("\n=== VectorStore Test ===")
    store = VectorStore()
    store.add("d1", "Added new user authentication with JWT tokens")
    store.add("d2", "Fixed bug in payment processing calculate total")
    store.add("d3", "Refactored database connection pool settings")
    store.add("d4", "Added unit tests for user login flow")
    store.add("d5", "Updated CSS styles for the dashboard layout")
    store.rebuild()

    results = store.search("user login authentication", top_k=3)
    print("  Query: 'user login authentication'")
    for r in results:
        print(f"    [{r['score']}] {r['text'][:60]}")

    print(f"\n  Stats: {store.stats()}")
