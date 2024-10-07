import base64
from io import BytesIO
import config
import logging
import database
import tiktoken
import openai
from event_handler import EventHandler
from i18n import t

# setup openai client
openai.api_key = config.openai_api_key
openai.assistant_id = config.openai_api_assistant
if config.openai_api_base is not None:
    openai.api_base = config.openai_api_base
if config.openai_api_organization is not None:
    openai.organization = config.openai_api_organization
if config.openai_api_project is not None:
    openai.project_id = config.openai_api_project
logger = logging.getLogger(__name__)

OPENAI_COMPLETION_OPTIONS = {
    "temperature": 0.7,
    "max_tokens": 1000,
    "top_p": 1,
    "frequency_penalty": 0,
    "presence_penalty": 0,
    "request_timeout": 60.0,
}

client = openai.OpenAI(
    api_key = config.openai_api_key,
    # organization = config.openai_api_organization,
    # project = config.openai_api_project
)

db = database.Database()

class ChatGPT:
    def __init__(self, model="gpt-3.5-turbo"):
        assert model in {
            "text-davinci-003",
            "gpt-3.5-turbo-16k",
            "gpt-3.5-turbo",
            "gpt-4",
            "gpt-4o",
            "gpt-4-1106-preview",
            "gpt-4-vision-preview",
        }, f"Unknown model: {model}"
        self.model = model
        self.assistants = {}  # Cache for assistants per chat_mode
        

# ASSISTANT_ID = "asst_nPqP4wzrr4N4mYlz9bZGlsWR"  # Используем готовый ID ассистента

    async def send_message(self, message, dialog_messages=[], chat_mode="assistant"):
        n_dialog_messages_before = len(dialog_messages)
        answer = None

        while answer is None:
            try:
                messages = self._prepare_messages(message, dialog_messages)
                assistant= client.beta.assistants.retrieve(openai.assistant_id)

                response = await assistant.chat( #openai.client.beta.
                    messages=messages
                )
                answer = response.choices[0].message["content"]
                answer = self._postprocess_answer(answer)
                n_input_tokens = response.usage.prompt_tokens
                n_output_tokens = response.usage.completion_tokens
            except openai.error.InvalidRequestError as e:
                if len(dialog_messages) == 0:
                    raise ValueError(t("Too many tokens even after reducing dialog messages")) from e
                dialog_messages = dialog_messages[1:]

        n_first_dialog_messages_removed = n_dialog_messages_before - len(dialog_messages)
        return answer, (n_input_tokens, n_output_tokens), n_first_dialog_messages_removed

    async def send_message_stream(self, message_in, user_id, dialog_messages=[], chat_mode="assistant"):
        if chat_mode not in config.chat_modes.keys():
            raise ValueError(t("Chat mode {chat_mode} is not supported"))
        n_dialog_messages_before = len(dialog_messages)
        answer = None
        assistant= client.beta.assistants.retrieve(openai.assistant_id)
        n_input_tokens = 0 
        n_output_tokens = 0
        n_first_dialog_messages_removed = 0
        while answer is None:
            try:
                messages = self._prepare_messages(message_in, dialog_messages)
                # get_or_create thread 
                thread_id = db.get_user_attribute(user_id, "thread_id") 
                if thread_id is None:
                    thread = client.beta.threads.create()
                    db.set_user_attribute(user_id, "thread_id", thread.id)
                    thread_id = thread.id
                
                message = client.beta.threads.messages.create(
                    thread_id=thread_id,
                    role="user",
                    content=message_in

                )
                #async? 
                with client.beta.threads.runs.stream(
                    thread_id=thread_id,
                    assistant_id=assistant.id,
                    # event_handler=EventHandler(),
                ) as stream:
                  answer = ""
                  for event in stream:
                    if event.event == "thread.message.delta" and event.data.delta.content:
                        delta = event.data.delta.content[0] # delta["content"]
                        answer += delta.text.value
                        n_input_tokens, n_output_tokens = self._count_tokens_from_messages(
                            messages, answer, model=self.model
                        )
                        n_first_dialog_messages_removed = n_dialog_messages_before - len(dialog_messages)
                        yield "not_finished", answer, (n_input_tokens, n_output_tokens), n_first_dialog_messages_removed
                  stream.until_done()                                        
                    
                answer = self._postprocess_answer(answer)
            # except Exception as e:
            except openai.error.InvalidRequestError as e:
                # raise e
                if len(dialog_messages) == 0:
                    raise e
                dialog_messages = dialog_messages[1:]

        yield "finished", answer, (n_input_tokens, n_output_tokens), n_first_dialog_messages_removed


    def _prepare_messages(self, message, dialog_messages):
        messages = []
        for dialog_message in dialog_messages:
            messages.append({"role": "user", "content": dialog_message["user"]})
            messages.append({"role": "assistant", "content": dialog_message["bot"]})
        messages.append({"role": "user", "content": message})
        return messages

    def _postprocess_answer(self, answer):
        return answer.strip()

    def _count_tokens_from_messages(self, messages, answer, model="gpt-3.5-turbo"):
        encoding = tiktoken.encoding_for_model(model)
        if model in ["gpt-3.5-turbo-16k", "gpt-3.5-turbo"]:
            tokens_per_message = 4
        elif model in ["gpt-4", "gpt-4o", "gpt-4-1106-preview", "gpt-4-vision-preview"]:
            tokens_per_message = 3
        else:
            raise ValueError(f"Unknown model: {model}")

        n_input_tokens = 0
        for message in messages:
            n_input_tokens += tokens_per_message
            mess = message['content'][0]['text'] if isinstance (message['content'],  list)  else message['content']
           
            n_input_tokens += len(encoding.encode(mess))
        n_input_tokens += 2  # additional tokens for assistant

        n_output_tokens = 1 + len(encoding.encode(answer))
        return n_input_tokens, n_output_tokens

  