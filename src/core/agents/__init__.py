"""Agent support: judgement where a rule cannot reach.

Four kinds, deliberately separated because they have different risks.

``provider``  how a model is reached at all, and what happens when none is
              configured. Everything else goes through it.
``analysts``  agents that read crawled pages and judge what rules cannot:
              experience and expertise signals, whether a passage can be
              quoted by an AI answer, whether content is thin in substance
              rather than in word count.
``fixer``     hands a repository's issues to a coding agent as a brief, and
              records what came back. It proposes; it never commits.
``monitor``   watches a series of crawls and decides which changes are worth
              waking someone for.

The rule that governs all of them: **a model's opinion is evidence, not a
finding**. Everything an agent produces is marked as such, carries the model
that said it, and is severity `info` unless a deterministic check agrees.
A crawler whose errors are generated text is a crawler nobody can trust.
"""

from src.core.agents import provider  # noqa: F401
