from uuid import UUID
from typing import Optional

from pydantic import BaseModel, ConfigDict

from ada_backend.database import models as db


class IngestionTask(BaseModel):
    source_name: str
    source_type: db.SourceType
    status: db.TaskStatus


class SourceAttributes(BaseModel):
    access_token: Optional[str] = None
    path: Optional[str] = None
    folder_id: Optional[str] = None
    source_db_url: Optional[str] = None
    source_table_name: Optional[str] = None
    id_column_name: Optional[str] = None
    text_column_names: Optional[list[str]] = None
    source_schema_name: Optional[str] = None
    metadata_column_names: Optional[list[str]] = None
    timestamp_column_name: Optional[str] = None
    is_sync_enabled: Optional[bool] = False


class IngestionTaskUpdate(IngestionTask):
    id: UUID
    source_id: Optional[UUID] = None

    model_config = ConfigDict(
        from_attributes=True,
        json_encoders={UUID: str},
    )


class IngestionTaskResponse(IngestionTaskUpdate):
    created_at: Optional[str] = None
    updated_at: Optional[str] = None


class IngestionTaskQueue(IngestionTask):
    source_attributes: SourceAttributes
