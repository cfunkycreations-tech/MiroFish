"""
Zep retrieval tool service
Wraps graph search, node reading, edge querying and other tools for the Report Agent

Core retrieval tools (after optimization):
1. InsightForge (deep insight retrieval) - the most powerful hybrid retrieval; generates sub-questions automatically and searches along several dimensions
2. PanoramaSearch (broad search) - gives the whole picture, including expired content
3. QuickSearch (simple search) - fast retrieval
"""

import time
import json
from typing import Dict, Any, List, Optional
from dataclasses import dataclass, field

from zep_cloud.client import Zep

from ..config import Config
from ..utils.logger import get_logger
from ..utils.llm_client import LLMClient
from ..utils.zep_paging import fetch_all_nodes, fetch_all_edges

logger = get_logger('mirofish.zep_tools')


@dataclass
class SearchResult:
    """Search results"""
    facts: List[str]
    edges: List[Dict[str, Any]]
    nodes: List[Dict[str, Any]]
    query: str
    total_count: int
    
    def to_dict(self) -> Dict[str, Any]:
        return {
            "facts": self.facts,
            "edges": self.edges,
            "nodes": self.nodes,
            "query": self.query,
            "total_count": self.total_count
        }
    
    def to_text(self) -> str:
        """Convert to a text format the LLM can read"""
        text_parts = [f"Search query: {self.query}", f"Found {self.total_count} related results"]
        
        if self.facts:
            text_parts.append("\n### Related facts:")
            for i, fact in enumerate(self.facts, 1):
                text_parts.append(f"{i}. {fact}")
        
        return "\n".join(text_parts)


@dataclass
class NodeInfo:
    """Node information"""
    uuid: str
    name: str
    labels: List[str]
    summary: str
    attributes: Dict[str, Any]
    
    def to_dict(self) -> Dict[str, Any]:
        return {
            "uuid": self.uuid,
            "name": self.name,
            "labels": self.labels,
            "summary": self.summary,
            "attributes": self.attributes
        }
    
    def to_text(self) -> str:
        """Convert to a text format"""
        entity_type = next((l for l in self.labels if l not in ["Entity", "Node"]), "Unknown type")
        return f"Entity: {self.name} (type: {entity_type})\nSummary: {self.summary}"


@dataclass
class EdgeInfo:
    """Edge information"""
    uuid: str
    name: str
    fact: str
    source_node_uuid: str
    target_node_uuid: str
    source_node_name: Optional[str] = None
    target_node_name: Optional[str] = None
    # Temporal information
    created_at: Optional[str] = None
    valid_at: Optional[str] = None
    invalid_at: Optional[str] = None
    expired_at: Optional[str] = None
    
    def to_dict(self) -> Dict[str, Any]:
        return {
            "uuid": self.uuid,
            "name": self.name,
            "fact": self.fact,
            "source_node_uuid": self.source_node_uuid,
            "target_node_uuid": self.target_node_uuid,
            "source_node_name": self.source_node_name,
            "target_node_name": self.target_node_name,
            "created_at": self.created_at,
            "valid_at": self.valid_at,
            "invalid_at": self.invalid_at,
            "expired_at": self.expired_at
        }
    
    def to_text(self, include_temporal: bool = False) -> str:
        """Convert to a text format"""
        source = self.source_node_name or self.source_node_uuid[:8]
        target = self.target_node_name or self.target_node_uuid[:8]
        base_text = f"Relation: {source} --[{self.name}]--> {target}\nFact: {self.fact}"
        
        if include_temporal:
            valid_at = self.valid_at or "Unknown"
            invalid_at = self.invalid_at or "Present"
            base_text += f"\nValidity: {valid_at} - {invalid_at}"
            if self.expired_at:
                base_text += f" (expired: {self.expired_at})"
        
        return base_text
    
    @property
    def is_expired(self) -> bool:
        """Whether it has expired"""
        return self.expired_at is not None
    
    @property
    def is_invalid(self) -> bool:
        """Whether it is no longer valid"""
        return self.invalid_at is not None


@dataclass
class InsightForgeResult:
    """
    Deep insight retrieval result (InsightForge)
    Contains the retrieval results for several sub-questions plus a combined analysis
    """
    query: str
    simulation_requirement: str
    sub_queries: List[str]
    
    # Retrieval results per dimension
    semantic_facts: List[str] = field(default_factory=list)  # semantic search results
    entity_insights: List[Dict[str, Any]] = field(default_factory=list)  # entity insights
    relationship_chains: List[str] = field(default_factory=list)  # relationship chains
    
    # Statistics
    total_facts: int = 0
    total_entities: int = 0
    total_relationships: int = 0
    
    def to_dict(self) -> Dict[str, Any]:
        return {
            "query": self.query,
            "simulation_requirement": self.simulation_requirement,
            "sub_queries": self.sub_queries,
            "semantic_facts": self.semantic_facts,
            "entity_insights": self.entity_insights,
            "relationship_chains": self.relationship_chains,
            "total_facts": self.total_facts,
            "total_entities": self.total_entities,
            "total_relationships": self.total_relationships
        }
    
    def to_text(self) -> str:
        """Convert to a detailed text format the LLM can read"""
        text_parts = [
            f"## Deep analysis of the future prediction",
            f"Analysis question: {self.query}",
            f"Prediction scenario: {self.simulation_requirement}",
            f"\n### Prediction statistics",
            f"- Related prediction facts: {self.total_facts}",
            f"- Entities involved: {self.total_entities}",
            f"- Relationship chains: {self.total_relationships}"
        ]
        
        # Sub-questions
        if self.sub_queries:
            text_parts.append(f"\n### Analysis sub-questions")
            for i, sq in enumerate(self.sub_queries, 1):
                text_parts.append(f"{i}. {sq}")
        
        # Semantic search results
        if self.semantic_facts:
            text_parts.append(f"\n### [Key facts] (quote this source text in the report)")
            for i, fact in enumerate(self.semantic_facts, 1):
                text_parts.append(f"{i}. \"{fact}\"")
        
        # Entity insights
        if self.entity_insights:
            text_parts.append(f"\n### [Core entities]")
            for entity in self.entity_insights:
                text_parts.append(f"- **{entity.get('name', 'Unknown')}** ({entity.get('type', 'Entity')})")
                if entity.get('summary'):
                    text_parts.append(f"  Summary: \"{entity.get('summary')}\"")
                if entity.get('related_facts'):
                    text_parts.append(f"  Related facts: {len(entity.get('related_facts', []))}")
        
        # Relationship chains
        if self.relationship_chains:
            text_parts.append(f"\n### [Relationship chains]")
            for chain in self.relationship_chains:
                text_parts.append(f"- {chain}")
        
        return "\n".join(text_parts)


@dataclass
class PanoramaResult:
    """
    Broad search result (Panorama)
    Contains every related piece of information, including expired content
    """
    query: str
    
    # All nodes
    all_nodes: List[NodeInfo] = field(default_factory=list)
    # All edges (expired ones included)
    all_edges: List[EdgeInfo] = field(default_factory=list)
    # Currently valid facts
    active_facts: List[str] = field(default_factory=list)
    # Expired / invalid facts (the historical record)
    historical_facts: List[str] = field(default_factory=list)
    
    # Statistics
    total_nodes: int = 0
    total_edges: int = 0
    active_count: int = 0
    historical_count: int = 0
    
    def to_dict(self) -> Dict[str, Any]:
        return {
            "query": self.query,
            "all_nodes": [n.to_dict() for n in self.all_nodes],
            "all_edges": [e.to_dict() for e in self.all_edges],
            "active_facts": self.active_facts,
            "historical_facts": self.historical_facts,
            "total_nodes": self.total_nodes,
            "total_edges": self.total_edges,
            "active_count": self.active_count,
            "historical_count": self.historical_count
        }
    
    def to_text(self) -> str:
        """Convert to a text format (the full version, untruncated)"""
        text_parts = [
            f"## Broad search results (the full future panorama)",
            f"Query: {self.query}",
            f"\n### Statistics",
            f"- Total nodes: {self.total_nodes}",
            f"- Total edges: {self.total_edges}",
            f"- Currently valid facts: {self.active_count}",
            f"- Historical / expired facts: {self.historical_count}"
        ]
        
        # Currently valid facts (full output, untruncated)
        if self.active_facts:
            text_parts.append(f"\n### [Currently valid facts] (verbatim simulation output)")
            for i, fact in enumerate(self.active_facts, 1):
                text_parts.append(f"{i}. \"{fact}\"")
        
        # Historical / expired facts (full output, untruncated)
        if self.historical_facts:
            text_parts.append(f"\n### [Historical / expired facts] (a record of how things evolved)")
            for i, fact in enumerate(self.historical_facts, 1):
                text_parts.append(f"{i}. \"{fact}\"")
        
        # Key entities (full output, untruncated)
        if self.all_nodes:
            text_parts.append(f"\n### [Entities involved]")
            for node in self.all_nodes:
                entity_type = next((l for l in node.labels if l not in ["Entity", "Node"]), "Entity")
                text_parts.append(f"- **{node.name}** ({entity_type})")
        
        return "\n".join(text_parts)


@dataclass
class AgentInterview:
    """Interview result for a single Agent"""
    agent_name: str
    agent_role: str  # role type (e.g. student, teacher, media)
    agent_bio: str  # bio
    question: str  # interview question
    response: str  # interview answer
    key_quotes: List[str] = field(default_factory=list)  # key quotes
    
    def to_dict(self) -> Dict[str, Any]:
        return {
            "agent_name": self.agent_name,
            "agent_role": self.agent_role,
            "agent_bio": self.agent_bio,
            "question": self.question,
            "response": self.response,
            "key_quotes": self.key_quotes
        }
    
    def to_text(self) -> str:
        text = f"**{self.agent_name}** ({self.agent_role})\n"
        # Show the full agent_bio, untruncated
        text += f"_Bio: {self.agent_bio}_\n\n"
        text += f"**Q:** {self.question}\n\n"
        text += f"**A:** {self.response}\n"
        if self.key_quotes:
            text += "\n**Key quotes:**\n"
            for quote in self.key_quotes:
                # Clean up the various quote characters
                clean_quote = quote.replace('\u201c', '').replace('\u201d', '').replace('"', '')
                clean_quote = clean_quote.replace('\u300c', '').replace('\u300d', '')
                clean_quote = clean_quote.strip()
                # Strip leading punctuation
                while clean_quote and clean_quote[0] in ',;:.!?\n\r\t ':
                    clean_quote = clean_quote[1:]
                # Filter out junk that contains a question number (questions 1-9)
                skip = False
                for d in '123456789':
                    if f'Question {d}' in clean_quote:
                        skip = True
                        break
                if skip:
                    continue
                # Truncate anything too long (at a sentence boundary rather than a hard cut)
                if len(clean_quote) > 150:
                    dot_pos = clean_quote.find('. ', 80)
                    if dot_pos > 0:
                        clean_quote = clean_quote[:dot_pos + 1]
                    else:
                        clean_quote = clean_quote[:147] + "..."
                if clean_quote and len(clean_quote) >= 10:
                    text += f'> "{clean_quote}"\n'
        return text


@dataclass
class InterviewResult:
    """
    Interview result (Interview)
    Contains the answers collected from several simulated Agents
    """
    interview_topic: str  # interview topic
    interview_questions: List[str]  # the list of interview questions
    
    # The Agents chosen for the interview
    selected_agents: List[Dict[str, Any]] = field(default_factory=list)
    # Each Agent's interview answers
    interviews: List[AgentInterview] = field(default_factory=list)
    
    # Why these Agents were chosen
    selection_reasoning: str = ""
    # The combined interview summary
    summary: str = ""
    
    # Statistics
    total_agents: int = 0
    interviewed_count: int = 0
    
    def to_dict(self) -> Dict[str, Any]:
        return {
            "interview_topic": self.interview_topic,
            "interview_questions": self.interview_questions,
            "selected_agents": self.selected_agents,
            "interviews": [i.to_dict() for i in self.interviews],
            "selection_reasoning": self.selection_reasoning,
            "summary": self.summary,
            "total_agents": self.total_agents,
            "interviewed_count": self.interviewed_count
        }
    
    def to_text(self) -> str:
        """Convert to a detailed text format for the LLM to read and the report to quote"""
        text_parts = [
            f"## Deep interview report",
            f"**Interview topic:** {self.interview_topic}",
            f"**Interviewees:** {self.interviewed_count} / {self.total_agents} simulated Agents",
            "\n### Why these interviewees were chosen",
            self.selection_reasoning or "(automatic selection)",
            "\n---",
            "\n### Interview transcript",
        ]

        if self.interviews:
            for i, interview in enumerate(self.interviews, 1):
                text_parts.append(f"\n#### Interview #{i}: {interview.agent_name}")
                text_parts.append(interview.to_text())
                text_parts.append("\n---")
        else:
            text_parts.append("(no interview recorded)\n\n---")

        text_parts.append("\n### Interview summary and key takeaways")
        text_parts.append(self.summary or "(no summary)")

        return "\n".join(text_parts)


class ZepToolsService:
    """
    Zep retrieval tool service.

    Wraps graph search, node reading, edge querying and the other tools the Report Agent needs.

    Core retrieval tools:
    1. insight_forge - deep insight retrieval (the most powerful: generates sub-questions
       automatically and searches along several dimensions)
    2. panorama_search - broad search (the whole picture, expired content included)
    3. quick_search - simple search (fast retrieval)
    4. interview_agents - deep interview (interviews simulated Agents for several perspectives)

    Basic tools:
    - search_graph - semantic search over the graph
    - get_all_nodes - fetch every node in the graph
    - get_all_edges - fetch every edge in the graph (with temporal information)
    - get_node_detail - detailed node information
    - get_node_edges - the edges attached to a node
    - get_entities_by_type - fetch entities by type
    - get_entity_summary - a relationship summary for an entity
    """
    
    # Retry configuration
    MAX_RETRIES = 3
    RETRY_DELAY = 2.0
    
    def __init__(self, api_key: Optional[str] = None, llm_client: Optional[LLMClient] = None):
        self.api_key = api_key or Config.ZEP_API_KEY
        if not self.api_key:
            raise ValueError("ZEP_API_KEY is not configured")
        
        self.client = Zep(api_key=self.api_key)
        # LLM client used by InsightForge to generate sub-questions
        self._llm_client = llm_client
        logger.info("ZepToolsService initialized")
    
    @property
    def llm(self) -> LLMClient:
        """Lazily initialize the LLM client"""
        if self._llm_client is None:
            self._llm_client = LLMClient()
        return self._llm_client
    
    def _call_with_retry(self, func, operation_name: str, max_retries: int = None):
        """API call with a retry mechanism"""
        max_retries = max_retries or self.MAX_RETRIES
        last_exception = None
        delay = self.RETRY_DELAY
        
        for attempt in range(max_retries):
            try:
                return func()
            except Exception as e:
                last_exception = e
                if attempt < max_retries - 1:
                    logger.warning(
                        f"Zep {operation_name} attempt {attempt + 1} failed: {str(e)[:100]}, "
                        f"retrying in {delay:.1f}s..."
                    )
                    time.sleep(delay)
                    delay *= 2
                else:
                    logger.error(f"Zep {operation_name} still failing after {max_retries} attempts: {str(e)}")
        
        raise last_exception
    
    def search_graph(
        self, 
        graph_id: str, 
        query: str, 
        limit: int = 10,
        scope: str = "edges"
    ) -> SearchResult:
        """
        Graph semantic search
        
        Uses hybrid search (semantic + BM25) to find related information in the graph.
        Falls back to local keyword matching when the Zep Cloud search API is unavailable.
        
        Args:
            graph_id: the graph ID (standalone graph)
            query: the search query
            limit: how many results to return
            scope: search scope, "edges" or "nodes"
            
        Returns:
            SearchResult: the search results
        """
        logger.info(f"Graph search: graph_id={graph_id}, query={query[:50]}...")
        
        # Try the Zep Cloud Search API
        try:
            search_results = self._call_with_retry(
                func=lambda: self.client.graph.search(
                    graph_id=graph_id,
                    query=query,
                    limit=limit,
                    scope=scope,
                    reranker="cross_encoder"
                ),
                operation_name=f"graph search (graph={graph_id})"
            )
            
            facts = []
            edges = []
            nodes = []
            
            # Parse the edge search results
            if hasattr(search_results, 'edges') and search_results.edges:
                for edge in search_results.edges:
                    if hasattr(edge, 'fact') and edge.fact:
                        facts.append(edge.fact)
                    edges.append({
                        "uuid": getattr(edge, 'uuid_', None) or getattr(edge, 'uuid', ''),
                        "name": getattr(edge, 'name', ''),
                        "fact": getattr(edge, 'fact', ''),
                        "source_node_uuid": getattr(edge, 'source_node_uuid', ''),
                        "target_node_uuid": getattr(edge, 'target_node_uuid', ''),
                    })
            
            # Parse the node search results
            if hasattr(search_results, 'nodes') and search_results.nodes:
                for node in search_results.nodes:
                    nodes.append({
                        "uuid": getattr(node, 'uuid_', None) or getattr(node, 'uuid', ''),
                        "name": getattr(node, 'name', ''),
                        "labels": getattr(node, 'labels', []),
                        "summary": getattr(node, 'summary', ''),
                    })
                    # A node summary also counts as a fact
                    if hasattr(node, 'summary') and node.summary:
                        facts.append(f"[{node.name}]: {node.summary}")
            
            logger.info(f"Search complete: found {len(facts)} related facts")
            
            return SearchResult(
                facts=facts,
                edges=edges,
                nodes=nodes,
                query=query,
                total_count=len(facts)
            )
            
        except Exception as e:
            logger.warning(f"Zep Search API failed, falling back to local search: {str(e)}")
            # Fallback: local keyword matching search
            return self._local_search(graph_id, query, limit, scope)
    
    def _local_search(
        self, 
        graph_id: str, 
        query: str, 
        limit: int = 10,
        scope: str = "edges"
    ) -> SearchResult:
        """
        Local keyword matching search (the fallback for the Zep Search API)
        
        Fetches all edges/nodes and then matches keywords locally
        
        Args:
            graph_id: the graph ID
            query: the search query
            limit: how many results to return
            scope: search scope
            
        Returns:
            SearchResult: the search results
        """
        logger.info(f"Using local search: query={query[:30]}...")
        
        facts = []
        edges_result = []
        nodes_result = []
        
        # Extract the query keywords (simple tokenization)
        query_lower = query.lower()
        keywords = [w.strip() for w in query_lower.replace(',', ' ').split() if len(w.strip()) > 1]
        
        def match_score(text: str) -> int:
            """Compute the match score between the text and the query"""
            if not text:
                return 0
            text_lower = text.lower()
            # Exact match on the query
            if query_lower in text_lower:
                return 100
            # Keyword matches
            score = 0
            for keyword in keywords:
                if keyword in text_lower:
                    score += 10
            return score
        
        try:
            if scope in ["edges", "both"]:
                # Fetch every edge and match
                all_edges = self.get_all_edges(graph_id)
                scored_edges = []
                for edge in all_edges:
                    score = match_score(edge.fact) + match_score(edge.name)
                    if score > 0:
                        scored_edges.append((score, edge))
                
                # Sort by score
                scored_edges.sort(key=lambda x: x[0], reverse=True)
                
                for score, edge in scored_edges[:limit]:
                    if edge.fact:
                        facts.append(edge.fact)
                    edges_result.append({
                        "uuid": edge.uuid,
                        "name": edge.name,
                        "fact": edge.fact,
                        "source_node_uuid": edge.source_node_uuid,
                        "target_node_uuid": edge.target_node_uuid,
                    })
            
            if scope in ["nodes", "both"]:
                # Fetch every node and match
                all_nodes = self.get_all_nodes(graph_id)
                scored_nodes = []
                for node in all_nodes:
                    score = match_score(node.name) + match_score(node.summary)
                    if score > 0:
                        scored_nodes.append((score, node))
                
                scored_nodes.sort(key=lambda x: x[0], reverse=True)
                
                for score, node in scored_nodes[:limit]:
                    nodes_result.append({
                        "uuid": node.uuid,
                        "name": node.name,
                        "labels": node.labels,
                        "summary": node.summary,
                    })
                    if node.summary:
                        facts.append(f"[{node.name}]: {node.summary}")
            
            logger.info(f"Local search complete: found {len(facts)} related facts")
            
        except Exception as e:
            logger.error(f"Local search failed: {str(e)}")
        
        return SearchResult(
            facts=facts,
            edges=edges_result,
            nodes=nodes_result,
            query=query,
            total_count=len(facts)
        )
    
    def get_all_nodes(self, graph_id: str) -> List[NodeInfo]:
        """
        Fetch every node in the graph (paged)

        Args:
            graph_id: the graph ID

        Returns:
            the list of nodes
        """
        logger.info(f"Fetching every node in graph {graph_id}...")

        nodes = fetch_all_nodes(self.client, graph_id)

        result = []
        for node in nodes:
            node_uuid = getattr(node, 'uuid_', None) or getattr(node, 'uuid', None) or ""
            result.append(NodeInfo(
                uuid=str(node_uuid) if node_uuid else "",
                name=node.name or "",
                labels=node.labels or [],
                summary=node.summary or "",
                attributes=node.attributes or {}
            ))

        logger.info(f"Fetched {len(result)} nodes")
        return result

    def get_all_edges(self, graph_id: str, include_temporal: bool = True) -> List[EdgeInfo]:
        """
        Fetch every edge in the graph (paged, including temporal information)

        Args:
            graph_id: the graph ID
            include_temporal: whether to include temporal information (default True)

        Returns:
            the list of edges (with created_at, valid_at, invalid_at, expired_at)
        """
        logger.info(f"Fetching every edge in graph {graph_id}...")

        edges = fetch_all_edges(self.client, graph_id)

        result = []
        for edge in edges:
            edge_uuid = getattr(edge, 'uuid_', None) or getattr(edge, 'uuid', None) or ""
            edge_info = EdgeInfo(
                uuid=str(edge_uuid) if edge_uuid else "",
                name=edge.name or "",
                fact=edge.fact or "",
                source_node_uuid=edge.source_node_uuid or "",
                target_node_uuid=edge.target_node_uuid or ""
            )

            # Add the temporal information
            if include_temporal:
                edge_info.created_at = getattr(edge, 'created_at', None)
                edge_info.valid_at = getattr(edge, 'valid_at', None)
                edge_info.invalid_at = getattr(edge, 'invalid_at', None)
                edge_info.expired_at = getattr(edge, 'expired_at', None)

            result.append(edge_info)

        logger.info(f"Fetched {len(result)} edges")
        return result
    
    def get_node_detail(self, node_uuid: str) -> Optional[NodeInfo]:
        """
        Fetch the details of a single node
        
        Args:
            node_uuid: the node UUID
            
        Returns:
            the node information, or None
        """
        logger.info(f"Fetching node details: {node_uuid[:8]}...")
        
        try:
            node = self._call_with_retry(
                func=lambda: self.client.graph.node.get(uuid_=node_uuid),
                operation_name=f"fetch node details (uuid={node_uuid[:8]}...)"
            )
            
            if not node:
                return None
            
            return NodeInfo(
                uuid=getattr(node, 'uuid_', None) or getattr(node, 'uuid', ''),
                name=node.name or "",
                labels=node.labels or [],
                summary=node.summary or "",
                attributes=node.attributes or {}
            )
        except Exception as e:
            logger.error(f"Failed to fetch the node details: {str(e)}")
            return None
    
    def get_node_edges(self, graph_id: str, node_uuid: str) -> List[EdgeInfo]:
        """
        Fetch every edge attached to a node
        
        Fetches every edge in the graph and filters out the ones attached to the given node
        
        Args:
            graph_id: the graph ID
            node_uuid: the node UUID
            
        Returns:
            the list of edges
        """
        logger.info(f"Fetching the edges attached to node {node_uuid[:8]}...")
        
        try:
            # Fetch every edge in the graph and then filter
            all_edges = self.get_all_edges(graph_id)
            
            result = []
            for edge in all_edges:
                # Check whether the edge touches the given node (as source or target)
                if edge.source_node_uuid == node_uuid or edge.target_node_uuid == node_uuid:
                    result.append(edge)
            
            logger.info(f"Found {len(result)} edges attached to the node")
            return result
            
        except Exception as e:
            logger.warning(f"Failed to fetch the node edges: {str(e)}")
            return []
    
    def get_entities_by_type(
        self, 
        graph_id: str, 
        entity_type: str
    ) -> List[NodeInfo]:
        """
        Fetch entities by type
        
        Args:
            graph_id: the graph ID
            entity_type: the entity type (e.g. Student, PublicFigure, ...)
            
        Returns:
            the list of entities of that type
        """
        logger.info(f"Fetching entities of type {entity_type}...")
        
        all_nodes = self.get_all_nodes(graph_id)
        
        filtered = []
        for node in all_nodes:
            # Check whether the labels contain the given type
            if entity_type in node.labels:
                filtered.append(node)
        
        logger.info(f"Found {len(filtered)} entities of type {entity_type}")
        return filtered
    
    def get_entity_summary(
        self, 
        graph_id: str, 
        entity_name: str
    ) -> Dict[str, Any]:
        """
        Fetch a relationship summary for a given entity
        
        Searches for everything related to the entity and produces a summary
        
        Args:
            graph_id: the graph ID
            entity_name: the entity name
            
        Returns:
            the entity summary
        """
        logger.info(f"Fetching the relationship summary for entity {entity_name}...")
        
        # Search for information about this entity first
        search_result = self.search_graph(
            graph_id=graph_id,
            query=entity_name,
            limit=20
        )
        
        # Try to find the entity among all the nodes
        all_nodes = self.get_all_nodes(graph_id)
        entity_node = None
        for node in all_nodes:
            if node.name.lower() == entity_name.lower():
                entity_node = node
                break
        
        related_edges = []
        if entity_node:
            # Pass the graph_id argument
            related_edges = self.get_node_edges(graph_id, entity_node.uuid)
        
        return {
            "entity_name": entity_name,
            "entity_info": entity_node.to_dict() if entity_node else None,
            "related_facts": search_result.facts,
            "related_edges": [e.to_dict() for e in related_edges],
            "total_relations": len(related_edges)
        }
    
    def get_graph_statistics(self, graph_id: str) -> Dict[str, Any]:
        """
        Fetch statistics about the graph
        
        Args:
            graph_id: the graph ID
            
        Returns:
            the statistics
        """
        logger.info(f"Fetching statistics for graph {graph_id}...")
        
        nodes = self.get_all_nodes(graph_id)
        edges = self.get_all_edges(graph_id)
        
        # Count the entity type distribution
        entity_types = {}
        for node in nodes:
            for label in node.labels:
                if label not in ["Entity", "Node"]:
                    entity_types[label] = entity_types.get(label, 0) + 1
        
        # Count the relation type distribution
        relation_types = {}
        for edge in edges:
            relation_types[edge.name] = relation_types.get(edge.name, 0) + 1
        
        return {
            "graph_id": graph_id,
            "total_nodes": len(nodes),
            "total_edges": len(edges),
            "entity_types": entity_types,
            "relation_types": relation_types
        }
    
    def get_simulation_context(
        self, 
        graph_id: str,
        simulation_requirement: str,
        limit: int = 30
    ) -> Dict[str, Any]:
        """
        Fetch the context information related to a simulation
        
        Searches for everything related to the simulation goal
        
        Args:
            graph_id: the graph ID
            simulation_requirement: a description of the simulation goal
            limit: how many of each kind of information to include
            
        Returns:
            the simulation context information
        """
        logger.info(f"Fetching the simulation context: {simulation_requirement[:50]}...")
        
        # Search for information related to the simulation goal
        search_result = self.search_graph(
            graph_id=graph_id,
            query=simulation_requirement,
            limit=limit
        )
        
        # Fetch the graph statistics
        stats = self.get_graph_statistics(graph_id)
        
        # Fetch every entity node
        all_nodes = self.get_all_nodes(graph_id)
        
        # Keep only entities that carry a real type (not bare Entity nodes)
        entities = []
        for node in all_nodes:
            custom_labels = [l for l in node.labels if l not in ["Entity", "Node"]]
            if custom_labels:
                entities.append({
                    "name": node.name,
                    "type": custom_labels[0],
                    "summary": node.summary
                })
        
        return {
            "simulation_requirement": simulation_requirement,
            "related_facts": search_result.facts,
            "graph_statistics": stats,
            "entities": entities[:limit],  # cap the count
            "total_entities": len(entities)
        }
    
    # ========== Core retrieval tools (after optimization) ==========
    
    def insight_forge(
        self,
        graph_id: str,
        query: str,
        simulation_requirement: str,
        report_context: str = "",
        max_sub_queries: int = 5
    ) -> InsightForgeResult:
        """
        [InsightForge - deep insight retrieval]
        
        The most powerful hybrid retrieval function: it decomposes the question automatically and searches along several dimensions:
        1. Use the LLM to split the question into several sub-questions
        2. Run a semantic search for each sub-question
        3. Extract the related entities and fetch their details
        4. Trace the relationship chains
        5. Combine every result into a deep insight
        
        Args:
            graph_id: the graph ID
            query: the user's question
            simulation_requirement: a description of the simulation goal
            report_context: report context (optional, used to generate sharper sub-questions)
            max_sub_queries: the maximum number of sub-questions
            
        Returns:
            InsightForgeResult: the deep insight retrieval result
        """
        logger.info(f"InsightForge deep insight retrieval: {query[:50]}...")
        
        result = InsightForgeResult(
            query=query,
            simulation_requirement=simulation_requirement,
            sub_queries=[]
        )
        
        # Step 1: use the LLM to generate the sub-questions
        sub_queries = self._generate_sub_queries(
            query=query,
            simulation_requirement=simulation_requirement,
            report_context=report_context,
            max_queries=max_sub_queries
        )
        result.sub_queries = sub_queries
        logger.info(f"Generated {len(sub_queries)} sub-questions")
        
        # Step 2: run a semantic search for each sub-question
        all_facts = []
        all_edges = []
        seen_facts = set()
        
        for sub_query in sub_queries:
            search_result = self.search_graph(
                graph_id=graph_id,
                query=sub_query,
                limit=15,
                scope="edges"
            )
            
            for fact in search_result.facts:
                if fact not in seen_facts:
                    all_facts.append(fact)
                    seen_facts.add(fact)
            
            all_edges.extend(search_result.edges)
        
        # Also search for the original question
        main_search = self.search_graph(
            graph_id=graph_id,
            query=query,
            limit=20,
            scope="edges"
        )
        for fact in main_search.facts:
            if fact not in seen_facts:
                all_facts.append(fact)
                seen_facts.add(fact)
        
        result.semantic_facts = all_facts
        result.total_facts = len(all_facts)
        
        # Step 3: extract the relevant entity UUIDs from the edges and fetch only those entities (not every node)
        entity_uuids = set()
        for edge_data in all_edges:
            if isinstance(edge_data, dict):
                source_uuid = edge_data.get('source_node_uuid', '')
                target_uuid = edge_data.get('target_node_uuid', '')
                if source_uuid:
                    entity_uuids.add(source_uuid)
                if target_uuid:
                    entity_uuids.add(target_uuid)
        
        # Fetch the details of every related entity (unlimited, full output)
        entity_insights = []
        node_map = {}  # used later to build the relationship chains
        
        for uuid in list(entity_uuids):  # handle every entity, no truncation
            if not uuid:
                continue
            try:
                # Fetch each related node individually
                node = self.get_node_detail(uuid)
                if node:
                    node_map[uuid] = node
                    entity_type = next((l for l in node.labels if l not in ["Entity", "Node"]), "Entity")
                    
                    # Fetch every fact related to this entity (untruncated)
                    related_facts = [
                        f for f in all_facts 
                        if node.name.lower() in f.lower()
                    ]
                    
                    entity_insights.append({
                        "uuid": node.uuid,
                        "name": node.name,
                        "type": entity_type,
                        "summary": node.summary,
                        "related_facts": related_facts  # full output, untruncated
                    })
            except Exception as e:
                logger.debug(f"Failed to fetch node {uuid}: {e}")
                continue
        
        result.entity_insights = entity_insights
        result.total_entities = len(entity_insights)
        
        # Step 4: build every relationship chain (unlimited)
        relationship_chains = []
        for edge_data in all_edges:  # handle every edge, no truncation
            if isinstance(edge_data, dict):
                source_uuid = edge_data.get('source_node_uuid', '')
                target_uuid = edge_data.get('target_node_uuid', '')
                relation_name = edge_data.get('name', '')
                
                source_name = node_map.get(source_uuid, NodeInfo('', '', [], '', {})).name or source_uuid[:8]
                target_name = node_map.get(target_uuid, NodeInfo('', '', [], '', {})).name or target_uuid[:8]
                
                chain = f"{source_name} --[{relation_name}]--> {target_name}"
                if chain not in relationship_chains:
                    relationship_chains.append(chain)
        
        result.relationship_chains = relationship_chains
        result.total_relationships = len(relationship_chains)
        
        logger.info(f"InsightForge complete: {result.total_facts} facts, {result.total_entities} entities, {result.total_relationships} relations")
        return result
    
    def _generate_sub_queries(
        self,
        query: str,
        simulation_requirement: str,
        report_context: str = "",
        max_queries: int = 5
    ) -> List[str]:
        """
        Use the LLM to generate sub-questions
        
        Splits a complex question into several sub-questions that can each be retrieved independently
        """
        system_prompt = """You are a professional question analyst. Your job is to split one complex question into several sub-questions that can each be observed independently inside the simulated world.

Requirements:
1. Each sub-question must be specific enough that related Agent behavior or events can be found in the simulated world
2. The sub-questions should cover different dimensions of the original question (who, what, why, how, when, where)
3. The sub-questions should be relevant to the simulation scenario
4. Respond with JSON: {"sub_queries": ["sub-question 1", "sub-question 2", ...]}"""

        user_prompt = f"""Simulation goal background:
{simulation_requirement}

{f"Report context: {report_context[:500]}" if report_context else ""}

Please split the following question into {max_queries} sub-questions:
{query}

Respond with a JSON list of sub-questions."""

        try:
            response = self.llm.chat_json(
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_prompt}
                ],
                temperature=0.3
            )
            
            sub_queries = response.get("sub_queries", [])
            # Make sure it is a list of strings
            return [str(sq) for sq in sub_queries[:max_queries]]
            
        except Exception as e:
            logger.warning(f"Failed to generate sub-questions: {str(e)}, using the default sub-questions")
            # Fallback: return variants built from the original question
            return [
                query,
                f"the main participants in: {query}",
                f"the causes and effects of: {query}",
                f"how the following unfolded: {query}"
            ][:max_queries]
    
    def panorama_search(
        self,
        graph_id: str,
        query: str,
        include_expired: bool = True,
        limit: int = 50
    ) -> PanoramaResult:
        """
        [PanoramaSearch - broad search]
        
        Gives the whole picture, including all related content and the historical/expired information:
        1. Fetch every related node
        2. Fetch every edge (expired and invalid ones included)
        3. Group the current and historical information separately
        
        This tool suits cases where you need the whole event and want to trace how it evolved.
        
        Args:
            graph_id: the graph ID
            query: the search query (used for relevance ranking)
            include_expired: whether to include expired content (default True)
            limit: a cap on how many results to return
            
        Returns:
            PanoramaResult: the broad search result
        """
        logger.info(f"PanoramaSearch broad search: {query[:50]}...")
        
        result = PanoramaResult(query=query)
        
        # Fetch every node
        all_nodes = self.get_all_nodes(graph_id)
        node_map = {n.uuid: n for n in all_nodes}
        result.all_nodes = all_nodes
        result.total_nodes = len(all_nodes)
        
        # Fetch every edge (with temporal information)
        all_edges = self.get_all_edges(graph_id, include_temporal=True)
        result.all_edges = all_edges
        result.total_edges = len(all_edges)
        
        # Group the facts
        active_facts = []
        historical_facts = []
        
        for edge in all_edges:
            if not edge.fact:
                continue
            
            # Attach the entity names to the facts
            source_name = node_map.get(edge.source_node_uuid, NodeInfo('', '', [], '', {})).name or edge.source_node_uuid[:8]
            target_name = node_map.get(edge.target_node_uuid, NodeInfo('', '', [], '', {})).name or edge.target_node_uuid[:8]
            
            # Work out whether it has expired or become invalid
            is_historical = edge.is_expired or edge.is_invalid
            
            if is_historical:
                # Historical/expired facts, with a time marker
                valid_at = edge.valid_at or "Unknown"
                invalid_at = edge.invalid_at or edge.expired_at or "Unknown"
                fact_with_time = f"[{valid_at} - {invalid_at}] {edge.fact}"
                historical_facts.append(fact_with_time)
            else:
                # Currently valid facts
                active_facts.append(edge.fact)
        
        # Rank by relevance to the query
        query_lower = query.lower()
        keywords = [w.strip() for w in query_lower.replace(',', ' ').split() if len(w.strip()) > 1]
        
        def relevance_score(fact: str) -> int:
            fact_lower = fact.lower()
            score = 0
            if query_lower in fact_lower:
                score += 100
            for kw in keywords:
                if kw in fact_lower:
                    score += 10
            return score
        
        # Sort and cap the count
        active_facts.sort(key=relevance_score, reverse=True)
        historical_facts.sort(key=relevance_score, reverse=True)
        
        result.active_facts = active_facts[:limit]
        result.historical_facts = historical_facts[:limit] if include_expired else []
        result.active_count = len(active_facts)
        result.historical_count = len(historical_facts)
        
        logger.info(f"PanoramaSearch complete: {result.active_count} valid, {result.historical_count} historical")
        return result
    
    def quick_search(
        self,
        graph_id: str,
        query: str,
        limit: int = 10
    ) -> SearchResult:
        """
        [QuickSearch - simple search]
        
        A fast, lightweight retrieval tool:
        1. Calls Zep semantic search directly
        2. Returns the most relevant results
        3. Suited to simple, direct lookups
        
        Args:
            graph_id: the graph ID
            query: the search query
            limit: how many results to return
            
        Returns:
            SearchResult: the search results
        """
        logger.info(f"QuickSearch simple search: {query[:50]}...")
        
        # Call the existing search_graph method directly
        result = self.search_graph(
            graph_id=graph_id,
            query=query,
            limit=limit,
            scope="edges"
        )
        
        logger.info(f"QuickSearch complete: {result.total_count} results")
        return result
    
    def interview_agents(
        self,
        simulation_id: str,
        interview_requirement: str,
        simulation_requirement: str = "",
        max_agents: int = 5,
        custom_questions: List[str] = None
    ) -> InterviewResult:
        """
        Interview the Agents running in the simulation through the real OASIS interview API.

        1. Reads the persona files to learn about every simulated Agent
        2. Uses the LLM to analyse the interview request and pick the most relevant Agents
        3. Uses the LLM to generate the interview questions
        4. Calls /api/simulation/interview/batch to run the real interview (on both platforms at once)
        5. Combines every interview result into an interview report

        The simulation environment must be running (the OASIS environment must not be shut down).
        Useful when you need the event seen from different roles, opinions from several sides,
        or the real answers of the Agents rather than an LLM approximation.

        Args:
            simulation_id: the simulation ID (used to locate the persona files and call the interview API)
            interview_requirement: the interview request, in free text (e.g. \"find out what students think about the event\")
            simulation_requirement: the simulation goal background (optional)
            max_agents: the maximum number of Agents to interview
            custom_questions: custom interview questions (optional; generated automatically if omitted)
            
        Returns:
            InterviewResult: the interview result
        """
        from .simulation_runner import SimulationRunner
        
        logger.info(f"InterviewAgents deep interview (real API): {interview_requirement[:50]}...")
        
        result = InterviewResult(
            interview_topic=interview_requirement,
            interview_questions=custom_questions or []
        )
        
        # Step 1: read the persona files
        profiles = self._load_agent_profiles(simulation_id)
        
        if not profiles:
            logger.warning(f"No persona file found for simulation {simulation_id}")
            result.summary = "No Agent persona file was found to interview"
            return result
        
        result.total_agents = len(profiles)
        logger.info(f"Loaded {len(profiles)} Agent personas")
        
        # Step 2: use the LLM to pick the Agents to interview (returns a list of agent_ids)
        selected_agents, selected_indices, selection_reasoning = self._select_agents_for_interview(
            profiles=profiles,
            interview_requirement=interview_requirement,
            simulation_requirement=simulation_requirement,
            max_agents=max_agents
        )
        
        result.selected_agents = selected_agents
        result.selection_reasoning = selection_reasoning
        logger.info(f"Selected {len(selected_agents)} Agents to interview: {selected_indices}")
        
        # Step 3: generate the interview questions (when none were supplied)
        if not result.interview_questions:
            result.interview_questions = self._generate_interview_questions(
                interview_requirement=interview_requirement,
                simulation_requirement=simulation_requirement,
                selected_agents=selected_agents
            )
            logger.info(f"Generated {len(result.interview_questions)} interview questions")
        
        # Merge the questions into a single interview prompt
        combined_prompt = "\n".join([f"{i+1}. {q}" for i, q in enumerate(result.interview_questions)])
        
        # Prefix that constrains how the Agent answers
        INTERVIEW_PROMPT_PREFIX = (
            "You are taking part in an interview. Drawing on your persona and on all of your past memories and actions, "
            "answer the questions below directly in plain text.\n"
            "Answer requirements:\n"
            "1. Answer in natural language; do not call any tools\n"
            "2. Do not return JSON or tool-call syntax\n"
            "3. Do not use Markdown headings (such as #, ##, ###)\n"
            "4. Answer the questions one by one, numbering them as \"Question X:\" (X is the question number)\n"
            "5. Separate each answer with a blank line\n"
            "6. Give substantive answers: at least 2-3 sentences per question\n\n"
        )
        optimized_prompt = f"{INTERVIEW_PROMPT_PREFIX}{combined_prompt}"
        
        # Step 4: call the real interview API (no platform given, so both platforms are interviewed at once)
        try:
            # Build the batch interview list (no platform given, interview on both platforms)
            interviews_request = []
            for agent_idx in selected_indices:
                interviews_request.append({
                    "agent_id": agent_idx,
                    "prompt": optimized_prompt  # the optimized prompt
                    # No platform is set, so the API interviews on both Twitter and Reddit
                })
            
            logger.info(f"Calling the batch interview API (both platforms): {len(interviews_request)} Agents")
            
            # Call SimulationRunner's batch interview method (no platform, so both platforms)
            api_result = SimulationRunner.interview_agents_batch(
                simulation_id=simulation_id,
                interviews=interviews_request,
                platform=None,  # no platform: interview on both
                timeout=180.0   # both platforms need a longer timeout
            )
            
            logger.info(f"Interview API returned: {api_result.get('interviews_count', 0)} results, success={api_result.get('success')}")
            
            # Check whether the API call succeeded
            if not api_result.get("success", False):
                error_msg = api_result.get("error", "unknown error")
                logger.warning(f"The interview API returned a failure: {error_msg}")
                result.summary = f"The interview API call failed: {error_msg}. Please check the state of the OASIS simulation environment."
                return result
            
            # Step 5: parse the API response and build the AgentInterview objects
            # Dual-platform response format: {"twitter_0": {...}, "reddit_0": {...}, "twitter_1": {...}, ...}
            api_data = api_result.get("result", {})
            results_dict = api_data.get("results", {}) if isinstance(api_data, dict) else {}
            
            for i, agent_idx in enumerate(selected_indices):
                agent = selected_agents[i]
                agent_name = agent.get("realname", agent.get("username", f"Agent_{agent_idx}"))
                agent_role = agent.get("profession", "Unknown")
                agent_bio = agent.get("bio", "")
                
                # Fetch this Agent's interview results on both platforms
                twitter_result = results_dict.get(f"twitter_{agent_idx}", {})
                reddit_result = results_dict.get(f"reddit_{agent_idx}", {})
                
                twitter_response = twitter_result.get("response", "")
                reddit_response = reddit_result.get("response", "")

                # Clean up any JSON tool-call wrapping
                twitter_response = self._clean_tool_call_response(twitter_response)
                reddit_response = self._clean_tool_call_response(reddit_response)

                # Always emit both platform markers
                twitter_text = twitter_response if twitter_response else "(no reply on this platform)"
                reddit_text = reddit_response if reddit_response else "(no reply on this platform)"
                response_text = f"[Twitter platform answer]\n{twitter_text}\n\n[Reddit platform answer]\n{reddit_text}"

                # Extract the key quotes (from both platforms' answers)
                import re
                combined_responses = f"{twitter_response} {reddit_response}"

                # Clean up the response text: strip markers, numbering, Markdown and other noise
                clean_text = re.sub(r'#{1,6}\s+', '', combined_responses)
                clean_text = re.sub(r'\{[^}]*tool_name[^}]*\}', '', clean_text)
                clean_text = re.sub(r'[*_`|>~\-]{2,}', '', clean_text)
                clean_text = re.sub(r'Question\s*\d+[:]\s*', '', clean_text)
                clean_text = re.sub(r'\[[^\]]{1,20}\]', '', clean_text)

                # Strategy 1 (primary): pull out complete sentences with real substance
                sentences = re.split(r'(?<=[.!?])\s+', clean_text)
                meaningful = [
                    s.strip() for s in sentences
                    if 20 <= len(s.strip()) <= 150
                    and not re.match(r'^[\s\W,;:]+', s.strip())
                    and not s.strip().startswith(('{', 'Question'))
                ]
                meaningful.sort(key=len, reverse=True)
                key_quotes = [s if s.endswith((".", "!", "?")) else s + "." for s in meaningful[:3]]

                # Strategy 2 (supplementary): long text inside properly paired double quotes
                if not key_quotes:
                    paired = re.findall(r'\u201c([^\u201c\u201d]{15,100})\u201d', clean_text)
                    paired += re.findall(r'\u300c([^\u300c\u300d]{15,100})\u300d', clean_text)
                    key_quotes = [q for q in paired if not re.match(r'^[\s,;:]', q)][:3]
                
                interview = AgentInterview(
                    agent_name=agent_name,
                    agent_role=agent_role,
                    agent_bio=agent_bio[:1000],  # raise the bio length limit
                    question=combined_prompt,
                    response=response_text,
                    key_quotes=key_quotes[:5]
                )
                result.interviews.append(interview)
            
            result.interviewed_count = len(result.interviews)
            
        except ValueError as e:
            # The simulation environment is not running
            logger.warning(f"The interview API call failed (environment not running?): {e}")
            result.summary = f"Interview failed: {str(e)}. The simulation environment may have been shut down; make sure the OASIS environment is running."
            return result
        except Exception as e:
            logger.error(f"Error during the interview API call: {e}")
            import traceback
            logger.error(traceback.format_exc())
            result.summary = f"Something went wrong during the interview: {str(e)}"
            return result
        
        # Step 6: generate the interview summary
        if result.interviews:
            result.summary = self._generate_interview_summary(
                interviews=result.interviews,
                interview_requirement=interview_requirement
            )
        
        logger.info(f"InterviewAgents complete: interviewed {result.interviewed_count} Agents (both platforms)")
        return result
    
    @staticmethod
    def _clean_tool_call_response(response: str) -> str:
        """Strip any JSON tool-call wrapping from an Agent reply and extract the actual content"""
        if not response or not response.strip().startswith('{'):
            return response
        text = response.strip()
        if 'tool_name' not in text[:80]:
            return response
        import re as _re
        try:
            data = json.loads(text)
            if isinstance(data, dict) and 'arguments' in data:
                for key in ('content', 'text', 'body', 'message', 'reply'):
                    if key in data['arguments']:
                        return str(data['arguments'][key])
        except (json.JSONDecodeError, KeyError, TypeError):
            match = _re.search(r'"content"\s*:\s*"((?:[^"\\]|\\.)*)"', text)
            if match:
                return match.group(1).replace('\\n', '\n').replace('\\"', '"')
        return response

    def _load_agent_profiles(self, simulation_id: str) -> List[Dict[str, Any]]:
        """Load the simulation's Agent persona files"""
        import os
        import csv
        
        # Build the persona file paths
        sim_dir = os.path.join(
            Config.OASIS_SIMULATION_DATA_DIR, simulation_id
        )
        
        profiles = []
        
        # Try the Reddit JSON format first
        reddit_profile_path = os.path.join(sim_dir, "reddit_profiles.json")
        if os.path.exists(reddit_profile_path):
            try:
                with open(reddit_profile_path, 'r', encoding='utf-8') as f:
                    profiles = json.load(f)
                logger.info(f"Loaded {len(profiles)} personas from reddit_profiles.json")
                return profiles
            except Exception as e:
                logger.warning(f"Failed to read reddit_profiles.json: {e}")
        
        # Try the Twitter CSV format
        twitter_profile_path = os.path.join(sim_dir, "twitter_profiles.csv")
        if os.path.exists(twitter_profile_path):
            try:
                with open(twitter_profile_path, 'r', encoding='utf-8') as f:
                    reader = csv.DictReader(f)
                    for row in reader:
                        # Convert the CSV format into the unified format
                        profiles.append({
                            "realname": row.get("name", ""),
                            "username": row.get("username", ""),
                            "bio": row.get("description", ""),
                            "persona": row.get("user_char", ""),
                            "profession": "Unknown"
                        })
                logger.info(f"Loaded {len(profiles)} personas from twitter_profiles.csv")
                return profiles
            except Exception as e:
                logger.warning(f"Failed to read twitter_profiles.csv: {e}")
        
        return profiles
    
    def _select_agents_for_interview(
        self,
        profiles: List[Dict[str, Any]],
        interview_requirement: str,
        simulation_requirement: str,
        max_agents: int
    ) -> tuple:
        """
        Use the LLM to pick the Agents to interview
        
        Returns:
            tuple: (selected_agents, selected_indices, reasoning)
                - selected_agents: the full information of the selected Agents
                - selected_indices: the indexes of the selected Agents (used for the API call)
                - reasoning: why they were selected
        """
        
        # Build the Agent summary list
        agent_summaries = []
        for i, profile in enumerate(profiles):
            summary = {
                "index": i,
                "name": profile.get("realname", profile.get("username", f"Agent_{i}")),
                "profession": profile.get("profession", "Unknown"),
                "bio": profile.get("bio", "")[:200],
                "interested_topics": profile.get("interested_topics", [])
            }
            agent_summaries.append(summary)
        
        system_prompt = """You are a professional interview planner. Your job is to pick the Agents most worth interviewing from the list of simulated Agents, based on the interview request.

Selection criteria:
1. The Agent's identity/profession is relevant to the interview topic
2. The Agent is likely to hold a distinctive or valuable opinion
3. Pick a diverse range of perspectives (supporters, opponents, neutral parties, professionals, ...)
4. Prefer roles directly involved in the event

Respond with JSON:
{
    "selected_indices": [the indexes of the selected Agents],
    "reasoning": "why these were chosen"
}"""

        user_prompt = f"""Interview request:
{interview_requirement}

Simulation background:
{simulation_requirement if simulation_requirement else "not provided"}

Available Agents ({len(agent_summaries)} in total):
{json.dumps(agent_summaries, ensure_ascii=False, indent=2)}

Pick at most {max_agents} Agents that are the best fit for the interview and explain your choice."""

        try:
            response = self.llm.chat_json(
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_prompt}
                ],
                temperature=0.3
            )
            
            selected_indices = response.get("selected_indices", [])[:max_agents]
            reasoning = response.get("reasoning", "chosen automatically by relevance")
            
            # Fetch the full information of the selected Agents
            selected_agents = []
            valid_indices = []
            for idx in selected_indices:
                if 0 <= idx < len(profiles):
                    selected_agents.append(profiles[idx])
                    valid_indices.append(idx)
            
            return selected_agents, valid_indices, reasoning
            
        except Exception as e:
            logger.warning(f"Failed to have the LLM select the Agents, using the default selection: {e}")
            # Fallback: take the first N
            selected = profiles[:max_agents]
            indices = list(range(min(max_agents, len(profiles))))
            return selected, indices, "used the default selection strategy"
    
    def _generate_interview_questions(
        self,
        interview_requirement: str,
        simulation_requirement: str,
        selected_agents: List[Dict[str, Any]]
    ) -> List[str]:
        """Use the LLM to generate the interview questions"""
        
        agent_roles = [a.get("profession", "Unknown") for a in selected_agents]
        
        system_prompt = """You are a professional reporter/interviewer. Based on the interview request, write 3-5 in-depth interview questions.

Question requirements:
1. Open-ended questions that encourage detailed answers
2. Questions that may produce different answers from different roles
3. Cover several dimensions: facts, opinions, feelings
4. Natural language, the way a real interview sounds
5. Keep each question under 50 characters, short and clear
6. Ask directly, with no background or preamble

Respond with JSON: {"questions": ["question 1", "question 2", ...]}"""

        user_prompt = f"""Interview request: {interview_requirement}

Simulation background: {simulation_requirement if simulation_requirement else "not provided"}

Roles of the interviewees: {', '.join(agent_roles)}

Please write 3-5 interview questions."""

        try:
            response = self.llm.chat_json(
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_prompt}
                ],
                temperature=0.5
            )
            
            return response.get("questions", [f"What do you think about {interview_requirement}?"])
            
        except Exception as e:
            logger.warning(f"Failed to generate the interview questions: {e}")
            return [
                f"What is your view on {interview_requirement}?",
                "How does this affect you or the group you represent?",
                "How do you think this problem should be solved or improved?"
            ]
    
    def _generate_interview_summary(
        self,
        interviews: List[AgentInterview],
        interview_requirement: str
    ) -> str:
        """Generate the interview summary"""
        
        if not interviews:
            return "No interview was completed"
        
        # Collect every interview transcript
        interview_texts = []
        for interview in interviews:
            interview_texts.append(f"[{interview.agent_name} ({interview.agent_role})]\n{interview.response[:500]}")
        
        system_prompt = """You are a professional news editor. Based on the answers of several interviewees, write an interview summary.

Summary requirements:
1. Distil the main point of view of each side
2. Point out where they agree and where they differ
3. Highlight the most valuable quotes
4. Stay objective and neutral; take no side
5. Keep it under 1000 words

Formatting rules (must be followed):
- Plain-text paragraphs, with a blank line between sections
- Do not use Markdown headings (such as #, ##, ###)
- Do not use horizontal rules (such as ---, ***)
- Put an interviewee's exact words in quotation marks
- You may use **bold** for key terms, but no other Markdown syntax"""

        user_prompt = f"""Interview topic: {interview_requirement}

Interview content:
{"".join(interview_texts)}

Please write the interview summary."""

        try:
            summary = self.llm.chat(
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_prompt}
                ],
                temperature=0.3,
                max_tokens=800
            )
            return summary
            
        except Exception as e:
            logger.warning(f"Failed to generate the interview summary: {e}")
            # Fallback: simple concatenation
            return f"Interviewed {len(interviews)} people in total, including: " + ", ".join([i.agent_name for i in interviews])
