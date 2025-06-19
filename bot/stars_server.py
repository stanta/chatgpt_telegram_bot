import logging
import json
from telegram import Update, LabeledPrice
from telegram.ext import Application, CommandHandler,  CallbackContext, PreCheckoutQueryHandler

from aiohttp import web, ClientSession
from datetime import datetime

import config
import database
from i18n import t

# Configuration
# return_url = "https://t.me/Yours_coach_bot"  #TODO: change to get  this from config or from bot data

db = database.Database()
logger = logging.getLogger(__name__)
# yookassa.Configuration.configure (config.yookassa_shop_id, config.yookassa_api_key, logger=logger)

async def create_order(params,  update: Update = {}, context:CallbackContext = {}):
    try:
        chat_id = update.message.chat_id if update.message else update.callback_query.message.chat_id
        currency, price, amount = params
        payload = json.dumps(params)
        await context.bot.send_invoice(
            chat_id=chat_id,
            title="Exams AI Coach tokens",
            description=f"Order for {amount} Exams AI Coach tokens  INV-Stars-ExamsCoach-{datetime.now().strftime('%Y%m%d%H%M%S')}",
            payload= payload, #f"INV-ExamsCoach-{datetime.now().strftime('%Y%m%d%H%M%S')}",
            provider_token="",  # Пустой токен для цифровых товаров
            currency=currency,
            prices=  [LabeledPrice("Exams AI Coach tokens", int(float(price) * float(amount)))],
            start_parameter="start_parameter"
        )
        return (True, t(f"Created order for {amount} Exams AI Coach tokens  INV-Stars-ExamsCoach-{datetime.now().strftime('%Y%m%d%H%M%S')}"))
    except Exception as e:
        logger.exception(f"Exception occurred while creating order: {e}")
        return (False, str(e))


async def precheckout_callback(update: Update, context: CallbackContext):
    query = update.pre_checkout_query
    # if query.invoice_payload != '':
    #     await query.answer(ok=False, error_message=t("Something goes wrong with stars"))
    # else:
    await query.answer(ok=True)

async def successful_payment_callback(update: Update, context: CallbackContext):
    payment = update.message.successful_payment
    telegram_payment_charge_id = payment.telegram_payment_charge_id
    params = json.loads(payment.invoice_payload)
    await db.add_balance(update.effective_user.id, params )

    await update.message.reply_text(t(f"Payment received ID: {telegram_payment_charge_id}"))

async def check_order(order):
    return (True, "dumb scenario, but how?..")