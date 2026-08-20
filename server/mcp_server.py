from mcp.server.fastmcp import FastMCP
from rag_pipeline import get_pipeline
import json

mcp = FastMCP("Diffy")

@mcp.tool()
def query(question: str) -> str:
    """
    Ask a question about the codebase using the git diff RAG index.
    """
    pipeline = get_pipeline()
    return pipeline.query(question, stream=False)

@mcp.tool()
def retrieve(question: str, top_k: int = 5) -> str:
    """
    Retrieve relevant diff chunks for a given question without querying the LLM.
    Returns JSON string of results.
    """
    pipeline = get_pipeline()
    results = pipeline.retrieve(question, top_k=top_k)
    return json.dumps(results, indent=2)

@mcp.tool()
def review_commits(repo_path: str, num_commits: int = 5) -> str:
    """
    Review recent commits in a given repository for potential issues.
    """
    pipeline = get_pipeline()
    return pipeline.review_commits(repo_path, num_commits=num_commits)

if __name__ == "__main__":
    mcp.run()
