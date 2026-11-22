"""
OASIS Agent Profile generator
Converts entities from the Zep graph into the Agent Profile format the OASIS simulation platform needs

Improvements:
1. Calls Zep retrieval to enrich each node a second time
2. Uses refined prompts to generate very detailed personas
3. Distinguishes individual entities from abstract group entities
"""

import json
import random
import time
from typing import Dict, Any, List, Optional
from dataclasses import dataclass, field
from datetime import datetime

from openai import OpenAI
from zep_cloud.client import Zep

from ..config import Config
from ..utils.logger import get_logger
from .zep_entity_reader import EntityNode, ZepEntityReader

logger = get_logger('mirofish.oasis_profile')


@dataclass
class OasisAgentProfile:
    """The OASIS Agent Profile data structure"""
    # Common fields
    user_id: int
    user_name: str
    name: str
    bio: str
    persona: str
    
    # Optional fields - Reddit style
    karma: int = 1000
    
    # Optional fields - Twitter style
    friend_count: int = 100
    follower_count: int = 150
    statuses_count: int = 500
    
    # Extra persona information
    age: Optional[int] = None
    gender: Optional[str] = None
    mbti: Optional[str] = None
    country: Optional[str] = None
    profession: Optional[str] = None
    interested_topics: List[str] = field(default_factory=list)
    
    # Source entity information
    source_entity_uuid: Optional[str] = None
    source_entity_type: Optional[str] = None
    
    created_at: str = field(default_factory=lambda: datetime.now().strftime("%Y-%m-%d"))
    
    def to_reddit_format(self) -> Dict[str, Any]:
        """Convert to the Reddit platform format"""
        profile = {
            "user_id": self.user_id,
            "username": self.user_name,  # the OASIS library expects the field to be called username (no underscore)
            "name": self.name,
            "bio": self.bio,
            "persona": self.persona,
            "karma": self.karma,
            "created_at": self.created_at,
        }
        
        # Add the extra persona information (when present)
        if self.age:
            profile["age"] = self.age
        if self.gender:
            profile["gender"] = self.gender
        if self.mbti:
            profile["mbti"] = self.mbti
        if self.country:
            profile["country"] = self.country
        if self.profession:
            profile["profession"] = self.profession
        if self.interested_topics:
            profile["interested_topics"] = self.interested_topics
        
        return profile
    
    def to_twitter_format(self) -> Dict[str, Any]:
        """Convert to the Twitter platform format"""
        profile = {
            "user_id": self.user_id,
            "username": self.user_name,  # the OASIS library expects the field to be called username (no underscore)
            "name": self.name,
            "bio": self.bio,
            "persona": self.persona,
            "friend_count": self.friend_count,
            "follower_count": self.follower_count,
            "statuses_count": self.statuses_count,
            "created_at": self.created_at,
        }
        
        # Add the extra persona information
        if self.age:
            profile["age"] = self.age
        if self.gender:
            profile["gender"] = self.gender
        if self.mbti:
            profile["mbti"] = self.mbti
        if self.country:
            profile["country"] = self.country
        if self.profession:
            profile["profession"] = self.profession
        if self.interested_topics:
            profile["interested_topics"] = self.interested_topics
        
        return profile
    
    def to_dict(self) -> Dict[str, Any]:
        """Convert to a full dictionary"""
        return {
            "user_id": self.user_id,
            "user_name": self.user_name,
            "name": self.name,
            "bio": self.bio,
            "persona": self.persona,
            "karma": self.karma,
            "friend_count": self.friend_count,
            "follower_count": self.follower_count,
            "statuses_count": self.statuses_count,
            "age": self.age,
            "gender": self.gender,
            "mbti": self.mbti,
            "country": self.country,
            "profession": self.profession,
            "interested_topics": self.interested_topics,
            "source_entity_uuid": self.source_entity_uuid,
            "source_entity_type": self.source_entity_type,
            "created_at": self.created_at,
        }


class OasisProfileGenerator:
    """
    OASIS Profile generator
    
    Converts entities from the Zep graph into the Agent Profiles the OASIS simulation needs
    
    Features:
    1. Calls Zep graph retrieval for a richer context
    2. Generates very detailed personas (basic information, career history, personality, social media behavior, ...)
    3. Distinguishes individual entities from abstract group entities
    """
    
    # The MBTI types
    MBTI_TYPES = [
        "INTJ", "INTP", "ENTJ", "ENTP",
        "INFJ", "INFP", "ENFJ", "ENFP",
        "ISTJ", "ISFJ", "ESTJ", "ESFJ",
        "ISTP", "ISFP", "ESTP", "ESFP"
    ]
    
    # Common countries
    COUNTRIES = [
        "China", "US", "UK", "Japan", "Germany", "France", 
        "Canada", "Australia", "Brazil", "India", "South Korea"
    ]
    
    # Individual entity types (these get a concrete persona)
    INDIVIDUAL_ENTITY_TYPES = [
        "student", "alumni", "professor", "person", "publicfigure", 
        "expert", "faculty", "official", "journalist", "activist"
    ]
    
    # Group/institution entity types (these get a representative persona)
    GROUP_ENTITY_TYPES = [
        "university", "governmentagency", "organization", "ngo", 
        "mediaoutlet", "company", "institution", "group", "community"
    ]
    
    def __init__(
        self, 
        api_key: Optional[str] = None,
        base_url: Optional[str] = None,
        model_name: Optional[str] = None,
        zep_api_key: Optional[str] = None,
        graph_id: Optional[str] = None
    ):
        self.api_key = api_key or Config.LLM_API_KEY
        self.base_url = base_url or Config.LLM_BASE_URL
        self.model_name = model_name or Config.LLM_MODEL_NAME
        
        if not self.api_key:
            raise ValueError("LLM_API_KEY is not configured")
        
        self.client = OpenAI(
            api_key=self.api_key,
            base_url=self.base_url
        )
        
        # Zep client for retrieving a richer context
        self.zep_api_key = zep_api_key or Config.ZEP_API_KEY
        self.zep_client = None
        self.graph_id = graph_id
        
        if self.zep_api_key:
            try:
                self.zep_client = Zep(api_key=self.zep_api_key)
            except Exception as e:
                logger.warning(f"Failed to initialize the Zep client: {e}")
    
    def generate_profile_from_entity(
        self, 
        entity: EntityNode, 
        user_id: int,
        use_llm: bool = True
    ) -> OasisAgentProfile:
        """
        Generate an OASIS Agent Profile from a Zep entity
        
        Args:
            entity: the Zep entity node
            user_id: the user ID (for OASIS)
            use_llm: whether to use the LLM to generate a detailed persona
            
        Returns:
            OasisAgentProfile
        """
        entity_type = entity.get_entity_type() or "Entity"
        
        # Basic information
        name = entity.name
        user_name = self._generate_username(name)
        
        # Build the context information
        context = self._build_entity_context(entity)
        
        if use_llm:
            # Use the LLM to generate a detailed persona
            profile_data = self._generate_profile_with_llm(
                entity_name=name,
                entity_type=entity_type,
                entity_summary=entity.summary,
                entity_attributes=entity.attributes,
                context=context
            )
        else:
            # Use the rule-based path to generate a basic persona
            profile_data = self._generate_profile_rule_based(
                entity_name=name,
                entity_type=entity_type,
                entity_summary=entity.summary,
                entity_attributes=entity.attributes
            )
        
        return OasisAgentProfile(
            user_id=user_id,
            user_name=user_name,
            name=name,
            bio=profile_data.get("bio", f"{entity_type}: {name}"),
            persona=profile_data.get("persona", entity.summary or f"A {entity_type} named {name}."),
            karma=profile_data.get("karma", random.randint(500, 5000)),
            friend_count=profile_data.get("friend_count", random.randint(50, 500)),
            follower_count=profile_data.get("follower_count", random.randint(100, 1000)),
            statuses_count=profile_data.get("statuses_count", random.randint(100, 2000)),
            age=profile_data.get("age"),
            gender=profile_data.get("gender"),
            mbti=profile_data.get("mbti"),
            country=profile_data.get("country"),
            profession=profile_data.get("profession"),
            interested_topics=profile_data.get("interested_topics", []),
            source_entity_uuid=entity.uuid,
            source_entity_type=entity_type,
        )
    
    def _generate_username(self, name: str) -> str:
        """Generate a username"""
        # Strip special characters and lowercase it
        username = name.lower().replace(" ", "_")
        username = ''.join(c for c in username if c.isalnum() or c == '_')
        
        # Add a random suffix to avoid duplicates
        suffix = random.randint(100, 999)
        return f"{username}_{suffix}"
    
    def _search_zep_for_entity(self, entity: EntityNode) -> Dict[str, Any]:
        """
        Use Zep graph hybrid search to pull rich information about the entity
        
        Zep has no built-in hybrid search endpoint, so edges and nodes are searched separately and the results merged.
        The two requests run in parallel to save time.
        
        Args:
            entity: the entity node object
            
        Returns:
            a dictionary with facts, node_summaries and context
        """
        import concurrent.futures
        
        if not self.zep_client:
            return {"facts": [], "node_summaries": [], "context": ""}
        
        entity_name = entity.name
        
        results = {
            "facts": [],
            "node_summaries": [],
            "context": ""
        }
        
        # A graph_id is required for searching
        if not self.graph_id:
            logger.debug(f"Skipping Zep retrieval: no graph_id set")
            return results
        
        comprehensive_query = f"All information, activity, events, relations and background about {entity_name}"
        
        def search_edges():
            """Search the edges (facts/relations) - with retries"""
            max_retries = 3
            last_exception = None
            delay = 2.0
            
            for attempt in range(max_retries):
                try:
                    return self.zep_client.graph.search(
                        query=comprehensive_query,
                        graph_id=self.graph_id,
                        limit=30,
                        scope="edges",
                        reranker="rrf"
                    )
                except Exception as e:
                    last_exception = e
                    if attempt < max_retries - 1:
                        logger.debug(f"Zep edge search failed on attempt {attempt + 1}: {str(e)[:80]}, retrying...")
                        time.sleep(delay)
                        delay *= 2
                    else:
                        logger.debug(f"Zep edge search still failing after {max_retries} attempts: {e}")
            return None
        
        def search_nodes():
            """Search the nodes (entity summaries) - with retries"""
            max_retries = 3
            last_exception = None
            delay = 2.0
            
            for attempt in range(max_retries):
                try:
                    return self.zep_client.graph.search(
                        query=comprehensive_query,
                        graph_id=self.graph_id,
                        limit=20,
                        scope="nodes",
                        reranker="rrf"
                    )
                except Exception as e:
                    last_exception = e
                    if attempt < max_retries - 1:
                        logger.debug(f"Zep node search failed on attempt {attempt + 1}: {str(e)[:80]}, retrying...")
                        time.sleep(delay)
                        delay *= 2
                    else:
                        logger.debug(f"Zep node search still failing after {max_retries} attempts: {e}")
            return None
        
        try:
            # Run the edge and node searches in parallel
            with concurrent.futures.ThreadPoolExecutor(max_workers=2) as executor:
                edge_future = executor.submit(search_edges)
                node_future = executor.submit(search_nodes)
                
                # Get the results
                edge_result = edge_future.result(timeout=30)
                node_result = node_future.result(timeout=30)
            
            # Process the edge search results
            all_facts = set()
            if edge_result and hasattr(edge_result, 'edges') and edge_result.edges:
                for edge in edge_result.edges:
                    if hasattr(edge, 'fact') and edge.fact:
                        all_facts.add(edge.fact)
            results["facts"] = list(all_facts)
            
            # Process the node search results
            all_summaries = set()
            if node_result and hasattr(node_result, 'nodes') and node_result.nodes:
                for node in node_result.nodes:
                    if hasattr(node, 'summary') and node.summary:
                        all_summaries.add(node.summary)
                    if hasattr(node, 'name') and node.name and node.name != entity_name:
                        all_summaries.add(f"Related entity: {node.name}")
            results["node_summaries"] = list(all_summaries)
            
            # Build the combined context
            context_parts = []
            if results["facts"]:
                context_parts.append("Factual information:\n" + "\n".join(f"- {f}" for f in results["facts"][:20]))
            if results["node_summaries"]:
                context_parts.append("Related entities:\n" + "\n".join(f"- {s}" for s in results["node_summaries"][:10]))
            results["context"] = "\n\n".join(context_parts)
            
            logger.info(f"Zep hybrid retrieval complete: {entity_name}, got {len(results['facts'])} facts, {len(results['node_summaries'])} related nodes")
            
        except concurrent.futures.TimeoutError:
            logger.warning(f"Zep retrieval timed out ({entity_name})")
        except Exception as e:
            logger.warning(f"Zep retrieval failed ({entity_name}): {e}")
        
        return results
    
    def _build_entity_context(self, entity: EntityNode) -> str:
        """
        Build the full context information for an entity
        
        Includes:
        1. The entity's own edge information (facts)
        2. Detailed information about the related nodes
        3. The richer information Zep hybrid search returned
        """
        context_parts = []
        
        # 1. Add the entity's attributes
        if entity.attributes:
            attrs = []
            for key, value in entity.attributes.items():
                if value and str(value).strip():
                    attrs.append(f"- {key}: {value}")
            if attrs:
                context_parts.append("### Entity attributes\n" + "\n".join(attrs))
        
        # 2. Add the related edge information (facts/relations)
        existing_facts = set()
        if entity.related_edges:
            relationships = []
            for edge in entity.related_edges:  # no limit
                fact = edge.get("fact", "")
                edge_name = edge.get("edge_name", "")
                direction = edge.get("direction", "")
                
                if fact:
                    relationships.append(f"- {fact}")
                    existing_facts.add(fact)
                elif edge_name:
                    if direction == "outgoing":
                        relationships.append(f"- {entity.name} --[{edge_name}]--> (related entity)")
                    else:
                        relationships.append(f"- (related entity) --[{edge_name}]--> {entity.name}")
            
            if relationships:
                context_parts.append("### Related facts and relations\n" + "\n".join(relationships))
        
        # 3. Add the detailed information about the related nodes
        if entity.related_nodes:
            related_info = []
            for node in entity.related_nodes:  # no limit
                node_name = node.get("name", "")
                node_labels = node.get("labels", [])
                node_summary = node.get("summary", "")
                
                # Filter out the default labels
                custom_labels = [l for l in node_labels if l not in ["Entity", "Node"]]
                label_str = f" ({', '.join(custom_labels)})" if custom_labels else ""
                
                if node_summary:
                    related_info.append(f"- **{node_name}**{label_str}: {node_summary}")
                else:
                    related_info.append(f"- **{node_name}**{label_str}")
            
            if related_info:
                context_parts.append("### Related entity information\n" + "\n".join(related_info))
        
        # 4. Use Zep hybrid retrieval for even richer information
        zep_results = self._search_zep_for_entity(entity)
        
        if zep_results.get("facts"):
            # De-duplicate: skip facts that are already present
            new_facts = [f for f in zep_results["facts"] if f not in existing_facts]
            if new_facts:
                context_parts.append("### Facts found by Zep retrieval\n" + "\n".join(f"- {f}" for f in new_facts[:15]))
        
        if zep_results.get("node_summaries"):
            context_parts.append("### Related nodes found by Zep retrieval\n" + "\n".join(f"- {s}" for s in zep_results["node_summaries"][:10]))
        
        return "\n\n".join(context_parts)
    
    def _is_individual_entity(self, entity_type: str) -> bool:
        """Check whether this is an individual entity"""
        return entity_type.lower() in self.INDIVIDUAL_ENTITY_TYPES
    
    def _is_group_entity(self, entity_type: str) -> bool:
        """Check whether this is a group/institution entity"""
        return entity_type.lower() in self.GROUP_ENTITY_TYPES
    
    def _generate_profile_with_llm(
        self,
        entity_name: str,
        entity_type: str,
        entity_summary: str,
        entity_attributes: Dict[str, Any],
        context: str
    ) -> Dict[str, Any]:
        """
        Use the LLM to generate a very detailed persona
        
        The output depends on the entity type:
        - Individual entity: a concrete character
        - Group/institution entity: a representative account
        """
        
        is_individual = self._is_individual_entity(entity_type)
        
        if is_individual:
            prompt = self._build_individual_persona_prompt(
                entity_name, entity_type, entity_summary, entity_attributes, context
            )
        else:
            prompt = self._build_group_persona_prompt(
                entity_name, entity_type, entity_summary, entity_attributes, context
            )

        # Retry until it succeeds or the maximum number of attempts is reached
        max_attempts = 3
        last_error = None
        
        for attempt in range(max_attempts):
            try:
                response = self.client.chat.completions.create(
                    model=self.model_name,
                    messages=[
                        {"role": "system", "content": self._get_system_prompt(is_individual)},
                        {"role": "user", "content": prompt}
                    ],
                    response_format={"type": "json_object"},
                    temperature=0.7 - (attempt * 0.1)  # lower the temperature with every retry
                    # No max_tokens cap, so the LLM can write freely
                )
                
                content = response.choices[0].message.content
                
                # Check whether it was truncated (finish_reason is not 'stop')
                finish_reason = response.choices[0].finish_reason
                if finish_reason == 'length':
                    logger.warning(f"LLM output truncated (attempt {attempt+1}), trying to repair it...")
                    content = self._fix_truncated_json(content)
                
                # Try to parse the JSON
                try:
                    result = json.loads(content)
                    
                    # Validate the required fields
                    if "bio" not in result or not result["bio"]:
                        result["bio"] = entity_summary[:200] if entity_summary else f"{entity_type}: {entity_name}"
                    if "persona" not in result or not result["persona"]:
                        result["persona"] = entity_summary or f"{entity_name} is a {entity_type}."
                    
                    return result
                    
                except json.JSONDecodeError as je:
                    logger.warning(f"JSON parsing failed (attempt {attempt+1}): {str(je)[:80]}")
                    
                    # Try to repair the JSON
                    result = self._try_fix_json(content, entity_name, entity_type, entity_summary)
                    if result.get("_fixed"):
                        del result["_fixed"]
                        return result
                    
                    last_error = je
                    
            except Exception as e:
                logger.warning(f"LLM call failed (attempt {attempt+1}): {str(e)[:80]}")
                last_error = e
                import time
                time.sleep(1 * (attempt + 1))  # exponential backoff
        
        logger.warning(f"LLM persona generation failed ({max_attempts} attempts): {last_error}, falling back to the rule-based persona")
        return self._generate_profile_rule_based(
            entity_name, entity_type, entity_summary, entity_attributes
        )
    
    def _fix_truncated_json(self, content: str) -> str:
        """Repair truncated JSON (output cut off by max_tokens)"""
        import re
        
        # If the JSON was truncated, try to close it
        content = content.strip()
        
        # Count the unclosed brackets
        open_braces = content.count('{') - content.count('}')
        open_brackets = content.count('[') - content.count(']')
        
        # Check for an unclosed string
        # Quick check: if the last quote has no comma or closing bracket after it, the string may be truncated
        if content and content[-1] not in '",}]':
            # Try to close the string
            content += '"'
        
        # Close the brackets
        content += ']' * open_brackets
        content += '}' * open_braces
        
        return content
    
    def _try_fix_json(self, content: str, entity_name: str, entity_type: str, entity_summary: str = "") -> Dict[str, Any]:
        """Try to repair broken JSON"""
        import re
        
        # 1. First try repairing the truncated case
        content = self._fix_truncated_json(content)
        
        # 2. Try extracting the JSON part
        json_match = re.search(r'\{[\s\S]*\}', content)
        if json_match:
            json_str = json_match.group()
            
            # 3. Handle the newline problem inside strings
            # Find every string value and replace the newlines inside it
            def fix_string_newlines(match):
                s = match.group(0)
                # Replace real newlines inside a string with a space
                s = s.replace('\n', ' ').replace('\r', ' ')
                # Collapse the extra spaces
                s = re.sub(r'\s+', ' ', s)
                return s
            
            # Match the JSON string values
            json_str = re.sub(r'"[^"\\]*(?:\\.[^"\\]*)*"', fix_string_newlines, json_str)
            
            # 4. Try parsing
            try:
                result = json.loads(json_str)
                result["_fixed"] = True
                return result
            except json.JSONDecodeError as e:
                # 5. If that still fails, try a more aggressive repair
                try:
                    # Remove every control character
                    json_str = re.sub(r'[\x00-\x1f\x7f-\x9f]', ' ', json_str)
                    # Collapse every run of whitespace
                    json_str = re.sub(r'\s+', ' ', json_str)
                    result = json.loads(json_str)
                    result["_fixed"] = True
                    return result
                except:
                    pass
        
        # 6. Try extracting partial information from the content
        bio_match = re.search(r'"bio"\s*:\s*"([^"]*)"', content)
        persona_match = re.search(r'"persona"\s*:\s*"([^"]*)', content)  # may be truncated
        
        bio = bio_match.group(1) if bio_match else (entity_summary[:200] if entity_summary else f"{entity_type}: {entity_name}")
        persona = persona_match.group(1) if persona_match else (entity_summary or f"{entity_name} is a {entity_type}.")
        
        # When something meaningful was extracted, mark it as repaired
        if bio_match or persona_match:
            logger.info(f"Extracted partial information from the broken JSON")
            return {
                "bio": bio,
                "persona": persona,
                "_fixed": True
            }
        
        # 7. It is a total failure, so return the basic structure
        logger.warning(f"JSON repair failed, returning the basic structure")
        return {
            "bio": entity_summary[:200] if entity_summary else f"{entity_type}: {entity_name}",
            "persona": entity_summary or f"{entity_name} is a {entity_type}."
        }
    
    def _get_system_prompt(self, is_individual: bool) -> str:
        """Get the system prompt"""
        base_prompt = "You are an expert at writing social media user profiles. Generate a detailed, realistic persona for opinion simulation that reproduces the known real-world situation as closely as possible. Respond with valid JSON only, and never put an unescaped newline inside a string value. Write everything in English."
        return base_prompt
    
    def _build_individual_persona_prompt(
        self,
        entity_name: str,
        entity_type: str,
        entity_summary: str,
        entity_attributes: Dict[str, Any],
        context: str
    ) -> str:
        """Build the detailed persona prompt for an individual entity"""
        
        attrs_str = json.dumps(entity_attributes, ensure_ascii=False) if entity_attributes else "none"
        context_str = context[:3000] if context else "no extra context"
        
        return f"""Generate a detailed social media persona for this entity that reproduces the known real-world situation as closely as possible.

Entity name: {entity_name}
Entity type: {entity_type}
Entity summary: {entity_summary}
Entity attributes: {attrs_str}

Context:
{context_str}

Respond with JSON containing these fields:

1. bio: a social media bio, about 200 characters
2. persona: a detailed persona description (about 2000 words of plain text) covering:
   - basic information (age, profession, education, location)
   - background (key experiences, how they relate to the event, social ties)
   - personality (MBTI type, core traits, how they express emotion)
   - social media behavior (posting frequency, content preferences, interaction style, language habits)
   - stance and opinions (attitude to the topic, what would provoke or move them)
   - distinctive details (catchphrases, unusual experiences, personal interests)
   - personal memory (an important part of the persona: how this individual relates to the event, and what they have already done and said about it)
3. age: a number (must be an integer)
4. gender: must be "male" or "female"
5. mbti: the MBTI type (e.g. INTJ, ENFP)
6. country: the country name
7. profession: the occupation
8. interested_topics: an array of topics they care about

Important:
- every field value must be a string or a number, with no newlines
- persona must be one continuous piece of prose
- write in English (the gender field is one of the English words male/female)
- the content must stay consistent with the entity information
- age must be a valid integer and gender must be "male" or "female"
"""

    def _build_group_persona_prompt(
        self,
        entity_name: str,
        entity_type: str,
        entity_summary: str,
        entity_attributes: Dict[str, Any],
        context: str
    ) -> str:
        """Build the detailed persona prompt for a group/institution entity"""
        
        attrs_str = json.dumps(entity_attributes, ensure_ascii=False) if entity_attributes else "none"
        context_str = context[:3000] if context else "no extra context"
        
        return f"""Generate a detailed social media account profile for this institution/group entity that reproduces the known real-world situation as closely as possible.

Entity name: {entity_name}
Entity type: {entity_type}
Entity summary: {entity_summary}
Entity attributes: {attrs_str}

Context:
{context_str}

Respond with JSON containing these fields:

1. bio: an official account bio, about 200 characters, professional and appropriate
2. persona: a detailed account description (about 2000 words of plain text) covering:
   - basic institution information (official name, nature of the institution, founding background, main functions)
   - account positioning (account type, target audience, core purpose)
   - voice (language habits, typical phrasing, taboo subjects)
   - content characteristics (content types, posting frequency, active hours)
   - stance (the official position on the core topic, how controversy is handled)
   - notes (the group it represents, operating habits)
   - institutional memory (an important part of the persona: how this institution relates to the event, and what it has already done and said about it)
3. age: always 30 (the virtual age of an institutional account)
4. gender: always "other" (institutional accounts use other to mean non-personal)
5. mbti: an MBTI type describing the account's style, e.g. ISTJ for careful and conservative
6. country: the country name
7. profession: a description of the institution's function
8. interested_topics: an array of fields it follows

Important:
- every field value must be a string or a number, with no null values
- persona must be one continuous piece of prose, with no newlines
- write in English (the gender field is the English word "other")
- age must be the integer 30 and gender must be the string "other"
- everything the account says must fit its position"""
    
    def _generate_profile_rule_based(
        self,
        entity_name: str,
        entity_type: str,
        entity_summary: str,
        entity_attributes: Dict[str, Any]
    ) -> Dict[str, Any]:
        """Generate a basic persona with rules"""
        
        # Generate a different persona depending on the entity type
        entity_type_lower = entity_type.lower()
        
        if entity_type_lower in ["student", "alumni"]:
            return {
                "bio": f"{entity_type} with interests in academics and social issues.",
                "persona": f"{entity_name} is a {entity_type.lower()} who is actively engaged in academic and social discussions. They enjoy sharing perspectives and connecting with peers.",
                "age": random.randint(18, 30),
                "gender": random.choice(["male", "female"]),
                "mbti": random.choice(self.MBTI_TYPES),
                "country": random.choice(self.COUNTRIES),
                "profession": "Student",
                "interested_topics": ["Education", "Social Issues", "Technology"],
            }
        
        elif entity_type_lower in ["publicfigure", "expert", "faculty"]:
            return {
                "bio": f"Expert and thought leader in their field.",
                "persona": f"{entity_name} is a recognized {entity_type.lower()} who shares insights and opinions on important matters. They are known for their expertise and influence in public discourse.",
                "age": random.randint(35, 60),
                "gender": random.choice(["male", "female"]),
                "mbti": random.choice(["ENTJ", "INTJ", "ENTP", "INTP"]),
                "country": random.choice(self.COUNTRIES),
                "profession": entity_attributes.get("occupation", "Expert"),
                "interested_topics": ["Politics", "Economics", "Culture & Society"],
            }
        
        elif entity_type_lower in ["mediaoutlet", "socialmediaplatform"]:
            return {
                "bio": f"Official account for {entity_name}. News and updates.",
                "persona": f"{entity_name} is a media entity that reports news and facilitates public discourse. The account shares timely updates and engages with the audience on current events.",
                "age": 30,  # the virtual age of an institution
                "gender": "other",  # institutions use other
                "mbti": "ISTJ",  # institutional style: careful and conservative
                "country": "China",
                "profession": "Media",
                "interested_topics": ["General News", "Current Events", "Public Affairs"],
            }
        
        elif entity_type_lower in ["university", "governmentagency", "ngo", "organization"]:
            return {
                "bio": f"Official account of {entity_name}.",
                "persona": f"{entity_name} is an institutional entity that communicates official positions, announcements, and engages with stakeholders on relevant matters.",
                "age": 30,  # the virtual age of an institution
                "gender": "other",  # institutions use other
                "mbti": "ISTJ",  # institutional style: careful and conservative
                "country": "China",
                "profession": entity_type,
                "interested_topics": ["Public Policy", "Community", "Official Announcements"],
            }
        
        else:
            # The default persona
            return {
                "bio": entity_summary[:150] if entity_summary else f"{entity_type}: {entity_name}",
                "persona": entity_summary or f"{entity_name} is a {entity_type.lower()} participating in social discussions.",
                "age": random.randint(25, 50),
                "gender": random.choice(["male", "female"]),
                "mbti": random.choice(self.MBTI_TYPES),
                "country": random.choice(self.COUNTRIES),
                "profession": entity_type,
                "interested_topics": ["General", "Social Issues"],
            }
    
    def set_graph_id(self, graph_id: str):
        """Set the graph ID used for Zep retrieval"""
        self.graph_id = graph_id
    
    def generate_profiles_from_entities(
        self,
        entities: List[EntityNode],
        use_llm: bool = True,
        progress_callback: Optional[callable] = None,
        graph_id: Optional[str] = None,
        parallel_count: int = 5,
        realtime_output_path: Optional[str] = None,
        output_platform: str = "reddit"
    ) -> List[OasisAgentProfile]:
        """
        Generate Agent Profiles from entities in bulk (with parallel generation)
        
        Args:
            entities: the list of entities
            use_llm: whether to use the LLM to generate a detailed persona
            progress_callback: a progress callback (current, total, message)
            graph_id: the graph ID, used by Zep retrieval for a richer context
            parallel_count: how many to generate in parallel, default 5
            realtime_output_path: the file to stream into (when given, every profile is written as soon as it exists)
            output_platform: the output platform format ("reddit" or "twitter")
            
        Returns:
            the list of Agent Profiles
        """
        import concurrent.futures
        from threading import Lock
        
        # Set the graph_id used for Zep retrieval
        if graph_id:
            self.graph_id = graph_id
        
        total = len(entities)
        profiles = [None] * total  # preallocate the list so the order is kept
        completed_count = [0]  # a list so the closure can update it
        lock = Lock()
        
        # Helper that writes to the file as the profiles are produced
        def save_profiles_realtime():
            """Save the profiles generated so far to the file as they arrive"""
            if not realtime_output_path:
                return
            
            with lock:
                # Keep only the profiles that exist already
                existing_profiles = [p for p in profiles if p is not None]
                if not existing_profiles:
                    return
                
                try:
                    if output_platform == "reddit":
                        # Reddit JSON format
                        profiles_data = [p.to_reddit_format() for p in existing_profiles]
                        with open(realtime_output_path, 'w', encoding='utf-8') as f:
                            json.dump(profiles_data, f, ensure_ascii=False, indent=2)
                    else:
                        # Twitter CSV format
                        import csv
                        profiles_data = [p.to_twitter_format() for p in existing_profiles]
                        if profiles_data:
                            fieldnames = list(profiles_data[0].keys())
                            with open(realtime_output_path, 'w', encoding='utf-8', newline='') as f:
                                writer = csv.DictWriter(f, fieldnames=fieldnames)
                                writer.writeheader()
                                writer.writerows(profiles_data)
                except Exception as e:
                    logger.warning(f"Failed to stream-save the profiles: {e}")
        
        def generate_single_profile(idx: int, entity: EntityNode) -> tuple:
            """Worker function that generates a single profile"""
            entity_type = entity.get_entity_type() or "Entity"
            
            try:
                profile = self.generate_profile_from_entity(
                    entity=entity,
                    user_id=idx,
                    use_llm=use_llm
                )
                
                # Stream the generated persona to the console and the log
                self._print_generated_profile(entity.name, entity_type, profile)
                
                return idx, profile, None
                
            except Exception as e:
                logger.error(f"Failed to generate a persona for entity {entity.name}: {str(e)}")
                # Create a basic profile
                fallback_profile = OasisAgentProfile(
                    user_id=idx,
                    user_name=self._generate_username(entity.name),
                    name=entity.name,
                    bio=f"{entity_type}: {entity.name}",
                    persona=entity.summary or f"A participant in social discussions.",
                    source_entity_uuid=entity.uuid,
                    source_entity_type=entity_type,
                )
                return idx, fallback_profile, str(e)
        
        logger.info(f"Generating {total} Agent personas in parallel (parallelism: {parallel_count})...")
        print(f"\n{'='*60}")
        print(f"Generating Agent personas - {total} entities in total, parallelism: {parallel_count}")
        print(f"{'='*60}\n")
        
        # Run them in parallel with a thread pool
        with concurrent.futures.ThreadPoolExecutor(max_workers=parallel_count) as executor:
            # Submit every task
            future_to_entity = {
                executor.submit(generate_single_profile, idx, entity): (idx, entity)
                for idx, entity in enumerate(entities)
            }
            
            # Collect the results
            for future in concurrent.futures.as_completed(future_to_entity):
                idx, entity = future_to_entity[future]
                entity_type = entity.get_entity_type() or "Entity"
                
                try:
                    result_idx, profile, error = future.result()
                    profiles[result_idx] = profile
                    
                    with lock:
                        completed_count[0] += 1
                        current = completed_count[0]
                    
                    # Stream to the file
                    save_profiles_realtime()
                    
                    if progress_callback:
                        progress_callback(
                            current, 
                            total, 
                            f"Completed {current}/{total}: {entity.name} ({entity_type})"
                        )
                    
                    if error:
                        logger.warning(f"[{current}/{total}] using the fallback persona for {entity.name}: {error}")
                    else:
                        logger.info(f"[{current}/{total}] generated a persona: {entity.name} ({entity_type})")
                        
                except Exception as e:
                    logger.error(f"Error while handling entity {entity.name}: {str(e)}")
                    with lock:
                        completed_count[0] += 1
                    profiles[idx] = OasisAgentProfile(
                        user_id=idx,
                        user_name=self._generate_username(entity.name),
                        name=entity.name,
                        bio=f"{entity_type}: {entity.name}",
                        persona=entity.summary or "A participant in social discussions.",
                        source_entity_uuid=entity.uuid,
                        source_entity_type=entity_type,
                    )
                    # Stream to the file (even the fallback personas)
                    save_profiles_realtime()
        
        print(f"\n{'='*60}")
        print(f"Persona generation complete! {len([p for p in profiles if p])} Agents generated")
        print(f"{'='*60}\n")
        
        return profiles
    
    def _print_generated_profile(self, entity_name: str, entity_type: str, profile: OasisAgentProfile):
        """Stream a generated persona to the console (full content, untruncated)"""
        separator = "-" * 70
        
        # Build the full output (untruncated)
        topics_str = ', '.join(profile.interested_topics) if profile.interested_topics else 'none'
        
        output_lines = [
            f"\n{separator}",
            f"[generated] {entity_name} ({entity_type})",
            f"{separator}",
            f"Username: {profile.user_name}",
            f"",
            f"[Bio]",
            f"{profile.bio}",
            f"",
            f"[Detailed persona]",
            f"{profile.persona}",
            f"",
            f"[Basic attributes]",
            f"Age: {profile.age} | Gender: {profile.gender} | MBTI: {profile.mbti}",
            f"Profession: {profile.profession} | Country: {profile.country}",
            f"Interested topics: {topics_str}",
            separator
        ]
        
        output = "\n".join(output_lines)
        
        # Console only (the logger no longer repeats the full content)
        print(output)
    
    def save_profiles(
        self,
        profiles: List[OasisAgentProfile],
        file_path: str,
        platform: str = "reddit"
    ):
        """
        Save the profiles to a file (in the right format for the platform)
        
        OASIS platform format requirements:
        - Twitter: CSV
        - Reddit: JSON
        
        Args:
            profiles: the list of profiles
            file_path: the file path
            platform: the platform type ("reddit" or "twitter")
        """
        if platform == "twitter":
            self._save_twitter_csv(profiles, file_path)
        else:
            self._save_reddit_json(profiles, file_path)
    
    def _save_twitter_csv(self, profiles: List[OasisAgentProfile], file_path: str):
        """
        Save the Twitter profiles as CSV (as OASIS requires)
        
        The CSV columns OASIS expects for Twitter:
        - user_id: the user ID (0, 1, 2, ... in CSV order)
        - name: the user's real name
        - username: the handle used inside the system
        - user_char: the detailed persona (injected into the LLM system prompt to steer the Agent)
        - description: the short public bio (shown on the profile page)
        
        user_char vs description:
        - user_char: internal, part of the LLM system prompt, decides how the Agent thinks and acts
        - description: external, the bio other users see
        """
        import csv
        
        # Make sure the extension is .csv
        if not file_path.endswith('.csv'):
            file_path = file_path.replace('.json', '.csv')
        
        with open(file_path, 'w', newline='', encoding='utf-8') as f:
            writer = csv.writer(f)
            
            # Write the CSV header OASIS expects
            headers = ['user_id', 'name', 'username', 'user_char', 'description']
            writer.writerow(headers)
            
            # Write the data rows
            for idx, profile in enumerate(profiles):
                # user_char: the full persona (bio + persona), used in the LLM system prompt
                user_char = profile.bio
                if profile.persona and profile.persona != profile.bio:
                    user_char = f"{profile.bio} {profile.persona}"
                # Handle newlines (replaced with spaces in the CSV)
                user_char = user_char.replace('\n', ' ').replace('\r', ' ')
                
                # description: the short bio, shown externally
                description = profile.bio.replace('\n', ' ').replace('\r', ' ')
                
                row = [
                    idx,                    # user_id: a sequential ID starting at 0
                    profile.name,           # name: the real name
                    profile.user_name,      # username: the handle
                    user_char,              # user_char: the full persona (internal, for the LLM)
                    description             # description: the short bio (shown externally)
                ]
                writer.writerow(row)
        
        logger.info(f"Saved {len(profiles)} Twitter profiles to {file_path} (OASIS CSV format)")
    
    def _normalize_gender(self, gender: Optional[str]) -> str:
        """
        Normalize the gender field to the English values OASIS expects
        
        OASIS expects: male, female, other
        """
        if not gender:
            return "other"
        
        gender_lower = gender.lower().strip()
        
        # Chinese input mapping
        gender_map = {
            "male": "male",
            "female": "female",
            "institution": "other",
            "other": "other",
            # Already English
            "male": "male",
            "female": "female",
            "other": "other",
        }
        
        return gender_map.get(gender_lower, "other")
    
    def _save_reddit_json(self, profiles: List[OasisAgentProfile], file_path: str):
        """
        Save the Reddit profiles as JSON
        
        Uses the same format as to_reddit_format() so OASIS can read it.
        The user_id field is required: it is what OASIS agent_graph.get_agent() matches on!
        
        Required fields:
        - user_id: the user ID (an integer, matching poster_agent_id in initial_posts)
        - username: the handle
        - name: the display name
        - bio: the bio
        - persona: the detailed persona
        - age: the age (an integer)
        - gender: "male", "female" or "other"
        - mbti: the MBTI type
        - country: the country
        """
        data = []
        for idx, profile in enumerate(profiles):
            # Use the same format as to_reddit_format()
            item = {
                "user_id": profile.user_id if profile.user_id is not None else idx,  # important: user_id is required
                "username": profile.user_name,
                "name": profile.name,
                "bio": profile.bio[:150] if profile.bio else f"{profile.name}",
                "persona": profile.persona or f"{profile.name} is a participant in social discussions.",
                "karma": profile.karma if profile.karma else 1000,
                "created_at": profile.created_at,
                # The fields OASIS requires - make sure each has a default
                "age": profile.age if profile.age else 30,
                "gender": self._normalize_gender(profile.gender),
                "mbti": profile.mbti if profile.mbti else "ISTJ",
                "country": profile.country if profile.country else "China",
            }
            
            # Optional fields
            if profile.profession:
                item["profession"] = profile.profession
            if profile.interested_topics:
                item["interested_topics"] = profile.interested_topics
            
            data.append(item)
        
        with open(file_path, 'w', encoding='utf-8') as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        
        logger.info(f"Saved {len(profiles)} Reddit profiles to {file_path} (JSON format, with the user_id field)")
    
    # Keep the old method name as an alias for backward compatibility
    def save_profiles_to_json(
        self,
        profiles: List[OasisAgentProfile],
        file_path: str,
        platform: str = "reddit"
    ):
        """[Deprecated] use save_profiles() instead"""
        logger.warning("save_profiles_to_json is deprecated, use save_profiles instead")
        self.save_profiles(profiles, file_path, platform)

