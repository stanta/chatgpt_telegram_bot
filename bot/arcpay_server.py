import hashlib
import hmac
import json
import os
import logging
from aiohttp import web, ClientSession
from datetime import datetime
import aiohttp_cors
import config
import database


PRIVATE_KEY = os.getenv("PRIVATE_KEY")
ARC_KEY = os.getenv("ARC_KEY")

shop_database = {}

db = database.Database()
logger = logging.getLogger(__name__)


async def handle_webhook(request):
    try:
        # Read the request body
        raw_body_bytes = await request.read()
        raw_body = raw_body_bytes.decode("utf-8")
        data = json.loads(raw_body)

        # Get the signature from the request headers
        signature = request.headers.get("X-Signature")
        if not signature:
            return web.Response(status=400, text="Missing signature header")

        # Calculate the expected signature
        expected_signature = hmac.new(
            PRIVATE_KEY.encode("utf-8"), raw_body_bytes, hashlib.sha256
        ).hexdigest()

        # Validate the signature
        if not hmac.compare_digest(signature, expected_signature):
            return web.Response(status=403, text="Invalid signature")

        # Process the request
        # Add your logic here to handle the received data
        print(f"Received data: {data}")

        if data["event"] == "order.status.changed":
            shop_database[data["data"]["uuid"]] = data["data"]  # update store order
            if shop_database[data["data"]["uuid"]]["status"] == "received":
                print("Order received successfully, we capture it!")

        return web.Response(status=200, text="Webhook received successfully")

    except json.JSONDecodeError:
        return web.Response(status=400, text="Invalid JSON format")
    except Exception as e:
        return web.Response(status=500, text=f"Unexpected error: {str(e)}")


async def create_order(request):

    request_data = await request.json()

    print(request_data)

    url = "https://arcpay.online/api/v1/arcpay/order"
    headers = {"Content-Type": "application/json", "ArcKey": ARC_KEY}

    data = config.payment_plans[["payment_plans"]]
    result = None
    async with ClientSession() as session:
        async with session.post(url, json=data, headers=headers) as response:
            if response.status == 200:
                result = await response.json()
                print(f"Order created successfully: {result}")
                # Process the response
                # Add your logic here to handle the received data
                shop_database[result["uuid"]] = result  # example store order
                return web.json_response(result)
            else:
                print(
                    f"Failed to create order. Status: {response.status}, Error: {await response.text()}"
                )


async def get_shop_database(request):
    return web.json_response(shop_database)


# To run the server
def create_app():
    app = web.Application()
    cors = aiohttp_cors.setup(
        app,
        defaults={
            "*": aiohttp_cors.ResourceOptions(
                allow_credentials=True,
                expose_headers="*",
                allow_headers="*",
                allow_methods=["POST", "GET", "OPTIONS"],
            )
        },
    )
    app.router.add_post("/create", create_order)
    app.router.add_post("/webhook", handle_webhook)
    app.router.add_get("/", get_shop_database)
    for route in list(app.router.routes()):
        cors.add(route)
    return app


if __name__ == "__main__":
    web.run_app(create_app(), port=1080)