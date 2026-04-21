from .inbound import Inbound
from .user import User

__all__ = ["Inbound", "User"]

User.model_rebuild()
