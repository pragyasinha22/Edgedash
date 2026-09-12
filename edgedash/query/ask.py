"""
Natural language query interface — two-call pipeline per rules 42-45.

ROUTE: model selects tool from registry
EXECUTE: tool runs with validated params
PHRASE: model turns rows into prose using only numbers present
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from edgedash import storage
from edgedash.config import load_config
from edgedash.llm import LLMError, complete_json
from edgedash.query import guards
from edgedash.query.tools import TOOLS


# ---------------------------------------------------------------------------
# Answer dataclass
# ---------------------------------------------------------------------------

@dataclass
class Answer:
    """Result of asking a question."""
    text: str
    rows: list[dict[str, Any]]
    tool_used: str | None
    params: dict[str, Any] | None
    summary: str | None


# ---------------------------------------------------------------------------
# Routing prompt
# ---------------------------------------------------------------------------

def _build_routing_prompt(question: str, tools: dict) -> str:
    """Build the prompt for the ROUTE call - tool selection."""

    # Build tool descriptions
    tool_descriptions = []

    for tool_name, tool_info in tools.items():
        desc = tool_info["description"]
        params = tool_info["params"]

        tool_descriptions.append(
            f"- {tool_name}: {desc}"
        )

        if "properties" in params:
            for param_name, param_spec in params["properties"].items():
                param_desc = param_spec.get("description", "")
                default = param_spec.get("default", "")

                tool_descriptions.append(
                    f"  - {param_name}: {param_desc} (default: {default})"
                )

    tools_text = "\n".join(tool_descriptions)

    return f"""You are a query router. Your job is to select the right tool to answer a user's question.

AVAILABLE TOOLS:

{tools_text}

USER QUESTION:

{question}

INSTRUCTIONS:

1. Read the user's question carefully.

2. Select the tool that EXACTLY matches what the user is asking for.

3. If NO tool matches the question, return tool=null. DO NOT pick the closest tool. DO NOT guess. DO NOT force a match.

4. Extract any parameters the user specified from the question.

5. IMPORTANT PARAMETER INTERPRETATION RULES:

   - If the user asks for a job role/title AND a location, keep them as separate parameters.

     Example:
     "Show me Data Analyst jobs in Bengaluru."
     -> keyword="Data Analyst", location="Bengaluru"

   - If the user asks for jobs requiring MULTIPLE skills, put each skill separately in the skills array.

     Example:
     "Show me jobs with SQL and Python."
     -> skills=["SQL", "Python"]

   - Do NOT combine multiple filters into one keyword.

     WRONG:
     keyword="Data Analyst Bengaluru"

     CORRECT:
     keyword="Data Analyst"
     location="Bengaluru"

   - For "Which companies are hiring Data Analysts?", use companies_hiring and set keyword="Data Analyst".

   - For "Which companies are hiring?" without a specific role or keyword, use companies_hiring with its default parameters.

   - For a single skill question such as "Which jobs require Python?", use search_listings with skills=["Python"].

   - For a single role/title question such as "Show me Data Analyst jobs", use search_listings with keyword="Data Analyst".

6. Return your response as JSON with this schema:

{{
  "tool": "tool_name" | null,
  "params": {{"param_name": value, ...}},
  "confidence": "high" | "low"
}}

STRICT RULES:

- If the question cannot be answered by ANY tool above, return tool=null.

- Never return a tool name that is not in the available tools list above.

- Do not invent parameters that are not in the tool's spec.

- If a parameter is not specified by the user, use the default value from the tool spec.

- Keep different filters in their correct parameters. Do not merge them into the keyword parameter.

- When multiple skills are requested, use the skills array rather than putting the skills together in keyword.

- Set confidence="high" only when the tool CLEARLY and EXACTLY matches the question.

- Set confidence="low" if the match is uncertain but you're making a best effort.

- When in doubt, return tool=null. It is better to say "I can't answer this" than to pick the wrong tool.

Return ONLY the JSON response. No other text."""


# ---------------------------------------------------------------------------
# Phrasing prompt
# ---------------------------------------------------------------------------
def _build_phrasing_prompt(question: str, rows: list[dict], summary: str) -> str:
    """Build the prompt for the PHRASE call - turn rows into prose."""
    rows_text = "\n".join(str(row) for row in rows)

    return f"""You are a data analyst. Write a 2-3 sentence answer to the user's question based ONLY on the data rows provided.

USER QUESTION:

{question}

DATA SUMMARY:

{summary}

DATA ROWS:

{rows_text}

STRICT RULES:

- Use ONLY the numbers present in these rows. Do not estimate, extrapolate, or add outside context.
- If the rows are empty, say plainly: "The data does not contain an answer to this question."
- Do not use any numbers that are not in the rows above.
- Do not make up statistics or averages that aren't explicitly in the data.
- Keep your answer to 2-3 sentences maximum.

Return ONLY your answer text. No other text."""


# ---------------------------------------------------------------------------
# Number extraction for post-check
# ---------------------------------------------------------------------------

def _extract_numbers(text: str) -> list[float]:
    """Extract all numbers (integers and decimals) from text."""
    return [float(match) for match in re.findall(r"\d+(?:\.\d+)?", text)]


def _numbers_in_rows(numbers: list[float], rows: list[dict]) -> bool:
    """Check if all numbers appear somewhere in the row data."""
    if not numbers:
        return True
    
    # Flatten all values from rows into strings
    row_values = []
    for row in rows:
        for value in row.values():
            if value is not None:
                row_values.append(str(value))
    
    row_text = " ".join(row_values)
    
    # Check each number
    for num in numbers:
        # Check both integer and decimal representations
        if str(int(num)) not in row_text and f"{num:.1f}" not in row_text and f"{num:.2f}" not in row_text:
            return False
    
    return True


# ---------------------------------------------------------------------------
# Main ask function
# ---------------------------------------------------------------------------

def ask(question: str) -> Answer:
    """
    Ask a natural language question about the data.
    
    Returns:
        Answer with text, rows, tool_used, params, summary
    """
    start_time = datetime.now(timezone.utc)
    config = load_config()
    
    # 1. Run abuse guards
    allowed, rejection_reason, log_reason = guards.check_question_allowed(question)
    if not allowed:
        # Log the rejection
        storage.log_query(
            path=config.db_path,
            question=question,
            tool_used=None,
            params=None,
            answerable=False,
            reason=log_reason,
            duration_ms=int((datetime.now(timezone.utc) - start_time).total_seconds() * 1000),
        )
        return Answer(
            text=rejection_reason,
            rows=[],
            tool_used=None,
            params=None,
            summary=None,
        )
    
    # 2. ROUTE - select tool
    routing_prompt = _build_routing_prompt(question, TOOLS)
    routing_schema = {
        "type": "object",
        "properties": {
            "tool": {"type": ["string", "null"]},
            "params": {"type": "object"},
            "confidence": {"type": "string", "enum": ["high", "low"]},
        },
    }
    
    try:
        routing_result = complete_json(routing_prompt, routing_schema)
        print("DEBUG ROUTING RESULT:", routing_result)
    except LLMError as exc:
        # Log routing failure
        storage.log_query(
            path=config.db_path,
            question=question,
            tool_used=None,
            params=None,
            answerable=False,
            reason=f"routing failed: {exc}",
            duration_ms=int((datetime.now(timezone.utc) - start_time).total_seconds() * 1000),
        )
        return Answer(
            text="I couldn't process that question. Please try again.",
            rows=[],
            tool_used=None,
            params=None,
            summary=None,
        )
    
    tool_name = routing_result.get("tool")
    params = routing_result.get("params", {})
    
    # 3. NULL handling - no tool matched
    if tool_name is None or tool_name not in TOOLS:
        # Build list of available tools
        available_tools = "\n".join(
            f"- {name}: {info['description']}"
            for name, info in TOOLS.items()
        )
        
        text = f"I can't answer that question with the available tools. Here's what you can ask:\n\n{available_tools}"
        
        storage.log_query(
            path=config.db_path,
            question=question,
            tool_used=None,
            params=None,
            answerable=False,
            reason="no tool matched",
            duration_ms=int((datetime.now(timezone.utc) - start_time).total_seconds() * 1000),
        )
        
        return Answer(
            text=text,
            rows=[],
            tool_used=None,
            params=None,
            summary=None,
        )
    
    # 4. EXECUTE - run the tool
    tool_info = TOOLS[tool_name]
    tool_func = tool_info["function"]
    
    try:
        rows, summary = tool_func(**params)
    except Exception as exc:
        storage.log_query(
            path=config.db_path,
            question=question,
            tool_used=tool_name,
            params=params,
            answerable=False,
            reason=f"tool execution failed: {exc}",
            duration_ms=int((datetime.now(timezone.utc) - start_time).total_seconds() * 1000),
        )
        return Answer(
            text=f"Something went wrong: {exc}",
            rows=[],
            tool_used=tool_name,
            params=params,
            summary=None,
        )
    
    # 5. PHRASE - turn rows into prose
    phrasing_prompt = _build_phrasing_prompt(question, rows, summary)
    phrasing_schema = {
        "type": "object",
        "properties": {
            "answer": {"type": "string"},
        },
    }
    
    try:
        phrasing_result = complete_json(phrasing_prompt, phrasing_schema)
        answer_text = phrasing_result.get("answer", "")
    except LLMError:
        # Fallback to summary if phrasing fails
        answer_text = summary
    
    # 6. Post-check: verify numbers in answer are in rows
    answer_numbers = _extract_numbers(answer_text)
    if not _numbers_in_rows(answer_numbers, rows):
        # Log warning but still return the answer
        import logging
        logger = logging.getLogger(__name__)
        logger.warning(
            "Answer contains numbers not in rows: question=%s, numbers=%s",
            question[:50],
            answer_numbers,
        )
    
    # 7. Log successful query
    duration_ms = int((datetime.now(timezone.utc) - start_time).total_seconds() * 1000)
    storage.log_query(
        path=config.db_path,
        question=question,
        tool_used=tool_name,
        params=params,
        answerable=True,
        reason=None,
        duration_ms=duration_ms,
    )
    
    return Answer(
        text=answer_text,
        rows=rows,
        tool_used=tool_name,
        params=params,
        summary=summary,
    )
