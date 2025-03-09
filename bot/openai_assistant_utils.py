import base64
from io import BytesIO
import config
import logging
import database
import tiktoken
import openai
from event_handler import EventHandler
from i18n import t
from openai import AsyncOpenAI, OpenAI
import asyncio
# setup openai client
client = AsyncOpenAI(api_key=config.openai_api_key,
                    organization=config.openai_api_organization
                    )



logger = logging.getLogger(__name__)

OPENAI_COMPLETION_OPTIONS = {
    "temperature": 0.7,
    "max_tokens": 1000,
    "top_p": 1,
    "frequency_penalty": 0,
    "presence_penalty": 0,
    "request_timeout": 60.0,
}

db = database.Database()

class ChatGPT:
    def __init__(self, model="gpt-3.5-turbo"):
        # assert model in {
        #     "text-davinci-003",
        #     "gpt-3.5-turbo-16k",
        #     "gpt-3.5-turbo",
        #     "gpt-4",
        #     "gpt-4o",
        #     "gpt-4-turbo",
        #     "gpt-4-vision-preview",
        # }, f"Unknown model: {model}"
        self.model = model
        self.assistants = {}  # Cache for assistants per chat_mode

    async def convolute_dialog (self, user_id,  dialog_messages, last_pit_stop_message_number):
        pit_stop_message_number = 0
        answer = None
        retries = 5
                
        pit_stop_message_number = len(dialog_messages)
        messages_to_analalyze_num = pit_stop_message_number - last_pit_stop_message_number
        answer = None
        while answer is None and retries > 0 :
            retries -= 1
            try:
                # messages = self._generate_prompt_messages(config.chat_modes["assistant"]["prompt_resume"], dialog_messages, "assistant" )
                answer = await self.send_message (config.chat_modes["assistant"]["prompt_resume"] + str(messages_to_analalyze_num), user_id)
            except Exception as e:  # too many tokens
                logger.warning(f"No answer when convol_dialog because of {e} ")
                    
        return  pit_stop_message_number, answer

    async def send_message(self, message, user_id,  dialog_messages=[], chat_mode="assistant"):
        n_dialog_messages_before = len(dialog_messages)
        answer = None
        messages = self._prepare_messages(message, dialog_messages)
        assistant= await client.beta.assistants.retrieve(config.openai_api_assistant)
        thread_id = db.get_dialog_attribute(user_id, "thread_id") 
        run_id = ''
        while answer is None:
            try:
                if thread_id == '':
                    thread = await client.beta.threads.create()
                    db.set_dialog_attribute(user_id, "thread_id", thread.id)
                    thread_id = thread.id

                # List runs in the thread to detect an active one.
                runs_page = await client.beta.threads.runs.list(thread_id)
                active_run = None
                if runs_page.data:
                    for run in runs_page.data:
                        if run.status in ['running', 'queued']:
                            active_run = run
                            break

                if active_run:
                    # There is an active run, so wait until it completes.
                    run_id = active_run.id
                    while True:
                        cur_run = await client.beta.threads.runs.retrieve(thread_id, run_id)
                        if cur_run.status == 'completed':
                            break
                        await asyncio.sleep(0.1)
                    # Retrieve the answer from the completed run.
                    response = await client.beta.threads.messages.list(thread_id)
                    
                    answer = response.data[0].content[0].text.value
                    answer = self._postprocess_answer(answer)
                   
                    await client.beta.threads.runs.cancel(thread_id, run_id)
                else:
                    # No active run, so create a new run.
                    message_ai = await client.beta.threads.messages.create(
                        thread_id=thread_id,
                        role="user",
                        content=message
                    )
                    new_run = await client.beta.threads.runs.create(
                        thread_id=thread_id,
                        assistant_id=assistant.id,
                    )
                    run_id = new_run.id
                    # Wait for the new run to complete.
                    while True:
                        cur_run = await client.beta.threads.runs.retrieve(thread_id = thread_id, run_id = run_id)
                        if cur_run.status == 'completed':
                            break
                        await asyncio.sleep(0.5)
                    response = await client.beta.threads.messages.list(thread_id)
                    answer = response.data[0].content[0].text.value
                    answer = self._postprocess_answer(answer)
                    
                    # if hasattr(cur_run, "usage") and cur_run.usage is not None:
                    n_input_tokens = cur_run.usage.prompt_tokens
                    n_output_tokens = cur_run.usage.completion_tokens
                    # else:
                    #     n_input_tokens, n_output_tokens = self._count_tokens_from_messages(
                    #     messages, answer, model=self.model
                    #     )
                    # Optionally cancel or mark the run as done if needed.                    
                    # n_input_tokens, n_output_tokens = self._count_tokens_from_messages(
                    #     messages, answer, model=self.model
                    # )
                    # await client.beta.threads.runs.cancel(thread_id = thread_id, run_id = run_id)
            except Exception as e:
                logger.warning(f"Error in send_message: {e}")
                # if len(dialog_messages) == 0:
                    # raise ValueError(t("Too many tokens even after reducing dialog messages")) from e
                # dialog_messages = dialog_messages[1:]
                # n_first_dialog_messages_removed = n_dialog_messages_before - len(dialog_messages)
            return answer, (n_input_tokens, n_output_tokens), 0 #n_first_dialog_messages_removed
 
    async def send_message_stream(self, message_in, user_id, dialog_messages=[], chat_mode="assistant"):
        if chat_mode not in config.chat_modes.keys():
            raise ValueError(t("Chat mode {chat_mode} is not supported"))
        n_dialog_messages_before = len(dialog_messages)
        answer = None
        assistant=await client.beta.assistants.retrieve(config.openai_api_assistant)
        n_input_tokens = 0 
        n_output_tokens = 0
        n_first_dialog_messages_removed = 0
        while answer is None:
            try:
                messages = self._prepare_messages(message_in, dialog_messages)
                # get_or_create thread 
                thread_id = db.get_dialog_attribute(user_id, "thread_id") 
                if thread_id == '':
                    thread = await client.beta.threads.create()
                    db.set_dialog_attribute(user_id, "thread_id", thread.id)
                    thread_id = thread.id

                message = await client.beta.threads.messages.create(
                    thread_id=thread_id,
                    role="user",
                    content=message_in

                )
                #async? 
                async with  client.beta.threads.runs.stream(
                    thread_id=thread_id,
                    assistant_id=assistant.id,
                    # event_handler=EventHandler(),
                ) as stream:
                  answer = ""
                  async for event in stream:
                    if event.event == "thread.message.delta" and event.data.delta.content:
                        delta = event.data.delta.content[0] # delta["content"]
                        answer += delta.text.value
                        n_input_tokens, n_output_tokens = self._count_tokens_from_messages(
                            messages, answer, model=self.model
                        )
                        n_first_dialog_messages_removed = n_dialog_messages_before - len(dialog_messages)
                        yield "not_finished", answer, (n_input_tokens, n_output_tokens), n_first_dialog_messages_removed
                  await stream.until_done()                                        

                answer = self._postprocess_answer(answer)
            except Exception as e:
            #except Exception as e:
                # raise e
                if len(dialog_messages) == 0:
                    raise e
                dialog_messages = dialog_messages[1:]

        yield "finished", answer, (n_input_tokens, n_output_tokens), n_first_dialog_messages_removed


    def _prepare_messages(self, message, dialog_messages):
        messages = []
        for dialog_message in dialog_messages:
            messages.append({"role": "user", "content": dialog_message["user"]})
            messages.append({"role": "assistant", "content": dialog_message["assistant"]})
        messages.append({"role": "user", "content": message + t(" FORMAT ANSWER EXACTLY AS ") + config.chat_modes["assistant"]["parse_mode"]})
        return messages

    def _postprocess_answer(self, answer):
        return answer.strip()

    def _count_tokens_from_messages(self, messages, answer, model="gpt-3.5-turbo"):
        encoding = tiktoken.encoding_for_model(model)
        if model in ["gpt-3.5-turbo-16k", "gpt-3.5-turbo"]:
            tokens_per_message = 4
        elif model in ["gpt-4", "gpt-4o", "gpt-4-turbo", "gpt-4-vision-preview", "gpt-4-turbo"]:
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

