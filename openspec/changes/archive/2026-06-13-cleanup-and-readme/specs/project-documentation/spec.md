## ADDED Requirements

### Requirement: README shall follow a layered structure
The README SHALL organize content from "quick start for end users" to "deep reference for maintainers".

#### Scenario: Top-down information architecture
- **WHEN** a reader scrolls through the README
- **THEN** sections appear in this order: Project Overview, Quick Start, Architecture, Module Reference, API Reference, LangSmith Integration, LangGraph Studio Debugging, Development Guide, Testing, Configuration, FAQ

### Requirement: Architecture diagram
The README SHALL include an ASCII or Mermaid architecture diagram showing the NL2SQL pipeline stages.

#### Scenario: Diagram exists
- **WHEN** a reader reaches the Architecture section
- **THEN** a diagram shows the flow from user query → preprocessing → IR retrieval → schema selection → SQL generation → execution → decision → response

### Requirement: Module reference
The README SHALL list every package under `src/` with a brief description of its responsibility.

#### Scenario: Package listing is complete
- **WHEN** comparing the README module list to `ls src/`
- **THEN** every directory under `src/` is documented

### Requirement: Environment configuration
The README SHALL document all environment variables consumed by the project.

#### Scenario: Required variables are documented
- **WHEN** a developer sets up the project
- **THEN** the README lists `OPENAI_API_KEY`, `LANGCHAIN_API_KEY`, `LANGCHAIN_PROJECT`, `TAVILY_API_KEY` with their purpose

### Requirement: Run instructions
The README SHALL include instructions for running both the API server and the LangGraph Studio dev server.

#### Scenario: API server startup is documented
- **WHEN** a developer follows the "Run" section
- **THEN** they can start `python src/run_api.py` successfully

#### Scenario: LangGraph Studio startup is documented
- **WHEN** a developer follows the "Debug" section
- **THEN** they can start `langgraph dev` and access Studio UI