# Why RLM + IPython?

Adaptive Agent Harness combines a bounded Recursive Language Model (RLM) runtime with persistent IPython workspaces. This page explains the product and architecture reasoning behind that choice.

## 1. The problem: context is an expensive interface

Large agent tasks often fail in predictable ways:

- the input is larger than one useful model context;
- files or documents are repeatedly reread;
- intermediate tables and hypotheses are converted back into prose;
- tool schemas and raw results consume the context window;
- a client disconnect is mistaken for cancellation;
- retries occur without knowing whether the previous call produced an effect;
- a new process cannot distinguish old state from current authority.

A larger context window helps, but it does not turn language generation into reliable state management or deterministic computation.

## 2. RLM: treat long context as an environment

Zhang, Kraska, and Khattab describe Recursive Language Models as an inference paradigm in which the long prompt is placed in an external environment. The model can programmatically examine and decompose it, then recursively call a model over selected snippets.

The key product idea is simple:

> Do not ask the model to remember every byte. Let it decide which bytes deserve language reasoning.

An RLM loop can use ordinary code for search, filtering, aggregation, ranking, deduplication, and bookkeeping. Model calls are reserved for ambiguous interpretation, synthesis, or generation.

Adaptive Agent Harness adds explicit bounds:

- maximum wall time;
- maximum model requests;
- input and output token budgets;
- child-operation and artifact-byte budgets;
- grants and deadlines;
- durable request and receipt digests.

“Recursive” therefore does not mean uncontrolled fan-out. Calls are brokered, counted, retained, and attributable to one operation.

## 3. IPython: a computational working set

An RLM needs an environment that is useful to a model and familiar to developers. IPython provides:

- persistent variables and helper functions;
- introspection and readable errors;
- DataFrames and arrays;
- iterative execution;
- rich Python ecosystem access;
- a natural CodeAct-style interface.

The workspace can retain investigation state that would be wasteful or lossy in the prompt:

```python
candidate_sections = search(corpus, query)
ranked = score(candidate_sections)
selected = ranked[:20]
sub_answers = [ask_model(x) for x in selected]
result = synthesize(sub_answers)
```

The prompt can hold the plan and compact references. The workspace holds the data, functions, and computed state.

## 4. The synergy

RLM and IPython are complementary:

| Failure mode | RLM response | IPython response |
|---|---|---|
| Input too large | Select and recursively query relevant slices | Search, filter, index, and aggregate the external data |
| Repeated tool loops | Replace chatter with a bounded program | Execute loops and joins locally |
| Lost intermediate state | Persist step and receipt boundaries | Retain variables, tables, and helper functions |
| Too much serialized output | Keep compact references in context | Keep full values in the workspace or artifacts |
| Ambiguous retry | Record broker request/receipt state | Recompute deterministic transforms without replaying effects |
| Frontend disconnect | Continue under a durable operation | Keep worker ownership separate from the frontend |

Adaptive Agent Harness connects these layers with a durable supervisor, versioned operation state, explicit authority, and MCP access.

## 5. Persistence without pretending everything is portable

A live IPython process may contain modules, open files, sockets, generators, threads, iterators, DataFrames, arrays, and native objects. Serializing all of that is unsafe and often meaningless.

The current checkpoint contract is intentionally narrower:

- JSON-like values can be included in a deterministic manifest;
- unsupported values appear as explicit exclusions;
- artifact references can represent larger portable data;
- checkpoint identity is bound to workspace generation and revision;
- restore into a new generation remains a separately verified capability gate.

This is more honest than presenting `pickle` as universal process recovery.

## 6. Durable operations around the workspace

The workspace is not the source of authority. The operation registry retains:

- logical operation identity;
- attempts, workers, generations, leases, and revisions;
- grants, budgets, deadlines, and cancellation intent;
- broker requests and authoritative receipts;
- progress events, result, certainty, and reconciliation state;
- artifacts and checkpoint bindings.

A frontend can disappear while the supervisor continues to own the operation. A new authorized connection can read the state. A recovery successor is admitted only when the persisted boundary is certain and compatible.

## 7. What we learned from adjacent projects

### Prime Agent

Prime Agent publicly positions persistent IPython as the built-in model tool and native subagents as programmatic function calls. Its fuller agent runtime also includes daemon-backed continuity and coding/research workflows.

That is strong evidence for the user value of the RLM + persistent-workbench model. Adaptive Agent Harness chooses a different product boundary: it focuses on portable operation contracts, receipts, evidence, and host-owned authority. A future integration can place Prime or another rich worker behind that boundary.

### NVIDIA Object Oriented Agents (NOOA)

NOOA explores Python-native, typed agent objects and CodeAct-style orchestration. This supports a useful interface direction: capabilities should be discoverable as typed methods and return structured objects rather than forcing every action through an opaque `exec_python` string.

NOOA is design input only. The current package has no NOOA adapter or dependency.

## 8. Good workloads

The combination works best when a task has both language ambiguity and computational structure:

- compare many documents with citations;
- map a large codebase and retain impact evidence;
- iterate over experiments or evaluation rows;
- analyze tables and request model judgment only on selected cases;
- run bounded research subcalls and combine their receipts;
- maintain a reproducible evidence trail across a long task.

It is less useful for a tiny one-shot question, a fully deterministic script, or untrusted code that requires a real security sandbox.

## 9. Safety and authority

Persistent Python is powerful. It is not isolation.

Adaptive Agent Harness therefore keeps several capabilities deliberately outside the runtime:

- provider credentials;
- general external-effect execution;
- final user delivery;
- host admission and approval;
- multi-tenant security isolation.

The host selects credentials, grants, workspace, budget, and effect policy. The runtime computes, records, and proposes.

## Sources

1. Alex L. Zhang, Tim Kraska, and Omar Khattab, [Recursive Language Models](https://arxiv.org/abs/2512.24601), arXiv:2512.24601 (v3 observed 2026-05-11).
2. [Prime Agent](https://github.com/PrimeIntellect-ai/prime-agent), product README and RLM/runtime documentation.
3. [NVIDIA Object Oriented Agents](https://github.com/NVIDIA-NeMo/labs-OO-Agents), public repository.
4. [Model Context Protocol](https://modelcontextprotocol.io/), public specification.

Competitor and research references support the described design influences only. They do not establish compatibility, endorsement, relative performance, or a production guarantee for Adaptive Agent Harness.
