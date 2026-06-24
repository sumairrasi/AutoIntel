from pydantic import BaseModel
from typing import Any
from enum import Enum
from datetime import datetime
from typing import Optional, List, Dict
from pydantic import BaseModel, Field
from decimal import Decimal

from app.utils.util_file import to_ist





class Status(str, Enum):
    uploaded = "uploaded"
    verified = "verified"
    injected = "injected"



class DocumentCreate(BaseModel):
    filename: str
    metadata: dict[str, Any]
    version: int
    doc_type: str    
    status: Status
    
    class Config:
        orm_mode = True



class DocumentRead(DocumentCreate):
    id: int
    uploaded_time: datetime

    class Config:
        orm_mode = True
        json_encoders = {
            datetime: lambda v: to_ist(v).isoformat() if v else None
        }



class DocumentModifyResponse(BaseModel):
    status: str
    message: str
    db_id: int
    version: int
    filename: str
    

class DocumentOut(BaseModel):
    id: int
    filename: str
    file_id: Optional[str]

    class Config:
        from_attributes = True
    
    
class ChatPayload(BaseModel):
    user_id: int = Field(..., description="ID of the authenticated user")
    chat_id: Optional[str] = Field(None, description="ID of an existing chat session")
    message: str = Field(..., min_length=1, description="User's message content")

class Usage(BaseModel):
    prompt_tokens: int
    completion_tokens: int
    total_tokens: int
    # prompt_cost_usd: float
    # completion_cost_usd: float
    total_cost_usd: float

class ChatResponse(BaseModel):
    chat_id: str
    message_id: str 
    input: str
    context: List[Any]        
    answer: str
    created_at: Optional[datetime] = None 
    last_message_at: Optional[datetime] = None 
    usage: Usage


    class Config:
        json_encoders = {
            datetime: lambda v: to_ist(v).isoformat() if v else None
        }



class HistoryEntry(BaseModel):
    message_id: Optional[str] = None
    role: Optional[str] = None
    feedback: Optional[str] = None
    content: Optional[str] = None
    question: Optional[str] = None
    context: List[Any] = Field(default_factory=list)
    usage: Optional[Dict[str, Any]] = None 

class ChatHistoryResponse(BaseModel):
    chat_id: str
    user_id: int
    history: List[HistoryEntry]
    created_at: datetime
    last_message_at: Optional[datetime] = None

    # --- If using Pydantic v2 ---
    # model_config = ConfigDict(json_encoders={Decimal: lambda v: float(v)})

    # --- If using Pydantic v1 ---
    class Config:
        json_encoders = {Decimal: lambda v: float(v),datetime: lambda v: to_ist(v).isoformat() if v else None}



class FirstQuestionResponse(BaseModel):
    chat_id: str
    first_question: str
    created_at: Optional[datetime] = None
    


    class Config:
        orm_mode = True
        json_encoders = {
            datetime: lambda v: to_ist(v).isoformat() if v else None
        }



class Message(BaseModel):
    role: Optional[str] = None
    content: Optional[str] = None
    context: List[Any] = Field(default_factory=list)

class LatestMessagesResponse(BaseModel):
    user_id: int
    chat_id: str
    created_at: datetime
    last_message_at: Optional[datetime] = None
    # Newest-first; at most 2
    messages: List[Message]

    class Config:
        json_encoders = {
            datetime: lambda v: to_ist(v).isoformat() if v else None
        }


class FAQItem(BaseModel):
    chat_id: str
    question: str
    answer: str
    timestamp: datetime

class FAQListResponse(BaseModel):
    questionsCount: int
    faqs: List[FAQItem]



    class Config:
        orm_mode = True
        
class FeedbackChoice(str, Enum):
    POSITIVE = "positive"
    NEGATIVE = "negative"

# Request schema
class FeedbackCreate(BaseModel):
    user_id: int    # This should match ChatUser.user_id
    chat_id: str
    date: datetime
    description: Optional[str] = None
    feed_choice: FeedbackChoice
    source: Optional[dict] = None 
    class Config:
        json_encoders = {
            datetime: lambda v: to_ist(v).isoformat() if v else None
        }
# Response schema
class FeedbackResponse(BaseModel):
    id: int
    chat_id: str
    description: Optional[str] = None
    feed_choice: FeedbackChoice
    date: datetime

    class Config:
        orm_mode = True
        json_encoders = {
            datetime: lambda v: to_ist(v).isoformat() if v else None
        }
        
class LLMConfigCreate(BaseModel):
    temperature: Optional[float] = 0.0
    frequency_penalty: Optional[float] = 0.0
    presence_penalty: Optional[float] = 0.0

class LLMConfigResponse(LLMConfigCreate):
    id: int
    class Config:
        orm_mode = True
        
        

class FeedbackChoice(str, Enum):
    POSITIVE = "positive"
    NEGATIVE = "negative"

class FeedbackCreate(BaseModel):
    user_id: int
    chat_id: str
    message_id: str
    feed_choice: FeedbackChoice
    description: Optional[str] = None
    source: Optional[Dict] = None

class FeedbackResponse(BaseModel):
    id: int
    chat_id: str
    message_id: str
    feed_choice: FeedbackChoice
    description: Optional[str]
    source: Optional[Dict]
    date: datetime
    
    
class LLMConfigTuneResponse(BaseModel):
    message: str
    params: Dict[str, float]