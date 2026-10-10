"""Bounded RAGFlow candidate discovery; remote text never becomes a Hex card.

Only the documented retrieval endpoint is used. Provisioning, indexing and model
downloads belong to the independently operated service, not this adapter.
"""
from __future__ import annotations

import json
import math
import os
import time
from typing import Literal
from urllib.parse import urlsplit

import httpx
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from ..contracts import Card
from ..io import digest


class CardMapping(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    dataset_id: str = Field(min_length=1, max_length=200)
    document_id: str = Field(min_length=1, max_length=200)
    card_id: str = Field(pattern=r"^[a-z][a-z0-9_.-]{2,100}$")
    card_version: int = Field(ge=1, strict=True)
    card_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")


class RagflowConfig(BaseModel):
    """Safe to freeze in a packet: a key *name* is stored, never the key."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    enabled: bool = False
    base_url: str = "http://127.0.0.1:9380"
    api_key_env: str = Field(default="HEX_RAGFLOW_API_KEY", pattern=r"^[A-Z][A-Z0-9_]{2,100}$")
    api_contract: Literal["ragflow-0.27.2-retrieval-v1"] = "ragflow-0.27.2-retrieval-v1"
    dataset_ids: list[str] = Field(default_factory=list, max_length=8)
    mapping_manifest: list[CardMapping] = Field(default_factory=list, max_length=2000)
    timeout_seconds: float = Field(default=8.0, gt=0, le=30)
    max_queries: int = Field(default=3, ge=1, le=3)
    max_hits: int = Field(default=6, ge=1, le=6)
    candidate_limit: int = Field(default=12, ge=1, le=24)
    snippet_chars: int = Field(default=1200, ge=1, le=4000)
    similarity_threshold: float = Field(default=0.2, ge=0, le=1)
    rerank_id: str | None = Field(default=None, max_length=200)

    @field_validator("base_url")
    @classmethod
    def safe_service_origin(cls, value: str) -> str:
        parsed = urlsplit(value)
        if (parsed.scheme not in {"http", "https"} or not parsed.hostname
                or parsed.username or parsed.password or parsed.query or parsed.fragment
                or parsed.path not in {"", "/"}):
            raise ValueError("RAGFlow base_url 必须是无凭据、路径或查询参数的 HTTP(S) 服务地址")
        if parsed.scheme == "http" and parsed.hostname not in {"localhost", "127.0.0.1", "::1"}:
            raise ValueError("远程 RAGFlow 必须使用 HTTPS；HTTP 仅用于本机回环")
        return value.rstrip("/")

    @field_validator("dataset_ids")
    @classmethod
    def dataset_identifiers(cls, value: list[str]) -> list[str]:
        if any(not item.strip() or len(item) > 200 for item in value) or len(set(value)) != len(value):
            raise ValueError("dataset_ids 必须是不重复、非空的服务数据集 ID")
        return value

    @model_validator(mode="after")
    def configured_mapping(self):
        if self.enabled and not self.dataset_ids:
            raise ValueError("启用 RAGFlow 前须指定 dataset_ids")
        keys = [(m.dataset_id, m.document_id) for m in self.mapping_manifest]
        if len(set(keys)) != len(keys):
            raise ValueError("同一数据集/文档只能映射一个本地 Card 版本")
        if any(m.dataset_id not in self.dataset_ids for m in self.mapping_manifest):
            raise ValueError("mapping_manifest 数据集必须属于 dataset_ids")
        return self


def card_fingerprint(card: Card) -> str:
    """Use Hex's existing canonical digest for the indexing manifest."""
    return digest(card.model_dump())


def build_backend(config: dict | RagflowConfig | None) -> RagflowRetriever | None:
    if config is None:
        return None
    parsed = config if isinstance(config, RagflowConfig) else RagflowConfig.model_validate(config)
    return RagflowRetriever(parsed) if parsed.enabled else None


class RagflowRetriever:
    def __init__(self, config: RagflowConfig | dict, *, client: httpx.Client | None = None):
        self.config = config if isinstance(config, RagflowConfig) else RagflowConfig.model_validate(config)
        self.client = client
        self.query_count = 0

    def retrieve(self, query: str, limit: int = 8) -> dict:
        config = self.config
        call = {"provider": "ragflow", "api_contract": config.api_contract,
                "request_digest": digest({"query": query, "config": config.model_dump()}),
                "mapping_manifest_digest": digest([m.model_dump() for m in config.mapping_manifest]),
                "network_call_count": 0, "elapsed_ms": None, "status": "not_run",
                "cost": {"amount": None, "currency": None, "status": "not_reported"},
                "model_usage": None}
        result = {"candidates": [], "source_evidence": [], "excluded": [], "call": call}
        if not config.enabled:
            call.update(reason="disabled")
            return result
        if self.query_count >= config.max_queries:
            call.update(reason="query_limit")
            return result
        key = os.environ.get(config.api_key_env)
        if not key:
            call.update(reason="missing_api_key")
            return result
        if not query.strip():
            call.update(reason="empty_query")
            return result
        self.query_count += 1
        # Candidate breadth is explicit and independent of the final adoption cap.
        page_size = config.candidate_limit
        body = {"question": query, "dataset_ids": config.dataset_ids,
                "page": 1, "page_size": page_size, "similarity_threshold": config.similarity_threshold,
                "top_k": page_size, "knn_top_k": page_size,
                "knn_num_candidates": page_size, "rerank_candidates_count": page_size,
                "keyword": False, "highlight": False, "use_kg": False,
                "include_knowledge_compilation": False, "cross_languages": []}
        if config.rerank_id:
            body["rerank_id"] = config.rerank_id
        started = time.monotonic()
        call.update(network_call_count=1, status="failed")
        owned = self.client is None
        client = self.client or httpx.Client(timeout=config.timeout_seconds, trust_env=False,
                                            follow_redirects=False)
        try:
            with client.stream("POST", config.base_url + "/api/v1/retrieval",
                               headers={"Authorization": "Bearer " + key}, json=body,
                               timeout=config.timeout_seconds, follow_redirects=False) as response:
                call["http_status"] = response.status_code
                response.raise_for_status()
                pieces, count = [], 0
                for piece in response.iter_bytes():
                    count += len(piece)
                    if count > 2_000_000:
                        raise ValueError("response_too_large")
                    pieces.append(piece)
                payload = json.loads(b"".join(pieces))
            if not isinstance(payload, dict) or type(payload.get("code")) is not int:
                raise ValueError("invalid_envelope")
            call["business_code"] = payload["code"]
            if payload["code"] != 0:
                call.update(reason="service_business_error")
                return result
            data = payload.get("data")
            chunks = data.get("chunks") if isinstance(data, dict) else None
            if not isinstance(chunks, list):
                raise ValueError("invalid_chunks")
            mappings = {(m.dataset_id, m.document_id): m for m in config.mapping_manifest}
            for chunk in chunks[:page_size]:
                if not isinstance(chunk, dict):
                    result["excluded"].append({"id": "", "code": "invalid_chunk", "reason": "片段格式不正确"})
                    continue
                ident, dataset, document = chunk.get("id"), chunk.get("dataset_id"), chunk.get("document_id")
                content, score = chunk.get("content"), chunk.get("similarity")
                if (not all(isinstance(v, str) and v for v in (ident, dataset, document))
                        or dataset not in config.dataset_ids or not isinstance(content, str)
                        or type(score) not in (float, int) or not math.isfinite(score)):
                    result["excluded"].append({"id": ident if isinstance(ident, str) else "",
                                               "code": "invalid_chunk", "reason": "片段来源、分数或内容不正确"})
                    continue
                evidence = {"provider": "ragflow", "chunk_id": ident, "dataset_id": dataset,
                            "document_id": document, "text": content[:config.snippet_chars],
                            "text_truncated": len(content) > config.snippet_chars,
                            "content_sha256": digest(content), "similarity": score,
                            "verification_status": "unverified", "trust": "untrusted_source_data",
                            "instruction_use": "forbidden", "generation_input_authorized": False}
                result["source_evidence"].append(evidence)
                mapping = mappings.get((dataset, document))
                if not mapping:
                    result["excluded"].append({"id": ident, "code": "unmapped_source",
                                               "reason": "原始外部片段没有冻结的本地知识映射"})
                    continue
                result["candidates"].append({**mapping.model_dump(), "score": score,
                                              "source": {k: evidence[k] for k in (
                                                  "provider", "chunk_id", "dataset_id", "document_id",
                                                  "content_sha256", "verification_status")}})
            call.update(status="succeeded")
        except httpx.TimeoutException:
            call.update(reason="timeout")
        except httpx.HTTPStatusError:
            call.update(reason="http_error")
        except httpx.RequestError:
            call.update(reason="network_error")
        except (ValueError, TypeError):
            call.update(reason="invalid_response")
        finally:
            call["elapsed_ms"] = round((time.monotonic() - started) * 1000, 3)
            if owned:
                client.close()
        return result
