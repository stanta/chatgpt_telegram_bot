
from telegram import InlineKeyboardMarkup, InlineKeyboardButton, Update
from telegram.ext import Updater, CommandHandler, CallbackQueryHandler, CallbackContext
import config


# Функция для создания меню на основе конфигурации
def build_menu(menu_config, submenu_name=None):
    keyboard = []
    # Если submenu_name указан, ищем соответствующий раздел меню
    if submenu_name:
        submenu = next(item["submenu"] for item in menu_config["menu"] if item["title"] == submenu_name)
        for button in submenu:
            keyboard.append([InlineKeyboardButton(button["text"], callback_data=button["callback_data"])])
        keyboard.append([InlineKeyboardButton("Назад", callback_data="back")])
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

# Функция для обработки команды /start
def buy_start(update: Update, context: CallbackContext) -> None:
    context.user_data["menu_config"] = config.buy_menu_config
    reply_markup = build_menu(context.user_data["menu_config"])
    update.message.reply_text(t("Choose what to buy:"), reply_markup=reply_markup)

# Функция для обработки нажатий кнопок
def button_handler(update: Update, context: CallbackContext) -> None:
    query = update.callback_query
    query.answer()
    
    data = query.data
    if data.startswith("submenu_"):
        submenu_name = data.split("submenu_")[1]
        reply_markup = build_menu(context.user_data["menu_config"], submenu_name=submenu_name)
        query.edit_message_text(text="Выберите опцию:", reply_markup=reply_markup)
    elif data == "back":
        reply_markup = build_menu(context.user_data["menu_config"])
        query.edit_message_text(text="Главное меню:", reply_markup=reply_markup)
    else:
        query.edit_message_text(text=f"Вы выбрали: {data}")

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
