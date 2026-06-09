"""
Diffy — In-House TF-IDF Vector Store
Custom tokenizer, TF-IDF vectorizer, and sub-linear search using SQLite.
"""

import math
import re
import json
import os
import hashlib
import sqlite3
import threading
from collections import Counter

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
        if not text:
            return []

        text = re.sub(r"[/\\\.,:;=<>!&|{}()\[\]\"'`~@#$%^*+?]", " ", text)
        raw_tokens = re.split(r"[\s_\-]+", text)

        tokens = []
        for raw in raw_tokens:
            if not raw:
                continue
            parts = _CAMEL_RE.split(raw)
            for part in parts:
                lower = part.lower()
                if (len(lower) >= _MIN_TOKEN_LEN
                        and lower not in _STOP_WORDS
                        and not lower.isdigit()):
                    tokens.append(lower)

        return tokens


# ---------------------------------------------------------------------------
# Vector Store (SQLite based)
# ---------------------------------------------------------------------------

class VectorStore:
    """
    SQLite-backed vector store with TF-IDF indexing and sub-linear search.
    Documents are stored with metadata for retrieval.
    """

    def __init__(self):
        self._db_path = None
        self._local = threading.local()
        self._lock = threading.Lock()
        self._tokenizer = Tokenizer()

    def _get_conn(self):
        if getattr(self._local, "conn", None) is None:
            if not self._db_path:
                self._local.conn = sqlite3.connect(":memory:")
            else:
                self._local.conn = sqlite3.connect(self._db_path, timeout=30.0)
            self._local.conn.execute("PRAGMA journal_mode=WAL")
            self._local.conn.execute("PRAGMA synchronous=NORMAL")
        return self._local.conn

    def _init_db(self):
        with self._lock:
            conn = self._get_conn()
            conn.execute('''CREATE TABLE IF NOT EXISTS documents (
                doc_id TEXT PRIMARY KEY,
                text TEXT,
                metadata TEXT,
                total_tokens INTEGER
            )''')
            conn.execute('''CREATE TABLE IF NOT EXISTS inverted_index (
                term TEXT,
                doc_id TEXT,
                tf REAL,
                PRIMARY KEY (term, doc_id),
                FOREIGN KEY(doc_id) REFERENCES documents(doc_id) ON DELETE CASCADE
            )''')
            conn.execute('''CREATE TABLE IF NOT EXISTS global_term_df (
                term TEXT PRIMARY KEY,
                doc_count INTEGER
            )''')
            conn.execute("CREATE INDEX IF NOT EXISTS idx_inv_doc ON inverted_index(doc_id)")
            conn.commit()

    def load(self, path):
        """Initialize connection to the SQLite database."""
        if path.endswith(".json"):
            path = path[:-5] + ".db"
        
        with self._lock:
            self._db_path = path
            os.makedirs(os.path.dirname(self._db_path), exist_ok=True)
            self._init_db()
        return True

    def save(self, path):
        """SQLite is auto-saved, but we keep this method for API compatibility."""
        pass

    def add(self, doc_id, text, metadata=None):
        self.add_batch([{"id": doc_id, "text": text, "metadata": metadata or {}}])

    def add_batch(self, items):
        """Add multiple documents and update incremental TF-IDF."""
        with self._lock:
            conn = self._get_conn()
            cursor = conn.cursor()
            
            for item in items:
                doc_id = item["id"]
                text = item["text"]
                metadata = json.dumps(item.get("metadata", {}))
                
                tokens = self._tokenizer.tokenize(text)
                tf_counts = Counter(tokens)
                total_tokens = len(tokens) if tokens else 1
                
                # Check if doc exists to properly update df
                cursor.execute("SELECT term FROM inverted_index WHERE doc_id = ?", (doc_id,))
                existing_terms = {row[0] for row in cursor.fetchall()}
                
                # Delete existing
                if existing_terms:
                    cursor.execute("DELETE FROM inverted_index WHERE doc_id = ?", (doc_id,))
                    cursor.execute("DELETE FROM documents WHERE doc_id = ?", (doc_id,))
                    # Decrement df for existing terms
                    for term in existing_terms:
                        cursor.execute("UPDATE global_term_df SET doc_count = doc_count - 1 WHERE term = ?", (term,))
                
                # Insert document
                cursor.execute("INSERT INTO documents (doc_id, text, metadata, total_tokens) VALUES (?, ?, ?, ?)",
                               (doc_id, text, metadata, total_tokens))
                
                # Insert terms and update df
                for term, count in tf_counts.items():
                    tf = count / total_tokens
                    cursor.execute("INSERT INTO inverted_index (term, doc_id, tf) VALUES (?, ?, ?)", (term, doc_id, tf))
                    
                    if term not in existing_terms:
                        cursor.execute("INSERT INTO global_term_df (term, doc_count) VALUES (?, 1) ON CONFLICT(term) DO UPDATE SET doc_count = doc_count + 1", (term,))
            
            # Cleanup global_term_df where doc_count <= 0
            cursor.execute("DELETE FROM global_term_df WHERE doc_count <= 0")
            conn.commit()

    def rebuild(self):
        """No-op for incremental SQLite implementation."""
        pass

    def search(self, query, top_k=5):
        """Sub-linear search using inverted index and dot product."""
        query_tokens = self._tokenizer.tokenize(query)
        if not query_tokens:
            return []
            
        query_tf = Counter(query_tokens)
        query_total = len(query_tokens)
        
        with self._lock:
            conn = self._get_conn()
            cursor = conn.cursor()
            
            cursor.execute("SELECT COUNT(*) FROM documents")
            doc_count = cursor.fetchone()[0]
            if doc_count == 0:
                return []
                
            placeholders = ",".join(["?"] * len(query_tf))
            cursor.execute(f"SELECT term, doc_count FROM global_term_df WHERE term IN ({placeholders})", list(query_tf.keys()))
            
            combined_weights = []
            for term, df in cursor.fetchall():
                idf = math.log((doc_count + 1) / (df + 1)) + 1.0
                q_tf = query_tf[term] / query_total
                # doc_weight = doc_tf * idf
                # query_weight = q_tf * idf
                # dot_product = sum(query_weight * doc_weight)
                # combined_weight = query_weight * idf = q_tf * idf * idf
                combined_weights.append((term, q_tf * idf * idf))
                
            if not combined_weights:
                return []
                
            cursor.execute("CREATE TEMPORARY TABLE IF NOT EXISTS temp_query_weights (term TEXT PRIMARY KEY, weight REAL)")
            cursor.execute("DELETE FROM temp_query_weights")
            cursor.executemany("INSERT INTO temp_query_weights (term, weight) VALUES (?, ?)", combined_weights)
            
            cursor.execute(f'''
                SELECT d.doc_id, d.text, d.metadata, SUM(i.tf * q.weight) as score
                FROM inverted_index i
                JOIN temp_query_weights q ON i.term = q.term
                JOIN documents d ON i.doc_id = d.doc_id
                GROUP BY i.doc_id
                ORDER BY score DESC
                LIMIT ?
            ''', (top_k,))
            
            results = []
            for row in cursor.fetchall():
                doc_id, text, metadata_str, score = row
                results.append({
                    "doc_id": doc_id,
                    "score": round(score, 4),
                    "text": text,
                    "metadata": json.loads(metadata_str)
                })
            return results

    def remove(self, doc_id):
        """Remove a document."""
        with self._lock:
            conn = self._get_conn()
            cursor = conn.cursor()
            cursor.execute("SELECT term FROM inverted_index WHERE doc_id = ?", (doc_id,))
            terms = {row[0] for row in cursor.fetchall()}
            
            cursor.execute("DELETE FROM inverted_index WHERE doc_id = ?", (doc_id,))
            cursor.execute("DELETE FROM documents WHERE doc_id = ?", (doc_id,))
            
            for term in terms:
                cursor.execute("UPDATE global_term_df SET doc_count = doc_count - 1 WHERE term = ?", (term,))
            cursor.execute("DELETE FROM global_term_df WHERE doc_count <= 0")
            conn.commit()

    def clear(self):
        """Clear all documents."""
        with self._lock:
            conn = self._get_conn()
            conn.execute("DELETE FROM inverted_index")
            conn.execute("DELETE FROM global_term_df")
            conn.execute("DELETE FROM documents")
            conn.commit()

    def stats(self):
        """Return store statistics."""
        with self._lock:
            conn = self._get_conn()
            cursor = conn.cursor()
            cursor.execute("SELECT COUNT(*) FROM documents")
            docs = cursor.fetchone()[0]
            cursor.execute("SELECT COUNT(*) FROM global_term_df")
            vocab = cursor.fetchone()[0]
            return {
                "documents": docs,
                "vocabulary_size": vocab,
                "is_fitted": docs > 0,
            }

    def has_doc(self, doc_id):
        """Check if a document exists."""
        with self._lock:
            conn = self._get_conn()
            cursor = conn.cursor()
            cursor.execute("SELECT 1 FROM documents WHERE doc_id = ?", (doc_id,))
            return cursor.fetchone() is not None


# ---------------------------------------------------------------------------
# Utility: generate a document ID from content
# ---------------------------------------------------------------------------

def make_doc_id(repo_name, commit_hash, file_path, hunk_index=0):
    """Generate a deterministic document ID."""
    raw = f"{repo_name}:{commit_hash}:{file_path}:{hunk_index}"
    return hashlib.sha256(raw.encode()).hexdigest()[:16]


if __name__ == "__main__":
    print("=== VectorStore SQLite Test ===")
    store = VectorStore()
    store.add("d1", "Added new user authentication with JWT tokens", {"file": "auth.ts"})
    store.add("d2", "Fixed bug in payment processing calculate total", {"file": "payment.ts"})
    store.add("d3", "Refactored database connection pool settings", {"file": "db.ts"})
    
    results = store.search("user login authentication", top_k=3)
    print("  Query: 'user login authentication'")
    for r in results:
        print(f"    [{r['score']}] {r['text'][:60]}")
    print(f"\n  Stats: {store.stats()}")
