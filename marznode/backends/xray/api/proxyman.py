"""Methods to update Xray-core users/inbounds"""

import grpclib
from grpclib.client import UnaryUnaryMethod
from grpclib.const import Status

from . import inbound_users_pb2
from .base import XrayAPIBase
from .exceptions import RelatedError, UnimplementedError
from .proto.app.proxyman.command import command_pb2, command_grpc
from .proto.common.protocol import user_pb2
from .types.account import Account
from .types.message import Message, TypedMessage


# pylint: disable=E1101

# try:
#    from .proto.core import config_pb2 as core_config_pb2
# except ModuleNotFoundError:
#    from .proto import config_pb2 as core_config_pb2


class Proxyman(XrayAPIBase):
    """Implements methods to update Xray-core users/inbounds"""

    async def __alter_inbound(self, tag: str, operation: TypedMessage) -> None:
        stub = command_grpc.HandlerServiceStub(self._channel)
        try:
            await stub.AlterInbound(
                command_pb2.AlterInboundRequest(tag=tag, operation=operation)
            )
        except grpclib.exceptions.GRPCError as error:
            raise RelatedError(error) from error

    async def add_inbound_user(self, tag: str, user: Account) -> None:
        """Adds a user to an inbound"""
        await self.__alter_inbound(
            tag=tag,
            operation=Message(
                command_pb2.AddUserOperation(
                    user=user_pb2.User(
                        level=user.level, email=user.email, account=user.message
                    )
                )
            ),
        )

    async def remove_inbound_user(self, tag: str, email: str) -> None:
        """Removes a user from an inbound"""
        await self.__alter_inbound(
            tag=tag, operation=Message(command_pb2.RemoveUserOperation(email=email))
        )

    async def get_inbound_users(self, tag: str, timeout: float = 10.0) -> list[str]:
        """Emails of the users an inbound holds right now.

        Xray cores without GetInboundUsers (older than 25.x) answer
        UNIMPLEMENTED, raised as UnimplementedError.
        """
        method = UnaryUnaryMethod(
            self._channel,
            inbound_users_pb2.GET_INBOUND_USERS,
            inbound_users_pb2.GetInboundUserRequest,
            inbound_users_pb2.GetInboundUserResponse,
        )
        try:
            response = await method(
                inbound_users_pb2.GetInboundUserRequest(tag=tag), timeout=timeout
            )
        except grpclib.exceptions.GRPCError as error:
            if error.status == Status.UNIMPLEMENTED:
                raise UnimplementedError(error.message) from error
            raise RelatedError(error) from error
        return [user.email for user in response.users]

    # TODO: implement add/remove inbound/outbound if necessary
