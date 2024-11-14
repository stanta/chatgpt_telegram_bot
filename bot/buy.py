
from telegram import InlineKeyboardMarkup, InlineKeyboardButton, Update
from telegram.ext import Updater, CommandHandler, CallbackQueryHandler, CallbackContext
import config
from i18n import t
from staff import tt

# Функция для создания меню на основе конфигурации
async def build_menu(menu_config, submenu_name=None):
    keyboard = []
    # Если submenu_name указан, ищем соответствующий раздел меню
    if submenu_name:
        submenu = next((item["submenu"] for item in menu_config["menu"][0]["buttons"] if item["text"] == submenu_name), None)
        if submenu is None:
            raise ValueError(f"Submenu with name {submenu_name} not found")
        for button in submenu[0]["buttons"]:
            keyboard.append([InlineKeyboardButton(button["text"], callback_data=button["callback_data"])])
        keyboard.append([InlineKeyboardButton(t("Back"), callback_data="back")])
    else:
        for item in menu_config["menu"]:
            buttons = []
            for button in item["buttons"]:
                if "submenu" in button:
                    buttons.append(InlineKeyboardButton(button["text"], callback_data=f"submenu_{button['text']}"))
                else:
                    buttons.append(InlineKeyboardButton(button["text"], callback_data=button["callback_data"]))
            keyboard.append(buttons)
    return InlineKeyboardMarkup(keyboard)


# Функция для обработки нажатий кнопок
async def buy_button_handler(update: Update, context: CallbackContext) -> None:
    query =  update.callback_query
    await query.answer()
    
    data = query.data
    if data.startswith("submenu_"):
        submenu_name = data.split("submenu_")[1]
        reply_markup = await build_menu(context.user_data["menu_config"], submenu_name=submenu_name)
        await query.edit_message_text(text=t("Choose option:"), reply_markup=reply_markup)
    elif data == "back":
        reply_markup = await build_menu(context.user_data["menu_config"])
        await query.edit_message_text(text=t("Main menu :"), reply_markup=reply_markup)
    else:
        await query.edit_message_text(text=t(f"Your choose: {data}"))

# Функция для обработки команды /start
async def buy_start(update: Update, context: CallbackContext) -> None:
    context.user_data["menu_config"] = tt(config.buy_menu_config, update.message.from_user.language_code)
    reply_markup = await build_menu(context.user_data["menu_config"])
    await update.message.reply_text(t("Choose:"), reply_markup=reply_markup)

# # Запуск бота
# def main():
#     updater = Updater("YOUR_BOT_TOKEN")
#     dp = updater.dispatcher

#     dp.add_handler(CommandHandler("start", start))
#     dp.add_handler(CallbackQueryHandler(button_handler))

#     updater.start_polling()
#     updater.idle()

# if __name__ == '__main__':
#     main()
