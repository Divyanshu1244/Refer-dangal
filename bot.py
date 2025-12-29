import asyncio
import os
from datetime import datetime, timedelta
from motor.motor_asyncio import AsyncIOMotorClient
from pyrogram import Client, filters
from pyrogram.types import InlineKeyboardMarkup, InlineKeyboardButton, Message
from pyrogram.enums import ChatMemberStatus
from dotenv import load_dotenv

load_dotenv()

# Configuration
BOT_TOKEN = os.getenv("BOT_TOKEN")
API_ID = int(os.getenv("API_ID"))
API_HASH = os.getenv("API_HASH")
MONGO_URI = os.getenv("MONGO_URI")  # e.g., mongodb://localhost:27017
DB_NAME = "tournament_bot"
REQUIRED_CHANNELS = ["@your_channel1", "@your_channel2"]  # Force-subscribe channels
TOURNAMENT_START = datetime(2023, 10, 1)  # Set actual start date
TOURNAMENT_END = TOURNAMENT_START + timedelta(days=10)
PRIZE_POOL = "₹50,000"
SUPPORT_CONTACT = "@your_support_username"  # Or group link
UPDATE_CHANNEL = "@your_update_channel"

# Initialize bot and DB
app = Client("referral_bot", api_id=API_ID, api_hash=API_HASH, bot_token=BOT_TOKEN)
db = AsyncIOMotorClient(MONGO_URI)[DB_NAME]
users_collection = db["users"]
leaderboard_cache = db["leaderboard_cache"]

# Helper: Check if user is in all required channels
async def is_subscribed(user_id):
    for channel in REQUIRED_CHANNELS:
        try:
            member = await app.get_chat_member(channel, user_id)
            if member.status in [ChatMemberStatus.LEFT, ChatMemberStatus.BANNED]:
                return False
        except Exception:
            return False
    return True

# Helper: Get or create user
async def get_or_create_user(user_id):
    user = await users_collection.find_one({"user_id": user_id})
    if not user:
        referral_link = f"https://t.me/{(await app.get_me()).username}?start=ref_{user_id}"
        user = {
            "user_id": user_id,
            "referral_link": referral_link,
            "referrals": [],
            "referral_count": 0,
            "joined_at": datetime.utcnow(),
            "last_start": None
        }
        await users_collection.insert_one(user)
    return user

# Helper: Update leaderboard cache (run periodically)
async def update_leaderboard_cache():
    while True:
        top_users = await users_collection.find().sort("referral_count", -1).limit(10).to_list(10)
        if top_users:  # Fix: Only insert if not empty
            await leaderboard_cache.drop()
            await leaderboard_cache.insert_many(top_users)
        await asyncio.sleep(300)  # Update every 5 minutes

# Helper: Get user's rank
async def get_user_rank(user_id):
    user = await users_collection.find_one({"user_id": user_id})
    if not user:
        return None
    count = user["referral_count"]
    higher_count = await users_collection.count_documents({"referral_count": {"$gt": count}})
    return higher_count + 1

# Start command handler
@app.on_message(filters.command("start"))
async def start_handler(client: Client, message: Message):
    user_id = message.from_user.id
    now = datetime.utcnow()
    
    # Check tournament status
    if now < TOURNAMENT_START or now > TOURNAMENT_END:
        await message.reply("Tournament has ended or not started yet.")
        return
    
    # Parse referral
    referrer_id = None
    if len(message.command) > 1 and message.command[1].startswith("ref_"):
        try:
            referrer_id = int(message.command[1][4:])
        except ValueError:
            pass
    
    user = await get_or_create_user(user_id)
    
    # Check force-subscribe
    if not await is_subscribed(user_id):
        keyboard = InlineKeyboardMarkup([
            [InlineKeyboardButton("Join Channels", url=f"https://t.me/{REQUIRED_CHANNELS[0][1:]}")]  # Link to first channel
        ])
        await message.reply("Please join the required channels to participate.", reply_markup=keyboard)
        return
    
    # Process referral if valid
    if referrer_id and referrer_id != user_id and user_id not in (await users_collection.find_one({"user_id": referrer_id}) or {}).get("referrals", []):
        referrer = await users_collection.find_one({"user_id": referrer_id})
        if referrer:
            # Increment referral count
            await users_collection.update_one(
                {"user_id": referrer_id},
                {"$push": {"referrals": user_id}, "$inc": {"referral_count": 1}}
            )
    
    # Update last start
    await users_collection.update_one({"user_id": user_id}, {"$set": {"last_start": now}})
    
    # Start screen
    rank = await get_user_rank(user_id)
    text = f"""
🏆 Referral Tournament
Duration: {TOURNAMENT_START.strftime('%d/%m')} - {TOURNAMENT_END.strftime('%d/%m')}
Prize Pool: {PRIZE_POOL}
Top 10 Winners!

Steps:
1. Share your referral link.
2. Get friends to join channels & start the bot.
3. Climb the leaderboard!

Your Rank: {rank or 'N/A'}
    """
    keyboard = InlineKeyboardMarkup([
        [InlineKeyboardButton("🔗 Refer & Win", callback_data="refer")],
        [InlineKeyboardButton("📊 Leaderboard", callback_data="leaderboard")],
        [InlineKeyboardButton("📜 Rules", callback_data="rules")],
        [InlineKeyboardButton("📢 Updates", callback_data="updates")],
        [InlineKeyboardButton("🆘 Support", callback_data="support")]
    ])
    await message.reply(text, reply_markup=keyboard)

# Callback handlers
@app.on_callback_query()
async def callback_handler(client: Client, query):
    user_id = query.from_user.id
    data = query.data
    
    if data == "refer":
        user = await users_collection.find_one({"user_id": user_id})
        rank = await get_user_rank(user_id)
        text = f"""
🔗 Your Referral Link: {user['referral_link']}
Total Referrals: {user['referral_count']}
Current Rank: {rank or 'N/A'}
        """
        keyboard = InlineKeyboardMarkup([[InlineKeyboardButton("Back", callback_data="back")]])
        await query.edit_message_text(text, reply_markup=keyboard)
    
    elif data == "leaderboard":
        top_users = await leaderboard_cache.find().sort("referral_count", -1).to_list(10)
        text = "📊 Top 10 Referrers:\n\n"
        for i, user in enumerate(top_users, 1):
            text += f"{i}. User {user['user_id']} - {user['referral_count']} referrals\n"
        keyboard = InlineKeyboardMarkup([[InlineKeyboardButton("Back", callback_data="back")]])
        await query.edit_message_text(text, reply_markup=keyboard)
    
    elif data == "rules":
        text = """
📜 Rules:
- Share your unique link to refer friends.
- Referrals count only after joining required channels and starting the bot.
- No self-referrals, duplicates, or abuse.
- Anti-fraud policy: Violations lead to disqualification.
- Winners: Top 10 by referral count at tournament end.
        """
        keyboard = InlineKeyboardMarkup([[InlineKeyboardButton("Back", callback_data="back")]])
        await query.edit_message_text(text, reply_markup=keyboard)
    
    elif data == "updates":
        await query.edit_message_text(f"📢 Follow for updates: {UPDATE_CHANNEL}")
    
    elif data == "support":
        await query.edit_message_text(f"🆘 Contact Support: {SUPPORT_CONTACT}")
    
    elif data == "back":
        # Re-show start screen (simplified)
        rank = await get_user_rank(user_id)
        text = f"🏆 Tournament Active\nYour Rank: {rank or 'N/A'}"
        keyboard = InlineKeyboardMarkup([
            [InlineKeyboardButton("🔗 Refer & Win", callback_data="refer")],
            [InlineKeyboardButton("📊 Leaderboard", callback_data="leaderboard")],
            [InlineKeyboardButton("📜 Rules", callback_data="rules")],
            [InlineKeyboardButton("📢 Updates", callback_data="updates")],
            [InlineKeyboardButton("🆘 Support", callback_data="support")]
        ])
        await query.edit_message_text(text, reply_markup=keyboard)

# Run the bot (Pyrogram v2 style)
async def main():
    asyncio.create_task(update_leaderboard_cache())  # Start leaderboard updater
    await app.start()
    print("Bot started")
    # No idle() in v2

if __name__ == "__main__":
    app.run(main())
