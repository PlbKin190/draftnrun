from typing import Optional
import json

import base64
import tiktoken
from pydantic import BaseModel
from openinference.semconv.trace import OpenInferenceSpanKindValues, SpanAttributes
from openai import OpenAI
from openai.types import Embedding
from openai.types.chat import ChatCompletion
from tenacity import retry, wait_random_exponential, stop_after_attempt

from engine.trace.trace_manager import TraceManager
from engine.agent.agent import ToolDescription
from engine.llm_services.llm_service import LLMService
from engine.llm_services.utils import chat_completion_to_response
from engine.agent.utils import load_str_to_json
from engine.llm_services.constrained_output_models import OutputFormatModel
from settings import settings


class OpenAILLMService(LLMService):
    def __init__(
        self,
        trace_manager: TraceManager,
        model_name: str = "gpt-4o-mini",
        embedding_model_name: str = "text-embedding-3-large",
        default_temperature: float = 0.3,
        model_speech_to_text: str = "whisper-1",
        model_config_text_to_speech: dict[str, str] | None = None,
        base_url: Optional[str] = None,
        api_key: Optional[str] = None,
    ):
        super().__init__(trace_manager=trace_manager)
        if model_config_text_to_speech is None:
            self._model_config_text_to_speech = {"model": "tts-1", "speaker_type": "nova"}
        if api_key is None:
            api_key = settings.OPENAI_API_KEY
        self._client = OpenAI(api_key=api_key, base_url=base_url)
        self._completion_model = model_name
        self._embedding_model = embedding_model_name
        self._default_temperature = default_temperature
        self._model_speech_to_text = model_speech_to_text

    @retry(wait=wait_random_exponential(multiplier=1, max=40), stop=stop_after_attempt(3))
    def embed(
        self,
        input_text: str | list[str],
    ) -> list[Embedding]:
        return self._client.embeddings.create(
            input=input_text,
            model=self._embedding_model,
        ).data

    @retry(wait=wait_random_exponential(multiplier=1, max=60), stop=stop_after_attempt(5))
    def complete(
        self,
        messages: list[dict],
        temperature: float = None,
    ) -> str:
        temperature = temperature or self._default_temperature
        return (
            self._client.chat.completions.create(
                messages=messages,
                model=self._completion_model,
                temperature=temperature,
            )
            .choices[0]
            .message.content
        )

    @retry(wait=wait_random_exponential(multiplier=1, max=60), stop=stop_after_attempt(5))
    def web_search(
        self,
        query: str,
    ) -> str:
        return self._client.responses.create(
            input=query,
            tools=[{"type": "web_search_preview"}],
            model=self._completion_model,
        ).output_text

    @retry(wait=wait_random_exponential(multiplier=1, max=40), stop=stop_after_attempt(3))
    def _function_call_without_trace(
        self,
        messages: list[dict],
        temperature: Optional[float] = None,
        tools: Optional[list[ToolDescription]] = None,
        tool_choice: str = "auto",
    ) -> ChatCompletion:
        if tools is None:
            tools = []
        temperature = temperature or self._default_temperature
        response = self._client.chat.completions.create(
            messages=messages,
            model=self._completion_model,
            temperature=temperature,
            tools=[tool.openai_format for tool in tools],
            tool_choice=tool_choice,
        )
        return response

    @retry(wait=wait_random_exponential(multiplier=1, max=40), stop=stop_after_attempt(3))
    def constrained_complete(
        self,
        messages: list[dict],
        temperature: float = None,
        response_format: Optional[BaseModel | str] = None,
    ) -> BaseModel:
        messages = chat_completion_to_response(messages)
        kwargs = {
            "input": messages,
            "model": self._completion_model,
            "temperature": temperature or self._default_temperature,
        }
        if isinstance(response_format, str):
            response_format = load_str_to_json(response_format)
            # validate with the basemodel OutputFormatModel
            response_format["strict"] = True
            response_format["type"] = "json_schema"
            response_format = OutputFormatModel(**response_format).model_dump(exclude_none=True, exclude_unset=True)
            kwargs["text"] = {"format": response_format}
            response = self._client.responses.parse(**kwargs)
            return response.output_text
        elif issubclass(response_format, BaseModel):
            kwargs["text_format"] = response_format
            response = self._client.responses.parse(**kwargs)
            return response.output_parsed
        else:
            raise ValueError("response_format must be a string or a BaseModel subclass.")

    @retry(wait=wait_random_exponential(multiplier=1, max=40), stop=stop_after_attempt(3))
    def generate_transcript(self, audio_path: str, language: str) -> str:
        span_name = "SpeechToText"
        with self.trace_manager.start_span(span_name) as span:
            if audio_path is None:
                transcription = ""
            else:
                audio_file = open(audio_path, "rb")
                transcription = self._client.audio.transcriptions.create(
                    model=self._model_speech_to_text, file=audio_file, language=language, response_format="text"
                )
            span.set_attributes(
                {
                    SpanAttributes.OPENINFERENCE_SPAN_KIND: OpenInferenceSpanKindValues.LLM.value,
                }
            )
            span.set_attributes(
                {
                    "audio_path_for_transcription": audio_path,
                    "transcription_result": transcription,
                }
            )

        return transcription

    @retry(wait=wait_random_exponential(multiplier=1, max=40), stop=stop_after_attempt(3))
    def generate_speech_from_text(self, transcription: str, speech_audio_path: str) -> str:
        span_name = "TextToSpeech"
        with self.trace_manager.start_span(span_name) as span:
            response = self._client.audio.speech.create(
                model=self._model_config_text_to_speech["model"],
                voice=self._model_config_text_to_speech["speaker_type"],
                input=transcription,
            )
            response.write_to_file(speech_audio_path)
            span.set_attributes(
                {
                    SpanAttributes.OPENINFERENCE_SPAN_KIND: OpenInferenceSpanKindValues.LLM.value,
                }
            )
            span.set_attributes(
                {
                    "transcription": transcription,
                    "path_of_speech_generation": speech_audio_path,
                    "text_to_speech_config": json.dumps(self._model_config_text_to_speech, indent=2),
                }
            )
        return speech_audio_path

    def _format_image_content(self, image_content_list: list[bytes]) -> list[dict[str, str]]:
        return [
            {
                "type": "image_url",
                "image_url": {"url": f"data:image/jpeg;base64,{base64.b64encode(image_content).decode('utf-8')}"},
            }
            for image_content in image_content_list
        ]

    def complete_with_files(
        self,
        messages: list[dict],
        files: list[bytes],
        temperature: float = None,
    ) -> str:
        raise NotImplementedError("This method is not implemented for OpenAI LLM service.")

    def get_token_size(self, content: str) -> int:
        return len(tiktoken.encoding_for_model(self._completion_model).encode(content))
