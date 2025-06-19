import asyncio
import database as db

import config
import database
import openai_assistant_utils
import logging
import random
from datetime import datetime, timedelta
from i18n import t
from telegram.ext import CallbackContext
from telegram.error import Forbidden

db = database.Database()
logger = logging.getLogger(__name__)

async def check_is_blocked(bot, user_id):
    try:
        # Try to retrieve chat info. If the bot is blocked, this call may raise Forbidden.
        chat = await bot.get_chat(user_id)
        return False  # Bot is not blocked.
    except Forbidden as e:
        logger.warning(f"User {user_id} appears to have blocked the bot: {e}")
        # Optionally update your database here to mark the user as blocked.
        await db.set_user_attribute(user_id, "blocked", True)
        return True  # Bot is blocked.
    
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
        await db.set_user_attribute(user_id, "blocked", True)
        
    except Exception as e:
        logger.info(f"Failed to send message to user {user_id} after delay: {e}")

async def check_user_inactivity(context: CallbackContext, threshold_minutes: int = 48*60): # 48 часов неактивности
    
    threshold = datetime.now() - timedelta(minutes=threshold_minutes)
    # Получаем всех пользователей, у которых last_interaction раньше порогового времени
    inactive_users = db.user_collection.find({"last_interaction": {"$lt": threshold}})    
    async for user in inactive_users:
        answer = ""
        user_id = user["_id"]
        if await db.get_user_attribute(user_id, "blocked") == True:        
            continue   
        # if await check_is_blocked(context.bot, user_id):
        #     continue
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
            await db.update_n_used_tokens(user_id, "gpt-4o-mini", int(n_input_tokens/7), int(n_output_tokens/7)) # учитываем оказанную консультацию, как и то, что gpt-4o-mini в 7 раз дешевле
            message_text = username + config.chat_modes["assistant"]["prompt_continue2"] + answer

        # Определяем задержку отправки:
        # target_time – время сегодня с часовым значением last_interaction
        last_interaction = user["last_interaction"]
        now = datetime.now()
        target_time = now.replace(
            hour=last_interaction.hour,
            minute= random.randint(0, 10), #last_interaction.minute,
            second=59,
            microsecond=0
        )
        # Если target_time уже прошло, переносим на следующий день
        if target_time < now:
            target_time += timedelta(days=1)
        delay = (target_time - now).total_seconds()
        # delay = 1 # для теста
        new_dialog_message = {
                    "user": [{"type": "text", "text": ""}],
                    "assistant": message_text,
                    "date": datetime.now() + timedelta(seconds=delay),
                }
        await db.add_dialog_message(user_id, new_dialog_message, dialog_id=None)
        # Планируем нерBlocking отправку сообщения
        asyncio.create_task(send_delayed_message(context.bot, user["chat_id"], message_text, delay, user_id))
