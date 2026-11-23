"""
LLM client wrapper
Uses the OpenAI format throughout
"""

import json
import logging
import re
import time
from typing import Optional, Dict, Any, List
from openai import OpenAI, RateLimitError, APIError

from ..config import Config

logger = logging.getLogger('mirofish.llm')

# Local, self-hosted providers (Ollama, llama.cpp, LM Studio, OmniRoute, ...) do
# not authenticate, but the OpenAI SDK refuses an empty key, so use a placeholder.
LOCAL_HOSTS = ('127.0.0.1', 'localhost', '0.0.0.0', '[::1]', 'host.docker.internal')
PLACEHOLDER_KEY = 'local'


def is_local_endpoint(base_url: str) -> bool:
    """Whether this base URL points at a local server that needs no API key."""
    host = (base_url or '').split('//', 1)[-1].split('/', 1)[0].rsplit(':', 1)[0].lower()
    return host in LOCAL_HOSTS


def resolve_api_key(api_key: str, base_url: str) -> str:
    """Return a usable key: the configured one, or a placeholder for local servers."""
    if api_key:
        return api_key
    if is_local_endpoint(base_url):
        return PLACEHOLDER_KEY
    return ''  


class LLMClient:
    """The LLM client"""
    
    # Retry configuration for rate limit errors
    # Gemini free tier can impose 10-40s delays, so use aggressive backoff
    MAX_RETRIES = 5
    RETRY_BASE_DELAY = 15  # seconds (15s, 30s, 60s, 120s, 240s)
    
    def __init__(
        self,
        api_key: Optional[str] = None,
        base_url: Optional[str] = None,
        model: Optional[str] = None
    ):
        self.base_url = base_url or Config.LLM_BASE_URL
        self.api_key = resolve_api_key(api_key or Config.LLM_API_KEY, self.base_url)
        self.model = model or Config.LLM_MODEL_NAME
        
        if not self.api_key:
            raise ValueError("LLM_API_KEY is not configured")
        
        self.client = OpenAI(
            api_key=self.api_key,
            base_url=self.base_url
        )
        
        logger.info(f"LLMClient initialized: model={self.model}, base_url={self.base_url}")
    
    def _call_with_retry(self, **kwargs) -> Any:
        """
        Call the OpenAI API with automatic retry on rate limit (429) errors.
        Uses exponential backoff: 5s, 10s, 20s delays.
        """
        last_error = None
        for attempt in range(self.MAX_RETRIES + 1):
            try:
                return self.client.chat.completions.create(**kwargs)
            except RateLimitError as e:
                last_error = e
                if attempt < self.MAX_RETRIES:
                    delay = self.RETRY_BASE_DELAY * (2 ** attempt)
                    logger.warning(
                        f"Rate limited (429). Retry {attempt + 1}/{self.MAX_RETRIES} "
                        f"in {delay}s. Error: {e}"
                    )
                    time.sleep(delay)
                else:
                    logger.error(f"Rate limit exceeded after {self.MAX_RETRIES} retries: {e}")
                    raise
            except APIError as e:
                # Log the full error details for debugging
                logger.error(f"API error (code={e.status_code}): {e}")
                raise
        raise last_error  # Should not reach here, but just in case
    
    def chat(
        self,
        messages: List[Dict[str, str]],
        temperature: float = 0.7,
        max_tokens: int = 4096,
        response_format: Optional[Dict] = None
    ) -> str:
        """
        Send a chat request
        
        Args:
            messages: the message list
            temperature: the temperature
            max_tokens: the maximum number of tokens
            response_format: the response format (such as JSON mode)
            
        Returns:
            the model response text
        """
        kwargs = {
            "model": self.model,
            "messages": messages,
            "temperature": temperature,
            "max_tokens": max_tokens,
        }
        
        if response_format:
            kwargs["response_format"] = response_format
        
        try:
            response = self._call_with_retry(**kwargs)
        except Exception as e:
            # If response_format is not supported (e.g. some providers), retry without it
            if response_format and not isinstance(e, RateLimitError):
                logger.warning(
                    f"LLM call failed with response_format={response_format}, "
                    f"retrying without it. Error: {e}"
                )
                kwargs.pop("response_format", None)
                response = self._call_with_retry(**kwargs)
            else:
                logger.error(f"LLM call failed: {e}")
                raise
        
        content = response.choices[0].message.content
        # Some models (such as MiniMax M2.5) put reasoning inside <think> in the content, which must be removed
        content = re.sub(r'<think>[\s\S]*?</think>', '', content).strip()
        return content
    
    def chat_json(
        self,
        messages: List[Dict[str, str]],
        temperature: float = 0.3,
        max_tokens: int = 4096
    ) -> Dict[str, Any]:
        """
        Send a chat request and return JSON
        
        Args:
            messages: the message list
            temperature: the temperature
            max_tokens: the maximum number of tokens
            
        Returns:
            the parsed JSON object
        """
        response = self.chat(
            messages=messages,
            temperature=temperature,
            max_tokens=max_tokens,
            response_format={"type": "json_object"}
        )
        # Strip the markdown code fence markers
        cleaned_response = response.strip()
        cleaned_response = re.sub(r'^```(?:json)?\s*\n?', '', cleaned_response, flags=re.IGNORECASE)
        cleaned_response = re.sub(r'\n?```\s*$', '', cleaned_response)
        cleaned_response = cleaned_response.strip()

        try:
            return json.loads(cleaned_response)
        except json.JSONDecodeError:
            logger.error(f"LLM returned invalid JSON: {cleaned_response[:500]}...")
            raise ValueError(f"the LLM returned invalid JSON: {cleaned_response[:200]}")

