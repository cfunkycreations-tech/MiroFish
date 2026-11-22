"""
Ontology generation service
Endpoint 1: analyses the text and generates the entity and relation type definitions a social simulation needs
"""

import json
from typing import Dict, Any, List, Optional
from ..utils.llm_client import LLMClient


# The system prompt used to generate the ontology
ONTOLOGY_SYSTEM_PROMPT = """You are a professional knowledge graph ontology designer. Your task is to analyse the given text and simulation goal, and design the entity types and relation types that suit **social media opinion simulation**.

**Important: you must output valid JSON and nothing else.**

## Core context

We are building a **social media opinion simulation system**. In that system:
- every entity is an account or actor that can speak, interact and spread information on social media
- entities influence, repost, comment on and reply to each other
- we need to simulate how each side reacts to an opinion event and how information travels

So **every entity must be a real-world actor that can speak and interact on social media**:

**It may be**:
- a specific individual (public figure, person involved, opinion leader, expert, ordinary person)
- a company or business (including its official account)
- an organization (university, association, NGO, union, ...)
- a government body or regulator
- a media organization (newspaper, TV station, independent outlet, website)
- a social media platform itself
- a representative of a specific group (alumni association, fan club, advocacy group, ...)

**It must not be**:
- an abstract concept (such as "public opinion", "emotion", "trend")
- a theme or topic (such as "academic integrity", "education reform")
- a viewpoint or stance (such as "supporters", "opponents")

## Output format

Output JSON with this structure:

```json
{
    "entity_types": [
        {
            "name": "entity type name (English, PascalCase)",
            "description": "a short description (English, at most 100 characters)",
            "attributes": [
                {
                    "name": "attribute name (English, snake_case)",
                    "type": "text",
                    "description": "the attribute description"
                }
            ],
            "examples": ["example entity 1", "example entity 2"]
        }
    ],
    "edge_types": [
        {
            "name": "relation type name (English, UPPER_SNAKE_CASE)",
            "description": "a short description (English, at most 100 characters)",
            "source_targets": [
                {"source": "source entity type", "target": "target entity type"}
            ],
            "attributes": []
        }
    ],
    "analysis_summary": "a short analysis of the text (in English)"
}
```

## Design guidelines (extremely important!)

### 1. Entity type design - must be followed strictly

**Count: exactly 10 entity types**

**Hierarchy (specific types and fallback types are both required)**:

Your 10 entity types must include:

A. **Fallback types (required; put them last)**:
   - `Person`: the fallback for any individual. Use it when a person does not fit a more specific type.
   - `Organization`: the fallback for any organization. Use it when an organization does not fit a more specific type.

B. **Specific types (8, designed from the text)**:
   - design more specific types for the main actors in the text
   - e.g. for an academic event: `Student`, `Professor`, `University`
   - e.g. for a business event: `Company`, `CEO`, `Employee`

**Why fallback types are needed**:
- the text mentions all kinds of people, such as "a school teacher", "a passer-by", "some netizen"
- when no specific type fits, they must fall back to `Person`
- likewise, small organizations and ad-hoc groups fall back to `Organization`

**How to design the specific types**:
- identify the actor types that appear often or matter most in the text
- each specific type needs clear boundaries so they do not overlap
- the description must make clear how the type differs from its fallback type

### 2. Relation type design

- count: 6-10
- relations should reflect real interactions on social media
- make sure the source_targets of the relations cover the entity types you defined

### 3. Attribute design

- 1-3 key attributes per entity type
- **Note**: attribute names must not be `name`, `uuid`, `group_id`, `created_at` or `summary` (these are reserved)
- Good choices: `full_name`, `title`, `role`, `position`, `location`, `description` and so on

## Entity type reference

**Individuals (specific)**:
- Student: a student
- Professor: a professor/scholar
- Journalist: a journalist
- Celebrity: a celebrity/influencer
- Executive: an executive
- Official: a government official
- Lawyer: a lawyer
- Doctor: a doctor

**Individuals (fallback)**:
- Person: any individual (used when none of the specific types fit)

**Organizations (specific)**:
- University: a university
- Company: a company or business
- GovernmentAgency: a government body
- MediaOutlet: a media organization
- Hospital: a hospital
- School: a school
- NGO: a non-governmental organization

**Organizations (fallback)**:
- Organization: any organization (used when none of the specific types fit)

## Relation type reference

- WORKS_FOR: works for
- STUDIES_AT: studies at
- AFFILIATED_WITH: is affiliated with
- REPRESENTS: represents
- REGULATES: regulates
- REPORTS_ON: reports on
- COMMENTS_ON: comments on
- RESPONDS_TO: responds to
- SUPPORTS: supports
- OPPOSES: opposes
- COLLABORATES_WITH: collaborates with
- COMPETES_WITH: competes with
"""


class OntologyGenerator:
    """
    Ontology generator
    Analyses the text and generates the entity and relation type definitions
    """
    
    def __init__(self, llm_client: Optional[LLMClient] = None):
        self.llm_client = llm_client or LLMClient()
    
    def generate(
        self,
        document_texts: List[str],
        simulation_requirement: str,
        additional_context: Optional[str] = None
    ) -> Dict[str, Any]:
        """
        Generate the ontology definition
        
        Args:
            document_texts: the list of document texts
            simulation_requirement: a description of the simulation goal
            additional_context: extra context
            
        Returns:
            the ontology definition (entity_types, edge_types and so on)
        """
        # Build the user message
        user_message = self._build_user_message(
            document_texts, 
            simulation_requirement,
            additional_context
        )
        
        messages = [
            {"role": "system", "content": ONTOLOGY_SYSTEM_PROMPT},
            {"role": "user", "content": user_message}
        ]
        
        # Call the LLM
        result = self.llm_client.chat_json(
            messages=messages,
            temperature=0.3,
            max_tokens=4096
        )
        
        # Validate and post-process
        result = self._validate_and_process(result)
        
        return result
    
    # Maximum length of the text sent to the LLM (50k characters)
    MAX_TEXT_LENGTH_FOR_LLM = 50000
    
    def _build_user_message(
        self,
        document_texts: List[str],
        simulation_requirement: str,
        additional_context: Optional[str]
    ) -> str:
        """Build the user message"""
        
        # Merge the texts
        combined_text = "\n\n---\n\n".join(document_texts)
        original_length = len(combined_text)
        
        # Truncate when the text exceeds 50k characters (only the LLM input is affected, not graph construction)
        if len(combined_text) > self.MAX_TEXT_LENGTH_FOR_LLM:
            combined_text = combined_text[:self.MAX_TEXT_LENGTH_FOR_LLM]
            combined_text += f"\n\n...(the original is {original_length} characters; the first {self.MAX_TEXT_LENGTH_FOR_LLM} are used for the ontology analysis)..."
        
        message = f"""## Simulation goal

{simulation_requirement}

## Document content

{combined_text}
"""
        
        if additional_context:
            message += f"""
## Extra notes

{additional_context}
"""
        
        message += """
Based on the content above, design the entity types and relation types that suit social opinion simulation.

**Rules that must be followed**:
1. Output exactly 10 entity types
2. The last 2 must be the fallback types: Person (individuals) and Organization (organizations)
3. The first 8 are specific types designed from the text
4. Every entity type must be a real-world actor that can speak; abstract concepts are not allowed
5. Attribute names must not use reserved words such as name, uuid or group_id; use full_name, org_name and so on instead
"""
        
        return message
    
    def _validate_and_process(self, result: Dict[str, Any]) -> Dict[str, Any]:
        """Validate and post-process the result"""
        
        # Make sure the required fields are present
        if "entity_types" not in result:
            result["entity_types"] = []
        if "edge_types" not in result:
            result["edge_types"] = []
        if "analysis_summary" not in result:
            result["analysis_summary"] = ""
        
        # Validate the entity types
        for entity in result["entity_types"]:
            if "attributes" not in entity:
                entity["attributes"] = []
            if "examples" not in entity:
                entity["examples"] = []
            # Make sure the description is at most 100 characters
            if len(entity.get("description", "")) > 100:
                entity["description"] = entity["description"][:97] + "..."
        
        # Validate the relation types
        for edge in result["edge_types"]:
            if "source_targets" not in edge:
                edge["source_targets"] = []
            if "attributes" not in edge:
                edge["attributes"] = []
            if len(edge.get("description", "")) > 100:
                edge["description"] = edge["description"][:97] + "..."
        
        # Zep API limit: at most 10 custom entity types and 10 custom edge types
        MAX_ENTITY_TYPES = 10
        MAX_EDGE_TYPES = 10
        
        # Fallback type definitions
        person_fallback = {
            "name": "Person",
            "description": "Any individual person not fitting other specific person types.",
            "attributes": [
                {"name": "full_name", "type": "text", "description": "Full name of the person"},
                {"name": "role", "type": "text", "description": "Role or occupation"}
            ],
            "examples": ["ordinary citizen", "anonymous netizen"]
        }
        
        organization_fallback = {
            "name": "Organization",
            "description": "Any organization not fitting other specific organization types.",
            "attributes": [
                {"name": "org_name", "type": "text", "description": "Name of the organization"},
                {"name": "org_type", "type": "text", "description": "Type of organization"}
            ],
            "examples": ["small business", "community group"]
        }
        
        # Check whether the fallback types are already present
        entity_names = {e["name"] for e in result["entity_types"]}
        has_person = "Person" in entity_names
        has_organization = "Organization" in entity_names
        
        # The fallback types that need adding
        fallbacks_to_add = []
        if not has_person:
            fallbacks_to_add.append(person_fallback)
        if not has_organization:
            fallbacks_to_add.append(organization_fallback)
        
        if fallbacks_to_add:
            current_count = len(result["entity_types"])
            needed_slots = len(fallbacks_to_add)
            
            # When adding them would exceed 10, drop some of the existing types
            if current_count + needed_slots > MAX_ENTITY_TYPES:
                # Work out how many to drop
                to_remove = current_count + needed_slots - MAX_ENTITY_TYPES
                # Drop from the end (the important specific types come first)
                result["entity_types"] = result["entity_types"][:-to_remove]
            
            # Add the fallback types
            result["entity_types"].extend(fallbacks_to_add)
        
        # Finally make sure the limit is respected (defensive programming)
        if len(result["entity_types"]) > MAX_ENTITY_TYPES:
            result["entity_types"] = result["entity_types"][:MAX_ENTITY_TYPES]
        
        if len(result["edge_types"]) > MAX_EDGE_TYPES:
            result["edge_types"] = result["edge_types"][:MAX_EDGE_TYPES]
        
        return result
    
    def generate_python_code(self, ontology: Dict[str, Any]) -> str:
        """
        Convert the ontology definition into Python code (similar to ontology.py)
        
        Args:
            ontology: the ontology definition
            
        Returns:
            the Python code as a string
        """
        code_lines = [
            '"""',
            'Custom entity type definitions',
            'generated automatically by MiroFish for social opinion simulation',
            '"""',
            '',
            'from pydantic import Field',
            'from zep_cloud.external_clients.ontology import EntityModel, EntityText, EdgeModel',
            '',
            '',
            '# ============== Entity type definitions ==============',
            '',
        ]
        
        # Generate the entity types
        for entity in ontology.get("entity_types", []):
            name = entity["name"]
            desc = entity.get("description", f"A {name} entity.")
            
            code_lines.append(f'class {name}(EntityModel):')
            code_lines.append(f'    """{desc}"""')
            
            attrs = entity.get("attributes", [])
            if attrs:
                for attr in attrs:
                    attr_name = attr["name"]
                    attr_desc = attr.get("description", attr_name)
                    code_lines.append(f'    {attr_name}: EntityText = Field(')
                    code_lines.append(f'        description="{attr_desc}",')
                    code_lines.append(f'        default=None')
                    code_lines.append(f'    )')
            else:
                code_lines.append('    pass')
            
            code_lines.append('')
            code_lines.append('')
        
        code_lines.append('# ============== Relation type definitions ==============')
        code_lines.append('')
        
        # Generate the relation types
        for edge in ontology.get("edge_types", []):
            name = edge["name"]
            # Convert to a PascalCase class name
            class_name = ''.join(word.capitalize() for word in name.split('_'))
            desc = edge.get("description", f"A {name} relationship.")
            
            code_lines.append(f'class {class_name}(EdgeModel):')
            code_lines.append(f'    """{desc}"""')
            
            attrs = edge.get("attributes", [])
            if attrs:
                for attr in attrs:
                    attr_name = attr["name"]
                    attr_desc = attr.get("description", attr_name)
                    code_lines.append(f'    {attr_name}: EntityText = Field(')
                    code_lines.append(f'        description="{attr_desc}",')
                    code_lines.append(f'        default=None')
                    code_lines.append(f'    )')
            else:
                code_lines.append('    pass')
            
            code_lines.append('')
            code_lines.append('')
        
        # Generate the type dictionary
        code_lines.append('# ============== Type configuration ==============')
        code_lines.append('')
        code_lines.append('ENTITY_TYPES = {')
        for entity in ontology.get("entity_types", []):
            name = entity["name"]
            code_lines.append(f'    "{name}": {name},')
        code_lines.append('}')
        code_lines.append('')
        code_lines.append('EDGE_TYPES = {')
        for edge in ontology.get("edge_types", []):
            name = edge["name"]
            class_name = ''.join(word.capitalize() for word in name.split('_'))
            code_lines.append(f'    "{name}": {class_name},')
        code_lines.append('}')
        code_lines.append('')
        
        # Generate the source_targets map for the edges
        code_lines.append('EDGE_SOURCE_TARGETS = {')
        for edge in ontology.get("edge_types", []):
            name = edge["name"]
            source_targets = edge.get("source_targets", [])
            if source_targets:
                st_list = ', '.join([
                    f'{{"source": "{st.get("source", "Entity")}", "target": "{st.get("target", "Entity")}"}}'
                    for st in source_targets
                ])
                code_lines.append(f'    "{name}": [{st_list}],')
        code_lines.append('}')
        
        return '\n'.join(code_lines)

