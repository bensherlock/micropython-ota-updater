#! /usr/bin/env python
#
# MicroPython OTA Updater
#
# This file is part of micropython-ota-updater.
# https://github.com/bensherlock/micropython-ota-updater
#
# This file was forked and modified from the work by rdehuyss at:
# https://github.com/rdehuyss/micropython-ota-updater
# Copyright (c) 2018 rdehuyss https://github.com/rdehuyss
#
# MIT License
# Copyright (c) 2020 Benjamin Sherlock <benjamin.sherlock@ncl.ac.uk>
#
# Permission is hereby granted, free of charge, to any person obtaining a copy
# of this software and associated documentation files (the "Software"), to deal
# in the Software without restriction, including without limitation the rights
# to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
# copies of the Software, and to permit persons to whom the Software is
# furnished to do so, subject to the following conditions:
#
# The above copyright notice and this permission notice shall be included in all
# copies or substantial portions of the Software.
#
# THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
# IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
# FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
# AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
# LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
# OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
# SOFTWARE.
#
"""MicroPython OTA Updater."""

import usocket
import os
import gc


class OTAUpdater:
    """OTA Updater for a given module."""

    def __init__(self, github_repo, module='', main_dir='main'):
        """Initialise with url to the github repo, the module, and the main directory within the module."""
        self._http_client = HttpClient()
        self._github_repo = github_repo.rstrip('/').replace('https://github.com', 'https://api.github.com/repos')
        self._main_dir = main_dir
        self._module = module.rstrip('/')

    def __call__(self):
        return self

    @staticmethod
    def using_network(ssid, password):
        """Connects to the wifi with the given ssid and password."""
        import network
        sta_if = network.WLAN(network.STA_IF)
        if not sta_if.isconnected():
            print('connecting to network...')
            sta_if.active(True)
            sta_if.connect(ssid, password)
            while not sta_if.isconnected():
                # Check the status
                status = sta_if.status()
                # Constants aren't implemented for PYBD as of MicroPython v1.13.
                # From: https://github.com/micropython/micropython/issues/4682
                # 'So "is-connecting" is defined as s.status() in (1, 2) and "is-connected" is defined as s.status() == 3.'
                #
                if status <= 0:
                    # Error States?
                    return False
                #if ((status == network.WLAN.STAT_IDLE) or (status == network.WLAN.STAT_WRONG_PASSWORD)
                #        or (status == network.WLAN.STAT_NO_AP_FOUND) or (status == network.WLAN.STAT_CONNECT_FAIL)):
                    # Problems so return
                #    return False

        print('network config:', sta_if.ifconfig())
        return True

    def download_updates_if_available(self):
        """Downloads available updates and leaves them in the 'next' directory alongside the 'main' directory."""
        current_version = self.get_version(self.get_module_and_path(self._main_dir))
        latest_version = self.get_latest_version()

        print('Checking version... ')
        print('\tCurrent version: ', current_version)
        print('\tLatest version: ', latest_version)

        if not latest_version:
            return False

        if (not current_version) or (latest_version > current_version):
            print('Updating...')
            if not self.path_exists(self._module):
                os.mkdir(self._module)
            os.mkdir(self.get_module_and_path('next'))
            self.download_all_files(self._github_repo + '/contents/' + self._main_dir, latest_version)
            with open(self.get_module_and_path('next/.version'), 'w') as versionfile:
                versionfile.write(latest_version)
                versionfile.close()

            return True
        return False

    def apply_pending_updates_if_available(self):
        """Checks for 'next' directory and version number and overwrites the current 'main' directory."""
        if self.path_exists(self._module) and 'next' in os.listdir(self._module):
            if '.version' in os.listdir(self.get_module_and_path('next')):
                pending_update_version = self.get_version(self.get_module_and_path('next'))
                print('Pending update found: ', pending_update_version)
                if self.path_exists(self.get_module_and_path(self._main_dir)):
                    self.rmtree(self.get_module_and_path(self._main_dir))  # Remove the 'main' directory and contents.
                os.rename(self.get_module_and_path('next'), self.get_module_and_path(self._main_dir))  # Move the 'next' to 'main'
                print('Update applied (', pending_update_version, '), ready to rock and roll')
            else:
                print('Corrupt pending update found, discarding...')
                self.rmtree(self.get_module_and_path('next'))
        else:
            print('No pending update found')

    def rmtree(self, directory):
        """Remove the directory tree."""
        for entry in os.ilistdir(directory):
            is_dir = (entry[1] == 0x4000)  # 0x4000 for directories and 0x8000 for regular files
            if is_dir:
                self.rmtree(directory + '/' + entry[0])  # Recurse into subdirectory
            else:
                os.remove(directory + '/' + entry[0])  # Remove this object
        os.rmdir(directory)  # Remove the now empty directory.

    def get_version(self, directory, version_file_name='.version'):
        """Get the current installed version.
        Returns the version or None if no version file exists."""
        if self.path_exists(directory) and (version_file_name in os.listdir(directory)):
            f = open(directory + '/' + version_file_name)
            version = f.read()
            f.close()
            return version
        return None

    def get_latest_version(self):
        """Get the latest release version information from the github repo url.
        Returns the version or None if no releases exist."""
        latest_release = self._http_client.get(self._github_repo + '/releases/latest')
        if not 'tag_name' in latest_release.json():
            return None
        version = latest_release.json()['tag_name']
        latest_release.close()
        return version

    def download_all_files(self, root_url, version):
        """Download all files and directories from the version at the repo url below the 'main' directory."""
        file_list = self._http_client.get(root_url + '?ref=refs/tags/' + version)
        for file in file_list.json():
            if file['type'] == 'file':
                download_url = file['download_url']
                download_path = self.get_module_and_path('next/' + file['path'].replace(self._main_dir + '/', ''))
                self.download_file(download_url.replace('refs/tags/', ''), download_path)
            elif file['type'] == 'dir':
                path = self.get_module_and_path('next/' + file['path'].replace(self._main_dir + '/', ''))
                os.mkdir(path)
                self.download_all_files(root_url + '/' + file['name'], version)  # Recurse into the subdirectory.

        file_list.close()

    def download_file(self, url, path):
        """Download file from the url to the given path."""
        print('\tDownloading: ', path)
        with open(path, 'w') as outfile:
            try:
                response = self._http_client.get(url)
                outfile.write(response.text)
            finally:
                response.close()
                outfile.close()
                gc.collect()

    def get_module_and_path(self, path):
        """Get the combined path of module and the provided path appended."""
        return self._module + '/' + path if self._module else path

    def path_exists(self, path):
        """Test if path exists. Returns True if found."""
        try:
            os.stat(path)
        except OSError:
            return False
        return True

class Response:
    """HTTP Response."""

    def __init__(self, f):
        self.raw = f
        self.encoding = 'utf-8'
        self._cached = None

    def close(self):
        if self.raw:
            self.raw.close()
            self.raw = None
        self._cached = None

    @property
    def content(self):
        if self._cached is None:
            try:
                self._cached = self.raw.read()
            finally:
                self.raw.close()
                self.raw = None
        return self._cached

    @property
    def text(self):
        return str(self.content, self.encoding)

    def json(self):
        import ujson
        return ujson.loads(self.content)


class HttpClient:
    """HTTP Client."""

    def request(self, method, url, data=None, json=None, headers={}, stream=None):
        try:
            proto, dummy, host, path = url.split('/', 3)
        except ValueError:
            proto, dummy, host = url.split('/', 2)
            path = ''
        if proto == 'http:':
            port = 80
        elif proto == 'https:':
            import ussl
            port = 443
        else:
            raise ValueError('Unsupported protocol: ' + proto)

        if ':' in host:
            host, port = host.split(':', 1)
            port = int(port)

        ai = usocket.getaddrinfo(host, port, 0, usocket.SOCK_STREAM)
        ai = ai[0]

        s = usocket.socket(ai[0], ai[1], ai[2])
        try:
            # Set Timeout (in seconds) to make it non-blocking.
            s.settimeout(5)
            s.connect(ai[-1])
            if proto == 'https:':
                s = ussl.wrap_socket(s, server_hostname=host)
            s.write(b'%s /%s HTTP/1.0\r\n' % (method, path))
            if not 'Host' in headers:
                s.write(b'Host: %s\r\n' % host)
            # Iterate over keys to avoid tuple alloc
            for k in headers:
                s.write(k)
                s.write(b': ')
                s.write(headers[k])
                s.write(b'\r\n')
            # add user agent
            s.write('User-Agent')
            s.write(b': ')
            s.write('MicroPython OTAUpdater')
            s.write(b'\r\n')
            if json is not None:
                assert data is None
                import ujson
                data = ujson.dumps(json)
                s.write(b'Content-Type: application/json\r\n')
            if data:
                s.write(b'Content-Length: %d\r\n' % len(data))
            s.write(b'\r\n')
            if data:
                s.write(data)

            l = s.readline()
            # print(l)
            l = l.split(None, 2)
            status = int(l[1])
            reason = ''
            if len(l) > 2:
                reason = l[2].rstrip()
            while True:
                l = s.readline()
                if not l or l == b'\r\n':
                    break
                # print(l)
                if l.startswith(b'Transfer-Encoding:'):
                    if b'chunked' in l:
                        raise ValueError('Unsupported ' + l)
                elif l.startswith(b'Location:') and not 200 <= status <= 299:
                    raise NotImplementedError('Redirects not yet supported')
        except OSError:
            s.close()
            raise

        resp = Response(s)
        resp.status_code = status
        resp.reason = reason
        return resp

    def head(self, url, **kw):
        return self.request('HEAD', url, **kw)

    def get(self, url, **kw):
        return self.request('GET', url, **kw)

    def post(self, url, **kw):
        return self.request('POST', url, **kw)

    def put(self, url, **kw):
        return self.request('PUT', url, **kw)

    def patch(self, url, **kw):
        return self.request('PATCH', url, **kw)

    def delete(self, url, **kw):
        return self.request('DELETE', url, **kw)
