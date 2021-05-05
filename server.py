#!/usr/bin/env python

import os
import socket
import struct
import sys
from threading import Lock, Thread


QUEUE_LENGTH = 10
SEND_BUFFER = 4096


class DASHServer:
    """
    The __init__ method sets up the DASH server in the following steps:
        - it identifies all of the .mp3 files specified at 'music_path' and stores them in 'self.all_mp3s'
        - it creates an dictionary mapping song names to empty lists called 'self.songs_data'
        - it reads each .mp3 file into chunks of size 'chunk_size' bytes (1024 by default) and appends each chunk to the
            list in 'self.songs_data' mapped to that song
    """
    def __init__(self, music_path, chunk_size=SEND_BUFFER-100): # -100 for rest of message
        print('initializing DASH server')
        self.all_mp3s = [s for s in os.listdir(music_path) if s.endswith('.mp3') and os.path.isfile(os.path.join(music_path, s))]
        self.songs_data = {song_name: [] for song_name in self.all_mp3s}
        for song_name in self.all_mp3s:
            i = 0
            print('chunking song: ' + song_name)
            with open(os.path.join(music_path, song_name), 'rb') as f:
                chunk_data = f.read(chunk_size)
                while chunk_data:
                    i += 1
                    self.songs_data[song_name].append(str(chunk_data))
                    chunk_data = f.read(chunk_size)

    """
    The get_song_chunk method takes a song name and a chunk num (stored in each client object) and returns the data for
    that chunk

    Returns are of the form status, data
    Statuses:
        -1: invalid song name (no data)
        0: still playing song (valid data)
        1: finished song (no data)
    """
    def get_song_chunk(self, song_num, chunk_num):
        song_name = 'NO_SONG'
        for i in self.get_song_list().split('\n'):
            if str(song_num) + ': ' in i:
                song_name = i.split(': ')[1]
                break

        if song_name not in self.songs_data:
            return -1, None
        elif chunk_num >= len(self.songs_data[song_name]) or chunk_num < 0:
            return 1, None
        else:
            return 0, self.songs_data[song_name][chunk_num]

    def song_exists(self, song_name):
        if not song_name.endswith('.mp3'):
            song_name += '.mp3'
        return song_name in self.songs_data

    def get_song_list(self):
        songs = ''
        for i, song_name in enumerate(self.all_mp3s):
            songs += '{0}: {1}'.format(i, song_name)
            if i != len(self.all_mp3s) - 1:
                songs += '\n'
        return songs


# per-client struct
class Client:
    def __init__(self, id):
        self.id = id
        self.lock = Lock()
        self.current_song = None
        self.current_chunk = 0
        self.status = 'PAUSED'  # Paused, Playing
        self.unexecuted_commands = []
        self.connected = True


# TODO: Thread that sends music and lists to the client.  All send() calls
# should be contained in this function.  Control signals from client_read could
# be passed to this thread through the associated Client object.  Make sure you
# use locks or similar synchronization tools to ensure that the two threads play
# nice with one another!
def client_write(client, sock, dash_server):
    while True:
        client.lock.acquire()
        if not client.connected:
            exit(0)

        # first, send song if playing, then handle outstanding commands
        if client.status == 'PLAYING' and len(client.unexecuted_commands) == 0:
            dash_status, dash_data = dash_server.get_song_chunk(client.current_song, client.current_chunk)
            if dash_status == 0: # this means still playing song (valid data)
                client.current_chunk += 1
                streaming_payload = 'DATA%%%%%' + client.current_song + '%%%%%' + dash_data
            else:  # only code 1 (finished song) should be possible here
                streaming_payload = 'FINI%%%%%' + client.current_song + '%%%%%_'
                client.current_song = None
                client.status = 'PAUSED'

            # make sure to send data even if timeout (idk why it would time out here but it does)
            while True:
                try:
                    pack_size = str(len(streaming_payload))
                    padding_length = 10 - len(pack_size)
                    pack_size = padding_length * '0' + pack_size
                    payload = pack_size + streaming_payload
                    sock.send(payload)
                    break
                except socket.error as e:
                    pass

        # now handle commands
        payload = None
        for command_type, song_name, data in client.unexecuted_commands:
            if command_type == 'PLAY':
                print('playing song', int(song_name))
                if 0 <= int(song_name) < len(dash_server.all_mp3s):
                    client.status = 'PLAYING'
                    client.current_song = song_name
                    client.current_chunk = 0
                else:
                    payload = 'ERRO%%%%%_%%%%%invalid song name'

            elif command_type == 'STOP':
                client.status = 'PAUSED'

            elif command_type == 'LIST':
                payload = 'MP3S%%%%%_%%%%%' + dash_server.get_song_list()

            if payload is not None:
                pack_size = str(len(payload))
                padding_length = 10 - len(pack_size)
                pack_size = padding_length * '0' + pack_size
                payload = pack_size + payload
                sock.send(payload)

        client.unexecuted_commands = []
        client.lock.release()


# Thread that receives commands from the client.  All recv() calls should
# be contained in this function.
def client_read(client, sock):
    # just update client object here and then send based on that later
    sock.settimeout(0.05)
    while True: # keep reading messages
        command_string = 'NONE%%%%%_%%%%%_'
        try:
            command_string = sock.recv(SEND_BUFFER)
        except socket.error as e:  # timeout
            command_string = 'NONE%%%%%_%%%%%_'  # fake command that says keep playing

        if type(command_string) != str:
            command_string = command_string.decode("utf-8")

        try:
            command_type, song_name, data = command_string.split('%%%%%')
        except ValueError:
            print('Client ID {0} has disconnected'.format(client.id))
            client.lock.acquire()
            client.connected = False
            client.lock.release()
            exit(0)

        # update client object based on message
        client.lock.acquire()
        if command_type != 'NONE':
            client.unexecuted_commands.append((command_type, song_name, data))
        client.lock.release()


def main():
    if len(sys.argv) != 3:
        sys.exit("Usage: python server.py [port] [musicdir]")
    if not os.path.isdir(sys.argv[2]):
        sys.exit("Directory '{0}' does not exist".format(sys.argv[2]))

    port = int(sys.argv[1])
    threads = []

    dash_server = DASHServer(sys.argv[2])

    addr_info = socket.getaddrinfo(None, sys.argv[1], socket.AF_INET, socket.SOCK_STREAM, 0, socket.AI_PASSIVE)[0]
    first_socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    first_socket.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1) # reuse socket
    first_socket.bind(addr_info[4])
    first_socket.listen(QUEUE_LENGTH)
    print('listening for connections')

    # Create a socket and accept incoming connections
    next_client_id = 0
    while True:
        new_socket, address = first_socket.accept()
        print('Accepted new connection from client ID {0}'.format(next_client_id))
        client = Client(next_client_id)
        next_client_id += 1
        t = Thread(target=client_read, args=(client, new_socket))
        t.daemon = True  # this should let is ctrl-c
        threads.append(t)
        t.start()
        t = Thread(target=client_write, args=(client, new_socket, dash_server))
        t.daemon = True  # this should let is ctrl-c
        threads.append(t)
        t.start()
    s.close()


if __name__ == "__main__":
    main()
