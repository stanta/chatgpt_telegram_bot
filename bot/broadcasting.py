import asyncio
import database as db
from datetime import datetime, timedelta
import config
import database
from telegram.ext import CallbackContext
from i18n import t
import openai_assistant_utils
import logging
import random

db = database.Database()
logger = logging.getLogger(__name__)

async def send_delayed_message(bot, chat_id, text, delay, user_id):
    await asyncio.sleep(delay)
    try:
        await bot.send_message(
            chat_id=chat_id,
            text=text,
            parse_mode="HTML"
        )
        logger.info(f"Sent message to user {user_id} after delay: {datetime.now().isoformat()}")
    except Forbidden as e:
        # Бот заблокирован пользователем
        logger.warning(f"Bot blocked by user {user_id}: {e}")
        #TODO  Здесь можно обновить информацию в БД, отметив, что бот заблокирован        
    except Exception as e:
        logger.info(f"Failed to send message to user {user_id} after delay: {e}")

async def check_user_inactivity(context: CallbackContext, threshold_minutes: int = 24*60):
    threshold = datetime.now() - timedelta(minutes=threshold_minutes)
    # Получаем всех пользователей, у которых last_interaction раньше порогового времени
    inactive_users = db.user_collection.find({"last_interaction": {"$lt": threshold}})    
    for user in inactive_users:
        answer = ""
        user_id = user["_id"]
        username = user["username"] if user["username"] is not None else user["first_name"] + " " + user["first_name"]
        if user["n_used_tokens"] == {}:
            message_text = username + config.chat_modes["assistant"]["prompt_awake"]
        elif user["balance"] < 0:
            message_text = username + config.chat_modes["assistant"]["prompt_negative_balance"]
        else:
            n_input_tokens = 0
            n_output_tokens = 0
            chatgpt_instance = openai_assistant_utils.ChatGPT(model="gpt-4o-mini")
            (answer, (n_input_tokens, n_output_tokens), _) = await chatgpt_instance.send_message(
                config.chat_modes["assistant"]["prompt_continue"], user_id
            )
            message_text = username + config.chat_modes["assistant"]["prompt_continue2"] + answer

        # Определяем задержку отправки:
        # target_time – время сегодня с часовым значением last_interaction
        last_interaction = user["last_interaction"]
        now = datetime.now()
        target_time = now.replace(
            hour=last_interaction.hour,
            minute= random.randint(0, 10), #last_interaction.minute,
            second=0,
            microsecond=0
        )
        # Если target_time уже прошло, переносим на следующий день
        if target_time < now:
            target_time += timedelta(days=1)
        delay = (target_time - now).total_seconds()
        
        new_dialog_message = {
                    "user": [{"type": "text", "text": ""}],
                    "assistant": message_text,
                    "date": datetime.now() + timedelta(seconds=delay),
                }
        db.add_dialog_message(user_id, new_dialog_message, dialog_id=None)
        # Планируем нерBlocking отправку сообщения
        asyncio.create_task(send_delayed_message(context.bot, user["chat_id"], message_text, delay, user_id))
