"""
Simulation configuration generator
Uses the LLM to derive detailed simulation parameters from the simulation goal, the documents and the graph
Fully automated: no parameter needs to be set by hand

Generates in stages, so one oversized response cannot fail the whole run:
1. Generate the time configuration
2. Generate the event configuration
3. Generate the Agent configurations in batches
4. Generate the platform configuration
"""

import json
import math
from typing import Dict, Any, List, Optional, Callable
from dataclasses import dataclass, field, asdict
from datetime import datetime

from openai import OpenAI

from ..config import Config
from ..utils.llm_client import resolve_api_key
from ..utils.logger import get_logger
from .zep_entity_reader import EntityNode, ZepEntityReader

logger = get_logger('mirofish.simulation_config')

# A daily rhythm for the simulated population (Beijing time)
CHINA_TIMEZONE_CONFIG = {
    # Late night (almost nobody active)
    "dead_hours": [0, 1, 2, 3, 4, 5],
    # Morning (people gradually wake up)
    "morning_hours": [6, 7, 8],
    # Working hours
    "work_hours": [9, 10, 11, 12, 13, 14, 15, 16, 17, 18],
    # Evening peak (the most active period)
    "peak_hours": [19, 20, 21, 22],
    # Night (activity winds down)
    "night_hours": [23],
    # Activity multipliers
    "activity_multipliers": {
        "dead": 0.05,      # the small hours, almost nobody around
        "morning": 0.4,    # waking up
        "work": 0.7,       # working hours, moderate
        "peak": 1.5,       # evening peak
        "night": 0.5       # late night, winding down
    }
}


@dataclass
class AgentActivityConfig:
    """The activity configuration of a single Agent"""
    agent_id: int
    entity_uuid: str
    entity_name: str
    entity_type: str
    
    # Activity level (0.0-1.0)
    activity_level: float = 0.5  # overall activity
    
    # Posting frequency (expected posts per hour)
    posts_per_hour: float = 1.0
    comments_per_hour: float = 2.0
    
    # Active hours (24-hour clock, 0-23)
    active_hours: List[int] = field(default_factory=lambda: list(range(8, 23)))
    
    # Response speed (delay before reacting to a hot event, in simulated minutes)
    response_delay_min: int = 5
    response_delay_max: int = 60
    
    # Sentiment bias (-1.0 to 1.0, negative to positive)
    sentiment_bias: float = 0.0
    
    # Stance (attitude to a particular topic)
    stance: str = "neutral"  # supportive, opposing, neutral, observer
    
    # Influence weight (how likely their posts are to reach other Agents)
    influence_weight: float = 1.0


@dataclass  
class TimeSimulationConfig:
    """The time configuration (based on a realistic daily rhythm)"""
    # Total simulation length (in simulated hours)
    total_simulation_hours: int = 72  # 72 hours (3 days) by default
    
    # Simulated minutes represented by one round - 60 by default, to speed the clock up
    minutes_per_round: int = 60
    
    # How many Agents are activated per hour
    agents_per_hour_min: int = 5
    agents_per_hour_max: int = 20
    
    # Peak hours (19:00-22:00, when people are most active)
    peak_hours: List[int] = field(default_factory=lambda: [19, 20, 21, 22])
    peak_activity_multiplier: float = 1.5
    
    # Off-peak hours (00:00-05:00, when almost nobody is active)
    off_peak_hours: List[int] = field(default_factory=lambda: [0, 1, 2, 3, 4, 5])
    off_peak_activity_multiplier: float = 0.05  # activity nearly stops in the small hours
    
    # Morning hours
    morning_hours: List[int] = field(default_factory=lambda: [6, 7, 8])
    morning_activity_multiplier: float = 0.4
    
    # Working hours
    work_hours: List[int] = field(default_factory=lambda: [9, 10, 11, 12, 13, 14, 15, 16, 17, 18])
    work_activity_multiplier: float = 0.7


@dataclass
class EventConfig:
    """The event configuration"""
    # Initial events (fired when the simulation starts)
    initial_posts: List[Dict[str, Any]] = field(default_factory=list)
    
    # Scheduled events (fired at a specific time)
    scheduled_events: List[Dict[str, Any]] = field(default_factory=list)
    
    # Trending topic keywords
    hot_topics: List[str] = field(default_factory=list)
    
    # The narrative direction to steer public opinion
    narrative_direction: str = ""


@dataclass
class PlatformConfig:
    """Platform-specific configuration"""
    platform: str  # twitter or reddit
    
    # Recommendation algorithm weights
    recency_weight: float = 0.4  # freshness
    popularity_weight: float = 0.3  # popularity
    relevance_weight: float = 0.3  # relevance
    
    # Viral threshold (how much interaction triggers wider spread)
    viral_threshold: int = 10
    
    # Echo chamber strength (how tightly similar views cluster)
    echo_chamber_strength: float = 0.5


@dataclass
class SimulationParameters:
    """The complete simulation configuration"""
    # Basic information
    simulation_id: str
    project_id: str
    graph_id: str
    simulation_requirement: str
    
    # Time configuration
    time_config: TimeSimulationConfig = field(default_factory=TimeSimulationConfig)
    
    # The list of Agent configurations
    agent_configs: List[AgentActivityConfig] = field(default_factory=list)
    
    # Event configuration
    event_config: EventConfig = field(default_factory=EventConfig)
    
    # Platform configuration
    twitter_config: Optional[PlatformConfig] = None
    reddit_config: Optional[PlatformConfig] = None
    
    # LLM configuration
    llm_model: str = ""
    llm_base_url: str = ""
    
    # Generation metadata
    generated_at: str = field(default_factory=lambda: datetime.now().isoformat())
    generation_reasoning: str = ""  # the LLM's explanation
    
    def to_dict(self) -> Dict[str, Any]:
        """Convert to a dictionary"""
        time_dict = asdict(self.time_config)
        return {
            "simulation_id": self.simulation_id,
            "project_id": self.project_id,
            "graph_id": self.graph_id,
            "simulation_requirement": self.simulation_requirement,
            "time_config": time_dict,
            "agent_configs": [asdict(a) for a in self.agent_configs],
            "event_config": asdict(self.event_config),
            "twitter_config": asdict(self.twitter_config) if self.twitter_config else None,
            "reddit_config": asdict(self.reddit_config) if self.reddit_config else None,
            "llm_model": self.llm_model,
            "llm_base_url": self.llm_base_url,
            "generated_at": self.generated_at,
            "generation_reasoning": self.generation_reasoning,
        }
    
    def to_json(self, indent: int = 2) -> str:
        """Convert to a JSON string"""
        return json.dumps(self.to_dict(), ensure_ascii=False, indent=indent)


class SimulationConfigGenerator:
    """
    Simulation configuration generator
    
    Analyses the simulation goal, the documents and the graph entities with the LLM
    and derives the best simulation configuration
    
    Generates in stages:
    1. The time and event configuration (lightweight)
    2. The Agent configurations in batches (10-20 each)
    3. The platform configuration
    """
    
    # Maximum characters of context
    MAX_CONTEXT_LENGTH = 50000
    # How many Agents to generate per batch
    AGENTS_PER_BATCH = 15
    
    # Context truncation lengths per step, in characters
    TIME_CONFIG_CONTEXT_LENGTH = 10000   # time configuration
    EVENT_CONFIG_CONTEXT_LENGTH = 8000   # event configuration
    ENTITY_SUMMARY_LENGTH = 300          # entity summary
    AGENT_SUMMARY_LENGTH = 300           # entity summary inside the Agent configuration
    ENTITIES_PER_TYPE_DISPLAY = 20       # how many entities of each type to show
    
    def __init__(
        self,
        api_key: Optional[str] = None,
        base_url: Optional[str] = None,
        model_name: Optional[str] = None
    ):
        self.base_url = base_url or Config.LLM_BASE_URL
        self.api_key = resolve_api_key(api_key or Config.LLM_API_KEY, self.base_url)
        self.model_name = model_name or Config.LLM_MODEL_NAME
        
        if not self.api_key:
            raise ValueError("LLM_API_KEY is not configured")
        
        self.client = OpenAI(
            api_key=self.api_key,
            base_url=self.base_url
        )
    
    def generate_config(
        self,
        simulation_id: str,
        project_id: str,
        graph_id: str,
        simulation_requirement: str,
        document_text: str,
        entities: List[EntityNode],
        enable_twitter: bool = True,
        enable_reddit: bool = True,
        progress_callback: Optional[Callable[[int, int, str], None]] = None,
    ) -> SimulationParameters:
        """
        Generate the complete simulation configuration (stage by stage)
        
        Args:
            simulation_id: the simulation ID
            project_id: the project ID
            graph_id: the graph ID
            simulation_requirement: a description of the simulation goal
            document_text: the original document content
            entities: the filtered list of entities
            enable_twitter: whether Twitter is enabled
            enable_reddit: whether Reddit is enabled
            progress_callback: a progress callback (current_step, total_steps, message)
            
        Returns:
            SimulationParameters: the complete simulation configuration
        """
        logger.info(f"Generating the simulation configuration: simulation_id={simulation_id}, entities={len(entities)}")
        
        # Work out the total number of steps
        num_batches = math.ceil(len(entities) / self.AGENTS_PER_BATCH)
        total_steps = 3 + num_batches  # time + events + N Agent batches + platform
        current_step = 0
        
        def report_progress(step: int, message: str):
            nonlocal current_step
            current_step = step
            if progress_callback:
                progress_callback(step, total_steps, message)
            logger.info(f"[{step}/{total_steps}] {message}")
        
        # 1. Build the base context information
        context = self._build_context(
            simulation_requirement=simulation_requirement,
            document_text=document_text,
            entities=entities
        )
        
        reasoning_parts = []
        
        # ========== Step 1: generate the time configuration ==========
        report_progress(1, "Generating the time configuration...")
        num_entities = len(entities)
        time_config_result = self._generate_time_config(context, num_entities)
        time_config = self._parse_time_config(time_config_result, num_entities)
        reasoning_parts.append(f"Time configuration: {time_config_result.get('reasoning', 'ok')}")
        
        # ========== Step 2: generate the event configuration ==========
        report_progress(2, "Generating the event configuration and trending topics...")
        event_config_result = self._generate_event_config(context, simulation_requirement, entities)
        event_config = self._parse_event_config(event_config_result)
        reasoning_parts.append(f"Event configuration: {event_config_result.get('reasoning', 'ok')}")
        
        # ========== Steps 3-N: generate the Agent configurations in batches ==========
        all_agent_configs = []
        for batch_idx in range(num_batches):
            start_idx = batch_idx * self.AGENTS_PER_BATCH
            end_idx = min(start_idx + self.AGENTS_PER_BATCH, len(entities))
            batch_entities = entities[start_idx:end_idx]
            
            report_progress(
                3 + batch_idx,
                f"Generating the Agent configurations ({start_idx + 1}-{end_idx}/{len(entities)})..."
            )
            
            batch_configs = self._generate_agent_configs_batch(
                context=context,
                entities=batch_entities,
                start_idx=start_idx,
                simulation_requirement=simulation_requirement
            )
            all_agent_configs.extend(batch_configs)
        
        reasoning_parts.append(f"Agent configurations: {len(all_agent_configs)} generated")
        
        # ========== Assign a poster to every initial post ==========
        logger.info("Assigning a suitable poster Agent to each initial post...")
        event_config = self._assign_initial_post_agents(event_config, all_agent_configs)
        assigned_count = len([p for p in event_config.initial_posts if p.get("poster_agent_id") is not None])
        reasoning_parts.append(f"Initial posts: {assigned_count} posts given a poster")
        
        # ========== Final step: generate the platform configuration ==========
        report_progress(total_steps, "Generating the platform configuration...")
        twitter_config = None
        reddit_config = None
        
        if enable_twitter:
            twitter_config = PlatformConfig(
                platform="twitter",
                recency_weight=0.4,
                popularity_weight=0.3,
                relevance_weight=0.3,
                viral_threshold=10,
                echo_chamber_strength=0.5
            )
        
        if enable_reddit:
            reddit_config = PlatformConfig(
                platform="reddit",
                recency_weight=0.3,
                popularity_weight=0.4,
                relevance_weight=0.3,
                viral_threshold=15,
                echo_chamber_strength=0.6
            )
        
        # Build the final parameters
        params = SimulationParameters(
            simulation_id=simulation_id,
            project_id=project_id,
            graph_id=graph_id,
            simulation_requirement=simulation_requirement,
            time_config=time_config,
            agent_configs=all_agent_configs,
            event_config=event_config,
            twitter_config=twitter_config,
            reddit_config=reddit_config,
            llm_model=self.model_name,
            llm_base_url=self.base_url,
            generation_reasoning=" | ".join(reasoning_parts)
        )
        
        logger.info(f"Simulation configuration complete: {len(params.agent_configs)} Agent configurations")
        
        return params
    
    def _build_context(
        self,
        simulation_requirement: str,
        document_text: str,
        entities: List[EntityNode]
    ) -> str:
        """Build the LLM context, truncated to the maximum length"""
        
        # Entity summaries
        entity_summary = self._summarize_entities(entities)
        
        # Build the context
        context_parts = [
            f"## Simulation goal\n{simulation_requirement}",
            f"\n## Entity information ({len(entities)} entities)\n{entity_summary}",
        ]
        
        current_length = sum(len(p) for p in context_parts)
        remaining_length = self.MAX_CONTEXT_LENGTH - current_length - 500  # keep a 500-character margin
        
        if remaining_length > 0 and document_text:
            doc_text = document_text[:remaining_length]
            if len(document_text) > remaining_length:
                doc_text += "\n...(document truncated)"
            context_parts.append(f"\n## Original document content\n{doc_text}")
        
        return "\n".join(context_parts)
    
    def _summarize_entities(self, entities: List[EntityNode]) -> str:
        """Generate the entity summary"""
        lines = []
        
        # Group by type
        by_type: Dict[str, List[EntityNode]] = {}
        for e in entities:
            t = e.get_entity_type() or "Unknown"
            if t not in by_type:
                by_type[t] = []
            by_type[t].append(e)
        
        for entity_type, type_entities in by_type.items():
            lines.append(f"\n### {entity_type} ({len(type_entities)} entities)")
            # Use the configured display count and summary length
            display_count = self.ENTITIES_PER_TYPE_DISPLAY
            summary_len = self.ENTITY_SUMMARY_LENGTH
            for e in type_entities[:display_count]:
                summary_preview = (e.summary[:summary_len] + "...") if len(e.summary) > summary_len else e.summary
                lines.append(f"- {e.name}: {summary_preview}")
            if len(type_entities) > display_count:
                lines.append(f"  ... {len(type_entities) - display_count} more")
        
        return "\n".join(lines)
    
    def _call_llm_with_retry(self, prompt: str, system_prompt: str) -> Dict[str, Any]:
        """Call the LLM with retries, including JSON repair"""
        import re
        
        max_attempts = 3
        last_error = None
        
        for attempt in range(max_attempts):
            try:
                response = self.client.chat.completions.create(
                    model=self.model_name,
                    messages=[
                        {"role": "system", "content": system_prompt},
                        {"role": "user", "content": prompt}
                    ],
                    response_format={"type": "json_object"},
                    temperature=0.7 - (attempt * 0.1)  # lower the temperature with every retry
                    # No max_tokens cap, so the LLM can write freely
                )
                
                content = response.choices[0].message.content
                finish_reason = response.choices[0].finish_reason
                
                # Check whether the output was truncated
                if finish_reason == 'length':
                    logger.warning(f"LLM output truncated (attempt {attempt+1})")
                    content = self._fix_truncated_json(content)
                
                # Try parsing the JSON
                try:
                    return json.loads(content)
                except json.JSONDecodeError as e:
                    logger.warning(f"JSON parsing failed (attempt {attempt+1}): {str(e)[:80]}")
                    
                    # Try repairing the JSON
                    fixed = self._try_fix_config_json(content)
                    if fixed:
                        return fixed
                    
                    last_error = e
                    
            except Exception as e:
                logger.warning(f"LLM call failed (attempt {attempt+1}): {str(e)[:80]}")
                last_error = e
                import time
                time.sleep(2 * (attempt + 1))
        
        raise last_error or Exception("The LLM call failed")
    
    def _fix_truncated_json(self, content: str) -> str:
        """Repair truncated JSON"""
        content = content.strip()
        
        # Count the unclosed brackets
        open_braces = content.count('{') - content.count('}')
        open_brackets = content.count('[') - content.count(']')
        
        # Check for an unclosed string
        if content and content[-1] not in '",}]':
            content += '"'
        
        # Close the brackets
        content += ']' * open_brackets
        content += '}' * open_braces
        
        return content
    
    def _try_fix_config_json(self, content: str) -> Optional[Dict[str, Any]]:
        """Try to repair the configuration JSON"""
        import re
        
        # Repair the truncated case
        content = self._fix_truncated_json(content)
        
        # Extract the JSON part
        json_match = re.search(r'\{[\s\S]*\}', content)
        if json_match:
            json_str = json_match.group()
            
            # Remove the newlines inside strings
            def fix_string(match):
                s = match.group(0)
                s = s.replace('\n', ' ').replace('\r', ' ')
                s = re.sub(r'\s+', ' ', s)
                return s
            
            json_str = re.sub(r'"[^"\\]*(?:\\.[^"\\]*)*"', fix_string, json_str)
            
            try:
                return json.loads(json_str)
            except:
                # Try removing every control character
                json_str = re.sub(r'[\x00-\x1f\x7f-\x9f]', ' ', json_str)
                json_str = re.sub(r'\s+', ' ', json_str)
                try:
                    return json.loads(json_str)
                except:
                    pass
        
        return None
    
    def _generate_time_config(self, context: str, num_entities: int) -> Dict[str, Any]:
        """Generate the time configuration"""
        # Use the configured context truncation length
        context_truncated = context[:self.TIME_CONFIG_CONTEXT_LENGTH]
        
        # Work out the maximum allowed value (80% of the Agent count)
        max_agents_allowed = max(1, int(num_entities * 0.9))
        
        prompt = f"""Based on the simulation goal below, generate the time configuration.

{context_truncated}

## Task
Respond with the time configuration as JSON.

### Guidelines (for reference only; adapt them to the event and the people involved):
- the simulated population behaves like a typical online crowd, on a 24-hour clock
- almost nobody is active between 00:00 and 05:00 (activity multiplier 0.05)
- activity picks up between 06:00 and 08:00 (multiplier 0.4)
- working hours, 09:00-18:00, are moderately active (multiplier 0.7)
- 19:00-22:00 is the evening peak (multiplier 1.5)
- activity falls off after 23:00 (multiplier 0.5)
- the general pattern: quiet at night, rising in the morning, moderate during the day, peak in the evening
- **Important**: the sample values below are only a starting point; adapt the hours to the nature of the event and the people involved
  - e.g. students may peak at 21:00-23:00; the media are active all day; official bodies only during working hours
  - e.g. a breaking scandal may keep people talking late into the night, so off_peak_hours can be shortened

### JSON format (no markdown)

Example:
{{
    "total_simulation_hours": 72,
    "minutes_per_round": 60,
    "agents_per_hour_min": 5,
    "agents_per_hour_max": 50,
    "peak_hours": [19, 20, 21, 22],
    "off_peak_hours": [0, 1, 2, 3, 4, 5],
    "morning_hours": [6, 7, 8],
    "work_hours": [9, 10, 11, 12, 13, 14, 15, 16, 17, 18],
    "reasoning": "why this time configuration suits the event"
}}

Fields:
- total_simulation_hours (int): the total simulation length, 24-168 hours; shorter for breaking news, longer for ongoing topics
- minutes_per_round (int): the length of one round, 30-120 minutes; 60 is recommended
- agents_per_hour_min (int): the minimum number of Agents activated per hour (1-{max_agents_allowed})
- agents_per_hour_max (int): the maximum number of Agents activated per hour (1-{max_agents_allowed})
- peak_hours (array of ints): the peak hours, adapted to the people involved in the event
- off_peak_hours (array of ints): the quiet hours, usually late at night
- morning_hours (array of ints): the morning hours
- work_hours (array of ints): the working hours
- reasoning (string): a short explanation of the configuration"""

        system_prompt = "You are a social media simulation expert. Respond with plain JSON only, and make the time configuration fit a realistic daily rhythm."
        
        try:
            return self._call_llm_with_retry(prompt, system_prompt)
        except Exception as e:
            logger.warning(f"LLM time configuration generation failed: {e}, using the default configuration")
            return self._get_default_time_config(num_entities)
    
    def _get_default_time_config(self, num_entities: int) -> Dict[str, Any]:
        """Return the default time configuration (a typical daily rhythm)"""
        return {
            "total_simulation_hours": 72,
            "minutes_per_round": 60,  # one hour per round, to speed the clock up
            "agents_per_hour_min": max(1, num_entities // 15),
            "agents_per_hour_max": max(5, num_entities // 5),
            "peak_hours": [19, 20, 21, 22],
            "off_peak_hours": [0, 1, 2, 3, 4, 5],
            "morning_hours": [6, 7, 8],
            "work_hours": [9, 10, 11, 12, 13, 14, 15, 16, 17, 18],
            "reasoning": "Using the default daily-rhythm configuration (one hour per round)"
        }
    
    def _parse_time_config(self, result: Dict[str, Any], num_entities: int) -> TimeSimulationConfig:
        """Parse the time configuration and make sure agents_per_hour never exceeds the Agent count"""
        # Read the raw values
        agents_per_hour_min = result.get("agents_per_hour_min", max(1, num_entities // 15))
        agents_per_hour_max = result.get("agents_per_hour_max", max(5, num_entities // 5))
        
        # Validate and correct them so they cannot exceed the Agent count
        if agents_per_hour_min > num_entities:
            logger.warning(f"agents_per_hour_min ({agents_per_hour_min}) exceeds the Agent count ({num_entities}), corrected")
            agents_per_hour_min = max(1, num_entities // 10)
        
        if agents_per_hour_max > num_entities:
            logger.warning(f"agents_per_hour_max ({agents_per_hour_max}) exceeds the Agent count ({num_entities}), corrected")
            agents_per_hour_max = max(agents_per_hour_min + 1, num_entities // 2)
        
        # Make sure min < max
        if agents_per_hour_min >= agents_per_hour_max:
            agents_per_hour_min = max(1, agents_per_hour_max // 2)
            logger.warning(f"agents_per_hour_min >= max, corrected to {agents_per_hour_min}")
        
        return TimeSimulationConfig(
            total_simulation_hours=result.get("total_simulation_hours", 72),
            minutes_per_round=result.get("minutes_per_round", 60),  # one hour per round by default
            agents_per_hour_min=agents_per_hour_min,
            agents_per_hour_max=agents_per_hour_max,
            peak_hours=result.get("peak_hours", [19, 20, 21, 22]),
            off_peak_hours=result.get("off_peak_hours", [0, 1, 2, 3, 4, 5]),
            off_peak_activity_multiplier=0.05,  # almost nobody active in the small hours
            morning_hours=result.get("morning_hours", [6, 7, 8]),
            morning_activity_multiplier=0.4,
            work_hours=result.get("work_hours", list(range(9, 19))),
            work_activity_multiplier=0.7,
            peak_activity_multiplier=1.5
        )
    
    def _generate_event_config(
        self, 
        context: str, 
        simulation_requirement: str,
        entities: List[EntityNode]
    ) -> Dict[str, Any]:
        """Generate the event configuration"""
        
        # Fetch the available entity types so the LLM can refer to them
        entity_types_available = list(set(
            e.get_entity_type() or "Unknown" for e in entities
        ))
        
        # List representative entity names for each type
        type_examples = {}
        for e in entities:
            etype = e.get_entity_type() or "Unknown"
            if etype not in type_examples:
                type_examples[etype] = []
            if len(type_examples[etype]) < 3:
                type_examples[etype].append(e.name)
        
        type_info = "\n".join([
            f"- {t}: {', '.join(examples)}" 
            for t, examples in type_examples.items()
        ])
        
        # Use the configured context truncation length
        context_truncated = context[:self.EVENT_CONFIG_CONTEXT_LENGTH]
        
        prompt = f"""Based on the simulation goal below, generate the event configuration.

Simulation goal: {simulation_requirement}

{context_truncated}

## Available entity types and examples
{type_info}

## Task
Respond with the event configuration as JSON:
- extract the trending topic keywords
- describe the direction public opinion will take
- design the initial posts, and **every post must name a poster_type**

**Important**: poster_type must come from the available entity types listed above, so each initial post can be handed to a suitable Agent. For example: an official statement comes from an Official/University type, news from MediaOutlet, student views from Student.
JSON format (no markdown):

"hot_topics": ["keyword 1", "keyword 2", ...],
{{
    "narrative_direction": "<how public opinion will develop>",
    {{"content": "the post text", "poster_type": "an entity type from the list above"}},
    "initial_posts": [
        "reasoning": "<short explanation>"
        ...
    ],
    system_prompt = "You are a public opinion analyst. Respond with plain JSON only. poster_type must exactly match one of the available entity types."
}}"""

        system_prompt = "You are a public opinion analyst. Respond with plain JSON only. poster_type must exactly match one of the available entity types."
        
        try:
            return self._call_llm_with_retry(prompt, system_prompt)
        except Exception as e:
            logger.warning(f"LLM event configuration generation failed: {e}, using the default configuration")
            return {
                "hot_topics": [],
                "narrative_direction": "",
                "initial_posts": [],
                "reasoning": "using the default configuration"
            }
    
    def _parse_event_config(self, result: Dict[str, Any]) -> EventConfig:
        """Parse the event configuration result"""
        return EventConfig(
            initial_posts=result.get("initial_posts", []),
            scheduled_events=[],
            hot_topics=result.get("hot_topics", []),
            narrative_direction=result.get("narrative_direction", "")
        )
    
    def _assign_initial_post_agents(
        self,
        event_config: EventConfig,
        agent_configs: List[AgentActivityConfig]
    ) -> EventConfig:
        """
        Matches the best agent_id for each post based on its poster_type
        
        # Index the agents by entity type
        """
        if not event_config.initial_posts:
            return event_config
        
        # Type alias table (handles the different formats the LLM may produce)
        agents_by_type: Dict[str, List[AgentActivityConfig]] = {}
        for agent in agent_configs:
            etype = agent.entity_type.lower()
            if etype not in agents_by_type:
                agents_by_type[etype] = []
            agents_by_type[etype].append(agent)
        
        # Track which agent indexes are already used per type, so one agent is not reused
        type_aliases = {
            "official": ["official", "university", "governmentagency", "government"],
            "university": ["university", "official"],
            "mediaoutlet": ["mediaoutlet", "media"],
            "student": ["student", "person"],
            "professor": ["professor", "expert", "teacher"],
            "alumni": ["alumni", "person"],
            "organization": ["organization", "ngo", "company", "group"],
            "person": ["person", "student", "alumni"],
        }
        
        # Try to find a matching agent
        used_indices: Dict[str, int] = {}
        
        updated_posts = []
        for post in event_config.initial_posts:
            poster_type = post.get("poster_type", "").lower()
            content = post.get("content", "")
            
            # 1. A direct match
            matched_agent_id = None
            
            # 2. A match through the aliases
            if poster_type in agents_by_type:
                agents = agents_by_type[poster_type]
                idx = used_indices.get(poster_type, 0) % len(agents)
                matched_agent_id = agents[idx].agent_id
                used_indices[poster_type] = idx + 1
            else:
                # 3. When nothing matches, use the most influential agent
                for alias_key, aliases in type_aliases.items():
                    if poster_type in aliases or alias_key == poster_type:
                        for alias in aliases:
                            if alias in agents_by_type:
                                agents = agents_by_type[alias]
                                idx = used_indices.get(alias, 0) % len(agents)
                                matched_agent_id = agents[idx].agent_id
                                used_indices[alias] = idx + 1
                                break
                    if matched_agent_id is not None:
                        break
            
            logger.warning(f"No Agent matches the type '{poster_type}', using the most influential Agent")
            if matched_agent_id is None:
                # Sort by influence and take the top one
                if agent_configs:
                    logger.info(f"Initial post assignment: poster_type='{poster_type}' -> agent_id={matched_agent_id}")
                    sorted_agents = sorted(agent_configs, key=lambda a: a.influence_weight, reverse=True)
                    matched_agent_id = sorted_agents[0].agent_id
                else:
                    matched_agent_id = 0
            
            updated_posts.append({
                "content": content,
                "poster_type": post.get("poster_type", "Unknown"),
                "poster_agent_id": matched_agent_id
            })
            
            """Generate the Agent configurations in batches"""
        
        event_config.initial_posts = updated_posts
        return event_config
    
    def _generate_agent_configs_batch(
        self,
        context: str,
        entities: List[EntityNode],
        start_idx: int,
        simulation_requirement: str
    ) -> List[AgentActivityConfig]:
        # Build the entity information (using the configured summary length)
        
        prompt = f"""Based on the information below, generate a social media activity configuration for each entity.
        entity_list = []
        summary_len = self.AGENT_SUMMARY_LENGTH
        for i, e in enumerate(entities):
            entity_list.append({
                "agent_id": start_idx + i,
                "entity_name": e.name,
                "entity_type": e.get_entity_type() or "Unknown",
                "summary": e.summary[:summary_len] if e.summary else ""
            })
        
        ## Entity list

Simulation goal: {simulation_requirement}

Generate an activity configuration for each entity. Note:
```json
{json.dumps(entity_list, ensure_ascii=False, indent=2)}
```

## Task
- **the hours should follow a realistic daily rhythm**: almost no activity between 00:00 and 05:00, most activity between 19:00 and 22:00
- **official bodies** (University/GovernmentAgency): low activity (0.1-0.3), active during working hours (9-17), slow to respond (60-240 minutes), high influence (2.5-3.0)
- **media** (MediaOutlet): medium activity (0.4-0.6), active all day (8-23), fast to respond (5-30 minutes), high influence (2.0-2.5)
- **individuals** (Student/Person/Alumni): high activity (0.6-0.9), mostly in the evening (18-23), fast to respond (1-15 minutes), low influence (0.8-1.2)
- **public figures/experts**: medium activity (0.4-0.6), medium-to-high influence (1.5-2.0)
"agent_id": <must match the input>,

"hot_topics": ["keyword 1", "keyword 2", ...],
{{
    "agent_configs": [
        {{
            "posts_per_hour": <posting frequency>,
            "activity_level": <0.0-1.0>,
            "comments_per_hour": <comment frequency>,
            "active_hours": [<the active hours, following a realistic daily rhythm>],
            "response_delay_min": <minimum response delay in minutes>,
            "response_delay_max": <maximum response delay in minutes>,
            "sentiment_bias": <-1.0 to 1.0>,
            "influence_weight": <influence weight>
            "stance": "<supportive/opposing/neutral/observer>",
            system_prompt = "You are a social media behavior analyst. Respond with plain JSON, and make the configuration fit a realistic daily rhythm."
        }},
        ...
    ]
}}"""

        logger.warning(f"LLM Agent configuration batch failed: {e}, falling back to the rule-based configuration")
        
        try:
            result = self._call_llm_with_retry(prompt, system_prompt)
            llm_configs = {cfg["agent_id"]: cfg for cfg in result.get("agent_configs", [])}
        except Exception as e:
            # Build the AgentActivityConfig object
            llm_configs = {}
        
        # Fall back to the rule-based configuration when the LLM produced nothing
        configs = []
        for i, entity in enumerate(entities):
            agent_id = start_idx + i
            cfg = llm_configs.get(agent_id, {})
            
            if not cfg:
                cfg = self._generate_agent_config_by_rule(entity)
            
            config = AgentActivityConfig(
                agent_id=agent_id,
                entity_uuid=entity.uuid,
                entity_name=entity.name,
                entity_type=entity.get_entity_type() or "Unknown",
                activity_level=cfg.get("activity_level", 0.5),
                posts_per_hour=cfg.get("posts_per_hour", 0.5),
                comments_per_hour=cfg.get("comments_per_hour", 1.0),
                active_hours=cfg.get("active_hours", list(range(9, 23))),
                response_delay_min=cfg.get("response_delay_min", 5),
                response_delay_max=cfg.get("response_delay_max", 60),
                sentiment_bias=cfg.get("sentiment_bias", 0.0),
                stance=cfg.get("stance", "neutral"),
                influence_weight=cfg.get("influence_weight", 1.0)
            )
            configs.append(config)
        
        return configs
    
    def _generate_agent_config_by_rule(self, entity: EntityNode) -> Dict[str, Any]:
        """Generate a single Agent configuration by rule (a typical daily rhythm)"""
        # Official bodies: active during working hours, low frequency, high influence
        entity_type = (entity.get_entity_type() or "Unknown").lower()
        
        if entity_type in ["university", "governmentagency", "ngo"]:
            # Official bodies: active during working hours, low frequency, high influence
            return {
                "activity_level": 0.2,
                "posts_per_hour": 0.1,
                "comments_per_hour": 0.05,
                "active_hours": list(range(9, 18)),  # 9:00-17:59
                "response_delay_min": 60,
                "response_delay_max": 240,
                "sentiment_bias": 0.0,
                "stance": "neutral",
                "influence_weight": 3.0
            }
        elif entity_type in ["mediaoutlet"]:
            # Media: active all day, medium frequency, high influence
            return {
                "activity_level": 0.5,
                "posts_per_hour": 0.8,
                "comments_per_hour": 0.3,
                "active_hours": list(range(7, 24)),  # 7:00-23:59
                "response_delay_min": 5,
                "response_delay_max": 30,
                "sentiment_bias": 0.0,
                "stance": "observer",
                "influence_weight": 2.5
            }
        elif entity_type in ["professor", "expert", "official"]:
            # Experts/professors: working hours plus evening, medium frequency
            return {
                "activity_level": 0.4,
                "posts_per_hour": 0.3,
                "comments_per_hour": 0.5,
                "active_hours": list(range(8, 22)),  # 8:00-21:59
                "response_delay_min": 15,
                "response_delay_max": 90,
                "sentiment_bias": 0.0,
                "stance": "neutral",
                "influence_weight": 2.0
            }
        elif entity_type in ["student"]:
            # Students: mostly the evening, high frequency
            return {
                "activity_level": 0.8,
                "posts_per_hour": 0.6,
                "comments_per_hour": 1.5,
                "active_hours": [8, 9, 10, 11, 12, 13, 18, 19, 20, 21, 22, 23],  # morning + evening
                "response_delay_min": 1,
                "response_delay_max": 15,
                "sentiment_bias": 0.0,
                "stance": "neutral",
                "influence_weight": 0.8
            }
        elif entity_type in ["alumni"]:
            # Alumni: mostly the evening
            return {
                "activity_level": 0.6,
                "posts_per_hour": 0.4,
                "comments_per_hour": 0.8,
                "active_hours": [12, 13, 19, 20, 21, 22, 23],  # lunch break + evening
                "response_delay_min": 5,
                "response_delay_max": 30,
                "sentiment_bias": 0.0,
                "stance": "neutral",
                "influence_weight": 1.0
            }
        else:
            # Ordinary people: the evening peak
            return {
                "activity_level": 0.7,
                "posts_per_hour": 0.5,
                "comments_per_hour": 1.2,
                "active_hours": [9, 10, 11, 12, 13, 18, 19, 20, 21, 22, 23],  # daytime + evening
                "response_delay_min": 2,
                "response_delay_max": 20,
                "sentiment_bias": 0.0,
                "stance": "neutral",
                "influence_weight": 1.0
            }
    

