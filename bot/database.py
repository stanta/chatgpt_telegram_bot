from typing import Optional, Any, List
import pymongo
import uuid
import json
from datetime import datetime
import config
from openai_utils import get_message_embedding


class Database:
    def __init__(self):
        self.client = pymongo.AsyncMongoClient(config.mongodb_uri)        
        self.db = self.client[config.project_name + "_db"]

        self.user_collection = self.db["user"]
        self.dialog_collection = self.db["dialog"]
        # Новая коллекция для хранения сообщений по отдельности
        self.dialog_message_collection = self.db["dialog_message"]

        # Создаём индексы для быстрого поиска по номеру и дате сообщений
        # Note: Index creation moved to async method since AsyncMongoClient requires await
        # self.dialog_message_collection.create_index(
        #     [("dialog_id", pymongo.ASCENDING), ("message_number", pymongo.ASCENDING)],
        #     unique=True
        # )
        # self.dialog_message_collection.create_index(
        #     [("dialog_id", pymongo.ASCENDING), ("date", pymongo.ASCENDING)]
        # )
        
        # Добавляем коллекцию для платежей в __init__
        self.payments_collection = self.db["payments"]
        # self.payments_collection.create_index([("date", pymongo.ASCENDING)])
        # self.payments_collection.create_index([("currency", pymongo.ASCENDING)])

    async def initialize_indexes(self):
        """
        Initialize database indexes. Should be called once after creating Database instance.
        """
        # Create indexes for dialog messages
        await self.dialog_message_collection.create_index(
            [("dialog_id", pymongo.ASCENDING), ("message_number", pymongo.ASCENDING)],
            unique=True
        )
        await self.dialog_message_collection.create_index(
            [("dialog_id", pymongo.ASCENDING), ("date", pymongo.ASCENDING)]
        )
        
        # Create indexes for payments
        await self.payments_collection.create_index([("date", pymongo.ASCENDING)])
        await self.payments_collection.create_index([("currency", pymongo.ASCENDING)])
        await self.create_vector_index_for_dialog_messages()
        

    async def create_vector_index_for_dialog_messages(self, dimensions=1536, collection_name="dialog_message"):
        """
        Пример создания векторного индекса в MongoDB Atlas через 'createSearchIndexes'.
        Предполагается, что размерность эмбеддинга совпадает с 'dimensions'.
        Выполните этот метод один раз после развертывания, чтобы включить поиск по векторному полю.
        """
        command = {
            "createSearchIndexes": collection_name,
            "indexes": [
                {
                    "name": "vector_idx",
                    "definition": {
                        "mappings": {
                            "dynamic": False,
                            "fields": {
                                "vectorized": {
                                    "type": "knnVector",
                                    "dimensions": dimensions,
                                    "similarity": "cosine"
                                }
                            }
                        }
                    }
                }
            ]
        }
        await self.db.command(command)

    async def check_if_user_exists(self, user_id: int, raise_exception: bool = False):
        if await self.user_collection.count_documents({"_id": user_id}) > 0:
            return True
        else:
            if raise_exception:
                raise ValueError(f"User {user_id} does not exist")
            else:
                return False

    async def add_new_user(
        self,
        user_id: int,
        chat_id: int,
        username: str = "",
        first_name: str = "",
        last_name: str = "",
        referral: int = None
    ):
        user_dict = {
            "_id": user_id,
            "chat_id": chat_id,

            "username": username,
            "first_name": first_name,
            "last_name": last_name,
            "referral": referral,

            "last_interaction": datetime.now(),
            "first_seen": datetime.now(),

            "current_dialog_id": None,
            "current_chat_mode": "assistant",
            "current_model": config.models["available_text_models"][0],

            "n_used_tokens": {},

            "n_generated_images": 0,
            "n_transcribed_seconds": 0.0,  # voice message transcription

            "balance": config.init_user_balance,  # in TOKENS
            "last_pit_stop_message_number": 0,
        }

        if not await self.check_if_user_exists(user_id):
            await self.user_collection.insert_one(user_dict)

    async def start_new_dialog(self, user_id: int):
        await self.check_if_user_exists(user_id, raise_exception=True)

        dialog_id = str(uuid.uuid4())
        dialog_dict = {
            "_id": dialog_id,
            "user_id": user_id,
            "chat_mode": await self.get_user_attribute(user_id, "current_chat_mode"),
            "start_time": datetime.now(),
            "model": await self.get_user_attribute(user_id, "current_model"),
            "thread_id": "",
            # Старая логика – поле messages для обратной совместимости
            "messages": [],
            # Новое поле для хранения последнего номера сообщения
            "last_message_number": 0,
            "last_pit_stop_message_number": 0
        }

        # Добавляем новый диалог
        await self.dialog_collection.insert_one(dialog_dict)

        # Обновляем у пользователя текущий диалог
        await self.user_collection.update_one(
            {"_id": user_id},
            {"$set": {"current_dialog_id": dialog_id}}
        )

        return dialog_id
    
    async def get_dialog_attribute(self, user_id,  key: str, dialog_id: str = ""):
        if dialog_id == "":
            dialog_id = await self.get_user_attribute(user_id, "current_dialog_id")
        dialog_dict = await self.dialog_collection.find_one({"_id": dialog_id})
        if key not in dialog_dict:
            return None
        return dialog_dict[key]

    async def get_user_attribute(self, user_id: int, key: str):
        await self.check_if_user_exists(user_id, raise_exception=True)
        user_dict = await self.user_collection.find_one({"_id": user_id})

        if key not in user_dict:
            await self.set_user_attribute(user_id, key, "")
            return None

        return user_dict[key]

    async def set_dialog_attribute(self, user_id: int,  key: str, value: Any, dialog_id: str =""):
        if dialog_id == "":
            dialog_id = await self.get_user_attribute(user_id, "current_dialog_id")
        await self.dialog_collection.update_one({"_id": dialog_id}, {"$set": {key: value
        }})
        
    async def set_user_attribute(self, user_id: int, key: str, value: Any):
        await self.check_if_user_exists(user_id, raise_exception=True)
        await self.user_collection.update_one({"_id": user_id}, {"$set": {key: value}})

    async def update_n_used_tokens(self, user_id: int, model: str, n_input_tokens: int, n_output_tokens: int):
        n_used_tokens_dict = await self.get_user_attribute(user_id, "n_used_tokens")
        bal_attr = await self.get_user_attribute(user_id, "balance")
        balance =  float(bal_attr) if bal_attr !="" else 0
        if model in n_used_tokens_dict:
            n_used_tokens_dict[model]["n_input_tokens"] += n_input_tokens
            n_used_tokens_dict[model]["n_output_tokens"] += n_output_tokens
        else:
            n_used_tokens_dict[model] = {
                "n_input_tokens": n_input_tokens,
                "n_output_tokens": n_output_tokens
            }
        balance -= n_input_tokens
        balance -= n_output_tokens

        await self.set_user_attribute(user_id, "n_used_tokens", n_used_tokens_dict)
        await self.set_user_attribute(user_id, "balance", balance)
        
        n_used_tokens_dialog = 0 if await self.get_dialog_attribute(user_id, "n_used_tokens_dialog") is None else await self.get_dialog_attribute(user_id, "n_used_tokens_dialog")
        n_used_tokens_dialog = int(n_used_tokens_dialog) + n_input_tokens + n_output_tokens
        
        await self.set_user_attribute(user_id, "n_used_tokens", n_used_tokens_dict)
        await self.set_user_attribute(user_id, "balance", balance)
        await self.set_dialog_attribute(user_id, "n_used_tokens_dialog", n_used_tokens_dialog)

    async def check_balance_positive(self, user_id: int):
        bal_attr = await self.get_user_attribute(user_id, "balance")
        balance =  float(bal_attr) if bal_attr !="" else 0
        return  int(balance) >= 0

    async def add_balance(self, 
                    user_id: int, 
                    params: List[Any]):#params currency, price, amount
        currency, price, amount_tokens = params
        balance = await self.get_user_attribute(user_id, "balance")
        if balance is None:
            balance = float(amount_tokens)  # обратная совместимость для старых пользователей
        else:
            balance += float(amount_tokens)
        await self.set_user_attribute(user_id, "balance", balance)
        await self.add_payment(
                user_id, 
                datetime.now(),
                currency,
                float(price) * int(amount_tokens),
                int(amount_tokens)
            )
        
    # ===== Новые методы работы с сообщениями диалога =====

    async def _migrate_legacy_messages(self, dialog_id: str):
        """
        Если в диалоге ещё присутствуют сообщения в старом формате (хранятся в поле messages),
        они последовательно разбираются, сохраняются в новой коллекции с присвоением номера,
        а поле messages очищается. Это позволяет обеспечить бесшовную обратную совместимость.
        """
        dialog_doc = await self.dialog_collection.find_one({"_id": dialog_id})
        if not dialog_doc:
            return

        legacy_messages = dialog_doc.get("messages", [])
        if legacy_messages:
            count = 0
            for legacy_msg in legacy_messages:
                try:
                    if isinstance(legacy_msg, dict):
                        msg_dict = legacy_msg
                    else:
                        msg_dict = json.loads(legacy_msg)
                except Exception:
                    continue
                count += 1
                # Преобразуем дату из строки в datetime; ожидаемый формат: "YYYY-MM-DD HH:MM:SS"
                # try:
                #     msg_date = datetime.strptime(msg_dict.get("date", ""), "%Y-%m-%d %H:%M:%S")
                # except Exception:
                #     msg_date = datetime.now()
                
                assistant_str = ""
                if  "assistant" in msg_dict :
                    assistant_str = msg_dict["assistant"] 
                if "bot" in msg_dict  :
                    assistant_str = assistant_str + " " + msg_dict["bot"]             
                new_msg_doc = {
                    "dialog_id": dialog_id,
                    "message_number": count,
                    "user": msg_dict["user"],
                    "assistant": assistant_str,
                    "date": msg_dict["date"]
                }
                await self.dialog_message_collection.insert_one(new_msg_doc)
            # Обновляем диалог: очищаем legacy-сообщения и запоминаем последний номер
            await self.dialog_collection.update_one(
                {"_id": dialog_id},
                {"$set": {"messages": [], "last_message_number": count}}
            )

    async def get_dialog_messages(
        self,
        user_id: int,
        dialog_id: Optional[str] = None,
        message_start: Optional[int] = None,
        message_end: Optional[int] = None,
        date_start: Optional[datetime] = None,
        date_end: Optional[datetime] = None
    ):
        """
        Получает список сообщений для диалога.
        Если заданы параметры диапазона по номеру или по дате, применяется соответствующий фильтр.
        Результат возвращается в том же формате, что и раньше – список JSON-строк.
        """
        await self.check_if_user_exists(user_id, raise_exception=True)

        if dialog_id is None:
            dialog_id = await self.get_user_attribute(user_id, "current_dialog_id")

        # Если есть legacy-сообщения – мигрируем их в новую коллекцию
        await self._migrate_legacy_messages(dialog_id)

        query = {"dialog_id": dialog_id}
        if message_start is not None or message_end is not None:
            num_query = {}
            if message_start is not None:
                num_query["$gte"] = message_start
            if message_end is not None:
                num_query["$lte"] = message_end
            query["message_number"] = num_query
        if date_start is not None or date_end is not None:
            date_query = {}
            if date_start is not None:
                date_query["$gte"] = date_start
            if date_end is not None:
                date_query["$lte"] = date_end
            query["date"] = date_query

        # Выбираем сообщения, сортируя по возрастанию номера
        messages_cursor = self.dialog_message_collection \
            .find(query) \
            .sort("message_number", pymongo.ASCENDING)
        messages = await messages_cursor.to_list(length=None)
        # messages = []
        # for msg in messages_cursor:
        #     # Приводим сообщение к тому же формату, что использовался ранее
        #     msg_dict = {
        #         "user": msg["user"],
        #         "assistant": msg["assistant"],
        #         "date": msg["date"]
        #     }
        #     messages.append(msg_dict)
        return messages

    async def set_dialog_messages(self, user_id: int, dialog_messages: list, dialog_id: Optional[str] = None):
        """
        Перезаписывает все сообщения диалога.
        Принимает список сообщений в старом формате (как JSON-строки или dict).
        Для обратной совместимости legacy-поле messages очищается, а все сообщения сохраняются
        в новой коллекции с последовательной нумерацией.
        """
        await self.check_if_user_exists(user_id, raise_exception=True)

        if dialog_id is None:
            dialog_id = await self.get_user_attribute(user_id, "current_dialog_id")

        # Очищаем legacy-поле
        await self.dialog_collection.update_one(
            {"_id": dialog_id},
            {"$set": {"messages": []}}
        )
        # Удаляем все сообщения в новой коллекции для данного диалога
        await self.dialog_message_collection.delete_many({"dialog_id": dialog_id})

        message_number = 0
        for msg in dialog_messages:
            # Если сообщение представлено в виде JSON-строки – разбираем его
            if isinstance(msg, str):
                try:
                    msg_dict = json.loads(msg)
                except Exception:
                    continue
            elif isinstance(msg, dict):
                msg_dict = msg
            else:
                continue

            message_number += 1
            # try:
            #     msg_date = datetime.now # strptime(msg_dict.get("date", ""), "%Y-%m-%d %H:%M:%S")
            # except Exception:
            #     msg_date = datetime.now()
            new_msg_doc = {
                "dialog_id": dialog_id,
                "message_number": message_number,
                "user": msg_dict["user"],
                "assistant": msg_dict["assistant"],
                "date": msg_dict["date"]
            }
            await self.dialog_message_collection.insert_one(new_msg_doc)

        # Обновляем в диалоге последний номер сообщения
        await self.dialog_collection.update_one(
            {"_id": dialog_id},
            {"$set": {"last_message_number": message_number}}
        )

    async def add_dialog_message(self, user_id: int, message: Any, vectorized: Optional[list[Any]] = None, dialog_id: Optional[str] = None):
        """
        Добавляет одно сообщение в диалог с векторным хранением для MongoDB Atlas.
        """
        await self.check_if_user_exists(user_id, raise_exception=True)

        if dialog_id is None:
            dialog_id = await self.get_user_attribute(user_id, "current_dialog_id")

        # Мигрируем любые legacy-сообщения
        # await self._migrate_legacy_messages(dialog_id)

        updated_dialog = await self.dialog_collection.find_one_and_update(
            {"_id": dialog_id},
            {"$inc": {"last_message_number": 1}},
            return_document=pymongo.ReturnDocument.AFTER
        )
        message_number = updated_dialog.get("last_message_number", 1)

        if isinstance(message, str):
            import json
            try:
                msg_dict = json.loads(message)
            except Exception:
                raise ValueError("Неверный формат сообщения")
        elif isinstance(message, dict):
            msg_dict = message
        else:
            raise ValueError("Сообщение должно быть строкой или словарем")

        # normalize user field
        user_field = msg_dict["user"]
        if isinstance(user_field, list):
            # join list elements into one string
            user_text = ";".join(map(str, user_field))
        else:
            user_text = str(user_field)

        # normalize assistant field
        assistant_text = str(msg_dict["assistant"])
        # vectorized = await get_message_embedding(user_text + " " + assistant_text)
        new_msg_doc = {
            "dialog_id":    dialog_id,
            "message_number": message_number,
            "user":         msg_dict["user"],
            "assistant":    msg_dict["assistant"],
            "date":         msg_dict["date"],
            "vectorized":   vectorized
        }
        await self.dialog_message_collection.insert_one(new_msg_doc)


    # Метод get_dialog_messages (без дополнительных параметров) можно оставить для обратной совместимости,
    # так как он вызывает новую реализацию с фильтрами, если они не заданы.

    async def find_relevant_contexts(self, query_text: str, top_k: int = 3):
        """
        Выполняет поиск по векторному полю 'vectorized' в dialog_message_collection и возвращает
        наиболее релевантные фрагменты контекста, взятые из полей 'user' и 'assistant'.
        Требует заранее созданного индекса (см. create_vector_index_for_dialog_messages).
        """
        # Получаем вектор эмбеддинга запроса
        embedding = await get_message_embedding(query_text)

        # Формируем pipeline с использованием knnBeta для поиска по векторному полю
        pipeline = [
            {
                "$search": {
                    "index": "vector_idx",
                    "knnBeta": {
                        "vector": embedding[0],
                        "path": "vectorized",
                        "k": top_k
                    }
                }
            },
            {
                "$project": {
                    "_id": 0,
                    "dialog_id": 1,
                    "user": 1,
                    "assistant": 1,
                    "score": {"$meta": "searchScore"}
                }
            },
            {"$limit": top_k}
        ]

        # Выполняем запрос
        raw_cursor = await self.dialog_message_collection.aggregate(pipeline)
        docs = await raw_cursor.to_list(length=top_k)        
        # Собираем найденные записи
        contexts = []
        for doc in docs:
            # Формируем строку контекста из полей user и assistant
            context_str = f"User: {doc.get('user', '')}\nAssistant: {doc.get('assistant', '')}"
            contexts.append(context_str)

        return contexts
    


    async def add_payment(
        self,
        user_id: int,
        payment_date: datetime,
        currency: str,
        amount_money: float,
        amount_tokens: float
    ):
        """
        Добавляет запись о платеже в базу данных
        """
        await self.check_if_user_exists(user_id, raise_exception=True)
        
        payment_doc = {
            "user_id": user_id,
            "date": payment_date,
            "currency": currency,
            "amount_money": amount_money,
            "amount_tokens": amount_tokens
        }
        
        # Вставляем запись в коллекцию платежей
        await self.payments_collection.insert_one(payment_doc)
    
    async def get_payments(self, user_id:int):
        payments_cursor = self.payments_collection.find({"user_id": user_id}).sort("date", pymongo.DESCENDING)
        return await payments_cursor.to_list(length=None)

    async def get_referrals_number(self, ref_id):
        return await self.user_collection.count_documents({"referral": str(ref_id)})
    
    async def get_referalls_purchases(self, ref_id:int):
        # Получаем пользователей с заданным referral
        referrals_cursor = self.user_collection.find({"referral": str(ref_id)})
        referrals = await referrals_cursor.to_list(length=None)
        purchases = 0
        for ref in referrals:
            # Получаем платежи для каждого найденного пользователя
            payments_cursor = self.payments_collection.find({"user_id": ref["_id"]})
            payments = await payments_cursor.to_list(length=None)
            for pay in payments:
                purchases += pay.get("amount_tokens", 0.0)
        return purchases
