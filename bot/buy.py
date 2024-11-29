from telegram import InlineKeyboardMarkup, InlineKeyboardButton, Update
from telegram.ext import Updater, CommandHandler, CallbackQueryHandler, CallbackContext
import config
from i18n import t
from staff import tt
from arcpay_server import create_order, check_order
import database

db = database.Database()

def create_callback_data(action, params):
    return f"{action}:{':'.join(params)}"

# Recursive function to build the menu
async def build_menu(menu_items, path=[]):
    keyboard = []
    for item in menu_items:
        buttons = []
        for button in item.get('buttons', []):
            if 'submenu' in button:
                # Build callback data to identify the submenu path
                submenu_path = path + [button['text']]
                callback_data = 'submenu_' + '_'.join(submenu_path)
                buttons.append(InlineKeyboardButton(button['text'], callback_data=callback_data))
            else:
                action = button['action']
                params = button['params']
                callback_data = create_callback_data(action, params)
                buttons.append(InlineKeyboardButton(button['text'], callback_data=callback_data))
        if buttons:
            keyboard.append(buttons)
    if path:
        # Add a 'Back' button to go to the previous menu
        keyboard.append([InlineKeyboardButton(t("Back"), callback_data='back_' + '_'.join(path[:-1]))])
    return InlineKeyboardMarkup(keyboard)

# Function to handle button presses
async def button_handler(update: Update, context: CallbackContext) -> None:
    query = update.callback_query
    await query.answer()

    data = query.data
    if data.startswith('submenu_'):
        # Extract the submenu path from the callback data
        path = data[len('submenu_'):].split('_')
        # Navigate through the menu configuration to find the submenu
        submenu = context.user_data['menu_config']
        for p in path:
            for item in submenu:
                for button in item.get('buttons', []):
                    if button['text'] == p:
                        submenu = button.get('submenu', [])
                        break
        reply_markup = await build_menu(submenu, path=path)
        await query.edit_message_text(text=t("Choose option:"), reply_markup=reply_markup)
    elif data.startswith('back_'):
        # Handle the 'Back' action
        path = data[len('back_'):].split('_') if '_' in data else []
        submenu = context.user_data['menu_config']
        for p in path:
            for item in submenu:
                for button in item.get('buttons', []):
                    if button['text'] == p:
                        submenu = button.get('submenu', [])
                        break
        reply_markup = await build_menu(submenu, path=path)
        await query.edit_message_text(text=t("Choose option:"), reply_markup=reply_markup)
    else:
        # Handle the final action
        action, *params = data.split(':')
        await query.edit_message_text(text=t(f"Your choice: {data}"))
        if action == "create_order":
            (order_created, order) = await create_order(params[0], params[1], params[2]) #currency, price, amount
            if order_created :
                await query.edit_message_text(t(str(order) +
                    "\n Payment link: ") + order["paymentUrl"])
                (order_payed, status) = await check_order(order['uuid'])
                await query.edit_message_text( status)
                if order_payed: 
                    db.add_balance(update.effective_user.id,  params[2])
            else:
                await query.edit_message_text(order)


# Function to handle the /start command
async def menu_start(update: Update, context: CallbackContext) -> None:
    context.user_data["menu_config"] = tt(config.buy_menu_config, update.message.from_user.language_code)
    reply_markup = await build_menu(context.user_data["menu_config"])
    await update.message.reply_text(t("Choose:"), reply_markup=reply_markup)
