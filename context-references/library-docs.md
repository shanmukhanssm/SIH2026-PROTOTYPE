# Library Docs

> REFERENCE EXAMPLE — format for `context/library-docs.md`. Project-specific usage patterns per library. Read the relevant section before implementing any feature touching that library.

**Authority order for any library question:**

```
MCP server docs (if configured) → skills/ → THIS FILE → official docs → training knowledge
```

Never rely on training knowledge alone — LangGraph APIs especially have shifted repeatedly. Check the pinned version's actual signatures before writing against an API.

---

## LangGraph

**Version policy:** pinned in `pyproject.toml` (currently `langgraph>=0.4`). On any graph-API error, read the installed version's source in site-packages before improvising.

### Graph assembly

```python
# src/deep_research/graph.py — the ONLY root wiring file
from langgraph.graph import StateGraph, START, END
from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver
from deep_research.nodes.plan import plan
from deep_research.nodes.synthesize import synthesize
from deep_research.nodes.critique import critique
from deep_research.subgraphs.research import research_graph
from deep_research.state import ResearchState

def build_graph(checkpointer) -> StateGraph:
    g = StateGraph(ResearchState)
    g.add_node("plan", plan)
    g.add_node("research", research_graph)   # compiled subgraph added as a node
    g.add_node("synthesize", synthesize)
    g.add_node("critique", critique)
    g.add_edge(START, "plan")
    g.add_edge("plan", "research")
    g.add_edge("research", "synthesize")
    g.add_edge("synthesize", "critique")
    g.add_conditional_edges("critique", route_after_critique,
                            {"retry": "research", "done": END})
    return g.compile(checkpointer=checkpointer)
```

Rules: wiring in `build_graph` only; routers are pure functions taking state, returning a string key; node names match the topology table in graph-design.md exactly.

### Checkpointer

```python
from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver

async with AsyncSqliteSaver.from_conn_string(config.DB_PATH) as saver:
    graph = build_graph(saver)
    # thread_id per run — minted in api.py, never inside nodes
    config = {"configurable": {"thread_id": thread_id},
              "recursion_limit": config.RECURSION_LIMIT}
    await graph.ainvoke(initial_state, config=config)
```

### Human-in-the-loop interrupt

```python
from langgraph.types import interrupt, Command

# inside plan node — the ONLY interrupt() in the codebase
answer = interrupt({"sub_questions": [q.model_dump() for q in sub_questions],
                    "question": state.question})

# answer is the resume value:
# {"action": "approve"} | {"action": "edit", "sub_questions": [...]} | {"action": "reject"}
if answer["action"] == "edit":
    sub_questions = [SubQuestion(**sq) for sq in answer["sub_questions"]]
```

```python
# resuming (api.py)
await graph.ainvoke(Command(resume=resume_payload), config=same_thread_config)
```

Rules: resume payload validated before use; the thread config must be identical to the interrupted run's.

### Subgraphs

- Subgraph gets its own state type; map parent → sub state at input, sub → parent keys at output.
- Compiled subgraphs are added as nodes: `g.add_node("research", research_graph)`.
- The subgraph is independently invokable for Layer-2 evals — design it that way from day one.

---

## OpenAI via langchain-openai

```python
# src/deep_research/models.py — the ONLY place model strings appear
from langchain.chat_models import init_chat_model

MODEL_CONFIG = {
    "plan":       {"model": "gpt-4o",       "temperature": 0.4, "max_tokens": 700},
    "take_notes": {"model": "gpt-4o-mini",  "temperature": 0.3, "max_tokens": 400},
    "synthesize": {"model": "gpt-4o",       "temperature": 0.3, "max_tokens": 1200},
    "critique":   {"model": "gpt-4o",       "temperature": 0.2, "max_tokens": 500},
}  # sourced from prompt-registry.md — keep in sync in the same commit

def get_model(role: str):
    cfg = MODEL_CONFIG[role]
    return init_chat_model(cfg["model"], temperature=cfg["temperature"],
                           max_tokens=cfg["max_tokens"])
```

### Structured output

```python
model = get_model("plan").with_structured_output(PlanOutput)
result: PlanOutput = await model.ainvoke(prompt_text)
# Pydantic instance out — validate downstream, never re-parse JSON strings
```

Rules: `with_structured_output` over JSON-mode parsing. One retry on validation failure, then the node's documented degraded path.

---

## Tavily

```python
# REST over the SDK — one less dependency surface, same capability
resp = await client.get("https://api.tavily.com/search",
                        params={"api_key": TAVILY_KEY, "query": q,
                                "max_results": n, "search_depth": "basic"})
results = resp.json()["results"]
# each: {title, url, content(snippet), score 0.0–1.0}
```

Rules: `score` drives the `should_fetch` router (≥ 0.7). Snippets are `content` — already trimmed, do not re-truncate below 200 chars.

---

## httpx + trafilatura

```python
# fetch: follow ≤3 redirects, 15s timeout, then main-content extraction
resp = await client.get(url, follow_redirects=True)
html = resp.text
main = trafilatura.extract(html, include_comments=False, include_tables=False) or ""
```

Rules: `trafilatura.extract` returning `None` → treat as fetch failure (`fetched_ok=False`), not an exception. Cap text at `max_chars` AFTER extraction, never before.

---

## LangSmith

```python
# .env — tracing is non-blocking by config; never wrap runs in try/except for tracing
LANGSMITH_TRACING=true
LANGSMITH_PROJECT=deep-research
LANGSMITH_API_KEY=lsv2_...
```

Rules: every graph invocation is traced automatically under these vars; metadata per run: `{"thread_id", "depth"}` via `config["metadata"]`. Eval runs use `LANGSMITH_PROJECT=deep-research-evals` to keep datasets separate.

---

## pytest / pytest-asyncio

```python
@pytest.mark.asyncio
async def test_search_returns_empty_on_no_results(respx_mock):
    respx_mock.get("https://api.tavily.com/search").respond(json={"results": []})
    assert await tavily_search(SearchArgs(query="obsure topic xyz")) == []
```

Rules: recorded fixtures for subgraph/e2e layers (no live web in unit/CI); live golden cases only in Layer 3 marked `e2e`; `asyncio_mode = "auto"` in pyproject.
