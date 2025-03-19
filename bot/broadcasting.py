import database as db
from datetime import datetime, timedelta
import config
import database
from telegram.ext import (CallbackContext)
from i18n import t
import openai_assistant_utils
db = database.Database()

async def check_user_inactivity(context: CallbackContext, threshold_minutes: int = 24*60):
    threshold = datetime.now() - timedelta(minutes=threshold_minutes)
    # Получаем всех пользователей, у которых last_interaction раньше порогового времени
    inactive_users = db.user_collection.find({"last_interaction": {"$lt": threshold}})
    for user in inactive_users:
        user_id = user["_id"]
        username = user["username"] if user["username"] is not None else user["first_name"] + " " + user["first_name"]  # Используем имя пользователя из данных пользователя
        if user["n_used_tokens"] == {}:
            #welcome
            message_text = username + config.chat_modes["assistant"]["prompt_awake"]

        elif user["balance"] < 0:
            message_text = username +  config.chat_modes["assistant"]["prompt_negative_balance"]
            
        else:
            chatgpt_instance = openai_assistant_utils.ChatGPT(model= "gpt-4o-mini")

            (answer, (n_input_tokens, n_output_tokens),_) = await chatgpt_instance.send_message( config.chat_modes["assistant"]["prompt_continue"], user_id)
            
            message_text =  username + config.chat_modes["assistant"]["prompt_continue2"]  + answer
            
            
        # Отправляем уведомление пользователю
        try:
            await context.bot.send_message(
                chat_id=user["chat_id"],  # Используем chat_id из данных пользователя
                text=message_text,
                parse_mode="MARKDOWN"
            )
        except Exception as e:
            print(f"Failed to send message to user {user_id}: {e}")
        