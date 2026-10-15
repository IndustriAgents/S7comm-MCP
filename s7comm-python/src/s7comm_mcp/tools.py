"""Compatibility imports; implementation lives in domain, application and protocol modules."""

import functools as functools
import inspect as inspect
import json as json
import logging as logging
import math as math
import struct as struct
from collections.abc import Awaitable as Awaitable
from collections.abc import Callable as Callable
from collections.abc import Mapping as Mapping
from dataclasses import dataclass as dataclass
from pathlib import Path as Path
from typing import Any as Any

from pydantic import StrictInt as StrictInt

from s7comm_mcp.application.helpers import execute_write as execute_write
from s7comm_mcp.application.helpers import precheck_write as precheck_write
from s7comm_mcp.application.helpers import read_value as read_value
from s7comm_mcp.application.resources import ToolResources as ToolResources
from s7comm_mcp.config import env_bool as env_bool
from s7comm_mcp.config import env_str as env_str
from s7comm_mcp.domain.errors import MAX_BYTE_OFFSET as MAX_BYTE_OFFSET
from s7comm_mcp.domain.errors import OperationMeta as OperationMeta
from s7comm_mcp.domain.errors import S7ClientError as S7ClientError
from s7comm_mcp.domain.errors import describe_error as describe_error
from s7comm_mcp.domain.model import AREA_ALIASES as AREA_ALIASES
from s7comm_mcp.domain.model import FLOAT32_MAX as FLOAT32_MAX
from s7comm_mcp.domain.model import INPUTS_READ_ONLY as INPUTS_READ_ONLY
from s7comm_mcp.domain.model import INT_RANGES as INT_RANGES
from s7comm_mcp.domain.model import MAX_BATCH as MAX_BATCH
from s7comm_mcp.domain.model import MAX_IO_BYTES as MAX_IO_BYTES
from s7comm_mcp.domain.model import NOT_SENT as NOT_SENT
from s7comm_mcp.domain.model import STRING_MAX_CHARS as STRING_MAX_CHARS
from s7comm_mcp.domain.model import SUPPORTED_TYPES as SUPPORTED_TYPES
from s7comm_mcp.domain.model import TYPE_SIZES as TYPE_SIZES
from s7comm_mcp.domain.model import WRITE_TOOLS as WRITE_TOOLS
from s7comm_mcp.domain.model import WRITES_DISABLED as WRITES_DISABLED
from s7comm_mcp.domain.model import Address as Address
from s7comm_mcp.domain.model import Envelope as Envelope
from s7comm_mcp.domain.model import PreparedWrite as PreparedWrite
from s7comm_mcp.domain.model import TagMapError as TagMapError
from s7comm_mcp.domain.model import _error_text as _error_text
from s7comm_mcp.domain.model import _float32_shortest as _float32_shortest
from s7comm_mcp.domain.model import _pick as _pick
from s7comm_mcp.domain.model import address_from_spec as address_from_spec
from s7comm_mcp.domain.model import check_string_header as check_string_header
from s7comm_mcp.domain.model import decode_s7_string as decode_s7_string
from s7comm_mcp.domain.model import decode_scalar as decode_scalar
from s7comm_mcp.domain.model import encode_scalar as encode_scalar
from s7comm_mcp.domain.model import encode_string_text as encode_string_text
from s7comm_mcp.domain.model import envelope as envelope
from s7comm_mcp.domain.model import fail as fail
from s7comm_mcp.domain.model import int_arg as int_arg
from s7comm_mcp.domain.model import logger as logger
from s7comm_mcp.domain.model import make_address as make_address
from s7comm_mcp.domain.model import normalize_area as normalize_area
from s7comm_mcp.domain.model import normalize_dtype as normalize_dtype
from s7comm_mcp.domain.model import ok as ok
from s7comm_mcp.domain.model import parse_bool as parse_bool
from s7comm_mcp.domain.model import prepare_write as prepare_write
from s7comm_mcp.domain.model import raw_bytes as raw_bytes
from s7comm_mcp.domain.model import write_outcome as write_outcome
from s7comm_mcp.infrastructure.maps import TagMap as TagMap
from s7comm_mcp.infrastructure.tool_config import ToolConfig as ToolConfig
from s7comm_mcp.s7_client import S7Client as S7Client

from .protocol.support import READ_ONLY_HINTS as READ_ONLY_HINTS
from .protocol.support import WRITE_HINTS as WRITE_HINTS
from .protocol.support import FastMCP as FastMCP
from .protocol.support import ToolAnnotations as ToolAnnotations
from .protocol.support import mcp_request_cancel_scope as mcp_request_cancel_scope
from .protocol.tools import register_tools as register_tools
