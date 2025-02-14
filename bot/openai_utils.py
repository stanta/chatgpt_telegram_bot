import base64
from io import BytesIO
import config
import logging

import tiktoken
import openai
from openai import AsyncOpenAI, OpenAI

# client = AsyncOpenAI(api_key=config.openai_api_key)
client = OpenAI(api_key=config.openai_api_key, base_url=config.openai_api_base)


# setup openai
# if config.openai_api_base is not None:
#     # TODO: The 'openai.api_base' option isn't read in the client API. You will need to pass it when you instantiate the client, e.g. 'OpenAI(base_url=config.openai_api_base)'
#     # openai.api_base = config.openai_api_base
# if config.openai_api_organization is not None:
#     # TODO: The 'openai.organization' option isn't read in the client API. You will need to pass it when you instantiate the client, e.g. 'OpenAI(organization=config.openai_api_organization)'
#     # openai.organization = config.openai_api_organization
logger = logging.getLogger(__name__)


OPENAI_COMPLETION_OPTIONS = {
    "temperature": 0.7,
    "max_tokens": 2048,
    "top_p": 1,
    "frequency_penalty": 0.5,
    "presence_penalty": 0.5,
    # "include_usage": True
    # "request_timeout": 60.0,
}

TOKEN_LEN = 3
def len_in_tokens (tokens):
    tokens = tokens.split(' ')
    lentok = 0
    for token in tokens:
        if len(token) > 0 and len(token) < TOKEN_LEN:
            lentok += 1
        else:
            lentok += int(len(token) / TOKEN_LEN) + 1
    return lentok
                    
                    
def calculate_total_content_length(messages):
    total_length = 0
    for message in messages:
        if 'content' in message:
                total_length += len_in_tokens (message['content'])
        if 'assistant' in message:
                total_length += len_in_tokens (message['assistant'])
        if 'user' in message:
                total_length += len_in_tokens (message['user'][0]['text'])
    return total_length

class ChatGPT:
    def __init__(self, model="gpt-3.5-turbo"):
        assert model in config.models['available_text_models'], f"Unknown model: {model}"
        self.model = model
        
    async def convolute_dialog (self, dialog_messages):
        pit_stop_message_number = 0
        answer = None
        retries = 5
                
        n_input_tokens = calculate_total_content_length(dialog_messages)
        if n_input_tokens  > config.models['info'][self.model]['context_window_size'] * 0.9:
            pit_stop_message_number = len(dialog_messages)
            answer = None
            while answer is None and retries > 0 :
                retries -= 1
                try:
                    messages = self._generate_prompt_messages(config.chat_modes["assistant"]["prompt_resume"], dialog_messages, "assistant" )
                    r = client.chat.completions.create(model=self.model,
                    messages=messages,
                    **OPENAI_COMPLETION_OPTIONS)
                    answer = r.choices[0].message.content        
                except openai.InvalidRequestError as e:  # too many tokens
                    logger.warning(f"No answer when convol_dialog because of {e} ")
                    
        return  pit_stop_message_number, answer

    async def send_message(self, message, dialog_messages=[], chat_mode="assistant"):
        if chat_mode not in config.chat_modes.keys():
            raise ValueError(f"Chat mode {chat_mode} is not supported")

        n_dialog_messages_before = len(dialog_messages)
        answer = None
        n_input_tokens = 0
        n_output_tokens = 0
        while answer is None:
            try:
                if self.model in config.models['available_text_models'] and config.models['info'][self.model]['type'] == 'chat_completion' :
                    messages = self._generate_prompt_messages(message, dialog_messages, chat_mode)

                    r = client.chat.completions.create(model=self.model,
                    messages=messages,
                    **OPENAI_COMPLETION_OPTIONS)
                    answer = r.choices[0].message.content
                elif self.model == "text-davinci-003":
                    prompt = self._generate_prompt(message, dialog_messages, chat_mode)
                    r = client.completions.create(engine=self.model,
                    prompt=prompt,
                    **OPENAI_COMPLETION_OPTIONS)
                    answer = r.choices[0].text
                else:
                    raise ValueError(f"Unknown model: {self.model}")

                answer = self._postprocess_answer(answer)
                #TODO: fix this tokens!
                #n_input_tokens, n_output_tokens = r.usage.prompt_tokens, r.usage.completion_tokens
                n_input_tokens = calculate_total_content_length(messages)
                n_output_tokens = len_in_tokens(answer)
                    
            except openai.InvalidRequestError as e:  # too many tokens
                if len(dialog_messages) == 0:
                    raise ValueError("Dialog messages is reduced to zero, but still has too many tokens to make completion") from e

                # forget first message in dialog_messages
                dialog_messages = dialog_messages[1:]

        n_first_dialog_messages_removed = n_dialog_messages_before - len(dialog_messages)

        return answer, (n_input_tokens, n_output_tokens), n_first_dialog_messages_removed

    async def send_message_stream(self, message, dialog_messages=[], chat_mode="assistant"):
        if chat_mode not in config.chat_modes.keys():
            raise ValueError(f"Chat mode {chat_mode} is not supported")

        n_dialog_messages_before = len(dialog_messages)
        answer = None
        n_input_tokens = 0
        n_output_tokens = 0
        n_first_dialog_messages_removed = 0
        while answer is None:
            try:
                if self.model in config.models['available_text_models'] and config.models['info'][self.model]['type'] == 'chat_completion' :
                    messages = self._generate_prompt_messages(message, dialog_messages, chat_mode)

                    r_gen = client.chat.completions.create(model=self.model,
                    # r_gen =  openai.ChatCompletion.create(
                        messages=messages,
                        stream=True,
                        **OPENAI_COMPLETION_OPTIONS)

                    answer = ""
                    for r_item in r_gen:
                        delta = r_item.choices[0].delta

                        if "content" in delta:
                            answer += delta.content
                            n_input_tokens, n_output_tokens = self._count_tokens_from_messages(messages, answer, model=self.model)
                            n_first_dialog_messages_removed = 0

                            yield "not_finished", answer, (n_input_tokens, n_output_tokens), n_first_dialog_messages_removed


                elif self.model == "text-davinci-003":
                    prompt = self._generate_prompt(message, dialog_messages, chat_mode)
                    r_gen = client.completions.create(engine=self.model,
                    prompt=prompt,
                    stream=True,
                    **OPENAI_COMPLETION_OPTIONS)

                    answer = ""
                    async for r_item in r_gen:
                        answer += r_item.choices[0].text
                        n_input_tokens, n_output_tokens = self._count_tokens_from_prompt(prompt, answer, model=self.model)
                        n_first_dialog_messages_removed = n_dialog_messages_before - len(dialog_messages)
                        yield "not_finished", answer, (n_input_tokens, n_output_tokens), n_first_dialog_messages_removed

                answer = self._postprocess_answer(answer)

            except openai.InvalidRequestError as e:  # too many tokens
                if len(dialog_messages) == 0:
                    raise e

                # forget first message in dialog_messages
                dialog_messages = dialog_messages[1:]

        yield "finished", answer, (n_input_tokens, n_output_tokens), n_first_dialog_messages_removed  # sending final answer

    async def send_vision_message(
        self,
        message,
        dialog_messages=[],
        chat_mode="assistant",
        image_buffer: BytesIO = None,
    ):
        n_dialog_messages_before = len(dialog_messages)
        answer = None
        while answer is None:
            try:
                if self.model == "gpt-4-vision-preview" or self.model == "gpt-4o":
                    messages = self._generate_prompt_messages(
                        message, dialog_messages, chat_mode, image_buffer
                    )
                    r = client.chat.completions.create(model=self.model,
                    messages=messages,
                    **OPENAI_COMPLETION_OPTIONS)
                    answer = r.choices[0].message.content
                else:
                    raise ValueError(f"Unsupported model: {self.model}")

                answer = self._postprocess_answer(answer)
                n_input_tokens, n_output_tokens = (
                    r.usage.prompt_tokens,
                    r.usage.completion_tokens,
                )
            except openai.InvalidRequestError as e:  # too many tokens
                if len(dialog_messages) == 0:
                    raise ValueError(
                        "Dialog messages is reduced to zero, but still has too many tokens to make completion"
                    ) from e

                # forget first message in dialog_messages
                dialog_messages = dialog_messages[1:]

        n_first_dialog_messages_removed = n_dialog_messages_before - len(
            dialog_messages
        )

        return (
            answer,
            (n_input_tokens, n_output_tokens),
            n_first_dialog_messages_removed,
        )

    async def send_vision_message_stream(
        self,
        message,
        dialog_messages=[],
        chat_mode="assistant",
        image_buffer: BytesIO = None,
    ):
        n_dialog_messages_before = len(dialog_messages)
        answer = None
        while answer is None:
            try:
                if self.model == "gpt-4-vision-preview" or self.model == "gpt-4o":
                    messages = self._generate_prompt_messages(
                        message, dialog_messages, chat_mode, image_buffer
                    )

                    r_gen = client.chat.completions.create(model=self.model,
                    messages=messages,
                    stream=True,
                    **OPENAI_COMPLETION_OPTIONS)

                    answer = ""
                    async for r_item in r_gen:
                        delta = r_item.choices[0].delta
                        if "content" in delta:
                            answer += delta.content
                            (
                                n_input_tokens,
                                n_output_tokens,
                            ) = self._count_tokens_from_messages(
                                messages, answer, model=self.model
                            )
                            n_first_dialog_messages_removed = (
                                n_dialog_messages_before - len(dialog_messages)
                            )
                            yield "not_finished", answer, (
                                n_input_tokens,
                                n_output_tokens,
                            ), n_first_dialog_messages_removed

                answer = self._postprocess_answer(answer)

            except openai.InvalidRequestError as e:  # too many tokens
                if len(dialog_messages) == 0:
                    raise e
                # forget first message in dialog_messages
                dialog_messages = dialog_messages[1:]

        yield "finished", answer, (
            n_input_tokens,
            n_output_tokens,
        ), n_first_dialog_messages_removed

    def _generate_prompt(self, message, dialog_messages, chat_mode):
        prompt = config.chat_modes[chat_mode]["prompt_start"]
        prompt += "\n\n"

        # add chat context
        if len(dialog_messages) > 0:
            prompt += "Chat:\n"
            for dialog_message in dialog_messages:
                prompt += f"User: {dialog_message['user']}\n"
                prompt += f"Assistant: {dialog_message['assistant']}\n"

        # current message
        prompt += f"User: {message}\n"
        prompt += "Assistant: "

        return prompt

    def _encode_image(self, image_buffer: BytesIO) -> bytes:
        return base64.b64encode(image_buffer.read()).decode("utf-8")

    def _generate_prompt_messages(self, message, dialog_messages, chat_mode, image_buffer: BytesIO = None, prompt = None):
        if prompt is None:
            prompt = config.chat_modes[chat_mode]["prompt_start"]

        messages = [{"role": "assistant", "content": prompt}]

        for dialog_message in dialog_messages:
            messages.append({"role": "user", "content": dialog_message["user"][0]['text']})
            messages.append({"role": "assistant", "content": dialog_message["assistant"]})

        if image_buffer is not None:
            pass
            # messages.append(
            #     {
            #         "role": "user", 
            #         "content": [
            #             {
            #                 "type": "text",
            #                 "text": message,
            #             },
            #             {
            #                 "type": "image_url",
            #                 "image_url" : {

            #                     "url": f"data:image/jpeg;base64,{self._encode_image(image_buffer)}",
            #                     "detail":"high"
            #                 }
            #             }
            #         ]
            #     }

            # )
        else:
            messages.append({"role": "user", "content": message})

        return messages

    def _postprocess_answer(self, answer):
        answer = answer.strip()
        return answer

    def _count_tokens_from_messages(self, messages, answer, model="gpt-3.5-turbo"):
        encoding = tiktoken.encoding_for_model(model)

        if model == "gpt-3.5-turbo-16k":
            tokens_per_message = 4  # every message follows <im_start>{role/name}\n{content}<im_end>\n
            tokens_per_name = -1  # if there's a name, the role is omitted
        elif model == "gpt-3.5-turbo":
            tokens_per_message = 4
            tokens_per_name = -1
        elif model == "gpt-4":
            tokens_per_message = 3
            tokens_per_name = 1
        elif model == "gpt-4-turbo": #gpt-4-turbo":
            tokens_per_message = 3
            tokens_per_name = 1
        elif model == "gpt-4-vision-preview":
            tokens_per_message = 3
            tokens_per_name = 1
        elif model == "gpt-4o":
            tokens_per_message = 3
            tokens_per_name = 1
        else:
            raise ValueError(f"Unknown model: {model}")

        # input
        n_input_tokens = 0
        for message in messages:
            n_input_tokens += tokens_per_message
            if isinstance(message["content"], list):
                for sub_message in message["content"]:
                    if "type" in sub_message:
                        if sub_message["type"] == "text":
                            n_input_tokens += len(encoding.encode(sub_message["text"]))
                        elif sub_message["type"] == "image_url":
                            pass
            else:
                if "type" in message:
                    if message["type"] == "text":
                        n_input_tokens += len(encoding.encode(message["text"]))
                    elif message["type"] == "image_url":
                        pass


        n_input_tokens += 2

        # output
        n_output_tokens = 1 + len(encoding.encode(answer))

        return n_input_tokens, n_output_tokens

    def _count_tokens_from_prompt(self, prompt, answer, model="text-davinci-003"):
        encoding = tiktoken.encoding_for_model(model)

        n_input_tokens = len(encoding.encode(prompt)) + 1
        n_output_tokens = len(encoding.encode(answer))

        return n_input_tokens, n_output_tokens


async def transcribe_audio(audio_file) -> str:
    # r = await openai.Audio.atranscribe("whisper-1", audio_file)
    r = await client.audio.transcriptions.create(model="whisper-1", file=audio_file)
    return r.text or ""


async def generate_images(prompt, n_images=4, size="512x512"):
    r = await client.images.generate(prompt=prompt, n=n_images, size=size)
    image_urls = [item.url for item in r.data]
    return image_urls


async def is_content_acceptable(prompt):
    r = await client.moderations.create(input=prompt)
    return not all(r.results[0].categories.values())
