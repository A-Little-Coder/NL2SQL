## Requirements

### Requirement: Fixed UserMemory topic schema
The system SHALL store UserMemory as JSON with a fixed set of top-level topics. The allowed top-level topics SHALL include `term_preferences`, `frequently_used_tables`, `metric_definitions`, `query_preferences`, `domain_context`, and `clarification_history`.

#### Scenario: New user memory is created
- **WHEN** the system creates UserMemory for a new user
- **THEN** the JSON document SHALL contain all predefined top-level topics with default empty values

#### Scenario: Unknown top-level key appears
- **WHEN** a UserMemory update contains a top-level key outside the predefined topic schema
- **THEN** the system SHALL ignore or reject that key and SHALL NOT persist it to the UserMemory JSON document

### Requirement: Schema-driven UserMemory updates
The system SHALL update UserMemory through structured patches aligned to the predefined topic schema. LLM-based summarization MUST output topic-specific updates, and code MUST merge those updates into the stored JSON.

#### Scenario: LLM produces valid topic updates
- **WHEN** the memory updater receives structured updates for predefined topics
- **THEN** the system SHALL merge those updates into the corresponding UserMemory topics without replacing unrelated topics

#### Scenario: LLM omits uncertain information
- **WHEN** the memory updater cannot determine a reliable update for a topic
- **THEN** the system SHALL preserve the existing value for that topic

### Requirement: UserMemory excludes few-shot examples
The system SHALL NOT store few-shot examples in UserMemory. SQL generation examples MUST remain managed by the SQLGenerator few-shot selection mechanism rather than long-term user memory.

#### Scenario: Memory summary includes few-shot-like examples
- **WHEN** a memory update attempts to store curated examples, demonstration examples, or few-shot SQL cases in UserMemory
- **THEN** the system SHALL filter them out and SHALL NOT persist them

#### Scenario: SQL generation needs examples
- **WHEN** SQL generation requires few-shot examples
- **THEN** the system SHALL use the SQLGenerator example selection mechanism rather than reading few-shot examples from UserMemory

### Requirement: UserMemory excludes result data and intermediate graph state
The system SHALL NOT store final result rows, large result payloads, LLM thinking text, or full intermediate graph state in UserMemory.

#### Scenario: Query completes successfully
- **WHEN** the memory updater processes a successful query
- **THEN** UserMemory SHALL only be updated with stable user-level topics such as table usage, metric definitions, query preferences, term preferences, domain context, or clarification history

#### Scenario: Update payload contains result data
- **WHEN** a UserMemory update payload contains final result rows or intermediate state data
- **THEN** the system SHALL discard those fields before saving UserMemory

### Requirement: Existing UserMemory files are normalized
The system SHALL normalize loaded UserMemory JSON documents to the fixed topic schema by adding missing predefined topics and removing unsupported top-level keys during save.

#### Scenario: Existing memory lacks a predefined topic
- **WHEN** the system loads a UserMemory file that lacks one or more predefined topics
- **THEN** it SHALL add the missing topics with default empty values before use

#### Scenario: Existing memory has unsupported top-level keys
- **WHEN** the system saves a UserMemory file containing unsupported top-level keys
- **THEN** it SHALL omit those keys from the persisted JSON
