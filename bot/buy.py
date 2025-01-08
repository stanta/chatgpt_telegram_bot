from telegram import InlineKeyboardMarkup, InlineKeyboardButton, Update
from telegram.ext import Updater, CommandHandler, CallbackQueryHandler, CallbackContext
from telegram.constants import ParseMode
import config
from i18n import t
from staff import tt
import arcpay_server
import yookassa_server
# import yookassa_GPT as yookassa_server
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
                keyboard.append([InlineKeyboardButton(button['text'], callback_data=callback_data)])
            else:
                action = button['action']
                params = button['params']
                callback_data = create_callback_data(action, params)
                keyboard.append([InlineKeyboardButton(button['text'], callback_data=callback_data)])
        # if buttons:
        #     keyboard.append(buttons)
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
        match action:
            case "create_order":
                payserver = arcpay_server
            case "create_order_card_rf":
                payserver = yookassa_server
            case _:
                return
        (order_created, order) = await payserver.create_order(params[0], params[1], params[2]) #currency, price, amount

        if order_created :
            match action:
                case "create_order_card_rf":

                    formatted_order = t(f"""
    *{order['description']}*
    - *Order ID:* {order['metadata']['orderId']}
    - *Status:* {order['status']}
    - *AMOUNT TO PAY:* {order.amount.value} {order.amount.currency}

    - [CLICK HERE TO PAY]({order.confirmation.confirmation_url})

                    """) 
                    await query.edit_message_text(formatted_order, parse_mode=ParseMode.MARKDOWN)
                    (order_payed, status) = await payserver.check_order(order.id) 
                case "create_order": 
                    formatted_order = t(f"""
    - *{order['title']}*
    - *Order ID:* {order['orderId']}
    - *Status:* {order['status']}
    *Items:*
    - *Item ID:* {order['items'][0]['itemId']}
    - *Title:* {order['items'][0]['title']}
    - *Description:* {order['items'][0]['description']}
    - *Price:* {order['items'][0]['price']} {order['currency']}
    - *Count:* {order['items'][0]['count']}
    - *AMOUNT TO PAY:* {order['amount']} {order['currency']}

    - [CLICK HERE TO PAY]({order['paymentUrl']})

                    """)                
                    await query.edit_message_text(formatted_order, parse_mode=ParseMode.MARKDOWN)
                    (order_payed, status) = await payserver.check_order(order['uuid'])
            
            if order_payed: 
                match action:
                    case "create_order":
                        formatted_status = t(f"""
    *{status['title']}*
    - *Order ID:* {status['orderId']}
    - *Status:* {status['status']}
    *Transaction:*
    - *Hash:* {status['txn']['hash']}
    😇 PAYED SUCCESSFULLY ✅ 
                """)
                        await query.edit_message_text(formatted_status, parse_mode=ParseMode.MARKDOWN)
                    case "create_order_card_rf":
                        formatted_status = t(f"""
    *Order ID:* {status['metadata']['orderId']}
    *Payed:* {status['captured_at']}
    😇 PAYED SUCCESSFULLY ✅ 
        """)
                        await query.edit_message_text(formatted_status, parse_mode=ParseMode.MARKDOWN)
                    
                db.add_balance(update.effective_user.id,  params[2])
            else:
                match action:
                    case "create_order":           
                        formatted_status = t(f"""
    *{status['title']}*
    - *Order ID:* {status['orderId']}
    - *Status:* {status['status']}
    🥲 Unfortunately NOT PAYED ❌    
                    """)
                    case "create_order_card_rf":
                        formatted_status = t(f"""    
    *Order ID:* {status}
    🥲 Unfortunately NOT PAYED ❌    
                    """)
                await query.edit_message_text(formatted_status, parse_mode=ParseMode.MARKDOWN)
        else:
                formatted_order = t(f"""
                - *{order['title']}*
                - *Order ID:* {order['orderId']}
                - *Status:* {order['status']}

                """)
                await query.edit_message_text(formatted_order, parse_mode=ParseMode.MARKDOWN)
        
            
            
# Function to handle the /start command
async def menu_start(update: Update, context: CallbackContext) -> None:
    context.user_data["menu_config"] = tt(config.buy_menu_config, update.message.from_user.language_code)
    reply_markup = await build_menu(context.user_data["menu_config"])
    await update.message.reply_text(t("Choose:"), reply_markup=reply_markup)
