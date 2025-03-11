import hashlib
import hmac
import json
import os
import logging
import asyncio
from aiohttp import web, ClientSession
from datetime import datetime
from telegram import Update, LabeledPrice
from telegram.ext import CallbackContext
# import aiohttp_cors
import config
import database
from i18n import t

PRIVATE_KEY = config.arc_private_key
ARC_KEY = config.arc_API_key

shop_database = {}

db = database.Database()
logger = logging.getLogger(__name__)


async def create_order(params,  update: Update = {}, context:CallbackContext = {}):
    currency, price, amount = params
    # request_data = await request.json()

    # print(request_data)

    headers = {"Content-Type": "application/json", "ArcKey": ARC_KEY}

    data = config.payment_plans['payment_plans']
    data['currency'] = currency
    data['orderId'] = "INV-ExamsCoach-"+ currency +"-"+ datetime.now().strftime('%Y%m%d%H%M%S')    
    data['items'][0]['title'] = amount + " tokens"
    data['items'][0]['price'] = price
    data['items'][0]['count'] = amount
    result = None
    async with ClientSession() as session:
        async with session.post(config.arcpay_url , json=data, headers=headers) as response:
            if response.status == 200:
                result = await response.json()
                logger.info (f"Order created successfully: {result}")
                # print(f"Order created successfully: {result}")
                # Process the response
                # Add your logic here to handle the received data
                # shop_database[result["uuid"]] = result  # example store order                
                return (True, result) # web.json_response(result)
            
            else:
                status = await response.text()
                logger.exception(
                    f"Failed to create order. Status: {response.status}, Error: {status}"
                )
                
                return (False,  t("Failed to create order, try again, please. Status:") + status)

async def check_order (orderId):
    for i in range(1, 360):
        await asyncio.sleep(10)
        async with ClientSession() as session:
            try:    
                async with session.get(config.arcpay_url + "/" + orderId['uuid'] ) as response:
                    if response.status == 200:
                        result = await response.json()
                        
                        # Process the response
                        # Add your logic here to handle the received data
                        # shop_database[result["uuid"]] = result  # example store order                
                        if result["status"] == "received": 
                            logger.info(f"Order payed successfully: {result}")
                            return (True, result)
                        
                        elif result["status"] == "cancelled" or result["status"] == "failed": 
                            logger.warning(
                                f"Order cancelled or failed  Status: {result}"
                            )
                            return (False, result)
                    
                    else:
                        txt = await response.text()
                        logger.exception(
                        f"Failed to check order status: {response.status}, Error: {txt}"
                        )
                        return (False,  t("Failed to check order status:") +  response.status + "Error:" + txt)
            except Exception as e:
                logger.exception(
                    f"Failed to connect: {e}"
                )
                # return (False,  t("Failed to check order status:") + str(e))

    return (False,  t(f"Order {orderId} wasn't payed for 1 hour,  make a new order, please.") )


# async def get_shop_database(request):
#     return web.json_response(shop_database)


# async def handle_webhook(request):
#     try:
#         # Read the request body
#         raw_body_bytes = await request.read()
#         raw_body = raw_body_bytes.decode("utf-8")
#         data = json.loads(raw_body)

#         # Get the signature from the request headers
#         signature = request.headers.get("X-Signature")
#         if not signature:
#             return web.Response(status=400, text="Missing signature header")

#         # Calculate the expected signature
#         expected_signature = hmac.new(
#             PRIVATE_KEY.encode("utf-8"), raw_body_bytes, hashlib.sha256
#         ).hexdigest()

#         # Validate the signature
#         if not hmac.compare_digest(signature, expected_signature):
#             return web.Response(status=403, text="Invalid signature")

#         # Process the request
#         # Add your logic here to handle the received data
#         print(f"Received data: {data}")

#         if data["event"] == "order.status.changed":
#             shop_database[data["data"]["uuid"]] = data["data"]  # update store order
#             if shop_database[data["data"]["uuid"]]["status"] == "received":
#                 logger.info("Order received successfully, we capture it!")

#         return web.Response(status=200, text="Webhook received successfully")

#     except json.JSONDecodeError:
#         return web.Response(status=400, text="Invalid JSON format")
#     except Exception as e:
#         return web.Response(status=500, text=f"Unexpected error: {str(e)}")



# To run the server
# def create_app():
#     app = web.Application()
#     cors = aiohttp_cors.setup(
#         app,
#         defaults={
#             "*": aiohttp_cors.ResourceOptions(
#                 allow_credentials=True,
#                 expose_headers="*",
#                 allow_headers="*",
#                 allow_methods=["POST", "GET", "OPTIONS"],
#             )
#         },
#     )
#     app.router.add_post("/create", create_order)
#     app.router.add_post("/webhook", handle_webhook)
#     app.router.add_get("/", get_shop_database)
#     for route in list(app.router.routes()):
#         cors.add(route)
#     return app


# if __name__ == "__main__":
#     web.run_app(create_app(), port=1080)