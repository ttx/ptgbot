#! /usr/bin/env python

# Copyright (c) 2017, Thierry Carrez
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
# http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

import argparse
import configparser
import daemon
import irc.bot
import logging.config
import os
import time
import ssl

import ptgbot.db

try:
    import daemon.pidlockfile as pid_file_module
except ImportError:
    # as of python-daemon 1.6 it doesn't bundle pidlockfile anymore
    # instead it depends on lockfile-0.9.1
    import daemon.pidfile as pid_file_module

# https://bitbucket.org/jaraco/irc/issue/34/
# irc-client-should-not-crash-on-failed
# ^ This is why pep8 is a bad idea.
irc.client.ServerConnection.buffer_class.errors = 'replace'
ANTI_FLOOD_SLEEP = 2


class PTGBot(irc.bot.SingleServerIRCBot):
    log = logging.getLogger("ptgbot.bot")

    def __init__(self, nickname, password, server, port, channels, db):
        if port == 6697:
            factory = irc.connection.Factory(wrapper=ssl.wrap_socket)
            irc.bot.SingleServerIRCBot.__init__(self,
                                                [(server, port)],
                                                nickname, nickname,
                                                connect_factory=factory)
        else:
            irc.bot.SingleServerIRCBot.__init__(self,
                                                [(server, port)],
                                                nickname, nickname)
        self.nickname = nickname
        self.password = password
        self.channel_list = channels
        self.identify_msg_cap = False
        self.data = db

    def on_nicknameinuse(self, c, e):
        self.log.debug("Nickname in use, releasing")
        c.nick(c.get_nickname() + "_")
        c.privmsg("nickserv", "identify %s " % self.password)
        c.privmsg("nickserv", "ghost %s %s" % (self.nickname, self.password))
        c.privmsg("nickserv", "release %s %s" % (self.nickname, self.password))
        time.sleep(ANTI_FLOOD_SLEEP)
        c.nick(self.nickname)

    def on_welcome(self, c, e):
        self.identify_msg_cap = False
        self.log.debug("Requesting identify-msg capability")
        c.cap('REQ', 'identify-msg')
        c.cap('END')
        if (self.password):
            self.log.debug("Identifying to nickserv")
            c.privmsg("nickserv", "identify %s " % self.password)
        for channel in self.channel_list:
            self.log.info("Joining %s" % channel)
            c.join(channel)
            time.sleep(ANTI_FLOOD_SLEEP)

    def on_cap(self, c, e):
        self.log.debug("Received cap response %s" % repr(e.arguments))
        if e.arguments[0] == 'ACK' and 'identify-msg' in e.arguments[1]:
            self.log.debug("identify-msg cap acked")
            self.identify_msg_cap = True

    def usage(self, channel):
        self.send(channel, "Format is '@ROOM [now|next] SESSION'")

    def on_pubmsg(self, c, e):
        if not self.identify_msg_cap:
            self.log.debug("Ignoring message because identify-msg "
                           "cap not enabled")
            return
        nick = e.source.split('!')[0]
        auth = e.arguments[0][0] == '+'
        msg = e.arguments[0][1:]
        chan = e.target

        if msg.startswith('#'):
            if not (self.channels[chan].is_voiced(nick) or
                    self.channels[chan].is_oper(nick)):
                self.send(chan, "%s: Need voice to issue commands" % (nick,))
                return
            words = msg.split()
            if len(words) < 3:
                self.send(chan, "%s: Incorrect number of arguments" % (nick,))
                self.usage(chan)
                return
            room = words[0][1:].lower()
            # TODO: Add test for room/day/person match
            adverb = words[1].lower()
            session = str.join(' ', words[2:])
            if adverb == 'now':
                self.data.add_now(room, session)
            elif adverb == 'next':
                self.data.add_next(room, session)
            else:
                self.send(chan, "%s: unknown directive '%s'" % (nick, adverb))
                self.usage(chan)
                return
            self.send(chan, "%s: ack" % (nick,))

        if msg.startswith('!'):
            if not self.channels[chan].is_oper(nick):
                self.send(chan, "%s: Need op for admin commands" % (nick,))
                return
            words = msg.split()
            command = words[0][1:].lower()
            if command == 'wipe':
                self.data.wipe()
            else:
                self.send(chan, "%s: unknown command '%s'" % (nick, command))
                return
            self.send(chan, "%s: done" % (nick,))

    def send(self, channel, msg):
        self.connection.privmsg(channel, msg)
        time.sleep(ANTI_FLOOD_SLEEP)


def start(configpath):
    config = configparser.RawConfigParser()
    config.read(configpath)

    if config.has_option('ircbot', 'log_config'):
        log_config = config.get('ircbot', 'log_config')
        fp = os.path.expanduser(log_config)
        if not os.path.exists(fp):
            raise Exception("Unable to read logging config file at %s" % fp)
        logging.config.fileConfig(fp)
    else:
        logging.basicConfig(level=logging.DEBUG)

    channels = ['#' + name.strip() for name in
                config.get('ircbot', 'channels').split(',')]

    db = ptgbot.db.PTGDataBase(
        config.get('db', 'filename'),
        config.get('db', 'ethercalc'),
        config.get('db', 'cells'))

    bot = PTGBot(config.get('ircbot', 'nick'),
                 config.get('ircbot', 'pass'),
                 config.get('ircbot', 'server'),
                 config.getint('ircbot', 'port'),
                 channels,
                 db)
    bot.start()


def main():
    parser = argparse.ArgumentParser(description='PTG bot.')
    parser.add_argument('configfile', nargs=1,
                        help='specify the config file')
    parser.add_argument('-d', dest='nodaemon', action='store_true',
                        help='do not run as a daemon')
    args = parser.parse_args()

    if not args.nodaemon:
        pid = pid_file_module.TimeoutPIDLockFile(
            "/var/run/ptgbot/ptgbot.pid", 10)
        with daemon.DaemonContext(pidfile=pid):
            start(args.configfile)
    start(args.configfile)


if __name__ == "__main__":
    main()
