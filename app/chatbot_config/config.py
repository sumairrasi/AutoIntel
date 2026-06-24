from langchain_openai import ChatOpenAI
from langchain_core.prompts import ChatPromptTemplate, PromptTemplate
from langchain.chains import create_retrieval_chain
from langchain.chains.combine_documents import create_stuff_documents_chain
from langchain_core.documents import Document
from langchain_core.runnables import chain, RunnableLambda
from operator import itemgetter
from typing import List, Literal
from pydantic import BaseModel, Field

from app.config.vectordb_config import vector_store
from app.utils.util_file import load_prompts

# ------------------ Load prompts once ------------------ #
prompts = load_prompts()

# ------------------ Guardrails Schema ------------------ #
class GuardrailsOutput(BaseModel):
    decision: Literal["rag", "general"] = Field(
        description=prompts["guardrails_schema"]["decision"]["description"]
    )

# ------------------ Chat Router ------------------ #
class ChatRouter:
    def __init__(self, temperature: float, presence_penalty: float, frequency_penalty: float):
        self.llm = ChatOpenAI(
            model="gpt-4o",
            temperature=temperature,
            presence_penalty=presence_penalty,
            frequency_penalty=frequency_penalty
        )

        # ------------------ Guardrails Classifier ------------------ #
        guardrails_prompt = ChatPromptTemplate.from_messages([
            ("system", prompts["guardrails_system"]),
            ("human", "{input}"),
        ])
        guardrails_chain = guardrails_prompt | self.llm.with_structured_output(GuardrailsOutput)
        self.guard_classifier = guardrails_chain | RunnableLambda(lambda out: out.decision)

        # ------------------ RAG Retriever Chain ------------------ #
        qa_prompt = ChatPromptTemplate.from_messages([
            ("system", prompts["system_prompt"]),
            ("human", "{input}"),
        ])
        question_answer_chain = create_stuff_documents_chain(self.llm, qa_prompt)
        self.rag_chain = create_retrieval_chain(self.retriever, question_answer_chain)

        # ------------------ General Chain ------------------ #
        general_prompt = PromptTemplate.from_template(prompts["general_prompt"])
        self.general_chain = general_prompt | self.llm

        # ------------------ Router ------------------ #
        self.full_chain = {
            "topic": self.guard_classifier,
            "input": itemgetter("input"),
        } | RunnableLambda(self.route)

    @chain
    def retriever(inputs) -> List[Document]:
        # Handle both dict and string inputs
        if isinstance(inputs, dict):
            query = inputs.get("input", "")
        else:
            query = inputs

        docs_scores = vector_store.similarity_search_with_score(query)
        docs = []
        for doc, score in docs_scores:
            doc.metadata["score"] = float(score)
            docs.append(doc)
        return docs

    def route(self, payload: dict, *, config=None, **_):
        """
        Terminal router.
        payload: {"topic": "general"|"rag", "input": "..."}
        """
        topic = payload["topic"]
        user_input = payload["input"]

        if topic == "general":
            print("general--------------------------")
            msg = self.general_chain.invoke({"input": user_input})
            answer_text = getattr(msg, "content", str(msg))
            return {
                "topic": "general",
                "input": user_input,
                "context": [],
                "chat_history": [],
                "answer": answer_text,
            }

        # rag branch
        return self.rag_chain.invoke({"input": user_input})

    def invoke(self, message: str):
        return self.full_chain.invoke({"input": message})
