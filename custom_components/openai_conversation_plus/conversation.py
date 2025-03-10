"""Conversation support for OpenAI."""
import asyncio
from collections.abc import AsyncGenerator, Callable
from datetime import datetime
import json
from typing import Any, Literal, cast

from mem0 import AsyncMemoryClient
import openai
from openai._streaming import AsyncStream
from openai._types import NOT_GIVEN
from openai.types.chat import (
    ChatCompletionAssistantMessageParam,
    ChatCompletionChunk,
    ChatCompletionMessageParam,
    ChatCompletionMessageToolCallParam,
    ChatCompletionToolMessageParam,
    ChatCompletionToolParam,
)
from openai.types.chat.chat_completion_message_tool_call_param import Function
from openai.types.shared_params import FunctionDefinition
from voluptuous_openapi import convert

from homeassistant.components import assist_pipeline, conversation
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_LLM_HASS_API, MATCH_ALL
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import chat_session, device_registry as dr, intent, llm
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from homeassistant.util import dt as dt_util

from . import OpenAIPlusConfigEntry
from .const import (
    CONF_CHAT_MODEL,
    CONF_MAX_TOKENS,
    CONF_MEMORY_API_KEY,
    CONF_MEMORY_URL,
    CONF_MEMORY_USER_ID_MAP,
    CONF_PROMPT,
    CONF_REASONING_EFFORT,
    CONF_TEMPERATURE,
    CONF_TOP_P,
    DOMAIN,
    LOGGER as _LOGGER,
    RECOMMENDED_CHAT_MODEL,
    RECOMMENDED_MAX_TOKENS,
    RECOMMENDED_REASONING_EFFORT,
    RECOMMENDED_TEMPERATURE,
    RECOMMENDED_TOP_P,
)
from .memory import MemorySearchResults, MemorySettings, format_memories

# Max number of back and forth with the LLM to generate a response
MAX_TOOL_ITERATIONS = 10


# noinspection PyUnusedLocal
async def async_setup_entry(
    hass: HomeAssistant,
    config_entry: OpenAIPlusConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up conversation entities."""
    agent = OpenAIConversationPlusEntity(config_entry)
    async_add_entities([agent])


def _format_tool(
    tool: llm.Tool, custom_serializer: Callable[[Any], Any] | None
) -> ChatCompletionToolParam:
    """Format tool specification."""
    tool_spec = FunctionDefinition(
        name=tool.name,
        parameters=convert(tool.parameters, custom_serializer=custom_serializer),
    )
    if tool.description:
        tool_spec["description"] = tool.description
    return ChatCompletionToolParam(type="function", function=tool_spec)


def _convert_content_to_param(
    content: conversation.Content,
) -> ChatCompletionMessageParam:
    """Convert any native chat message for this agent to the native format."""
    if content.role == "tool_result":
        assert type(content) is conversation.ToolResultContent
        return ChatCompletionToolMessageParam(
            role="tool",
            tool_call_id=content.tool_call_id,
            content=json.dumps(content.tool_result),
        )
    if content.role != "assistant" or not content.tool_calls:  # type: ignore[union-attr]
        role = content.role
        if role == "system":
            role = "developer"
        return cast(
            ChatCompletionMessageParam,
            {"role": role, "content": content.content},  # type: ignore[union-attr]
        )

    # Handle the Assistant content including tool calls.
    assert type(content) is conversation.AssistantContent
    return ChatCompletionAssistantMessageParam(
        role="assistant",
        content=content.content,
        tool_calls=[
            ChatCompletionMessageToolCallParam(
                id=tool_call.id,
                function=Function(
                    arguments=json.dumps(tool_call.tool_args),
                    name=tool_call.tool_name,
                ),
                type="function",
            )
            for tool_call in content.tool_calls
        ],
    )


async def _transform_stream(
    result: AsyncStream[ChatCompletionChunk],
) -> AsyncGenerator[conversation.AssistantContentDeltaDict]:
    """Transform an OpenAI delta stream into HA format."""
    current_tool_call: dict | None = None

    async for chunk in result:
        _LOGGER.debug("Received chunk: %s", chunk)
        choice = chunk.choices[0]

        if choice.finish_reason:
            if current_tool_call:
                yield {
                    "tool_calls": [
                        llm.ToolInput(
                            id=current_tool_call["id"],
                            tool_name=current_tool_call["tool_name"],
                            tool_args=json.loads(current_tool_call["tool_args"]),
                        )
                    ]
                }

            break

        delta = chunk.choices[0].delta

        # We can yield delta messages not continuing or starting tool calls
        if current_tool_call is None and not delta.tool_calls:
            yield {  # type: ignore[misc]
                key: value
                for key in ("role", "content")
                if (value := getattr(delta, key)) is not None
            }
            continue

        # When doing tool calls, we should always have a tool call
        # object or we have gotten stopped above with a finish_reason set.
        # noinspection PyUnboundLocalVariable
        if (
            not delta.tool_calls
            or not (delta_tool_call := delta.tool_calls[0])
            or not delta_tool_call.function
        ):
            raise ValueError("Expected delta with tool call")

        if current_tool_call and delta_tool_call.index == current_tool_call["index"]:
            current_tool_call["tool_args"] += delta_tool_call.function.arguments or ""
            continue

        # We got tool call with new index, so we need to yield the previous
        if current_tool_call:
            yield {
                "tool_calls": [
                    llm.ToolInput(
                        id=current_tool_call["id"],
                        tool_name=current_tool_call["tool_name"],
                        tool_args=json.loads(current_tool_call["tool_args"]),
                    )
                ]
            }

        current_tool_call = {
            "index": delta_tool_call.index,
            "id": delta_tool_call.id,
            "tool_name": delta_tool_call.function.name,
            "tool_args": delta_tool_call.function.arguments or "",
        }


class OpenAIConversationPlusEntity(
    conversation.ConversationEntity, conversation.AbstractConversationAgent
):
    """OpenAI conversation agent."""

    _attr_has_entity_name = True
    _attr_name = None

    _memory_client = None
    _memory_min_score = 0.25
    _memory_update_task: asyncio.Task | None = None
    _memory_last_update_time: datetime
    _memory_settings: MemorySettings

    def __init__(self, entry: OpenAIPlusConfigEntry) -> None:
        """Initialize the agent."""
        self.entry = entry
        self._attr_unique_id = entry.entry_id
        self._attr_device_info = dr.DeviceInfo(
            identifiers={(DOMAIN, entry.entry_id)},
            name=entry.title,
            manufacturer="OpenAI",
            model="ChatGPT",
            entry_type=dr.DeviceEntryType.SERVICE,
        )
        if self.entry.options.get(CONF_LLM_HASS_API):
            self._attr_supported_features = (
                conversation.ConversationEntityFeature.CONTROL
            )

        self._memory_last_update_time = dt_util.utcnow()
        self._memory_settings = {
            "throttle_seconds": 30,
            "message_history_length": 5,
            "memory_min_score": 0.25,
        }

    @property
    def supported_languages(self) -> list[str] | Literal["*"]:
        """Return a list of supported languages."""
        return MATCH_ALL

    @property
    def _memory(self) -> AsyncMemoryClient:
        if self._memory_client is None:
            self._memory_client = AsyncMemoryClient(
                api_key=self.entry.options.get(CONF_MEMORY_API_KEY),
                host=self.entry.options.get(CONF_MEMORY_URL),
            )
        return self._memory_client

    async def async_added_to_hass(self) -> None:
        """When entity is added to Home Assistant."""
        await super().async_added_to_hass()
        assist_pipeline.async_migrate_engine(
            self.hass, "conversation", self.entry.entry_id, self.entity_id
        )
        conversation.async_set_agent(self.hass, self.entry, self)
        self.entry.async_on_unload(
            self.entry.add_update_listener(self._async_entry_update_listener)
        )

    async def async_will_remove_from_hass(self) -> None:
        """When entity will be removed from Home Assistant."""
        conversation.async_unset_agent(self.hass, self.entry)
        await super().async_will_remove_from_hass()

    async def async_process(
        self, user_input: conversation.ConversationInput
    ) -> conversation.ConversationResult:
        """Process a sentence."""
        mem_params = {
            "limit": 10,
            "agent_id": user_input.agent_id,
        }
        if user_id := self._memory_user_id(user_input):
            mem_params["user_id"] = user_id
        try:
            # noinspection PyTypeChecker
            memory_data: MemorySearchResults = await self._memory.search(
                user_input.text,
                **mem_params,
            )
        except Exception as err:  # noqa: BLE001
            _LOGGER.error("Error searching memory: %s", err)
            memory_data = {"results": [], "relations": []}

        formatted_memory_string = format_memories(memory_data, self._memory_min_score)
        if user_input.extra_system_prompt:
            user_input.extra_system_prompt += f"\n\n{formatted_memory_string}"
        else:
            user_input.extra_system_prompt = formatted_memory_string

        with (
            chat_session.async_get_chat_session(
                self.hass, user_input.conversation_id
            ) as session,
            conversation.async_get_chat_log(self.hass, session, user_input) as chat_log,
        ):
            return await self._async_handle_message(user_input, chat_log)

    async def _async_handle_message(
        self,
        user_input: conversation.ConversationInput,
        chat_log: conversation.ChatLog,
    ) -> conversation.ConversationResult:
        """Call the API."""
        options = self.entry.options

        try:
            await chat_log.async_update_llm_data(
                DOMAIN,
                user_input,
                options.get(CONF_LLM_HASS_API),
                options.get(CONF_PROMPT),
            )
        except conversation.ConverseError as err:
            return err.as_conversation_result()

        tools: list[ChatCompletionToolParam] | None = None
        if chat_log.llm_api:
            tools = [
                _format_tool(tool, chat_log.llm_api.custom_serializer)
                for tool in chat_log.llm_api.tools
            ]

        model = options.get(CONF_CHAT_MODEL, RECOMMENDED_CHAT_MODEL)
        messages = [_convert_content_to_param(content) for content in chat_log.content]

        client = self.entry.runtime_data

        # To prevent infinite loops, we limit the number of iterations
        for _iteration in range(MAX_TOOL_ITERATIONS):
            model_args = {
                "model": model,
                "messages": messages,
                "tools": tools or NOT_GIVEN,
                "max_completion_tokens": options.get(
                    CONF_MAX_TOKENS, RECOMMENDED_MAX_TOKENS
                ),
                "top_p": options.get(CONF_TOP_P, RECOMMENDED_TOP_P),
                "temperature": options.get(CONF_TEMPERATURE, RECOMMENDED_TEMPERATURE),
                "user": chat_log.conversation_id,
                "stream": True,
            }

            if model.startswith("o"):
                model_args["reasoning_effort"] = options.get(
                    CONF_REASONING_EFFORT, RECOMMENDED_REASONING_EFFORT
                )

            try:
                result = await client.chat.completions.create(**model_args)
            except openai.RateLimitError as err:
                _LOGGER.error("Rate limited by OpenAI: %s", err)
                raise HomeAssistantError("Rate limited or insufficient funds") from err
            except openai.OpenAIError as err:
                _LOGGER.error("Error talking to OpenAI: %s", err)
                raise HomeAssistantError("Error talking to OpenAI") from err

            messages.extend(
                [
                    _convert_content_to_param(content)
                    async for content in chat_log.async_add_delta_content_stream(
                        user_input.agent_id, _transform_stream(result)
                    )
                ]
            )

            if not chat_log.unresponded_tool_results:
                break

        # Schedule throttled memory update
        await self._schedule_memory_update(chat_log, user_input)

        intent_response = intent.IntentResponse(language=user_input.language)
        assert type(chat_log.content[-1]) is conversation.AssistantContent
        intent_response.async_set_speech(chat_log.content[-1].content or "")
        return conversation.ConversationResult(
            response=intent_response, conversation_id=chat_log.conversation_id
        )

    # noinspection PyMethodMayBeStatic
    async def _async_entry_update_listener(
        self, hass: HomeAssistant, entry: ConfigEntry
    ) -> None:
        """Handle options update."""
        # Reload as we update device info + entity name + supported features
        await hass.config_entries.async_reload(entry.entry_id)

    def _memory_user_id(self, user_input: conversation.ConversationInput) -> str | None:
        if user_input.context and user_input.context.user_id:
            user_ids = self.entry.options.get(CONF_MEMORY_USER_ID_MAP, {})
            if user_id_mapped := user_ids.get(user_input.context.user_id):
                return user_id_mapped
        return None

    async def _schedule_memory_update(
        self, chat_log: conversation.ChatLog, user_input: conversation.ConversationInput
    ):
        """Schedule a throttled memory update."""
        current_time = dt_util.utcnow()
        time_diff = (current_time - self._memory_last_update_time).total_seconds()
        if time_diff < self._memory_settings["throttle_seconds"]:
            if self._memory_update_task and not self._memory_update_task.done():
                self._memory_update_task.cancel()
                _LOGGER.debug("Cancelled previous memory update task.")

        self._memory_last_update_time = current_time
        self._memory_update_task = self.hass.async_create_task(
            self._throttled_memory_update(chat_log, user_input)
        )

    async def _throttled_memory_update(
        self, chat_log: conversation.ChatLog, user_input: conversation.ConversationInput
    ):
        """Throttled memory update logic."""
        await asyncio.sleep(self._memory_settings["throttle_seconds"])

        messages_to_process = [
            {"role": msg.role, "content": msg.content}
            for msg in chat_log.content[
                -self._memory_settings["message_history_length"] :
            ]
            if isinstance(
                msg, (conversation.UserContent, conversation.AssistantContent)
            )
            and msg.content
        ]

        if messages_to_process:
            mem_params = {
                "agent_id": user_input.agent_id,
            }
            if user_id := self._memory_user_id(user_input):
                mem_params["user_id"] = user_id
            await self._memory.add(
                messages=messages_to_process,
                **mem_params,
            )
        else:
            _LOGGER.debug("No messages to process for memory update.")
