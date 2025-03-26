"""Constants for the OpenAI Conversation Plus integration."""

import logging

DOMAIN = "openai_conversation_plus"
LOGGER = logging.getLogger(__package__)

CONF_BASE_URL = "base_url"
CONF_RECOMMENDED = "recommended"
CONF_PROMPT = "prompt"
CONF_CHAT_MODEL = "chat_model"
CONF_FILENAMES = "filenames"
CONF_SMART_CHAT_MODEL = "smart_chat_model"
CONF_MEMORY_API_KEY = "memory_api_key"
CONF_MEMORY_URL = "memory_url"
CONF_MEMORY_USER_ID_MAP = "memory_user_id_map"
RECOMMENDED_CHAT_MODEL = "gpt-4o-mini"
RECOMMENDED_SMART_CHAT_MODEL = "gpt-4o"
CONF_MAX_TOKENS = "max_tokens"
RECOMMENDED_MAX_TOKENS = 150
CONF_TOP_P = "top_p"
CONF_WEB_SEARCH = "web_search"
CONF_WEB_SEARCH_USER_LOCATION = "user_location"
CONF_WEB_SEARCH_CONTEXT_SIZE = "search_context_size"
CONF_WEB_SEARCH_CITY = "city"
CONF_WEB_SEARCH_REGION = "region"
CONF_WEB_SEARCH_COUNTRY = "country"
CONF_WEB_SEARCH_TIMEZONE = "timezone"
RECOMMENDED_TOP_P = 1.0
RECOMMENDED_WEB_SEARCH = False
RECOMMENDED_WEB_SEARCH_CONTEXT_SIZE = "medium"
RECOMMENDED_WEB_SEARCH_USER_LOCATION = False
CONF_TEMPERATURE = "temperature"
RECOMMENDED_TEMPERATURE = 1.0
CONF_REASONING_EFFORT = "reasoning_effort"
RECOMMENDED_REASONING_EFFORT = "low"

UNSUPPORTED_MODELS = [
    "o1-mini",
    "o1-mini-2024-09-12",
    "o1-preview",
    "o1-preview-2024-09-12",
    "gpt-4o-realtime-preview",
    "gpt-4o-realtime-preview-2024-12-17",
    "gpt-4o-realtime-preview-2024-10-01",
    "gpt-4o-mini-realtime-preview",
    "gpt-4o-mini-realtime-preview-2024-12-17",
]
