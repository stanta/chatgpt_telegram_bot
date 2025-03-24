from telegram import (
    Update,
    User,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    BotCommand
)
from telegram.ext import (
    Application,
    ApplicationBuilder,
    CallbackContext,
    CommandHandler,
    MessageHandler,
    CallbackQueryHandler,
    AIORateLimiter,
    filters,
    PreCheckoutQueryHandler
)
from i18n import t
import config
import database


db = database.Database()
async def reflink_handler(update: Update, context: CallbackContext) -> None:
    # здесь вы можете генерировать идентификатор реферала на основе id пользователя
    ref_id = update.effective_user.id
    # bot_username = bot.get_me().username
    referrals_number = db.get_referrals_number(ref_id)
    referals_purchases = db.get_referalls_purchases(ref_id)
    rewards = referals_purchases * config.reward_share  - float (db.get_user_attribute(ref_id, "withdrawn") or 0)
    ref_link = f"{context._application.bot.link}/?start={ref_id}"

    # создаем клавиатуру с кнопкой withdraw
    keyboard = [
        [InlineKeyboardButton(t("Withdraw"), callback_data="withdraw_handler")]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)

    await context.bot.send_message(
        chat_id=update.effective_chat.id,
        text=t("Your referral link: {ref_link}\nCopy and share this link with your friends. \n\nYour referals: {referrals_number} \nTheir purchases: {referals_purchases} tokens\nYour rewards: {rewards} tokens\n\n").format(
            ref_link=ref_link,
            referrals_number=referrals_number,
            referals_purchases=referals_purchases,
            rewards=rewards
        ),
        reply_markup=reply_markup
    )
    
async def withdraw_handler(update: Update, context: CallbackContext) -> None:
    user_id = update.effective_user.id
    # Получаем текущее значение withdrawn, если его ещё нет – считаем равным 0
    current_withdrawn = float(db.get_user_attribute(user_id, "withdrawn") or 0)
    referrals_purchases = db.get_referalls_purchases(user_id)
    # Расчет доступной суммы для вывода
    available_reward = referrals_purchases * config.reward_share - current_withdrawn

    if available_reward <= 0:
        await update.callback_query.answer(t("No rewards to withdraw"), show_alert=True)
        return

    # Увеличиваем withdrawn на сумму available_reward
    new_withdrawn = current_withdrawn + available_reward
    db.set_user_attribute(user_id, "withdrawn", new_withdrawn)
    db.add_balance(user_id, [ "tokens",1, available_reward])
    await update.callback_query.answer(t("Rewards withdrawn successfully"))
    await context.bot.send_message(
        chat_id=update.effective_chat.id,
        text=t("You have successfully withdrawn {rewards} tokens to your /balance").format(rewards=available_reward)
    )