# System Responsibilities

| Layer | Owns task logic? | Owns tools? | DB access? | Main role |
|---|---:|---:|---:|---|
| Frontend | No | No | Local cache only | UI and user config |
| API Endpoint | No | No | Yes | Request intake and task decision |
| Queue / Load Balancer | No | No | Optional/minimal | Route tasks to workers |
| Worker Servers | Yes | Yes | Yes | Execute workflows |
| LLM Server | No | No | No | LLM inference helper |
| Web Server | No | No | No | General web interaction helper |
| LinkedIn Bot Server | No | No | No | LinkedIn browser automation helper |
| MongoDB Docker / Server DB | No | No | Source of truth | Persistent server data |
| Local Mongita DB | No | No | Client cache | Fast frontend reads and offline-ish UI state |

API Endpoint responsibilities:

- Receive frontend requests.
- Validate request shape and user permissions.
- Decide which task type should run.
- Create normalized task payload.
- Persist request/task metadata to server DB.
- Forward the task to queue or load balancer.
- Return task status and cached results to frontend.

Worker Server responsibilities:

- Own workflows and tool execution.
- Own LLM runtime loop orchestration.
- Read and write server MongoDB.
- Call helper services.
- Save webpage snapshots and browser state metadata.
- Return structured task results.

Helper service responsibilities:

- LLM Server accepts OpenAI-compatible chat requests.
- Web Server provides generic browser/page interaction.
- LinkedIn Bot Server provides LinkedIn-specific browser automation and can manage multiple Chrome profiles.

Important boundary:

Helper services do not decide business workflow. Workers decide what to do, then call helpers to do specific steps.
