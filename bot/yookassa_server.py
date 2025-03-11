import hashlib
import hmac
import json
import os
import logging
import asyncio
import uuid
from aiohttp import web, ClientSession
from datetime import datetime
from telegram import Update, LabeledPrice
from telegram.ext import CallbackContext
import yookassa
import config
import database
from i18n import t

# Configuration
# return_url = "https://t.me/Yours_coach_bot"  #TODO: change to get  this from config or from bot data

db = database.Database()
logger = logging.getLogger(__name__)
yookassa.Configuration.configure (config.yookassa_shop_id, config.yookassa_api_key, logger=logger)

async def create_order(params,  update: Update = {}, context:CallbackContext = {}):
    currency, price, amount = params    
    try:
        payment_data = {
            "amount": {
                "value": str(float(price) * float(amount)),
                "currency": currency
            },
            "confirmation": {
                "type": "redirect",
                "return_url": context._application.bot.link
            },
            "capture": True,
            "description": f"Order for {amount} tokens",
            "metadata": {
                "orderId": f"INV-{currency}-ExamsCoach-{datetime.now().strftime('%Y%m%d%H%M%S')}"
            }
        }

        # Create payment using yookassa Payment.create
        response = yookassa.Payment.create(payment_data) #, idempotency_key=idempotency_key)

        if response.status == "pending":
            return True, response # {"order_id": order_id, "payment_url": payment_url}
        else:
            logger.error(f"Failed to create order: {response.status}")
            return False, response

    except Exception as e:
        logger.exception(f"Exception occurred while creating order: {e}")
        return False, str(e)


async def check_order(order_id):
    for _ in range(60):
        await asyncio.sleep(10)
        try:
            payment = yookassa.Payment.find_one(order_id.id)
            if payment.status == 'succeeded':
                logger.info(f"Order {order_id} ({payment['metadata']['orderId']}) paid successfully")
                return (True, payment)
            elif payment.status in ['canceled', 'failed']:
                logger.warning(f"Order {order_id} ({payment['metadata']['orderId']} canceled or failed")
                return (False, t(f"Order {payment['metadata']['orderId']} canceled or failed"))
        except Exception as e:
            logger.exception(f"Failed to check order {order_id}: {str(e)}")
            return (False, str(e))
    logger.warning(f"Order {order_id} ({payment['metadata']['orderId']}) not paid after 1 hour")
    return (False, t(f"Order {payment['metadata']['orderId']}  wasn't paid for 10 mins, make a new order, please."))

async def handle_webhook(request):
    try:
        raw_body = await request.read()
        data = json.loads(raw_body)
        signature = request.headers.get("X-Yoo-Kassa-Signature")

        expected_signature = hmac.new(
            yookassa.Configuration.secret_key.encode("utf-8"),
            raw_body,
            hashlib.sha256
        ).hexdigest()

        if not hmac.compare_digest(signature, expected_signature):
            return web.Response(status=403, text="Invalid signature")

        if data.get('event') == 'payment.succeeded':
            order_id = data.get('data', {}).get('object', {}).get('id')
            logger.info(f"Payment {order_id} succeeded")
            # Perform actions upon successful payment
            return web.Response(status=200, text="Webhook received successfully")

        return web.Response(status=200, text="Webhook received successfully")

    except Exception as e:
        logger.exception(f"Error handling webhook: {str(e)}")
        return web.Response(status=500, text=f"Unexpected error: {str(e)}")

# def create_app():
#     app = web.Application()
#     # Enable CORS if necessary
#     # cors = aiohttp_cors.setup(...)
#     app.router.add_post('/create', create_order)
#     app.router.add_post('/webhook', handle_webhook)
#     # Add more routes as needed
#     # for route in list(app.router.routes()):
#     #     cors.add(route)
#     return app

# # if __name__ == "__main__":
#     # web.run_app(create_app(), port=1080)