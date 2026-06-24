from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from app.db.database import get_db
from app.db import models
from app.schemas import schema
from app.utils.logging_config import get_logger

router = APIRouter(prefix="/llm-config", tags=["llm-config"])
logger = get_logger("llm_config")
@router.post("/create", response_model=schema.LLMConfigResponse)
async def create_llm_config(config: schema.LLMConfigCreate, db: AsyncSession = Depends(get_db)):
    logger.info(f"Endpoint: /create | Request received  config={config.dict()}")
    new_config = models.LLMConfig(**config.dict())
    db.add(new_config)
    await db.commit()
    await db.refresh(new_config)
    logger.info(f"Endpoint: /create | New LLM config created  id={new_config.id}")
    return new_config

@router.get("/latest", response_model=schema.LLMConfigResponse)
async def get_latest_llm_config(db: AsyncSession = Depends(get_db)):
    logger.info("Endpoint: /latest | Request received")
    result = await db.execute(select(models.LLMConfig).order_by(models.LLMConfig.id.desc()))
    config = result.scalars().first()
    if not config:
        logger.warning("Endpoint: /latest | No LLM config found")
        raise HTTPException(status_code=404, detail="No LLM config found")
    logger.info(f"Endpoint: /latest | Returning latest LLM config  id={config.id}")
    return config



