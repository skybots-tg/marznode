from typing import TYPE_CHECKING, Optional

from pydantic import BaseModel, Field

if TYPE_CHECKING:
    from marznode.models import Inbound


class User(BaseModel):
    id: int
    username: str
    key: str
    inbounds: list["Inbound"] = []
    
    # Device limit enforcement fields
    device_limit: Optional[int] = None  # None = no limit
    allowed_fingerprints: list[str] = Field(default_factory=list)
    enforce_device_limit: bool = False  # Whether to enforce at proxy level