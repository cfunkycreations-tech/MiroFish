"""
Configuration management
Loads every setting from the .env file at the project root
"""

import os
from dotenv import load_dotenv

# Load the .env file at the project root
# Path: MiroFish/.env (relative to backend/app/config.py)
# The desktop launcher points MIROFISH_ENV_FILE at the .env in the user data directory
_env_override = os.environ.get('MIROFISH_ENV_FILE')
project_root_env = _env_override or os.path.join(os.path.dirname(__file__), '../../.env')

if project_root_env and os.path.exists(project_root_env):
    load_dotenv(project_root_env, override=True)
else:
    # When the root has no .env, fall back to the process environment (production deployments)
    load_dotenv(override=True)


def _resolve_data_dir() -> str:
    """
    Resolve the writable data directory.

    A source deployment still defaults to backend/uploads (unchanged behavior);
    the desktop build (--desktop) has the launcher set MIROFISH_DATA_DIR to the user data directory,
    because the install directory (such as C:\Program Files) is usually read-only.
    """
    data_dir = os.environ.get('MIROFISH_DATA_DIR')
    if data_dir:
        return os.path.abspath(data_dir)
    return os.path.join(os.path.dirname(__file__), '../uploads')


DATA_DIR = _resolve_data_dir()


class Config:
    """The Flask configuration class"""
    
    # Flask configuration
    SECRET_KEY = os.environ.get('SECRET_KEY', 'mirofish-secret-key')
    DEBUG = os.environ.get('FLASK_DEBUG', 'True').lower() == 'true'
    
    # JSON configuration - disable ASCII escaping so non-ASCII text shows as-is (instead of \uXXXX)
    JSON_AS_ASCII = False
    
    # LLM configuration (OpenAI format throughout)
    LLM_API_KEY = os.environ.get('LLM_API_KEY')
    LLM_BASE_URL = os.environ.get('LLM_BASE_URL', 'https://api.openai.com/v1')
    LLM_MODEL_NAME = os.environ.get('LLM_MODEL_NAME', 'gpt-4o-mini')
    
    # Zep configuration
    ZEP_API_KEY = os.environ.get('ZEP_API_KEY')
    
    # File upload configuration
    MAX_CONTENT_LENGTH = 50 * 1024 * 1024  # 50MB
    UPLOAD_FOLDER = DATA_DIR
    ALLOWED_EXTENSIONS = {'pdf', 'md', 'txt', 'markdown'}
    
    # Text processing configuration
    DEFAULT_CHUNK_SIZE = 500  # default chunk size
    DEFAULT_CHUNK_OVERLAP = 50  # default overlap size
    
    # OASIS simulation configuration
    OASIS_DEFAULT_MAX_ROUNDS = int(os.environ.get('OASIS_DEFAULT_MAX_ROUNDS', '10'))
    OASIS_SIMULATION_DATA_DIR = os.path.join(DATA_DIR, 'simulations')
    
    # Actions available on each OASIS platform
    OASIS_TWITTER_ACTIONS = [
        'CREATE_POST', 'LIKE_POST', 'REPOST', 'FOLLOW', 'DO_NOTHING', 'QUOTE_POST'
    ]
    OASIS_REDDIT_ACTIONS = [
        'LIKE_POST', 'DISLIKE_POST', 'CREATE_POST', 'CREATE_COMMENT',
        'LIKE_COMMENT', 'DISLIKE_COMMENT', 'SEARCH_POSTS', 'SEARCH_USER',
        'TREND', 'REFRESH', 'DO_NOTHING', 'FOLLOW', 'MUTE'
    ]
    
    # Report Agent configuration
    REPORT_AGENT_MAX_TOOL_CALLS = int(os.environ.get('REPORT_AGENT_MAX_TOOL_CALLS', '5'))
    REPORT_AGENT_MAX_REFLECTION_ROUNDS = int(os.environ.get('REPORT_AGENT_MAX_REFLECTION_ROUNDS', '2'))
    REPORT_AGENT_TEMPERATURE = float(os.environ.get('REPORT_AGENT_TEMPERATURE', '0.5'))
    
    @classmethod
    def validate(cls):
        """Validate the required configuration"""
        errors = []
        if not cls.LLM_API_KEY:
            errors.append("LLM_API_KEY is not configured")
        if not cls.ZEP_API_KEY:
            errors.append("ZEP_API_KEY is not configured")
        return errors

