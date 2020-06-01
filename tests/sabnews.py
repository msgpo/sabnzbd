#!/usr/bin/python3 -OO
# Copyright 2007-2020 The SABnzbd-Team <team@sabnzbd.org>
#
# This program is free software; you can redistribute it and/or
# modify it under the terms of the GNU General Public License
# as published by the Free Software Foundation; either version 2
# of the License, or (at your option) any later version.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with this program; if not, write to the Free Software
# Foundation, Inc., 51 Franklin Street, Fifth Floor, Boston, MA  02110-1301, USA.

"""
tests.sabnews - Fake newsserver to use in end-to-end testing
"""

import os
import re
import zlib
import asyncio
import logging


# Expecting the following message-id:
# ARTICLE <file=folder/filename.mkv|part=4|start=5000|size=5000>\r\n
ARTICLE_INFO = re.compile(
    b"^(ARTICLE|BODY) (?P<message_id><file=(?P<file>.*)\|part=(?P<part>\d+)\|start=(?P<start>\d+)\|size=(?P<size>\d+)>)\\r\\n$",
    re.MULTILINE,
)
YENC_ESCAPE = [0x00, 0x0A, 0x0D, ord("="), ord(".")]


class NewsServerProtocol(asyncio.Protocol):
    def __init__(self):
        self.transport = None
        self.connected = False
        self.in_article = False
        super().__init__()

    def connection_made(self, transport):
        logging.info("Connection from %s", transport.get_extra_info("peername"))
        self.transport = transport
        self.connected = True
        self.transport.write(b"200 Welcome (SABNews)\r\n")

    def data_received(self, message):
        logging.debug("Data received: %s", message.strip())

        # Handle basic commands
        if message.startswith(b"QUIT"):
            self.close_connection()
        elif message.startswith((b"ARTICLE", b"BODY")):
            parsed_message = ARTICLE_INFO.search(message)
            self.serve_article(parsed_message)

        # self.transport.write(data)

    def serve_article(self, parsed_message):
        # Check if we parsed everything
        try:
            message_id = parsed_message.group("message_id")
            file = parsed_message.group("file")
            file_base = os.path.basename(file)
            part = int(parsed_message.group("part"))
            start = int(parsed_message.group("start"))
            size = int(parsed_message.group("size"))
        except (AttributeError, ValueError):
            logging.info("Can't parse article information")
            self.transport.write(b"430 No Such Article Found (bad message-id)\r\n")
            return

        # Check if file exists
        if not os.path.exists(file):
            logging.info("File not found: %s", file)
            self.transport.write(b"430 No Such Article Found (no file on disk)\r\n")
            return

        # Check if sizes are valid
        file_size = os.path.getsize(file)
        if start + size > file_size:
            logging.info("Invalid start/size attributes")
            self.transport.write(b"430 No Such Article Found (invalid start/size attributes)\r\n")
            return

        # File is found, send headers
        self.transport.write(b"222 0 %s\r\n" % message_id)
        self.transport.write(b"Message-ID: %s\r\n" % message_id)
        self.transport.write(b'Subject: "%s"\r\n\r\n' % file_base)

        # Write yEnc headers
        self.transport.write(b"=ybegin part=%d line=128 size=%d name=%s\r\n" % (part, file_size, file_base))
        self.transport.write(b"=ypart begin=%d end=%d\r\n" % (start + 1, start + size))

        with open(file, "rb") as inp_file:
            inp_file.seek(start)
            inp_buffer = inp_file.read(size)

        # Calculate CRC of input
        crc = zlib.crc32(inp_buffer) & 0xFFFFFFFF

        # yEnc-encoder
        line_size = 0
        crc = 0
        for ch in inp_buffer:
            # Write special chars
            out_ch = (ch + 42) % 256
            if out_ch in YENC_ESCAPE:
                self.transport.write(b"=")
                line_size += 1
                out_ch = (out_ch + 64) % 256

            # Write regular chars
            self.transport.write(bytes([out_ch]))
            line_size += 1

            # Check line-size
            if line_size and not line_size >= 128:
                self.transport.write(b"\r\n")
                line_size = 0

        # Write footer
        self.transport.write(b"=yend size=%d part=%d pcrc32=%08x\r\n" % (size, part, crc))

    def close_connection(self):
        logging.debug("Closing connection")
        self.transport.write(b"205 Connection closing\r\n")
        self.transport.close()


async def main():
    # Get parameters
    hostname = "127.0.0.1"
    port = 8888

    # Start server
    logging.getLogger().setLevel(logging.DEBUG)
    logging.info("Starting SABNews on %s:%d", hostname, port)
    loop = asyncio.get_running_loop()

    server = await loop.create_server(lambda: NewsServerProtocol(), hostname, port)

    async with server:
        await server.serve_forever()


asyncio.run(main())
