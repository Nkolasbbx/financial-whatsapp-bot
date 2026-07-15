import json
import logging
from datetime import datetime

logger = logging.getLogger("financial")

# IN-MEMORY STORAGE (fallback when Supabase is not configured)
users_db: dict = {}


def get_user(phone: str) -> dict | None:
    """Get user profile from storage."""
    import dependencies
    if dependencies.supabase:
        try:
            result = dependencies.supabase.table("users").select("*").eq("phone", phone).execute()
            if result.data:
                user = result.data[0]
                if isinstance(user.get("roadmap"), str):
                    user["roadmap"] = json.loads(user["roadmap"])
                return user
        except Exception as e:
            logger.error(f"Supabase get_user error: {e}")
    return users_db.get(phone)


def save_user(phone: str, data: dict):
    """Save user profile to storage."""
    data["phone"] = phone
    data["updated_at"] = datetime.utcnow().isoformat()

    import dependencies
    if dependencies.supabase:
        try:
            db_data = {**data}
            if "roadmap" in db_data and not isinstance(db_data["roadmap"], str):
                db_data["roadmap"] = json.dumps(db_data["roadmap"])
            # Remove fields not in users table
            db_data.pop("conversation_history", None)
            dependencies.supabase.table("users").upsert(db_data, on_conflict="phone").execute()
            return
        except Exception as e:
            logger.error(f"Supabase save_user error: {e}")

    users_db[phone] = data


def save_message(phone: str, role: str, content: str):
    """Save individual message to messages table."""
    import dependencies
    if dependencies.supabase:
        try:
            dependencies.supabase.table("messages").insert({
                "phone": phone,
                "role": role,
                "content": content,
            }).execute()
        except Exception as e:
            logger.error(f"Supabase save_message error: {e}")


def get_messages(phone: str, limit: int = 12) -> list:
    """Get last N messages for a user."""
    import dependencies
    if dependencies.supabase:
        try:
            result = (
                dependencies.supabase.table("messages")
                .select("role, content")
                .eq("phone", phone)
                .order("created_at", desc=True)
                .limit(limit)
                .execute()
            )
            messages = result.data or []
            return list(reversed(messages))
        except Exception as e:
            logger.error(f"Supabase get_messages error: {e}")
    return []