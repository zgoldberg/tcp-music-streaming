#!/usr/bin/env python

import ao
import mad
import readline
import socket
import struct
import sys
import threading
from time import sleep
import time


SEND_BUFFER = 4096

# The Mad audio library we're using expects to be given a file object, but
# we're not dealing with files, we're reading audio data over the network.  We
# use this object to trick it.  All it really wants from the file object is the
# read() method, so we create this wrapper with a read() method for it to
# call, and it won't know the difference.
# NOTE: You probably don't need to modify this class.
class mywrapper(object):
    def __init__(self):
        self.mf = None
        self.data = ""
        self.song_name = ''
        self.stopped = True
        self.lock = threading.Lock()

    # When it asks to read a specific size, give it that many bytes, and
    # update our remaining data.
    def read(self, size):
        result = self.data[:size]
        self.data = self.data[size:]
        return result


# Receive messages.  If they're responses to info/list, print
# the results for the user to see.  If they contain song data, the
# data needs to be added to the wrapper object.  Be sure to protect
# the wrapper with synchronization, since the other thread is using
# it too!
def recv_thread_func(wrap, cond_filled, sock):
    while True:
        cond_filled.acquire()

        try:
            size = int(sock.recv(10))
            recieved_message = ''
            while len(recieved_message) < size:
                recieved_message += sock.recv(size - len(recieved_message))
        except socket.error as e:
            cond_filled.release()
            continue

        try:
            command_type, song_name, recieved_data = recieved_message.split('%%%%%')
        except ValueError:
            cond_filled.release()
            continue

        if command_type == 'DATA':
            if song_name == wrap.song_name:
                wrap.data += recieved_data
            else:
                wrap.data = recieved_data
                wrap.song_name = song_name
        elif command_type == 'MP3S':
            print('The available songs are...')
            print(recieved_data)
        elif command_type == 'ERRO':
            print('Error message recieved: ' + recieved_data)

        cond_filled.release()


# If there is song data stored in the wrapper object, play it!
# Otherwise, wait until there is.  Be sure to protect your accesses
# to the wrapper with synchronization, since the other thread is
# using it too!
def play_thread_func(wrap, cond_filled, dev):
    while True:
        if len(wrap.data) > 0 and not wrap.stopped:
            buf = None
            while buf is None:
                buf = wrap.mf.read()
            buf_buf = buffer(buf)
            dev.play(buf_buf, len(buf))


def main():
    if len(sys.argv) < 3:
        print 'Usage: %s <server name/ip> <server port>' % sys.argv[0]
        sys.exit(1)

    # Create a pseudo-file wrapper, condition variable, and socket.  These will
    # be passed to the thread we're about to create.
    wrap = mywrapper()

    # Create a condition variable to synchronize the receiver and player threads.
    # In python, this implicitly creates a mutex lock too.
    # See: https://docs.python.org/2/library/threading.html#condition-objects
    cond_filled = threading.Condition()

    # Create a TCP socket and try connecting to the server.
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.connect((sys.argv[1], int(sys.argv[2])))

    # Create a thread whose job is to receive messages from the server.
    recv_thread = threading.Thread(
        target=recv_thread_func,
        args=(wrap, cond_filled, sock)
    )
    recv_thread.daemon = True
    recv_thread.start()

    # Create a thread whose job is to play audio file data.
    dev = ao.AudioDevice('pulse')
    wrap.mf = mad.MadFile(wrap)  # we did this

    play_thread = threading.Thread(
        target=play_thread_func,
        args=(wrap, cond_filled, dev)
    )
    play_thread.daemon = True
    play_thread.start()

    # Enter our never-ending user I/O loop.  Because we imported the readline
    # module above, raw_input gives us nice shell-like behavior (up-arrow to
    # go backwards, etc.).
    while True:
        line = raw_input('>> ')

        if ' ' in line:
            cmd, args = line.split(' ', 1)
        else:
            cmd = line

        # Send messages to the server when the user types things.
        if cmd in ['l', 'list']:
            print 'The user asked for list.'
            payload = 'LIST%%%%%_%%%%%_'

        if cmd in ['p', 'play']:
            print 'The user asked to play:', args
            payload = 'PLAY%%%%%' + args + '%%%%%_'
            wrap.stopped = False

        if cmd in ['s', 'stop']:
            print 'The user asked for stop.'
            payload = 'STOP%%%%%_%%%%%_'
            wrap.stopped = True
            wrap.data = ''

        if cmd in ['quit', 'q', 'exit']:
            sys.exit(0)

        sock.send(payload)
        time.sleep(0.2)
        line = ''

if __name__ == '__main__':
    main()
