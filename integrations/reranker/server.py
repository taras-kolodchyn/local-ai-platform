"""Local Cohere-compatible ranking endpoint for the LiteLLM gateway."""
from threading import Lock
from typing import Annotated
from uuid import uuid4

import numpy as np
import onnxruntime as ort
from fastapi import FastAPI
from pydantic import BaseModel, Field
from tokenizers import Tokenizer

app = FastAPI(docs_url=None, redoc_url=None)
tokenizer = Tokenizer.from_file('/model/tokenizer.json')
tokenizer.enable_truncation(max_length=512)
tokenizer.enable_padding(pad_id=0, pad_token='[PAD]')
options = ort.SessionOptions()
options.intra_op_num_threads = 2
options.inter_op_num_threads = 1
model = ort.InferenceSession('/model/model_qint8_arm64.onnx', options, providers=['CPUExecutionProvider'])
inference = Lock()


class Request(BaseModel):
    query: str = Field(min_length=1, max_length=4000)
    documents: list[Annotated[str, Field(max_length=4096)]] = Field(min_length=1, max_length=480)
    top_n: int | None = Field(default=None, ge=1)
    return_documents: bool = False


@app.get('/health')
def health():
    return {'status': 'ok'}


@app.post('/v1/rerank')
@app.post('/v2/rerank')
def rerank(request: Request):
    scores = []
    with inference:
        for start in range(0, len(request.documents), 3):
            batch = tokenizer.encode_batch([(request.query, text) for text in request.documents[start:start + 3]])
            inputs = {name: np.asarray([getattr(item, attr) for item in batch], dtype=np.int64)
                      for name, attr in [('input_ids', 'ids'), ('attention_mask', 'attention_mask'),
                                         ('token_type_ids', 'type_ids')]}
            scores.extend(model.run(None, inputs)[0].reshape(-1).tolist())
    order = sorted(range(len(scores)), key=scores.__getitem__, reverse=True)
    return {'id': str(uuid4()), 'results': [
        {'index': i, 'relevance_score': float(1 / (1 + np.exp(-scores[i]))),
         **({'document': {'text': request.documents[i]}} if request.return_documents else {})}
        for i in order[:request.top_n]]}
