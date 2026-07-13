"""The one and only generative step in the system: an LLM writes the final
answer from the top-5 reranked chunks. Everything upstream (embeddings,
cross-encoder) is local, non-generative retrieval machinery.

Uses litellm so the model/provider is just a string in .env (LLM_MODEL) —
e.g. "ollama/llama3.1", "claude-sonnet-5", "gpt-4o-mini" — with no
provider-specific code here. litellm reads ANTHROPIC_API_KEY / OPENAI_API_KEY
from the environment automatically for hosted providers.
"""
import litellm

from app.config import settings

SYSTEM_PROMPT = (
    "You are a question-answering assistant for a shared document knowledge base. "
    "Answer the user's question using ONLY the numbered context chunks below. "
    "Cite the chunks you rely on inline like [1] or [2]. "
    "If the context doesn't contain enough information to answer, say "
    '"I don\'t know" rather than guessing or using outside knowledge.'
)


def generate_answer(question: str, ranked_chunks: list[dict]) -> str:
    context_blocks = "\n\n".join(
        f"[{i}] {chunk['content']}" for i, chunk in enumerate(ranked_chunks, start=1)
    )
    user_message = f"Context:\n{context_blocks}\n\nQuestion: {question}"

    response = litellm.completion(
        model=settings.llm_model,
        api_base=settings.llm_api_base if settings.llm_model.startswith("ollama/") else None,
        messages=[
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": user_message},
        ],
    )
    return response.choices[0].message.content
