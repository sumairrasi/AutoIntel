from pydantic import BaseModel, Field
from langchain_openai import ChatOpenAI



class LLMParams(BaseModel):
    temperature: float = Field(..., ge=0, le=2)
    frequency_penalty: float = Field(..., ge=-2, le=2)
    presence_penalty: float = Field(..., ge=-2, le=2)





class LLmConfigutils:
    def __init__(self):
        self.param_selector_llm = ChatOpenAI(
            model="gpt-4o-mini", temperature=0.7
        ).with_structured_output(LLMParams)
        
        
    def choose_llm_params(self,extra_prompt: str) -> LLMParams:
        instruction = f"""
            You are selecting LLM generation parameters from the sets:
            - temperature ∈ {{0.0, 0.2, 0.3, 0.5, 0.7, 0.9}}
            - frequency_penalty ∈ {{0.0, 0.2, 0.3, 0.5}}
            - presence_penalty ∈ {{0.0, 0.2, 0.3, 0.5}}

            Selection rules:
            - If the instruction emphasizes creativity, rephrasing, brainstorming, marketing copy, or diverse variations → temperature=0.7–0.9; presence_penalty=0.2–0.5; frequency_penalty=0.2–0.5.
            - If the instruction emphasizes precision, correctness, citations, or reproducibility → temperature=0.0–0.2; penalties=0.0–0.2.
            - If the instruction asks to avoid repetition or produce many distinct ideas → increase frequency_penalty to 0.3–0.5.
            - Only return temperature=0.0 if the instruction explicitly requests “deterministic”, “exact reproduction”, “no randomness”, or equivalent.
            - Otherwise, when uncertain, prefer temperature=0.2 over 0.0.

            Extra instruction: "{extra_prompt}"
            Return the chosen values.
            """
        return self.param_selector_llm.invoke(instruction)
    

    def choose_llm_params(self,extra_prompt: str) -> LLMParams:
        return self.param_selector_llm.invoke(extra_prompt)


    def ask_llm(self,extra_prompt: str):
        params = self.choose_llm_params(extra_prompt)
        return params  
        

