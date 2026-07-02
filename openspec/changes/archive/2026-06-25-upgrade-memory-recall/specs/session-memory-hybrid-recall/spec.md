## ADDED Requirements

### Requirement: Session-scoped successful query indexing
The system SHALL index only successful historical queries for SessionMemory recall. Each indexed query MUST include the original query text, embedding, final SQL, user id, session id, database id, turn id, conversation id, success flag, and creation timestamp.

#### Scenario: Successful query is indexed
- **WHEN** a query finishes with a valid final SQL, successful execution, no rejection reason, and no error
- **THEN** the system SHALL write the query to the query recall index and write the corresponding no-result conversation data to the conversation store

#### Scenario: Failed query is not indexed
- **WHEN** a query fails, is rejected, has no final SQL, has an execution error, or is marked untrustworthy
- **THEN** the system SHALL NOT write that query to the query recall index used by SessionMemory recall

### Requirement: Two-layer SessionMemory storage
The system SHALL separate SessionMemory recall data into a query recall index and a conversation store. The query recall index SHALL be optimized for query retrieval, while the conversation store SHALL preserve historical conversation content without result data.

#### Scenario: Recall index stores retrieval metadata
- **WHEN** the system writes a successful historical query
- **THEN** the query recall index SHALL store retrieval metadata sufficient to filter by `user_id`, `session_id`, `db_id`, and `success=true`

#### Scenario: Conversation store excludes result data
- **WHEN** the system persists the historical conversation for a successful query
- **THEN** the conversation store SHALL include query and final SQL information but SHALL NOT include final result rows, large execution result payloads, LLM thinking text, or full intermediate graph state

### Requirement: Session-scoped hybrid recall
The system SHALL perform SessionMemory recall only within the current session scope. Before dense vector recall or BM25 recall, the system MUST filter candidate memories by current `user_id`, current `session_id`, current `db_id`, and `success=true`.

#### Scenario: Recall only searches current session
- **WHEN** a user submits a query with `session_id=s1`
- **THEN** SessionMemory recall SHALL only consider successful historical queries whose metadata contains `session_id=s1`, matching `user_id`, and matching `db_id`

#### Scenario: Other sessions are ignored
- **WHEN** another session contains a semantically similar successful query
- **THEN** SessionMemory recall SHALL NOT return that query unless it belongs to the current session

### Requirement: Dense and BM25 recall fusion
The system SHALL retrieve candidates using both dense vector recall and BM25 recall within the filtered session scope, merge the candidate sets, and rank them using Reciprocal Rank Fusion.

#### Scenario: Candidate appears in both channels
- **WHEN** a historical query appears in both dense vector recall and BM25 recall
- **THEN** the system SHALL compute its RRF score using both ranks

#### Scenario: Candidate appears in one channel
- **WHEN** a historical query appears only in dense vector recall or only in BM25 recall
- **THEN** the system SHALL still include it in the RRF candidate set and compute its score from the available rank

### Requirement: RRF threshold controls memory recall
The system SHALL use the final RRF score as the threshold gate for recalled memories. A historical memory SHALL be returned only when `rrf_score >= rrf_threshold`.

#### Scenario: RRF score reaches threshold
- **WHEN** a candidate memory has `rrf_score` greater than or equal to the configured `rrf_threshold`
- **THEN** the system SHALL load the corresponding conversation data by id and include it in the recalled memory set

#### Scenario: RRF score is below threshold
- **WHEN** a candidate memory has `rrf_score` below the configured `rrf_threshold`
- **THEN** the system SHALL NOT load or return that memory for HistoryCache evaluation

### Requirement: Conversation lookup after recall
The system SHALL load historical conversation content from the conversation store only after a memory passes RRF threshold filtering.

#### Scenario: Candidate passes RRF threshold
- **WHEN** a candidate passes the RRF threshold gate
- **THEN** the system SHALL use its `conversation_id` and `turn_id` to load the no-result historical conversation or the relevant historical turn summary

#### Scenario: Candidate does not pass RRF threshold
- **WHEN** a candidate does not pass the RRF threshold gate
- **THEN** the system SHALL NOT read its conversation payload from the conversation store

### Requirement: HistoryCache reuse remains authoritative
The system SHALL treat SessionMemory hybrid recall as candidate retrieval only. SQL reuse MUST still be decided by HistoryCache.

#### Scenario: HistoryCache accepts recalled SQL
- **WHEN** SessionMemory recalls a historical query and HistoryCache determines that its SQL can answer the current query with sufficient confidence
- **THEN** the system SHALL set `cache_hit=true`, use the historical SQL as `cached_sql`, and route to execution for re-running the SQL

#### Scenario: HistoryCache rejects recalled SQL
- **WHEN** SessionMemory recalls a historical query but HistoryCache determines that its SQL is not reusable
- **THEN** the system SHALL continue the standard NL2SQL pipeline instead of directly executing the historical SQL

### Requirement: Non-reusable history becomes weak SQL reference
The system SHALL preserve only historical query and final SQL as weak reference when recalled history is not reusable. The system MUST discard historical intermediate steps and result data.

#### Scenario: Recalled history is not reusable
- **WHEN** HistoryCache rejects a recalled historical memory
- **THEN** the system SHALL expose only `historical_query`, `historical_sql`, `rrf_score`, ranks, and source identifiers as weak reference for later SQL generation

#### Scenario: Weak reference is used by SQL generation
- **WHEN** SQL generation receives historical SQL references
- **THEN** it SHALL treat them as optional style or metric references and MUST NOT use tables or columns outside the currently selected schema
