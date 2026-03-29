"""
LLM客户端封装
统一使用OpenAI格式调用
"""

import json
import logging
import re
import time
from typing import Optional, Dict, Any, List
from openai import OpenAI, RateLimitError, APIError

from ..config import Config

logger = logging.getLogger('mirofish.llm')


class LLMClient:
    """LLM客户端"""
    
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
        self.api_key = api_key or Config.LLM_API_KEY
        self.base_url = base_url or Config.LLM_BASE_URL
        self.model = model or Config.LLM_MODEL_NAME
        
        if not self.api_key:
            raise ValueError("LLM_API_KEY 未配置")
        
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
        发送聊天请求
        
        Args:
            messages: 消息列表
            temperature: 温度参数
            max_tokens: 最大token数
            response_format: 响应格式（如JSON模式）
            
        Returns:
            模型响应文本
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
        # 部分模型（如MiniMax M2.5）会在content中包含<think>思考内容，需要移除
        content = re.sub(r'<think>[\s\S]*?</think>', '', content).strip()
        return content
    
    def chat_json(
        self,
        messages: List[Dict[str, str]],
        temperature: float = 0.3,
        max_tokens: int = 4096
    ) -> Dict[str, Any]:
        """
        发送聊天请求并返回JSON
        
        Args:
            messages: 消息列表
            temperature: 温度参数
            max_tokens: 最大token数
            
        Returns:
            解析后的JSON对象
        """
        response = self.chat(
            messages=messages,
            temperature=temperature,
            max_tokens=max_tokens,
            response_format={"type": "json_object"}
        )
        # 清理markdown代码块标记
        cleaned_response = response.strip()
        cleaned_response = re.sub(r'^```(?:json)?\s*\n?', '', cleaned_response, flags=re.IGNORECASE)
        cleaned_response = re.sub(r'\n?```\s*$', '', cleaned_response)
        cleaned_response = cleaned_response.strip()

        try:
            return json.loads(cleaned_response)
        except json.JSONDecodeError:
            logger.error(f"LLM returned invalid JSON: {cleaned_response[:500]}...")
            raise ValueError(f"LLM返回的JSON格式无效: {cleaned_response[:200]}")

