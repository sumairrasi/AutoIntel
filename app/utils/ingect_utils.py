from pdf2image import convert_from_bytes
from io import BytesIO
import base64
from langchain.schema import SystemMessage, HumanMessage,Document
from langchain_openai import ChatOpenAI
from langchain_text_splitters import RecursiveCharacterTextSplitter
from langchain_community.document_loaders import UnstructuredPDFLoader
from typing import List
from app.config.constant import OPENAI_API_KEY
import os
from pypdf import PdfReader

class IngectDataClass:
    def __init__(self):
        self.llm = ChatOpenAI(model="gpt-4o",temperature=0,api_key=OPENAI_API_KEY)

    def split_into_chunks(self,docs):
        text_splitter = RecursiveCharacterTextSplitter()
        splits = text_splitter.split_documents(docs)
        return splits

    def store_vdb(self,vector_store,data):
        vector_store.add_documents(data)
        
    def image_to_text(self,file_path, doc_id: int, file_id: str):
        docs = []
        # 1. Read PDF into memory (if you have it as bytes already)
        with open(file_path, "rb") as f:
            pdf_bytes = f.read()
        images = convert_from_bytes(pdf_bytes, dpi=300)
        total_pages = len(images)
        for page_num, img in enumerate(images, start=1):
        # 4a) Encode the page as a JPEG & base64-URI
            buf = BytesIO()
            img.save(buf, format="JPEG")
            img_b64 = base64.b64encode(buf.getvalue()).decode("utf-8")
            data_uri = f"data:image/jpeg;base64,{img_b64}"

            # 4b) Build the message with the CORRECT image_url object
            messages = [
                SystemMessage(content="You are a helpful assistant that extracts all text from images."),
                HumanMessage(
                    content=[
                        {"type": "text",      "text": f"Please extract all textual content from page {page_num} of the document."},
                        {"type": "image_url", "image_url": {"url": data_uri, "detail": "auto"}}
                    ]
                ),
            ]

            # 4c) Send to GPT-4o Vision and print result
            response = self.llm(messages)
            ocr_text = response.content.strip()
            doc = Document(
                page_content=ocr_text,
                metadata={
                    "source": file_path,
                    "page": page_num,
                    "total_pages": total_pages,
                    "doc_id": doc_id,     # added
                    "file_id": file_id 
                    
                }
                
                
            )
            print("enriched metadata of imagepdf",doc)
            
            docs.append(doc)

            return docs
        
        

class ExtractTextClass(IngectDataClass):
    def __init__(self):
        pass
    def pdf_to_langchain_docs(self, pdf_path: str, doc_id: int, file_id: str) -> List[Document]:
        """
        Loads the PDF via UnstructuredPDFLoader and returns a list of
        LangChain Document objects, one per page, with enriched metadata.
        """
        # 1. Load pages
        loader = UnstructuredPDFLoader(pdf_path, mode="paged")
        raw_docs: List[Document] = loader.load()

        # 2. Determine total pages
        total_pages = total_pages = len(PdfReader(pdf_path).pages) 
        print("total_pages:",total_pages)
        # 3. Enrich metadata & return new Document list
        docs: List[Document] = []
        for page_doc in raw_docs:
            # page_doc.metadata already contains at least {"page": <n>}
            enriched_meta = {
                **page_doc.metadata,
                "source": os.path.basename(pdf_path),
                "total_pages": total_pages,
                "doc_id": doc_id,       # added
                "file_id": file_id 
            }
            print("enriched metadata",enriched_meta)
            docs.append(
                Document(
                    page_content=page_doc.page_content,
                    metadata=enriched_meta
                )
            )

        return docs
    




