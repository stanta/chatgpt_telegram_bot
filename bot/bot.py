import io
import logging
import asyncio
import traceback
import html
import json
from markdown import markdown
from datetime import datetime
from os import path
import openai
import base64
import telegram
from telegram import (
    Update,
    User,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    BotCommand
)
from telegram.ext import (
    Application,
    ApplicationBuilder,
    CallbackContext,
    CommandHandler,
    MessageHandler,
    CallbackQueryHandler,
    AIORateLimiter,
    filters,
    PreCheckoutQueryHandler
)
from telegram.constants import ParseMode, ChatAction


import i18n
from i18n import t
from staff import tt
from buy import menu_start, button_handler
import config
import database

import openai_utils
import openai_assistant_utils

from stars_server import precheckout_callback, successful_payment_callback

ROOT_DIR = path.abspath(".")
localedir = path.join(ROOT_DIR, 'locales')
i18n.load_path.append(localedir)
i18n.set('skip_locale_root_data', True)
i18n.set('file_format', 'json')
i18n.set('filename_format', '{locale}.{format}')
i18n.set('fallback', 'en')

# setup
db = database.Database()
logger = logging.getLogger(__name__)

user_semaphores = {}
user_tasks = {}

# telegram_client = TelegramClient('anon', config.telegram_api_id, config.telegram_api_hash)

HELP_MESSAGE = config.help_message
HELP_GROUP_CHAT_MESSAGE = config.help_group_chat_message


def split_text_into_chunks(text, chunk_size):
    for i in range(0, len(text), chunk_size):
        yield text[i:i + chunk_size]

# async def is_no_enough_balance (update: Update, context: CallbackContext):
#     if not db.check_balance_positive (update.message.from_user.id):
#         return True
#     await context.bot.send_message(
#             update.callback_query.message.chat.id,
#             t("No enough balance, /buy tokens"),
#             parse_mode=ParseMode.HTML
#         )
#     return False

async def register_user_if_not_exists(
            update: Update, 
            context: CallbackContext, 
            user: User, 
            referral: int =None):
    if not db.check_if_user_exists(user.id):
        db.add_new_user(
            user.id,
            update.message.chat_id,
            username=user.username,
            first_name=user.first_name,
            last_name= user.last_name, 
            referral= referral
        )
        db.start_new_dialog(user.id)

    if db.get_user_attribute(user.id, "current_dialog_id") is None:
        db.start_new_dialog(user.id)

    if user.id not in user_semaphores:
        user_semaphores[user.id] = asyncio.Semaphore(1)

    if db.get_user_attribute(user.id, "current_model") is None:
        db.set_user_attribute(user.id, "current_model", config.models["available_text_models"][0])

    # back compatibility for n_used_tokens field
    n_used_tokens = db.get_user_attribute(user.id, "n_used_tokens")
    if isinstance(n_used_tokens, int) or isinstance(n_used_tokens, float):  # old format
        new_n_used_tokens = {
            "gpt-3.5-turbo": {
                "n_input_tokens": 0,
                "n_output_tokens": n_used_tokens
            }
        }
        db.set_user_attribute(user.id, "n_used_tokens", new_n_used_tokens)

    # voice message transcription
    if db.get_user_attribute(user.id, "n_transcribed_seconds") is None:
        db.set_user_attribute(user.id, "n_transcribed_seconds", 0.0)

    # image generation
    if db.get_user_attribute(user.id, "n_generated_images") is None:
        db.set_user_attribute(user.id, "n_generated_images", 0)


async def is_bot_mentioned(update: Update, context: CallbackContext):
     try:
         message = update.message

         if message.chat.type == "private":
             return True

         if message.text is not None and ("@" + context.bot.username) in message.text:
             return True

         if message.reply_to_message is not None:
             if message.reply_to_message.from_user.id == context.bot.id:
                 return True
     except:
         return True
     else:
         return False


async def start_handle(update: Update, context: CallbackContext):
    i18n.set('locale', update.message.from_user.language_code)
    ref_id = update.message.text.split()[1] if len(update.message.text.split()) > 1 else None

    await register_user_if_not_exists(update, context, update.message.from_user, referral = ref_id)
    user_id = update.message.from_user.id

    db.set_user_attribute(user_id, "last_interaction", datetime.now())
    db.start_new_dialog(user_id)

    reply_text = t("Hi! I'm <b>ExamsCoach</b> bot🤖\n\n")
    reply_text += tt (HELP_MESSAGE, update.message.from_user.language_code) 

    await update.message.reply_text(reply_text, parse_mode=ParseMode.HTML)
    
    await show_chat_modes_handle(update, context)


async def help_handle(update: Update, context: CallbackContext):
    await register_user_if_not_exists(update, context, update.message.from_user)
    user_id = update.message.from_user.id
    db.set_user_attribute(user_id, "last_interaction", datetime.now())
    await update.message.reply_text(tt(HELP_MESSAGE, update.message.from_user.language_code), parse_mode=ParseMode.HTML)


async def help_group_chat_handle(update: Update, context: CallbackContext):
    #  i18n.set('locale', update.message.from_user.language_code)


     await register_user_if_not_exists(update, context, update.message.from_user)
     user_id = update.message.from_user.id
     db.set_user_attribute(user_id, "last_interaction", datetime.now())
     h_g_c_m = tt( HELP_GROUP_CHAT_MESSAGE, update.message.from_user.language_code) 
     text = h_g_c_m.format(bot_username="@" + context.bot.username)

     await update.message.reply_text(text, parse_mode=ParseMode.HTML)
     await update.message.reply_video(config.help_group_chat_video_path)


async def retry_handle(update: Update, context: CallbackContext):
    await register_user_if_not_exists(update, context, update.message.from_user)
    if await is_previous_message_not_answered_yet(update, context): return

    user_id = update.message.from_user.id
    db.set_user_attribute(user_id, "last_interaction", datetime.now())
    pit_stop_message_number = db.get_dialog_attribute(user_id, key = "last_pit_stop_message_number")
    dialog_messages = db.get_dialog_messages(user_id, dialog_id=None, message_start = pit_stop_message_number)
    if len(dialog_messages) == 0:
        await update.message.reply_text(t("No message to retry 🤷‍♂️"))
        return

    last_dialog_message = dialog_messages.pop()
    db.set_dialog_messages(user_id, dialog_messages, dialog_id=None)  # last message was removed from the context

    await message_handle_fn(update, context, message=last_dialog_message)

async def _vision_message_handle_fn(
    update: Update, context: CallbackContext, use_new_dialog_timeout: bool = True
):

    # logger.info('_vision_message_handle_fn')
    user_id = update.message.from_user.id
    current_model = "gpt-4o-mini"

    # if current_model != "gpt-4-vision-preview" and current_model != "gpt-4o":
    #     await update.message.reply_text(
    #         t("🥲 Images processing is only available for <b>gpt-4-vision-preview</b> and <b>gpt-4o</b> model. Please change your settings in /settings"),
    #         parse_mode=ParseMode.HTML,
    #     )
    #     return

    chat_mode = db.get_user_attribute(user_id, "current_chat_mode")

    # new dialog timeout
    # if use_new_dialog_timeout:
    #     if (datetime.now() - db.get_user_attribute(user_id, "last_interaction")).seconds > config.new_dialog_timeout and len(db.get_dialog_messages(user_id)) > 0:
    #         # db.start_new_dialog(user_id)
    #         # await update.message.reply_text(t("Starting new dialog due to timeout (<b>{chat_mode}</b> mode) ✅").format(chat_mode=config.chat_modes[chat_mode]['name']), parse_mode=ParseMode.HTML)
    #         await update.message.reply_text(t("Nice to see you again! Some time gone, want to start /new dialog (make new chat context, save tokens) or continue this chat (spend more tokens)"), parse_mode=ParseMode.HTML)
    # db.set_user_attribute(user_id, "last_interaction", datetime.now())
    # if not db.check_balance_positive(user_id):
    #     await context.bot.send_message(
    #         chat_id=update.message.chat_id,
    #         text="No enough balance 🥲, /buy tokens to top up 😎",
    #         parse_mode=ParseMode.HTML
    #     )
    #     return

    buf = None
    if update.message.effective_attachment:
        photo = update.message.effective_attachment[-1]
        photo_file = await context.bot.get_file(photo.file_id)

        # store file in memory, not on disk
        buf = io.BytesIO()
        await photo_file.download_to_memory(buf)
        buf.name = "image.jpg"  # file extension is required
        buf.seek(0)  # move cursor to the beginning of the buffer

    # in case of CancelledError
    n_input_tokens, n_output_tokens = 0, 0

    try:
        # send placeholder message to user
        # placeholder_message = await update.message.reply_text(t("..."))
        message = update.message.caption or update.message.text or ''

        # send typing action
        await update.message.chat.send_action(action="typing")

        # dialog_messages = db.get_dialog_messages(user_id, dialog_id=None)
        parse_mode = {"html": ParseMode.HTML, "markdown": ParseMode.MARKDOWN}[
            config.chat_modes[chat_mode]["parse_mode"]
        ]

        chatgpt_instance = openai_utils.ChatGPT(model=current_model)
        # if config.enable_message_streaming:
        #     gen = chatgpt_instance.send_vision_message_stream(
        #         message,
        #         dialog_messages=dialog_messages,
        #         image_buffer=buf,
        #         chat_mode=chat_mode,
        #     )
        # else:
        (
        answer,
        (n_input_tokens, n_output_tokens),
        n_first_dialog_messages_removed,
        ) = await chatgpt_instance.send_vision_message(
            message,
            # dialog_messages=dialog_messages,
            image_buffer=buf,
            chat_mode=chat_mode,
        )
        

        # async def fake_gen():
        #     yield "finished", answer, (
        #         n_input_tokens,
        #         n_output_tokens,
        #     ), n_first_dialog_messages_removed

        # gen = fake_gen()
        
        # prev_answer = ""
        # async for gen_item in gen:
        #     (
        #         status,
        #         answer,
        #         (n_input_tokens, n_output_tokens),
        #         n_first_dialog_messages_removed,
        #     ) = gen_item

        #     answer = answer[:4096]  # telegram message limit

        #     # update only when 100 new symbols are ready
        #     if abs(len(answer) - len(prev_answer)) < 100 and status != "finished":
        #         continue

        #     try:
        #         await context.bot.edit_message_text(
        #             answer,
        #             chat_id=placeholder_message.chat_id,
        #             message_id=placeholder_message.message_id,
        #             parse_mode=parse_mode,
        #         )
        #     except telegram.error.BadRequest as e:
        #         if str(e).startswith(t("Message is not modified")):
        #             continue
        #         else:
        #             await context.bot.edit_message_text(
        #                 answer,
        #                 chat_id=placeholder_message.chat_id,
        #                 message_id=placeholder_message.message_id,
        #             )

        #     await asyncio.sleep(0.01)  # wait a bit to avoid flooding

        #     prev_answer = answer
        # update user data
        if buf is not None:
            base_image = base64.b64encode(buf.getvalue()).decode("utf-8")
            new_dialog_message = {"user": [
                        {
                            "type": "text",
                            "text": "", #message,
                        },
                        {
                            "type": "image",
                            "image": base_image,
                        }
                    ]
                , "assistant": answer, "date": datetime.now()}
        else:
            new_dialog_message = {"user": [{"type": "text", "text": message}], "assistant": answer, "date": datetime.now()}
        
        db.set_dialog_messages(
            user_id,
            db.get_dialog_messages(user_id, dialog_id=None) + [new_dialog_message],
            dialog_id=None
        )

        db.update_n_used_tokens(user_id, current_model, n_input_tokens, n_output_tokens)
        if update.message.caption is not None:
            answer = update.message.caption + " " + answer
        await message_handle_fn(update, context, message= answer)


    except asyncio.CancelledError:
        # note: intermediate token updates only work when enable_message_streaming=True (config.yml)
        db.update_n_used_tokens(user_id, current_model, n_input_tokens, n_output_tokens)
        raise

    except Exception as e:
        error_text = t("Something went wrong during completion. Reason: ") + str(e)
        logger.error(error_text)
        await update.message.reply_text(error_text)
        return

async def unsupport_message_handle(update: Update, context: CallbackContext, message=None):
    error_text = t("I don't know how to read files or videos. Send the picture in normal mode (Quick Mode).")
    logger.error(error_text)
    await update.message.reply_text(error_text)
    return

async def message_handle_fn(update: Update, context: CallbackContext,  message: str = ""):
        user_id = update.message.from_user.id

            # Обновляем время последнего взаимодействия пользователя
        chat_mode = db.get_user_attribute(user_id, "current_chat_mode")            
        db.set_user_attribute(user_id, "last_interaction", datetime.now())

        # Переменные для токенов (на случай отмены запроса)
        n_input_tokens, n_output_tokens = 0, 0

        try:
            # Отправляем placeholder-сообщение пользователю
            placeholder_message = await update.message.reply_text(t("answering..."))
            # Отправляем индикатор набора текста
            # await update.message.chat.send_action(action="typing")
            if len (message) == 0:
                _message = update.message.text
            else:
                _message = message
            if _message is None or len(_message) == 0:
                await update.message.reply_text(
                    t("🥲 You sent <b>empty message</b>. Please, try again!"),
                    parse_mode=ParseMode.HTML
                )
                return
            # Получаем номер последней точки "pit stop" (сохранения контекста)
            last_pit_stop_message_number_in = db.get_dialog_attribute(user_id, key="last_pit_stop_message_number")
            last_pit_stop_message_number = int(last_pit_stop_message_number_in) if last_pit_stop_message_number_in is not None else 0

            # Получаем текущий список сообщений диалога
            dialog_messages = db.get_dialog_messages(user_id, dialog_id=None, message_start = last_pit_stop_message_number )
            parse_mode = {
                "html": ParseMode.HTML,
                "markdown": ParseMode.MARKDOWN
            }[config.chat_modes[chat_mode]["parse_mode"]]
            
            current_model = "o3-mini"
            chatgpt_instance = openai_assistant_utils.ChatGPT(model=current_model)

            # Определяем, какую версию диалога (контекст) передавать в ChatGPT для формирования ответа
            # (Заметим, что переменная dialog_messages всё ещё содержит старый список – это не критично,
            #  так как для новых сообщений применяется add_dialog_message и номер последнего сообщения обновляется)
            enable_message_streaming =  config.models['info'][current_model]['enable_message_streaming'] if 'enable_message_streaming' in config.models['info'][current_model] else config.enable_message_streaming
            if enable_message_streaming:
                gen = chatgpt_instance.send_message_stream(
                    _message,
                    user_id,
                    dialog_messages=dialog_messages,
                    chat_mode=chat_mode
                )
            else:
                answer, (n_input_tokens, n_output_tokens), n_first_dialog_messages_removed = await chatgpt_instance.send_message(
                    _message,
                    user_id,
                    dialog_messages=dialog_messages,
                    chat_mode=chat_mode
                )

                async def fake_gen():
                    yield "finished", answer, (n_input_tokens, n_output_tokens), n_first_dialog_messages_removed
                gen = fake_gen()

            prev_answer = ""
            async for gen_item in gen:
                status, answer, (n_input_tokens, n_output_tokens), n_first_dialog_messages_removed = gen_item

                # Ограничиваем ответ по лимиту Telegram
                answer = answer[:4096]

                # Обновляем placeholder-сообщение только если накопилось примерно 100 символов
                if abs(len(answer) - len(prev_answer)) < 100 and status != "finished":
                    continue

                try:
                    if parse_mode == ParseMode.HTML:
                        escaped_answer = html.escape(answer)
                    elif parse_mode == ParseMode.MARKDOWN:
                        escaped_answer = answer
                    else:
                        escaped_answer = answer

                    await context.bot.edit_message_text(
                        escaped_answer,
                        chat_id=placeholder_message.chat_id,
                        message_id=placeholder_message.message_id,
                        parse_mode=parse_mode
                    )
                except telegram.error.BadRequest as e:
                    if str(e).startswith("Message is not modified"):
                        continue
                    else:
                        await context.bot.edit_message_text(
                            escaped_answer,
                            chat_id=placeholder_message.chat_id,
                            message_id=placeholder_message.message_id
                        )

                await asyncio.sleep(0.01)  # Небольшая задержка для избежания флудинга
                prev_answer = answer

            # После получения финального ответа добавляем новое сообщение в диалог
            new_dialog_message = {
                "user": [{"type": "text", "text": _message}],
                "assistant": answer,
                "date": datetime.now()
            }
            db.add_dialog_message(user_id, new_dialog_message, dialog_id=None)

            # Обновляем информацию по использованным токенам и балансу пользователя
            db.update_n_used_tokens(user_id, current_model, n_input_tokens, n_output_tokens)
            # Если накопился достаточно большой контекст – добавляем промежуточное сообщение - резюме  
            total_dialog_tokens= 0 if db.get_dialog_attribute(user_id, "n_used_tokens_dialog") is None  else int (db.get_dialog_attribute(user_id, "n_used_tokens_dialog"))
            if total_dialog_tokens > config.models['info'][current_model]['context_window_size'] * 0.9:
            # Передаём в ChatGPT контекст диалога, начиная с последней точки останова ч
                pit_stop_message_number, answer = await chatgpt_instance.convolute_dialog(user_id, dialog_messages, last_pit_stop_message_number)
                
                
                # Обновляем номер точки останова (забираем дополнительно 10 последних сообщений для контекста)
                last_pit_stop_message_number = last_pit_stop_message_number + pit_stop_message_number - 10
                db.set_dialog_attribute(user_id, key = "last_pit_stop_message_number", value = last_pit_stop_message_number)

                new_dialog_message = {
                    "user": [{"type": "text", "text": config.chat_modes["assistant"]["prompt_resume"]}],
                    "assistant": answer,
                    "date": datetime.now()
                }
                # Вместо перезаписи всего списка, добавляем новое сообщение
                db.add_dialog_message(user_id, new_dialog_message, dialog_id=None)    
                # Сбрасываем счётчик использованных токенов
                db.set_dialog_attribute(user_id, key = "n_used_tokens_dialog", value = 0)        

        except asyncio.CancelledError:
            # При отмене обновляем токены и пробрасываем исключение
            db.update_n_used_tokens(user_id, current_model, n_input_tokens, n_output_tokens)
            raise

        except Exception as e:
            error_text = t("Something went wrong during completion. Reason: ") + str(e)
            logger.error(error_text)
            await update.message.reply_text(error_text)
            return

        # Если из-за превышения длины диалога были удалены первые сообщения – уведомляем пользователя
        if n_first_dialog_messages_removed > 0:
            if n_first_dialog_messages_removed == 1:
                text = t(
                    "✍️ <i>Note:</i> Your current dialog is too long, so your <b>first message</b> was removed from the context.\n"
                    "Send /new command to start new dialog"
                )
            else:
                text = t(
                    "✍️ <i>Note:</i> Your current dialog is too long, so <b>{n} first messages</b> were removed from the context.\n"
                    "Send /new command to start new dialog"
                ).format(n=n_first_dialog_messages_removed)
            await update.message.reply_text(text, parse_mode=ParseMode.HTML)

async def message_handle(update: Update, context: CallbackContext, message=None, use_new_dialog_timeout=True):
    # check if bot was mentioned (for group chats)

    if not await is_bot_mentioned(update, context):
        return

    # check if message is edited
    if update.edited_message is not None:
        await edited_message_handle(update, context)
        return

    _message = message or update.message.text
#TODO      replied_message = update.message.reply_to_message if update.message.reply_to_message else update.message.forward_from_message

    # remove bot mention (in group chats)
    if update.message.chat.type != "private":
        _message = _message.replace("@" + context.bot.username, "").strip()

    await register_user_if_not_exists(update, context, update.message.from_user)

    if await is_previous_message_not_answered_yet(update, context): return
    
    # Проверяем, достаточно ли у пользователя средств
    user_id = update.message.from_user.id
    if not db.check_balance_positive(user_id):
        await context.bot.send_message(
            chat_id=update.message.chat_id,
            text="No enough balance 🥲, /buy tokens to top up 😎",
            parse_mode=ParseMode.HTML
        )
        return
    
    

    # if chat_mode == "artist":
    #     await generate_image_handle(update, context, message=message)
    #     return

    # current_model = "gpt-o3-mini"


    async with user_semaphores[user_id]: 
        if len(update.message.photo) > 0: # current_model == "gpt-4-vision-preview" or current_model == "gpt-4o" or update.message.photo is not None and             
            # logger.error(current_model)
            # What is this? ^^^

            # if current_model != "gpt-4o" and current_model != "gpt-4-vision-preview":
            #     current_model = "gpt-4o"
            #     db.set_user_attribute(user_id, "current_model", "gpt-4o")
            task = asyncio.create_task(
                _vision_message_handle_fn(update, context)
            )
        elif update.message.document is not None and update.message.document.file_size > 0:
            task = asyncio.create_task(
                textdoc_message_handle(update, context)
            )
        elif update.message.voice is not None and update.message.voice.file_size > 0:
            task = asyncio.create_task(
                voice_message_handle(update, context)
            )
        elif update.message.audio is not None  and update.message.audio.file_size > 0:
            task = asyncio.create_task(
                audio_message_handle(update, context)
            )
        else:
            task = asyncio.create_task(
                message_handle_fn(update, context,  message= _message)
            )            

        user_tasks[user_id] = task

        try:
            await task
        except asyncio.CancelledError:
            await update.message.reply_text(t("✅ Canceled"), parse_mode=ParseMode.HTML)
        else:
            pass
        finally:
            if user_id in user_tasks:
                del user_tasks[user_id]


async def is_previous_message_not_answered_yet(update: Update, context: CallbackContext):
    # await register_user_if_not_exists(update, context, update.message.from_user)

    user_id = update.message.from_user.id
    if user_semaphores[user_id].locked():
        text = t("⏳ Please <b>wait</b> for a reply to the previous message\n")
        text += t("Or you can /cancel it")
        await update.message.reply_text(text, reply_to_message_id=update.message.id, parse_mode=ParseMode.HTML)
        return True
    else:
        return False


async def voice_message_handle(update: Update, context: CallbackContext):
    # check if bot was mentioned (for group chats)
    if not await is_bot_mentioned(update, context):
        return

    # await register_user_if_not_exists(update, context, update.message.from_user)
    # if await is_previous_message_not_answered_yet(update, context): return

    user_id = update.message.from_user.id
    db.set_user_attribute(user_id, "last_interaction", datetime.now())
    if not db.check_balance_positive(user_id):
        await context.bot.send_message(
            chat_id=update.message.chat_id,
            text="No enough balance 🥲, /buy tokens to top up 😎",
            parse_mode=ParseMode.HTML
        )
        return
    voice = update.message.voice
    voice_file = await context.bot.get_file(voice.file_id)
    
    # store file in memory, not on disk
    buf = io.BytesIO()
    await voice_file.download_to_memory(buf)
    buf.name = "voice.oga"  # file extension is required
    buf.seek(0)  # move cursor to the beginning of the buffer

    answer = await openai_utils.transcribe_audio(buf)
    # text = t("🎤: <i>{transcribed_text}</i>").format(transcribed_text=transcribed_text)

    # await update.message.reply_text(text, parse_mode=ParseMode.HTML)

    # update n_transcribed_seconds
    current_model = "o3-mini"
    tokens_per_second = config.models["info"][current_model]["tokens_per_second"]
    db.set_user_attribute(user_id, "n_transcribed_seconds", voice.duration + db.get_user_attribute(user_id, "n_transcribed_seconds"))
    db.update_n_used_tokens(user_id, current_model, 0, int(voice.duration * tokens_per_second))
    if update.message.caption is not None:
            answer = update.message.caption + " " + answer
    await message_handle_fn(update, context, message= answer)

async def audio_message_handle(update: Update, context: CallbackContext):
    # check if bot was mentioned (for group chats)
    if not await is_bot_mentioned(update, context):
        return

    # await register_user_if_not_exists(update, context, update.message.from_user)
    # if await is_previous_message_not_answered_yet(update, context): return

    user_id = update.message.from_user.id
    db.set_user_attribute(user_id, "last_interaction", datetime.now())

    audio = update.message.audio 
    if audio.file_size < 20*1024*1024:
        audio_file = await context.bot.get_file(audio.file_id)
        # await update.message.reply_text(t("starting processing..."))
    else: 
        # await update.message.reply_text(t("files > 20mb not supported yet"))
        return

    # store file in memory, not on disk
    buf = io.BytesIO()
    await audio_file.download_to_memory(buf)
    buf.name = "audio.oga"  # file extension is required
    buf.seek(0)  # move cursor to the beginning of the buffer

    transcribed_text = await openai_utils.transcribe_audio(buf)
    answer = t("🎤: <i>{transcribed_text}</i>").format(transcribed_text=transcribed_text)
    # await update.message.reply_text(t("text transcribed, prepare result..."))
    # if len(text) < 500: 
    #     await update.message.reply_text(text, parse_mode=ParseMode.HTML)
    # else:
    #     with open("/tmp/" + audio.file_name + ".txt", 'w') as file:
    #         file.write(transcribed_text)
    #     await update.message.reply_document(file.name, parse_mode=ParseMode.HTML, caption=audio.file_name + ".txt") 

    # update n_transcribed_seconds
    current_model = "o3-mini"
    tokens_per_second = config.models["info"][current_model]["tokens_per_second"]
    db.set_user_attribute(user_id, "n_transcribed_seconds", voice.duration + db.get_user_attribute(user_id, "n_transcribed_seconds"))
    db.update_n_used_tokens(user_id, current_model, 0, int(voice.duration * tokens_per_second))
    if update.message.caption is not None:
            answer = update.message.caption + " " + answer
    await message_handle_fn(update, context, message=answer)
    
async def textdoc_message_handle(update: Update, context: CallbackContext):
    # check if bot was mentioned (for group chats)
    if not await is_bot_mentioned(update, context):
        return

    # await register_user_if_not_exists(update, context, update.message.from_user)
    # if await is_previous_message_not_answered_yet(update, context): return

    user_id = update.message.from_user.id
    db.set_user_attribute(user_id, "last_interaction", datetime.now())

    doc = update.message.document 
    if doc.file_size < 20*1024*1024:
        doc_file = await context.bot.get_file(doc.file_id)
        await update.message.reply_text(t("starting processing..."))
    else: 
        await update.message.reply_text(t("files > 20mb not supported yet"))

    # store file in memory, not on disk
    buf = io.BytesIO()
    await doc_file.download_to_memory(buf)
    buf.name = "doc.txt"  # file extension is required
    buf.seek(0)  # move cursor to the beginning of the buffer
    byte_str = buf.read()

    # Convert to a "unicode" object
    text = byte_str.decode('UTF-8')  # Or use the encoding you expect

    await message_handle_fn(update, context, message=update.message.caption + " " + text)


async def generate_image_handle(update: Update, context: CallbackContext, message=None):
    await register_user_if_not_exists(update, context, update.message.from_user)
    if await is_previous_message_not_answered_yet(update, context): return

    user_id = update.message.from_user.id
    db.set_user_attribute(user_id, "last_interaction", datetime.now())

    await update.message.chat.send_action(action="upload_photo")

    message = message or update.message.text

    try:
        image_urls = await openai_utils.generate_images(message, n_images=config.return_n_generated_images, size=config.image_size)
    except openai.error.InvalidRequestError as e:
        if str(e).startswith(t("Your request was rejected as a result of our safety system")):
            text = t("🥲 Your request <b>doesn't comply</b> with OpenAI's usage policies.\nWhat did you write there, huh?")
            await update.message.reply_text(text, parse_mode=ParseMode.HTML)
            return
        else:
            raise

    # token usage
    db.set_user_attribute(user_id, "n_generated_images", config.return_n_generated_images + db.get_user_attribute(user_id, "n_generated_images"))

    for i, image_url in enumerate(image_urls):
        await update.message.chat.send_action(action="upload_photo")
        await update.message.reply_photo(image_url, parse_mode=ParseMode.HTML)


async def new_dialog_handle(update: Update, context: CallbackContext):
    await register_user_if_not_exists(update, context, update.message.from_user)
    if await is_previous_message_not_answered_yet(update, context): return

    user_id = update.message.from_user.id
    db.set_user_attribute(user_id, "last_interaction", datetime.now())
    if db.get_user_attribute(user_id, "current_model") not in  config.models["available_text_models"]:
        db.set_user_attribute(user_id,  "current_model",  config.models["available_text_models"][0] ) 
    # db.set_user_attribute(user_id,  "locale", update.message.from_user.language_code ) 
    db.start_new_dialog(user_id)
    locale =  update.message.from_user.language_code
    i18n.set('locale', locale)
    await update.message.reply_text(t("Starting new dialog ✅"))

    chat_mode = db.get_user_attribute(user_id, "current_chat_mode")
    welcome_message =  tt(config.chat_modes[chat_mode]['welcome_message'], locale) 
    await update.message.reply_text(f"{welcome_message}", parse_mode=ParseMode.HTML)


async def cancel_handle(update: Update, context: CallbackContext):
    await register_user_if_not_exists(update, context, update.message.from_user)

    user_id = update.message.from_user.id
    db.set_user_attribute(user_id, "last_interaction", datetime.now())

    if user_id in user_tasks:
        task = user_tasks[user_id]
        task.cancel()
    else:
        await update.message.reply_text(t("<i>Nothing to cancel...</i>"), parse_mode=ParseMode.HTML)


def get_chat_mode_menu(page_index: int):
    n_chat_modes_per_page = config.n_chat_modes_per_page
    text = t("Select <b>chat mode</b> ({n} modes available):").format(n=len(config.chat_modes))

    # buttons
    chat_mode_keys = list(config.chat_modes.keys())
    page_chat_mode_keys = chat_mode_keys[page_index * n_chat_modes_per_page:(page_index + 1) * n_chat_modes_per_page]

    keyboard = []
    for chat_mode_key in page_chat_mode_keys:
        name = config.chat_modes[chat_mode_key]["name"]
        keyboard.append([InlineKeyboardButton(name, callback_data=f"set_chat_mode|{chat_mode_key}")])

    # pagination
    if len(chat_mode_keys) > n_chat_modes_per_page:
        is_first_page = (page_index == 0)
        is_last_page = ((page_index + 1) * n_chat_modes_per_page >= len(chat_mode_keys))

        if is_first_page:
            keyboard.append([
                InlineKeyboardButton("»", callback_data=f"show_chat_modes|{page_index + 1}")
            ])
        elif is_last_page:
            keyboard.append([
                InlineKeyboardButton("«", callback_data=f"show_chat_modes|{page_index - 1}"),
            ])
        else:
            keyboard.append([
                InlineKeyboardButton("«", callback_data=f"show_chat_modes|{page_index - 1}"),
                InlineKeyboardButton("»", callback_data=f"show_chat_modes|{page_index + 1}")
            ])

    reply_markup = InlineKeyboardMarkup(keyboard)

    return text, reply_markup


async def show_chat_modes_handle(update: Update, context: CallbackContext):
    await register_user_if_not_exists(update, context, update.message.from_user)
    if await is_previous_message_not_answered_yet(update, context): return

    user_id = update.message.from_user.id
    db.set_user_attribute(user_id, "last_interaction", datetime.now())

    text, reply_markup = get_chat_mode_menu(0)
    await update.message.reply_text(text, reply_markup=reply_markup, parse_mode=ParseMode.HTML)


async def show_chat_modes_callback_handle(update: Update, context: CallbackContext):
     i18n.set('locale', update.message.from_user.language_code)
     await register_user_if_not_exists(update.callback_query, context, update.callback_query.from_user)
     if await is_previous_message_not_answered_yet(update.callback_query, context): return

     user_id = update.callback_query.from_user.id
     db.set_user_attribute(user_id, "last_interaction", datetime.now())

     query = update.callback_query
     await query.answer()

     page_index = int(query.data.split("|")[1])
     if page_index < 0:
         return

     text, reply_markup = get_chat_mode_menu(page_index)
     try:
         await query.edit_message_text(text, reply_markup=reply_markup, parse_mode=ParseMode.HTML)
     except telegram.error.BadRequest as e:
         if str(e).startswith(t("Message is not modified")):
             pass


async def set_chat_mode_handle(update: Update, context: CallbackContext):
    await register_user_if_not_exists(update.callback_query, context, update.callback_query.from_user)
    user_id = update.callback_query.from_user.id

    query = update.callback_query
    await query.answer()

    chat_mode = query.data.split("|")[1]

    db.set_user_attribute(user_id, "current_chat_mode", chat_mode)
    db.start_new_dialog(user_id)
    if update.message is not None:
        language_code = update.message.from_user.language_code
    elif update.callback_query is not None:
        language_code = update.callback_query.from_user.language_code
    else:
        language_code = "en"  # или любой язык по умолчанию

    await context.bot.send_message(
        update.callback_query.message.chat.id,
        tt(config.chat_modes[chat_mode]['welcome_message'], language_code),
        parse_mode=ParseMode.HTML
    )


def get_settings_menu(user_id: int, update: Update):
    current_model = db.get_user_attribute(user_id, "current_model")
    if current_model not in config.models["info"]:
        current_model = config.models["info"][0]
    if update.message is not None:
        language_code = update.message.from_user.language_code
    elif update.callback_query is not None:
        language_code = update.callback_query.from_user.language_code
    else:
        language_code = "en"  # или любой язык по умолчанию
    
    text = tt (config.models["info"][current_model]["description"], language_code)

    text += "\n\n"
    score_dict = config.models["info"][current_model]["scores"]
    for score_key, score_value in score_dict.items():
        text += "🟢" * score_value + "⚪️" * (5 - score_value) + f" – {score_key}\n\n"

    text += t("\nSelect <b>model</b>:")

    # buttons to choose models
    buttons = []
    for model_key in config.models["available_text_models"]:
        title = config.models["info"][model_key]["name"]
        if model_key == current_model:
            title = "✅ " + title

        buttons.append(
            InlineKeyboardButton(title, callback_data=f"set_settings|{model_key}")
        )
    reply_markup = InlineKeyboardMarkup([buttons])

    return text, reply_markup


async def settings_handle(update: Update, context: CallbackContext):
    await register_user_if_not_exists(update, context, update.message.from_user)
    if await is_previous_message_not_answered_yet(update, context): return

    user_id = update.message.from_user.id
    db.set_user_attribute(user_id, "last_interaction", datetime.now())

    text, reply_markup = get_settings_menu(user_id, update)
    await update.message.reply_text(text, reply_markup=reply_markup, parse_mode=ParseMode.HTML)


async def set_settings_handle(update: Update, context: CallbackContext):
    
    await register_user_if_not_exists(update.callback_query, context, update.callback_query.from_user)
    user_id = update.callback_query.from_user.id

    query = update.callback_query
    await query.answer()

    _, model_key = query.data.split("|")
    db.set_user_attribute(user_id, "current_model", model_key)
    # db.start_new_dialog(user_id)

    text, reply_markup = get_settings_menu(user_id, update)
    try:
        await query.edit_message_text(text, reply_markup=reply_markup, parse_mode=ParseMode.HTML)
    except telegram.error.BadRequest as e:
        
        # i18n.set('locale', update.message.from_user.language_code)        
        if str(e).startswith("Message is not modified"):
            pass


async def show_balance_handle(update: Update, context: CallbackContext):
    await register_user_if_not_exists(update, context, update.message.from_user)

    user_id = update.message.from_user.id
    db.set_user_attribute(user_id, "last_interaction", datetime.now())

    # count total usage statistics
    total_n_spent_dollars = 0
    total_n_used_tokens = 0

    n_used_tokens_dict = db.get_user_attribute(user_id, "n_used_tokens")
    n_generated_images = db.get_user_attribute(user_id, "n_generated_images")
    n_transcribed_seconds = db.get_user_attribute(user_id, "n_transcribed_seconds")
    balance = db.get_user_attribute(user_id, "balance")
    i18n.set('locale', update.message.from_user.language_code)
    details_text = t("🏷️ Details:\n")
    for model_key in sorted(n_used_tokens_dict.keys()):
        n_input_tokens, n_output_tokens = n_used_tokens_dict[model_key]["n_input_tokens"], n_used_tokens_dict[model_key]["n_output_tokens"]
        total_n_used_tokens += n_input_tokens + n_output_tokens

        # n_input_spent_dollars = config.models["info"][model_key]["price_per_1000_input_tokens"] * (n_input_tokens / 1000)
        # n_output_spent_dollars = config.models["info"][model_key]["price_per_1000_output_tokens"] * (n_output_tokens / 1000)
        # total_n_spent_dollars += n_input_spent_dollars + n_output_spent_dollars

        details_text += f"- {model_key}: <b>{n_input_tokens + n_output_tokens} tokens</b>\n"

    # image generation
    # image_generation_n_spent_dollars = config.models["info"]["dalle-2"]["price_per_1_image"] * n_generated_images
    if n_generated_images != 0:
        details_text += t("- DALL·E 2 (image generation):  <b>{n_images} generated images</b>\n").format(
            # spent_dollars=image_generation_n_spent_dollars,
            n_images=n_generated_images
        )

    # total_n_spent_dollars += image_generation_n_spent_dollars

    # voice recognition
    # voice_recognition_n_spent_dollars = config.models["info"]["whisper"]["price_per_1_min"] * (n_transcribed_seconds / 60)
    if n_transcribed_seconds != 0:
        details_text += t("- Whisper (voice recognition):  <b>{n_seconds:.01f} seconds</b>\n").format(
            # spent_dollars=voice_recognition_n_spent_dollars,
            n_seconds=n_transcribed_seconds
        )

    # total_n_spent_dollars += voice_recognition_n_spent_dollars


    # text = t("You spent <b>{dollars:.03f}$</b>\n").format(dollars=total_n_spent_dollars)
    text =  t("Your current balance: <b>{balance}</b> tokens\n\n").format(balance=balance)
    text += t("to get more tokens, /buy \n\n")
    text += t("You totally spent <b>{tokens}</b> tokens\n\n").format(tokens=total_n_used_tokens)
    text += details_text

    await update.message.reply_text(text, parse_mode=ParseMode.HTML)


async def edited_message_handle(update: Update, context: CallbackContext):
    i18n.set('locale', update.message.from_user.language_code)
    if update.edited_message.chat.type == "private":
        text = t("🥲 Unfortunately, message <b>editing</b> is not supported")
        await update.edited_message.reply_text(text, parse_mode=ParseMode.HTML)


async def error_handle(update: Update, context: CallbackContext) -> None:
    logger.error(msg=t("Exception while handling an update:"), exc_info=context.error)

    try:
        # collect error message
        tb_list = traceback.format_exception(None, context.error, context.error.__traceback__)
        tb_string = "".join(tb_list)
        update_str = update.to_dict() if isinstance(update, Update) else str(update)
        message = (
            t("An exception was raised while handling an update\n") +
            f"<pre>update = {html.escape(json.dumps(update_str, indent=2, ensure_ascii=False))}"
            "</pre>\n\n"
            f"<pre>{html.escape(tb_string)}</pre>"
        )

        # split text into multiple messages due to 4096 character limit
        for message_chunk in split_text_into_chunks(message, 4096):
            try:
                await context.bot.send_message(update.effective_chat.id, message_chunk, parse_mode=ParseMode.HTML)
            except telegram.error.BadRequest:
                # answer has invalid characters, so we send it without parse_mode
                await context.bot.send_message(update.effective_chat.id, message_chunk)
    except Exception as e:        
        if hasattr(update, "effective_chat"): 
            await context.bot.send_message(update.effective_chat.id, t("Some error in error handler") + str (e))
        else:
            error_text = t("Some error in error handler. Reason: ") + str(e)
            logger.error(error_text)

async def reflink_handler(update: Update, context: CallbackContext) -> None:
    # здесь вы можете генерировать идентификатор реферала на основе id пользователя
    ref_id = update.effective_user.id
    # bot_username = bot.get_me().username
    referrals_number = db.get_referrals_number(ref_id)
    referals_purchases = db.get_referalls_purchases(ref_id)
    rewards = referals_purchases * config.reward_share
    
    ref_link = f"{context._application.bot.link}/?start={ref_id}"
    
    await context.bot.send_message(chat_id=update.effective_chat.id, text=t(f"Your referral link: {ref_link}\nCopy and share this link with your friends. \n\nYour referals: {referrals_number} \nTheir purchases: {referals_purchases} tokens\nYour rewards: {rewards} tokens\n\n <Get reward> (coming soon)"))

async def post_init(application: Application):
    await application.bot.set_my_commands([
        # BotCommand("/new", t("Start new dialog")),
        BotCommand("/buy", t("Buy bot tokens")),
        # BotCommand("/mode", t("Select chat mode")),
        # BotCommand("/retry", t("Re-generate response for previous query")),
        BotCommand("/balance", t("Show balance")),
        BotCommand("/settings", t("Show settings")),
        BotCommand("/reflink", t("Referrals")),
        BotCommand("/help", t("Show help message")),
    ])

def run_bot() -> None:
    application = (
        ApplicationBuilder()
        .token(config.telegram_token)
        .concurrent_updates(True)
        .rate_limiter(AIORateLimiter(max_retries=5))
        .http_version("1.1")
        .get_updates_http_version("1.1")
        .post_init(post_init)
        .build()
    )

    # add handlers
    user_filter = filters.ALL
    if len(config.allowed_telegram_usernames) > 0:
        usernames = [x for x in config.allowed_telegram_usernames if isinstance(x, str)]
        any_ids = [x for x in config.allowed_telegram_usernames if isinstance(x, int)]
        user_ids = [x for x in any_ids if x > 0]
        group_ids = [x for x in any_ids if x < 0]
        user_filter = filters.User(username=usernames) | filters.User(user_id=user_ids) | filters.Chat(chat_id=group_ids)

    application.add_handler(CommandHandler("start", start_handle, filters=user_filter))
    
    application.add_handler(CommandHandler("help", help_handle, filters=user_filter))
    application.add_handler(CommandHandler("help_group_chat", help_group_chat_handle, filters=user_filter))
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND & user_filter, message_handle))
    # application.add_handler(MessageHandler(filters.Document.TEXT & user_filter, textdoc_message_handle))

    application.add_handler(MessageHandler(filters.PHOTO & ~filters.COMMAND & user_filter, message_handle)) # _vision_message_handle_fn
    application.add_handler(MessageHandler(filters.VIDEO & ~filters.COMMAND & user_filter, unsupport_message_handle)) #unsupport_message_handle
    application.add_handler(MessageHandler(filters.VOICE & user_filter, message_handle )) #voice_message_handle
    application.add_handler(MessageHandler(filters.AUDIO & user_filter,message_handle )) #audio_message_handle
    application.add_handler(MessageHandler(filters.Document.ALL & ~filters.COMMAND & ~filters.Document.TEXT & user_filter, unsupport_message_handle))

    # application.add_handler(CommandHandler("retry", retry_handle, filters=user_filter))
    application.add_handler(CommandHandler("new", new_dialog_handle, filters=user_filter))
    application.add_handler(CommandHandler("cancel", cancel_handle, filters=user_filter))

    

    # application.add_handler(CommandHandler("mode", show_chat_modes_handle, filters=user_filter))
    # application.add_handler(CallbackQueryHandler(show_chat_modes_callback_handle, pattern="^show_chat_modes"))
    # application.add_handler(CallbackQueryHandler(set_chat_mode_handle, pattern="^set_chat_mode"))

    application.add_handler(CommandHandler("settings", settings_handle, filters=user_filter))
    application.add_handler(CallbackQueryHandler(set_settings_handle, pattern="^set_settings"))

    application.add_handler(CommandHandler("balance", show_balance_handle, filters=user_filter))
    application.add_handler(CommandHandler("buy", menu_start, filters=user_filter))
    application.add_handler(CallbackQueryHandler(button_handler))
    application.add_error_handler(error_handle)
    
    #TODO - rewrite main menu to use only menu_config.yml
    
    application.add_handler(PreCheckoutQueryHandler(precheckout_callback))
    application.add_handler(MessageHandler(filters.SUCCESSFUL_PAYMENT, successful_payment_callback))
    
    application.add_handler(CommandHandler("reflink", reflink_handler))


    # start the bot
    logger.info(t('bot started...'))
    application.run_polling()


if __name__ == "__main__":
    run_bot()
