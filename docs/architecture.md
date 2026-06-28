# Final Architecture

The full product is a distributed job-search automation system. The frontend is only the UI and local cache. The API receives requests and decides task type. Workers own the actual workflow logic and call helper services for LLM, web, and LinkedIn automation.

```text
Users
  v
Frontend
  <-> Local Mongita DB / Client Cache
  v
API Endpoint
  v
Queue / Load Balancer
  v
Worker Servers
```

Shared server persistence:

```text
API Endpoint  <->  MongoDB Docker / Server DB  <->  Worker Servers
```

Full runtime shape:

```text
Users
  v
Frontend
  <-> Local Mongita DB / Client Cache
  v
API Endpoint
  |- receives frontend requests
  |- validates input
  |- decides task type
  |- creates task payload
  `- forwards task
        v
Queue / Load Balancer
  `- routes task to available Worker Server
        v
Worker Servers
  |- Task workflow logic
  |- Tool functions
  |- Read/write access to MongoDB Docker / Server DB
  |
  |- uses -> LLM Server
  |          `- OpenAI-compatible server format
  |
  |- uses -> Web Server
  |          `- general web interface/helper
  |
  `- uses -> LinkedIn Bot Server
             `- LinkedIn interface/helper
                |- many Chrome instances
                `- separate browser profile per instance

MongoDB Docker / Server DB
  <-> API Endpoint
  <-> Worker Servers
```

Clean mental model:

```text
Frontend = UI
API = task intake + task decision
Queue / Load Balancer = dispatch
Workers = actual work + workflows + tools
LLM/Web/LinkedIn servers = helper interfaces
MongoDB = shared server source of truth
Mongita = frontend/local cache
```

Current repo status:

| Area | Current status |
|---|---|
| Browser automation | Implemented locally with Selenium and undetected-chromedriver |
| HTML to markdown | Implemented as `output_markdown(driver)` |
| Generic web tools | Implemented as local wrappers in `browser/webagent.py` |
| LinkedIn tools | Implemented as local wrappers in `browser/linkedin.py` |
| LLM runtimes | Implemented as notebook test loops under `tests/llm_test*` |
| MongoDB server | Not implemented yet |
| Mongita frontend cache | Not implemented yet |
| API endpoint | Not implemented yet |
| Queue/load balancer | Not implemented yet |
| Worker server process | Not implemented yet |
| Helper services | Not split into network services yet |
