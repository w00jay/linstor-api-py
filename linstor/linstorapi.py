"""
Linstorapi module
"""

import struct
import threading
import logging
import socket
import select
import ssl
from collections import deque
from datetime import datetime
import time
from google.protobuf.internal import encoder
from google.protobuf.internal import decoder

try:
    from urlparse import urlparse
except ImportError:
    from urllib.parse import urlparse

from linstor.proto.MsgHeader_pb2 import MsgHeader
from linstor.proto.MsgApiVersion_pb2 import MsgApiVersion
from linstor.proto.MsgApiCallResponse_pb2 import MsgApiCallResponse
from linstor.proto.MsgEvent_pb2 import MsgEvent
from linstor.proto.MsgCrtNode_pb2 import MsgCrtNode
from linstor.proto.MsgModNode_pb2 import MsgModNode
from linstor.proto.MsgDelNode_pb2 import MsgDelNode
from linstor.proto.MsgCrtNetInterface_pb2 import MsgCrtNetInterface
from linstor.proto.MsgModNetInterface_pb2 import MsgModNetInterface
from linstor.proto.MsgDelNetInterface_pb2 import MsgDelNetInterface
from linstor.proto.MsgLstNode_pb2 import MsgLstNode
from linstor.proto.MsgCrtStorPoolDfn_pb2 import MsgCrtStorPoolDfn
from linstor.proto.MsgModStorPoolDfn_pb2 import MsgModStorPoolDfn
from linstor.proto.MsgDelStorPoolDfn_pb2 import MsgDelStorPoolDfn
from linstor.proto.MsgLstStorPoolDfn_pb2 import MsgLstStorPoolDfn
from linstor.proto.MsgCrtStorPool_pb2 import MsgCrtStorPool
from linstor.proto.MsgModStorPool_pb2 import MsgModStorPool
from linstor.proto.MsgDelStorPool_pb2 import MsgDelStorPool
from linstor.proto.MsgLstStorPool_pb2 import MsgLstStorPool
from linstor.proto.MsgCrtRscDfn_pb2 import MsgCrtRscDfn
from linstor.proto.MsgModRscDfn_pb2 import MsgModRscDfn
from linstor.proto.MsgDelRscDfn_pb2 import MsgDelRscDfn
from linstor.proto.MsgLstRscDfn_pb2 import MsgLstRscDfn
from linstor.proto.MsgCrtVlmDfn_pb2 import MsgCrtVlmDfn
from linstor.proto.MsgAutoPlaceRsc_pb2 import MsgAutoPlaceRsc
from linstor.proto.MsgModVlmDfn_pb2 import MsgModVlmDfn
from linstor.proto.MsgDelVlmDfn_pb2 import MsgDelVlmDfn
from linstor.proto.MsgCrtRsc_pb2 import MsgCrtRsc
from linstor.proto.MsgModRsc_pb2 import MsgModRsc
from linstor.proto.MsgDelRsc_pb2 import MsgDelRsc
from linstor.proto.MsgLstRsc_pb2 import MsgLstRsc
from linstor.proto.MsgLstSnapshotDfn_pb2 import MsgLstSnapshotDfn
from linstor.proto.MsgSetCtrlCfgProp_pb2 import MsgSetCtrlCfgProp
from linstor.proto.MsgLstCtrlCfgProps_pb2 import MsgLstCtrlCfgProps
from linstor.proto.MsgDelCtrlCfgProp_pb2 import MsgDelCtrlCfgProp
from linstor.proto.MsgControlCtrl_pb2 import MsgControlCtrl
from linstor.proto.MsgCrtWatch_pb2 import MsgCrtWatch
from linstor.proto.MsgDelWatch_pb2 import MsgDelWatch
from linstor.proto.MsgEnterCryptPassphrase_pb2 import MsgEnterCryptPassphrase
from linstor.proto.MsgCrtCryptPassphrase_pb2 import MsgCrtCryptPassphrase
from linstor.proto.MsgModCryptPassphrase_pb2 import MsgModCryptPassphrase
from linstor.proto.MsgModRscConn_pb2 import MsgModRscConn
from linstor.proto.MsgReqErrorReport_pb2 import MsgReqErrorReport
from linstor.proto.MsgErrorReport_pb2 import MsgErrorReport
from linstor.proto.MsgHostname_pb2 import MsgHostname
from linstor.proto.MsgCrtSnapshot_pb2 import MsgCrtSnapshot
from linstor.proto.MsgDelSnapshot_pb2 import MsgDelSnapshot
from linstor.proto.MsgRestoreSnapshotVlmDfn_pb2 import MsgRestoreSnapshotVlmDfn
from linstor.proto.MsgRestoreSnapshotRsc_pb2 import MsgRestoreSnapshotRsc
from linstor.proto.Filter_pb2 import Filter
from linstor.proto.eventdata.EventVlmDiskState_pb2 import EventVlmDiskState
from linstor.proto.eventdata.EventRscState_pb2 import EventRscState
from linstor.proto.eventdata.EventRscDeploymentState_pb2 import EventRscDeploymentState
from linstor.proto.eventdata.EventRscDfnReady_pb2 import EventRscDfnReady
from linstor.proto.eventdata.EventSnapshotDeployment_pb2 import EventSnapshotDeployment
from linstor.proto.MsgQryMaxVlmSizes_pb2 import MsgQryMaxVlmSizes
from linstor.proto.MsgRspMaxVlmSizes_pb2 import MsgRspMaxVlmSizes
import linstor.sharedconsts as apiconsts

API_VERSION = 2
API_VERSION_MIN = 2


logging.basicConfig(level=logging.WARNING)


class AtomicInt(object):
    """
    This is a thread-safe integer type for incrementing, mostly reassembling modern atomic types,
    but with the overhead of a lock.
    """
    def __init__(self, init=0):
        self.val = init
        self.lock = threading.RLock()

    def get_and_inc(self):
        with self.lock:
            val = self.val
            self.val += 1
        return val


class LinstorError(Exception):
    """
    Linstor basic error class with a message
    """
    def __init__(self, msg, more_errors=None):
        self._msg = msg
        if more_errors is None:
            more_errors = []
        self._errors = more_errors

    def all_errors(self):
        return self._errors

    @property
    def message(self):
        return self._msg

    def __str__(self):
        return "Error: {msg}".format(msg=self._msg)

    def __repr__(self):
        return "LinstorError('{msg}')".format(msg=self._msg)


class LinstorNetworkError(LinstorError):
    """
    Linstor Error indicating an network/connection error.
    """
    def __init__(self, msg, more_errors=None):
        super(LinstorNetworkError, self).__init__(msg, more_errors)


class LinstorTimeoutError(LinstorError):
    """
    Linstor network timeout error
    """
    def __init__(self, msg, more_errors=None):
        super(LinstorTimeoutError, self).__init__(msg, more_errors)


class ProtoMessageResponse(object):
    """
    A base protobuf wrapper class, all api response use.
    """
    def __init__(self, proto_response):
        self._proto_msg = proto_response

    @property
    def proto_msg(self):
        """
        Returns the stored protobuf message object.

        :return: A protobuf message object.
        """
        return self._proto_msg

    def __nonzero__(self):
        return self.__bool__()

    def __bool__(self):
        return self._proto_msg.ByteSize() > 0

    def __str__(self):
        return str(self._proto_msg)

    def __repr__(self):
        return "ProtoMessageResponse(" + repr(self._proto_msg) + ")"


class ApiCallResponse(ProtoMessageResponse):
    """
    This is a wrapper class for a proto MsgApiCallResponse.
    It provides some additional methods for easier state checking of the ApiCallResponse.
    """
    def __init__(self, proto_response):
        super(ApiCallResponse, self).__init__(proto_response)

    @classmethod
    def from_json(cls, json_data):
        """
        Creates a ApiCallResponse from a data block.

        :param json_data: Parsed json data with "ret_code", "message" and "details" fields.
        :return: a new ApiCallResponse()
        """
        apiresp = MsgApiCallResponse()
        apiresp.ret_code = json_data["ret_code"]
        if "message" in json_data:
            apiresp.message = json_data["message"]
        if "details" in json_data:
            apiresp.details = json_data["details"]

        return ApiCallResponse(apiresp)

    def is_error(self):
        """
        Returns True if the ApiCallResponse is an error.

        :return: True if it is an error.
        """
        return True if self.ret_code & apiconsts.MASK_ERROR == apiconsts.MASK_ERROR else False

    def is_warning(self):
        """
        Returns True if the ApiCallResponse is a warning.

        :return: True if it is a warning.
        """
        return True if self.ret_code & apiconsts.MASK_WARN == apiconsts.MASK_WARN else False

    def is_info(self):
        """
        Returns True if the ApiCallResponse is an info.

        :return: True if it is an info.
        """
        return True if self.ret_code & apiconsts.MASK_INFO == apiconsts.MASK_INFO else False

    def is_success(self):
        """
        Returns True if the ApiCallResponse is a success message.

        :return: True if it is a success message.
        """
        return not self.is_error() and not self.is_warning() and not self.is_info()

    @property
    def ret_code(self):
        """
        Returns the numeric return code mask.

        :return: Return code mask value
        """
        return self._proto_msg.ret_code

    def __str__(self):
        return self._proto_msg.message

    def __repr__(self):
        return "ApiCallResponse({retcode}, {msg})".format(retcode=self.ret_code, msg=self.proto_msg.message)


class ErrorReport(ProtoMessageResponse):
    def __init__(self, protobuf):
        super(ErrorReport, self).__init__(protobuf)

    @property
    def datetime(self):
        dt = datetime.fromtimestamp(self._proto_msg.error_time / 1000)
        return dt.replace(microsecond=(self._proto_msg.error_time % 1000) * 1000)

    @property
    def id(self):
        return self._proto_msg.filename[len("ErrorReport-"):-len(".log")]

    @property
    def text(self):
        return self._proto_msg.text

    @property
    def node_names(self):
        return self._proto_msg.node_names


class ObjectIdentifier(object):
    def __init__(
            self,
            node_name=None,
            resource_name=None,
            volume_number=None,
            snapshot_name=None):
        self._node_name = node_name
        self._resource_name = resource_name
        self._volume_number = volume_number
        self._snapshot_name = snapshot_name

    def write_to_create_watch_msg(self, msg):
        if self._node_name is not None:
            msg.node_name = self._node_name
        if self._resource_name is not None:
            msg.resource_name = self._resource_name
        if self._volume_number is not None:
            msg.filter_by_volume_number = True
            msg.volume_number = self._volume_number
        if self._snapshot_name is not None:
            msg.snapshot_name = self._snapshot_name


class _LinstorNetClient(threading.Thread):
    IO_SIZE = 4096
    HDR_LEN = 16

    COMPLETE_ANSWERS = object()

    REPLY_MAP = {
        apiconsts.API_PONG: (None, None),
        apiconsts.API_REPLY: (MsgApiCallResponse, ApiCallResponse),
        apiconsts.API_LST_STOR_POOL_DFN: (MsgLstStorPoolDfn, ProtoMessageResponse),
        apiconsts.API_LST_STOR_POOL: (MsgLstStorPool, ProtoMessageResponse),
        apiconsts.API_LST_NODE: (MsgLstNode, ProtoMessageResponse),
        apiconsts.API_LST_RSC_DFN: (MsgLstRscDfn, ProtoMessageResponse),
        apiconsts.API_LST_RSC: (MsgLstRsc, ProtoMessageResponse),
        apiconsts.API_LST_VLM: (MsgLstRsc, ProtoMessageResponse),
        apiconsts.API_LST_SNAPSHOT_DFN: (MsgLstSnapshotDfn, ProtoMessageResponse),
        apiconsts.API_LST_CFG_VAL: (MsgLstCtrlCfgProps, ProtoMessageResponse),
        apiconsts.API_HOSTNAME: (MsgHostname, ProtoMessageResponse),
        apiconsts.API_LST_ERROR_REPORTS: (MsgErrorReport, ErrorReport),
        apiconsts.API_RSP_MAX_VLM_SIZE: (MsgRspMaxVlmSizes, ProtoMessageResponse)
    }

    EVENT_READER_TABLE = {
        apiconsts.EVENT_VOLUME_DISK_STATE: EventVlmDiskState,
        apiconsts.EVENT_RESOURCE_STATE: EventRscState,
        apiconsts.EVENT_RESOURCE_DEPLOYMENT_STATE: EventRscDeploymentState,
        apiconsts.EVENT_RESOURCE_DEFINITION_READY: EventRscDfnReady,
        apiconsts.EVENT_SNAPSHOT_DEPLOYMENT: EventSnapshotDeployment
    }

    URL_SCHEMA_MAP = {
        'linstor': apiconsts.DFLT_CTRL_PORT_PLAIN,
        'linstor+ssl': apiconsts.DFLT_CTRL_PORT_SSL,
        'linstorstlt': apiconsts.DFLT_STLT_PORT_PLAIN,
        'linstorstlt+ssl': apiconsts.DFLT_STLT_PORT_SSL
    }

    def __init__(self, timeout, keep_alive):
        super(_LinstorNetClient, self).__init__()
        self._socket = None  # type: socket.socket
        self._host = None  # type: str
        self._timeout = timeout
        self._slock = threading.RLock()
        self._cv_sock = threading.Condition(self._slock)
        self._logger = logging.getLogger('LinstorNetClient')
        self._replies = {}
        self._events = {}
        self._errors = []  # list of errors that happened in the select thread
        self._api_version = None
        self._cur_api_call_id = AtomicInt(1)
        self._cur_watch_id = AtomicInt(1)
        self._stats_received = 0
        self._controller_info = None  # type: str
        self._keep_alive = keep_alive  # type: bool

    def __del__(self):
        self.disconnect()

    @classmethod
    def parse_host(cls, host_str):
        """
        Tries to parse an ipv4, ipv6 or host address.

        Args:
            host_str (str): host/ip string
        Returns:
          Tuple(str, str): a tuple with the ip/host and port
        """
        if not host_str:
            return host_str, None

        if host_str[0] == '[':
            # ipv6 with port
            brace_close_pos = host_str.rfind(']')
            if brace_close_pos == -1:
                raise ValueError("No closing brace found in '{s}'".format(s=host_str))

            host_ipv6 = host_str[:brace_close_pos + 1].strip('[]')
            port_ipv6 = host_str[brace_close_pos + 2:]
            return host_ipv6, port_ipv6 if port_ipv6 else None

        if host_str.count(':') == 1:
            return host_str.split(':')

        return host_str, None

    @classmethod
    def _split_proto_msgs(cls, payload):
        """
        Splits a linstor payload into each raw proto buf message
        :param bytes payload: payload data
        :return: list of raw proto buf messages
        :rtype: list
        """
        # split payload, just a list of pbs, the receiver has to deal with them
        pb_msgs = []
        n = 0
        while n < len(payload):
            msg_len, new_pos = decoder._DecodeVarint32(payload, n)
            n = new_pos
            msg_buf = payload[n:n + msg_len]
            n += msg_len
            pb_msgs.append(msg_buf)
        return pb_msgs

    @classmethod
    def _parse_event(cls, event_name, event_data_bytes):
        """
        Parses the given byte data according to the event header name.

        :param event_name: Event header name
        :param event_data_bytes: Data bytes for protobuf message
        :return: parsed protobuf message
        """
        event_reader = cls.EVENT_READER_TABLE.get(event_name)

        if event_reader is None:
            return None

        event_data = event_reader()
        event_data.ParseFromString(event_data_bytes)
        return event_data

    @classmethod
    def _parse_proto_msgs(cls, type_tuple, data):
        """
        Parses a list of proto buf messages into their protobuf and/or wrapper classes,
        defined in the type_tuple.
        :param type_tuple: first item specifies the protobuf message, second item is a wrapper class or None
        :param list data: a list of raw protobuf message data
        :return: A list with protobuf or wrapper classes from the data
        """
        msg_resps = []
        msg_type = type_tuple[0]
        wrapper_type = type_tuple[1]

        if msg_type is None:
            return msg_resps

        for msg in data:
            resp = msg_type()
            resp.ParseFromString(msg)
            if wrapper_type:
                msg_resps.append(wrapper_type(resp))
            else:
                msg_resps.append(resp)
        return msg_resps

    @classmethod
    def _parse_proto_msg(cls, msg_type, data):
        msg = msg_type()
        msg.ParseFromString(data)
        return msg

    def _parse_api_version(self, data):
        """
        Parses data as a MsgApiVersion and checks if we support the api version.

        :param bytes data: byte data containing the MsgApiVersion message
        :return: True if parsed correctly and version supported
        :raises LinstorError: if the parsed api version is not supported
        """
        msg = self._parse_proto_msg(MsgApiVersion, data)
        if self._api_version is None:
            self._controller_info = msg.controller_info
            self._api_version = msg.version
            if API_VERSION_MIN > msg.version or msg.version > API_VERSION:
                raise LinstorError(
                    "Client API version '{v}' is incompatible with controller version '{r}', update your client."
                    .format(
                        v=API_VERSION,
                        r=msg.version)
                )
        else:
            self._logger.warning("API version message already received.")
        return True

    @classmethod
    def _parse_payload_length(cls, header):
        """
        Parses the payload length from a linstor header.

        :param bytes header: 16 bytes header data
        :return: Length of the payload
        """
        struct_format = "!xxxxIxxxxxxxx"
        assert struct.calcsize(struct_format) == len(header), "Header has unexpected size"
        exp_pkg_len, = struct.unpack(struct_format, header)
        return exp_pkg_len

    def _read_api_version_blocking(self):
        """
        Receives a api version message with blocking reads from the _socket and parses/checks it.

        :return: True
        """
        api_msg_data = self._socket.recv(self.IO_SIZE)
        while len(api_msg_data) < 16:
            api_msg_data += self._socket.recv(self.IO_SIZE)

        pkg_len = self._parse_payload_length(api_msg_data[:16])

        while len(api_msg_data) < pkg_len + 16:
            api_msg_data += self._socket.recv(self.IO_SIZE)

        msgs = self._split_proto_msgs(api_msg_data[16:])
        assert len(msgs) > 0, "Api version header message missing"
        hdr = self._parse_proto_msg(MsgHeader, msgs[0])

        assert hdr.msg_content == apiconsts.API_VERSION, "Unexpected message for API_VERSION"
        self._parse_api_version(msgs[1])
        return True

    def fetch_errors(self):
        """
        Get all errors that are currently on this object, list will be cleared.
        This error list will contain all errors that happened within the select thread.
        Usually you want this list after your socket was closed unexpected.

        :return: A list of LinstorErrors
        :rtype: list[LinstorError]
        """
        errors = self._errors
        self._errors = []
        return errors

    def connect(self, server):
        """
        Connects to the given server.
        The url has to be given in the linstor uri scheme. either linstor:// or linstor+ssl://

        :param str server: uri to the server
        :return: True if connected, else raises an LinstorError
        :raise LinstorError: if connection fails.
        """
        self._logger.debug("connecting to " + server)
        try:
            url = urlparse(server)

            if url.scheme not in _LinstorNetClient.URL_SCHEMA_MAP:
                raise LinstorError("Unknown uri scheme '{sc}' in '{uri}'.".format(sc=url.scheme, uri=server))

            host, port = self.parse_host(url.netloc)
            if not port:
                port = _LinstorNetClient.URL_SCHEMA_MAP[url.scheme]
            self._socket = socket.create_connection((host, port), timeout=self._timeout)

            # check if ssl
            if url.scheme.endswith('+ssl'):
                self._socket = ssl.wrap_socket(self._socket)
            self._socket.settimeout(self._timeout)

            # read api version
            if not url.scheme.startswith('linstorstlt'):
                self._read_api_version_blocking()

            self._socket.setblocking(0)
            self._logger.debug("connected to " + server)
            self._host = server
            return True
        except socket.error as err:
            self._socket = None
            raise LinstorNetworkError("Unable connecting to {hp}: {err}".format(hp=server, err=err))

    def disconnect(self):
        """
        Disconnects your current connection.

        :return: True if socket was connected, else False
        """
        with self._slock:
            if self._socket:
                self._logger.debug("disconnecting")
                self._socket.close()
                self._socket = None
                return True
        return False

    def controller_info(self):
        """
        Returns the controller info string parsed from the MsgApiVersion after connecting

        :return: String the controller sent as info
        :rtype: str
        """
        return self._controller_info

    @classmethod
    def _current_milli_time(cls):
        return int(round(time.time() * 1000))

    def run(self):
        """
        Runs the main select loop that handles incoming messages, parses them and
        puts them on the self._replies map.
        Errors that happen within this thread will be collected on the self._errors list
        and can be fetched with the fetch_errors() methods.

        :return:
        """
        self._errors = []
        package = bytes()  # current package data
        exp_pkg_len = 0  # expected package length

        last_read_time = self._current_milli_time()
        last_ping_time = self._current_milli_time()
        while self._socket:
            rds = []
            wds = []
            eds = []
            try:
                rds, wds, eds = select.select([self._socket], [], [self._socket], 2)
            except (IOError, TypeError):
                pass  # maybe check if socket is None, so we know it was closed on purpose

            self._logger.debug("select exit with:" + ",".join([str(rds), str(wds), str(eds)]))

            if eds:
                self._logger.debug("Socket exception on {hp}".format(hp=self._adrtuple2str(self._socket.getpeername())))
                self._errors.append(LinstorNetworkError(
                    "Socket exception on {hp}".format(hp=self._adrtuple2str(self._socket.getpeername()))))

            if last_read_time + (self._timeout * 1000) < self._current_milli_time():
                self._socket.close()
                self._socket = None
                self._errors.append(LinstorTimeoutError(
                    "Socket timeout, no data received since {t}ms.".format(
                        t=(self._current_milli_time()-last_read_time)
                    )
                ))

            if self._keep_alive and last_ping_time + 5000 < self._current_milli_time():
                self.send_msg(apiconsts.API_PING)
                last_ping_time = self._current_milli_time()

            for sock in rds:
                with self._slock:
                    if self._socket is None:  # socket was closed
                        break

                    read = self._socket.recv(_LinstorNetClient.IO_SIZE)

                    if len(read) == 0:
                        self._logger.debug(
                            "No data from {hp}, closing connection".format(
                                hp=self._adrtuple2str(self._socket.getpeername())))
                        self._socket.close()
                        self._socket = None
                        self._errors.append(
                            LinstorNetworkError("Remote '{hp}' closed connection dropped.".format(hp=self._host)))

                    last_read_time = self._current_milli_time()

                    package += read
                    pkg_len = len(package)
                    self._stats_received += pkg_len
                    self._logger.debug("pkg_len: " + str(pkg_len))

                    def has_hdr():  # used as macro
                        return pkg_len > _LinstorNetClient.HDR_LEN - 1 and exp_pkg_len == 0

                    def has_more_data():  # used as macro
                        return pkg_len >= (exp_pkg_len + _LinstorNetClient.HDR_LEN) and exp_pkg_len

                    while has_hdr() or has_more_data():
                        if has_hdr():  # header is 16 bytes
                            exp_pkg_len = self._parse_payload_length(package[:_LinstorNetClient.HDR_LEN])

                        self._logger.debug("exp_pkg_len: " + str(exp_pkg_len))

                        if has_more_data():
                            # cut out the parsing package
                            parse_buf = package[_LinstorNetClient.HDR_LEN:exp_pkg_len + _LinstorNetClient.HDR_LEN]
                            msgs = self._split_proto_msgs(parse_buf)
                            assert len(msgs) > 0, "we should have at least a header message"

                            # update buffer and length variables
                            package = package[exp_pkg_len + _LinstorNetClient.HDR_LEN:]  # put data into next parse run
                            pkg_len = len(package)  # update package length
                            self._logger.debug("pkg_len upd: " + str(len(package)))
                            exp_pkg_len = 0

                            self._process_msgs(msgs)

    def _process_msgs(self, msgs):
        hdr = self._parse_proto_msg(MsgHeader, msgs[0])  # parse header
        self._logger.debug(str(hdr))

        if hdr.msg_type == MsgHeader.MsgType.Value('API_CALL'):
            self._header_parsing_error(hdr)

        elif hdr.msg_type == MsgHeader.MsgType.Value('ONEWAY'):
            if hdr.msg_content == apiconsts.API_EVENT:
                event_header = MsgEvent()
                event_header.ParseFromString(msgs[1])
                self._logger.debug(
                    "Event '" + event_header.event_name + "', action " + event_header.event_action + " received")
                if event_header.event_action == apiconsts.EVENT_STREAM_VALUE:
                    event_data = self._parse_event(event_header.event_name, msgs[2]) \
                        if len(msgs) > 2 else None
                else:
                    event_data = None
                with self._cv_sock:
                    if event_header.watch_id in self._events:
                        self._events[event_header.watch_id].append((event_header, event_data))
                        self._cv_sock.notifyAll()
            else:
                self._header_parsing_error(hdr)

        elif hdr.msg_type == MsgHeader.MsgType.Value('ANSWER'):
            if hdr.msg_content in self.REPLY_MAP:
                # parse other message according to the reply_map and add them to the self._replies
                replies = self._parse_proto_msgs(self.REPLY_MAP[hdr.msg_content], msgs[1:])
                with self._cv_sock:
                    reply_deque = self._replies.get(hdr.api_call_id)
                    if reply_deque is None:
                        self._logger.warning(
                            "Unexpected answer received for API call ID " + hdr.api_call_id)
                    else:
                        reply_deque.extend(replies)
                        self._cv_sock.notifyAll()
            else:
                self._header_parsing_error(hdr)

        elif hdr.msg_type == MsgHeader.MsgType.Value('COMPLETE'):
            with self._cv_sock:
                reply_deque = self._replies.get(hdr.api_call_id)
                if reply_deque is None:
                    self._logger.warning(
                        "Unexpected completion received for API call ID " + hdr.api_call_id)
                else:
                    reply_deque.append(self.COMPLETE_ANSWERS)
                    self._cv_sock.notifyAll()

        else:
            self._header_parsing_error(hdr)

    def _header_parsing_error(self, hdr):
        self._logger.error(
            "Unknown message of type " + MsgHeader.MsgType.Name(hdr.msg_type) +
            ("" if hdr.msg_content == "" else " and content " + hdr.msg_content) + " received ")
        self.disconnect()
        with self._cv_sock:
            self._cv_sock.notifyAll()

    @property
    def connected(self):
        """Check if the socket is currently connected."""
        return self._socket is not None

    def send_msg(self, api_call_type, msg=None):
        """
        Sends a single or just a header message.

        :param str api_call_type: api call type that is set in the header message.
        :param msg: Message to be sent, if None only the header will be sent.
        :return: Message id of the message for wait_for_result()
        :rtype: int
        """
        return self.send_msgs(api_call_type, [msg] if msg else None)

    def send_msgs(self, api_call_type, msgs=None):
        """
        Sends a list of message or just a header.

        :param str api_call_type: api call type that is set in the header message.
        :param list msgs: List of message to be sent, if None only the header will be sent.
        :return: Message id of the message for wait_for_result()
        :rtype: int
        """
        hdr_msg = MsgHeader()
        hdr_msg.msg_content = api_call_type

        api_call_id = self._cur_api_call_id.get_and_inc()
        self._replies[api_call_id] = deque()

        hdr_msg.msg_type = MsgHeader.MsgType.Value('API_CALL')
        hdr_msg.api_call_id = api_call_id

        h_type = struct.pack("!I", 0)  # currently always 0, 32 bit
        h_reserved = struct.pack("!Q", 0)  # reserved, 64 bit

        msg_serialized = bytes()

        header_serialized = hdr_msg.SerializeToString()
        delim = encoder._VarintBytes(len(header_serialized))
        msg_serialized += delim + header_serialized

        if msgs:
            for msg in msgs:
                payload_serialized = msg.SerializeToString()
                delim = encoder._VarintBytes(len(payload_serialized))
                msg_serialized += delim + payload_serialized

        h_payload_length = len(msg_serialized)
        h_payload_length = struct.pack("!I", h_payload_length)  # 32 bit

        full_msg = h_type + h_payload_length + h_reserved + msg_serialized

        with self._slock:
            if not self.connected:
                raise LinstorNetworkError("Not connected to controller.", self.fetch_errors())

            msg_len = len(full_msg)
            self._logger.debug("sending " + str(msg_len))
            sent = 0
            while sent < msg_len:
                sent += self._socket.send(full_msg)
            self._logger.debug("sent " + str(sent))
        return hdr_msg.api_call_id

    def wait_for_result(self, api_call_id, answer_handler):
        """
        This method blocks and waits for all answers to the given api_call_id.

        :param int api_call_id: identifies the answers to wait for
        :param Callable answer_handler: function that is called for each answer that is received
        """
        with self._cv_sock:
            try:
                while api_call_id in self._replies and len(self._replies[api_call_id]) == 0:
                    if not self.connected:
                        return

                    self._cv_sock.wait(1)

                    if api_call_id in self._replies:
                        reply_deque = self._replies[api_call_id]
                        while len(reply_deque) > 0:
                            reply = reply_deque.popleft()
                            if reply == self.COMPLETE_ANSWERS:
                                return
                            else:
                                answer_handler(reply)
            finally:
                self._replies.pop(api_call_id)

    def wait_for_events(self, watch_id, event_handler):
        """
        This method blocks and waits for any events.
        The handler function is called for each event.
        When the value returned by the handler is not None, this method returns that value.

        :param int watch_id: watch id to watch for
        :param Callable event_handler: function that is called if an event was received.
        :return: The result of the handler function if it returns not None
        """
        local_queue = deque()
        while True:
            with self._cv_sock:
                if not self.connected:
                    return None

                self._cv_sock.wait(0.2)

                while watch_id in self._events and self._events[watch_id]:
                    # copy events to local queue to allow to run event_handler without lock
                    local_queue.append(self._events[watch_id].popleft())

            while local_queue:
                event_handler_result = event_handler(*local_queue.popleft())
                if event_handler_result is not None:
                    return event_handler_result

    def register_watch(self, watch_id):
        """
        Add a queue entry into the events map.

        :param watch_id: watch id to add
        :return: None
        """
        with self._slock:
            self._events[watch_id] = deque()

    def deregister_watch(self, watch_id):
        """
        Remove a queue entry from the events map.
        :param watch_id: watch id to remove
        :return: None
        """
        with self._slock:
            del self._events[watch_id]

    def next_watch_id(self):
        return self._cur_watch_id.get_and_inc()

    def stats(self):
        """
        Returns network statistics as printable string.

        :return: Returns network statistics as printable string.
        :rtype: str
        """
        return "Received bytes: {b}".format(b=self._stats_received)

    @staticmethod
    def _adrtuple2str(tuple):
        ip = tuple[0]
        port = tuple[1]
        s = "[{ip}]".format(ip=ip) if ':' in ip else ip
        s += ":" + str(port)
        return s


class Linstor(object):
    """
    Linstor class represents a client connection to the Linstor controller.
    It has all methods to manipulate all kind of objects on the controller.

    The controller host address has to be specified as linstor url.
    e.g: ``linstor://localhost``, ``linstor+ssl://localhost``

    :param str ctrl_host: Linstor uri to the controller e.g. ``linstor://192.168.0.1``
    :param bool keep_alive: Sends PING messages to the controller
    """
    _node_types = [
        apiconsts.VAL_NODE_TYPE_CTRL,
        apiconsts.VAL_NODE_TYPE_AUX,
        apiconsts.VAL_NODE_TYPE_CMBD,
        apiconsts.VAL_NODE_TYPE_STLT
    ]

    def __init__(self, ctrl_host, timeout=300, keep_alive=False):
        self._ctrl_host = ctrl_host
        self._linstor_client = None  # type: _LinstorNetClient
        self._logger = logging.getLogger('Linstor')
        self._timeout = timeout
        self._keep_alive = keep_alive

    def __del__(self):
        self.disconnect()

    def __enter__(self):
        self.connect()  # raises exception if error
        return self

    def __exit__(self, type, value, traceback):
        self.disconnect()

    @classmethod
    def all_api_responses_success(cls, replies):
        """
        Checks if none of the responses has an error.

        :param list[ApiCallResponse] replies: apicallresponse to check
        :return: True if none of the replies has an error.
        :rtype: bool
        """
        return all([not r.is_error() for r in replies])

    @classmethod
    def filter_api_call_response(cls, replies):
        """
        Filters api call responses from Controller replies.

        :param list[ProtoMessageResponse] replies: controller reply list
        :return: Returns all only ApiCallResponses from replies or empty list.
        :rtype: [ApiCallResponse]
        """
        return [reply for reply in replies if isinstance(reply, ApiCallResponse)]

    @classmethod
    def return_if_failure(cls, replies_):
        """
        Returns None if any of the replies is no success.

        :param list[ApiCallResponse] replies_: list of api call responses
        :return: None if any is not success, else all given replies
        """
        if not cls.all_api_responses_success(replies_):
            return replies_
        return None

    @classmethod
    def exit_on_error_event_handler(cls, event_header, event_data):
        """
        Extracts non success ApiCallResponses from event_data.

        :param MsgEvent event_header: protobuf event message
        :param MsgEventRscDeploymentState event_data: to check for non success replies.
        :return: None if there are only success replies, else list of error ApiCallResponses
        :rtype: [AbiCallResponse]
        """
        if event_header.event_name == apiconsts.EVENT_RESOURCE_DEPLOYMENT_STATE and event_data is not None:
            api_call_responses = [
                ApiCallResponse(response)
                for response in event_data.responses
            ]
            failure_responses = [
                api_call_response for api_call_response in api_call_responses
                if not api_call_response.is_success()
            ]

            return failure_responses if failure_responses else None
        return None

    @classmethod
    def _modify_props(cls, msg, property_dict, delete_props=None):
        if property_dict:
            for key, val in property_dict.items():
                lin_kv = msg.override_props.add()
                lin_kv.key = key
                lin_kv.value = val

        if delete_props:
            msg.delete_prop_keys.extend(delete_props)
        return msg

    def _send_and_wait(self, api_call, msg=None, allow_no_reply=False):
        """
        Helper function that sends a api call[+msg] and waits for the answer from the controller

        :param str api_call: API call identifier
        :param msg: Proto message to send
        :param bool allow_no_reply: Do not raise an error if there are no replies.
        :return: A list containing ApiCallResponses from the controller.
        :rtype: list[ApiCallResponse]
        """
        api_call_id = self._linstor_client.send_msg(api_call, msg)
        replies = []

        def answer_handler(answer):
            replies.append(answer)

        self._linstor_client.wait_for_result(api_call_id, answer_handler)

        errors = self._linstor_client.fetch_errors()
        if errors:
            raise errors[0]  # for now only send the first error

        if not allow_no_reply and len(replies) == 0:
            raise LinstorNetworkError("No answer received")

        return replies

    def _watch_send_and_wait(
            self,
            api_call,
            msg,
            async,
            event_name,
            object_identifier):
        """
        Helper function that sends a api call[+msg], waits for the answer from the controller and waits for a response
        in the form of an event containing API responses.

        :param str api_call: API call identifier
        :param msg: Proto message to send
        :param bool async: True to return without waiting for the action to complete on the satellites.
        :param str event_name: Event name
        :param ObjectIdentifier object_identifier: Object to subscribe for events
        :return: A list containing ApiCallResponses from the controller.
        :rtype: list[ApiCallResponse]
        """

        if async:
            watch_id = None
        else:
            watch_id = self._linstor_client.next_watch_id()

        try:
            if not async:
                watch_responses = self.watch_create(watch_id, object_identifier)

                if not self.all_api_responses_success(watch_responses):
                    return watch_responses

            responses = self._send_and_wait(api_call, msg)
            if async or not self.all_api_responses_success(responses):
                return responses

            def event_handler(event_header, event_data, responses):
                if event_header.event_name == event_name:
                    if event_header.event_action in [
                        apiconsts.EVENT_STREAM_CLOSE_REMOVED,
                        apiconsts.EVENT_STREAM_CLOSE_NO_CONNECTION
                    ]:
                        return ()
                    else:
                        event_responses = [ApiCallResponse(response) for response in event_data.responses]
                        responses += event_responses
                        return self.return_if_failure(event_responses)
                return None

            self._linstor_client.wait_for_events(
                watch_id, lambda event_header, event_data: event_handler(event_header, event_data, responses))
            return responses
        finally:
            if watch_id is not None:
                self._watch_delete(watch_id)

    def connect(self):
        """
        Connects the internal linstor network client.

        :return: True
        """
        self._linstor_client = _LinstorNetClient(timeout=self._timeout, keep_alive=self._keep_alive)
        self._linstor_client.connect(self._ctrl_host)
        self._linstor_client.daemon = True
        self._linstor_client.start()
        return True

    @property
    def connected(self):
        """
        Checks if the Linstor object is connect to a controller.

        :return: True if connected, else False.
        """
        return self._linstor_client.connected

    def disconnect(self):
        """
        Disconnects the current connection.

        :return: True if the object was connected else False.
        """
        return self._linstor_client.disconnect()

    def node_create(
            self,
            node_name,
            node_type,
            ip,
            com_type=apiconsts.VAL_NETCOM_TYPE_PLAIN,
            port=None,
            netif_name='default'
    ):
        """
        Creates a node on the controller.

        :param str node_name: Name of the node.
        :param str node_type: Node type of the new node, one of linstor.consts.VAL_NODE_TYPE*
        :param str ip: IP address to use for the nodes default netinterface.
        :param str com_type: Communication type of the node.
        :param int port: Port number of the node.
        :param str netif_name: Netinterface name that is created.
        :return: A list containing ApiCallResponses from the controller.
        :rtype: list[ApiCallResponse]
        """
        msg = MsgCrtNode()

        msg.node.name = node_name
        if node_type not in self._node_types:
            raise LinstorError(
                "Unknown node type '{nt}'. Known types are: {kt}".format(nt=node_type, kt=", ".join(self._node_types))
            )
        msg.node.type = node_type
        netif = msg.node.net_interfaces.add()
        netif.name = netif_name
        netif.address = ip

        if port is None:
            if com_type == apiconsts.VAL_NETCOM_TYPE_PLAIN:
                port = apiconsts.DFLT_CTRL_PORT_PLAIN \
                    if msg.node.type == apiconsts.VAL_NODE_TYPE_CTRL else apiconsts.DFLT_STLT_PORT_PLAIN
            elif com_type == apiconsts.VAL_NETCOM_TYPE_SSL:
                if msg.node.type == apiconsts.VAL_NODE_TYPE_STLT:
                    port = apiconsts.DFLT_STLT_PORT_SSL
                else:
                    port = apiconsts.DFLT_CTRL_PORT_SSL
            else:
                raise LinstorError("Communication type %s has no default port" % com_type)

        netif.stlt_port = port
        netif.stlt_encryption_type = com_type

        return self._send_and_wait(apiconsts.API_CRT_NODE, msg)

    def node_modify(self, node_name, node_type=None, property_dict=None, delete_props=None):
        """
        Modify the properties of a given node.

        :param str node_name: Name of the node to modify.
        :param int node_type: Type of the node, any of VAL_NODE_TYPE_*
        :param dict[str, str] property_dict: Dict containing key, value pairs for new values.
        :param list[str] delete_props: List of properties to delete
        :return: A list containing ApiCallResponses from the controller.
        :rtype: list[ApiCallResponse]
        """
        msg = MsgModNode()
        msg.node_name = node_name

        if node_type is not None:
            msg.node_type = node_type

        self._modify_props(msg, property_dict, delete_props)

        return self._send_and_wait(apiconsts.API_MOD_NODE, msg)

    def node_delete(self, node_name):
        """
        Deletes the given node on the controller.

        :param str node_name: Node name to delete.
        :return: A list containing ApiCallResponses from the controller.
        :rtype: list[ApiCallResponse]
        """
        msg = MsgDelNode()
        msg.node_name = node_name

        return self._send_and_wait(apiconsts.API_DEL_NODE, msg)

    def node_lost(self, node_name):
        """
        Deletes an unrecoverable node on the controller.

        :param str node_name: Node name to delete.
        :return: A list containing ApiCallResponses from the controller.
        :rtype: list[ApiCallResponse]
        """
        msg = MsgDelNode()
        msg.node_name = node_name

        return self._send_and_wait(apiconsts.API_LOST_NODE, msg)

    def netinterface_create(self, node_name, interface_name, ip, port=None, com_type=None):
        """
        Create a netinterface for a given node.

        :param str node_name: Name of the node to add the interface.
        :param str interface_name: Name of the new interface.
        :param str ip: IP address of the interface.
        :param int port: Port of the interface
        :param str com_type: Communication type to use on the interface.
        :return: A list containing ApiCallResponses from the controller.
        :rtype: list[ApiCallResponse]
        """
        msg = MsgCrtNetInterface()
        msg.node_name = node_name

        msg.net_if.name = interface_name
        msg.net_if.address = ip

        if port:
            msg.net_if.stlt_port = port
            msg.net_if.stlt_encryption_type = com_type

        return self._send_and_wait(apiconsts.API_CRT_NET_IF, msg)

    def netinterface_modify(self, node_name, interface_name, ip, port=None, com_type=None):
        """
        Modify a netinterface on the given node.

        :param str node_name: Name of the node.
        :param str interface_name: Name of the netinterface to modify.
        :param str ip: New IP address of the netinterface
        :param int port: New Port of the netinterface
        :param str com_type: New communication type of the netinterface
        :return: A list containing ApiCallResponses from the controller.
        :rtype: list[ApiCallResponse]
        """
        msg = MsgModNetInterface()

        msg.node_name = node_name
        msg.net_if.name = interface_name
        msg.net_if.address = ip

        if port:
            msg.net_if.stlt_port = port
            msg.net_if.stlt_encryption_type = com_type

        return self._send_and_wait(apiconsts.API_MOD_NET_IF, msg)

    def netinterface_delete(self, node_name, interface_name):
        """
        Deletes a netinterface on the given node.

        :param str node_name: Name of the node.
        :param str interface_name: Name of the netinterface to delete.
        :return: A list containing ApiCallResponses from the controller.
        :rtype: list[ApiCallResponse]
        """
        msg = MsgDelNetInterface()
        msg.node_name = node_name
        msg.net_if_name = interface_name

        return self._send_and_wait(apiconsts.API_DEL_NET_IF, msg)

    def node_list(self):
        """
        Request a list of all nodes known to the controller.

        :return: A MsgLstNode proto message containing all information.
        :rtype: list[ProtoMessageResponse]
        """
        return self._send_and_wait(apiconsts.API_LST_NODE)

    def storage_pool_dfn_create(self, name):
        """
        Creates a new storage pool definition on the controller.

        :param str name: Storage pool definition name.
        :return: A list containing ApiCallResponses from the controller.
        :rtype: list[ApiCallResponse]
        """
        msg = MsgCrtStorPoolDfn()
        msg.stor_pool_dfn.stor_pool_name = name

        return self._send_and_wait(apiconsts.API_CRT_STOR_POOL_DFN, msg)

    def storage_pool_dfn_modify(self, name, property_dict, delete_props=None):
        """
        Modify properties of a given storage pool definition.

        :param str name: Storage pool definition name to modify
        :param dict[str, str] property_dict: Dict containing key, value pairs for new values.
        :param list[str] delete_props: List of properties to delete
        :return: A list containing ApiCallResponses from the controller.
        :rtype: list[ApiCallResponse]
        """
        msg = MsgModStorPoolDfn()
        msg.stor_pool_name = name

        msg = self._modify_props(msg, property_dict, delete_props)

        return self._send_and_wait(apiconsts.API_MOD_STOR_POOL_DFN, msg)

    def storage_pool_dfn_delete(self, name):
        """
        Delete a given storage pool definition.

        :param str name: Storage pool definition name to delete.
        :return: A list containing ApiCallResponses from the controller.
        :rtype: list[ApiCallResponse]
        """
        msg = MsgDelStorPoolDfn()
        msg.stor_pool_name = name

        return self._send_and_wait(apiconsts.API_DEL_STOR_POOL_DFN, msg)

    def storage_pool_dfn_list(self):
        """
        Request a list of all storage pool definitions known to the controller.

        :return: A MsgLstStorPoolDfn proto message containing all information.
        :rtype: list[ProtoMessageResponse]
        """
        return self._send_and_wait(apiconsts.API_LST_STOR_POOL_DFN)

    def storage_pool_dfn_max_vlm_sizes(
            self,
            place_count,
            storage_pool_name=None,
            do_not_place_with=None,
            do_not_place_with_regex=None,
            replicas_on_same=None,
            replicas_on_different=None
    ):
        """
        Auto places(deploys) a resource to the amount of place_count.

        :param int place_count: Number of placements, on how many different nodes
        :param str storage_pool_name: Only check for the given storage pool name
        :param list[str] do_not_place_with: Do not place with resource names in this list
        :param str do_not_place_with_regex: A regex string that rules out resources
        :param list[str] replicas_on_same: A list of node property names, their values should match
        :param list[str] replicas_on_different: A list of node property names, their values should not match
        :return: A list containing ApiCallResponses or ProtoMessageResponse (with MsgRspMaxVlmSizes)
        :rtype: Union[list[ApiCallResponse], list[ProtoMessageResponse]]
        """
        msg = MsgQryMaxVlmSizes()
        msg_filter = msg.select_filter
        msg_filter.place_count = place_count

        if storage_pool_name:
            msg_filter.storage_pool = storage_pool_name
        if do_not_place_with:
            msg_filter.not_place_with_rsc.extend(do_not_place_with)
        if do_not_place_with_regex:
            msg_filter.not_place_with_rsc_regex = do_not_place_with_regex
        if replicas_on_same:
            msg_filter.replicas_on_same.extend(replicas_on_same)
        if replicas_on_different:
            msg_filter.replicas_on_different.extend(replicas_on_different)

        return self._send_and_wait(apiconsts.API_QRY_MAX_VLM_SIZE, msg)

    @staticmethod
    def _storage_driver_pool_to_props(storage_driver, driver_pool_name):
        if storage_driver == 'Diskless':
            return []

        if not driver_pool_name:
            raise LinstorError(
                "Driver '{drv}' needs a driver pool name.".format(drv=storage_driver)
            )

        if storage_driver == 'Lvm':
            return [(apiconsts.NAMESPC_STORAGE_DRIVER + '/' + apiconsts.KEY_STOR_POOL_VOLUME_GROUP, driver_pool_name)]

        if storage_driver == 'LvmThin':
            driver_pool_parts = driver_pool_name.split('/')
            if not len(driver_pool_parts) == 2:
                raise LinstorError("Pool name '{dp}' does not have format VG/LV".format(dp=driver_pool_name))
            return \
                [(apiconsts.NAMESPC_STORAGE_DRIVER + '/' + apiconsts.KEY_STOR_POOL_VOLUME_GROUP, driver_pool_parts[0]),
                 (apiconsts.NAMESPC_STORAGE_DRIVER + '/' + apiconsts.KEY_STOR_POOL_THIN_POOL, driver_pool_parts[1])]

        if storage_driver == 'Zfs':
            return [(apiconsts.NAMESPC_STORAGE_DRIVER + '/' + apiconsts.KEY_STOR_POOL_ZPOOL, driver_pool_name)]

        raise LinstorError(
            "Unknown storage driver '{drv}', known drivers: lvm, lvmthin, zfs, diskless".format(drv=storage_driver)
        )

    @staticmethod
    def _find_prop(props, search_key, default):
        for entry in props:
            if entry.key == search_key:
                return entry.value
        return default

    @staticmethod
    def storage_props_to_driver_pool(storage_driver, props):
        """
        Find the storage pool value for the given storage_driver in the given props.

        :param str storage_driver: String specifying a storage driver [``Lvm``, ``LvmThin``, ``Zfs``]
        :param props: Properties to search the storage pool value.
        :return: If found the storage pool value, else ''
        :rtype: str
        """
        if storage_driver == 'Lvm':
            return Linstor._find_prop(
                props, apiconsts.NAMESPC_STORAGE_DRIVER + '/' + apiconsts.KEY_STOR_POOL_VOLUME_GROUP, ''
            )

        if storage_driver == 'LvmThin':
            vg = Linstor._find_prop(
                props, apiconsts.NAMESPC_STORAGE_DRIVER + '/' + apiconsts.KEY_STOR_POOL_VOLUME_GROUP, ''
            )
            lv = Linstor._find_prop(
                props, apiconsts.NAMESPC_STORAGE_DRIVER + '/' + apiconsts.KEY_STOR_POOL_THIN_POOL, ''
            )
            return "{vg}/{lv}".format(vg=vg, lv=lv)

        if storage_driver == 'Zfs':
            return Linstor._find_prop(
                props, apiconsts.NAMESPC_STORAGE_DRIVER + '/' + apiconsts.KEY_STOR_POOL_ZPOOL, ''
            )

        return ''

    def storage_pool_create(self, node_name, storage_pool_name, storage_driver, driver_pool_name):
        """
        Creates a new storage pool on the given node.
        If there doesn't yet exist a storage pool definition the controller will implicitly create one.

        :param str node_name: Node on which to create the storage pool.
        :param str storage_pool_name: Name of the storage pool.
        :param str storage_driver: Storage driver to use.
        :param str driver_pool_name: Name of the pool the storage driver should use on the node.
        :return: A list containing ApiCallResponses from the controller.
        :rtype: list[ApiCallResponse]
        """
        msg = MsgCrtStorPool()
        msg.stor_pool.stor_pool_name = storage_pool_name
        msg.stor_pool.node_name = node_name
        msg.stor_pool.driver = '{driver}Driver'.format(driver=storage_driver)

        # set driver device pool properties
        for key, value in self._storage_driver_pool_to_props(storage_driver, driver_pool_name):
            prop = msg.stor_pool.props.add()
            prop.key = key
            prop.value = value

        return self._send_and_wait(apiconsts.API_CRT_STOR_POOL, msg)

    def storage_pool_modify(self, node_name, storage_pool_name, property_dict, delete_props=None):
        """
        Modify properties of a given storage pool on the given node.

        :param str node_name: Node on which the storage pool resides.
        :param str storage_pool_name: Name of the storage pool.
        :param dict[str, str] property_dict: Dict containing key, value pairs for new values.
        :param list[str] delete_props: List of properties to delete
        :return: A list containing ApiCallResponses from the controller.
        :rtype: list[ApiCallResponse]
        """
        msg = MsgModStorPool()
        msg.node_name = node_name
        msg.stor_pool_name = storage_pool_name

        msg = self._modify_props(msg, property_dict, delete_props)

        return self._send_and_wait(apiconsts.API_MOD_STOR_POOL, msg)

    def storage_pool_delete(self, node_name, storage_pool_name):
        """
        Deletes a storage pool on the given node.

        :param str node_name: Node on which the storage pool resides.
        :param str storage_pool_name: Name of the storage pool.
        :return: A list containing ApiCallResponses from the controller.
        :rtype: list[ApiCallResponse]
        """
        msg = MsgDelStorPool()
        msg.node_name = node_name
        msg.stor_pool_name = storage_pool_name

        return self._send_and_wait(apiconsts.API_DEL_STOR_POOL, msg)

    def storage_pool_list(self, filter_by_nodes=None, filter_by_stor_pools=None):
        """
        Request a list of all storage pool known to the controller.

        :param list[str] filter_by_nodes: Filter storage pools by nodes.
        :param list[str] filter_by_stor_pools: Filter storage pools by storage pool names.
        :return: A MsgLstStorPool proto message containing all information.
        :rtype: list[ProtoMessageResponse]
        """
        f = Filter()
        if filter_by_nodes:
            f.node_names.extend(filter_by_nodes)
        if filter_by_stor_pools:
            f.stor_pool_names.extend(filter_by_stor_pools)
        return self._send_and_wait(apiconsts.API_LST_STOR_POOL, f)

    def resource_dfn_create(self, name, port=None):
        """
        Creates a resource definition.

        :param str name: Name of the new resource definition.
        :param int port: Port the resource definition should use.
        :return: A list containing ApiCallResponses from the controller.
        :rtype: list[ApiCallResponse]
        """
        msg = MsgCrtRscDfn()
        msg.rsc_dfn.rsc_name = name
        if port is not None:
            msg.rsc_dfn.rsc_dfn_port = port
        # if args.secret:
        #     p.secret = args.secret
        return self._send_and_wait(apiconsts.API_CRT_RSC_DFN, msg)

    def resource_dfn_modify(self, name, property_dict, delete_props=None):
        """
        Modify properties of the given resource definition.

        :param str name: Name of the resource definition to modify.
        :param dict[str, str] property_dict: Dict containing key, value pairs for new values.
        :param list[str] delete_props: List of properties to delete
        :return: A list containing ApiCallResponses from the controller.
        :rtype: list[ApiCallResponse]
        """
        msg = MsgModRscDfn()
        msg.rsc_name = name

        msg = self._modify_props(msg, property_dict, delete_props)

        return self._send_and_wait(apiconsts.API_MOD_RSC_DFN, msg)

    def resource_dfn_delete(self, name):
        """
        Delete a given resource definition.

        :param str name: Resource definition name to delete.
        :return: A list containing ApiCallResponses from the controller.
        :rtype: list[ApiCallResponse]
        """
        msg = MsgDelRscDfn()
        msg.rsc_name = name

        return self._send_and_wait(apiconsts.API_DEL_RSC_DFN, msg)

    def resource_dfn_list(self):
        """
        Request a list of all resource definitions known to the controller.

        :return: A MsgLstRscDfn proto message containing all information.
        :rtype: list[ProtoMessageResponse]
        """
        return self._send_and_wait(apiconsts.API_LST_RSC_DFN)

    def volume_dfn_create(self, rsc_name, size, volume_nr=None, minor_nr=None, encrypt=False, storage_pool=None):
        """
        Create a new volume definition on the controller.

        :param str rsc_name: Name of the resource definition it is linked to.
        :param int size: Size of the volume definition in kibibytes.
        :param int volume_nr: Volume number to use.
        :param int minor_nr: Minor number to use.
        :param bool encrypt: Encrypt created volumes from this volume definition.
        :param storage_pool: Storage pool this volume definition will use.
        :return: A list containing ApiCallResponses from the controller.
        :rtype: list[ApiCallResponse]
        """
        msg = MsgCrtVlmDfn()
        msg.rsc_name = rsc_name

        vlmdf = msg.vlm_dfns.add()
        vlmdf.vlm_size = size
        if minor_nr is not None:
            vlmdf.vlm_minor = minor_nr

        if volume_nr is not None:
            vlmdf.vlm_nr = volume_nr

        if encrypt:
            vlmdf.vlm_flags.extend([apiconsts.FLAG_ENCRYPTED])

        if storage_pool:
            prop = vlmdf.vlm_props.add()
            prop.key = apiconsts.KEY_STOR_POOL_NAME
            prop.value = storage_pool

        return self._send_and_wait(apiconsts.API_CRT_VLM_DFN, msg)

    def volume_dfn_modify(self, rsc_name, volume_nr, set_properties=None, delete_properties=None, size=None):
        """
        Modify properties of the given volume definition.

        :param str rsc_name: Name of the resource definition.
        :param int volume_nr: Volume number of the volume definition.
        :param dict[str, str] set_properties: Dict containing key, value pairs for new values.
        :param list[str] delete_properties: List of properties to delete
        :param int size: New size of the volume definition in kibibytes.
        :return: A list containing ApiCallResponses from the controller.
        :rtype: list[ApiCallResponse]
        """
        msg = MsgModVlmDfn()
        msg.rsc_name = rsc_name
        msg.vlm_nr = volume_nr

        if size:
            msg.vlm_size = size

        msg = self._modify_props(msg, set_properties, delete_properties)

        return self._send_and_wait(apiconsts.API_MOD_VLM_DFN, msg)

    def volume_dfn_delete(self, rsc_name, volume_nr):
        """
        Delete a given volume definition.

        :param str rsc_name: Resource definition name of the volume definition.
        :param volume_nr: Volume number.
        :return: A list containing ApiCallResponses from the controller.
        :rtype: list[ApiCallResponse]
        """
        msg = MsgDelVlmDfn()
        msg.rsc_name = rsc_name
        msg.vlm_nr = volume_nr

        return self._send_and_wait(apiconsts.API_DEL_VLM_DFN, msg)

    def resource_create(self, node_name, rsc_name, diskless=False, storage_pool=None, node_id=None):
        """
        Creates a new resource on the given node.

        :param str node_name:
        :param str rsc_name:
        :param bool diskless: Should the resource be diskless
        :param storage_pool:
        :return:
        """
        msg = MsgCrtRsc()
        msg.rsc.name = rsc_name
        msg.rsc.node_name = node_name

        if storage_pool:
            prop = msg.rsc.props.add()
            prop.key = apiconsts.KEY_STOR_POOL_NAME
            prop.value = storage_pool

        if diskless:
            msg.rsc.rsc_flags.append(apiconsts.FLAG_DISKLESS)

        if node_id is not None:
            msg.override_node_id = True
            msg.rsc.node_id = node_id

        return self._send_and_wait(apiconsts.API_CRT_RSC, msg)

    def resource_auto_place(
            self,
            rsc_name,
            place_count,
            storage_pool=None,
            do_not_place_with=None,
            do_not_place_with_regex=None,
            replicas_on_same=None,
            replicas_on_different=None,
            diskless_on_remaining=False
    ):
        """
        Auto places(deploys) a resource to the amount of place_count.

        :param str rsc_name: Name of the resource definition to deploy
        :param int place_count: Number of placements, on how many different nodes
        :param str storage_pool: Storage pool to use
        :param list[str] do_not_place_with: Do not place with resource names in this list
        :param str do_not_place_with_regex: A regex string that rules out resources
        :param list[str] replicas_on_same: A list of node property names, their values should match
        :param list[str] replicas_on_different: A list of node property names, their values should not match
        :param bool diskless_on_remaining: If True all remaining nodes will add a diskless resource
        :return: A list containing ApiCallResponses from the controller.
        :rtype: list[ApiCallResponse]
        """
        msg = MsgAutoPlaceRsc()
        msg.rsc_name = rsc_name
        msg.diskless_on_remaining = diskless_on_remaining
        msg_filter = msg.select_filter
        msg_filter.place_count = place_count

        if storage_pool:
            msg_filter.storage_pool = storage_pool
        if do_not_place_with:
            msg_filter.not_place_with_rsc.extend(do_not_place_with)
        if do_not_place_with_regex:
            msg_filter.not_place_with_rsc_regex = do_not_place_with_regex
        if replicas_on_same:
            msg_filter.replicas_on_same.extend(replicas_on_same)
        if replicas_on_different:
            msg_filter.replicas_on_different.extend(replicas_on_different)

        return self._send_and_wait(apiconsts.API_AUTO_PLACE_RSC, msg)

    def resource_modify(self, node_name, rsc_name, property_dict, delete_props=None):
        """
        Modify properties of a given resource.

        :param str node_name: Node name where the resource is deployed.
        :param str rsc_name: Name of the resource.
        :param dict[str, str] property_dict: Dict containing key, value pairs for new values.
        :param list[str] delete_props: List of properties to delete
        :return: A list containing ApiCallResponses from the controller.
        :rtype: list[ApiCallResponse]
        """
        msg = MsgModRsc()
        msg.node_name = node_name
        msg.rsc_name = rsc_name

        msg = self._modify_props(msg, property_dict, delete_props)

        return self._send_and_wait(apiconsts.API_MOD_RSC, msg)

    def resource_delete(self, node_name, rsc_name):
        """
        Deletes a given resource on the given node.

        :param str node_name: Name of the node where the resource is deployed.
        :param str rsc_name: Name of the resource.
        :return: A list containing ApiCallResponses from the controller.
        :rtype: list[ApiCallResponse]
        """
        msg = MsgDelRsc()
        msg.node_name = node_name
        msg.rsc_name = rsc_name

        return self._send_and_wait(apiconsts.API_DEL_RSC, msg)

    def resource_list(self, filter_by_nodes=None, filter_by_resources=None):
        """
        Request a list of all resources known to the controller.

        :param list[str] filter_by_nodes: filter resources by nodes
        :param list[str] filter_by_resources: filter resources by resource names
        :return: A MsgLstRsc proto message containing all information.
        :rtype: list[ProtoMessageResponse]
        """
        f = Filter()
        if filter_by_nodes:
            f.node_names.extend(filter_by_nodes)
        if filter_by_resources:
            f.resource_names.extend(filter_by_resources)
        return self._send_and_wait(apiconsts.API_LST_RSC, f)

    def volume_list(self, filter_by_nodes=None, filter_by_stor_pools=None, filter_by_resources=None):
        """
        Request a list of all volumes known to the controller.

        :param list[str] filter_by_nodes: filter resources by nodes
        :param list[str] filter_by_stor_pools: filter resources by storage pool names
        :param list[str] filter_by_resources: filter resources by resource names
        :return: A MsgLstRsc proto message containing all information.
        :rtype: list[ProtoMessageResponse]
        """
        f = Filter()
        if filter_by_nodes:
            f.node_names.extend(filter_by_nodes)
        if filter_by_stor_pools:
            f.stor_pool_names.extend(filter_by_stor_pools)
        if filter_by_resources:
            f.resource_names.extend(filter_by_resources)
        return self._send_and_wait(apiconsts.API_LST_VLM, f)

    def controller_props(self):
        """
        Request a list of all controller properties.

        :return: A MsgLstCtrlCfgProps proto message containing all controller props.
        :rtype: list
        """
        return self._send_and_wait(apiconsts.API_LST_CFG_VAL)

    @classmethod
    def _split_prop_key(cls, fkey):
        key = fkey
        namespace = None
        ns_pos = key.rfind('/')
        if ns_pos >= 0:
            namespace = key[:ns_pos]
            key = key[ns_pos + 1:]

        return key, namespace

    def controller_set_prop(self, key, value):
        """
        Sets a property on the controller.

        :param str key: Key of the property.
        :param str value:  New Value of the property.
        :return: A list containing ApiCallResponses from the controller.
        :rtype: list[ApiCallResponse]
        """
        msg = MsgSetCtrlCfgProp()
        msg.value = value
        split_key, ns = self._split_prop_key(key)
        msg.key = split_key
        if ns:
            msg.namespace = ns

        return self._send_and_wait(apiconsts.API_SET_CFG_VAL, msg)

    def controller_del_prop(self, key):
        """
        Deletes a property on the controller.

        :param key: Key of the property.
        :return: A list containing ApiCallResponses from the controller.
        :rtype: list[ApiCallResponse]
        """
        msg = MsgDelCtrlCfgProp()
        split_key, ns = self._split_prop_key(key)
        msg.key = split_key
        if ns:
            msg.namespace = ns

        return self._send_and_wait(apiconsts.API_DEL_CFG_VAL, msg)

    def controller_shutdown(self):
        """
        Sends a shutdown command to the controller.

        :return: A list containing ApiCallResponses from the controller.
        :rtype: list[ApiCallResponse]
        """
        msg = MsgControlCtrl()
        msg.command = apiconsts.API_CMD_SHUTDOWN
        return self._send_and_wait(apiconsts.API_CONTROL_CTRL, msg)

    def controller_info(self):
        """
        If connected this method returns the controller info string.

        :return: Controller info string or None if not connected.
        :rtype: str
        """
        return self._linstor_client.controller_info()

    def watch_create(self, watch_id, object_identifier):
        """
        Create watch for events from the controller.

        :param int watch_id: ID for watch
        :param ObjectIdentifier object_identifier: Object to subscribe for events
        :return: A list containing ApiCallResponses from the controller.
        :rtype: list[ApiCallResponse]
        """
        msg = MsgCrtWatch()
        msg.watch_id = watch_id
        object_identifier.write_to_create_watch_msg(msg)
        self._linstor_client.register_watch(watch_id)
        return self._send_and_wait(apiconsts.API_CRT_WATCH, msg)

    def _watch_delete(self, watch_id):
        """
        Delete watch for events from the controller.

        :param int watch_id: ID for watch
        :return: A list containing ApiCallResponses from the controller.
        :rtype: list[ApiCallResponse]
        """
        msg = MsgDelWatch()
        msg.watch_id = watch_id
        self._linstor_client.deregister_watch(watch_id)
        return self._send_and_wait(apiconsts.API_DEL_WATCH, msg)

    def watch_events(self, reply_handler, event_handler, object_identifier):
        """
        Create watch and process events from the controller.

        :param Callable[[ApiCallResponse], None] reply_handler: function that is called on the watch creation reply.
        :param Callable event_handler: function that is called if an event was received.
        :param ObjectIdentifier object_identifier: Object to subscribe for events
        :return: Return value of reply_handler or event_handler, when not None
        """
        watch_id = self._linstor_client.next_watch_id()
        try:
            replies = self.watch_create(watch_id, object_identifier)
            reply_handler_result = reply_handler(replies)
            if reply_handler_result is not None:
                return reply_handler_result

            return self._linstor_client.wait_for_events(watch_id, event_handler)
        finally:
            self._watch_delete(watch_id)

    def crypt_create_passphrase(self, passphrase):
        """
        Create a new crypt passphrase on the controller.

        :param passphrase: New passphrase.
        :return: A list containing ApiCallResponses from the controller.
        :rtype: list[ApiCallResponse]
        """
        msg = MsgCrtCryptPassphrase()
        msg.passphrase = passphrase
        return self._send_and_wait(apiconsts.API_CRT_CRYPT_PASS, msg)

    def crypt_enter_passphrase(self, passphrase):
        """
        Send the master passphrase to unlock crypted volumes.

        :param passphrase: Passphrase to send to the controller.
        :return: A list containing ApiCallResponses from the controller.
        :rtype: list[ApiCallResponse]
        """
        msg = MsgEnterCryptPassphrase()
        msg.passphrase = passphrase
        return self._send_and_wait(apiconsts.API_ENTER_CRYPT_PASS, msg)

    def crypt_modify_passphrase(self, old_passphrase, new_passphrase):
        """
        Modify the current crypt passphrase.

        :param old_passphrase: Old passphrase, need for decrypt current volumes.
        :param new_passphrase: New passphrase.
        :return: A list containing ApiCallResponses from the controller.
        :rtype: list[ApiCallResponse]
        """
        msg = MsgModCryptPassphrase()
        msg.old_passphrase = old_passphrase
        msg.new_passphrase = new_passphrase
        return self._send_and_wait(apiconsts.API_MOD_CRYPT_PASS, msg)

    def resource_conn_modify(self, rsc_name, node_a, node_b, property_dict, delete_props):
        """
        Modify properties of a resource connection.
        Identified by the resource name, node1 and node2 arguments.

        :param str rsc_name: Name of the resource.
        :param str node_a: Name of the first node.
        :param str node_b: Name of the second node.
        :param dict[str, str] property_dict: Dict containing key, value pairs for new values.
        :param list[str] delete_props: List of properties to delete
        :return: A list containing ApiCallResponses from the controller.
        :rtype: list[ApiCallResponse]
        """
        msg = MsgModRscConn()

        msg.rsc_name = rsc_name
        msg.node_1_name = node_a
        msg.node_2_name = node_b
        msg = self._modify_props(msg, property_dict, delete_props)
        return self._send_and_wait(apiconsts.API_MOD_RSC_CONN, msg)

    def snapshot_create(self, node_names, rsc_name, snapshot_name, async):
        """
        Create a snapshot.

        :param list[str] node_names: Names of the nodes.
        :param str rsc_name: Name of the resource.
        :param str snapshot_name: Name of the new snapshot.
        :param bool async: True to return without waiting for the action to complete on the satellites.
        :return: A list containing ApiCallResponses from the controller.
        :rtype: list[ApiCallResponse]
        """
        msg = MsgCrtSnapshot()

        for node_name in node_names:
            snapshot = msg.snapshot_dfn.snapshots.add()
            snapshot.node_name = node_name

        msg.snapshot_dfn.rsc_name = rsc_name
        msg.snapshot_dfn.snapshot_name = snapshot_name
        return self._watch_send_and_wait(
            apiconsts.API_CRT_SNAPSHOT,
            msg,
            async,
            apiconsts.EVENT_SNAPSHOT_DEPLOYMENT,
            ObjectIdentifier(resource_name=rsc_name, snapshot_name=snapshot_name)
        )

    def snapshot_volume_definition_restore(self, from_resource, from_snapshot, to_resource):
        """
        Create volume definitions from a snapshot.

        :param str from_resource: Name of the snapshot resource.
        :param str from_snapshot: Name of the snapshot.
        :param str to_resource: Name of the new resource.
        :return: A list containing ApiCallResponses from the controller.
        :rtype: list[ApiCallResponse]
        """
        msg = MsgRestoreSnapshotVlmDfn()
        msg.from_resource_name = from_resource
        msg.from_snapshot_name = from_snapshot
        msg.to_resource_name = to_resource
        return self._send_and_wait(apiconsts.API_RESTORE_VLM_DFN, msg)

    def snapshot_resource_restore(self, node_names, from_resource, from_snapshot, to_resource):
        """
        Restore from a snapshot.

        :param list[str] node_names: Names of the nodes.
        :param str from_resource: Name of the snapshot resource.
        :param str from_snapshot: Name of the snapshot.
        :param str to_resource: Name of the new resource.
        :return: A list containing ApiCallResponses from the controller.
        :rtype: list[ApiCallResponse]
        """
        msg = MsgRestoreSnapshotRsc()

        for node_name in node_names:
            node = msg.nodes.add()
            node.name = node_name

        msg.from_resource_name = from_resource
        msg.from_snapshot_name = from_snapshot
        msg.to_resource_name = to_resource
        return self._send_and_wait(apiconsts.API_RESTORE_SNAPSHOT, msg)

    def snapshot_delete(self, rsc_name, snapshot_name):
        """
        Delete a snapshot.

        :param str rsc_name: Name of the resource.
        :param str snapshot_name: Name of the snapshot.
        :return: A list containing ApiCallResponses from the controller.
        :rtype: list[ApiCallResponse]
        """
        msg = MsgDelSnapshot()

        msg.rsc_name = rsc_name
        msg.snapshot_name = snapshot_name
        return self._send_and_wait(apiconsts.API_DEL_SNAPSHOT, msg)

    def snapshot_dfn_list(self):
        """
        Request a list of all snapshot definitions known to the controller.

        :return: A MsgLstSnapshotDfn proto message containing all information.
        :rtype: list[ProtoMessageResponse]
        """
        return self._send_and_wait(apiconsts.API_LST_SNAPSHOT_DFN)

    def error_report_list(self, nodes=None, with_content=False, since=None, to=None, ids=None):
        """
        Retrieves an error report list from the controller.

        :param list[str] nodes: Nodes to filter, if None all
        :param bool with_content: If true the full log content will be retrieved
        :param datetime since: Start datetime from when to include, if None all
        :param datetime to: Until datetime to include error reports, if None all
        :param list[str] ids: Ids there string starts with to include, if None all
        :return: A list containing ErrorReport from the controller.
        :rtype: list[ErrorReport]
        """
        msg = MsgReqErrorReport()
        for node in nodes if nodes else []:
            msg.node_names.extend([node])
        msg.with_content = with_content
        if since:
            msg.since = int(time.mktime(since.timetuple()) * 1000)
        if to:
            msg.to = int(time.mktime(to.timetuple()) * 1000)
        if ids:
            msg.ids.extend(ids)
        return self._send_and_wait(apiconsts.API_REQ_ERROR_REPORTS, msg, allow_no_reply=True)

    def hostname(self):
        """
        Sends an hostname request and should return the `uname -n` output.
        This is a call that is actually used if connected to a satellite.

        :return: List containing 1 MsgHostname proto
        :rtype: list[ProtoMsgResponse]
        """
        return self._send_and_wait(apiconsts.API_HOSTNAME)

    def ping(self):
        """
        Sends a ping message to the controller.

        :return: Message id used for this message
        :rtype: int
        """
        return self._linstor_client.send_msg(apiconsts.API_PING, oneway=True)

    def wait_for_message(self, api_call_id):
        """
        Wait for a message from the controller.

        :param int api_call_id: Message id to wait for.
        :return: A list containing ApiCallResponses from the controller.
        :rtype: list[ApiCallResponse]
        """
        return self._linstor_client.wait_for_result(api_call_id)

    def stats(self):
        """
        Returns a printable string containing network statistics.

        :return: A string containing network stats.s
        :rtype: str
        """
        return self._linstor_client.stats()


if __name__ == "__main__":
    lin = Linstor("linstor://127.0.0.1")
    lin.connect()
    id = lin.ping()
    print(id)
    lin.wait_for_message(id)

    #print(lin.node_create('testnode', apiconsts.VAL_NODE_TYPE_STLT, '10.0.0.1'))
    for x in range(1, 20):
        print(lin.node_create('testnode' + str(x), apiconsts.VAL_NODE_TYPE_STLT, '10.0.0.' + str(x)))

    for x in range(1, 20):
        print(lin.node_delete('testnode' + str(x)))
    # replies = lin.storage_pool_list()
    # print(replies)
    # print(lin.list_nodes())
    # print(lin.list_resources())
