# Product Data Flow

| Task | Frontend input | Saved to client DB | Response | Saved to server DB | Frontend output/configuration tab |
|---|---|---|---|---|---|
| Onboard - Create | Upload documents, edit fields, submit onboard create | Initial documents: doc1, doc2, doc3, etc. | Status + result | Stored documents + onboard result | Document upload and editing section / management tab |
| Onboard - Edit | Edit existing documents, upload replacements, submit onboard edit | Client-side updated documents | Status + result | Updated documents + updated onboard result | Document upload and editing section / management tab |
| Find LinkedIn Job Listings | Search preferences, filters, pages to fetch | Search preference snapshot | Job listings table | Cached/searchable job listings + webpage snapshot per listing/search result | Listings table + checkbox filters |
| Analyze / Score Listings | Select listings, adjust scoring weights, run scoring | Selected listings + scoring weights | Scored listings table | Stored listings data | Listings table + checkboxes + weights |
| Fetch Listing Descriptions | Select listing IDs from filtered table, fetch descriptions | Selected listing IDs | Listing descriptions | Stored listings + fetched descriptions + webpage snapshot per fetched listing | Filtered listings tab + checkboxes |
| Analyze / Score Listing Descriptions | Select descriptions, apply filters, adjust weights, run scoring | Selected descriptions + filters + weights | Scored listings table | Stored descriptions + metadata | Description scoring tab + filters + checkboxes + weights |
| Apply Jobs | Select scored jobs, confirm applications | Selected scored jobs | Application status table | Stored job data + application state + webpage snapshot per application result | Final application selection tab |

Core persistence rule:

Initial inputs originate from the client, but most durable data is persisted and processed on the server. The frontend reads from its local cache, which reflects the latest server DB state. For every fetched webpage result, save a snapshot of the webpage in server DB so future tasks can inspect the exact state used earlier.

Current prototype note:

The current repo uses `EmbeddedMongoStore` as a local JSON Mongo-style mock. Production should use MongoDB Docker / server DB.
