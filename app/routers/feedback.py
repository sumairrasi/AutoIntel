from fastapi import APIRouter, Body, Depends, HTTPException,Query, Request
import pytz
from app.db import models
from app.schemas import schema
from app.db.database import get_db
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import func, select
from datetime import datetime, date
from typing import Optional
import requests
from fastapi import Depends, HTTPException
from sqlalchemy import select, desc
from app.utils.logging_config import get_logger
from app.utils.token_utils import verify_token
from app.schemas.schema import LLMConfigTuneResponse
from app.config.constant import THRESHOLD
from app.config.constant import GATEWAY_BASE_URL
from app.utils.llm_utils import LLmConfigutils
from math import ceil

from app.utils.util_file import update_negative_feedback_flag

router = APIRouter(prefix="/feedback", tags=["feedbacks"])
logger = get_logger("feedback")



@router.post("/feedback/create", response_model=schema.FeedbackResponse)
async def create_or_update_feedback(
    feedback: schema.FeedbackCreate,
    db: AsyncSession = Depends(get_db),
    request: Request = None,
):
    logger.info(f"Endpoint: {request.url.path if request else '/feedback/create'} | Request received  user_id={feedback.user_id}, chat_id={feedback.chat_id}, message_id={feedback.message_id}")

    # Ensure the chat user exists
    result = await db.execute(
        select(models.ChatUser).where(
            models.ChatUser.user_id == feedback.user_id,
            models.ChatUser.chat_id == feedback.chat_id
        )
    )
    chat_user = result.scalar_one_or_none()
    if not chat_user:
        logger.warning(f"Endpoint: {request.url.path if request else '/feedback/create'} | Chat user not found  user_id={feedback.user_id}, chat_id={feedback.chat_id}")
        raise HTTPException(status_code=404, detail="Chat user not found")

    # Check if feedback already exists for this message
    stmt = select(models.Feedback).where(
        models.Feedback.chat_id == feedback.chat_id,
        models.Feedback.message_id == feedback.message_id,
    )
    res = await db.execute(stmt)
    existing = res.scalars().first()

    if existing:
        # Update existing feedback
        logger.info(f"Endpoint: {request.url.path if request else '/feedback/create'} | Updating existing feedback  feedback_id={existing.id}, choice={feedback.feed_choice}")
        existing.feed_choice = feedback.feed_choice
        existing.description = feedback.description
        existing.source = feedback.source
        existing.date = datetime.now(pytz.timezone("Asia/Kolkata"))
        await db.commit()
        await db.refresh(existing)

        if feedback.feed_choice == models.FeedbackChoice.NEGATIVE:
            logger.info(f"Endpoint: {request.url.path if request else '/feedback/create'} | Negative feedback detected  updating flag")
            await update_negative_feedback_flag(db)

        logger.info(f"Endpoint: {request.url.path if request else '/feedback/create'} | Feedback updated successfully  id={existing.id}")
        return existing

    # Otherwise, create new feedback
    logger.info(f"Endpoint: {request.url.path if request else '/feedback/create'} | Creating new feedback  choice={feedback.feed_choice}")
    new_feedback = models.Feedback(
        chat_id=feedback.chat_id,
        message_id=feedback.message_id,
        feed_choice=feedback.feed_choice,
        description=feedback.description,
        source=feedback.source,
    )
    db.add(new_feedback)
    await db.commit()
    await db.refresh(new_feedback)

    await update_negative_feedback_flag(db, feedback.feed_choice)

    logger.info(f"Endpoint: {request.url.path if request else '/feedback/create'} | New feedback created successfully  id={new_feedback.id}")
    return new_feedback






    



@router.get("/report")
async def feedback_report(
    start_date: Optional[date] = Query(None, description="Start date (YYYY-MM-DD)"),
    end_date: Optional[date] = Query(None, description="End date (YYYY-MM-DD)"),
    page: int = Query(0, ge=0, description="Page number (0-based)"),
    size: int = Query(10, ge=1, le=100, description="Page size"),
    sort_by: str = Query(
        "total_feedbacks",
        description="Sort by: total_feedbacks | positive_count | negative_count | last_feedback_date"
    ),
    sort_dir: str = Query("desc", description="Sort direction: asc | desc"),
    search: Optional[str] = Query(None, description="Search by username or email"),  
    db: AsyncSession = Depends(get_db),
    user: dict = Depends(verify_token),
):
    logger.info(f"Endpoint: /report | Request received  start_date={start_date}, end_date={end_date}, page={page}, size={size}, sort_by={sort_by}, sort_dir={sort_dir}, search={search}")

    token = user["raw_token"]

    # ---- Default to today if no dates given ----
    today = datetime.utcnow().date()
    if not start_date and not end_date:
        start_date = today
        end_date = today
    logger.info(f"Endpoint: /report | Date range applied  {start_date} to {end_date}")

    # ---- Date filtering ----
    if end_date:
        end_dt = datetime.combine(end_date, datetime.max.time())
    else:
        end_dt = datetime.combine(today, datetime.max.time())

    query = select(models.Feedback)
    if start_date:
        start_dt = datetime.combine(start_date, datetime.min.time())
        query = query.where(
            models.Feedback.date >= start_dt,
            models.Feedback.date <= end_dt
        )
    else:
        query = query.where(models.Feedback.date <= end_dt)

    result = await db.execute(query)
    feedbacks = result.scalars().all()
    logger.info(f"Endpoint: /report | Feedbacks fetched  {len(feedbacks)}")

    # ---- Fetch related ChatUsers ----
    chat_ids = [f.chat_id for f in feedbacks]
    result_users = await db.execute(
        select(models.ChatUser).where(models.ChatUser.chat_id.in_(chat_ids))
    )
    chat_users = result_users.scalars().all()
    logger.info(f"Endpoint: /report | ChatUsers fetched  {len(chat_users)}")

    chat_user_map = {cu.chat_id: cu.user_id for cu in chat_users}

    # ---- Call user-service API to fetch ALL users ----
    url = f"{GATEWAY_BASE_URL}/user-service/api/user/find-list"
    headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}
    payload = {
        "searchTerm": "",
        "searchFields": [],
        "sortBy": "createdOn",
        "sortDirection": "DESC",
        "page": 0,
        "size": 10000,
        "filters": {
             "isDeleted": {"condition": "=", "value": False}
        }
    }

    logger.info("Endpoint: /report | Calling user-service to fetch users")
    resp = requests.post(url, headers=headers, json=payload)
    if resp.status_code != 200:
        logger.error(f"Endpoint: /report | user-service error  {resp.status_code}, {resp.text}")
        raise HTTPException(status_code=resp.status_code, detail=resp.text)

    users_data = resp.json().get("data", [])
    logger.info(f"Endpoint: /report | user-service returned  {len(users_data)} users")

    users_data_ids = {int(u["id"]) for u in users_data}

    # build user_map ONLY for users present in users_data
    user_map = {
        int(u["id"]): {
            "user_id": int(u["id"]),
            "username": u.get("firstName") or u.get("usersDetails", {}).get("displayName", "Unknown"),
            "email": u.get("email"),
            "isActive": u.get("isActive"),
            "role": u["roles"][0]["name"] if u.get("roles") else None,
            "role_id": u["roles"][0]["id"] if u.get("roles") else None,
            "positive_count": 0,
            "negative_count": 0,
            "total_feedbacks": 0,
            "last_feedback_date": None,
        }
        for u in users_data
    }

    for f in feedbacks:
        user_id = chat_user_map.get(f.chat_id)

        if not user_id or user_id not in users_data_ids:
            continue

        if f.feed_choice == "positive":
            user_map[user_id]["positive_count"] += 1
        elif f.feed_choice == "negative":
            user_map[user_id]["negative_count"] += 1

        user_map[user_id]["total_feedbacks"] += 1

        if (
            not user_map[user_id]["last_feedback_date"]
            or f.date > user_map[user_id]["last_feedback_date"]
        ):
            user_map[user_id]["last_feedback_date"] = f.date

    all_users = list(user_map.values())
    logger.info(f"Endpoint: /report | Processed users after feedback mapping  {len(all_users)}")

    if search:
        search_lower = search.lower()
        before_filter = len(all_users)
        all_users = [
            u for u in all_users
            if (u["username"] and search_lower in u["username"].lower())
            or (u["email"] and search_lower in u["email"].lower())
        ]
        logger.info(f"Endpoint: /report | Search applied  before={before_filter}, after={len(all_users)}")

    reverse = sort_dir.lower() == "desc"
    if sort_by in {"total_feedbacks", "positive_count", "negative_count"}:
        all_users.sort(key=lambda x: x[sort_by], reverse=reverse)
    elif sort_by == "last_feedback_date":
        all_users.sort(key=lambda x: x[sort_by] or datetime.min, reverse=reverse)

    # ---- Pagination ----
    total_count = len(all_users)
    start_idx = page * size
    end_idx = start_idx + size
    paginated_users = all_users[start_idx:end_idx]
    last_page = ceil(total_count / size)

    logger.info(f"Endpoint: /report | Pagination applied  total={total_count}, page={page}, size={size}, last_page={last_page}, returned={len(paginated_users)}")

    # ---- Global summary ----
    positive = sum(u["positive_count"] for u in all_users)
    negative = sum(u["negative_count"] for u in all_users)
    total_feedbacks = sum(u["total_feedbacks"] for u in all_users)
    total_queries = positive + negative

    logger.info(f"Endpoint: /report | Summary  total_feedbacks={total_feedbacks}, positive={positive}, negative={negative}, queries={total_queries}")

    return {
        "summary": {
            "start_date": str(start_date) if start_date else "all",
            "end_date": end_dt.isoformat(),
            "total_feedbacks": total_feedbacks,
            "positive_count": positive,
            "negative_count": negative,
            "total_queries": total_queries
        },
        "pagination": {
            "page": page,
            "size": size,
            "total_users": total_count,
            "last_page": last_page
        },
        "data": paginated_users
    }

















@router.get("/user/{user_id}/report")
async def user_feedback_report(
    user_id: int,
    start_date: Optional[date] = Query(None, description="Start date (YYYY-MM-DD)"),
    db: AsyncSession = Depends(get_db),
    user: dict = Depends(verify_token)   
):
    logger.info(f"Endpoint: /user/{user_id}/report | Request received  user_id={user_id}, start_date={start_date}")

    token = user["raw_token"]

    # End date = today end of day (UTC)
    today = datetime.utcnow().date()
    end_dt = datetime.combine(today, datetime.max.time())
    logger.info(f"Endpoint: /user/{user_id}/report | Date range applied  end_date={end_dt}")

    query = (
        select(models.Feedback)
        .join(models.ChatUser, models.ChatUser.chat_id == models.Feedback.chat_id)
        .where(models.ChatUser.user_id == user_id)
    )

    if start_date:
        start_dt = datetime.combine(start_date, datetime.min.time())
        query = query.where(
            models.Feedback.date >= start_dt,
            models.Feedback.date <= end_dt
        )
        logger.info(f"Endpoint: /user/{user_id}/report | Query filter  feedbacks between {start_dt} and {end_dt}")
    else:
        query = query.where(models.Feedback.date <= end_dt)
        logger.info(f"Endpoint: /user/{user_id}/report | Query filter  feedbacks up to {end_dt}")

    # Sort by date descending
    result = await db.execute(query.order_by(models.Feedback.date.desc()))
    feedbacks = result.scalars().all()
    logger.info(f"Endpoint: /user/{user_id}/report | Feedbacks fetched  {len(feedbacks)}")

    if not feedbacks:
        logger.warning(f"Endpoint: /user/{user_id}/report | No feedback found for user_id={user_id}")
        return {
            "summary": {
                "user_id": user_id,
                "username": None,
                "start_date": str(start_date) if start_date else "all",
                "end_date": end_dt.isoformat(),
                "total_feedbacks": 0,
                "positive_count": 0,
                "negative_count": 0,
                "total_queries": 0
            },
            "data": []
        }

    # ---- Call user-service API to map user_id -> firstname ----
    logger.info(f"Endpoint: /user/{user_id}/report | Calling user-service to fetch username")
    url = f"{GATEWAY_BASE_URL}/user-service/api/user/find-list"
    headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}
    payload = {
        "searchTerm": "",
        "searchFields": [],
        "sortBy": "createdOn",
        "sortDirection": "DESC",
        "page": 0,
        "size": 1,
        "filters": {
            "id": {
                "value": [user_id],
                "condition": "IN"
            }
        }
    }

    resp = requests.post(url, headers=headers, json=payload)
    if resp.status_code != 200:
        logger.error(f"Endpoint: /user/{user_id}/report | user-service error  {resp.status_code}, {resp.text}")
        raise HTTPException(status_code=resp.status_code, detail=resp.text)

    users_data = resp.json().get("data", [])
    username = None
    if users_data:
        u = users_data[0]
        username = u.get("firstName") or u.get("usersDetails", {}).get("displayName", "Unknown")
    logger.info(f"Endpoint: /user/{user_id}/report | Username mapped → {username}")

    # ---- Aggregates ----
    total = len(feedbacks)
    positive = len([f for f in feedbacks if f.feed_choice == "positive"])
    negative = len([f for f in feedbacks if f.feed_choice == "negative"])
    total_queries = positive + negative
    logger.info(f"Endpoint: /user/{user_id}/report | Summary  total={total}, positive={positive}, negative={negative}, queries={total_queries}")

    return {
        "summary": {
            "user_id": user_id,
            "username": username,   
            "start_date": str(start_date) if start_date else "all",
            "end_date": end_dt.isoformat(),
            "total_feedbacks": total,
            "positive_count": positive,
            "negative_count": negative,
            "total_queries": total_queries
        },
        "data": [
            {
                "id": f.id,
                "chat_id": f.chat_id,
                "date": f.date.isoformat(),
                "description": f.description,
                "feed_choice": f.feed_choice,
                "source": f.source,
            }
            for f in feedbacks
        ]
    }









@router.post("/llm-tune", response_model=LLMConfigTuneResponse)
async def get_latest_negative_feedbacks(
    is_clicked: bool = Body(False, embed=True),
    db: AsyncSession = Depends(get_db),
):
    logger.info(f"Endpoint: /llm-tune | Request received  is_clicked={is_clicked}")

    # Fetch latest 30 negative feedbacks
    result = await db.execute(
        select(models.Feedback.description)
        .where(models.Feedback.feed_choice == "negative")
        .order_by(desc(models.Feedback.date))
        .limit(30)
    )
    feedbacks = result.scalars().all()
    logger.info(f"Endpoint: /llm-tune | Negative feedbacks fetched  count={len(feedbacks)}")

    if not feedbacks:
        logger.warning("Endpoint: /llm-tune | No negative feedbacks found")
        raise HTTPException(status_code=404, detail="No negative feedbacks found")

    combined = "\n".join([f"- {desc}" for desc in feedbacks if desc])
    logger.info(f"Endpoint: /llm-tune | Combined feedback text length  {len(combined)} characters")

    # Call LLM utility
    llm_config = LLmConfigutils()
    params = llm_config.ask_llm(combined)
    dict_params = dict(params)
    logger.info(f"Endpoint: /llm-tune | LLM returned params  {dict_params}")

    # Fetch latest LLM config
    result = await db.execute(select(models.LLMConfig).order_by(models.LLMConfig.id.desc()))
    existing = result.scalars().first()

    if existing:
        existing.temperature = dict_params.get("temperature", existing.temperature)
        existing.frequency_penalty = dict_params.get("frequency_penalty", existing.frequency_penalty)
        existing.presence_penalty = dict_params.get("presence_penalty", existing.presence_penalty)
        message = "LLM config updated successfully"
        logger.info(f"Endpoint: /llm-tune | Existing LLMConfig updated")
    else:
        new_config = models.LLMConfig(**dict_params)
        db.add(new_config)
        message = "LLM config created successfully"
        logger.info(f"Endpoint: /llm-tune | New LLMConfig created")

    # Reset ConfigFlag when clicked
    if is_clicked:
        cfg_result = await db.execute(select(models.ConfigFlag))
        cfg_flag = cfg_result.scalar_one_or_none()
        if cfg_flag:
            cfg_flag.last_triggered_count = 0
            cfg_flag.status = False
            logger.info("Endpoint: /llm-tune | ConfigFlag reset due to click")

    await db.commit()
    logger.info(f"Endpoint: /llm-tune | Database commit completed, returning response")

    return {
        "message": message,
        "params": dict_params
    }




@router.get("/recent")
async def get_recent_feedbacks(
    db: AsyncSession = Depends(get_db),
    user: dict = Depends(verify_token)
):
    logger.info("Endpoint: /recent | Request received")
    token = user["raw_token"]

    result = await db.execute(
        select(models.Feedback)
        .order_by(desc(models.Feedback.date))
        .limit(5)
    )
    feedbacks = result.scalars().all()
    logger.info(f"Endpoint: /recent | Feedbacks fetched  count={len(feedbacks)}")

    if not feedbacks:
        logger.warning("Endpoint: /recent | No feedbacks found")
        raise HTTPException(status_code=404, detail="No feedbacks found")

    chat_ids = [f.chat_id for f in feedbacks]
    result_users = await db.execute(
        select(models.ChatUser).where(models.ChatUser.chat_id.in_(chat_ids))
    )
    chat_users = result_users.scalars().all()
    user_ids = list({cu.user_id for cu in chat_users})
    logger.info(f"Endpoint: /recent | ChatUsers fetched  count={len(chat_users)}, unique user_ids={len(user_ids)}")

    url = f"{GATEWAY_BASE_URL}/user-service/api/user/find-list"
    headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}
    payload = {
        "searchTerm": "",
        "searchFields": [],
        "sortBy": "createdOn",
        "sortDirection": "DESC",
        "page": 0,
        "size": len(user_ids) if user_ids else 1,
        "filters": {
            "isDeleted": {"condition": "=", "value": False},
            "id": {"condition": "IN", "value": user_ids},
        } if user_ids else {}
    }

    logger.info(f"Endpoint: /recent | Calling user-service to fetch users, user_ids={user_ids}")
    resp = requests.post(url, headers=headers, json=payload)
    if resp.status_code != 200:
        logger.error(f"Endpoint: /recent | user-service error  {resp.status_code}, {resp.text}")
        raise HTTPException(status_code=resp.status_code, detail=resp.text)

    users_data = resp.json().get("data", [])
    logger.info(f"Endpoint: /recent | user-service returned  {len(users_data)} users")
    print("users_data: ", users_data)

    # build user_map only for valid users
    user_map = {}
    for u in users_data:
        try:
            uid = int(u.get("id"))
        except (TypeError, ValueError):
            continue
        name = (
            u.get("firstName")
            or (u.get("usersDetails") or {}).get("displayName")
            or u.get("lastName")
            or u.get("username")
            or "Unknown"
        )
        user_map[uid] = name

    logger.info(f"Endpoint: /recent | user_map built  {len(user_map)} valid users")

    # return only feedbacks whose user exists in user_map
    result = []
    for f in feedbacks:
        cu = next((x for x in chat_users if x.chat_id == f.chat_id), None)
        if cu and int(cu.user_id) in user_map:
            result.append({
                "username": user_map[int(cu.user_id)],
                "description": f.description,
                "feed_choice": f.feed_choice,
                "date": f.date.isoformat(),
                "source": f.source,
            })



    logger.info(f"Endpoint: /recent | Returning {len(result)} feedback entries")
    return result


@router.get("/feedback/configure")
async def get_config_flag(db: AsyncSession = Depends(get_db)):
    logger.info("Endpoint: /feedback/configure | Request received")

    result = await db.execute(select(models.ConfigFlag).order_by(models.ConfigFlag.id.desc()))
    config_flag = result.scalar_one_or_none()

    if not config_flag:
        logger.warning("Endpoint: /feedback/configure | No config flag found")
        raise HTTPException(status_code=404, detail="No config flag found")

    logger.info(f"Endpoint: /feedback/configure | Returning config_flag  id={config_flag.id}, last_triggered_count={config_flag.last_triggered_count}, status={config_flag.status}")
    return {
        "id": config_flag.id,
        "last_triggered_count": config_flag.last_triggered_count,
        "status": config_flag.status
    }

