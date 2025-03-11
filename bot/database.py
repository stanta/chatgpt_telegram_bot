from typing import Optional, Any
import pymongo
import uuid
import json
from datetime import datetime
import config


class Database:
    def __init__(self):
        self.client = pymongo.MongoClient(config.mongodb_uri)
        self.db = self.client["chatgpt_telegram_bot"]

        self.user_collection = self.db["user"]
        self.dialog_collection = self.db["dialog"]
        # Новая коллекция для хранения сообщений по отдельности
        self.dialog_message_collection = self.db["dialog_message"]

        # Создаём индексы для быстрого поиска по номеру и дате сообщений
        self.dialog_message_collection.create_index(
            [("dialog_id", pymongo.ASCENDING), ("message_number", pymongo.ASCENDING)],
            unique=True
        )
        self.dialog_message_collection.create_index(
            [("dialog_id", pymongo.ASCENDING), ("date", pymongo.ASCENDING)]
        )
        # Добавляем коллекцию для платежей в __init__
        self.payments_collection = self.db["payments"]
        self.payments_collection.create_index([("date", pymongo.ASCENDING)])
        self.payments_collection.create_index([("currency", pymongo.ASCENDING)])

    def check_if_user_exists(self, user_id: int, raise_exception: bool = False):
        if self.user_collection.count_documents({"_id": user_id}) > 0:
            return True
        else:
            if raise_exception:
                raise ValueError(f"User {user_id} does not exist")
            else:
                return False

    def add_new_user(
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

        if not self.check_if_user_exists(user_id):
            self.user_collection.insert_one(user_dict)

    def start_new_dialog(self, user_id: int):
        self.check_if_user_exists(user_id, raise_exception=True)

        dialog_id = str(uuid.uuid4())
        dialog_dict = {
            "_id": dialog_id,
            "user_id": user_id,
            "chat_mode": self.get_user_attribute(user_id, "current_chat_mode"),
            "start_time": datetime.now(),
            "model": self.get_user_attribute(user_id, "current_model"),
            "thread_id": "",
            # Старая логика – поле messages для обратной совместимости
            "messages": [],
            # Новое поле для хранения последнего номера сообщения
            "last_message_number": 0,
            "last_pit_stop_message_number": 0
        }

        # Добавляем новый диалог
        self.dialog_collection.insert_one(dialog_dict)

        # Обновляем у пользователя текущий диалог
        self.user_collection.update_one(
            {"_id": user_id},
            {"$set": {"current_dialog_id": dialog_id}}
        )

        return dialog_id
    
    def get_dialog_attribute(self, user_id,  key: str, dialog_id: str = ""):
        if dialog_id == "":
            dialog_id = self.get_user_attribute(user_id, "current_dialog_id")
        dialog_dict = self.dialog_collection.find_one({"_id": dialog_id})
        if key not in dialog_dict:
            return None
        return dialog_dict[key]

    def get_user_attribute(self, user_id: int, key: str):
        self.check_if_user_exists(user_id, raise_exception=True)
        user_dict = self.user_collection.find_one({"_id": user_id})

        if key not in user_dict:
            self.set_user_attribute(user_id, key, "")
            return None

        return user_dict[key]

    def set_dialog_attribute(self, user_id: int,  key: str, value: Any, dialog_id: str =""):
        if dialog_id == "":
            dialog_id = self.get_user_attribute(user_id, "current_dialog_id")
        self.dialog_collection.update_one({"_id": dialog_id}, {"$set": {key: value
        }})
        
    def set_user_attribute(self, user_id: int, key: str, value: Any):
        self.check_if_user_exists(user_id, raise_exception=True)
        self.user_collection.update_one({"_id": user_id}, {"$set": {key: value}})

    def update_n_used_tokens(self, user_id: int, model: str, n_input_tokens: int, n_output_tokens: int):
        n_used_tokens_dict = self.get_user_attribute(user_id, "n_used_tokens")
        bal_attr = self.get_user_attribute(user_id, "balance")
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

        self.set_user_attribute(user_id, "n_used_tokens", n_used_tokens_dict)
        self.set_user_attribute(user_id, "balance", balance)
        
        n_used_tokens_dialog = 0 if self.get_dialog_attribute(user_id, "n_used_tokens_dialog") is None else self.get_dialog_attribute(user_id, "n_used_tokens_dialog")
        n_used_tokens_dialog = int(n_used_tokens_dialog) + n_input_tokens + n_output_tokens
        
        self.set_user_attribute(user_id, "n_used_tokens", n_used_tokens_dict)
        self.set_user_attribute(user_id, "balance", balance)
        self.set_dialog_attribute(user_id, "n_used_tokens_dialog", n_used_tokens_dialog)

    def check_balance_positive(self, user_id: int):
        bal_attr = self.get_user_attribute(user_id, "balance")
        balance =  float(bal_attr) if bal_attr !="" else 0
        return  int(balance) >= 0

    def add_balance(self, 
                    user_id: int, 
                    params: [] ):#params currency, price, amount
        currency, price, amount_tokens = params
        balance = self.get_user_attribute(user_id, "balance")
        if balance is None:
            balance = float(amount_tokens)  # обратная совместимость для старых пользователей
        else:
            balance += float(amount_tokens)
        self.set_user_attribute(user_id, "balance", balance)
        self.add_payment(
                user_id, 
                datetime.now(),
                currency,
                float(price) * int(amount_tokens),
                int(amount_tokens)
            )
        
    # ===== Новые методы работы с сообщениями диалога =====

    def _migrate_legacy_messages(self, dialog_id: str):
        """
        Если в диалоге ещё присутствуют сообщения в старом формате (хранятся в поле messages),
        они последовательно разбираются, сохраняются в новой коллекции с присвоением номера,
        а поле messages очищается. Это позволяет обеспечить бесшовную обратную совместимость.
        """
        dialog_doc = self.dialog_collection.find_one({"_id": dialog_id})
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
                self.dialog_message_collection.insert_one(new_msg_doc)
            # Обновляем диалог: очищаем legacy-сообщения и запоминаем последний номер
            self.dialog_collection.update_one(
                {"_id": dialog_id},
                {"$set": {"messages": [], "last_message_number": count}}
            )

    def get_dialog_messages(
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
        self.check_if_user_exists(user_id, raise_exception=True)

        if dialog_id is None:
            dialog_id = self.get_user_attribute(user_id, "current_dialog_id")

        # Если есть legacy-сообщения – мигрируем их в новую коллекцию
        self._migrate_legacy_messages(dialog_id)

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
            .sort("message_number", pymongo.ASCENDING) \
            .to_list()
        # messages = []
        # for msg in messages_cursor:
        #     # Приводим сообщение к тому же формату, что использовался ранее
        #     msg_dict = {
        #         "user": msg["user"],
        #         "assistant": msg["assistant"],
        #         "date": msg["date"]
        #     }
        #     messages.append(msg_dict)
        return messages_cursor

    def set_dialog_messages(self, user_id: int, dialog_messages: list, dialog_id: Optional[str] = None):
        """
        Перезаписывает все сообщения диалога.
        Принимает список сообщений в старом формате (как JSON-строки или dict).
        Для обратной совместимости legacy-поле messages очищается, а все сообщения сохраняются
        в новой коллекции с последовательной нумерацией.
        """
        self.check_if_user_exists(user_id, raise_exception=True)

        if dialog_id is None:
            dialog_id = self.get_user_attribute(user_id, "current_dialog_id")

        # Очищаем legacy-поле
        self.dialog_collection.update_one(
            {"_id": dialog_id},
            {"$set": {"messages": []}}
        )
        # Удаляем все сообщения в новой коллекции для данного диалога
        self.dialog_message_collection.delete_many({"dialog_id": dialog_id})

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
            self.dialog_message_collection.insert_one(new_msg_doc)

        # Обновляем в диалоге последний номер сообщения
        self.dialog_collection.update_one(
            {"_id": dialog_id},
            {"$set": {"last_message_number": message_number}}
        )

    def add_dialog_message(self, user_id: int, message: Any, dialog_id: Optional[str] = None):
        """
        Добавляет одно сообщение в диалог.
        Аргумент message может быть либо dict, либо JSON-строкой с ключами 'user', 'assistant' и 'date'.
        При добавлении выполняется атомарное инкрементирование номера сообщения.
        """
        self.check_if_user_exists(user_id, raise_exception=True)

        if dialog_id is None:
            dialog_id = self.get_user_attribute(user_id, "current_dialog_id")

        # Если есть legacy-сообщения – мигрируем их
        self._migrate_legacy_messages(dialog_id)

        # Атомарно увеличиваем счётчик сообщений в документе диалога
        updated_dialog = self.dialog_collection.find_one_and_update(
            {"_id": dialog_id},
            {"$inc": {"last_message_number": 1}},
            return_document=pymongo.ReturnDocument.AFTER
        )
        message_number = updated_dialog.get("last_message_number", 1)

        if isinstance(message, str):
            try:
                msg_dict = json.loads(message)
            except Exception:
                raise ValueError("Неверный формат сообщения")
        elif isinstance(message, dict):
            msg_dict = message
        else:
            raise ValueError("Сообщение должно быть строкой или словарем")

        # try:
        #     msg_date = datetime.strptime(msg_dict.get("date", ""), "%Y-%m-%d %H:%M:%S")
        # except Exception:
        #     msg_date = datetime.now()

        new_msg_doc = {
            "dialog_id": dialog_id,
            "message_number": message_number,
            "user": msg_dict["user"],
            "assistant": msg_dict["assistant"],
            "date": msg_dict["date"]
        }
        self.dialog_message_collection.insert_one(new_msg_doc)

    # Метод get_dialog_messages (без дополнительных параметров) можно оставить для обратной совместимости,
    # так как он вызывает новую реализацию с фильтрами, если они не заданы.

    def add_payment(
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
        self.check_if_user_exists(user_id, raise_exception=True)
        
        payment_doc = {
            "user_id": user_id,
            "date": payment_date,
            "currency": currency,
            "amount_money": amount_money,
            "amount_tokens": amount_tokens
        }
        
        # Вставляем запись в коллекцию платежей
        self.payments_collection.insert_one(payment_doc)
        