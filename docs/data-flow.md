# Product Data Flow

| Task | Frontend input | Saved to client DB | Response | Saved to server DB | Frontend output/configuration tab |
|---|---|---|---|---|---|
| Onboard - Create | Upload documents, edit fields, submit onboard create | Initial documents: doc1, doc2, doc3, etc. | Status + result | Stored documents + onboard result | Document upload and editing section / management tab |
| Onboard - Edit | Edit existing documents, upload replacements, submit onboard edit | Client-side updated documents | Status + result | Updated documents + updated onboard result | Document upload and editing section / management tab |
| Find LinkedIn Job Listings | Search preferences, filters, pages to fetch | Search preference snapshot | Job listings table | Cached/searchable job listings + webpage snapshot per listing/search result | Listings table + checkbox filters |
| Analyze / Score Listings | `digitized_user` with `constraints.hard_yes` and `constraints.hard_no`, candidate listings, optional scoring settings | Candidate listings snapshot | Scored listings table | Stored scored candidates + per-batch result snapshot, including compact `exclude_reason.current` and `exclude_reason.target` for exclusions | Listings table + checkboxes + weights |
| Fetch Listing Descriptions | Select listing IDs from filtered table, fetch descriptions | Selected listing IDs | Listing descriptions | Stored listings + fetched descriptions + webpage snapshot per fetched listing | Filtered listings tab + checkboxes |
| Analyze / Score Listing Descriptions | Select descriptions, apply filters, adjust weights, run scoring | Selected descriptions + filters + weights | Scored listings table | Stored descriptions + metadata | Description scoring tab + filters + checkboxes + weights |
| Apply Jobs | Select scored jobs, confirm applications | Selected scored jobs | Application status table | Stored job data + application state + webpage snapshot per application result | Final application selection tab |

Core persistence rule:

Initial inputs originate from the client, but most durable data is persisted and processed on the server. The frontend reads from its local cache, which reflects the latest server DB state. For every fetched webpage result, save a snapshot of the webpage in server DB so future tasks can inspect the exact state used earlier.

Current prototype note:

The current repo uses `EmbeddedMongoStore` as a local JSON Mongo-style mock. Production should use MongoDB Docker / server DB.

Onboarding handoff note:

The onboarding task should emit one stable `digitized_user` object that downstream scoring stages can consume directly. Keep eligibility, seniority, application policy, and user preferences in separate buckets so the scorer does not have to infer them from a flattened summary.
When extracting onboarding data, treat explicit resume bullets like UAE National, driving license, availability, and work-arrangement compatibility as primary facts, then normalize them into canonical buckets instead of copying them verbatim into role lists.
If a preference section mixes concrete choices with explanatory prose, keep the choices in the main list and move the prose into a sibling `notes` list so the scorer sees the signal cleanly.
If a preference bullet combines a numeric range or amount with a reason clause, keep the numeric range as the primary value and move the reason into `notes`.
If a compensation trade-off such as `lower_if` includes a rationale sentence, keep the trade-off in the main list and move the rationale into `notes`.
If a hard constraint contains both the actual rule and an explanation, keep the rule in `constraints.hard_yes` or `constraints.hard_no` and move only the explanatory fragment into `constraints.notes`. Only promote a constraint to `hard_no` when the source uses explicit dealbreaker language or the item is objectively unsafe, invalid, illegal, unpaid, commission-only, or otherwise clearly unacceptable; softer "poor fit" wording should stay out of hard-no unless the source is explicit.
Only keep a `notes` entry when it adds information that is not already represented by the structured field; if the bucket already captures the meaning, leave `notes` empty.
Keep `seniority.evidence` factual and `seniority.hints` for instruction-like cues such as “entry-level / recent-graduate profile.”
Treat formal graduate programs, UAE National / Emirati / UAEN aliases, and talent-pool text carefully: only mark them as hard no when the source clearly says they are vague, unpaid, commission-only, or otherwise invalid.
If the document contains numeric salary or compensation ranges, keep them structured instead of only preserving the raw text. The scorer should be able to compare them without reparsing the source.
Do not mark the handoff complete unless the required identity/contact fields are actually present; missing required fields should keep the output partial and lower confidence.
