"""Messages of Xray's HandlerService.GetInboundUsers.

The vendored protos under ``proto/`` predate this RPC (it appeared in
Xray 25.x), and regenerating the whole tree for two messages would drag
in every other upstream change. Upstream defines them as::

    message GetInboundUserRequest  { string tag = 1; string email = 2; }
    message GetInboundUserResponse { repeated xray.common.protocol.User users = 1; }

Only the field numbers and the RPC path matter on the wire, so they are
declared in a package of their own: a future full regeneration brings
``xray.app.proxyman.command.GetInboundUserRequest`` without a clash in
the descriptor pool. The file is registered the way the generated
``*_pb2`` modules next to it do it, which keeps it working on the same
protobuf runtimes they work on.
"""

from google.protobuf import descriptor_pb2 as _descriptor_pb2
from google.protobuf import descriptor_pool as _descriptor_pool
from google.protobuf.internal import builder as _builder

# Registers xray.common.protocol.User, which the response refers to.
from .proto.common.protocol import user_pb2 as _user_pb2  # noqa: F401

GET_INBOUND_USERS = "/xray.app.proxyman.command.HandlerService/GetInboundUsers"


def _serialized_file() -> bytes:
    field = _descriptor_pb2.FieldDescriptorProto
    file = _descriptor_pb2.FileDescriptorProto(
        name="marznode/xray_inbound_users.proto",
        package="marznode.xray_inbound_users",
        syntax="proto3",
        dependency=["common/protocol/user.proto"],
    )
    request = file.message_type.add(name="GetInboundUserRequest")
    request.field.add(
        name="tag", number=1, type=field.TYPE_STRING, label=field.LABEL_OPTIONAL
    )
    request.field.add(
        name="email", number=2, type=field.TYPE_STRING, label=field.LABEL_OPTIONAL
    )
    response = file.message_type.add(name="GetInboundUserResponse")
    response.field.add(
        name="users",
        number=1,
        type=field.TYPE_MESSAGE,
        label=field.LABEL_REPEATED,
        type_name=".xray.common.protocol.User",
    )
    return file.SerializeToString()


DESCRIPTOR = _descriptor_pool.Default().AddSerializedFile(_serialized_file())

_builder.BuildMessageAndEnumDescriptors(DESCRIPTOR, globals())
_builder.BuildTopDescriptorsAndMessages(DESCRIPTOR, __name__, globals())

GetInboundUserRequest = globals()["GetInboundUserRequest"]
GetInboundUserResponse = globals()["GetInboundUserResponse"]
