## ADDED Requirements

### Requirement: Dead code removal
The cleanup SHALL remove all Python code that is confirmed unreachable or unused by the current runtime.

#### Scenario: Unreferenced imports are removed
- **WHEN** `autoflake --check --imports` scans a source file
- **THEN** no unreferenced import statements remain in the file

#### Scenario: Unused local variables are removed
- **WHEN** static analysis scans for assigned-but-never-read local variables
- **THEN** all such variables are removed

#### Scenario: Orphaned functions/classes are identified
- **WHEN** `vulture` scans the source tree
- **THEN** all functions, methods, and classes with zero call sites are listed for human review

### Requirement: Legacy module removal
The cleanup SHALL remove modules, classes, or functions that have been completely replaced by a newer implementation and are no longer called by any active code path.

#### Scenario: Replaced pipeline logic is removed
- **WHEN** an older function/class is found whose functionality is fully covered by an active LangGraph subgraph node
- **THEN** the older implementation is deleted

#### Scenario: Orphaned test code is removed
- **WHEN** a test file or test function references a symbol that no longer exists in the source tree
- **THEN** that test file or function is deleted

### Requirement: README completeness
The project documentation SHALL include a root-level README.md covering all sections defined in this specification.

#### Scenario: Installation instructions exist
- **WHEN** a new developer reads the README
- **THEN** they can install all dependencies with a single command

#### Scenario: Architecture overview exists
- **WHEN** a new developer reads the README
- **THEN** they see a diagram and description of the NL2SQL pipeline stages

#### Scenario: Module index exists
- **WHEN** a developer searches for a specific module's responsibility
- **THEN** the README lists every top-level `src/` package with a one-line description

#### Scenario: API usage example exists
- **WHEN** a user wants to call the NL2SQL API
- **THEN** the README contains at least one working curl example

#### Scenario: Monitoring & debugging section exists
- **WHEN** a developer wants to trace a query through the system
- **THEN** the README explains how to use LangSmith trace and LangGraph Studio

#### Scenario: Test instructions exist
- **WHEN** a contributor wants to run tests
- **THEN** the README documents the test command and any prerequisite setup