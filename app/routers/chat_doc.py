from datetime import datetime, timedelta
from fastapi import APIRouter,HTTPException,Query
from typing import List
from sqlalchemy.ext.asyncio import AsyncSession
from app.db.database import get_db
from fastapi import APIRouter, Depends, HTTPException, status
from app.chatbot_config.config import ChatRouter
from app.utils.logging_config import get_logger
from app.utils.util_file import create_new_chat_session
from fastapi import APIRouter, Depends, HTTPException, Query, status
from app.schemas.schema import ChatResponse,ChatPayload,ChatHistoryResponse, FirstQuestionResponse,Usage,LatestMessagesResponse
from typing import List
from sqlalchemy import select
from app.db.models import ChatUser, ChatUserHistory,Feedback,FeedbackChoice
from langchain_community.callbacks import get_openai_callback
from app.chatbot_config.chat_history_serializer import serialize_docs
from sqlalchemy import select, func
import json
from collections import Counter
from app.utils.util_file import get_chat_model
import uuid
import pytz

router = APIRouter(prefix="/chat", tags=["Doc Chat"])
logger = get_logger("chat_doc")
    
    
from fastapi import Request

@router.post("/chat", response_model=ChatResponse, status_code=status.HTTP_200_OK)
async def send_message(
    payload: ChatPayload,
    db: AsyncSession = Depends(get_db),
    request: Request = None,
) -> ChatResponse:
    logger.info(f"Endpoint: {request.url.path if request else '/chat'} Request received  send_message for user_id={payload.user_id}")

    # ---- Find or create chat session ----
    if payload.chat_id is not None:
        logger.info(f"Checking existing chat session  chat_id={payload.chat_id}, user_id={payload.user_id}")
        stmt = select(ChatUser).where(
            ChatUser.chat_id == payload.chat_id,
            ChatUser.user_id == payload.user_id
        )
        result = await db.execute(stmt)
        chat_user = result.scalars().first()
        if chat_user is None:
            logger.warning(f"Chat session not found  chat_id={payload.chat_id}, user_id={payload.user_id}")
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"chat_id '{payload.chat_id}' not found for user {payload.user_id}"
            )
        chat_id = payload.chat_id
        logger.info(f"Using existing chat session  chat_id={chat_id}")
    else:
        logger.info(f"Creating new chat session for user_id={payload.user_id}")
        chat_user = await create_new_chat_session(db, payload.user_id)
        chat_id = chat_user.chat_id
        logger.info(f"New chat session created  chat_id={chat_id}")

    # --- Use IST timestamp ---
    chat_user.last_message_at = datetime.now(pytz.timezone("Asia/Kolkata"))

    # ---- Lazy default feedback for last AI response ----
    hist_stmt = select(ChatUserHistory).where(ChatUserHistory.chat_id == chat_id)
    hist_res = await db.execute(hist_stmt)
    history = hist_res.scalars().first()

    if history and history.chat_history:
        last_ai = next((msg for msg in reversed(history.chat_history) if msg["role"] == "ai"), None)
        if last_ai:
            fb_stmt = select(Feedback).where(Feedback.chat_id == chat_id)
            fb_res = await db.execute(fb_stmt)
            feedbacks = fb_res.scalars().all()
            if not feedbacks:
                logger.info(f"No feedback found for last AI response  chat_id={chat_id}, adding default POSITIVE feedback")
                feedback = Feedback(
                    chat_id=chat_id,
                    feed_choice=FeedbackChoice.POSITIVE
                )
                db.add(feedback)
                await db.commit()

    # ---- Generate AI response ----
    logger.info(f"Invoking chatbot model for chat_id={chat_id}, user_id={payload.user_id}")
    llm = await get_chat_model(db)
    chatbot = ChatRouter(
        temperature=llm.temperature,
        presence_penalty=llm.presence_penalty,
        frequency_penalty=llm.frequency_penalty
    )

    with get_openai_callback() as cb:
        chat = chatbot.invoke(payload.message)

    serialized_context = serialize_docs(chat.get("context"), max_chars=500)
    logger.info(f"Chatbot response generated  tokens used: {cb.total_tokens}, cost: {cb.total_cost}")

    chat_user.total_tokens = cb.total_tokens
    chat_user.prompt_tokens = cb.prompt_tokens
    chat_user.completion_tokens = cb.completion_tokens
    chat_user.total_cost = cb.total_cost

    if history is None:
        logger.info(f"No chat history found  creating new history for chat_id={chat_id}")
        history = ChatUserHistory(
            chat_id=chat_id,
            chat_history=[]
        )
        db.add(history)

    history.chat_history.append({
        "role": "user",
        "content": payload.message
    })
    ai_message_id = str(uuid.uuid4())

    ai_message = {
        "id": ai_message_id,
        "role": "ai",
        "question": payload.message,
        "context": serialized_context,
        "content": chat["answer"],
        "usage": {
            "prompt_tokens": cb.prompt_tokens,
            "completion_tokens": cb.completion_tokens,
            "total_tokens": cb.total_tokens,
            "total_cost_usd": float(chat_user.total_cost),
        }
    }
    history.chat_history.append(ai_message)

    feedback = Feedback(
        chat_id=chat_id,
        message_id=ai_message_id,
        feed_choice=FeedbackChoice.POSITIVE,
        source={
            "input": payload.message,
            "context": serialized_context,
            "answer": chat["answer"]
        }
    )
    db.add(feedback)
    await db.commit()
    await db.refresh(history)

    logger.info(f"Chat session updated successfully  chat_id={chat_id}, ai_message_id={ai_message_id}")

    return ChatResponse(
        chat_id=chat_id,
        message_id=ai_message_id,
        input=chat["input"],
        context=chat["context"],
        answer=chat["answer"],
        created_at=chat_user.created_at,
        last_message_at=chat_user.last_message_at,
        usage=Usage(
            prompt_tokens=cb.prompt_tokens,
            completion_tokens=cb.completion_tokens,
            total_tokens=cb.total_tokens,
            total_cost_usd=float(chat_user.total_cost),
        ),
    )





@router.get("/chat-ids", response_model=List[str])
async def get_chat_ids(
    user_id: int = Query(..., description="User ID to fetch chat IDs for"),
    db: AsyncSession = Depends(get_db),
):
    stmt = select(ChatUser.chat_id).where(ChatUser.user_id == user_id)
    result = await db.execute(stmt)
    chat_ids = result.scalars().all()

    if not chat_ids:
        raise HTTPException(status_code=404, detail="No chats found for this user")

    return chat_ids



@router.get(
    "/chat/history/{chat_id}",
    response_model=ChatHistoryResponse,
    status_code=status.HTTP_200_OK,
    summary="Fetch chat history for a given chat_id",
)
async def fetch_chat_history(
    chat_id: str,
    db: AsyncSession = Depends(get_db),
    request: Request = None,
) -> ChatHistoryResponse:
    """
    Fetch chat history with user_id, timestamps, token usage, cost fields,
    and feedback choice (positive/negative) if available.
    """
    logger.info(f"Endpoint: {request.url.path if request else '/chat/history'} | Request received  fetch_chat_history for chat_id={chat_id}")

    stmt = (
        select(
            ChatUserHistory,
            ChatUser.user_id,
            ChatUser.created_at,
            ChatUser.last_message_at,
        )
        .join(ChatUser, ChatUser.chat_id == ChatUserHistory.chat_id)
        .where(ChatUserHistory.chat_id == chat_id)
    )

    result = await db.execute(stmt)
    row = result.first()
    if row is None:
        logger.warning(f"No chat history found  chat_id={chat_id}")
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"No chat history found for chat_id '{chat_id}'",
        )

    (
        history_obj,
        user_id,
        created_at,
        last_message_at,
    ) = row

    logger.info(f"Chat history found  chat_id={chat_id}, user_id={user_id}, created_at={created_at}, last_message_at={last_message_at}")

    # ---- Fetch all feedbacks for this chat_id ----
    fb_stmt = select(Feedback).where(Feedback.chat_id == chat_id)
    fb_res = await db.execute(fb_stmt)
    feedbacks = fb_res.scalars().all()
    logger.info(f"Fetched {len(feedbacks)} feedback entries for chat_id={chat_id}")

    feedback_map = {fb.message_id: fb.feed_choice for fb in feedbacks}

    history_with_context = []
    for entry in history_obj.chat_history:
        if isinstance(entry, str):
            try:
                entry = json.loads(entry)
            except Exception as e:
                logger.error(f"Failed to parse history entry for chat_id={chat_id}: {str(e)}")
                entry = {}

        msg_id = entry.get("id")
        logger.debug(f"Processing message  chat_id={chat_id}, message_id={msg_id}, role={entry.get('role')}")

        history_with_context.append(
            {
                "message_id": msg_id,
                "role": entry.get("role"),
                "question": entry.get("question"),
                "content": entry.get("content"),
                "context": entry.get("context", []),
                "usage": entry.get("usage", {}),
                "feedback": feedback_map.get(msg_id),
            }
        )

    logger.info(f"Returning chat history  chat_id={chat_id}, total_messages={len(history_with_context)}")

    return ChatHistoryResponse(
        chat_id=history_obj.chat_id,
        user_id=user_id,
        history=history_with_context,
        created_at=created_at,
        last_message_at=last_message_at,
    )







@router.get(
    "/chat/first-question/{user_id}",
    response_model=List[FirstQuestionResponse],
    status_code=status.HTTP_200_OK,
    summary="Fetch first user-asked question for all chats of given user_id"
)
async def fetch_first_question(
    user_id: int,
    db: AsyncSession = Depends(get_db),
    request: Request = None,
):
    logger.info(f"Endpoint: {request.url.path if request else '/chat/first-question'} | Request received → fetch_first_question for user_id={user_id}")

    # Get all chat sessions for the user
    stmt = select(ChatUser).where(ChatUser.user_id == user_id)
    result = await db.execute(stmt)
    chat_users = result.scalars().all()
    logger.info(f"Fetched {len(chat_users)} chat sessions for user_id={user_id}")

    if not chat_users:
        logger.warning(f"No chat sessions found  user_id={user_id}")
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"No chat sessions found for user_id {user_id}"
        )

    responses = []

    # Loop through each chat session and get first question
    for chat_user in chat_users:
        hist_stmt = select(ChatUserHistory).where(ChatUserHistory.chat_id == chat_user.chat_id)
        hist_res = await db.execute(hist_stmt)
        history = hist_res.scalars().first()

        if history and history.chat_history:
            first_question = next(
                (msg.get("content") for msg in history.chat_history if msg.get("role") == "user"),
                None
            )

            if first_question:
                logger.info(f"Found first question for chat_id={chat_user.chat_id}: {first_question[:50]}...")
                responses.append(
                    FirstQuestionResponse(
                        chat_id=chat_user.chat_id,
                        first_question=first_question,
                        created_at=chat_user.created_at
                    )
                )
            else:
                logger.debug(f"No user question found in chat_id={chat_user.chat_id}")

    if not responses:
        logger.warning(f"No user questions found in any chat history  user_id={user_id}")
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"No user questions found in any chat history for user_id {user_id}"
        )

    responses.sort(key=lambda r: r.created_at, reverse=True)
    logger.info(f"Returning {len(responses)} first questions for user_id={user_id}")
    return responses





@router.get(
    "/chat/latest/{user_id}",
    response_model=LatestMessagesResponse,
    status_code=status.HTTP_200_OK,
    summary="Fetch up to the last 2 messages from the user's latest chat session (newest-first)",
)
async def get_latest_messages_from_latest_chat(
    user_id: int,
    max_messages: int = Query(2, ge=1, le=2, description="Max number of messages to return (1-2)"),
    db: AsyncSession = Depends(get_db),
    request: Request = None,
) -> LatestMessagesResponse:
    logger.info(f"Endpoint: {request.url.path if request else '/chat/latest'} | Request received → get_latest_messages_from_latest_chat for user_id={user_id}, max_messages={max_messages}")

    # Find the latest chat_id for the user 
    latest_chat_stmt = (
        select(
            ChatUser.chat_id,
            ChatUser.user_id,
            ChatUser.created_at,
            ChatUser.last_message_at,
        )
        .where(ChatUser.user_id == user_id)
        .order_by(func.coalesce(ChatUser.last_message_at, ChatUser.created_at).desc())
        .limit(1)
    )
    chat_row = (await db.execute(latest_chat_stmt)).first()
    if chat_row is None:
        logger.warning(f"Endpoint: {request.url.path if request else '/chat/latest'} | No chats found for user_id={user_id}")
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"No chats found for user_id '{user_id}'",
        )

    chat_id, _uid, created_at, last_message_at = chat_row
    logger.info(f"Endpoint: {request.url.path if request else '/chat/latest'} | Latest chat found  chat_id={chat_id}, created_at={created_at}, last_message_at={last_message_at}")

    # Fetch the chat history JSON for that chat_id 
    history_stmt = select(ChatUserHistory.chat_history).where(ChatUserHistory.chat_id == chat_id)
    history_json = (await db.execute(history_stmt)).scalar_one_or_none()
    if history_json is None:
        logger.warning(f"Endpoint: {request.url.path if request else '/chat/latest'} | No history found for chat_id={chat_id}, returning empty messages")
        return LatestMessagesResponse(
            user_id=user_id,
            chat_id=chat_id,
            created_at=created_at,
            last_message_at=last_message_at,
            messages=[],
        )

    # Ensure list type
    entries = list(history_json) if isinstance(history_json, list) else []
    logger.info(f"Endpoint: {request.url.path if request else '/chat/latest'} | Total history entries found={len(entries)} for chat_id={chat_id}")

    # entries are assumed oldest->newest; slice last N and reverse to newest-first
    n = max(1, min(max_messages, 2))
    newest_slice = list(reversed(entries[-n:]))
    logger.info(f"Endpoint: {request.url.path if request else '/chat/latest'} | Returning last {len(newest_slice)} messages (newest-first) for chat_id={chat_id}")

    messages = [
        {
            "role": (e.get("role") if isinstance(e, dict) else None),
            "content": (e.get("content") if isinstance(e, dict) else None),
            "context": (e.get("context") if isinstance(e, dict) and isinstance(e.get("context"), list) else []),
        }
        for e in newest_slice
    ]

    return LatestMessagesResponse(
        user_id=user_id,
        chat_id=chat_id,
        created_at=created_at,
        last_message_at=last_message_at,
        messages=messages,
    )





#  faq


@router.get("/faq/weekly")
async def get_weekly_faqs(
    db: AsyncSession = Depends(get_db),
    request: Request = None,
):
    logger.info(f"Endpoint: {request.url.path if request else '/faq/weekly'} | Request received  get_weekly_faqs")

    # Fetch histories with their user info (removed weekly/today filter)
    result = await db.execute(
        select(ChatUserHistory, ChatUser)
        .join(ChatUser, ChatUser.chat_id == ChatUserHistory.chat_id)
    )
    histories = result.all()
    logger.info(f"Endpoint: {request.url.path if request else '/faq/weekly'} | Histories fetched  count={len(histories)}")

    # store (question, answer, chat_id, timestamp)
    question_data = []  
    for history, user in histories:
        if not history.chat_history:
            logger.debug(f"Endpoint: {request.url.path if request else '/faq/weekly'} | Skipping chat_id={user.chat_id}  empty history")
            continue
        messages = history.chat_history
        for i, msg in enumerate(messages):
            if msg.get("role") == "user":
                question = msg.get("content")
                answer = ""
                # get the next message from AI
                if i + 1 < len(messages) and messages[i + 1].get("role") == "ai":
                    answer = messages[i + 1].get("content")
                if question:
                    question_data.append({
                        "question": question.strip(),
                        "answer": answer.strip(),
                        "chat_id": user.chat_id,
                        "timestamp": user.last_message_at
                    })
    logger.info(f"Endpoint: {request.url.path if request else '/faq/weekly'} | Total Q&A pairs collected={len(question_data)}")

    # Count duplicates
    question_counter = Counter([q["question"] for q in question_data])
    duplicate_questions = {q for q, c in question_counter.items() if c > 1}
    logger.info(f"Endpoint: {request.url.path if request else '/faq/weekly'} | Duplicate questions found={len(duplicate_questions)}")

    # Build FAQs
    faqs = []
    seen_questions = set()

    if duplicate_questions:
        for q in question_data:
            if q["question"] in duplicate_questions and q["question"] not in seen_questions:
                faqs.append(q)
                seen_questions.add(q["question"])
        logger.info(f"Endpoint: {request.url.path if request else '/faq/weekly'} | Returning FAQs (frequent) count={len(faqs)}")

    # Fallback or fill up to 5
    if len(faqs) < 5:
        sorted_data = sorted(question_data, key=lambda x: x["timestamp"], reverse=True)
        for q in sorted_data:
            if q["question"] not in seen_questions:
                faqs.append(q)
                seen_questions.add(q["question"])
            if len(faqs) >= 5:
                break
        logger.info(f"Endpoint: {request.url.path if request else '/faq/weekly'} | Filled up FAQs to 5, final count={len(faqs)}")

    return {
        "questionsCount": len(faqs),
        "faqs": faqs
    }
