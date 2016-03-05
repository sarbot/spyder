# -*- coding: utf-8 -*-
#
# Copyright © 2016 The Spyder development team
# Licensed under the terms of the MIT License
# (see spyderlib/__init__.py for details)

import threading
import socket
import errno
import os
import sys
import time
import atexit

# Local imports
from spyderlib.utils.introspection.utils import connect_to_port
from spyderlib.py3compat import Queue
from spyderlib.utils.bsdsocket import read_packet, write_packet


# Timeout in seconds
TIMEOUT = 60


class PluginServer(object):

    """
    Introspection plugin server, provides a separate process
    for interacting with the plugin.
    """

    def __init__(self, client_port, plugin_name):
        mod_name = plugin_name + '_plugin'
        mod = __import__('spyderlib.utils.introspection.' + mod_name,
                         fromlist=[mod_name])
        cls = getattr(mod, '%sPlugin' % plugin_name.capitalize())
        plugin = cls()
        plugin.load_plugin()
        self.tlast = time.time()
        self.plugin = plugin

        self._client_port = int(client_port)
        sock, self.server_port = connect_to_port()
        sock.listen(2)
        atexit.register(sock.close)
        self._server_sock = sock

        self.queue = Queue.Queue()
        self._listener = threading.Thread(target=self.listen)
        self._listener.setDaemon(True)
        self._listener.start()

        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.connect(("127.0.0.1", self._client_port))
        write_packet(sock, self.server_port)
        sock.close()

    def listen(self):
        """Listen for requests"""
        while True:
            try:
                conn, _addr = self._server_sock.accept()
            except socket.error as e:
                badfd = errno.WSAEBADF if os.name == 'nt' else errno.EBADF
                extra = errno.WSAENOTSOCK if os.name == 'nt' else badfd
                if e.args[0] in [errno.ECONNABORTED, badfd, extra]:
                    return
                # See Issue 1275 for details on why errno EINTR is
                # silently ignored here.
                eintr = errno.WSAEINTR if os.name == 'nt' else errno.EINTR
                if e.args[0] == eintr:
                    continue
                raise
            self.queue.put(read_packet(conn))

    def run(self):
        """Handle requests"""
        while 1:
            # Get most recent request
            request = None
            while 1:
                try:
                    request = self.queue.get(True, 0.01)
                except Queue.Empty:
                    break
            if request is None:
                if time.time() - self.tlast > TIMEOUT:
                    sys.exit('Program timed out')
                continue
            self.tlast = time.time()
            try:
                method = getattr(self.plugin, request['method'])
                args = request.get('args', [])
                kwargs = request.get('kwargs', {})
                request['result'] = method(*args, **kwargs)
            except Exception as e:
                request['error'] = str(e)
            sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            sock.connect(("127.0.0.1", self._client_port))
            write_packet(sock, request)
            sock.close()


if __name__ == '__main__':
    args = sys.argv[1:]
    if not len(args) == 2:
        print('Usage: plugin_server.py client_port plugin_name')
        sys.exit(0)
    plugin = PluginServer(*args)
    print('Started')
    plugin.run()