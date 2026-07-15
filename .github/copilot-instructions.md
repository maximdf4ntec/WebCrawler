**SCOPE WARNING:** These instructions apply strictly to the **Python/Agno Backend** located in the `backend/` directory. 
If the user is editing files in `frontend/`, rely on standard React best practices and ignore the Python rules below.
## 1. Python & Architecture Standards
* **Framework**: Use **Agno** for all agentic logic.
* **Style**: Strict **Object-Oriented Programming (OOP)**.
    * Use Pydantic models for data validation (standard in Agno).
    * Use Type Hints (`def my_func(x: int) -> str:`) everywhere.
    * Include Google-style Docstrings for every class and method.
* **Design Patterns**:
    * Dependency Injection for tools and database connections.
    * Factory Pattern for creating Agent instances.
* **Frontend**: React (Vite) + Ant Design (if UI components are needed).

## 2. Task vs. Discussion Protocol
You must classify every user input into one of two categories:
1.  **DISCUSSION**: User asks "How do I...", "Explain...", or "What is better...".
    * *Action:* Answer normally.
2.  **PROGRAMMING TASK**: User asks "Build...", "Refactor...", "Fix...", or "Create...".
    * *Action:* Execute the code generation **AND** append a **Task Log** (see below).

## 3. Automatic Task Logging
If the input is a **PROGRAMMING TASK**, you must end your response with a Markdown block titled "LOG ENTRY". The user will save this to their `tasks/` folder.
Format:
```markdown
# [TASK-ID] <Short Title>
**Type:** Programming
**Status:** Pending Review
**Date:** {Current Date}

## Context
<1-2 sentence summary of what was requested>

## Implementation Details
* [ ] Created/Modified `file_name.py`
* [ ] Logic added: <brief description>

## Validation Strategy
* [ ] Unit Test generated? (Yes/No)
* [ ] Integration Test generated? (Yes/No)