"""
Report Agent service
Implements ReACT-style simulation report generation on top of LangChain + Zep

Features:
1. Generates a report from the simulation goal and the Zep graph information
2. Plans the table of contents first, then generates it section by section
3. Each section uses ReACT with several rounds of reasoning and reflection
4. Converses with the user and calls retrieval tools on its own during the conversation
"""

import os
import json
import time
import re
from typing import Dict, Any, List, Optional, Callable
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum

from ..config import Config
from ..utils.llm_client import LLMClient
from ..utils.logger import get_logger
from .zep_tools import (
    ZepToolsService, 
    SearchResult, 
    InsightForgeResult, 
    PanoramaResult,
    InterviewResult
)

logger = get_logger('mirofish.report_agent')


class ReportLogger:
    """
    Detailed logger for the Report Agent
    
    Writes an agent_log.jsonl file into the report folder, recording every step in detail.
    Each line is a complete JSON object with a timestamp, action type, details and so on.
    """
    
    def __init__(self, report_id: str):
        """
        Initialize the logger
        
        Args:
            report_id: the report ID, used to work out the log file path
        """
        self.report_id = report_id
        self.log_file_path = os.path.join(
            Config.UPLOAD_FOLDER, 'reports', report_id, 'agent_log.jsonl'
        )
        self.start_time = datetime.now()
        self._ensure_log_file()
    
    def _ensure_log_file(self):
        """Make sure the directory containing the log file exists"""
        log_dir = os.path.dirname(self.log_file_path)
        os.makedirs(log_dir, exist_ok=True)
    
    def _get_elapsed_time(self) -> float:
        """Elapsed time in seconds since the start"""
        return (datetime.now() - self.start_time).total_seconds()
    
    def log(
        self, 
        action: str, 
        stage: str,
        details: Dict[str, Any],
        section_title: str = None,
        section_index: int = None
    ):
        """
        Record one log entry
        
        Args:
            action: the action type, e.g. 'start', 'tool_call', 'llm_response', 'section_complete'
            stage: the current stage, e.g. 'planning', 'generating', 'completed'
            details: a dictionary of details, untruncated
            section_title: the current section title (optional)
            section_index: the current section index (optional)
        """
        log_entry = {
            "timestamp": datetime.now().isoformat(),
            "elapsed_seconds": round(self._get_elapsed_time(), 2),
            "report_id": self.report_id,
            "action": action,
            "stage": stage,
            "section_title": section_title,
            "section_index": section_index,
            "details": details
        }
        
        # Append to the JSONL file
        with open(self.log_file_path, 'a', encoding='utf-8') as f:
            f.write(json.dumps(log_entry, ensure_ascii=False) + '\n')
    
    def log_start(self, simulation_id: str, graph_id: str, simulation_requirement: str):
        """Record the start of report generation"""
        self.log(
            action="report_start",
            stage="pending",
            details={
                "simulation_id": simulation_id,
                "graph_id": graph_id,
                "simulation_requirement": simulation_requirement,
                "message": "Report generation task started"
            }
        )
    
    def log_planning_start(self):
        """Record the start of outline planning"""
        self.log(
            action="planning_start",
            stage="planning",
            details={"message": "Planning the report outline"}
        )
    
    def log_planning_context(self, context: Dict[str, Any]):
        """Record the context information fetched while planning"""
        self.log(
            action="planning_context",
            stage="planning",
            details={
                "message": "Fetching the simulation context",
                "context": context
            }
        )
    
    def log_planning_complete(self, outline_dict: Dict[str, Any]):
        """Record that outline planning finished"""
        self.log(
            action="planning_complete",
            stage="planning",
            details={
                "message": "Outline planning complete",
                "outline": outline_dict
            }
        )
    
    def log_section_start(self, section_title: str, section_index: int):
        """Record the start of a section"""
        self.log(
            action="section_start",
            stage="generating",
            section_title=section_title,
            section_index=section_index,
            details={"message": f"Generating section: {section_title}"}
        )
    
    def log_react_thought(self, section_title: str, section_index: int, iteration: int, thought: str):
        """Record a ReACT reasoning step"""
        self.log(
            action="react_thought",
            stage="generating",
            section_title=section_title,
            section_index=section_index,
            details={
                "iteration": iteration,
                "thought": thought,
                "message": f"ReACT iteration {iteration}"
            }
        )
    
    def log_tool_call(
        self, 
        section_title: str, 
        section_index: int,
        tool_name: str, 
        parameters: Dict[str, Any],
        iteration: int
    ):
        """Record a tool call"""
        self.log(
            action="tool_call",
            stage="generating",
            section_title=section_title,
            section_index=section_index,
            details={
                "iteration": iteration,
                "tool_name": tool_name,
                "parameters": parameters,
                "message": f"Calling tool: {tool_name}"
            }
        )
    
    def log_tool_result(
        self,
        section_title: str,
        section_index: int,
        tool_name: str,
        result: str,
        iteration: int
    ):
        """Record the result of a tool call (full content, untruncated)"""
        self.log(
            action="tool_result",
            stage="generating",
            section_title=section_title,
            section_index=section_index,
            details={
                "iteration": iteration,
                "tool_name": tool_name,
                "result": result,  # the full result, untruncated
                "result_length": len(result),
                "message": f"Tool {tool_name} returned a result"
            }
        )
    
    def log_llm_response(
        self,
        section_title: str,
        section_index: int,
        response: str,
        iteration: int,
        has_tool_calls: bool,
        has_final_answer: bool
    ):
        """Record an LLM response (full content, untruncated)"""
        self.log(
            action="llm_response",
            stage="generating",
            section_title=section_title,
            section_index=section_index,
            details={
                "iteration": iteration,
                "response": response,  # the full response, untruncated
                "response_length": len(response),
                "has_tool_calls": has_tool_calls,
                "has_final_answer": has_final_answer,
                "message": f"LLM response (tool call: {has_tool_calls}, final answer: {has_final_answer})"
            }
        )
    
    def log_section_content(
        self,
        section_title: str,
        section_index: int,
        content: str,
        tool_calls_count: int
    ):
        """Record that a section's content was generated (the content only, not the whole section)"""
        self.log(
            action="section_content",
            stage="generating",
            section_title=section_title,
            section_index=section_index,
            details={
                "content": content,  # the full content, untruncated
                "content_length": len(content),
                "tool_calls_count": tool_calls_count,
                "message": f"Section {section_title} content generated"
            }
        )
    
    def log_section_full_complete(
        self,
        section_title: str,
        section_index: int,
        full_content: str
    ):
        """
        Record that a section finished generating

        The frontend listens for this entry to decide whether a section is really complete, and to read its full content
        """
        self.log(
            action="section_complete",
            stage="generating",
            section_title=section_title,
            section_index=section_index,
            details={
                "content": full_content,
                "content_length": len(full_content),
                "message": f"Section {section_title} generated"
            }
        )
    
    def log_report_complete(self, total_sections: int, total_time_seconds: float):
        """Record that report generation finished"""
        self.log(
            action="report_complete",
            stage="completed",
            details={
                "total_sections": total_sections,
                "total_time_seconds": round(total_time_seconds, 2),
                "message": "Report generation complete"
            }
        )
    
    def log_error(self, error_message: str, stage: str, section_title: str = None):
        """Record an error"""
        self.log(
            action="error",
            stage=stage,
            section_title=section_title,
            section_index=None,
            details={
                "error": error_message,
                "message": f"An error occurred: {error_message}"
            }
        )


class ReportConsoleLogger:
    """
    Console log writer for the Report Agent
    
    Writes console-style log lines (INFO, WARNING, ...) to console_log.txt in the report folder.
    Unlike agent_log.jsonl these are plain-text console output.
    """
    
    def __init__(self, report_id: str):
        """
        Initialize the console log writer
        
        Args:
            report_id: the report ID, used to work out the log file path
        """
        self.report_id = report_id
        self.log_file_path = os.path.join(
            Config.UPLOAD_FOLDER, 'reports', report_id, 'console_log.txt'
        )
        self._ensure_log_file()
        self._file_handler = None
        self._setup_file_handler()
    
    def _ensure_log_file(self):
        """Make sure the directory containing the log file exists"""
        log_dir = os.path.dirname(self.log_file_path)
        os.makedirs(log_dir, exist_ok=True)
    
    def _setup_file_handler(self):
        """Attach a file handler so the logs are written to the file as well"""
        import logging
        
        # Create the file handler
        self._file_handler = logging.FileHandler(
            self.log_file_path,
            mode='a',
            encoding='utf-8'
        )
        self._file_handler.setLevel(logging.INFO)
        
        # Use the same compact format as the console
        formatter = logging.Formatter(
            '[%(asctime)s] %(levelname)s: %(message)s',
            datefmt='%H:%M:%S'
        )
        self._file_handler.setFormatter(formatter)
        
        # Attach it to the report_agent loggers
        loggers_to_attach = [
            'mirofish.report_agent',
            'mirofish.zep_tools',
        ]
        
        for logger_name in loggers_to_attach:
            target_logger = logging.getLogger(logger_name)
            # Avoid adding it twice
            if self._file_handler not in target_logger.handlers:
                target_logger.addHandler(self._file_handler)
    
    def close(self):
        """Close the file handler and remove it from the logger"""
        import logging
        
        if self._file_handler:
            loggers_to_detach = [
                'mirofish.report_agent',
                'mirofish.zep_tools',
            ]
            
            for logger_name in loggers_to_detach:
                target_logger = logging.getLogger(logger_name)
                if self._file_handler in target_logger.handlers:
                    target_logger.removeHandler(self._file_handler)
            
            self._file_handler.close()
            self._file_handler = None
    
    def __del__(self):
        """Make sure the file handler is closed when the object is collected"""
        self.close()


class ReportStatus(str, Enum):
    """Report status"""
    PENDING = "pending"
    PLANNING = "planning"
    GENERATING = "generating"
    COMPLETED = "completed"
    FAILED = "failed"


@dataclass
class ReportSection:
    """Report section"""
    title: str
    content: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return {
            "title": self.title,
            "content": self.content
        }

    def to_markdown(self, level: int = 2) -> str:
        """Convert to Markdown"""
        md = f"{'#' * level} {self.title}\n\n"
        if self.content:
            md += f"{self.content}\n\n"
        return md


@dataclass
class ReportOutline:
    """Report outline"""
    title: str
    summary: str
    sections: List[ReportSection]
    
    def to_dict(self) -> Dict[str, Any]:
        return {
            "title": self.title,
            "summary": self.summary,
            "sections": [s.to_dict() for s in self.sections]
        }
    
    def to_markdown(self) -> str:
        """Convert to Markdown"""
        md = f"# {self.title}\n\n"
        md += f"> {self.summary}\n\n"
        for section in self.sections:
            md += section.to_markdown()
        return md


@dataclass
class Report:
    """Full report"""
    report_id: str
    simulation_id: str
    graph_id: str
    simulation_requirement: str
    status: ReportStatus
    outline: Optional[ReportOutline] = None
    markdown_content: str = ""
    created_at: str = ""
    completed_at: str = ""
    error: Optional[str] = None
    
    def to_dict(self) -> Dict[str, Any]:
        return {
            "report_id": self.report_id,
            "simulation_id": self.simulation_id,
            "graph_id": self.graph_id,
            "simulation_requirement": self.simulation_requirement,
            "status": self.status.value,
            "outline": self.outline.to_dict() if self.outline else None,
            "markdown_content": self.markdown_content,
            "created_at": self.created_at,
            "completed_at": self.completed_at,
            "error": self.error
        }


# ═══════════════════════════════════════════════════════════════
# Prompt template constants
# ═══════════════════════════════════════════════════════════════

# ── Tool descriptions ──

TOOL_DESC_INSIGHT_FORGE = """\
[Deep insight retrieval - the powerful retrieval tool]
This is our powerful retrieval function, designed for deep analysis. It:
1. Splits your question into several sub-questions automatically
2. Retrieves information from the simulated graph along several dimensions
3. Combines the results of semantic search, entity analysis and relationship-chain tracing
4. Returns the broadest and deepest retrieval content

[When to use it]
- When you need to analyse a topic in depth
- When you need several sides of an event
- When you need rich material to support a report section

[What it returns]
- The source text of the related facts (quotable as-is)
- Insights into the core entities
- An analysis of the relationship chains"""

TOOL_DESC_PANORAMA_SEARCH = """\
[Broad search - the whole picture]
Use this tool to get the complete picture of the simulation output; it is especially good for understanding how an event evolved. It:
1. Fetches every related node and relation
2. Separates the currently valid facts from the historical/expired ones
3. Helps you understand how public opinion developed

[When to use it]
- When you need the full arc of an event
- When you need to compare public opinion across stages
- When you need comprehensive entity and relation information

[What it returns]
- Currently valid facts (the newest simulation output)
- Historical/expired facts (the record of how things changed)
- Every entity involved"""

TOOL_DESC_QUICK_SEARCH = """\
[Simple search - fast retrieval]
A lightweight, fast retrieval tool for simple, direct lookups.

[When to use it]
- When you need to find one specific detail quickly
- When you need to verify a single fact
- For simple information lookups

[What it returns]
- The list of facts most relevant to the query"""

TOOL_DESC_INTERVIEW_AGENTS = """\
[Deep interview - real Agent interviews (both platforms)]
Calls the interview API of the OASIS simulation environment to interview the Agents that are
actually running! These are not LLM role-plays: the real interview endpoint returns the
simulated Agents' original answers. By default the interview runs on both Twitter and Reddit
at once, for a broader spread of opinions.

How it works:
1. Reads the persona files automatically to learn about every simulated Agent
2. Picks the Agents most relevant to the interview topic (students, media, officials, ...)
3. Generates the interview questions automatically
4. Calls /api/simulation/interview/batch to run the real interview on both platforms
5. Combines every interview result into a multi-perspective analysis

[When to use it]
- When you need the event seen from different roles (what do students think? the media? officials?)
- When you need opinions and positions from several sides
- When you need the real answers of the simulated Agents (from the OASIS simulation environment)
- When you want a livelier report with an "interview transcript"

[What it returns]
- The identity of each interviewed Agent
- Each Agent's answers on both Twitter and Reddit
- Key quotes (quotable as-is)
- An interview summary and a comparison of viewpoints

[Important] The OASIS simulation environment must be running for this tool to work!"""

# ── Outline planning prompt ──

PLAN_SYSTEM_PROMPT = """\
You are an expert writer of "future prediction reports", with a god's-eye view of the simulated world: you can see every Agent's behavior, words and interactions in the simulation.

[Core idea]
We built a simulated world and injected a specific "simulation goal" into it as a variable. How the simulated world evolves IS the prediction of what may happen in the future. What you are observing is not "experimental data" but "a rehearsal of the future".

[Your task]
Write a "future prediction report" that answers:
1. Under the conditions we set, what happened in the future?
2. How did the various Agents (groups of people) react and act?
3. Which future trends and risks does this simulation reveal?

[Positioning of the report]
- YES: this is a future prediction report built on a simulation, revealing "if this happens, what does the future look like"
- YES: focus on the prediction: where the event goes, how groups react, emergent phenomena, potential risks
- YES: what the Agents say and do is the prediction of how people will behave
- NO: it is not an analysis of the current state of the real world
- NO: it is not a vague overview of public opinion

[Section count]
- At least 2 sections, at most 5
- No sub-sections: write each section's full content directly
- Keep it concise and focused on the core predictions
- Design the section structure yourself, based on the prediction

[Language]
Write everything in English, including the title, the summary and the section titles and descriptions.

Output the report outline as JSON, in this format:
{
    "title": "report title",
    "summary": "report summary (the core prediction in one sentence)",
    "sections": [
        {
            "title": "section title",
            "description": "what the section covers"
        }
    ]
}

Note: the sections array must hold at least 2 and at most 5 elements!"""

PLAN_USER_PROMPT_TEMPLATE = """\
[The prediction scenario]
The variable we injected into the simulated world (the simulation goal): {simulation_requirement}

[Scale of the simulated world]
- Entities taking part in the simulation: {total_nodes}
- Relations between entities: {total_edges}
- Entity type distribution: {entity_types}
- Active Agents: {total_entities}

[A sample of the future facts the simulation predicted]
{related_facts_json}

Look at this rehearsal of the future from a god's-eye view:
1. Under the conditions we set, what state did the future reach?
2. How did the various groups (Agents) react and act?
3. Which future trends does this simulation reveal?

Design the most suitable section structure for the report, based on the prediction.

[Reminder] Section count: at least 2, at most 5, concise and focused on the core predictions. Write everything in English."""

# ── Section generation prompt ──

SECTION_SYSTEM_PROMPT_TEMPLATE = """\
You are an expert writer of "future prediction reports" and you are writing one section of the report.

Report title: {report_title}
Report summary: {report_summary}
Prediction scenario (simulation goal): {simulation_requirement}

Section to write now: {section_title}

═══════════════════════════════════════════════════════════════
[Core idea]
═══════════════════════════════════════════════════════════════

The simulated world is a rehearsal of the future. We injected specific conditions (the simulation goal) into it,
and the Agents' behavior and interactions predict how people will behave.

Your task:
- Reveal what happened in the future under the conditions set
- Predict how the various groups (Agents) reacted and acted
- Identify the future trends, risks and opportunities worth watching

NO: do not write an analysis of the current state of the real world
YES: focus on "what the future looks like" - the simulation output IS the predicted future

═══════════════════════════════════════════════════════════════
[The most important rules - they must be followed]
═══════════════════════════════════════════════════════════════

1. [You must call tools to observe the simulated world]
   - You are watching a rehearsal of the future from a god's-eye view
   - Everything must come from events and Agent behavior in the simulated world
   - Do not use your own knowledge to write the report
   - Call tools at least 3 times per section (at most 5) to observe the simulated world, which represents the future

2. [You must quote the Agents' own words and actions]
   - What Agents say and do predicts how people will behave
   - Show those predictions in the report as quotations, for example:
     > "A group like this would say: the original text..."
   - These quotes are the core evidence of the simulation's prediction

3. [Language: the whole report is written in English]
   - The tool output may be in English or in another language
   - Write the entire report in English, including every quotation block
   - When you quote tool output that is not in English, translate it into fluent English before putting it in the report
   - Keep the meaning intact when translating, and make the wording read naturally
   - This rule applies to both the body text and the quotation blocks (> format)

4. [Present the prediction faithfully]
   - The report must reflect the simulation output, which represents the future
   - Do not add information that the simulation does not contain
   - If some aspect is under-represented, say so plainly

═══════════════════════════════════════════════════════════════
[Formatting rules - extremely important!]
═══════════════════════════════════════════════════════════════

[One section = the smallest unit of content]
- Each section is the smallest block of the report
- NO: no Markdown headings inside a section (#, ##, ###, ####, ...)
- NO: do not start the content with the section title
- YES: the system adds the section title; you write only the body
- YES: use **bold**, paragraphs, quotes and lists to organise the content, but no headings

[Correct example]
```
This section analyses how the event spread through public opinion. Digging into the simulation data, we found...

**The ignition stage**

Weibo was the front line of public opinion and carried the initial spread of information:

> "Weibo accounted for 68% of the first wave of posts..."

**The amplification stage**

Douyin amplified the event further:

- strong visual impact
- high emotional resonance
```

[Wrong example]
```
## Executive summary      <- wrong! do not add any heading
### 1. The ignition stage <- wrong! do not split with ###
#### 1.1 Detailed analysis <- wrong! do not subdivide with ####

This section analyses...
```

═══════════════════════════════════════════════════════════════
[Available retrieval tools] (call 3-5 of them per section)
═══════════════════════════════════════════════════════════════

{tools_description}

[Tool advice - mix different tools, do not rely on just one]
- insight_forge: deep insight analysis; splits the question automatically and retrieves facts and relations along several dimensions
- panorama_search: wide panoramic search; the whole event, its timeline and how it evolved
- quick_search: quickly verify one specific detail
- interview_agents: interview simulated Agents for first-person views from different roles

═══════════════════════════════════════════════════════════════
[Workflow]
═══════════════════════════════════════════════════════════════

In each reply you may do exactly one of the following two things (never both):

Option A - call a tool:
Write your reasoning, then call a single tool in this format:
<tool_call>
{{"name": "tool name", "parameters": {{"parameter name": "parameter value"}}}}
</tool_call>
The system runs the tool and returns the result to you. You neither need nor may write the tool result yourself.

Option B - output the final content:
Once the tools have given you enough information, start with "Final Answer:" and output the section content.

Strictly forbidden:
- Never put a tool call and Final Answer in the same reply
- Never invent tool results (Observation); every tool result is injected by the system
- At most one tool call per reply

═══════════════════════════════════════════════════════════════
[Section content requirements]
═══════════════════════════════════════════════════════════════

1. The content must be based on simulation data retrieved by the tools
2. Quote the source text generously to show the simulation at work
3. Use Markdown (but no headings):
   - use **bold text** for emphasis (instead of sub-headings)
   - use lists (- or 1. 2. 3.) to organise points
   - separate paragraphs with blank lines
   - NO: no heading syntax at all (#, ##, ###, ####)
4. [Quotation rules - each quote on its own paragraph]
   A quote must be its own paragraph, with a blank line before and after; never mix it into a paragraph:

   Correct:
   ```
   The school's response was seen as lacking substance.

   > "The school's handling looked rigid and slow in a fast-moving social media environment."

   That assessment reflected the general dissatisfaction.
   ```

   Wrong:
   ```
   The school's response was seen as lacking substance.> "The school's handling..." That assessment reflected...
   ```
5. Stay logically consistent with the other sections
6. [Avoid repetition] Read the finished sections below carefully and do not describe the same information twice
7. [Once more] Do not add any headings! Use **bold** instead of sub-headings"""

SECTION_USER_PROMPT_TEMPLATE = """\
Finished sections (read them carefully and do not repeat them):
{previous_content}

═══════════════════════════════════════════════════════════════
[Current task] Write the section: {section_title}
═══════════════════════════════════════════════════════════════

[Important reminders]
1. Read the finished sections above carefully and do not repeat the same content!
2. You must call a tool to fetch simulation data before you start
3. Mix different tools; do not rely on just one
4. The report content must come from the retrieval results, not from your own knowledge

[Format warnings - must be followed]
- NO: do not write any heading (#, ##, ###, #### are all forbidden)
- NO: do not open with "{section_title}"
- YES: the system adds the section title
- YES: write the body directly and use **bold** instead of sub-headings

Begin:
1. First think (Thought) about what information this section needs
2. Then call a tool (Action) to fetch simulation data
3. Once you have enough, output the Final Answer (plain body text, with no headings)
"""

# ── Messages used inside the ReACT loop ──

REACT_OBSERVATION_TEMPLATE = """\
Observation (retrieval result):

═══ Tool {tool_name} returned ═══
{result}

═══════════════════════════════════════════════════════════════
Tool calls used {tool_calls_count}/{max_tool_calls} (already used: {used_tools_str}){unused_hint}
- If you have enough: start with "Final Answer:" and write the section (you must quote the source text above)
- If you need more: call one tool to keep retrieving
═══════════════════════════════════════════════════════════════
"""

REACT_INSUFFICIENT_TOOLS_MSG = (
    "[Note] You have only called {tool_calls_count} tools; at least {min_tool_calls} are required. "
    "Please call more tools for more simulation data, then output the Final Answer.{unused_hint}"
)

REACT_INSUFFICIENT_TOOLS_MSG_ALT = (
    "You have only called {tool_calls_count} tools; at least {min_tool_calls} are required. "
    "Please call a tool to fetch simulation data.{unused_hint}"
)

REACT_TOOL_LIMIT_MSG = (
    "You have reached the tool call limit ({tool_calls_count}/{max_tool_calls}) and cannot call any more tools. "
    'Using the information you already have, start with "Final Answer:" and write the section right away.'
)

REACT_UNUSED_TOOLS_HINT = "\n💡 You have not used these yet: {unused_list}. Try other tools for additional angles"

REACT_FORCE_FINAL_MSG = "The tool call limit is reached. Output Final Answer: and write the section content directly."

# ── Chat prompt ──

CHAT_SYSTEM_PROMPT_TEMPLATE = """\
You are a concise, efficient simulation prediction assistant.

[Background]
Prediction conditions: {simulation_requirement}

[The analysis report generated so far]
{report_content}

[Rules]
1. Answer from the report content above wherever possible
2. Answer directly and skip long-winded reasoning
3. Only call tools to fetch more data when the report does not answer the question
4. Answer concisely, clearly and in a well-structured way
5. Always answer in English

[Available tools] (only when needed; call 1-2 at most)
{tools_description}

[Tool call format]
<tool_call>
{{"name": "tool name", "parameters": {{"parameter name": "parameter value"}}}}
</tool_call>

[Answer style]
- Concise and direct, no long essays
- Quote key content with the > format
- Give the conclusion first, then explain why
"""

CHAT_OBSERVATION_SUFFIX = "\n\nPlease answer the question concisely, in English."


# ═══════════════════════════════════════════════════════════════
# The main ReportAgent class
# ═══════════════════════════════════════════════════════════════


class ReportAgent:
    """
    Report Agent - the simulation report generation Agent

    Uses the ReACT (Reasoning + Acting) pattern:
    1. Planning: analyses the simulation goal and plans the report's table of contents
    2. Generation: writes the sections one by one, calling tools as often as needed per section
    3. Reflection: checks the content for completeness and accuracy
    """
    
    # Maximum tool calls per section
    MAX_TOOL_CALLS_PER_SECTION = 5
    
    # Maximum reflection rounds
    MAX_REFLECTION_ROUNDS = 3
    
    # Maximum tool calls in a conversation
    MAX_TOOL_CALLS_PER_CHAT = 2
    
    def __init__(
        self, 
        graph_id: str,
        simulation_id: str,
        simulation_requirement: str,
        llm_client: Optional[LLMClient] = None,
        zep_tools: Optional[ZepToolsService] = None
    ):
        """
        Initialize the Report Agent
        
        Args:
            graph_id: the graph ID
            simulation_id: the simulation ID
            simulation_requirement: a description of the simulation goal
            llm_client: LLM client (optional)
            zep_tools: Zep tools service (optional)
        """
        self.graph_id = graph_id
        self.simulation_id = simulation_id
        self.simulation_requirement = simulation_requirement
        
        self.llm = llm_client or LLMClient()
        self.zep_tools = zep_tools or ZepToolsService()
        
        # Tool definitions
        self.tools = self._define_tools()
        
        # Logger (initialized in generate_report)
        self.report_logger: Optional[ReportLogger] = None
        # Console log writer (initialized in generate_report)
        self.console_logger: Optional[ReportConsoleLogger] = None
        
        logger.info(f"ReportAgent initialized: graph_id={graph_id}, simulation_id={simulation_id}")
    
    def _define_tools(self) -> Dict[str, Dict[str, Any]]:
        """Define the available tools"""
        return {
            "insight_forge": {
                "name": "insight_forge",
                "description": TOOL_DESC_INSIGHT_FORGE,
                "parameters": {
                    "query": "the question or topic you want to analyse in depth",
                    "report_context": "context for the current report section (optional; helps generate sharper sub-questions)"
                }
            },
            "panorama_search": {
                "name": "panorama_search",
                "description": TOOL_DESC_PANORAMA_SEARCH,
                "parameters": {
                    "query": "the search query, used for relevance ranking",
                    "include_expired": "whether to include expired/historical content (default True)"
                }
            },
            "quick_search": {
                "name": "quick_search",
                "description": TOOL_DESC_QUICK_SEARCH,
                "parameters": {
                    "query": "the search query string",
                    "limit": "how many results to return (optional, default 10)"
                }
            },
            "interview_agents": {
                "name": "interview_agents",
                "description": TOOL_DESC_INTERVIEW_AGENTS,
                "parameters": {
                    "interview_topic": "the interview topic or request (e.g. 'what do students think about the dorm formaldehyde incident')",
                    "max_agents": "the maximum number of Agents to interview (optional, default 5, max 10)"
                }
            }
        }
    
    def _execute_tool(self, tool_name: str, parameters: Dict[str, Any], report_context: str = "") -> str:
        """
        Execute a tool call
        
        Args:
            tool_name: the tool name
            parameters: the tool parameters
            report_context: the report context (used by InsightForge)
            
        Returns:
            the tool result (as text)
        """
        logger.info(f"Executing tool: {tool_name}, parameters: {parameters}")
        
        try:
            if tool_name == "insight_forge":
                query = parameters.get("query", "")
                ctx = parameters.get("report_context", "") or report_context
                result = self.zep_tools.insight_forge(
                    graph_id=self.graph_id,
                    query=query,
                    simulation_requirement=self.simulation_requirement,
                    report_context=ctx
                )
                return result.to_text()
            
            elif tool_name == "panorama_search":
                # Broad search - the whole picture
                query = parameters.get("query", "")
                include_expired = parameters.get("include_expired", True)
                if isinstance(include_expired, str):
                    include_expired = include_expired.lower() in ['true', '1', 'yes']
                result = self.zep_tools.panorama_search(
                    graph_id=self.graph_id,
                    query=query,
                    include_expired=include_expired
                )
                return result.to_text()
            
            elif tool_name == "quick_search":
                # Simple search - fast retrieval
                query = parameters.get("query", "")
                limit = parameters.get("limit", 10)
                if isinstance(limit, str):
                    limit = int(limit)
                result = self.zep_tools.quick_search(
                    graph_id=self.graph_id,
                    query=query,
                    limit=limit
                )
                return result.to_text()
            
            elif tool_name == "interview_agents":
                # Deep interview - calls the real OASIS interview API to get the simulated Agents' answers (both platforms)
                interview_topic = parameters.get("interview_topic", parameters.get("query", ""))
                max_agents = parameters.get("max_agents", 5)
                if isinstance(max_agents, str):
                    max_agents = int(max_agents)
                max_agents = min(max_agents, 10)
                result = self.zep_tools.interview_agents(
                    simulation_id=self.simulation_id,
                    interview_requirement=interview_topic,
                    simulation_requirement=self.simulation_requirement,
                    max_agents=max_agents
                )
                return result.to_text()
            
            # ========== Legacy tools kept for compatibility (redirected internally to the new ones) ==========
            
            elif tool_name == "search_graph":
                # Redirect to quick_search
                logger.info("search_graph redirected to quick_search")
                return self._execute_tool("quick_search", parameters, report_context)
            
            elif tool_name == "get_graph_statistics":
                result = self.zep_tools.get_graph_statistics(self.graph_id)
                return json.dumps(result, ensure_ascii=False, indent=2)
            
            elif tool_name == "get_entity_summary":
                entity_name = parameters.get("entity_name", "")
                result = self.zep_tools.get_entity_summary(
                    graph_id=self.graph_id,
                    entity_name=entity_name
                )
                return json.dumps(result, ensure_ascii=False, indent=2)
            
            elif tool_name == "get_simulation_context":
                # Redirect to insight_forge, which is more powerful
                logger.info("get_simulation_context redirected to insight_forge")
                query = parameters.get("query", self.simulation_requirement)
                return self._execute_tool("insight_forge", {"query": query}, report_context)
            
            elif tool_name == "get_entities_by_type":
                entity_type = parameters.get("entity_type", "")
                nodes = self.zep_tools.get_entities_by_type(
                    graph_id=self.graph_id,
                    entity_type=entity_type
                )
                result = [n.to_dict() for n in nodes]
                return json.dumps(result, ensure_ascii=False, indent=2)
            
            else:
                return f"Unknown tool: {tool_name}. Use one of: insight_forge, panorama_search, quick_search"
                
        except Exception as e:
            logger.error(f"Tool execution failed: {tool_name}, error: {str(e)}")
            return f"Tool execution failed: {str(e)}"
    
    # The set of valid tool names, used to validate the bare-JSON fallback parse
    VALID_TOOL_NAMES = {"insight_forge", "panorama_search", "quick_search", "interview_agents"}

    def _parse_tool_calls(self, response: str) -> List[Dict[str, Any]]:
        """
        Parse a tool call out of an LLM response

        Supported formats (in priority order):
        1. <tool_call>{"name": "tool_name", "parameters": {...}}</tool_call>
        2. Bare JSON (the whole response, or a single line, is a tool call JSON object)
        """
        tool_calls = []

        # Format 1: XML style (the standard format)
        xml_pattern = r'<tool_call>\s*(\{.*?\})\s*</tool_call>'
        for match in re.finditer(xml_pattern, response, re.DOTALL):
            try:
                call_data = json.loads(match.group(1))
                tool_calls.append(call_data)
            except json.JSONDecodeError:
                pass

        if tool_calls:
            return tool_calls

        # Format 2: fallback - the LLM emitted bare JSON without the <tool_call> tag
        # Only tried when format 1 did not match, so JSON in the body text is not picked up by mistake
        stripped = response.strip()
        if stripped.startswith('{') and stripped.endswith('}'):
            try:
                call_data = json.loads(stripped)
                if self._is_valid_tool_call(call_data):
                    tool_calls.append(call_data)
                    return tool_calls
            except json.JSONDecodeError:
                pass

        # The response may hold reasoning text plus bare JSON; take the last JSON object
        json_pattern = r'(\{"(?:name|tool)"\s*:.*?\})\s*$'
        match = re.search(json_pattern, stripped, re.DOTALL)
        if match:
            try:
                call_data = json.loads(match.group(1))
                if self._is_valid_tool_call(call_data):
                    tool_calls.append(call_data)
            except json.JSONDecodeError:
                pass

        return tool_calls

    def _is_valid_tool_call(self, data: dict) -> bool:
        """Validate that the parsed JSON really is a tool call"""
        # Both {"name": ..., "parameters": ...} and {"tool": ..., "params": ...} are accepted
        tool_name = data.get("name") or data.get("tool")
        if tool_name and tool_name in self.VALID_TOOL_NAMES:
            # Normalize the keys to name / parameters
            if "tool" in data:
                data["name"] = data.pop("tool")
            if "params" in data and "parameters" not in data:
                data["parameters"] = data.pop("params")
            return True
        return False
    
    def _get_tools_description(self) -> str:
        """Build the tool description text"""
        desc_parts = ["Available tools:"]
        for name, tool in self.tools.items():
            params_desc = ", ".join([f"{k}: {v}" for k, v in tool["parameters"].items()])
            desc_parts.append(f"- {name}: {tool['description']}")
            if params_desc:
                desc_parts.append(f"  parameters: {params_desc}")
        return "\n".join(desc_parts)
    
    def plan_outline(
        self, 
        progress_callback: Optional[Callable] = None
    ) -> ReportOutline:
        """
        Plan the report outline
        
        Uses the LLM to analyse the simulation goal and plan the report's table of contents
        
        Args:
            progress_callback: a progress callback
            
        Returns:
            ReportOutline: the report outline
        """
        logger.info("Planning the report outline...")
        
        if progress_callback:
            progress_callback("planning", 0, "Analysing the simulation goal...")
        
        # Fetch the simulation context first
        context = self.zep_tools.get_simulation_context(
            graph_id=self.graph_id,
            simulation_requirement=self.simulation_requirement
        )
        
        if progress_callback:
            progress_callback("planning", 30, "Generating the report outline...")
        
        system_prompt = PLAN_SYSTEM_PROMPT
        user_prompt = PLAN_USER_PROMPT_TEMPLATE.format(
            simulation_requirement=self.simulation_requirement,
            total_nodes=context.get('graph_statistics', {}).get('total_nodes', 0),
            total_edges=context.get('graph_statistics', {}).get('total_edges', 0),
            entity_types=list(context.get('graph_statistics', {}).get('entity_types', {}).keys()),
            total_entities=context.get('total_entities', 0),
            related_facts_json=json.dumps(context.get('related_facts', [])[:10], ensure_ascii=False, indent=2),
        )

        try:
            response = self.llm.chat_json(
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_prompt}
                ],
                temperature=0.3
            )
            
            if progress_callback:
                progress_callback("planning", 80, "Parsing the outline structure...")
            
            # Parse the outline
            sections = []
            for section_data in response.get("sections", []):
                sections.append(ReportSection(
                    title=section_data.get("title", ""),
                    content=""
                ))
            
            outline = ReportOutline(
                title=response.get("title", "Simulation analysis report"),
                summary=response.get("summary", ""),
                sections=sections
            )
            
            if progress_callback:
                progress_callback("planning", 100, "Outline planning complete")
            
            logger.info(f"Outline planning complete: {len(sections)} sections")
            return outline
            
        except Exception as e:
            logger.error(f"Outline planning failed: {str(e)}")
            # Return a default outline (3 sections) as the fallback
            return ReportOutline(
                title="Future prediction report",
                summary="Future trends and risks predicted by the simulation",
                sections=[
                    ReportSection(title="Prediction scenario and key findings"),
                    ReportSection(title="Predicted group behavior"),
                    ReportSection(title="Trends and risks ahead")
                ]
            )
    
    def _generate_section_react(
        self, 
        section: ReportSection,
        outline: ReportOutline,
        previous_sections: List[str],
        progress_callback: Optional[Callable] = None,
        section_index: int = 0
    ) -> str:
        """
        Generate a single section using the ReACT pattern
        
        The ReACT loop:
        1. Thought - what information do I need
        2. Action - call a tool to fetch it
        3. Observation - analyse what the tool returned
        4. Repeat until there is enough information or the maximum is reached
        5. Final Answer - write the section content
        
        Args:
            section: the section to generate
            outline: the full outline
            previous_sections: the content of the earlier sections (for consistency)
            progress_callback: the progress callback
            section_index: the section index (for logging)
            
        Returns:
            the section content (Markdown)
        """
        logger.info(f"ReACT generating section: {section.title}")
        
        # Log the start of the section
        if self.report_logger:
            self.report_logger.log_section_start(section.title, section_index)
        
        system_prompt = SECTION_SYSTEM_PROMPT_TEMPLATE.format(
            report_title=outline.title,
            report_summary=outline.summary,
            simulation_requirement=self.simulation_requirement,
            section_title=section.title,
            tools_description=self._get_tools_description(),
        )

        # Build the user prompt - each finished section contributes up to 4000 characters
        if previous_sections:
            previous_parts = []
            for sec in previous_sections:
                # Up to 4000 characters per section
                truncated = sec[:4000] + "..." if len(sec) > 4000 else sec
                previous_parts.append(truncated)
            previous_content = "\n\n---\n\n".join(previous_parts)
        else:
            previous_content = "(this is the first section)"
        
        user_prompt = SECTION_USER_PROMPT_TEMPLATE.format(
            previous_content=previous_content,
            section_title=section.title,
        )

        messages = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt}
        ]
        
        # The ReACT loop
        tool_calls_count = 0
        max_iterations = 5  # maximum iterations
        min_tool_calls = 3  # minimum tool calls
        conflict_retries = 0  # consecutive conflicts where a tool call and Final Answer appear together
        used_tools = set()  # the tool names already called
        all_tools = {"insight_forge", "panorama_search", "quick_search", "interview_agents"}

        # Report context, used by InsightForge to generate sub-questions
        report_context = f"Section title: {section.title}\nSimulation goal: {self.simulation_requirement}"
        
        for iteration in range(max_iterations):
            if progress_callback:
                progress_callback(
                    "generating", 
                    int((iteration / max_iterations) * 100),
                    f"Deep retrieval and writing ({tool_calls_count}/{self.MAX_TOOL_CALLS_PER_SECTION})"
                )
            
            # Call the LLM
            response = self.llm.chat(
                messages=messages,
                temperature=0.5,
                max_tokens=4096
            )

            # Check whether the LLM returned None (API error or empty content)
            if response is None:
                logger.warning(f"Section {section.title}, iteration {iteration + 1}: the LLM returned None")
                # If there are iterations left, add a message and retry
                if iteration < max_iterations - 1:
                    messages.append({"role": "assistant", "content": "(empty response)"})
                    messages.append({"role": "user", "content": "Please continue writing."})
                    continue
                # The last iteration also returned None, so break out and force a wrap-up
                break

            logger.debug(f"LLM response: {response[:200]}...")

            # Parse once and reuse the result
            tool_calls = self._parse_tool_calls(response)
            has_tool_calls = bool(tool_calls)
            has_final_answer = "Final Answer:" in response

            # ── Conflict handling: the LLM produced a tool call and a Final Answer together ──
            if has_tool_calls and has_final_answer:
                conflict_retries += 1
                logger.warning(
                    f"Section {section.title}, round {iteration+1}: "
                    f"the LLM produced both a tool call and a Final Answer (conflict {conflict_retries})"
                )

                if conflict_retries <= 2:
                    # First two times: discard the response and ask the LLM to reply again
                    messages.append({"role": "assistant", "content": response})
                    messages.append({
                        "role": "user",
                        "content": (
                            "[Format error] You included both a tool call and a Final Answer in one reply, which is not allowed.\n"
                            "Each reply may do exactly one of these two things:\n"
                            "- call one tool (output a single <tool_call> block, no Final Answer)\n"
                            "- output the final content (start with 'Final Answer:', with no <tool_call>)\n"
                            "Please reply again with just one of them."
                        ),
                    })
                    continue
                else:
                    # Third time: degrade gracefully, truncate at the first tool call and force it through
                    logger.warning(
                        f"Section {section.title}: {conflict_retries} conflicts in a row, "
                        "falling back to executing only the first tool call"
                    )
                    first_tool_end = response.find('</tool_call>')
                    if first_tool_end != -1:
                        response = response[:first_tool_end + len('</tool_call>')]
                        tool_calls = self._parse_tool_calls(response)
                        has_tool_calls = bool(tool_calls)
                    has_final_answer = False
                    conflict_retries = 0

            # Log the LLM response
            if self.report_logger:
                self.report_logger.log_llm_response(
                    section_title=section.title,
                    section_index=section_index,
                    response=response,
                    iteration=iteration + 1,
                    has_tool_calls=has_tool_calls,
                    has_final_answer=has_final_answer
                )

            # ── Case 1: the LLM produced a Final Answer ──
            if has_final_answer:
                # Too few tool calls, so reject it and ask for more
                if tool_calls_count < min_tool_calls:
                    messages.append({"role": "assistant", "content": response})
                    unused_tools = all_tools - used_tools
                    unused_hint = f" (not used yet, worth a try: {', '.join(unused_tools)})" if unused_tools else ""
                    messages.append({
                        "role": "user",
                        "content": REACT_INSUFFICIENT_TOOLS_MSG.format(
                            tool_calls_count=tool_calls_count,
                            min_tool_calls=min_tool_calls,
                            unused_hint=unused_hint,
                        ),
                    })
                    continue

                # Finished normally
                final_answer = response.split("Final Answer:")[-1].strip()
                logger.info(f"Section {section.title} generated (tool calls: {tool_calls_count})")

                if self.report_logger:
                    self.report_logger.log_section_content(
                        section_title=section.title,
                        section_index=section_index,
                        content=final_answer,
                        tool_calls_count=tool_calls_count
                    )
                return final_answer

            # ── Case 2: the LLM tried to call a tool ──
            if has_tool_calls:
                # The tool budget is exhausted, so say so plainly and ask for the Final Answer
                if tool_calls_count >= self.MAX_TOOL_CALLS_PER_SECTION:
                    messages.append({"role": "assistant", "content": response})
                    messages.append({
                        "role": "user",
                        "content": REACT_TOOL_LIMIT_MSG.format(
                            tool_calls_count=tool_calls_count,
                            max_tool_calls=self.MAX_TOOL_CALLS_PER_SECTION,
                        ),
                    })
                    continue

                # Execute only the first tool call
                call = tool_calls[0]
                if len(tool_calls) > 1:
                    logger.info(f"The LLM tried to call {len(tool_calls)} tools, executing only the first: {call['name']}")

                if self.report_logger:
                    self.report_logger.log_tool_call(
                        section_title=section.title,
                        section_index=section_index,
                        tool_name=call["name"],
                        parameters=call.get("parameters", {}),
                        iteration=iteration + 1
                    )

                result = self._execute_tool(
                    call["name"],
                    call.get("parameters", {}),
                    report_context=report_context
                )

                if self.report_logger:
                    self.report_logger.log_tool_result(
                        section_title=section.title,
                        section_index=section_index,
                        tool_name=call["name"],
                        result=result,
                        iteration=iteration + 1
                    )

                tool_calls_count += 1
                used_tools.add(call['name'])

                # Build the unused-tools hint
                unused_tools = all_tools - used_tools
                unused_hint = ""
                if unused_tools and tool_calls_count < self.MAX_TOOL_CALLS_PER_SECTION:
                    unused_hint = REACT_UNUSED_TOOLS_HINT.format(unused_list=", ".join(unused_tools))

                messages.append({"role": "assistant", "content": response})
                messages.append({
                    "role": "user",
                    "content": REACT_OBSERVATION_TEMPLATE.format(
                        tool_name=call["name"],
                        result=result,
                        tool_calls_count=tool_calls_count,
                        max_tool_calls=self.MAX_TOOL_CALLS_PER_SECTION,
                        used_tools_str=", ".join(used_tools),
                        unused_hint=unused_hint,
                    ),
                })
                continue

            # ── Case 3: there is neither a tool call nor a Final Answer ──
            messages.append({"role": "assistant", "content": response})

            if tool_calls_count < min_tool_calls:
                # Too few tool calls, so suggest the tools not used yet
                unused_tools = all_tools - used_tools
                unused_hint = f" (not used yet, worth a try: {', '.join(unused_tools)})" if unused_tools else ""

                messages.append({
                    "role": "user",
                    "content": REACT_INSUFFICIENT_TOOLS_MSG_ALT.format(
                        tool_calls_count=tool_calls_count,
                        min_tool_calls=min_tool_calls,
                        unused_hint=unused_hint,
                    ),
                })
                continue

            # Enough tool calls, and the LLM produced content without the "Final Answer:" prefix
            # Take that content as the final answer rather than spinning further
            logger.info(f"Section {section.title}: no 'Final Answer:' prefix found, taking the LLM output as the final content (tool calls: {tool_calls_count})")
            final_answer = response.strip()

            if self.report_logger:
                self.report_logger.log_section_content(
                    section_title=section.title,
                    section_index=section_index,
                    content=final_answer,
                    tool_calls_count=tool_calls_count
                )
            return final_answer
        
        # The maximum iteration count is reached, so force the content out
        logger.warning(f"Section {section.title} hit the maximum iteration count, forcing generation")
        messages.append({"role": "user", "content": REACT_FORCE_FINAL_MSG})
        
        response = self.llm.chat(
            messages=messages,
            temperature=0.5,
            max_tokens=4096
        )

        # Check whether the forced wrap-up also returned None
        if response is None:
            logger.error(f"Section {section.title}: the LLM returned None during the forced wrap-up, using a default error notice")
            final_answer = f"(This section could not be generated: the LLM returned an empty response. Please try again later.)"
        elif "Final Answer:" in response:
            final_answer = response.split("Final Answer:")[-1].strip()
        else:
            final_answer = response
        
        # Log that the section content was generated
        if self.report_logger:
            self.report_logger.log_section_content(
                section_title=section.title,
                section_index=section_index,
                content=final_answer,
                tool_calls_count=tool_calls_count
            )
        
        return final_answer
    
    def generate_report(
        self, 
        progress_callback: Optional[Callable[[str, int, str], None]] = None,
        report_id: Optional[str] = None
    ) -> Report:
        """
        Generate the full report, streaming it out section by section
        
        Each section is written to disk as soon as it is done; there is no need to wait for the whole report.
        File layout:
        reports/{report_id}/
            meta.json       - report metadata
            outline.json    - the report outline
            progress.json   - generation progress
            section_01.md   - section 1
            section_02.md   - section 2
            ...
            full_report.md  - the full report
        
        Args:
            progress_callback: a progress callback (stage, progress, message)
            report_id: the report ID (optional; generated automatically when omitted)
            
        Returns:
            Report: the full report
        """
        import uuid
        
        # Generate a report_id automatically when none was passed in
        if not report_id:
            report_id = f"report_{uuid.uuid4().hex[:12]}"
        start_time = datetime.now()
        
        report = Report(
            report_id=report_id,
            simulation_id=self.simulation_id,
            graph_id=self.graph_id,
            simulation_requirement=self.simulation_requirement,
            status=ReportStatus.PENDING,
            created_at=datetime.now().isoformat()
        )
        
        # Titles of the finished sections (used to track progress)
        completed_section_titles = []
        
        try:
            # Initialize: create the report folder and save the initial state
            ReportManager._ensure_report_folder(report_id)
            
            # Initialize the structured logger (agent_log.jsonl)
            self.report_logger = ReportLogger(report_id)
            self.report_logger.log_start(
                simulation_id=self.simulation_id,
                graph_id=self.graph_id,
                simulation_requirement=self.simulation_requirement
            )
            
            # Initialize the console log writer (console_log.txt)
            self.console_logger = ReportConsoleLogger(report_id)
            
            ReportManager.update_progress(
                report_id, "pending", 0, "Initializing the report...",
                completed_sections=[]
            )
            ReportManager.save_report(report)
            
            # Stage 1: plan the outline
            report.status = ReportStatus.PLANNING
            ReportManager.update_progress(
                report_id, "planning", 5, "Planning the report outline...",
                completed_sections=[]
            )
            
            # Log the start of planning
            self.report_logger.log_planning_start()
            
            if progress_callback:
                progress_callback("planning", 0, "Planning the report outline...")
            
            outline = self.plan_outline(
                progress_callback=lambda stage, prog, msg: 
                    progress_callback(stage, prog // 5, msg) if progress_callback else None
            )
            report.outline = outline
            
            # Log the completed planning
            self.report_logger.log_planning_complete(outline.to_dict())
            
            # Save the outline to disk
            ReportManager.save_outline(report_id, outline)
            ReportManager.update_progress(
                report_id, "planning", 15, f"Outline planned: {len(outline.sections)} sections",
                completed_sections=[]
            )
            ReportManager.save_report(report)
            
            logger.info(f"Outline saved to file: {report_id}/outline.json")
            
            # Stage 2: generate section by section, saving as we go
            report.status = ReportStatus.GENERATING
            
            total_sections = len(outline.sections)
            generated_sections = []  # keep the content for context
            
            for i, section in enumerate(outline.sections):
                section_num = i + 1
                base_progress = 20 + int((i / total_sections) * 70)
                
                # Update progress
                ReportManager.update_progress(
                    report_id, "generating", base_progress,
                    f"Generating section: {section.title} ({section_num}/{total_sections})",
                    current_section=section.title,
                    completed_sections=completed_section_titles
                )
                
                if progress_callback:
                    progress_callback(
                        "generating", 
                        base_progress, 
                        f"Generating section: {section.title} ({section_num}/{total_sections})"
                    )
                
                # Generate the main section content
                section_content = self._generate_section_react(
                    section=section,
                    outline=outline,
                    previous_sections=generated_sections,
                    progress_callback=lambda stage, prog, msg:
                        progress_callback(
                            stage, 
                            base_progress + int(prog * 0.7 / total_sections),
                            msg
                        ) if progress_callback else None,
                    section_index=section_num
                )
                
                section.content = section_content
                generated_sections.append(f"## {section.title}\n\n{section_content}")

                # Save the section
                ReportManager.save_section(report_id, section_num, section)
                completed_section_titles.append(section.title)

                # Log the section completion
                full_section_content = f"## {section.title}\n\n{section_content}"

                if self.report_logger:
                    self.report_logger.log_section_full_complete(
                        section_title=section.title,
                        section_index=section_num,
                        full_content=full_section_content.strip()
                    )

                logger.info(f"Section saved: {report_id}/section_{section_num:02d}.md")
                
                # Update progress
                ReportManager.update_progress(
                    report_id, "generating", 
                    base_progress + int(70 / total_sections),
                    f"Section {section.title} is done",
                    current_section=None,
                    completed_sections=completed_section_titles
                )
            
            # Stage 3: assemble the full report
            if progress_callback:
                progress_callback("generating", 95, "Assembling the full report...")
            
            ReportManager.update_progress(
                report_id, "generating", 95, "Assembling the full report...",
                completed_sections=completed_section_titles
            )
            
            # Use ReportManager to assemble the full report
            report.markdown_content = ReportManager.assemble_full_report(report_id, outline)
            report.status = ReportStatus.COMPLETED
            report.completed_at = datetime.now().isoformat()
            
            # Measure the total elapsed time
            total_time_seconds = (datetime.now() - start_time).total_seconds()
            
            # Log the completed report
            if self.report_logger:
                self.report_logger.log_report_complete(
                    total_sections=total_sections,
                    total_time_seconds=total_time_seconds
                )
            
            # Save the final report
            ReportManager.save_report(report)
            ReportManager.update_progress(
                report_id, "completed", 100, "Report generation complete",
                completed_sections=completed_section_titles
            )
            
            if progress_callback:
                progress_callback("completed", 100, "Report generation complete")
            
            logger.info(f"Report generation complete: {report_id}")
            
            # Close the console log writer
            if self.console_logger:
                self.console_logger.close()
                self.console_logger = None
            
            return report
            
        except Exception as e:
            logger.error(f"Report generation failed: {str(e)}")
            report.status = ReportStatus.FAILED
            report.error = str(e)
            
            # Log the error
            if self.report_logger:
                self.report_logger.log_error(str(e), "failed")
            
            # Save the failed state
            try:
                ReportManager.save_report(report)
                ReportManager.update_progress(
                    report_id, "failed", -1, f"Report generation failed: {str(e)}",
                    completed_sections=completed_section_titles
                )
            except Exception:
                pass  # ignore errors while saving the failure state
            
            # Close the console log writer
            if self.console_logger:
                self.console_logger.close()
                self.console_logger = None
            
            return report
    
    def chat(
        self, 
        message: str,
        chat_history: List[Dict[str, str]] = None
    ) -> Dict[str, Any]:
        """
        Chat with the Report Agent
        
        During the conversation the Agent may call retrieval tools on its own to answer
        
        Args:
            message: the user's message
            chat_history: the conversation history
            
        Returns:
            {
                "response": "the Agent's reply",
                "tool_calls": [the tools it called],
                "sources": [where the information came from]
            }
        """
        logger.info(f"Report Agent conversation: {message[:50]}...")
        
        chat_history = chat_history or []
        
        # Fetch the report content generated so far
        report_content = ""
        try:
            report = ReportManager.get_report_by_simulation(self.simulation_id)
            if report and report.markdown_content:
                # Cap the report length so the context does not grow too large
                report_content = report.markdown_content[:15000]
                if len(report.markdown_content) > 15000:
                    report_content += "\n\n... [report content truncated] ..."
        except Exception as e:
            logger.warning(f"Failed to fetch the report content: {e}")
        
        system_prompt = CHAT_SYSTEM_PROMPT_TEMPLATE.format(
            simulation_requirement=self.simulation_requirement,
            report_content=report_content if report_content else "(no report yet)",
            tools_description=self._get_tools_description(),
        )

        # Build the messages
        messages = [{"role": "system", "content": system_prompt}]
        
        # Add the conversation history
        for h in chat_history[-10:]:  # cap the history length
            messages.append(h)
        
        # Add the user's message
        messages.append({
            "role": "user", 
            "content": message
        })
        
        # The ReACT loop (simplified)
        tool_calls_made = []
        max_iterations = 2  # fewer iterations
        
        for iteration in range(max_iterations):
            response = self.llm.chat(
                messages=messages,
                temperature=0.5
            )
            
            # Parse the tool calls
            tool_calls = self._parse_tool_calls(response)
            
            if not tool_calls:
                # No tool call, so return the response as-is
                clean_response = re.sub(r'<tool_call>.*?</tool_call>', '', response, flags=re.DOTALL)
                clean_response = re.sub(r'\[TOOL_CALL\].*?\)', '', clean_response)
                
                return {
                    "response": clean_response.strip(),
                    "tool_calls": tool_calls_made,
                    "sources": [tc.get("parameters", {}).get("query", "") for tc in tool_calls_made]
                }
            
            # Execute the tool calls (capped)
            tool_results = []
            for call in tool_calls[:1]:  # at most one tool call per round
                if len(tool_calls_made) >= self.MAX_TOOL_CALLS_PER_CHAT:
                    break
                result = self._execute_tool(call["name"], call.get("parameters", {}))
                tool_results.append({
                    "tool": call["name"],
                    "result": result[:1500]  # cap the result length
                })
                tool_calls_made.append(call)
            
            # Add the result to the messages
            messages.append({"role": "assistant", "content": response})
            observation = "\n".join([f"[{r['tool']} result]\n{r['result']}" for r in tool_results])
            messages.append({
                "role": "user",
                "content": observation + CHAT_OBSERVATION_SUFFIX
            })
        
        # The iteration cap is reached, so get the final response
        final_response = self.llm.chat(
            messages=messages,
            temperature=0.5
        )
        
        # Clean up the response
        clean_response = re.sub(r'<tool_call>.*?</tool_call>', '', final_response, flags=re.DOTALL)
        clean_response = re.sub(r'\[TOOL_CALL\].*?\)', '', clean_response)
        
        return {
            "response": clean_response.strip(),
            "tool_calls": tool_calls_made,
            "sources": [tc.get("parameters", {}).get("query", "") for tc in tool_calls_made]
        }


class ReportManager:
    """
    Report manager
    
    Handles persistence and retrieval of reports
    
    File layout (streamed section by section):
    reports/
      {report_id}/
        meta.json          - report metadata and status
        outline.json       - the report outline
        progress.json      - generation progress
        section_01.md      - section 1
        section_02.md      - section 2
        ...
        full_report.md     - the full report
    """
    
    # Report storage directory
    REPORTS_DIR = os.path.join(Config.UPLOAD_FOLDER, 'reports')
    
    @classmethod
    def _ensure_reports_dir(cls):
        """Make sure the report root directory exists"""
        os.makedirs(cls.REPORTS_DIR, exist_ok=True)
    
    @classmethod
    def _get_report_folder(cls, report_id: str) -> str:
        """Get the report folder path"""
        return os.path.join(cls.REPORTS_DIR, report_id)
    
    @classmethod
    def _ensure_report_folder(cls, report_id: str) -> str:
        """Make sure the report folder exists and return its path"""
        folder = cls._get_report_folder(report_id)
        os.makedirs(folder, exist_ok=True)
        return folder
    
    @classmethod
    def _get_report_path(cls, report_id: str) -> str:
        """Get the path of the report metadata file"""
        return os.path.join(cls._get_report_folder(report_id), "meta.json")
    
    @classmethod
    def _get_report_markdown_path(cls, report_id: str) -> str:
        """Get the path of the full report Markdown file"""
        return os.path.join(cls._get_report_folder(report_id), "full_report.md")
    
    @classmethod
    def _get_outline_path(cls, report_id: str) -> str:
        """Get the path of the outline file"""
        return os.path.join(cls._get_report_folder(report_id), "outline.json")
    
    @classmethod
    def _get_progress_path(cls, report_id: str) -> str:
        """Get the path of the progress file"""
        return os.path.join(cls._get_report_folder(report_id), "progress.json")
    
    @classmethod
    def _get_section_path(cls, report_id: str, section_index: int) -> str:
        """Get the path of a section's Markdown file"""
        return os.path.join(cls._get_report_folder(report_id), f"section_{section_index:02d}.md")
    
    @classmethod
    def _get_agent_log_path(cls, report_id: str) -> str:
        """Get the path of the Agent log file"""
        return os.path.join(cls._get_report_folder(report_id), "agent_log.jsonl")
    
    @classmethod
    def _get_console_log_path(cls, report_id: str) -> str:
        """Get the path of the console log file"""
        return os.path.join(cls._get_report_folder(report_id), "console_log.txt")
    
    @classmethod
    def get_console_log(cls, report_id: str, from_line: int = 0) -> Dict[str, Any]:
        """
        Read the console log content
        
        This is the console output produced while the report was generated (INFO, WARNING, ...),
        as opposed to the structured log in agent_log.jsonl.
        
        Args:
            report_id: the report ID
            from_line: the line to start from (for incremental reads; 0 means from the beginning)
            
        Returns:
            {
                "logs": [the log lines],
                "total_lines": the total number of lines,
                "from_line": the starting line number,
                "has_more": whether there is more
            }
        """
        log_path = cls._get_console_log_path(report_id)
        
        if not os.path.exists(log_path):
            return {
                "logs": [],
                "total_lines": 0,
                "from_line": 0,
                "has_more": False
            }
        
        logs = []
        total_lines = 0
        
        with open(log_path, 'r', encoding='utf-8') as f:
            for i, line in enumerate(f):
                total_lines = i + 1
                if i >= from_line:
                    # Keep the raw log lines, with the trailing newline removed
                    logs.append(line.rstrip('\n\r'))
        
        return {
            "logs": logs,
            "total_lines": total_lines,
            "from_line": from_line,
            "has_more": False  # already at the end
        }
    
    @classmethod
    def get_console_log_stream(cls, report_id: str) -> List[str]:
        """
        Read the whole console log in one go
        
        Args:
            report_id: the report ID
            
        Returns:
            the list of log lines
        """
        result = cls.get_console_log(report_id, from_line=0)
        return result["logs"]
    
    @classmethod
    def get_agent_log(cls, report_id: str, from_line: int = 0) -> Dict[str, Any]:
        """
        Read the Agent log content
        
        Args:
            report_id: the report ID
            from_line: the line to start from (for incremental reads; 0 means from the beginning)
            
        Returns:
            {
                "logs": [the log entries],
                "total_lines": the total number of lines,
                "from_line": the starting line number,
                "has_more": whether there is more
            }
        """
        log_path = cls._get_agent_log_path(report_id)
        
        if not os.path.exists(log_path):
            return {
                "logs": [],
                "total_lines": 0,
                "from_line": 0,
                "has_more": False
            }
        
        logs = []
        total_lines = 0
        
        with open(log_path, 'r', encoding='utf-8') as f:
            for i, line in enumerate(f):
                total_lines = i + 1
                if i >= from_line:
                    try:
                        log_entry = json.loads(line.strip())
                        logs.append(log_entry)
                    except json.JSONDecodeError:
                        # Skip the lines that failed to parse
                        continue
        
        return {
            "logs": logs,
            "total_lines": total_lines,
            "from_line": from_line,
            "has_more": False  # already at the end
        }
    
    @classmethod
    def get_agent_log_stream(cls, report_id: str) -> List[Dict[str, Any]]:
        """
        Read the whole Agent log (everything in one go)
        
        Args:
            report_id: the report ID
            
        Returns:
            the list of log entries
        """
        result = cls.get_agent_log(report_id, from_line=0)
        return result["logs"]
    
    @classmethod
    def save_outline(cls, report_id: str, outline: ReportOutline) -> None:
        """
        Save the report outline
        
        Called as soon as the planning stage finishes
        """
        cls._ensure_report_folder(report_id)
        
        with open(cls._get_outline_path(report_id), 'w', encoding='utf-8') as f:
            json.dump(outline.to_dict(), f, ensure_ascii=False, indent=2)
        
        logger.info(f"Outline saved: {report_id}")
    
    @classmethod
    def save_section(
        cls,
        report_id: str,
        section_index: int,
        section: ReportSection
    ) -> str:
        """
        Save a single section

        Called as soon as each section is generated, which is what streams the output

        Args:
            report_id: the report ID
            section_index: the section index (starting at 1)
            section: the section object

        Returns:
            the saved file path
        """
        cls._ensure_report_folder(report_id)

        # Build the section Markdown, cleaning up any duplicated headings
        cleaned_content = cls._clean_section_content(section.content, section.title)
        md_content = f"## {section.title}\n\n"
        if cleaned_content:
            md_content += f"{cleaned_content}\n\n"

        # Save the file
        file_suffix = f"section_{section_index:02d}.md"
        file_path = os.path.join(cls._get_report_folder(report_id), file_suffix)
        with open(file_path, 'w', encoding='utf-8') as f:
            f.write(md_content)

        logger.info(f"Section saved: {report_id}/{file_suffix}")
        return file_path
    
    @classmethod
    def _clean_section_content(cls, content: str, section_title: str) -> str:
        """
        Clean up section content
        
        1. Remove a leading Markdown heading that duplicates the section title
        2. Turn every heading at level ### or deeper into bold text
        
        Args:
            content: the raw content
            section_title: the section title
            
        Returns:
            the cleaned-up content
        """
        import re
        
        if not content:
            return content
        
        content = content.strip()
        lines = content.split('\n')
        cleaned_lines = []
        skip_next_empty = False
        
        for i, line in enumerate(lines):
            stripped = line.strip()
            
            # Check whether this is a Markdown heading line
            heading_match = re.match(r'^(#{1,6})\s+(.+)$', stripped)
            
            if heading_match:
                level = len(heading_match.group(1))
                title_text = heading_match.group(2).strip()
                
                # Check whether it duplicates the section title (only in the first 5 lines)
                if i < 5:
                    if title_text == section_title or title_text.replace(' ', '') == section_title.replace(' ', ''):
                        skip_next_empty = True
                        continue
                
                # Turn headings of any level (#, ##, ###, ####, ...) into bold
                # The section title is added by the system, so the content should carry no headings at all
                cleaned_lines.append(f"**{title_text}**")
                cleaned_lines.append("")  # add a blank line
                continue
            
            # Skip this blank line too when the previous line was a dropped heading
            if skip_next_empty and stripped == '':
                skip_next_empty = False
                continue
            
            skip_next_empty = False
            cleaned_lines.append(line)
        
        # Remove the leading blank lines
        while cleaned_lines and cleaned_lines[0].strip() == '':
            cleaned_lines.pop(0)
        
        # Remove a leading horizontal rule
        while cleaned_lines and cleaned_lines[0].strip() in ['---', '***', '___']:
            cleaned_lines.pop(0)
            # Also remove the blank line that follows the horizontal rule
            while cleaned_lines and cleaned_lines[0].strip() == '':
                cleaned_lines.pop(0)
        
        return '\n'.join(cleaned_lines)
    
    @classmethod
    def update_progress(
        cls, 
        report_id: str, 
        status: str, 
        progress: int, 
        message: str,
        current_section: str = None,
        completed_sections: List[str] = None
    ) -> None:
        """
        Update report generation progress
        
        The frontend can read progress.json for live progress
        """
        cls._ensure_report_folder(report_id)
        
        progress_data = {
            "status": status,
            "progress": progress,
            "message": message,
            "current_section": current_section,
            "completed_sections": completed_sections or [],
            "updated_at": datetime.now().isoformat()
        }
        
        with open(cls._get_progress_path(report_id), 'w', encoding='utf-8') as f:
            json.dump(progress_data, f, ensure_ascii=False, indent=2)
    
    @classmethod
    def get_progress(cls, report_id: str) -> Optional[Dict[str, Any]]:
        """Get the report generation progress"""
        path = cls._get_progress_path(report_id)
        
        if not os.path.exists(path):
            return None
        
        with open(path, 'r', encoding='utf-8') as f:
            return json.load(f)
    
    @classmethod
    def get_generated_sections(cls, report_id: str) -> List[Dict[str, Any]]:
        """
        Fetch the list of generated sections
        
        Returns the file information for every section saved so far
        """
        folder = cls._get_report_folder(report_id)
        
        if not os.path.exists(folder):
            return []
        
        sections = []
        for filename in sorted(os.listdir(folder)):
            if filename.startswith('section_') and filename.endswith('.md'):
                file_path = os.path.join(folder, filename)
                with open(file_path, 'r', encoding='utf-8') as f:
                    content = f.read()

                # Parse the section index from the filename
                parts = filename.replace('.md', '').split('_')
                section_index = int(parts[1])

                sections.append({
                    "filename": filename,
                    "section_index": section_index,
                    "content": content
                })

        return sections
    
    @classmethod
    def assemble_full_report(cls, report_id: str, outline: ReportOutline) -> str:
        """
        Assemble the full report
        
        Assembles the full report from the saved section files and cleans up the headings
        """
        folder = cls._get_report_folder(report_id)
        
        # Build the report header
        md_content = f"# {outline.title}\n\n"
        md_content += f"> {outline.summary}\n\n"
        md_content += f"---\n\n"
        
        # Read every section file in order
        sections = cls.get_generated_sections(report_id)
        for section_info in sections:
            md_content += section_info["content"]
        
        # Post-process: clean up the heading problems across the whole report
        md_content = cls._post_process_report(md_content, outline)
        
        # Save the full report
        full_path = cls._get_report_markdown_path(report_id)
        with open(full_path, 'w', encoding='utf-8') as f:
            f.write(md_content)
        
        logger.info(f"Full report assembled: {report_id}")
        return md_content
    
    @classmethod
    def _post_process_report(cls, content: str, outline: ReportOutline) -> str:
        """
        Post-process the report content
        
        1. Remove duplicated headings
        2. Keep the report title (#) and the section titles (##), drop every other heading level (###, ####, ...)
        3. Clean up surplus blank lines and horizontal rules
        
        Args:
            content: the raw report content
            outline: the report outline
            
        Returns:
            the processed content
        """
        import re
        
        lines = content.split('\n')
        processed_lines = []
        prev_was_heading = False
        
        # Collect every section title from the outline
        section_titles = set()
        for section in outline.sections:
            section_titles.add(section.title)
        
        i = 0
        while i < len(lines):
            line = lines[i]
            stripped = line.strip()
            
            # Check whether this is a heading line
            heading_match = re.match(r'^(#{1,6})\s+(.+)$', stripped)
            
            if heading_match:
                level = len(heading_match.group(1))
                title = heading_match.group(2).strip()
                
                # Check for a duplicated heading (the same heading within 5 consecutive lines)
                is_duplicate = False
                for j in range(max(0, len(processed_lines) - 5), len(processed_lines)):
                    prev_line = processed_lines[j].strip()
                    prev_match = re.match(r'^(#{1,6})\s+(.+)$', prev_line)
                    if prev_match:
                        prev_title = prev_match.group(2).strip()
                        if prev_title == title:
                            is_duplicate = True
                            break
                
                if is_duplicate:
                    # Skip the duplicate heading and the blank lines after it
                    i += 1
                    while i < len(lines) and lines[i].strip() == '':
                        i += 1
                    continue
                
                # Heading level handling:
                # - # (level=1) is kept only for the report title
                # - ## (level=2) is kept for section titles
                # - ### and deeper (level>=3) become bold text
                
                if level == 1:
                    if title == outline.title:
                        # Keep the report title
                        processed_lines.append(line)
                        prev_was_heading = True
                    elif title in section_titles:
                        # A section title mistakenly used #, so fix it to ##
                        processed_lines.append(f"## {title}")
                        prev_was_heading = True
                    else:
                        # Other level-1 headings become bold
                        processed_lines.append(f"**{title}**")
                        processed_lines.append("")
                        prev_was_heading = False
                elif level == 2:
                    if title in section_titles or title == outline.title:
                        # Keep the section title
                        processed_lines.append(line)
                        prev_was_heading = True
                    else:
                        # A level-2 heading that is not a section becomes bold
                        processed_lines.append(f"**{title}**")
                        processed_lines.append("")
                        prev_was_heading = False
                else:
                    # Headings at level ### or deeper become bold text
                    processed_lines.append(f"**{title}**")
                    processed_lines.append("")
                    prev_was_heading = False
                
                i += 1
                continue
            
            elif stripped == '---' and prev_was_heading:
                # Skip a horizontal rule that directly follows a heading
                i += 1
                continue
            
            elif stripped == '' and prev_was_heading:
                # Keep only one blank line after a heading
                if processed_lines and processed_lines[-1].strip() != '':
                    processed_lines.append(line)
                prev_was_heading = False
            
            else:
                processed_lines.append(line)
                prev_was_heading = False
            
            i += 1
        
        # Collapse runs of blank lines (keep at most 2)
        result_lines = []
        empty_count = 0
        for line in processed_lines:
            if line.strip() == '':
                empty_count += 1
                if empty_count <= 2:
                    result_lines.append(line)
            else:
                empty_count = 0
                result_lines.append(line)
        
        return '\n'.join(result_lines)
    
    @classmethod
    def save_report(cls, report: Report) -> None:
        """Save the report metadata and the full report"""
        cls._ensure_report_folder(report.report_id)
        
        # Save the metadata JSON
        with open(cls._get_report_path(report.report_id), 'w', encoding='utf-8') as f:
            json.dump(report.to_dict(), f, ensure_ascii=False, indent=2)
        
        # Save the outline
        if report.outline:
            cls.save_outline(report.report_id, report.outline)
        
        # Save the full Markdown report
        if report.markdown_content:
            with open(cls._get_report_markdown_path(report.report_id), 'w', encoding='utf-8') as f:
                f.write(report.markdown_content)
        
        logger.info(f"Report saved: {report.report_id}")
    
    @classmethod
    def get_report(cls, report_id: str) -> Optional[Report]:
        """Fetch a report"""
        path = cls._get_report_path(report_id)
        
        if not os.path.exists(path):
            # Legacy format: look for files stored directly under the reports directory
            old_path = os.path.join(cls.REPORTS_DIR, f"{report_id}.json")
            if os.path.exists(old_path):
                path = old_path
            else:
                return None
        
        with open(path, 'r', encoding='utf-8') as f:
            data = json.load(f)
        
        # Rebuild the Report object
        outline = None
        if data.get('outline'):
            outline_data = data['outline']
            sections = []
            for s in outline_data.get('sections', []):
                sections.append(ReportSection(
                    title=s['title'],
                    content=s.get('content', '')
                ))
            outline = ReportOutline(
                title=outline_data['title'],
                summary=outline_data['summary'],
                sections=sections
            )
        
        # If markdown_content is empty, try reading full_report.md
        markdown_content = data.get('markdown_content', '')
        if not markdown_content:
            full_report_path = cls._get_report_markdown_path(report_id)
            if os.path.exists(full_report_path):
                with open(full_report_path, 'r', encoding='utf-8') as f:
                    markdown_content = f.read()
        
        return Report(
            report_id=data['report_id'],
            simulation_id=data['simulation_id'],
            graph_id=data['graph_id'],
            simulation_requirement=data['simulation_requirement'],
            status=ReportStatus(data['status']),
            outline=outline,
            markdown_content=markdown_content,
            created_at=data.get('created_at', ''),
            completed_at=data.get('completed_at', ''),
            error=data.get('error')
        )
    
    @classmethod
    def get_report_by_simulation(cls, simulation_id: str) -> Optional[Report]:
        """Fetch the report for a simulation ID"""
        cls._ensure_reports_dir()
        
        for item in os.listdir(cls.REPORTS_DIR):
            item_path = os.path.join(cls.REPORTS_DIR, item)
            # New format: a folder
            if os.path.isdir(item_path):
                report = cls.get_report(item)
                if report and report.simulation_id == simulation_id:
                    return report
            # Legacy format: a JSON file
            elif item.endswith('.json'):
                report_id = item[:-5]
                report = cls.get_report(report_id)
                if report and report.simulation_id == simulation_id:
                    return report
        
        return None
    
    @classmethod
    def list_reports(cls, simulation_id: Optional[str] = None, limit: int = 50) -> List[Report]:
        """List the reports"""
        cls._ensure_reports_dir()
        
        reports = []
        for item in os.listdir(cls.REPORTS_DIR):
            item_path = os.path.join(cls.REPORTS_DIR, item)
            # New format: a folder
            if os.path.isdir(item_path):
                report = cls.get_report(item)
                if report:
                    if simulation_id is None or report.simulation_id == simulation_id:
                        reports.append(report)
            # Legacy format: a JSON file
            elif item.endswith('.json'):
                report_id = item[:-5]
                report = cls.get_report(report_id)
                if report:
                    if simulation_id is None or report.simulation_id == simulation_id:
                        reports.append(report)
        
        # Newest first
        reports.sort(key=lambda r: r.created_at, reverse=True)
        
        return reports[:limit]
    
    @classmethod
    def delete_report(cls, report_id: str) -> bool:
        """Delete a report (the whole folder)"""
        import shutil
        
        folder_path = cls._get_report_folder(report_id)
        
        # New format: delete the whole folder
        if os.path.exists(folder_path) and os.path.isdir(folder_path):
            shutil.rmtree(folder_path)
            logger.info(f"Report folder deleted: {report_id}")
            return True
        
        # Legacy format: delete the individual files
        deleted = False
        old_json_path = os.path.join(cls.REPORTS_DIR, f"{report_id}.json")
        old_md_path = os.path.join(cls.REPORTS_DIR, f"{report_id}.md")
        
        if os.path.exists(old_json_path):
            os.remove(old_json_path)
            deleted = True
        if os.path.exists(old_md_path):
            os.remove(old_md_path)
            deleted = True
        
        return deleted
