# LangGraph

For engineers who want a LangGraph agent's writes reviewed. Read the
[integration guide](integration.md) first, above all
[what reviewers must not see](integration.md#what-reviewers-must-not-see).

> **Status:** pre-release and not pre-validated. Tested with `langgraph` 1.2.24 and, in CI, the
> newest release.

```bash
pip install "reviewproof[langgraph]"
```

## How it fits together

ReviewProof gives you a graph step, `ReviewStep`, that takes **one tool call**. Your graph sends
it each call. The step submits the call to review, pauses, and after a decision writes through
ReviewProof's guard.

This example uses a scripted model in place of a real one:

```python
import json
from typing import Annotated, TypedDict

from langchain_core.messages import AIMessage, AnyMessage, ToolMessage
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.graph import END, START, StateGraph
from langgraph.graph.message import add_messages

from reviewproof import Oversight, initialise
from reviewproof.adapters.langgraph import ReviewStep
from reviewproof.adapters.tool import ReviewedTool

settings = "examples/deviation_triage/reviewproof.yaml"
with open("examples/deviation_triage/items.json", encoding="utf-8") as file:
    deviation = json.load(file)[0]
initialise("oversight.db", "planted.db")  # once, when you set up
review_queue = {}
written = []


def save_deviation(record_id, **fields):  # your write function
    written.append(record_id)
    return "saved"


tool = ReviewedTool(
    lambda: Oversight(settings, "deviation-triage"),
    save_deviation,
    target_argument="record_id",
    enqueue=lambda ticket: review_queue.setdefault(ticket.token, ticket.payload),
)
review = ReviewStep({"save_deviation": tool})  # the tool name your model uses


class State(TypedDict):
    messages: Annotated[list[AnyMessage], add_messages]


def model(state):  # stands in for your model
    if isinstance(state["messages"][-1], ToolMessage):
        return {"messages": [AIMessage(content="Done.")]}
    call = {"name": "save_deviation", "args": {**deviation, "record_id": "QMS-401"}}
    return {"messages": [AIMessage(content="", tool_calls=[{**call, "id": "call-1"}])]}


def route(state):
    calls = state["messages"][-1].tool_calls
    return review.sends("review", calls) or END  # one step for each call


builder = StateGraph(State)
builder.add_node("model", model)
builder.add_node("review", review)  # no retry policy
builder.add_edge(START, "model")
builder.add_conditional_edges("model", route, ["review", END])
builder.add_edge("review", "model")
agent = builder.compile(checkpointer=InMemorySaver())  # use a durable one in production
config = {"configurable": {"thread_id": "run-7"}}

# 1. Run until it pauses. The tickets are now in your review queue.
agent.invoke({"messages": [("user", "Record the deviation.")]}, config)
assert written == []

# 2. In your review application, after every decision.
with Oversight(settings, "deviation-triage") as oversight:
    for token, payload in review_queue.items():
        oversight.shown(token, reviewer="r-104")
        oversight.decided(token, reviewer="r-104", decision="approve")
        oversight.settle(token, payload)

# 3. Resume whatever has been decided. None means nothing is ready.
command = review.resume_command(agent, config)
if command is not None:
    agent.invoke(command, config)
assert written == ["QMS-401"]
assert review.resume_command(agent, config) is None
```

## Rules

- **Send each call to the step with `sends()`.** Do not give the step the graph state, and do
  not call a `ReviewedTool` from LangGraph's prebuilt `ToolNode` or from a step that makes other
  reviewed calls. LangGraph runs a paused step again from its start, so a step with two reviewed
  calls reaches the first write again. The guard refuses it, the run breaks and the second
  approved call never runs.
- **Route your other tools yourself.** `sends()` returns only calls to the tools you gave the
  step.
- **On Python 3.10, use `invoke`, not `ainvoke`.** LangGraph itself cannot pause inside
  `ainvoke` before Python 3.11.
- **Use a checkpointer and a `thread_id`.** The key for each call is the thread ID and the
  model's tool-call ID. If your model repeats a call ID within a thread, the later call is
  refused.
- **Do not give the step a retry policy.** A retry cannot write twice, but its error hides the
  first one.
- **Let errors from `invoke` reach your application.** After a failed write, check the target
  system. Running the graph again does not repeat the write.
- **The pause names the tool and the key,** and holds no ticket or content. Do not build your
  review screen from it.
- **Resume with `resume_command()`.** It offers only pauses whose action has been decided, and a
  resumed pause is not offered again. What you pass back on resume is ignored: the recorded
  decision is what counts.
- **Edits** need `approved_payload` on the `ReviewedTool`: see
  [agents that pause](integration.md#agents-that-pause-and-resume).

## When to resume

**Resume on a schedule, not straight after each decision,** unless your review queue is busy
enough that nobody can connect the two. When the agent continues, it may send its next action to
review. In a quiet queue, an item that arrives right after a decision shows that the decided item
was real. A schedule alone may not hide this in a very quiet queue: if only one decision was
made since the last resume, what arrives next still points to it. Make sure enough other work
arrives, or that reviewers cannot see when items arrive.

## What leaves your computer

ReviewProof sends nothing and changes no tracing settings. In ReviewProof's test of LangGraph,
with tracing not set up, no connection was attempted. **LangSmith tracing**, if you turn it on,
sends data about your runs to LangSmith. Decide whether that is allowed for your data, and keep
reviewers away from the traces.
